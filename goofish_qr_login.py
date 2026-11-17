"""Cloud QR login for Goofish.

Opens the Goofish login page through the home residential exit, publishes the QR
code to the `goofish-login` branch (qr.png + status.json), and waits for the owner
to scan it with the Xianyu app. On success the session is encrypted and left in
.goofish_state/ for the workflow to save into the Actions cache.

It never solves captchas or slider checks: if Goofish asks for a security
verification, it reports that and stops.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

import goofish_session as gs

HOME_URL = "https://www.goofish.com/"
MINI_LOGIN = ("https://passport.goofish.com/mini_login.htm?lang=zh_cn&appName=xianyu&appEntrance=web"
              "&styleType=vertical&bizParams=&notLoadSsoView=false&notKeepLogin=false&isMobile=false&qrCodeFirst=true&stie=77")
PUB = Path("/tmp/goofish-login-pub")
WAIT_SECONDS = int(os.environ.get("LOGIN_WAIT_SECONDS", "540"))
REPO = os.environ.get("GITHUB_REPOSITORY", "")
RUN_URL = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{REPO}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
SECURITY_SELECTORS = "#nc_1_n1z, .nc-container, .nc_wrapper, #baxia-dialog-content, .baxia-dialog"
SEQ = 0


def log(*a):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def sh(*args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True).stdout.strip()


def setup_pub():
    PUB.mkdir(parents=True, exist_ok=True)
    if (PUB / ".git").exists():
        return
    sh("git", "init", "-q", cwd=PUB)
    sh("git", "config", "user.name", "Watch Radar Login", cwd=PUB)
    sh("git", "config", "user.email", "watch-radar@users.noreply.github.com", cwd=PUB)
    token = os.environ.get("GITHUB_TOKEN", "")
    sh("git", "remote", "add", "origin", f"https://x-access-token:{token}@github.com/{REPO}.git", cwd=PUB)


def publish(status, message, png=None):
    """Force-push a single-commit branch holding status.json (+ qr.png while waiting)."""
    global SEQ
    SEQ += 1
    for f in PUB.iterdir():
        if f.name != ".git":
            f.unlink()
    now = datetime.now(timezone.utc)
    st = {"status": status, "message": message, "updated_at": now.isoformat(), "run_id": os.environ.get("GITHUB_RUN_ID"),
          "run_url": RUN_URL, "seq": SEQ, "has_qr": png is not None}
    if png:
        (PUB / "qr.png").write_bytes(png)
        st["qr_expires_at"] = (now + timedelta(seconds=150)).isoformat()
    (PUB / "status.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    sh("git", "update-ref", "-d", "HEAD", cwd=PUB, check=False)
    sh("git", "add", "-A", cwd=PUB)
    sh("git", "commit", "-q", "-m", f"goofish login: {status} #{SEQ}", cwd=PUB)
    r = subprocess.run(["git", "push", "-q", "-f", "origin", "HEAD:refs/heads/goofish-login"], cwd=PUB, capture_output=True, text=True)
    log("PUBLISH", status, SEQ, "ok" if r.returncode == 0 else "push failed")


def login_frame(page):
    for f in page.frames:
        if "mini_login" in (f.url or ""):
            return f
    return None


def open_login(page):
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
    for _ in range(15):
        f = login_frame(page)
        if f:
            try:
                f.wait_for_selector("#qrcode-img canvas", timeout=8000)
                return f
            except Exception:
                break
        page.wait_for_timeout(1000)
    # Fallback: open the passport page directly with the QR tab first.
    page.goto(MINI_LOGIN, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector("#qrcode-img canvas", timeout=20000)
    return page.main_frame


def identity_frame(page):
    for f in page.frames:
        if "identity_verify" in (f.url or ""):
            return f
    return None


def frame_png(frame):
    try:
        return frame.frame_element().screenshot(type="png")
    except Exception:
        return None


def frame_text(frame):
    try:
        return frame.locator("body").inner_text(timeout=3000) or ""
    except Exception:
        return ""


def security_check_present(page) -> str | None:
    """Only a *visible* slider/captcha counts; Goofish preloads hidden anti-bot frames on every page."""
    for f in page.frames:
        url = f.url or ""
        if f is not page.main_frame and re.search(r"punish|_____tmd_____|captcha", url, re.I):
            try:
                el = f.frame_element()
                box = el.bounding_box() if el.is_visible() else None
                if box and box["width"] > 120 and box["height"] > 80:
                    return "frame:" + url.split("?")[0][:80]
            except Exception:
                pass
        try:
            loc = f.locator(SECURITY_SELECTORS)
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    return "element:" + SECURITY_SELECTORS
        except Exception:
            pass
    return None


def qr_png(frame):
    try:
        el = frame.locator("#qrcode-img")
        if el.count() and el.first.is_visible():
            return el.first.screenshot(type="png")
    except Exception:
        pass
    return None


def refresh_qr(frame):
    for sel in ["#qrcode-img .refresh", ".qrcode-error a", ".qrcode-error button", "text=刷新"]:
        try:
            loc = frame.locator(sel)
            if loc.count():
                loc.first.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def verify_search(page) -> bool:
    page.goto("https://www.goofish.com/search?q=" + quote("劳力士 126610LN"), wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    body = frame_text(page.main_frame)
    if re.search(r"请先登录|登录后", body) and page.locator('a[href*="/item?id="]').count() == 0:
        return False
    return True


def main():
    setup_pub()
    publish("starting", "云端浏览器正在打开闲鱼登录页（约30秒）…")
    proxy = os.environ.get("WATCH_SOCKS_PROXY", "").strip()
    if not proxy:
        publish("error", "家庭出口未连通，未登录（避免用机房IP登录触发风控）。请稍后重试。")
        return 2
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-dev-shm-usage"])
        ctx = browser.new_context(locale="zh-CN", viewport={"width": 1280, "height": 900}, proxy={"server": proxy})
        page = ctx.new_page()
        try:
            frame = open_login(page)
            log("LOGIN_FRAME", (frame.url or "").split("?")[0], "frames:", [ (f.url or "").split("?")[0][:70] for f in page.frames ])
        except Exception as e:
            publish("error", f"登录页没有出现二维码：{type(e).__name__}")
            return 3
        deadline = time.time() + WAIT_SECONDS
        last_png, scanned, refreshes = None, False, 0
        scanned_at, reloaded, last_dbg = 0.0, False, 0.0
        verify_png, verifying = None, False
        while time.time() < deadline:
            if gs.is_logged_in(ctx):
                break
            # 新设备登录时闲鱼会要求账号本人再扫一个码、按提示刷脸确认；把这个码原样转给本人
            idf = identity_frame(page)
            if idf:
                if not verifying:
                    log("IDENTITY_VERIFY", re.sub(r"\s+", " ", frame_text(idf))[:80])
                    verifying = True
                    deadline = max(deadline, time.time() + 300)
                png = frame_png(idf)
                if png and png != verify_png:
                    publish("verify_identity", "闲鱼要求本人验证：请用闲鱼App再扫这个码，按提示完成刷脸（闲鱼对新设备登录的安全确认）", png)
                    verify_png = png
                page.wait_for_timeout(3000)
                continue
            if time.time() - last_dbg > 10:
                last_dbg = time.time()
                names = gs.cookie_names(ctx)
                log("WAIT", "scanned" if scanned else "qr", "cookies:", len(names), "unb" in names, "tracknick" in names,
                    "page:", (page.url or "").split("?")[0][:60], "frames:", [(f.url or "").split("?")[0][-40:] for f in page.frames][:6],
                    "text:", re.sub(r"\s+", " ", frame_text(frame))[:80])
            # 扫码确认后，登录结果有时只在父页面刷新后才落到 goofish.com 的 cookie 上
            if scanned and not verifying and not reloaded and time.time() - scanned_at > 60:
                reloaded = True
                try:
                    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(3000)
                except Exception:
                    pass
                if gs.is_logged_in(ctx):
                    break
                log("RELOAD_NOT_LOGGED_IN", "will show a fresh QR")
                frame = login_frame(page)
                if not frame:
                    try:
                        frame = open_login(page)
                    except Exception:
                        frame = page.main_frame
                scanned, last_png = False, None
            hit = security_check_present(page)
            if hit:
                log("SECURITY_CHECK", hit)
                publish("security", "闲鱼要求安全验证（滑块等）。为避免风控，云端不会自动处理，请过一段时间再试。")
                return 4
            text = frame_text(frame)
            if re.search(r"扫码成功|扫描成功|手机上确认|请在手机", text):
                if not scanned:
                    publish("scanned", "已扫码，请在手机上点“确认登录”")
                    scanned, scanned_at = True, time.time()
            elif re.search(r"已失效|已过期|刷新", text):
                if refreshes >= 4:
                    break
                refreshes += 1
                refresh_qr(frame)
                page.wait_for_timeout(1500)
                last_png = None
                scanned = False
            elif not scanned:
                png = qr_png(frame)
                if png and png != last_png:
                    publish("waiting_scan", "请用闲鱼App扫码：首页左上角“扫一扫”", png)
                    last_png = png
            page.wait_for_timeout(2000)

        if not gs.is_logged_in(ctx):
            publish("expired", "二维码超时未扫，云端仍未登录。需要时重新启动登录即可。")
            return 1
        page.wait_for_timeout(2000)
        ok = verify_search(page)
        saved = gs.save_from_context(ctx)
        browser.close()
    if not saved:
        publish("error", "已登录，但登录态加密保存失败（缺少密钥材料），请查看运行日志。")
        return 5
    publish("success", "云端已登录闲鱼。之后每轮采集会自动续期登录态。" + ("" if ok else "（搜索页验证未通过，下一轮采集会再确认）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
