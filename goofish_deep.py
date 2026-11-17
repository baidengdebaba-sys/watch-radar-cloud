"""Deeper Goofish collection: query variants, pagination, and full-description parsing.

Search cards only show the first two lines of a listing, and many sellers write the
reference number (or the dial / card year / full-set info) further down. So cards are
filtered loosely, and the decision is made on the detail page's full description.
Already-checked listings are not reopened unless their price changed or the check is old.
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import goofish_home as base

MIN_PRICE = 30000
MAX_PAGES = 5
SEARCH_PAGE_CAP = 70   # 每轮最多翻的搜索结果页，防止触发闲鱼风控
DETAIL_REFRESH_DAYS = 3

# 常见昵称：只用来多搜一轮，最终型号仍以详情描述里的编号为准
NICKNAMES = {
    "126500LN": ["熊猫迪"],
    "126610LV": ["绿水鬼 126610"],
    "126610LN": ["黑水鬼 126610"],
    "126710BLRO": ["百事圈"],
    "126710BLNR": ["蝙蝠侠 126710"],
    "126720VTNR": ["雪碧圈"],
}
# 卡片上没写编号时，必须同时出现品牌和这些系列词才进候选，再由详情描述确认型号
SERIES_HINTS = {
    "126500": ["迪通拿", "熊猫"], "116500": ["迪通拿", "熊猫"],
    "214270": ["探险家"], "124270": ["探险家"],
    "126334": ["日志"], "126300": ["日志"],
    "126610": ["水鬼", "潜航者"], "124060": ["水鬼", "潜航者"],
    "126710": ["格林尼治", "百事", "蝙蝠"], "126720": ["格林尼治", "雪碧"],
    "5711": ["鹦鹉螺", "NAUTILUS"], "5712": ["鹦鹉螺", "NAUTILUS"], "5990": ["鹦鹉螺", "NAUTILUS"],
    "5167": ["手雷", "AQUANAUT"], "5168": ["手雷", "AQUANAUT"], "5066": ["手雷", "AQUANAUT"], "5065": ["手雷", "AQUANAUT"],
    "5396": ["年历"], "5205": ["年历"],
    "15510": ["皇家橡树"], "15500": ["皇家橡树"], "15400": ["皇家橡树"], "15202": ["皇家橡树"],
    "26240": ["皇家橡树"], "26331": ["皇家橡树"], "15550": ["皇家橡树"],
}
# 只有数字时用描述里的关键词补后缀；补不出来就保持无后缀（不参与日本比价）
ROLEX_SUFFIX = {
    "126500": [("LN", r".")],
    "116500": [("LN", r".")],
    "126610": [("LV", r"LV|绿水鬼|绿圈|绿色圈"), ("LN", r"LN|黑水鬼|黑圈|黑色圈")],
    "116610": [("LV", r"LV|绿水鬼|绿圈|绿盘"), ("LN", r"LN|黑水鬼|黑圈")],
    "126710": [("BLRO", r"BLRO|百事|红蓝"), ("BLNR", r"BLNR|蝙蝠|蓝黑")],
    "126720": [("VTNR", r"VTNR|雪碧|绿黑")],
    "126711": [("CHNR", r"CHNR|沙士|可乐")],
}
REPLICA_RE = re.compile(
    r"复刻|高仿|精仿|仿品|假表|1[:：比]1|一比一|超A|顶级版|组装|拼装|副厂|代用|"
    r"(?<![原工出])(?:clean|vsf?|zf|3k|ew|bt|ar|jf|noob|apf|gmf?|tw|bvf?|ipf|kw|ks|vr|c)\s*厂",
    re.I,
)
DEALER_RE = re.compile(r"实体店|门店|店铺|现货|收表|回收|寄卖|置换|主理人|同行|批发|支持鉴定|可送检|一物一图")
PERSONAL_RE = re.compile(r"个人闲置|个人出|自用|非商家|本人|一手自用|老公|老婆|送人")
ZH_DIAL = [
    ("black", r"黑熊猫|黑迪|黑钢迪|黑水鬼|黑(?:色)?(?:表盘|盘|面|字面)"),
    ("white", r"白熊猫|熊猫迪|熊猫|雪山白|白(?:色)?(?:表盘|盘|面|字面)"),
    ("iceblue", r"冰蓝"),
    ("meteorite", r"陨石"),
    ("champagne", r"香槟"),
    ("chocolate", r"(?:巧克力|棕)(?:色)?(?:表盘|盘|面)"),
    ("grey", r"灰(?:色)?(?:表盘|盘|面)"),
    ("silver", r"银(?:色)?(?:表盘|盘|面)"),
    ("blue", r"蓝(?:色)?(?:表盘|盘|面)"),
    ("green", r"绿(?:色)?(?:表盘|盘|面)"),
]


def log(*a):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def pause(lo=1.4, hi=2.8):
    return int(random.uniform(lo, hi) * 1000)


def ref_number(ref: str) -> str | None:
    """The searchable core: 126500LN -> 126500, 5711/1A -> 5711, 15510ST -> 15510."""
    r = str(ref or "").upper()
    m = re.match(r"(\d{4,6})", r)
    if m and (len(m.group(1)) >= 5 or "/" in r or re.match(r"\d{4}[A-Z]", r)):
        return m.group(1)
    return None


def queries_for(target: dict) -> list[dict]:
    ref, brand = target["ref"], target.get("brand") or ""
    num = ref_number(ref)
    out = []
    if num and len(num) >= 5:
        out.append({"q": num, "pages": MAX_PAGES, "num": num})
    elif num:
        out.append({"q": f"{brand} {num}".strip(), "pages": MAX_PAGES, "num": num})
    else:
        out.append({"q": f"{brand} {ref}".strip(), "pages": 2})
    for nick in NICKNAMES.get(base.normalize_ref(ref) or ref, []):
        out.append({"q": nick, "pages": 2})
    return out


def token_in(text: str, num: str) -> bool:
    t = str(text or "").upper().replace("​", "")
    rx = re.compile(rf"(?<![0-9]){re.escape(num)}(?![0-9])")
    if rx.search(t):
        return True
    # 也容忍卖家把编号拆开写，如 "m1265 00LN"
    return bool(rx.search(re.sub(r"(?<=\d) (?=\d{1,3}[A-Z])", "", t)))


def resolve_ref(desc: str, target_ref: str) -> tuple[str | None, str]:
    """Return (ref, how). Prefer an explicit full reference in the text."""
    num = ref_number(target_ref)
    full = base.ref_from_text(desc)
    if full and num and full.startswith(num) and full != num:
        return full, "description"
    if num and token_in(desc, num):
        hints = ROLEX_SUFFIX.get(num)
        if hints:
            for suf, rx in hints:
                if re.search(rx, desc, re.I):
                    return num + suf, "description+hint"
            return num, "number_only"
        norm = base.normalize_ref(target_ref)
        return norm, "description"
    if full and not (num and full.startswith(num)):
        return full, "other_model"
    return None, "not_found"


def dial_of(text: str) -> str | None:
    for key, rx in ZH_DIAL:
        if re.search(rx, text):
            return key
    return None


def seller_type(text: str) -> str:
    if PERSONAL_RE.search(text) and not re.search(r"实体店|门店|主理人", text):
        return "个人"
    if DEALER_RE.search(text):
        return "商家"
    return "未知"


def card_rows(page) -> list[dict]:
    """Read every listing card on the current results page (no model filtering here)."""
    rows = page.evaluate("""() => [...document.querySelectorAll('a[href*="/item?id="]')].map(a => {
        const t = a.querySelector('[class*="row1-wrap-title"]') || a.querySelector('[class*="title"]');
        const p = a.querySelector('[class*="row3-wrap-price"]') || a.querySelector('[class*="price"]');
        const area = a.querySelector('[class*="area"], [class*="location"]');
        const img = a.querySelector('img');
        return { href: a.href, title: (t ? t.innerText : a.innerText).replace(/\\s+/g, ' ').trim().slice(0, 300),
                 text: a.innerText.replace(/\\s+/g, ' ').trim().slice(0, 400), price: p ? p.innerText.replace(/\\s+/g, ' ') : '',
                 area: area ? area.innerText.trim().slice(0, 20) : '', image: img ? (img.src || img.dataset.src || '') : '' };
    })""")
    return rows or []


def goto_page(page, n: int) -> bool:
    ok = page.evaluate("""(n) => {
        const box = [...document.querySelectorAll('[class*="search-pagination-page-box"]')].find(e => e.textContent.trim() === String(n));
        if (!box) return false; box.click(); return true; }""", n)
    if ok:
        page.wait_for_timeout(pause(2.2, 3.4))
    return bool(ok)


_QUERY_CACHE: dict[str, list[dict]] = {}
_STATE = {"pages": 0, "empty_streak": 0}


def search_query(page, q: str, pages: int, apply_min_price, check_page, num: str | None = None) -> list[dict]:
    """Same query for two targets (126610LN/LV, 126710BLRO/BLNR) is searched once per run."""
    if q in _QUERY_CACHE:
        log("search", q, "cached", len(_QUERY_CACHE[q]))
        return _QUERY_CACHE[q]
    out = _search_query(page, q, pages, apply_min_price, check_page, num)
    _QUERY_CACHE[q] = out
    return out


def _search_query(page, q: str, pages: int, apply_min_price, check_page, num: str | None = None) -> list[dict]:
    page.goto("https://www.goofish.com/search?q=" + quote(q), wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(pause(1.2, 2.0))
    check_page(page)
    if _STATE["pages"] >= SEARCH_PAGE_CAP:
        log("SEARCH_PAGE_CAP", q)
        return []
    if not page.query_selector('a[href*="/item?id="]'):
        page.wait_for_timeout(pause(3, 5))
    if not page.query_selector('a[href*="/item?id="]'):
        # 整页空白：等一会儿重载一次；连续 3 个词都空白说明被闲鱼限流了，整轮停止搜索
        log("EMPTY_RESULTS", q)
        page.wait_for_timeout(pause(15, 25))
        page.reload(wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(pause(3, 5))
        check_page(page)
        if not page.query_selector('a[href*="/item?id="]'):
            _STATE["empty_streak"] += 1
            if _STATE["empty_streak"] >= 3:
                raise RuntimeError("GOOFISH_THROTTLED: search results empty 3 times in a row")
            return []
    _STATE["empty_streak"] = 0
    if not apply_min_price(page):
        log("PRICE_FILTER_NOT_APPLIED", q)
    out, seen = [], set()
    for n in range(1, pages + 1):
        if _STATE["pages"] >= SEARCH_PAGE_CAP:
            log("SEARCH_PAGE_CAP", q)
            break
        if n > 1 and not goto_page(page, n):
            break
        _STATE["pages"] += 1
        for _ in range(3):
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(350)
        check_page(page)
        rows = [r for r in card_rows(page) if r["href"] not in seen]
        for r in rows:
            seen.add(r["href"])
        good = [r for r in rows if (base.parse_price(r["price"]) or 0) >= MIN_PRICE]
        out.extend(good)
        hits = sum(1 for r in good if token_in(f"{r['title']} {r['text']}", num)) if num else len(good)
        log("search", q, "page", n, "cards", len(rows), ">=min", len(good), "with_ref", hits)
        # 结果开始偏到别的型号（页内带这个编号的不足 5 条）就不再往后翻
        if len(good) < 6 or hits < 5:
            break
    return out


def loose_match(card: dict, target: dict) -> bool:
    """Candidate for this target: the number is on the card, or brand + series word with no other number."""
    text = f"{card.get('title', '')} {card.get('text', '')}"
    if REPLICA_RE.search(text):
        return False
    ref = str(target["ref"] or "")
    num = ref_number(ref)
    if num and token_in(text, num):
        return True
    if not num:
        key = re.sub(r"[^0-9A-Z]", "", ref.upper())
        if key and key in re.sub(r"[^0-9A-Z]", "", text.upper()):
            return True
    brand = target.get("brand") or ""
    hints = SERIES_HINTS.get(num or "", [])
    if not (brand and brand in text and any(h in text.upper() for h in hints)):
        return False
    # 卡片上写了别的 5–6 位编号（如 224270），就不是这个型号
    rx = r"(?<![0-9.])([3-7][0-9]{3})(?![0-9.]|元)" if num and len(num) == 4 else r"(?<![0-9.])([12][0-9]{4,5})(?![0-9.]|元)"
    return not [n for n in re.findall(rx, text) if n != num and not n.startswith("20")]


def detail_description(body: str) -> str:
    body = body or ""
    for stop in ("为你推荐", "猜你喜欢", "相似宝贝"):
        i = body.find(stop)
        if i > 0:
            body = body[:i]
    return re.sub(r"\s+", " ", body).strip()


def read_detail(page, item: dict, target: dict) -> dict:
    """Open the listing, verify the price and parse the whole description."""
    y = dict(item)
    page.goto(y["item_url"], wait_until="domcontentloaded", timeout=25000)
    try:
        page.wait_for_selector('[class*="price--"]', timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(pause(0.6, 1.2))
    body = page.locator("body").inner_text(timeout=8000) or ""
    if re.search(r"验证码|安全验证|异常访问|非法访问", body[:3000]):
        raise RuntimeError("GOOFISH_SECURITY_VERIFICATION")
    desc = detail_description(body)
    price = None
    loc = page.locator('[class*="price--"]')
    for i in range(min(loc.count(), 6)):
        try:
            price = base.parse_detail_integer_price(loc.nth(i).inner_text(timeout=500))
        except Exception:
            price = None
        if price:
            break
    ref, how = resolve_ref(desc, target["ref"])
    meta_title = base.annotate(y.get("title") or "")
    meta_desc = base.annotate(desc)
    warnings = sorted(set(meta_title["warning_keywords"] + meta_desc["warning_keywords"] + [m.group(0) for m in REPLICA_RE.finditer(desc)][:3]))
    y.update({
        "detail_checked_at": datetime.now(timezone.utc).isoformat(),
        "detail_title_match": how in ("description", "description+hint", "number_only"),
        "ref_source": how,
        "parsed_ref": ref if how != "not_found" else y.get("parsed_ref"),
        "dial_label": dial_of(f"{y.get('title', '')} {desc}"),
        "condition_label": meta_title["condition_label"] if meta_title["condition_label"] != "成色未注明" else meta_desc["condition_label"],
        "completeness_label": meta_title["completeness_label"] if meta_title["completeness_label"] != "附件未注明" else meta_desc["completeness_label"],
        "year_label": meta_title["year_label"] or meta_desc["year_label"],
        "seller_type": seller_type(desc),
        "warning_keywords": warnings,
        "suspicious_keyword": warnings[0] if warnings else None,
        "desc_snippet": desc[:220],
        "detail_price_found": bool(price),
    })
    if price:
        y["search_price_cny"] = y.get("search_price_cny") or y.get("price_cny")
        y["price_cny"] = price
        y["price_verified"] = y["detail_title_match"]
        y["price_source"] = "item_detail" if y["detail_title_match"] else "item_detail_model_mismatch"
    return y


def reusable(prev: dict | None, card_price: int | None) -> bool:
    if not prev or not prev.get("detail_checked_at") or not prev.get("price_verified"):
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(str(prev["detail_checked_at"]).replace("Z", "+00:00"))
    except Exception:
        return False
    same_price = card_price is None or abs(int(prev.get("search_price_cny") or prev.get("price_cny") or 0) - int(card_price)) < 50
    return age < timedelta(days=DETAIL_REFRESH_DAYS) and same_price
