#!/usr/bin/env python3
"""A stratified viewing sample of cut adverts: three stills each, a gallery page, and a
coding sheet for close reading before modelling (supervision of 20 Oct 2026, Step 0).
按品类分层抽样已切分的广告：每条截三帧，生成看片图库页面和一张精读编码表。

    # 8 adverts per major category, from the October full run
    python3 viewing_sample.py --run ~/runs/v17 --src $SRC --csv CSV_DIR --per-group 8 --out ~/viewing_01

    # one category in depth, 2024 only, ITV1 only
    python3 viewing_sample.py --run ~/runs/v17 --src $SRC --csv CSV_DIR \
        --only Food --year 2024 --channel ITV1 --per-group 40 --group-by minor --out ~/viewing_food

Clips come from the ledger (start_s / end_s in the recording), so nothing has to be cut
first. Each sampled advert appears once (one airing picked at random); addressable
placeholders are skipped. OUT/index.html shows the stills with brand, category, channel
and date, plus an ffplay command to watch the advert; OUT/coding_sheet.csv has empty
columns to fill in while watching.

Needs ffmpeg on PATH. Frames are decoded and written as JPEG; the source is untouched.
"""
import argparse
import csv
import glob
import html
import os
import random
import re
import shlex
import shutil
import subprocess
from collections import defaultdict

from corpus_stats import canon_channel, load_records, placeholder_kind, read_rows

CODING_COLUMNS = ["who_appears", "setting", "tone_or_address", "implied_audience", "notes"]


def recording_info(name: str) -> tuple:
    """'2024-03-26_ITV1HD_09:00:00.mp4' -> ('2024-03-26', 'ITV1HD', 32400)
    从文件名取日期、频道和录像开始时刻（秒）。"""
    m = re.match(r"(\d{4}-\d{2}-\d{2})_(.+?)_(\d{2})[:_](\d{2})[:_](\d{2})", os.path.basename(name))
    if not m:
        return "", "", None
    return m.group(1), canon_channel(m.group(2)), int(m.group(3)) * 3600 + int(m.group(4)) * 60 + int(m.group(5))


def find_video(src: str, recording: str) -> str:
    if os.path.isabs(recording) and os.path.exists(recording):
        return recording
    p = os.path.join(src, os.path.basename(recording))
    return p if os.path.exists(p) else ""


