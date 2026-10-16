## HK PropPulse v7.0 · 盘源搜罗 · 笋盘雷达 · 财经新闻 · 走势预测 · 全网舆情

个人香港楼市研究看板。GitHub Actions 每天自动运行一条数据管线，结果写入 `market_data.json`，`index.html` 读取后展示。可部署在 GitHub Pages 或嵌入 Google Sites（方法见看板第 4 页）。

### 1. 管线六个阶段

| 阶段 | 做什么 | 数据来源 |
|---|---|---|
| `listings` 盘源 | Playwright 翻页抓取，去重、价格历史、减价识别、14 天未出现自动下架 | 中原、28Hse、美联、利嘉阁；`seed_listings.json` 种子盘 |
| `deals` 笋盘 | 每个买盘打 0–100 分并分 S/A/B 级，附理由与风险提示 | 盘源库 |
| `indicators` 宏观 | 1M / 隔夜 HIBOR、银行体系结余、差估署售价/租金指数、H 按实际利率、实盘回报、减价盘占比 | 金管局开放 API、差估署 data.gov.hk、`inbox/indicators_manual.json` |
| `news` 新闻 | 近 30 天楼市/财经新闻：去重、9 类自动分类、情绪研判、新闻情绪指数 | Google News RSS、政府新闻公报、RTHK 财经 |
| `social` 舆情 | 网民帖文/评论的情绪、话题、平台对比、周趋势、感知剖析；热帖解剖室 | 亲子王国、宅谷地产论坛、YouTube（需 API key）、Telegram 公开频道、手动导入 |
| `forecast` 预测 | 3 个月 / 半年 / 1 年多因子模型，给基准/乐观/悲观区间、上涨概率与因子贡献 | 以上全部 |

每个阶段独立容错：某个来源失败只记录错误，保留上次数据，看板不会被清空。运行状态写在 `pipeline_status`。

### 2. 关于小红书、Facebook 群组、WhatsApp/微信群
这些平台需要登录，条款也禁止自动抓取（Facebook 已停止向第三方开放群组内容 API），所以**不做自动抓取**。改用手动导入：
- 在看板「全网舆情」页的**手动导入框**粘贴内容，浏览器内即时分析，可下载 CSV；
- 或把 CSV / TXT 放进 `inbox/social/`（格式见该文件夹 README），每天自动纳入统计。

系统只保存 ≤120 字摘录，自动去掉 @用户名、电话、邮箱，不保存作者名。

### 3. 部署
1. 把整个文件夹推到仓库根目录（含 `proppulse/`、`inbox/`、`.github/workflows/scrape.yml`）。
2. 可选 Secrets：`YT_API_KEY`（YouTube 评论）、`OPENAI_API_KEY`（+ 可选 `OPENAI_BASE_URL`、`OPENAI_MODEL`，用于 AI 归纳大众感知）。
3. Actions → *HK PropPulse daily pipeline* → Run workflow，`mode` 填 `discover` 先跑一次，下载 `discover-debug` 检查 `summary.json`、`listings_summary.json`、`social_items.json`。
4. 之后每天 09:00 全量运行、18:00 轻量更新新闻/舆情/预测。

本地：`python scraper.py --offline`（不联网重算）；`python scraper.py --stages news,social,forecast`（只跑部分阶段）。

### 4. 调参
所有网址、权重、关键词都在 `proppulse/config.py`：盘源目标、笋盘权重、新闻 RSS 与关键词、论坛来源（LIHKG / 香港讨论区默认关闭）、Telegram 频道、YouTube 关键词、预测因子权重。情绪词典在 `proppulse/lexicon.py`。

### 5. 方法与限制
- **笋盘分** = 35% 较中位实尺折让 + 25% 表面回报 + 10% 楼龄 + 10% 已减价 + 10% 印花税档 + 5% 新上架 + 5% 实租佐证。可比中位数优先同屋苑，其次同片区同楼龄档；楼龄未知时取较低档（保守）。折让无法核实的盘最多 B 级。
- **情绪分析**是词典法（粤语 + 书面语 + 英文，含否定词反转），透明但不懂反讽；样本 < 30 条时会提示。
- **预测模型**是规则透明的多因子打分，**未经历史回测**；数据缺失或可信度低的因子会降权，并放宽区间。旧版人工研判保留在每张预测卡的「人工研判对照」里。
- 只抓公开页面，先检查 robots.txt，被禁止即跳过；遇验证码/拦截页即跳过，不做任何绕过。Reddit 的 robots.txt 禁止一般爬虫，所以默认会被跳过。
- 仅供个人研究，不构成投资建议。
