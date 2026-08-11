"""A股尾盘扫描 - 9条规则全满足筛选（腾讯 + Baostock 版）"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from data_sources import (
    bs_login,
    bs_logout,
    fetch_history,
    fetch_minute,
    fetch_snapshot_batch,
    get_universe,
)


# ==================== 规则参数 ====================

RULE_PARAMS = {
    "gain_min": 3.0,
    "gain_max": 5.0,
    "volume_ratio_min": 1.0,
    "turnover_min": 5.0,
    "turnover_max": 10.0,
    "float_mv_min_yi": 50.0,
    "float_mv_max_yi": 200.0,
    "vol_expansion_min": 1.2,
    "vol_expansion_max": 2.0,
    "limitup_lookback": 20,
    "tail_new_high_after": "1445",  # 14:45 之后
    "intraday_below_avg_tolerance_min": 3,  # 允许最多3分钟破均价线
}


def _limit_up_pct(code: str) -> float:
    """按代码前缀返回涨停幅度 %"""
    if code.startswith(("300", "301")):
        return 20.0
    if code.startswith(("688", "689")):
        return 20.0
    if code.startswith(("8", "43", "92")):
        return 30.0
    return 10.0


# ==================== 规则实现 ====================

def check_rule_1(snap: Dict) -> Tuple[bool, str]:
    pct = snap["pct_change"]
    ok = RULE_PARAMS["gain_min"] <= pct <= RULE_PARAMS["gain_max"]
    return ok, f"涨幅{pct:.2f}%"


def check_rule_2(snap: Dict) -> Tuple[bool, str]:
    vr = snap["volume_ratio"]
    ok = vr > RULE_PARAMS["volume_ratio_min"]
    return ok, f"量比{vr:.2f}"


def check_rule_3(snap: Dict) -> Tuple[bool, str]:
    tv = snap["turnover_rate"]
    ok = RULE_PARAMS["turnover_min"] <= tv <= RULE_PARAMS["turnover_max"]
    return ok, f"换手{tv:.2f}%"


def check_rule_4(snap: Dict) -> Tuple[bool, str]:
    mv = snap["float_mv_yi"]
    ok = RULE_PARAMS["float_mv_min_yi"] <= mv <= RULE_PARAMS["float_mv_max_yi"]
    return ok, f"流通市值{mv:.1f}亿"


def check_rule_5(history: List[Dict], snap: Dict) -> Tuple[bool, str]:
    """今日成交量 / 近5日均量 ∈ [1.2, 2]"""
    if len(history) < 5:
        return False, "量能数据不足"
    today_vol = snap["volume_hand"] * 100  # 手 → 股
    avg5 = sum(h["volume"] for h in history[-5:]) / 5
    if avg5 <= 0:
        return False, "均量为0"
    ratio = today_vol / avg5
    ok = RULE_PARAMS["vol_expansion_min"] <= ratio <= RULE_PARAMS["vol_expansion_max"]
    return ok, f"量能{ratio:.2f}x"


def check_rule_6(history: List[Dict], snap: Dict) -> Tuple[bool, str]:
    """MA5>MA10>MA20 且价格站上 MA5（用截止昨日的均线 + 今日价）"""
    if len(history) < 20:
        return False, "均线数据不足"
    closes = [h["close"] for h in history]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    price = snap["price"]
    ok = ma5 > ma10 > ma20 and price > ma5
    return ok, f"MA5={ma5:.2f}/MA10={ma10:.2f}/MA20={ma20:.2f}"


def check_rule_7(minute: List[Dict]) -> Tuple[bool, str]:
    """分时全天在均价线上方（允许 <= 3 分钟破位）"""
    if len(minute) < 30:
        return False, "分时数据不足"
    below = 0
    for row in minute:
        if row["cum_vol"] <= 0:
            continue
        avg = (row["cum_amt"] / row["cum_vol"]) if row["cum_amt"] > 0 else row["price"]
        if row["price"] < avg:
            below += 1
    ok = below <= RULE_PARAMS["intraday_below_avg_tolerance_min"]
    return ok, f"分时{'强势' if ok else f'弱势({below}分钟破均价)'}"


def check_rule_8(minute: List[Dict]) -> Tuple[bool, str]:
    """14:45 后创当日新高且不破均价线"""
    if len(minute) < 30:
        return False, "尾盘数据不足"
    cutoff = RULE_PARAMS["tail_new_high_after"]  # '1445'
    before = [r for r in minute if r["time"] < cutoff]
    tail = [r for r in minute if r["time"] >= cutoff]
    if not tail:
        return False, "尾盘尚未到"
    max_before = max((r["price"] for r in before), default=0)
    max_tail = max(r["price"] for r in tail)
    made_new_high = max_tail > max_before
    # 检查尾盘阶段是否跌破均价
    breaks = 0
    for r in tail:
        if r["cum_vol"] <= 0:
            continue
        avg = r["cum_amt"] / r["cum_vol"] if r["cum_amt"] > 0 else r["price"]
        if r["price"] < avg:
            breaks += 1
    not_break = breaks == 0
    ok = made_new_high and not_break
    tag = ("创新高" if made_new_high else "未创新高") + "/" + ("守均价" if not_break else "破均价")
    return ok, f"尾盘{tag}"


def check_rule_9(history: List[Dict], code: str) -> Tuple[bool, str]:
    """近20日至少1次实体涨停（排除一字板、T字板）"""
    if len(history) < 2:
        return False, "历史数据不足"
    limit_pct = _limit_up_pct(code)
    recent = history[-(RULE_PARAMS["limitup_lookback"] + 1):]
    found = 0
    for i in range(1, len(recent)):
        prev = recent[i - 1]["close"]
        row = recent[i]
        limit_price = round(prev * (1 + limit_pct / 100), 2)
        hit_limit = abs(row["close"] - limit_price) < 0.02
        # 实体涨停 = 命中涨停 且 开盘/最低价 < 涨停价（非一字非T）
        solid = (row["open"] < limit_price - 0.01) and (row["low"] < limit_price - 0.01)
        if hit_limit and solid:
            found += 1
    ok = found >= 1
    return ok, f"20日实体涨停{found}次"


# ==================== 主扫描流程 ====================

@dataclass
class ScanResult:
    code: str
    name: str
    price: float
    gain_pct: float
    volume_ratio: float
    turnover: float
    float_mv_yi: float
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "code": self.code,
            "name": self.name,
            "price": round(self.price, 2),
            "gain_pct": round(self.gain_pct, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "turnover": round(self.turnover, 2),
            "float_mv_yi": round(self.float_mv_yi, 1),
            "reasons": self.reasons,
        }


def scan(limit: Optional[int] = None,
         progress_cb: Optional[Callable[[int, int, str], None]] = None) -> List[Dict]:
    """执行完整扫描。"""
    print("[scan] 拉取股票池…")
    universe = get_universe()
    print(f"[scan] 股票池 {len(universe)} 只")

    print("[scan] 批量拉快照…")
    snapshots = fetch_snapshot_batch(universe)
    print(f"[scan] 获得快照 {len(snapshots)} 只")

    # 先用快照字段过滤规则 1-4
    candidates: List[Dict] = []
    for s in snapshots:
        if not (RULE_PARAMS["gain_min"] <= s["pct_change"] <= RULE_PARAMS["gain_max"]):
            continue
        if s["volume_ratio"] <= RULE_PARAMS["volume_ratio_min"]:
            continue
        if not (RULE_PARAMS["turnover_min"] <= s["turnover_rate"] <= RULE_PARAMS["turnover_max"]):
            continue
        if not (RULE_PARAMS["float_mv_min_yi"] <= s["float_mv_yi"] <= RULE_PARAMS["float_mv_max_yi"]):
            continue
        candidates.append(s)

    print(f"[scan] 规则1-4通过 {len(candidates)} 只候选")
    if limit:
        candidates = candidates[:limit]

    bs_login()
    results: List[ScanResult] = []
    total = len(candidates)

    try:
        for idx, snap in enumerate(candidates):
            code = snap["code"]
            if progress_cb:
                progress_cb(idx + 1, total, code)

            reasons: List[str] = []
            r1, m1 = check_rule_1(snap); reasons.append(f"①{m1}")
            r2, m2 = check_rule_2(snap); reasons.append(f"②{m2}")
            r3, m3 = check_rule_3(snap); reasons.append(f"③{m3}")
            r4, m4 = check_rule_4(snap); reasons.append(f"④{m4}")

            history = fetch_history(code, days=40)
            if not history:
                continue

            r5, m5 = check_rule_5(history, snap)
            if not r5:
                continue
            reasons.append(f"⑤{m5}")

            r6, m6 = check_rule_6(history, snap)
            if not r6:
                continue
            reasons.append(f"⑥{m6}")

            r9, m9 = check_rule_9(history, code)
            if not r9:
                continue
            reasons.append(f"⑨{m9}")

            minute = fetch_minute(code)
            if not minute:
                continue

            r7, m7 = check_rule_7(minute)
            if not r7:
                continue
            reasons.append(f"⑦{m7}")

            r8, m8 = check_rule_8(minute)
            if not r8:
                continue
            reasons.append(f"⑧{m8}")

            results.append(ScanResult(
                code=code,
                name=snap["name"],
                price=snap["price"],
                gain_pct=snap["pct_change"],
                volume_ratio=snap["volume_ratio"],
                turnover=snap["turnover_rate"],
                float_mv_yi=snap["float_mv_yi"],
                reasons=reasons,
            ))
    finally:
        bs_logout()

    return [r.to_dict() for r in results]


if __name__ == "__main__":
    def cb(i, total, code):
        if i % 10 == 0 or i == total:
            print(f"  [{i}/{total}] {code}")

    out = scan(progress_cb=cb)
    print(f"\n命中 {len(out)} 只")
    for r in out:
        print(r)
