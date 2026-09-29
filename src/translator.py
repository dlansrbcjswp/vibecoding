"""중국어 원문을 한국어로 번역합니다.

2026-09 기준으로 deep-translator가 쓰던 translate.google.com/m 경로는 이 사무실
IP가 구글에 남용으로 분류되어 HTTP 429로 영구 차단된 상태입니다. 대기하거나
재시도해도 풀리지 않습니다(8분간 12회 재시도 전부 429). 대신 같은 IP에서
clients5.google.com 경로는 정상 동작하는 것을 실측해, 그쪽을 기본 경로로 씁니다.

차단을 부른 근본 원인은 "번역 결과를 어디에도 저장하지 않아 앱을 켤 때마다
같은 글을 처음부터 다시 번역한 것"입니다. 그래서 번역 결과를 디스크에 영속
저장하고, 요청 간격 제한·재시도·연속 실패 시 중단(회로 차단)을 함께 둡니다.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path

import requests

# 회사 PC의 Windows 인증서 저장소를 사용합니다.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass


CHINESE_PATTERN = re.compile(r"[㐀-䶿一-鿿]")

# GET 쿼리스트링으로 원문을 보내므로 URL 길이 제한을 고려해야 합니다.
# 중국어 1자는 URL 인코딩 시 9바이트가 되어, 3000자면 URL이 27KB로 한도를 넘습니다.
MAX_CHUNK_LENGTH = 800
# 한 요청에 여러 건을 묶어 보내 요청 수 자체를 줄입니다(차단 위험 감소).
MAX_BATCH_ITEMS = 8
MAX_BATCH_CHARS = 1200

SOURCE_LANG = "zh-CN"
TARGET_LANG = "ko"

# 실측(2026-09-29) 기준 동작 순서입니다. 앞쪽이 막히면 다음으로 넘어갑니다.
# 여러 건을 한 요청에 묶어 보낼 수 있는 경로들입니다.
TRANSLATION_ENDPOINTS = (
    ("clients5", "https://clients5.google.com/translate_a/t"),
    ("googleapis", "https://translate.googleapis.com/translate_a/t"),
)
# 위 경로가 모두 막혔을 때 쓰는 마지막 경로입니다. 호스트가 달라 함께 막힐
# 가능성이 낮지만, 한 번에 한 건씩만 보낼 수 있어 느립니다.
SINGLE_FALLBACK_URL = "https://translate.google.com/translate_a/single"

REQUEST_TIMEOUT = (5, 20)
# 구글 공개 한도는 초당 5건입니다. 80% 수준으로 눌러 둡니다.
MIN_REQUEST_INTERVAL = 0.25
MAX_ATTEMPTS = 3
# 모든 경로가 연속으로 이만큼 실패하면 이번 실행에서는 번역을 포기합니다.
# 예전에는 차단 상태에서도 2시간 넘게 계속 두드렸습니다.
CIRCUIT_FAILURE_LIMIT = 8


def _resolve_translation_cache_path() -> Path:
    """번역 결과는 개인 PC에만 쌓습니다(공유 폴더 실행 시 충돌 방지)."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "l2m_cn_trend_tool" / "translation_cache.json"
    return Path.home() / ".l2m_cn_trend_tool" / "translation_cache.json"


CACHE_PATH = _resolve_translation_cache_path()

_cache: dict[str, str] | None = None
_cache_dirty = False
_last_request_at = 0.0
_consecutive_failures = 0


def _load_cache() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache

    _cache = {}
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _cache

    entries = raw.get("entries") if isinstance(raw, dict) else None
    if isinstance(entries, dict):
        for key, value in entries.items():
            if isinstance(key, str) and isinstance(value, str):
                _cache[key] = value
    return _cache


