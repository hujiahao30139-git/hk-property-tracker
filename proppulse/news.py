# -*- coding: utf-8 -*-
"""近 30 天财经 / 楼市新闻：RSS 抓取 → 去重 → 分类 → 情绪 → 指数。只存标题、来源、日期、链接与 ≤120 字摘要。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter

from . import config as C
from .common import days_ago, http_get, log, parse_date, short, strip_html
from .lexicon import STANCE_LABEL, analyse, to_trad

CATEGORIES = [  # (key, label, keywords) —— 按顺序匹配第一个
    ("us_fed", "美联储与环球息口", ["聯儲", "联储", "美聯儲", "Fed", "美息", "美國加息", "美國減息", "鮑威爾"]),
    ("hk_mortgage", "香港利率与按揭", ["按揭", "HIBOR", "拆息", "最優惠利率", "P按", "H按", "定按", "金管局", "供樓", "銀行"]),
    ("mainland_policy", "内地政策与北水", ["內地", "内地", "人民幣", "人民币", "深圳", "大灣區", "北水", "人才", "高才", "優才"]),
    ("hk_supply", "土地供应与新盘", ["賣地", "卖地", "地皮", "招標", "新盤", "新盘", "價單", "開價", "一手", "貨尾", "落成", "市建局"]),
    ("hk_policy", "施政与房屋政策", ["施政報告", "施政报告", "辣招", "印花稅", "印花税", "房屋政策", "財政預算", "公屋", "居屋", "簡樸房"]),
    ("hk_rent", "租赁市场", ["租金", "租盤", "收租", "租務", "租客"]),
    ("hk_economy", "香港经济与就业", ["失業", "失业", "GDP", "經濟", "经济", "零售", "恒指", "股市", "裁員"]),
    ("hk_market", "二手成交与楼价", ["樓價", "楼价", "成交", "屋苑", "CCL", "指數", "二手", "差估署", "樓市", "楼市"]),
]
CATEGORY_LABEL = {k: lbl for k, lbl, _ in CATEGORIES}

LINKAGE = {
    "us_fed": "美息走向经联系汇率传导至 HIBOR，再影响 H 按实际供款；对 3–6 个月置业成本最敏感。",
    "hk_mortgage": "直接决定供款与「供平过租」门槛，是近期买家入市意愿的最强杠杆。",
    "mainland_policy": "内地买家与专才是近年新增需求主力，影响中小型单位与租务需求。",
    "hk_supply": "一手供应与开价策略压制或带动二手叫价，是 6–12 个月的价格天花板。",
    "hk_policy": "税费与房屋政策改变交易成本及换楼链条，常带来短期成交脉冲。",
    "hk_rent": "租金决定收租回报与「租转买」动力，是楼价的中期支撑。",
    "hk_economy": "就业与收入决定供款能力，是 1 年期楼价的底层基本面。",
    "hk_market": "反映即时成交与价格动量，是 3 个月走势最直接的信号。",
}


def classify(text: str) -> str:
    t = to_trad(text)
    for key, _, kws in CATEGORIES:
        if any(to_trad(k) in t for k in kws):
            return key
    return "hk_market"


def _text(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def parse_rss(xml_text: str, feed: dict) -> list[dict]:
    xml_text = re.sub(r"^[^<]+", "", xml_text)
    root = ET.fromstring(xml_text)
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    out = []
    for it in items:
        title = _text(it, "title") or _text(it, "{http://www.w3.org/2005/Atom}title")
        link = _text(it, "link")
        if not link:
            le = it.find("{http://www.w3.org/2005/Atom}link")
            link = le.get("href") if le is not None else ""
        date = _text(it, "pubDate") or _text(it, "{http://www.w3.org/2005/Atom}updated") or _text(it, "{http://purl.org/dc/elements/1.1/}date")
        desc = strip_html(_text(it, "description") or _text(it, "{http://www.w3.org/2005/Atom}summary"))
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else ""
        if not source and " - " in title:           # Google News 标题形如「标题 - 来源」
            title, source = title.rsplit(" - ", 1)
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        if desc.startswith(title[:15]):              # Google News 的 description 只是标题重复
            desc = ""
        out.append({"title": title.strip(), "url": link, "dateRaw": date, "desc": desc,
                    "source": source or feed["name"], "feed": feed["name"]})
    return out


def _norm(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", to_trad(t))[:22]


def build_items(raw: list[dict]) -> list[dict]:
    cutoff = days_ago(C.NEWS_DAYS)
    seen, out = set(), []
    for r in raw:
        d = parse_date(r.get("dateRaw"))
        if not d or d < cutoff or not r.get("title"):
            continue
        key = _norm(r["title"])
        if key in seen:
            continue
        seen.add(key)
        txt = r["title"] + " " + r.get("desc", "")
        a = analyse(txt)
        cat = classify(txt)
        hits = "、".join((a["bull"] + a["bear"])[:4])
        out.append({
            "date": d.strftime("%Y-%m-%d"), "category": cat, "categoryLabel": CATEGORY_LABEL[cat],
            "title": r["title"], "source": r["source"], "impact": a["stance"], "impactLabel": STANCE_LABEL[a["stance"]],
            "sentiment": a["score"], "topics": a["topics"],
            "summary": short(r.get("desc") or "（RSS 仅提供标题，请点「核验原文」阅读）", 120),
            "macroLinkage": f"【自动研判】{LINKAGE[cat]}" + (f" 命中词：{hits}。" if hits else ""),
            "url": r["url"], "feed": r["feed"], "origin": "rss",
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:C.NEWS_MAX]


def news_index(items: list[dict]) -> dict:
    """新闻情绪指数 −100…+100；越新的新闻权重越高（7 天半衰期）。"""
    if not items:
        return {"index": None, "n": 0}
    import math
    now = days_ago(0)
    num_, den = 0.0, 0.0
    for it in items:
        d = parse_date(it["date"])
        age = max(0.0, (now - d).days) if d else 15
        w = math.exp(-age / 10)
        num_ += w * (it.get("sentiment") or 0)
        den += w
    cats = Counter(i["category"] for i in items)
    stance = Counter(i["impact"] for i in items)
    return {"index": round(num_ / den * 100, 1) if den else None, "n": len(items),
            "byCategory": {CATEGORY_LABEL[k]: v for k, v in cats.most_common()},
            "byStance": dict(stance)}


def run(snapshot: dict, *, offline: bool) -> dict:
    raw, errors = [], []
    if not offline:
        kws = [to_trad(k) for k in C.NEWS_KEYWORDS]
        for feed in C.NEWS_FEEDS:
            try:
                xml = http_get(feed["url"], timeout=20)
                items = parse_rss(xml, feed)
                if feed.get("keyword_filter"):
                    items = [i for i in items if any(k in to_trad(i["title"] + i["desc"]) for k in kws)]
                raw += items
                log("news", f"{feed['name']}: {len(items)} 条")
            except Exception as exc:
                errors.append(f"{feed['name']}: {str(exc)[:120]}")
                log("news", f"WARN {feed['name']}: {exc}")
    fresh = build_items(raw)
    # 保留 30 天内的人工整理新闻（origin 缺省 = curated），与自动新闻合并
    cutoff = days_ago(C.NEWS_DAYS).strftime("%Y-%m-%d")
    curated = []
    for n in snapshot.get("recent_1m_news", []):
        if n.get("origin") == "rss":
            continue
        d = parse_date(n.get("date"))
        if d and d.strftime("%Y-%m-%d") >= cutoff:
            n.setdefault("origin", "curated")
            if "sentiment" not in n:
                n["sentiment"] = {"bull": 0.6, "bear": -0.6}.get(n.get("impact"), 0.0)
            curated.append(n)
    keys = {_norm(n["title"]) for n in curated}
    items = curated + [f for f in fresh if _norm(f["title"]) not in keys]
    if not fresh and not offline:
        # 全部源失败时保留上次的 RSS 新闻（仍限 30 天内）
        items += [n for n in snapshot.get("recent_1m_news", []) if n.get("origin") == "rss" and n.get("date", "") >= cutoff]
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    snapshot["recent_1m_news"] = items
    idx = news_index(items)
    snapshot["news_meta"] = {**idx, "window": f"{cutoff} – {days_ago(0):%Y-%m-%d}", "errors": errors,
                             "categories": [{"key": k, "label": l} for k, l, _ in CATEGORIES]}
    return {"fresh": len(fresh), "total": len(items), "index": idx["index"], "errors": len(errors)}
