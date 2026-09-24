# Market Digest：博主点位汇总 + 宏观简报

把你关注的博主在 **YouTube、X、个人网站、Discord** 上给出的股票点位自动收集起来，用 Claude 提取成结构化数据，汇总到一个随时可看的 **点位看板（Dashboard）**。程序还会按时把**盘前、盘中、收盘简报**和**宏观分析**发到你的邮箱。

## 它做什么

| 信息源 | 怎么拿 | 延迟 |
|---|---|---|
| YouTube | 发现新视频 → 下载字幕；**没有字幕的频道**（如 Shanghao Jin）自动下载音频，用 Whisper 在本机语音识别（免费） | 有字幕：几分钟；语音识别：30 分钟视频约 10–20 分钟 |
| X / Twitter | 官方 API（按量付费）；没配 API 时读取 X 的通知邮件 | 几分钟 |
| 博主个人网站 | 定时抓取页面，内容变了就重新提取点位 | 几分钟 |
| Discord | ① 安卓手机转发 Discord 通知（全自动）② 你手动转发消息/截图到自己的服务器 | 实时 |

每条内容会交给 Claude 处理：
- **点位**：代码、价格或区间、类型（支撑/阻力/目标/止损/入场/多空分界）、时间框架、观点强弱，以及原文出处。
- **宏观分析**：利率、美联储、通胀、战争、地缘政治等的中文总结和风险判断。

**点位看板**（`http://你的服务器:8000`）：
- 每 30 秒自动刷新。博主一更新，点位就出现在看板上。
- 多个博主给出的相近点位会合并，并标出「×2 共识」。
- 显示现价和距离；点开某只股票可以看它的点位阶梯，上面标着每个博主的原话和原文链接。
- 可以按博主筛选，也可以只看共识点位。
- 右侧有宏观观点、最新动态和信息源统计。
- 手机上也能看。链接后面加 `#NVDA` 会直接展开该股票。

**邮件**（美东时间，可以在 `config.yaml` 里改）：

| 邮件 | 时间 | 内容 |
|---|---|---|
| 盘前点位 | 周一至五 08:45 | **每次都列出全部点位**，并和上一封比较：新增、调整（旧价→新价）、移除的点位会被高亮；另有接近现价的点位和 AI 综述 |
| 盘中更新 | 12:30 | 同上 |
| 收盘复盘 | 16:30 | 同上 |
| **每日宏观总结** | 每天 19:00 | 综合过去 24 小时所有来源（视频、推文、Discord、网站）的宏观观点：按主题（利率/美联储、通胀、地缘/战争……）整理，标明谁持什么观点，列出需关注的事件、风险和分歧，附各博主原始观点 |
| 宏观周报 | 周日 18:00 | 一周宏观观点综合 |
| 宏观即时提醒（默认关闭） | 博主一发宏观内容 | 需要时把 `macro_alert_immediately` 改为 `true` |

点位以看板为准，看板实时更新；邮件只是定时快照。

## 大概费用（每月）

| 项目 | 费用 |
|---|---|
| Claude API（提取点位和宏观 + 写简报） | 每天 20 条推文、20 条 Discord、每周 10 个视频：约 $55 |
| X API（按量付费，$0.005/条推文） | 每天 20 条：约 $3 |
| 服务器 | 家里电脑 $0；VPS 约 $5 |
| YouTube 字幕代理（只有用 VPS 才需要） | 约 $3–10 |

邮件是精心排版的 HTML（卡片、颜色标签、现价分隔线），在 Gmail 网页版和手机 App 上都能正常显示。

## 部署：两种选择

程序需要一台**一直开着**的机器。

