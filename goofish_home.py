from __future__ import annotations
import os, re, json, time, base64
from datetime import datetime, timezone
from urllib.parse import quote, urlparse, parse_qs
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path("data/latest.json")
STATE_FILE = Path("/tmp/goofish_state.json")
MIN_PRICE = 30000
BAD_WORDS = ["高仿","复刻","仿品","定制","改装","后改","表带","表盒","配件","订金","定金","专拍","机芯单卖","拆件","故障","仅配件"]
DEFAULT_KEYWORDS = [
    "126500LN","126610LN","126610LV","126710BLRO","126710BLNR","116500LN","124060","126334",
    "214270","15510ST","15500ST","26240ST","5711/1A","5712/1A","5167A","310.30.42.50.01.002",
    "劳力士","欧米茄","爱彼","百达翡丽","卡地亚","江诗丹顿","万国","积家","宝珀","帝舵","沛纳海","真力时","宇舶","百年灵"
]

def log(*x):
    print(datetime.now().strftime("%H:%M:%S"), *x, flush=True)

def ref_from_text(text: str):
    u=(text or "").upper()
    pats=[
        r"\b(1[012]\d{4}[A-Z]{0,6})\b", r"\b(15\d{3}[A-Z]{0,6})\b",
        r"\b(26\d{3}[A-Z]{0,6})\b", r"\b(57\d{2}(?:/[A-Z0-9]+)?)\b",
        r"\b(51\d{2}[A-Z]?)\b", r"\b(\d{3}\.\d{2}\.\d{2}\.\d{2}\.\d{2}\.\d{3})\b",
        r"\b(\d{4}\.\d{2}(?:\.\d{2})?)\b"
    ]
    for p in pats:
        m=re.search(p,u)
        if m:return m.group(1)
    return None

def parse_price(s: str):
    s=(s or "").replace(",","").replace("￥","¥")
    m=re.search(r"([0-9]+(?:\.[0-9]+)?)",s)
    if not m:return None
    v=float(m.group(1))
    if "万" in s:v*=10000
    return int(round(v))

def item_id(url):
    try:return str((parse_qs(urlparse(url).query).get("id") or [""])[0])
    except:return ""

def scan_goofish(page, keyword, limit=40):
    page.goto("https://www.goofish.com/search?q="+quote(keyword),wait_until="domcontentloaded",timeout=45000)
    page.wait_for_timeout(2200)
    for _ in range(2):
        page.mouse.wheel(0,1800)
        page.wait_for_timeout(700)

    body=page.locator("body").inner_text(timeout=10000) or ""
    if re.search(r"验证码|安全验证|异常访问|非法访问",body):
        raise RuntimeError("GOOFISH_SECURITY_VERIFICATION")

    cards=page.locator('a[href*="/item?id="]')
    if cards.count()==0 and re.search(r"请先登录|登录后",body):
        raise RuntimeError("GOOFISH_LOGIN_EXPIRED")

    out=[]
    for i in range(min(cards.count(),limit)):
        c=cards.nth(i)
        try:
            href=c.get_attribute("href") or ""
            if href.startswith("/"): href="https://www.goofish.com"+href
            iid=item_id(href)
            if not iid: continue

            text=re.sub(r"\s+"," ",c.inner_text(timeout=1500)).strip()
            title=""
            for sel in ['[class*="row1-wrap-title"]','[class*="main-title"]']:
                loc=c.locator(sel)
                if loc.count():
                    title=re.sub(r"\s+"," ",loc.first.inner_text(timeout=800)).strip()
                    if title:break
            if not title:title=text[:180]

            pw=c.locator('[class*="price-wrap"]')
            ptxt=pw.first.inner_text(timeout=800) if pw.count() else text
            price=parse_price(ptxt)
            if not price or price<MIN_PRICE:continue

            bad=next((w for w in BAD_WORDS if w in title),None)
            ref=ref_from_text(title)
            out.append({
                "item_id":iid,"keyword":keyword,"title":title,"price_cny":price,
                "seller":"","area":"","badge":"","item_url":href,"image_url":"",
                "publish_time":None,"parsed_ref":ref,"suspicious_keyword":bad,
                "risk_level":"high" if bad else ("low" if ref else "medium")
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

def load_existing():
    if OUT.exists():
        try:return json.loads(OUT.read_text(encoding="utf-8"))
        except Exception: pass
    return {
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "runner":"github-actions",
        "items":[],
        "jp_quotes":[],
        "errors":[]
    }

def main():
    data=load_existing()
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

    keywords=[x.strip() for x in os.environ.get("WATCH_KEYWORDS",",".join(DEFAULT_KEYWORDS)).split(",") if x.strip()][:30]
    all_items=[]
    errors=[]

    with sync_playwright() as p:
        browser=p.chromium.launch(headless=False,args=["--disable-dev-shm-usage"])
        ctx=browser.new_context(
            storage_state=str(STATE_FILE),
            locale="zh-CN",
            proxy={"server":proxy}
        )
        page=ctx.new_page()

        try:
            page.goto("https://api.ipify.org",wait_until="domcontentloaded",timeout=20000)
            exit_ip=(page.locator("body").inner_text(timeout=5000) or "").strip()
            data["goofish_home_exit_ip"]=exit_ip
            log("HOME_EXIT_IP",exit_ip)
        except Exception as e:
            errors.append(f"goofish proxy check: {e}")

        for i,k in enumerate(keywords,1):
            try:
                xs=scan_goofish(page,k)
                all_items.extend(xs)
                log("goofish",i,k,len(xs))
            except Exception as e:
                errors.append(f"goofish {k}: {e}")
                log("goofish ERROR",k,e)
                if "LOGIN_EXPIRED" in str(e) or "SECURITY" in str(e):
                    break

        browser.close()

    data["items"]=list({x["item_id"]:x for x in all_items}.values())
    data["goofish_status"]="ok" if data["items"] else ("blocked_or_expired" if errors else "empty")
    data["errors"].extend(errors)
    data["generated_at"]=datetime.now(timezone.utc).isoformat()
    data["runner"]="github-actions-home-exit"

    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    log("wrote",len(data["items"]),"Goofish items; preserved",len(data.get("jp_quotes",[])),"JP quotes")

if __name__=="__main__":
    main()
