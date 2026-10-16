#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HK PropPulse v6.0 - scraper.py
Playwright (真实 Chromium) 多页抓取 + 被动拦截页面自身发出的 JSON 接口响应 + DOM 文本兜底解析。

运行模式：
  python scraper.py                 # 正常抓取 → 合并 → 写 market_data.json
  python scraper.py --discover      # 只打开每个目标第 1 页，把所有 JSON 响应 URL 与样本存入 debug/，用于确认接口
  python scraper.py --offline       # 不联网，只把 seed_listings.json 合并、重算指标（本地预览/调试用）
  python scraper.py --max-pages 3   # 覆盖每个目标的最大翻页数

设计原则：
  * 先检查 robots.txt；被禁止的路径直接跳过。
  * 每页之间随机等待 3–6 秒，单平台串行，不做任何反爬绕过（不伪造指纹、不破解验证码）。
  * 拦截的是浏览器正常加载页面时发出的 XHR/fetch 响应（被动监听），不直接构造请求轰炸接口。
  * 抓取失败的平台保留上一次数据，不会把看板清空。
请自行确认各平台使用条款，仅作个人研究、低频抓取。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import random
import re
import statistics
import sys
import urllib.parse
import urllib.robotparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "market_data.json"
SEED_FILE = ROOT / "seed_listings.json"
DEBUG_DIR = ROOT / "debug"
HKT = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.datetime.now(HKT).strftime("%Y-%m-%d")

STALE_DAYS = 14          # 超过 N 天未再出现的盘 → 视为已下架
MAX_LISTINGS = 5000      # JSON 体积保护
MORTGAGE_RATE = 0.0268   # 与前端测算仪默认方案一致
LTV = 0.70

# ---------------------------------------------------------------------------
# 1) 抓取目标配置
#    url: 列表页；page_param: 若平台支持 URL 翻页则填（如 "page"），否则留 None 走「点击下一页 / 滚动」。
#    sub_area / district / region: 写入盘源的地理标签（列表页本身就是按区筛选的）。
#    首次部署请先跑 --discover，确认 json_hint 是否命中真实接口，再按需调整。
# ---------------------------------------------------------------------------
TARGETS = [
    # ---- 焦点四区 ----
    {"source": "Centaline", "type": "sale", "sub_area": "黄埔", "district": "九龙城区", "region": "九龙",
     "url": "https://hk.centanet.com/findproperty/list/buy/%E9%BB%83%E5%9F%94%E8%8A%B1%E5%9C%92_3-MZDIIHHAHN"},
    {"source": "Centaline", "type": "sale", "sub_area": "红磡", "district": "九龙城区", "region": "九龙",
     "url": "https://hk.centanet.com/findproperty/list/buy/%E7%B4%85%E7%A3%A1_Mtr-5"},
    {"source": "Centaline", "type": "sale", "sub_area": "湾仔", "district": "湾仔区", "region": "港岛",
     "url": "https://hk.centanet.com/findproperty/list/buy/%E7%81%A3%E4%BB%94_Mtr-56"},
    {"source": "Centaline", "type": "sale", "sub_area": "铜锣湾", "district": "湾仔区", "region": "港岛",
     "url": "https://hk.centanet.com/findproperty/list/buy/%E9%8A%85%E9%91%BC%E7%81%A3_19-HMA144"},
    {"source": "Centaline", "type": "rent", "sub_area": "湾仔", "district": "湾仔区", "region": "港岛",
     "url": "https://hk.centanet.com/findproperty/list/rent/%E7%81%A3%E4%BB%94_19-HMA160"},
    {"source": "28Hse", "type": "sale", "sub_area": "黄埔", "district": "九龙城区", "region": "九龙",
     "url": "https://www.28hse.com/buy/a2/dg115/c3060", "page_param": "page"},
    {"source": "28Hse", "type": "sale", "sub_area": "红磡", "district": "九龙城区", "region": "九龙",
     "url": "https://www.28hse.com/buy/a2/dg31/c75", "page_param": "page"},
    {"source": "Ricacorp", "type": "sale", "sub_area": "湾仔/铜锣湾", "district": "湾仔区", "region": "港岛",
     "url": "https://www.ricacorp.com/zh-hk/property/list/buy/%E7%81%A3%E4%BB%94-%E9%8A%85%E9%91%BC%E7%81%A3-district-%E6%B8%AF%E5%B3%B6-scope-hk"},
    # ---- 全港 18 区（按平台总列表翻页；地区由卡片文字自动识别）----
    {"source": "Centaline", "type": "sale", "url": "https://hk.centanet.com/findproperty/list/buy"},
    {"source": "Centaline", "type": "rent", "url": "https://hk.centanet.com/findproperty/list/rent"},
    {"source": "28Hse", "type": "sale", "url": "https://www.28hse.com/buy/apartment", "page_param": "page"},
    {"source": "28Hse", "type": "sale", "url": "https://www.28hse.com/buy/apartment/housetype-tong_lau", "page_param": "page"},
    {"source": "28Hse", "type": "rent", "url": "https://www.28hse.com/rent/apartment", "page_param": "page"},
    {"source": "Midland", "type": "sale", "url": "https://www.midland.com.hk/zh-hk/list/buy"},
    {"source": "Midland", "type": "rent", "url": "https://www.midland.com.hk/zh-hk/list/rent"},
]
DEFAULT_MAX_PAGES = 6
JSON_HINTS = ("api", "search", "list", "property", "stock", "post", "graphql")

