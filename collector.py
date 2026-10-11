from __future__ import annotations
import re, json
from datetime import datetime, timezone
from urllib.parse import quote
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path("data/latest.json")

DEFAULT_REFS = [
    "126500LN","126610LN","126610LV","126710BLRO","126710BLNR","116500LN","124060","126334",
    "214270","15510ST","15500ST","26240ST","5711/1A","5712/1A","5167A","310.30.42.50.01.002"
]

REF_BRAND = {
    "126500LN":"rolex","126610LN":"rolex","126610LV":"rolex","126710BLRO":"rolex",
    "126710BLNR":"rolex","116500LN":"rolex","124060":"rolex","126334":"rolex","214270":"rolex",
    "15510ST":"audemars","15500ST":"audemars","26240ST":"audemars",
    "5711/1A":"patek","5712/1A":"patek","5167A":"patek",
    "310.30.42.50.01.002":"omega",
}

BRAND_PAGES = {
    "RASIN": {
        "rolex":"https://www.kaitori-ginza.com/teigaku/rolex_price/",
        "audemars":"https://www.kaitori-ginza.com/teigaku/audemarspiguet_price/",
        "patek":"https://www.kaitori-ginza.com/teigaku/patekphilippe_price/",
        "omega":"https://www.kaitori-ginza.com/teigaku/omega_price/",
    },
    "なんぼや": {
        "rolex":"https://nanboya.com/tokei-kaitori/rolex/",
        "audemars":"https://nanboya.com/tokei-kaitori/audemarspiguet/",
        "patek":"https://nanboya.com/tokei-kaitori/patekphilippe/",
        "omega":"https://nanboya.com/tokei-kaitori/omega/",
    },
    "ブラリバ": {
        "rolex":"https://brandrevalue.com/cat/watch/purchase-watch/rolex",
        "audemars":"https://brandrevalue.com/cat/watch/audemarspiguet",
        "patek":"https://brandrevalue.com/cat/watch/patek-philippe",
        "omega":"https://brandrevalue.com/cat/watch/omega",
    },
    "WATCHNIAN": {
        "rolex":"https://watchnian.com/buy/categories/item_watch/brand_rolex/",
        "audemars":"https://watchnian.com/buy/categories/item_watch/brand_audemars_piguet/",
        "patek":"https://watchnian.com/buy/categories/item_watch/brand_patek_philippe/",
        "omega":"https://watchnian.com/buy/categories/item_watch/brand_omega/",
    },
}

PREMIER_URLS = {
    "126500LN":"https://premiervalue.jp/rolex/daytona/ref_126500LN/",
    "116500LN":"https://premiervalue.jp/rolex/daytona/ref_116500LN/",
    "126610LN":"https://premiervalue.jp/rolex/submariner/ref_126610LN/",
    "126610LV":"https://premiervalue.jp/rolex/submariner/ref_126610LV/",
    "124060":"https://premiervalue.jp/rolex/submariner/ref_124060/",
    "214270":"https://premiervalue.jp/rolex/explorer/ref_214270/",
    "126334":"https://premiervalue.jp/rolex/datejust/ref_126334/",
}

def log(*x):
    print(datetime.now().strftime("%H:%M:%S"), *x, flush=True)

def normalize_ref(raw):
    if not raw:
        return None
    r = str(raw).upper().replace(" ", "").strip()
    if r.startswith("M") and len(r) > 5 and r[1].isdigit():
        r = r[1:]
    r = re.sub(r"-(?:000\d|\d{3,4})$", "", r)
    return r

def variant(text):
    t = (text or "").upper()
    out = []
    for n, l in [
        ("ホワイト","白"),("WHITE","白"),("白文字盤","白"),
        ("ブラック","黑"),("BLACK","黑"),("黒文字盤","黑"),
        ("ブルー","蓝"),("BLUE","蓝"),("青文字盤","蓝"),
        ("グリーン","绿"),("GREEN","绿"),("緑文字盤","绿"),
        ("シルバー","银"),("SILVER","银"),
    ]:
        if n in t and l not in out:
            out.append(l)
    return " ".join(out[:2])

