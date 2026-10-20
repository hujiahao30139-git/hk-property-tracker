# -*- coding: utf-8 -*-
"""
社区舆情（v7.3）：只抓公开、无需登录即可浏览的页面 / 官方 API；不保存用户名，看板只显示 ≤90 字摘录。
  自动：宅谷地产论坛、亲子王国（Discuz）、Telegram 公开频道预览、YouTube Data API（需 YT_API_KEY）
  手动：inbox/social/*.csv|*.txt —— 小红书、Facebook 群组、WhatsApp/微信群等

v7.3 修正（根据 2026-10-20 discover 样本）：
  * 宅谷列表页有嵌套表格，旧版会读到外层整张表 → 回复数/浏览量错乱（如 11,061 回复）；现在只读最内层的行
  * 帖子日期改用「发帖日期」而不是「最后回复日期」→ 2015 年开的长寿帖不再被当成近 30 天新帖
  * 宅谷的帖子是专栏文章：多段正文不再被误当作「回复」；去掉作者简介、「敬請留言者自律」、网站导航等杂讯
  * 人工整理样本去重，并固定日期 2026-10-16（30 天后自动退出统计）
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import time
import urllib.parse
from collections import Counter, defaultdict

from . import config as C
from .common import DEBUG_DIR, INBOX_DIR, days_ago, http_get, log, now_hkt, num, parse_date, short, strip_html
from .lexicon import STANCE_LABEL, TOPICS, analyse, sanitize_quote, to_trad

PLATFORM_LABEL = {
    "BabyKingdom": "亲子王国", "PropertyHK": "宅谷地产论坛（多为专栏文章）", "Reddit": "Reddit", "Telegram": "Telegram 公开频道",
    "YouTube": "YouTube 评论", "LIHKG": "连登 LIHKG", "Discuss": "香港讨论区", "Xiaohongshu": "小红书（手动导入）",
    "Facebook": "Facebook（手动导入）", "WhatsApp": "WhatsApp 群（手动导入）", "WeChat": "微信群（手动导入）",
    "Manual": "其他手动导入", "Curated": "人工整理样本",
}
DEPTH_LABEL = {"title": "仅标题", "body": "已读正文", "body+replies": "已读正文+回复", "full": "全文（导入/评论）"}
CURATED_DATE = "2026-10-16"
_REL = [to_trad(k).lower() for k in getattr(C, "SOCIAL_RELEVANCE", [])]
_SKIP = getattr(C, "SOCIAL_SKIP_URL", ["pwdreset", "login", "register"])
DETAIL_MAX = getattr(C, "SOCIAL_DETAIL_MAX", 25)
DETAIL_REPLIES = getattr(C, "SOCIAL_DETAIL_REPLIES", 5)
DETAIL_DELAY = 1.5
_UI_NOISE = ("登入", "登錄", "註冊", "忘記密碼", "版權所有", "Copyright", "免責聲明", "私隱政策", "回覆主題", "發表回覆",
             "返回列表", "上一主題", "下一主題", "加入書籤", "分享到", "Powered by", "敬請留言者自律", "保留刪除",
             "下一篇文章", "上一篇文章", "我要放盤")
_NAV_WORDS = ("租樓", "租屋", "成交", "居屋", "村屋", "放盤", "睇樓", "買樓")
_BIO_WORDS = ("創辦人", "會長", "總裁", "版主", "群主", "顧問", "主席", "董事", "發起人", "專欄作家", "分析師")


def is_relevant(title: str) -> bool:
    if not _REL:
        return True
    t = to_trad(title or "").lower()
    return any(k in t for k in _REL)


def _item(platform, channel, title, text, date, replies=0, likes=0, url="", kind="post", quotes=None, depth="title"):
    return {"platform": platform, "channel": channel, "title": short(title, 120), "text": sanitize_quote(text, 120),
            "date": date.strftime("%Y-%m-%d") if date else None, "replies": int(replies or 0), "likes": int(likes or 0),
            "url": url, "kind": kind, "quotes": quotes or [], "depth": depth, "analysisText": ""}


# ------------------------------------------------------------------ 列表页
def fetch_discuz(src: dict) -> list[dict]:
    from bs4 import BeautifulSoup
    out = []
    for p in range(1, src.get("pages", 1) + 1):
        url = src["url"] + (f"&page={p}" if p > 1 else "")
        soup = BeautifulSoup(http_get(url), "html.parser")
        for tb in soup.select("tbody[id^=normalthread_]"):
            a = tb.select_one("a.xst") or tb.select_one("th a[href*='thread']")
            if not a:
                continue
            nums = tb.select("td.num a, td.num em")
            replies = num(nums[0].get_text()) if nums else 0
            views = num(nums[1].get_text()) if len(nums) > 1 else 0
            by = tb.select("td.by")
            post_date = None
            if by:   # 第一个 td.by 是发帖人+发帖日期
                sp = by[0].select_one("em span[title]")
                post_date = parse_date(sp.get("title") if sp else by[0].get_text(" "))
            out.append(_item(src["platform"], src["name"], a.get_text(strip=True), "", post_date, replies, (views or 0) / 50,
                             urllib.parse.urljoin(url, a.get("href", ""))))
    return out


def fetch_generic_table(src: dict) -> list[dict]:
    from bs4 import BeautifulSoup
    out, seen = [], set()
    for p in range(1, src.get("pages", 1) + 1):
        url = src["url"] if p == 1 else src["url"] + ("&" if "?" in src["url"] else "?") + f"{src.get('page_param', 'page')}={p}"
        soup = BeautifulSoup(http_get(url), "html.parser")
        for tr in soup.select("tr"):
            if tr.find("tr"):            # 只要最内层的行（外层整张表会令数字错乱）
                continue
            a = next((x for x in tr.select("a[href]") if len(x.get_text(strip=True)) >= 4
                      and re.search(r"thread|tid=|topic|forum_content|forum\.php\?.*id", x.get("href", ""))
                      and not any(s in x.get("href", "") for s in _SKIP)), None)
            if not a:
                continue
            href = urllib.parse.urljoin(url, a["href"])
            if href in seen:
                continue
            seen.add(href)
            title = a.get_text(strip=True)
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td", recursive=False)] or \
                    [td.get_text(" ", strip=True) for td in tr.select("td")]
            rest = [c for c in cells if title not in c]
            all_dates = [parse_date(m) for m in re.findall(r"20\d{2}-\d{2}-\d{2}", " ".join(rest))]
            all_dates = [d for d in all_dates if d]
            post_date = min(all_dates) if all_dates else None       # 发帖日期（最早那个）
            last_reply = max(all_dates) if all_dates else None
            pure_nums = [int(c.replace(",", "")) for c in rest if re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", c.strip())]
            replies = pure_nums[0] if len(pure_nums) >= 2 else 0
            views = pure_nums[-1] if pure_nums else 0
            it = _item(src["platform"], src["name"], title, "", post_date, replies, views / 50, href)
            it["lastReply"] = last_reply.strftime("%Y-%m-%d") if last_reply else None
            out.append(it)
    return out


# ------------------------------------------------------------------ 帖子正文 + 回复
POST_SELECTORS = ["td.t_f", "div.t_fsz", "div.postmessage", "div.post-content", "div.message", "div.content-body"]


def _is_noise(t: str) -> bool:
    if any(n in t for n in _UI_NOISE):
        return True
    if sum(t.count(w) for w in _NAV_WORDS) >= 4:            # 网站导航/区域列表
        return True
    if len(t) < 90 and sum(w in t for w in _BIO_WORDS) >= 1 and not re.search(r"[。！？]", t):   # 作者简介
        return True
    return False


def extract_post(html: str) -> tuple[str, list[str]]:
    """返回 (正文, [回复…])。Discuz 论坛：第 1 楼=正文，其后=回复；其他（专栏文章）：所有段落合并为正文，无回复。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "iframe"]):
        t.decompose()
    for sel in POST_SELECTORS:
        found = [x.get_text(" ", strip=True) for x in soup.select(sel)]
        found = [f for f in found if len(f) >= 6 and not _is_noise(f)]
        if found:
            return found[0], found[1:1 + DETAIL_REPLIES]
    blocks = []
    for el in soup.find_all(["td", "div", "p", "li"]):
        if el.find(["td", "div", "p", "li", "table"]):
            continue
        t = el.get_text(" ", strip=True)
        if len(t) >= (15 if el.name == "p" else 30) and not _is_noise(t):
            blocks.append(t)
    blocks = list(dict.fromkeys(blocks))
    return (" ".join(blocks[:12]), []) if blocks else ("", [])


