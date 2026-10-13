# -*- coding: utf-8 -*-
"""
Stage B: refine each advert's first-frame cut point, from the silence midpoint to
the first scene change that follows it.

Why this exists
---------------
Stage A (detect_silence.py) puts the cut at the MIDPOINT of the silence. Checking
frame by frame, Stephen found that the midpoint usually falls a few frames before
the true first frame, so the last 7-8 frames of the previous advert bleed into the
start of this one. His fix is to run scene detection over the FIRST SECOND of each
segment only and take the first scene change as the true first frame. Measured
corrections: 800 ms in one case, 120 ms in another; widening the window past one
second starts catching cuts inside the advert instead.

Cost: the END of each advert stays on the next silence midpoint, so 1-3 frames are
lost at the tail. Adverts almost always end on a held frame, so Stephen judged this
acceptable (three-way meeting, 18 Aug 2026).

Methodological point (usable directly in the methodology chapter): neither stage is
machine learning. ffmpeg detects the absence of an audio signal; scene detection
compares pixel differences between adjacent frames. Corpus construction is
deterministic and reproducible; the VLM is used only in the later content-analysis
layer.

Usage
-----
As a module (called by cut_adverts.py):
    from refine_first_frame import refine_start
    new_start, shift, status = refine_start("rec.mp4", 758.60)

Standalone (to check a batch of cut points):
    python3 refine_first_frame.py rec.mp4 758.60 788.50 798.02
    python3 refine_first_frame.py rec.mp4 --csv cuts.csv --window 1.0

Backend: chosen automatically - scenedetect when importable (the same
implementation as Stephen's script), otherwise ffmpeg's select='gt(scene,T)'
filter, which needs no Python dependencies at all. Force one with --backend.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys

# Two backends:
#   scenedetect - same implementation as Stephen's first_scene_change_detect.py,
#                 which makes frame-by-frame cross-checking straightforward
#   ffmpeg      - the select='gt(scene,T)' filter only; no Python dependencies
# Measured on the server 21 Aug 2026: scenedetect is installed under ~/.local but
# cv2 is missing, both venvs are broken, and Python 3.14 may have no opencv wheel.
# The ffmpeg backend is therefore the path that currently works.
try:
    from scenedetect import ContentDetector, SceneManager, open_video
    from scenedetect.frame_timecode import FrameTimecode
    HAVE_SCENEDETECT = True
except Exception:                       # missing cv2 raises ModuleNotFoundError, not just ImportError
    HAVE_SCENEDETECT = False

FFMPEG = "ffmpeg"
FFMPEG_SCENE_THRESHOLD = 0.3   # ffmpeg scene-change threshold (0-1); settle it against Stephen's output
WINDOW_S = 1.0                 # first second only, as agreed; a wider window catches in-advert cuts
MIN_SHIFT_S = 0.02             # below half a frame (40 ms at 25 fps) counts as noise
_video_cache: dict = {}


def _refine_ffmpeg(recording: str, start_s: float, window_s: float, threshold: float | None):
    """
    ffmpeg backend: find the first scene change in [start_s, start_s + window_s].

    Same principle as PySceneDetect - compare successive frames and flag a cut when
    the difference exceeds a threshold - but done by ffmpeg's select filter, so no
    opencv or scenedetect is needed. `-ss` before `-i` makes decoding start at that
    point with output timestamps restarting at zero, so pts_time is the offset from
    the cut point. The first decoded frame is often flagged as a scene change (it has
    no predecessor to compare with); its pts_time is about 0 and MIN_SHIFT_S drops it.
    """
    thr = FFMPEG_SCENE_THRESHOLD if threshold is None else threshold
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-ss", f"{start_s:.3f}", "-t", f"{window_s:.3f}",
           "-i", recording, "-an", "-vf", f"select='gt(scene,{thr})',showinfo", "-f", "null", "-"]
    proc = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]
    for t in sorted(times):
        if MIN_SHIFT_S <= t <= window_s:
            return start_s + t, t, "refined"
    return start_s, 0.0, "clean"


def _open(path: str):
    """Open a video, reusing the handle across cut points of the same file."""
    if path not in _video_cache:
        _video_cache[path] = open_video(path)
    return _video_cache[path]


def refine_start(recording: str, start_s: float, window_s: float = WINDOW_S,
                 threshold: float | None = None, verbose: bool = False,
                 backend: str = "auto", min_scene_len: int | None = None):
    """
    Find the first scene change in [start_s, start_s + window_s].

    In:
        recording : path to the video
        start_s   : the Stage A cut point (silence midpoint), in seconds
        window_s  : search window, default 1.0 s (the agreed value; do not enlarge)
        threshold : detector threshold; None uses the backend default
        backend   : "auto" | "ffmpeg" | "scenedetect"
        min_scene_len : scenedetect only, in FRAMES; None keeps PySceneDetect's default
                    (15). The default suppresses any cut in the first 15 frames of the
                    window, which is where the true first frame usually is (measured
                    15 Sep 2026: every refinement landed at >= 597 ms). Use 1.
    Out:
        (refined_s, shift_s, status)
        refined_s : refined cut point, equal to start_s when nothing was found
        shift_s   : how far it moved forward, in seconds (0 = unchanged)
        status    : "refined" | "clean" (no scene change; the first frame was already
                    clean) | "no-backend" | "error: ..."

    When there is no scene change NOTHING is changed. Stephen measured no false
    positives with this method: a clean first frame reports no scene, which is what
    makes it safe to run over the whole batch.
    """
    if backend == "ffmpeg" or (backend == "auto" and not HAVE_SCENEDETECT):
        try:
            return _refine_ffmpeg(recording, start_s, window_s, threshold)
        except Exception as e:                          # noqa: BLE001
            return start_s, 0.0, f"error: {e}"
    if not HAVE_SCENEDETECT:
        return start_s, 0.0, "no-backend"
    try:
        video = _open(recording)
        fps = video.frame_rate
        video.seek(FrameTimecode(start_s, fps))

        sm = SceneManager()
        # Pass only what was asked for, so an unset run is byte-for-byte the old call
        kwargs = {}
        if threshold is not None:
            kwargs["threshold"] = threshold
        if min_scene_len is not None:
            kwargs["min_scene_len"] = min_scene_len
        sm.add_detector(ContentDetector(**kwargs))
        sm.detect_scenes(video=video, duration=FrameTimecode(window_s, fps),
                         show_progress=False)
        # start_in_scene=True treats the window start as the start of a scene, so the
        # END of scenes[0] is the first scene change inside the window
        scenes = sm.get_scene_list(start_in_scene=True)
        sm.clear()

        if len(scenes) < 2:
            return start_s, 0.0, "clean"

        cut_at = scenes[0][1].get_seconds()
        shift = cut_at - start_s
        if shift < MIN_SHIFT_S or shift > window_s:
            return start_s, 0.0, "clean"
        if verbose:
            print(f"    refine {start_s:.3f} -> {cut_at:.3f}  (+{shift * 1000:.0f} ms "
                  f"= {shift * fps:.1f} frames)")
        return cut_at, shift, "refined"
    except Exception as e:                              # noqa: BLE001
        # one failed cut point must not stop the batch: fall back to the Stage A cut
        # and carry the reason out with the result
        return start_s, 0.0, f"error: {e}"


def scene_changes(recording: str, start_s: float, window_s: float,
                  threshold: float | None = None, backend: str = "auto",
                  min_scene_len: int | None = None):
    """Every scene change in [start_s, start_s + window_s], earliest first.

    refine_start() returns the first change after its start point and nothing else.
    Searching a whole silence needs the list, because the change wanted there is not
    always the first one after the midpoint: it can lie before the midpoint.
    Returns (times in seconds, status), status "ok" | "no-backend" | "error: ...".
    """
    if backend == "ffmpeg" or (backend == "auto" and not HAVE_SCENEDETECT):
        try:
            thr = FFMPEG_SCENE_THRESHOLD if threshold is None else threshold
            cmd = [FFMPEG, "-hide_banner", "-nostats", "-ss", f"{start_s:.3f}", "-t", f"{window_s:.3f}",
                   "-i", recording, "-an", "-vf", f"select='gt(scene,{thr})',showinfo", "-f", "null", "-"]
            proc = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
            times = sorted(float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr))
            return [start_s + t for t in times if MIN_SHIFT_S <= t <= window_s], "ok"
        except Exception as e:                          # noqa: BLE001
            return [], f"error: {e}"
    if not HAVE_SCENEDETECT:
        return [], "no-backend"
    try:
        video = _open(recording)
        fps = video.frame_rate
        video.seek(FrameTimecode(start_s, fps))
        sm = SceneManager()
        kwargs = {}
        if threshold is not None:
            kwargs["threshold"] = threshold
        if min_scene_len is not None:
            kwargs["min_scene_len"] = min_scene_len
        sm.add_detector(ContentDetector(**kwargs))
        sm.detect_scenes(video=video, duration=FrameTimecode(window_s, fps), show_progress=False)
        scenes = sm.get_scene_list(start_in_scene=True)
        sm.clear()
        cuts = [sc[1].get_seconds() for sc in scenes[:-1]]      # the last scene ends with the window
        return [c for c in cuts if MIN_SHIFT_S <= c - start_s <= window_s], "ok"
    except Exception as e:                              # noqa: BLE001
        return [], f"error: {e}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("starts", nargs="*", type=float, help="Stage A cut points in seconds")
    ap.add_argument("--csv", help="read cut points from a CSV with a start_s column")
    ap.add_argument("--window", type=float, default=WINDOW_S)
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--min-scene-len", type=int, default=None,
                    help="scenedetect only, in frames; default 15 hides cuts in the first 0.6 s")
    ap.add_argument("--backend", choices=["auto", "ffmpeg", "scenedetect"], default="auto")
    a = ap.parse_args()

    backend = a.backend
    if backend == "auto":
        backend = "scenedetect" if HAVE_SCENEDETECT else "ffmpeg"
    if backend == "scenedetect" and not HAVE_SCENEDETECT:
        print("!! scenedetect unavailable (missing cv2 or not installed); use --backend ffmpeg")
        sys.exit(1)
    thr = a.threshold if a.threshold is not None else FFMPEG_SCENE_THRESHOLD
    print(f"backend = {backend}" + (f" (threshold {thr})" if backend == "ffmpeg" else ""))

    rows = []
    if a.csv:
        with open(a.csv, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        starts = [float(r["start_s"]) for r in rows]
    else:
        starts = a.starts
    if not starts:
        print("nothing to do: give cut points in seconds, or use --csv")
        sys.exit(1)

    n_ref = 0
    print(f"{'start_s':>10} {'refined_s':>10} {'shift_ms':>9}  status")
    for i, s in enumerate(starts):
        r, shift, st = refine_start(a.recording, s, a.window, a.threshold, backend=backend,
                                min_scene_len=a.min_scene_len)
        n_ref += st == "refined"
        print(f"{s:10.3f} {r:10.3f} {shift * 1000:9.0f}  {st}")
        if rows:
            rows[i]["refined_s"] = f"{r:.3f}"
            rows[i]["shift_ms"] = f"{shift * 1000:.0f}"
            rows[i]["refine_status"] = st

    print(f"\nrefined {n_ref}/{len(starts)} cut points (the rest were already clean)")

    if a.csv and rows:
        out = a.csv.replace(".csv", "_refined.csv")
        with open(out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"written: {out}")


if __name__ == "__main__":
    main()
