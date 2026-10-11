package com.watchradar.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {
    private static final String WATCH_BASE = "https://japan-watch-buyback.hatchable.site";
    private static final String COLLECTOR_API = WATCH_BASE + "/api/xianyu-collector";
    private static final String GOOFISH_HOME = "https://www.goofish.com/";
    private static final String GOOFISH_SEARCH = "https://www.goofish.com/search?q=";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();

    private WebView web;
    private TextView status;
    private Button radarBtn, fishBtn, testBtn, syncBtn, stopBtn;
    private volatile boolean syncing = false;
    private volatile boolean scrapeScheduled = false;
    private List<KeywordCfg> keywords = new ArrayList<>();
    private int keywordIndex = 0;
    private JSONArray collected = new JSONArray();
    private String currentKeyword = "";

    private static class KeywordCfg {
        final String keyword;
        final int minPrice;
        KeywordCfg(String keyword, int minPrice) {
            this.keyword = keyword;
            this.minPrice = minPrice;
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(10), dp(10), dp(10), dp(8));

        status = new TextView(this);
        status.setTextSize(15f);
        status.setTextColor(Color.rgb(25,25,25));
        status.setText("第一次使用：①连接 Watch Radar 登录 ②登录闲鱼 ③同步全部");
        status.setPadding(dp(4), dp(2), dp(4), dp(10));
        root.addView(status, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        LinearLayout buttons1 = new LinearLayout(this);
        buttons1.setOrientation(LinearLayout.HORIZONTAL);
        radarBtn = button("① 连接 Radar");
        fishBtn = button("② 登录闲鱼");
        buttons1.addView(radarBtn, weight());
        buttons1.addView(fishBtn, weight());
        root.addView(buttons1);

        LinearLayout buttons2 = new LinearLayout(this);
        buttons2.setOrientation(LinearLayout.HORIZONTAL);
        testBtn = button("③ 测试126500LN");
        syncBtn = button("同步全部");
        stopBtn = button("停止");
        buttons2.addView(testBtn, weight());
        buttons2.addView(syncBtn, weight());
        buttons2.addView(stopBtn, weight());
        root.addView(buttons2);

        web = new WebView(this);
        root.addView(web, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);

        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(web, true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                CookieManager.getInstance().flush();
                if (syncing && url != null && url.contains("goofish.com") && url.contains("search")) {
                    scheduleScrape();
                }
            }
        });

        radarBtn.setOnClickListener(v -> {
            stopSync(false);
            setStatus("请在下面页面登录 Watch Radar。登录完成后直接点“② 登录闲鱼”。");
            web.loadUrl(WATCH_BASE);
        });

        fishBtn.setOnClickListener(v -> {
            stopSync(false);
            setStatus("请在下面页面正常登录闲鱼。登录成功后回到顶部点“③ 同步全部”。");
            web.loadUrl(GOOFISH_HOME);
        });

        testBtn.setOnClickListener(v -> startSync(true));
        syncBtn.setOnClickListener(v -> startSync(false));
        stopBtn.setOnClickListener(v -> stopSync(true));

        web.loadUrl(WATCH_BASE);
    }

    private Button button(String text) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextSize(14f);
        b.setAllCaps(false);
        return b;
    }

    private LinearLayout.LayoutParams weight() {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, dp(48), 1f);
        p.setMargins(dp(2), dp(2), dp(2), dp(2));
        return p;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private void setStatus(String t) {
        ui.post(() -> status.setText(t));
    }

    private void startSync(boolean testOnly) {
        if (syncing) return;
        setStatus("正在读取 Watch Radar 的关键词列表…");
        io.execute(() -> {
            try {
                JSONObject cfg = getJson(COLLECTOR_API + "?action=config");
                JSONArray arr = cfg.optJSONArray("keywords");
                if (arr == null) throw new Exception("没有读取到关键词");
                List<KeywordCfg> list = new ArrayList<>();
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject x = arr.getJSONObject(i);
                    if (!x.optBoolean("enabled", true)) continue;
                    list.add(new KeywordCfg(
                            x.optString("keyword", ""),
                            Math.max(30000, x.optInt("min_price", 30000))
                    ));
                }
                if (list.isEmpty()) throw new Exception("关键词列表为空");
                if (testOnly) {
                    KeywordCfg chosen = null;
                    for (KeywordCfg k : list) if ("126500LN".equalsIgnoreCase(k.keyword)) { chosen = k; break; }
                    List<KeywordCfg> one = new ArrayList<>();
                    one.add(chosen != null ? chosen : list.get(0));
                    list = one;
                }
                final List<KeywordCfg> ready = list;

                ui.post(() -> {
                    keywords = ready;
                    keywordIndex = 0;
                    collected = new JSONArray();
                    syncing = true;
                    scrapeScheduled = false;
                    setStatus("开始同步，共 " + keywords.size() + " 个关键词。");
                    loadNext();
                });
            } catch (Exception e) {
                setStatus("无法读取 Watch Radar 配置：" + friendly(e) + "\n请先点“① 连接 Radar”完成登录。");
            }
        });
    }

    private void loadNext() {
        if (!syncing) return;
        if (keywordIndex >= keywords.size()) {
            finishAndUpload(null);
            return;
        }
        KeywordCfg cfg = keywords.get(keywordIndex);
        currentKeyword = cfg.keyword;
        scrapeScheduled = false;
        setStatus("正在扫描 " + (keywordIndex + 1) + "/" + keywords.size() +
                "：" + currentKeyword + "\n已收集 " + collected.length() + " 条");
        try {
            String q = URLEncoder.encode(currentKeyword, StandardCharsets.UTF_8.name());
            web.loadUrl(GOOFISH_SEARCH + q);
        } catch (Exception e) {
            stopWithError("关键词编码失败");
        }
    }

    private void scheduleScrape() {
        if (scrapeScheduled || !syncing) return;
        scrapeScheduled = true;

        ui.postDelayed(() -> {
            if (!syncing) return;
            web.evaluateJavascript("window.scrollTo(0, document.body.scrollHeight);", null);
            ui.postDelayed(() -> {
                if (!syncing) return;
                web.evaluateJavascript("window.scrollTo(0, document.body.scrollHeight);", null);
                ui.postDelayed(this::extractCurrentPage, 1000);
            }, 900);
        }, 2200);
    }

    private void extractCurrentPage() {
        if (!syncing) return;

        String js = """
        (() => {
          const body = (document.body && document.body.innerText) || '';
          const security = /非法访问|安全验证|验证码|异常访问|RGV587|Baxia/i.test(body);
          const login = /请先登录|登录后查看|登录闲鱼/i.test(body) && !document.querySelector('a[href*="/item?id="]');
          const anchors = Array.from(document.querySelectorAll('a[href*="/item?id="]')).slice(0, 50);
          const items = anchors.map(a => {
            const tx = (a.innerText || '').replace(/\\s+/g,' ').trim();
            const titleEl = a.querySelector('[class*="row1-wrap-title"],[class*="main-title"],[class*="title"]');
            const title = ((titleEl && titleEl.innerText) || tx).replace(/\\s+/g,' ').trim().slice(0,220);
            let priceText = '';
            const priceEl = a.querySelector('[class*="price-wrap"],[class*="price"]');
            if (priceEl) priceText = (priceEl.innerText || '').replace(/\\s+/g,' ').trim();
            if (!priceText) {
              const parts = Array.from(a.querySelectorAll('span,div')).map(x => (x.innerText||'').trim());
              priceText = parts.find(x => /^[¥￥]?\\s*\\d+(?:\\.\\d+)?\\s*万?\\s*$/.test(x)) || '';
            }
            const img = a.querySelector('img');
            return {
              href: a.href || '',
              title,
              priceText,
              image: img ? (img.currentSrc || img.src || '') : ''
            };
          });
          return JSON.stringify({security, login, body: body.slice(0,500), items});
        })()
        """;

        web.evaluateJavascript(js, raw -> {
            try {
                Object unquoted = new JSONTokener(raw).nextValue();
                String jsonText = unquoted == JSONObject.NULL ? "" : String.valueOf(unquoted);
                JSONObject result = new JSONObject(jsonText);

                if (result.optBoolean("security", false)) {
                    finishAndUpload("GOOFISH_SECURITY_VERIFICATION");
                    return;
                }
                if (result.optBoolean("login", false)) {
                    finishAndUpload("GOOFISH_LOGIN_REQUIRED");
                    return;
                }

                JSONArray arr = result.optJSONArray("items");
                int added = 0;
                KeywordCfg cfg = keywords.get(keywordIndex);
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject x = arr.optJSONObject(i);
                        if (x == null) continue;
                        String href = x.optString("href", "");
                        String title = x.optString("title", "").trim();
                        String priceText = x.optString("priceText", "");
                        String id = extractItemId(href);
                        int price = parsePrice(priceText);
                        if (id.isEmpty() || title.isEmpty() || price < cfg.minPrice) continue;

                        JSONObject item = new JSONObject();
                        item.put("item_id", id);
                        item.put("keyword", currentKeyword);
                        item.put("title", title);
                        item.put("price_cny", price);
                        item.put("seller", "");
                        item.put("area", "");
                        item.put("badge", "");
                        item.put("item_url", href);
                        item.put("image_url", x.optString("image", ""));
                        item.put("parsed_ref", parseRef(title));
                        String bad = suspiciousWord(title);
                        item.put("suspicious_keyword", bad.isEmpty() ? JSONObject.NULL : bad);
                        item.put("risk_level", !bad.isEmpty() ? "high" : (!parseRef(title).isEmpty() ? "low" : "medium"));
                        item.put("raw", x);

                        if (!containsItem(collected, id)) {
                            collected.put(item);
                            added++;
                        }
                    }
                }

                int shown = arr == null ? 0 : arr.length();
                setStatus("完成 " + (keywordIndex + 1) + "/" + keywords.size() + "：" + currentKeyword +
                        "\n页面 " + shown + " 条，本次加入 " + added + " 条，累计 " + collected.length() + " 条");

                keywordIndex++;
                ui.postDelayed(this::loadNext, 700);
            } catch (Exception e) {
                finishAndUpload("PARSE_ERROR: " + friendly(e));
            }
        });
    }

    private boolean containsItem(JSONArray arr, String id) {
        for (int i = 0; i < arr.length(); i++) {
            JSONObject x = arr.optJSONObject(i);
            if (x != null && id.equals(x.optString("item_id"))) return true;
        }
        return false;
    }

    private int parsePrice(String s) {
        if (s == null) return 0;
        String t = s.replace(",", "").replace("￥", "¥").trim();
        Matcher m = Pattern.compile("(\\d+(?:\\.\\d+)?)").matcher(t);
        if (!m.find()) return 0;
        try {
            double v = Double.parseDouble(m.group(1));
            if (t.contains("万")) v *= 10000d;
            if (v < 1000 && !t.contains("万")) return 0;
            return (int)Math.round(v);
        } catch (Exception e) {
            return 0;
        }
    }

    private String extractItemId(String href) {
        try {
            Uri u = Uri.parse(href);
            String id = u.getQueryParameter("id");
            return id == null ? "" : id;
        } catch (Exception e) {
            return "";
        }
    }

    private String parseRef(String text) {
        if (text == null) return "";
        String u = text.toUpperCase(Locale.ROOT);
        String[] patterns = {
                "\\b(1[012]\\d{4}[A-Z]{0,6})\\b",
                "\\b(15\\d{3}[A-Z]{0,6})\\b",
                "\\b(26\\d{3}[A-Z]{0,6})\\b",
                "\\b(57\\d{2}(?:/[A-Z0-9]+)?)\\b",
                "\\b(51\\d{2}[A-Z]?)\\b",
                "\\b(\\d{3}\\.\\d{2}\\.\\d{2}\\.\\d{2}\\.\\d{2}\\.\\d{3})\\b"
        };
        for (String p : patterns) {
            Matcher m = Pattern.compile(p).matcher(u);
            if (m.find()) return m.group(1);
        }
        return "";
    }

    private String suspiciousWord(String t) {
        if (t == null) return "";
        String[] bad = {"高仿","复刻","仿品","定制","改装","后改","表带","表盒","配件","订金","定金","专拍","机芯单卖","拆件","故障","仅配件"};
        for (String x : bad) if (t.contains(x)) return x;
        return "";
    }

    private void finishAndUpload(String error) {
        if (!syncing) return;
        syncing = false;
        scrapeScheduled = false;
        setStatus("正在上传 " + collected.length() + " 条到 Watch Radar…");

        final JSONArray payloadItems = collected;
        io.execute(() -> {
            try {
                JSONObject b = new JSONObject();
                b.put("action", "upload");
                b.put("device_name", "Android Phone");
                b.put("scanned", payloadItems.length());
                b.put("items", payloadItems);
                if (error != null) b.put("error", error);

                JSONObject resp = postJson(COLLECTOR_API, b);
                int received = resp.optInt("received", payloadItems.length());
                int fresh = resp.optInt("new_items", 0);
                String tail = error == null
                        ? "同步完成：上传 " + received + " 条，其中新增 " + fresh + " 条。"
                        : "同步已停止：" + error + "\n已把停止前的 " + received + " 条上传。";
                setStatus(tail + "\n现在打开 Watch Radar 就能看结果。");
            } catch (Exception e) {
                setStatus("采集完成，但上传失败：" + friendly(e) +
                        "\n请先点“① 连接 Radar”确认仍处于登录状态，然后再同步。");
            }
        });
    }

    private void stopWithError(String err) {
        if (!syncing) return;
        finishAndUpload(err);
    }

    private void stopSync(boolean show) {
        syncing = false;
        scrapeScheduled = false;
        if (show) setStatus("已停止。已抓但尚未上传的数据不会继续处理。");
    }

    private JSONObject getJson(String url) throws Exception {
        HttpURLConnection c = open(url, "GET");
        int code = c.getResponseCode();
        String body = readAll(code >= 400 ? c.getErrorStream() : c.getInputStream());
        if (code != 200) throw new Exception("HTTP " + code);
        if (body.trim().startsWith("<")) throw new Exception("Watch Radar 尚未登录");
        return new JSONObject(body);
    }

    private JSONObject postJson(String url, JSONObject payload) throws Exception {
        HttpURLConnection c = open(url, "POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
        c.setFixedLengthStreamingMode(data.length);
        try (OutputStream out = c.getOutputStream()) {
            out.write(data);
        }
        int code = c.getResponseCode();
        String body = readAll(code >= 400 ? c.getErrorStream() : c.getInputStream());
        if (code != 200) throw new Exception("HTTP " + code + " " + body);
        if (body.trim().startsWith("<")) throw new Exception("Watch Radar 登录已失效");
        return new JSONObject(body);
    }

    private HttpURLConnection open(String url, String method) throws Exception {
        CookieManager.getInstance().flush();
        HttpURLConnection c = (HttpURLConnection)new URL(url).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(15000);
        c.setReadTimeout(20000);
        c.setRequestProperty("Accept", "application/json");
        c.setRequestProperty("User-Agent", web.getSettings().getUserAgentString());
        String cookie = CookieManager.getInstance().getCookie(WATCH_BASE);
        if (cookie != null && !cookie.isEmpty()) c.setRequestProperty("Cookie", cookie);
        return c;
    }

    private String readAll(InputStream in) throws Exception {
        if (in == null) return "";
        BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        StringBuilder b = new StringBuilder();
        String line;
        while ((line = r.readLine()) != null) b.append(line);
        r.close();
        return b.toString();
    }

    private String friendly(Exception e) {
        String m = e.getMessage();
        return (m == null || m.isEmpty()) ? e.getClass().getSimpleName() : m;
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        syncing = false;
        io.shutdownNow();
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