def enrich_with_detail(items: list[dict], debug: list) -> int:
    cand = sorted([i for i in items if i["kind"] == "post" and i["depth"] == "title" and i["url"]], key=lambda i: -weight(i))
    ok = 0
    for i in cand[:DETAIL_MAX]:
        try:
            body, replies = extract_post(http_get(i["url"], timeout=20))
            if len(debug) < 5:
                debug.append({"url": i["url"], "title": i["title"], "body": body[:400], "replies": [r[:150] for r in replies]})
            if body:
                i["analysisText"] = body[:2000] + " " + " ".join(r[:300] for r in replies)
                i["text"] = sanitize_quote(body, 120)
                i["quotes"] = [{"text": sanitize_quote(r)} for r in replies[:4]]
                i["depth"] = "body+replies" if replies else "body"
                ok += 1
        except Exception as exc:
            debug.append({"url": i["url"], "error": str(exc)[:150]})
        time.sleep(DETAIL_DELAY)
    return ok


# ------------------------------------------------------------------ 其他来源
def fetch_reddit(src: dict) -> list[dict]:
    import datetime as dt
    data = http_get(src["url"], as_json=True)
    out = []
    for ch in data.get("data", {}).get("children", []):
        p = ch.get("data", {})
        d = dt.datetime.fromtimestamp(p.get("created_utc", 0), tz=now_hkt().tzinfo)
        it = _item("Reddit", src["name"], p.get("title", ""), p.get("selftext", ""), d, p.get("num_comments", 0),
                   p.get("score", 0), "https://www.reddit.com" + p.get("permalink", ""), depth="body" if p.get("selftext") else "title")
        it["analysisText"] = (p.get("selftext") or "")[:1500]
        out.append(it)
    return out


