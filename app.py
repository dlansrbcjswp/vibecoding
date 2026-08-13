from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src.analyzer import (
    CATEGORY_ORDER,
    analyze_posts,
    build_category_stats,
    build_trend_narrative,
    build_unclassified_terms,
    build_keyword_stats,
    filter_posts_by_date,
    split_evidence,
)
from src.collector import collect_posts
from src.translator import translate_posts


PAGE_TITLE = "리니지2M 중국 유저 동향 툴 v2.3"
SOURCE_LABELS = [
    "QQ 공식 채널",
    "TapTap",
    "Bilibili",
]


st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.3rem;
        padding-bottom: 2rem;
        max-width: 1600px;
    }
    [data-testid="stHeader"] {
        background: rgba(0, 0, 0, 0);
    }
    [data-testid="stMetric"] {
        border: 1px solid rgba(128, 128, 128, 0.24);
        border-radius: 12px;
        padding: 0.7rem 0.9rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _empty_results() -> pd.DataFrame:
    return analyze_posts([])


def _initialize_state() -> None:
    defaults = {
        "result_df": _empty_results(),
        "source_status": {},
        "has_run": False,
        "translation_failures": 0,
        "unknown_date_count": 0,
        "out_of_range_count": 0,
        "irrelevant_count": 0,
        "invalid_count": 0,
        "campaign_count": 0,
        "low_signal_count": 0,
        "candidate_count": 0,
        "last_period": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _show_source_status(status: dict[str, dict]) -> None:
    if not status:
        return

    st.subheader("수집 상태")
    columns = st.columns(min(4, max(1, len(status))))
    for index, (source, result) in enumerate(status.items()):
        count = int(result.get("count", 0) or 0)
        selected_count = int(result.get("selected_count", count) or 0)
        message = result.get("message", "")
        state = result.get("state", "")
        icon = {
            "success": "✅",
            "warning": "⚠️",
            "blocked": "⛔",
            "error": "❌",
        }.get(state, "✅" if result.get("ok") else "⚠️")
        candidate_count = int(result.get("candidate_count", 0) or 0)
        live_candidate_count = int(result.get("live_candidate_count", 0) or 0)
        new_candidate_count = int(result.get("new_candidate_count", 0) or 0)
        cumulative_candidate_count = int(
            result.get("cumulative_candidate_count", candidate_count) or 0
        )
        unknown_count = int(result.get("excluded_unknown_date", 0) or 0)
        outside_count = int(result.get("excluded_out_of_range", 0) or 0)
        irrelevant_count = int(result.get("excluded_irrelevant", 0) or 0)
        invalid_count = int(result.get("excluded_invalid", 0) or 0)
        campaign_count = int(result.get("excluded_campaign", 0) or 0)
        low_signal_count = int(result.get("excluded_low_signal", 0) or 0)
        coverage_message = str(result.get("coverage_message", "") or "")
        coverage_state = str(result.get("coverage_state", "") or "")
        collection_rounds = int(result.get("collection_rounds", 0) or 0)
        successful_rounds = int(result.get("successful_rounds", 0) or 0)
        latest_window_new = int(result.get("latest_window_new", 0) or 0)
        collection_mode = str(result.get("collection_mode", "") or "")
        period_boundary_reached = bool(result.get("period_boundary_reached", False))
        main_board_candidate_count = int(
            result.get("main_board_candidate_count", 0) or 0
        )
        official_board_candidate_count = int(
            result.get("official_board_candidate_count", 0) or 0
        )
        detail_verified_count = int(result.get("detail_verified_count", 0) or 0)
        detail_failed_count = int(result.get("detail_failed_count", 0) or 0)
        oldest_verified_date = str(result.get("oldest_verified_date", "") or "")
        fallback_sample_used = bool(result.get("fallback_sample_used", False))
        qq_board_results = result.get("qq_board_results", []) or []
        cache_load_message = str(result.get("cache_load_message", "") or "")
        cache_save_message = str(result.get("cache_save_message", "") or "")
        with columns[index % len(columns)]:
            st.metric(f"{icon} {source}", f"분석 포함 {selected_count:,}건")
            st.caption(message)
            st.caption(
                f"이번 실행 확보 {live_candidate_count:,} · "
                f"새로 확인 {new_candidate_count:,} · "
                f"누적 원문 {cumulative_candidate_count:,}"
            )
            if candidate_count:
                st.caption(
                    f"기간 내 유효 {count:,} · 누적 후보 {candidate_count:,} · "
                    f"이벤트성 제외 {campaign_count:,} · 저정보 제외 {low_signal_count:,} · "
                    f"날짜 미확인 제외 {unknown_count:,} · "
                    f"기간 밖 제외 {outside_count:,} · 저관련 제외 {irrelevant_count:,} · "
                    f"비정상 제외 {invalid_count:,}"
                )
            if coverage_message:
                round_text = f" · {collection_rounds}회 확인" if collection_rounds else ""
                if successful_rounds:
                    round_text += f"(성공 {successful_rounds}회)"
                if (
                    source == "QQ 공식 채널"
                    and collection_rounds
                    and collection_mode != "chronological_period_complete"
                ):
                    round_text += f" · 최근 표본 신규 {latest_window_new:,}건"
                st.caption(f"확인 범위: {coverage_message}{round_text}")
            if source == "QQ 공식 채널" and collection_mode:
                if period_boundary_reached:
                    st.success(
                        "날짜순 기간 경계 확인 완료 · "
                        f"일반 게시판 {main_board_candidate_count:,}건 · "
                        f"공식 정보 {official_board_candidate_count:,}건 · "
                        f"게시일 확인 {detail_verified_count:,}건"
                        + (f" · 최저 날짜 {oldest_verified_date}" if oldest_verified_date else "")
                    )
                else:
                    st.caption(
                        "수집 방식: 공개 웹 표본"
                        + (" · 인기 노출 화면 기반" if fallback_sample_used else "")
                        + (f" · 게시일 미확인 {detail_failed_count:,}건" if detail_failed_count else "")
                    )
                if qq_board_results:
                    discovered = result.get("discovered_boards") or []
                    if discovered:
                        st.caption(
                            "발견한 게시판: "
                            + " · ".join(str(item) for item in discovered)
                        )
                    with st.expander("QQ 게시판별 수집 범위", expanded=False):
                        board_frame = pd.DataFrame(qq_board_results)
                        board_columns = [
                            column
                            for column in [
                                "section",
                                "channel_id",
                                "candidate_count",
                                "detail_verified_count",
                                "detail_failed_count",
                                "oldest_verified_date",
                                "rounds",
                                "scroll_target_count",
                                "captured_batch_count",
                                "complete",
                                "stop_reason",
                            ]
                            if column in board_frame.columns
                        ]
                        st.dataframe(
                            board_frame[board_columns],
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "section": "게시판",
                                "channel_id": "게시판 ID",
                                "candidate_count": "확인 글",
                                "detail_verified_count": "게시일 확인",
                                "detail_failed_count": "게시일 미확인",
                                "oldest_verified_date": "최저 날짜",
                                "rounds": "스크롤 회차",
                                "scroll_target_count": "스크롤 컨테이너",
                                "captured_batch_count": "피드 응답 수",
                                "complete": "기간 경계 완료",
                                "stop_reason": "종료 사유",
                            },
                        )
            if cache_load_message:
                st.warning(cache_load_message)
            if cache_save_message:
                st.warning(cache_save_message)

    excluded_rows: list[dict] = []
    for source, result in status.items():
        for sample in result.get("excluded_samples", []) or []:
            excluded_rows.append({"출처": source, **sample})
    if excluded_rows:
        with st.expander("이벤트성·저정보 제외 예시", expanded=False):
            excluded_df = pd.DataFrame(excluded_rows)
            st.dataframe(
                excluded_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "reason": "제외 사유",
                    "date": "게시일",
                    "title": "원문 제목/본문",
                    "url": st.column_config.LinkColumn("원문", display_text="열기"),
                },
            )


