# -*- coding: utf-8 -*-
"""
笋盘自动筛选（v7.2）：
  1) 每个买盘打 0–100「笋盘分」并分 S/A/B 级（笋盘雷达用）。
  2) 每天用当天实盘重新生成第 1 页「多维权重筛选引擎」的盘源（curated_deals），取代旧版手工录入的 15 个静态盘。
     四个维度分数全部由数据计算：
       ① 低水幅度   = 较同屋苑/片区放盘中位实尺的折让
       ② 回报与现金流 = 表面回报 + 7 成按揭月净差额
       ③ 升值潜力   = 步行到港铁分钟数 + 是否焦点四区 + 是否已减价
       ④ 估价与流动性 = 楼龄 + 同屋苑在售盘数（越多越易比价/转手）+ 是否有实租佐证
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
from collections import Counter

from . import config as C
from .common import stamp_duty_hkd, today


def _lin(x, x0, x1):
    if x is None:
        return None
    return max(0.0, min(100.0, (x - x0) / (x1 - x0) * 100))


def score_listing(d: dict) -> dict | None:
    if d.get("type") != "sale" or not d.get("priceWan"):
        return None
    parts, reasons, warn = {}, [], []
    vs = d.get("vsMedianPct")
    parts["discount"] = _lin(-vs, -15, 20) if vs is not None else 40.0
    if vs is not None and vs <= -8:
        reasons.append(f"实尺低于{(d.get('vsMedianBasis') or '').split('放盘')[0]}中位数 {abs(vs)}%")
    elif vs is None:
        warn.append("同屋苑/片区可比盘不足，折让未能核实")
    if vs is not None and vs <= -25 and d.get("ageYears") is None:
        warn.append("楼龄未知且折让很大：可能是唐楼/无电梯/单幢楼，折让或反映楼宇质素")
    y = d.get("grossYield")
    parts["yield"] = _lin(y, 2.5, 5.5) if y else 30.0
    if y and y >= 4:
        reasons.append(f"表面回报 {y}%" + ("（实租）" if (d.get("rentBasis") or "").startswith("实盘") else "（估算）"))
    if d.get("monthlyCarry") is not None and d["monthlyCarry"] >= 0:
        reasons.append(f"7成按揭供平过租 +${d['monthlyCarry']:,}/月")
    age = d.get("ageYears")
    parts["age"] = 60.0 if age is None else _lin(-age, -55, -15)
    if age and age >= 50:
        warn.append(f"楼龄 {int(age)} 年：按揭成数可能受限，须查强制验楼/验窗")
    cut = d.get("cutPct") or 0
    parts["cut"] = _lin(cut, 0, 10)
    if cut:
        reasons.append(f"已减价 {cut}%")
    p = d["priceWan"]
    parts["stamp"] = 100.0 if p <= 400 else _lin(-p, -700, -400)
    if p <= 400:
        reasons.append("≤400万，印花税仅 $100")
    try:
        days = (dt.date.fromisoformat(today()) - dt.date.fromisoformat(d.get("firstSeen", today()))).days
    except ValueError:
        days = 0
    parts["fresh"] = _lin(-days, -30, 0)
    parts["rent_proof"] = 100.0 if (d.get("rentBasis") or "").startswith("实盘") or "连租约" in (d.get("tags") or []) else 0.0
    w = C.DEAL_WEIGHTS
    score = sum(w[k] * (parts[k] if parts[k] is not None else 50.0) for k in w)
    if vs is None:
        score = min(score, 64.9)
    tier = next((t for s, t in C.DEAL_TIERS if score >= s), "")
    return {"score": round(score, 1), "tier": tier, "reasons": reasons[:4], "warnings": warn[:3],
            "parts": {k: round(v, 0) for k, v in parts.items() if v is not None}}


# ---------------------------------------------------------------- 第 1 页引擎用的四维分数
def _mtr_minutes(d: dict):
    m = re.search(r"(\d+)\s*分", d.get("mtr") or "")
    return int(m.group(1)) if m else None


def four_dims(d: dict, estate_count: Counter) -> dict:
    vs = d.get("vsMedianPct")
    disc = _lin(-vs, -10, 25) if vs is not None else 35.0
    y, carry = d.get("grossYield") or 0, d.get("monthlyCarry")
    yld = 0.7 * (_lin(y, 2.5, 6.0) or 0) + 0.3 * (_lin(carry, -15000, 10000) if carry is not None else 30)
    mins = _mtr_minutes(d)
    up = 0.5 * (_lin(-mins, -15, -2) if mins is not None else 40) \
        + 0.3 * (100 if any(a in (d.get("subArea") or "") for a in C.FOCUS_SUB_AREAS) else 40) \
        + 0.2 * (_lin(d.get("cutPct") or 0, 0, 8) or 0)
    age = d.get("ageYears")
    n_est = estate_count.get(d.get("estate"), 1)
    safe = 0.5 * (_lin(-age, -55, -10) if age is not None else 45) + 0.3 * (_lin(n_est, 1, 10) or 0) \
        + 0.2 * (100 if (d.get("rentBasis") or "").startswith("实盘") else 30)
    return {"scoreDiscount": round(disc), "scoreYield": round(yld), "scoreUpside": round(up), "scoreSafety": round(safe)}


def _category(d: dict) -> tuple[str, str]:
    if (d.get("ageYears") or 0) >= 40 and (d.get("grossYield") or 0) >= 5:
        return "tonglau_yield", "旧楼高息"
    if d.get("cutPct"):
        return "price_cut", "业主减价"
    if d.get("monthlyCarry") is not None and d["monthlyCarry"] >= 0:
        return "positive_carry", "供平过租"
    return "below_median", "低于中位实尺"


def to_engine_deal(d: dict, s: dict, estate_count: Counter, gen: str) -> dict | None:
    if not d.get("estRent") or not d.get("grossYield"):
        return None
    dims = four_dims(d, estate_count)
    cat, cat_lbl = _category(d)
    price, vs = d["priceWan"], d.get("vsMedianPct")
    if vs is not None and vs > 0:
        fair = d["psf"] / (1 + vs / 100) * d["area"] / 10_000
        bid = f"参考同类中位实尺约 {fair:.0f} 万；叫价高于中位数 {vs}%，宜大幅议价"
    elif vs is not None:
        bid = f"叫价已低于中位数 {abs(vs)}%，可尝试议价 2–3% 至约 {price * 0.975:.0f}–{price * 0.98:.0f} 万"
    else:
        bid = "可比盘不足，先查同屋苑近月成交价再出价"
    if 400 < price <= 432:
        bid += "；压到 400 万内印花税只需 $100"
    sd = stamp_duty_hkd(price * 10_000)
    unit = " ".join(x for x in (d.get("building"), d.get("floor"), d.get("unit"), d.get("rooms")) if x)
    tier = {"S": "S", "A": "A+", "B": "B"}.get(s["tier"], "B")
    rent_kind = "实租" if (d.get("rentBasis") or "").startswith("实盘") else "估算租金"
    return {
        "id": d["id"], "auto": True, "generated": gen,
        "portal": d.get("source"), "portalName": f"{d.get('sourceName')} · 每日自动",
        "tier": tier, "tierLabel": f"{s['tier'] or '—'}级 · 笋盘分 {s['score']}",
        "category": cat, "categoryLabel": cat_lbl, "region": d.get("region") or "—", "district": d.get("district") or "—",
        "name": f"{d.get('subArea') or ''} · {d.get('estate')}", "unitDetail": unit or "—",
        "area": d["area"], "priceWan": price, "psf": d["psf"],
        "valuationGap": (f"{vs:+.1f}% （{d.get('vsMedianBasis')}）" if vs is not None else "— （可比盘不足）"),
        "monthlyRent": d["estRent"], "rentPsf": d.get("rentPsf"), "grossYield": d["grossYield"],
        "netYield": round(d["grossYield"] * 0.88, 2),
        "stampDuty": "$100" if sd <= 100 else f"约 ${sd:,.0f}",
        "renoCostWan": 0, **dims, "bidTarget": bid,
        "agentInfo": f"{d.get('sourceName')} 物业编号 {d.get('ref') or '—'} · 首次见于 {d.get('firstSeen', '')}" + (f" · {d.get('mtr')}" if d.get("mtr") else ""),
        "expertPros": f"【自动生成 {gen}】" + ("；".join(s["reasons"]) or "综合分数较高"),
        "expertCons": "；".join(s["warnings"] + [f"月租为{rent_kind}（{d.get('rentBasis')}），落订前须向代理核实"]),
        "suitableFor": {"tonglau_yield": "追求高回报、能接受旧楼维修的收租客", "price_cut": "想趁业主减价议价的自住/投资者",
                        "positive_carry": "希望供款低于租金的首置或收租客", "below_median": "重视入市价低于同类盘的买家"}[cat],
        "url": d.get("url"),
    }


def run(snapshot: dict) -> dict:
    listings = snapshot.get("listings", [])
    scored = []
    for d in listings:
        s = score_listing(d)
        for k in ("dealScore", "dealTier", "dealReasons", "dealWarnings"):
            d.pop(k, None)
        if s:
            d["dealScore"], d["dealTier"], d["dealReasons"], d["dealWarnings"] = s["score"], s["tier"], s["reasons"], s["warnings"]
            scored.append((s["score"], d, s))
    scored.sort(key=lambda x: -x[0])

    def pick(d, s):
        return {"id": d["id"], "score": s["score"], "tier": s["tier"], "estate": d.get("estate"), "subArea": d.get("subArea"),
                "district": d.get("district"), "unit": " ".join(x for x in (d.get("building"), d.get("floor"), d.get("unit")) if x),
                "rooms": d.get("rooms"), "area": d.get("area"), "priceWan": d.get("priceWan"), "psf": d.get("psf"),
                "vsMedianPct": d.get("vsMedianPct"), "grossYield": d.get("grossYield"), "monthlyCarry": d.get("monthlyCarry"),
                "reasons": s["reasons"], "warnings": s["warnings"], "parts": s["parts"], "url": d.get("url"),
                "sourceName": d.get("sourceName")}

    by_focus = {area: [pick(d, s) for _, d, s in scored if area in (d.get("subArea") or "")][:8] for area in C.FOCUS_SUB_AREAS}
    tiers = {t: sum(1 for _, _, s in scored if s["tier"] == t) for _, t in C.DEAL_TIERS}
    snapshot["deal_picks"] = {
        "generated": today(), "weights": C.DEAL_WEIGHTS, "tiers": tiers, "scored": len(scored),
        "top": [pick(d, s) for _, d, s in scored[:C.DEAL_TOP_N]], "byFocus": by_focus,
        "method": "笋盘分 = 35% 较同屋苑/片区中位实尺折让 + 25% 表面回报 + 10% 楼龄 + 10% 已减价幅度 + 10% 印花税档 "
                  "+ 5% 新上架 + 5% 有实租佐证；折让无法核实的盘封顶 B 级。S≥75 · A≥65 · B≥55。",
    }

    # ---- 第 1 页「多维权重筛选引擎」：每天用实盘重建（全港前 16 + 焦点四区各前 3，去重）----
    gen = today()
    estate_count = Counter(d.get("estate") for d in listings if d.get("type") == "sale")
    chosen, ids = [], set()
    for area in C.FOCUS_SUB_AREAS:
        for _, d, s in [x for x in scored if area in (x[1].get("subArea") or "") and x[2]["tier"]][:3]:
            e = to_engine_deal(d, s, estate_count, gen)
            if e and e["id"] not in ids:
                chosen.append(e); ids.add(e["id"])
    for _, d, s in scored:
        if len(chosen) >= 28:
            break
        if d["id"] in ids or not s["tier"]:
            continue
        e = to_engine_deal(d, s, estate_count, gen)
        if e:
            chosen.append(e); ids.add(e["id"])
    if chosen:
        old = [x for x in snapshot.get("curated_deals", []) if not x.get("auto")]
        if old and not snapshot.get("curated_deals_archive"):
            snapshot["curated_deals_archive"] = old          # 旧版手工盘只存档，不再显示
        snapshot["curated_deals"] = chosen
    snapshot["curated_meta"] = {"generated": gen, "count": len(chosen), "auto": bool(chosen),
                                "note": "第 1 页筛选引擎的盘源每天由实盘库自动生成；四个维度分数均由数据计算。"}
    return {"scored": len(scored), "tiers": tiers, "engineDeals": len(chosen)}