def fetch_telegram(channel: str) -> list[dict]:
    from bs4 import BeautifulSoup
    url = f"https://t.me/s/{channel}"
    soup = BeautifulSoup(http_get(url), "html.parser")
    out = []
    for m in soup.select(".tgme_widget_message"):
        t = m.select_one(".tgme_widget_message_text")
        if not t:
            continue
        tm = m.select_one("time[datetime]")
        views = m.select_one(".tgme_widget_message_views")
        v = views.get_text() if views else "0"
        v = num(v) * (1000 if "K" in v else 1) if num(v) else 0
        link = m.get("data-post", "")
        text = t.get_text(" ", strip=True)
        it = _item("Telegram", f"Telegram @{channel}", text[:60], text, parse_date(tm["datetime"]) if tm else None,
                   0, v / 100, f"https://t.me/{link}" if link else url, depth="full")
        it["analysisText"] = text[:1500]
        out.append(it)
    return out


def fetch_youtube() -> list[dict]:
    key = os.environ.get("YT_API_KEY")
    if not key:
        raise RuntimeError("未设置 YT_API_KEY，跳过（可选功能）")
    after = days_ago(C.SOCIAL_DAYS).strftime("%Y-%m-%dT%H:%M:%SZ")
    vids = {}
    for q in C.YOUTUBE_QUERIES:
        u = ("https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode({
            "part": "snippet", "type": "video", "q": q, "regionCode": "HK", "relevanceLanguage": "zh-Hant",
            "publishedAfter": after, "order": "viewCount", "maxResults": 10, "key": key}))
        for it in http_get(u, as_json=True, check_robots=False).get("items", []):
            vids[it["id"]["videoId"]] = it["snippet"]
    out = []
    for vid, sn in list(vids.items())[: C.YOUTUBE_MAX_VIDEOS]:
        u = ("https://www.googleapis.com/youtube/v3/commentThreads?" + urllib.parse.urlencode({
            "part": "snippet", "videoId": vid, "maxResults": C.YOUTUBE_COMMENTS_PER_VIDEO, "order": "relevance",
            "textFormat": "plainText", "key": key}))
        try:
            cm = http_get(u, as_json=True, check_robots=False).get("items", [])
        except Exception:
            cm = []
        quotes = []
        for c in cm:
            s = c["snippet"]["topLevelComment"]["snippet"]
            txt = s.get("textDisplay", "")
            it = _item("YouTube", f"YouTube · {short(sn.get('channelTitle', ''), 20)}", short(txt, 60), txt,
                       parse_date(s.get("publishedAt")), c["snippet"].get("totalReplyCount", 0), s.get("likeCount", 0),
                       f"https://www.youtube.com/watch?v={vid}", kind="comment", depth="full")
            it["analysisText"] = txt[:800]
            out.append(it)
            quotes.append({"text": sanitize_quote(txt), "likes": s.get("likeCount", 0)})
        quotes.sort(key=lambda q: -q["likes"])
        out.append(_item("YouTube", f"YouTube · {short(sn.get('channelTitle', ''), 20)}", strip_html(sn.get("title", "")),
                         sn.get("description", ""), parse_date(sn.get("publishedAt")), len(cm), 0,
                         f"https://www.youtube.com/watch?v={vid}", kind="video", quotes=quotes[:4], depth="body+replies"))
    return out


