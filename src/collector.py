from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# 회사 PC에서도 Windows 인증서 저장소를 사용합니다. SSL 검증을 끄지 않습니다.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    # requirements.txt 설치 전의 문법·단위 검사도 가능하게 둡니다.
    pass

GAME_KEYWORD = "天堂2盟约"
GAME_ALIASES = (
    "天堂2盟约",
    "天堂2：盟约",
    "天堂2:盟约",
    "天堂2 盟约",
    "lineage2m",
    "lineage 2m",
    "l2m",
)

# 검색어를 한 줄 포함했을 뿐인 모바일게임 종합 목록·뉴스 영상은
# 리니지2M 중국 동향 근거로 쓰지 않습니다.
BILIBILI_ROUNDUP_MARKERS = (
    "手游榜",
    "手游资讯",
    "手游新闻",
    "手游日报",
    "手游早报",
    "手游推荐",
    "手游盘点",
    "手游目录",
    "手游省钱",
    "手游折扣",
    "手游特惠",
    "游戏资讯",
    "游戏新闻",
    "游戏日报",
    "游戏早报",
    "游戏盘点",
    "新游推荐",
)

TAPTAP_TOPIC_URL = "https://www.taptap.cn/app/749379/topic"
BILIBILI_SEARCH_ENDPOINTS = (
    "https://api.bilibili.com/x/web-interface/wbi/search/type",
    "https://api.bilibili.com/x/web-interface/search/type",
)
BILIBILI_VIEW_ENDPOINT = "https://api.bilibili.com/x/web-interface/view"
TIEBA_URL = "https://tieba.baidu.com/f?kw=" + quote(GAME_KEYWORD)
QQ_CHANNEL_URL = "https://pd.qq.com/g/pd38175600"
QQ_CHANNEL_ID = "pd38175600"

REQUEST_TIMEOUT = 18
REQUEST_DELAY = 0.12
CHINA_TZ = timezone(timedelta(hours=8))

# QQ 기본 화면은 '인기' 표본 10건 안팎만 노출합니다. v0.8은 기본 화면의
# Nuxt 공개 데이터에서 실제 게시판 ID를 확인한 뒤, 게시글 광장·공식 정보의
# 날짜순 피드를 조회 시작일 이전까지 스크롤합니다. 아래 회전 표본 설정은
# 날짜순 경로가 열리지 않거나 기간 경계에 도달하지 못했을 때만 보조로 씁니다.
QQ_MIN_SUCCESSFUL_ROUNDS = 30
QQ_MAX_ROUNDS = 60
QQ_CONVERGENCE_WINDOW = 10
QQ_ENTRY_URLS = (
    QQ_CHANNEL_URL,
    f"{QQ_CHANNEL_URL}?subc=hot",
)
QQ_EVENT_SECTIONS = {"活动专区", "故事征集"}
# 진단 결과 실제 탭은 全部(hot) / 官方资讯 / 活动专区 / 故事征集 4개뿐입니다.
# 이전 버전이 메인으로 쓰던 帖子广场(688289023)은 탭이 아니라 hiddenChannelId 이며,
# 유저 글과 공식 글이 함께 흐르는 실제 메인 피드는 全部(subc=hot) 입니다.
QQ_MAIN_SECTION = "全部"
QQ_HIDDEN_SECTION = "帖子广场"
QQ_OFFICIAL_SECTION = "官方资讯"
# 공개 페이지 구조가 일시적으로 바뀌어 게시판 자동 발견이 실패할 때 쓰는
# 2026-08-12 기준 안전한 폴백입니다. 정상 동작에서는 페이지가 제공한 ID를 우선합니다.
QQ_FALLBACK_BOARD_ROUTES = (
    ("hot", QQ_MAIN_SECTION),
    ("688289023", QQ_HIDDEN_SECTION),
    ("690970370", QQ_OFFICIAL_SECTION),
    ("728458210", "活动专区"),
    ("690970406", "故事征集"),
)
# 조회 기간이 짧아도 수집은 항상 이 일수만큼은 거슬러 올라갑니다.
# 수집 깊이와 표시 범위를 분리하지 않으면, 하루만 조회할 때 스캔이
# 즉시 종료돼 사실상 캐시에 있는 것만 보이게 됩니다.
COLLECT_MIN_LOOKBACK_DAYS = 14
# 화면(app.py)에서도 조회 시작일·종료일을 이 일수보다 과거로는 고르지 못하게
# 막아 둡니다. 여기서도 동일한 값으로 한 번 더 막아, UI 제한을 우회해 호출되는
# 경우에도 QQ 스캔이 한없이 과거로 내려가지 않도록 합니다.
QQ_MAX_SCAN_LOOKBACK_DAYS = 30

# 한 화면씩 내려가므로 라운드 수를 늘리되, 전체 실행 시간이 과도해지지
# 않도록 게시판당 시간 상한을 함께 둡니다. 조회 기간을 최대 한 달 전까지
# 허용하므로(QQ_MAX_SCAN_LOOKBACK_DAYS), 그 깊이까지 스크롤할 여유를 둡니다.
# 실제로는 대부분 기간 경계(period_boundary_reached)에 먼저 도달해 이 상한을
# 다 쓰지 않고 끝납니다.
QQ_PERIOD_SCAN_MAX_SCROLLS = 200
QQ_BOARD_TIME_BUDGET_SECONDS = 300.0
QQ_PERIOD_SCAN_STALE_ROUNDS = 6

# 내부 스크롤 컨테이너를 찾아 바닥까지 내리고, 마지막 카드도 화면에 붙입니다.
# 반환값은 실제로 스크롤이 움직인 컨테이너 개수(진단용)입니다.
QQ_SCROLL_SCRIPT = """
() => {
  // QQ 피드는 vue-recycle-scroller(가상 스크롤러)입니다.
  // 화면 밖 게시글은 DOM에서 제거되므로, 바닥으로 한 번에 점프하면
  // 중간 게시글이 읽히기 전에 사라집니다.
  // 따라서 한 화면씩 조금씩 내려서 매 라운드마다 읽을 수 있게 합니다.
  let moved = 0;
  let atBottom = 0;
  const targets = [];
  for (const el of document.querySelectorAll('*')) {
    if (el.scrollHeight <= el.clientHeight + 120) continue;
    const oy = getComputedStyle(el).overflowY;
    if (oy !== 'auto' && oy !== 'scroll') continue;
    targets.push(el);
  }
  for (const el of targets) {
    const before = el.scrollTop;
    const step = Math.max(240, Math.floor(el.clientHeight * 0.75));
    el.scrollTop = Math.min(before + step, el.scrollHeight);
    try {
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: step, bubbles: true }));
      el.dispatchEvent(new Event('scroll', { bubbles: true }));
    } catch (e) {}
    if (el.scrollTop !== before) moved += 1;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 12) atBottom += 1;
  }
  if (targets.length === 0) {
    const before = window.scrollY;
    window.scrollBy(0, Math.floor(window.innerHeight * 0.75));
    try { window.dispatchEvent(new Event('scroll')); } catch (e) {}
    if (window.scrollY !== before) moved += 1;
  }
  return { moved: moved, targets: targets.length, atBottom: atBottom };
}
"""
QQ_PERIOD_BOUNDARY_OLD_POSTS = 12
QQ_DETAIL_REQUEST_DELAY = 0.04
QQ_CAMPAIGN_MARKERS = (
    "#我们的故事",
    "#ourstory",
    "#covenantparadise2covenant",
    "#战盟的狩猎时刻",
    "#锦鲤传递官",
    "#游戏封神名场面",
    "#今天打宝出了什么",
    "参与话题活动赢q币奖励",
)
QQ_LOW_SIGNAL_EXACT = {
    "分享图片",
    "分享视频",
    "爽",
    "终于到我了",
    "来了",
    "路过",
}
# 목록 카드 클래스명이 바뀐 이력이 있어 옛 이름도 함께 받습니다.
TAPTAP_ITEM_SELECTOR = ".moment-list-item, .moment-feed-list-item"
# 게시판과 별개로 리뷰(评价) 탭이 있고, 유저가 직접 쓴 평가라 여론 가치가 큽니다.
TAPTAP_REVIEW_URL = "https://www.taptap.cn/app/749379/review"
TAPTAP_REVIEW_ITEM_SELECTOR = ".review-item"
TAPTAP_REVIEW_MAX_ROUNDS = 40
TAPTAP_MAX_SCROLL_ROUNDS = 60
TAPTAP_STALE_ROUNDS = 5
TAPTAP_BOUNDARY_CONFIRM_ROUNDS = 2

# 사용자가 같은 기간을 다시 조사할 때 QQ의 회전 표본이 매번 바뀌어 결과가
# 줄어드는 문제를 막기 위해 공개 페이지에서 확인한 원문을 로컬에 누적합니다.
# 캐시는 분석 결과가 아니라 출처별 원문·게시일 근거만 보존합니다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 프로그램 폴더의 캐시는 '공유 기준값'으로 읽기만 합니다.
# 네트워크 공유 폴더에 툴을 올려 여러 명이 함께 쓰는 경우,
# 같은 파일에 동시에 쓰면 서로의 누적 결과를 덮어씁니다.
# 따라서 저장은 항상 각자 PC의 개인 폴더에만 합니다.
SHARED_CACHE_PATH = PROJECT_ROOT / "data" / "collection_cache.json"


def _resolve_local_cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "l2m_cn_trend_tool" / "collection_cache.json"
    return Path.home() / ".l2m_cn_trend_tool" / "collection_cache.json"


CACHE_PATH = _resolve_local_cache_path()
CACHE_SCHEMA_VERSION = 1
SOURCE_FETCH_LIMIT = 500


class SourceUnavailable(RuntimeError):
    """공개 요청 방식으로 해당 소스를 수집할 수 없을 때 사용합니다."""


@dataclass
class CollectedPost:
    source: str
    title: str
    original_text: str
    url: str
    published_at: str = ""
    author: str = ""
    views: int | None = None
    comments: int | None = None
    content_scope: str = ""
    source_note: str = ""
    source_section: str = ""
    published_at_source: str = ""
    is_official: bool = False
    is_campaign: bool = False
    is_low_signal: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()


def _strip_html(value: object) -> str:
    return _clean_text(BeautifulSoup(str(value or ""), "html.parser").get_text(" "))


def _absolute_url(base_url: str, href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    return urljoin(base_url, href)


def _canonical_url(url: str) -> str:
    """추적용 쿼리는 제거하되 TapTap의 group_id처럼 필요한 쿼리는 보존합니다."""
    parts = urlsplit(url)
    keep_query = (
        "taptap.cn/moment/" in url
        or ("pd.qq.com/g/" in url and "/post/" in url)
    )
    query = parts.query if keep_query else ""
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path, query, ""))


def _qq_post_key(url: object) -> str:
    """QQ 게시글 URL의 쿼리 유무와 관계없이 같은 글을 식별합니다."""
    match = re.search(r"/post/([^?/#]+)", _clean_text(url))
    return match.group(1) if match else ""


def _post_cache_key(post: CollectedPost) -> str:
    """출처별 원문을 실행 간 동일하게 식별하는 안정 키입니다."""
    if post.source == "QQ 공식 채널":
        qq_key = _qq_post_key(post.url)
        if qq_key:
            return f"{post.source}|{qq_key}"

    canonical_url = _canonical_url(post.url)
    if canonical_url:
        return f"{post.source}|{canonical_url}"

    title_key = re.sub(r"\W+", "", _clean_text(post.title)).casefold()
    return f"{post.source}|{post.published_at}|{title_key}"


def _post_from_dict(value: object) -> CollectedPost | None:
    if not isinstance(value, dict):
        return None
    allowed = CollectedPost.__dataclass_fields__
    required = ("source", "title", "original_text", "url")
    if any(not _clean_text(value.get(name)) for name in required):
        return None
    payload = {name: value.get(name, field.default) for name, field in allowed.items()}
    try:
        return CollectedPost(**payload)
    except (TypeError, ValueError):
        return None


def _merge_post(existing: CollectedPost, incoming: CollectedPost) -> CollectedPost:
    """새 표본의 빈 필드 때문에 이전에 검증한 근거가 사라지지 않도록 합칩니다."""
    old = existing.to_dict()
    new = incoming.to_dict()
    merged = dict(old)

    for name in CollectedPost.__dataclass_fields__:
        old_value = old.get(name)
        new_value = new.get(name)
        if name in {"is_official", "is_campaign", "is_low_signal"}:
            merged[name] = bool(old_value) or bool(new_value)
        elif name in {"views", "comments"}:
            numeric_values = [
                int(value)
                for value in (old_value, new_value)
                if isinstance(value, (int, float))
            ]
            merged[name] = max(numeric_values) if numeric_values else None
        elif name in {"title", "original_text", "content_scope", "source_note"}:
            old_text = _clean_text(old_value)
            new_text = _clean_text(new_value)
            merged[name] = new_value if len(new_text) >= len(old_text) else old_value
        elif name == "url":
            # QQ의 subc가 있는 직접 링크가 채널 재진입에 더 안정적입니다.
            merged[name] = new_value if len(_clean_text(new_value)) >= len(_clean_text(old_value)) else old_value
        elif new_value not in (None, ""):
            merged[name] = new_value

    return CollectedPost(**merged)


