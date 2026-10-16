# -*- coding: utf-8 -*-
"""
HK PropPulse v7.1 —— 所有可调参数集中在这里。平台改版时，一般只需改本文件。

v7.1 根据第一次 discover 结果调整：
  * 中原：可正常抓取（页面 + /findproperty/api/Post/Search 接口），加入焦点四区租盘、全港翻页加到 10 页
  * 28Hse、美联：robots.txt 不允许自动抓取 → 已停用（enabled=False），尊重网站规则
  * 利嘉阁：返回 HTTP 403（拒绝访问）→ 已停用
"""

FOCUS_SUB_AREAS = ["湾仔", "铜锣湾", "红磡", "黄埔"]

_C = "https://hk.centanet.com/findproperty/list/"
_WHAMPOA = "%E9%BB%83%E5%9F%94%E8%8A%B1%E5%9C%92_3-MZDIIHHAHN"   # 黄埔花园
_HUNGHOM = "%E7%B4%85%E7%A3%A1_Mtr-5"                            # 红磡站附近
_WANCHAI_MTR = "%E7%81%A3%E4%BB%94_Mtr-56"                       # 湾仔站附近
_WANCHAI = "%E7%81%A3%E4%BB%94_19-HMA160"                        # 湾仔片区
_CWB = "%E9%8A%85%E9%91%BC%E7%81%A3_19-HMA144"                   # 铜锣湾片区

# ============================================================ 1. 盘源（Playwright）
LISTING_TARGETS = [
    # ---- 焦点四区：买盘 ----
    {"source": "Centaline", "type": "sale", "sub_area": "黄埔", "district": "九龙城区", "region": "九龙", "url": _C + "buy/" + _WHAMPOA},
    {"source": "Centaline", "type": "sale", "sub_area": "红磡", "district": "九龙城区", "region": "九龙", "url": _C + "buy/" + _HUNGHOM},
    {"source": "Centaline", "type": "sale", "sub_area": "湾仔", "district": "湾仔区", "region": "港岛", "url": _C + "buy/" + _WANCHAI_MTR},
    {"source": "Centaline", "type": "sale", "sub_area": "湾仔", "district": "湾仔区", "region": "港岛", "url": _C + "buy/" + _WANCHAI},
    {"source": "Centaline", "type": "sale", "sub_area": "铜锣湾", "district": "湾仔区", "region": "港岛", "url": _C + "buy/" + _CWB},
    # ---- 焦点四区：租盘（用于计算真实租金回报）----
    {"source": "Centaline", "type": "rent", "sub_area": "湾仔", "district": "湾仔区", "region": "港岛", "url": _C + "rent/" + _WANCHAI},
    {"source": "Centaline", "type": "rent", "sub_area": "铜锣湾", "district": "湾仔区", "region": "港岛", "url": _C + "rent/" + _CWB},
    {"source": "Centaline", "type": "rent", "sub_area": "红磡", "district": "九龙城区", "region": "九龙", "url": _C + "rent/" + _HUNGHOM},
    {"source": "Centaline", "type": "rent", "sub_area": "黄埔", "district": "九龙城区", "region": "九龙", "url": _C + "rent/" + _WHAMPOA},
    # ---- 全港 18 区（片区/行政区由中原数据自带）----
    {"source": "Centaline", "type": "sale", "url": _C + "buy", "max_pages": 10},
    {"source": "Centaline", "type": "rent", "url": _C + "rent", "max_pages": 8},
    # ---- 已停用：robots.txt 不允许或拒绝访问（2026-10-16 discover 结果）----
    {"source": "28Hse", "type": "sale", "url": "https://www.28hse.com/buy/apartment", "enabled": False},
    {"source": "28Hse", "type": "rent", "url": "https://www.28hse.com/rent/apartment", "enabled": False},
    {"source": "Midland", "type": "sale", "url": "https://www.midland.com.hk/zh-hk/list/buy", "enabled": False},
    {"source": "Midland", "type": "rent", "url": "https://www.midland.com.hk/zh-hk/list/rent", "enabled": False},
    {"source": "Ricacorp", "type": "sale", "enabled": False,
     "url": "https://www.ricacorp.com/zh-hk/property/list/buy/%E7%81%A3%E4%BB%94-%E9%8A%85%E9%91%BC%E7%81%A3-district-%E6%B8%AF%E5%B3%B6-scope-hk"},
]
LISTING_MAX_PAGES = 6
LISTING_STALE_DAYS = 14
LISTING_MAX = 5000
MORTGAGE_RATE = 0.0268     # 与前端测算仪默认方案一致
LTV = 0.70

