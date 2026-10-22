# -*- coding: utf-8 -*-
"""
一手新盘（v7.5）：价单搜寻 / 新开精选推荐 / 对所在区域楼价的影响 / 与同区二手对比。

数据来源（后者覆盖前者）：
  1) seed_newlaunch.json —— 人工整理的起始快照（注明来源与日期）
  2) inbox/newlaunch/*.csv —— 手动补充（发展商网站 / 价单 PDF 自行整理），格式见该文件夹 README
  3) 一手住宅物业销售资讯网 SRPE（www.srpe.gov.hk，销售监管局官方资料库）——
     Playwright 打开公开页面，读取页面链接与数据接口中的价单 PDF，再用 pdfplumber 解析。
     先检查 robots.txt；遇验证/拦截页即停止。SRPE 页面结构未经实测，首次请用 discover 模式检查样本。

「对区域楼价影响」是按新盘折实呎价相对同区二手（扣除新旧楼正常溢价）、项目规模和销情推算的方向判断，
是规则估算，不是统计模型，仅供参考。
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import json
import os
import re
import statistics
import urllib.parse

from . import config as C
from .common import DEBUG_DIR, INBOX_DIR, ROOT, load_json, log, num, robots_allows, today

SEED_FILE = ROOT / "seed_newlaunch.json"
SRPE_START = getattr(C, "SRPE_START_URLS", ["https://www.srpe.gov.hk/opip/index.htm?lang=zh_TW"])
SRPE_MAX_PDF = getattr(C, "SRPE_MAX_PRICE_LISTS", 25)
NL_DAYS = getattr(C, "NEWLAUNCH_DAYS", 120)          # 只保留最近 N 天有价单的项目
BLOCK_MARKERS = ("verify you are human", "access denied", "請完成驗證", "captcha")

# 新盘常见地名 → (片区, 行政区, 大区)；listings.AREA_MAP 未覆盖的先在这里处理
EXTRA_AREA = [
    (("西沙", "十四鄉", "十四乡", "烏溪沙", "乌溪沙"), "西沙/马鞍山", "沙田区", "新界东"),
    (("石硤尾", "石硖尾", "南昌", "西九龍", "西九龙"), "深水埗", "深水埗区", "九龙"),
    (("古洞", "北都"), "北区", "北区", "新界东"),
    (("北角", "炮台山"), "东区", "东区", "港岛"),
    (("錦田", "锦田"), "元朗", "元朗区", "新界西"),
]


def _detect(text: str):
    from .listings import detect_area
    for keys, sub, dist, reg in EXTRA_AREA:
        if any(k in (text or "") for k in keys):
            return sub, dist, reg
    return detect_area(text or "")


# ------------------------------------------------------------------ 价单 PDF 解析
# 香港价单标准格式：座 / 楼层 / 单位 | 实用面积 平方米(平方呎) | 售价 | 每平方米(每平方呎)售价
RE_UNIT_ROW = re.compile(
    r"(?P<pre>.*?)"
    r"(?P<sqm>\d{1,3}\.\d{1,3})\s*\(\s*(?P<sqft>\d{2,4})\s*\)\s*"
    r"(?P<price>\d{1,3}(?:,\d{3}){2,3})\s*"
    r"(?P<rsqm>\d{1,3}(?:,\d{3})+)\s*\(\s*(?P<rsqft>\d{1,3}(?:,\d{3})+)\s*\)")
RE_DISCOUNT = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%\s*(?:的)?(?:售價)?\s*(?:折扣|折實|discount)", re.I)
RE_PL_NO = re.compile(r"價單(?:第|編號)?\s*([0-9A-Z]{1,5})\s*號|Price\s*List\s*No\.?\s*([0-9A-Z]{1,5})", re.I)
RE_PL_DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日|(\d{1,2})[./](\d{1,2})[./](20\d{2})")


def parse_price_list_text(text: str) -> dict:
    """从价单文字解析单位行与最高折扣。返回 {units, discountPct, no, date}。"""
    units = []
    for line in (text or "").splitlines():
        m = RE_UNIT_ROW.search(line.replace("，", ","))
        if not m:
            continue
        area, price, psf = int(m["sqft"]), num(m["price"]), num(m["rsqft"])
        if not (120 <= area <= 6000 and 1_000_000 <= price <= 2_000_000_000):
            continue
        if psf and abs(price / area - psf) / psf > 0.08:      # 呎价与 售价/面积 不符 → 视为误读
            continue
        pre = m["pre"].split()
        units.append({"block": " ".join(pre[:-2]) if len(pre) > 2 else (pre[0] if len(pre) == 3 else ""),
                      "floor": pre[-2] if len(pre) >= 2 else "", "unit": pre[-1] if pre else "",
                      "area": area, "priceWan": round(price / 10_000, 1), "psf": int(psf or price / area)})
    discs = [float(x) for x in RE_DISCOUNT.findall(text or "") if 0 < float(x) <= 25]
    no = RE_PL_NO.search(text or "")
    d = RE_PL_DATE.search(text or "")
    date = None
    if d:
        date = (f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" if d.group(1)
                else f"{d.group(6)}-{int(d.group(5)):02d}-{int(d.group(4)):02d}")
    return {"units": units, "discountPct": max(discs) if discs else None,
            "no": (no.group(1) or no.group(2)) if no else None, "date": date}


def parse_price_list_pdf(data: bytes) -> dict:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages[:40])
    out = parse_price_list_text(text)
    out["textSample"] = text[:1500]
    return out


def summarise_units(units: list[dict], discount: float | None) -> dict:
    if not units:
        return {}
    ps, psf, ar = [u["priceWan"] for u in units], [u["psf"] for u in units], [u["area"] for u in units]
    k = 1 - (discount or 0) / 100
    return {"units": len(units), "priceMinWan": min(ps), "priceMaxWan": max(ps), "psfMin": min(psf), "psfMax": max(psf),
            "psfMedian": int(statistics.median(psf)), "areaMin": min(ar), "areaMax": max(ar), "areaMedian": int(statistics.median(ar)),
            "discountPct": discount, "netPriceMinWan": round(min(ps) * k, 1), "netPsfMin": int(min(psf) * k),
            "netPsfMedian": int(statistics.median(psf) * k)}


# ------------------------------------------------------------------ SRPE（Playwright）
JS_LINKS = r"""
() => Array.from(document.querySelectorAll('a[href]')).map(a => ({t: (a.innerText || a.title || '').trim().slice(0, 120),
  h: a.href, ctx: ((a.closest('tr,li') || a.parentElement || a).innerText || '').slice(0, 300)}))
