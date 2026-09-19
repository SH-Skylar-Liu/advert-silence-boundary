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

The timestamp is drawn by ffmpeg into the picture (`%{pts:hms:<offset>}`), so a
thumbnail cannot be mislabelled by any arithmetic in this script.

中文：人工真值只需要回答一个问题——广告的第一帧是哪一帧。在播放器里逐帧翻既慢又容易看错
（广告之间常有黑场或渐变）。本脚本把切点前后的每一帧导出成一页缩略图，时间戳由 ffmpeg
直接烧进画面，因此不会因为脚本里的换算而贴错标签，看图直接读时间即可。

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
each thumbnail shows its own timestamp, burned in by ffmpeg</p>
<div class="grid">
{cells}
</div>
</body></html>
"""


def extract(recording: str, start: float, dur: float, outdir: str, width: int) -> list[str]:
    """Write every frame in [start, start+dur] as a JPEG with its absolute time drawn on it.

    中文：把窗口内每一帧导出成 JPEG，并把该帧的绝对时间画在画面上。
    `-ss` 放在 `-i` 之前是快速定位；`pts:hms:start` 把输出的相对时间加回窗口起点，
    因此画面上显示的就是源录制里的真实时间。
    """
    label = f"%{{pts\\:hms\\:{start}}}"
    vf = (f"drawtext=text='{label}':fontsize=26:fontcolor=white:borderw=3:"
          f"bordercolor=black:x=8:y=8,scale={width}:-2")
    # -fps_mode passthrough keeps every decoded frame, one JPEG each. It replaced
    # -vsync, which ffmpeg 9 (the build on the workstation) no longer accepts, so
    # fall back to the old spelling for older ffmpeg rather than requiring one.
    # 中文：-fps_mode passthrough 保留每一帧，一帧一张图。它取代了 -vsync；
    # 工作站上的 ffmpeg 9 已不接受 -vsync，旧版 ffmpeg 则只认 -vsync，故两种都试。
    base = [FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}",
            "-i", recording, "-t", f"{dur:.3f}", "-map", "0:v:0", "-vf", vf]
    tail = ["-q:v", "3", os.path.join(outdir, "f%04d.jpg")]
    for mode in (["-fps_mode", "passthrough"], ["-vsync", "0"]):
        r = subprocess.run(base + mode + tail, stderr=subprocess.PIPE, text=True)
        if r.returncode == 0:
            break
        if "Unrecognized option" not in r.stderr and "Option not found" not in r.stderr:
            sys.exit(f"ffmpeg failed: {r.stderr[-400:]}")
    else:
        sys.exit(f"ffmpeg failed (neither -fps_mode nor -vsync accepted): {r.stderr[-300:]}")
    return sorted(os.listdir(outdir))


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
        # Look the cut point up in a ledger, so the window is centred on exactly the
        # point the pipeline chose. 中文：从台账里查切点，保证窗口正对 pipeline 选的位置。
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
        names = extract(a.recording, start, a.before + a.after, tmp, a.width)
        cells = []
        for i, name in enumerate(names):
            with open(os.path.join(tmp, name), "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            # Frame index is informational only; the authoritative time is the one
            # ffmpeg drew into the picture.
            # 中文：序号仅供参考，权威时间是 ffmpeg 画在画面上的那个。
            approx = start + i / 25.0
            cut = ' class="cut"' if approx <= at < approx + 0.04 else ""
            cells.append(
                f'<figure{cut}><img src="data:image/jpeg;base64,{b64}" alt="frame {i}">'
                f"<figcaption>#{i}&nbsp;&nbsp;~{approx:.3f} s</figcaption></figure>"
            )
        page = PAGE.format(rec=html.escape(os.path.basename(a.recording)), at=at,
                           before=a.before, after=a.after, n=len(names),
                           cells="\n".join(cells))
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"written: {os.path.abspath(out)}  ({len(names)} frames)")


if __name__ == "__main__":
    main()
