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


PLACEHOLDER_PREFIX = "SKYADSMART"
BREAK_GAP_S = 120.0      # no advert is this long; a longer gap inside a break is two breaks


def clean_breaks(ad_breaks: list) -> list:
    """Repair how the schedule is cut into breaks, before anything is aligned.

    The schedule opens a new break at every record marked First. Three things in the
    delivered files follow from that and none of them is about the recording
    (counted over all 1,670 files on 12 Oct 2026):

    1. Sky AdSmart is addressable advertising: the slot is sold again per household,
       and the schedule logs a placeholder record beside the advert that was
       broadcast. The placeholder and the broadcast advert at the head of a break are
       BOTH marked First, so the break is cut in two at its first record. In 1,758
       breaks the lone record is the placeholder; in 958 it is the real first advert,
       which then has a single boundary and can never be aligned, while the rest of
       the break opens on the placeholder and its first clip is labelled as one.
       -> adjacent breaks with the same start_time are one break: merge them.
    2. A placeholder never stands for an advert in the recording, and every one of
       them shares its start_time with a broadcast advert (20,812 of 20,812, measured
       21 Aug 2026), so removing it removes no boundary.
       -> drop every placeholder record; drop a break left empty.
    3. In 231 breaks two consecutive adverts start more than two minutes apart: a
       break whose first record is not marked First is appended to the one before.
       -> split a break wherever the gap exceeds BREAK_GAP_S (the longest slot is 90 s).

    Order matters: merge first, while the two halves still share a start_time.

    Returns new break dicts in the input schema. A merged break keeps the index of
    its first half. The parts of a split break are numbered index*100 + 1, 2, ...,
    so every break still has a unique integer index and the original is recoverable.
    Breaks of another channel that some files also list are NOT handled here.
    """
    merged = []
    for br in ad_breaks:
        if merged and br.get("start_time") == merged[-1].get("start_time"):
            merged[-1]["adverts"] = merged[-1]["adverts"] + list(br.get("adverts", []))
            continue
        nb = dict(br)
        nb["adverts"] = list(br.get("adverts", []))
        merged.append(nb)

    out = []
    for br in merged:
        ads = [a for a in br["adverts"]
               if not str(a.get("unique_id", "")).startswith(PLACEHOLDER_PREFIX)]
        if not ads:
            continue
        parts, cur = [], [ads[0]]
        for prev, a in zip(ads, ads[1:]):
            if hms_to_s(a["start_time"]) - hms_to_s(prev["start_time"]) > BREAK_GAP_S:
                parts.append(cur)
                cur = []
            cur.append(a)
        parts.append(cur)
        if len(parts) == 1:
            nb = dict(br)
            nb["adverts"] = ads
            out.append(nb)
            continue
        for k, part in enumerate(parts, 1):
            nb = dict(br)
            nb["index"] = int(br.get("index", 0)) * 100 + k
            nb["start_time"] = part[0]["start_time"]
            nb["adverts"] = part
            out.append(nb)

    # A record that opens the NEXT break is sometimes appended to the break before
    # (2021-04-30_Channel 5_14:42:10: break 28 ends with a record at 15:09:43, the
    # instant break 29 starts). The split above leaves it as a part of its own, which
    # now starts when the next break does: put it at the head of that break.
    final = []
    for br in out:
        if final and br.get("start_time") == final[-1].get("start_time"):
            head = final.pop()
            nb = dict(br)
            nb["adverts"] = head["adverts"] + br["adverts"]
            final.append(nb)                 # keeps the index of the break it belongs to
            continue
        final.append(br)
    return final


