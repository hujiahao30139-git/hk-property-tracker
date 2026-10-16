# -*- coding: utf-8 -*-
"""
盘源抓取（Playwright 真实 Chromium）+ 合并 + 指标计算。
提取顺序：页面内嵌 SSR 状态 → 页面自身发出的 XHR/fetch JSON → DOM 卡片文字兜底。
不做任何反爬绕过；遇验证码/拦截页即跳过该目标。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import statistics
import urllib.parse

from . import config as C
from .common import (DEBUG_DIR, HKT, SEED_FILE, days_ago, load_json, log, num, robots_allows,
                     stamp_duty_hkd, today)

JSON_HINTS = ("api", "search", "list", "property", "stock", "post", "graphql")
JSON_EXCLUDE = ("newproperty", "captcha", "getmtrlines", "schoolnet", "university", "gethmaplaces",
                "banner", "bookmark", "comparisoncart", "popularagent", "hottags", "estateandhmabrief",
                "googleapis", "appier", "facebook", "doubleclick", "google-analytics")
BLOCK_MARKERS = ("verify you are human", "access denied", "請完成驗證", "请完成验证", "cf-challenge", "captcha-container")

SOURCE_NAMES = {
    "Centaline": "中原地产", "28Hse": "28Hse 屋网", "Midland": "美联物业", "House730": "House730",
    "Ricacorp": "利嘉阁", "Spacious": "千居 Spacious", "PropertyHK": "宅谷 property.hk",
    "HomeDash": "HomeDash 家速", "BuyHouse": "buyhouse.hk 聚合", "Agency": "地区代理",
}

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


def detect_area(text: str):
    for keys, sub, district, region in AREA_MAP:
        if any(k in text for k in keys):
            return sub, district, region
    return None, None, None


def listing_key(d: dict) -> str:
    raw = "|".join(str(d.get(k, "")) for k in ("source", "type", "estate", "building", "floor", "unit", "area"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def slugify(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    host = p.netloc.split(":")[0].split(".")
    site = host[-2] if len(host) >= 2 else host[0]
    return re.sub(r"\W+", "_", site + "_" + urllib.parse.unquote(p.path)).strip("_")[:80]


# ------------------------------------------------------------------ 解析
ALIASES = {
    "estate": ("estateName", "estate_name", "buildingName", "bigEstateName", "displayName", "estateNameC",
               "buildingNameC", "title", "name", "estate"),
    "building": ("phaseName", "blockName", "block", "tower", "building"),
    "floor": ("floor", "floorName", "floorLevel", "floorDesc", "floorC"),
    "unit": ("flat", "unit", "flatName", "room"),
    "area": ("saleableArea", "netArea", "nArea", "netSize", "saleable_area", "areaSaleable", "sArea", "nfa", "area"),
    "price": ("salePrice", "sellPrice", "price", "askingPrice", "listPrice", "sell", "sellingPrice"),
    "rent": ("rentPrice", "rent", "leasePrice", "askingRent", "rentalPrice"),
    "rooms": ("bedroom", "bedrooms", "roomCount", "room_num", "rooms"),
    "url": ("url", "link", "detailUrl", "shareUrl", "href"),
    "district": ("districtName", "district", "region", "areaName", "hmaName", "scopeName"),
    "age": ("buildingAge", "age", "opAge"),
}


def _pick(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, "", 0):
            v = d[k]
            if isinstance(v, dict):
                v = next((x for x in v.values() if x not in (None, "", 0) and not isinstance(x, (dict, list))), None)
            if isinstance(v, list):
                continue
            return v
    return None


def _walk_lists(obj, depth=0):
    if depth > 8:
        return
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj[:5]):
        yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_lists(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:80]:
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
            if price and price > 10_000:
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
            if rec["building"] == rec["estate"]:
                rec["building"] = ""
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
NOISE = ("中原地產", "美聯物業", "利嘉閣地產", "裝修及講房", "獨家", "鎖匙盤", "到價提示", "黃金", "AI講房", "AI裝修", "VR")

JS_CARDS = r"""
() => {
  const hasArea = t => /實用(面積)?\s*[:：]?\s*[\d,]{2,5}\s*[呎尺]/.test(t);
  const hasMoney = t => /(售\s*\$?\s*[\d,.]+\s*萬)|(租\s*\$?\s*[\d,]{4,7})/.test(t);
  const nodes = Array.from(document.querySelectorAll('a, li, article, div'));
  const hits = nodes.filter(n => { const t = n.innerText || ''; return t.length < 700 && hasArea(t) && hasMoney(t); });
  const minimal = hits.filter(n => !hits.some(o => o !== n && n.contains(o)));
  return minimal.slice(0, 150).map(n => {
    const a = n.tagName === 'A' ? n : (n.closest('a[href]') || n.querySelector('a[href]'));
    return { text: n.innerText, href: a ? a.href : '' };
  });
}
"""
JS_EMBEDDED = r"""
() => {
  const out = [];
  try { if (window.__NUXT__) out.push(['__NUXT__', JSON.parse(JSON.stringify(window.__NUXT__))]); } catch (e) {}
  try { if (window.__NEXT_DATA__) out.push(['__NEXT_DATA__', window.__NEXT_DATA__]); } catch (e) {}
  try { if (window.__INITIAL_STATE__) out.push(['__INITIAL_STATE__', JSON.parse(JSON.stringify(window.__INITIAL_STATE__))]); } catch (e) {}
  document.querySelectorAll('script[type="application/json"], script#__NEXT_DATA__, script#__NUXT_DATA__').forEach((s, i) => {
    try { out.push(['script#' + (s.id || i), JSON.parse(s.textContent)]); } catch (e) {}
  });
  return out;
}
"""


def parse_card_text(text: str, href: str, target: dict) -> dict | None:
    t = " ".join(text.split())
    m_area = RE_AREA.search(t)
    if not m_area:
        return None
    area = num(m_area.group(1))
    orig = price = None
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
    for noise in NOISE:
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
        "rentAsk": round(rent) if rent else None,
        "ageYears": num(m_age.group(1)) if m_age else None, "url": href or target["url"],
        "areaText": t[:200], "tags": tags,
    }


# ------------------------------------------------------------------ Playwright
def page_url(target: dict, n: int) -> str:
    if n == 1 or not target.get("page_param"):
        return target["url"]
    parts = urllib.parse.urlsplit(target["url"])
    q = dict(urllib.parse.parse_qsl(parts.query))
    q[target["page_param"]] = str(n)
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


async def settle(page, ms=15000):
    try:
        await page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        await page.wait_for_timeout(2500)


async def goto_next(page) -> bool:
    for sel in ("a[rel=next]", "button[aria-label*=Next]", "li.next a", ".btn-next", "button.btn-next",
                ".el-pagination .btn-next", "text=下一頁", "text=下一页", "text=Next"):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible() and await loc.is_enabled():
                await loc.scroll_into_view_if_needed(timeout=5000)
                await loc.click(timeout=5000)
                await settle(page)
                return True
        except Exception:
            continue
    before = await page.evaluate("document.body.scrollHeight")
    await page.mouse.wheel(0, 6000)
    await page.wait_for_timeout(2500)
    return (await page.evaluate("document.body.scrollHeight")) > before


async def scrape_target(ctx, target: dict, max_pages: int, discover: bool, summary: list) -> list[dict]:
    diag = {"source": target["source"], "url": target["url"], "type": target["type"], "pages": []}
    summary.append(diag)
    ua_page = await ctx.new_page()
    user_agent = await ua_page.evaluate("navigator.userAgent")
    await ua_page.close()
    if not robots_allows(target["url"], user_agent):
        diag["status"] = "skipped_robots"
        log("listings", f"SKIP robots.txt：{target['url']}")
        return []

    page = await ctx.new_page()
    captured: list = []

    async def on_response(resp):
        try:
            u = resp.url.lower()
            if "json" in resp.headers.get("content-type", "") and any(h in u for h in JSON_HINTS) \
                    and not any(x in u for x in JSON_EXCLUDE):
                captured.append((resp.url, await resp.json()))
        except Exception:
            pass

    page.on("response", on_response)
    results, seen = [], set()
    slug = slugify(target["url"]) + ("_rent" if target["type"] == "rent" else "")
    pages_to_try = min(max_pages, 2) if discover else max_pages

    for n in range(1, pages_to_try + 1):
        pdiag = {"page": n}
        diag["pages"].append(pdiag)
        try:
            if n == 1 or target.get("page_param"):
                resp = await page.goto(page_url(target, n), wait_until="domcontentloaded", timeout=45000)
                pdiag["http"] = resp.status if resp else None
                await settle(page)
            elif not await goto_next(page):
                pdiag["note"] = "no next page"
                break
        except Exception as exc:
            pdiag["error"] = str(exc)[:200]
            break

        body = (await page.evaluate("document.body ? document.body.innerText.slice(0, 5000) : ''")).lower()
        if any(m in body for m in BLOCK_MARKERS):
            pdiag["blocked"] = True
            log("listings", f"BLOCK {target['source']} p{n} 出现验证/拦截页，跳过")
            break

        embedded = await page.evaluate(JS_EMBEDDED)
        cards = await page.evaluate(JS_CARDS)
        b_xhr, b_ssr, b_dom = [], [], []
        for _, payload in captured:
            b_xhr += parse_json_payload(payload, target)
        for _, payload in embedded:
            b_ssr += parse_json_payload(payload, target)
        for c in cards:
            rec = parse_card_text(c["text"], c["href"], target)
            if rec:
                b_dom.append(rec)
        pdiag.update({"xhr_urls": [u for u, _ in captured], "xhr_parsed": len(b_xhr),
                      "embedded_keys": [k for k, _ in embedded], "ssr_parsed": len(b_ssr),
                      "dom_cards": len(cards), "dom_parsed": len(b_dom)})
        if discover:
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / f"listing_{slug}_p{n}.html").write_text(await page.content(), encoding="utf-8")
            for i, (k, p) in enumerate(embedded[:3]):
                (DEBUG_DIR / f"listing_{slug}_p{n}_embedded{i}.json").write_text(
                    json.dumps(p, ensure_ascii=False)[:400000], encoding="utf-8")
            for i, (u, p) in enumerate(captured[:10]):
                (DEBUG_DIR / f"listing_{slug}_p{n}_xhr{i}.json").write_text(
                    json.dumps({"url": u, "body": p}, ensure_ascii=False)[:200000], encoding="utf-8")
            (DEBUG_DIR / f"listing_{slug}_p{n}_cards.json").write_text(json.dumps(
                {"cards": cards[:5], "parsed": (b_xhr or b_ssr or b_dom)[:5]}, ensure_ascii=False, indent=1), encoding="utf-8")
        captured.clear()

        batch = b_xhr or b_ssr or b_dom
        pdiag["used"] = "xhr" if b_xhr else ("ssr" if b_ssr else ("dom" if b_dom else "none"))
        new = 0
        for rec in batch:
            rec["source"] = target["source"]
            k = listing_key(rec)
            if k not in seen:
                seen.add(k)
                results.append(rec)
                new += 1
        pdiag["new"] = new
        log("listings", f"{target['source']} {target.get('sub_area', '全港')} {target['type']} p{n}: +{new} "
                        f"(xhr {len(b_xhr)} / ssr {len(b_ssr)} / dom {len(b_dom)})")
        if new == 0 and not discover:
            break
        await page.wait_for_timeout(random.randint(3000, 6000))
    await page.close()
    diag["status"] = "ok" if results else "empty"
    diag["total"] = len(results)
    for rec in results:
        if target.get("sub_area"):
            rec["subArea"], rec["district"], rec["region"] = target["sub_area"], target["district"], target["region"]
        else:
            rec["subArea"], rec["district"], rec["region"] = detect_area(rec.get("areaText", "") + rec.get("estate", ""))
        rec.pop("areaText", None)
    return results


async def _scrape_all(max_pages: int, discover: bool, only: str | None):
    from playwright.async_api import async_playwright

    recs, ok, summary = [], set(), []
    async with async_playwright() as p:
        launch = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = await p.chromium.launch(**launch)
        ctx = await browser.new_context(locale="zh-HK", timezone_id="Asia/Hong_Kong", viewport={"width": 1366, "height": 900})
        for t in C.LISTING_TARGETS:
            if only and t["source"].lower() != only.lower():
                continue
            try:
                r = await scrape_target(ctx, t, max_pages, discover, summary)
                recs += r
                if r:
                    ok.add(t["source"])
            except Exception as exc:
                summary.append({"source": t["source"], "url": t["url"], "status": "exception", "error": str(exc)[:300]})
        await browser.close()
    return recs, ok, summary


# ------------------------------------------------------------------ 合并 / 指标
def merge(old: list[dict], fresh: list[dict], ok_sources: set[str]) -> list[dict]:
    by_id = {d["id"]: d for d in old}
    td = today()
    for rec in fresh:
        rec.setdefault("id", f"{rec['source'][:4].upper()}-{listing_key(rec)}")
        prev = by_id.get(rec["id"])
        if prev:
            hist = prev.get("priceHistory", [])
            if rec.get("priceWan") and prev.get("priceWan") and rec["priceWan"] != prev["priceWan"]:
                hist.append({"date": td, "from": prev["priceWan"], "to": rec["priceWan"]})
                if rec["priceWan"] < prev["priceWan"]:
                    rec["originalPriceWan"] = max(prev.get("originalPriceWan") or 0, prev["priceWan"])
            rec["priceHistory"] = hist[-10:]
            rec["firstSeen"] = prev.get("firstSeen", td)
            rec["originalPriceWan"] = rec.get("originalPriceWan") or prev.get("originalPriceWan")
        else:
            rec["firstSeen"] = td
        rec["lastSeen"] = td
        by_id[rec["id"]] = {**(prev or {}), **{k: v for k, v in rec.items() if v not in (None, "")}}
    cutoff = days_ago(C.LISTING_STALE_DAYS).strftime("%Y-%m-%d")
    kept = [d for d in by_id.values()
            if not (d.get("origin") != "seed_web_verified" and d.get("source") in ok_sources and d.get("lastSeen", td) < cutoff)]
    return kept[:C.LISTING_MAX]


def enrich(listings: list[dict]) -> list[dict]:
    def band(d):
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
            for b in (None, band(d)):
                sale_psf["sub"].setdefault((d.get("subArea"), b), []).append(v)

    r = 1 + C.MORTGAGE_RATE / 12
    bl = {"old": "≥40年旧楼", "new": "<40年楼"}
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
        rp, basis = None, ""
        if d.get("rentAsk"):
            rp, basis = d["rentAsk"] / d["area"], "实盘租金（同盘/连租约）"
        else:
            xs = rent_psf["estate"].get(d.get("estate"))
            if xs:
                rp, basis = statistics.median(xs), f"估算：同屋苑租盘中位数 @${statistics.median(xs):.1f}/呎（{len(xs)}个样本）"
            else:
                b = band(d)
                for lvl, key, label in (("sub", d.get("subArea"), "同片区"), ("district", d.get("district"), "同行政区")):
                    if b and len(rent_psf[lvl].get((key, b), [])) >= 2:
                        xs = rent_psf[lvl][(key, b)]
                        rp, basis = statistics.median(xs), f"估算：{label}{bl[b]}租盘中位数 @${statistics.median(xs):.1f}/呎（{len(xs)}个）"
                        break
                    if not b:
                        cands = [(statistics.median(v), len(v), bb) for bb in ("old", "new")
                                 if len(v := rent_psf[lvl].get((key, bb), [])) >= 2]
                        if cands:
                            m, n, bb = min(cands)
                            rp, basis = m, f"估算(楼龄未知取保守档)：{label}{bl[bb]}租盘中位数 @${m:.1f}/呎（{n}个）"
                            break
                if not rp and b != "old":
                    for lvl, key, label in (("sub", d.get("subArea"), "同片区"), ("district", d.get("district"), "同行政区")):
                        xs = rent_psf[lvl].get((key, None))
                        if xs:
                            rp, basis = statistics.median(xs), f"估算(样本少)：{label}租盘中位数 @${statistics.median(xs):.1f}/呎（{len(xs)}个）"
                            break
        for k in ("rentPsf", "estRent", "grossYield", "monthlyCarry", "vsMedianPct", "vsMedianBasis", "cutPct"):
            d.pop(k, None)
        if rp:
            d["rentPsf"] = round(rp, 1)
            d["estRent"] = d["rentAsk"] if d.get("rentAsk") else int(round(rp * d["area"] / 100) * 100)
            d["grossYield"] = round(d["estRent"] * 12 / price_hkd * 100, 2)
            loan = price_hkd * C.LTV
            pmt = loan * (C.MORTGAGE_RATE / 12) * r ** 360 / (r ** 360 - 1)
            d["monthlyCarry"] = int(round(d["estRent"] - pmt))
        d["rentBasis"] = basis or "暂无租金样本"
        ref, ref_label = sale_psf["estate"].get(d.get("estate")), "同屋苑"
        if not ref or len(ref) < 3:
            b = band(d)
            if b and len(sale_psf["sub"].get((d.get("subArea"), b), [])) >= 3:
                ref, ref_label = sale_psf["sub"][(d.get("subArea"), b)], "同片区" + bl[b]
            else:
                cands = [(statistics.median(v), v, bb) for bb in ("old", "new")
                         if len(v := sale_psf["sub"].get((d.get("subArea"), bb), [])) >= 3] if not b else []
                if cands:   # 楼龄未知 → 取新/旧两档中较低的中位数（保守，避免夸大折让）
                    _, ref, bb = min(cands, key=lambda x: x[0])
                    ref_label = "同片区" + bl[bb] + "(楼龄未知取保守档)"
                else:
                    ref, ref_label = sale_psf["sub"].get((d.get("subArea"), None)), "同片区(不分楼龄)"
        if ref and len(ref) >= 3:
            m = statistics.median(ref)
            d["vsMedianPct"] = round((d["psf"] / m - 1) * 100, 1)
            d["vsMedianBasis"] = f"{ref_label}放盘中位数 @${m:,.0f}/呎（{len(ref)}个）"
        tags.discard("减价盘")
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
    def by(k):
        vals = {d.get(k) for d in listings}
        return dict(sorted(((v, sum(1 for d in listings if d.get(k) == v)) for v in vals), key=lambda x: -x[1]))
    sale = [d for d in listings if d.get("type") == "sale"]
    ys = [d["grossYield"] for d in sale if d.get("grossYield")]
    return {"total": len(listings), "sale": len(sale), "rent": len(listings) - len(sale),
            "bySource": by("source"), "bySubArea": by("subArea"), "byDistrict": by("district"),
            "medianYield": round(statistics.median(ys), 2) if ys else None,
            "cutShare": round(sum(1 for d in sale if "减价盘" in d.get("tags", [])) / len(sale), 3) if sale else None}


def run(snapshot: dict, *, offline: bool, discover: bool, max_pages: int, only: str | None) -> dict:
    old = snapshot.get("listings", [])
    seeds = load_json(SEED_FILE, [])
    ids = {d["id"] for d in old}
    old += [s for s in seeds if s["id"] not in ids]
    fresh, ok, summary = [], set(), []
    if not offline:
        fresh, ok, summary = asyncio.run(_scrape_all(max_pages, discover, only))
    if discover:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "listings_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    listings = enrich(merge(old, fresh, ok))
    listings.sort(key=lambda d: (d.get("lastSeen", ""), d.get("firstSeen", "")), reverse=True)
    prev_meta = snapshot.get("listings_meta", {})
    snapshot["listings"] = listings
    snapshot["listings_meta"] = {**build_meta(listings), "lastRun": f"{__import__('datetime').datetime.now(HKT):%Y-%m-%d %H:%M} HKT",
                                 "okSources": sorted(ok), "freshCount": len(fresh),
                                 "prevTotal": prev_meta.get("total"),
                                 "runSummary": [{k: s.get(k) for k in ("source", "url", "status", "total")} for s in summary]}
    return {"count": len(listings), "fresh": len(fresh), "okSources": sorted(ok)}
