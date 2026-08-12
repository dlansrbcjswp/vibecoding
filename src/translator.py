from __future__ import annotations

import re
import time
from functools import lru_cache

from deep_translator import GoogleTranslator


# 회사 PC의 Windows 인증서 저장소를 사용합니다.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass


CHINESE_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
MAX_CHUNK_LENGTH = 3000


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


@lru_cache(maxsize=2000)
def translate_text(text: str) -> str:
    """중국어가 포함된 텍스트를 한국어로 번역합니다."""
    clean_text = (text or "").strip()
    if not clean_text:
        return ""
    if not CHINESE_PATTERN.search(clean_text):
        return clean_text

    translator = GoogleTranslator(source="zh-CN", target="ko")
    translated_chunks: list[str] = []
    for chunk in _split_text(clean_text):
        translated = translator.translate(chunk)
        if not translated:
            raise RuntimeError("번역 결과가 비어 있습니다.")
        translated_chunks.append(translated.strip())
        time.sleep(0.1)
    return "\n".join(translated_chunks)


def translate_posts(posts: list[dict]) -> list[dict]:
    """원문은 보존하고 title_ko, translated_text, translation_error를 추가합니다."""
    translated_posts: list[dict] = []

    for post in posts:
        item = dict(post)
        errors: list[str] = []

        try:
            item["title_ko"] = translate_text(item.get("title", ""))
        except Exception as error:
            item["title_ko"] = ""
            errors.append(f"제목: {type(error).__name__}")

        try:
            item["translated_text"] = translate_text(
                item.get("original_text", "")
            )
        except Exception as error:
            item["translated_text"] = ""
            errors.append(f"본문: {type(error).__name__}")

        item["translation_error"] = ", ".join(errors)
        translated_posts.append(item)

    return translated_posts
