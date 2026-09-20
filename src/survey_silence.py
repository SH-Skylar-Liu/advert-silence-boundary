# -*- coding: utf-8 -*-
"""
Survey where the silence-boundary method works: stratified sample by channel and
year, run the same detection and alignment over each file, and report hit rates.

Why
---
On 2024-03-26 ITV1 HD the method hit 55/55 boundaries. On 2020-01-30 ITV2 the same
pipeline hits 4/14, and loosening the threshold from -35 to -30 dB (10 to 65 silence
segments) does not change that. So the cue is not uniformly present: it appears to
vary by channel and by era. That is a property of the material, not a parameter to
tune away, and it decides three things:
  1. which material the silence pipeline may be applied to, and which needs a fallback
  2. the "conditions of validity" section of the methodology chapter
  3. the answer to the question a panel will ask - how do you know this generalises

What it does
------------
    1. Parses channel and date from each filename (YYYY-MM-DD_CHANNEL_HH:MM:SS.mp4)
    2. Draws a stratified sample: --per-stratum files from each (channel, year) cell
    3. For every sampled file, runs ffmpeg silencedetect and the existing alignment,
       recording silence count, fitted offset T, boundaries hit, and hit rate
    4. Writes a per-file CSV and prints summaries by channel, by year, and by cell

The alignment code is imported from the existing module, so this survey measures
exactly the pipeline that would run in production - not a re-implementation.

Usage
-----
    # see the sample plan without touching any video
    python3 survey_silence.py /home/shihan/storage/video/unique_advert_source_videos --list

    # run it (parallel ffmpeg; each file takes a few seconds)
    python3 survey_silence.py <dir> --per-stratum 5 --jobs 4 --out ~/silence_survey.csv

    # compare thresholds on the same sample (the seed keeps the sample fixed)
    python3 survey_silence.py <dir> --noise -35 --out ~/survey_n35.csv
    python3 survey_silence.py <dir> --noise -30 --out ~/survey_n30.csv
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from silence_detect import align, load_breaks, parse_silences, run_silencedetect

FNAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(.+)_(\d{2}):(\d{2}):(\d{2})\.mp4$")


def parse_name(p: Path):
    """Return (year, channel) from YYYY-MM-DD_CHANNEL_HH:MM:SS.mp4, else (None, None)."""
    m = FNAME.match(p.name)
    if not m:
        return None, None
    return m.group(1), m.group(4)


def normalise_channel(ch: str) -> str:
    """Fold the variants seen in the metadata into one label per broadcaster feed.

    Measured 21 Aug 2026: the delivery mixes 'ITV1' with 'ITV1 HD' and 'Channel 5'
    with '5STAR'. HD and SD feeds of one channel are treated as the same channel;
    5STAR is a separate channel from Channel 5 and is kept apart.
    """
    c = ch.strip()
    c = re.sub(r"\s*HD$", "", c, flags=re.I)
    return c


def sample(files: list[Path], per_stratum: int, seed: int):
    """Stratified sample: up to `per_stratum` files from each (channel, year) cell."""
    cells: dict[tuple[str, str], list[Path]] = defaultdict(list)
    skipped = []
    for f in files:
        year, ch = parse_name(f)
        if not year:
            skipped.append(f)
            continue
        cells[(normalise_channel(ch), year)].append(f)
    rnd = random.Random(seed)
    picked = []
    for key in sorted(cells):
        pool = sorted(cells[key])
        picked.extend(rnd.sample(pool, min(per_stratum, len(pool))))
    return picked, cells, skipped


def survey_one(video: Path, args) -> dict:
    """Run detection + alignment on one file and return its record."""
    meta = video.with_name(video.stem + "_metadata.json")
    rec = {"file": video.name, "channel": "", "year": "", "n_silence": 0,
           "n_breaks": 0, "n_bounds": 0, "n_hit": 0, "hit_rate": "", "offset_T": "",
           "note": ""}
    year, ch = parse_name(video)
    rec["year"], rec["channel"] = year or "", normalise_channel(ch or "")
    if not meta.exists():
        rec["note"] = "no metadata"
        return rec
    try:
        silences = parse_silences(run_silencedetect(str(video), args.noise, args.min_dur))
        rec["n_silence"] = len(silences)
        breaks = load_breaks(str(meta))
        rec["n_breaks"] = len(breaks)
        # A delivered file normally holds one break; sum over breaks if there are more.
        tot_b = tot_h = 0
        offsets = []
        for br in breaks:
            n, T, _ = align(br["bounds"], silences, args.tol)
            tot_b += len(br["bounds"])
            tot_h += n
            offsets.append(T)
        rec["n_bounds"], rec["n_hit"] = tot_b, tot_h
        rec["hit_rate"] = round(tot_h / tot_b, 3) if tot_b else ""
        rec["offset_T"] = round(offsets[0], 2) if offsets else ""
    except Exception as e:                                  # noqa: BLE001
        rec["note"] = f"error: {type(e).__name__}: {e}"
    return rec


def summarise(rows: list[dict], key_fn, label: str) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["hit_rate"] != "":
            groups[key_fn(r)].append(r)
    if not groups:
        return
    print(f"\n===== hit rate by {label} =====")
    print(f"{label:<18} {'files':>6} {'bounds':>8} {'hit':>7} {'rate':>7} {'min':>6} {'max':>6}")
    for k in sorted(groups):
        g = groups[k]
        b = sum(r["n_bounds"] for r in g)
        h = sum(r["n_hit"] for r in g)
        rates = [r["hit_rate"] for r in g]
        print(f"{k:<18} {len(g):>6} {b:>8} {h:>7} {h / b if b else 0:>7.1%} "
              f"{min(rates):>6.0%} {max(rates):>6.0%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="directory of source mp4 + *_metadata.json")
    ap.add_argument("--per-stratum", type=int, default=3,
                    help="files per (channel, year) cell (default 3)")
    ap.add_argument("--seed", type=int, default=0, help="sampling seed; keep fixed to compare runs")
    ap.add_argument("--noise", type=float, default=-30)
    ap.add_argument("--min-dur", type=float, default=0.2)
    ap.add_argument("--tol", type=float, default=0.35)
    ap.add_argument("--jobs", type=int, default=4, help="parallel ffmpeg processes")
    ap.add_argument("--limit", type=int, default=0, help="cap the sample size (after stratifying)")
    ap.add_argument("--out", help="write the per-file CSV here")
    ap.add_argument("--list", action="store_true", help="print the sample plan and exit")
    a = ap.parse_args()

    files = sorted(Path(a.root).glob("*.mp4"))
    print(f"source files: {len(files)}")
    picked, cells, skipped = sample(files, a.per_stratum, a.seed)
    if skipped:
        print(f"unparsable filenames skipped: {len(skipped)} (e.g. {skipped[0].name})")

    print(f"\nstrata (channel x year): {len(cells)}")
    print(f"{'channel':<16} {'year':<6} {'available':>10} {'sampled':>8}")
    for key in sorted(cells):
        ch, yr = key
        n_av = len(cells[key])
        print(f"{ch:<16} {yr:<6} {n_av:>10} {min(a.per_stratum, n_av):>8}")
    if a.limit:
        picked = picked[: a.limit]
    print(f"\nsample size: {len(picked)}  (noise {a.noise} dB, min {a.min_dur}s, tol {a.tol}s)")
    if a.list:
        for p in picked:
            print("  ", p.name)
        return
    if not picked:
        sys.exit("nothing to survey")

    rows = []
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(survey_one, p, a): p for p in picked}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            rows.append(r)
            rate = f"{r['hit_rate']:.0%}" if r["hit_rate"] != "" else "-"
            print(f"  [{i}/{len(picked)}] {r['channel']:<12} {r['year']}  "
                  f"sil={r['n_silence']:<4} {r['n_hit']:>3}/{r['n_bounds']:<3} {rate:>5}  "
                  f"T={r['offset_T']}  {r['note']}")

    rows.sort(key=lambda r: (r["channel"], r["year"], r["file"]))
    summarise(rows, lambda r: r["channel"], "channel")
    summarise(rows, lambda r: r["year"], "year")
    summarise(rows, lambda r: f"{r['channel']} {r['year']}", "channel x year")

    good = [r for r in rows if r["hit_rate"] != "" and r["hit_rate"] >= 0.8]
    poor = [r for r in rows if r["hit_rate"] != "" and r["hit_rate"] < 0.4]
    print(f"\nfiles at >=80% hit rate: {len(good)}/{len(rows)}    below 40%: {len(poor)}")
    print("A file below 40% has no usable silence cue at this threshold and needs a "
          "fallback route, not a looser threshold.")

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwritten: {a.out}")


if __name__ == "__main__":
    main()
