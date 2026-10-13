from __future__ import annotations

import json
import urllib.request

import goofish_targeted as collector

BOARD_URL = "https://japan-watch-buyback.hatchable.site/api/collector-targets"
MAX_TARGETS = 58
LAST_ORIGIN = "fallback"


def make_target(ref, brand, source):
    ref = str(ref or "").strip()
    brand = str(brand or "").strip()
    return {
        "ref": ref,
        "brand": brand,
        "source": source,
        "query": f"{brand} {collector.core_ref(ref)}".strip(),
    }


def fetch_board():
    global LAST_ORIGIN
    try:
        request = urllib.request.Request(
            BOARD_URL,
            headers={"User-Agent": "WatchRadar/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=18) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = payload.get("targets", []) if isinstance(payload, dict) else []
        targets = []
        seen = set()
        for row in raw:
            ref = str(row.get("ref") or "").strip()
            if not ref or ref.upper() in seen:
                continue
            seen.add(ref.upper())
            targets.append(make_target(ref, row.get("brand"), "board-" + str(row.get("source") or "unknown")))
            if len(targets) >= MAX_TARGETS:
                break
        if targets:
            LAST_ORIGIN = "hatchable_board_api"
            collector.log("BOARD_API_TARGETS", len(targets))
            return targets
    except Exception as exc:
        collector.log("BOARD_API_UNAVAILABLE", type(exc).__name__, "using matched board fallback")
    return []


def last_brand(label):
    found = ("", -1)
    for brand, pattern in collector.BRAND_RULES:
        for match in pattern.finditer(str(label or "")):
            if match.start() >= found[1]:
                found = (brand, match.start())
    return found[0]


def board_fallback():
    global LAST_ORIGIN
    # Mirrors the two board sources: trend reference seeds + Catamap luxury take-board.
    # Never use generic brand-only discovery queries.
    positive_trend_refs = {
        "5066/1A", "5396R", "5065/1A", "126500LN",
        "15550ST.OO.1356ST.08", "26240ST.OO.1320ST.08",
        "W62054V3", "W6206006", "W62017V3", "126334", "214270",
    }
    fixed = [(r, b, "trend-fallback") for r, b in collector.PRIORITY_TARGETS if r in positive_trend_refs]
    market = collector.fetch_catamap_targets()
    take = []
    for ref, brand, label in market:
        resolved_brand = last_brand(label) or brand
        if resolved_brand:
            take.append((ref, resolved_brand, "take-fallback"))
        if len(take) >= 40:
            break
    fixed.extend(take)
    targets = []
    seen = set()
    for ref, brand, source in fixed:
        key = str(ref).upper()
        if not key or key in seen:
            continue
        seen.add(key)
        targets.append(make_target(ref, brand, source))
        if len(targets) >= MAX_TARGETS:
            break
    LAST_ORIGIN = "board_mirror_fallback"
    collector.log("BOARD_MIRROR_FALLBACK_TARGETS", len(targets))
    return targets


def build_board_targets():
    return fetch_board() or board_fallback()


def main():
    collector.build_targets = build_board_targets
    collector.main()
    try:
        data = json.loads(collector.OUT.read_text(encoding="utf-8"))
        scan = data.get("goofish_scan")
        if isinstance(scan, dict):
            scan["strategy"] = "board_refs_only"
            scan["target_origin"] = LAST_ORIGIN
            scan["brand_only_queries"] = 0
            collector.OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        collector.log("BOARD_SCAN_METADATA_WARNING", type(exc).__name__)


if __name__ == "__main__":
    main()
