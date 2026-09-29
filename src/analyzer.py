from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import date

import pandas as pd


# 순서가 같은 점수일 때의 주 분류 우선순위입니다.
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "신규 서버",
        (
            "新区",
            "新服",
            "新服务器",
            "新区服",
            "新区开服",
            "开新区",
            "开服",
            "开区",
            "服务器开",
            "合服",
            "转服",
            "开服时间",
            "신규 서버",
            "신서버",
            "새 서버",
        ),
    ),
    (
        "작업장",
        (
            "工作室",
            "机器人",
            "脚本号",
            "脚本",
            "外挂",
            "多开",
            "批量账号",
            "打金",
            "搬砖",
            "日入",
            "日赚",
            "一天赚",
            "赚R",
            "赚r",
            "出金",
            "提现",
            "变现",
            "bot",
            "작업장",
            "매크로",
            "오토",
            "다계정",
            "현금화",
            "일일 수익",
        ),
    ),
    (
        "접속·성능",
        (
            "卡顿",
            "掉线",
            "断线",
            "延迟",
            "闪退",
            "崩溃",
            "崩了",
            "进不去",
            "登不上",
            "登录排队",
            "登陆排队",
            "服务器排队",
            "服务器爆满",
            "爆满",
            "维护延长",
            "回档",
            "丢失",
            "卡死",
            "掉帧",
            "发热",
            "耗电",
            "렉",
            "튕김",
            "优化",
            "手感",
            "体验",
            "界面",
            "操作",
            "适配",
            "접속 불가",
            "대기열",
            "서버 지연",
        ),
    ),
    (
        "과금·결제",
        (
            "充值",
            "氪金",
            "首充",
            "充钱",
            "付费",
            "月卡",
            "通行证",
            "商城",
            "礼包价",
            "648",
            "内购",
            "退款",
            "白嫖",
            "零氪",
            "微氪",
            "土豪",
            "과금",
            "결제",
            "충전",
            "월정액",
            "패스",
            "환불",
        ),
    ),
    (
        "운영·CS",
        (
            "客服",
            "封号",
            "封禁",
            "解封",
            "举报",
            "处罚",
            "违规",
            "官方回复",
            "工单",
            "申诉",
            "反馈",
            "策划",
            "运营",
            "고객센터",
            "제재",
            "정지",
            "신고",
            "문의",
            "운영진",
        ),
    ),
    (
        "밸런스",
        (
            "平衡", "削弱", "加强", "增强", "强度", "太强", "太弱",
            "版本之子", "毕业", "battle", "克制", "打不过", "打不动",
            "밸런스", "너프", "버프", "상향", "하향",
        ),
    ),
    (
        "타 서버 비교",
        (
            "韩服", "国服", "台服", "日服", "美服", "国际服", "原版",
            "韩国", "国内版", "海外版", "跟韩服", "和韩服",
            "한국 서버", "한섭", "국내 서버", "본섭",
        ),
    ),
    (
        "번역·현지화",
        (
            "翻译", "汉化", "本地化", "错字", "错别字", "文本",
            "描述错误", "机翻", "语病", "看不懂",
            "번역", "오타", "현지화", "텍스트 오류",
        ),
    ),
    (
        "혈맹·쟁",
        (
            "战盟",
            "血盟",
            "公会",
            "联盟",
            "攻城",
            "城战",
            "要塞战",
            "争霸战",
            "敌对",
            "宣战",
            "打架",
            "pk",
            "组队",
            "队友",
            "队伍",
            "团队",
            "招人",
            "匹配",
            "单排",
            "혈맹",
            "연합",
            "동맹",
            "공성",
            "요새전",
            "쟁",
        ),
    ),
    (
        "거래·경제",
        (
            "交易所",
            "交易",
            "拍卖",
            "成交",
            "物价",
            "价格",
            "钻石",
            "金币",
            "亚丁",
            "分红",
            "分配",
            "概率",
            "抽奖",
            "抽卡",
            "保底",
            "欧皇",
            "非酋",
            "爆出",
            "거래소",
            "거래",
            "경매",
            "시세",
            "다이아",
            "아데나",
            "분배",
        ),
    ),
    (
        "클래스",
        (
            "职业转换",
            "职业变更",
            "转职",
            "换职业",
            "职业",
            "클래스 체인지",
            "클래스",
            "직업 변경",
            "직업",
        ),
    ),
    (
        "스킬",
        (
            "技能书",
            "技能",
            "被动",
            "主动",
            "觉醒",
            "스킬북",
            "스킬",
            "패시브",
            "액티브",
            "각성",
        ),
    ),
    (
        "장비",
        (
            "紫装",
            "红装",
            "装备",
            "武器",
            "防具",
            "饰品",
            "强化",
            "套装",
            "耳环",
            "手镯",
            "印章",
            "分解",
            "洗练",
            "附魔",
            "镶嵌",
            "宝石",
            "变身",
            "魔法娃娃",
            "纹章",
            "符文",
            "属性",
            "장비",
            "무기",
            "방어구",
            "액세서리",
            "강화",
            "귀걸이",
            "팔찌",
            "인장",
        ),
    ),
    (
        "아가시온",
        (
            "亚加西翁",
            "亚加西昂",
            "阿加西翁",
            "独角兽",
            "아가시온",
            "유니콘",
        ),
    ),
    (
        "보스",
        (
            "世界首领",
            "世界boss",
            "首领",
            "领主",
            "boss",
            "보스",
            "월드 보스",
            "레이드",
        ),
    ),
    (
        "사냥터",
        (
            "狩猎场",
            "象牙塔",
            "地牢",
            "副本",
            "猎场",
            "练级点",
            "挂机",
            "刷怪",
            "打怪",
            "掉落",
            "爆率",
            "排队",
            "占位",
            "抢点",
            "抢位",
            "卡位",
            "入口",
            "入场",
            "区域",
            "地图",
            "黄昏",
            "传送",
            "刷新时间",
            "打宝",
            "怪物",
            "掉率",
            "刷新",
            "组队点",
            "挂机点",
            "练功",
            "疲劳",
            "打本",
            "野外",
            "地下城",
            "怪多",
            "怪少",
            "사냥터",
            "던전",
            "자동 사냥",
            "파밍",
            "드랍",
        ),
    ),
    (
        "성장",
        (
            "成长",
            "升级",
            "等级",
            "经验",
            "战力",
            "评分",
            "养成",
            "冲级",
            "材料",
            "道具",
            "背包",
            "仓库",
            "药水",
            "卷轴",
            "收集",
            "图鉴",
            "레벨",
            "경험치",
            "任务",
            "主线",
            "支线",
            "日常",
            "周常",
            "排行榜",
            "排行",
            "新手",
            "萌新",
            "回归",
            "老玩家",
            "角色",
            "账号",
            "성장",
            "전투력",
            "스펙",
        ),
    ),
    (
        "업데이트·이벤트",
        (
            "版本更新",
            "更新",
            "维护",
            "活动",
            "公告",
            "福利",
            "签到",
            "礼包",
            "玩法",
            "内容",
            "新版本",
            "预告",
            "上线",
            "업데이트",
            "점검",
            "이벤트",
            "공지",
            "보상",
            "패키지",
        ),
    ),
]

