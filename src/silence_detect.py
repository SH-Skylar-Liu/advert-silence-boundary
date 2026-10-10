# -*- coding: utf-8 -*-
"""
Detect inter-ad silence, and (optionally) align it against real advert boundaries.

Usage
-----
Silence only:
  python3 detect_silence.py "video.mp4"

Silence + alignment to metadata boundaries (main feature):
  python3 detect_silence.py "video.mp4" --metadata "xxx_metadata.json"

Why
---
Japanese ad segmentation relies on ~0.5s of silence between ads. To check whether
UK breaks carry the same cue, we first measure every silence segment, then align
each advert's scheduled boundary onto it and see whether boundaries land on silence.
The metadata gives wall-clock times (13:52:05 ...), while a clip usually contains
some programme before the break, so clip_time = boundary(relative to break start) + T,
an unknown offset. This script finds T automatically from the advert-duration
pattern, so any recording of the same day works without manual alignment.

Deps: only ffmpeg (from PATH, called bare like ffprobe). No :1000 video server needed;
it reads the file directly from disk.
"""
import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys

FFMPEG = "ffmpeg"  # from PATH; hardcode an absolute path only if not on PATH


# ---------------------------------------------------------------------------
# 1. silence detection
# ---------------------------------------------------------------------------
def run_silencedetect(video: str, noise_db: float, min_dur: float) -> str:
    """Run ffmpeg's silencedetect filter; results are printed to stderr."""
    cmd = [
        FFMPEG, "-hide_banner", "-nostats", "-i", video, "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, stderr=subprocess.PIPE,
                          stdout=subprocess.DEVNULL, text=True)
    return proc.stderr


def parse_silences(text: str) -> list:
    """Extract each silence segment's start / end / dur (seconds)."""
    segs, cur = [], {}
    for line in text.splitlines():
        m = re.search(r"silence_start:\s*([-\d.]+)", line)
        if m:
            cur = {"start": float(m.group(1))}
            continue
        m = re.search(r"silence_end:\s*([-\d.]+).*silence_duration:\s*([-\d.]+)", line)
        if m and cur:
            cur["end"] = float(m.group(1))
            cur["dur"] = float(m.group(2))
            segs.append(cur)
            cur = {}
    return segs


# ---------------------------------------------------------------------------
# 2. advert boundaries from metadata
# ---------------------------------------------------------------------------
def hms_to_s(t: str) -> float:
    """'13:52:05' -> seconds. Accepts HH:MM:SS or HH:MM:SS.mmm."""
    parts = t.strip().split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def load_breaks(meta_path: str) -> list:
    """Read metadata.json; return each break's boundaries (relative to break start).
    Each break: {'label': ..., 'bounds': [(name, rel_seconds), ...]}.
    Boundaries = each advert's start (first = programme->break, rest = ad<->ad),
    plus break end if present.
    """
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    out = []
    for br in meta.get("ad_breaks", []):
        b0 = hms_to_s(br["start_time"])
        bounds = []
        kept_ads = []          # the advert behind each boundary, in the same order as bounds
        seen_rel = {}          # rel -> advert kept at that instant (collapses duplicates)
        for ad in br.get("adverts", []):
            rel = hms_to_s(ad["start_time"]) - b0
            # --- collapse records sharing one start_time into a single boundary ---
            # Measured 21 Aug 2026: of 97,213 records, 23,239 share their start_time
            # with the previous record; 20,812 of those are Sky AdSmart addressable
            # placeholders (unique_id starts with SKYADSMART, only on 5STAR and
            # Channel 5). Two records at the same instant put a zero-length gap into
            # the boundary sequence, which throws off the duration-pattern fit in
            # align(). They are therefore collapsed while building bounds, keeping
            # the real advert in preference to the placeholder.
            is_placeholder = str(ad.get("unique_id", "")).startswith("SKYADSMART")
            if rel in seen_rel:
                prev = seen_rel[rel]
                prev_is_ph = str(prev.get("unique_id", "")).startswith("SKYADSMART")
                if not (prev_is_ph and not is_placeholder):
                    continue                      # the record already kept wins; drop this one
                kept_ads = [k for k, b in zip(kept_ads, bounds) if b[1] != rel]
                bounds = [b for b in bounds if b[1] != rel]   # replace placeholder with real advert
            seen_rel[rel] = ad
            kept_ads.append(ad)
            name = f"{ad.get('position_in_break', '?')} {ad.get('brand', ad.get('unique_id', '?'))}"
            bounds.append((name, rel))
        if br.get("end_time"):
            bounds.append(("break end", hms_to_s(br["end_time"]) - b0))
        out.append({
            "label": f"break {br.get('index', '?')} ({br.get('start_time')}-{br.get('end_time')})",
            "bounds": bounds,
            # Boundary i belongs to adverts[i]. The raw metadata list is NOT in step
            # with bounds once same-instant records have been collapsed, so anything
            # that labels a cut point must read the advert from here.
            "adverts": kept_ads,
        })
    return out