def own_run(ad_breaks: list, name_time):
    """The breaks that belong to the recording, where a file lists two channels.

    327 of the 1,670 schedule files hold break indices in more than one unbroken run
    (12 Oct 2026). In the files looked at, the runs are two channels: the programmes
    around them differ (2025-11-30_5_19:42:44 has Police Interceptors around breaks
    68-91 and NFL around 156-172; E4 files carry a second run of More4-type
    programmes), and video_info.channel is 5STAR in every file named 5. The schedule
    has no channel per break, so the run is identified by the file name instead: its
    time is the start_time of one break (true of 319 of the 327 files), and the run
    of consecutive indices holding that break is the recording's own.

    Returns that run, in schedule order, or None when the file has a single run or
    no break starts at name_time (the caller then keeps every break).
    """
    ordered = sorted(ad_breaks, key=lambda b: int(b.get("index", 0)))
    runs, cur = [], []
    for br in ordered:
        if cur and int(br.get("index", 0)) != int(cur[-1].get("index", 0)) + 1:
            runs.append(cur)
            cur = []
        cur.append(br)
    if cur:
        runs.append(cur)
    if len(runs) < 2 or not name_time:
        return None
    for run in runs:
        if any(br.get("start_time") == name_time for br in run):
            keep = {id(br) for br in run}
            return [br for br in ad_breaks if id(br) in keep]
    return None


def name_time_of(path: str):
    """'.../2025-11-30_5_19:42:44_metadata.json' -> '19:42:44' (None if absent)."""
    import os
    import re
    m = re.search(r"_(\d{2}:\d{2}:\d{2})(?:_|\.|$)", os.path.basename(path))
    return m.group(1) if m else None


def load_breaks(meta_path: str, clean: bool = False, own: bool = False) -> list:
    """Read metadata.json; return each break's boundaries (relative to break start).
    Each break: {'label': ..., 'bounds': [(name, rel_seconds), ...]}.
    Boundaries = each advert's start (first = programme->break, rest = ad<->ad),
    plus break end if present.
    """
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    ad_breaks = meta.get("ad_breaks", [])
    if own:                                   # before cleaning: uses the original indices
        run = own_run(ad_breaks, name_time_of(meta_path))
        if run is not None:
            ad_breaks = run
    if clean:
        ad_breaks = clean_breaks(ad_breaks)
    out = []
    for br in ad_breaks:
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
            "index": br.get("index"),
            "start_time": br.get("start_time"),
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


def align_near(bounds: list, silences: list, expected_T: float, window: float,
               tol: float = 0.35):
    """align(), but only offsets within `window` seconds of expected_T are tried.

    Used once the start of the recording is known: a break then has one place it can
    be, and the question is no longer "where in the file does this pattern fit best"
    but "do its boundaries fall on silences there". Ties go to the offset nearest the
    expected one. Returns (num_matched, T, per-boundary matched silence or None);
    with nothing in range, (0, expected_T, all None).
    """
    rels = [r for _, r in bounds]
    none = (0, expected_T, [None] * len(bounds))
    if not bounds or not silences:
        return none

    def matched_silence(clip_t):
        for sil in silences:
            if sil["start"] - tol <= clip_t <= sil["end"] + tol:
                return sil
        return None

    near = [sil["start"] for sil in silences
            if rels[0] + expected_T - window - rels[-1] <= sil["start"]
            <= rels[-1] + expected_T + window]
    best = None
    for r in rels:
        for ss in near:
            T = ss - r
            if abs(T - expected_T) > window:
                continue
            hits = [matched_silence(r_i + T) for r_i in rels]
            n = sum(1 for h in hits if h)
            key = (n, -abs(T - expected_T))
            if best is None or key > best[0]:
                best = (key, T, hits)
    if best is None:
        return none
    (n, _), T, hits = best
    refit = statistics.median([h["start"] - rels[i] for i, h in enumerate(hits) if h])
    if abs(refit - expected_T) <= window:
        re_hits = [matched_silence(r_i + refit) for r_i in rels]
        if sum(1 for h in re_hits if h) >= n:
            T, hits, n = refit, re_hits, sum(1 for h in re_hits if h)
    return (n, T, hits)


