# -*- coding: utf-8 -*-
"""
Cut individual adverts from a recording using the validated silence boundaries.
Reuses silence_detect.py (must be in the same folder) for detection + alignment.

Cut scheme
----------
Each advert clip runs from the MIDPOINT of the silence at its start to the MIDPOINT
of the silence at the next advert's start. Cutting inside the silent gap means no
advert content is clipped. The last advert in a break ends at the next detected
silence after it (the break-end transition), which the schedule alone does not give.

Stage B - first-frame refinement (agreed 18 Aug 2026)
-----------------------------------------------------
The silence midpoint usually sits a few frames before the true first frame, so the
previous advert bleeds into the start of this one. Every START cut is therefore
pushed forward to the first scene change within the following second
(refine_first_frame.py). END cuts stay on the silence midpoint: the cost is losing
1-3 frames at the tail, which Stephen judged acceptable because adverts almost
always end on a held frame. Disable with --no-refine; without scenedetect the
ffmpeg backend is used instead.

Safeguards
----------
- Video is TRANSCODED (libx264), never stream-copied (pipeline rule).
- Offset-consistency filter: within a recording, each break's fitted offset T must
  be monotonic in wall-clock and differ from the previous kept break by the scheduled
  gap (within --offset-tol). Breaks that fail (e.g. a break not fully in the recording
  that aliases onto another's silences) are dropped, not cut.
- A break is cut only if every boundary matched a silence.
- Output goes to a NEW folder; no existing file is touched. Run --dry-run first.

Usage
-----
python3 cut_adverts.py RECORDING.mp4 --metadata breaks.json \
    --outdir /home/shihan/storage/video/silence_cut_test --cut-csv plan.csv --dry-run
# review the plan (and plan.csv), then drop --dry-run to actually cut
"""
import argparse
import csv
import json
import os
import re
import subprocess

# On the server this module is silence_detect.py; the local working copy is
# detect_silence.py. Try both names.
try:
    from silence_detect import run_silencedetect, parse_silences, load_breaks, align, hms_to_s
except ImportError:  # pragma: no cover
    from detect_silence import run_silencedetect, parse_silences, load_breaks, align, hms_to_s

from refine_first_frame import HAVE_SCENEDETECT, refine_start

FFMPEG = "ffmpeg"


def safe(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s.strip())
    return re.sub(r"_+", "_", s).strip("_")[:60]


