# -*- coding: utf-8 -*-
"""
综合走势预测（3 个月 / 半年 / 1 年）——透明的多因子打分模型，不是经回测验证的统计模型。
因子（均标准化到 −1…+1）：
  momentum 价格动量（差估署售价指数 3 个月变化，缺失时用按年变化）
  rates    利率（1M HIBOR 水平与 30 天变化）
  carry    供平过租（实盘库表面回报中位数 − H 按实际利率）
  supply   供应压力（实盘库已减价盘占比，越高越负）
  news     新闻情绪指数
  social   网民情绪指数（极端值时减半，避免羊群效应）
缺失因子不计入，并按覆盖率放宽区间。
"""
from __future__ import annotations

import math

from . import config as C
from .common import clamp, today


def factors(s: dict) -> dict:
    ind = s.get("macro_indicators", {}) or {}
    out = {}
    rvd = ind.get("rvdPrice", {})
    if rvd.get("chg3m") is not None:
        out["momentum"] = (clamp(rvd["chg3m"] / 4), f"差估署售价指数 3 个月 {rvd['chg3m']:+.1f}%（{rvd.get('month', '')}）", 1.0)
    elif rvd.get("yoy") is not None:
        out["momentum"] = (clamp(rvd["yoy"] / 12), f"差估署售价指数按年 {rvd['yoy']:+.1f}%（{rvd.get('month', '')}，缺 3 个月数据）", 0.5)
    h = ind.get("hibor1m", {})
    if h.get("value") is not None:
        chg = h.get("chg30d")
        b = clamp((3.0 - h["value"]) / 1.5)
        if chg is None:
            out["rates"] = (b, f"1M HIBOR {h['value']:.2f}%（缺 30 天变化，只看水平）", 0.6)
        else:
            out["rates"] = (0.5 * clamp(-chg / 0.5) + 0.5 * b, f"1M HIBOR {h['value']:.2f}%（30 天 {chg:+.2f}）", 1.0)
    n_sale = (s.get("listings_meta") or {}).get("sale") or 0
    q_list = min(1.0, n_sale / 300)
    y = ind.get("listingYield", {}).get("value")
    m = ind.get("hMortgage", {}).get("value") or C.MORTGAGE_RATE * 100
    if y:
        out["carry"] = (clamp((y - m) / 1.0), f"实盘回报中位数 {y:.2f}% vs 按揭 {m:.2f}%（{n_sale} 盘）", q_list)
    cs = ind.get("cutShare", {}).get("value")
    if cs is not None:
        out["supply"] = (clamp(-(cs - 15) / 15), f"减价盘占比 {cs:.1f}%（{n_sale} 盘）", q_list)
    nm = s.get("news_meta") or {}
    if nm.get("index") is not None:
        out["news"] = (clamp(nm["index"] / 40), f"新闻情绪指数 {nm['index']:+.0f}（{nm.get('n', 0)} 条）", min(1.0, (nm.get("n") or 0) / 20))
    ss = s.get("sentiment_summary") or {}
    if ss.get("index") is not None:
        si = ss["index"]
        f = clamp(si / 40) * (0.5 if abs(si) > 60 else 1.0)
        out["social"] = (f, f"网民情绪指数 {si:+.0f}（{ss.get('n', 0)} 条）", min(1.0, (ss.get("n") or 0) / 60))
    return out


LABELS = {"momentum": "价格动量", "rates": "利率", "carry": "供平过租", "supply": "供应/减价压力", "news": "新闻情绪", "social": "网民情绪"}