SOURCE_NAMES = {
    "Centaline": "中原地产", "28Hse": "28Hse 屋网", "Midland": "美联物业", "House730": "House730",
    "Ricacorp": "利嘉阁", "Spacious": "千居 Spacious", "PropertyHK": "宅谷 property.hk",
    "HomeDash": "HomeDash 家速", "BuyHouse": "buyhouse.hk 聚合", "Agency": "地区代理",
}

# 片区关键词 → (片区, 行政区, 大区)。卡片文字命中即打标签（繁简皆可）。
AREA_MAP = [
    (("黃埔", "黄埔"), "黄埔", "九龙城区", "九龙"), (("紅磡", "红磡"), "红磡", "九龙城区", "九龙"),
    (("灣仔", "湾仔", "金鐘", "金钟"), "湾仔", "湾仔区", "港岛"), (("銅鑼灣", "铜锣湾", "天后", "大坑"), "铜锣湾", "湾仔区", "港岛"),
    (("跑馬地", "跑马地"), "跑马地", "湾仔区", "港岛"),
    (("中環", "中环", "上環", "上环", "西營盤", "西营盘", "堅尼地城", "坚尼地城", "半山"), "中西区", "中西区", "港岛"),
    (("北角", "鰂魚涌", "鲗鱼涌", "太古", "西灣河", "西湾河", "筲箕灣", "筲箕湾", "柴灣", "柴湾", "杏花邨"), "东区", "东区", "港岛"),
    (("香港仔", "鴨脷洲", "鸭脷洲", "黃竹坑", "黄竹坑", "薄扶林", "赤柱", "淺水灣", "浅水湾"), "南区", "南区", "港岛"),
    (("旺角", "油麻地", "佐敦", "尖沙咀", "太子", "大角咀", "奧運", "奥运", "九龍站", "九龙站"), "油尖旺", "油尖旺区", "九龙"),
    (("深水埗", "長沙灣", "长沙湾", "荔枝角", "美孚", "石硤尾", "石硖尾", "南昌"), "深水埗", "深水埗区", "九龙"),
    (("九龍城", "九龙城", "土瓜灣", "土瓜湾", "何文田", "啟德", "启德", "九龍塘", "九龙塘"), "九龙城", "九龙城区", "九龙"),
    (("黃大仙", "黄大仙", "新蒲崗", "新蒲岗", "鑽石山", "钻石山", "樂富", "乐富", "牛池灣", "牛池湾"), "黄大仙", "黄大仙区", "九龙"),
    (("觀塘", "观塘", "九龍灣", "九龙湾", "牛頭角", "牛头角", "藍田", "蓝田", "油塘"), "观塘", "观塘区", "九龙"),
    (("沙田", "大圍", "大围", "火炭", "馬鞍山", "马鞍山"), "沙田", "沙田区", "新界东"),
    (("將軍澳", "将军澳", "康城", "坑口", "寶琳", "宝琳", "調景嶺", "调景岭", "西貢", "西贡"), "西贡及将军澳", "西贡及将军澳", "新界东"),
    (("大埔", "太和", "白石角"), "大埔", "大埔区", "新界东"), (("粉嶺", "粉岭", "上水"), "北区", "北区", "新界东"),
    (("荃灣", "荃湾", "大窩口", "大窝口", "深井"), "荃湾", "荃湾区", "新界西"),
    (("葵涌", "葵芳", "青衣", "荔景"), "葵青", "葵青区", "新界西"),
    (("屯門", "屯门"), "屯门", "屯门区", "新界西"), (("元朗", "天水圍", "天水围", "洪水橋", "洪水桥", "錦田", "锦田"), "元朗", "元朗区", "新界西"),
    (("東涌", "东涌", "愉景灣", "愉景湾", "馬灣", "马湾", "長洲", "长洲"), "离岛", "离岛区", "新界西"),
]


