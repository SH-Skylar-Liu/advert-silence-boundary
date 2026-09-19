#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compare two runs of the pipeline over the same recordings, ledger against ledger.

Two questions, both answered from the ledgers alone (no video decoding):

1. Per recording, which run lands closer to the scheduled slot lengths? A pooled
   median hides the case where one run wins on some recordings and loses on others,
   which is exactly what happened between the ffmpeg and scenedetect backends
   (equivalent over twelve 2020 recordings, scenedetect clearly ahead on one 2024
   recording).

2. Where does each run's cut point sit relative to the END of the silence, i.e.
   the moment audio resumes? That position comes from the audio and owes nothing
   to the picture detector, so it catches a systematic drift that the slot metric
   cannot see: clip length is a difference, so a shift applied to every cut point
   cancels out. Needs the silence_start_s / silence_end_s columns (v1.3+).

中文：用两份台账比较同一批录制上的两次运行，不重新解码视频。
（1）**分录制**比较与排期档位的偏差——合并中位数会掩盖"有的录制赢、有的录制输"；
（2）比较切点相对**静音结束**（声音重新出现）的位置——该位置来自音频，与画面检测器无关，
因此能发现档位指标看不见的系统性漂移（时长是减法，整体平移会被抵消）。

Usage
-----
    python3 compare_runs.py A.csv B.csv
    python3 compare_runs.py A.csv B.csv --labels ffmpeg scenedetect
    python3 compare_runs.py A.csv B.csv --fps 25
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict

# Slot lengths sold by UK broadcasters; the same list compare_end_modes.py uses.
# 中文：英国电视广告的固定档位，与 compare_end_modes.py 保持一致。
SLOTS = [10, 15, 20, 30, 40, 45, 60, 90]


def deviation(duration: float) -> float:
    """Absolute difference between a clip's duration and the nearest slot."""
    return min(abs(duration - s) for s in SLOTS)


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def per_recording(rows: list[dict]) -> dict[str, list[float]]:
    """Variant C deviations, grouped by recording.

    中文：按 (录制, 广告段) 分组后，计算每个切点在变体 C 下的档位偏差；
    分组必须带上录制名，因为每个录制的广告段编号都从 1 开始。
    """
    by_break: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_break[(r.get("recording", ""), r["break"])].append(r)

    out: dict[str, list[float]] = defaultdict(list)
    for (rec, _), rs in by_break.items():
        rs.sort(key=lambda r: int(r["pos"]))
        for i, r in enumerate(rs):
            nxt = float(rs[i + 1]["start_s"]) if i + 1 < len(rs) else float(r["end_s"])
            out[rec].append(deviation(nxt - float(r["start_s"])))
    return out


def drift(rows: list[dict]) -> list[float]:
    """start_s - silence_end_s, in milliseconds. Empty when the column is absent.

    中文：切点减去静音结束时刻（毫秒）。负值 = 切点落在声音恢复之前。
    """
    return sorted((float(r["start_s"]) - float(r["silence_end_s"])) * 1000
                  for r in rows if r.get("silence_end_s"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger_a")
    ap.add_argument("ledger_b")
    ap.add_argument("--labels", nargs=2, default=["A", "B"])
    ap.add_argument("--fps", type=float, default=25.0)
    ap.add_argument("--tol", type=float, default=0.005,
                    help="median difference below this counts as a draw (default 5 ms)")
    a = ap.parse_args()

    la, lb = a.labels
    rows_a, rows_b = load(a.ledger_a), load(a.ledger_b)
    print(f"{la}: {a.ledger_a}  ({len(rows_a)} cuts)")
    print(f"{lb}: {a.ledger_b}  ({len(rows_b)} cuts)\n")

    # --- 1. slot deviation, per recording -----------------------------------
    pa, pb = per_recording(rows_a), per_recording(rows_b)
    print("Slot deviation, median per recording (variant C)")
    print(f"{'recording':<36}{'n':>4}{la:>10}{lb:>10}   winner")
    wins = {la: 0, lb: 0, "=": 0}
    for rec in sorted(pa):
        if rec not in pb:
            continue
        ma, mb = statistics.median(pa[rec]), statistics.median(pb[rec])
        w = lb if mb < ma - a.tol else (la if mb > ma + a.tol else "=")
        wins[w] += 1
        print(f"{rec[:35]:<36}{len(pa[rec]):>4}{ma:>10.3f}{mb:>10.3f}   {w}")
    all_a = [v for vs in pa.values() for v in vs]
    all_b = [v for vs in pb.values() for v in vs]
    print(f"{'POOLED':<36}{len(all_a):>4}"
          f"{statistics.median(all_a):>10.3f}{statistics.median(all_b):>10.3f}")
    print(f"recordings won: {la} {wins[la]}   {lb} {wins[lb]}   draw {wins['=']}\n")

    # --- 2. drift against the silence end (audio) ---------------------------
    print("Cut point relative to the silence END (audio; negative = before audio resumes)")
    frame_ms = 1000.0 / a.fps
    for label, rows in ((la, rows_a), (lb, rows_b)):
        d = drift(rows)
        if not d:
            print(f"  {label:<12} no silence_end_s column (ledger written before v1.3)")
            continue
        n = len(d)
        med = d[n // 2]
        print(f"  {label:<12} n={n:<5} median {med:+7.0f} ms ({med / frame_ms:+.1f} frames)"
              f"  | before {sum(x < -20 for x in d):>4}  within 20 ms {sum(-20 <= x <= 20 for x in d):>4}"
              f"  after {sum(x > 20 for x in d):>4}"
              f"  | p10 {d[n // 10]:+.0f}  p90 {d[9 * n // 10]:+.0f}")
    print("\nA drift shared by both runs is not visible in the slot metric; confirm its\n"
          "cause on a few cut points by eye before treating either run as more accurate.")


if __name__ == "__main__":
    main()
