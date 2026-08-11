"""数据源封装：腾讯实时/分时 + Baostock 历史K线

选择原因：香港/云端访问东方财富接口不稳定，改用腾讯（qt.gtimg.cn）+ Baostock。
"""

from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any, Dict, List, Optional

import requests

try:
    import baostock as bs
except ImportError:
    bs = None  # type: ignore

# ==================== 腾讯字段位置（qt.gtimg.cn 返回值按 ~ 分隔） ====================
# 参考：字段索引在多个财经工具库中都有稳定映射
TX = {
    "name": 1,
    "code": 2,
    "price": 3,
    "prev_close": 4,
    "open": 5,
    "volume_hand": 6,      # 累计成交量（手）
    "high_day": 33,        # 当日最高
    "low_day": 34,
    "change": 31,           # 涨跌额
    "pct_change": 32,       # 涨跌幅 %
    "amount_wan": 37,       # 累计成交额（万元）
    "turnover_rate": 38,    # 换手率 %
    "pe_ttm": 39,
    "amplitude": 43,
    "float_mv_yi": 44,      # 流通市值（亿）
    "total_mv_yi": 45,      # 总市值（亿）
    "pb": 46,
    "limit_up_price": 47,
    "limit_down_price": 48,
    "volume_ratio": 49,     # 量比
}

TENCENT_URL = "http://qt.gtimg.cn/q={codes}"
TENCENT_MINUTE_URL = "http://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"


# ==================== 通用工具 ====================

def _prefix(code: str) -> str:
    """把纯代码转成腾讯格式：sh600xxx / sz00xxxx / sz30xxxx / bj8xxxxx"""
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "51", "58")):
        return "sh" + code
    if code.startswith(("00", "30", "20")):
        return "sz" + code
    if code.startswith(("8", "43", "92")):
        return "bj" + code
    return "sh" + code


def _bs_code(code: str) -> str:
    """腾讯代码 → Baostock 代码：sh600000 → sh.600000"""
    code = code.strip()
    if code.startswith("sh") or code.startswith("sz"):
        return code[:2] + "." + code[2:]
    # 纯6位
    if code.startswith(("60", "68")):
        return "sh." + code
    return "sz." + code


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


# ==================== 通用股票池 ====================

_A_STOCK_UNIVERSE: Optional[List[str]] = None


def get_universe() -> List[str]:
    """获取全部 A股代码列表。使用一个稳定的静态方案：从 Sina 拿全量。
    返回：['000001', '000002', ...]
    """
    global _A_STOCK_UNIVERSE
    if _A_STOCK_UNIVERSE is not None:
        return _A_STOCK_UNIVERSE

    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()  # Sina 全市场
        codes = df["代码"].tolist()
        # Sina 返回可能是 sz000001 或 bj920000 前缀，也可能纯6位
        cleaned = []
        for c in codes:
            c = str(c).strip()
            if c.startswith(("sh", "sz", "bj")):
                cleaned.append(c[2:])
            else:
                cleaned.append(c.zfill(6))
        _A_STOCK_UNIVERSE = [c for c in cleaned if len(c) == 6 and c[0] in "036852469"]
        return _A_STOCK_UNIVERSE
    except Exception as e:
        raise RuntimeError(f"获取股票池失败: {e}")


# ==================== 腾讯实时快照 ====================

def _parse_tencent_line(line: str) -> Optional[Dict[str, Any]]:
    """解析单条腾讯返回，例如：v_sz000001="51~平安银行~000001~11.28~..."""
    m = re.match(r'v_([a-z]+\d+)="([^"]+)"', line.strip())
    if not m:
        return None
    prefixed = m.group(1)
    body = m.group(2)
    fields = body.split("~")
    if len(fields) < 50:
        return None
    try:
        return {
            "prefixed": prefixed,
            "code": fields[TX["code"]],
            "name": fields[TX["name"]],
            "price": _safe_float(fields[TX["price"]]),
            "prev_close": _safe_float(fields[TX["prev_close"]]),
            "open": _safe_float(fields[TX["open"]]),
            "high_day": _safe_float(fields[TX["high_day"]]),
            "low_day": _safe_float(fields[TX["low_day"]]),
            "pct_change": _safe_float(fields[TX["pct_change"]]),
            "volume_hand": _safe_float(fields[TX["volume_hand"]]),
            "amount_wan": _safe_float(fields[TX["amount_wan"]]),
            "turnover_rate": _safe_float(fields[TX["turnover_rate"]]),
            "float_mv_yi": _safe_float(fields[TX["float_mv_yi"]]),
            "total_mv_yi": _safe_float(fields[TX["total_mv_yi"]]),
            "volume_ratio": _safe_float(fields[TX["volume_ratio"]]),
            "limit_up_price": _safe_float(fields[TX["limit_up_price"]]),
            "limit_down_price": _safe_float(fields[TX["limit_down_price"]]),
        }
    except (IndexError, ValueError):
        return None