# ---------------------------------------------------------------------------
# 2) 工具函数：香港从价印花税（第 2 标准税率）、数字解析、地区识别
# ---------------------------------------------------------------------------
def stamp_duty_hkd(price_hkd: float) -> float:
    """香港住宅从价印花税第 2 标准（≤400 万为 $100，含边际缓冲区）。"""
    p = price_hkd
    bands = [
        (4_000_000, lambda p: 100),
        (4_323_780, lambda p: 100 + (p - 4_000_000) * 0.20),
        (4_500_000, lambda p: p * 0.015),
        (4_935_480, lambda p: 67_500 + (p - 4_500_000) * 0.10),
        (6_000_000, lambda p: p * 0.0225),
        (6_642_860, lambda p: 135_000 + (p - 6_000_000) * 0.10),
        (9_000_000, lambda p: p * 0.03),
        (10_080_000, lambda p: 270_000 + (p - 9_000_000) * 0.10),
        (20_000_000, lambda p: p * 0.0375),
        (21_739_120, lambda p: 750_000 + (p - 20_000_000) * 0.10),
    ]
    for upper, fn in bands:
        if p <= upper:
            return round(fn(p))
    return round(p * 0.0425)


def num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"[\d,]+(?:\.\d+)?", str(s))
    return float(m.group(0).replace(",", "")) if m else None


def detect_area(text: str):
    for keys, sub, district, region in AREA_MAP:
        if any(k in text for k in keys):
            return sub, district, region
    return None, None, None


def listing_key(d: dict) -> str:
    raw = "|".join(str(d.get(k, "")) for k in ("source", "type", "estate", "building", "floor", "unit", "area"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 3) 解析：(a) JSON 接口启发式映射  (b) DOM 卡片文字正则兜底
# ---------------------------------------------------------------------------
ALIASES = {
    "estate": ("estateName", "estate_name", "buildingName", "bigEstateName", "displayName", "title", "name", "estate"),
    "building": ("phaseName", "blockName", "block", "tower", "building"),
    "floor": ("floor", "floorName", "floorLevel", "floorDesc"),
    "unit": ("flat", "unit", "flatName", "room"),
    "area": ("saleableArea", "netArea", "nArea", "netSize", "saleable_area", "areaSaleable", "sArea", "area"),
    "price": ("salePrice", "sellPrice", "price", "askingPrice", "listPrice", "sell"),
    "rent": ("rentPrice", "rent", "leasePrice", "askingRent"),
    "rooms": ("bedroom", "bedrooms", "roomCount", "room_num", "rooms"),
    "url": ("url", "link", "detailUrl", "shareUrl", "href"),
    "district": ("districtName", "district", "region", "areaName", "hmaName"),
    "age": ("buildingAge", "age"),
}


def _pick(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, "", 0):
            v = d[k]
            if isinstance(v, dict):  # e.g. {"zh-hk": "..."}
                v = next((x for x in v.values() if x), None)
            return v
    return None


def _walk_lists(obj, depth=0):
    if depth > 6:
        return
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj[:5]):
        yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_lists(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:50]:
            yield from _walk_lists(v, depth + 1)