def yen_values(s):
    vals = []
    for m in re.finditer(r"(?:¥|￥)?\s*([0-9]{1,3}(?:,[0-9]{3})+)\s*円?", s or ""):
        v = int(m.group(1).replace(",", ""))
        if 100000 <= v <= 100000000:
            vals.append(v)
    return vals

def labeled_price(chunk, labels):
    for label in labels:
        m = re.search(re.escape(label) + r".{0,90}?(?:¥|￥)?\s*([0-9]{1,3}(?:,[0-9]{3})+)\s*円?", chunk, re.I | re.S)
        if m:
            v = int(m.group(1).replace(",", ""))
            if 100000 <= v <= 100000000:
                return v
    return None

def state_rank(chunk):
    m = re.search(r"(?:状態ランク|ランク)\s*([NSAB]{1,2})\b", chunk or "", re.I)
    return m.group(1).upper() if m else None

def quote_row(source, ref, url, chunk, new_price=None, used_price=None, quote_type="public_buyback",
              condition_label=None, condition_rank=None):
    return {
        "source": source,
        "ref": normalize_ref(ref),
        "variant": variant(chunk),
        "label": normalize_ref(ref),
        "new_price": int(new_price) if new_price else None,
        "used_price": int(used_price) if used_price else None,
        "quote_type": quote_type,
        "condition_label": condition_label,
        "condition_rank": condition_rank,
        "source_url": url,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }

def fetch_text(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1000)
    return page.locator("body").inner_text(timeout=12000) or ""

def scan_gmt(page, ref):
    url = "https://www.gmt-j.com/ec/assessment/search?keyword=" + quote(ref)
    text = fetch_text(page, url)
    if "Access Denied" in text:
        return []
    out = []
    for m in re.finditer(re.escape(ref), text, re.I):
        chunk = text[max(0, m.start()-220):min(len(text), m.start()+900)]
        p = labeled_price(chunk, ["買取上限価格","ワンプライス買取価格","買取価格"])
        if p:
            out.append(quote_row("GMT", ref, url, chunk, used_price=p,
                                 condition_label="中古（公開買取上限）"))
    return out

def scan_simple_page(page, refs, name, url):
    text = fetch_text(page, url)
    out = []
    for ref in refs:
        for m in re.finditer(re.escape(ref), text, re.I):
            chunk = text[max(0, m.start()-220):min(len(text), m.start()+800)]
            if name == "KOMEHYO":
                used = labeled_price(chunk, ["使用感の少ない中古品","中古品"])
                new = labeled_price(chunk, ["未使用品","相場価格"])
                condition = "中古（使用感少）" if used else None
            elif name == "大黒屋":
                vals = yen_values(chunk)
                new = vals[0] if vals else None
                used = vals[1] if len(vals) > 1 else None
                condition = "中古"
            else:
                vals = yen_values(chunk)
                new = None
                used = vals[-1] if vals else None
                condition = "中古"
            if new or used:
                out.append(quote_row(name, ref, url, chunk, new, used,
                                     condition_label=condition))
    return out

def scan_brand_source(page, source, brand, refs, url):
    try:
        text = fetch_text(page, url)
    except Exception as e:
        log(source, brand, "fetch failed", type(e).__name__)
        return []
    if not text or "403 Forbidden" in text or "Access Denied" in text:
        return []

    out = []
    for ref in refs:
        if REF_BRAND.get(normalize_ref(ref)) != brand:
            continue
        hits = list(re.finditer(re.escape(ref), text, re.I))[:12]
        for m in hits:
            chunk = text[max(0, m.start()-260):min(len(text), m.start()+900)]
            new_price = None
            used_price = None
            rank = state_rank(chunk)
            qtype = "public_buyback"
            cond = None

            if source == "WATCHNIAN":
                new_price = labeled_price(chunk, ["新品","未使用"])
                used_price = labeled_price(chunk, ["中古"])
                cond = "中古" if used_price else "新品/未使用" if new_price else None

            elif source == "なんぼや":
                new_price = labeled_price(chunk, ["未使用品"])
                used_price = labeled_price(chunk, ["中古品"])
                if rank:
                    vals = yen_values(chunk)
                    if vals:
                        used_price = vals[0]
                        qtype = "actual_buyback"
                        cond = f"実績ランク{rank}"
                elif used_price:
                    cond = "中古品（同社条件準拠）"

            elif source == "ブラリバ":
                vals = yen_values(chunk)
                if rank and vals:
                    used_price = vals[0]
                    qtype = "actual_buyback"
                    cond = f"実績ランク{rank}"
                else:
                    used_price = labeled_price(chunk, ["参考買取価格","買取金額"])
                    cond = "参考買取/実績"

            elif source == "RASIN":
                new_price = labeled_price(chunk, ["未使用品"])
                used_price = labeled_price(chunk, ["中古品","中古"])
                if not (new_price or used_price):
                    vals = yen_values(chunk)
                    if len(vals) >= 2:
                        new_price, used_price = max(vals[:2]), min(vals[:2])
                    elif vals:
                        used_price = vals[0]
                cond = "中古（定額買取条件準拠）" if used_price else "未使用品" if new_price else None

            if new_price or used_price:
                out.append(quote_row(source, ref, url, chunk, new_price, used_price,
                                     quote_type=qtype, condition_label=cond, condition_rank=rank))
    return out

