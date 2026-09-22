#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Frame strip: every frame around a cut point as one labelled HTML page.

Why this exists
---------------
Establishing ground truth means answering one question per cut point: which frame
is the advert's first? Stepping through a player frame by frame is slow and easy to
misread, because breaks often contain black or dissolving frames that look like a
boundary. This writes every frame in a window to a single page, each thumbnail
carrying its own timestamp burned in by ffmpeg, so the answer is read off the page
instead of counted by hand.

Every timestamp comes from ffmpeg, never from arithmetic here: `showinfo` reports
each frame's presentation time, and where the build has `drawtext` the time is
painted into the picture too. The workstation's ffmpeg has no drawtext (no
libfreetype), so there the caption is the authoritative label.


Usage
-----
    python3 frame_strip.py RECORDING.mp4 --at 2780.32 --out strip.html
    python3 frame_strip.py RECORDING.mp4 --at 2780.32 --before 0.4 --after 0.4
    python3 frame_strip.py RECORDING.mp4 --ledger plan.csv --clip b41_03   # look it up

Open the HTML in a browser (on the workstation), find the first frame of the new
advert, and record the timestamp printed on it.
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import os
import re
import subprocess
import sys
import tempfile

FFMPEG = "ffmpeg"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>frames around {at:.3f}s — {rec}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 16px; background: #fafafa; }}
 h1 {{ font-size: 1rem; margin-bottom: 2px; }}
 p.sub {{ color: #666; margin-top: 0; font-size: .8rem; }}
 .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }}
 figure {{ margin: 0; background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 6px; }}
 figure.cut {{ border: 2px solid #c33; }}
 figcaption {{ font-size: .75rem; color: #444; text-align: center; margin-top: 4px; }}
 img {{ width: 100%; display: block; }}
</style></head><body>
<h1>{rec}</h1>
<p class="sub">cut point {at:.3f} s &middot; window −{before:.2f} s / +{after:.2f} s &middot;
{n} frames &middot; the red border is the frame the pipeline cut at &middot;
times reported by ffmpeg ({label_src})</p>
<div class="grid">
{cells}
</div>
</body></html>
"""


def has_drawtext() -> bool:
    """Is ffmpeg built with drawtext? The workstation's build is not (no libfreetype)."""
    r = subprocess.run([FFMPEG, "-hide_banner", "-filters"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return " drawtext " in (r.stdout or "")


def extract(recording: str, start: float, dur: float, outdir: str, width: int,
            burn: bool) -> tuple[list[str], list[float]]:
    """Write every frame in [start, start+dur] as a JPEG, and return their times.

    The timestamp comes from ffmpeg itself: `drawtext` paints it into the picture
    where that filter exists, and `showinfo` reports every frame's pts on stderr
    either way. Times are read from that report, not assumed from a frame rate, so
    a variable frame rate or an inexact seek cannot silently mislabel a thumbnail.
    """
    chain = []
    if burn:
        label = "%{pts" + r"\:" + "hms" + r"\:" + f"{start}}}"
        chain.append(f"drawtext=text='{label}':fontsize=26:fontcolor=white:borderw=3:"
                     f"bordercolor=black:x=8:y=8")
    chain.append(f"scale={width}:-2")
    chain.append("showinfo")
    vf = ",".join(chain)

    base = [FFMPEG, "-hide_banner", "-loglevel", "info", "-ss", f"{start:.3f}",
            "-i", recording, "-t", f"{dur:.3f}", "-map", "0:v:0", "-vf", vf]
    tail = ["-q:v", "3", os.path.join(outdir, "f%04d.jpg")]
    # -fps_mode passthrough keeps every decoded frame, one JPEG each. It replaced
    # -vsync, which ffmpeg 9 (the build on the workstation) no longer accepts, so
    # fall back to the old spelling for older ffmpeg rather than requiring one.
    err = ""
    for mode in (["-fps_mode", "passthrough"], ["-vsync", "0"]):
        r = subprocess.run(base + mode + tail, stderr=subprocess.PIPE, text=True)
        err = r.stderr or ""
        if r.returncode == 0:
            break
        if "Unrecognized option" not in err and "Option not found" not in err:
            sys.exit(f"ffmpeg failed: {err[-400:]}")
    else:
        sys.exit(f"ffmpeg failed (neither -fps_mode nor -vsync accepted): {err[-300:]}")

    # showinfo prints one line per frame; pts_time is relative to the seek point,
    # so the absolute time in the recording is start + pts_time.
    times = [start + float(m) for m in re.findall(r"pts_time:([0-9.]+)", err)]
    names = sorted(os.listdir(outdir))
    if len(times) != len(names):        # keep the pairing honest rather than guessing
        times = []
    return names, times


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("--at", type=float, help="cut point in seconds (source recording time)")
    ap.add_argument("--ledger", help="cut ledger CSV, to look a cut point up by clip name")
    ap.add_argument("--clip", help="clip name or prefix inside that ledger, e.g. b41_03")
    ap.add_argument("--before", type=float, default=0.6, help="seconds before the cut (default 0.6)")
    ap.add_argument("--after", type=float, default=0.6, help="seconds after the cut (default 0.6)")
    ap.add_argument("--width", type=int, default=420, help="thumbnail width in pixels")
    ap.add_argument("--out", help="output HTML (default: strip_<at>.html in the current folder)")
    a = ap.parse_args()

    at = a.at
    if at is None:
        # Look the cut point up in a ledger, so the window is centred on exactly
        # the point the pipeline chose.
        if not (a.ledger and a.clip):
            sys.exit("give --at, or --ledger together with --clip")
        for row in csv.DictReader(open(a.ledger, encoding="utf-8", newline="")):
            if row["clip"].startswith(a.clip):
                at = float(row["start_s"])
                print(f"{row['clip']}  start_s {at:.3f}  "
                      f"stage A {row['start_silence_mid_s']}  shift {row['shift_ms']} ms")
                break
        else:
            sys.exit(f"no clip starting with {a.clip!r} in {a.ledger}")

    start = max(0.0, at - a.before)
    out = a.out or f"strip_{at:.3f}.html"
    with tempfile.TemporaryDirectory() as tmp:
        burn = has_drawtext()
        names, times = extract(a.recording, start, a.before + a.after, tmp, a.width, burn)
        cells = []
        for i, name in enumerate(names):
            with open(os.path.join(tmp, name), "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            # Prefer the time ffmpeg reported for this very frame; fall back to a
            # nominal 25 fps grid only when the report could not be paired up, and
            # mark those captions with ~ so an estimate is never read as measured.
            if times:
                t, mark = times[i], ""
            else:
                t, mark = start + i / 25.0, "~"
            cut = ' class="cut"' if t <= at < t + 0.04 else ""
            mmss = f"{int(t // 60):d}:{t % 60:06.3f}"
            cells.append(
                f'<figure{cut}><img src="data:image/jpeg;base64,{b64}" alt="frame {i}">'
                f"<figcaption>#{i}&nbsp;&nbsp;{mark}{t:.3f} s&nbsp;&nbsp;({mmss})"
                f"</figcaption></figure>"
            )
        label_src = ("showinfo, and burned into the picture" if burn and times else
                     "showinfo" if times else "estimated at 25 fps (showinfo unavailable)")
        page = PAGE.format(rec=html.escape(os.path.basename(a.recording)), at=at,
                           before=a.before, after=a.after, n=len(names),
                           label_src=label_src, cells="\n".join(cells))
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"written: {os.path.abspath(out)}  ({len(names)} frames)")


if __name__ == "__main__":
    main()
