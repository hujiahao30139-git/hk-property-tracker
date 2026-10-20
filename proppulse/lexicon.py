# -*- coding: utf-8 -*-
"""
楼市情绪 / 话题词典（粤语 + 书面中文 + 英文），供新闻、社区舆情、前端手动导入共用。  v7.4
规则：命中「看涨」词 +1，「看淡」词 −1；词前 5 字内出现否定/阻碍词则反转；双重否定（很難不…）视为肯定；同一词只计一次。

v7.4 修正：「無論」「不論」「不過」「不少」「不斷」等不是否定，之前会把「無論樓價與成交急升」误判为「成交急升(否定)」。
词典法不理解反讽和复杂语境；要更准确的判断请设置 OPENAI_API_KEY 让 AI 归纳。
"""
from __future__ import annotations

import math
import re

_T2S_PAIRS = ("樓楼 價价 減减 撈捞 見见 築筑 樂乐 過过 買买 賣卖 創创 購购 漲涨 蝕蚀 負负 資资 銀银 斷断 撻挞 頂顶 "
              "觀观 貨货 應应 損损 搶抢 車车 業业 讓让 盤盘 開开 錢钱 稅税 內内 經经 濟济 員员 數数 報报 歡欢 謹谨 "
              "會会 繼继 續续 還还 長长 調调 壓压 鬆松 緊紧 優优 檔档 單单 個个 為为 這这 們们 沒没 嗎吗 擔担 "
              "憂忧 險险 機机 說说 話话 現现 實实 進进 場场 動动 興兴 奮奋 驚惊 嚇吓 慘惨 爛烂 壞坏 戶户 劃划 "
              "國国 際际 聯联 儲储 華华 東东 換换 貴贵 輕轻 響响 衝冲 擊击 適适 點点 對对 從从 雖虽 與与 舊旧 "
              "齡龄 層层 廳厅 間间 窩窝 號号 補补 費费 紓纾 撐撑 樣样 種种 難难 頭头 熱热 億亿 萬万 發发 灣湾 "
              "銅铜 鑼锣 紅红 環环 區区 運运 嚴严 釋释 頒颁 選选 戰战 傷伤 惡恶 產产 權权 庫库 規规 "
              "後后 準准 營营 礙碍 輸输 無无 論论 斷断")
T2S = {}
for pair in _T2S_PAIRS.split():
    if len(pair) == 2 and pair[0] != pair[1]:
        T2S[pair[0]] = pair[1]
S2T = {v: k for k, v in T2S.items()}


def to_trad(s: str) -> str:
    return "".join(S2T.get(c, c) for c in s or "")


BULL_TERMS = [
    "樓價回升", "樓價反彈", "樓價上升", "樓價上漲", "樓價升", "樓價連升", "樓價大升", "樓市回暖", "樓市復甦", "樓市轉好", "樓市向好",
    "止跌回升", "止跌", "見底", "築底", "谷底反彈", "成交急升", "成交上升", "成交增", "成交回升", "睇樓量增", "租金上升",
    "租金新高", "租金上漲", "樓價新高", "創新高", "連升", "抽升", "爆升", "企穩", "回穩", "升值", "股跌樓升",
    "搶盤", "搶購", "搶樓", "超購", "沽清", "售罄", "加價", "反價", "封盤", "惜售",
    "入市", "上車", "撈底", "趁低吸納", "值博", "抵買", "筍盤", "供平過租", "正現金流",
    "減息", "降息", "息口回落", "低息", "HIBOR回落", "拆息回落", "拆息下跌", "撤辣", "放寬按揭", "人才湧入", "北水", "資金流入",
    "牛市", "看好", "睇好", "樂觀", "有信心", "會升", "再升", "升咗", "仲升",
    "rebound", "recovery", "prices rise", "bullish", "rate cut", "undervalued", "optimistic",
]
BEAR_TERMS = [
    "樓價下跌", "樓價回落", "樓價大跌", "樓價急跌", "樓價跌", "樓價下滑", "樓價下調", "樓市下跌", "樓市下行", "樓市調整",
    "樓市轉差", "樓市插水", "成交下跌", "成交回落", "成交減少", "成交量下跌", "零成交", "有價無市", "租金下跌", "租金回落", "租金下調",
    "跌市", "跌價", "跌穿", "跌破", "殺跌", "冧價", "下調", "拖低", "被拖低", "輸家", "全軍覆沒",
    "劈價", "減價", "削價", "蝕讓", "蝕錢", "損手", "負資產", "銀主盤", "銀主", "斷供", "撻訂", "壞帳", "破產",
    "見頂", "摸頂", "泡沫", "爆煲", "熊市", "淡市", "睇淡", "看淡", "悲觀", "觀望", "冇人買", "唔好買", "唔抵", "高追", "接火棒", "韭菜",
    "加息", "升息", "息口上升", "高息", "HIBOR上升", "拆息上升", "拆息抽升", "供唔起", "供樓壓力", "貨尾", "供應過剩", "庫存增加",
    "裁員", "失業率上升", "經濟差", "衰退", "移民潮", "走資", "利淡",
    "會跌", "再跌", "跌咗", "仲跌", "繼續跌",
    "crash", "prices fall", "bearish", "negative equity", "bubble", "rate hike", "oversupply", "pessimistic",
]
NEGATIONS = ["唔", "不", "未", "冇", "沒", "無", "非", "未必", "唔會", "不會", "窒礙", "阻礙", "打擊", "not ", "no "]
# 含否定字、但本身不是否定的词：判断前先从前文中剔除
NOT_NEGATION = ["無論", "不論", "不過", "不少", "不斷", "不僅", "不但", "無疑", "非常", "未來", "不同", "無數", "不久",
                "唔該", "唔使", "冇耐", "無限", "不錯", "沒錯"]