def parse_json_payload(payload, target: dict) -> list[dict]:
    out = []
    for arr in _walk_lists(payload):
        keys = set().union(*(x.keys() for x in arr[:5]))
        if not (keys & set(ALIASES["area"])) or not (keys & set(ALIASES["price"] + ALIASES["rent"])):
            continue
        for it in arr:
            area = num(_pick(it, ALIASES["area"]))
            price = num(_pick(it, ALIASES["price"]))
            rent = num(_pick(it, ALIASES["rent"]))
            if not area or area < 80 or area > 6000:
                continue
            if price and price > 10_000:      # 接口常以 HKD 返回 → 转万
                price = price / 10_000
            rec = {
                "type": target["type"], "estate": str(_pick(it, ALIASES["estate"]) or "").strip(),
                "building": str(_pick(it, ALIASES["building"]) or ""), "floor": str(_pick(it, ALIASES["floor"]) or ""),
                "unit": str(_pick(it, ALIASES["unit"]) or ""), "rooms": str(_pick(it, ALIASES["rooms"]) or ""),
                "area": round(area), "priceWan": round(price, 1) if price and target["type"] == "sale" else None,
                "rentAsk": round(rent) if rent and target["type"] == "rent" else None,
                "ageYears": num(_pick(it, ALIASES["age"])), "url": _pick(it, ALIASES["url"]) or target["url"],
                "areaText": str(_pick(it, ALIASES["district"]) or ""),
            }
            if rec["estate"] and (rec["priceWan"] or rec["rentAsk"]):
                out.append(rec)
    return out


RE_AREA = re.compile(r"實用(?:面積)?\s*[:：]?\s*([\d,]{2,5})\s*[呎尺]")
RE_PRICE_CUT = re.compile(r"\$\s*([\d,.]+)\s*萬\s*售\s*\$\s*([\d,.]+)\s*萬")
RE_PRICE = re.compile(r"售\s*\$?\s*([\d,.]+)\s*萬")
RE_RENT = re.compile(r"租\s*\$?\s*([\d,]{4,7})\s*(?:元)?")
RE_ROOMS = re.compile(r"(\d)\s*房(?:\s*\(\d套房\))?|開放式|开放式")
RE_FLOOR = re.compile(r"(極高層|高層|中層|低層|极高层|高层|中层|低层)")
RE_AGE = re.compile(r"(\d{1,3})\s*年樓齡")
RE_UNIT = re.compile(r"\b([A-Z]{1,2}\d{0,2}|\d{1,2})室")

JS_CARDS = r"""
() => {
  const hasArea = t => /實用(面積)?\s*[:：]?\s*[\d,]{2,5}\s*[呎尺]/.test(t);
  const hasMoney = t => /(售\s*\$?\s*[\d,.]+\s*萬)|(租\s*\$?\s*[\d,]{4,7})/.test(t);
  const nodes = Array.from(document.querySelectorAll('a, li, article, div'));
  const hits = nodes.filter(n => { const t = n.innerText || ''; return t.length < 700 && hasArea(t) && hasMoney(t); });
  const minimal = hits.filter(n => !hits.some(o => o !== n && n.contains(o)));
  return minimal.slice(0, 120).map(n => {
    const a = n.tagName === 'A' ? n : n.querySelector('a[href]');
    return { text: n.innerText, href: a ? a.href : '' };
  });
}
"""


