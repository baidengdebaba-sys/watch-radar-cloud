from __future__ import annotations

import base64
import html
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

import goofish_home as base

OUT = Path("data/latest.json")
PREV = Path(os.environ.get("WATCH_PREVIOUS_DATA", "/tmp/watchradar_previous.json"))
STATE_FILE = Path("/tmp/goofish_state.json")
MIN_PRICE = 30000
MAX_TARGETS = 58
ROTATING_MARKET_REFS = 26
DETAIL_VERIFY_LIMIT = 140

# These are the watches that currently appear in the external trend/ranking layer.
# They are always searched explicitly instead of hoping a broad brand search surfaces them.
PRIORITY_TARGETS = [
    ("126500LN", "劳力士"), ("214270", "劳力士"), ("126334", "劳力士"),
    ("126300", "劳力士"), ("126234", "劳力士"),
    ("W62016V3", "卡地亚"), ("W62017V3", "卡地亚"), ("W62054V3", "卡地亚"),
    ("W6206006", "卡地亚"),
    ("5711/1A", "百达翡丽"), ("5712/1A", "百达翡丽"), ("5726/1A", "百达翡丽"),
    ("5980/1A", "百达翡丽"), ("5066/1A", "百达翡丽"), ("5065/1A", "百达翡丽"),
    ("5396R", "百达翡丽"),
    ("26240ST.OO.1320ST.08", "爱彼"), ("15550ST.OO.1356ST.08", "爱彼"),
]

CORE_TARGETS = [
    ("126610LN", "劳力士"), ("126610LV", "劳力士"), ("126710BLRO", "劳力士"),
    ("126710BLNR", "劳力士"), ("116500LN", "劳力士"), ("124060", "劳力士"),
    ("15510ST", "爱彼"), ("15500ST", "爱彼"), ("26240ST", "爱彼"),
    ("5167A", "百达翡丽"), ("310.30.42.50.01.002", "欧米茄"),
]

DISCOVERY_QUERIES = ["劳力士", "百达翡丽", "爱彼", "欧米茄", "卡地亚", "江诗丹顿"]

BRAND_RULES = [
    ("劳力士", re.compile(r"劳力士|ROLEX|ロレックス", re.I)),
    ("欧米茄", re.compile(r"欧米茄|OMEGA|オメガ", re.I)),
    ("卡地亚", re.compile(r"卡地亚|CARTIER|カルティエ", re.I)),
    ("百达翡丽", re.compile(r"百达翡丽|PATEK|パテック", re.I)),
    ("爱彼", re.compile(r"爱彼|AUDEMARS|オーデマ|\bAP\b", re.I)),
    ("江诗丹顿", re.compile(r"江诗丹顿|VACHERON|ヴァシュロン", re.I)),
    ("万国", re.compile(r"万国|\bIWC\b", re.I)),
    ("帝舵", re.compile(r"帝舵|TUDOR|チューダー", re.I)),
    ("百年灵", re.compile(r"百年灵|BREITLING|ブライトリング", re.I)),
    ("宇舶", re.compile(r"宇舶|HUBLOT|ウブロ", re.I)),
    ("沛纳海", re.compile(r"沛纳海|PANERAI|パネライ", re.I)),
    ("真力时", re.compile(r"真力时|ZENITH|ゼニス", re.I)),
    ("宝玑", re.compile(r"宝玑|BREGUET|ブレゲ", re.I)),
    ("积家", re.compile(r"积家|JAEGER|ジャガー", re.I)),
    ("香奈儿", re.compile(r"香奈儿|CHANEL|シャネル", re.I)),
    ("冠蓝狮", re.compile(r"冠蓝狮|GRAND\s*SEIKO|グランドセイコー", re.I)),
]

CITY_RE = re.compile(
    r"(?:北京|上海|天津|重庆|深圳|广州|杭州|苏州|南京|成都|武汉|西安|长沙|郑州|青岛|厦门|福州|宁波|无锡|"
    r"佛山|东莞|沈阳|大连|济南|合肥|昆明|贵阳|南宁|海口|三亚|石家庄|太原|长春|哈尔滨|珠海|温州|"
    r"绍兴|嘉兴|常州|泉州|南昌|烟台|潍坊|徐州|扬州|镇江|金华|义乌|台州|惠州|中山|汕头|南通|"
    r"河北|河南|山东|山西|陕西|江苏|浙江|安徽|福建|江西|湖北|湖南|广东|广西|海南|四川|贵州|云南|"
    r"辽宁|吉林|黑龙江|内蒙古|新疆|西藏|宁夏|甘肃|青海)"
)