def fetch_snapshot_batch(codes: List[str], batch_size: int = 50, retry: int = 2) -> List[Dict[str, Any]]:
    """批量拉取实时快照，自动分批 + 重试。
    codes: 纯6位代码列表
    返回：list of snapshot dict
    """
    results: List[Dict[str, Any]] = []
    prefixed = [_prefix(c) for c in codes]
    for i in range(0, len(prefixed), batch_size):
        batch = prefixed[i:i + batch_size]
        url = TENCENT_URL.format(codes=",".join(batch))
        for attempt in range(retry + 1):
            try:
                r = requests.get(url, timeout=10)
                r.encoding = "gbk"
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                for line in r.text.split("\n"):
                    parsed = _parse_tencent_line(line)
                    if parsed:
                        results.append(parsed)
                break
            except Exception as e:
                if attempt == retry:
                    print(f"[snapshot] batch {i}-{i+batch_size} 最终失败: {e}")
                else:
                    time.sleep(0.5)
        time.sleep(0.05)  # 每批温柔间隔
    return results


# ==================== 腾讯分时数据 ====================

def fetch_minute(code: str, retry: int = 2) -> List[Dict[str, Any]]:
    """获取当日1分钟数据，返回 [{'time':'HHMM', 'price':float, 'cum_vol':int, 'cum_amt':float}, ...]"""
    prefixed = _prefix(code)
    url = TENCENT_MINUTE_URL.format(code=prefixed)
    for attempt in range(retry + 1):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            j = r.json()
            data = j.get("data", {}).get(prefixed, {}).get("data", {}).get("data", [])
            out: List[Dict[str, Any]] = []
            for entry in data:
                parts = entry.split()
                if len(parts) < 3:
                    continue
                out.append({
                    "time": parts[0],
                    "price": _safe_float(parts[1]),
                    "cum_vol": _safe_float(parts[2]),
                    "cum_amt": _safe_float(parts[3]) if len(parts) > 3 else 0.0,
                })
            return out
        except Exception as e:
            if attempt == retry:
                return []
            time.sleep(0.3)
    return []


# ==================== Baostock 历史K线 ====================

_BS_LOGGED_IN = False


def bs_login() -> None:
    global _BS_LOGGED_IN
    if _BS_LOGGED_IN or bs is None:
        return
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"Baostock login failed: {lg.error_msg}")
    _BS_LOGGED_IN = True


def bs_logout() -> None:
    global _BS_LOGGED_IN
    if _BS_LOGGED_IN and bs is not None:
        bs.logout()
        _BS_LOGGED_IN = False


def fetch_history(code: str, days: int = 40) -> List[Dict[str, Any]]:
    """通过 Baostock 拿最近 N 天日K线（含 turn 换手率、pctChg 涨跌幅）"""
    if bs is None:
        return []
    bs_login()
    bs_code = _bs_code(code)
    end = dt.date.today()
    start = end - dt.timedelta(days=days * 2 + 20)  # 冗余覆盖节假日
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,open,high,low,close,preclose,volume,amount,turn,pctChg",
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        frequency="d",
        adjustflag="3",
    )
    rows: List[Dict[str, Any]] = []
    if rs.error_code != "0":
        return []
    while rs.next():
        r = rs.get_row_data()
        try:
            rows.append({
                "date": r[0],
                "open": _safe_float(r[1]),
                "high": _safe_float(r[2]),
                "low": _safe_float(r[3]),
                "close": _safe_float(r[4]),
                "preclose": _safe_float(r[5]),
                "volume": _safe_float(r[6]),
                "amount": _safe_float(r[7]),
                "turn": _safe_float(r[8]),
                "pctChg": _safe_float(r[9]),
            })
        except Exception:
            continue
    return rows[-days:]
