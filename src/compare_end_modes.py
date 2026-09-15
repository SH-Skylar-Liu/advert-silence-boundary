#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ablation: how the choice of cut points affects the accuracy of the resulting clips.

Three variants are compared on the SAME set of cut points, so the only thing that
changes is which two instants define a clip:

    A  silence midpoint  ->  next silence midpoint      (Stage A only)
    B  refined start     ->  next silence midpoint      (Stage B, cut_adverts.py --end-mode midpoint)
    C  refined start     ->  next refined start         (Stage B, cut_adverts.py --end-mode next-start)

Metric
------
UK television advertising is sold in fixed slot lengths (10 / 20 / 30 / 60 s, with
15 / 40 / 45 / 90 s also occurring). The scheduled duration of a clip is therefore
known a priori, and the error of a cut can be measured without any manual frame
inspection: take the absolute difference between the cut clip's duration and the
nearest slot length. This is an INDEPENDENT signal - the slot grid comes from the
advertising market, not from the detector - which is what makes it usable as
evidence rather than as self-confirmation.

Why this script exists separately
---------------------------------
The ablation needs no re-cutting and no re-decoding. The cut points themselves are
identical across variants; only the arithmetic differs. Because cut_adverts.py
writes BOTH the Stage A midpoint (`start_silence_mid_s`) and the Stage B refined
point (`start_s`) into its ledger, all three variants can be recomputed after the
fact from a single ledger CSV. Recording raw and derived values side by side in the
ledger is what makes this kind of ablation cheap; it is worth keeping that habit at
every stage of the pipeline.

Measured on 2020-08-29_ITV1 HD_09:39:00.mp4 (8 breaks, 67 adverts, 35 clips kept),
10 September 2026:

    A  median 0.206 s (~5 frames)     mean 0.277 s   max 0.943 s   <0.2 s: 49%
    B  median 0.065 s (~1.6 frames)   mean 0.195 s   max 0.993 s   <0.2 s: 69%
    C  median 0.022 s (~0.55 frames)  mean 0.138 s   max 0.702 s   <0.2 s: 77%

B improves the median but worsens the maximum: refining the start while leaving the
end on the silence midpoint mixes two references, so a clip's length absorbs the
NEXT advert's shift as error. C removes that by construction.

Usage
-----
    python3 compare_end_modes.py /tmp/plan_C2.csv
    python3 compare_end_modes.py ledger.csv --fps 25 --per-clip
    python3 compare_end_modes.py ../examples/example_cut_ledger.csv   # runnable demo
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict

# Slot lengths sold by UK broadcasters. 10/20/30 dominate; 60 and 40 occur; 15/45/90
# are rarer but real. Keep this list explicit - it is an assumption of the metric.
SLOTS = [10, 15, 20, 30, 40, 45, 60, 90]


def deviation(duration: float, slots=SLOTS) -> float:
    """Absolute difference between a clip's duration and the nearest scheduled slot."""
    return min(abs(duration - s) for s in slots)


def variants_from_ledger(rows: list[dict]) -> dict[str, list[tuple]]:
    """Recompute clip durations for the three variants from one ledger.

    Returns {variant: [(duration, brand, shift_ms), ...]}. The last advert of each
    break keeps the ledger's own end (the break-end silence), because no following
    advert exists to abut on; it is identical in all three variants.
    """
    # Key on recording as well as break: ledgers from several recordings are often
    # concatenated for a pooled figure, and break numbers restart in every recording.
    by_break: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_break[(r.get("recording", ""), r["break"])].append(r)

    out: dict[str, list[tuple]] = {"A": [], "B": [], "C": []}
    for _, rs in by_break.items():
        rs.sort(key=lambda r: int(r["pos"]))
        for i, r in enumerate(rs):
            mid = float(r["start_silence_mid_s"])       # Stage A cut point
            ref = float(r["start_s"])                   # Stage B refined cut point
            if i + 1 < len(rs):
                next_mid = float(rs[i + 1]["start_silence_mid_s"])
                next_ref = float(rs[i + 1]["start_s"])
            else:
                next_mid = next_ref = float(r["end_s"])
            brand, shift = r.get("brand", ""), r.get("shift_ms", "")
            out["A"].append((next_mid - mid, brand, shift))
            out["B"].append((next_mid - ref, brand, shift))
            out["C"].append((next_ref - ref, brand, shift))
    return out


def summarise(items: list[tuple], fps: float) -> str:
    devs = sorted(deviation(d) for d, _, _ in items if d > 0)
    if not devs:
        return "no usable clips"
    med = statistics.median(devs)
    return ("n=%d  median %.3fs (%.1f frames)  mean %.3fs  max %.3fs  <0.2s %.0f%%"
            % (len(devs), med, med * fps, statistics.mean(devs), max(devs),
               100 * sum(1 for v in devs if v < 0.2) / len(devs)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger", help="cut ledger CSV written by cut_adverts.py --cut-csv")
    ap.add_argument("--fps", type=float, default=25.0, help="for the frame conversion (UK broadcast: 25)")
    ap.add_argument("--per-clip", action="store_true", help="also list every clip, worst first")
    ap.add_argument("--out", help="write the per-clip comparison to a CSV")
    a = ap.parse_args()

    with open(a.ledger, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    need = {"break", "pos", "start_silence_mid_s", "start_s", "end_s"}
    missing = need - set(rows[0].keys() if rows else [])
    if missing:
        raise SystemExit(f"ledger is missing columns: {sorted(missing)} "
                         f"(was it written by an older cut_adverts.py?)")

    v = variants_from_ledger(rows)
    print(f"ledger: {a.ledger}   clips: {len(rows)}\n")
    print("A  silence midpoint -> next midpoint    :", summarise(v["A"], a.fps))
    print("B  refined start    -> next midpoint    :", summarise(v["B"], a.fps))
    print("C  refined start    -> next refined     :", summarise(v["C"], a.fps))

    da = statistics.median(sorted(deviation(d) for d, _, _ in v["A"] if d > 0))
    dc = statistics.median(sorted(deviation(d) for d, _, _ in v["C"] if d > 0))
    if dc > 0:
        print(f"\nA -> C median improvement: {da / dc:.1f}x")

    if a.per_clip or a.out:
        table = []
        for i in range(len(v["A"])):
            dur_a, brand, shift = v["A"][i]
            dur_b = v["B"][i][0]
            dur_c = v["C"][i][0]
            table.append({"brand": brand, "shift_ms": shift,
                          "dur_A": round(dur_a, 3), "dev_A": round(deviation(dur_a), 3),
                          "dur_B": round(dur_b, 3), "dev_B": round(deviation(dur_b), 3),
                          "dur_C": round(dur_c, 3), "dev_C": round(deviation(dur_c), 3)})
        table.sort(key=lambda r: -r["dev_C"])
        if a.per_clip:
            print(f"\n{'brand':<34}{'shift':>7}{'dev_A':>8}{'dev_B':>8}{'dev_C':>8}")
            for r in table:
                print(f"{str(r['brand'])[:33]:<34}{str(r['shift_ms']):>7}"
                      f"{r['dev_A']:>8.3f}{r['dev_B']:>8.3f}{r['dev_C']:>8.3f}")
        if a.out:
            with open(a.out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
                w.writeheader()
                w.writerows(table)
            print(f"\nwritten: {a.out}")


if __name__ == "__main__":
    main()
