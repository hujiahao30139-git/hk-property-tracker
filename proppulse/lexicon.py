# -*- coding: utf-8 -*-
"""
楼市情绪 / 话题词典（粤语 + 书面中文 + 英文），供新闻、社区舆情、前端手动导入共用。
规则透明、可调：命中「看涨」词 +1，「看淡」词 -1；前 3 字内出现否定词则反转。
"""
from __future__ import annotations

import math
import re

# 繁→简（仅覆盖词典与常见楼市用字），用于把输入统一成繁体后匹配
_T2S_PAIRS = ("樓楼 價价 減减 撈捞 見见 築筑 樂乐 過过 買买 賣卖 創创 購购 漲涨 蝕蚀 負负 資资 銀银 斷断 撻挞 頂顶 "
              "觀观 貨货 應应 損损 搶抢 車车 業业 讓让 盤盘 開开 錢钱 稅税 內内 經经 濟济 員员 數数 報报 歡欢 謹谨 "
              "會会 繼继 續续 還还 長长 調调 壓压 鬆松 緊紧 優优 惠惠 檔档 單单 個个 為为 這这 們们 沒没 嗎吗 擔担 "
              "憂忧 險险 機机 會会 說说 話话 現现 實实 進进 場场 動动 興兴 奮奋 驚惊 嚇吓 慘惨 爛烂 壞坏 戶户 劃划 "
              "國国 際际 聯联 儲储 華华 東东 換换 貴贵 輕轻 鬆松 響响 衝冲 擊击 適适 點点 對对 從从 雖虽 與与 舊旧 "
              "齡龄 層层 廳厅 間间 窩窝 號号 補补 揭揭 費费 紓纾 撐撑 樣样 種种 劍剑 難难 苦苦 頭头 熱热 炒炒 億亿 "
              "萬万 發发 展展 灣湾 銅铜 鑼锣 紅红 磡磡 環环 區区 灣湾 運运 嚴严 釋释 頒颁 選选 戰战 傷伤 惡恶 冧冧 "
              "樓楼 唔唔 冇冇")
T2S = {}
for pair in _T2S_PAIRS.split():
    if len(pair) == 2:
        T2S[pair[0]] = pair[1]
S2T = {v: k for k, v in T2S.items()}


def to_trad(s: str) -> str:
    return "".join(S2T.get(c, c) for c in s or "")


BULL_TERMS = [
    # 价格/成交
    "樓價回升", "樓價反彈", "樓市回暖", "樓市復甦", "止跌回升", "見底", "築底", "谷底反彈", "反彈", "回升", "回暖", "復甦",
    "上升", "升值", "升幅", "連升", "抽升", "爆升", "創新高", "新高", "向好", "轉好", "企穩", "回穩", "穩定",
    "搶盤", "搶購", "搶樓", "超購", "沽清", "售罄", "清貨", "加價", "反價", "封盤", "惜售", "睇樓量增", "成交急升", "成交增",
    "入市", "上車", "撈底", "趁低吸納", "值博", "抵買", "筍盤", "供平過租", "正現金流", "收租",
    # 资金/政策
    "減息", "降息", "息口回落", "低息", "HIBOR回落", "拆息回落", "撤辣", "放寬", "優惠", "高才通", "人才", "北水", "資金流入",
    "牛市", "看好", "睇好", "樂觀", "有信心", "利好", "會升", "再升", "升咗", "仲升",
    # 英文
    "rebound", "recover", "recovery", "rise", "rising", "surge", "bullish", "rate cut", "buy now", "undervalued", "optimistic",
]
BEAR_TERMS = [
    "樓價下跌", "樓價回落", "樓市下行", "跌市", "下跌", "跌價", "跌幅", "回落", "急跌", "大跌", "插水", "跌穿", "跌破", "殺跌", "冧",
    "劈價", "減價", "削價", "蝕讓", "蝕錢", "損手", "負資產", "銀主盤", "銀主", "斷供", "撻訂", "壞帳", "破產",
    "見頂", "摸頂", "泡沫", "爆煲", "熊市", "淡市", "睇淡", "看淡", "悲觀", "觀望", "冇人買", "唔好買", "唔抵", "高追", "接火棒", "韭菜",
    "加息", "升息", "息口上升", "高息", "HIBOR上升", "拆息上升", "供唔起", "供樓壓力", "貨尾", "庫存", "供應過剩", "供應增加",
    "裁員", "失業", "經濟差", "衰退", "移民潮", "走資", "利淡", "風險", "會跌", "再跌", "跌咗", "仲跌", "跌",
    # 英文
    "crash", "falling", "decline", "drop", "bearish", "negative equity", "bubble", "rate hike", "oversupply", "pessimistic",
]
NEGATIONS = ["唔", "不", "未", "冇", "沒", "無", "非", "未必", "唔會", "不會", "not ", "no "]