"""


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:500]):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def _pdf_candidates(links: list[dict], payloads: list) -> list[dict]:
    out, seen = [], set()
    for l in links:
        h, t, ctx = l.get("h", ""), l.get("t", ""), l.get("ctx", "")
        if ".pdf" in h.lower() and re.search(r"價單|价单|price\s*list|/pl/|_pl", t + " " + h, re.I) and h not in seen:
            seen.add(h)
            out.append({"url": h, "ctx": ctx, "title": t})
    for url, p in payloads:
        for path, v in _walk(p):
            if isinstance(v, str) and ".pdf" in v.lower() and re.search(r"price|pl|價單", path + v, re.I):
                h = urllib.parse.urljoin(url, v)
                if h not in seen:
                    seen.add(h)
                    out.append({"url": h, "ctx": path, "title": ""})
    return out


async def _srpe(discover: bool) -> tuple[list[dict], dict]:
    from playwright.async_api import async_playwright
    diag = {"pages": [], "pdfs": []}
    projects = []
    async with async_playwright() as p:
        launch = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = await p.chromium.launch(**launch)
        ctx = await browser.new_context(locale="zh-HK", timezone_id="Asia/Hong_Kong")
        page = await ctx.new_page()
        ua = await page.evaluate("navigator.userAgent")
        payloads, links = [], []

        async def on_resp(resp):
            try:
                if "json" in resp.headers.get("content-type", ""):
                    payloads.append((resp.url, await resp.json()))
            except Exception:
                pass
        page.on("response", on_resp)

        async def settle():
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                await page.wait_for_timeout(3000)

        for start in SRPE_START:
            if not robots_allows(start, ua):
                diag["pages"].append({"url": start, "status": "skipped_robots"})
                continue
            try:
                r = await page.goto(start, wait_until="domcontentloaded", timeout=45000)
                await settle()
                body = (await page.evaluate("document.body ? document.body.innerText.slice(0,4000) : ''")).lower()
                if any(b in body for b in BLOCK_MARKERS):
                    diag["pages"].append({"url": start, "status": "blocked"})
                    continue
                links += await page.evaluate(JS_LINKS)
                diag["pages"].append({"url": start, "http": r.status if r else None, "links": len(links), "json": len(payloads)})
                for label in ("新上載的銷售文件", "新上载的销售文件", "Newly Uploaded Sales Documents"):
                    loc = page.get_by_text(label, exact=False).first
                    try:
                        if await loc.count():
                            await loc.click(timeout=5000)
                            await settle()
                            links += await page.evaluate(JS_LINKS)
                            diag["pages"].append({"url": page.url, "clicked": label, "links": len(links), "json": len(payloads)})
                            break
                    except Exception as exc:
                        diag["pages"].append({"clicked": label, "error": str(exc)[:120]})
            except Exception as exc:
                diag["pages"].append({"url": start, "error": str(exc)[:200]})
        cands = _pdf_candidates(links, payloads)
        diag["pdfCandidates"] = len(cands)
        if discover:
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / "srpe_links.json").write_text(json.dumps(links[:400], ensure_ascii=False, indent=1), encoding="utf-8")
            (DEBUG_DIR / "srpe_json.json").write_text(json.dumps([{"url": u, "body": b} for u, b in payloads[:15]],
                                                                ensure_ascii=False)[:600000], encoding="utf-8")
            try:
                await page.screenshot(path=str(DEBUG_DIR / "srpe_page.png"), full_page=True)
            except Exception:
                pass
        for c in cands[:SRPE_MAX_PDF]:
            try:
                resp = await ctx.request.get(c["url"], timeout=45000)
                if resp.status >= 400:
                    diag["pdfs"].append({"url": c["url"], "http": resp.status})
                    continue
                parsed = parse_price_list_pdf(await resp.body())
                name = re.split(r"\s{2,}|\n|價單|价单|Price List", c["ctx"] or c["title"])[0].strip()[:40] or "（未识别项目名）"
                diag["pdfs"].append({"url": c["url"], "name": name, "units": len(parsed["units"]), "discount": parsed["discountPct"],
                                     "sample": parsed.get("textSample", "")[:400] if discover else None})
                if parsed["units"]:
                    sub, dist, reg = _detect(c["ctx"])
                    projects.append({"name": name, "areaText": c["ctx"][:80], "subArea": sub, "district": dist, "region": reg,
                                     "priceList": {"no": parsed["no"], "date": parsed["date"] or today(), "url": c["url"],
                                                   **summarise_units(parsed["units"], parsed["discountPct"])},
                                     "units": parsed["units"][:400], "origin": "srpe", "source": "一手住宅物业销售资讯网（价单 PDF）"})
            except Exception as exc:
                diag["pdfs"].append({"url": c["url"], "error": str(exc)[:150]})
            await page.wait_for_timeout(1500)
        await browser.close()
    if discover:
        (DEBUG_DIR / "srpe_pdfs.json").write_text(json.dumps(diag["pdfs"], ensure_ascii=False, indent=1), encoding="utf-8")
    return projects, diag


# ------------------------------------------------------------------ 手动 CSV
PL_NUM = ("priceMinWan", "priceMaxWan", "psfMin", "psfMax", "psfMedian", "areaMin", "areaMax", "discountPct",
          "netPsfMin", "netPriceMinWan", "units")


def load_manual() -> list[dict]:
    """inbox/newlaunch/*.csv：项目级行（priceMinWan、psfMin…）或价单单位级行（area、price）都可以。"""
    folder = INBOX_DIR / "newlaunch"
    out = {}
    if not folder.exists():
        return []
    for f in sorted(folder.glob("*.csv")):
        try:
            rows = list(csv.DictReader(f.read_text(encoding="utf-8-sig").splitlines()))
        except Exception as exc:
            log("newlaunch", f"WARN {f.name}: {exc}")
            continue
        for r in rows:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            p = out.setdefault(name, {"name": name, "origin": "manual", "source": f"手动导入 {f.name}", "units": [], "priceList": {}})
            for k in ("nameEn", "areaText", "address", "developer", "mtr", "keyDate", "url"):
                if (r.get(k) or "").strip():
                    p[k] = r[k].strip()
            for k in ("unitsTotal", "unitsSold", "mtrMinutes"):
                if num(r.get(k)) is not None:
                    p[k] = int(num(r[k]))
            pl = p["priceList"]
            if (r.get("priceListNo") or "").strip():
                pl["no"] = r["priceListNo"].strip()
            if (r.get("priceListDate") or "").strip():
                pl["date"] = r["priceListDate"].strip()[:10]
            for k in PL_NUM:
                if num(r.get(k)) is not None:
                    pl[k] = num(r[k])
            if num(r.get("area")) and num(r.get("price")):          # 单位级行
                price = num(r["price"])
                price = price * 10_000 if price < 100_000 else price
                p["units"].append({"block": r.get("block", ""), "floor": r.get("floor", ""), "unit": r.get("unit", ""),
                                   "area": int(num(r["area"])), "priceWan": round(price / 10_000, 1), "psf": int(price / num(r["area"]))})
    for p in out.values():
        if p["units"]:
            p["priceList"] = {**summarise_units(p["units"], p["priceList"].get("discountPct")), **p["priceList"]}
        sub, dist, reg = _detect(" ".join((p.get("areaText", ""), p.get("address", ""), p["name"])))
        p.update({"subArea": sub, "district": dist, "region": reg})
    return list(out.values())


# ------------------------------------------------------------------ 合并 / 分析
def _key(name: str) -> str:
    return re.sub(r"[\s·‧・()（）\-]+", "", (name or "").upper())


def merge(*groups) -> list[dict]:
    """后面的组覆盖前面的组（seed < 上次结果 < 手动 < SRPE）；若新数据价单日期更旧则保留旧价单、只补资料。"""
    by = {}
    for g in groups:
        for p in g:
            k = _key(p["name"])
            old = by.get(k)
            if old and (old.get("priceList", {}).get("date") or "") > (p.get("priceList", {}).get("date") or ""):
                for f in ("unitsTotal", "unitsSold", "keyDate", "developer", "mtr", "mtrMinutes", "nameEn", "address"):
                    if old.get(f) in (None, "") and p.get(f) not in (None, ""):
                        old[f] = p[f]
                continue
            merged = {**(old or {}), **{k2: v for k2, v in p.items() if v not in (None, "", [], {})}}
            if old and old.get("priceList") and p.get("priceList"):
                merged["priceList"] = {**old["priceList"], **{a: b for a, b in p["priceList"].items() if b not in (None, "")}}
            if old and not p.get("units"):
                merged["units"] = old.get("units", [])
            by[k] = merged
    return list(by.values())


def _net_psf(p) -> int | None:
    pl = p.get("priceList", {})
    for k in ("netPsfMedian", "netPsfMin"):
        if pl.get(k):
            return int(pl[k])
    base = pl.get("psfMedian") or pl.get("psfMin")
    if not base:
        return None
    if not pl.get("discountPct") and pl.get("netPriceMinWan") and pl.get("priceMinWan"):
        return int(base * pl["netPriceMinWan"] / pl["priceMinWan"])      # 用 折实入场价/原价入场价 推算折扣
    return int(base * (1 - (pl.get("discountPct") or 0) / 100))


def compare_secondary(p: dict, listings: list[dict]) -> dict:
    sale = [d for d in listings if d.get("type") == "sale" and d.get("psf")]
    pool, level = [], None
    for level, key in (("subArea", p.get("subArea")), ("district", p.get("district"))):
        pool = [d for d in sale if key and d.get(level) == key]
        if len(pool) >= 3:
            break
    if len(pool) < 3:
        return {"basis": "同区二手样本不足（<3 盘）", "secN": len(pool), "comps": []}
    label = "同片区" if level == "subArea" else "同行政区"
    med = int(statistics.median(d["psf"] for d in pool))
    ages = [d["ageYears"] for d in pool if d.get("ageYears") is not None]
    med_age = statistics.median(ages) if ages else None
    newer = [d for d in pool if d.get("ageYears") is not None and d["ageYears"] <= 15]
    npsf = _net_psf(p)
    res = {"basis": f"{label}二手放盘 {len(pool)} 个", "label": label, "secN": len(pool), "secMedianPsf": med,
           "secMedianAge": round(med_age) if med_age is not None else None}
    if len(newer) >= 3:
        res["secNewMedianPsf"], res["secNewN"] = int(statistics.median(d["psf"] for d in newer)), len(newer)
    if npsf:
        res["premiumPct"] = round((npsf / med - 1) * 100, 1)
        exp = min(30.0, 0.6 * med_age) if med_age is not None else 15.0   # 经验假设：楼龄每差 1 年约 0.6%，封顶 30%
        res["expectedPremiumPct"] = round(exp, 1)
        res["gapPct"] = round(res["premiumPct"] - exp, 1)
        if res.get("secNewMedianPsf"):
            res["vsNewSecPct"] = round((npsf / res["secNewMedianPsf"] - 1) * 100, 1)
    pl = p.get("priceList", {})
    mid = pl.get("areaMedian") or (((pl.get("areaMin") or 0) + (pl.get("areaMax") or 0)) / 2) or 450
    comps = sorted(pool, key=lambda d: (abs((d.get("area") or 0) - mid) / mid > 0.3,
                                        d["ageYears"] if d.get("ageYears") is not None else 99, abs((d.get("area") or 0) - mid)))
    per, picked = {}, []
    for d in comps:                                   # 每个屋苑最多 2 盘，让对比更多元
        if per.get(d.get("estate"), 0) < 2:
            per[d.get("estate")] = per.get(d.get("estate"), 0) + 1
            picked.append(d)
        if len(picked) >= 6:
            break
    comps = picked
    res["comps"] = [{"id": d.get("id"), "estate": d.get("estate"), "unit": " ".join(x for x in (d.get("building"), d.get("floor")) if x),
                     "rooms": d.get("rooms"), "area": d.get("area"), "priceWan": d.get("priceWan"), "psf": d.get("psf"),
                     "ageYears": d.get("ageYears"), "vsNewPct": round((d["psf"] / npsf - 1) * 100, 1) if npsf else None,
                     "url": d.get("url"), "sourceName": d.get("sourceName")} for d in comps]
    return res


def impact(p: dict, cmp: dict) -> dict:
    units = p.get("unitsTotal") or p.get("priceList", {}).get("units") or 0
    size = 1.0 if units >= 800 else (0.7 if units >= 300 else 0.4)
    sold = (p["unitsSold"] / p["unitsTotal"]) if p.get("unitsSold") is not None and p.get("unitsTotal") else None
    gap = cmp.get("gapPct")
    if gap is None:
        return {"label": "数据不足", "score": None, "size": units,
                "text": "未能取得同区二手呎价或新盘折实呎价，暂无法判断。可在 config 增加该区的中原盘源目标。"}
    s = max(-1.0, min(1.0, gap / 15)) * size + (0.25 * (sold - 0.5) * 2 if sold is not None else 0)
    s = round(max(-1.0, min(1.0, s)), 2)
    lbl = ("利好同区二手" if s >= 0.3 else "略为支撑" if s >= 0.1 else "中性" if s > -0.1 else "略有下压" if s > -0.3 else "下压同区二手")
    parts = [f"新盘折实呎价较{cmp['label']}二手中位数 {cmp['premiumPct']:+.1f}%",
             f"区内二手楼龄中位数 {cmp.get('secMedianAge', '—')} 年，新旧楼正常溢价约 {cmp['expectedPremiumPct']:.0f}%",
             f"扣除楼龄因素后定价{'偏低' if gap < -3 else '偏高' if gap > 3 else '贴市'}（{gap:+.1f}%）"]
    if gap < -3:
        parts.append("以贴市或低于二手的价钱推盘，会分流区内买家，未来 3–6 个月二手业主较难反价、议价空间扩大")
    elif gap > 3:
        parts.append("定价高于区内二手正常水平，为区内较新屋苑提供价格参考，二手叫价较易企稳")
    else:
        parts.append("定价与区内二手大致一致，对二手价格影响有限，主要是分流成交量")
    parts.append(f"项目规模 {units or '未知'} 伙" + (f"，已售 {sold * 100:.0f}%" if sold is not None else ""))
    return {"label": lbl, "score": s, "size": units, "soldRatio": round(sold, 2) if sold is not None else None,
            "text": "；".join(parts) + "。（规则估算，未经回测）"}


def _mtr_minutes(p):
    if p.get("mtrMinutes") is not None:
        return p["mtrMinutes"]
    t = p.get("mtr") or ""
    m = re.search(r"([\d.]+)\s*公里", t)
    if m:
        return float(m.group(1)) * 1000 / 80
    m = re.search(r"(\d+)\s*米", t)
    if m:
        return int(m.group(1)) / 80
    m = re.search(r"(\d+)\s*分", t)
    return int(m.group(1)) if m else None


def recommend(p: dict, cmp: dict) -> dict:
    pl = p.get("priceList", {})
    sc, reasons, warns = {}, [], []
    gap = cmp.get("gapPct")
    sc["value"] = 50 if gap is None else max(0, min(100, 50 - gap * 3))
    if gap is not None and gap <= -5:
        reasons.append(f"扣除楼龄后较同区二手低 {abs(gap):.0f}%")
    entry = pl.get("netPriceMinWan") or pl.get("priceMinWan")
    sc["entry"] = 50 if not entry else max(0, min(100, (900 - entry) / 5))
    if entry and entry <= 500:
        reasons.append(f"入场约 {entry:.0f} 万")
    mins = _mtr_minutes(p)
    sc["mtr"] = 50 if mins is None else max(0, min(100, 100 - (mins - 3) * 7))
    if mins is not None and mins <= 8:
        reasons.append(f"步行约 {mins:.0f} 分钟到港铁")
    sold = (p["unitsSold"] / p["unitsTotal"]) if p.get("unitsSold") is not None and p.get("unitsTotal") else None
    sc["momentum"] = 50 if sold is None else sold * 100
    if sold is not None and sold >= 0.7:
        reasons.append(f"已售 {sold * 100:.0f}%")
    try:
        age = (dt.date.fromisoformat(today()) - dt.date.fromisoformat((pl.get("date") or "")[:10])).days
    except ValueError:
        age = 60
    sc["fresh"] = max(0, min(100, 100 - age * 2))
    if age <= 14:
        reasons.append("两周内新价单")
    sc["focus"] = 100 if any(a in (p.get("subArea") or "") for a in C.FOCUS_SUB_AREAS) else 30
    w = {"value": 0.30, "entry": 0.20, "mtr": 0.15, "momentum": 0.15, "fresh": 0.10, "focus": 0.10}
    total = round(sum(w[k] * sc[k] for k in w), 1)
    if cmp.get("secN", 0) < 3:
        warns.append("同区二手样本不足，价值判断可信度低")
        total = min(total, 61.9)
    elif cmp.get("label") == "同行政区":
        warns.append("同片区二手不足，改用同行政区其他片区对比，价值判断仅供参考")
        total = min(total, 61.9)
    if not pl.get("discountPct") and not pl.get("netPsfMin") and not pl.get("netPriceMinWan"):
        warns.append("未取得价单折扣，折实呎价按原价计（偏保守）")
    if p.get("keyDate"):
        try:
            months = (dt.date.fromisoformat(p["keyDate"][:10]) - dt.date.fromisoformat(today())).days / 30
            if months > 18:
                warns.append(f"楼花期约 {months:.0f} 个月，留意建期利率与估价风险")
        except ValueError:
            pass
    tier = "S" if total >= 72 else "A" if total >= 62 else "B" if total >= 52 else ""
    return {"score": total, "tier": tier, "reasons": reasons[:4], "warnings": warns[:3], "parts": {k: round(v) for k, v in sc.items()}}


def run(snapshot: dict, *, offline: bool, discover: bool = False) -> dict:
    seed = load_json(SEED_FILE, []) or []
    for s in seed:
        s.setdefault("origin", "seed")
    prev = [p for p in snapshot.get("newlaunch_projects", []) if p.get("origin") != "seed"]
    manual = load_manual()
    srpe, diag = [], {}
    if not offline:
        try:
            srpe, diag = asyncio.run(_srpe(discover))
        except Exception as exc:
            diag = {"error": str(exc)[:300]}
            log("newlaunch", f"WARN SRPE: {exc}")
    projects = merge(seed, prev, manual, srpe)
    cutoff = (dt.date.fromisoformat(today()) - dt.timedelta(days=NL_DAYS)).isoformat()
    projects = [p for p in projects if (p.get("priceList", {}).get("date") or today()) >= cutoff]
    listings = snapshot.get("listings", [])
    for p in projects:
        if not p.get("district"):
            sub, dist, reg = _detect(" ".join((p.get("areaText", ""), p.get("address", ""), p["name"])))
            p["subArea"], p["district"], p["region"] = p.get("subArea") or sub, dist, reg
        p["id"] = p.get("id") or "NL-" + _key(p["name"])[:20]
        p["netPsf"] = _net_psf(p)
        p["compare"] = compare_secondary(p, listings)
        p["impact"] = impact(p, p["compare"])
        p["rec"] = recommend(p, p["compare"])
        p["units"] = (p.get("units") or [])[:400]
    projects.sort(key=lambda p: -(p["rec"]["score"] or 0))
    areas = {}
    for p in projects:
        areas.setdefault(p.get("district") or "未识别", []).append(p)
    area_summary = []
    for k, ps in areas.items():
        scored = [p for p in ps if p["impact"].get("score") is not None]
        wsum = sum(p["impact"]["size"] or 300 for p in scored)
        s = round(sum((p["impact"]["size"] or 300) * p["impact"]["score"] for p in scored) / wsum, 2) if wsum else None
        area_summary.append({"district": k, "projects": len(ps), "units": sum((p.get("unitsTotal") or 0) for p in ps),
                             "impactScore": s, "names": [p["name"] for p in ps][:6]})
    area_summary.sort(key=lambda a: (a["impactScore"] is None, a["impactScore"] or 0))
    if discover:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "newlaunch_summary.json").write_text(json.dumps(diag, ensure_ascii=False, indent=1), encoding="utf-8")
    snapshot["newlaunch_projects"] = projects
    snapshot["newlaunch_meta"] = {
        "generated": today(), "count": len(projects), "fromSrpe": len(srpe), "manual": len(manual),
        "seed": sum(1 for p in projects if p.get("origin") == "seed"), "areas": area_summary,
        "diag": {k: v for k, v in diag.items() if k in ("pages", "pdfCandidates", "error")},
        "method": ("推荐分 = 30% 扣除楼龄后较同区二手折让 + 20% 入场价 + 15% 港铁距离 + 15% 销情 + 10% 价单新鲜度 + 10% 焦点区；"
                   "同区二手样本不足时最多 B 级。区域影响 = 新盘折实呎价相对同区二手（扣除新旧楼正常溢价）× 项目规模 + 销情，"
                   "为规则估算，未经回测。"),
    }
    return {"projects": len(projects), "srpe": len(srpe), "manual": len(manual), "pdfCandidates": diag.get("pdfCandidates")}