def scan_premier(page, ref, url):
    try:
        text = fetch_text(page, url)
    except Exception as e:
        log("PREMIER VALUE", ref, "fetch failed", type(e).__name__)
        return []
    if not text or "403 Forbidden" in text:
        return []
    chunk = text[:12000]
    new_price = labeled_price(chunk, ["新品・未使用品","新品","最高買取価格"])
    used_price = None
    m = re.search(r"中古品.{0,220}?(?:最高値|最高買取価格)?\s*(?:¥|￥)\s*([0-9]{1,3}(?:,[0-9]{3})+)", chunk, re.I | re.S)
    if m:
        used_price = int(m.group(1).replace(",", ""))
    if not (new_price or used_price):
        return []
    return [quote_row("PREMIER VALUE", ref, url, chunk, new_price, used_price,
                      condition_label="新品/中古（月次相場）")]

def dedupe_quotes(rows):
    d = {}
    for x in rows:
        key = (
            x.get("source"), normalize_ref(x.get("ref")), x.get("variant",""),
            x.get("new_price"), x.get("used_price"), x.get("condition_rank")
        )
        d[key] = x
    return list(d.values())

def main():
    refs = DEFAULT_REFS
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner": "github-actions",
        "goofish_status": "pending_home_exit",
        "items": [],
        "jp_quotes": [],
        "jp_source_summary": {},
        "errors": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        ctx = browser.new_context(locale="ja-JP")
        page = ctx.new_page()
        q = []

        for ref in refs:
            try:
                q.extend(scan_gmt(page, ref))
            except Exception as e:
                result["errors"].append(f"GMT {ref}: {e}")

        for name, url in [
            ("KOMEHYO","https://komehyo.jp/kaitori/watch/"),
            ("QUARK","https://www.909.co.jp/rolex_buy.html"),
            ("大黒屋","https://kaitori.e-daikoku.com/brand/shop/ikebukuro-higashiguchi/"),
        ]:
            try:
                q.extend(scan_simple_page(page, refs, name, url))
            except Exception as e:
                result["errors"].append(f"{name}: {e}")

        for source, by_brand in BRAND_PAGES.items():
            for brand, url in by_brand.items():
                try:
                    xs = scan_brand_source(page, source, brand, refs, url)
                    q.extend(xs)
                    log(source, brand, len(xs))
                except Exception as e:
                    result["errors"].append(f"{source} {brand}: {e}")

        for ref, url in PREMIER_URLS.items():
            try:
                q.extend(scan_premier(page, ref, url))
            except Exception as e:
                result["errors"].append(f"PREMIER VALUE {ref}: {e}")

        browser.close()

    result["jp_quotes"] = dedupe_quotes(q)
    summary = {}
    for x in result["jp_quotes"]:
        s = x.get("source") or "unknown"
        summary[s] = summary.get(s, 0) + 1
    result["jp_source_summary"] = summary

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log("wrote", len(result["jp_quotes"]), "JP quotes", summary)

if __name__ == "__main__":
    main()