def parse_card_text(text: str, href: str, target: dict) -> dict | None:
    t = " ".join(text.split())
    m_area = RE_AREA.search(t)
    if not m_area:
        return None
    area = num(m_area.group(1))
    orig = price = rent = None
    m_cut = RE_PRICE_CUT.search(t)
    if m_cut:
        orig, price = num(m_cut.group(1)), num(m_cut.group(2))
    else:
        m_p = RE_PRICE.search(t)
        price = num(m_p.group(1)) if m_p else None
    m_r = RE_RENT.search(t)
    rent = num(m_r.group(1)) if m_r else None
    if target["type"] == "sale" and not price:
        return None
    if target["type"] == "rent" and not rent:
        return None
    head = t
    for noise in ("中原地產", "美聯物業", "利嘉閣地產", "裝修及講房", "獨家", "鎖匙盤", "到價提示", "黃金", "AI講房", "AI裝修", "VR"):
        head = head.replace(noise, " ")
    head = " ".join(head.split())
    estate = re.split(r"\s(?=\d+期|\S{1,3}座|極高層|高層|中層|低層|\d房|開放式|實用|\d+年樓齡)|[|｜]", head)[0].strip()[:40]
    m_rooms, m_floor, m_age, m_unit = RE_ROOMS.search(t), RE_FLOOR.search(t), RE_AGE.search(t), RE_UNIT.search(t)
    tags = [k for k in ("獨家", "鎖匙盤", "筍盤", "連租約", "露台", "平台", "天台", "減價", "業主盤", "銀主", "唐樓") if k in t]
    return {
        "type": target["type"], "estate": estate or "未命名", "building": "", "unit": m_unit.group(0) if m_unit else "",
        "floor": m_floor.group(1) if m_floor else "", "rooms": m_rooms.group(0) if m_rooms else "",
        "area": round(area), "priceWan": price if target["type"] == "sale" else None,
        "originalPriceWan": orig if orig and price and orig > price else None,
        "rentAsk": round(rent) if target["type"] == "rent" and rent else (round(rent) if rent and target["type"] == "sale" else None),
        "ageYears": num(m_age.group(1)) if m_age else None, "url": href or target["url"],
        "areaText": t[:200], "tags": tags,
    }


# ---------------------------------------------------------------------------
# 4) Playwright 抓取
# ---------------------------------------------------------------------------
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allows(url: str, ua: str) -> bool:
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        try:
            rp.read()
            _robots_cache[base] = rp
        except Exception:
            _robots_cache[base] = None   # 读取失败 → 视为未声明限制
    rp = _robots_cache[base]
    return True if rp is None else rp.can_fetch(ua, url)


def page_url(target: dict, n: int) -> str:
    if n == 1 or not target.get("page_param"):
        return target["url"]
    parts = urllib.parse.urlsplit(target["url"])
    q = dict(urllib.parse.parse_qsl(parts.query))
    q[target["page_param"]] = str(n)
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


async def goto_next(page) -> bool:
    """无 URL 翻页参数时：尝试点击「下一頁」；否则滚动加载。返回是否可能有新内容。"""
    for sel in ("a[rel=next]", "button[aria-label*=Next]", "li.next a", ".btn-next",
                "text=下一頁", "text=下一页", "text=Next"):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_enabled():
                await loc.click(timeout=5000)
                await page.wait_for_load_state("networkidle", timeout=20000)
                return True
        except Exception:
            continue
    before = await page.evaluate("document.body.scrollHeight")
    await page.mouse.wheel(0, 6000)
    await page.wait_for_timeout(2500)
    return (await page.evaluate("document.body.scrollHeight")) > before