def fetch_playwright_titles(src: dict) -> list[dict]:
    import asyncio
    from playwright.async_api import async_playwright

    async def go():
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True, **({"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}))
            pg = await b.new_page(locale="zh-HK")
            await pg.goto(src["url"], wait_until="domcontentloaded", timeout=45000)
            try:
                await pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            body = (await pg.evaluate("document.body.innerText.slice(0,3000)")).lower()
            if any(k in body for k in ("verify you are human", "captcha", "請完成驗證")):
                await b.close()
                raise RuntimeError("出现验证页，按规则跳过")
            links = await pg.evaluate("""() => Array.from(document.querySelectorAll('a[href*="/thread/"]'))
                .map(a => ({t: a.innerText.trim(), h: a.href})).filter(x => x.t.length >= 4).slice(0, 80)""")
            await b.close()
            return links
    links = asyncio.run(go())
    return [_item(src["platform"], src["name"], l["t"].split("\n")[0], "", now_hkt(), 0, 0, l["h"]) for l in links]


def load_manual() -> list[dict]:
    out = []
    folder = INBOX_DIR / "social"
    if not folder.exists():
        return out
    for f in sorted(folder.glob("*")):
        try:
            if f.suffix.lower() == ".csv":
                for r in csv.DictReader(f.read_text(encoding="utf-8-sig").splitlines()):
                    if not (r.get("title") or r.get("text")):
                        continue
                    pf = (r.get("platform") or "Manual").strip()
                    it = _item(pf, r.get("channel") or PLATFORM_LABEL.get(pf, pf), r.get("title") or short(r.get("text", ""), 60),
                               r.get("text", ""), parse_date(r.get("date")) or now_hkt(), num(r.get("replies")) or 0,
                               num(r.get("likes")) or 0, r.get("url", ""), kind="manual", depth="full")
                    it["analysisText"] = (r.get("text") or "")[:1500]
                    out.append(it)
            elif f.suffix.lower() == ".txt":
                pf = re.split(r"[_\-.]", f.stem)[0]
                pf = {"xiaohongshu": "Xiaohongshu", "xhs": "Xiaohongshu", "facebook": "Facebook", "fb": "Facebook",
                      "whatsapp": "WhatsApp", "wechat": "WeChat"}.get(pf.lower(), "Manual")
                for block in re.split(r"\n\s*\n", f.read_text(encoding="utf-8")):
                    block = block.strip()
                    if len(block) < 6:
                        continue
                    it = _item(pf, PLATFORM_LABEL.get(pf, pf), short(block, 60), block, parse_date(block[:30]) or now_hkt(),
                               kind="manual", depth="full")
                    it["analysisText"] = block[:1500]
                    out.append(it)
        except Exception as exc:
            log("social", f"WARN 手动文件 {f.name}: {exc}")
    return out


def curated_items(snapshot: dict) -> list[dict]:
    out, seen = [], set()
    for t in snapshot.get("deep_sentiment_threads", []):
        if t.get("origin") == "auto":
            continue
        for c in t.get("comments", []):
            key = (t.get("title", ""), c.get("quote", ""))
            if key in seen:
                continue
            seen.add(key)
            it = _item("Curated", t.get("channel", "人工整理"), t.get("title", ""), c.get("quote", ""),
                       parse_date(t.get("date") or CURATED_DATE), 0, 0, t.get("url", ""), kind="curated", depth="full")
            it["analysisText"] = c.get("quote", "")
            out.append(it)
    return out


# ------------------------------------------------------------------ 分析
def weight(it) -> float:
    w = 1 + math.log1p(max(0, min(it.get("replies", 0), 500)) + max(0, min(it.get("likes", 0), 2000)) / 5)
    return w * (0.5 if it.get("depth") == "title" else 1.0)


def aggregate(items: list[dict], news_idx, ind: dict) -> dict:
    for it in items:
        a = analyse(f"{it['title']} {it.get('analysisText') or it['text']}")
        it.update({"score": a["score"], "stance": a["stance"], "topics": a["topics"], "hits": (a["bull"] + a["bear"])[:5]})
    scored = [i for i in items if i["kind"] != "video"]
    if not scored:
        return {"index": None, "n": 0}

    def idx(xs):
        w = sum(weight(i) for i in xs)
        return round(sum(weight(i) * i["score"] for i in xs) / w * 100, 1) if w else None

    def dist(xs):
        c = Counter(i["stance"] for i in xs)
        n = len(xs) or 1
        return {k: round(c.get(k, 0) / n * 100) for k in ("bull", "bear", "neutral")}

    by_src, by_topic, weeks = defaultdict(list), defaultdict(list), defaultdict(list)
    for i in scored:
        by_src[i["platform"]].append(i)
        for t in i["topics"]:
            by_topic[t].append(i)
        d = parse_date(i["date"])
        if d:
            weeks[f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"].append(i)
    terms = Counter(h for i in scored for h in i["hits"])
    overall = idx(scored)
    depth = Counter(i.get("depth", "title") for i in scored)
    sources = sorted([{"platform": k, "label": PLATFORM_LABEL.get(k, k), "n": len(v), "index": idx(v), **dist(v),
                       "readBody": sum(1 for i in v if i.get("depth") != "title")}
                      for k, v in by_src.items()], key=lambda x: -x["n"])
    topics = sorted([{"key": k, "label": TOPICS[k][0], "n": len(v), "share": round(len(v) / len(scored) * 100),
                      "index": idx(v), **dist(v)} for k, v in by_topic.items()], key=lambda x: -x["n"])
    rep = sorted(scored, key=lambda i: -weight(i))

    def card(i):
        c = {k: i[k] for k in ("platform", "channel", "title", "text", "date", "replies", "url", "stance", "score", "hits")}
        c["depth"] = DEPTH_LABEL.get(i.get("depth"), "")
        return c

    return {
        "index": overall, "n": len(scored), "dist": dist(scored), "sources": sources, "topics": topics,
        "depth": {DEPTH_LABEL.get(k, k): v for k, v in depth.items()},
        "trend": [{"week": w, "index": idx(v), "n": len(v)} for w, v in sorted(weeks.items())][-8:],
        "topTerms": terms.most_common(20),
        "representative": {"bull": [card(i) for i in rep if i["stance"] == "bull"][:6],
                           "bear": [card(i) for i in rep if i["stance"] == "bear"][:6]},
        "insights": build_insights(overall, sources, topics, news_idx, ind, len(scored), depth),
    }


def _mood(x):
    if x is None:
        return "样本不足"
    return ("明显乐观" if x >= 30 else "偏乐观" if x >= 10 else "中性偏好" if x >= 3 else
            "中性偏淡" if x > -10 else "偏悲观" if x > -30 else "明显悲观")


def build_insights(overall, sources, topics, news_idx, ind, n, depth) -> list[str]:
    title_only = depth.get("title", 0)
    out = [f"整体网民情绪指数 {overall:+.0f}（{_mood(overall)}），基于近 {C.SOCIAL_DAYS} 天 {n} 条帖文/评论；"
           f"其中 {n - title_only} 条读了正文或评论，{title_only} 条只有标题（权重减半）。"]
    if any(s["platform"] == "PropertyHK" for s in sources):
        out.append("宅谷地产论坛的帖子多为地产专栏作者文章，反映的是评论员观点，不完全等于一般买家/租客的感受。")
    if any(s["platform"] == "Curated" for s in sources):
        out.append("统计中包含人工整理样本（固定于 2026-10-16，30 天后自动退出）。")
    big = [t for t in topics if t["n"] >= 3]
    if big:
        worst = min(big, key=lambda t: t["index"] or 0)
        best = max(big, key=lambda t: t["index"] or 0)
        out.append(f"讨论最多的是「{topics[0]['label']}」（占 {topics[0]['share']}%）；最悲观的话题是「{worst['label']}」"
                   f"（{worst['index']:+.0f}，看淡占 {worst['bear']}%），最乐观的是「{best['label']}」（{best['index']:+.0f}）。")
    if news_idx is not None and overall is not None:
        gap = overall - news_idx
        if gap <= -15:
            out.append(f"网民比媒体更悲观（网民 {overall:+.0f} vs 新闻 {news_idx:+.0f}）。")
        elif gap >= 15:
            out.append(f"网民比媒体更乐观（网民 {overall:+.0f} vs 新闻 {news_idx:+.0f}）。")
        else:
            out.append(f"网民与媒体情绪大致同步（网民 {overall:+.0f} vs 新闻 {news_idx:+.0f}）。")
    rvd = (ind or {}).get("rvdPrice", {})
    if rvd.get("yoy") is not None and overall is not None:
        if rvd["yoy"] > 3 and overall < 0:
            out.append(f"官方售价指数按年 {rvd['yoy']:+.1f}%，但网民仍偏淡：价格回升未转化为大众信心。")
        elif rvd["yoy"] < -3 and overall > 0:
            out.append(f"官方售价指数按年 {rvd['yoy']:+.1f}%，网民却偏乐观：情绪可能领先于数据。")
    if n < 30:
        out.append("⚠️ 样本少于 30 条，结论只作方向参考；可在 inbox/social 导入更多小红书 / Facebook 群组内容。")
    return out


def llm_summary(agg: dict, items: list[dict]) -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    import urllib.request
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", C.LLM_DEFAULT_MODEL)
    lines = [f"[{i['platform']}] {i['title']} — {short(i.get('analysisText') or '', 200)}"
             for i in sorted(items, key=lambda i: -weight(i))[:60]]
    prompt = ("你是香港楼市研究员。根据以下网民讨论与专栏摘录，用简体中文写 5 条要点，剖析大众对楼市的真实感知："
              "主要担忧、主要期待、不同人群差异、与官方数据/媒体的落差、对未来 3–12 个月置业意愿的含义。每条≤60字，不要编造数据。\n\n"
              f"统计：{json.dumps({k: agg[k] for k in ('index', 'n', 'dist', 'topics', 'sources')}, ensure_ascii=False)[:3000]}\n\n帖文：\n" + "\n".join(lines))
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        log("social", f"WARN LLM 摘要失败：{exc}")
        return None


def build_threads(items: list[dict], snapshot: dict) -> list[dict]:
    auto, seen = [], set()
    posts = sorted([i for i in items if i["kind"] in ("post", "video", "manual")], key=lambda i: -weight(i))
    for i in posts:
        if i["title"] in seen:
            continue
        seen.add(i["title"])
        comments = []
        if i["text"] and i["text"] != i["title"] and i["kind"] == "post":
            a = analyse(i.get("analysisText") or i["text"])
            comments.append({"author": "正文摘录", "side": STANCE_LABEL[a["stance"]], "quote": i["text"]})
        for q in (i.get("quotes") or [])[:4]:
            a = analyse(q["text"])
            comments.append({"author": f"回复（{PLATFORM_LABEL.get(i['platform'], i['platform'])}）",
                             "side": STANCE_LABEL[a["stance"]], "quote": q["text"]})
        hits = "、".join(i.get("hits", [])[:5]) or "无明显情绪词"
        tl = "、".join(TOPICS[t][0] for t in i.get("topics", [])[:3]) or "未归类"
        dl = DEPTH_LABEL.get(i.get("depth"), "")
        auto.append({
            "id": "AUTO-" + hashlib.md5((i["url"] + i["title"]).encode()).hexdigest()[:10], "origin": "auto",
            "source": i["platform"], "depth": dl,
            "channel": f"{PLATFORM_LABEL.get(i['platform'], i['channel'])} · {i['replies']} 回复 · 发帖 {i['date'] or ''} · {dl}",
            "date": i["date"], "stance": i["stance"], "stanceBadge": f"{STANCE_LABEL[i['stance']]}（{i['score']:+.2f}）",
            "title": i["title"], "url": i["url"], "topicTags": [TOPICS[t][0] for t in i.get("topics", [])],
            "comments": comments,
            "expertFactCheck": (f"【自动分析 · {dl}】话题：{tl}；情绪命中词：{hits}。"
                                + ("仅凭标题判断，可信度低，请点开原帖。" if i.get("depth") == "title"
                                   else "基于正文/回复的关键词规则，不理解反讽，请点开原帖核实语境。")),
        })
        if len(auto) >= 24:
            break
    curated, ckeys = [], set()
    for t in snapshot.get("deep_sentiment_threads", []):
        if t.get("origin") == "auto" or t.get("title") in ckeys:
            continue
        ckeys.add(t.get("title"))
        t.setdefault("origin", "curated")
        t.setdefault("date", CURATED_DATE)
        curated.append(t)
    return auto + curated


def run(snapshot: dict, *, offline: bool, discover: bool = False) -> dict:
    items, errors, counts, detail_debug, forum_items = [], [], {}, [], []
    cutoff = days_ago(C.SOCIAL_DAYS).strftime("%Y-%m-%d")
    if not offline:
        for src in C.SOCIAL_SOURCES:
            if src.get("enabled") is False:
                continue
            fn = {"discuz": fetch_discuz, "generic_table": fetch_generic_table, "reddit": fetch_reddit,
                  "playwright_titles": fetch_playwright_titles}[src["kind"]]
            try:
                got = fn(src)
                rel = [g for g in got if is_relevant(g["title"])]
                recent = [g for g in rel if (g["date"] or "9999") >= cutoff]
                if src["kind"] in ("discuz", "generic_table"):
                    forum_items += recent
                items += recent
                counts[src["name"]] = f"{len(recent)}/{len(rel)}/{len(got)}（30天内/楼市相关/全部）"
                log("social", f"{src['name']}: 30天内 {len(recent)} / 楼市相关 {len(rel)} / 全部 {len(got)}")
            except Exception as exc:
                errors.append(f"{src['name']}: {str(exc)[:200]}")
                log("social", f"WARN {src['name']}: {exc}")
        n_ok = enrich_with_detail(forum_items, detail_debug)
        counts["读取正文"] = f"{n_ok}/{min(len(forum_items), DETAIL_MAX)}"
        for ch in C.TELEGRAM_PUBLIC_CHANNELS:
            try:
                got = [g for g in fetch_telegram(ch) if is_relevant(g["text"])]
                items += got
                counts[f"Telegram @{ch}"] = len(got)
            except Exception as exc:
                errors.append(f"Telegram {ch}: {str(exc)[:150]}")
        try:
            got = fetch_youtube()
            items += got
            counts["YouTube"] = len(got)
        except Exception as exc:
            errors.append(f"YouTube: {str(exc)[:150]}")
    manual = load_manual()
    counts["手动导入"] = len(manual)
    cur = curated_items(snapshot)
    counts["人工整理样本"] = len(cur)
    items = [i for i in items + manual + cur if (i["date"] or "9999") >= cutoff]
    news_idx = (snapshot.get("news_meta") or {}).get("index")
    agg = aggregate(items, news_idx, snapshot.get("macro_indicators", {}))
    agg["llmSummary"] = llm_summary(agg, items) if agg.get("n") else None
    if discover:
        DEBUG_DIR.mkdir(exist_ok=True)
        dump = [{**i, "analysisText": (i.get("analysisText") or "")[:300]} for i in items[:300]]
        (DEBUG_DIR / "social_items.json").write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
        (DEBUG_DIR / "social_detail_samples.json").write_text(json.dumps(detail_debug, ensure_ascii=False, indent=1), encoding="utf-8")
    agg.update({"window": f"{cutoff} – {now_hkt():%Y-%m-%d}", "counts": counts, "errors": errors,
                "notes": "论坛帖按发帖日期计入近 30 天、且标题与楼市相关者；热度最高的前 %d 帖会读取正文（论坛另读前几条回复），"
                         "其余仅凭标题（权重减半）。小红书、Facebook 群组、WhatsApp/微信群请用手动导入。" % DETAIL_MAX})
    snapshot["sentiment_summary"] = agg
    snapshot["deep_sentiment_threads"] = build_threads(items, snapshot)
    for i in items:
        i.pop("analysisText", None)
    return {"items": len(items), "index": agg.get("index"), "detailRead": counts.get("读取正文"), "errors": errors}
