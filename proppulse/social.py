# -*- coding: utf-8 -*-
"""
社区舆情（v7.1）：只抓公开、无需登录即可浏览的页面 / 官方 API；不保存用户名，引用 ≤90 字。
  自动：宅谷地产论坛、亲子王国（Discuz）、Telegram 公开频道预览、YouTube Data API（需 YT_API_KEY）
  手动：inbox/social/*.csv|*.txt —— 小红书、Facebook 群组、WhatsApp/微信群等需要登录或禁止抓取的平台

v7.1 修正（根据第一次 discover）：
  * 去掉「忘記密碼」等非帖子链接
  * 回复数不再误读标题里的数字（如「茶餐廳手記（#1132）」被当成 1132 回复）
  * 只有标题与楼市相关的论坛帖才计入舆情指数（宅谷论坛有大量时事/生活帖）
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import urllib.parse
from collections import Counter, defaultdict

from . import config as C
from .common import DEBUG_DIR, INBOX_DIR, days_ago, http_get, log, now_hkt, num, parse_date, short, strip_html
from .lexicon import STANCE_LABEL, TOPICS, analyse, sanitize_quote, to_trad

PLATFORM_LABEL = {
    "BabyKingdom": "亲子王国", "PropertyHK": "宅谷地产论坛", "Reddit": "Reddit", "Telegram": "Telegram 公开频道",
    "YouTube": "YouTube 评论", "LIHKG": "连登 LIHKG", "Discuss": "香港讨论区", "Xiaohongshu": "小红书（手动导入）",
    "Facebook": "Facebook（手动导入）", "WhatsApp": "WhatsApp 群（手动导入）", "WeChat": "微信群（手动导入）",
    "Manual": "其他手动导入", "Curated": "人工整理样本",
}
_REL = [to_trad(k).lower() for k in getattr(C, "SOCIAL_RELEVANCE", [])]
_SKIP = getattr(C, "SOCIAL_SKIP_URL", [])


def is_relevant(title: str) -> bool:
    if not _REL:
        return True
    t = to_trad(title or "").lower()
    return any(k in t for k in _REL)


def _item(platform, channel, title, text, date, replies=0, likes=0, url="", kind="post", quotes=None):
    return {"platform": platform, "channel": channel, "title": short(title, 120), "text": sanitize_quote(text, 120),
            "date": date.strftime("%Y-%m-%d") if date else None, "replies": int(replies or 0), "likes": int(likes or 0),
            "url": url, "kind": kind, "quotes": quotes or []}


# ------------------------------------------------------------------ 各来源
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
            spans = [s.get("title") for s in tb.select("td.by em span[title]")]
            dates = spans or [s.get_text() for s in tb.select("td.by em")]
            d = max([x for x in (parse_date(t) for t in dates) if x], default=None)
            out.append(_item(src["platform"], src["name"], a.get_text(strip=True), "", d, replies, (views or 0) / 50,
                             urllib.parse.urljoin(url, a.get("href", ""))))
    return out


def fetch_generic_table(src: dict) -> list[dict]:
    from bs4 import BeautifulSoup
    out, seen = [], set()
    for p in range(1, src.get("pages", 1) + 1):
        url = src["url"] if p == 1 else src["url"] + ("&" if "?" in src["url"] else "?") + f"{src.get('page_param', 'page')}={p}"
        soup = BeautifulSoup(http_get(url), "html.parser")
        for tr in soup.select("tr"):
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
            cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
            rest = " ".join(c for c in cells if title not in c)          # 去掉标题所在格，避免误读标题里的数字
            dates = [parse_date(m) for m in re.findall(r"20\d{2}-\d{2}-\d{2}", rest)]
            d = max([x for x in dates if x], default=None)
            ints = [int(x.replace(",", "")) for x in
                    re.findall(r"(?<![\d:-])\d{1,3}(?:,\d{3})+(?![\d:-])|(?<![\d:-])\d{1,7}(?![\d:-])", re.sub(r"20\d{2}-\d{2}-\d{2}(\s+\d{2}:\d{2})?", " ", rest))]
            replies = ints[-2] if len(ints) >= 2 else 0
            views = ints[-1] if ints else 0
            out.append(_item(src["platform"], src["name"], title, "", d, replies, views / 50, href))
    return out


def fetch_reddit(src: dict) -> list[dict]:
    import datetime as dt
    data = http_get(src["url"], as_json=True)
    out = []
    for ch in data.get("data", {}).get("children", []):
        p = ch.get("data", {})
        d = dt.datetime.fromtimestamp(p.get("created_utc", 0), tz=now_hkt().tzinfo)
        out.append(_item("Reddit", src["name"], p.get("title", ""), p.get("selftext", ""), d,
                         p.get("num_comments", 0), p.get("score", 0), "https://www.reddit.com" + p.get("permalink", "")))
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
        out.append(_item("Telegram", f"Telegram @{channel}", text[:60], text, parse_date(tm["datetime"]) if tm else None,
                         0, v / 100, f"https://t.me/{link}" if link else url))
    return out


def fetch_youtube() -> list[dict]:
    key = os.environ.get("YT_API_KEY")
    if not key:
        raise RuntimeError("未设置 YT_API_KEY，跳过")
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
            out.append(_item("YouTube", f"YouTube · {short(sn.get('channelTitle', ''), 20)}", short(txt, 60), txt,
                             parse_date(s.get("publishedAt")), c["snippet"].get("totalReplyCount", 0), s.get("likeCount", 0),
                             f"https://www.youtube.com/watch?v={vid}", kind="comment"))
            quotes.append({"text": sanitize_quote(txt), "likes": s.get("likeCount", 0)})
        quotes.sort(key=lambda q: -q["likes"])
        out.append(_item("YouTube", f"YouTube · {short(sn.get('channelTitle', ''), 20)}", strip_html(sn.get("title", "")),
                         sn.get("description", ""), parse_date(sn.get("publishedAt")), len(cm), 0,
                         f"https://www.youtube.com/watch?v={vid}", kind="video", quotes=quotes[:4]))
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
    """inbox/social/*.csv（列：date,platform,channel,title,text,replies,likes,url）或 *.txt（空行分隔，每段一帖；文件名首段 = 平台）。"""
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
                    out.append(_item(pf, r.get("channel") or PLATFORM_LABEL.get(pf, pf), r.get("title") or short(r.get("text", ""), 60),
                                     r.get("text", ""), parse_date(r.get("date")) or now_hkt(), num(r.get("replies")) or 0,
                                     num(r.get("likes")) or 0, r.get("url", ""), kind="manual"))
            elif f.suffix.lower() == ".txt":
                pf = re.split(r"[_\-.]", f.stem)[0]
                pf = {"xiaohongshu": "Xiaohongshu", "xhs": "Xiaohongshu", "facebook": "Facebook", "fb": "Facebook",
                      "whatsapp": "WhatsApp", "wechat": "WeChat"}.get(pf.lower(), "Manual")
                for block in re.split(r"\n\s*\n", f.read_text(encoding="utf-8")):
                    block = block.strip()
                    if len(block) < 6:
                        continue
                    d = parse_date(block[:30]) or now_hkt()
                    out.append(_item(pf, PLATFORM_LABEL.get(pf, pf), short(block, 60), block, d, kind="manual"))
        except Exception as exc:
            log("social", f"WARN 手动文件 {f.name}: {exc}")
    return out


def curated_items(snapshot: dict) -> list[dict]:
    out = []
    for t in snapshot.get("deep_sentiment_threads", []):
        if t.get("origin") == "auto":
            continue
        for c in t.get("comments", []):
            out.append(_item("Curated", t.get("channel", "人工整理"), t.get("title", ""), c.get("quote", ""),
                             parse_date(t.get("date")) or now_hkt(), 0, 0, t.get("url", ""), kind="curated"))
    return out


# ------------------------------------------------------------------ 分析
def weight(it) -> float:
    return 1 + math.log1p(max(0, it.get("replies", 0)) + max(0, it.get("likes", 0)) / 5)


def aggregate(items: list[dict], news_idx, ind: dict) -> dict:
    for it in items:
        a = analyse(f"{it['title']} {it['text']}")
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

    by_src = defaultdict(list)
    for i in scored:
        by_src[i["platform"]].append(i)
    by_topic = defaultdict(list)
    for i in scored:
        for t in i["topics"]:
            by_topic[t].append(i)
    weeks = defaultdict(list)
    for i in scored:
        d = parse_date(i["date"])
        if d:
            weeks[f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"].append(i)
    terms = Counter(h for i in scored for h in i["hits"])
    overall = idx(scored)

    sources = sorted([{"platform": k, "label": PLATFORM_LABEL.get(k, k), "n": len(v), "index": idx(v), **dist(v)}
                      for k, v in by_src.items()], key=lambda x: -x["n"])
    topics = sorted([{"key": k, "label": TOPICS[k][0], "n": len(v), "share": round(len(v) / len(scored) * 100),
                      "index": idx(v), **dist(v)} for k, v in by_topic.items()], key=lambda x: -x["n"])
    rep = sorted(scored, key=lambda i: -weight(i))

    def card(i):
        return {k: i[k] for k in ("platform", "channel", "title", "text", "date", "replies", "url", "stance", "score", "hits")}

    insights = build_insights(overall, sources, topics, news_idx, ind, len(scored))
    return {
        "index": overall, "n": len(scored), "dist": dist(scored), "sources": sources, "topics": topics,
        "trend": [{"week": w, "index": idx(v), "n": len(v)} for w, v in sorted(weeks.items())][-8:],
        "topTerms": terms.most_common(20),
        "representative": {"bull": [card(i) for i in rep if i["stance"] == "bull"][:6],
                           "bear": [card(i) for i in rep if i["stance"] == "bear"][:6]},
        "insights": insights,
    }


def _mood(x):
    if x is None:
        return "样本不足"
    return ("明显乐观" if x >= 30 else "偏乐观" if x >= 10 else "中性偏好" if x >= 3 else
            "中性偏淡" if x > -10 else "偏悲观" if x > -30 else "明显悲观")


def build_insights(overall, sources, topics, news_idx, ind, n) -> list[str]:
    out = [f"整体网民情绪指数 {overall:+.0f}（{_mood(overall)}），基于近 {C.SOCIAL_DAYS} 天 {n} 条帖文/评论，按回复与点赞加权。"]
    big = [t for t in topics if t["n"] >= 3]
    if big:
        worst = min(big, key=lambda t: t["index"] or 0)
        best = max(big, key=lambda t: t["index"] or 0)
        out.append(f"讨论最多的是「{topics[0]['label']}」（占 {topics[0]['share']}%）；最悲观的话题是「{worst['label']}」"
                   f"（{worst['index']:+.0f}，看淡占 {worst['bear']}%），最乐观的是「{best['label']}」（{best['index']:+.0f}）。")
    if news_idx is not None and overall is not None:
        gap = overall - news_idx
        if gap <= -15:
            out.append(f"网民比媒体更悲观（网民 {overall:+.0f} vs 新闻 {news_idx:+.0f}）：报道偏正面但大众未有同感，常见于「价格先行、信心滞后」阶段。")
        elif gap >= 15:
            out.append(f"网民比媒体更乐观（网民 {overall:+.0f} vs 新闻 {news_idx:+.0f}）：留意追高情绪，以及媒体是否在提示风险。")
        else:
            out.append(f"网民与媒体情绪大致同步（网民 {overall:+.0f} vs 新闻 {news_idx:+.0f}）。")
    rvd = (ind or {}).get("rvdPrice", {})
    if rvd.get("yoy") is not None and overall is not None:
        if rvd["yoy"] > 3 and overall < 0:
            out.append(f"官方售价指数按年 {rvd['yoy']:+.1f}%，但网民仍偏淡：实际价格回升未转化为大众信心，感知落后于数据。")
        elif rvd["yoy"] < -3 and overall > 0:
            out.append(f"官方售价指数按年 {rvd['yoy']:+.1f}%，网民却偏乐观：情绪领先或过度乐观，需观察成交能否跟上。")
    h = (ind or {}).get("hibor1m", {})
    if h.get("chg30d") is not None:
        rate_topic = next((t for t in topics if t["key"] == "rates"), None)
        if rate_topic:
            dirn = "上升" if h["chg30d"] > 0.05 else ("回落" if h["chg30d"] < -0.05 else "持平")
            out.append(f"1 个月 HIBOR 近 30 天{dirn}（{h['chg30d']:+.2f} 厘），网民对利率话题的情绪 {rate_topic['index']:+.0f}。")
    ss = [s for s in sources if s["n"] >= 5 and s["index"] is not None]
    if len(ss) >= 2:
        hi, lo = max(ss, key=lambda s: s["index"]), min(ss, key=lambda s: s["index"])
        if hi["index"] - lo["index"] >= 25:
            out.append(f"平台分化明显：{hi['label']} {hi['index']:+.0f} vs {lo['label']} {lo['index']:+.0f}，不同人群（用家/投资者/租客）感受差异大。")
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
    titles = [f"[{i['platform']}|{STANCE_LABEL[i['stance']]}] {i['title']}" for i in sorted(items, key=lambda i: -weight(i))[:80]]
    prompt = ("你是香港楼市研究员。根据以下网民讨论统计与帖文标题，用简体中文写 5 条要点，剖析大众对楼市的真实感知："
              "主要担忧、主要期待、不同人群差异、与官方数据/媒体的落差、对未来 3–12 个月置业意愿的含义。每条≤60字，不要编造数据。\n\n"
              f"统计：{json.dumps({k: agg[k] for k in ('index', 'n', 'dist', 'topics', 'sources')}, ensure_ascii=False)[:3000]}\n\n标题：\n" + "\n".join(titles))
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
    auto = []
    posts = [i for i in items if i["kind"] in ("post", "video", "manual")]
    posts.sort(key=lambda i: -weight(i))
    seen = set()
    for i in posts:
        if i["title"] in seen:
            continue
        seen.add(i["title"])
        quotes = i.get("quotes") or ([{"text": i["text"]}] if i["text"] and i["text"] != i["title"] else [])
        comments = []
        for q in quotes[:4]:
            a = analyse(q["text"])
            comments.append({"author": f"网民（{PLATFORM_LABEL.get(i['platform'], i['platform'])}）",
                             "side": STANCE_LABEL[a["stance"]], "quote": q["text"]})
        hits = "、".join(i.get("hits", [])[:5]) or "无明显情绪词"
        tl = "、".join(TOPICS[t][0] for t in i.get("topics", [])[:3]) or "未归类"
        auto.append({
            "id": "AUTO-" + hashlib.md5((i["url"] + i["title"]).encode()).hexdigest()[:10], "origin": "auto", "source": i["platform"],
            "channel": f"{i['channel']} · {i['replies']} 回复 · {i['date'] or ''}", "date": i["date"],
            "stance": i["stance"], "stanceBadge": f"{STANCE_LABEL[i['stance']]}（{i['score']:+.2f}）",
            "title": i["title"], "url": i["url"], "topicTags": [TOPICS[t][0] for t in i.get("topics", [])],
            "comments": comments,
            "expertFactCheck": f"【自动分析】话题：{tl}；情绪命中词：{hits}。仅基于标题/摘录的词典判断，请点开原帖核实语境。",
        })
        if len(auto) >= 24:
            break
    curated = [t for t in snapshot.get("deep_sentiment_threads", []) if t.get("origin") != "auto"]
    for t in curated:
        t.setdefault("origin", "curated")
    return auto + curated


def run(snapshot: dict, *, offline: bool, discover: bool = False) -> dict:
    items, errors, counts = [], [], {}
    if not offline:
        for src in C.SOCIAL_SOURCES:
            if src.get("enabled") is False:
                continue
            fn = {"discuz": fetch_discuz, "generic_table": fetch_generic_table, "reddit": fetch_reddit,
                  "playwright_titles": fetch_playwright_titles}[src["kind"]]
            try:
                got = fn(src)
                rel = [g for g in got if is_relevant(g["title"])]
                items += rel
                counts[src["name"]] = f"{len(rel)}/{len(got)}（楼市相关/全部）"
                log("social", f"{src['name']}: 楼市相关 {len(rel)} / 全部 {len(got)}")
            except Exception as exc:
                errors.append(f"{src['name']}: {str(exc)[:150]}")
                log("social", f"WARN {src['name']}: {exc}")
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
    items += manual
    cur = curated_items(snapshot)
    counts["人工整理样本"] = len(cur)
    cutoff = days_ago(C.SOCIAL_DAYS).strftime("%Y-%m-%d")
    items = [i for i in items + cur if (i["date"] or "9999") >= cutoff]
    if discover:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "social_items.json").write_text(json.dumps(items[:300], ensure_ascii=False, indent=1), encoding="utf-8")
    news_idx = (snapshot.get("news_meta") or {}).get("index")
    agg = aggregate(items, news_idx, snapshot.get("macro_indicators", {}))
    agg["llmSummary"] = llm_summary(agg, items) if agg.get("n") else None
    agg.update({"window": f"{cutoff} – {now_hkt():%Y-%m-%d}", "counts": counts, "errors": errors,
                "notes": "小红书、Facebook 群组、WhatsApp/微信群需登录且平台条款禁止自动抓取，请把内容导出/复制到 inbox/social "
                         "或在看板「手动导入」框粘贴；系统只保存≤120 字摘录，不保存用户名。论坛帖只计入标题与楼市相关者。"})
    snapshot["sentiment_summary"] = agg
    snapshot["deep_sentiment_threads"] = build_threads(items, snapshot)
    return {"items": len(items), "index": agg.get("index"), "errors": len(errors)}