def _repair_official_flag(post: CollectedPost) -> CollectedPost:
    """이전 버전이 잘못 저장한 is_official 값을 읽는 시점에 바로잡습니다.

    캐시를 지우지 않아도 기존 누적 원문이 자동으로 교정됩니다.
    """
    if post.source != "QQ 공식 채널":
        return post
    if post.source_section == QQ_OFFICIAL_SECTION:
        post.is_official = True
        return post
    post.is_official = _is_qq_official_poster(None, post.author)
    return post


def _read_cache_file(path: Path) -> tuple[dict[str, CollectedPost], str]:
    if not path.exists():
        return {}, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("posts", []) if isinstance(payload, dict) else []
        result: dict[str, CollectedPost] = {}
        for row in rows:
            post = _post_from_dict(row)
            if post is None:
                continue
            result[_post_cache_key(post)] = _repair_official_flag(post)
        return result, ""
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        return {}, f"누적 원문을 읽지 못해 이번 실행 결과만 사용({type(error).__name__})"


def _load_collection_cache() -> tuple[dict[str, CollectedPost], str]:
    """공유 기준 캐시와 개인 캐시를 합칩니다. 저장은 개인 캐시에만 합니다."""
    shared, shared_error = _read_cache_file(SHARED_CACHE_PATH)
    local, local_error = _read_cache_file(CACHE_PATH)

    merged: dict[str, CollectedPost] = dict(shared)
    for key, post in local.items():
        existing = merged.get(key)
        merged[key] = _merge_post(existing, post) if existing else post

    message = local_error or shared_error
    return merged, message


def _save_collection_cache(posts_by_key: dict[str, CollectedPost]) -> str:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "posts": [
                post.to_dict()
                for _, post in sorted(posts_by_key.items(), key=lambda item: item[0])
            ],
        }
        temp_path = CACHE_PATH.with_name(f".{CACHE_PATH.name}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(CACHE_PATH)
        return ""
    except OSError as error:
        return f"누적 원문을 저장하지 못함({type(error).__name__})"


def _parse_iso_cn_date(value: object) -> str:
    """QQ JSON-LD의 datePublished를 중국 현지 게시일로 변환합니다."""
    raw = _clean_text(value)
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(CHINA_TZ)
    return parsed.date().isoformat()


def _parse_iso_calendar_date(value: object) -> str:
    """출처 상세 화면이 표시하는 ISO 날짜의 달력 날짜를 그대로 보존합니다."""
    raw = _clean_text(value)
    match = re.match(r"(20\d{2})-(\d{2})-(\d{2})", raw)
    if not match:
        return ""
    try:
        return date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        ).isoformat()
    except ValueError:
        return ""


def _parse_qq_json_ld(value: object) -> dict[str, dict]:
    """QQ 공개 페이지의 실제 게시일·섹션·제목 메타데이터를 읽습니다."""
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}

    result: dict[str, dict] = {}
    for graph in payload.get("@graph", []):
        if not isinstance(graph, dict):
            continue
        section = _clean_text(graph.get("name"))
        for entry in graph.get("itemListElement", []):
            item = entry.get("item") if isinstance(entry, dict) else None
            if not isinstance(item, dict):
                continue
            key = _qq_post_key(item.get("url"))
            if not key:
                continue
            author = item.get("author") or {}
            result[key] = {
                "url": _clean_text(item.get("url")),
                "headline": _clean_text(item.get("headline")),
                "description": _clean_text(item.get("description")),
                "published_at": _parse_iso_cn_date(item.get("datePublished")),
                "date_published_raw": _clean_text(item.get("datePublished")),
                "author": _clean_text(author.get("name") if isinstance(author, dict) else ""),
                "section": section,
            }
    return result


_NUXT_REF_WRAPPERS = {
    "ShallowReactive",
    "Reactive",
    "Ref",
    "ShallowRef",
    "Readonly",
    "ShallowReadonly",
    "EmptyRef",
}


def _decode_nuxt_payload(value: object) -> object:
    """Nuxt가 공개 HTML에 넣은 devalue 평탄화 JSON을 읽습니다.

    QQ가 게시판 ID를 일반 링크로 노출하지 않아도 공개 첫 화면의
    ``commonGuildStore``에는 탭 이름·게시판 ID가 들어 있습니다. 앱 번들이나
    비공개 API를 역호출하지 않고, 현재 페이지가 직접 제공한 값만 사용합니다.
    """
    raw_text = str(value or "").strip()
    if not raw_text:
        return {}
    try:
        flat = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(flat, list) or not flat:
        return {}

    memo: dict[int, object] = {}

    def resolve_reference(index: int) -> object:
        if index < 0:
            return {
                -1: None,
                -2: math.nan,
                -3: math.inf,
                -4: -math.inf,
                -5: -0.0,
            }.get(index)
        if index >= len(flat):
            return None
        if index in memo:
            return memo[index]

        node = flat[index]
        if isinstance(node, list):
            if node and isinstance(node[0], str) and node[0] in _NUXT_REF_WRAPPERS:
                result = resolve_value(node[1]) if len(node) >= 2 else None
                memo[index] = result
                return result
            if node and node[0] == "Date":
                result = node[1] if len(node) >= 2 else ""
                memo[index] = result
                return result
            if node and node[0] == "Map":
                result_map: dict[object, object] = {}
                memo[index] = result_map
                for position in range(1, len(node) - 1, 2):
                    key = resolve_value(node[position])
                    item = resolve_value(node[position + 1])
                    try:
                        result_map[key] = item
                    except TypeError:
                        continue
                return result_map
            if node and node[0] == "Set":
                result_list: list[object] = []
                memo[index] = result_list
                result_list.extend(resolve_value(item) for item in node[1:])
                return result_list

            result_list = []
            memo[index] = result_list
            result_list.extend(resolve_value(item) for item in node)
            return result_list

        if isinstance(node, dict):
            result_dict: dict[str, object] = {}
            memo[index] = result_dict
            for key, item in node.items():
                result_dict[str(key)] = resolve_value(item)
            return result_dict

        memo[index] = node
        return node

    def resolve_value(item: object) -> object:
        if isinstance(item, bool):
            return item
        if isinstance(item, int):
            return resolve_reference(item)
        if isinstance(item, list):
            if item and isinstance(item[0], str) and item[0] in _NUXT_REF_WRAPPERS:
                return resolve_value(item[1]) if len(item) >= 2 else None
            return [resolve_value(part) for part in item]
        if isinstance(item, dict):
            return {str(key): resolve_value(part) for key, part in item.items()}
        return item

    return resolve_value(flat[0])


def _qq_board_routes_from_payloads(payloads: list[str]) -> list[dict]:
    """공개 Nuxt 데이터에서 게시판 이름·ID를 찾아 안정적인 진입 경로를 만듭니다."""
    discovered_by_section: dict[str, dict] = {}
    for payload_text in sorted(payloads, key=len, reverse=True):
        decoded = _decode_nuxt_payload(payload_text)
        if not isinstance(decoded, dict):
            continue
        pinia = decoded.get("pinia")
        if not isinstance(pinia, dict):
            continue
        common_store = pinia.get("commonGuildStore")
        if not isinstance(common_store, dict):
            continue

        hidden_id = _clean_text(common_store.get("hiddenChannelId"))
        if hidden_id.isdigit():
            discovered_by_section.setdefault(
                QQ_HIDDEN_SECTION,
                {
                    "channel_id": hidden_id,
                    "section": QQ_HIDDEN_SECTION,
                    "discovery": "page_payload",
                },
            )

        tab_info = common_store.get("tabInfoList")
        if isinstance(tab_info, list):
            for item in tab_info:
                if not isinstance(item, dict):
                    continue
                channel_id = _clean_text(item.get("channel_id"))
                section = _clean_text(item.get("name"))
                if not channel_id.isdigit() or not section:
                    continue
                discovered_by_section.setdefault(
                    section,
                    {
                        "channel_id": channel_id,
                        "section": section,
                        "discovery": "page_payload",
                    },
                )
        if QQ_MAIN_SECTION in discovered_by_section:
            break

    # 페이지 구조 변경으로 일부 이름을 읽지 못한 경우에만 알려진 공개 경로를 채웁니다.
    for channel_id, section in QQ_FALLBACK_BOARD_ROUTES:
        discovered_by_section.setdefault(
            section,
            {
                "channel_id": channel_id,
                "section": section,
                "discovery": "fallback",
            },
        )

    # 이전 버전은 이름이 정확히 일치하는 4개 게시판만 사용하고 나머지는
    # 버렸습니다. 채널에 다른 게시판이 있어도 수집 대상에서 빠졌습니다.
    # 이제는 발견한 게시판을 모두 대상으로 삼고, 이벤트성 게시판만 제외합니다.
    priority = {
        QQ_MAIN_SECTION: 0,
        QQ_HIDDEN_SECTION: 1,
        QQ_OFFICIAL_SECTION: 2,
    }
    routes: list[dict] = []
    for section, route in sorted(
        discovered_by_section.items(),
        key=lambda item: (priority.get(item[0], 3), item[0]),
    ):
        # 全部 탭의 channel_id 는 숫자가 아니라 'hot' 입니다.
        if not route or not str(route.get("channel_id", "")).strip():
            continue
        routes.append(
            {
                **route,
                "url": f"{QQ_CHANNEL_URL}?subc={route['channel_id']}",
                "excluded_section": section in QQ_EVENT_SECTIONS,
            }
        )
    return routes


def _parse_qq_detail_json_ld(value: object) -> dict:
    """QQ 개별 글의 정확한 게시일·전체 텍스트를 구조화 데이터에서 읽습니다."""
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}

    queue: list[object] = payload if isinstance(payload, list) else [payload]
    while queue:
        item = queue.pop(0)
        if not isinstance(item, dict):
            continue
        graph = item.get("@graph")
        if isinstance(graph, list):
            queue.extend(graph)
        item_type = item.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if not any(
            value in {"DiscussionForumPosting", "SocialMediaPosting"}
            for value in types
        ):
            continue
        author = item.get("author") or {}
        text = _clean_text(item.get("text") or item.get("description"))
        return {
            "url": _clean_text(item.get("url") or item.get("mainEntityOfPage")),
            "headline": _clean_text(item.get("headline")),
            "description": text,
            "published_at": _parse_iso_cn_date(item.get("datePublished")),
            "date_published_raw": _clean_text(item.get("datePublished")),
            "author": _clean_text(
                author.get("name") if isinstance(author, dict) else ""
            ),
        }
    return {}


def _parse_qq_detail_html(value: object) -> dict:
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for node in soup.select('script[type="application/ld+json"]'):
        metadata = _parse_qq_detail_json_ld(node.get_text(strip=True))
        if metadata:
            return metadata
    return {}


def _parse_qq_epoch_date(value: object) -> str:
    raw = _clean_text(value)
    if not raw:
        return ""
    try:
        timestamp = int(raw[:10])
        return datetime.fromtimestamp(timestamp, CHINA_TZ).date().isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return ""