async def scrape_target(ctx, target: dict, max_pages: int, discover: bool) -> list[dict]:
    ua = await ctx.new_page()
    user_agent = await ua.evaluate("navigator.userAgent")
    await ua.close()
    if not robots_allows(target["url"], user_agent):
        print(f"[SKIP] robots.txt 不允许：{target['url']}")
        return []

    page = await ctx.new_page()
    captured: list[tuple[str, object]] = []

    async def on_response(resp):
        try:
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype and any(h in resp.url.lower() for h in JSON_HINTS):
                captured.append((resp.url, await resp.json()))
        except Exception:
            pass

    page.on("response", on_response)
    results: list[dict] = []
    seen_keys: set[str] = set()
    for n in range(1, max_pages + 1):
        try:
            if n == 1 or target.get("page_param"):
                await page.goto(page_url(target, n), wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_load_state("networkidle", timeout=20000)
            elif not await goto_next(page):
                break
        except Exception as exc:
            print(f"[WARN] {target['source']} 第{n}页加载失败：{exc}")
            break

        batch = []
        for url, payload in captured:
            batch += parse_json_payload(payload, target)
        if discover:
            DEBUG_DIR.mkdir(exist_ok=True)
            slug = re.sub(r"\W+", "_", target["url"])[-60:]
            (DEBUG_DIR / f"{slug}_json_urls.txt").write_text("\n".join(u for u, _ in captured), encoding="utf-8")
            for i, (u, p) in enumerate(captured[:15]):
                (DEBUG_DIR / f"{slug}_sample{i}.json").write_text(json.dumps(p, ensure_ascii=False)[:200000], encoding="utf-8")
        captured.clear()
        if not batch:  # 兜底：DOM 卡片
            for c in await page.evaluate(JS_CARDS):
                rec = parse_card_text(c["text"], c["href"], target)
                if rec:
                    batch.append(rec)
        new = 0
        for rec in batch:
            rec["source"] = target["source"]
            k = listing_key(rec)
            if k not in seen_keys:
                seen_keys.add(k)
                results.append(rec)
                new += 1
        print(f"[OK] {target['source']} {target.get('sub_area', '全港')} {target['type']} p{n}: +{new}")
        if discover or new == 0:
            break
        await page.wait_for_timeout(random.randint(3000, 6000))
    await page.close()

    for rec in results:  # 地理标签：目标页固定 > 卡片文字识别
        if target.get("sub_area"):
            rec["subArea"], rec["district"], rec["region"] = target["sub_area"], target["district"], target["region"]
        else:
            rec["subArea"], rec["district"], rec["region"] = detect_area(rec.get("areaText", "") + rec.get("estate", ""))
        rec.pop("areaText", None)
    return results


async def scrape_all(max_pages: int, discover: bool) -> tuple[list[dict], set[str]]:
    from playwright.async_api import async_playwright

    all_recs, ok_sources = [], set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="zh-HK", timezone_id="Asia/Hong_Kong",
                                        viewport={"width": 1366, "height": 900})
        for t in TARGETS:
            try:
                recs = await scrape_target(ctx, t, max_pages, discover)
                all_recs += recs
                if recs:
                    ok_sources.add(t["source"])
            except Exception as exc:
                print(f"[WARN] {t['source']} 抓取异常，保留旧数据：{exc}")
        await browser.close()
    return all_recs, ok_sources


