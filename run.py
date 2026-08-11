"""GitHub Actions 扫描入口：跑一遍扫描、写 JSON、清理3个月外的历史"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

from scanner import scan

ROOT = Path(__file__).parent
RESULTS_DIR = ROOT / "docs" / "results"
INDEX_FILE = ROOT / "docs" / "results" / "index.json"
KEEP_DAYS = 92  # 保留3个月


def utc_now_to_bj() -> dt.datetime:
    return dt.datetime.utcnow() + dt.timedelta(hours=8)


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    bj = utc_now_to_bj()
    date_str = bj.strftime("%Y-%m-%d")
    time_tag = bj.strftime("%H%M")  # 1445 或 1448
    out_file = RESULTS_DIR / f"{date_str}-{time_tag}.json"

    print(f"[run] 扫描开始 北京时间 {bj.isoformat()}")
    diagnostics: list = []
    try:
        results = scan(diagnostics=diagnostics)
    except Exception as e:
        print(f"[run] 扫描失败: {e}")
        results = []
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({
                "scan_time": bj.isoformat(),
                "date": date_str,
                "time_tag": time_tag,
                "error": str(e),
                "results": [],
                "diagnostics": diagnostics,
            }, f, ensure_ascii=False, indent=2)
        rebuild_index()
        return 1

    payload = {
        "scan_time": bj.isoformat(),
        "date": date_str,
        "time_tag": time_tag,
        "count": len(results),
        "results": results,
        "diagnostics": diagnostics,
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[run] 写入 {out_file.name}, 命中 {len(results)} 只")

    prune_old()
    rebuild_index()
    return 0


def prune_old() -> None:
    """删除超过 KEEP_DAYS 的历史 JSON"""
    cutoff = dt.date.today() - dt.timedelta(days=KEEP_DAYS)
    for f in RESULTS_DIR.glob("*.json"):
        if f.name == "index.json":
            continue
        try:
            d_str = f.stem.split("-")[:3]  # ['2026', '08', '11']
            d = dt.date(int(d_str[0]), int(d_str[1]), int(d_str[2]))
            if d < cutoff:
                f.unlink()
                print(f"[run] 清理旧文件 {f.name}")
        except Exception:
            continue


def rebuild_index() -> None:
    """重建 index.json，前端用来列出所有可选日期"""
    entries = []
    for f in sorted(RESULTS_DIR.glob("*.json"), reverse=True):
        if f.name == "index.json":
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            entries.append({
                "file": f.name,
                "date": data.get("date"),
                "time_tag": data.get("time_tag"),
                "count": data.get("count", len(data.get("results", []))),
                "scan_time": data.get("scan_time"),
            })
        except Exception:
            continue
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)
    print(f"[run] index.json 更新, {len(entries)} 条记录")


if __name__ == "__main__":
    sys.exit(main())
