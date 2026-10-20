# -*- coding: utf-8 -*-
"""
近 30 天财经 / 楼市新闻（v7.2）：RSS 抓取 → 相关度过滤 → 去重 → 分类 → 情绪 → 新闻情绪指数。
只存标题、来源、日期、链接与 ≤120 字摘要，不转载全文。

v7.2 修正（用户反馈「食環署誘蚊器指數」被纳入）：
  * 旧版只要标题/摘要含「樓」「息」等单字就收录，政府新闻公报几乎全部通过 → 改为「相关度打分」：
    标题必须含至少 1 个楼市核心词，或标题含利率/经济词且摘要含楼市核心词；命中噪音词（蚊、火警、天气…）一律剔除。
  * 分类改为按命中词数打分，标题权重 ×2；匹配不到任何类别的不再默认塞进「二手成交与楼价」。
  * 「自动研判」改为如实列出：分类依据、情绪命中词、该类新闻与楼价的一般关系；不再给人「已分析新闻内容」的错觉。
  * 每次运行都会用新规则重新审核已保存的自动新闻，旧的误收条目会被清走。
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections import Counter

from . import config as C
from .common import days_ago, http_get, log, parse_date, short, strip_html
from .lexicon import STANCE_LABEL, analyse, to_trad

# ---------------------------------------------------------------- 相关度
CORE = ["樓市", "樓價", "樓盤", "樓花", "住宅", "物業", "私樓", "私宅", "二手", "一手", "新盤", "屋苑", "按揭", "供樓", "首期",
        "印花稅", "租金", "租務", "租盤", "收租", "地皮", "賣地", "土地供應", "房屋", "公屋", "居屋", "劏房", "簡樸房",
        "差估署", "地產", "發展商", "呎價", "實用面積", "業主", "租客", "銀主", "負資產", "CCL", "中原城市領先指數",
        "樓宇買賣", "土地註冊處", "市建局", "重建", "強拍", "置業", "買樓", "賣樓", "上車", "辣招", "撤辣", "樓宇按揭"]
MACRO = ["HIBOR", "拆息", "最優惠利率", "息口", "加息", "減息", "聯儲局", "聯儲", "金管局", "利率", "銀行結餘", "總結餘",
         "失業率", "本地生產總值", "GDP", "經濟", "股市", "恒指", "通脹"]
NOISE = ["蚊", "誘蚊器", "火警", "罪案", "拘捕", "被捕", "交通意外", "天文台", "颱風", "暴雨", "天氣", "疫苗", "流感", "食環署",
         "衞生防護", "食物安全", "動物", "演唱會", "體育", "足球", "選舉", "旅遊", "郵票", "考試"]
_CORE_T = [to_trad(k).lower() for k in CORE]
_MACRO_T = [to_trad(k).lower() for k in MACRO]
_NOISE_T = [to_trad(k).lower() for k in NOISE]


def _hits(text: str, terms: list[str]) -> list[str]:
    t = to_trad(text or "").lower()
    return [k for k in terms if k in t]


def relevance(title: str, desc: str) -> dict:
    """返回 {ok, score, reason}。"""
    tc, tm, tn = _hits(title, _CORE_T), _hits(title, _MACRO_T), _hits(title, _NOISE_T)
    dc = _hits(desc, _CORE_T)
    score = 2 * len(tc) + len(tm) + 0.5 * min(len(dc), 4)
    if tn and len(tc) < 2:
        return {"ok": False, "score": score, "reason": f"标题含无关词「{tn[0]}」"}
    if tc:
        return {"ok": True, "score": score, "reason": "标题含楼市词：" + "、".join(tc[:4])}
    if tm and dc:
        return {"ok": True, "score": score, "reason": f"标题含宏观词「{tm[0]}」且摘要含楼市词「{dc[0]}」"}
    return {"ok": False, "score": score, "reason": "标题未出现楼市核心词"}


# ---------------------------------------------------------------- 分类
CATEGORIES = [  # (key, label, keywords, 与楼价的一般关系)
    ("us_fed", "美联储与环球息口", ["聯儲", "美聯儲", "Fed", "美息", "美國加息", "美國減息", "鮑威爾"],
     "美息经联系汇率传导至 HIBOR，再影响 H 按供款；对 3–6 个月置业成本最敏感。"),
    ("hk_mortgage", "香港利率与按揭", ["按揭", "HIBOR", "拆息", "最優惠利率", "P按", "H按", "定按", "金管局", "供樓", "息口", "加息", "減息"],
     "直接决定供款与「供平过租」门槛，是近期买家入市意愿的主要杠杆。"),
    ("mainland_policy", "内地政策与北水", ["內地", "人民幣", "深圳", "大灣區", "北水", "人才", "高才", "優才", "內地買家"],
     "内地买家与专才是近年新增需求来源，主要影响中小型单位与租务需求。"),
    ("hk_supply", "土地供应与新盘", ["賣地", "地皮", "招標", "新盤", "價單", "開價", "一手", "貨尾", "落成", "市建局", "發展商", "樓花"],
     "一手供应与开价策略影响二手叫价空间，是 6–12 个月价格的重要约束。"),
    ("hk_policy", "施政与房屋政策", ["施政報告", "辣招", "撤辣", "印花稅", "房屋政策", "財政預算", "公屋", "居屋", "簡樸房", "劏房"],
     "税费与房屋政策改变交易成本及换楼链条，常带来短期成交变化。"),
    ("hk_rent", "租赁市场", ["租金", "租盤", "收租", "租務", "租客", "租樓"],
     "租金决定收租回报与「租转买」动力，是楼价的中期支撑。"),
    ("hk_economy", "香港经济与就业", ["失業率", "本地生產總值", "GDP", "經濟", "零售", "恒指", "股市", "裁員"],
     "就业与收入决定供款能力，是 1 年期楼价的底层基本面。"),
    ("hk_market", "二手成交与楼价", ["樓價", "成交", "屋苑", "CCL", "指數", "二手", "差估署", "樓市", "呎價", "銀主", "負資產"],
     "反映即时成交与价格动量，是 3 个月走势最直接的信号。"),
]
CATEGORY_LABEL = {k: lbl for k, lbl, _, _ in CATEGORIES}
CATEGORY_LABEL["hk_property_general"] = "楼市综合"
LINKAGE = {k: note for k, _, _, note in CATEGORIES}
LINKAGE["hk_property_general"] = "与楼市相关，但未能归入特定类别；仅计入总体情绪。"


def classify(title: str, desc: str) -> tuple[str, list[str]]:
    best, best_s, best_hits = "hk_property_general", 0.0, []
    for key, _, kws, _ in CATEGORIES:
        th = _hits(title, [to_trad(k).lower() for k in kws])
        dh = _hits(desc, [to_trad(k).lower() for k in kws])
        s = 2 * len(th) + 0.5 * len(dh)
        if s > best_s:
            best, best_s, best_hits = key, s, th or dh
    return best, best_hits


# ---------------------------------------------------------------- RSS
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
        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        if desc.startswith(title[:15]):
            desc = ""
        out.append({"title": title.strip(), "url": link, "dateRaw": date, "desc": desc,
                    "source": source or feed["name"], "feed": feed["name"]})
    return out


def _norm(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", to_trad(t))[:22]


def judge(title: str, desc: str) -> dict | None:
    """相关度 + 分类 + 情绪；不相关返回 None。"""
    rel = relevance(title, desc)
    if not rel["ok"]:
        return None
    cat, cat_hits = classify(title, desc)
    a = analyse(f"{title} {desc}")
    sent_hits = a["bull"] + a["bear"]
    # 情绪至少要命中 1 个词才判断偏好/偏淡，否则中性
    impact = a["stance"] if sent_hits else "neutral"
    parts = [f"分类依据：{('、'.join(cat_hits[:4]) or '未命中特定类别词')} → {CATEGORY_LABEL[cat]}",
             ("情绪命中：" + "、".join(f"＋{w}" for w in a["bull"]) + ("、" if a["bull"] and a["bear"] else "")
              + "、".join(f"－{w}" for w in a["bear"])) if sent_hits else "情绪：未命中情绪词，判为中性",
             f"一般关系：{LINKAGE[cat]}"]
    return {"category": cat, "categoryLabel": CATEGORY_LABEL[cat], "impact": impact, "impactLabel": STANCE_LABEL[impact],
            "sentiment": a["score"] if sent_hits else 0.0, "topics": a["topics"], "relevance": round(rel["score"], 1),
            "relevanceReason": rel["reason"], "macroLinkage": "【规则研判 · 仅基于标题与摘要关键词】" + "；".join(parts)}


def build_items(raw: list[dict], rejected: list) -> list[dict]:
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
        j = judge(r["title"], r.get("desc", ""))
        if not j:
            rejected.append({"title": r["title"], "feed": r["feed"], "reason": relevance(r["title"], r.get("desc", ""))["reason"]})
            continue
        out.append({"date": d.strftime("%Y-%m-%d"), "title": r["title"], "source": r["source"],
                    "summary": short(r.get("desc") or "（RSS 仅提供标题，请点「核验原文」阅读）", 120),
                    "url": r["url"], "feed": r["feed"], "origin": "rss", **j})
    out.sort(key=lambda x: (x["date"], x["relevance"]), reverse=True)
    return out[:C.NEWS_MAX]


def news_index(items: list[dict]) -> dict:
    """新闻情绪指数 −100…+100；越新、越相关的新闻权重越高。"""
    if not items:
        return {"index": None, "n": 0}
    now = days_ago(0)
    num_, den = 0.0, 0.0
    for it in items:
        d = parse_date(it["date"])
        age = max(0.0, (now - d).days) if d else 15
        w = math.exp(-age / 10) * (1 + min(it.get("relevance", 2), 8) / 4)
        num_ += w * (it.get("sentiment") or 0)
        den += w
    cats = Counter(i["category"] for i in items)
    stance = Counter(i["impact"] for i in items)
    return {"index": round(num_ / den * 100, 1) if den else None, "n": len(items),
            "byCategory": {CATEGORY_LABEL.get(k, k): v for k, v in cats.most_common()}, "byStance": dict(stance)}


def run(snapshot: dict, *, offline: bool) -> dict:
    raw, errors, rejected = [], [], []
    if not offline:
        for feed in C.NEWS_FEEDS:
            try:
                xml = http_get(feed["url"], timeout=20)
                items = parse_rss(xml, feed)
                raw += items
                log("news", f"{feed['name']}: {len(items)} 条（过滤前）")
            except Exception as exc:
                errors.append(f"{feed['name']}: {str(exc)[:120]}")
                log("news", f"WARN {feed['name']}: {exc}")
    fresh = build_items(raw, rejected)
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
        # 本次所有来源失败：保留上次的自动新闻，但先用新规则重新审核
        for n in snapshot.get("recent_1m_news", []):
            if n.get("origin") == "rss" and n.get("date", "") >= cutoff:
                j = judge(n["title"], n.get("summary", ""))
                if j:
                    items.append({**n, **j})
                else:
                    rejected.append({"title": n["title"], "feed": n.get("feed"), "reason": "旧数据按新规则剔除"})
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    snapshot["recent_1m_news"] = items
    idx = news_index(items)
    snapshot["news_meta"] = {**idx, "window": f"{cutoff} – {days_ago(0):%Y-%m-%d}", "errors": errors,
                             "fetched": len(raw), "rejectedCount": len(rejected), "rejectedSample": rejected[:15],
                             "method": "收录条件：标题含楼市核心词，或标题含利率/经济词且摘要含楼市词；含无关词（蚊、火警、天气等）剔除。"
                                       "分类与情绪均为关键词规则，只读标题与 RSS 摘要，不读全文。",
                             "categories": [{"key": k, "label": l} for k, l, _, _ in CATEGORIES] +
                                           [{"key": "hk_property_general", "label": "楼市综合"}]}
    return {"fetched": len(raw), "kept": len(fresh), "rejected": len(rejected), "total": len(items), "index": idx["index"]}