def _category_sort_key(category: str, count: int) -> tuple:
    """건수 내림차순으로 정렬하되 '기타'는 항상 맨 마지막에 둡니다."""
    return (
        1 if category == "기타" else 0,
        -int(count),
        CATEGORY_ORDER.index(category) if category in CATEGORY_ORDER else 999,
    )


def _show_category_metrics(category_stats: pd.DataFrame) -> None:
    observed = category_stats[category_stats["전체"] > 0].copy()
    if observed.empty:
        return

    rows = sorted(
        observed.to_dict("records"),
        key=lambda item: _category_sort_key(item["분류"], item["전체"]),
    )
    for start in range(0, len(rows), 4):
        columns = st.columns(4)
        for offset, item in enumerate(rows[start : start + 4]):
            with columns[offset]:
                st.metric(item["분류"], f"{int(item['전체']):,}건")


def _format_count_option(label: str, counts: dict[str, int]) -> str:
    if label == "전체":
        return "전체"
    return f"{label} ({counts.get(label, 0):,}건)"


def _filter_display_rows(
    frame: pd.DataFrame,
    category: str,
    content_type: str,
    keyword: str,
) -> pd.DataFrame:
    result = frame.copy()
    if category != "전체":
        result = result[result["category"] == category]
    if content_type != "전체":
        result = result[result["content_type"] == content_type]
    if keyword != "전체":
        result = result[
            result["keyword_labels"].apply(
                lambda labels: keyword in labels if isinstance(labels, list) else False
            )
        ]
    return result.reset_index(drop=True)


