# -*- coding: utf-8 -*-
"""笋盘自动筛选：透明加权打分（0–100），并给出逐项理由。"""
from __future__ import annotations

import datetime as dt

from . import config as C
from .common import clamp, today


def _lin(x, x0, x1):
    """x0→0 分，x1→100 分，线性并截断。"""
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
        score = min(score, 64.9)   # 折让无法核实的盘最多 B 级
    tier = next((t for s, t in C.DEAL_TIERS if score >= s), "")
    return {"score": round(score, 1), "tier": tier, "reasons": reasons[:4], "warnings": warn[:3],
            "parts": {k: round(v, 0) for k, v in parts.items() if v is not None}}


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

    by_focus = {}
    for area in C.FOCUS_SUB_AREAS:
        by_focus[area] = [pick(d, s) for _, d, s in scored if area in (d.get("subArea") or "")][:8]
    tiers = {t: sum(1 for _, _, s in scored if s["tier"] == t) for _, t in C.DEAL_TIERS}
    snapshot["deal_picks"] = {
        "generated": today(), "weights": C.DEAL_WEIGHTS, "tiers": tiers, "scored": len(scored),
        "top": [pick(d, s) for _, d, s in scored[:C.DEAL_TOP_N]], "byFocus": by_focus,
        "method": "笋盘分 = 35% 较同屋苑/片区中位实尺折让 + 25% 表面回报 + 10% 楼龄 + 10% 已减价幅度 + 10% 印花税档 "
                  "+ 5% 新上架 + 5% 有实租佐证；折让无法核实的盘封顶 B 级。S≥75 · A≥65 · B≥55。",
    }
    return {"scored": len(scored), "tiers": tiers}