def grab(video: str, t: float, out: str) -> bool:
    """One frame at t seconds, 320 px wide / 在 t 秒处截一帧。"""
    if os.path.exists(out):
        return True
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video,
           "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "4", out]
    try:
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except FileNotFoundError:                     # no ffmpeg
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run directory with */cut_plan.csv")
    ap.add_argument("--src", required=True, help="directory of the recordings and <name>_metadata.json")
    ap.add_argument("--csv", nargs="+", help="BFI schedule CSVs (gives major/mid category; without it, minor only)")
    ap.add_argument("--group-by", default="major", choices=["major", "mid", "minor", "channel", "year"])
    ap.add_argument("--per-group", type=int, default=8)
    ap.add_argument("--only", help="keep adverts whose major or minor category contains this text")
    ap.add_argument("--year", help="keep one year, e.g. 2024")
    ap.add_argument("--channel", help="keep channels whose name contains this text")
    ap.add_argument("--seed", type=int, default=20261020)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not shutil.which("ffmpeg"):
        print("WARNING: ffmpeg not on PATH, no stills will be written")

    # metadata per identifier / 每个标识符对应的元数据
    _, recs, _, _ = load_records(a.csv, a.src)
    # the same advert airs many times: keep every airing, keyed by (identifier, date)
    # 同一条广告会播很多次：按（标识符, 日期）保留所有播出，后面取与片段时刻最近的那次
    airings = defaultdict(list)
    for r in recs:
        if not r["kind"]:
            airings[(r["code"], r["date"])].append(r)
            airings[(r["code"], "")].append(r)

    if a.group_by in ("major", "mid") and not any(r.get(a.group_by) for r in recs):
        print(f"no {a.group_by} category in this metadata (JSON only?): grouping by minor instead")
        a.group_by = "minor"

    # ledger rows, one per planned clip / 台账：每行一个计划切出的片段
    clips = defaultdict(list)
    for fp in sorted(glob.glob(os.path.join(os.path.expanduser(a.run), "*", "cut_plan.csv"))):
        for row in read_rows(fp):
            code = (row.get("unique_id") or "").strip()
            if not code or placeholder_kind(code) or not row.get("start_s") or not row.get("end_s"):
                continue
            clips[code].append(row)
    print(f"ledger: {len(clips):,} unique adverts with a planned clip")

    # filter, then group / 过滤后分组
    rng = random.Random(a.seed)
    groups = defaultdict(list)
    for code, rows in clips.items():
        row = rng.choice(rows)
        date, chan, t0 = recording_info(row.get("recording", ""))
        cands = airings.get((code, date)) or airings.get((code, ""), [])
        clip_t = t0 + float(row["start_s"]) if t0 is not None else None
        m = min(cands, key=lambda r: abs((r["t"] or 0) - clip_t)) if cands and clip_t is not None             else (cands[0] if cands else {})
        info = {"code": code, "row": row, "date": date, "year": date[:4], "channel": chan,
                "brand": m.get("brand") or row.get("brand", ""), "advertiser": m.get("advertiser", ""),
                "major": m.get("major", ""), "mid": m.get("mid", ""), "minor": m.get("minor", ""),
                "prog_before": m.get("prog_before", "")}
        if a.only and a.only.lower() not in (info["major"] + "|" + info["minor"]).lower():
            continue
        if a.year and info["year"] != a.year:
            continue
        if a.channel and a.channel.lower() not in chan.lower():
            continue
        groups[info[a.group_by] or "(unknown)"].append(info)

    os.makedirs(os.path.join(a.out, "stills"), exist_ok=True)
    picked, missing = [], 0
    for g in sorted(groups, key=lambda g: -len(groups[g])):
        pool = sorted(groups[g], key=lambda x: x["code"])
        for info in rng.sample(pool, min(a.per_group, len(pool))):
            video = find_video(os.path.expanduser(a.src), info["row"]["recording"])
            if not video:
                missing += 1
                continue
            s, e = float(info["row"]["start_s"]), float(info["row"]["end_s"])
            info["video"], info["start"], info["dur"] = video, s, e - s
            info["stills"] = []
            for k, frac in enumerate((0.15, 0.5, 0.85)):          # opening, middle, end card
                name = f"{info['code']}_{k}.jpg"
                if grab(video, s + frac * (e - s), os.path.join(a.out, "stills", name)):
                    info["stills"].append(name)
            info["group"] = g
            picked.append(info)
        print(f"  {len([p for p in picked if p['group'] == g]):>3} / {len(pool):>5}  {g}")
    if missing:
        print(f"{missing} sampled adverts skipped: recording not found in {a.src}")

    # coding sheet / 精读编码表
    with open(os.path.join(a.out, "coding_sheet.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "group", "identifier", "brand", "advertiser", "major", "mid", "minor", "channel", "date",
                    "programme_before", "recording", "start_s", "seconds"] + CODING_COLUMNS)
        for i, p in enumerate(picked, 1):
            w.writerow([i, p["group"], p["code"], p["brand"], p["advertiser"], p["major"], p["mid"], p["minor"],
                        p["channel"], p["date"], p["prog_before"], os.path.basename(p["video"]),
                        f"{p['start']:.3f}", f"{p['dur']:.1f}"] + [""] * len(CODING_COLUMNS))

    # gallery page / 看片图库页面
    e = html.escape
    cards, last = [], None
    for i, p in enumerate(picked, 1):
        if p["group"] != last:
            cards.append(f"<h2>{e(p['group'])} <small>({len(groups[p['group']]):,} adverts in the ledger)</small></h2>")
            last = p["group"]
        play = f"ffplay -autoexit -ss {p['start']:.3f} -t {p['dur']:.1f} {shlex.quote(p['video'])}"
        imgs = "".join(f"<img src='stills/{e(s)}' loading=lazy>" for s in p["stills"])
        cards.append(f"<div class=card><div class=imgs>{imgs}</div><b>{i}. {e(p['brand'])}</b> "
                     f"<span class=m>{e(p['advertiser'])}</span><br><span class=m>{e(' › '.join(x for x in (p['major'], p['mid'], p['minor']) if x))}"
                     f"<br>{e(p['channel'])} · {e(p['date'])} · {p['dur']:.0f}s · after {e(p['prog_before'])} · {e(p['code'])}</span>"
                     f"<code>{e(play)}</code></div>")
    css = """:root{--bg:#fcfcfb;--ink:#0b0b0b;--m:#6b6a66;--line:#e1e0d9}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--ink:#f0efec;--m:#a3a29b;--line:#2c2c2a}}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px}
h2{border-top:1px solid var(--line);padding-top:12px;margin-top:28px}small,.m{color:var(--m);font-weight:normal}
.card{margin:14px 0;padding-bottom:12px;border-bottom:1px solid var(--line)}.imgs{display:flex;gap:4px;flex-wrap:wrap}
img{width:32%;min-width:200px;border-radius:4px}code{display:block;font-size:11px;color:var(--m);margin-top:4px;overflow-wrap:anywhere}"""
    with open(os.path.join(a.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>Viewing sample</title><style>{css}</style><h1>Viewing sample · grouped by {e(a.group_by)}</h1>"
                f"<p class=m>{len(picked)} adverts, seed {a.seed}. Stills at 15%, 50% and 85% of each clip. "
                f"Copy the ffplay line into a terminal to watch; notes go in coding_sheet.csv (same numbering).</p>"
                + "".join(cards))
    print(f"wrote {os.path.join(a.out, 'index.html')} and coding_sheet.csv ({len(picked)} adverts)")


if __name__ == "__main__":
    main()
