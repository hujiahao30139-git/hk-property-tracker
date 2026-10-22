# -*- coding: utf-8 -*-
"""
一手住宅物业销售资讯网（SRPE）抓取（v7.6）。

2026-10-21 discover 结果：网站内容页（新上载的销售文件、住宅发展项目索引等）前面有一页「条款及细则」，
须勾选「已阅读并同意」后按 Continue 才能进入；旧版没有处理这一页，所以 0 个价单。

本版处理方式：
  * 只有在 config.py 设 SRPE_ACCEPT_TERMS = True 时才会代你勾选同意。请先亲自在浏览器读完 SRPE 的
    条款及细则与版权告示，确认个人研究、低频下载价单符合条款后再打开。默认 False，不会自动同意。
  * 进入后记录页面自身调用的 /api/SrpeWebService/ 数据接口、页面链接、PDF 回应，从中找价单 PDF。
  * discover 模式额外保存：同意后页面截图、页面文字、全部接口回应，用于下一步调整。
  * 每个 PDF 之间间隔 2 秒，最多 SRPE_MAX_PRICE_LISTS 份；遇验证/拦截页即停止。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse

from . import config as C
from .common import DEBUG_DIR, robots_allows, today

START_URLS = getattr(C, "SRPE_START_URLS", [
    "https://www.srpe.gov.hk/opip/disclaimer_newly_upload_sales_brochure_price_list",
])
ACCEPT = getattr(C, "SRPE_ACCEPT_TERMS", False)
MAX_PDF = getattr(C, "SRPE_MAX_PRICE_LISTS", 25)
BLOCK = ("verify you are human", "access denied", "請完成驗證", "captcha")

JS_LINKS = r"""
() => Array.from(document.querySelectorAll('a[href], [onclick]')).map(a => ({
  t: (a.innerText || a.title || a.getAttribute('aria-label') || '').trim().slice(0, 160),
  h: a.href || '', oc: (a.getAttribute('onclick') || '').slice(0, 200),
  ctx: ((a.closest('tr,li') || a.parentElement || a).innerText || '').slice(0, 300)}))
"""


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2000]):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def _records(payloads):
    """从接口 JSON 找出「项目 + 价单」记录：同一个 dict 里同时有名称类字段与 pdf/文件类字段。"""
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
            for v in o[:2000]:
                visit(v)
    for url, p in payloads:
        visit(p)
    return out


async def accept_terms(page, diag) -> bool:
    """勾选「已阅读并同意」并按 Continue / 繼續。只在 SRPE_ACCEPT_TERMS=True 时调用。"""
    try:
        box = page.locator("input[type=checkbox]").first
        if not await box.count():
            diag["terms"] = "未见条款页"
            return True
        try:
            await box.check(timeout=5000)
        except Exception:
            await page.get_by_text(re.compile("agree to be bound|同意受|同意遵守|已閱讀|已阅读")).first.click(timeout=5000)
        for name in ("Continue", "繼續", "继续"):
            btn = page.get_by_role("button", name=name)
            if await btn.count():
                await btn.first.click(timeout=5000)
                break
        else:
            await page.locator("button, input[type=submit]").filter(has_text=re.compile("Continue|繼續|继续")).first.click(timeout=5000)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            await page.wait_for_timeout(4000)
        diag["terms"] = f"已按设定同意，现时网址 {page.url}"
        return True
    except Exception as exc:
        diag["terms"] = f"处理条款页失败：{str(exc)[:150]}"
        return False


async def fetch(discover: bool, parse_pdf, summarise, detect) -> tuple[list[dict], dict]:
    from playwright.async_api import async_playwright
    diag = {"accept": ACCEPT, "pages": [], "pdfs": []}
    if not ACCEPT:
        diag["status"] = ("SRPE 内容页须先同意条款及细则；config.py 的 SRPE_ACCEPT_TERMS 为 False，已跳过。"
                          "请先亲自阅读条款后再决定是否开启。")
        return [], diag
    projects, payloads, pdf_resps, links = [], [], [], []
    async with async_playwright() as p:
        launch = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = await p.chromium.launch(**launch)
        ctx = await browser.new_context(locale="zh-HK", timezone_id="Asia/Hong_Kong")
        page = await ctx.new_page()
        ua = await page.evaluate("navigator.userAgent")

        async def on_resp(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if "pdf" in ct:
                    pdf_resps.append(resp.url)
                elif "json" in ct and "/api/" in resp.url:
                    payloads.append((resp.url, await resp.json()))
            except Exception:
                pass
        page.on("response", on_resp)

        for i, start in enumerate(START_URLS):
            if not robots_allows(start, ua):
                diag["pages"].append({"url": start, "status": "skipped_robots"})
                continue
            try:
                r = await page.goto(start, wait_until="domcontentloaded", timeout=45000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    await page.wait_for_timeout(3000)
                if not await accept_terms(page, diag):
                    continue
                body = await page.evaluate("document.body ? document.body.innerText : ''")
                if any(b in body.lower()[:4000] for b in BLOCK):
                    diag["pages"].append({"url": start, "status": "blocked"})
                    continue
                # 页面可能是分页列表 / 需要展开：滚到底触发懒加载
                for _ in range(3):
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
        for r in recs:
            for u in r["pdfs"]:
                full = urllib.parse.urljoin("https://www.srpe.gov.hk/", u)
                if full not in seen and re.search(r"price|pl|價單|价单", full + json.dumps(r["fields"], ensure_ascii=False), re.I):
                    seen.add(full)
                    cands.append({"url": full, "name": r["name"], "ctx": json.dumps(r["fields"], ensure_ascii=False)})
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
                                     "units": parsed["units"][:400], "origin": "srpe",
                                     "source": "一手住宅物业销售资讯网（价单 PDF）"})
            except Exception as exc:
                diag["pdfs"].append({"url": c["url"], "error": str(exc)[:150]})
            await page.wait_for_timeout(2000)
        await browser.close()
    if discover:
        (DEBUG_DIR / "srpe_pdfs.json").write_text(json.dumps(diag["pdfs"], ensure_ascii=False, indent=1), encoding="utf-8")
        (DEBUG_DIR / "srpe_diag.json").write_text(json.dumps({k: v for k, v in diag.items() if k != "pdfs"},
                                                            ensure_ascii=False, indent=1), encoding="utf-8")
    return projects, diag
