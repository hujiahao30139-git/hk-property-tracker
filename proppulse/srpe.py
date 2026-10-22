# -*- coding: utf-8 -*-
"""
一手住宅物业销售资讯网（SRPE）抓取（v7.7）。

2026-10-22 discover：已勾选同意并按 Continue，网站调用了 addActivityLog（记录同意），随即跳到
/opip/disclaimer?return=... 这个中转页；程序在中转页还没转到目标页时就截图，所以是白页、0 个链接。

v7.7：
  * 同意后最多等 30 秒，直到网址离开 disclaimer 页、且页面出现内容；仍未转到时，在同一分页直接打开目标页
    （同意状态保存在同一浏览器会话里）
  * 勾选框若是自订元件，改为点击文字标签，并确认已勾选才按 Continue
  * 记录网站全部 /api/ 请求网址（含非 JSON），discover 时保存，方便找出项目列表与价单接口
  * 价单只用于个人研究：单位级价单不写入公开的 market_data.json（见 PUBLISH_UNITS）

条款要点（2026-09-24 版）：政府版权内容只限个人或非商业内部用途下载；价单属发展商（第三方）版权，
除了查阅及下载，转载或分发须取得版权持有人授权。GitHub Pages 公开网站属「向公众提供」，
所以默认只公开从价单计算出的摘要数字（价钱范围、呎价、折扣），不公开逐个单位的价单表。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse

from . import config as C
from .common import DEBUG_DIR, robots_allows, today

BASE = "https://www.srpe.gov.hk/opip/"
TARGETS = getattr(C, "SRPE_TARGETS", [
    # (同意页, 同意后的目标页)
    ("disclaimer_newly_upload_sales_brochure_price_list", "newly_upload_sales_brochure_price_list"),
])
ACCEPT = getattr(C, "SRPE_ACCEPT_TERMS", False)
MAX_PDF = getattr(C, "SRPE_MAX_PRICE_LISTS", 25)
PUBLISH_UNITS = getattr(C, "SRPE_PUBLISH_UNITS", False)
BLOCK = ("verify you are human", "access denied", "請完成驗證", "captcha")

JS_LINKS = r"""
() => Array.from(document.querySelectorAll('a[href], [onclick], button')).map(a => ({
  t: (a.innerText || a.title || a.getAttribute('aria-label') || '').trim().slice(0, 160),
  h: a.href || '', oc: (a.getAttribute('onclick') || '').slice(0, 200),
  ctx: ((a.closest('tr,li') || a.parentElement || a).innerText || '').slice(0, 300)}))
