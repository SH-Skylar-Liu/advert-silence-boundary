#!/usr/bin/env python3
"""Look at addressable placeholder records in the schedule, and at what the
recording holds where one of them was cut as a clip.

    python3 show_placeholders.py SRC                       # counts, then examples
    python3 show_placeholders.py SRC --kind ITVADDRESSABLE --own-slot --n 5
    python3 show_placeholders.py SRC --run ~/run_loss/s_prec   # where to look in the video

SRC is the directory holding <name>_metadata.json. Each example prints one whole
break as the schedule gives it, with the placeholder rows marked:

    same time   another record of the break has the same start_time
    OWN SLOT    no other record starts at that instant

With --run, ledger rows carrying a placeholder identifier are listed with the
second at which the clip starts in the recording and a frame_strip.py command,
so the pictures can be checked by eye.
"""
import argparse
import csv
import glob
import json
import os
import random
from collections import Counter

from silence_detect import is_placeholder, hms_to_s

KINDS = ("SKYADSMART", "C4ADDRESSABLE", "ITVADDRESSABLE")


def kind_of(uid: str) -> str:
    u = str(uid or "").upper().replace(" ", "")
    return next((k for k in KINDS if k in u), "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--kind", choices=KINDS, nargs="+", default=["C4ADDRESSABLE", "ITVADDRESSABLE"])
    ap.add_argument("--own-slot", action="store_true", help="examples only where the placeholder has a slot of its own")
    ap.add_argument("--n", type=int, default=3, help="examples per kind")
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--run", help="a run directory; list its ledger rows labelled as placeholders")
    a = ap.parse_args()

    counts, examples = Counter(), {k: [] for k in a.kind}
    for f in sorted(glob.glob(os.path.join(a.src, "*_metadata.json"))):
        name = os.path.basename(f)[: -len("_metadata.json")]
        for br in json.load(open(f, encoding="utf-8")).get("ad_breaks", []):
            ads = br.get("adverts", [])
            for ad in ads:
                k = kind_of(ad.get("unique_id"))
                if not k:
                    continue
                own = not any(b is not ad and b.get("start_time") == ad.get("start_time") for b in ads)
                counts[(k, "own slot" if own else "same time", name[:4])] += 1
                if k in examples and (own or not a.own_slot):
                    examples[k].append((name, br))

    print("COUNTS (kind, where, year)")
    for key in sorted(counts):
        print(f"  {counts[key]:>6,}  {key[0]:<15} {key[1]:<10} {key[2]}")

    rng = random.Random(a.seed)
    for k in a.kind:
        seen, picked = set(), []
        for name, br in rng.sample(examples[k], len(examples[k])):
            if (name, br.get("index")) not in seen:
                seen.add((name, br.get("index")))
                picked.append((name, br))
            if len(picked) == a.n:
                break
        for name, br in picked:
            ads = br.get("adverts", [])
            print(f"\n{k}   {name}   break {br.get('index')}  (starts {br.get('start_time')})")
            print(f"  {'time':<9} {'pos':<6} {'to next':>7}  {'identifier':<18} brand")
            for i, ad in enumerate(ads):
                later = [hms_to_s(b["start_time"]) for b in ads[i + 1:]
                         if b.get("start_time") != ad.get("start_time")]
                gap = f"{(later[0] - hms_to_s(ad['start_time'])) % 86400:.0f}s" if later else "(last)"
                mark = ""
                if is_placeholder(ad.get("unique_id")):
                    own = not any(b is not ad and b.get("start_time") == ad.get("start_time") for b in ads)
                    mark = "   <== OWN SLOT" if own else "   <== same time as a broadcast advert"
                print(f"  {ad.get('start_time', ''):<9} {str(ad.get('position_in_break', '')):<6} {gap:>7}  "
                      f"{str(ad.get('unique_id', '')):<18} {ad.get('brand', '')}{mark}")

    if a.run:
        print(f"\nLEDGER ROWS LABELLED AS A PLACEHOLDER in {a.run}")
        rows = []
        for plan in sorted(glob.glob(os.path.join(os.path.expanduser(a.run), "*", "cut_plan.csv"))):
            stem = os.path.basename(os.path.dirname(plan))
            for r in csv.DictReader(open(plan, encoding="utf-8")):
                if kind_of(r.get("unique_id")) in a.kind:
                    rows.append((stem, r))
        print(f"  {len(rows)} rows; showing {min(a.n * 2, len(rows))}")
        for stem, r in rng.sample(rows, min(a.n * 2, len(rows))):
            s = float(r["start_s"])
            print(f"\n  {stem}  break {r['break']} pos {r['pos']}  {r['unique_id']}")
            print(f"    starts at {int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:04.1f} in the file, "
                  f"clip length {float(r['duration_s']):.1f}s")
            print(f'    python3 src/frame_strip.py "{a.src}/{stem}.mp4" --at {s:.3f} --before 0.4 --after 1.0 '
                  f'--out "ph_{stem[:10]}_{r["break"]}_{r["pos"]}.html"')


if __name__ == "__main__":
    main()