CATEGORY_ORDER = [name for name, _ in CATEGORY_RULES] + ["기타"]


# 키워드 표에는 이용자가 실제로 확인할 만한 업무용 표현만 노출합니다.
# 각 표시명 아래의 중국어·한국어 표현을 하나의 키워드로 합쳐 집계합니다.
KEYWORD_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("보스", "보스", ("boss", "首领", "领主", "보스", "레이드")),
    ("월드 보스", "보스", ("世界boss", "世界首领", "월드 보스")),
    ("혈맹", "혈맹·쟁", ("战盟", "血盟", "公会", "혈맹")),
    ("연합·동맹", "혈맹·쟁", ("联盟", "연합", "동맹")),
    ("쟁·PK", "혈맹·쟁", ("争霸战", "敌对", "宣战", "打架", "pk", "쟁")),
    ("공성·요새전", "혈맹·쟁", ("攻城", "城战", "要塞战", "공성", "요새전")),
    ("성장", "성장", ("成长", "养成", "성장")),
    ("레벨", "성장", ("等级", "升级", "冲级", "레벨")),
    ("경험치", "성장", ("经验", "경험치")),
    ("전투력·스펙", "성장", ("战力", "评分", "전투력", "스펙")),
    ("사냥터", "사냥터", ("狩猎场", "猎场", "练级点", "사냥터")),
    ("던전", "사냥터", ("地牢", "副本", "象牙塔", "던전")),
    ("자동 사냥", "사냥터", ("挂机", "自动狩猎", "자동 사냥")),
    ("파밍", "사냥터", ("刷怪", "打怪", "파밍")),
    ("드랍", "사냥터", ("掉落", "爆率", "出货", "드랍")),
    ("거래소", "거래·경제", ("交易所", "거래소")),
    ("거래·경매", "거래·경제", ("交易", "拍卖", "成交", "거래", "경매")),
    ("시세·가격", "거래·경제", ("物价", "价格", "시세", "가격")),
    ("다이아", "거래·경제", ("钻石", "다이아")),
    ("아데나·골드", "거래·경제", ("亚丁", "金币", "아데나", "골드")),
    ("보상 분배", "거래·경제", ("分红", "分配", "분배")),
    ("신규 서버", "신규 서버", ("新区", "新服", "新服务器", "신규 서버", "신서버")),
    ("서버 이전", "신규 서버", ("转服", "服务器转移", "서버 이전")),
    ("작업장", "작업장", ("工作室", "打金", "搬砖", "작업장")),
    (
        "현금화·수익",
        "작업장",
        (
            "日入",
            "日赚",
            "一天赚",
            "赚R",
            "赚r",
            "出金",
            "提现",
            "变现",
            "현금화",
            "일일 수익",
        ),
    ),
    ("BOT·매크로", "작업장", ("机器人", "脚本", "外挂", "bot", "매크로")),
    ("다계정", "작업장", ("多开", "批量账号", "다계정")),
    ("클래스", "클래스", ("职业", "클래스", "직업")),
    ("클래스 체인지", "클래스", ("职业转换", "职业变更", "转职", "클래스 체인지")),
    ("스킬", "스킬", ("技能", "스킬")),
    ("스킬북", "스킬", ("技能书", "스킬북")),
    ("각성", "스킬", ("觉醒", "각성")),
    ("장비", "장비", ("装备", "紫装", "红装", "장비")),
    ("무기", "장비", ("武器", "무기")),
    ("방어구", "장비", ("防具", "방어구")),
    ("액세서리", "장비", ("饰品", "耳环", "手镯", "印章", "액세서리", "귀걸이", "팔찌", "인장")),
    ("강화", "장비", ("强化", "강화")),
    ("아가시온", "아가시온", ("亚加西翁", "亚加西昂", "阿加西翁", "아가시온")),
    ("업데이트", "업데이트·이벤트", ("版本更新", "更新", "업데이트")),
    ("이벤트", "업데이트·이벤트", ("活动", "이벤트")),
    ("보상·혜택", "업데이트·이벤트", ("奖励", "福利", "보상", "혜택")),
    ("패키지·상품", "업데이트·이벤트", ("礼包", "商品", "充值", "패키지", "상품")),
    ("오류·버그", "기타", ("错误", "bug", "异常", "버그", "오류")),
    ("렉·지연", "기타", ("卡顿", "延迟", "掉线", "렉", "지연", "튕김")),
]