def save_cache() -> None:
    """번역 캐시를 디스크에 기록합니다. 쓰다가 죽어도 기존 파일이 깨지지 않게 합니다."""
    global _cache_dirty
    if not _cache_dirty or _cache is None:
        return

    payload = {
        "schema_version": 1,
        "source": SOURCE_LANG,
        "target": TARGET_LANG,
        "entries": _cache,
    }
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = CACHE_PATH.with_name(f".{CACHE_PATH.name}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_path.replace(CACHE_PATH)
        _cache_dirty = False
    except OSError:
        # 캐시 저장 실패가 번역 자체를 막지는 않습니다.
        pass


def _cache_get(text: str) -> str | None:
    return _load_cache().get(text)


def _cache_put(text: str, translated: str) -> None:
    global _cache_dirty
    cache = _load_cache()
    if cache.get(text) == translated:
        return
    cache[text] = translated
    _cache_dirty = True


def _split_text(text: str, max_length: int = MAX_CHUNK_LENGTH) -> list[str]:
    text = text.strip()
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        cut = max(
            remaining.rfind("。", 0, max_length),
            remaining.rfind("！", 0, max_length),
            remaining.rfind("？", 0, max_length),
            remaining.rfind("\n", 0, max_length),
        )
        if cut < max_length // 2:
            cut = max_length
        else:
            cut += 1

        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [chunk for chunk in chunks if chunk]


def _throttle() -> None:
    global _last_request_at
    wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _parse_batch_response(payload: object, expected: int) -> list[str]:
    """clients5 응답은 ["번역"] 또는 [["번역", ...], ...] 두 형태로 옵니다."""
    if not isinstance(payload, list) or not payload:
        raise ValueError("예상과 다른 번역 응답 형식입니다.")

    if isinstance(payload[0], str):
        values = [str(item) for item in payload]
    else:
        values = []
        for item in payload:
            if isinstance(item, list) and item:
                values.append(str(item[0]))
            elif isinstance(item, str):
                values.append(item)
            else:
                raise ValueError("번역 응답 항목을 읽지 못했습니다.")

    if len(values) < expected:
        raise ValueError("번역 응답 개수가 요청보다 적습니다.")
    return values[:expected]


def _request_batch(session: requests.Session, url: str, texts: list[str]) -> list[str]:
    params = [("client", "dict-chrome-ex"), ("sl", SOURCE_LANG), ("tl", TARGET_LANG)]
    params.extend(("q", text) for text in texts)

    _throttle()
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return _parse_batch_response(response.json(), len(texts))


def _request_single(session: requests.Session, text: str) -> str:
    """마지막 폴백 경로. 한 번에 한 건만 보내고 응답 구조도 다릅니다."""
    params = {
        "client": "gtx",
        "sl": SOURCE_LANG,
        "tl": TARGET_LANG,
        "dt": "t",
    }
    _throttle()
    response = session.post(
        SINGLE_FALLBACK_URL,
        params=params,
        data={"q": text},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    # [[["번역", "원문", ...], ...], ...] 형태라 첫 묶음의 조각들을 이어 붙입니다.
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        raise ValueError("예상과 다른 번역 응답 형식입니다.")
    parts = [
        str(segment[0])
        for segment in payload[0]
        if isinstance(segment, list) and segment and segment[0]
    ]
    if not parts:
        raise ValueError("번역 결과가 비어 있습니다.")
    return "".join(parts)


def _translate_batch(session: requests.Session, texts: list[str]) -> list[str] | None:
    """한 묶음을 번역합니다. 모든 경로가 실패하면 None을 돌려줍니다."""
    global _consecutive_failures

    for attempt in range(MAX_ATTEMPTS):
        for _, url in TRANSLATION_ENDPOINTS:
            try:
                values = _request_batch(session, url, texts)
            except Exception:
                continue
            _consecutive_failures = 0
            return values

        if attempt < MAX_ATTEMPTS - 1:
            # 모든 경로가 한 바퀴 실패했습니다. 조금 쉬었다 다시 시도합니다.
            backoff = min(2 ** attempt, 8) * (1 + random.random() * 0.3)
            time.sleep(backoff)

    # 묶음 경로가 모두 막혔으면 호스트가 다른 마지막 경로를 한 건씩 시도합니다.
    try:
        values = [_request_single(session, text) for text in texts]
    except Exception:
        _consecutive_failures += 1
        return None

    _consecutive_failures = 0
    return values


def _circuit_open() -> bool:
    return _consecutive_failures >= CIRCUIT_FAILURE_LIMIT


def _needs_translation(text: str) -> bool:
    return bool(text) and bool(CHINESE_PATTERN.search(text))


def _translate_chunks(chunks: list[str]) -> dict[str, str]:
    """캐시에 없는 조각들만 묶어서 번역하고, 성공한 것만 돌려줍니다."""
    pending = [chunk for chunk in dict.fromkeys(chunks) if _cache_get(chunk) is None]
    if not pending:
        return {}

    translated: dict[str, str] = {}
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    batch: list[str] = []
    batch_chars = 0

    def flush() -> None:
        nonlocal batch, batch_chars
        if not batch:
            return
        values = _translate_batch(session, batch)
        if values:
            for source_text, value in zip(batch, values):
                cleaned = value.strip()
                if cleaned:
                    translated[source_text] = cleaned
                    _cache_put(source_text, cleaned)
        batch = []
        batch_chars = 0

    for chunk in pending:
        if _circuit_open():
            break
        if batch and (
            len(batch) >= MAX_BATCH_ITEMS or batch_chars + len(chunk) > MAX_BATCH_CHARS
        ):
            flush()
        batch.append(chunk)
        batch_chars += len(chunk)

    if not _circuit_open():
        flush()

    session.close()
    return translated


def translate_text(text: str) -> str:
    """중국어가 포함된 텍스트를 한국어로 번역합니다. 실패하면 예외를 던집니다."""
    clean_text = (text or "").strip()
    if not clean_text:
        return ""
    if not CHINESE_PATTERN.search(clean_text):
        return clean_text

    chunks = _split_text(clean_text)
    _translate_chunks(chunks)

    parts: list[str] = []
    for chunk in chunks:
        value = _cache_get(chunk)
        if value is None:
            if _circuit_open():
                raise RuntimeError("번역 경로가 모두 차단되어 이번 실행에서는 중단했습니다.")
            raise RuntimeError("번역 결과가 비어 있습니다.")
        parts.append(value)
    return "\n".join(parts)


def translate_posts(posts: list[dict]) -> list[dict]:
    """원문은 보존하고 title_ko, translated_text, translation_error를 추가합니다.

    먼저 번역이 필요한 문장을 모두 모아 한 번에 묶어 처리합니다. 같은 문장이
    여러 번 나와도 한 번만 번역하고, 이전 실행에서 번역해 둔 것은 디스크 캐시에서
    바로 꺼내 씁니다.
    """
    chunk_map: dict[int, dict[str, list[str]]] = {}
    all_chunks: list[str] = []

    for index, post in enumerate(posts):
        fields: dict[str, list[str]] = {}
        for field, source_key in (("title_ko", "title"), ("translated_text", "original_text")):
            raw = (post.get(source_key) or "").strip()
            if _needs_translation(raw):
                chunks = _split_text(raw)
                fields[field] = chunks
                all_chunks.extend(chunks)
            else:
                fields[field] = []
        chunk_map[index] = fields

    if all_chunks:
        _translate_chunks(all_chunks)
        save_cache()

    translated_posts: list[dict] = []
    for index, post in enumerate(posts):
        item = dict(post)
        errors: list[str] = []

        for field, source_key, label in (
            ("title_ko", "title", "제목"),
            ("translated_text", "original_text", "본문"),
        ):
            raw = (post.get(source_key) or "").strip()
            chunks = chunk_map[index][field]
            if not chunks:
                # 중국어가 없으면 원문을 그대로 씁니다(번역 불필요).
                item[field] = raw
                continue

            parts = [_cache_get(chunk) for chunk in chunks]
            if any(part is None for part in parts):
                item[field] = ""
                errors.append(f"{label}: 번역 실패")
            else:
                item[field] = "\n".join(str(part) for part in parts)

        item["translation_error"] = ", ".join(errors)
        translated_posts.append(item)

    return translated_posts