# ---------------------------------------------------------------------------
# 3. auto-align boundary pattern to silences
# ---------------------------------------------------------------------------
def align(bounds: list, silences: list, tol: float = 0.35):
    """Find the offset T (clip_time = rel + T) that lands the most boundaries
    inside a detected silence. Brute-force every (silence_start - boundary_rel)
    candidate. Returns (num_matched, T, per-boundary matched silence or None).
    """
    if not bounds or not silences:
        return (0, 0.0, [None] * len(bounds))
    starts = [s["start"] for s in silences]
    rels = [r for _, r in bounds]

    def matched_silence(clip_t):
        for s in silences:
            if s["start"] - tol <= clip_t <= s["end"] + tol:
                return s
        return None

    best = None
    for r in rels:
        for ss in starts:
            T = ss - r
            hits = [matched_silence(r_i + T) for r_i in rels]
            n = sum(1 for h in hits if h)
            if best is None or n > best[0]:
                best = (n, T, hits)
    n, T, hits = best
    residual_offsets = [h["start"] - rels[i] for i, h in enumerate(hits) if h]
    if residual_offsets:
        T = statistics.median(residual_offsets)
        hits = [matched_silence(r_i + T) for r_i in rels]
        n = sum(1 for h in hits if h)
    return (n, T, hits)


# ---------------------------------------------------------------------------
# 4. main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Detect inter-ad silence and align to real boundaries")
    ap.add_argument("video")
    ap.add_argument("--metadata", help="matching _metadata.json (enables boundary alignment)")
    ap.add_argument("--noise", type=float, default=-40,
                    help="silence threshold dB (default -40; try -30 if none, -50 if too many)")
    ap.add_argument("--min-dur", type=float, default=0.2, help="min silence seconds (default 0.2)")
    ap.add_argument("--tol", type=float, default=0.35, help="boundary-silence match tolerance s (default 0.35)")
    a = ap.parse_args()

    if not os.path.exists(a.video):
        sys.exit(f"video not found: {a.video}")

    silences = parse_silences(run_silencedetect(a.video, a.noise, a.min_dur))
    print(f"\nvideo : {a.video}")
    print(f"silence segments : {len(silences)}  (noise {a.noise}dB, min {a.min_dur}s)")

    # ---- no metadata: just list silences ----
    if not a.metadata:
        print(f"\n{'#':>3}  {'start(s)':>10}  {'end(s)':>10}  {'dur(s)':>8}")
        for i, s in enumerate(silences, 1):
            print(f"{i:>3}  {s['start']:>10.3f}  {s['end']:>10.3f}  {s['dur']:>8.3f}")
        print("\n(Pass --metadata to auto-align real advert boundaries.)")
        return

    # ---- with metadata: align each break ----
    if not os.path.exists(a.metadata):
        sys.exit(f"metadata not found: {a.metadata}")
    breaks = load_breaks(a.metadata)
    print(f"breaks in metadata : {len(breaks)}")

    rows_for_csv = []
    for br in breaks:
        n, T, hits = align(br["bounds"], silences, a.tol)
        total = len(br["bounds"])
        print(f"\n=== {br['label']} ===")
        print(f"discovered offset T : {T:.2f}s   (clip_time = rel + T)")
        print(f"boundaries on a silence : {n}/{total}")
        print(f"\n{'advert':<34}{'rel(s)':>8}{'clip(s)':>9}   matched silence")
        for (name, rel), hit in zip(br["bounds"], hits):
            clip_t = rel + T
            info = f"{hit['start']:.2f}-{hit['end']:.2f}  ({hit['dur']:.2f}s)" if hit else "-  MISS"
            print(f"{name[:33]:<34}{rel:>8.1f}{clip_t:>9.1f}   {info}")
            rows_for_csv.append([br["label"], name, f"{rel:.1f}", f"{clip_t:.1f}",
                                 f"{T:.2f}", 1 if hit else 0,
                                 f"{hit['dur']:.3f}" if hit else ""])
        if total:
            rate = n / total
            if rate >= 0.8:
                print(f"-> verdict: {n}/{total} boundaries on silence -- this break DOES carry a silence cue.")
            elif rate >= 0.4:
                print(f"-> verdict: partial ({n}/{total}) -- signal present but not clean; check threshold or more samples.")
            else:
                print(f"-> verdict: few hits ({n}/{total}) -- no reliable boundary silence in this break.")

    out_csv = os.path.splitext(a.video)[0] + "_boundary_alignment.csv"
    try:
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["break", "advert", "rel_s", "clip_s", "offset_T", "on_silence", "silence_dur_s"])
            w.writerows(rows_for_csv)
        print(f"\nalignment written : {out_csv}")
    except OSError as e:
        print(f"\n(CSV not written: {e}; terminal result still valid)")


if __name__ == "__main__":
    main()
