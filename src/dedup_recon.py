#!/usr/bin/env python3
"""Count slots once per recording when the same video was delivered under several names.

    python3 dedup_recon.py SRC RUN [--exclude NAME ...]

115 videos of the first delivery come under two or three file names each, 242 files
in all, each with its own part of the schedule (14 Oct 2026). Counted file by file,
a break listed in two of those schedules is counted twice, and so is every cut point
in it. This script groups files by video (same size and same first 4 MB), reads each
schedule as an --clean-schedule --own-channel run did, and counts each break once per
video, identified by its start time. Where the copies disagree about a break, the
best outcome is taken: kept, then lost, then outside the recording.

Fates, from the run: kept = the break has rows in the ledger; outside = the log has
an [outside] line for it; lost = anything else (unmatched boundaries, offset
inconsistent, or no recording start and therefore no outside test).
"""
import argparse
import csv
import hashlib
import os
import re
from collections import Counter, defaultdict

from silence_detect import load_breaks, is_placeholder

RANK = {"kept": 2, "lost": 1, "outside": 0}


def video_key(path: str):
    with open(path, "rb") as f:
        return (os.path.getsize(path), hashlib.md5(f.read(4 << 20)).hexdigest())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("run")
    ap.add_argument("--exclude", nargs="+", default=[])
    a = ap.parse_args()

    stems = sorted(d for d in os.listdir(a.run)
                   if os.path.isdir(os.path.join(a.run, d)) and not d.startswith("_")
                   and not any(x in d for x in a.exclude)
                   and os.path.exists(os.path.join(a.src, d + "_metadata.json")))
    # only files whose size is shared with another need hashing
    size = {s: os.path.getsize(os.path.join(a.src, s + ".mp4")) for s in stems}
    shared = {v for v, n in Counter(size.values()).items() if n > 1}
    key = {s: (video_key(os.path.join(a.src, s + ".mp4")) if size[s] in shared else s) for s in stems}
    copies = Counter(key.values())

    raw = Counter()
    best = {}                                    # (video, break start) -> (slots, fate)
    for s in stems:
        kept = set()
        led = os.path.join(a.run, s, "cut_plan.csv")
        if os.path.exists(led):
            kept = {int(r["break"]) for r in csv.DictReader(open(led, encoding="utf-8"))}
        out = set()
        log = os.path.join(a.run, "_logs", s + ".log")
        if os.path.exists(log):
            out = {int(m) for m in re.findall(r"^\[outside\] break (\d+)",
                                              open(log, encoding="utf-8", errors="replace").read(), re.M)}
        for b in load_breaks(os.path.join(a.src, s + "_metadata.json"), clean=True, own=True):
            n = sum(1 for ad in b["adverts"] if not is_placeholder(ad.get("unique_id")))
            idx = int(b["index"])
            fate = "kept" if idx in kept else ("outside" if idx in out else "lost")
            raw[fate] += n
            raw["breaks " + fate] += 1
            k = (key[s], b["start_time"])
            if k not in best or RANK[fate] > RANK[best[k][1]]:
                best[k] = (n, fate)

    ded = Counter()
    for n, fate in best.values():
        ded[fate] += n
        ded["breaks " + fate] += 1

    def show(name, c):
        tot = c["kept"] + c["lost"] + c["outside"]
        inside = c["kept"] + c["lost"]
        print(f"{name}")
        print(f"  slots in the schedules     {tot:>8,}   breaks {c['breaks kept'] + c['breaks lost'] + c['breaks outside']:>6,}")
        print(f"  outside the recording      {c['outside']:>8,}   breaks {c['breaks outside']:>6,}")
        print(f"  inside the recording       {inside:>8,}   breaks {c['breaks kept'] + c['breaks lost']:>6,}")
        print(f"  kept                       {c['kept']:>8,}   breaks {c['breaks kept']:>6,}   "
              f"({100 * c['kept'] / inside:.1f}% of the slots inside)" if inside else "")

    print(f"files {len(stems):,}; videos {len(copies):,}; videos under more than one name "
          f"{sum(1 for v in copies.values() if v > 1):,} ({sum(v for v in copies.values() if v > 1):,} files)\n")
    show("FILE BY FILE (as recon counts)", raw)
    print()
    show("ONCE PER VIDEO (break identified by its start time)", ded)


if __name__ == "__main__":
    main()