def _extract_qq_feed_text(value: object) -> str:
    """QQ 피드 객체의 중첩 title·contents에서 사람이 작성한 텍스트를 합칩니다."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return

        text_content = node.get("text_content")
        if isinstance(text_content, dict):
            text = _clean_text(text_content.get("text"))
            if text:
                found.append(text)
        direct_text = node.get("text")
        if isinstance(direct_text, str):
            text = _clean_text(direct_text)
            if text:
                found.append(text)

        for key, item in node.items():
            if key in {"text", "text_content"}:
                continue
            if key in {
                "title",
                "contents",
                "content_with_style",
                "summary",
                "task_content",
            }:
                walk(item)

    if isinstance(value, dict):
        walk(value.get("title"))
        walk(value.get("contents"))
        walk(value.get("content_with_style"))
        walk(value.get("summary"))
    else:
        walk(value)

    result: list[str] = []
    for text in found:
        if not text or any(text in existing for existing in result):
            continue
        result = [existing for existing in result if existing not in text]
        result.append(text)
    return _clean_text(" ".join(result))


def _find_qq_feed_batches(value: object) -> list[dict]:
    """Nuxt 초기 데이터와 후속 공개 JSON 응답에서 vecFeed 묶음을 찾습니다."""
    batches: list[dict] = []
    seen: set[int] = set()

    def walk(node: object) -> None:
        if not isinstance(node, (dict, list)):
            return
        identity = id(node)
        if identity in seen:
            return
        seen.add(identity)
        if isinstance(node, list):
            for item in node:
                walk(item)
            return

        feeds = node.get("vecFeed")
        if isinstance(feeds, list):
            batches.append(
                {
                    "feeds": feeds,
                    "feed_attach_info": _clean_text(
                        node.get("feedAttchInfo") or node.get("feedAttachInfo")
                    ),
                    "is_finish": node.get("isFinish"),
                    "trace_id": _clean_text(node.get("trace_id")),
                }
            )
        for item in node.values():
            walk(item)

    walk(value)
    return batches


def _qq_post_from_feed_item(
    item: object,
    section: str,
    channel_id: str,
) -> CollectedPost | None:
    if not isinstance(item, dict):
        return None
    feed_id = _clean_text(item.get("id") or item.get("feed_id"))
    if not feed_id:
        return None

    channel_info = item.get("channelInfo") or {}
    channel_sign = (
        channel_info.get("sign")
        if isinstance(channel_info, dict)
        else {}
    )
    feed_channel_id = _clean_text(
        channel_sign.get("channel_id")
        if isinstance(channel_sign, dict)
        else ""
    )
    resolved_channel_id = feed_channel_id or channel_id
    url = f"{QQ_CHANNEL_URL}/post/{feed_id}"
    if resolved_channel_id:
        url += f"?subc={resolved_channel_id}"

    original_text = _extract_qq_feed_text(item)
    if not original_text:
        original_text = _clean_text(
            item.get("feed_abstract")
            or item.get("subtitle")
            or item.get("discussion_num")
        )
    if not original_text and isinstance(item.get("images"), list) and item.get("images"):
        original_text = "分享图片"
    if not original_text and isinstance(item.get("videos"), list) and item.get("videos"):
        original_text = "分享视频"

    poster = item.get("poster") or {}
    author = _clean_text(
        poster.get("nick") if isinstance(poster, dict) else ""
    )
    published_at = _parse_qq_epoch_date(
        item.get("createTime") or item.get("create_time")
    )
    is_official = section == QQ_OFFICIAL_SECTION or _is_qq_official_poster(
        poster, author
    )
    visitor_info = item.get("visitorInfo") or {}
    view_count = (
        visitor_info.get("viewCount") if isinstance(visitor_info, dict) else None
    )

    return CollectedPost(
        source="QQ 공식 채널",
        title=_excerpt_title(original_text),
        original_text=original_text,
        url=_canonical_url(url),
        published_at=published_at,
        author=author,
        views=_parse_count(view_count),
        comments=_parse_count(item.get("commentCount")),
        content_scope="게시글 피드 텍스트 전체(이미지·댓글 제외)",
        source_note="QQ 날짜순 공개 게시판 · 원본 createTime 기준",
        source_section=section,
        published_at_source=(
            "QQ feed createTime (Asia/Shanghai)" if published_at else "미확인"
        ),
        is_official=is_official,
        is_campaign=_is_qq_campaign(original_text, section, author),
        is_low_signal=_is_qq_low_signal(original_text),
    )


# 관리자·공식 계정 판정에 쓰는 표식입니다.
# 실제 데이터 확인 결과 공식 계정은 작성자명에 【值班管理9-24】처럼
# 관리 표식이 들어갑니다.
QQ_OFFICIAL_AUTHOR_MARKERS = (
    "管理", "官方", "客服", "策划", "运营", "版主", "值班",
    "小助手", "小秘书", "GM",
)


def _qq_manage_tag_text(poster: object) -> str:
    """manage_tag 안에 실제로 들어 있는 문자열만 뽑아냅니다."""
    if not isinstance(poster, dict):
        return ""
    raw = poster.get("manage_tag")
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        parts = [str(v).strip() for v in raw.values() if isinstance(v, str)]
        return " ".join(part for part in parts if part)
    if isinstance(raw, (list, tuple)):
        parts = [str(v).strip() for v in raw if isinstance(v, str)]
        return " ".join(part for part in parts if part)
    return ""


def _is_qq_official_poster(poster: object = None, author: str = "") -> bool:
    """관리자·공식 계정 여부를 보수적으로 판정합니다.

    이전 코드는 bool(poster.get("manage_tag")) 만으로 판정했습니다.
    QQ 피드 API는 관리자가 아닌 계정에도 manage_tag 를 빈 객체로 내려주는데,
    파이썬에서 dict/list 는 비어 있지 않으면 항상 참이라 일반 유저 글이
    전부 공식 글로 분류됐습니다. 그 결과 자연 발생 유저 글이 0건이 됐습니다.
    """
    haystack = f"{_qq_manage_tag_text(poster)} {_clean_text(author)}"
    return any(marker in haystack for marker in QQ_OFFICIAL_AUTHOR_MARKERS)


def _qq_posts_from_payload(
    payload: object,
    section: str,
    channel_id: str,
) -> tuple[list[CollectedPost], list[dict]]:
    decoded = (
        _decode_nuxt_payload(payload)
        if isinstance(payload, str)
        else payload
    )
    posts_by_key: dict[str, CollectedPost] = {}
    batches = _find_qq_feed_batches(decoded)
    for batch in batches:
        for item in batch.get("feeds", []):
            post = _qq_post_from_feed_item(item, section, channel_id)
            if post is None:
                continue
            key = _qq_post_key(post.url)
            existing = posts_by_key.get(key)
            posts_by_key[key] = _merge_post(existing, post) if existing else post
    return list(posts_by_key.values()), batches


def _is_qq_campaign(text: str, section: str = "", author: str = "") -> bool:
    normalized = _clean_text(f"{text} {author}").casefold()
    if section in QQ_EVENT_SECTIONS:
        return True
    if "锦鲤传递官" in author:
        return True
    return any(marker in normalized for marker in QQ_CAMPAIGN_MARKERS)


def _is_qq_low_signal(text: str) -> bool:
    normalized = _clean_text(text)
    if normalized in QQ_LOW_SIGNAL_EXACT:
        return True
    # 해시태그·구두점만 남은 게시글은 동향 근거로 쓰지 않습니다.
    without_tags = re.sub(r"#[^\s#]+", "", normalized)
    without_noise = re.sub(r"[\W_]+", "", without_tags, flags=re.UNICODE)
    return len(without_noise) < 2


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,ko;q=0.8,en;q=0.7",
            "Referer": "https://www.baidu.com/",
        }
    )
    return session


SESSION = _make_session()


def _get_response(url: str, **kwargs) -> requests.Response:
    response = SESSION.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
    response.raise_for_status()
    return response


def _get_soup(url: str, **kwargs) -> BeautifulSoup:
    response = _get_response(url, **kwargs)
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding
    return BeautifulSoup(response.text, "html.parser")


def _parse_count(value: object) -> int | None:
    text = _clean_text(value).replace(",", "")
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(万|億|亿|千)?", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    multiplier = {"千": 1_000, "万": 10_000, "億": 100_000_000, "亿": 100_000_000}.get(unit, 1)
    return int(number * multiplier)


def _today_cn() -> date:
    return datetime.now(CHINA_TZ).date()


def _parse_cn_date(value: object, reference_date: date | None = None) -> str:
    """중국 사이트의 절대/상대 날짜를 YYYY-MM-DD로 통일합니다."""
    text = _clean_text(value)
    if not text:
        return ""
    reference = reference_date or _today_cn()

    match = re.search(r"(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return ""

    match = re.search(r"(?<!\d)(\d{1,2})[./月-](\d{1,2})(?:日)?(?!\d)", text)
    if match:
        try:
            parsed = date(reference.year, int(match.group(1)), int(match.group(2)))
            # 연말/연초에 전년도 글이 다음 해로 해석되는 것을 막습니다.
            if parsed > reference + timedelta(days=7):
                parsed = date(reference.year - 1, parsed.month, parsed.day)
            return parsed.isoformat()
        except ValueError:
            return ""

    if "前天" in text:
        return (reference - timedelta(days=2)).isoformat()
    if "昨天" in text or "昨日" in text:
        return (reference - timedelta(days=1)).isoformat()
    if "今天" in text or "今日" in text or "刚刚" in text or "分钟前" in text or "小时前" in text:
        return reference.isoformat()

    match = re.search(r"(\d+)\s*天前", text)
    if match:
        return (reference - timedelta(days=int(match.group(1)))).isoformat()

    return ""


def _in_period(iso_date: str, start_date: date, end_date: date) -> bool:
    try:
        parsed = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return False
    return start_date <= parsed <= end_date


def _is_meaningful_text(value: str) -> bool:
    text = _clean_text(value)
    if len(text) < 3:
        return False
    return not bool(re.fullmatch(r"[\d\W_]+", text))


def _excerpt_title(value: str, max_length: int = 72) -> str:
    """제목이 없는 커뮤니티 글에 쓸 짧은 표시 제목을 만듭니다."""
    text = _clean_text(value)
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _launch_installed_chromium(playwright):
    """별도 브라우저 다운로드 없이 PC에 설치된 Chrome/Edge를 사용합니다."""
    errors: list[str] = []
    for channel, label in (("chrome", "Chrome"), ("msedge", "Edge")):
        try:
            return playwright.chromium.launch(channel=channel, headless=True), label
        except Exception as error:  # 브라우저 설치·회사 정책 차이를 한 메시지로 합칩니다.
            errors.append(f"{label}: {type(error).__name__}")

    # 사용자가 이전에 Playwright 브라우저를 설치한 환경도 지원합니다.
    try:
        return playwright.chromium.launch(headless=True), "Chromium"
    except Exception as error:
        errors.append(f"Chromium: {type(error).__name__}")

    raise SourceUnavailable(
        "Chrome/Edge 자동 수집 실행 실패 (" + ", ".join(errors) + ")"
    )


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SourceUnavailable(
            "브라우저 수집 모듈 미설치 — requirements.txt를 다시 설치해 주세요"
        ) from error
    return sync_playwright


def _is_game_relevant(value: str) -> bool:
    normalized = _clean_text(value).casefold().replace("：", ":")
    return any(alias.casefold().replace("：", ":") in normalized for alias in GAME_ALIASES)


def _is_bilibili_roundup(title: str) -> bool:
    """여러 게임을 한꺼번에 나열한 검색 잡음을 보수적으로 거릅니다."""
    text = _clean_text(title)
    if not text:
        return False

    has_roundup_marker = any(marker in text for marker in BILIBILI_ROUNDUP_MARKERS)
    list_segments = [part for part in re.split(r"[,，、|｜/；;]", text) if _clean_text(part)]
    starts_with_target = any(text.casefold().startswith(alias.casefold()) for alias in GAME_ALIASES)
    return has_roundup_marker and (len(list_segments) >= 3 or not starts_with_target)


def _dedupe_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        clean = _clean_text(line)
        if clean and (not result or clean != result[-1]):
            result.append(clean)
    return result


TAPTAP_STOP_LINES = {
    "下载手机 APP",
    "下载手机APP",
    "下载 TapTap",
    "下载TapTap",
    "扫码下载",
    "Android APK 下载",
    "App Store 下载",
    "下载 PC 版",
    "TapTap PC 版",
    "Windows 版下载",
    "Mac 版下载",
    "前往论坛",
    "相关推荐",
    "热门游戏论坛",
}


def _parse_taptap_json_ld(soup: BeautifulSoup) -> dict:
    """TapTap 상세 글의 구조화된 실제 게시 시각과 작성자를 읽습니다."""
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.get_text(strip=True) or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "NewsArticle":
                continue
            author = item.get("author") or {}
            return {
                # TapTap 상세 화면은 datePublished의 달력 날짜를 그대로 표시하므로
                # 타임존 재변환 없이 같은 날짜를 보존합니다.
                "published_at": _parse_iso_calendar_date(item.get("datePublished")),
                "date_published_raw": _clean_text(item.get("datePublished")),
                "headline": _clean_text(item.get("headline")),
                "description": _clean_text(item.get("description")),
                "author": _clean_text(
                    author.get("name") if isinstance(author, dict) else ""
                ),
            }
    return {}


def _parse_taptap_detail(
    soup: BeautifulSoup,
    url: str,
    fallback_title: str,
    reference_date: date | None = None,
) -> CollectedPost:
    structured = _parse_taptap_json_ld(soup)
    h1 = soup.select_one("h1")
    og_title = soup.select_one('meta[property="og:title"]')
    title = _clean_text(
        (h1.get_text(" ", strip=True) if h1 else "")
        or (og_title.get("content") if og_title else "")
        or fallback_title
    )
    title = re.sub(r"\s*[-–—]\s*天堂2.*$", "", title).strip()

    root = soup.body or soup
    lines = _dedupe_lines(root.get_text("\n", strip=True).splitlines())

    meta_index = -1
    meta_line = ""
    for index, line in enumerate(lines):
        if "浏览" in line:
            meta_index = index
            meta_line = line
            break

    published_at = _clean_text(structured.get("published_at"))
    if not published_at:
        published_at = _parse_cn_date(meta_line, reference_date)
    if not published_at and meta_index >= 0:
        # 실제 TapTap HTML은 '昨天 / 06:40 / 13 浏览' 또는
        # '更新时间 / 06/23 / 729 浏览'처럼 날짜와 조회 수를 나눠 둡니다.
        for nearby_line in reversed(lines[max(0, meta_index - 5) : meta_index]):
            published_at = _parse_cn_date(nearby_line, reference_date)
            if published_at:
                break
    views_match = re.search(r"([\d,.]+\s*[万千]?)\s*浏览", meta_line)
    views = _parse_count(views_match.group(1)) if views_match else None

    body_lines: list[str] = []
    if meta_index >= 0:
        for line in lines[meta_index + 1 :]:
            if line in TAPTAP_STOP_LINES:
                break
            if body_lines and (re.fullmatch(r"\d+", line) or line == "点赞"):
                break
            if body_lines and line in {"天堂2：盟约", "天堂2:盟约"}:
                break
            if line in {"关注", title}:
                continue
            body_lines.append(line)

    original_text = _clean_text("\n".join(body_lines))
    original_text = re.sub(r"\s*(猜你想搜|相关搜索).*$", "", original_text).strip()
    if not _is_meaningful_text(original_text):
        description = soup.select_one('meta[name="description"]')
        candidate = _clean_text(description.get("content")) if description else ""
        if candidate and candidate != title and "TapTap" not in candidate:
            original_text = candidate

    author = _clean_text(structured.get("author"))
    if meta_index > 0:
        for line in reversed(lines[max(0, meta_index - 8) : meta_index]):
            if author:
                break
            if line not in {"关注", title} and len(line) <= 40 and not re.search(r"浏览|下载|TapTap", line):
                author = line
                break

    has_body = _is_meaningful_text(original_text)
    return CollectedPost(
        source="TapTap",
        title=title,
        original_text=original_text if has_body else title,
        url=_canonical_url(url),
        published_at=published_at,
        author=author,
        views=views,
        comments=None,
        content_scope="게시글 본문 전체" if has_body else "제목만(본문 미확인)",
        source_note="TapTap 개별 게시글 공개 페이지",
        published_at_source=(
            "TapTap JSON-LD datePublished"
            if structured.get("published_at")
            else "TapTap 개별 글 표시 날짜"
        ),
    )


def _collect_taptap_static(limit: int, start_date: date, end_date: date) -> list[CollectedPost]:
    soup = _get_soup(TAPTAP_TOPIC_URL, headers={"Referer": TAPTAP_TOPIC_URL})
    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    for link in soup.select('a[href*="/moment/"]'):
        url = _canonical_url(_absolute_url(TAPTAP_TOPIC_URL, link.get("href", "")))
        title = _clean_text(link.get("title") or link.get_text(" ", strip=True))
        if not url or url in seen:
            continue
        seen.add(url)
        links.append((url, title))

    if not links:
        raise SourceUnavailable("게시글 링크가 공개 HTML에 없음")

    posts: list[CollectedPost] = []
    candidate_limit = min(max(limit * 4, 30), 120)
    for url, fallback_title in links[:candidate_limit]:
        try:
            detail_soup = _get_soup(url, headers={"Referer": TAPTAP_TOPIC_URL})
            post = _parse_taptap_detail(detail_soup, url, fallback_title)
            posts.append(post)
        except requests.RequestException:
            continue
        if len([item for item in posts if _in_period(item.published_at, start_date, end_date)]) >= limit:
            break
        time.sleep(REQUEST_DELAY)
    return posts


def _collect_taptap_links_browser(
    limit: int,
    start_date: date,
    end_date: date,
) -> tuple[list[tuple[str, str, str]], dict]:
    """TapTap 최신 목록을 조회 시작일 경계까지 불러와 링크를 확보합니다."""
    _ = limit  # 결과 상한은 수집 범위를 바꾸지 않습니다.
    sync_playwright = _load_playwright()
    candidates: dict[str, tuple[str, str, str]] = {}
    rounds = 0
    stale_rounds = 0
    boundary_rounds = 0
    reported_total = 0
    taptap_scroll_targets = 0
    oldest_listed_date = ""
    stop_reason = "max_rounds"
    boundary_date = start_date - timedelta(days=1)

    with sync_playwright() as playwright:
        browser, browser_label = _launch_installed_chromium(playwright)
        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 2400},
            extra_http_headers={
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        page = context.new_page()
        page.set_default_timeout(18_000)
        try:
            page.goto(
                TAPTAP_TOPIC_URL,
                wait_until="domcontentloaded",
                timeout=35_000,
            )
            # TapTap이 목록 카드 클래스명을 moment-feed-list-item -> moment-list-item
            # 으로 바꾼 적이 있습니다(2026-09 확인). 한쪽만 보면 어느 날 갑자기
            # 수집이 0건이 되므로 둘 다 받아 둡니다.
            try:
                page.locator(TAPTAP_ITEM_SELECTOR).first.wait_for(
                    state="attached",
                    timeout=18_000,
                )
            except Exception as error:
                # 목록을 못 읽으면 정적 HTML 폴백으로 넘어가야 합니다.
                raise SourceUnavailable(
                    "TapTap 목록 카드를 찾지 못했습니다(페이지 구조 변경 가능성)."
                ) from error
            page.wait_for_timeout(800)

            body_text = _clean_text(page.locator("body").inner_text())
            total_match = re.search(r"([\d,]+)\s*帖子", body_text)
            if total_match:
                reported_total = int(total_match.group(1).replace(",", ""))

            for round_index in range(TAPTAP_MAX_SCROLL_ROUNDS):
                rounds = round_index + 1
                items = page.locator(TAPTAP_ITEM_SELECTOR).evaluate_all(
                    """
                    nodes => nodes.map(node => {
                        const link = node.querySelector('a[href*="/moment/"]');
                        const time = node.querySelector('.moment-card__time');
                        const title = node.querySelector(
                            '.moment-article__summary--title'
                        );
                        const body = node.querySelector(
                            '.moment-article__summary--content'
                        );
                        return {
                            url: link?.href || '',
                            title: (title?.innerText || body?.innerText || '').trim(),
                            date: (time?.getAttribute('title') || time?.innerText || '').trim()
                        };
                    })
                    """
                )

                before_count = len(candidates)
                current_list_dates: list[str] = []
                for item in items:
                    url = _canonical_url(_clean_text(item.get("url")))
                    if not url:
                        continue
                    listed_date = _parse_cn_date(item.get("date"))
                    candidates[url] = (
                        url,
                        _clean_text(item.get("title")),
                        listed_date,
                    )
                    if listed_date:
                        current_list_dates.append(listed_date)
                new_count = len(candidates) - before_count
                stale_rounds = stale_rounds + 1 if new_count == 0 else 0

                listed_dates = [
                    listed_date
                    for _, _, listed_date in candidates.values()
                    if listed_date
                ]
                if listed_dates:
                    oldest_listed_date = min(listed_dates)
                    # 상단 고정 공지 한 건 때문에 기간 경계에 도달했다고 오판하지
                    # 않도록 현재 목록의 맨 아래 날짜 묶음 전체가 경계를 지났는지 봅니다.
                    tail_dates = current_list_dates[-3:]
                    if (
                        len(tail_dates) >= 3
                        and max(tail_dates) <= boundary_date.isoformat()
                    ):
                        boundary_rounds += 1
                    else:
                        boundary_rounds = 0

                if reported_total and len(candidates) >= reported_total:
                    stop_reason = "reported_total_reached"
                    break
                if boundary_rounds >= TAPTAP_BOUNDARY_CONFIRM_ROUNDS:
                    stop_reason = "period_boundary_reached"
                    break
                if stale_rounds >= TAPTAP_STALE_ROUNDS:
                    stop_reason = "stable_list"
                    break

                # TapTap도 내부 스크롤 컨테이너를 사용합니다.
                try:
                    _scrolled = page.evaluate(QQ_SCROLL_SCRIPT)
                    taptap_scroll_targets = max(
                        taptap_scroll_targets,
                        int((_scrolled or {}).get("targets") or 0)
                        if isinstance(_scrolled, dict)
                        else int(_scrolled or 0),
                    )
                except Exception:
                    pass
                try:
                    page.mouse.move(700, 500)
                    page.mouse.wheel(0, 2400)
                except Exception:
                    pass
                page.wait_for_timeout(1_100)
        finally:
            context.close()
            browser.close()

    links = list(candidates.values())
    coverage_complete = bool(reported_total and len(links) >= reported_total)
    period_complete = coverage_complete or stop_reason == "period_boundary_reached"
    if coverage_complete:
        coverage_state = "complete_public"
        coverage_message = f"공개 포럼 전체 목록 {len(links):,}/{reported_total:,}건 확인"
    elif period_complete:
        coverage_state = "period_complete_public"
        coverage_message = (
            f"조회기간 경계까지 공개 목록 {len(links):,}건 확인"
            + (f" · 최저 날짜 {oldest_listed_date}" if oldest_listed_date else "")
        )
    else:
        coverage_state = "partial"
        coverage_message = (
            f"공개 포럼 목록 {len(links):,}/{reported_total:,}건 확인"
            if reported_total
            else f"공개 포럼 목록 {len(links):,}건 확인 · 전체 게시글 수 미확인"
        )
    meta = {
        "coverage_state": coverage_state,
        "coverage_message": coverage_message,
        "collection_rounds": rounds,
        "reported_total_posts": reported_total,
        "oldest_listed_date": oldest_listed_date,
        "period_boundary_reached": period_complete,
        "sample_stop_reason": stop_reason,
        "browser_label": browser_label,
    }
    return links, meta


def _collect_taptap_reviews_browser(
    start_date: date,
    end_date: date,
) -> tuple[list[CollectedPost], dict]:
    """TapTap 리뷰(评价) 탭을 수집합니다.

    게시판(topic)과 달리 리뷰는 목록에 본문이 통째로 들어 있어 상세 요청이
    필요 없습니다. 리뷰는 유저가 직접 남긴 평가라 여론 파악에 가치가 큽니다.
    """
    sync_playwright = _load_playwright()
    posts_by_url: dict[str, CollectedPost] = {}
    rounds = 0
    stale_rounds = 0
    stop_reason = "max_rounds"
    boundary_date = (start_date - timedelta(days=1)).isoformat()
    oldest_seen = ""

    with sync_playwright() as playwright:
        browser, browser_label = _launch_installed_chromium(playwright)
        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 2400},
        )
        page = context.new_page()
        page.set_default_timeout(18_000)
        try:
            page.goto(
                TAPTAP_REVIEW_URL,
                wait_until="domcontentloaded",
                timeout=35_000,
            )
            try:
                page.locator(TAPTAP_REVIEW_ITEM_SELECTOR).first.wait_for(
                    state="attached",
                    timeout=18_000,
                )
            except Exception as error:
                raise SourceUnavailable(
                    "TapTap 리뷰 목록을 찾지 못했습니다(페이지 구조 변경 가능성)."
                ) from error
            page.wait_for_timeout(800)

            for round_index in range(TAPTAP_REVIEW_MAX_ROUNDS):
                rounds = round_index + 1
                before = len(posts_by_url)
                try:
                    items = page.locator(TAPTAP_REVIEW_ITEM_SELECTOR).evaluate_all(
                        """
                        nodes => nodes.map(node => {
                            const link = node.querySelector('a[href*="/review/"]');
                            const author = node.querySelector(
                                '.review-item__author-name'
                            );
                            // 날짜는 operations-time 입니다.
                            // time-label 은 '9.7 시간 플레이' 같은 플레이시간이라
                            // 날짜로 읽으면 9월 7일로 잘못 해석됩니다.
                            const time = node.querySelector(
                                '.review-item__operations-time'
                            ) || node.querySelector('.review-item__updated-time');
                            const body = node.querySelector('.review-item__contents');
                            const device = node.querySelector('.review-item__device');
                            return {
                                url: link ? link.href : '',
                                author: (author?.innerText || '').trim(),
                                date_text: (time?.innerText || '').trim(),
                                text: (body?.innerText || '').trim(),
                                device: (device?.innerText || '').trim(),
                            };
                        })
                        """
                    )
                except Exception:
                    items = []

                for item in items:
                    url = _canonical_url(_clean_text(item.get("url")))
                    text = _clean_text(item.get("text"))
                    if not url or not text:
                        continue
                    published_at = _parse_cn_date(item.get("date_text"))
                    if published_at:
                        oldest_seen = (
                            published_at
                            if not oldest_seen
                            else min(oldest_seen, published_at)
                        )
                    post = CollectedPost(
                        source="TapTap",
                        title=_excerpt_title(text),
                        original_text=text,
                        url=url,
                        published_at=published_at,
                        author=_clean_text(item.get("author")),
                        content_scope="리뷰 본문 전체(평점·기기 정보 제외)",
                        source_note="TapTap 공개 리뷰(评价) 탭",
                        source_section="리뷰",
                        published_at_source=(
                            "TapTap 리뷰 표시 날짜" if published_at else "미확인"
                        ),
                    )
                    existing = posts_by_url.get(url)
                    posts_by_url[url] = (
                        _merge_post(existing, post) if existing else post
                    )

                if len(posts_by_url) == before:
                    stale_rounds += 1
                    if stale_rounds >= TAPTAP_STALE_ROUNDS:
                        stop_reason = "stable_list"
                        break
                else:
                    stale_rounds = 0

                if oldest_seen and oldest_seen < boundary_date:
                    stop_reason = "period_boundary_reached"
                    break

                try:
                    page.evaluate(QQ_SCROLL_SCRIPT)
                    page.mouse.move(700, 500)
                    page.mouse.wheel(0, 2400)
                except Exception:
                    pass
                page.wait_for_timeout(1_100)
        finally:
            context.close()
            browser.close()

    posts = list(posts_by_url.values())
    meta = {
        "review_rounds": rounds,
        "review_posts": len(posts),
        "review_oldest_date": oldest_seen,
        "review_stop_reason": stop_reason,
        "browser_label": browser_label,
    }
    return posts, meta


def _collect_taptap_batch(
    limit: int,
    start_date: date,
    end_date: date,
) -> tuple[list[CollectedPost], dict]:
    """TapTap 공개 목록 전체를 시도한 뒤 기간 내 상세 글을 검증합니다."""
    try:
        links, meta = _collect_taptap_links_browser(limit, start_date, end_date)
    except Exception:
        # 브라우저 경로가 어떤 이유로든 실패하면(구조 변경·타임아웃·브라우저 미설치)
        # 수집을 통째로 포기하지 말고 정적 HTML로라도 확보합니다.
        posts = _collect_taptap_static(limit, start_date, end_date)
        return posts, {
            "coverage_state": "partial",
            "coverage_message": "공개 정적 HTML에 노출된 글만 확인",
            "collection_rounds": 1,
        }

    # TapTap 목록과 상세의 표시 날짜가 자정 경계에서 하루 다를 수 있으므로
    # 목록 단계에서는 양쪽 하루를 더 확보하고, 최종 판정은 상세 datePublished로 합니다.
    prefilter_start = start_date - timedelta(days=1)
    prefilter_end = end_date + timedelta(days=1)
    period_links = [
        (url, title)
        for url, title, listed_date in links
        if listed_date and _in_period(listed_date, prefilter_start, prefilter_end)
    ]
    # 목록 날짜를 읽지 못한 글은 상세 페이지에서만 날짜를 판정할 수 있으므로 포함합니다.
    period_links.extend(
        (url, title)
        for url, title, listed_date in links
        if not listed_date
    )

    posts: list[CollectedPost] = []
    for url, fallback_title in period_links:
        try:
            detail_soup = _get_soup(url, headers={"Referer": TAPTAP_TOPIC_URL})
            posts.append(
                _parse_taptap_detail(detail_soup, url, fallback_title)
            )
        except requests.RequestException:
            continue
        time.sleep(REQUEST_DELAY)

    # 게시판 글에 더해 리뷰 탭도 함께 확보합니다. 한쪽이 실패해도 다른 쪽은 남깁니다.
    try:
        review_posts, review_meta = _collect_taptap_reviews_browser(
            start_date,
            end_date,
        )
    except Exception as error:
        review_posts, review_meta = [], {
            "review_stop_reason": f"failed:{type(error).__name__}",
        }
    posts.extend(review_posts)

    meta["period_links_seen"] = len(period_links)
    meta["review_posts"] = len(review_posts)
    meta["review_stop_reason"] = _clean_text(review_meta.get("review_stop_reason"))
    meta["review_oldest_date"] = _clean_text(review_meta.get("review_oldest_date"))
    return posts, meta


def _collect_taptap(limit: int, start_date: date, end_date: date) -> list[CollectedPost]:
    posts, _meta = _collect_taptap_batch(limit, start_date, end_date)
    return posts


def _api_json(url: str, params: dict) -> dict:
    response = _get_response(
        url,
        params=params,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://search.bilibili.com/",
            "Origin": "https://search.bilibili.com",
        },
    )
    try:
        payload = response.json()
    except json.JSONDecodeError as error:
        raise SourceUnavailable("JSON 응답이 아님") from error
    if int(payload.get("code", -1)) != 0:
        raise SourceUnavailable(f"공개 API 거절(code={payload.get('code')})")
    return payload


def _search_bilibili(page: int) -> list[dict]:
    last_error: Exception | None = None
    params = {
        "search_type": "video",
        "keyword": GAME_KEYWORD,
        "order": "pubdate",
        "page": page,
        "page_size": 50,
    }
    for endpoint in BILIBILI_SEARCH_ENDPOINTS:
        try:
            payload = _api_json(endpoint, params)
            return list((payload.get("data") or {}).get("result") or [])
        except (requests.RequestException, SourceUnavailable) as error:
            last_error = error
    raise SourceUnavailable(str(last_error or "검색 API 응답 없음"))


def _bilibili_detail(bvid: str) -> dict:
    try:
        payload = _api_json(BILIBILI_VIEW_ENDPOINT, {"bvid": bvid})
        return dict(payload.get("data") or {})
    except (requests.RequestException, SourceUnavailable):
        return {}


def _bilibili_post_from_result(result: dict) -> CollectedPost | None:
    bvid = _clean_text(result.get("bvid"))
    if not bvid:
        arcurl = _clean_text(result.get("arcurl"))
        match = re.search(r"/video/(BV[0-9A-Za-z]+)", arcurl)
        bvid = match.group(1) if match else ""
    if not bvid:
        return None

    detail = _bilibili_detail(bvid)
    title = _strip_html(detail.get("title") or result.get("title"))
    description = _clean_text(detail.get("desc") or _strip_html(result.get("description")))
    if description in {"-", "--", "无", "暂无简介", "暂无介绍"}:
        description = ""
    if not _is_meaningful_text(title):
        return None
    if not _is_game_relevant(f"{title} {description}"):
        return None

    timestamp = detail.get("pubdate") or result.get("pubdate") or result.get("senddate")
    try:
        published_at = datetime.fromtimestamp(int(timestamp), CHINA_TZ).date().isoformat()
    except (TypeError, ValueError, OSError):
        published_at = ""

    stat = detail.get("stat") or {}
    owner = detail.get("owner") or {}
    views = _parse_count(stat.get("view") or result.get("play"))
    comments = _parse_count(stat.get("reply") or result.get("review") or result.get("video_review"))
    author = _clean_text(owner.get("name") or result.get("author"))

    return CollectedPost(
        source="Bilibili",
        title=title,
        original_text=description or title,
        url=f"https://www.bilibili.com/video/{bvid}",
        published_at=published_at,
        author=author,
        views=views,
        comments=comments,
        content_scope="영상 설명 전체" if description else "영상 제목만(설명 없음)",
        source_note="Bilibili 공개 검색·영상 정보 API",
        published_at_source="Bilibili API pubdate",
    )


def _collect_bilibili(limit: int, start_date: date, end_date: date) -> list[CollectedPost]:
    posts: list[CollectedPost] = []
    seen: set[str] = set()

    for page in range(1, 7):
        results = _search_bilibili(page)
        if not results:
            break

        page_dates: list[str] = []
        for result in results:
            timestamp = result.get("pubdate") or result.get("senddate")
            try:
                quick_date = datetime.fromtimestamp(int(timestamp), CHINA_TZ).date().isoformat()
            except (TypeError, ValueError, OSError):
                quick_date = ""
            if quick_date:
                page_dates.append(quick_date)
                if not _in_period(quick_date, start_date, end_date):
                    continue

            post = _bilibili_post_from_result(result)
            if not post or post.url in seen:
                continue
            seen.add(post.url)
            posts.append(post)
            if len(posts) >= limit:
                return posts
            time.sleep(REQUEST_DELAY)

        # pubdate 순 정렬 결과가 시작일보다 모두 오래되면 다음 페이지를 요청하지 않습니다.
        if page_dates and max(page_dates) < start_date.isoformat():
            break
    return posts


def _parse_tieba_detail(soup: BeautifulSoup, url: str, fallback_title: str) -> CollectedPost:
    title_node = soup.select_one("h3.core_title_txt, .core_title_txt")
    title = _clean_text(
        (title_node.get("title") or title_node.get_text(" ", strip=True))
        if title_node
        else fallback_title
    )
    first_post = soup.select_one(".d_post_content")
    original_text = _clean_text(first_post.get_text(" ", strip=True)) if first_post else ""

    published_at = ""
    first_floor = soup.select_one("div.l_post")
    if first_floor:
        for node in first_floor.select("span[title], .post-tail-wrap span"):
            published_at = _parse_cn_date(node.get("title") or node.get_text(" ", strip=True))
            if published_at:
                break

    author_node = first_floor.select_one(".d_name, .p_author_name") if first_floor else None
    author = _clean_text(author_node.get_text(" ", strip=True)) if author_node else ""
    reply_node = soup.select_one("li.l_reply_num span.red")

    return CollectedPost(
        source="Baidu Tieba",
        title=title,
        original_text=original_text or title,
        url=_canonical_url(url),
        published_at=published_at,
        author=author,
        comments=_parse_count(reply_node.get_text()) if reply_node else None,
        content_scope="첫 게시글 본문 전체" if original_text else "제목만(본문 미확인)",
        source_note="Baidu Tieba 개별 게시글 공개 페이지",
    )


BAIDU_SECURITY_MARKERS = (
    "百度安全验证",
    "安全验证",
    "请输入验证码",
    "完成验证",
    "访问验证",
)


def _is_baidu_security_page(title: str, body_text: str) -> bool:
    combined = _clean_text(f"{title} {body_text}")
    return any(marker in combined for marker in BAIDU_SECURITY_MARKERS)


def _collect_tieba_browser(
    limit: int,
    start_date: date,
    end_date: date,
) -> list[CollectedPost]:
    """일반 요청이 막힐 때 설치된 Chrome/Edge로 공개 페이지를 한 번 확인합니다."""
    sync_playwright = _load_playwright()
    posts: list[CollectedPost] = []

    with sync_playwright() as playwright:
        browser, browser_label = _launch_installed_chromium(playwright)
        context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
        page = context.new_page()
        page.set_default_timeout(18_000)
        try:
            page.goto(TIEBA_URL, wait_until="domcontentloaded", timeout=35_000)
            page.wait_for_timeout(1_500)
            body_text = page.locator("body").inner_text(timeout=8_000)
            if _is_baidu_security_page(page.title(), body_text):
                raise SourceUnavailable(
                    "Baidu 안전 검증 화면이 표시되어 자동 수집 중단"
                )

            items = page.locator("li.j_thread_list a.j_th_tit").evaluate_all(
                """
                nodes => nodes.map(node => ({
                    title: (node.getAttribute('title') || node.innerText || '').trim(),
                    url: node.href || ''
                }))
                """
            )
            if not items:
                raise SourceUnavailable(
                    "Baidu 게시글 목록을 브라우저에서도 확인하지 못함"
                )

            candidate_limit = min(max(limit * 4, 30), 120)
            detail_page = context.new_page()
            detail_page.set_default_timeout(18_000)
            for item in items[:candidate_limit]:
                title = _clean_text(item.get("title"))
                url = _canonical_url(_clean_text(item.get("url")))
                if not title or not url:
                    continue
                try:
                    detail_page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    detail_page.wait_for_timeout(500)
                    detail_body = detail_page.locator("body").inner_text(timeout=8_000)
                    if _is_baidu_security_page(detail_page.title(), detail_body):
                        raise SourceUnavailable(
                            "Baidu 상세 글에서 안전 검증 화면이 표시되어 자동 수집 중단"
                        )
                    detail_soup = BeautifulSoup(detail_page.content(), "html.parser")
                    post = _parse_tieba_detail(detail_soup, url, title)
                    post.source_note = (
                        f"Baidu Tieba 공개 페이지 · {browser_label} 브라우저 렌더링"
                    )
                    posts.append(post)
                except SourceUnavailable:
                    raise
                except Exception:
                    continue

                if len(
                    [
                        post
                        for post in posts
                        if _in_period(post.published_at, start_date, end_date)
                    ]
                ) >= limit:
                    break
        finally:
            context.close()
            browser.close()

    return posts


def _collect_tieba(limit: int, start_date: date, end_date: date) -> list[CollectedPost]:
    try:
        soup = _get_soup(
            TIEBA_URL,
            headers={
                "Referer": "https://tieba.baidu.com/",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else ""
        if status in {403, 418, 429}:
            return _collect_tieba_browser(limit, start_date, end_date)
        raise SourceUnavailable(f"Baidu 접속 실패(HTTP {status})") from error

    items = soup.select("li.j_thread_list")
    if not items:
        return _collect_tieba_browser(limit, start_date, end_date)

    posts: list[CollectedPost] = []
    for item in items[: min(max(limit * 4, 30), 120)]:
        link = item.select_one("a.j_th_tit")
        if not link:
            continue
        title = _clean_text(link.get("title") or link.get_text(" ", strip=True))
        url = _absolute_url(TIEBA_URL, link.get("href", ""))
        try:
            detail = _get_soup(url, headers={"Referer": TIEBA_URL})
            post = _parse_tieba_detail(detail, url, title)
            posts.append(post)
        except requests.RequestException:
            continue
        if len([item for item in posts if _in_period(item.published_at, start_date, end_date)]) >= limit:
            break
        time.sleep(REQUEST_DELAY)
    return posts


def _qq_post_from_card(item: dict, metadata: dict[str, dict]) -> CollectedPost | None:
    """QQ 목록 카드와 JSON-LD 메타데이터를 하나의 검증 후보로 합칩니다."""
    url = _canonical_url(_clean_text(item.get("url")))
    key = _qq_post_key(url)
    meta = metadata.get(key, {})
    if not url or not key:
        return None

    section = _clean_text(meta.get("section"))
    author = _clean_text(item.get("author") or meta.get("author"))
    original_text = _clean_text(
        item.get("text") or meta.get("description") or meta.get("headline")
    )
    title = _excerpt_title(original_text or _clean_text(meta.get("headline")))
    is_official = (
        bool(item.get("is_official"))
        or section == QQ_OFFICIAL_SECTION
        or _is_qq_official_poster(None, author)
    )
    is_campaign = _is_qq_campaign(original_text, section, author)
    is_low_signal = _is_qq_low_signal(original_text)
    published_at = _clean_text(meta.get("published_at"))

    return CollectedPost(
        source="QQ 공식 채널",
        title=title,
        original_text=original_text,
        url=url,
        published_at=published_at,
        author=author,
        comments=_parse_count(item.get("comments")),
        content_scope="게시글 텍스트 전체(이미지·댓글 제외)",
        source_note=(
            "QQ 공개 웹 표본 · JSON-LD datePublished 기준"
            if published_at
            else "QQ 공개 웹 표본 · 실제 게시일 미확인"
        ),
        source_section=section,
        published_at_source=(
            "QQ JSON-LD datePublished" if published_at else "미확인"
        ),
        is_official=is_official,
        is_campaign=is_campaign,
        is_low_signal=is_low_signal,
    )


def _normalize_qq_entry_url(value: object) -> str:
    """게시글 링크가 아닌 동일 채널의 subc 진입 경로만 보존합니다."""
    raw = _clean_text(value)
    if not raw or "/post/" in raw:
        return ""
    absolute = _absolute_url(QQ_CHANNEL_URL, raw)
    parts = urlsplit(absolute)
    if parts.netloc != "pd.qq.com" or parts.path.rstrip("/") != "/g/pd38175600":
        return ""
    subc_match = re.search(r"(?:^|&)subc=([^&]+)", parts.query)
    if not subc_match:
        return QQ_CHANNEL_URL
    return f"{QQ_CHANNEL_URL}?subc={subc_match.group(1)}"


def _read_qq_metadata(page: object) -> dict[str, dict]:
    metadata: dict[str, dict] = {}
    locator = page.locator('script[type="application/ld+json"]')
    texts: list[str] = []
    try:
        texts = list(locator.all_text_contents())
    except Exception:
        try:
            texts = [locator.first.text_content() or ""]
        except Exception:
            texts = []
    for text in texts:
        metadata.update(_parse_qq_json_ld(text))
    return metadata


def _read_qq_entry_urls(page: object) -> list[str]:
    try:
        hrefs = page.locator('a[href*="subc="]').evaluate_all(
            "nodes => nodes.map(node => node.href || node.getAttribute('href') || '')"
        )
    except Exception:
        return []
    routes: list[str] = []
    for href in hrefs:
        route = _normalize_qq_entry_url(href)
        if route and route not in routes:
            routes.append(route)
    return routes


def _read_qq_board_routes(page: object) -> list[dict]:
    try:
        payloads = list(
            page.locator('script[type="application/json"]').all_text_contents()
        )
    except Exception:
        payloads = []
    return _qq_board_routes_from_payloads(payloads)


def _read_qq_page_payload_posts(
    page: object,
    section: str,
    channel_id: str,
) -> tuple[list[CollectedPost], list[dict]]:
    try:
        payloads = list(
            page.locator('script[type="application/json"]').all_text_contents()
        )
    except Exception:
        payloads = []
    posts_by_key: dict[str, CollectedPost] = {}
    batches: list[dict] = []
    for payload in payloads:
        posts, found_batches = _qq_posts_from_payload(
            payload,
            section,
            channel_id,
        )
        batches.extend(found_batches)
        for post in posts:
            key = _qq_post_key(post.url)
            existing = posts_by_key.get(key)
            posts_by_key[key] = _merge_post(existing, post) if existing else post
    return list(posts_by_key.values()), batches


def _read_qq_cards(page: object) -> list[dict]:
    try:
        return list(
            page.locator('a[href*="/post/"]').evaluate_all(
                """
                nodes => nodes.map(node => {
                    const texts = Array.from(
                        node.querySelectorAll('.feed-topic, .feed-detail-text')
                    ).map(item => (item.innerText || item.textContent || '').trim())
                     .filter(Boolean);
                    const comment = node.querySelector(
                        '.game-guild-main__short-content__comment'
                    );
                    return {
                        url: node.href || '',
                        author: (node.querySelector('.nick')?.innerText || '').trim(),
                        edit_time: (node.querySelector('.edit-time')?.innerText || '').trim(),
                        text: texts.join(' ').trim(),
                        comments: (comment?.innerText || '').trim(),
                        is_official: /管理员/.test(node.innerText || '')
                    };
                })
                """
            )
        )
    except Exception:
        return []


def _read_qq_detail_metadata(
    context: object,
    detail_page: object,
    url: str,
) -> tuple[dict, str]:
    """브라우저 쿠키를 공유한 빠른 요청을 먼저 쓰고, 실패 시 상세 탭으로 검증합니다."""
    request_context = getattr(context, "request", None)
    if request_context is not None:
        try:
            response = request_context.get(
                url,
                headers={"Referer": QQ_CHANNEL_URL},
                timeout=18_000,
            )
            if bool(getattr(response, "ok", False)):
                metadata = _parse_qq_detail_html(response.text())
                if metadata.get("published_at"):
                    return metadata, "context_request"
        except Exception:
            pass

    try:
        detail_page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=25_000,
        )
        detail_page.wait_for_timeout(120)
        texts = list(
            detail_page.locator(
                'script[type="application/ld+json"]'
            ).all_text_contents()
        )
        for text in texts:
            metadata = _parse_qq_detail_json_ld(text)
            if metadata.get("published_at"):
                return metadata, "detail_page"
    except Exception:
        pass
    return {}, "failed"


def _qq_post_from_detail(
    card: dict,
    list_metadata: dict[str, dict],
    detail_metadata: dict,
    section: str,
) -> CollectedPost | None:
    """목록 카드와 상세 JSON-LD를 합쳐 정확한 게시일·본문 후보를 만듭니다."""
    url = _canonical_url(_clean_text(card.get("url")))
    key = _qq_post_key(url)
    if not key:
        return None

    detail = dict(detail_metadata or {})
    detail["section"] = section
    combined_metadata = dict(list_metadata)
    if detail:
        previous = dict(combined_metadata.get(key, {}))
        previous.update(
            {
                name: value
                for name, value in detail.items()
                if value not in (None, "")
            }
        )
        combined_metadata[key] = previous

    post = _qq_post_from_card(card, combined_metadata)
    if post is None:
        return None

    detail_text = _clean_text(
        detail_metadata.get("description")
        or detail_metadata.get("headline")
    )
    if len(detail_text) >= len(_clean_text(post.original_text)):
        post.original_text = detail_text
        post.title = _excerpt_title(detail_text)
    elif not post.original_text:
        headline = _clean_text(detail_metadata.get("headline"))
        post.original_text = headline
        post.title = _excerpt_title(headline)

    detail_author = _clean_text(detail_metadata.get("author"))
    if detail_author:
        post.author = detail_author
    detail_date = _clean_text(detail_metadata.get("published_at"))
    if detail_date:
        post.published_at = detail_date
        post.published_at_source = "QQ 상세 JSON-LD datePublished"

    post.source_section = section
    post.is_official = post.is_official or section == QQ_OFFICIAL_SECTION
    post.is_campaign = _is_qq_campaign(
        post.original_text,
        section,
        post.author,
    )
    post.is_low_signal = _is_qq_low_signal(post.original_text)
    post.content_scope = "게시글 상세 텍스트 전체(이미지·댓글 제외)"
    post.source_note = (
        "QQ 날짜순 공개 게시판 · 상세 datePublished 재검증"
        if post.published_at
        else "QQ 날짜순 공개 게시판 · 상세 게시일 확인 실패"
    )
    return post


def _scan_qq_board(
    page: object,
    context: object,
    detail_page: object,
    route: dict,
    start_date: date,
    end_date: date,
) -> tuple[list[CollectedPost], dict]:
    """한 QQ 게시판을 최신순으로 조회 시작일 이전까지 순차 확인합니다."""
    _ = end_date
    section = _clean_text(route.get("section"))
    channel_id = _clean_text(route.get("channel_id"))
    route_url = _clean_text(route.get("url"))
    posts_by_key: dict[str, CollectedPost] = {}
    seen_cards: set[str] = set()
    detail_verified_count = 0
    detail_failed_count = 0
    request_verified_count = 0
    detail_page_verified_count = 0
    stale_rounds = 0
    rounds = 0
    exact_old_streak = 0
    old_post_count = 0
    oldest_verified_date = ""
    stop_reason = "max_scrolls"
    captured_posts_by_key: dict[str, CollectedPost] = {}
    captured_batch_count = 0
    scroll_target_count = 0
    bottom_rounds = 0

    def accept_post(post: CollectedPost, method: str) -> bool:
        nonlocal detail_verified_count
        nonlocal detail_failed_count
        nonlocal request_verified_count
        nonlocal detail_page_verified_count
        nonlocal exact_old_streak
        nonlocal old_post_count
        nonlocal oldest_verified_date

        key = _qq_post_key(post.url)
        if not key:
            return False
        is_new = key not in posts_by_key
        existing = posts_by_key.get(key)
        posts_by_key[key] = _merge_post(existing, post) if existing else post
        if not is_new:
            return False

        if post.published_at:
            detail_verified_count += 1
            if method == "context_request":
                request_verified_count += 1
            elif method == "detail_page":
                detail_page_verified_count += 1
            if not oldest_verified_date or post.published_at < oldest_verified_date:
                oldest_verified_date = post.published_at
            if post.published_at < start_date.isoformat():
                exact_old_streak += 1
                old_post_count += 1
            else:
                exact_old_streak = 0
        else:
            detail_failed_count += 1
            exact_old_streak = 0
        return True

    def capture_response(response: object) -> None:
        nonlocal captured_batch_count
        try:
            request = getattr(response, "request", None)
            resource_type = _clean_text(
                getattr(request, "resource_type", "") if request else ""
            )
            if resource_type and resource_type not in {"xhr", "fetch", "document"}:
                return
            payload = response.json()
            posts, batches = _qq_posts_from_payload(payload, section, channel_id)
            if not batches:
                return
            captured_batch_count += len(batches)
            for post in posts:
                key = _qq_post_key(post.url)
                existing = captured_posts_by_key.get(key)
                captured_posts_by_key[key] = (
                    _merge_post(existing, post) if existing else post
                )
        except Exception:
            return

    listener_attached = False
    if hasattr(page, "on"):
        try:
            page.on("response", capture_response)
            listener_attached = True
        except Exception:
            listener_attached = False

    try:
        page.goto(
            route_url,
            wait_until="domcontentloaded",
            timeout=35_000,
        )
        try:
            page.locator('a[href*="/post/"]').first.wait_for(
                state="attached",
                timeout=6_000,
            )
        except Exception:
            # 숨김 일반 게시판은 서버 데이터만 먼저 내려오고 DOM 카드가 늦게
            # 붙거나 보이지 않을 수 있습니다. 아래 Nuxt payload로 계속 검증합니다.
            pass
        page.wait_for_timeout(700)
    except Exception as error:
        if listener_attached and hasattr(page, "remove_listener"):
            try:
                page.remove_listener("response", capture_response)
            except Exception:
                pass
        return [], {
            "channel_id": channel_id,
            "section": section,
            "complete": False,
            "rounds": 0,
            "candidate_count": 0,
            "detail_verified_count": 0,
            "detail_failed_count": 0,
            "oldest_verified_date": "",
            "stop_reason": f"open_failed:{type(error).__name__}",
        }

    initial_payload_posts, initial_batches = _read_qq_page_payload_posts(
        page,
        section,
        channel_id,
    )
    captured_batch_count += len(initial_batches)
    for post in initial_payload_posts:
        accept_post(post, "page_payload")
    if not initial_payload_posts and not _read_qq_cards(page):
        if listener_attached and hasattr(page, "remove_listener"):
            try:
                page.remove_listener("response", capture_response)
            except Exception:
                pass
        return [], {
            "channel_id": channel_id,
            "section": section,
            "complete": False,
            "rounds": 0,
            "candidate_count": 0,
            "detail_verified_count": 0,
            "detail_failed_count": 0,
            "oldest_verified_date": "",
            "stop_reason": "no_public_feed_data",
        }

    try:
        board_started_at = time.monotonic()
        for round_index in range(QQ_PERIOD_SCAN_MAX_SCROLLS):
            if time.monotonic() - board_started_at > QQ_BOARD_TIME_BUDGET_SECONDS:
                stop_reason = "time_budget"
                break
            rounds = round_index + 1
            before_count = len(posts_by_key)

            for key, post in list(captured_posts_by_key.items()):
                if key not in posts_by_key:
                    accept_post(post, "network_response")

            cards = _read_qq_cards(page)
            list_metadata = _read_qq_metadata(page)
            new_cards: list[dict] = []
            for card in cards:
                key = _qq_post_key(card.get("url"))
                if not key or key in seen_cards:
                    continue
                seen_cards.add(key)
                new_cards.append(card)

            for card in new_cards:
                list_post = _qq_post_from_card(card, list_metadata)
                detail_metadata: dict = {}
                method = "list_json_ld"
                if list_post is None or not list_post.published_at:
                    detail_metadata, method = _read_qq_detail_metadata(
                        context,
                        detail_page,
                        _canonical_url(_clean_text(card.get("url"))),
                    )

                post = _qq_post_from_detail(
                    card,
                    list_metadata,
                    detail_metadata,
                    section,
                )
                if post is None:
                    detail_failed_count += 1
                    exact_old_streak = 0
                    continue
                accept_post(post, method)
                if QQ_DETAIL_REQUEST_DELAY:
                    time.sleep(QQ_DETAIL_REQUEST_DELAY)

            new_count = len(posts_by_key) - before_count
            stale_rounds = stale_rounds + 1 if new_count == 0 else 0

            if exact_old_streak >= QQ_PERIOD_BOUNDARY_OLD_POSTS:
                stop_reason = "period_boundary_reached"
                break
            if stale_rounds >= QQ_PERIOD_SCAN_STALE_ROUNDS and bottom_rounds >= 2:
                stop_reason = "stable_list"
                break

            try:
                # QQ 채널은 높이가 고정된 SPA라 피드가 내부 스크롤 컨테이너
                # (overflow-y:auto) 안에 들어 있습니다. document만 스크롤하면
                # 무한 로딩이 걸리지 않아 초기 노출분에서 멈춥니다.
                # 실제 스크롤 가능한 컨테이너를 찾아 함께 내립니다.
                scrolled = page.evaluate(QQ_SCROLL_SCRIPT)
                if isinstance(scrolled, dict):
                    scroll_target_count = max(
                        scroll_target_count,
                        int(scrolled.get("targets") or 0),
                    )
                    if int(scrolled.get("moved") or 0) == 0:
                        bottom_rounds += 1
                    else:
                        bottom_rounds = 0
                else:
                    try:
                        scroll_target_count = max(
                            scroll_target_count, int(scrolled or 0)
                        )
                    except (TypeError, ValueError):
                        pass
                # 컨테이너를 못 찾은 경우를 대비해 휠 이벤트도 함께 보냅니다.
                try:
                    page.mouse.move(640, 420)
                    page.mouse.wheel(0, 2400)
                except Exception:
                    pass
                page.wait_for_timeout(1_200)
            except Exception:
                stop_reason = "scroll_failed"
                break
    finally:
        if listener_attached and hasattr(page, "remove_listener"):
            try:
                page.remove_listener("response", capture_response)
            except Exception:
                pass

    boundary_date = start_date - timedelta(days=1)
    # 목록이 엄격한 날짜순이 아니어서 '연속 12건'이 성립하지 않는 경우가 있습니다.
    # 조회 시작일 이전 글을 기준 건수 이상 실제로 확인했다면 경계를 지난 것으로 봅니다.
    reached_by_old_posts = (
        bool(oldest_verified_date)
        and oldest_verified_date <= boundary_date.isoformat()
        and old_post_count >= QQ_PERIOD_BOUNDARY_OLD_POSTS
    )
    period_boundary_reached = (
        stop_reason == "period_boundary_reached" or reached_by_old_posts
    )
    if period_boundary_reached and stop_reason != "period_boundary_reached":
        stop_reason = f"period_boundary_by_old_posts:{stop_reason}"
    return list(posts_by_key.values()), {
        "channel_id": channel_id,
        "section": section,
        "complete": period_boundary_reached,
        "rounds": rounds,
        "candidate_count": len(posts_by_key),
        "detail_verified_count": detail_verified_count,
        "detail_failed_count": detail_failed_count,
        "request_verified_count": request_verified_count,
        "detail_page_verified_count": detail_page_verified_count,
        "captured_batch_count": captured_batch_count,
        "scroll_target_count": scroll_target_count,
        "old_post_count": old_post_count,
        "oldest_verified_date": oldest_verified_date,
        "stop_reason": stop_reason,
    }


def _collect_qq_rotating_sample_on_page(
    page: object,
    start_date: date,
    end_date: date,
) -> tuple[list[CollectedPost], dict]:
    """날짜순 게시판 경로 실패 시에만 기존 인기 회전 표본을 보조로 확인합니다."""
    posts_by_key: dict[str, CollectedPost] = {}
    entry_urls = list(QQ_ENTRY_URLS)
    new_counts: list[int] = []
    successful_new_counts: list[int] = []
    successful_rounds = 0
    failed_rounds = 0
    stop_reason = "max_rounds"

    for round_index in range(QQ_MAX_ROUNDS):
        entry_url = entry_urls[round_index % len(entry_urls)]
        try:
            page.goto(
                entry_url,
                wait_until="domcontentloaded",
                timeout=35_000,
            )
            page.locator('a[href*="/post/"]').first.wait_for(
                state="attached",
                timeout=18_000,
            )
            page.wait_for_timeout(700)
        except Exception:
            new_counts.append(0)
            failed_rounds += 1
            continue

        items = _read_qq_cards(page)
        metadata = _read_qq_metadata(page)
        for discovered_url in _read_qq_entry_urls(page):
            if discovered_url not in entry_urls:
                entry_urls.append(discovered_url)

        new_count = 0
        for item in items:
            post = _qq_post_from_card(item, metadata)
            if not post:
                continue
            key = _qq_post_key(post.url)
            if key not in posts_by_key:
                new_count += 1
            posts_by_key[key] = post

        new_counts.append(new_count)
        successful_new_counts.append(new_count)
        successful_rounds += 1
        if (
            successful_rounds >= QQ_MIN_SUCCESSFUL_ROUNDS
            and len(successful_new_counts) >= QQ_CONVERGENCE_WINDOW
            and sum(successful_new_counts[-QQ_CONVERGENCE_WINDOW:]) == 0
        ):
            stop_reason = "sample_converged"
            break

    posts = list(posts_by_key.values())
    latest_window_new = sum(
        successful_new_counts[-QQ_CONVERGENCE_WINDOW:]
    )
    return posts, {
        "collection_rounds": len(new_counts),
        "successful_rounds": successful_rounds,
        "failed_rounds": failed_rounds,
        "new_counts_by_round": new_counts,
        "latest_window_new": latest_window_new,
        "sample_converged": stop_reason == "sample_converged",
        "entry_route_count": len(entry_urls),
        "sample_stop_reason": stop_reason,
        "period_candidates_seen": sum(
            _in_period(post.published_at, start_date, end_date)
            for post in posts
        ),
    }


# 채널 피드 API를 직접 호출하기 위한 설정입니다.
# 화면 스크롤로 긁던 방식은 가상 스크롤러가 주는 것만 받을 수 있어서
# 인기도가 낮은 기간이 통째로 빠지는 문제가 있었습니다(2026-07 넷째 주 전체 누락).
# 같은 데이터를 주는 내부 API를 페이지 번호로 직접 넘기면 그 구멍이 사라집니다.
QQ_FEED_API = (
    "https://pd.qq.com/qunng/guild/gotrpc/noauth/"
    "trpc.qchannel.commreader.ComReader/GetGuildFeeds"
)
# 이 두 헤더가 없으면 서버가 retcode=150으로 조용히 빈 응답을 돌려줍니다.
QQ_FEED_API_HEADERS = {
    "accept": "application/json",
    "accept-language": "zh-CN",
    "content-type": "application/json",
    "x-oidb": '{"uint32_service_type":12}',
    "x-qq-client-appid": "537246381",
}
QQ_FEED_PAGE_LIMIT = 120
QQ_FEED_PAGE_TIME_BUDGET_SECONDS = 150.0
# 이 피드는 시간순이 아니라 깊은 페이지에도 최신 글이 가끔 섞입니다. 그래서
# "날짜가 오래됐는지"가 아니라 "조회 기간에 해당하는 새 글이 더 나오는지"로
# 멈출 때를 판단합니다. 이만큼 연속으로 소득이 없으면 그만 봅니다.
QQ_FEED_BARREN_PAGE_STREAK = 8

QQ_FEED_FETCH_SCRIPT = """
async ([api, body, headers]) => {
  try {
    const response = await fetch(api, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(body),
      credentials: 'include',
    });
    return {status: response.status, body: await response.text()};
  } catch (error) {
    return {status: 0, body: String(error)};
  }
}
"""


def _collect_qq_feed_pages(
    page: object,
    start_date: date,
    end_date: date,
    section_by_channel: dict[str, str] | None = None,
) -> tuple[list[CollectedPost], dict]:
    """채널 피드 API를 페이지 번호로 직접 넘기며 글을 모읍니다.

    이 피드는 시간순이 아니라 인기도가 섞인 순서라 정렬 옵션으로는 해결되지
    않습니다(sortOption 0~3, get_type, from 값을 모두 확인). 대신 페이지를
    충분히 넘기면 기간 내 글이 빠짐없이 나오는 것을 실측했습니다.
    """
    section_map = section_by_channel or {}
    # 조회 기간이 짧으면 깊이 내려갈 필요가 없습니다. 기간에 비례해 상한을 둡니다.
    window_days = max(1, (end_date - start_date).days + 1)
    page_limit = min(QQ_FEED_PAGE_LIMIT, max(40, window_days * 4))
    posts_by_key: dict[str, CollectedPost] = {}
    pages_read = 0
    barren_streak = 0
    empty_streak = 0
    stop_reason = "page_limit"
    oldest_seen = ""
    period_new = 0
    started_at = time.monotonic()

    for page_num in range(1, page_limit + 1):
        if time.monotonic() - started_at > QQ_FEED_PAGE_TIME_BUDGET_SECONDS:
            stop_reason = "time_budget"
            break

        attach = (
            "" if page_num <= 1 else f"notUsed=&pageNum={page_num}&square_v2=1"
        )
        body = {
            "count": 20,
            "from": 7,
            "guild_number": QQ_CHANNEL_ID,
            "get_type": 1,
            "feedAttchInfo": attach,
            "sortOption": 0,
            "need_channel_list": False,
            "need_top_info": False,
        }
        try:
            result = page.evaluate(
                QQ_FEED_FETCH_SCRIPT,
                [QQ_FEED_API, body, QQ_FEED_API_HEADERS],
            )
        except Exception:
            stop_reason = "request_failed"
            break

        if not isinstance(result, dict) or result.get("status") != 200:
            stop_reason = "http_error"
            break
        try:
            payload = json.loads(result.get("body") or "")
        except (TypeError, json.JSONDecodeError):
            stop_reason = "bad_payload"
            break

        feeds = ((payload or {}).get("data") or {}).get("vecFeed") or []
        pages_read = page_num
        if not feeds:
            empty_streak += 1
            if empty_streak >= 2:
                stop_reason = "no_more_feeds"
                break
            continue
        empty_streak = 0

        page_period_new = 0
        for item in feeds:
            channel_info = item.get("channelInfo") if isinstance(item, dict) else None
            channel_sign = (
                channel_info.get("sign") if isinstance(channel_info, dict) else None
            )
            channel_id = _clean_text(
                channel_sign.get("channel_id") if isinstance(channel_sign, dict) else ""
            )
            section = section_map.get(channel_id, "")
            post = _qq_post_from_feed_item(item, section, channel_id)
            if post is None:
                continue
            key = _qq_post_key(post.url)
            if not key:
                continue
            existing = posts_by_key.get(key)
            is_new = existing is None
            posts_by_key[key] = _merge_post(existing, post) if existing else post
            if post.published_at:
                oldest_seen = (
                    post.published_at
                    if not oldest_seen
                    else min(oldest_seen, post.published_at)
                )
                if is_new and _in_period(post.published_at, start_date, end_date):
                    page_period_new += 1

        period_new += page_period_new
        if page_period_new:
            barren_streak = 0
        else:
            barren_streak += 1
            if barren_streak >= QQ_FEED_BARREN_PAGE_STREAK:
                stop_reason = "period_boundary_reached"
                break

    posts = list(posts_by_key.values())
    meta = {
        "feed_pages_read": pages_read,
        "feed_posts": len(posts),
        "feed_period_posts": period_new,
        "feed_oldest_date": oldest_seen,
        "feed_stop_reason": stop_reason,
    }
    return posts, meta


def _collect_qq_batch(
    limit: int,
    start_date: date,
    end_date: date,
) -> tuple[list[CollectedPost], dict]:
    """QQ의 날짜순 공개 게시판을 조회기간 경계까지 확인합니다."""
    _ = limit  # 결과 표시 상한은 QQ 수집 범위를 바꾸지 않습니다.
    sync_playwright = _load_playwright()
    posts_by_key: dict[str, CollectedPost] = {}
    board_results: list[dict] = []
    fallback_used = False
    fallback_meta: dict = {}
    feed_meta: dict = {}
    routes: list[dict] = []

    with sync_playwright() as playwright:
        browser, browser_label = _launch_installed_chromium(playwright)
        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 2400},
            extra_http_headers={
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        page = context.new_page()
        detail_page = context.new_page()
        page.set_default_timeout(18_000)
        detail_page.set_default_timeout(18_000)
        try:
            try:
                page.goto(
                    QQ_CHANNEL_URL,
                    wait_until="domcontentloaded",
                    timeout=35_000,
                )
                page.wait_for_timeout(700)
                routes = _read_qq_board_routes(page)
            except Exception:
                routes = _qq_board_routes_from_payloads([])

            # 이벤트성 게시판만 빼고 나머지는 모두 스캔합니다.
            # 게시판당 공개 노출 건수에 한계가 있으므로, 게시판 수를 늘리는
            # 것이 현재 조건에서 수집량을 늘리는 가장 확실한 방법입니다.
            analysis_routes = [
                route
                for route in routes
                if not route.get("excluded_section")
            ]

            # 게시판 스크롤보다 먼저, 채널 피드 API를 페이지 단위로 훑습니다.
            # 스크롤 경로가 놓치던 날짜를 여기서 대부분 메웁니다.
            section_by_channel = {
                _clean_text(route.get("channel_id")): _clean_text(route.get("section"))
                for route in routes
                if route.get("channel_id")
            }
            try:
                feed_posts, feed_meta = _collect_qq_feed_pages(
                    page,
                    start_date,
                    end_date,
                    section_by_channel,
                )
            except Exception as error:
                feed_posts, feed_meta = [], {
                    "feed_stop_reason": f"failed:{type(error).__name__}",
                }
            for post in feed_posts:
                key = _qq_post_key(post.url)
                existing = posts_by_key.get(key)
                posts_by_key[key] = (
                    _merge_post(existing, post) if existing else post
                )

            for route in analysis_routes:
                board_posts, board_meta = _scan_qq_board(
                    page,
                    context,
                    detail_page,
                    route,
                    start_date,
                    end_date,
                )
                board_results.append(board_meta)
                for post in board_posts:
                    key = _qq_post_key(post.url)
                    existing = posts_by_key.get(key)
                    posts_by_key[key] = (
                        _merge_post(existing, post) if existing else post
                    )

            main_board = next(
                (
                    result
                    for result in board_results
                    if result.get("section") == QQ_MAIN_SECTION
                ),
                {},
            )
            if not main_board.get("complete"):
                fallback_posts, fallback_meta = _collect_qq_rotating_sample_on_page(
                    page,
                    start_date,
                    end_date,
                )
                fallback_used = True
                for post in fallback_posts:
                    key = _qq_post_key(post.url)
                    existing = posts_by_key.get(key)
                    posts_by_key[key] = (
                        _merge_post(existing, post) if existing else post
                    )
        finally:
            context.close()
            browser.close()

    posts = list(posts_by_key.values())
    if not posts:
        raise SourceUnavailable(
            "QQ 공개 채널은 열렸으나 텍스트 게시글을 확인하지 못함"
        )

    period_seen = sum(
        _in_period(post.published_at, start_date, end_date)
        for post in posts
    )
    main_board = next(
        (
            result
            for result in board_results
            if result.get("section") == QQ_MAIN_SECTION
        ),
        {},
    )
    official_board = next(
        (
            result
            for result in board_results
            if result.get("section") == QQ_OFFICIAL_SECTION
        ),
        {},
    )
    # 피드 API가 기간 경계까지 훑었다면 스크롤 경로가 미치지 못했더라도
    # 그 기간은 확보한 것으로 봅니다.
    feed_complete = feed_meta.get("feed_stop_reason") == "period_boundary_reached"
    period_complete = bool(main_board.get("complete")) or feed_complete
    total_rounds = sum(int(result.get("rounds", 0) or 0) for result in board_results)
    detail_verified = sum(
        int(result.get("detail_verified_count", 0) or 0)
        for result in board_results
    )
    detail_failed = sum(
        int(result.get("detail_failed_count", 0) or 0)
        for result in board_results
    )
    board_discovery_fallback = any(
        route.get("discovery") == "fallback"
        for route in routes
        if route.get("section") in {QQ_MAIN_SECTION, QQ_OFFICIAL_SECTION}
    )

    if period_complete:
        coverage_state = "period_complete_public"
        coverage_message = (
            f"일반 게시글 광장 최신순으로 조회기간 경계까지 {int(main_board.get('candidate_count', 0)):,}건 확인"
            + (
                f" · 최저 날짜 {main_board.get('oldest_verified_date')}"
                if main_board.get("oldest_verified_date")
                else ""
            )
            + (
                f" · 시작일 이전 글 {int(main_board.get('old_post_count', 0)):,}건 확인"
                if main_board.get("old_post_count")
                else ""
            )
        )
    else:
        coverage_state = "partial"
        coverage_message = (
            "일반 게시글 광장 날짜순 확인이 기간 경계에 도달하지 못함"
            + (" · 인기 회전 표본 보조 수집" if fallback_used else "")
        )

    meta = {
        "coverage_state": coverage_state,
        "coverage_message": coverage_message,
        "collection_rounds": total_rounds,
        "successful_rounds": total_rounds,
        "failed_rounds": detail_failed,
        "new_counts_by_round": fallback_meta.get("new_counts_by_round", []),
        "latest_window_new": fallback_meta.get("latest_window_new", 0),
        "sample_converged": fallback_meta.get("sample_converged", False),
        "entry_route_count": len(routes),
        "current_run_unique": len(posts),
        "period_candidates_seen": period_seen,
        "sample_stop_reason": main_board.get("stop_reason", "no_main_board"),
        "browser_label": browser_label,
        "collection_mode": (
            "chronological_period_complete"
            if period_complete
            else "chronological_plus_rotating_sample"
        ),
        "period_boundary_reached": period_complete,
        "qq_board_results": board_results,
        "discovered_boards": [
            f"{route.get('section')}{'(제외)' if route.get('excluded_section') else ''}"
            for route in routes
        ],
        "board_route_count": len(routes),
        "board_discovery_fallback": board_discovery_fallback,
        "detail_verified_count": detail_verified,
        "detail_failed_count": detail_failed,
        "main_board_candidate_count": int(main_board.get("candidate_count", 0) or 0),
        "official_board_candidate_count": int(
            official_board.get("candidate_count", 0) or 0
        ),
        "oldest_verified_date": _clean_text(
            main_board.get("oldest_verified_date")
        ),
        "fallback_sample_used": fallback_used,
        "feed_pages_read": int(feed_meta.get("feed_pages_read", 0) or 0),
        "feed_posts": int(feed_meta.get("feed_posts", 0) or 0),
        "feed_oldest_date": _clean_text(feed_meta.get("feed_oldest_date")),
        "feed_stop_reason": _clean_text(feed_meta.get("feed_stop_reason")),
    }
    return posts, meta


def _collect_qq(limit: int, start_date: date, end_date: date) -> list[CollectedPost]:
    """기존 직접 호출과 테스트 호환용 QQ 수집 함수입니다."""
    posts, _meta = _collect_qq_batch(limit, start_date, end_date)
    return posts


Collector = Callable[[int, date, date], list[CollectedPost]]
COLLECTORS: dict[str, Collector] = {
    "QQ 공식 채널": _collect_qq,
    "TapTap": _collect_taptap,
    "Bilibili": _collect_bilibili,
}


def _validate_candidate(
    post: CollectedPost,
    start_date: date,
    end_date: date,
) -> tuple[bool, str]:
    if not post.published_at:
        return False, "unknown_date"
    if not _in_period(post.published_at, start_date, end_date):
        return False, "out_of_range"
    if post.is_campaign:
        return False, "campaign"
    if post.is_low_signal:
        return False, "low_signal"
    if not _is_meaningful_text(post.title):
        return False, "invalid"
    if not _is_meaningful_text(post.original_text):
        return False, "invalid"
    if post.source == "Bilibili":
        if not _is_game_relevant(f"{post.title} {post.original_text}"):
            return False, "irrelevant"
        if _is_bilibili_roundup(post.title):
            return False, "irrelevant"
    return True, "valid"


def collect_posts(
    selected_sources: list[str],
    max_posts: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """선택 소스의 새 표본과 누적 원문을 합쳐 검증된 글을 반환합니다."""
    period_end = end_date or _today_cn()
    period_start = start_date or (period_end - timedelta(days=7))
    # 표시 범위(period_start)와 수집 범위(scan_start)를 분리합니다.
    # 화면에는 사용자가 지정한 기간만 나오지만, 실제 수집은 더 깊이 내려가
    # 캐시를 채웁니다. 하루짜리 조회에서 결과가 비는 문제를 막습니다.
    # 다만 QQ 스캔은 '오늘'에서 과거로 직접 스크롤하는 방식이라 너무 깊이
    # 내려가면 실행 시간이 과도해지므로, 아래로는 최대 QQ_MAX_SCAN_LOOKBACK_DAYS
    # 만큼만 내려가도록 한 번 더 막습니다.
    scan_start = max(
        min(
            period_start,
            period_end - timedelta(days=COLLECT_MIN_LOOKBACK_DAYS),
        ),
        period_end - timedelta(days=QQ_MAX_SCAN_LOOKBACK_DAYS),
    )
    sources = [source for source in selected_sources if source in COLLECTORS]
    if not sources:
        return [], {}

    # 소스 수집 범위는 결과 표시 상한과 완전히 분리합니다. UI에서는 결과 상한을
    # 사용하지 않으며, max_posts는 이전 직접 호출과의 호환만 위해 남겨 둡니다.
    per_source_limit = SOURCE_FETCH_LIMIT
    collected: list[CollectedPost] = []
    status: dict[str, dict] = {}
    cache_by_key, cache_load_message = _load_collection_cache()
    cache_changed = False

    for source in sources:
        cache_before = {
            key: post
            for key, post in cache_by_key.items()
            if post.source == source
        }
        cached_period_before = sum(
            _in_period(post.published_at, period_start, period_end)
            for post in cache_before.values()
        )
        live_candidates: list[CollectedPost] = []
        collector_meta: dict = {}
        failure_state = ""
        failure_message = ""

        try:
            if source == "QQ 공식 채널":
                live_candidates, collector_meta = _collect_qq_batch(
                    per_source_limit,
                    scan_start,
                    period_end,
                )
            elif source == "TapTap":
                live_candidates, collector_meta = _collect_taptap_batch(
                    per_source_limit,
                    scan_start,
                    period_end,
                )
            else:
                live_candidates = COLLECTORS[source](
                    per_source_limit,
                    scan_start,
                    period_end,
                )
                if source == "Bilibili":
                    collector_meta = {
                        "coverage_state": "bounded_search",
                        "coverage_message": (
                            "‘天堂2盟约’ 정확 키워드 최신순 검색 최대 6페이지 · "
                            "영상 제목·설명만 확인"
                        ),
                    }
        except SourceUnavailable as error:
            failure_state = "blocked"
            failure_message = str(error)
        except requests.RequestException as error:
            failure_state = "blocked"
            failure_message = f"접속 실패: {type(error).__name__}"
        except Exception as error:
            failure_state = "error"
            failure_message = f"수집 오류: {type(error).__name__}"

        new_candidate_count = 0
        updated_candidate_count = 0
        for post in live_candidates:
            key = _post_cache_key(post)
            existing = cache_by_key.get(key)
            if existing is None:
                cache_by_key[key] = post
                new_candidate_count += 1
                cache_changed = True
                continue
            merged = _merge_post(existing, post)
            if merged.to_dict() != existing.to_dict():
                cache_by_key[key] = merged
                updated_candidate_count += 1
                cache_changed = True

        candidates = [
            post
            for key, post in sorted(cache_by_key.items())
            if post.source == source
        ]
        counts = {
            "valid": 0,
            "unknown_date": 0,
            "out_of_range": 0,
            "irrelevant": 0,
            "invalid": 0,
            "campaign": 0,
            "low_signal": 0,
        }
        valid_batch: list[CollectedPost] = []
        excluded_samples: list[dict] = []
        for post in candidates:
            valid, reason = _validate_candidate(post, period_start, period_end)
            counts[reason] += 1
            if valid:
                valid_batch.append(post)
            elif reason in {"campaign", "low_signal"} and len(excluded_samples) < 20:
                excluded_samples.append(
                    {
                        "reason": "이벤트성" if reason == "campaign" else "저정보",
                        "date": post.published_at,
                        "title": post.title,
                        "url": post.url,
                    }
                )
        collected.extend(valid_batch)

        if failure_message and valid_batch:
            state = "warning"
            message = (
                f"{failure_message} · 이전에 누적한 원문으로 기간 내 "
                f"{len(valid_batch):,}건 검증"
            )
        elif failure_message:
            state = failure_state
            message = failure_message
        elif valid_batch:
            state = "success"
            message = "기간·실제 게시일·본문 검증 완료"
        elif candidates:
            state = "warning"
            message = "누적 후보는 있으나 조회 기간 검증 후 0건"
        else:
            state = "warning"
            message = "공개 페이지에서 게시글을 찾지 못함"

        status[source] = {
            "ok": bool(valid_batch),
            "state": state,
            "count": len(valid_batch),
            "candidate_count": len(candidates),
            "live_candidate_count": len(live_candidates),
            "cache_before_count": len(cache_before),
            "cached_period_before": cached_period_before,
            "new_candidate_count": new_candidate_count,
            "updated_candidate_count": updated_candidate_count,
            "cumulative_candidate_count": len(candidates),
            "excluded_unknown_date": counts["unknown_date"],
            "excluded_out_of_range": counts["out_of_range"],
            "excluded_irrelevant": counts["irrelevant"],
            "excluded_invalid": counts["invalid"],
            "excluded_campaign": counts["campaign"],
            "excluded_low_signal": counts["low_signal"],
            "excluded_samples": excluded_samples,
            "cache_load_message": cache_load_message,
            "message": message,
            **collector_meta,
        }

    cache_save_message = ""
    if cache_changed:
        cache_save_message = _save_collection_cache(cache_by_key)
    for item in status.values():
        item["cache_save_message"] = cache_save_message
        item["cache_persisted"] = not bool(cache_save_message)

    all_unique_posts: list[dict] = []
    seen_keys: set[str] = set()
    for post in sorted(
        collected,
        key=lambda item: (item.published_at, item.source, _post_cache_key(item)),
        reverse=True,
    ):
        key = _post_cache_key(post)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        all_unique_posts.append(post.to_dict())

    result_cap = int(max_posts) if max_posts is not None else 0
    if result_cap > 0:
        unique_posts = all_unique_posts[:result_cap]
        result_limit_reached = len(all_unique_posts) > result_cap
    else:
        unique_posts = all_unique_posts
        result_limit_reached = False

    selected_counts: dict[str, int] = {}
    for post in unique_posts:
        selected_counts[post["source"]] = selected_counts.get(post["source"], 0) + 1
    for source, item in status.items():
        item["selected_count"] = selected_counts.get(source, 0)
        item["result_limit_reached"] = result_limit_reached
        item["total_valid_unique"] = len(all_unique_posts)

    return unique_posts, status
