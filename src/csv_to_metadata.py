# -*- coding: utf-8 -*-
"""
Build a metadata JSON (advert boundaries) from a BFI schedule CSV, in the same
format as the existing *_metadata.json, so detect_silence.py --metadata can align
silences to real ad boundaries on any recording of the same day.

Break grouping
--------------
Each advert row has "All PIB pos" (1,2,3,...) and "All PIB rel" (First,2nd,...,Last).
pos==1 (or rel=="First") marks the first advert of a new break; consecutive rows
until the next "First" belong to the same break.

Usage
-----
# whole day
python3 csv_to_metadata.py "2024-03-26_ITV1HD.csv" -o day.json

# only breaks inside a time window (for one recording, e.g. 09-00.mp4 -> 09:00-10:00)
python3 csv_to_metadata.py "2024-03-26_ITV1HD.csv" --after 09:00:00 --before 10:00:00 -o breaks_09.json
"""
import argparse
import csv
import json


def to_s(t: str) -> int:
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("-o", "--out", default="day_metadata.json")
    ap.add_argument("--after", help="keep only breaks starting >= this time (HH:MM:SS)")
    ap.add_argument("--before", help="keep only breaks starting <= this time (HH:MM:SS)")
    a = ap.parse_args()

    # utf-8-sig handles a possible BOM
    with open(a.csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    breaks, cur = [], None
    for r in rows:
        pos = (r.get("All PIB pos") or "").strip()
        rel = (r.get("All PIB rel") or "").strip()
        start = (r.get("Start time") or "").strip()
        if not start:
            continue
        # new break start
        if pos == "1" or rel.lower() == "first":
            cur = {
                "index": len(breaks) + 1,
                "start_time": start,
                "programme_before": {"title": r.get("BARB Prog Before", "")},
                "programme_after": {"title": r.get("BARB Prog After", "")},
                "adverts": [],
            }
            breaks.append(cur)
        if cur is None:  # leading rows before the first break start
            continue
        cur["adverts"].append({
            "unique_id": r.get("Film Code", ""),
            "advertiser": r.get("Advertiser", ""),
            "brand": r.get("Brand", ""),
            "category": r.get("Minor category", ""),
            "start_time": start,
            "position_in_break": rel,
        })

    # optional time-window filter
    def keep(b):
        t = to_s(b["start_time"])
        if a.after and t < to_s(a.after):
            return False
        if a.before and t > to_s(a.before):
            return False
        return True

    breaks = [b for b in breaks if keep(b)]
    # Note: end_time is intentionally omitted -- detect_silence only needs each
    # advert's start as a boundary, and leaving out "break end" avoids a false MISS
    # when the break end falls beyond the recording.

    out = {
        "video_info": {
            "channel": rows[0].get("Channel", "") if rows else "",
            "date": rows[0].get("Date", "") if rows else "",
        },
        "ad_breaks": breaks,
        "summary": {
            "total_ad_breaks": len(breaks),
            "total_adverts": sum(len(b["adverts"]) for b in breaks),
        },
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"written: {a.out}")
    print(f"breaks: {len(breaks)}   ads: {out['summary']['total_adverts']}\n")
    print(f"{'#':>3}  {'start':>9}  {'#ads':>4}   first brand              prog before")
    for b in breaks:
        first_brand = b["adverts"][0]["brand"] if b["adverts"] else ""
        print(f"{b['index']:>3}  {b['start_time']:>9}  {len(b['adverts']):>4}   "
              f"{first_brand[:24]:<24} {b['programme_before']['title'][:20]}")


if __name__ == "__main__":
    main()
