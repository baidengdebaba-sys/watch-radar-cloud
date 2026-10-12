from __future__ import annotations

import json
import re
from pathlib import Path

DATA = Path("data/latest.json")

BRANDS = {
    "rolex": re.compile(r"劳力士|ROLEX|ロレックス", re.I),
    "omega": re.compile(r"欧米茄|OMEGA|オメガ", re.I),
    "ap": re.compile(r"爱彼|AUDEMARS|オーデマ", re.I),
    "patek": re.compile(r"百达翡丽|PATEK|パテック", re.I),
    "cartier": re.compile(r"卡地亚|CARTIER|カルティエ", re.I),
    "iwc": re.compile(r"万国|\bIWC\b", re.I),
    "gs": re.compile(r"冠蓝狮|GRAND\s*SEIKO|グランドセイコー", re.I),
}


def normalize_ref(raw: str | None):
    if not raw:
        return None
    r = str(raw).upper().replace("\u200b", "").replace(" ", "").strip()
    if r.startswith("M") and len(r) > 5 and r[1].isdigit():
        r = r[1:]
    r = re.sub(r"-(?:000\d|\d{3,4})$", "", r)
    return r or None


def compact_model_text(raw: str | None):
    s = str(raw or "").upper().replace("\u200b", "")
    s = re.sub(r"(?<![A-Z0-9])M(?=\d{4,})", "", s)
    return re.sub(r"[^A-Z0-9]", "", s)


def model_keyword_ref(keyword: str | None, title: str):
    k = str(keyword or "").strip()
    if not k or not re.search(r"\d", k):
        return None
    if len(k) > 40 or re.search(r"[\u4e00-\u9fff]", k):
        return None
    nr = normalize_ref(k)
    if not nr or len(compact_model_text(nr)) < 4:
        return None
    return nr if compact_model_text(nr) in compact_model_text(title) else None


def first_group(pattern: str, text: str):
    m = re.search(pattern, text, re.I)
    return normalize_ref(m.group(1)) if m else None


def ref_from_title(title: str, keyword: str | None = None):
    title = str(title or "")
    by_keyword = model_keyword_ref(keyword, title)
    if by_keyword:
        return by_keyword

    # Long dotted Omega references are highly distinctive.
    x = first_group(r"(?<![0-9])(\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{2}\.\d{3})(?![0-9])", title)
    if x:
        return x

    if BRANDS["patek"].search(title):
        x = first_group(r"(?<![A-Z0-9])([3456]\d{3}(?:/[A-Z0-9]+)?[A-Z]?)(?:-\d{3,4})?(?![A-Z0-9])", title.upper())
        if x:
            return x

    if BRANDS["ap"].search(title):
        x = first_group(r"(?<![A-Z0-9])M?((?:15|26)\d{3}[A-Z]{0,5})(?:[.\s-][A-Z0-9.]+)?(?![A-Z0-9])", title.upper())
        if x:
            return x

    if BRANDS["rolex"].search(title):
        # Modern six-digit Rolex refs, including M126500LN and spaced suffixes.
        compact = re.sub(r"(?<=[A-Z0-9])\s+(?=[A-Z0-9])", "", title.upper())
        x = first_group(r"(?<![A-Z0-9])M?((?:1[0-2]\d{4}|2\d{5})[A-Z]{0,6})(?:-\d{3,4})?(?![A-Z0-9])", compact)
        if x:
            return x
        # Older five-digit refs; ASCII boundaries avoid matching inside a longer ref.
        x = first_group(r"(?<![A-Z0-9])M?((?:1[45679]\d{3}|2[0-4]\d{3})[A-Z]{0,3})(?![A-Z0-9])", compact)
        if x:
            return x

    if BRANDS["cartier"].search(title):
        x = first_group(r"(?<![A-Z0-9])(W[A-Z0-9]{6,})(?![A-Z0-9])", title.upper())
        if x:
            return x

    if BRANDS["iwc"].search(title):
        x = first_group(r"(?<![A-Z0-9])(IW\d{4,})(?![A-Z0-9])", title.upper())
        if x:
            return x

    if BRANDS["gs"].search(title):
        x = first_group(r"(?<![A-Z0-9])(SB[A-Z]\d{3})(?![A-Z0-9])", title.upper())
        if x:
            return x

    return None


def safe_int(v):
    try:
        n = int(v)
        return n if n > 0 else None
    except Exception:
        return None


def jp_compare_for(ref, condition, quotes):
    key = normalize_ref(ref)
    if not key:
        return {"matched": False, "ref": None, "quote_count": 0}
    same = [q for q in (quotes or []) if normalize_ref(q.get("ref")) == key]
    if not same:
        return {"matched": False, "ref": key, "quote_count": 0}
    used = [(safe_int(q.get("used_price")), q) for q in same]
    new = [(safe_int(q.get("new_price")), q) for q in same]
    used = [x for x in used if x[0]]
    new = [x for x in new if x[0]]
    is_new = condition == "全新/未使用"
    pool = new if is_new and new else used if used else new
    vals = [x[0] for x in pool]
    sources = []
    for _, q in pool:
        s = str(q.get("source") or "")
        if s and s not in sources:
            sources.append(s)
    return {
        "matched": bool(vals),
        "ref": key,
        "quote_count": len(vals),
        "basis": "new" if (is_new and new) else "used" if used else "available",
        "conservative_jpy": min(vals) if vals else None,
        "best_jpy": max(vals) if vals else None,
        "sources": sources[:8],
    }


def main():
    if not DATA.exists():
        print("repair_refs: data/latest.json missing")
        return
    data = json.loads(DATA.read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data.get("items"), list) else []
    quotes = data.get("jp_quotes") if isinstance(data.get("jp_quotes"), list) else []
    repaired = 0
    changed = 0
    for item in items:
        old = normalize_ref(item.get("parsed_ref"))
        new = ref_from_title(item.get("title") or "", item.get("keyword"))
        if new and new != old:
            item["parsed_ref"] = new
            item["jp_compare"] = jp_compare_for(new, item.get("condition_label"), quotes)
            item["ref_source"] = "keyword_or_title_repair"
            repaired += 1
            changed += 1
        elif old:
            item["parsed_ref"] = old
        elif new:
            item["parsed_ref"] = new
            item["jp_compare"] = jp_compare_for(new, item.get("condition_label"), quotes)
            item["ref_source"] = "keyword_or_title_repair"
            repaired += 1
            changed += 1
    with_ref = sum(1 for x in items if x.get("parsed_ref"))
    data["goofish_ref_repair"] = {
        "repaired_items": repaired,
        "items_with_ref": with_ref,
        "total_items": len(items),
    }
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"repair_refs: repaired {repaired}; with_ref {with_ref}/{len(items)}")


if __name__ == "__main__":
    main()
