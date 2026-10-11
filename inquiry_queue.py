from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

LATEST = Path("data/latest.json")
TOP_N = 3
CROSS_BORDER_COST_RATE = 0.12
MAX_SAFE_ROI = 30.0

REF_BRAND_JA = {
    "126500LN":"ロレックス","126610LN":"ロレックス","126610LV":"ロレックス",
    "126710BLRO":"ロレックス","126710BLNR":"ロレックス","116500LN":"ロレックス",
    "124060":"ロレックス","126334":"ロレックス","214270":"ロレックス",
    "15510ST":"オーデマ ピゲ","15500ST":"オーデマ ピゲ","26240ST":"オーデマ ピゲ",
    "5711/1A":"パテック フィリップ","5712/1A":"パテック フィリップ","5167A":"パテック フィリップ",
    "310.30.42.50.01.002":"オメガ",
}

def fx_jpy_to_cny():
    urls = [
        "https://open.er-api.com/v6/latest/JPY",
        "https://api.frankfurter.app/latest?from=JPY&to=CNY",
    ]
    for url in urls:
        try:
            req = Request(url, headers={"User-Agent":"WatchRadar/1.0"})
            with urlopen(req, timeout=10) as r:
                j = json.loads(r.read().decode("utf-8"))
            v = float((j.get("rates") or {}).get("CNY") or 0)
            if v > 0:
                return v
        except Exception:
            pass
    return 0.042

def ja_condition(label):
    m = {
        "全新/未使用":"未使用品",
        "99新":"中古・非常にきれい",
        "98新":"中古・非常にきれい",
        "95新":"中古・きれい",
        "9成新":"中古・きれい",
        "85新":"中古・使用感あり",
        "8成新":"中古・使用感あり",
        "轻微使用":"中古・軽微な使用感",
        "有使用痕迹":"中古・使用感あり",
        "二手/自用":"中古",
        "成色未注明":"中古・状態詳細未確認",
    }
    return m.get(label or "", "中古・状態詳細未確認")

def ja_completeness(label):
    m = {
        "单表":"本体のみ（箱・保証書なし）",
        "全套":"付属品一式あり",
        "盒卡":"箱・保証書あり",
        "附件不全":"付属品一部欠品",
        "附件未注明":"付属品詳細未確認",
    }
    return m.get(label or "", "付属品詳細未確認")

def make_message(item):
    ref = str(item.get("parsed_ref") or "")
    brand = REF_BRAND_JA.get(ref, "")
    year = item.get("year_label")
    bits = [f"{brand} {ref}".strip()]
    if year:
        bits.append(f"{year}年頃")
    bits.extend([
        ja_condition(item.get("condition_label")),
        ja_completeness(item.get("completeness_label")),
    ])
    spec = "、".join(x for x in bits if x)
    return (
        "お世話になっております。\n"
        f"{spec}の時計について、現物確認前の概算買取価格を伺いたくご連絡しました。\n"
        "写真なし・上記情報のみで構いませんので、現在の買取目安をご教示いただけますでしょうか。\n"
        "相場変動や実物査定で金額が変わる点は承知しております。よろしくお願いいたします。"
    )

def main():
    if not LATEST.exists():
        return
    data = json.loads(LATEST.read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data.get("items"), list) else []
    fx = fx_jpy_to_cny()
    candidates = []

    for x in items:
        if x.get("warning_keywords") or x.get("suspicious_keyword"):
            continue
        if x.get("condition_label") == "故障/待修":
            continue
        ref = x.get("parsed_ref")
        price_cny = float(x.get("price_cny") or 0)
        jp = x.get("jp_compare") or {}
        floor = float(jp.get("conservative_jpy") or 0)
        if not ref or price_cny < 30000 or floor <= 0 or fx <= 0:
            continue

        china_cost_jpy = price_cny / fx
        landed = china_cost_jpy * (1 + CROSS_BORDER_COST_RATE)
        roi = (floor - landed) / landed * 100 if landed > 0 else -999
        if roi <= 0 or roi > MAX_SAFE_ROI:
            continue

        quote_count = int(jp.get("quote_count") or 0)
        needs_manual = (
            quote_count < 3
            or x.get("condition_label") in (None, "", "成色未注明")
            or x.get("completeness_label") in (None, "", "附件未注明")
        )

        candidates.append({
            "item_id": x.get("item_id"),
            "ref": ref,
            "condition_label": x.get("condition_label"),
            "completeness_label": x.get("completeness_label"),
            "year_label": x.get("year_label"),
            "price_cny": int(price_cny),
            "jp_public_floor_jpy": int(floor),
            "jp_quote_count": quote_count,
            "safe_roi_pct": round(roi, 2),
            "needs_manual_quote": needs_manual,
            "reason": "捡漏榜Top候选：公开报价不足/成色附件需复核" if needs_manual else "捡漏榜Top候选：人工复核公开报价",
            "message_ja": make_message(x),
            "image_included": False,
            "source_item_url": x.get("item_url"),
            "status": "draft_ready",
        })

    candidates.sort(key=lambda z: z["safe_roi_pct"], reverse=True)
    queue = candidates[:TOP_N]
    for i, x in enumerate(queue, 1):
        x["rank"] = i

    data["manual_inquiry_queue"] = queue
    data["manual_inquiry_policy"] = {
        "top_n": TOP_N,
        "no_images": True,
        "same_item_same_dealer_cooldown_hours": 24,
        "daily_send_cap": 6,
        "only_safe_roi_lte_pct": MAX_SAFE_ROI,
        "send_status": "draft_only_until_channel_connected",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fx_jpy_to_cny": fx,
    }
    LATEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"prepared {len(queue)} manual inquiry drafts")

if __name__ == "__main__":
    main()