CONTENT_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "이벤트·홍보",
        (
            "#我们的故事",
            "#OURStory",
            "#ourstory",
            "#CovenantParadise2Covenant",
            "#战盟的狩猎时刻",
            "#锦鲤传递官",
            "故事征集",
            "参与话题活动",
            "赢Q币奖励",
            "限时活动",
            "充值返利",
            "参与抽奖",
            "签到活动",
            "预约奖励",
            "限时礼包",
            "福利活动",
            "登录领取",
            "免费领取",
            "活动开启",
            "이벤트",
            "프로모션",
            "패키지 판매",
        ),
    ),
    (
        "공략·영상",
        (
            "攻略",
            "教程",
            "测评",
            "直播",
            "视频",
            "공략",
            "가이드",
            "방송",
            "영상",
        ),
    ),
]

CONTENT_TYPE_ORDER = ["자연 발생", "공략·영상", "이벤트·홍보", "공식 공지"]
TREND_ELIGIBLE_CONTENT_TYPES = {"자연 발생"}
SUPPORT_CONTENT_TYPES = {"공략·영상", "이벤트·홍보", "공식 공지"}

ANALYSIS_COLUMNS = [
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
    "keyword_labels",
    "keyword_text",
    "keyword_counts",
    "views",
    "engagement",
    "author",
    "url",
    "translation_error",
]


def _normalize(text: object) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", " ", value).strip().casefold()


def _count_alias(text: str, alias: str) -> int:
    normalized_alias = _normalize(alias)
    if not normalized_alias:
        return 0

    if re.fullmatch(r"[a-z0-9_+\- ]+", normalized_alias):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])"
        return len(re.findall(pattern, text))
    return text.count(normalized_alias)


def _count_aliases(text: str, aliases: tuple[str, ...]) -> int:
    # 같은 표현 안에서 짧은 별칭과 긴 별칭이 겹칠 때 한 번만 셉니다.
    unique_aliases = sorted(
        {_normalize(alias) for alias in aliases if alias},
        key=len,
        reverse=True,
    )
    occupied: list[tuple[int, int]] = []

    for alias in unique_aliases:
        if re.fullmatch(r"[a-z0-9_+\- ]+", alias):
            pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
        else:
            pattern = re.escape(alias)

        for match in re.finditer(pattern, text):
            start, end = match.span()
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            occupied.append((start, end))

    return len(occupied)