# ---------------------------------------------------------------------------
# 5) 合并（去重 / 价格历史 / 减价识别 / 下架清理）+ 指标计算
# ---------------------------------------------------------------------------
def merge(old: list[dict], fresh: list[dict], ok_sources: set[str]) -> list[dict]:
    by_id = {d["id"]: d for d in old}
    for rec in fresh:
        rec.setdefault("id", f"{rec['source'][:4].upper()}-{listing_key(rec)}")
        prev = by_id.get(rec["id"])
        if prev:
            hist = prev.get("priceHistory", [])
            if rec.get("priceWan") and prev.get("priceWan") and rec["priceWan"] != prev["priceWan"]:
                hist.append({"date": TODAY, "from": prev["priceWan"], "to": rec["priceWan"]})
                if rec["priceWan"] < prev["priceWan"]:
                    rec["originalPriceWan"] = max(prev.get("originalPriceWan") or 0, prev["priceWan"])
            rec["priceHistory"] = hist[-10:]
            rec["firstSeen"] = prev.get("firstSeen", TODAY)
            rec["originalPriceWan"] = rec.get("originalPriceWan") or prev.get("originalPriceWan")
        else:
            rec["firstSeen"] = TODAY
        rec["lastSeen"] = TODAY
        by_id[rec["id"]] = {**(prev or {}), **{k: v for k, v in rec.items() if v not in (None, "")}}

    cutoff = (dt.datetime.now(HKT) - dt.timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
    kept = []
    for d in by_id.values():
        # 只有该平台今天抓取成功时，才按「久未出现」清理；种子盘不清理
        if d.get("origin") != "seed_web_verified" and d.get("source") in ok_sources and d.get("lastSeen", TODAY) < cutoff:
            continue
        kept.append(d)
    return kept[:MAX_LISTINGS]


def enrich(listings: list[dict]) -> list[dict]:
    def med(xs):
        return round(statistics.median(xs), 1) if xs else None

    def band(d):  # 楼龄分档：旧楼与新楼尺租差异大，避免用新盘尺租高估旧楼回报
        a = d.get("ageYears")
        return None if a is None else ("old" if a >= 40 else "new")

    rent_psf = {"estate": {}, "sub": {}, "district": {}}
    sale_psf = {"estate": {}, "sub": {}}
    for d in listings:
        if d.get("area") and d.get("rentAsk"):
            v = d["rentAsk"] / d["area"]
            rent_psf["estate"].setdefault(d.get("estate"), []).append(v)
            for b in (None, band(d)):
                rent_psf["sub"].setdefault((d.get("subArea"), b), []).append(v)
                rent_psf["district"].setdefault((d.get("district"), b), []).append(v)
        if d.get("type") == "sale" and d.get("area") and d.get("priceWan"):
            v = d["priceWan"] * 10_000 / d["area"]
            sale_psf["estate"].setdefault(d.get("estate"), []).append(v)
            sale_psf["sub"].setdefault(d.get("subArea"), []).append(v)

    r = 1 + MORTGAGE_RATE / 12
    for d in listings:
        d["sourceName"] = d.get("sourceName") or SOURCE_NAMES.get(d.get("source"), d.get("source"))
        tags = set(d.get("tags") or [])
        if d.get("type") == "rent":
            d["rentPsf"] = round(d["rentAsk"] / d["area"], 1) if d.get("rentAsk") and d.get("area") else None
            d["tags"] = sorted(tags | {"租盘"})
            continue
        price_hkd = d["priceWan"] * 10_000
        d["psf"] = round(price_hkd / d["area"])
        d["stampDutyHKD"] = stamp_duty_hkd(price_hkd)
        if d.get("rentAsk"):
            rp, basis = d["rentAsk"] / d["area"], "实盘租金（同盘/连租约）"
        else:
            rp, basis = None, ""
            xs = rent_psf["estate"].get(d.get("estate"))
            if xs:
                rp, basis = statistics.median(xs), f"估算：同屋苑租盘中位数 @${statistics.median(xs):.1f}/呎（{len(xs)}个样本）"
            else:
                b = band(d)
                bl = {"old": "≥40年旧楼", "new": "<40年楼"}
                for lvl, key, label in (("sub", d.get("subArea"), "同片区"), ("district", d.get("district"), "同行政区")):
                    if b and len(rent_psf[lvl].get((key, b), [])) >= 2:
                        xs = rent_psf[lvl][(key, b)]
                        rp, basis = statistics.median(xs), f"估算：{label}{bl[b]}租盘中位数 @${statistics.median(xs):.1f}/呎（{len(xs)}个）"
                        break
                    if not b:  # 楼龄未知 → 取新/旧两档中较低者（保守）
                        cands = [(statistics.median(v), len(v), bb) for bb in ("old", "new")
                                 if len(v := rent_psf[lvl].get((key, bb), [])) >= 2]
                        if cands:
                            m, n, bb = min(cands)
                            rp, basis = m, f"估算(楼龄未知取保守档)：{label}{bl[bb]}租盘中位数 @${m:.1f}/呎（{n}个）"
                            break
                if not rp and b != "old":  # 分档样本不足 → 不分楼龄（旧楼不套用新楼尺租，宁缺勿滥）
                    for lvl, key, label in (("sub", d.get("subArea"), "同片区"), ("district", d.get("district"), "同行政区")):
                        xs = rent_psf[lvl].get((key, None))
                        if xs:
                            rp, basis = statistics.median(xs), f"估算(样本少)：{label}租盘中位数 @${statistics.median(xs):.1f}/呎（{len(xs)}个）"
                            break
        if rp:
            d["rentPsf"] = round(rp, 1)
            d["estRent"] = int(round(rp * d["area"] / 100) * 100) if not d.get("rentAsk") else d["rentAsk"]
            d["grossYield"] = round(d["estRent"] * 12 / price_hkd * 100, 2)
            loan = price_hkd * LTV
            pmt = loan * (MORTGAGE_RATE / 12) * r ** 360 / (r ** 360 - 1)
            d["monthlyCarry"] = int(round(d["estRent"] - pmt))
        d["rentBasis"] = basis or "暂无租金样本"
        ref = sale_psf["estate"].get(d.get("estate"))
        ref_label = "同屋苑"
        if not ref or len(ref) < 3:
            ref, ref_label = sale_psf["sub"].get(d.get("subArea")), "同片区"
        if ref and len(ref) >= 3:
            m = statistics.median(ref)
            d["vsMedianPct"] = round((d["psf"] / m - 1) * 100, 1)
            d["vsMedianBasis"] = f"{ref_label}放盘中位数 @${m:,.0f}/呎（{len(ref)}个）"
        if price_hkd <= 4_000_000:
            tags.add("≤400万·$100印花税")
        if d.get("originalPriceWan") and d["originalPriceWan"] > d["priceWan"]:
            tags.add("减价盘")
            d["cutPct"] = round((1 - d["priceWan"] / d["originalPriceWan"]) * 100, 1)
        if (d.get("ageYears") or 0) >= 40:
            tags.add("楼龄≥40年")
        d["tags"] = sorted(tags)
    return listings


def build_meta(listings: list[dict]) -> dict:
    by = lambda k: dict(sorted(((v, sum(1 for d in listings if d.get(k) == v)) for v in {d.get(k) for d in listings}),
                               key=lambda x: -x[1]))
    return {"total": len(listings), "sale": sum(1 for d in listings if d.get("type") == "sale"),
            "rent": sum(1 for d in listings if d.get("type") == "rent"),
            "bySource": by("source"), "bySubArea": by("subArea"), "byDistrict": by("district")}


# ---------------------------------------------------------------------------
# 6) main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    args = ap.parse_args()

    snapshot = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else {}
    old = snapshot.get("listings", [])
    seeds = json.loads(SEED_FILE.read_text(encoding="utf-8")) if SEED_FILE.exists() else []
    old_ids = {d["id"] for d in old}
    old += [s for s in seeds if s["id"] not in old_ids]

    fresh, ok_sources = [], set()
    if not args.offline:
        try:
            fresh, ok_sources = asyncio.run(scrape_all(args.max_pages, args.discover))
        except Exception as exc:
            print(f"[WARN] Playwright 整体失败，保留旧数据：{exc}", file=sys.stderr)
    if args.discover:
        print(f"[DISCOVER] 完成，接口样本见 {DEBUG_DIR}/（不写入 market_data.json）")
        return

    listings = enrich(merge(old, fresh, ok_sources))
    listings.sort(key=lambda d: (d.get("lastSeen", ""), d.get("firstSeen", "")), reverse=True)
    snapshot["listings"] = listings
    snapshot["listings_meta"] = {**build_meta(listings), "lastRun": dt.datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT"),
                                 "okSources": sorted(ok_sources), "freshCount": len(fresh)}
    snapshot["updated_at"] = dt.datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")
    snapshot["version"] = "v6.0 Playwright Bulk Listings Edition"
    DATA_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[DONE] listings={len(listings)} (本次新抓 {len(fresh)}) → {DATA_FILE.name}")


if __name__ == "__main__":
    main()