DOUBLE_NEG = ("難不", "難唔", "不得不", "唔得唔", "無可避免", "免不了", "不能不")
NEG_WINDOW = 5

TOPICS = {
    "rates": ("利率与按揭", ["息", "HIBOR", "拆息", "按揭", "P按", "H按", "定按", "供樓", "聯儲", "金管局", "mortgage", "rate", "hibor"]),
    "price": ("楼价走势", ["樓價", "樓市", "指數", "CCL", "見頂", "見底", "呎價", "成交價", "price"]),
    "supply": ("新盘与供应", ["新盤", "一手", "貨尾", "開價", "價單", "賣地", "推地", "地皮", "供應", "落成", "new launch", "supply"]),
    "rent": ("租金与收租", ["租金", "收租", "租客", "回報", "租樓", "劏房", "rent", "landlord", "tenant", "yield"]),
    "firstbuyer": ("上车与首置", ["上車", "首置", "首期", "印花稅", "400萬", "後生", "年輕人", "買唔起", "first home", "deposit"]),
    "distress": ("负资产与银主", ["負資產", "銀主", "斷供", "撻訂", "蝕讓", "壞帳", "negative equity", "foreclos"]),
    "mainland": ("内地买家与人才", ["內地", "北水", "人才", "高才", "優才", "深圳", "大灣區", "北上", "mainland", "talent"]),
    "economy": ("经济与就业", ["經濟", "失業", "裁員", "股市", "恒指", "人工", "收入", "GDP", "economy", "jobs"]),
    "policy": ("政策与施政", ["施政報告", "辣招", "撤辣", "政府", "政策", "預算案", "policy", "government"]),
    "quality": ("楼宇质量", ["偷工減料", "鋼筋", "鋼根", "爛尾", "結構", "驗樓", "滲水"]),
}

_ALL_SENT = sorted([(t, 1) for t in BULL_TERMS] + [(t, -1) for t in BEAR_TERMS], key=lambda x: -len(x[0]))
_NEGS = [to_trad(n).lower() for n in NEGATIONS]
_NOTNEG = [to_trad(n).lower() for n in NOT_NEGATION]


def _polarity(pre: str, pol: int) -> int:
    for w in _NOTNEG:
        pre = pre.replace(w, "  ")
    if any(d in pre for d in DOUBLE_NEG):
        return pol
    return -pol if any(n in pre for n in _NEGS) else pol


def analyse(text: str) -> dict:
    """返回 {score, stance, bull[], bear[], topics[]}；score ∈ [-1, 1]。同一词只计一次。"""
    low = to_trad(text or "").lower()
    used = [False] * len(low)
    bull, bear = [], []
    for term, pol in _ALL_SENT:
        key = to_trad(term).lower()
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
            # 前文取到上一个标点为止，避免跨句的否定词
            pre = re.split(r"[，。！？；,.!?;\s]", low[max(0, i - NEG_WINDOW - 2):i])[-1][-NEG_WINDOW:]
            p = _polarity(pre, pol)
            lst = bull if p > 0 else bear
            label = term if p == pol else f"{term}(否定)"
            if label not in lst:
                lst.append(label)
    b, s = len(bull), len(bear)
    score = 0.0 if b + s == 0 else (b - s) / (b + s) * min(1.0, math.log1p(b + s) / math.log(4))
    stance = "bull" if score > 0.15 else ("bear" if score < -0.15 else "neutral")
    topics = [k for k, (_, kws) in TOPICS.items() if any(to_trad(kw).lower() in low for kw in kws)]
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