def _safe_int(value: object) -> int:
    if value is None or pd.isna(value):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _short_text(value: object, max_length: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _category_keyword_text(frame: pd.DataFrame, limit: int = 5) -> str:
    stats = build_keyword_stats(frame)
    if stats.empty:
        return "감지된 주요 키워드 없음"
    return " · ".join(
        f"{row['키워드']} {int(row['관련 글 수']):,}건"
        for _, row in stats.head(limit).iterrows()
    )


def _build_trend_report_text(
    trend_frame: pd.DataFrame,
    support_frame: pd.DataFrame,
    category_stats: pd.DataFrame,
) -> str:
    """동향 요약을 지표 카드 대신 문장형 보고문으로 만듭니다."""
    period = st.session_state.get("last_period", "")
    trend_count = len(trend_frame)

    if trend_count == 0:
        return (
            f"- **{period}** 기간에 검증을 통과한 자연 발생 유저 글이 없습니다.\n"
            "- 수집 범위와 제외 사유는 ‘수집 내역’ 탭에서 확인하세요."
        )

    # 출처별 구성
    source_counts = trend_frame["source"].value_counts().to_dict()
    source_text = " · ".join(
        f"{name} {int(count):,}건" for name, count in source_counts.items()
    )

    # 카테고리 상위 (기타 제외 — 미분류이므로 동향으로 읽지 않습니다)
    observed = category_stats[category_stats["전체"] > 0]
    ranked = sorted(
        observed.to_dict("records"),
        key=lambda item: _category_sort_key(item["분류"], item["전체"]),
    )
    named = [item for item in ranked if item["분류"] != "기타"]
    top_text = (
        " · ".join(
            f"{item['분류']} {int(item['전체']):,}건" for item in named[:5]
        )
        or "분류된 주제 없음"
    )
    other_row = next((item for item in ranked if item["분류"] == "기타"), None)
    other_count = int(other_row["전체"]) if other_row else 0
    other_ratio = other_count / trend_count * 100 if trend_count else 0.0

    # 주요 키워드
    keyword_stats = build_keyword_stats(trend_frame)
    keyword_text = (
        " · ".join(
            f"{row['키워드']} {int(row['관련 글 수']):,}건"
            for _, row in keyword_stats.head(5).iterrows()
        )
        if not keyword_stats.empty
        else "감지된 업무 키워드 없음"
    )

    lines = [
        f"**{period}** 기간 중국 공개 커뮤니티에서 게시일·본문이 검증된 "
        f"자연 발생 유저 글 **{trend_count:,}건**을 확인했습니다.",
        "",
        f"- **출처 구성**: {source_text}",
        f"- **주요 주제**: {top_text}",
        f"- **주요 키워드**: {keyword_text}",
        f"- **미분류(기타)**: {other_count:,}건 "
        f"({other_ratio:.0f}%) · 자동 분류 규칙에 걸리지 않은 글입니다.",
        f"- **참고 자료 분리**: 공략·영상·공식·홍보 글 {len(support_frame):,}건은 "
        "여론 집계에서 제외하고 ‘콘텐츠·공략 참고’ 탭으로 분리했습니다.",
    ]

    # 수집 신뢰도 경고를 요약문 안에 함께 남깁니다.
    cautions: list[str] = []
    status_map = st.session_state.get("source_status", {}) or {}
    if isinstance(status_map, dict):
        for name, result in status_map.items():
            if not isinstance(result, dict):
                continue
            state = str(result.get("state", "") or "")
            if state in {"success", ""} and result.get("ok", True):
                continue
            note = str(result.get("message", "") or "상태 미확인")
            cautions.append(f"{name} — {note}")
    if cautions:
        lines.append("")
        lines.append(
            "> ⚠️ **해석 주의** — 아래 소스는 이번 실행에서 정상 수집되지 않았습니다. "
            "표시된 건수에 이전 캐시가 섞여 있을 수 있습니다."
        )
        for note in cautions:
            lines.append(f"> - {note}")

    return "\n".join(lines)


def _top_posts(frame: pd.DataFrame, column: str, count: int = 10) -> pd.DataFrame:
    """지정한 지표 기준 상위 글을 뽑습니다. 값이 0인 글은 제외합니다."""
    if frame.empty or column not in frame.columns:
        return frame.iloc[0:0]
    part = frame.copy()
    part["_metric"] = pd.to_numeric(part[column], errors="coerce").fillna(0)
    part = part[part["_metric"] > 0]
    if part.empty:
        return part
    return part.sort_values("_metric", ascending=False).head(count)


def _render_top_post_list(
    frame: pd.DataFrame,
    metric_column: str,
    metric_label: str,
    empty_note: str,
    count: int = 10,
) -> None:
    part = _top_posts(frame, metric_column, count)
    if part.empty:
        st.caption(empty_note)
        return

    for rank, (_, row) in enumerate(part.iterrows(), 1):
        title = row.get("title_ko") or row.get("title") or "제목 없음"
        st.markdown(f"**{rank}. {_short_text(title, 60)}**")
        st.caption(
            f"{metric_label} {int(row['_metric']):,} · "
            f"{row.get('date', '')} · {row.get('source', '')} · "
            f"{row.get('category', '')}"
        )
        body = row.get("translated_text") or row.get("original_text") or ""
        if body and _short_text(body, 40) != _short_text(title, 40):
            st.write(_short_text(body, 110))
        if row.get("url"):
            st.caption(f"[원문 열기]({row['url']})")


def _show_popular_posts(frame: pd.DataFrame) -> None:
    """댓글 TOP 10과 조회수 TOP 10을 좌우로 나란히 보여줍니다."""
    if frame.empty:
        st.info("자연 발생 유저 글이 없습니다.")
        return

    comment_col, view_col = st.columns(2, gap="large")
    with comment_col:
        st.markdown("##### 댓글 TOP 10")
        _render_top_post_list(
            frame,
            "engagement",
            "댓글/반응",
            "댓글·반응이 확인된 글이 없습니다.",
            count=10,
        )
    with view_col:
        st.markdown("##### 조회수 TOP 10")
        _render_top_post_list(
            frame,
            "views",
            "조회수",
            "조회수가 확인된 글이 없습니다.",
            count=10,
        )


def _show_category_examples(frame: pd.DataFrame) -> None:
    """카테고리별 건수와 실제 관련 원문을 한 화면에서 확인합니다."""
    if frame.empty:
        st.info("자연 발생 유저 글이 없습니다.")
        return

    category_order = sorted(
        frame["category"].unique(),
        key=lambda category: _category_sort_key(
            category,
            int((frame["category"] == category).sum()),
        ),
    )
    for category in category_order:
        part = frame[frame["category"] == category].copy()
        with st.expander(
            f"{category} · {len(part):,}건",
            expanded=False,
        ):
            st.caption(f"주요 키워드: {_category_keyword_text(part)}")
            for row_index, (_, row) in enumerate(part.head(12).iterrows(), 1):
                title = row["title_ko"] or row["title"] or "제목 없음"
                translated = row["translated_text"] or title
                st.markdown(
                    f"**{row_index}) {row['date']} · {row['source']}**  {title}"
                )
                if translated and translated != title:
                    st.write(_short_text(translated))
                elif row["original_text"]:
                    st.caption(_short_text(row["original_text"]))
                if row["url"]:
                    st.link_button("해당 원문 열기", row["url"])


_initialize_state()

st.title(PAGE_TITLE)
st.caption(
    "중국 공개 커뮤니티 수집 → 게시일·본문 검증 → 중국어 원문 보존 → "
    "한국어 자동 번역 → 카테고리·키워드별 실제 글 확인"
)

with st.form("collection_form"):
    today = date.today()
    sources = st.multiselect(
        "조사 소스",
        SOURCE_LABELS,
        default=["QQ 공식 채널", "TapTap", "Bilibili"],
        help=(
            "TapTap·Bilibili는 공개 페이지/API를 사용합니다. "
            "QQ는 설치된 Chrome/Edge로 일반 게시글 광장의 날짜순 목록을 "
            "조회 시작일 이전까지 확인하고, 각 글의 실제 게시일을 검증합니다."
        ),
    )

    date_left, date_right = st.columns(2)
    with date_left:
        start_date = st.date_input(
            "조회 시작일",
            value=today - timedelta(days=7),
        )
    with date_right:
        end_date = st.date_input("조회 종료일", value=today)

    submitted = st.form_submit_button(
        "수집·분석 실행",
        type="primary",
        use_container_width=True,
    )

st.caption(
    "한국어 번역은 항상 적용됩니다. 게시일을 확인할 수 없거나 조회 기간 밖인 글은 "
    "집계에서 제외합니다. QQ 캠페인 해시태그·이벤트 모집 글과 텍스트 정보가 부족한 글도 "
    "유저 동향에서 제외합니다. 결과 글 수 상한 없이 검증을 통과한 누적 원문을 모두 표시합니다."
)

if submitted:
    if not sources:
        st.warning("조사 소스를 하나 이상 선택해 주세요.")
    elif start_date > end_date:
        st.warning("조회 시작일이 종료일보다 늦습니다.")
    else:
        try:
            with st.spinner(
                "공개 페이지 수집과 번역을 진행하고 있습니다. 글 수에 따라 시간이 걸릴 수 있습니다."
            ):
                raw_posts, source_status = collect_posts(
                    selected_sources=sources,
                    start_date=start_date,
                    end_date=end_date,
                )
                translated_posts = translate_posts(raw_posts)
                analyzed_df = analyze_posts(translated_posts)
                period_df, unknown_date_count = filter_posts_by_date(
                    analyzed_df,
                    start_date,
                    end_date,
                    keep_unknown_dates=False,
                )

            st.session_state.result_df = period_df
            st.session_state.source_status = source_status
            st.session_state.has_run = True
            st.session_state.translation_failures = sum(
                bool(item.get("translation_error")) for item in translated_posts
            )
            st.session_state.unknown_date_count = unknown_date_count
            st.session_state.unknown_date_count += sum(
                int(item.get("excluded_unknown_date", 0) or 0)
                for item in source_status.values()
            )
            st.session_state.out_of_range_count = sum(
                int(item.get("excluded_out_of_range", 0) or 0)
                for item in source_status.values()
            )
            st.session_state.irrelevant_count = sum(
                int(item.get("excluded_irrelevant", 0) or 0)
                for item in source_status.values()
            )
            st.session_state.invalid_count = sum(
                int(item.get("excluded_invalid", 0) or 0)
                for item in source_status.values()
            )
            st.session_state.campaign_count = sum(
                int(item.get("excluded_campaign", 0) or 0)
                for item in source_status.values()
            )
            st.session_state.low_signal_count = sum(
                int(item.get("excluded_low_signal", 0) or 0)
                for item in source_status.values()
            )
            st.session_state.candidate_count = sum(
                int(item.get("candidate_count", 0) or 0)
                for item in source_status.values()
            )
            st.session_state.last_period = (
                f"{start_date.isoformat()} ~ {end_date.isoformat()}"
            )
        except Exception as error:
            st.session_state.result_df = _empty_results()
            st.session_state.source_status = {}
            st.session_state.has_run = True
            st.session_state.translation_failures = 0
            st.session_state.unknown_date_count = 0
            st.session_state.out_of_range_count = 0
            st.session_state.irrelevant_count = 0
            st.session_state.invalid_count = 0
            st.session_state.campaign_count = 0
            st.session_state.low_signal_count = 0
            st.session_state.candidate_count = 0
            st.error(
                "실행 중 오류가 발생했습니다. 아래 오류명을 캡처해 보내주세요. "
                f"({type(error).__name__}: {error})"
            )


if not st.session_state.has_run:
    st.info(
        "조사 소스와 기간을 선택한 뒤 ‘수집·분석 실행’을 눌러주세요. "
        "이 버전은 데모 글을 표시하지 않습니다."
    )
    st.stop()

result_df = st.session_state.result_df

if result_df.empty:
    with st.expander("수집 상태·제외 내역", expanded=True):
        _show_source_status(st.session_state.source_status)
    st.warning(
        "선택한 기간에 검증을 통과한 게시글이 없습니다. 수집 상태를 확인해 주세요. "
        "차단된 소스나 날짜 미확인 글을 임의로 결과에 포함하지 않았습니다."
    )
    st.stop()

if st.session_state.translation_failures:
    st.warning(
        f"번역에 실패한 글이 {st.session_state.translation_failures:,}건 있습니다. "
        "실패한 글도 중국어 원문은 유지됩니다."
    )

trend_df, support_df = split_evidence(result_df)
category_stats = build_category_stats(trend_df)

st.divider()
st.subheader("유저 동향 요약")
_collection_notes: list[str] = []
_status_map = st.session_state.get("source_status", {}) or {}
if isinstance(_status_map, dict):
    for _name, _result in _status_map.items():
        if not isinstance(_result, dict):
            continue
        _state = str(_result.get("state", "") or "")
        if _state not in {"success", ""} or not _result.get("ok", True):
            _collection_notes.append(
                f"{_name} 수집이 이번 실행에서 정상 완료되지 않았습니다. "
                f"({_result.get('message', '사유 미확인')}) "
                "표시 건수에 이전 캐시가 섞여 있을 수 있습니다."
            )


st.markdown(
    build_trend_narrative(
        trend_df,
        support_df,
        period=st.session_state.last_period,
        collection_notes=_collection_notes,
    )
)

st.markdown("#### 카테고리별 건수")
_show_category_metrics(category_stats)
st.caption(
    "QQ·TapTap 등 자연 발생 글만 집계합니다. Bilibili 공략·영상과 공식·홍보 글은 "
    "별도 참고 영역으로 분리합니다."
)

with st.expander("인기 게시글", expanded=False):
    st.caption(
        "조회 기간 내 자연 발생 유저 글 중 댓글이 많은 순 / 조회수가 많은 순 "
        "상위 10건을 각각 보여줍니다. 해당 지표가 확인되지 않은 글은 순위에서 제외됩니다."
    )
    _show_popular_posts(trend_df)

keyword_stats = build_keyword_stats(trend_df)
filter_keyword_stats = build_keyword_stats(result_df)

category_counts = (
    result_df["category"].value_counts().astype(int).to_dict()
)
content_type_counts = (
    result_df["content_type"].value_counts().astype(int).to_dict()
)
keyword_counts = (
    dict(zip(filter_keyword_stats["키워드"], filter_keyword_stats["관련 글 수"]))
    if not filter_keyword_stats.empty
    else {}
)

filter_left, filter_middle, filter_right = st.columns(3)
with filter_left:
    category_options = [
        category for category in CATEGORY_ORDER if category_counts.get(category, 0) > 0
    ]
    selected_category = st.selectbox(
        "결과 분류 필터",
        ["전체"] + category_options,
        format_func=lambda value: _format_count_option(value, category_counts),
    )
with filter_middle:
    selected_content_type = st.selectbox(
        "글 유형 필터",
        ["전체"] + list(content_type_counts),
        format_func=lambda value: _format_count_option(value, content_type_counts),
    )
with filter_right:
    selected_keyword = st.selectbox(
        "키워드 필터",
        ["전체"] + list(keyword_counts),
        format_func=lambda value: _format_count_option(value, keyword_counts),
    )

display_df = _filter_display_rows(
    result_df,
    selected_category,
    selected_content_type,
    selected_keyword,
)
st.caption(f"현재 필터 결과: {len(display_df):,}건")

tab1, tab2, tab3, tab4 = st.tabs(
    ["동향 요약", "원문·번역", "콘텐츠·공략 참고", "수집 내역"]
)

with tab1:
    left, right = st.columns([1, 1.05], gap="large")
    with left:
        st.markdown("#### 카테고리 분류")
        st.dataframe(
            category_stats[["분류", "전체", "자연 발생"]],
            use_container_width=True,
            hide_index=True,
        )
        observed_chart = category_stats[category_stats["전체"] > 0]
        if not observed_chart.empty:
            st.bar_chart(observed_chart.set_index("분류")["전체"])

    with right:
        st.markdown("#### 주요 키워드")
        st.caption(
            "‘관련 글 수’는 같은 글에서 같은 단어가 여러 번 나와도 1건으로 계산합니다."
        )
        if keyword_stats.empty:
            st.info("등록된 업무 키워드와 일치하는 글이 없습니다.")
        else:
            st.dataframe(
                keyword_stats.head(30),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("#### 카테고리별 관련 원문")
    st.caption(
        "각 카테고리의 실제 게시글을 바로 확인할 수 있습니다. 자동 분류가 애매한 글은 "
        "원문·번역 탭에서 다시 확인하세요."
    )
    _show_category_examples(trend_df)

    unclassified = build_unclassified_terms(trend_df)
    with st.expander("미분류(기타) 글에서 자주 나온 표현", expanded=False):
        st.caption(
            "‘기타’로 남은 글에 2건 이상 반복 등장했지만 아직 분류 규칙에 없는 "
            "중국어 표현입니다. 실제로 의미 있는 주제라면 규칙에 추가해 "
            "미분류 비율을 낮출 수 있습니다."
        )
        if unclassified.empty:
            st.info("2건 이상 반복된 미등록 표현이 없습니다.")
        else:
            st.dataframe(
                unclassified,
                use_container_width=True,
                hide_index=True,
            )

with tab2:
    st.caption(
        "검증된 개별 게시글 주소와 실제 수집 범위를 함께 표시합니다. "
        "영상은 게시글 본문이 아니라 영상 제목·설명 기준입니다."
    )

    if display_df.empty:
        st.info("현재 필터에 맞는 글이 없습니다.")
    else:
        for index, row in display_df.iterrows():
            date_label = row["date"]
            title = row["title_ko"] or row["title"] or "제목 없음"
            expander_label = (
                f"[{row['source']}] {title} · {date_label} · {row['category']}"
            )
            with st.expander(expander_label, expanded=False):
                st.markdown(f"**중국어 제목:** {row['title'] or '제목 없음'}")
                st.markdown(f"**한국어 제목:** {row['title_ko'] or '번역 제목 없음'}")
                meta_left, meta_right = st.columns(2)
                with meta_left:
                    st.markdown(f"**글 유형:** {row['content_type']}")
                    st.markdown(f"**분석 용도:** {row['analysis_role']}")
                    if row.get("source_section", ""):
                        st.markdown(f"**원문 섹션:** {row['source_section']}")
                    st.markdown(
                        f"**게시일 기준:** "
                        f"{row.get('published_at_source', '') or '출처 표시 날짜'}"
                    )
                    st.markdown(
                        f"**수집 범위:** {row.get('content_scope', '') or '범위 미확인'}"
                    )
                    st.markdown(
                        f"**조회:** {_safe_int(row['views']):,} · "
                        f"**댓글/반응:** {_safe_int(row['engagement']):,}"
                    )
                with meta_right:
                    keywords = ", ".join(row["keyword_labels"]) or "감지 없음"
                    st.markdown(f"**감지 키워드:** {keywords}")
                    if row["translation_error"]:
                        st.warning(f"번역 오류: {row['translation_error']}")

                original_col, translated_col = st.columns(2, gap="large")
                with original_col:
                    scope_label = row.get("content_scope", "") or "수집 원문"
                    st.markdown(f"**중국어 원문 · {scope_label}**")
                    st.write(row["original_text"] or "원문 본문을 확인하지 못했습니다.")
                with translated_col:
                    st.markdown("**한국어 번역**")
                    st.write(row["translated_text"] or "번역문 없음")

                if row["url"]:
                    st.link_button("원문 열기", row["url"])
                    st.caption(f"원문 주소: {row['url']}")

with tab3:
    st.caption(
        "Bilibili 영상과 홍보·공식 자료입니다. 어떤 정보성 콘텐츠가 생산되는지는 "
        "볼 수 있지만, 댓글 본문을 수집하지 않았으므로 유저 반응·여론으로 집계하지 않습니다."
    )
    if support_df.empty:
        st.info("공략·영상·홍보 참고자료가 없습니다.")
    else:
        support_stats = (
            support_df.groupby(["content_type", "category"], dropna=False)
            .size()
            .reset_index(name="건수")
            .sort_values("건수", ascending=False)
        )
        st.dataframe(
            support_stats,
            use_container_width=True,
            hide_index=True,
            column_config={
                "content_type": "자료 유형",
                "category": "내용 분류",
            },
        )
        support_list = support_df[
            ["date", "source", "category", "content_type", "title_ko", "views", "url"]
        ].copy()
        st.dataframe(
            support_list,
            use_container_width=True,
            hide_index=True,
            column_config={
                "date": "일자",
                "source": "출처",
                "category": "내용 분류",
                "content_type": "자료 유형",
                "title_ko": "한국어 제목",
                "views": st.column_config.NumberColumn("조회", format="%d"),
                "url": st.column_config.LinkColumn("원문", display_text="열기"),
            },
        )

with tab4:
    _show_source_status(st.session_state.source_status)

    st.markdown("#### 검증·제외 내역")
    quality_columns = st.columns(4)
    quality_columns[0].metric("누적 수집 후보", f"{st.session_state.candidate_count:,}건")
    quality_columns[1].metric("기간 내 검증", f"{len(result_df):,}건")
    quality_columns[2].metric(
        "이벤트성 제외",
        f"{st.session_state.campaign_count:,}건",
    )
    quality_columns[3].metric(
        "저정보 제외",
        f"{st.session_state.low_signal_count:,}건",
    )
    quality_columns = st.columns(4)
    quality_columns[0].metric(
        "날짜 미확인 제외",
        f"{st.session_state.unknown_date_count:,}건",
    )
    quality_columns[1].metric(
        "기간 밖 제외",
        f"{st.session_state.out_of_range_count:,}건",
    )
    quality_columns[2].metric(
        "저관련 제외",
        f"{st.session_state.irrelevant_count:,}건",
    )
    quality_columns[3].metric(
        "비정상 제외",
        f"{st.session_state.invalid_count:,}건",
    )

    st.markdown("#### 일자별 건수")
    if result_df.empty or "date" not in result_df.columns:
        st.caption("표시할 글이 없습니다.")
    else:
        daily = (
            result_df[result_df["date"].astype(str) != ""]
            .groupby("date", dropna=False)
            .agg(
                전체=("date", "size"),
                자연발생=(
                    "content_type",
                    lambda values: int((values == "자연 발생").sum()),
                ),
            )
            .reset_index()
            .rename(columns={"date": "게시일"})
            .sort_values("게시일", ascending=False)
        )
        st.dataframe(daily, use_container_width=True, hide_index=True)
        st.caption(
            "특정 날짜만 유독 적다면 그날 글이 적었던 것이 아니라 "
            "그 시점 목록을 확보하지 못했을 가능성이 큽니다. "
            "매주 실행할수록 누적 캐시가 쌓여 과거 일자도 채워집니다."
        )

    st.markdown("#### 출처별 구성")
    source_df = (
        result_df.groupby("source", dropna=False)
        .agg(
            수집글수=("source", "size"),
            자연발생=("content_type", lambda values: int((values == "자연 발생").sum())),
            이벤트홍보=(
                "content_type",
                lambda values: int((values == "이벤트·홍보").sum()),
            ),
            공략영상=("content_type", lambda values: int((values == "공략·영상").sum())),
            날짜미확인=("date", lambda values: int((values == "").sum())),
        )
        .reset_index()
        .rename(columns={"source": "출처"})
        .sort_values("수집글수", ascending=False)
    )
    st.dataframe(source_df, use_container_width=True, hide_index=True)
    st.caption(
        f"분석 기간: {st.session_state.last_period} · "
        "QQ는 일반 게시글 광장·공식 정보의 공개 날짜순 목록 기준 · "
        "비공개·삭제 글과 이미지·댓글은 수집 대상 아님"
    )

st.divider()
st.subheader("결과 글 목록")
list_columns = [
    "date",
    "source",
    "category",
    "content_type",
    "analysis_role",
    "content_scope",
    "source_section",
    "published_at_source",
    "title_ko",
    "views",
    "engagement",
    "url",
]
st.dataframe(
    display_df[list_columns],
    use_container_width=True,
    hide_index=True,
    column_config={
        "date": "일자",
        "source": "출처",
        "category": "주 분류",
        "content_type": "글 유형",
        "analysis_role": "분석 용도",
        "content_scope": "수집 범위",
        "source_section": "원문 섹션",
        "published_at_source": "게시일 기준",
        "title_ko": "한국어 제목",
        "views": st.column_config.NumberColumn("조회", format="%d"),
        "engagement": st.column_config.NumberColumn("댓글/반응", format="%d"),
        "url": st.column_config.LinkColumn("원문", display_text="열기"),
    },
)

download_columns = [
    "date",
    "source",
    "category",
    "all_categories",
    "content_type",
    "analysis_role",
    "trend_eligible",
    "title",
    "title_ko",
    "original_text",
    "translated_text",
    "content_scope",
    "source_note",
    "source_section",
    "published_at_source",
    "is_official",
    "is_campaign",
    "is_low_signal",
    "keyword_text",
    "views",
    "engagement",
    "author",
    "url",
    "translation_error",
]
csv_data = display_df[download_columns].to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "현재 필터 결과 CSV 다운로드",
    data=csv_data,
    file_name="l2m_cn_trend_results.csv",
    mime="text/csv",
)
