#!/usr/bin/env python3
"""How long is a break? Advertising time per break against Ofcom's rule for public
service channels (COSTA Rule 5: a break during a programme on a PSB channel may hold
at most 3 min 30 s of advertising and teleshopping; films are exempt).
每个广告段里广告加起来有多长？对照 Ofcom COSTA Rule 5：PSB 频道节目中间的广告段，
广告不超过 3 分 30 秒（210 秒），电影除外。

    python3 break_lengths.py --src $SRC --out ~/break_lengths

Reads the metadata JSON only; nothing is decoded. Breaks are cleaned the way the
pipeline cleans them (silence_detect.clean_breaks: placeholders dropped, split breaks
merged, joined breaks split), and a break listed in several overlapping recordings is
counted once.

For each break:
    advertising  sum of the lengths the identifiers state, one advert per start time
    span         last start - first start + length of the last advert
    gap          span - advertising; above zero means time in the break that no record
                 accounts for (a trailer, an unlogged advert, a split that was missed)
    position     "during" if the programme before and after have the same title,
                 "between" otherwise. Rule 5 applies to "during" only.

Writes OUT/breaks.csv (one row per break), OUT/psb_during_over_210.csv (the breaks
that exceed the rule, to look at), and prints the summary tables.
"""
import argparse
import csv
import glob
import json
import os
import statistics
from collections import Counter, defaultdict

from corpus_stats import canon_channel, iso_date
from silence_detect import clean_breaks, hms_to_s, id_duration

PSB = {"ITV1", "Channel 4", "Channel 5"}       # COSTA definition (d); Channel 3 = ITV1 here
LIMIT = 210                                     # Rule 5: 3 min 30 s of advertising


def break_row(br: dict, vi: dict, rec: str):
    """One cleaned break -> a summary row, or None if it cannot be measured.
    把一个清洗后的广告段汇总成一行；量不出来（缺时长）时返回 None。"""
    by_start = {}
    for ad in br.get("adverts", []):              # one advert per start time / 同刻只留一条
        by_start.setdefault(ad.get("start_time"), ad)
    starts = sorted(by_start, key=hms_to_s)
    if not starts:
        return None
    lengths = [id_duration(by_start[t].get("unique_id")) for t in starts]
    if any(x is None for x in lengths):
        return None
    t0, t1 = hms_to_s(starts[0]), hms_to_s(starts[-1])
    if t1 < t0:                                    # break crosses midnight / 跨午夜
        t1 += 86400
    pb = br.get("programme_before") or {}
    pa = br.get("programme_after") or {}
    channel = canon_channel(pb.get("channel") or vi.get("channel", ""))
    before, after = (pb.get("title") or "").strip(), (pa.get("title") or "").strip()
    advertising = sum(lengths)
    span = (t1 - t0) + lengths[-1]
    return {
        "recording": rec, "channel": channel, "psb": "PSB" if channel in PSB else "non-PSB",
        "date": iso_date(str(vi.get("date", ""))), "start": starts[0], "hour": int(t0 // 3600) % 24,
        "position": "during" if before and before == after else "between",
        "programme_before": before, "programme_after": after,
        "adverts": len(starts), "advertising_s": advertising, "span_s": round(span, 1),
        "gap_s": round(span - advertising, 1),
    }


def summary(rows: list) -> list:
    a = [r["advertising_s"] for r in rows]
    if not a:
        return [0, "", "", "", "", ""]
    return [len(a), statistics.median(a), max(a),
            f"{sum(x <= LIMIT for x in a) / len(a):.1%}",
            f"{sum(x <= 230 for x in a) / len(a):.1%}",
            f"{sum(r['gap_s'] > 0 for r in rows) / len(rows):.1%}"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="directory of <name>_metadata.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(os.path.expanduser(a.out), exist_ok=True)

    rows, seen, skipped = [], set(), 0
    for fp in sorted(glob.glob(os.path.join(os.path.expanduser(a.src), "*_metadata.json"))):
        d = json.load(open(fp, encoding="utf-8"))
        vi, rec = d.get("video_info", {}), os.path.basename(fp)[: -len("_metadata.json")]
        for br in clean_breaks(d.get("ad_breaks", [])):
            r = break_row(br, vi, rec)
            if r is None:
                skipped += 1
                continue
            key = (r["channel"], r["date"], r["start"])    # same break in two recordings / 去重
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
    print(f"{len(rows):,} breaks measured, {skipped:,} skipped (an advert without a stated length)\n")

    head = ["n_breaks", "median_s", "max_s", f"<= {LIMIT}s", "<= 230s", "with_gap"]
    print("ADVERTISING TIME PER BREAK, by channel type and position")
    print(f"  {'group':<22}" + "".join(f"{h:>11}" for h in head))
    for psb in ("PSB", "non-PSB"):
        for pos in ("during", "between"):
            s = summary([r for r in rows if r["psb"] == psb and r["position"] == pos])
            print(f"  {psb + ' · ' + pos:<22}" + "".join(f"{str(x):>11}" for x in s))

    print("\nBY CHANNEL (breaks during a programme)")
    print(f"  {'channel':<22}" + "".join(f"{h:>11}" for h in head))
    chans = Counter(r["channel"] for r in rows)
    for c, _ in chans.most_common():
        s = summary([r for r in rows if r["channel"] == c and r["position"] == "during"])
        print(f"  {c:<22}" + "".join(f"{str(x):>11}" for x in s))

    print("\nPSB · during: 18:00-23:00 against the rest of the day")
    print(f"  {'hours':<22}" + "".join(f"{h:>11}" for h in head))
    psb_during = [r for r in rows if r["psb"] == "PSB" and r["position"] == "during"]
    for lab, keep in (("18:00-23:00", lambda h: 18 <= h < 23), ("other hours", lambda h: not 18 <= h < 23)):
        s = summary([r for r in psb_during if keep(r["hour"])])
        print(f"  {lab:<22}" + "".join(f"{str(x):>11}" for x in s))

    print("\nPSB · during: advertising time per break, in 10 s bins (counts)")
    bins = Counter(min(r["advertising_s"] // 10 * 10, 300) for r in psb_during)
    for b in sorted(bins):
        lab = f"{b}-{b + 9}s" if b < 300 else "300s+"
        mark = "  <- limit" if b == LIMIT else ""
        print(f"  {lab:>9} {bins[b]:>6} {'#' * min(60, bins[b] * 60 // max(bins.values()))}{mark}")

    print("\nPSB · during: by year")
    print(f"  {'year':<22}" + "".join(f"{h:>11}" for h in head))
    for y in sorted({r['date'][:4] for r in psb_during}):
        s = summary([r for r in psb_during if r["date"][:4] == y])
        print(f"  {y:<22}" + "".join(f"{str(x):>11}" for x in s))

    out = os.path.expanduser(a.out)
    with open(os.path.join(out, "breaks.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    over = sorted((r for r in psb_during if r["advertising_s"] > LIMIT), key=lambda r: -r["advertising_s"])
    with open(os.path.join(out, "psb_during_over_210.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(over)
    print(f"\n{len(over):,} PSB breaks during a programme exceed {LIMIT} s; the longest:")
    for r in over[:15]:
        print(f"  {r['advertising_s']:>4}s  {r['channel']:<10} {r['date']} {r['start']}  "
              f"{r['adverts']:>2} adverts  in '{r['programme_before']}'")
    print(f"\nwrote {out}/breaks.csv and {out}/psb_during_over_210.csv")


if __name__ == "__main__":
    main()
