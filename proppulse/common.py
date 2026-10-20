# -*- coding: utf-8 -*-
"""公共工具：时间、HTTP（含 robots.txt 检查）、JSON 读写、印花税、数字解析。  v7.3

v7.3 修正：robots.txt 检查改按 RFC 9309 处理——
  robots.txt 返回 4xx（如 403/404，即「没有提供规则」）→ 视为允许；
  返回 5xx 或连不上 → 视为暂时不允许。
  旧版用 Python 默认规则，robots.txt 一返回 403 就当成「全站禁止」，可能误跳过政府数据与新闻 RSS。
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "market_data.json"
SEED_FILE = ROOT / "seed_listings.json"
INBOX_DIR = ROOT / "inbox"
DEBUG_DIR = ROOT / "debug"

HKT = dt.timezone(dt.timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36 HKPropPulse/7.3 (personal research; low frequency)")


def now_hkt() -> dt.datetime:
    return dt.datetime.now(HKT)


def today() -> str:
    return now_hkt().strftime("%Y-%m-%d")


def days_ago(n: int) -> dt.datetime:
    return now_hkt() - dt.timedelta(days=n)


def parse_date(s) -> dt.datetime | None:
    """尽量解析各种日期格式，返回带 HKT 时区的 datetime。"""
    if not s:
        return None
    if isinstance(s, dt.datetime):
        return s if s.tzinfo else s.replace(tzinfo=HKT)
    s = str(s).strip()
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(s)
        if d:
            return d.astimezone(HKT) if d.tzinfo else d.replace(tzinfo=HKT)
    except Exception:
        pass
    m = re.search(r"(20\d{2}|\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        y = int(m.group(1))
        y = y + 2000 if y < 100 else y
        try:
            return dt.datetime(y, int(m.group(2)), int(m.group(3)), tzinfo=HKT)
        except ValueError:
            return None
    m = re.search(r"(\d+)\s*(分鐘|分钟|小時|小时|天|日)前", s)
    if m:
        n, u = int(m.group(1)), m.group(2)
        delta = dt.timedelta(minutes=n) if u in ("分鐘", "分钟") else (
            dt.timedelta(hours=n) if u in ("小時", "小时") else dt.timedelta(days=n))
        return now_hkt() - delta
    if "前天" in s:
        return now_hkt() - dt.timedelta(days=2)
    if "昨天" in s:
        return now_hkt() - dt.timedelta(days=1)
    return None


# ---------------------------------------------------------------- HTTP
_robots: dict = {}
ROBOTS_LOG: dict = {}          # base -> 状态说明（写入 pipeline_status 便于排查）


def robots_allows(url: str, ua: str = UA) -> bool:
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                rp.parse(r.read().decode("utf-8", errors="replace").splitlines())
            _robots[base] = rp
            ROBOTS_LOG[base] = "robots.txt 已读取"
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                _robots[base] = None                       # 没有规则 → 允许
                ROBOTS_LOG[base] = f"robots.txt 返回 {e.code}，按 RFC 9309 视为允许"
            else:
                _robots[base] = False
                ROBOTS_LOG[base] = f"robots.txt 返回 {e.code}，暂时视为不允许"
        except Exception as e:
            _robots[base] = False
            ROBOTS_LOG[base] = f"robots.txt 无法连接（{str(e)[:60]}），暂时视为不允许"
    rp = _robots[base]
    if rp is None:
        return True
    if rp is False:
        return False
    return rp.can_fetch(ua, url)


class FetchError(Exception):
    pass


def http_get(url: str, *, timeout: int = 20, headers: dict | None = None, check_robots: bool = True,
             retries: int = 2, as_json: bool = False):
    """GET，带 UA / gzip / 重试；默认先检查 robots.txt。"""
    if check_robots and not robots_allows(url):
        base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))
        raise FetchError(f"robots.txt 不允许或无法确认：{url}（{ROBOTS_LOG.get(base, '')}）")
    h = {"User-Agent": UA, "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en;q=0.7",
         "Accept-Encoding": "gzip"}
    h.update(headers or {})
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                charset = r.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                return json.loads(text) if as_json else text
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (i + 1))
    raise FetchError(f"{url}: {last}")


# ---------------------------------------------------------------- JSON IO
def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 数值
def num(s) -> float | None:
    if s is None or isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(s))
    return float(m.group(0).replace(",", "")) if m else None


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def stamp_duty_hkd(price_hkd: float) -> float:
    """香港住宅从价印花税第 2 标准（≤400 万为 $100，含边际缓冲区）。"""
    p = price_hkd
    bands = [
        (4_000_000, lambda p: 100), (4_323_780, lambda p: 100 + (p - 4_000_000) * 0.20),
        (4_500_000, lambda p: p * 0.015), (4_935_480, lambda p: 67_500 + (p - 4_500_000) * 0.10),
        (6_000_000, lambda p: p * 0.0225), (6_642_860, lambda p: 135_000 + (p - 6_000_000) * 0.10),
        (9_000_000, lambda p: p * 0.03), (10_080_000, lambda p: 270_000 + (p - 9_000_000) * 0.10),
        (20_000_000, lambda p: p * 0.0375), (21_739_120, lambda p: 750_000 + (p - 20_000_000) * 0.10),
    ]
    for upper, fn in bands:
        if p <= upper:
            return round(fn(p))
    return round(p * 0.0425)


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
          .replace("&quot;", '"').replace("&#39;", "'"))
    return " ".join(s.split())


def short(s: str, n: int = 100) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def log(stage: str, msg: str) -> None:
    print(f"[{now_hkt():%H:%M:%S}] [{stage}] {msg}", flush=True)