# ============================================================ 2. 笋盘筛选权重（总和 1.0）
DEAL_WEIGHTS = {"discount": 0.35, "yield": 0.25, "age": 0.10, "cut": 0.10, "stamp": 0.10, "fresh": 0.05, "rent_proof": 0.05}
DEAL_TIERS = [(75, "S"), (65, "A"), (55, "B")]
DEAL_TOP_N = 30

# ============================================================ 3. 新闻（只存标题/来源/日期/链接/短摘要，不转载全文）
NEWS_DAYS = 30
NEWS_MAX = 80
_GN = "https://news.google.com/rss/search?hl=zh-HK&gl=HK&ceid=HK:zh-Hant&q="
NEWS_FEEDS = [
    {"name": "Google News · 香港樓市", "url": _GN + "%E9%A6%99%E6%B8%AF+%E6%A8%93%E5%B8%82+when:30d"},
    {"name": "Google News · 香港樓價", "url": _GN + "%E9%A6%99%E6%B8%AF+%E6%A8%93%E5%83%B9+when:30d"},
    {"name": "Google News · 按揭/HIBOR", "url": _GN + "%E6%8C%89%E6%8F%AD+HIBOR+when:30d"},
    {"name": "Google News · 新盤", "url": _GN + "%E9%A6%99%E6%B8%AF+%E6%96%B0%E7%9B%A4+%E9%96%8B%E5%83%B9+when:30d"},
    {"name": "Google News · 租金", "url": _GN + "%E9%A6%99%E6%B8%AF+%E7%A7%9F%E9%87%91+%E4%BD%8F%E5%AE%85+when:30d"},
    {"name": "Google News · 金管局/聯儲局", "url": _GN + "%E9%87%91%E7%AE%A1%E5%B1%80+OR+%E8%81%AF%E5%84%B2%E5%B1%80+%E5%88%A9%E7%8E%87+when:30d"},
    {"name": "Google News · 差估署/施政報告", "url": _GN + "%E5%B7%AE%E4%BC%B0%E7%BD%B2+OR+%E6%96%BD%E6%94%BF%E5%A0%B1%E5%91%8A+%E6%88%BF%E5%B1%8B+when:30d"},
    {"name": "Google News · 內地樓市/北水", "url": _GN + "%E5%85%A7%E5%9C%B0+%E8%B2%B7%E5%AE%B6+%E9%A6%99%E6%B8%AF+%E7%89%A9%E6%A5%AD+when:30d"},
    {"name": "政府新闻公报", "url": "https://www.info.gov.hk/gia/rss/general_zh.xml", "keyword_filter": True},
    {"name": "RTHK 财经", "url": "https://rthk.hk/rthk/news/rss/c_expressnews_cfinance.xml", "keyword_filter": True},
]
NEWS_KEYWORDS = ["樓", "楼", "住宅", "物業", "物业", "按揭", "利率", "息", "HIBOR", "地皮", "賣地", "卖地",
                 "租金", "房屋", "差估", "印花稅", "印花税", "新盤", "新盘", "地產", "地产", "金管局"]

# ============================================================ 4. 宏观指标
HKMA_LIQUIDITY_API = ("https://api.hkma.gov.hk/public/market-data-and-statistics/daily-monetary-statistics/"
                      "daily-figures-interbank-liquidity?pagesize=100")