def log(*args):
    print(datetime.now().strftime("%H:%M:%S"), *args, flush=True)


def textify(raw: str) -> str:
    s = html.unescape(raw or "")
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def chinese_brand(label: str) -> str:
    for brand, rx in BRAND_RULES:
        if rx.search(label or ""):
            return brand
    return ""


def fetch_catamap_targets():
    out = []
    seen = set()
    for page_no in range(1, 6):
        url = "https://reuse.catamap.app/categories/watch" + ("" if page_no == 1 else f"?page={page_no}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Chrome/154 Safari/537.36", "Accept-Language": "ja-JP,ja;q=0.9"})
            with urllib.request.urlopen(req, timeout=18) as r:
                txt = textify(r.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            log("catamap target fetch failed", page_no, type(e).__name__)
            continue
        pattern = re.compile(r"(.{2,110}?)\s+([A-Za-z0-9][A-Za-z0-9./-]{2,})\s+中古の中央値\s*／\s*直近90日\s*¥[0-9,]+", re.I)
        for m in pattern.finditer(txt):
            label = re.sub(r"\s+", " ", m.group(1)).strip()
            ref = m.group(2).strip()
            key = ref.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append((ref, chinese_brand(label), label))
    return out


def core_ref(ref: str) -> str:
    raw = str(ref or "").strip().upper()
    if ".OO." in raw:
        raw = raw.split(".OO.", 1)[0]
    return base.normalize_ref(raw) or raw


def compact_model(s: str) -> str:
    s = str(s or "").upper().replace("\u200b", "")
    s = re.sub(r"(?<![A-Z0-9])M(?=\d{4,})", "", s)
    return re.sub(r"[^A-Z0-9]", "", s)


def title_brand(title: str) -> str:
    for brand, rx in BRAND_RULES:
        if rx.search(title or ""):
            return brand
    return ""


def title_matches_target(title: str, ref: str, brand: str) -> bool:
    key = core_ref(ref)
    if not key:
        return False
    raw = str(title or "").upper()
    wanted_brand = brand or ""
    got_brand = title_brand(raw)
    if wanted_brand and got_brand and wanted_brand != got_brand:
        return False

    nk = compact_model(key)
    nt = compact_model(raw)
    if len(nk) >= 6:
        return nk in nt

    # Short references must be a complete token. This prevents 14270 matching 214270.
    esc = re.escape(key)
    token = re.compile(rf"(?<![A-Z0-9])M?{esc}(?![A-Z0-9])", re.I)
    if token.search(raw):
        return bool(got_brand or wanted_brand)
    return False


def build_targets():
    fixed = [(r, b, "priority") for r, b in PRIORITY_TARGETS + CORE_TARGETS]
    market = fetch_catamap_targets()
    if market:
        slot = int(datetime.now(timezone.utc).timestamp() // 3600)
        start = (slot * ROTATING_MARKET_REFS) % len(market)
        rotated = [market[(start + i) % len(market)] for i in range(min(ROTATING_MARKET_REFS, len(market)))]
        fixed.extend((r, b, "market-rotation") for r, b, _label in rotated)

    seen = set()
    targets = []
    for ref, brand, source in fixed:
        k = (str(ref).upper(), brand)
        if k in seen:
            continue
        seen.add(k)
        targets.append({"ref": ref, "brand": brand, "source": source, "query": f"{brand} {core_ref(ref)}".strip()})
        if len(targets) >= MAX_TARGETS - len(DISCOVERY_QUERIES):
            break

    for q in DISCOVERY_QUERIES:
        targets.append({"ref": None, "brand": q, "source": "discovery", "query": q})
    return targets[:MAX_TARGETS]


def clean_area(value: str) -> str:
    v = re.sub(r"\s+", " ", str(value or "")).strip(" ·|/")
    v = re.sub(r"^(?:发布于|来自|所在地|发货地|地区)[:：\s]*", "", v)
    if not v or len(v) > 18:
        return ""
    if re.search(r"¥|￥|元|想要|浏览|分钟前|小时前|天前", v):
        return ""
    return v if CITY_RE.search(v) else ""


def detect_area(card, raw_lines):
    selectors = [
        '[class*="area"]', '[class*="location"]', '[class*="region"]', '[class*="address"]',
        '[class*="city"]', '[class*="position"]'
    ]
    for sel in selectors:
        try:
            loc = card.locator(sel)
            for i in range(min(loc.count(), 4)):
                v = clean_area(loc.nth(i).inner_text(timeout=400))
                if v:
                    return v
        except Exception:
            pass
    for line in reversed(raw_lines[-8:]):
        v = clean_area(line)
        if v:
            return v
    return ""


def detect_seller(card):
    for sel in ['[class*="user-name"]', '[class*="seller"]', '[class*="nickname"]', '[class*="nick"]']:
        try:
            loc = card.locator(sel)
            if loc.count():
                v = re.sub(r"\s+", " ", loc.first.inner_text(timeout=400)).strip()
                if 1 < len(v) <= 40:
                    return v
        except Exception:
            pass
    return ""


def scan_target(page, target, quotes, limit=100):
    query_text = target["query"]
    page.goto("https://www.goofish.com/search?q=" + quote(query_text), wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1200)
    for _ in range(4):
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(450)

    body = page.locator("body").inner_text(timeout=10000) or ""
    if re.search(r"验证码|安全验证|异常访问|非法访问", body):
        raise RuntimeError("GOOFISH_SECURITY_VERIFICATION")
    cards = page.locator('a[href*="/item?id="]')
    if cards.count() == 0 and re.search(r"请先登录|登录后", body):
        raise RuntimeError("GOOFISH_LOGIN_EXPIRED")

    now = datetime.now(timezone.utc).isoformat()
    out = []
    for i in range(min(cards.count(), limit)):
        card = cards.nth(i)
        try:
            href = card.get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://www.goofish.com" + href
            iid = base.item_id(href)
            if not iid:
                continue

            raw_text = card.inner_text(timeout=1500) or ""
            raw_lines = [re.sub(r"\s+", " ", x).strip() for x in raw_text.splitlines() if x.strip()]
            text = re.sub(r"\s+", " ", raw_text).strip()
            title = ""
            for sel in ['[class*="row1-wrap-title"]', '[class*="main-title"]', '[class*="title"]']:
                loc = card.locator(sel)
                if loc.count():
                    title = re.sub(r"\s+", " ", loc.first.inner_text(timeout=800)).strip()
                    if len(title) >= 4:
                        break
            if not title:
                title = text[:300]

            if target.get("ref") and not title_matches_target(title, target["ref"], target.get("brand") or ""):
                continue

            pw = card.locator('[class*="price-wrap"], [class*="price"]')
            ptxt = re.sub(r"\s+", " ", pw.first.inner_text(timeout=700)).strip() if pw.count() else ""
            price = base.parse_price(ptxt)
            if not price or price < MIN_PRICE:
                continue

            parsed = base.ref_from_text(title)
            target_ref = target.get("ref")
            if target_ref and not parsed:
                parsed = core_ref(target_ref)
            meta = base.annotate(title)
            area = detect_area(card, raw_lines)
            seller = detect_seller(card)
            image = ""
            try:
                im = card.locator("img")
                if im.count():
                    image = im.first.get_attribute("src") or im.first.get_attribute("data-src") or ""
            except Exception:
                pass
            jp = base.jp_compare_for(parsed, meta["condition_label"], quotes)
            warnings = meta["warning_keywords"]
            risk = "high" if warnings else ("low" if parsed else "medium")
            out.append({
                "item_id": iid,
                "keyword": query_text,
                "target_ref": target_ref,
                "target_source": target.get("source"),
                "title": title,
                "price_cny": price,
                "search_price_cny": price,
                "price_verified": False,
                "price_source": "targeted_search_card",
                "price_raw_text": ptxt,
                "detail_title_match": False,
                "seller": seller,
                "area": area,
                "badge": "",
                "item_url": href,
                "image_url": image,
                "publish_time": None,
                "parsed_ref": parsed,
                "risk_level": risk,
                "first_seen_at": now,
                "last_seen_at": now,
                "seen_in_latest_scan": True,
                "jp_compare": jp,
                **meta,
            })
        except Exception:
            continue
    return base.dedupe_best(out)


def load_state():
    raw = os.environ.get("GOOFISH_STORAGE_STATE_B64", "").strip()
    if not raw:
        return False
    try:
        STATE_FILE.write_bytes(base64.b64decode(raw))
        return True
    except Exception:
        return False


def main():
    data = base.load_existing()
    previous = base.load_json(PREV) or {}
    previous_items = previous.get("items") if isinstance(previous, dict) else []
    quotes = data.get("jp_quotes") if isinstance(data.get("jp_quotes"), list) else []

    if not load_state():
        data["goofish_status"] = "not_configured"
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    proxy = os.environ.get("WATCH_SOCKS_PROXY", "").strip()
    if not proxy:
        data["goofish_status"] = "home_proxy_missing"
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    targets = build_targets()
    log("targeted search targets", len(targets), ", ".join(t["query"] for t in targets[:20]))
    all_items = []
    errors = []
    searched = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-dev-shm-usage"])
        ctx = browser.new_context(storage_state=str(STATE_FILE), locale="zh-CN", proxy={"server": proxy})
        page = ctx.new_page()

        try:
            page.goto("https://api.ipify.org", wait_until="domcontentloaded", timeout=20000)
            exit_ip = (page.locator("body").inner_text(timeout=5000) or "").strip()
            log("HOME_EXIT_OK", bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", exit_ip)))
        except Exception as e:
            errors.append(f"goofish proxy check: {type(e).__name__}")

        for idx, target in enumerate(targets, 1):
            try:
                rows = scan_target(page, target, quotes)
                all_items.extend(rows)
                searched += 1
                log("target", idx, target["query"], len(rows))
            except Exception as e:
                errors.append(f"goofish target {target['query']}: {e}")
                log("target ERROR", target["query"], e)
                if "LOGIN_EXPIRED" in str(e) or "SECURITY" in str(e):
                    break

        unique_current = base.dedupe_best(all_items)
        order = sorted(unique_current, key=lambda x: (0 if x.get("target_ref") else 1, 0 if x.get("parsed_ref") else 1, -base.candidate_score(x)))
        detail_page = ctx.new_page()
        verified_by_id = {}
        verified_count = 0
        for idx, item in enumerate(order[:DETAIL_VERIFY_LIMIT], 1):
            try:
                y = base.verify_detail_price(detail_page, item)
                verified_by_id[y["item_id"]] = y
                if y.get("price_verified"):
                    verified_count += 1
                if idx % 25 == 0:
                    log("detail_verify", idx, "verified", verified_count)
            except RuntimeError as e:
                if "SECURITY" in str(e):
                    errors.append("goofish detail verify: SECURITY_VERIFICATION")
                    break
        detail_page.close()
        unique_current = [verified_by_id.get(x["item_id"], x) for x in unique_current]
        browser.close()

    data["items"] = base.merge_recent(unique_current, previous_items, quotes)
    data["goofish_status"] = "ok" if unique_current else ("blocked_or_expired" if errors else "empty")
    data["goofish_scan"] = {
        "strategy": "ranking_targeted_v2",
        "targets_planned": len(targets),
        "targets_searched": searched,
        "current_scan_items": len(unique_current),
        "saved_recent_items": len(data["items"]),
        "min_price_cny": MIN_PRICE,
        "retention_days": base.RETENTION_DAYS,
        "detail_verify_limit": DETAIL_VERIFY_LIMIT,
        "detail_verified_items": sum(1 for x in unique_current if x.get("price_verified") is True),
        "detail_unverified_items": sum(1 for x in unique_current if x.get("price_verified") is not True),
        "items_with_area": sum(1 for x in unique_current if x.get("area")),
        "priority_refs": [x[0] for x in PRIORITY_TARGETS],
        "target_queries_sample": [t["query"] for t in targets[:20]],
    }
    data.setdefault("errors", [])
    data["errors"] = [x for x in data["errors"] if not str(x).startswith("goofish ")]
    data["errors"].extend(errors)
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["runner"] = "github-actions-home-exit-targeted"
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log("wrote", len(unique_current), "targeted items; areas", data["goofish_scan"]["items_with_area"], "verified", data["goofish_scan"]["detail_verified_items"])


if __name__ == "__main__":
    main()
