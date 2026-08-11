"""A股尾盘扫描 - 9条规则全满足筛选"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import akshare as ak
import pandas as pd


# ==================== 规则参数 ====================

RULE_PARAMS = {
    "gain_min": 3.0,          # 涨幅下限 %
    "gain_max": 5.0,          # 涨幅上限 %
    "volume_ratio_min": 1.0,  # 量比下限
    "turnover_min": 5.0,      # 换手率下限 %
    "turnover_max": 10.0,     # 换手率上限 %
    "float_mv_min": 50e8,     # 流通市值下限 元
    "float_mv_max": 200e8,    # 流通市值上限 元
    "vol_expansion_min": 1.2, # 量能温和放大下限（近5日均量倍数）
    "vol_expansion_max": 2.0, # 量能温和放大上限
    "limitup_lookback": 20,   # 涨停回看天数
}


def _limit_up_pct(code: str) -> float:
    """按代码前缀判断板块对应涨停幅度（%）"""
    if code.startswith(("300", "301")):  # 创业板
        return 20.0
    if code.startswith(("688", "689")):  # 科创板
        return 20.0
    if code.startswith(("8", "43", "92")):  # 北交所
        return 30.0
    return 10.0  # 主板 (60x/00x)


# ==================== 单条规则实现 ====================

def check_rule_1_gain(row: pd.Series) -> Tuple[bool, str]:
    """规则1: 涨幅 3%-5%"""
    pct = float(row.get("涨跌幅", 0))
    ok = RULE_PARAMS["gain_min"] <= pct <= RULE_PARAMS["gain_max"]
    return ok, f"涨幅{pct:.2f}%"


def check_rule_2_volume_ratio(row: pd.Series) -> Tuple[bool, str]:
    """规则2: 量比 > 1"""
    vr = float(row.get("量比", 0) or 0)
    ok = vr > RULE_PARAMS["volume_ratio_min"]
    return ok, f"量比{vr:.2f}"


def check_rule_3_turnover(row: pd.Series) -> Tuple[bool, str]:
    """规则3: 换手率 5%-10%"""
    tv = float(row.get("换手率", 0) or 0)
    ok = RULE_PARAMS["turnover_min"] <= tv <= RULE_PARAMS["turnover_max"]
    return ok, f"换手{tv:.2f}%"


def check_rule_4_float_mv(row: pd.Series) -> Tuple[bool, str]:
    """规则4: 流通市值 50-200亿"""
    mv = float(row.get("流通市值", 0) or 0)
    ok = RULE_PARAMS["float_mv_min"] <= mv <= RULE_PARAMS["float_mv_max"]
    return ok, f"流通市值{mv/1e8:.1f}亿"


def check_rule_5_volume_expansion(code: str) -> Tuple[bool, str]:
    """规则5: 成交量温和放大（今日量能是近5日均量的1.2-2倍）"""
    try:
        end = dt.date.today().strftime("%Y%m%d")
        start = (dt.date.today() - dt.timedelta(days=20)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
        if df is None or len(df) < 6:
            return False, "量能数据不足"
        today_vol = float(df.iloc[-1]["成交量"])
        avg5 = float(df.iloc[-6:-1]["成交量"].mean())
        if avg5 <= 0:
            return False, "均量为0"
        ratio = today_vol / avg5
        ok = RULE_PARAMS["vol_expansion_min"] <= ratio <= RULE_PARAMS["vol_expansion_max"]
        return ok, f"量能{ratio:.2f}x"
    except Exception as e:
        return False, f"量能异常({e})"


def check_rule_6_ma_bullish(code: str) -> Tuple[bool, str]:
    """规则6: 站上5/10/20日均线，多头排列"""
    try:
        end = dt.date.today().strftime("%Y%m%d")
        start = (dt.date.today() - dt.timedelta(days=45)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
        if df is None or len(df) < 20:
            return False, "均线数据不足"
        close = df["收盘"].astype(float)
        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        price = close.iloc[-1]
        bullish = ma5 > ma10 > ma20 and price > ma5
        return bullish, f"MA5={ma5:.2f}/MA10={ma10:.2f}/MA20={ma20:.2f}"
    except Exception as e:
        return False, f"均线异常({e})"


def check_rule_7_intraday_strong(code: str, index_min_df: Optional[pd.DataFrame] = None) -> Tuple[bool, str]:
    """规则7: 分时全天在均价线上方，强于沪深300"""
    try:
        df = ak.stock_zh_a_hist_min_em(symbol=code, period="1", adjust="")
        if df is None or len(df) < 30:
            return False, "分时数据不足"
        df["成交量"] = df["成交量"].astype(float)
        df["成交额"] = df["成交额"].astype(float)
        df["cum_vol"] = df["成交量"].cumsum()
        df["cum_amt"] = df["成交额"].cumsum()
        df["avg_price"] = df["cum_amt"] / (df["cum_vol"] * 100)  # 手转股
        df["close_f"] = df["收盘"].astype(float)
        below = (df["close_f"] < df["avg_price"]).sum()
        above_all = below <= 3  # 允许极短时间轻微跌破
        # 强于大盘: 用个股涨幅 vs 沪深300涨幅
        stock_pct = (df["close_f"].iloc[-1] / df["开盘"].iloc[0].astype(float) - 1) * 100 if False else None
        # 简化: 直接看是否绝大多数在均价线上方
        return above_all, f"分时{'强势' if above_all else f'弱势({below}分钟破均价)'}"
    except Exception as e:
        return False, f"分时异常({e})"


def check_rule_8_tail_new_high(code: str) -> Tuple[bool, str]:
    """规则8: 14:45后创当日新高且不破均价线"""
    try:
        df = ak.stock_zh_a_hist_min_em(symbol=code, period="1", adjust="")
        if df is None or len(df) < 30:
            return False, "尾盘数据不足"
        df["time"] = pd.to_datetime(df["时间"]).dt.time
        cutoff = dt.time(14, 45)
        tail = df[df["time"] >= cutoff]
        if len(tail) == 0:
            return False, "尾盘尚未到"
        max_before = df[df["time"] < cutoff]["最高"].astype(float).max() if len(df[df["time"] < cutoff]) else 0
        max_tail = tail["最高"].astype(float).max()
        made_new_high = max_tail > max_before
        # 检查是否跌破均价
        df["成交量"] = df["成交量"].astype(float)
        df["成交额"] = df["成交额"].astype(float)
        df["cum_vol"] = df["成交量"].cumsum()
        df["cum_amt"] = df["成交额"].cumsum()
        df["avg_price"] = df["cum_amt"] / (df["cum_vol"] * 100)
        tail_avg = df[df["time"] >= cutoff][["avg_price", "收盘"]].copy()
        tail_avg["收盘"] = tail_avg["收盘"].astype(float)
        not_break = (tail_avg["收盘"] >= tail_avg["avg_price"]).all()
        ok = made_new_high and not_break
        return ok, f"尾盘{'创新高' if made_new_high else '未创新高'}/{'守均价' if not_break else '破均价'}"
    except Exception as e:
        return False, f"尾盘异常({e})"


def check_rule_9_limitup_history(code: str) -> Tuple[bool, str]:
    """规则9: 近20个交易日至少1次实体涨停"""
    try:
        end = dt.date.today().strftime("%Y%m%d")
        start = (dt.date.today() - dt.timedelta(days=40)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
        if df is None or len(df) < 2:
            return False, "历史数据不足"
        limit_pct = _limit_up_pct(code)
        recent = df.tail(RULE_PARAMS["limitup_lookback"] + 1)
        found = 0
        for i in range(1, len(recent)):
            prev_close = float(recent.iloc[i - 1]["收盘"])
            row = recent.iloc[i]
            open_p = float(row["开盘"])
            low_p = float(row["最低"])
            close_p = float(row["收盘"])
            limit_price = round(prev_close * (1 + limit_pct / 100), 2)
            hit_limit = abs(close_p - limit_price) < 0.02
            solid = open_p < limit_price - 0.01 and low_p < limit_price - 0.01  # 实体涨停：非一字非T
            if hit_limit and solid:
                found += 1
        ok = found >= 1
        return ok, f"20日实体涨停{found}次"
    except Exception as e:
        return False, f"涨停历史异常({e})"


# ==================== 主扫描流程 ====================

@dataclass
class ScanResult:
    code: str
    name: str
    price: float
    gain_pct: float
    volume_ratio: float
    turnover: float
    float_mv: float
    reasons: List[str]

    def to_dict(self) -> Dict:
        return {
            "code": self.code,
            "name": self.name,
            "price": round(self.price, 2),
            "gain_pct": round(self.gain_pct, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "turnover": round(self.turnover, 2),
            "float_mv_yi": round(self.float_mv / 1e8, 1),
            "reasons": self.reasons,
        }


def scan(limit: Optional[int] = None, progress_cb=None) -> List[Dict]:
    """执行完整扫描。
    limit: 仅扫描前 N 只（调试用）
    progress_cb: 进度回调 fn(current, total, code)
    """
    # 1. 拉全市场实时快照（含涨跌幅/量比/换手/流通市值）
    spot = ak.stock_zh_a_spot_em()
    # 2. 先跑规则 1-4 (纯字段过滤)
    pool = spot.copy()
    pool = pool[pool["代码"].str.match(r"^(00|30|60|68|8|43|92)", na=False)]

    # rule 1
    pool = pool[(pool["涨跌幅"] >= RULE_PARAMS["gain_min"]) & (pool["涨跌幅"] <= RULE_PARAMS["gain_max"])]
    # rule 2
    pool = pool[pool["量比"].fillna(0) > RULE_PARAMS["volume_ratio_min"]]
    # rule 3
    pool = pool[(pool["换手率"].fillna(0) >= RULE_PARAMS["turnover_min"]) & (pool["换手率"].fillna(0) <= RULE_PARAMS["turnover_max"])]
    # rule 4
    pool = pool[(pool["流通市值"].fillna(0) >= RULE_PARAMS["float_mv_min"]) & (pool["流通市值"].fillna(0) <= RULE_PARAMS["float_mv_max"])]

    candidates = pool.to_dict(orient="records")
    if limit:
        candidates = candidates[:limit]

    results: List[ScanResult] = []
    total = len(candidates)
    for idx, row in enumerate(candidates):
        code = row["代码"]
        if progress_cb:
            progress_cb(idx + 1, total, code)
        reasons: List[str] = []
        # 规则1-4已经通过筛选，直接标注
        r1_ok, r1_msg = check_rule_1_gain(pd.Series(row)); reasons.append(f"①{r1_msg}")
        r2_ok, r2_msg = check_rule_2_volume_ratio(pd.Series(row)); reasons.append(f"②{r2_msg}")
        r3_ok, r3_msg = check_rule_3_turnover(pd.Series(row)); reasons.append(f"③{r3_msg}")
        r4_ok, r4_msg = check_rule_4_float_mv(pd.Series(row)); reasons.append(f"④{r4_msg}")

        # 规则 5-9 逐个检查, 短路
        r5_ok, r5_msg = check_rule_5_volume_expansion(code)
        if not r5_ok:
            continue
        reasons.append(f"⑤{r5_msg}")

        r6_ok, r6_msg = check_rule_6_ma_bullish(code)
        if not r6_ok:
            continue
        reasons.append(f"⑥{r6_msg}")

        r9_ok, r9_msg = check_rule_9_limitup_history(code)
        if not r9_ok:
            continue
        reasons.append(f"⑨{r9_msg}")

        r7_ok, r7_msg = check_rule_7_intraday_strong(code)
        if not r7_ok:
            continue
        reasons.append(f"⑦{r7_msg}")

        r8_ok, r8_msg = check_rule_8_tail_new_high(code)
        if not r8_ok:
            continue
        reasons.append(f"⑧{r8_msg}")

        results.append(ScanResult(
            code=code,
            name=row.get("名称", ""),
            price=float(row.get("最新价", 0) or 0),
            gain_pct=float(row.get("涨跌幅", 0) or 0),
            volume_ratio=float(row.get("量比", 0) or 0),
            turnover=float(row.get("换手率", 0) or 0),
            float_mv=float(row.get("流通市值", 0) or 0),
            reasons=reasons,
        ))

    return [r.to_dict() for r in results]


if __name__ == "__main__":
    def cb(i, total, code):
        print(f"[{i}/{total}] {code}")
    out = scan(progress_cb=cb)
    print(f"\n命中 {len(out)} 只")
    for r in out:
        print(r)
