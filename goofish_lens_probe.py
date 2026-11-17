"""One-off: 闲鱼 asking prices for a short list of camera lenses (page 1 of each search only).

Reuses the logged-in session and the home exit; never saves the session back.
Paced slowly on purpose: the account was rate-limited earlier when runs were heavy.
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

import goofish_home as base
import goofish_session as session
import goofish_deep as deep

STATE_FILE = Path("/tmp/goofish_state.json")
OUT = Path("lens_probe.json")
MIN_CNY = 300

LENSES = [
    ("sony2470gm2", "SEL2470GM2", r"2470\s*GM\s*(II|2)|24-70.{0,12}GM\s*(II|2|二代)"),
    ("sony70200gm2", "SEL70200GM2", r"70200\s*GM\s*(II|2)|70-200.{0,12}GM\s*(II|2|二代)"),
    ("sony35gm", "SEL35F14GM", r"35F14GM|35\s*(mm)?\s*f?1\.4\s*GM"),
    ("rf2470", "佳能 RF24-70 2.8", r"RF\s?24-70.{0,8}2\.8"),
    ("rf70200", "佳能 RF70-200 2.8", r"RF\s?70-200.{0,8}2\.8"),
    ("z2470", "尼康 Z 24-70 2.8", r"Z\s?24-70.{0,8}2\.8"),
    ("z70200", "尼康 Z 70-200 2.8", r"Z\s?70-200.{0,8}2\.8"),
    ("xf56wr", "富士 XF56 1.2 WR", r"56.{0,6}1\.2.{0,10}WR|56.{0,4}二代"),
    ("fd55", "佳能 FD 55 1.2", r"FD.{0,8}55.{0,6}1\.2"),
    ("ais5012", "尼康 50 1.2 AIS", r"50.{0,6}1\.2.{0,12}AI-?S|AI-?S.{0,12}50.{0,6}1\.2"),
    ("rokkor58", "美能达 58 1.2", r"58.{0,6}1\.2"),
    ("contax5014", "康泰时 50 1.4", r"50.{0,6}1\.4"),
    ("mamiya7_80", "玛米亚7 80 f4", r"80.{0,6}(f|F)?\s?4"),
    ("hassel80cf", "哈苏 CF 80 2.8", r"80.{0,8}2\.8"),
    ("takumar5014", "takumar 50 1.4", r"50.{0,6}1\.4"),
]
BAD = re.compile(r"转接|接环|遮光罩|镜头盖|后盖|滤镜|UV|保护镜|贴膜|配件|零件|拆机|维修|故障|发霉|霉|雾|起雾|求购|收\s|回收|租|出租|置换|仅镜头盒|空盒|盒子|包装盒|说明书")


def main():
    if session.load_into(STATE_FILE) is None:
        raise SystemExit("no session")
    proxy = os.environ.get("WATCH_SOCKS_PROXY", "").strip()
    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-dev-shm-usage"])
        ctx = browser.new_context(storage_state=str(STATE_FILE), locale="zh-CN", proxy={"server": proxy})
        page = ctx.new_page()
        for lid, q, rx in LENSES:
            try:
                page.goto("https://www.goofish.com/search?q=" + quote(q), wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(deep.pause(3, 5))
                body = page.locator("body").inner_text(timeout=10000) or ""
                if re.search(r"验证码|安全验证|异常访问|非法访问", body[:4000]):
                    out[lid] = {"error": "security"}
                    break
                for _ in range(3):
                    page.mouse.wheel(0, 2400)
                    page.wait_for_timeout(400)
                rows = []
                for r in deep.card_rows(page):
                    text = f"{r['title']} {r['text']}"
                    price = base.parse_price(r["price"])
                    if not price or price < MIN_CNY or BAD.search(text) or deep.REPLICA_RE.search(text):
                        continue
                    if not re.search(rx, text, re.I):
                        continue
                    rows.append({"title": r["title"][:120], "price_cny": price})
                out[lid] = {"query": q, "cards": len(rows), "rows": rows}
                print(lid, q, "matched", len(rows), sorted(x["price_cny"] for x in rows)[:12], flush=True)
            except Exception as e:
                out[lid] = {"error": type(e).__name__}
                print(lid, "ERROR", type(e).__name__, flush=True)
            page.wait_for_timeout(int(random.uniform(9, 15) * 1000))
        browser.close()
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