def _classify_category(text: str) -> tuple[str, list[str]]:
    scored: list[tuple[int, int, str]] = []
    for order, (category, aliases) in enumerate(CATEGORY_RULES):
        score = _count_aliases(text, aliases)
        if score:
            scored.append((score, -order, category))

    if not scored:
        return "기타", ["기타"]

    scored.sort(reverse=True)
    all_categories = [item[2] for item in scored]
    return all_categories[0], all_categories


def _classify_content_type(source: str, text: str, is_official: bool = False) -> str:
    if source == "공식 홈페이지" or is_official:
        return "공식 공지"

    for content_type, aliases in CONTENT_TYPE_RULES:
        if _count_aliases(text, aliases):
            return content_type

    if source == "Bilibili":
        return "공략·영상"
    return "자연 발생"


def _analysis_role(content_type: str) -> tuple[str, bool]:
    """게시물의 쓰임을 분리해 영상·홍보물이 유저 동향으로 합산되지 않게 합니다."""
    if content_type in TREND_ELIGIBLE_CONTENT_TYPES:
        return "유저 반응 후보", True
    if content_type == "공략·영상":
        return "콘텐츠·공략 참고", False
    if content_type == "이벤트·홍보":
        return "홍보·이벤트 참고", False
    return "공식 배경 참고", False


def _detect_keywords(text: str) -> dict[str, int]:
    detected: dict[str, int] = {}
    for label, _category, aliases in KEYWORD_RULES:
        count = _count_aliases(text, aliases)
        if count:
            detected[label] = count
    return detected


def _detect_post_keywords(
    title: str,
    original_text: str,
    title_ko: str,
    translated_text: str,
) -> dict[str, int]:
    """제목·본문·번역에서 같은 의미가 반복돼도 가장 큰 횟수만 사용합니다."""
    merged: dict[str, int] = {}
    for field in (title, original_text, title_ko, translated_text):
        for label, count in _detect_keywords(_normalize(field)).items():
            merged[label] = max(merged.get(label, 0), count)
    return merged