RVD_PRICE_INDEX_CSV = [
    "https://www.rvd.gov.hk/datagovhk/1.4M.csv",
    "http://www.rvd.gov.hk/datagovhk/1.4M.csv",
    "https://www.rvd.gov.hk/datagovhk/1.4M%20(from_99).csv",
]
RVD_RENT_INDEX_CSV = [
    "https://www.rvd.gov.hk/datagovhk/1.3M.csv",
    "http://www.rvd.gov.hk/datagovhk/1.3M.csv",
]

# ============================================================ 5. 社区舆情（只抓公开、无需登录的页面；不存用户名）
SOCIAL_SOURCES = [
    {"name": "宅谷 property.hk · 地产论坛", "kind": "generic_table", "platform": "PropertyHK",
     "url": "https://www.property.hk/forum.php", "pages": 3, "page_param": "p"},
    {"name": "亲子王国 · 楼市动向", "kind": "discuz", "platform": "BabyKingdom",
     "url": "https://www.baby-kingdom.com/forum.php?mod=forumdisplay&fid=164", "pages": 2},
    {"name": "Reddit r/HongKong", "kind": "reddit", "platform": "Reddit", "enabled": False,   # robots.txt 不允许
     "url": "https://www.reddit.com/r/HongKong/search.json?q=property+OR+flat+OR+rent+OR+mortgage&restrict_sr=1&sort=new&t=month&limit=100"},
    {"name": "连登 LIHKG · 房屋台", "kind": "playwright_titles", "platform": "LIHKG", "enabled": False,
     "url": "https://lihkg.com/category/33"},
    {"name": "香港讨论区 · 地产版", "kind": "discuz", "platform": "Discuss", "enabled": False,
     "url": "https://www.discuss.com.hk/forumdisplay.php?fid=FILL_ME"},
]
# 只有标题含以下任一词的论坛帖才计入楼市舆情（宅谷论坛有大量时事/生活帖）
SOCIAL_RELEVANCE = ["樓", "楼", "屋", "業主", "业主", "租", "按揭", "供樓", "供楼", "首期", "上車", "上车", "地產", "地产",
                    "物業", "物业", "新盤", "新盘", "一手", "二手", "呎", "HIBOR", "息", "地皮", "賣地", "印花",
                    "劏房", "房屋", "居屋", "公屋", "負資產", "负资产", "銀主", "银主", "撻訂", "樓價", "楼价",
                    "property", "flat", "rent", "mortgage", "home", "housing", "landlord"]
SOCIAL_SKIP_URL = ["pwdreset", "login", "register", "logging.php", "member.php"]
TELEGRAM_PUBLIC_CHANNELS: list[str] = []
YOUTUBE_QUERIES = ["香港樓市", "香港樓價 走勢", "香港 買樓 上車"]
YOUTUBE_MAX_VIDEOS = 12
YOUTUBE_COMMENTS_PER_VIDEO = 40
SOCIAL_DAYS = 30

# ============================================================ 6. 预测模型
FORECAST_HORIZONS = [
    {"key": "3m", "label": "未来 3 个月", "months": 3, "scale": 4.0, "band": 1.6,
     "weights": {"momentum": 0.30, "rates": 0.20, "news": 0.20, "social": 0.10, "carry": 0.10, "supply": 0.10}},
    {"key": "6m", "label": "未来半年", "months": 6, "scale": 7.0, "band": 3.0,
     "weights": {"momentum": 0.20, "rates": 0.25, "news": 0.15, "social": 0.10, "carry": 0.15, "supply": 0.15}},
    {"key": "1y", "label": "未来 1 年", "months": 12, "scale": 10.0, "band": 5.0,
     "weights": {"momentum": 0.10, "rates": 0.30, "news": 0.10, "social": 0.05, "carry": 0.25, "supply": 0.20}},
]
MORTGAGE_CAP_RATE = 3.25
HIBOR_SPREAD = 1.3

# ============================================================ 7. 可选：大模型生成「大众感知」摘要
LLM_DEFAULT_MODEL = "gpt-4o-mini"
