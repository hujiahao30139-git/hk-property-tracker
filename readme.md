## HK PropPulse v6.0 · 全港实盘海量库 + 四大中介真实笋盘严选与近1月宏观预测终端

专为个人楼市投资研究打造的零成本自动化数据看板，聚合 **中原地产 (Centaline)**、**美联物业 (Midland)**、**28Hse 香港屋网**、**House730** 等平台公开在售/在租盘源，并内置**近1个月宏观财经新闻分析与 3个月/半年/1年楼价预测模型**。支持部署在 **Google Sites（协作平台）** 或 **GitHub Pages**。

### 0. v6.0 新增：Playwright 每日自动抓取 + 实盘海量库

| 文件 | 作用 |
|---|---|
| `scraper.py` | Playwright 真实 Chromium 翻页抓取；被动拦截页面自身的 JSON 接口响应，拿不到时用 DOM 卡片文字兜底；去重、价格历史、减价识别、14 天未出现自动下架 |
| `seed_listings.json` | 湾仔 / 铜锣湾 / 红磡 / 黄埔 首批 133 个经网页核对的盘源（118 买 + 15 租），每次运行都会合并 |
| `.github/workflows/scrape.yml` | 每天 09:00 HKT 自动运行并提交 `market_data.json` |
| `requirements.txt` | `playwright` |

**首次部署三步：**
1. 把以上文件连同 `index.html`、`market_data.json` 推到仓库根目录。
2. 在 Actions 页手动运行一次 *Daily HK listings scrape*，`mode` 填 `discover`，下载 `discover-debug` 产物，确认各平台的 JSON 接口 URL 是否被捕获；如平台改版，只需修改 `scraper.py` 顶部的 `TARGETS` / `JSON_HINTS`。
3. 之后每天自动运行（`mode` 留空 = normal）。本地预览可用 `python scraper.py --offline` 只重算种子数据。

**前端「全港实盘海量库」：** 焦点四区快捷标签、多关键词搜索（空格分隔，AND 匹配）、平台 / 大区 / 行政区 / 标签 / 价格 / 面积筛选、7 种排序、20/50/100 每页分页、导出当前筛选结果 CSV、一键带入按揭测算仪。点击「18 区估值表」任一行会同步过滤海量库。

**口径：** 估算月租优先用同盘/连租约实租 → 同屋苑租盘中位尺租 → 同片区按楼龄（≥40 年 / <40 年）分档的租盘中位尺租；旧楼缺同档样本时不估算。印花税按第 2 标准税率计算（≤400 万 $100，含边际缓冲区）。

**注意：** 仅作个人研究、低频抓取；scraper 会先检查 robots.txt，不做任何反爬绕过。GitHub Actions 的海外 IP 可能被部分平台限流或拦截，届时该平台保留上次数据。

### 1. 在 Google Sites（协作平台）实现相同效果的两种方法

#### 方法 A：整页嵌入 HTML 代码（无需配置 GitHub Pages 也能独立运行，30秒完成）
- 在画布（Canvas）中打开 index.html 预览，切换到 **「4. 🚀 Google Sites / GitHub 部署」** 板块，点击 **「📋 一键复制完整 HTML 代码」**。
- 打开 sites.google.com 并新建一个空白网站。
- 在右侧工具栏点击 **「页面 (Pages)」→ 将鼠标移到底部「+」号 → 点击「整页嵌入 (Full page embed)」**，命名为「香港楼市研究终端」。
- 点击页面中央的 **「添加嵌入内容 (Add embed)」→ 切换到「嵌入代码 (Embed code)」**，将刚刚复制的 index.html 完整代码粘贴进去，点击 **「下一步 (Next)」→「插入 (Insert)」**。
- 点击右上角 **「发布 (Publish)」**（可在右上角共享设置中设为**仅自己可见 Restricted**，保护个人私密研究）。

#### 方法 B：在 Google Sites 中同时实现「每日后台自动抓取更新」
- **方式 1（Google Sites + GitHub 自动数据源）**：保留你的 GitHub 仓库每天自动运行 scraper.py 更新 market\_data.json，然后在 Google Sites 网页的 **「4. 🚀 Google Sites / GitHub 部署」** 板块中，将你的 GitHub Raw 数据地址（格式为 https://raw.githubusercontent.com/你的用户名/仓库名/main/market\_data.json）填入 **「跨平台远程数据源 URL 配置」** 并点击保存。
- **方式 2（Google Sites 按网址嵌入）**：在 Google Sites 选择 **「嵌入 → 按网址 (By URL)」** 填入你的 GitHub Pages 网址。