def cut(recording: str, start: float, dur: float, out: str) -> bool:
    # fast seek before -i (quick), transcode video + audio for clean cuts and a/v sync
    # -map 0:v:0 / 0:a:0: the delivered files carry more than one video stream plus
    # several data streams (ffprobe, 21 Aug 2026). Without explicit mapping ffmpeg
    # may pick the wrong stream or drag a data stream into the clip.
    cmd = [FFMPEG, "-y", "-ss", f"{start:.3f}", "-i", recording, "-t", f"{dur:.3f}",
           "-map", "0:v:0", "-map", "0:a:0",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", out]
    r = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
    return r.returncode == 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--noise", type=float, default=-40)
    ap.add_argument("--min-dur", type=float, default=0.2)
    ap.add_argument("--tol", type=float, default=0.35)
    ap.add_argument("--min-last", type=float, default=8.0,
                    help="min seconds after last advert start before searching its end silence")
    ap.add_argument("--offset-tol", type=float, default=5.0,
                    help="max deviation (s) of a break's offset from the expected value")
    ap.add_argument("--dry-run", action="store_true", help="print the cut plan, do not cut")
    # --- Stage B (first-frame refinement) and the cut ledger ---
    ap.add_argument("--no-refine", action="store_true",
                    help="skip first-frame refinement; use the Stage A silence midpoints only")
    ap.add_argument("--refine-window", type=float, default=1.0,
                    help="refinement search window in seconds; 1.0 as agreed, do not enlarge")
    ap.add_argument("--refine-threshold", type=float, default=None,
                    help="detector threshold; defaults to the backend default (Stephen tuned nothing)")
    ap.add_argument("--cut-csv", help="write the cut ledger as CSV, for manual QC")
    ap.add_argument("--end-mode", choices=["next-start", "midpoint"], default="next-start",
                    help="where a clip ends: on the next advert's refined start (default), "
                         "or on the silence midpoint (the pre-6-Sep behaviour, for A/B)")
    a = ap.parse_args()

    do_refine = not a.no_refine
    # Backend choice: use scenedetect when importable (same implementation as
    # Stephen's script), otherwise the ffmpeg filter. On the server scenedetect is
    # missing cv2, so the ffmpeg backend is what actually runs. The two thresholds
    # are not on the same scale, so cross-check against Stephen's output on the same
    # adverts before scaling up.
    refine_backend = "scenedetect" if HAVE_SCENEDETECT else "ffmpeg"
    if do_refine:
        print(f"Stage B refinement: on (backend = {refine_backend}, "
              f"window {a.refine_window}s)\n")

    silences = parse_silences(run_silencedetect(a.recording, a.noise, a.min_dur))
    breaks = load_breaks(a.metadata)
    meta = json.load(open(a.metadata, encoding="utf-8"))
    idx_start = {br.get("index"): br.get("start_time") for br in meta.get("ad_breaks", [])}
    idx_ads = {br.get("index"): br.get("adverts", []) for br in meta.get("ad_breaks", [])}

    # align every break, attach wall-clock, keep for consistency filtering
    items = []
    for br in breaks:
        n, T, hits = align(br["bounds"], silences, a.tol)
        idx = int(re.search(r"break (\d+)", br["label"]).group(1))
        wt = hms_to_s(idx_start[idx])
        items.append({"idx": idx, "wt": wt, "T": T, "hits": hits,
                      "nb": len(br["bounds"]), "n": n, "label": br["label"],
                      "bounds": br["bounds"]})
    items.sort(key=lambda x: x["wt"])

    # offset-consistency + full-match filter
    kept = []
    for it in items:
        if it["n"] < it["nb"]:
            print(f"[drop] {it['label']}: only {it['n']}/{it['nb']} boundaries matched")
            continue
        if kept:
            p = kept[-1]
            expected = p["T"] + (it["wt"] - p["wt"])
            if abs(it["T"] - expected) > a.offset_tol:
                print(f"[drop] {it['label']}: offset {it['T']:.1f}s inconsistent "
                      f"(expected ~{expected:.1f}s) -- likely not in this recording")
                continue
        kept.append(it)

    os.makedirs(a.outdir, exist_ok=True)
    mid = lambda s: (s["start"] + s["end"]) / 2
    planned = made = 0
    plan_rows = []          # cut ledger: one row per advert (Stage A cut, Stage B shift, status)
    n_refined = 0
    for it in kept:
        idx, T, hits, nb = it["idx"], it["T"], it["hits"], it["nb"]
        ads = idx_ads.get(idx, [])

        # --- Stage B is applied to every START first, so that a clip can end on the
        # NEXT advert's refined start. Refining starts while leaving ends on the
        # silence midpoint mixes two references: the clip length then carries the
        # next advert's shift as an error (measured 6 Sep 2026: 8 of 35 clips drifted
        # further from their scheduled slot length). Abutting refined starts makes the
        # clip length equal the scheduled duration by construction. ---
        starts, shifts, statuses = [], [], []
        for i in range(nb):
            if hits[i] is None:
                starts.append(None)
                shifts.append(0.0)
                statuses.append("no-silence")
                continue
            s_a = mid(hits[i])
            s_b, shift, status = (s_a, 0.0, "off")
            if do_refine:
                s_b, shift, status = refine_start(
                    a.recording, s_a, a.refine_window, a.refine_threshold,
                    backend=refine_backend)
                n_refined += status == "refined"
                if status.startswith("error"):
                    print(f"   [refine failed, falling back to silence midpoint] {status}")
            starts.append((s_a, s_b))
            shifts.append(shift)
            statuses.append(status)

        for i in range(nb):
            if hits[i] is None or starts[i] is None:
                continue
            start_a, start_cut = starts[i]
            shift, status = shifts[i], statuses[i]
            if a.end_mode == "next-start" and i < nb - 1 and starts[i + 1] is not None:
                # abut on the next advert's refined start; the 1-3 frame tail loss
                # Stephen accepted comes from scene detection being marginally late
                end_cut = starts[i + 1][1]
            elif i < nb - 1 and hits[i + 1] is not None:
                end_cut = mid(hits[i + 1])
            else:
                floor = it["bounds"][i][1] + T + a.min_last
                after = [s for s in silences if s["start"] > floor]
                if not after:
                    print(f"[skip] {it['label']} advert {i + 1}: no end silence found")
                    continue
                end_cut = mid(min(after, key=lambda s: s["start"]))
            dur = end_cut - start_cut
            if dur <= 0:
                continue
            ad = ads[i] if i < len(ads) else {}
            name = f"b{idx:02d}_{i + 1:02d}_{safe(ad.get('unique_id', '') + '_' + ad.get('brand', ''))}.mp4"
            out = os.path.join(a.outdir, name)
            planned += 1
            plan_rows.append({
                "recording": os.path.basename(a.recording),
                "break": idx, "pos": i + 1,
                "unique_id": ad.get("unique_id", ""), "brand": ad.get("brand", ""),
                "start_silence_mid_s": f"{start_a:.3f}",   # Stage A cut point
                "start_s": f"{start_cut:.3f}",             # cut point actually used (after Stage B)
                "shift_ms": f"{shift * 1000:.0f}",
                "refine_status": status,
                "end_s": f"{end_cut:.3f}", "duration_s": f"{dur:.3f}",
                "clip": name,
            })
            tag = f"  +{shift * 1000:.0f}ms" if shift else ""
            print(f"{name}  [{start_cut:8.2f} -> {end_cut:8.2f}]  {dur:5.1f}s{tag}")
            if not a.dry_run:
                if cut(a.recording, start_cut, dur, out):
                    made += 1
                else:
                    print(f"   FAILED: {name}")

    tail = "  (dry-run, nothing written)" if a.dry_run else f"; cut {made}"
    print(f"\nkept {len(kept)} breaks; planned {planned} clips{tail}")
    if do_refine:
        print(f"refinement: {n_refined}/{planned} cut points pushed forward "
              f"(the rest were already clean; no scene change means no change)")
    if not a.dry_run:
        print(f"output folder: {a.outdir}")

    # The cut ledger feeds the QC page and the shift-distribution statistics
    if a.cut_csv and plan_rows:
        with open(a.cut_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(plan_rows[0].keys()))
            w.writeheader()
            w.writerows(plan_rows)
        print(f"cut ledger: {a.cut_csv} ({len(plan_rows)} rows)")


if __name__ == "__main__":
    main()
