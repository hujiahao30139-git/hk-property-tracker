# -*- coding: utf-8 -*-
"""
宏观指标：
  * 金管局开放 API —— 1 个月 / 隔夜 HIBOR、银行体系总结余（每日）
  * 差估署 data.gov.hk CSV —— 私人住宅售价 / 租金指数（每月）
  * inbox/indicators_manual.json —— 手动补充（如中原 CCL、按揭优惠、成交宗数），抓取失败时也作后备
"""
from __future__ import annotations

import csv
import io
import re

from . import config as C
from .common import INBOX_DIR, http_get, load_json, log, num, today

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def fetch_hkma() -> dict:
    data = http_get(C.HKMA_LIQUIDITY_API, as_json=True, check_robots=False)   # 官方开放 API
    recs = data["result"]["records"]
    recs = sorted([r for r in recs if r.get("hibor_fixing_1m") is not None], key=lambda r: r["end_of_date"])
    if not recs:
        raise ValueError("HKMA 无数据")
    last = recs[-1]

    def back(days):
        import datetime as dt
        target = (dt.date.fromisoformat(last["end_of_date"]) - dt.timedelta(days=days)).isoformat()
        older = [r for r in recs if r["end_of_date"] <= target]
        return older[-1] if older else recs[0]

    m1 = back(30)
    return {
        "hibor1m": {"value": float(last["hibor_fixing_1m"]), "date": last["end_of_date"],
                    "chg30d": round(float(last["hibor_fixing_1m"]) - float(m1["hibor_fixing_1m"]), 3),
                    "series": [[r["end_of_date"], float(r["hibor_fixing_1m"])] for r in recs[-90:]],
                    "source": "金管局开放 API"},
        "hiborON": {"value": float(last["hibor_overnight"]), "date": last["end_of_date"], "source": "金管局开放 API"},
        "aggBalance": {"value": float(last["closing_balance"]), "date": last["end_of_date"], "unit": "HK$百万",
                       "chg30d": round(float(last["closing_balance"]) - float(m1["closing_balance"])), "source": "金管局开放 API"},
    }


def parse_rvd_csv(text: str) -> list[tuple[str, float]]:
    """返回 [(YYYY-MM, 整体指数)]。兼容「年,月,...,All classes」及「Jan-2026,...」等格式。"""
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    col = None
    for r in rows[:15]:
        for i, c in enumerate(r):
            cl = c.strip().lower()
            if "all class" in cl or "整體" in cl or "所有類別" in cl or cl in ("all", "overall"):
                col = i
    out, year = [], None
    for r in rows:
        cells = [c.strip() for c in r]
        if not cells:
            continue
        joined = " ".join(cells[:3]).lower()
        y = re.search(r"(19|20)\d{2}", joined)
        if y:
            year = int(y.group(0))
        mm = None
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", joined)
        if m:
            mm = MONTHS[m.group(1)]
        else:
            m2 = re.search(r"(?:^|\s)(\d{1,2})(?:月|\s|$)", " ".join(cells[1:2]) + " ")
            if m2 and 1 <= int(m2.group(1)) <= 12:
                mm = int(m2.group(1))
        if not (year and mm):
            continue
        vals = [num(c) for c in cells[2:] if re.fullmatch(r"-?[\d,.]+\s*(\(P\))?|-?[\d,.]+[A-Z]?", c or "")]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        v = num(cells[col]) if col is not None and col < len(cells) and num(cells[col]) else vals[-1]
        if v and 20 < v < 1000:
            out.append((f"{year}-{mm:02d}", v))
    dedup = dict(out)
    return sorted(dedup.items())


def fetch_rvd(urls: list[str], label: str) -> dict:
    last_err = None
    for u in urls:
        try:
            series = parse_rvd_csv(http_get(u, timeout=30))
            if len(series) >= 13:
                (m0, v0) = series[-1]
                v1 = series[-2][1]
                v3 = series[-4][1]
                v12 = series[-13][1]
                return {"value": v0, "month": m0, "mom": round((v0 / v1 - 1) * 100, 2),
                        "chg3m": round((v0 / v3 - 1) * 100, 2), "yoy": round((v0 / v12 - 1) * 100, 2),
                        "series": [[m, v] for m, v in series[-36:]], "source": f"差估署 {label}（data.gov.hk）", "url": u}
            last_err = f"{u}: 只解析到 {len(series)} 个月"
        except Exception as exc:
            last_err = f"{u}: {exc}"
    raise ValueError(last_err)


def run(snapshot: dict, *, offline: bool) -> dict:
    prev = snapshot.get("macro_indicators", {}) or {}
    ind, errors = {}, []
    manual = load_json(INBOX_DIR / "indicators_manual.json", {}) or {}
    if not offline:
        for name, fn in (("hkma", fetch_hkma),
                         ("rvdPrice", lambda: {"rvdPrice": fetch_rvd(C.RVD_PRICE_INDEX_CSV, "私人住宅售价指数")}),
                         ("rvdRent", lambda: {"rvdRent": fetch_rvd(C.RVD_RENT_INDEX_CSV, "私人住宅租金指数")})):
            try:
                ind.update(fn())
                log("indicators", f"{name} OK")
            except Exception as exc:
                errors.append(f"{name}: {str(exc)[:160]}")
                log("indicators", f"WARN {name}: {exc}")
    # 后备顺序：本次抓取 > 手动文件 > 上次结果
    for k, v in manual.items():
        if k.startswith("_"):
            continue
        if k not in ind:
            ind[k] = {**v, "source": v.get("source", "手动录入") + "（手动/后备）"}
    for k, v in prev.items():
        if k not in ind and isinstance(v, dict) and not k.startswith("_"):
            ind[k] = {**v, "stale": True}
    # 衍生：H 按实际利率
    h = ind.get("hibor1m", {}).get("value")
    if h is not None:
        ind["hMortgage"] = {"value": round(min(h + C.HIBOR_SPREAD, C.MORTGAGE_CAP_RATE), 3),
                            "note": f"min(1M HIBOR + {C.HIBOR_SPREAD}%, 封顶 {C.MORTGAGE_CAP_RATE}%)"}
    lm = snapshot.get("listings_meta", {})
    if lm.get("medianYield"):
        ind["listingYield"] = {"value": lm["medianYield"], "note": f"实盘库买盘表面回报中位数（{lm.get('sale')} 盘）"}
    if lm.get("cutShare") is not None:
        ind["cutShare"] = {"value": round(lm["cutShare"] * 100, 1), "note": "实盘库中已减价盘占比 %"}
    ind["_meta"] = {"updated": today(), "errors": errors}
    snapshot["macro_indicators"] = ind
    return {"keys": [k for k in ind if not k.startswith("_")], "errors": len(errors)}
