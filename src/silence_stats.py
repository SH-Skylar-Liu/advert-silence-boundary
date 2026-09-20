#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
How long are the silences, and is the one-second refinement window long enough?

Two design claims rest on numbers that were, until this script, eyeballed from a
handful of examples:

  1. "the programme-to-break transition runs about two seconds, the gaps between
     adverts about half that"
  2. "a one-second search window is enough, because it reaches past the end of the
     silence and a wider window starts catching cuts inside the advert"

Both are measurable from the ledger alone. The refinement starts at the silence
MIDPOINT, so what the window has to cover is the distance from the midpoint to the
end of the silence: half the silence, plus however far the first frame sits beyond
it. Where half a silence already exceeds the window, the search stops inside the
silence and the true first frame is out of reach by construction.

中文：两条设计依据此前只是几个例子的观察值——「节目→广告段过渡约 2 秒、段内间隔约其一半」，
以及「1 秒窗口够用」。两者都能只从台账算出来。精修从静音**中点**起算，因此窗口需要覆盖的是
「中点到静音结束」这段距离；若半个静音已超过窗口，搜索就停在静音内部，真首帧结构上够不到。

Usage
-----
    python3 silence_stats.py LEDGER.csv
    python3 silence_stats.py LEDGER.csv --window 1.0 --fps 25
"""
from __future__ import annotations

import argparse
import csv
import statistics


def pct(values: list[float], q: float) -> float:
    """Percentile by nearest rank; values must be sorted."""
    if not values:
        return float("nan")
    i = min(len(values) - 1, max(0, round(q / 100 * len(values)) - 1))
    return values[i]


def describe(name: str, xs: list[float], window: float) -> str:
    if not xs:
        return f"{name:<22} (none)"
    xs = sorted(xs)
    return (f"{name:<22}{len(xs):>7}"
            f"{statistics.median(xs):>9.3f}{pct(xs, 90):>9.3f}{pct(xs, 99):>9.3f}{xs[-1]:>9.3f}"
            f"{sum(x > 1.0 for x in xs) / len(xs):>9.0%}"
            f"{sum(x > 2.0 for x in xs) / len(xs):>9.0%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger", help="cut ledger CSV (needs the v1.3+ silence columns)")
    ap.add_argument("--window", type=float, default=1.0, help="Stage B search window in seconds")
    ap.add_argument("--fps", type=float, default=25.0)
    a = ap.parse_args()

    rows = [r for r in csv.DictReader(open(a.ledger, encoding="utf-8", newline=""))
            if r.get("silence_end_s")]
    if not rows:
        raise SystemExit("no silence_start_s / silence_end_s columns: "
                         "this ledger was written before v1.3")

    # A break's FIRST advert follows the programme-to-break transition; the rest
    # follow an advert-to-advert gap. They are different animals and mixing them is
    # what makes "about two seconds" hard to pin down.
    # 中文：段内第一条广告前面是节目→广告段的过渡，其余是广告之间的间隔，两者性质不同；
    # 混在一起统计，正是「约两秒」说不清的原因。
    opening, within = [], []
    half_open, half_within = [], []
    for r in rows:
        dur = float(r["silence_end_s"]) - float(r["silence_start_s"])
        half = float(r["silence_end_s"]) - float(r["start_silence_mid_s"])
        (opening if r.get("pos") == "1" else within).append(dur)
        (half_open if r.get("pos") == "1" else half_within).append(half)

    print(f"ledger: {a.ledger}   cuts with a matched silence: {len(rows):,}\n")
    print(f"{'silence length (s)':<22}{'n':>7}{'median':>9}{'p90':>9}{'p99':>9}{'max':>9}{'>1 s':>9}{'>2 s':>9}")
    print(describe("break-opening", opening, a.window))
    print(describe("within a break", within, a.window))
    print(describe("all", opening + within, a.window))

    # What the window actually has to cover.
    print(f"\n{'midpoint to silence end':<22}{'n':>7}{'median':>9}{'p90':>9}{'p99':>9}{'max':>9}{'>1 s':>9}{'>2 s':>9}")
    print(describe("break-opening", half_open, a.window))
    print(describe("within a break", half_within, a.window))
    halves = sorted(half_open + half_within)
    print(describe("all", halves, a.window))
    beyond = sum(h > a.window for h in halves)
    print(f"\nsilences whose midpoint sits more than {a.window:.2f} s before the end of the "
          f"silence: {beyond:,} of {len(halves):,} ({beyond / len(halves):.1%})")
    print("For those cuts the window stops inside the silence, so a first frame at the "
          "return of audio\nis out of reach whatever the detector does.")

    # Did any refinement run up against the edge of the window?
    frame = 1.0 / a.fps
    shifts = sorted(int(r["shift_ms"]) / 1000 for r in rows
                    if r.get("refine_status") == "refined" and r.get("shift_ms"))
    if shifts:
        edge = sum(s >= a.window - frame for s in shifts)
        print(f"\nrefined cuts: {len(shifts):,}   median shift {statistics.median(shifts) * 1000:.0f} ms"
              f"   p99 {pct(shifts, 99) * 1000:.0f} ms   max {shifts[-1] * 1000:.0f} ms")
        print(f"within one frame of the window edge: {edge:,} ({edge / len(shifts):.1%})"
              f"  <- a large share would mean the window is truncating real corrections")
    else:
        print("\nno refined cuts in this ledger (refinement off, or every cut was already clean)")


if __name__ == "__main__":
    main()
