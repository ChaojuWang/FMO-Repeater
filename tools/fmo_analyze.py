#!/usr/bin/env python3
"""分析 fmo_capture.py 转存的 JSONL，输出 stream_begin_utc 变化规律。

用法：
    python tools/fmo_analyze.py /tmp/fmo_capture.jsonl

输出：
  - 每包时间线（接收时刻 / uid / callsign / stream_begin_utc / timestamp）
  - 相邻包的 stream_begin_utc 是否变化（= 持续说话时该字段是否滚动）
  - 同一 stream_begin_utc 承载的包数与时间跨度
"""

import argparse
import json
from collections import OrderedDict


def main():
    ap = argparse.ArgumentParser(description="分析 FMO 抓包 JSONL")
    ap.add_argument("file", help="fmo_capture.py 输出的 JSONL 文件")
    args = ap.parse_args()

    rows = []
    with open(args.file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "error" in row:
                continue
            rows.append(row)

    if not rows:
        print("无有效包记录")
        return

    print(f"共 {len(rows)} 个包\n")
    print(f"{'recv_ts':>8} {'uid':>6} {'callsign':<10} "
          f"{'stream_begin_utc':>16} {'timestamp':>16}")
    print("-" * 60)

    sbu_changes = 0
    prev = None
    for r in rows:
        marker = ""
        if prev is not None and r["stream_begin_utc"] != prev["stream_begin_utc"]:
            sbu_changes += 1
            marker = "  <-- sbu 变化"
        print(f"{r['recv_ts']:>8} {r['uid']:>6} {r['callsign']:<10} "
              f"{r['stream_begin_utc']:>16} {r['timestamp']:>16}{marker}")
        prev = r

    groups = OrderedDict()
    for r in rows:
        groups.setdefault(r["stream_begin_utc"], []).append(r)

    print("\n=== 同一 stream_begin_utc 的分组统计 ===")
    for sbu, rs in groups.items():
        span = rs[-1]["recv_ts"] - rs[0]["recv_ts"]
        print(f"sbu={sbu:<12} 包数={len(rs):<3} 时间跨度={span:.2f}s "
              f"uid={rs[0]['uid']} {rs[0]['callsign']}")

    print(f"\n相邻包 stream_begin_utc 变化次数: {sbu_changes}/{len(rows)-1}")


if __name__ == "__main__":
    main()