def run(snapshot: dict) -> dict:
    fs = factors(snapshot)
    ind = snapshot.get("macro_indicators", {}) or {}
    rvd, ccl = ind.get("rvdPrice", {}), ind.get("ccl", {})
    if rvd.get("value"):
        base_val, base_lbl = rvd["value"], f"差估署售价指数（{rvd.get('month', '')} = {rvd['value']}）"
    elif ccl.get("value"):
        base_val, base_lbl = ccl["value"], f"中原 CCL（{ccl.get('date', '')} = {ccl['value']}）"
    else:
        base_val, base_lbl = None, "楼价"
    rent_yoy = ind.get("rvdRent", {}).get("yoy")
    prev = {f.get("key"): f for f in snapshot.get("forecast_horizons", []) if f.get("key")}
    legacy = [f for f in snapshot.get("forecast_horizons", []) if not f.get("key")]
    horizons, model = [], {"generated": today(), "factors": {k: {"label": LABELS[k], "value": round(v, 3), "detail": d, "quality": round(q, 2)}
                                                            for k, (v, d, q) in fs.items()}}
    picks = (snapshot.get("deal_picks") or {}).get("tiers", {})
    for i, h in enumerate(C.FORECAST_HORIZONS):
        w = h["weights"]
        present = {k: w[k] * fs[k][2] for k in w if k in fs and fs[k][2] > 0}
        cov = sum(present.values())
        S = sum(present[k] * fs[k][0] for k in present) / cov if cov else 0.0
        base = S * h["scale"]
        band = h["band"] / max(cov, 0.4)
        lo, hi = base - band / 2, base + band / 2
        bull, bear = base + band, base - band
        prob_up = min(0.85, max(0.15, 1 / (1 + math.exp(-(base / band))))) if band else 0.5
        rent = (0.6 * rent_yoy * h["months"] / 12 + 0.4 * base) if rent_yoy is not None else 0.5 * base

        def lvl(p):
            return f"{base_val * (1 + p / 100):.1f}（{p:+.1f}%）" if base_val else f"{p:+.1f}%"

        contrib = sorted(((k, present[k] * fs[k][0] / cov) for k in present), key=lambda x: -abs(x[1]))
        drivers = "；".join(f"{'▲' if c > 0 else '▼'} {LABELS[k]}：{fs[k][1]}（贡献 {c:+.2f}）" for k, c in contrib[:4])
        theme = ("温和上行" if S > 0.25 else "偏强整固" if S > 0.08 else "窄幅整固" if S > -0.08 else "偏弱整固" if S > -0.25 else "下行压力")
        if S > 0.25:
            act = "趋势偏好：优先锁定低息按揭、主攻折让 ≥8% 且供平过租的 S/A 级盘，避免追反价盘。"
        elif S > -0.08:
            act = "区间市：耐心议价，只买「较中位数明显低水 + 有实租佐证」的盘；≤400 万盘优先。"
        else:
            act = "下行风险：观望为主，若入市只考虑银主/急让盘并预留 10% 以上估价缓冲。"
        act += f"（当前实盘库 S 级 {picks.get('S', 0)} 盘、A 级 {picks.get('A', 0)} 盘）"
        f = {
            "key": h["key"], "horizon": f"{h['label']}（基准 {today()}）", "theme": f"模型判断：{theme}（综合分 {S:+.2f}）",
            "cclBase": f"{lvl(lo)} 至 {lvl(hi)}", "cclBull": lvl(bull), "cclBear": lvl(bear),
            "rentForecast": f"{rent - band / 3:+.1f}% 至 {rent + band / 3:+.1f}%" + ("" if rent_yoy is not None else "（缺租金指数，按楼价推算）"),
            "keyDrivers": drivers or "数据不足", "actionStrategy": f"【{h['label']}操作建议】{act}",
            "probUp": round(prob_up * 100), "score": round(S, 3), "coverage": round(cov, 2), "baseLabel": base_lbl,
            "contrib": [{"factor": LABELS[k], "value": round(c, 3)} for k, c in contrib],
        }
        # 保留人工研判（旧版预测卡片），供对照
        old = prev.get(h["key"], {}).get("analystView") or (legacy[i] if i < len(legacy) else None)
        if old:
            f["analystView"] = {k: old.get(k) for k in ("horizon", "theme", "cclBase", "cclBull", "cclBear", "rentForecast", "keyDrivers", "actionStrategy")}
        horizons.append(f)
    model["coverageNote"] = ("因子覆盖率 = 有数据的因子权重之和；覆盖率越低，区间越宽。"
                             "这是规则透明的打分模型，未经历史回测，仅作方向参考，不构成投资建议。")
    snapshot["forecast_horizons"] = horizons
    snapshot["forecast_model"] = model
    hist = snapshot.get("history", [])
    hist = [x for x in hist if x.get("date") != today()]
    hist.append({"date": today(), "score3m": horizons[0]["score"], "score1y": horizons[-1]["score"],
                 "news": (snapshot.get("news_meta") or {}).get("index"), "social": (snapshot.get("sentiment_summary") or {}).get("index"),
                 "hibor1m": ind.get("hibor1m", {}).get("value"), "rvd": rvd.get("value"),
                 "listings": (snapshot.get("listings_meta") or {}).get("total")})
    snapshot["history"] = hist[-365:]
    return {"scores": {h["key"]: h["score"] for h in horizons}, "factors": list(fs)}