def _normalize_date(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = pd.to_datetime(raw, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.date().isoformat()


def _normalize_number(value: object) -> int:
    if value is None or value == "":
        return 0
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def analyze_posts(posts: list[dict]) -> pd.DataFrame:
    """수집·번역된 글을 사실 기반 카테고리, 글 유형, 키워드로 분류합니다."""
    records: list[dict] = []

    for post in posts:
        source = str(post.get("source", "") or "")
        title = str(post.get("title", "") or "")
        title_ko = str(post.get("title_ko", "") or "")
        original_text = str(post.get("original_text", "") or "")
        translated_text = str(post.get("translated_text", "") or "")
        content_scope = str(post.get("content_scope", "") or "")
        source_note = str(post.get("source_note", "") or "")
        source_section = str(post.get("source_section", "") or "")
        published_at_source = str(post.get("published_at_source", "") or "")
        is_official = bool(post.get("is_official", False))
        is_campaign = bool(post.get("is_campaign", False))
        is_low_signal = bool(post.get("is_low_signal", False))
        source_text = _normalize(" ".join((title, original_text)))
        combined_text = _normalize(
            " ".join((title, title_ko, original_text, translated_text))
        )

        category, all_categories = _classify_category(combined_text)
        keyword_counts = _detect_post_keywords(
            title,
            original_text,
            title_ko,
            translated_text,
        )
        content_type = _classify_content_type(source, source_text, is_official)
        analysis_role, trend_eligible = _analysis_role(content_type)

        records.append(
            {
                "date": _normalize_date(post.get("published_at", "")),
                "source": source,
                "category": category,
                "all_categories": ", ".join(all_categories),
                "content_type": content_type,
                "analysis_role": analysis_role,
                "trend_eligible": trend_eligible,
                "title": title,
                # 번역 실패 시 중국어를 넣으면 '한국어 제목' 칸과 CSV에 중국어가
                # 조용히 섞여 번역된 것처럼 보입니다. 비워 두고 실패를 드러냅니다.
                "title_ko": title_ko,
                "original_text": original_text,
                "translated_text": translated_text,
                "content_scope": content_scope,
                "source_note": source_note,
                "source_section": source_section,
                "published_at_source": published_at_source,
                "is_official": is_official,
                "is_campaign": is_campaign,
                "is_low_signal": is_low_signal,
                "keyword_labels": list(keyword_counts),
                "keyword_text": ", ".join(keyword_counts),
                "keyword_counts": keyword_counts,
                "views": _normalize_number(post.get("views")),
                "engagement": _normalize_number(post.get("comments")),
                "author": str(post.get("author", "") or ""),
                "url": str(post.get("url", "") or ""),
                "translation_error": str(post.get("translation_error", "") or ""),
            }
        )

    if not records:
        return pd.DataFrame(columns=ANALYSIS_COLUMNS)

    frame = pd.DataFrame(records, columns=ANALYSIS_COLUMNS)
    frame["_date_sort"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.sort_values(
        ["_date_sort", "engagement", "views"],
        ascending=[False, False, False],
        na_position="last",
    ).drop(columns="_date_sort")
    return frame.reset_index(drop=True)


def filter_posts_by_date(
    frame: pd.DataFrame,
    start_date: date,
    end_date: date,
    keep_unknown_dates: bool = False,
) -> tuple[pd.DataFrame, int]:
    """게시일이 확인되고 조회 기간 안에 있는 글만 남깁니다."""
    if frame.empty:
        return frame.copy(), 0

    parsed = pd.to_datetime(frame["date"], errors="coerce")
    unknown_mask = parsed.isna()

    # Streamlit의 date_input은 datetime.date를 반환하지만, pandas 열은
    # datetime64[ns]입니다. pandas 버전에 따라 두 형식을 바로 비교하면
    # "Invalid comparison between dtype=datetime64[ns] and date"가 발생하므로
    # 양쪽 경계를 pandas Timestamp로 통일합니다.
    start_timestamp = pd.Timestamp(start_date)
    end_timestamp = pd.Timestamp(end_date)
    period_mask = parsed.between(start_timestamp, end_timestamp, inclusive="both")
    mask = period_mask | unknown_mask if keep_unknown_dates else period_mask
    return frame[mask].reset_index(drop=True), int(unknown_mask.sum())


# 미분류 진단에서 걸러낼 일반 표현입니다. 게임 내용과 무관합니다.
UNCLASSIFIED_STOPWORDS: frozenset[str] = frozenset({
    "什么", "这个", "那个", "我们", "你们", "他们", "没有", "可以",
    "就是", "现在", "怎么", "这样", "那样", "一个", "自己", "时候",
    "知道", "感觉", "真的", "大家", "已经", "还是", "不是", "但是",
    "因为", "所以", "如果", "这么", "那么", "一下", "有点", "觉得",
    "希望", "请问", "到底", "而且", "或者", "应该", "可能", "一直",
    "为什么", "怎么办", "有没有", "是不是", "这是", "还有", "然后",
    "一样", "东西", "开始", "结束", "以后", "之后", "之前", "时间",
    "问题", "情况", "谢谢", "各位", "兄弟", "老哥", "求助", "请教",
})


def _registered_aliases() -> set[str]:
    """이미 분류 규칙에 등록된 표현 집합입니다."""
    registered: set[str] = set()
    for _, aliases in CATEGORY_RULES:
        registered.update(aliases)
    return registered


def build_unclassified_terms(
    frame: pd.DataFrame,
    limit: int = 25,
    min_posts: int = 2,
) -> pd.DataFrame:
    """'기타'로 남은 글에서 자주 나온 중국어 표현을 뽑습니다.

    자동 분류 규칙을 늘려야 할 후보를 사람이 직접 확인하기 위한 진단표입니다.
    """
    columns = ["표현", "글 수", "예시 글"]
    if frame.empty or "category" not in frame.columns:
        return pd.DataFrame(columns=columns)

    part = frame[frame["category"] == "기타"]
    if part.empty:
        return pd.DataFrame(columns=columns)

    registered = _registered_aliases()
    han = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

    post_counts: Counter = Counter()
    examples: dict[str, str] = {}

    for _, row in part.iterrows():
        text = f"{row.get('title', '')} {row.get('original_text', '')}"
        seen_in_post: set[str] = set()
        for run in han.findall(text):
            for size in (2, 3):
                for index in range(len(run) - size + 1):
                    term = run[index : index + size]
                    if term in UNCLASSIFIED_STOPWORDS or term in registered:
                        continue
                    seen_in_post.add(term)
        for term in seen_in_post:
            post_counts[term] += 1
            if term not in examples:
                examples[term] = str(
                    row.get("title_ko") or row.get("title") or ""
                )[:60]

    rows = [
        {"표현": term, "글 수": count, "예시 글": examples.get(term, "")}
        for term, count in post_counts.most_common()
        if count >= min_posts
    ]

    # 더 긴 표현이 이미 잡혔다면 그 안에 포함된 짧은 표현은 숨깁니다.
    kept: list[dict] = []
    for item in sorted(rows, key=lambda r: (-len(r["표현"]), -r["글 수"])):
        if any(
            item["표현"] in other["표현"] and item["글 수"] <= other["글 수"]
            for other in kept
        ):
            continue
        kept.append(item)

    kept.sort(key=lambda r: (-r["글 수"], r["표현"]))
    return pd.DataFrame(kept[:limit], columns=columns)


def _clip(value: object, length: int = 68) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= length:
        return text
    return text[: length - 1].rstrip() + "…"


def _post_label(row: pd.Series) -> str:
    title = row.get("title_ko") or row.get("title") or ""
    if not str(title).strip():
        title = row.get("translated_text") or row.get("original_text") or ""
    return _clip(title)


def _representative_posts(part: pd.DataFrame, count: int = 3) -> list[str]:
    """반응이 큰 순으로 대표 글 제목을 뽑습니다."""
    if part.empty:
        return []
    ranked = part.copy()
    for column in ("engagement", "views"):
        if column not in ranked.columns:
            ranked[column] = 0
    ranked["_score"] = (
        pd.to_numeric(ranked["engagement"], errors="coerce").fillna(0) * 3
        + pd.to_numeric(ranked["views"], errors="coerce").fillna(0)
    )
    ranked = ranked.sort_values("_score", ascending=False)

    labels: list[str] = []
    for _, row in ranked.iterrows():
        label = _post_label(row)
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= count:
            break
    return labels


def build_trend_narrative(
    trend_frame: pd.DataFrame,
    support_frame: pd.DataFrame,
    period: str = "",
    collection_notes: list[str] | None = None,
) -> str:
    """수집된 글 전체를 읽어 서술형 동향 보고문을 작성합니다."""
    if trend_frame.empty:
        lines = [
            f"{period} 기간에 게시일과 본문이 검증된 자연 발생 유저 글이 "
            "확인되지 않았습니다.",
            "",
            "**먼저 확인할 것**",
            "- 「수집 내역」 탭의 소스별 카드에서 '이번 실행 확보' 건수",
            "  0이면 수집 자체가 실패한 것입니다.",
            "- '기간 밖 제외' 건수가 크면 수집은 됐으나 해당 날짜 글이 "
            "없는 것입니다.",
            "- '날짜 미확인 제외' 건수가 크면 게시일을 읽지 못한 것입니다.",
            "- 조회 기간을 넓혀서(예: 최근 7일) 다시 실행해 보세요.",
        ]
        for note in collection_notes or []:
            lines.append(f"- {note}")
        return "\n".join(lines)

    total = len(trend_frame)
    sources = trend_frame["source"].value_counts().to_dict()
    source_text = ", ".join(
        f"{name} {int(count):,}건" for name, count in sources.items()
    )

    counts = trend_frame["category"].value_counts().to_dict()
    named = [
        (name, int(count))
        for name, count in counts.items()
        if name != "기타"
    ]
    named.sort(key=lambda item: (-item[1], item[0]))
    other_count = int(counts.get("기타", 0))

    blocks: list[str] = []

    # 1) 개요
    blocks.append(
        f"### {period} 중국 유저 동향\n\n"
        f"이번 기간 중국 공개 커뮤니티에서 게시일과 본문이 모두 검증된 자연 발생 "
        f"유저 글은 **{total:,}건**입니다. 출처 구성은 {source_text}이며, "
        f"공략·영상과 공식·홍보 성격의 글 {len(support_frame):,}건은 여론으로 "
        f"보기 어려워 집계에서 분리했습니다."
    )

    # 2) 이번 기간의 특징
    if named:
        top = named[:4]
        head_text = ", ".join(
            f"{name} {count:,}건({count / total * 100:.0f}%)"
            for name, count in top
        )
        lead = (
            f"분류된 글 기준으로 이번 기간 가장 많이 언급된 주제는 {head_text} "
            f"순입니다."
        )
        spread = len(named)
        if top and top[0][1] <= total * 0.15:
            lead += (
                f" 특정 주제로 쏠리지 않고 {spread}개 주제에 고르게 분산돼 "
                "있어, 단일 이슈가 여론을 지배하는 상황은 아닌 것으로 보입니다."
            )
        else:
            lead += (
                f" {top[0][0]} 주제가 상대적으로 두드러집니다."
            )
        blocks.append("**이번 기간의 특징**\n\n" + lead)

        for name, count in top:
            part = trend_frame[trend_frame["category"] == name]
            try:
                keywords = build_keyword_stats(part)
            except Exception:
                keywords = pd.DataFrame()
            keyword_text = (
                ", ".join(
                    str(row["키워드"]) for _, row in keywords.head(3).iterrows()
                )
                if not keywords.empty
                else ""
            )
            samples = _representative_posts(part, 2)
            sentence = f"- **{name} ({count:,}건)** — "
            if keyword_text:
                sentence += f"주로 {keyword_text} 관련 언급이 확인됩니다. "
            if samples:
                sentence += "대표 글: " + " / ".join(
                    f"“{item}”" for item in samples
                )
            blocks.append(sentence)

    # 3) 해석 주의
    cautions: list[str] = []
    if other_count:
        ratio = other_count / total * 100
        cautions.append(
            f"자동 분류 규칙에 걸리지 않은 미분류 글이 {other_count:,}건"
            f"({ratio:.0f}%)입니다. 위 주제별 건수는 분류된 글만 기준이므로 "
            "실제 비중과 다를 수 있습니다."
        )
    for note in collection_notes or []:
        cautions.append(note)
    cautions.append(
        "본 문서는 비로그인으로 열람 가능한 공개 게시글 범위에서 수집한 표본 기준입니다. "
        "댓글, 이미지 안의 텍스트, 비공개 글은 포함되지 않으며, 인기 노출 화면을 "
        "함께 활용하므로 반응이 큰 글에 편중될 수 있습니다. 주제별 건수는 상대적 "
        "참고치로만 보고, 판단 전 원문을 확인해야 합니다."
    )
    blocks.append("**해석 시 주의**")
    blocks.extend(f"- {item}" for item in cautions)

    return "\n\n".join(blocks)


def build_category_stats(frame: pd.DataFrame) -> pd.DataFrame:
    content_types = CONTENT_TYPE_ORDER
    rows: list[dict] = []

    for category in CATEGORY_ORDER:
        part = frame[frame["category"] == category]
        row: dict[str, object] = {"분류": category, "전체": len(part)}
        for content_type in content_types:
            row[content_type] = int((part["content_type"] == content_type).sum())
        rows.append(row)

    return pd.DataFrame(rows)


def split_evidence(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """실제 유저 반응과 공략·홍보·공식 참고자료를 분리합니다."""
    if frame.empty:
        return frame.copy(), frame.copy()

    if "trend_eligible" in frame.columns:
        mask = frame["trend_eligible"].fillna(False).astype(bool)
    else:
        mask = frame["content_type"].isin(TREND_ELIGIBLE_CONTENT_TYPES)
    return (
        frame[mask].reset_index(drop=True),
        frame[~mask].reset_index(drop=True),
    )


def build_keyword_stats(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=["순위", "키워드", "관련 글 수", "총 언급 횟수", "연결 분류"]
        )

    post_counts: Counter[str] = Counter()
    mention_counts: Counter[str] = Counter()
    keyword_category = {label: category for label, category, _ in KEYWORD_RULES}

    for counts in frame["keyword_counts"]:
        if not isinstance(counts, dict):
            continue
        for label, count in counts.items():
            post_counts[label] += 1
            mention_counts[label] += int(count)

    ordered = sorted(
        post_counts,
        key=lambda label: (-post_counts[label], -mention_counts[label], label),
    )
    rows = [
        {
            "순위": index,
            "키워드": label,
            "관련 글 수": post_counts[label],
            "총 언급 횟수": mention_counts[label],
            "연결 분류": keyword_category.get(label, "기타"),
        }
        for index, label in enumerate(ordered, 1)
    ]
    return pd.DataFrame(rows)


def _top_keywords(frame: pd.DataFrame, limit: int = 3) -> list[tuple[str, int]]:
    stats = build_keyword_stats(frame)
    if stats.empty:
        return []
    return [
        (str(row["키워드"]), int(row["관련 글 수"]))
        for _, row in stats.head(limit).iterrows()
    ]


def _format_counter(counter: Counter[str], limit: int = 3) -> str:
    return ", ".join(
        f"{label} {count:,}건" for label, count in counter.most_common(limit)
    )


def _repeated_topic_groups(
    frame: pd.DataFrame,
    min_posts: int = 2,
) -> list[tuple[str, str, pd.DataFrame]]:
    """같은 대분류가 아니라 같은 세부 키워드가 반복된 경우만 후보로 묶습니다."""
    natural_frame, _ = split_evidence(frame)
    if natural_frame.empty:
        return []

    keyword_categories = {label: category for label, category, _ in KEYWORD_RULES}
    candidates: list[tuple[str, str, pd.DataFrame]] = []

    # 더 구체적인 키워드를 먼저 처리합니다. 같은 글 집합이 '보스'와
    # '월드 보스'에 모두 걸리면 구체적인 '월드 보스'만 남깁니다.
    labels = sorted(keyword_categories, key=lambda value: (-len(value), value))
    seen_signatures: set[tuple[str, ...]] = set()
    for label in labels:
        category = keyword_categories[label]
        if category == "기타":
            continue
        mask = natural_frame["keyword_labels"].apply(
            lambda values: label in values if isinstance(values, list) else False
        )
        part = natural_frame[mask & (natural_frame["category"] == category)].copy()
        if len(part) < min_posts:
            continue

        signature = tuple(
            sorted(
                str(row.get("url") or f"{row.get('source')}|{row.get('title')}")
                for _, row in part.iterrows()
            )
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        candidates.append((category, label, part.reset_index(drop=True)))

    return sorted(
        candidates,
        key=lambda item: (-len(item[2]), CATEGORY_ORDER.index(item[0]), item[1]),
    )


def count_repeated_topics(frame: pd.DataFrame) -> int:
    return len(_repeated_topic_groups(frame, min_posts=2))


def build_trend_summaries(frame: pd.DataFrame, limit: int = 8) -> list[dict]:
    """자연 발생 글에서 같은 세부 주제가 2건 이상인 경우만 반환합니다."""
    summaries: list[dict] = []
    for category, topic, part in _repeated_topic_groups(frame, min_posts=2)[:limit]:
        type_counts = Counter(part["content_type"].tolist())
        source_counts = Counter(part["source"].tolist())
        keywords = _top_keywords(part, limit=3)

        evidence_label = "반복 확인 후보"
        sentence_parts = [
            f"자연 발생 글에서 '{topic}' 주제가 {len(part):,}건 확인됐습니다. "
            "제목·본문이 실제로 같은 현상을 가리키는지 원문 확인이 필요합니다."
        ]
        if type_counts:
            sentence_parts.append(f"글 유형은 {_format_counter(type_counts)}입니다.")
        if keywords:
            keyword_text = ", ".join(
                f"{label} {count:,}건" for label, count in keywords
            )
            sentence_parts.append(f"주요 감지 키워드는 {keyword_text}입니다.")

        examples: list[dict] = []
        seen_titles: set[str] = set()
        ranked = part.sort_values(
            ["engagement", "views", "date"],
            ascending=[False, False, False],
        )
        for _, row in ranked.iterrows():
            example_title = re.sub(
                r"\s+", " ", str(row["title_ko"] or row["title"] or "")
            ).strip()
            normalized_title = re.sub(r"\W+", "", example_title).casefold()
            if not example_title or normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)
            translated = re.sub(
                r"\s+", " ", str(row["translated_text"] or "")
            ).strip()
            examples.append(
                {
                    "date": str(row["date"] or ""),
                    "source": str(row["source"] or ""),
                    "title": example_title,
                    "excerpt": translated[:220],
                    "url": str(row["url"] or ""),
                    "content_scope": str(row.get("content_scope", "") or ""),
                }
            )
            if len(examples) >= 5:
                break

        summaries.append(
            {
                "title": f"{category} / {topic} · {len(part):,}건 · {evidence_label}",
                "summary": " ".join(sentence_parts),
                "meta": f"출처: {_format_counter(source_counts)}",
                "examples": examples,
            }
        )
    return summaries


def _representative_title(part: pd.DataFrame) -> str:
    if part.empty:
        return ""
    ranked = part.sort_values(
        ["engagement", "views"],
        ascending=[False, False],
    )
    title = str(ranked.iloc[0]["title_ko"] or ranked.iloc[0]["title"] or "")
    return re.sub(r"\s+", " ", title).strip()[:100]


def build_report_draft(frame: pd.DataFrame, limit: int = 5) -> str:
    """자동 해석 없이 확인 사실만 담은 짧은 참고 문안을 만듭니다."""
    natural_frame, support_frame = split_evidence(frame)
    if natural_frame.empty:
        return "유의미한 수집 결과가 없어 참고 문안을 생성하지 않았습니다."

    repeated_topics = _repeated_topic_groups(natural_frame, min_posts=2)
    if not repeated_topics:
        return (
            "[중국 공개 커뮤니티 동향 참고]\n"
            "- 자연 발생 글에서 동일 세부 주제로 2건 이상 반복 확인된 동향 없음\n"
            f"- 자연 발생 글 {len(natural_frame):,}건은 개별 원문 참고\n"
            f"- 공략·영상·홍보 자료 {len(support_frame):,}건은 유저 동향 집계에서 제외\n\n"
            "※ 단일 질문·CS와 공략 영상은 동향으로 확대 해석하지 않습니다."
        )

    lines = ["[중국 공개 커뮤니티 동향 참고]"]
    for category, topic, part in repeated_topics[:limit]:
        keywords = _top_keywords(part, limit=3)
        keyword_text = ", ".join(
            f"{label}({count:,}건)" for label, count in keywords
        ) or "감지 키워드 없음"
        representative = _representative_title(part)

        detail = (
            f"- {category}/{topic}: 자연 발생 글 {len(part):,}건 · "
            f"주요 키워드 {keyword_text}"
        )
        if representative:
            detail += f" · 대표 글: {representative}"
        lines.append(detail)

    lines.append(
        f"\n※ 공략·영상·홍보 {len(support_frame):,}건은 위 동향 집계에서 제외했습니다. "
        "로그인·앱 전용 게시물은 포함되지 않을 수 있습니다."
    )
    return "\n".join(lines)