**A. 家里的电脑 / 迷你主机（推荐起步用）**
YouTube 会拦截云服务器 IP 下载字幕，家里的网络没有这个问题，也不用买代理。缺点是电脑得一直开着；手机要转发 Discord 通知的话，还需要一个外网能访问的地址。可以用 [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) 或 [Tailscale Funnel](https://tailscale.com/kb/1223/funnel)，免费。

**B. 云服务器 VPS**
随时在线，自带公网地址，配好域名后用 `docker-compose` 里的 Caddy 自动上 HTTPS。但 **YouTube 字幕需要住宅代理**，在 `.env` 里填 `WEBSHARE_PROXY_USERNAME/PASSWORD` 或 `YOUTUBE_PROXY`。

## Windows 安装（推荐：跑在家里电脑上）

1. **安装 Python**：到 https://www.python.org/downloads/ 下载 Python 3.12。安装第一页**务必勾选「Add python.exe to PATH」**。
2. **下载代码**：[点这里下载 ZIP](https://github.com/MOFD308/resume/archive/refs/heads/claude/stock-aggregation-macro-analysis-wz1qby.zip)，解压到一个固定位置，比如 `D:\market-digest`。之后用到的是里面的 `market-digest` 文件夹。
3. **双击 `windows\install.bat`**：它会自动安装依赖，然后用记事本打开 `.env`。把密钥填进去，保存，关掉记事本。
4. **双击 `windows\check.bat`**：逐项检查 Claude、Gmail、X、YouTube 能不能连通，全部 ✅ 后你会收到一封测试邮件。
5. **双击 `windows\start.bat`**：程序启动，浏览器会自动打开看板。这个黑色窗口可以最小化，**但不要关**，关掉程序就停了。
6. **（可选）双击 `windows\autostart.bat`**：设置开机自动启动，还可以顺便关闭插电时的自动睡眠。

第一次启动时，Windows 防火墙可能会弹窗，点「允许」。这样手机在同一个 Wi-Fi 下就能打开 `http://电脑IP:8000` 看看板。

如果日志里出现 `Sign in to confirm you're not a bot`（下载没字幕的视频音频时），先用 Firefox 登录一下 YouTube，再把 `config.yaml` 里的 `cookies_from_browser` 改成 `firefox`。

## 安装步骤（通用 / Mac / Linux）

### 1. 准备密钥

```bash
cd market-digest
cp .env.example .env
cp config.example.yaml config.yaml
```

在 `.env` 里填：
- `ANTHROPIC_API_KEY`：到 https://console.anthropic.com/ 申请。
- `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD`：**不是你的 Gmail 登录密码**，而是 Google 为程序单独生成的 16 位「应用专用密码」。Gmail 先开两步验证，再到 https://myaccount.google.com/apppasswords 生成。`NEWSLETTER_TO` 填收简报的邮箱，可以就是同一个。
- `INGEST_TOKEN`：随便设一串长的随机字符，手机转发通知时要用。
- `DASHBOARD_PASSWORD`：看板的登录密码。**放到公网上一定要设。**
- `X_BEARER_TOKEN`（可选）：到 https://developer.x.com 开通按量付费，创建 App 后复制 Bearer Token。

### 2. 填写信息源（`config.yaml`）

- **YouTube**：`channel_id` 可以这样查：`python -m market_digest resolve-youtube @频道名`。
- **X**：填 `handle`，不带 @。
- **网站**：填点位页面的 `url`。如果需要登录，把浏览器里的 Cookie 放进 `.env` 的某个变量，再在 `cookie_env` 里填这个变量名。
- **Discord**：见下面第 4 步。
- `category`：`levels`（主要给点位）、`macro`（主要讲宏观）或 `both`。

### 3. 运行

```bash
# 方式一：直接运行
pip install -r requirements.txt
python -m market_digest run          # 全部启动：抓取、Discord、定时邮件、看板（端口 8000）

# 方式二：Docker（VPS 推荐）
docker compose up -d --build
```

其他常用命令：

```bash
python -m market_digest poll                # 立刻抓一次所有来源并提取点位
python -m market_digest preview premarket   # 生成盘前邮件预览到 data/preview-premarket.html（不发送）
python -m market_digest send premarket      # 立刻发一封盘前邮件（也可以是 midday / close / macro_daily / weekly）
python -m pytest                            # 运行测试
```

### 4. Discord 设置

你不是群管理员，所以不能把 Bot 加进博主的服务器。用你自己的账号自动抓消息（self-bot）违反 Discord 条款，会被封号，这个程序不做。可以用下面两种方式，也可以同时用。

**方式 ①：安卓手机转发通知（全自动）**

1. 在 Discord 里，对博主发点位的频道，把通知设成「所有消息」。
2. 手机装 **MacroDroid**（免费版就够）。新建一个宏：
   - **触发器**：通知 → 收到通知 → 应用选 Discord。
   - **动作**：网络 → HTTP 请求：
     - 方法 `POST`，网址 `https://你的地址/ingest/android?token=你的INGEST_TOKEN`
     - 内容类型选表单 `application/x-www-form-urlencoded`
     - 参数 `package` = `com.discord`，`title` = 通知标题，`text` = 通知文字。这两个值从 MacroDroid 的「魔术文字」菜单里选。
3. 在 `config.yaml` 里给这个博主写 `match` 关键词，比如服务器名加博主昵称。通知里同时出现这些词，才算这个博主的消息。
4. 手机上 MacroDroid 和 Discord 都要关掉「电池优化」，否则后台可能被系统杀掉。

限制：很长的消息在通知里可能被截断，图片看不到。程序会自动过滤重复通知。所有消息都会交给 Claude，包括不含数字的，这样宏观观点不会漏掉。

**方式 ②：手动转发到自己的服务器（适合截图）**

1. 在 Discord 里自己建一个服务器，比如「我的点位」，再建一个频道。
2. 到 https://discord.com/developers/applications 创建 Application：
   - 在 Bot 页面点 Reset Token，把 token 复制到 `.env` 的 `DISCORD_BOT_TOKEN`。
   - 打开 **MESSAGE CONTENT INTENT**。
3. 在 OAuth2 → URL Generator 里勾选 `bot`，权限选 `Read Messages/View Channels` 和 `Read Message History`，用生成的链接把 Bot 拉进**你自己的**服务器。
4. 打开 Discord 设置 → 高级 → 开发者模式，然后右键那个频道 → 复制频道 ID，填到 `config.yaml` 的 `channel_id`。
5. 以后看到重要点位，用 Discord 的「转发」按钮转过去，或者直接发截图。Claude 能读懂截图里的点位。

### 5. X 通知邮件（只在不用付费 API 时需要）

在 X 上打开这些博主的「🔔 推文通知」，并在 设置 → 通知 → 邮件通知 里打开相关邮件。程序会从 Gmail 读取 X 发来的通知邮件。这种方式可能漏推文，也有延迟，要准确及时还是建议用 API。

## 注意

- 点位由 AI 从字幕和帖子里提取。自动字幕偶尔会听错数字，所以每个点位都附了原话和原文链接，方便核对。
- 这只是整理你关注的博主的观点，不构成投资建议。
- 付费社区的内容仅供自己使用，不要对外分发。
