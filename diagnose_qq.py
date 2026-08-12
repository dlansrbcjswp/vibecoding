"""QQ 채널 구조 진단 스크립트.

수집이 왜 얕은지 추측하지 않고 실제 페이지 구조를 그대로 기록합니다.
아무것도 수정하지 않고 읽기만 합니다.

실행: DIAGNOSE_qq.bat 더블클릭
결과: qq_diagnose.txt 파일 생성 -> 이 파일을 그대로 전달
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "qq_diagnose.txt"
LINES: list[str] = []


def log(text: str = "") -> None:
    print(text)
    LINES.append(text)


def dump(label: str, value: object, limit: int = 4000) -> None:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        text = str(value)
    if len(text) > limit:
        text = text[:limit] + f"\n... (이하 {len(text) - limit}자 생략)"
    log(f"--- {label} ---")
    log(text)
    log()


def main() -> None:
    log("=" * 60)
    log("QQ 채널 구조 진단")
    log(f"실행 시각 : {datetime.now():%Y-%m-%d %H:%M:%S}")
    log("=" * 60)
    log()

    from playwright.sync_api import sync_playwright

    from src.collector import (
        QQ_CHANNEL_URL,
        _launch_installed_chromium,
        _read_qq_cards,
    )

    log(f"기준 URL : {QQ_CHANNEL_URL}")
    log()

    with sync_playwright() as playwright:
        browser, browser_label = _launch_installed_chromium(playwright)
        log(f"사용 브라우저 : {browser_label}")
        log()
        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 2400},
        )
        page = context.new_page()

        feed_urls: list[str] = []

        def on_response(response):
            url = response.url
            if any(k in url for k in ("vecFeed", "feed", "Feed", "post", "list")):
                feed_urls.append(f"{response.status} {url[:220]}")

        page.on("response", on_response)

        log("[1] 채널 페이지 접속")
        page.goto(QQ_CHANNEL_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(6_000)
        log(f"    최종 URL : {page.url}")
        log(f"    제목     : {page.title()}")
        log()

        log("[2] 게시판(탭) 목록 - pinia commonGuildStore")
        try:
            tabs = page.evaluate(
                """
                () => {
                  const out = {found:false, hiddenChannelId:null, tabs:[]};
                  const nuxt = window.__NUXT__ || {};
                  const pinia = nuxt.pinia || (nuxt.state && nuxt.state.pinia) || {};
                  const store = pinia.commonGuildStore;
                  if (store) {
                    out.found = true;
                    out.hiddenChannelId = store.hiddenChannelId || null;
                    const list = store.tabInfoList || [];
                    for (const t of list) {
                      out.tabs.push({
                        channel_id: t.channel_id,
                        name: t.name,
                        type: t.type,
                        sub_type: t.sub_type
                      });
                    }
                  }
                  return out;
                }
                """
            )
            dump("tabInfoList", tabs)
        except Exception as error:
            log(f"    실패: {error}")
            log()

        log("[3] __NUXT__ 최상위 키 구조")
        try:
            keys = page.evaluate(
                """
                () => {
                  const walk = (obj, depth) => {
                    if (depth > 2 || obj === null || typeof obj !== 'object') return null;
                    const out = {};
                    for (const k of Object.keys(obj).slice(0, 40)) {
                      const v = obj[k];
                      if (v && typeof v === 'object') {
                        out[k] = Array.isArray(v)
                          ? `[array ${v.length}]`
                          : (walk(v, depth + 1) || '{object}');
                      } else {
                        out[k] = typeof v;
                      }
                    }
                    return out;
                  };
                  return walk(window.__NUXT__ || {}, 0);
                }
                """
            )
            dump("__NUXT__ 구조", keys, limit=3000)
        except Exception as error:
            log(f"    실패: {error}")
            log()

        log("[4] 화면에 보이는 게시글 카드")
        try:
            cards = _read_qq_cards(page)
            log(f"    카드 수 : {len(cards)}")
            for item in cards[:5]:
                log(f"    - {json.dumps(item, ensure_ascii=False)[:200]}")
        except Exception as error:
            log(f"    실패: {error}")
        log()

        log("[5] 스크롤 가능한 내부 컨테이너 탐지")
        try:
            containers = page.evaluate(
                """
                () => {
                  const out = [];
                  for (const el of document.querySelectorAll('*')) {
                    if (el.scrollHeight <= el.clientHeight + 120) continue;
                    const oy = getComputedStyle(el).overflowY;
                    if (oy !== 'auto' && oy !== 'scroll') continue;
                    out.push({
                      tag: el.tagName,
                      cls: (el.className || '').toString().slice(0, 60),
                      scrollHeight: el.scrollHeight,
                      clientHeight: el.clientHeight
                    });
                  }
                  return out.slice(0, 10);
                }
                """
            )
            dump("스크롤 컨테이너", containers, limit=2000)
        except Exception as error:
            log(f"    실패: {error}")
            log()

        log("[6] 스크롤 10회 후 카드 수 변화")
        try:
            before = len(_read_qq_cards(page))
            for _ in range(10):
                page.evaluate(
                    """
                    () => {
                      for (const el of document.querySelectorAll('*')) {
                        if (el.scrollHeight > el.clientHeight + 120) {
                          const oy = getComputedStyle(el).overflowY;
                          if (oy === 'auto' || oy === 'scroll') {
                            el.scrollTop = el.scrollHeight;
                          }
                        }
                      }
                      window.scrollTo(0, document.documentElement.scrollHeight);
                    }
                    """
                )
                page.wait_for_timeout(1_200)
            after = len(_read_qq_cards(page))
            log(f"    스크롤 전 : {before}건")
            log(f"    스크롤 후 : {after}건")
            log(f"    증가      : {after - before}건")
            if after <= before:
                log("    -> 스크롤로 추가 로딩이 발생하지 않았습니다.")
                log("       비로그인 노출 제한일 가능성이 높습니다.")
        except Exception as error:
            log(f"    실패: {error}")
        log()

        log("[7] 관찰된 네트워크 요청")
        seen = []
        for item in feed_urls:
            if item not in seen:
                seen.append(item)
        log(f"    총 {len(seen)}건")
        for item in seen[:25]:
            log(f"    - {item}")
        log()

        log("[8] 게시판별 접속 시 카드 수")
        try:
            tab_list = (tabs or {}).get("tabs") or []
        except Exception:
            tab_list = []
        if not tab_list:
            # 탭을 못 읽었을 때 알려진 게시판 ID로라도 확인합니다.
            tab_list = [
                {"channel_id": "688289023", "name": "帖子广场(폴백)"},
                {"channel_id": "690970370", "name": "官方资讯(폴백)"},
            ]
            log("    탭 목록을 못 읽어 폴백 ID로 확인합니다.")
        for tab in tab_list[:8]:
            cid = tab.get("channel_id")
            name = tab.get("name")
            if not cid:
                continue
            url = f"{QQ_CHANNEL_URL}?subc={cid}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(4_000)
                count = len(_read_qq_cards(page))
                log(f"    {name} ({cid}) : {count}건  <- {url}")
            except Exception as error:
                log(f"    {name} ({cid}) : 실패 {error}")
        log()

        log("[9] subc 이름값 확인 (hot / new 등)")
        for keyword in ("hot", "new", "latest", "recent"):
            url = f"{QQ_CHANNEL_URL}?subc={keyword}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(4_000)
                cards_kw = _read_qq_cards(page)
                dates = sorted(
                    {
                        str(item.get("published_at") or item.get("date") or "")
                        for item in cards_kw
                    }
                    - {""}
                )
                log(
                    f"    subc={keyword:<7} : {len(cards_kw)}건"
                    + (f" · 날짜 {dates[0]} ~ {dates[-1]}" if dates else "")
                )
            except Exception as error:
                log(f"    subc={keyword:<7} : 실패 {type(error).__name__}")
        log()

        context.close()
        browser.close()

    log("=" * 60)
    log("진단 완료")
    log("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LINES.append("")
        LINES.append("!!! 오류 발생 !!!")
        LINES.append(traceback.format_exc())
        print(traceback.format_exc())
    finally:
        OUT.write_text("\n".join(LINES), encoding="utf-8")
        print()
        print(f"결과 파일 : {OUT}")
