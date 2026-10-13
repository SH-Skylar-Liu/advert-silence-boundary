#!/usr/bin/env python3
"""Test where Stage B should look, against cut points checked frame by frame.

    python3 refine_check.py --sample manual_sample.csv --run RUN --src SRC \\
        --frames 1:10 2:8 3:6 ...

--frames gives, for each row n of the sample, the frame of the strip on which the
advert first appears (frame_strip.py with --before 0.4: frame 10 is the cut point the
run made, 25 fps). The true first frame is then start_s + (frame - 10) / 25.

For each cut point the script reads its silence from the run's ledger, lists every
scene change the detector reports from the start of the silence to 80 ms after its
end, and compares three choices with the truth, in frames (+ late, - early):

    run        what the run did (midpoint, then one second forward, with the guard)
    silence    the first change in the whole silence, the midpoint if there is none
    nearest    the change nearest the truth: the best any rule could do with this
               detector, and so a ceiling, not a rule

No video is cut and no ledger is written.
"""
import argparse
import csv
import os
import statistics

from refine_first_frame import scene_changes

FPS = 25.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--frames", nargs="+", required=True, metavar="N:FRAME")
    ap.add_argument("--backend", default="scenedetect")
    ap.add_argument("--min-scene-len", type=int, default=1)
    ap.add_argument("--guard-ms", type=float, default=80.0)
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--lookback", type=float, default=None,
                    help="start the search this many seconds before the silence end (as --refine-lookback)")
    a = ap.parse_args()

    first = {int(k): float(v) for k, v in (x.split(":") for x in a.frames)}
    run = os.path.expanduser(a.run)
    errs = {"run": [], "silence": [], "nearest": []}
    print(f"{'n':>2} {'silence':>7} {'mid':>6}  {'changes in the silence, frames from the truth':<46} "
          f"{'run':>5} {'silence':>8} {'nearest':>8}")
    for r in csv.DictReader(open(os.path.expanduser(a.sample), encoding="utf-8")):
        n = int(r["n"])
        if n not in first:
            continue
        led = None
        for row in csv.DictReader(open(os.path.join(run, r["recording"], "cut_plan.csv"), encoding="utf-8")):
            if row["break"] == r["break"] and row["pos"] == r["pos"]:
                led = row
        s0, s1, cut = float(led["silence_start_s"]), float(led["silence_end_s"]), float(led["start_s"])
        mid = (s0 + s1) / 2
        truth = cut + (first[n] - 10) / FPS
        f0 = s0 if a.lookback is None else max(s0, s1 - a.lookback)
        cands, st = scene_changes(os.path.join(a.src, r["recording"] + ".mp4"), f0,
                                  s1 - f0 + a.guard_ms / 1000.0, None,
                                  backend=a.backend, min_scene_len=a.min_scene_len)
        fr = lambda t: (t - truth) * FPS
        e_run = fr(cut)
        long_silence = a.lookback is None and s1 - s0 > a.window
        e_sil = e_run if long_silence else fr(cands[0] if cands else mid)
        e_near = min((fr(c) for c in cands), key=abs) if cands else fr(mid)
        errs["run"].append(e_run)
        errs["silence"].append(e_sil)
        errs["nearest"].append(e_near)
        shown = " ".join(f"{fr(c):+.0f}" for c in cands) or ("(none)" if st == "ok" else st)
        note = "  long silence: as run" if long_silence else ""
        print(f"{n:>2} {s1 - s0:>6.2f}s {fr(mid):>+6.1f}  {shown:<46} {e_run:>+5.0f} {e_sil:>+8.0f} {e_near:>+8.0f}{note}")
    print()
    for k, v in errs.items():
        ab = [abs(x) for x in v]
        print(f"{k:<8} n={len(v)}  exact {sum(x < 0.5 for x in ab):>2}  within 2 frames {sum(x <= 2.5 for x in ab):>2}  "
              f"late by more than 2 {sum(x > 2.5 for x in v):>2}  early by more than 2 {sum(x < -2.5 for x in v):>2}  "
              f"median |error| {statistics.median(ab):.1f}  worst {max(ab):.0f}")
    print("\n'mid' column: where the silence midpoint lies, in frames from the truth.")


if __name__ == "__main__":
    main()
