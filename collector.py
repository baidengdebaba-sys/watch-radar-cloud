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
        page.mouse.wheel(0,1800);page.wait_for_timeout(700)
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
            if href.startswith("/"):href="https://www.goofish.com"+href
            iid=item_id(href)
            if not iid:continue
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

def variant(text):
    t=(text or "").upper();out=[]
    for n,l in [("ホワイト","白"),("WHITE","白"),("ブラック","黑"),("BLACK","黑"),("ブルー","蓝"),("BLUE","蓝"),("グリーン","绿"),("GREEN","绿")]:
        if n in t and l not in out:out.append(l)
    return " ".join(out[:2])

def scan_gmt(page, ref):
    url="https://www.gmt-j.com/ec/assessment/search?keyword="+quote(ref)
    page.goto(url,wait_until="domcontentloaded",timeout=45000);page.wait_for_timeout(1200)
    text=page.locator("body").inner_text(timeout=10000) or ""
    if "Access Denied" in text:return []
    out=[]
    for m in re.finditer(re.escape(ref),text,re.I):
        chunk=text[max(0,m.start()-180):min(len(text),m.start()+850)]
        pm=re.search(r"(?:買取上限価格|ワンプライス買取価格|買取価格)\s*[￥¥]?\s*([0-9]{1,3}(?:,[0-9]{3})+)",chunk)
        if pm:
            out.append({
                "source":"GMT","ref":ref,"variant":variant(chunk),"label":ref,
                "new_price":None,"used_price":int(pm.group(1).replace(",","")),
                "quote_type":"public_buyback","source_url":url
            })
    return out

def scan_simple_page(page, refs, name, url):
    page.goto(url,wait_until="domcontentloaded",timeout=45000);page.wait_for_timeout(1200)
    text=page.locator("body").inner_text(timeout=10000) or ""
    out=[]
    for ref in refs:
        for m in re.finditer(re.escape(ref),text,re.I):
            chunk=text[max(0,m.start()-180):min(len(text),m.start()+650)]
            vals=[int(x.replace(",","")) for x in re.findall(r"([0-9]{1,3}(?:,[0-9]{3})+)\s*円",chunk)]
            if not vals:continue
            if name=="KOMEHYO":
                usedm=re.search(r"使用感の少ない中古品\s*([0-9,]+)\s*円",chunk)
                newm=re.search(r"未使用品\s*([0-9,]+)\s*円",chunk)
                marketm=re.search(r"相場価格\s*([0-9,]+)\s*円",chunk)
                used=int(usedm.group(1).replace(",","")) if usedm else None
                new=int((newm or marketm).group(1).replace(",","")) if (newm or marketm) else None
            elif name=="大黒屋":
                new=vals[0];used=vals[1] if len(vals)>1 else None
            else:
                new=None;used=vals[-1]
            out.append({
                "source":name,"ref":ref,"variant":variant(chunk),"label":ref,
                "new_price":new,"used_price":used,"quote_type":"public_buyback","source_url":url
            })
    return out

def load_state():
    raw=os.environ.get("GOOFISH_STORAGE_STATE_B64","").strip()
    if not raw:return False
    try:
        STATE_FILE.write_bytes(base64.b64decode(raw))
        return True
    except Exception as e:
        log("state decode failed",e);return False

def main():
    keywords=[x.strip() for x in os.environ.get("WATCH_KEYWORDS",",".join(DEFAULT_KEYWORDS)).split(",") if x.strip()][:30]
    refs=[]
    for k in keywords:
        r=ref_from_text(k)
        if r and r.upper()==k.upper():refs.append(r)
    refs=list(dict.fromkeys(refs))[:20]
    has_state=load_state()
    result={
        "generated_at":datetime.now(timezone.utc).isoformat(),"runner":"github-actions",
        "goofish_login_configured":has_state,
        "goofish_status":"not_configured" if not has_state else "starting",
        "items":[],"jp_quotes":[],"errors":[]
    }
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=["--disable-dev-shm-usage"])
        ctx=browser.new_context(storage_state=str(STATE_FILE) if has_state else None,locale="zh-CN")
        page=ctx.new_page()
        if has_state:
            all_items=[]
            for i,k in enumerate(keywords,1):
                try:
                    xs=scan_goofish(page,k);all_items.extend(xs);log("goofish",i,k,len(xs))
                except Exception as e:
                    result["errors"].append(f"goofish {k}: {e}")
                    if "LOGIN_EXPIRED" in str(e) or "SECURITY" in str(e):break
            result["items"]=list({x["item_id"]:x for x in all_items}.values())
            result["goofish_status"]="ok" if result["items"] else ("blocked_or_expired" if result["errors"] else "empty")
        q=[]
        for r in refs:
            try:q.extend(scan_gmt(page,r))
            except Exception as e:result["errors"].append(f"GMT {r}: {e}")
        for name,url in [
            ("KOMEHYO","https://komehyo.jp/kaitori/watch/"),
            ("QUARK","https://www.909.co.jp/rolex_buy.html"),
            ("大黒屋","https://kaitori.e-daikoku.com/brand/shop/ikebukuro-higashiguchi/")
        ]:
            try:q.extend(scan_simple_page(page,refs,name,url))
            except Exception as e:result["errors"].append(f"{name}: {e}")
        dedup={}
        for x in q:dedup[(x["source"],x["ref"],x.get("variant",""),x.get("new_price"),x.get("used_price"))]=x
        result["jp_quotes"]=list(dedup.values())
        browser.close()
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    log("wrote",len(result["items"]),"items and",len(result["jp_quotes"]),"JP quotes")

if __name__=="__main__":
    main()
