#!/usr/bin/env python3
"""Which adverts sit next to which programmes? The programme around a break as a proxy
for the audience the advertiser bought, and groups of programmes with a similar
advertising mix as the industry's own picture of its audiences.
广告插在哪些节目旁边？把节目当作"广告商买到的观众"的代理；广告构成相似的节目
归为一组，就是行业心目中的几类观众。

    python3 programme_context.py --src $SRC --out ~/programme_context
    python3 programme_context.py --src $SRC --min-airings 60 --clusters 10 --out ~/pc_10

Reads the metadata JSON only. One airing = one broadcast advert (placeholders left
out, an airing listed in several recordings counted once). The programme of an
airing is the programme before its break.

For each programme with at least --min-airings airings:
    share(c)  fraction of its airings in category c (minor category)
    lift(c)   share(c) / share of c in the whole corpus; 3.0 = three times as common
              next to this programme as anywhere. Shown only where the programme has
              at least --min-count airings in c, so a single advert cannot make a lift.
Programmes are then grouped by the similarity of their category shares (cosine,
average-linkage clustering, standard library only).

Limits: a programme stands for the audience the advertiser intended, not the one
that watched; the corpus is 173 month-end days and 5STAR carries a third of the
airings, so check whether a group is only a channel (the main channel of each
programme is printed beside it).

Writes OUT/programmes.csv, OUT/lift.csv, OUT/clusters.csv.
"""
import argparse
import csv
import math
import os
from collections import Counter, defaultdict

from corpus_stats import load_records


def cosine(a: dict, b: dict) -> float:
    num = sum(v * b.get(k, 0.0) for k, v in a.items())
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    return num / (da * db) if da and db else 0.0


def cluster(names: list, vec: dict, k: int) -> list:
    """Average-linkage agglomerative clustering on cosine similarity.
    平均连接层次聚类：每次合并平均相似度最高的两组，直到剩 k 组。"""
    sim = {(a, b): cosine(vec[a], vec[b]) for i, a in enumerate(names) for b in names[i + 1:]}
    s = lambda a, b: sim.get((a, b), sim.get((b, a), 0.0))
    groups = [[n] for n in names]
    while len(groups) > k:
        best, pair = -1.0, None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                avg = sum(s(a, b) for a in groups[i] for b in groups[j]) / (len(groups[i]) * len(groups[j]))
                if avg > best:
                    best, pair = avg, (i, j)
        i, j = pair
        groups[i] += groups.pop(j)
    return groups


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="directory of <name>_metadata.json")
    ap.add_argument("--min-airings", type=int, default=100, help="programmes with at least this many airings")
    ap.add_argument("--min-count", type=int, default=5, help="airings needed in a category before a lift is shown")
    ap.add_argument("--clusters", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = os.path.expanduser(a.out)
    os.makedirs(out, exist_ok=True)

    _, recs, _, _ = load_records(None, a.src)
    ads = [r for r in recs if not r["kind"] and r.get("prog_before") and r.get("minor")]
    title = lambda r: r["prog_before"].strip().upper()

    # ---- counts / 计数 ----
    overall = Counter(r["minor"] for r in ads)
    n_all = len(ads)
    by_prog = defaultdict(Counter)
    chan_of = defaultdict(Counter)
    for r in ads:
        by_prog[title(r)][r["minor"]] += 1
        chan_of[title(r)][r["channel"]] += 1
    progs = sorted((p for p, c in by_prog.items() if sum(c.values()) >= a.min_airings),
                   key=lambda p: -sum(by_prog[p].values()))
    covered = sum(sum(by_prog[p].values()) for p in progs)
    print(f"{n_all:,} broadcast airings with a programme and a category; {len(by_prog):,} programmes")
    print(f"{len(progs)} programmes with at least {a.min_airings} airings, covering {covered:,} airings "
          f"({covered / n_all:.1%})\n")

    # ---- lift per programme / 每个节目的"偏好"品类 ----
    lift_rows, prog_rows = [], []
    for p in progs:
        n_p = sum(by_prog[p].values())
        lifts = []
        for c, n in by_prog[p].items():
            share = n / n_p
            lift = share / (overall[c] / n_all)
            lift_rows.append((p, c, n, round(share, 4), round(lift, 2)))
            if n >= a.min_count:
                lifts.append((lift, c, n, share))
        lifts.sort(reverse=True)
        main_ch = chan_of[p].most_common(1)[0][0]
        prog_rows.append((p, n_p, main_ch, "; ".join(f"{c} x{l:.1f}" for l, c, _, _ in lifts[:5])))
    print("PROGRAMMES AND THE CATEGORIES OVER-REPRESENTED NEXT TO THEM (lift = times the corpus share)")
    for p, n_p, ch, top in prog_rows[:40]:
        print(f"  {p[:34]:<34} {n_p:>5}  {ch:<10} {top}")
    if len(prog_rows) > 40:
        print(f"  ... {len(prog_rows) - 40} more in programmes.csv")

    # ---- groups of programmes / 节目分组 ----
    vec = {p: {c: n / sum(by_prog[p].values()) for c, n in by_prog[p].items()} for p in progs}
    groups = cluster(progs, vec, min(a.clusters, len(progs))) if len(progs) > 1 else [progs]
    groups.sort(key=lambda g: -sum(sum(by_prog[p].values()) for p in g))
    clus_rows = []
    print(f"\nGROUPS OF PROGRAMMES WITH A SIMILAR ADVERTISING MIX ({len(groups)} groups)")
    for gi, g in enumerate(groups, 1):
        agg = Counter()
        for p in g:
            agg.update(by_prog[p])
        n_g = sum(agg.values())
        lifts = sorted(((n / n_g) / (overall[c] / n_all), c, n) for c, n in agg.items() if n >= a.min_count * 2)
        lifts.reverse()
        chans = Counter()
        for p in g:
            chans.update(chan_of[p])
        ch_txt = ", ".join(f"{c} {n / sum(chans.values()):.0%}" for c, n in chans.most_common(3))
        print(f"\n  GROUP {gi}: {len(g)} programmes, {n_g:,} airings; channels: {ch_txt}")
        print("    programmes: " + "; ".join(sorted(g, key=lambda p: -sum(by_prog[p].values()))[:12])
              + (" ..." if len(g) > 12 else ""))
        print("    over-represented: " + "; ".join(f"{c} x{l:.1f}" for l, c, _ in lifts[:8]))
        for p in g:
            clus_rows.append((gi, p, sum(by_prog[p].values()), chan_of[p].most_common(1)[0][0]))

    with open(os.path.join(out, "programmes.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["programme", "airings", "main_channel", "top_lifts"])
        w.writerows(prog_rows)
    with open(os.path.join(out, "lift.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["programme", "minor_category", "airings", "share", "lift"])
        w.writerows(lift_rows)
    with open(os.path.join(out, "clusters.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "programme", "airings", "main_channel"])
        w.writerows(clus_rows)
    print(f"\nwrote {out}/programmes.csv, lift.csv, clusters.csv")


if __name__ == "__main__":
    main()
