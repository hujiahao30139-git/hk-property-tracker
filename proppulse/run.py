# -*- coding: utf-8 -*-
"""
主流程：python -m proppulse.run [--stages listings,deals,newlaunch,indicators,news,social,forecast] [--offline] [--discover]
每个阶段独立容错：某阶段失败只记录错误，保留上次数据，不影响其他阶段。
"""
from __future__ import annotations

import argparse
import time
import traceback

from . import config as C
from . import deals, forecast, indicators, lexicon, listings, newlaunch, news, social
from .common import DATA_FILE, DEBUG_DIR, load_json, log, now_hkt, save_json

ALL_STAGES = ["listings", "deals", "newlaunch", "indicators", "news", "social", "forecast"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="HK PropPulse v7 pipeline")
    ap.add_argument("--stages", default=",".join(ALL_STAGES))
    ap.add_argument("--offline", action="store_true", help="不联网，只用种子/手动/上次数据重算")
    ap.add_argument("--discover", action="store_true", help="诊断模式：保存样本到 debug/，不写 market_data.json")
    ap.add_argument("--max-pages", type=int, default=C.LISTING_MAX_PAGES)
    ap.add_argument("--only", help="盘源只跑某平台，如 Centaline")
    a = ap.parse_args(argv)
    stages = [s.strip() for s in a.stages.split(",") if s.strip()]

    snap = load_json(DATA_FILE, {}) or {}
    status = {}
    for st in ALL_STAGES:
        if st not in stages:
            continue
        t0 = time.time()
        try:
            if st == "listings":
                res = listings.run(snap, offline=a.offline, discover=a.discover, max_pages=a.max_pages, only=a.only)
            elif st == "deals":
                res = deals.run(snap)
            elif st == "newlaunch":
                res = newlaunch.run(snap, offline=a.offline, discover=a.discover)
            elif st == "indicators":
                res = indicators.run(snap, offline=a.offline)
            elif st == "news":
                res = news.run(snap, offline=a.offline)
            elif st == "social":
                res = social.run(snap, offline=a.offline, discover=a.discover)
            else:
                res = forecast.run(snap)
            status[st] = {"ok": True, "secs": round(time.time() - t0, 1), **res}
            log(st, f"OK {res}")
        except Exception as exc:  # noqa: BLE001
            status[st] = {"ok": False, "secs": round(time.time() - t0, 1), "error": str(exc)[:300]}
            log(st, f"FAILED {exc}\n{traceback.format_exc(limit=3)}")

    snap["pipeline_status"] = {"run": f"{now_hkt():%Y-%m-%d %H:%M} HKT", "offline": a.offline, "stages": status}
    snap["sentiment_lexicon"] = lexicon.export_for_frontend()
    snap["updated_at"] = f"{now_hkt():%Y-%m-%d %H:%M:%S} HKT"
    snap["version"] = "v7.5 All-in-one Pipeline"
    if a.discover:
        DEBUG_DIR.mkdir(exist_ok=True)
        save_json(DEBUG_DIR / "summary.json", snap["pipeline_status"])
        log("discover", f"完成，样本见 {DEBUG_DIR}/（未写 market_data.json）")
        return
    save_json(DATA_FILE, snap)
    log("done", f"已写入 {DATA_FILE.name}：" + ", ".join(f"{k}={'✓' if v['ok'] else '✗'}" for k, v in status.items()))


if __name__ == "__main__":
    main()
