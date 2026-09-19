#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batch driver: run the pipeline over a directory of paired recordings and metadata.

Each source file is processed by cut_adverts.py in its own subprocess, so one bad
file cannot take down the batch. Work is resumable: a recording whose ledger already
exists is skipped, which means the batch can be stopped and restarted freely.

Scale
-----
Stage A decodes the full audio track of every recording, which is the dominant cost:
roughly 1-2 minutes for a 2.2-hour file. Over 1,670 delivered files that is tens of
hours sequentially, so `--jobs` matters. ffmpeg here is CPU-bound and single-threaded
per file, so set `--jobs` to about the core count (leave headroom if the GPU jobs are
running). The GPU is not used at this stage at all.

Order of work
-------------
    1. --plan-only   : see what pairs up, and what does not
    2. --limit N --dry-run : ledgers only, no video written - check yield and timing
    3. full run      : write clips; use screen, it will take hours

Usage
-----
    python3 batch_cut.py <SOURCE_DIR> --outroot <OUT> --plan-only
    python3 batch_cut.py <SOURCE_DIR> --outroot <OUT> --limit 30 --jobs 8 --dry-run
    python3 batch_cut.py <SOURCE_DIR> --outroot <OUT> --jobs 8 2>&1 | tee batch.log

Outputs, under --outroot:
    <recording>/cut_plan.csv    ledger per recording (input to compare_end_modes.py)
    <recording>/*.mp4           clips, unless --dry-run
    _summary.csv                one row per recording: clips, refined, seconds
    _failures.csv               recordings that errored, with the tail of stderr
    _logs/<recording>.log       full stdout+stderr per recording
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

HERE = Path(__file__).resolve().parent
CUTTER = HERE / "cut_adverts.py"


def pairs(root: Path):
    """Match <name>.mp4 with <name>_metadata.json; report anything unmatched."""
    metas = {p.name[: -len("_metadata.json")]: p for p in root.rglob("*_metadata.json")}
    matched, orphan_mp4 = [], []
    for mp4 in sorted(root.rglob("*.mp4")):
        stem = mp4.name[:-4]
        if stem in metas:
            matched.append((mp4, metas.pop(stem)))
        else:
            orphan_mp4.append(mp4)
    return matched, orphan_mp4, list(metas.values())


def run_one(mp4: Path, meta: Path, args, outroot: Path, logdir: Path) -> dict:
    """Process one recording. Never raises: failures come back as a record."""
    stem = mp4.name[:-4]
    outdir = outroot / stem
    cut_csv = outdir / "cut_plan.csv"
    rec = {"recording": stem, "clips_planned": 0, "clips_cut": 0, "refined": 0,
           "seconds": 0.0, "status": ""}

    if cut_csv.exists() and not args.redo:
        rec["status"] = "skipped (ledger exists)"
        return rec

    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(CUTTER), str(mp4), "--metadata", str(meta),
           "--outdir", str(outdir), "--cut-csv", str(cut_csv),
           "--noise", str(args.noise), "--min-dur", str(args.min_dur),
           "--end-mode", args.end_mode, "--backend", args.backend,
           "--min-match-rate", str(args.min_match_rate), "--min-matches", str(args.min_matches),
           "--last-end", args.last_end]
    if args.min_scene_len is not None:
        cmd += ["--min-scene-len", str(args.min_scene_len)]
    if args.max_past_silence_end is not None:
        cmd += ["--max-past-silence-end", str(args.max_past_silence_end)]
    if args.no_fill_missing:
        cmd.append("--no-fill-missing")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.no_refine:
        cmd.append("--no-refine")

    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    rec["seconds"] = round(time.time() - t0, 1)
    (logdir / f"{stem}.log").write_text((p.stdout or "") + "\n" + (p.stderr or ""),
                                        encoding="utf-8")

    if p.returncode != 0:
        rec["status"] = f"FAILED rc={p.returncode}"
        rec["stderr_tail"] = (p.stderr or "")[-300:].replace("\n", " ")
        return rec

    # Count from the ledger rather than parsing stdout: the ledger is the artefact
    # that downstream steps consume, so if it is wrong the count should be wrong too.
    if cut_csv.exists():
        with open(cut_csv, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rec["clips_planned"] += 1
                rec["refined"] += row.get("refine_status") == "refined"
    rec["clips_cut"] = 0 if args.dry_run else len(list(outdir.glob("*.mp4")))
    rec["status"] = "ok"
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="directory holding <name>.mp4 and <name>_metadata.json")
    ap.add_argument("--outroot", required=True, help="output root; one subdirectory per recording")
    ap.add_argument("--jobs", type=int, default=4, help="parallel recordings (ffmpeg is CPU-bound)")
    ap.add_argument("--limit", type=int, default=0, help="process only the first N pairs")
    ap.add_argument("--stride", type=int, default=1,
                    help="take every Nth pair instead of the first N; spreads the sample "
                         "across channels and dates instead of clustering on one")
    ap.add_argument("--dry-run", action="store_true", help="write ledgers only, no video")
    ap.add_argument("--plan-only", action="store_true", help="list the pairing and exit")
    ap.add_argument("--no-refine", action="store_true", help="disable Stage B refinement")
    ap.add_argument("--end-mode", choices=["next-start", "midpoint"], default="next-start")
    # Passed straight through to cut_adverts.py, so a batch can reproduce an earlier
    # rule set exactly (e.g. the 12 Sep baseline: --min-match-rate 1 --no-fill-missing).
    ap.add_argument("--backend", choices=["auto", "ffmpeg", "scenedetect"], default="auto")
    ap.add_argument("--min-scene-len", type=int, default=None)
    ap.add_argument("--max-past-silence-end", type=float, default=None, metavar="MS")
    ap.add_argument("--min-match-rate", type=float, default=0.5)
    ap.add_argument("--min-matches", type=int, default=3)
    ap.add_argument("--no-fill-missing", action="store_true")
    ap.add_argument("--last-end", choices=["silence-start", "midpoint"], default="silence-start")
    ap.add_argument("--noise", type=float, default=-40)
    ap.add_argument("--min-dur", type=float, default=0.2)
    ap.add_argument("--redo", action="store_true", help="reprocess even if a ledger exists")
    args = ap.parse_args()

    root, outroot = Path(args.root), Path(args.outroot)
    todo, orphan_mp4, orphan_json = pairs(root)
    print(f"paired        : {len(todo):,}")
    print(f"mp4 only      : {len(orphan_mp4):,}   metadata only: {len(orphan_json):,}")
    for p in (orphan_mp4[:3] + orphan_json[:3]):
        print(f"   unpaired: {p.name}")

    if args.stride > 1:
        todo = todo[:: args.stride]
        print(f"stride {args.stride} -> {len(todo):,} pairs")
    if args.limit:
        todo = todo[: args.limit]
        print(f"limit  {args.limit} -> {len(todo):,} pairs")
    if args.plan_only:
        for mp4, _ in todo[:20]:
            print("  ", mp4.name)
        if len(todo) > 20:
            print(f"   ... and {len(todo) - 20:,} more")
        return
    if not todo:
        sys.exit("nothing to do")

    outroot.mkdir(parents=True, exist_ok=True)
    logdir = outroot / "_logs"
    logdir.mkdir(exist_ok=True)
    summary_path, fail_path = outroot / "_summary.csv", outroot / "_failures.csv"

    print(f"\njobs {args.jobs}   end-mode {args.end_mode}   backend {args.backend}   min-scene-len {args.min_scene_len}   "
          f"guard {args.max_past_silence_end}   "
          f"min-match-rate {args.min_match_rate}   fill {not args.no_fill_missing}   "
          f"{'dry-run (no video written)' if args.dry_run else 'WRITING CLIPS'}\n")

    t0 = time.time()
    done = skipped = failed = 0
    lock = Lock()
    rows = []

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_one, mp4, meta, args, outroot, logdir): mp4
                for mp4, meta in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            with lock:
                rows.append(r)
                if r["status"] == "ok":
                    done += 1
                elif r["status"].startswith("skipped"):
                    skipped += 1
                else:
                    failed += 1
                elapsed = time.time() - t0
                eta = elapsed / i * (len(todo) - i) / 60
                print(f"[{i}/{len(todo)}] {r['recording'][:44]:<46} "
                      f"{r['clips_planned']:>4} clips  {r['refined']:>4} refined  "
                      f"{r['seconds']:>6.1f}s  {r['status']:<22} ETA {eta:>5.0f} min")

    rows.sort(key=lambda r: r["recording"])
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["recording", "clips_planned", "clips_cut",
                                          "refined", "seconds", "status"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    bad = [r for r in rows if r["status"].startswith("FAILED")]
    if bad:
        with open(fail_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["recording", "status", "stderr_tail"],
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(bad)

    ok = [r for r in rows if r["status"] == "ok"]
    planned = sum(r["clips_planned"] for r in ok)
    refined = sum(r["refined"] for r in ok)
    wall = time.time() - t0
    print(f"\nprocessed {done}, skipped {skipped}, failed {failed}, in {wall / 60:.1f} min")
    print(f"clips planned: {planned:,}   refined: {refined:,} "
          f"({100 * refined / planned:.0f}%)" if planned else "no clips planned")
    if ok:
        per = sum(r["seconds"] for r in ok) / len(ok)
        print(f"mean {per:.1f}s per recording -> {len(todo) * per / args.jobs / 3600:.1f} h "
              f"for this many at {args.jobs} jobs")
    print(f"summary: {summary_path}")
    if bad:
        print(f"failures: {fail_path}")


if __name__ == "__main__":
    main()
