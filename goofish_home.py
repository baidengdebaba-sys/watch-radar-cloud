from __future__ import annotations
import os, re, json, base64
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlparse, parse_qs
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path("data/latest.json")
PREV = Path(os.environ.get("WATCH_PREVIOUS_DATA", "/tmp/watchradar_previous.json"))
STATE_FILE = Path("/tmp/goofish_state.json")
MIN_PRICE = 30000
MAX_PRICE = 20000000
MAX_SAVED_ITEMS = 2500
RETENTION_DAYS = 14

# These are annotations only. They DO NOT exclude an item from collection.
WARNING_WORDS = ["高仿","复刻","仿品","假表","定制","后改","改装","拆件","故障","待修","机芯单卖","仅配件"]
DEFAULT_KEYWORDS = [
    "126500LN","126610LN","126610LV","126710BLRO","126710BLNR","116500LN","124060","126334",
    "214270","15510ST","15500ST","26240ST","5711/1A","5712/1A","5167A","310.30.42.50.01.002",
    "劳力士","欧米茄","爱彼","百达翡丽","卡地亚","江诗丹顿","万国","积家","宝珀","帝舵",
    "沛纳海","真力时","宇舶","百年灵","宝玑","朗格","伯爵","芝柏","萧邦","香奈儿",
    "理查米尔","罗杰杜彼","雅典","格拉苏蒂","冠蓝狮","Grand Seiko","Rolex","Omega","Cartier",
    "Audemars Piguet","Patek Philippe","Vacheron Constantin","IWC","Jaeger Lecoultre","Breitling","Tudor",
    "二手名表","腕表","机械表","瑞士表"
]

def log(*x):
    print(datetime.now().strftime("%H:%M:%S"), *x, flush=True)

def normalize_ref(raw: str | None):
    if not raw:
        return None
    r = str(raw).upper().replace(" ", "").strip()
    if r.startswith("M") and len(r) > 5 and r[1].isdigit():
        r = r[1:]
    r = re.sub(r"-(?:000\d|\d{3,4})$", "", r)
    return r

def ref_from_text(text: str):
    u=(text or "").upper().replace("\u200b","").replace("\xa0"," ")
    compact=re.sub(r"(?<=[A-Z0-9])\s+(?=[A-Z0-9])","",u)
    pats=[
        r"\b(1[012]\d{4}[A-Z]{0,7})(?:-\d{3,4})?\b",
        r"\b(15\d{3}[A-Z]{0,7})(?:-\d{3,4})?\b",
        r"\b(26\d{3}[A-Z]{0,7})(?:-\d{3,4})?\b",
        r"\b(57\d{2}(?:/[A-Z0-9]+)?)(?:-\d{3,4})?\b",
        r"\b(51\d{2}[A-Z]?)(?:-\d{3,4})?\b",
        r"\b(\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{2}\.\d{3})\b",
        r"\b(\d{4}\.\d{2}(?:\.\d{2})?)\b"
    ]
    for p in pats:
        m=re.search(p,compact)
        if m:return normalize_ref(m.group(1))
    return None