TOPICS = {
    "rates": ("利率与按揭", ["息", "HIBOR", "拆息", "按揭", "P按", "H按", "定按", "供樓", "聯儲", "金管局", "銀行", "mortgage", "rate", "hibor"]),
    "price": ("楼价走势", ["樓價", "樓市", "指數", "CCL", "見頂", "見底", "跌", "升", "成交價", "price", "market"]),
    "supply": ("新盘与供应", ["新盤", "一手", "貨尾", "開價", "價單", "賣地", "推地", "地皮", "供應", "落成", "new launch", "supply"]),
    "rent": ("租金与收租", ["租金", "收租", "租客", "回報", "租樓", "業主", "劏房", "rent", "landlord", "tenant", "yield"]),
    "firstbuyer": ("上车与首置", ["上車", "首置", "首期", "印花稅", "400萬", "後生", "年輕人", "買唔起", "first home", "deposit"]),
    "distress": ("负资产与银主", ["負資產", "銀主", "斷供", "撻訂", "蝕讓", "壞帳", "negative equity", "foreclos"]),
    "mainland": ("内地买家与人才", ["內地", "北水", "人才", "高才", "優才", "深圳", "大灣區", "mainland", "talent"]),
    "economy": ("经济与就业", ["經濟", "失業", "裁員", "股市", "恒指", "人工", "收入", "GDP", "economy", "jobs", "stock"]),
    "policy": ("政策与施政", ["施政報告", "辣招", "撤辣", "政府", "政策", "預算案", "稅", "policy", "government"]),
}

_ALL_SENT = sorted([(t, 1) for t in BULL_TERMS] + [(t, -1) for t in BEAR_TERMS], key=lambda x: -len(x[0]))


def analyse(text: str) -> dict:
    """返回 {score, stance, bull[], bear[], topics[]}；score ∈ [-1, 1]。"""
    raw = text or ""
    t = to_trad(raw)
    low = t.lower()
    used = [False] * len(low)
    bull, bear = [], []
    for term, pol in _ALL_SENT:
        key = term.lower()
        start = 0
        while True:
            i = low.find(key, start)
            if i < 0:
                break
            start = i + len(key)
            if any(used[i:i + len(key)]):
                continue
            for k in range(i, i + len(key)):
                used[k] = True
            pre = low[max(0, i - 3):i]
            p = -pol if any(n in pre for n in NEGATIONS) else pol
            (bull if p > 0 else bear).append(term)
    b, s = len(bull), len(bear)
    score = 0.0 if b + s == 0 else (b - s) / (b + s) * min(1.0, math.log1p(b + s) / math.log(4))
    stance = "bull" if score > 0.15 else ("bear" if score < -0.15 else "neutral")
    topics = [k for k, (_, kws) in TOPICS.items() if any(kw.lower() in low for kw in kws)]
    return {"score": round(score, 3), "stance": stance, "bull": bull[:6], "bear": bear[:6], "topics": topics}


def export_for_frontend() -> dict:
    return {"bull": BULL_TERMS, "bear": BEAR_TERMS, "neg": NEGATIONS,
            "topics": {k: {"label": v[0], "kw": v[1]} for k, v in TOPICS.items()},
            "t2s": "".join(k + v for k, v in T2S.items())}


STANCE_LABEL = {"bull": "偏好 / 看涨", "bear": "偏淡 / 看跌", "neutral": "中性 / 观望"}


def sanitize_quote(s: str, n: int = 90) -> str:
    """去掉 @用户名、电话、邮箱后截短，避免保存个人资料。"""
    s = re.sub(r"@\S+", "@某网民", s or "")
    s = re.sub(r"\b\d{8}\b|\+?852[\s-]?\d{4}[\s-]?\d{4}", "[电话]", s)
    s = re.sub(r"\S+@\S+\.\S+", "[邮箱]", s)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
