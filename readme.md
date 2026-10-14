# HK PropPulse · 香港楼市硬数据与AI舆情个人研究终端

专为个人楼市投资研究打造的零成本自动化数据看板，整合香港四大权威硬数据渠道与本地主流社区舆情，并通过 **GitHub Actions + GitHub Pages** 实现每日早晚云端全自动抓取更新。

## 1. 核心数据源（已核验直达）

- **政府官方统计**：[差饷物业估价署 (RVD) 物业市场统计](https://www.google.com/search?q=https%3A%2F%2Fwww.rvd.gov.hk%2Fen%2Fpublications%2Fproperty_market_statistics.html)、[香港政府开放数据平台 RVD 数据集](https://www.google.com/search?q=https%3A%2F%2Fdata.gov.hk%2Fen-data%2Fdataset%2Fhk-rvd-tsinfo_rvd-property-market-statistics)、[土地注册处每月统计数字](https://www.google.com/search?q=https%3A%2F%2Fwww.landreg.gov.hk%2Fen%2Fmonthly%2Fmonthly.htm)、[IRIS 网上查册系统](https://www.google.com/search?q=https%3A%2F%2Fwww.iris.gov.hk%2F)
- **龙头代理行与放盘平台**：[中原城市领先指数 (CCL)](https://www.google.com/search?q=https%3A%2F%2Fhk.centanet.com%2Finfo%2Fen%2Findex)、[中原网上找楼成交数据](https://www.google.com/search?q=https%3A%2F%2Fhk.centanet.com%2Ffindproperty%2Fen%2Fcentadata)、[美联物业楼市洞察](https://www.google.com/search?q=https%3A%2F%2Fwww.midland.com.hk%2Fen%2Fmarket-insight)、[美联信心指数](https://www.google.com/search?q=https%3A%2F%2Fwww.midland.com.hk%2Fen%2Fmarket-insight%2Fconfidence-index)、[28Hse 香港屋网实时买卖盘](https://www.google.com/search?q=https%3A%2F%2Fwww.28hse.com%2Fen%2Fbuy%2Fapartment)、[House730 楼市数据中心](https://www.google.com/search?q=https%3A%2F%2Fwww.house730.com%2Fen-us%2Fmarket-data%2F)
- **本地论坛与社媒舆情**：[连登讨论区 (LIHKG) 房屋台](https://www.google.com/search?q=https%3A%2F%2Flihkg.com%2Fcategory%2F37)、[香港讨论区地产版 (Fid=110)](https://www.google.com/search?q=https%3A%2F%2Fwww.discuss.com.hk%2Fforumdisplay.php%3Ffid%3D110%26page%3D875)、[HKET 地产站网络热话](https://www.google.com/search?q=https%3A%2F%2Fps.hket.com%2Farticle%2F3440636%2F%25E3%2580%2590%25E7%25B6%25B2%25E7%25B5%25A1%25E7%2586%25B1%25E8%25A9%25B1%25E3%2580%2591%25E8%25B2%25B7%25E6%25A8%2593%25E9%2580%25A3%25E5%25A4%25A7%25E5%25B9%25B3%25E5%258F%25B0%25E6%2598%25AF%25E5%25A5%25BD%25E6%2598%25AF%25E5%25A3%259E%25E6%2583%25B9%25E7%2586%25B1%25E8%25AD%25B0%2520%2520%25E5%25A4%259A%25E5%2580%258B%25E8%258A%25B1%25E5%259C%2592vs%25E7%25A9%258D%25E5%25AD%2598%25E5%259E%2583%25E5%259C%25BE)

## 2. 仓库文件清单与作用

| 文件路径 | 作用说明 |
| :--- | :--- |
| `index.html` | 网站前端主页（打开网址时自动显示，每次加载自动读取最新的 `market_data.json`） |
| `market_data.json` | 存放香港各区呎价、CCL指数、一手新盘去化及论坛热帖的结构化数据文件 |
| `scraper.py` | Python 自动抓取脚本（由 GitHub 云端服务器定时运行，抓取最新数据并覆写 `market_data.json`） |
| `.github/workflows/update_data.yml` | GitHub Actions 定时任务配置文件（设定每天香港时间 09:00 与 18:00 自动运行爬虫） |
| `README.md` | 项目说明文档 |

## 3. GitHub 网页版零基础上传步骤

1. 在 GitHub 新建一个 **Public（公开）** 仓库（例如命名为 `hk-property-tracker`）。
2. 点击仓库页面的 **Add file → Upload files**，将 `index.html`、`market_data.json`、`scraper.py`、`README.md` 四个文件直接拖入网页并点击 **Commit changes**。
3. 点击 **Add file → Create new file**，在文件名框输入 `.github/workflows/update_data.yml`，将 `update_data.yml` 的内容粘贴进去并点击 **Commit changes**。
4. 进入仓库顶部 **Settings → Pages**，在 **Branch** 下拉框选择 `main` 并点击 **Save**，1 分钟后即可通过生成的网址访问自动更新看板。
