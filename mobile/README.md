# Watch Radar Mobile v1.0

用途：用 Android 手机自身网络访问闲鱼，将公开商品搜索结果同步到现有 Watch Radar。

首次使用：
1. 点「① 连接 Radar」，在 App 内登录现有 Watch Radar/Hatchable。
2. 点「② 登录闲鱼」，在 App 内正常登录闲鱼。
3. 先点「③ 测试126500LN」确认手机环境可正常访问；成功后再点「同步全部」。

同步逻辑：
- 从 Watch Radar `/api/xianyu-collector?action=config` 读取启用关键词。
- 手机 WebView 逐个打开闲鱼公开搜索。
- 只保留价格 >= 每个关键词的 min_price（现有配置默认为 30000 RMB）。
- 识别 Ref. 和异常关键词。
- 遇到 非法访问 / 安全验证 / 验证码 / RGV587 / Baxia 时立即停止。
- 不尝试绕过验证码或风控。
- 闲鱼 Cookie 不上传，只留在 Android WebView 的 Cookie 存储中。
- 采集结果 POST 回现有 `/api/xianyu-collector`。

GitHub：
把本目录放到 `baidengdebaba-sys/watch-radar-cloud/mobile/`，把 workflow 放到仓库根目录 `.github/workflows/build-mobile-apk.yml`。
Actions 会生成并提交 `downloads/WatchRadarMobile.apk`。