def align_by_recording(items: list, silences: list, tol: float, offset_tol: float,
                       min_matches: int, duration=None):
    """Fix the start of the recording first, then check every break where it must be.

    A recording is one continuous stretch of broadcast, so every break in it implies
    the same recording start: (position of the break in the file) - (its wall-clock
    time). Fitting each break on its own and comparing it with the previous kept one
    lets a single chance fit reject every correct break after it (full run, 11 Oct
    2026: some 17,000 slots lay in breaks that are inside their file by this
    reckoning and were dropped as "offset inconsistent"; in 2020-08-29 ITV1 HD 09:39
    a 4/7 chance fit of break 1 displaced four fully matched breaks).

    items: one dict per break with 'wt' (wall-clock seconds), 'bounds', and the free
    fit 'n', 'T', 'hits' from align(). Steps:
      1. anchors = breaks whose free fit matched EVERY boundary, with at least
         min_matches boundaries; a complete match is very unlikely to be chance;
      2. each anchor proposes a recording start; proposals within offset_tol of one
         another are one candidate;
      3. the candidate under which the most breaks, re-fitted near their expected
         positions, match completely is the recording start (ties: most boundaries);
      4. every break is then fitted only within offset_tol of its expected position.
         A break expected before second 0 or after the end of the file is marked
         'outside' and not fitted.
    No new parameter: tol and offset_tol are the ones align() and the old filter use.

    Returns (start, n_anchors, report), report being every candidate start with the
    number of breaks that match completely under it and the total of matched
    boundaries, best first. Each item gains 'place' ('fitted' | 'outside') and,
    where fitted, replaced 'n', 'T', 'hits'. With no anchor, returns (None, 0, []) and
    leaves items untouched so the caller can fall back to per-break fitting.
    """
    anchors = [it for it in items
               if it["nb"] >= min_matches and it["n"] == it["nb"]]
    if not anchors:
        return (None, 0, [])
    proposals = sorted(it["T"] - it["wt"] for it in anchors)
    candidates, group = [], [proposals[0]]
    for g in proposals[1:]:
        if g - group[0] <= offset_tol:
            group.append(g)
        else:
            candidates.append(statistics.median(group))
            group = [g]
    candidates.append(statistics.median(group))

    def inside(expected, rels):
        if expected + rels[0] < 0:
            return False
        return duration is None or expected + rels[0] <= duration

    def refit_all(start):
        out, total = [], 0
        for it in items:
            rels = [r for _, r in it["bounds"]]
            expected = start + it["wt"]
            if not rels or not inside(expected, rels):
                out.append(("outside", 0, expected, [None] * len(rels)))
                continue
            n, T, hits = align_near(it["bounds"], silences, expected, offset_tol, tol)
            out.append(("fitted", n, T, hits))
            total += n
        return total, out

    # Which candidate? The one that the most breaks CONFIRM by matching completely
    # where it puts them; total matched boundaries only breaks a tie. Breaks that are
    # placed correctly agree on the recording start to within a second (200 of 239
    # fully matched breaks in the 150-recording sample; the other 39 were more than
    # 5 s out, none in between), so a true start gathers several complete matches,
    # while a false one, taken from a break sitting on another break's silences,
    # gathers its own and little else. Ranking by total boundaries alone (the rule
    # until 13 Oct 2026) let a false start win where advert lengths are regular
    # enough for many breaks to pick up a few stray matches each.
    scored = []
    for c in candidates:
        total, fits = refit_all(c)
        full = sum(1 for it, f in zip(items, fits)
                   if f[0] == "fitted" and it["nb"] >= min_matches and f[1] == it["nb"])
        scored.append(((full, total), c, fits))
    (best, start, fits) = max(scored, key=lambda x: x[0])
    for it, (place, n, T, hits) in zip(items, fits):
        it["place"], it["n"], it["T"], it["hits"] = place, n, T, hits
    report = sorted(((c, full, total) for (full, total), c, _ in scored),
                    key=lambda x: (-x[1], -x[2]))
    return (start, len(anchors), report)


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