def parse_price(s: str):
    """Parse only the actual price token, never a year/model/list-price number elsewhere."""
    raw=(s or "").replace(",","").replace("，","").replace("￥","¥").strip()
    if not raw:
        return None

    # Prefer an explicit currency token. The 万 multiplier applies only to that token.
    m=re.search(r"¥\s*([0-9]+(?:\.[0-9]+)?)\s*(万)?", raw)
    if not m:
        m=re.search(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*(万)\s*(?:元)?", raw)
    if not m:
        m=re.search(r"(?<![0-9])([0-9]{4,9})\s*元(?![0-9])", raw)
    if not m:
        # Price widgets sometimes contain just the numeric value.
        m=re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(万)?\s*", raw)
    if not m:
        return None

    v=float(m.group(1))
    suffix=m.group(2) if m.lastindex and m.lastindex >= 2 else None
    if suffix == "万":
        v*=10000
    price=int(round(v))
    if price <= 0 or price > MAX_PRICE:
        return None
    return price

def item_id(url):
    try:return str((parse_qs(urlparse(url).query).get("id") or [""])[0])
    except:return ""

def detect_condition(title: str):
    t=title or ""
    checks=[
        (r"全新|未使用|未佩戴|未撕膜|原膜|全膜","全新/未使用"),
        (r"99(?:\.\d)?新|九九新","99新"),
        (r"98(?:\.\d)?新|九八新","98新"),
        (r"95(?:\.\d)?新|九五新","95新"),
        (r"9成新|九成新","9成新"),
        (r"85成新|8\.5成新|八五新","85新"),
        (r"8成新|八成新","8成新"),
        (r"轻微使用|很少用|很少佩戴|少量使用|轻微痕迹","轻微使用"),
        (r"使用痕迹|划痕|刮痕|磕碰|磨损|掉漆","有使用痕迹"),
        (r"故障|待修|不走|停走|走时不准","故障/待修")
    ]
    for p,label in checks:
        if re.search(p,t,re.I):return label
    if "二手" in t or "自用" in t:return "二手/自用"
    return "成色未注明"

def detect_completeness(title: str):
    t=title or ""
    if re.search(r"单表|裸表|只有表|仅手表|无盒无卡|无盒.*无卡|无附件",t,re.I):return "单表"
    if re.search(r"大全套|全套全长|全套齐全|附件齐全|全套附件|双吊牌|(?<!非)全套|有盒.*有卡|有卡.*有盒",t,re.I):return "全套"
    if re.search(r"盒卡齐|盒证齐|盒子.*保卡|保卡.*盒",t,re.I):return "盒卡"
    if re.search(r"无保卡|无卡|缺卡|无盒|缺盒",t,re.I):return "附件不全"
    return "附件未注明"

def detect_year(title: str):
    t=title or ""
    m=re.search(r"(?<!\d)(20(?:0\d|1\d|2[0-6]))\s*年?", t)
    if m:return m.group(1)
    # Common marketplace shorthand: 17年全套 / 23年5月 / 23年卡.
    m=re.search(r"(?<!\d)(0\d|1\d|2[0-6])\s*年(?:\s*\d{1,2}\s*月)?", t)
    if m:return "20"+m.group(1)
    return None

def annotate(title: str):
    warnings=[w for w in WARNING_WORDS if w in (title or "")]
    return {
        "condition_label":detect_condition(title),
        "completeness_label":detect_completeness(title),
        "year_label":detect_year(title),
        "warning_keywords":warnings,
        "suspicious_keyword":warnings[0] if warnings else None
    }

def jp_compare_for(ref, condition, quotes):
    key=normalize_ref(ref)
    if not key:return {"matched":False,"ref":None,"quote_count":0}
    same=[q for q in (quotes or []) if normalize_ref(q.get("ref"))==key]
    if not same:return {"matched":False,"ref":key,"quote_count":0}
    used=[(int(q.get("used_price")),q) for q in same if q.get("used_price")]
    new=[(int(q.get("new_price")),q) for q in same if q.get("new_price")]
    is_new=condition=="全新/未使用"
    pool=new if is_new and new else used if used else new
    vals=[x[0] for x in pool]
    sources=[]
    for _,q in pool:
        s=str(q.get("source") or "")
        if s and s not in sources:sources.append(s)
    return {
        "matched":bool(vals),"ref":key,"quote_count":len(vals),
        "basis":"new" if (is_new and new) else "used" if used else "available",
        "conservative_jpy":min(vals) if vals else None,
        "best_jpy":max(vals) if vals else None,
        "sources":sources[:8]
    }

def scan_goofish(page, keyword, quotes, limit=120):
    page.goto("https://www.goofish.com/search?q="+quote(keyword),wait_until="domcontentloaded",timeout=45000)
    page.wait_for_timeout(1500)
    for _ in range(5):
        page.mouse.wheel(0,2200)
        page.wait_for_timeout(550)

    body=page.locator("body").inner_text(timeout=10000) or ""
    if re.search(r"验证码|安全验证|异常访问|非法访问",body):
        raise RuntimeError("GOOFISH_SECURITY_VERIFICATION")

    cards=page.locator('a[href*="/item?id="]')
    if cards.count()==0 and re.search(r"请先登录|登录后",body):
        raise RuntimeError("GOOFISH_LOGIN_EXPIRED")

    out=[]
    now=datetime.now(timezone.utc).isoformat()
    for i in range(min(cards.count(),limit)):
        c=cards.nth(i)
        try:
            href=c.get_attribute("href") or ""
            if href.startswith("/"): href="https://www.goofish.com"+href
            iid=item_id(href)
            if not iid: continue

            text=re.sub(r"\s+"," ",c.inner_text(timeout=1500)).strip()
            title=""
            for sel in ['[class*="row1-wrap-title"]','[class*="main-title"]','[class*="title"]']:
                loc=c.locator(sel)
                if loc.count():
                    title=re.sub(r"\s+"," ",loc.first.inner_text(timeout=800)).strip()
                    if len(title)>=4:break
            if not title:title=text[:260]

            pw=c.locator('[class*="price-wrap"], [class*="price"]')
            ptxt=pw.first.inner_text(timeout=800) if pw.count() else text
            price=parse_price(ptxt)
            if not price or price<MIN_PRICE:continue

            ref=ref_from_text(title) or ref_from_text(keyword)
            meta=annotate(title)
            img=""
            try:
                im=c.locator('img')
                if im.count():img=im.first.get_attribute('src') or im.first.get_attribute('data-src') or ""
            except Exception:pass
            jp=jp_compare_for(ref,meta["condition_label"],quotes)
            warnings=meta["warning_keywords"]
            risk="high" if warnings else ("low" if ref else "medium")
            out.append({
                "item_id":iid,"keyword":keyword,"title":title,"price_cny":price,
                "seller":"","area":"","badge":"","item_url":href,"image_url":img,
                "publish_time":None,"parsed_ref":ref,"risk_level":risk,
                "first_seen_at":now,"last_seen_at":now,"seen_in_latest_scan":True,
                "jp_compare":jp,**meta
            })
        except Exception:
            continue
    return list({x["item_id"]:x for x in out}.values())

def load_state():
    raw=os.environ.get("GOOFISH_STORAGE_STATE_B64","").strip()
    if not raw:return False
    try:
        STATE_FILE.write_bytes(base64.b64decode(raw))
        return True
    except Exception as e:
        log("state decode failed",e)
        return False

def load_json(path: Path):
    try:return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:return None

def load_existing():
    d=load_json(OUT)
    if d:return d
    return {"generated_at":datetime.now(timezone.utc).isoformat(),"runner":"github-actions","items":[],"jp_quotes":[],"errors":[]}

def merge_recent(current_items, previous_items):
    now=datetime.now(timezone.utc)
    cutoff=now-timedelta(days=RETENTION_DAYS)
    merged={}
    for x in previous_items or []:
        iid=str(x.get("item_id") or "")
        if not iid:continue
        seen=x.get("last_seen_at") or x.get("first_seen_at")
        try:
            dt=datetime.fromisoformat(str(seen).replace("Z","+00:00")) if seen else now
            if dt<cutoff:continue
        except Exception:pass
        y=dict(x);y["seen_in_latest_scan"]=False;merged[iid]=y
    for x in current_items:
        iid=str(x.get("item_id") or "")
        if not iid:continue
        old=merged.get(iid)
        if old and old.get("first_seen_at"):x["first_seen_at"]=old["first_seen_at"]
        merged[iid]=x
    rows=list(merged.values())
    rows.sort(key=lambda x:str(x.get("last_seen_at") or ""),reverse=True)
    return rows[:MAX_SAVED_ITEMS]

def main():
    data=load_existing()
    previous=load_json(PREV) or {}
    previous_items=previous.get("items") if isinstance(previous,dict) else []
    data["goofish_login_configured"]=load_state()
    data.setdefault("errors",[])
    data["errors"]=[x for x in data["errors"] if not str(x).startswith("goofish ")]

    if not data["goofish_login_configured"]:
        data["goofish_status"]="not_configured"
        OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        return

    proxy=os.environ.get("WATCH_SOCKS_PROXY","").strip()
    if not proxy:
        data["goofish_status"]="home_proxy_missing"
        data["errors"].append("goofish: HOME_PROXY_MISSING")
        OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        return

    keywords=[x.strip() for x in os.environ.get("WATCH_KEYWORDS",",".join(DEFAULT_KEYWORDS)).split(",") if x.strip()][:60]
    quotes= data.get("jp_quotes") if isinstance(data.get("jp_quotes"),list) else []
    all_items=[]
    errors=[]

    with sync_playwright() as p:
        browser=p.chromium.launch(headless=False,args=["--disable-dev-shm-usage"])
        ctx=browser.new_context(storage_state=str(STATE_FILE),locale="zh-CN",proxy={"server":proxy})
        page=ctx.new_page()

        try:
            page.goto("https://api.ipify.org",wait_until="domcontentloaded",timeout=20000)
            exit_ip=(page.locator("body").inner_text(timeout=5000) or "").strip()
            log("HOME_EXIT_OK",bool(re.match(r"^\d+\.\d+\.\d+\.\d+$",exit_ip)))
        except Exception as e:
            errors.append(f"goofish proxy check: {e}")

        for i,k in enumerate(keywords,1):
            try:
                xs=scan_goofish(page,k,quotes)
                all_items.extend(xs)
                log("goofish",i,k,len(xs))
            except Exception as e:
                errors.append(f"goofish {k}: {e}")
                log("goofish ERROR",k,e)
                if "LOGIN_EXPIRED" in str(e) or "SECURITY" in str(e):break

        browser.close()

    unique_current=list({x["item_id"]:x for x in all_items}.values())
    data["items"]=merge_recent(unique_current,previous_items)
    data["goofish_status"]="ok" if unique_current else ("blocked_or_expired" if errors else "empty")
    data["goofish_scan"]={
        "keywords":len(keywords),"current_scan_items":len(unique_current),
        "saved_recent_items":len(data["items"]),"min_price_cny":MIN_PRICE,"retention_days":RETENTION_DAYS
    }
    data.pop("goofish_home_exit_ip",None)
    data["errors"].extend(errors)
    data["generated_at"]=datetime.now(timezone.utc).isoformat()
    data["runner"]="github-actions-home-exit"

    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    log("wrote",len(unique_current),"current Goofish items; saved",len(data["items"]),"recent items; preserved",len(quotes),"JP quotes")

if __name__=="__main__":
    main()