"""


def _records(payloads):
    """从接口 JSON 找出带 PDF 的记录（项目 + 价单）。"""
    out = []

    def visit(o):
        if isinstance(o, dict):
            flat = {k: v for k, v in o.items() if isinstance(v, (str, int, float))}
            pdfs = [v for v in flat.values() if isinstance(v, str) and re.search(r"\.pdf($|\?)", v, re.I)]
            name = next((flat[k] for k in flat if re.search(r"(dev|devt|development|name|eng|chi).*(name|nm)|^name", k, re.I)
                         and isinstance(flat[k], str) and 1 < len(flat[k]) < 80), None)
            if pdfs:
                out.append({"name": name, "pdfs": pdfs, "fields": {k: str(v)[:80] for k, v in list(flat.items())[:25]}})
            for v in o.values():
                visit(v)
        elif isinstance(o, list):
            for v in o[:3000]:
                visit(v)
    for _, p in payloads:
        visit(p)
    return out


async def _settle(page, ms=15000):
    try:
        await page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        await page.wait_for_timeout(3000)


async def _content_ready(page) -> bool:
    """离开 disclaimer 页且页面有实质内容（文字 > 300 字或有表格/链接）。"""
    if "disclaimer" in page.url:
        return False
    try:
        return await page.evaluate("""() => {
          const t = (document.body && document.body.innerText) || '';
          return t.length > 300 || document.querySelectorAll('table tr, a[href*=".pdf"]').length > 0;
        }""")
    except Exception:
        return False


async def accept_and_open(page, target: str, diag: dict) -> bool:
    """在同意页勾选并 Continue，等待转到目标页；不成功则在同一会话直接打开目标页。"""
    log = {"target": target}
    diag.setdefault("terms", []).append(log)
    try:
        box = page.locator("input[type=checkbox]").first
        if await box.count():
            try:
                await box.check(timeout=5000)
            except Exception:
                await page.get_by_text(re.compile("agree to be bound|同意受|已閱讀|已阅读")).first.click(timeout=5000)
            if not await box.is_checked():
                await page.get_by_text(re.compile("agree to be bound|同意受|已閱讀|已阅读")).first.click(timeout=5000)
            log["checked"] = await box.is_checked()
            btn = page.get_by_role("button", name=re.compile("Continue|繼續|继续"))
            if not await btn.count():
                btn = page.locator("button, input[type=submit], a").filter(has_text=re.compile("Continue|繼續|继续"))
            await btn.first.click(timeout=5000)
        else:
            log["checked"] = "未见勾选框"
        for _ in range(30):                      # 最多等 30 秒，让中转页转到目标页
            await page.wait_for_timeout(1000)
            if await _content_ready(page):
                break
        log["afterContinue"] = page.url
        if not await _content_ready(page):
            await page.goto(BASE + target, wait_until="domcontentloaded", timeout=45000)
            await _settle(page)
            for _ in range(15):
                if await _content_ready(page):
                    break
                await page.wait_for_timeout(1000)
            log["directOpen"] = page.url
        ok = await _content_ready(page)
        log["ok"] = ok
        return ok
    except Exception as exc:
        log["error"] = str(exc)[:200]
        return False


async def fetch(discover: bool, parse_pdf, summarise, detect) -> tuple[list[dict], dict]:
    from playwright.async_api import async_playwright
    diag = {"accept": ACCEPT, "pages": [], "pdfs": [], "apiCalls": []}
    if not ACCEPT:
        diag["status"] = ("SRPE 内容页须先同意条款及细则；config.py 的 SRPE_ACCEPT_TERMS 为 False，已跳过。")
        return [], diag
    projects, payloads, pdf_resps, links = [], [], [], []
    async with async_playwright() as p:
        launch = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = await p.chromium.launch(**launch)
        ctx = await browser.new_context(locale="zh-HK", timezone_id="Asia/Hong_Kong", viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        ua = await page.evaluate("navigator.userAgent")

        async def on_resp(resp):
            try:
                u, ct = resp.url, resp.headers.get("content-type", "")
                if "/api/" in u:
                    diag["apiCalls"].append({"url": u[:200], "status": resp.status, "type": ct[:40]})
                if "pdf" in ct:
                    pdf_resps.append(u)
                elif "json" in ct and "/api/" in u:
                    payloads.append((u, await resp.json()))
            except Exception:
                pass
        page.on("response", on_resp)

        for i, (gate, target) in enumerate(TARGETS):
            start = BASE + gate
            if not robots_allows(start, ua):
                diag["pages"].append({"url": start, "status": "skipped_robots"})
                continue
            try:
                r = await page.goto(start, wait_until="domcontentloaded", timeout=45000)
                await _settle(page)
                if not await accept_and_open(page, target, diag):
                    diag["pages"].append({"url": page.url, "status": "未能进入目标页"})
                body = await page.evaluate("document.body ? document.body.innerText : ''")
                if any(b in body.lower()[:4000] for b in BLOCK):
                    diag["pages"].append({"url": page.url, "status": "blocked"})
                    continue
                for _ in range(3):                    # 触发懒加载
                    await page.mouse.wheel(0, 4000)
                    await page.wait_for_timeout(800)
                links += await page.evaluate(JS_LINKS)
                diag["pages"].append({"url": page.url, "http": r.status if r else None, "links": len(links),
                                      "api": len(payloads), "textLen": len(body)})
                if discover:
                    DEBUG_DIR.mkdir(exist_ok=True)
                    await page.screenshot(path=str(DEBUG_DIR / f"srpe_after_terms_{i}.png"), full_page=True)
                    (DEBUG_DIR / f"srpe_after_terms_{i}.txt").write_text(f"URL: {page.url}\n\n{body[:20000]}", encoding="utf-8")
                    (DEBUG_DIR / f"srpe_after_terms_{i}.html").write_text((await page.content())[:500000], encoding="utf-8")
            except Exception as exc:
                diag["pages"].append({"url": start, "error": str(exc)[:200]})

        recs = _records(payloads)
        cands, seen = [], set()
        for rec in recs:
            for u in rec["pdfs"]:
                full = urllib.parse.urljoin("https://www.srpe.gov.hk/", u)
                if full not in seen and re.search(r"price|pl|價單|价单", full + json.dumps(rec["fields"], ensure_ascii=False), re.I):
                    seen.add(full)
                    cands.append({"url": full, "name": rec["name"], "ctx": json.dumps(rec["fields"], ensure_ascii=False)})
        for l in links:
            h = l.get("h", "")
            if ".pdf" in h.lower() and re.search(r"價單|价单|price\s*list", l.get("t", "") + l.get("ctx", "") + h, re.I) and h not in seen:
                seen.add(h)
                cands.append({"url": h, "name": None, "ctx": l.get("ctx", "")})
        for u in pdf_resps:
            if u not in seen:
                seen.add(u)
                cands.append({"url": u, "name": None, "ctx": ""})
        diag["apiRecordsWithPdf"] = len(recs)
        diag["pdfCandidates"] = len(cands)
        if discover:
            (DEBUG_DIR / "srpe_json.json").write_text(json.dumps([{"url": u, "body": b} for u, b in payloads[:40]],
                                                                ensure_ascii=False)[:2_000_000], encoding="utf-8")
            (DEBUG_DIR / "srpe_links.json").write_text(json.dumps(links[:600], ensure_ascii=False, indent=1), encoding="utf-8")
            (DEBUG_DIR / "srpe_records.json").write_text(json.dumps(recs[:200], ensure_ascii=False, indent=1), encoding="utf-8")

        for c in cands[:MAX_PDF]:
            try:
                resp = await ctx.request.get(c["url"], timeout=60000)
                if resp.status >= 400:
                    diag["pdfs"].append({"url": c["url"], "http": resp.status})
                    continue
                parsed = parse_pdf(await resp.body())
                name = c["name"] or (re.split(r"\s{2,}|\n|價單|价单|Price List", c["ctx"])[0].strip()[:40] if c["ctx"] else "") or "（未识别项目名）"
                diag["pdfs"].append({"url": c["url"], "name": name, "units": len(parsed["units"]), "discount": parsed["discountPct"],
                                     "no": parsed["no"], "date": parsed["date"],
                                     "sample": parsed.get("textSample", "")[:600] if discover else None})
                if parsed["units"]:
                    sub, dist, reg = detect(c["ctx"] + " " + name)
                    projects.append({"name": name, "areaText": c["ctx"][:80], "subArea": sub, "district": dist, "region": reg,
                                     "priceList": {"no": parsed["no"], "date": parsed["date"] or today(), "url": c["url"],
                                                   **summarise(parsed["units"], parsed["discountPct"])},
                                     "units": parsed["units"][:400] if PUBLISH_UNITS else [],
                                     "origin": "srpe", "source": "一手住宅物业销售资讯网（价单 PDF，只公开摘要数字）"})
            except Exception as exc:
                diag["pdfs"].append({"url": c["url"], "error": str(exc)[:150]})
            await page.wait_for_timeout(2000)
        await browser.close()
    diag["apiCalls"] = diag["apiCalls"][:80]
    if discover:
        (DEBUG_DIR / "srpe_pdfs.json").write_text(json.dumps(diag["pdfs"], ensure_ascii=False, indent=1), encoding="utf-8")
        (DEBUG_DIR / "srpe_diag.json").write_text(json.dumps({k: v for k, v in diag.items() if k != "pdfs"},
                                                            ensure_ascii=False, indent=1), encoding="utf-8")
    return projects, diag
