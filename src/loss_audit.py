#!/usr/bin/env python3
"""
loss_audit.py - where do adverts go missing between the schedule and the ledger?

Read-only. It reads the delivered *_metadata.json files, the per-recording ledgers
(<run>/<recording>/cut_plan.csv) and the per-recording logs (<run>/_logs/*.log)
written by batch_cut.py. It decodes no video and changes nothing.

Three subcommands, each writing CSV tables plus a plain-text summary to --out:

  recon    schedule -> ledger reconciliation for one run
           (how many slots, breaks and unique adverts survive; why breaks were
           dropped; whether the loss is even across channel, year, slot length and
           position; whether ledger labels agree with the collapsed schedule)

  misses   what a fill-missing run would add
           (per-break hit rate, yield at several acceptance rules, and a seeded
           sample of schedule-filled cut points for frame-by-frame checking)

  schedule what is in each schedule file before any alignment: channel in the
           name against channel in the metadata, runs of break index, breaks split
           or joined by the First marker (reads metadata only)

  offsets  do the breaks of a recording agree on when the recording started?
           (finds breaks dropped by the consistency filter although their offset
           matches the rest of the recording, and kept breaks that do not)

  fillcheck how good a schedule-filled boundary would be: leave-one-out error of
           the boundaries that did find a silence, and what each acceptance rule
           would add in recordings with a confirmed start

  precision slot deviation and audio offset of a refined run by group (new against
           already known, confirmed against not, short breaks against long), and a
           seeded sample of cut points to check frame by frame

  anchor   long silences
           (audio-picture offset by silence length, clips over 120 s, and a
           simulation of anchoring long silences on the silence end)

Usage:
    python3 loss_audit.py recon  --src SRC --run RUN_STRICT  --out OUT/recon
    python3 loss_audit.py misses --src SRC --run RUN_LENIENT --out OUT/misses
    python3 loss_audit.py anchor --run RUN_STRICT --out OUT/anchor
    python3 loss_audit.py selftest

Standard library only.
"""
import argparse
import csv
import json
import random
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from silence_detect import clean_breaks, own_run, name_time_of, id_duration   # noqa: E402  (as the cutter reads it)

SLOTS = [10, 15, 20, 30, 40, 45, 60, 90]
PLACEHOLDER = "SKYADSMART"
GAP_S = 120          # no advert is this long; a longer gap inside a break is two breaks
HIST_KEYS = ["1", "2", "3", "4+", "0 matched", "0 (too few boundaries)"]
DROP_MATCH = re.compile(r"\[drop\] break (\d+) \(.*?\): only (\d+)/(\d+) boundaries")
DROP_OFFSET = re.compile(r"\[drop\] break (\d+) \(.*?\): offset ")
DROP_OFFSET_T = re.compile(r"\[drop\] break (\d+) \(.*?\): offset (-?[\d.]+)s inconsistent")
SKIP_END = re.compile(r"\[skip\] break (\d+) \(.*?\) advert (\d+): no end silence")


def hms_to_s(t: str) -> float:
    p = t.strip().split(":")
    return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])


def is_ph(ad: dict) -> bool:
    return str(ad.get("unique_id", "")).startswith(PLACEHOLDER)


def collapse(br: dict) -> list:
    """The adverts that survive silence_detect.load_breaks(), in boundary order.

    Mirrors that function exactly: records sharing one start_time become a single
    boundary, and a real advert replaces a placeholder kept at the same instant.
    Returns [(rel_seconds, advert_dict), ...]. Position i+1 in the ledger refers to
    element i of this list, NOT to element i of the raw adverts list.
    """
    b0 = hms_to_s(br["start_time"])
    kept, seen = [], {}
    for ad in br.get("adverts", []):
        rel = hms_to_s(ad["start_time"]) - b0
        if rel in seen:
            if not (is_ph(seen[rel]) and not is_ph(ad)):
                continue
            kept = [k for k in kept if k[0] != rel]
        seen[rel] = ad
        kept.append((rel, ad))
    return kept


def parse_stem(stem: str):
    """'2020-08-29_ITV1 HD_09:39:00' -> ('2020', 'ITV1 HD'). Unknown parts become '?'."""
    parts = stem.split("_")
    year = parts[0][:4] if re.match(r"\d{4}-\d{2}-\d{2}", parts[0]) else "?"
    channel = parts[1] if len(parts) > 1 else "?"
    return year, channel


def slot_of(seconds):
    if seconds is None:
        return "last (unknown)"
    near = min(SLOTS, key=lambda s: abs(s - seconds))
    return f"{near}s" if abs(near - seconds) <= 1.0 else "non-standard"


def deviation(duration: float) -> float:
    return min(abs(duration - s) for s in SLOTS)


def load_schedule(src: Path) -> dict:
    """{stem: {break_index: [(rel, advert), ...]}} plus raw counts."""
    sched, raw = {}, Counter()
    for meta in sorted(src.rglob("*_metadata.json")):
        stem = meta.name[: -len("_metadata.json")]
        data = json.load(open(meta, encoding="utf-8"))
        breaks = {}
        ad_breaks = data.get("ad_breaks", [])
        if OWN:
            run = own_run(ad_breaks, name_time_of(str(meta)))
            if run is not None:
                raw["breaks of another channel left out"] += len(ad_breaks) - len(run)
                ad_breaks = run
        if CLEAN:
            ad_breaks = clean_breaks(ad_breaks)
        for br in ad_breaks:
            ads = br.get("adverts", [])
            raw["records"] += len(ads)
            raw["placeholders"] += sum(is_ph(a) for a in ads)
            raw["has_end_time"] += bool(br.get("end_time"))
            for a in ads:
                raw_ids[str(a.get("unique_id", ""))] += 1
            breaks[int(br.get("index"))] = collapse(br)
        sched[stem] = breaks
    return sched, raw


raw_ids = Counter()     # every unique_id in the delivered schedule, before collapsing
CLEAN = False           # set by --clean-schedule: read the schedule as cut_adverts.py did
OWN = False             # set by --own-channel


def load_run(run: Path):
    """Ledger rows by (stem, break, pos); drop reasons by (stem, break); stems seen."""
    rows, fate, stems = {}, {}, set()
    for led in sorted(run.glob("*/cut_plan.csv")):
        stem = led.parent.name
        stems.add(stem)
        with open(led, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows[(stem, int(r["break"]), int(r["pos"]))] = r
    for log in sorted((run / "_logs").glob("*.log")) if (run / "_logs").is_dir() else []:
        stem = log.name[:-4]
        stems.add(stem)
        text = log.read_text(encoding="utf-8", errors="replace")
        for m in DROP_MATCH.finditer(text):
            fate[(stem, int(m.group(1)))] = ("dropped: boundaries unmatched",
                                             int(m.group(2)), int(m.group(3)))
        for m in DROP_OFFSET.finditer(text):
            fate[(stem, int(m.group(1)))] = ("dropped: offset inconsistent", None, None)
    return rows, fate, stems


def probe_duration(mp4: Path):
    """Length of a recording in seconds, from its container header (no decoding)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(mp4)], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def write(path: Path, header: list, rows: list):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def slots_of_schedule(sched):
    """Yield one dict per collapsed schedule slot."""
    for stem, breaks in sched.items():
        year, channel = parse_stem(stem)
        for idx, kept in breaks.items():
            n = len(kept)
            for i, (rel, ad) in enumerate(kept):
                length = kept[i + 1][0] - rel if i + 1 < n else None
                yield {"stem": stem, "break": idx, "pos": i + 1, "n_in_break": n,
                       "uid": str(ad.get("unique_id", "")), "brand": ad.get("brand", ""),
                       "ph": is_ph(ad), "year": year, "channel": channel,
                       "slot": slot_of(length),
                       "position": "first" if i == 0 else ("last" if i == n - 1 else "middle"),
                       "break_size": "1-4" if n <= 4 else ("5-8" if n <= 8 else "9+")}


# ---------------------------------------------------------------------------
def cmd_recon(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sched, raw = load_schedule(Path(a.src))
    rows, fate, stems = load_run(Path(a.run))
    if a.only_run:
        # a sample run (batch_cut.py --stride / --limit): count only its recordings,
        # so yields are not diluted by recordings the run never touched
        sched = {k: v for k, v in sched.items() if k in stems}
        raw = Counter()
    if a.exclude:
        # leave named recordings out altogether (e.g. one stopped by hand, whose empty
        # ledger would otherwise count as a recording with nothing cut)
        drop = {k for k in set(sched) | stems if any(x in k for x in a.exclude)}
        sched = {k: v for k, v in sched.items() if k not in drop}
        rows = {k: v for k, v in rows.items() if k[0] not in drop}
        fate = {k: v for k, v in fate.items() if k[0] not in drop}
        stems = stems - drop
        raw = Counter()
        print(f'recordings excluded by name: {len(drop):,}')
    if a.common_with:
        # compare two runs on exactly the recordings both have finished
        _, _, other = load_run(Path(a.common_with))
        both = stems & other
        sched = {k: v for k, v in sched.items() if k in both}
        rows = {k: v for k, v in rows.items() if k[0] in both}
        fate = {k: v for k, v in fate.items() if k[0] in both}
        stems = both
        raw = Counter()
        print(f"recordings finished in both runs: {len(both):,}")
        print()
    slots = list(slots_of_schedule(sched))
    real = [s for s in slots if not s["ph"]]
    for s in slots:
        s["kept"] = (s["stem"], s["break"], s["pos"]) in rows

    uid_sched = {s["uid"] for s in real if s["uid"]}
    uid_kept = {s["uid"] for s in real if s["kept"] and s["uid"]}
    uid_raw_real = {u for u in raw_ids if u and not u.startswith(PLACEHOLDER)}
    n_breaks = sum(len(b) for b in sched.values())
    kept_breaks = {(s["stem"], s["break"]) for s in slots if s["kept"]}

    # --- label check: does the ledger name the advert the collapsed schedule puts there?
    mismatch = []
    for s in slots:
        r = rows.get((s["stem"], s["break"], s["pos"]))
        if r is not None and str(r.get("unique_id", "")) != s["uid"]:
            mismatch.append([s["stem"], s["break"], s["pos"], s["channel"],
                             r.get("unique_id", ""), r.get("brand", ""), s["uid"], s["brand"]])
    write(out / "label_mismatch.csv",
          ["recording", "break", "pos", "channel", "ledger_unique_id", "ledger_brand",
           "schedule_unique_id", "schedule_brand"], mismatch)

    # --- break fate
    fate_rows, fate_count, missing_hist = [], Counter(), Counter()
    fate_slots = Counter()
    status_of = {}
    ads_lost_by_missing = Counter()
    for stem, breaks in sched.items():
        for idx, kept in breaks.items():
            if (stem, idx) in kept_breaks:
                status, n, nb = "kept", "", ""
            elif (stem, idx) in fate:
                status, n, nb = fate[(stem, idx)]
                if n is not None:
                    miss = nb - n
                    # miss == 0 means every boundary matched and the break was still
                    # dropped: it has fewer boundaries than --min-matches, too short a
                    # pattern to align on.
                    key = ("0 matched" if n == 0 else "0 (too few boundaries)" if miss == 0
                           else (str(miss) if miss <= 3 else "4+"))
                    missing_hist[key] += 1
                    ads_lost_by_missing[key] += len(kept)
            elif stem not in stems:
                status, n, nb = "recording not in run", "", ""
            else:
                status, n, nb = "no ledger row and no log line", "", ""
            fate_count[status] += 1
            fate_slots[status] += len(kept)
            status_of[(stem, idx)] = status
            fate_rows.append([stem, idx, len(kept), status,
                              "" if n is None else n, "" if nb is None else nb])
    write(out / "break_fate.csv",
          ["recording", "break", "slots", "status", "matched", "boundaries"], fate_rows)
    write(out / "missing_boundaries.csv", ["boundaries_missing", "breaks", "slots_lost"],
          [[k, missing_hist[k], ads_lost_by_missing[k]]
           for k in HIST_KEYS if k in missing_hist])

    # --- breaks whose schedule holds a long gap between consecutive adverts.
    # No advert runs for minutes, so a gap over GAP_S means the metadata has joined
    # two separate breaks into one. The last advert before the gap then gets a clip
    # that runs across the programme (the clips over 120 s), and if either half lies
    # outside the recording the whole joined break fails the match and is dropped.
    gap_rows, gap_count, gap_slots = [], Counter(), Counter()
    for stem, breaks in sched.items():
        for idx, kept in breaks.items():
            rels = [r for r, _ in kept]
            gaps = [y - x for x, y in zip(rels, rels[1:])]
            if not gaps or (max(gaps) <= GAP_S and min(gaps) >= 0):
                continue
            kind = "gap over %d s" % GAP_S if max(gaps) > GAP_S else "start times out of order"
            st = status_of.get((stem, idx), "?")
            gap_count[(kind, st)] += 1
            gap_slots[(kind, st)] += len(kept)
            gap_rows.append([stem, idx, len(kept), kind, f"{max(gaps):.0f}", f"{min(gaps):.0f}",
                             1 + sum(g > GAP_S for g in gaps), st])
    write(out / "gap_breaks.csv",
          ["recording", "break", "slots", "kind", "max_gap_s", "min_gap_s", "parts", "status"],
          gap_rows)

    # --- loss by stratum (real adverts only)
    strata = []
    for dim in ["channel", "year", "slot", "position", "break_size"]:
        tot, kep, ut, uk = Counter(), Counter(), defaultdict(set), defaultdict(set)
        for s in real:
            tot[s[dim]] += 1
            kep[s[dim]] += s["kept"]
            ut[s[dim]].add(s["uid"])
            if s["kept"]:
                uk[s[dim]].add(s["uid"])
        for v in sorted(tot, key=lambda x: -tot[x]):
            strata.append([dim, v, tot[v], kep[v], pct(kep[v], tot[v]),
                           len(ut[v]), len(uk[v]), pct(len(uk[v]), len(ut[v]))])
    write(out / "loss_by_stratum.csv",
          ["dimension", "value", "slots", "slots_kept", "slot_yield",
           "unique_in_schedule", "unique_kept", "unique_coverage"], strata)

    # --- unique adverts never recovered
    airings, where = Counter(), defaultdict(set)
    for s in real:
        airings[s["uid"]] += 1
        where[s["uid"]].add(f"{s['channel']} {s['year']}")
    brand = {s["uid"]: s["brand"] for s in real}
    lost = sorted(uid_sched - uid_kept, key=lambda u: -airings[u])
    write(out / "lost_unique.csv", ["unique_id", "brand", "slots_in_delivery", "channel_year"],
          [[u, brand.get(u, ""), airings[u], "; ".join(sorted(where[u]))] for u in lost])
    once = sum(1 for u in uid_sched if airings[u] == 1)
    lost_once = sum(1 for u in lost if airings[u] == 1)

    rec_with_rows = {k[0] for k in rows}
    lines = [
        f"source   : {a.src}",
        f"run      : {a.run}",
        "",
        "SCHEDULE",
        f"  recordings with metadata        {len(sched):>8,}",
        f"  advert breaks                   {n_breaks:>8,}",
        f"  schedule records                {raw['records']:>8,}",
        f"    of which placeholders         {raw['placeholders']:>8,}  ({pct(raw['placeholders'], raw['records'])})",
        f"  slots after collapsing          {len(slots):>8,}",
        f"    of which real adverts         {len(real):>8,}",
        f"  unique adverts, raw ids         {len(uid_raw_real):>8,}  (placeholders excluded)",
        f"  unique adverts after collapsing {len(uid_sched):>8,}",
        f"    appearing once in delivery    {once:>8,}",
        f"  breaks with an end_time         {raw['has_end_time']:>8,}  (adds a 'break end' boundary)",
        "",
        "LEDGER",
        f"  ledger rows (cut points)        {len(rows):>8,}",
        f"  recordings with at least one    {len(rec_with_rows):>8,}  of {len(sched):,}",
        f"  breaks kept                     {len(kept_breaks):>8,}  ({pct(len(kept_breaks), n_breaks)})",
        f"  real-advert slots kept          {sum(s['kept'] for s in real):>8,}  ({pct(sum(s['kept'] for s in real), len(real))})",
        f"  unique adverts kept             {len(uid_kept):>8,}  ({pct(len(uid_kept), len(uid_sched))} coverage)",
        f"  unique adverts never recovered  {len(lost):>8,}",
        f"    of which appeared only once   {lost_once:>8,}",
        "",
        "BREAK FATE",
    ] + [f"  {k:<34}{v:>8,} breaks {fate_slots[k]:>8,} slots" for k, v in fate_count.most_common()] + [
        "",
        "BREAKS DROPPED FOR UNMATCHED BOUNDARIES, by number missing",
    ] + [f"  missing {k:<23} breaks {missing_hist[k]:>7,}   slots lost {ads_lost_by_missing[k]:>8,}"
         for k in HIST_KEYS if k in missing_hist] + [
        "",
        "BREAKS WITH A SCHEDULE GAP (two breaks joined in the metadata, or records out of order)",
    ] + [f"  {k[0]:<26}{k[1]:<34}{v:>6,} breaks {gap_slots[k]:>7,} slots"
         for k, v in sorted(gap_count.items(), key=lambda kv: -kv[1])] + [
        "",
        "LABEL CHECK (ledger unique_id vs collapsed schedule at the same position)",
        f"  ledger rows checked             {sum(1 for s in slots if s['kept']):>8,}",
        f"  mismatches                      {len(mismatch):>8,}",
    ] + [f"    {c:<30}{n:>8,}" for c, n in Counter(m[3] for m in mismatch).most_common()] + [
        "",
        "tables: break_fate.csv  missing_boundaries.csv  loss_by_stratum.csv",
        "        lost_unique.csv  label_mismatch.csv  gap_breaks.csv",
    ]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
def cmd_misses(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sched, _ = load_schedule(Path(a.src))
    rows, _, _ = load_run(Path(a.run))
    info = {(s["stem"], s["break"], s["pos"]): s for s in slots_of_schedule(sched)}
    by_break = defaultdict(list)
    for key, r in rows.items():
        by_break[key[:2]].append((key, r))
    total_real = sum(1 for s in info.values() if not s["ph"])
    total_uid = {s["uid"] for s in info.values() if not s["ph"] and s["uid"]}

    stats = {}
    for bk, items in by_break.items():
        n = len(items)
        filled = sum(1 for _, r in items if r.get("start_source") == "schedule")
        stats[bk] = (n, filled)

    rules = [("all matched (strict)", lambda n, f: f == 0),
             ("at most 1 filled", lambda n, f: f <= 1),
             ("at most 2 filled", lambda n, f: f <= 2),
             ("hit rate >= 0.8", lambda n, f: (n - f) / n >= 0.8),
             ("hit rate >= 0.66", lambda n, f: (n - f) / n >= 0.66),
             ("hit rate >= 0.5 (as run)", lambda n, f: True)]
    table = []
    for name, ok in rules:
        slots = filled_n = 0
        uids = set()
        for bk, (n, f) in stats.items():
            if not ok(n, f):
                continue
            for key, r in by_break[bk]:
                s = info.get(key)
                if s is None or s["ph"]:
                    continue
                slots += 1
                filled_n += r.get("start_source") == "schedule"
                uids.add(s["uid"])
        table.append([name, slots, pct(slots, total_real), filled_n, pct(filled_n, slots),
                      len(uids), pct(len(uids), len(total_uid))])
    write(out / "yield_by_rule.csv",
          ["acceptance_rule", "real_slots_kept", "slot_yield", "of_which_filled",
           "filled_share", "unique_kept", "unique_coverage"], table)

    # what Stage B did at filled boundaries (weak evidence only: see note in summary)
    st = Counter(r.get("refine_status", "") for r in rows.values()
                 if r.get("start_source") == "schedule")

    # seeded sample of filled cut points for frame-by-frame checking
    groups = defaultdict(list)
    for key, r in rows.items():
        if r.get("start_source") != "schedule":
            continue
        f = stats[key[:2]][1]
        groups["1 filled in break" if f == 1 else ("2 filled" if f == 2 else "3+ filled")].append((key, r))
    rng = random.Random(a.seed)
    sample = []
    for g in sorted(groups):
        pick = rng.sample(sorted(groups[g], key=lambda x: x[0]), min(a.per_group, len(groups[g])))
        for key, r in pick:
            s = info.get(key, {})
            sample.append([g, key[0], key[1], key[2], r.get("start_s", ""), r.get("refine_status", ""),
                           r.get("shift_ms", ""), s.get("uid", ""), s.get("brand", ""),
                           "", "", ""])
    write(out / "manual_sample_filled.csv",
          ["group", "recording", "break", "pos", "start_s", "refine_status", "shift_ms",
           "schedule_unique_id", "schedule_brand", "truth_s", "brand_ok", "note"], sample)

    lines = [f"run: {a.run}", "", "YIELD BY ACCEPTANCE RULE (real adverts only)"]
    lines += [f"  {t[0]:<26} slots {t[1]:>7,} ({t[2]:>6})  filled {t[3]:>6,} ({t[4]:>6})  "
              f"unique {t[5]:>6,} ({t[6]:>6})" for t in table]
    lines += ["", "STAGE B STATUS AT SCHEDULE-FILLED BOUNDARIES"]
    lines += [f"  {k or '(empty)':<26}{v:>8,}" for k, v in st.most_common()]
    lines += ["",
              "A filled boundary sits at schedule offset + T, so its clip length agrees with the",
              "slot grid by construction. Slot deviation therefore cannot validate it. Use",
              "manual_sample_filled.csv with frame_strip.py and record truth_s and brand_ok.",
              f"sample: {len(sample)} cut points, seed {a.seed}"]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
def cmd_anchor(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, _, _ = load_run(Path(a.run))
    data, outliers = [], []
    for (stem, br, pos), r in rows.items():
        try:
            dur = float(r["duration_s"])
        except (KeyError, ValueError):
            continue
        if dur > 120:
            sil = ""
            if r.get("silence_start_s") and r.get("silence_end_s"):
                sil = f"{float(r['silence_end_s']) - float(r['silence_start_s']):.2f}"
            outliers.append([stem, br, pos, f"{dur:.1f}", sil, r.get("break_last", ""),
                             r.get("refine_status", ""), r.get("brand", "")])
        if not (r.get("silence_start_s") and r.get("silence_end_s")):
            continue
        ss, se = float(r["silence_start_s"]), float(r["silence_end_s"])
        data.append({"len": se - ss, "se": se, "mid": float(r["start_silence_mid_s"]),
                     "start": float(r["start_s"]), "end": float(r["end_s"]),
                     "status": r.get("refine_status", ""), "first": pos == 1})
    write(out / "clips_over_120s.csv",
          ["recording", "break", "pos", "duration_s", "silence_len_s", "break_last",
           "refine_status", "brand"], sorted(outliers, key=lambda x: -float(x[3])))

    bins = [(0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 1e9)]
    med = lambda xs: statistics.median(xs) if xs else float("nan")
    table = []
    for lo, hi in bins:
        d = [x for x in data if lo <= x["len"] < hi]
        if not d:
            continue
        ok = [x for x in d if x["end"] - x["start"] <= 120]
        table.append([f"{lo}-{hi if hi < 1e9 else 'inf'}", len(d),
                      pct(sum(x["first"] for x in d), len(d)),
                      f"{1000 * med([x['start'] - x['se'] for x in d]):.0f}",
                      f"{1000 * med([x['start'] - x['se'] for x in d if x['status'] == 'refined']):.0f}",
                      f"{1000 * med([x['mid'] - x['se'] for x in d]):.0f}",
                      f"{med([deviation(x['end'] - x['start']) for x in ok]):.3f}",
                      f"{med([deviation(x['end'] - x['mid']) for x in ok]):.3f}"])
    write(out / "offset_by_silence_length.csv",
          ["silence_len_s", "n", "break_first_share", "median_start_minus_silence_end_ms",
           "same_refined_only_ms", "median_midpoint_minus_silence_end_ms",
           "median_slot_dev_actual_s", "median_slot_dev_midpoint_s"], table)

    # simulate: for silences longer than L, start = silence_end - back (end unchanged)
    sim = []
    for L in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]:     # 0.0 = every silence, i.e. one rule for all
        d = [x for x in data if x["len"] > L and x["end"] - x["start"] <= 120]
        if not d:
            continue
        base_mid = med([deviation(x["end"] - x["mid"]) for x in d])
        base_act = med([deviation(x["end"] - x["start"]) for x in d])
        for back in a.back:
            alt = [deviation(x["end"] - (x["se"] - back)) for x in d]
            sim.append([L, len(d), back, f"{base_mid:.3f}", f"{base_act:.3f}", f"{med(alt):.3f}",
                        pct(sum(v < 0.2 for v in alt), len(alt))])
    write(out / "anchor_simulation.csv",
          ["silence_longer_than_s", "n", "back_s", "median_dev_midpoint", "median_dev_actual",
           "median_dev_anchor", "anchor_within_0.2s"], sim)

    lines = [f"run: {a.run}", f"cut points with a matched silence: {len(data):,}",
             f"clips over 120 s: {len(outliers):,}", "",
             "OFFSET AND SLOT DEVIATION BY SILENCE LENGTH",
             "  len(s)      n   first  start-end(ms)  refined(ms)  mid-end(ms)  dev actual  dev midpoint"]
    lines += [f"  {t[0]:<9}{t[1]:>6,}  {t[2]:>6}  {t[3]:>12}  {t[4]:>11}  {t[5]:>11}  {t[6]:>10}  {t[7]:>12}"
              for t in table]
    lines += ["", "ANCHOR SIMULATION (start = silence end - back; clip end unchanged)",
              "  L(s)      n   back   dev midpoint  dev actual  dev anchor  anchor <0.2s"]
    lines += [f"  {s[0]:<5}{s[1]:>6,}  {s[2]:>5}  {s[3]:>12}  {s[4]:>10}  {s[5]:>10}  {s[6]:>12}" for s in sim]
    lines += ["", "Slot deviation is a valid differential test here only where the change moves a",
              "START and leaves the END alone (long silences are mostly break-first). It still",
              "measures agreement with scheduled durations, not position: confirm by eye."]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
def cmd_offsets(a):
    """Do the breaks of one recording agree on when the recording started?

    A recording is one continuous stretch of broadcast, so every break in it implies
    the same thing: recording start = (position of the break in the file) - (its
    wall-clock time). cut_adverts.py fits each break on its own and then accepts a
    break only if it agrees with the PREVIOUS KEPT one. If that earlier break was a
    chance fit, every correct break after it is thrown out. Here the offsets of all
    breaks that were fitted are put side by side, kept or not, and the value most of
    them agree on is taken as the recording start. Nothing is re-aligned and no
    video is read: fitted offsets come from the ledger (kept breaks) and from the
    [drop] lines in the logs (breaks dropped for an inconsistent offset).
    """
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src, run = Path(a.src), Path(a.run)
    sched, _ = load_schedule(src)
    rows, _, _ = load_run(run)
    wall = {}
    for meta in sorted(src.rglob("*_metadata.json")):
        stem = meta.name[: -len("_metadata.json")]
        for br in json.load(open(meta, encoding="utf-8")).get("ad_breaks", []):
            wall[(stem, int(br.get("index")))] = hms_to_s(br["start_time"])

    fitted = defaultdict(dict)                     # stem -> {break: (T, 'kept'|'dropped')}
    by_break = defaultdict(list)
    for (stem, idx, pos), r in rows.items():
        if r.get("start_source", "silence") != "schedule" and r.get("start_silence_mid_s"):
            rel = sched[stem][idx][pos - 1][0]
            by_break[(stem, idx)].append(float(r["start_silence_mid_s"]) - rel)
    for (stem, idx), ts in by_break.items():
        fitted[stem][idx] = (statistics.median(ts), "kept")
    for log in sorted((run / "_logs").glob("*.log")):
        stem = log.name[:-4]
        for m in DROP_OFFSET_T.finditer(log.read_text(encoding="utf-8", errors="replace")):
            fitted[stem].setdefault(int(m.group(1)), (float(m.group(2)), "dropped"))

    tol = a.tol
    none = "recording has no two breaks that agree"
    tally, slots, detail = Counter(), Counter(), []
    poisoned = undetermined = 0
    start_of = {}                 # stem -> (recording start on the wall clock, basis)
    for stem, brs in fitted.items():
        brs = {i: v for i, v in brs.items() if (stem, i) in wall and i in sched.get(stem, {})}
        g = {i: T - wall[(stem, i)] for i, (T, _) in brs.items()}
        vals = sorted(g.values())
        cluster = []
        if vals:
            best = max(vals, key=lambda v: sum(1 for x in vals if v <= x <= v + tol))
            cluster = [x for x in vals if best <= x <= best + tol]
        if len(cluster) < 2:
            undetermined += 1
            for i, (T, st) in brs.items():
                tally[(st, none)] += 1
                slots[(st, none)] += len(sched[stem][i])
            kept_one = [i for i, (_, st) in brs.items() if st == "kept"]
            if len(kept_one) == 1:          # the only evidence there is; unconfirmed
                start_of[stem] = (g[kept_one[0]], "one kept break, unconfirmed")
            continue
        centre = statistics.median(cluster)
        start_of[stem] = (centre, "two or more breaks agree")
        kept_ids = [i for i, (_, st) in brs.items() if st == "kept"]
        if kept_ids:
            first_kept = min(kept_ids, key=lambda i: wall[(stem, i)])
            poisoned += abs(g[first_kept] - centre) > tol
        for i, (T, st) in brs.items():
            verdict = "agrees with the recording" if abs(g[i] - centre) <= tol else "disagrees"
            tally[(st, verdict)] += 1
            slots[(st, verdict)] += len(sched[stem][i])
            detail.append([stem, i, len(sched[stem][i]), st, f"{T:.1f}",
                           f"{g[i] - centre:+.1f}", verdict, len(cluster), len(brs)])
    write(out / "break_offsets.csv",
          ["recording", "break", "slots", "status", "fitted_T_s", "offset_from_recording_s",
           "verdict", "breaks_in_agreement", "breaks_fitted"], detail)
    lines = [f"run: {a.run}", f"agreement window: {tol:g} s", "",
             "FITTED BREAKS, by what the pipeline did and whether the break agrees with",
             "the start time most breaks of its recording imply"]
    lines += [f"  {k[0]:<9}{k[1]:<42}{v:>6,} breaks {slots[k]:>7,} slots"
              for k, v in sorted(tally.items(), key=lambda kv: (kv[0][0], -kv[1]))]
    lines += ["",
              f"recordings whose FIRST kept break disagrees with the rest: {poisoned:,}",
              f"recordings where no two fitted breaks agree:              {undetermined:,}",
              "",
              "'dropped' + 'agrees' are breaks the consistency filter threw out although their",
              "offset matches the recording: recoverable without filling anything.",
              "'kept' + 'disagrees' are breaks in the ledger whose offset is out of step: suspect."]
    # --- does the file name already say when the recording started?
    # Names look like 2021-10-29_Channel 5_09:42:55. If that time is the recording
    # start, it can be compared with the start the fitted breaks imply (wall-clock
    # start = -centre). Agreement would mean the start is known before any fitting.
    diffs, sample_rows = [], []
    for stem, (start, basis) in start_of.items():
        if basis != "two or more breaks agree":
            continue
        m = re.search(r"_(\d{2}:\d{2}:\d{2})(?:_|$)", stem)
        if not m:
            continue
        d = -start - hms_to_s(m.group(1))
        diffs.append(d)
        sample_rows.append([stem, m.group(1), f"{-start:.1f}", f"{d:+.1f}"])
    write(out / "name_vs_fitted_start.csv",
          ["recording", "time_in_file_name", "fitted_start_wall_clock_s", "fitted_minus_name_s"],
          sample_rows)
    if diffs:
        ds = sorted(diffs)
        q = lambda f: ds[min(len(ds) - 1, int(f * len(ds)))]
        lines += ["", "TIME IN THE FILE NAME vs RECORDING START IMPLIED BY THE FITTED BREAKS",
                  f"  recordings compared (start confirmed by two or more breaks): {len(ds):,}",
                  f"  fitted start minus name time, seconds: p10 {q(0.1):+.1f}   median {q(0.5):+.1f}   p90 {q(0.9):+.1f}",
                  f"  within   5 s: {pct(sum(abs(x) <= 5 for x in ds), len(ds))}",
                  f"  within  60 s: {pct(sum(abs(x) <= 60 for x in ds), len(ds))}",
                  f"  within 600 s: {pct(sum(abs(x) <= 600 for x in ds), len(ds))}"]
    first_meta = next(iter(sorted(src.rglob("*_metadata.json"))), None)
    if first_meta is not None:
        d0 = json.load(open(first_meta, encoding="utf-8"))
        lines += ["", f"FIELDS BESIDE ad_breaks IN {first_meta.name}"]
        lines += [f"  {k}: {json.dumps(v, ensure_ascii=False)[:300]}" for k, v in d0.items() if k != "ad_breaks"]
        if d0.get("ad_breaks"):
            b0 = d0["ad_breaks"][0]
            lines += ["  first break, fields beside adverts: "
                      + json.dumps({k: v for k, v in b0.items() if k != "adverts"}, ensure_ascii=False)[:300]]
            if b0.get("adverts"):
                lines += ["  first advert: " + json.dumps(b0["adverts"][0], ensure_ascii=False)[:400]]

    # --- is each scheduled break inside its recording at all?
    # With the recording start known, every break in the schedule has a predicted
    # position in the file, whether or not it was ever fitted. A break predicted
    # before second 0 or after the end of the file was never recorded there, and no
    # alignment rule can recover it from this file.
    if a.probe:
        _, fate, _ = load_run(run)
        mp4s = {m.name[:-4]: m for m in src.rglob("*.mp4")}
        where, where_slots, inside_rows = Counter(), Counter(), []
        no_start = no_dur = 0
        for stem, breaks in sched.items():
            if stem not in start_of:
                no_start += 1
                continue
            dur = probe_duration(mp4s[stem]) if stem in mp4s else None
            if dur is None:
                no_dur += 1
                continue
            start, basis = start_of[stem]
            for idx, kept in breaks.items():
                if (stem, idx) not in wall:
                    continue
                pos = wall[(stem, idx)] + start
                length = kept[-1][0] if kept else 0.0
                if pos < 0 or pos > dur:
                    place = "outside the file"
                elif pos + length > dur:
                    place = "starts inside, runs past the end"
                else:
                    place = "inside the file"
                if any((stem, idx, q + 1) in rows for q in range(len(kept))):
                    st = "kept"
                elif (stem, idx) in fate:
                    st = fate[(stem, idx)][0]
                    if fate[(stem, idx)][1] is not None and fate[(stem, idx)][1] == fate[(stem, idx)][2]:
                        st = "dropped: too few boundaries"
                else:
                    st = "no ledger row and no log line"
                where[(basis, place, st)] += 1
                where_slots[(basis, place, st)] += len(kept)
                inside_rows.append([stem, idx, len(kept), st, f"{pos:.0f}", f"{dur:.0f}", place, basis])
        write(out / "break_positions.csv",
              ["recording", "break", "slots", "status", "predicted_position_s", "recording_length_s",
               "place", "basis_for_recording_start"], inside_rows)
        lines += ["", "WHERE EACH SCHEDULED BREAK FALLS, given the recording start"]
        for basis in ["two or more breaks agree", "one kept break, unconfirmed"]:
            lines += [f"  recording start from: {basis}"]
            lines += [f"    {k[1]:<34}{k[2]:<34}{v:>6,} breaks {where_slots[k]:>7,} slots"
                      for k, v in sorted(where.items(), key=lambda kv: (kv[0][1], -kv[1]))
                      if k[0] == basis]
        lines += [f"  recordings with no usable start: {no_start:,}   length unreadable: {no_dur:,}"]

    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
def cmd_schedule(a):
    """What is in each schedule file, before any alignment? Reads metadata only.

    Three things found by hand on 11-12 Oct 2026 are counted here across all files:
      * the channel in the file name against video_info.channel, and whether the
        break indices fall into more than one unbroken run (2025-11-30_5_19:42:44
        holds breaks 68-91 of one channel and 156-172 of another);
      * breaks that start at the same instant as the next break: a lone record marked
        First, followed by the rest of the break, also marked First. The lone record
        is either an addressable placeholder or the real first advert of the break;
      * breaks holding a gap of more than GAP_S between consecutive adverts.
    """
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cross, rows = Counter(), []
    tot = Counter()
    for meta in sorted(Path(a.src).rglob("*_metadata.json")):
        stem = meta.name[: -len("_metadata.json")]
        d = json.load(open(meta, encoding="utf-8"))
        name_ch = parse_stem(stem)[1]
        meta_ch = str((d.get("video_info") or {}).get("channel", "?"))
        cross[(name_ch, meta_ch)] += 1
        m = re.search(r"_(\d{2}:\d{2}:\d{2})(?:_|$)", stem)
        name_t = m.group(1) if m else None
        brs = sorted(d.get("ad_breaks", []), key=lambda b: int(b.get("index", 0)))
        tot["files"] += 1
        tot["breaks"] += len(brs)

        # unbroken runs of break index
        runs, cur = [], []
        for b in brs:
            if cur and int(b["index"]) != int(cur[-1]["index"]) + 1:
                runs.append(cur)
                cur = []
            cur.append(b)
        if cur:
            runs.append(cur)
        named = [i for i, r in enumerate(runs) if any(b.get("start_time") == name_t for b in r)]
        n_real = lambda r: sum(1 for b in r for ad in b.get("adverts", []) if not is_ph(ad))
        own = n_real(runs[named[0]]) if named else None
        other = sum(n_real(r) for i, r in enumerate(runs) if not named or i != named[0])
        if len(runs) > 1:
            tot["files with more than one index run"] += 1
            if named:
                tot["  of which the file-name break is found in one run"] += 1
                tot["  real records in the OTHER runs of those files"] += other
        if name_t and not named:
            tot["files whose name time matches no break"] += 1

        # same-instant break pairs, and gaps
        lone_ph = lone_real = gap = 0
        for b, nxt in zip(brs, brs[1:]):
            if b.get("start_time") == nxt.get("start_time") and len(b.get("adverts", [])) == 1:
                if is_ph(b["adverts"][0]):
                    lone_ph += 1
                else:
                    lone_real += 1
        for b in brs:
            kept = collapse(b)
            rels = [r for r, _ in kept]
            if any(y - x > GAP_S for x, y in zip(rels, rels[1:])):
                gap += 1
        single_ph_only = sum(1 for b in brs if b.get("adverts") and all(is_ph(x) for x in b["adverts"]))
        tot["breaks holding only placeholders"] += single_ph_only
        tot["lone placeholder ahead of a same-instant break"] += lone_ph
        tot["lone REAL advert ahead of a same-instant break (break split in two)"] += lone_real
        tot["breaks with a gap over %d s" % GAP_S] += gap
        titles = lambda r: "; ".join(sorted({str((b.get("programme_before") or {}).get("title", "?"))
                                              for b in r}))[:120]
        rows.append([stem, name_ch, meta_ch, len(brs), len(runs),
                     " / ".join(f"{r[0]['index']}-{r[-1]['index']}" for r in runs),
                     "" if not named else f"{runs[named[0]][0]['index']}-{runs[named[0]][-1]['index']}",
                     "" if own is None else own, other if len(runs) > 1 else 0,
                     lone_ph, lone_real, gap,
                     " || ".join(titles(r) for r in runs) if len(runs) > 1 else ""])
    write(out / "schedule_structure.csv",
          ["recording", "channel_in_name", "channel_in_metadata", "breaks", "index_runs", "runs",
           "run_with_name_break", "real_records_in_that_run", "real_records_in_other_runs",
           "lone_placeholder_pairs", "split_break_pairs", "gap_breaks", "programmes_per_run"], rows)

    lines = [f"source: {a.src}", "", "TOTALS"]
    lines += [f"  {k:<68}{v:>8,}" for k, v in tot.items()]
    lines += ["", "CHANNEL IN FILE NAME  vs  video_info.channel   (files)"]
    lines += [f"  {n:<14} {m:<14} {c:>6,}{'' if n == m else '   <-- differ'}"
              for (n, m), c in sorted(cross.items(), key=lambda kv: (kv[0][0], -kv[1]))]
    lines += ["", "table: schedule_structure.csv (one row per recording; programmes_per_run shows",
              "       the programme titles around each run of breaks where there is more than one)"]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
def cmd_diff(a):
    """Which adverts does one run cut that the other does not?

    Runs are compared by cut point, not by break number, so a run made with
    --clean-schedule (whose breaks are merged, split and renumbered) can be set beside
    one made without. Two cut points in the same recording are the same when they lie
    within 0.5 s of each other.
    """
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def cuts(run):
        rows, _, stems = load_run(Path(run))
        by = defaultdict(list)
        for (stem, _, _), r in rows.items():
            try:
                by[stem].append((float(r["start_silence_mid_s"]), str(r.get("unique_id", "")),
                                 r.get("brand", "")))
            except (KeyError, ValueError):
                pass
        return by, stems

    A, sa = cuts(a.a)
    B, sb = cuts(a.b)
    both = sa & sb
    if a.exclude:
        both = {k for k in both if not any(x in k for x in a.exclude)}
    only_a, only_b, same, relabel = [], [], 0, 0
    for stem in sorted(both):
        la, lb = sorted(A.get(stem, [])), sorted(B.get(stem, []))
        used = set()
        for t, uid, brand in la:
            j = next((j for j, (t2, _, _) in enumerate(lb) if j not in used and abs(t2 - t) <= 0.5), None)
            if j is None:
                only_a.append([stem, f"{t:.2f}", uid, brand])
            else:
                used.add(j)
                same += 1
                relabel += lb[j][1] != uid
        only_b += [[stem, f"{t:.2f}", uid, brand] for j, (t, uid, brand) in enumerate(lb) if j not in used]
    write(out / "only_in_a.csv", ["recording", "start_s", "unique_id", "brand"], only_a)
    write(out / "only_in_b.csv", ["recording", "start_s", "unique_id", "brand"], only_b)
    real = lambda rows: [r for r in rows if not r[2].startswith(PLACEHOLDER)]
    uid = lambda d: {u for stem in both for _, u, _ in d.get(stem, []) if u and not u.startswith(PLACEHOLDER)}
    ua, ub = uid(A), uid(B)
    lines = [f"A: {a.a}", f"B: {a.b}", f"recordings in both: {len(both):,}", "",
             f"cut points in both runs            {same:>7,}",
             f"  of which labelled differently    {relabel:>7,}",
             f"cut points only in A               {len(only_a):>7,}   ({len(real(only_a)):,} not placeholders)",
             f"cut points only in B               {len(only_b):>7,}   ({len(real(only_b)):,} not placeholders)",
             "",
             f"unique adverts (by ledger label) A {len(ua):>7,}",
             f"unique adverts (by ledger label) B {len(ub):>7,}",
             f"  in A but not in B                {len(ua - ub):>7,}",
             f"  in B but not in A                {len(ub - ua):>7,}",
             "", "tables: only_in_a.csv  only_in_b.csv"]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
def cmd_fillcheck(a):
    """How good would a boundary be if it had to be filled from the schedule?

    Filling puts a boundary that found no silence at (its schedule offset + the
    break's fitted offset T). Its clip length then agrees with the slot grid by
    construction, so slot deviation cannot test it. But the boundaries that DID find
    a silence can: leave one out, fit T from the others in the break, predict the one
    left out, and compare with where its silence actually is. That error is what a
    filled boundary would have had at that place. No video is read.

    Also counts, in recordings whose start two breaks confirm, the breaks dropped for
    unmatched boundaries by how many are missing: what each acceptance rule would add.
    """
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    run = Path(a.run)
    sched, _ = load_schedule(Path(a.src))
    rows, _, _ = load_run(run)
    by_break = defaultdict(list)
    for (stem, idx, pos), r in rows.items():
        if r.get("start_source", "silence") == "schedule" or not r.get("start_silence_mid_s"):
            continue
        try:
            rel = sched[stem][idx][pos - 1][0]
        except (KeyError, IndexError):
            continue
        conf = r.get("start_confirmed_by", "")
        by_break[(stem, idx)].append((pos, rel, float(r["start_silence_mid_s"]),
                                      int(conf) if str(conf).isdigit() else 0))
    errs = []                       # (signed error s, position class, year, confirmed)
    for (stem, idx), items in by_break.items():
        if len(items) < 4:
            continue
        year = parse_stem(stem)[0]
        for k, (pos, rel, mid, conf) in enumerate(items):
            others = [m - r for j, (_, r, m, _) in enumerate(items) if j != k]
            pred = rel + statistics.median(others)
            errs.append((mid - pred, "break-first" if pos == 1 else "within break", year, conf >= 2))

    def describe(sel):
        v = sorted(abs(e) for e in sel)
        if not v:
            return None
        q = lambda f: v[min(len(v) - 1, int(f * len(v)))]
        signed = sorted(sel)
        return [len(v), f"{signed[len(signed) // 2]:+.3f}", f"{q(0.5):.3f}", f"{q(0.9):.3f}", f"{q(0.99):.3f}",
                pct(sum(x <= 0.2 for x in v), len(v)), pct(sum(x <= 0.5 for x in v), len(v)),
                pct(sum(x <= 1.0 for x in v), len(v))]

    table = []
    groups = [("all", lambda e: True), ("within break", lambda e: e[1] == "within break"),
              ("break-first", lambda e: e[1] == "break-first"),
              ("start confirmed by two breaks", lambda e: e[3]),
              ("start not confirmed", lambda e: not e[3])]
    groups += [(y, (lambda e, y=y: e[2] == y)) for y in sorted({e[2] for e in errs})]
    for name, f in groups:
        d = describe([e[0] for e in errs if f(e)])
        if d:
            table.append([name] + d)
    write(out / "leave_one_out.csv",
          ["group", "n", "median_signed_s", "median_abs_s", "p90_abs_s", "p99_abs_s",
           "within_0.2s", "within_0.5s", "within_1.0s"], table)

    # what each acceptance rule would add, in recordings with a confirmed start
    gain, slots_gain = Counter(), Counter()
    conf_recs = fall_recs = 0
    for log in sorted((run / "_logs").glob("*.log")):
        text = log.read_text(encoding="utf-8", errors="replace")
        confirmed = "[align] recording start fixed" in text
        conf_recs += confirmed
        fall_recs += "[align] no recording start" in text
        for m in DROP_MATCH.finditer(text):
            n, nb = int(m.group(2)), int(m.group(3))
            miss = nb - n
            if nb < 3 or miss == 0:
                continue
            key = ("confirmed" if confirmed else "not confirmed",
                   str(miss) if miss <= 3 else ("4+ but at least half matched" if n * 2 >= nb
                                                else "4+ and under half matched"))
            gain[key] += 1
            slots_gain[key] += nb
    # chance: the [null] lines give, for each break, its match where it belongs and
    # at four places in programme. A rule is judged by how often a null would pass it.
    null_re = re.compile(r"\[null\] .*?: here (\d+)/(\d+); elsewhere ([\d ]+)")
    here, nulls = [], []
    for log in sorted((run / "_logs").glob("*.log")):
        for m in null_re.finditer(log.read_text(encoding="utf-8", errors="replace")):
            nb = int(m.group(2))
            here.append((int(m.group(1)), nb))
            nulls += [(int(x), nb) for x in m.group(3).split()]
    rules = [("all boundaries matched", lambda n, nb: n == nb),
             ("at most 1 missing", lambda n, nb: nb - n <= 1),
             ("at most 2 missing", lambda n, nb: nb - n <= 2),
             ("at most 3 missing", lambda n, nb: nb - n <= 3),
             ("at least 2/3 matched", lambda n, nb: n * 3 >= nb * 2),
             ("at least half matched", lambda n, nb: n * 2 >= nb)]
    null_table = []
    sizes = [("all sizes", lambda nb: True), ("3-4 boundaries", lambda nb: nb <= 4),
             ("5-7 boundaries", lambda nb: 5 <= nb <= 7), ("8 or more", lambda nb: nb >= 8)]
    if nulls:
        for sname, sz in sizes:
            h = [(n, nb) for n, nb in here if sz(nb)]
            z = [(n, nb) for n, nb in nulls if sz(nb)]
            if not z:
                continue
            for name, ok in rules:
                real = sum(1 for n, nb in h if n >= 3 and ok(n, nb))
                false = sum(1 for n, nb in z if n >= 3 and ok(n, nb))
                null_table.append([sname, name, real, pct(real, len(h)), false, pct(false, len(z)),
                                   len(h), len(z)])
        write(out / "chance_by_rule.csv",
              ["break size", "rule (and at least 3 matched)", "breaks passing where they belong",
               "share", "null positions passing", "share", "breaks", "null fits"], null_table)

    lines = [f"run: {a.run}", "",
             "LEAVE-ONE-OUT: error of a boundary predicted from the rest of its break",
             "(positive = the silence midpoint lies later than predicted)",
             f"  {'group':<32}{'n':>7}  {'median':>8}  {'|med|':>6}  {'|p90|':>6}  {'|p99|':>6}  <=0.2s  <=0.5s  <=1.0s"]
    lines += [f"  {t[0]:<32}{t[1]:>7,}  {t[2]:>8}  {t[3]:>6}  {t[4]:>6}  {t[5]:>6}  {t[6]:>6}  {t[7]:>6}  {t[8]:>6}"
              for t in table]
    lines += ["", f"recordings with a confirmed start: {conf_recs:,}   fell back: {fall_recs:,}", "",
              "BREAKS DROPPED FOR UNMATCHED BOUNDARIES (3 or more boundaries), by number missing",
              "  = what accepting them and filling the gaps would add"]
    for k in sorted(gain):
        lines.append(f"  {k[0]:<14} missing {k[1]:<30} {gain[k]:>5,} breaks {slots_gain[k]:>6,} slots")
    if null_table:
        lines += ["", f"CHANCE: {len(here):,} breaks fitted where they belong, {len(nulls):,} fits five and seven",
                  "minutes either side. A fit that far from a LONG break can still lie inside it, so the",
                  "figures for 8 or more boundaries overstate chance; those for short breaks do not."]
        last = None
        for t in null_table:
            if t[0] != last:
                lines += ["", f"  {t[0]}  ({t[6]:,} breaks, {t[7]:,} null fits)",
                          f"    {'rule (with at least 3 matched)':<32}{'pass, in place':>16}{'pass, elsewhere':>20}"]
                last = t[0]
            lines += [f"    {t[1]:<32}{t[2]:>8,} {t[3]:>7}{t[4]:>12,} {t[5]:>7}"]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
def cmd_precision(a):
    """Are the cut points of a run as well placed as before, and which should be
    looked at by eye?

    Two automatic layers, per group of cut points:
      slot deviation   |clip length - nearest scheduled slot|: agreement with the
                       schedule, NOT position (a uniform shift leaves it unchanged);
      audio offset     cut point minus the end of its silence, which comes from the
                       audio alone and shows a systematic drift if there is one.
    Groups: cut points the reference run also has against those only this run has
    (--ref, matched within 0.5 s on the Stage A cut); recording start confirmed by
    two breaks against not; breaks of 3-4 boundaries against longer ones; break-first
    against the rest; year.

    Then a seeded sample for frame-by-frame checking, drawn from the groups whose
    position has the least support, with the frame_strip.py command for each.
    """
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, _, _ = load_run(Path(a.run))
    ref = defaultdict(list)
    if a.ref:
        rrows, _, _ = load_run(Path(a.ref))
        for (stem, _, _), r in rrows.items():
            try:
                ref[stem].append(float(r["start_silence_mid_s"]))
            except (KeyError, ValueError):
                pass
    size = Counter((stem, br) for (stem, br, _) in rows)
    items = []
    for (stem, br, pos), r in rows.items():
        if any(x in stem for x in a.exclude):
            continue
        try:
            mid, start, dur = float(r["start_silence_mid_s"]), float(r["start_s"]), float(r["duration_s"])
        except (KeyError, ValueError):
            continue
        se = float(r["silence_end_s"]) if r.get("silence_end_s") else None
        conf = str(r.get("start_confirmed_by", ""))
        items.append({
            "stem": stem, "br": br, "pos": pos, "start": start, "dur": dur,
            "dev": deviation(dur) if dur <= 120 else None,
            # against the length this advert's own identifier states: a 30 s advert
            # cut to 20 s passes the nearest-slot test and fails this one
            "own": abs(dur - id_duration(r.get("unique_id"))) if id_duration(r.get("unique_id")) else None,
            "signed": dur - id_duration(r.get("unique_id")) if id_duration(r.get("unique_id")) else None,
            "delta": (start - se) if se is not None else None,
            "status": r.get("refine_status", ""), "brand": r.get("brand", ""),
            "uid": r.get("unique_id", ""), "last": r.get("break_last", "") == "1",
            "new": bool(a.ref) and not any(abs(mid - t) <= 0.5 for t in ref.get(stem, [])),
            "confirmed": conf.isdigit() and int(conf) >= 2,
            "small": size[(stem, br)] <= 4, "first": pos == 1, "year": parse_stem(stem)[0]})

    med = lambda v: statistics.median(v) if v else float("nan")

    def row(name, sel):
        d = [x["dev"] for x in sel if x["dev"] is not None]
        dl = [x["delta"] for x in sel if x["delta"] is not None]
        over = sum(1 for x in sel if x["dev"] is None)
        o = [x["own"] for x in sel if x["own"] is not None]
        return [name, len(sel), f"{med(d):.3f}", pct(sum(v <= 0.2 for v in d), len(d)), over,
                len(o), f"{med(o):.3f}", pct(sum(v <= 0.2 for v in o), len(o)),
                pct(sum(v > 1.0 for v in o), len(o)), pct(sum(v > 5.0 for v in o), len(o)),
                f"{1000 * med(dl):.0f}", pct(sum(v > 0.08 for v in dl), len(dl)),
                pct(sum(1 for x in sel if x["status"] == "refined"), len(sel)),
                pct(sum(1 for x in sel if x["status"] == "reverted-past-silence"), len(sel))]

    groups = [("all cut points", lambda x: True)]
    if a.ref:
        groups += [("also in the reference run", lambda x: not x["new"]),
                   ("only in this run", lambda x: x["new"])]
    groups += [("start confirmed by two breaks", lambda x: x["confirmed"]),
               ("start not confirmed", lambda x: not x["confirmed"]),
               ("break of 3-4 boundaries", lambda x: x["small"]),
               ("break of 5 or more", lambda x: not x["small"]),
               ("break-first", lambda x: x["first"]), ("within break", lambda x: not x["first"]),
               ("break-last", lambda x: x["last"]), ("not break-last", lambda x: not x["last"])]
    groups += [(y, (lambda x, y=y: x["year"] == y)) for y in sorted({x["year"] for x in items})]
    table = [row(n, [x for x in items if f(x)]) for n, f in groups]
    table = [t for t in table if t[1]]
    write(out / "precision_by_group.csv",
          ["group", "cut_points", "median_slot_dev_s", "slot_dev_within_0.2s", "clips_over_120s",
           "with_own_length", "median_own_dev_s", "own_dev_within_0.2s", "own_dev_over_1s",
           "own_dev_over_5s", "median_start_minus_silence_end_ms", "start_over_80ms_past_silence_end",
           "refined", "reverted_by_guard"], table)

    # sample for the eye: least-supported positions first
    rng = random.Random(a.seed)
    strata = [("only in this run, start confirmed", lambda x: x["new"] and x["confirmed"]),
              ("start not confirmed, only break kept in its recording",
               lambda x: not x["confirmed"] and kept_breaks[x["stem"]] == 1),
              ("break of 3-4 boundaries", lambda x: x["small"]),
              ("break-first, also in the reference run", lambda x: x["first"] and not x["new"]),
              ("within break, also in the reference run", lambda x: not x["first"] and not x["new"])]
    kept_breaks = Counter()
    for stem, br in size:
        kept_breaks[stem] += 1
    taken, sample = set(), []
    for name, f in strata:
        pool = sorted([x for x in items if f(x) and (x["stem"], x["br"], x["pos"]) not in taken],
                      key=lambda x: (x["stem"], x["br"], x["pos"]))
        for x in rng.sample(pool, min(a.per_group, len(pool))):
            taken.add((x["stem"], x["br"], x["pos"]))
            cmd = (f'python3 src/frame_strip.py "{a.src}/{x["stem"]}.mp4" --at {x["start"]:.3f} '
                   f'--before 0.4 --after 1.0 --out "strips/{len(sample) + 1:02d}.html"')
            sample.append([len(sample) + 1, name, x["stem"], x["br"], x["pos"], f'{x["start"]:.3f}',
                           x["status"], x["brand"], x["uid"], "", "", "", cmd])
    write(out / "manual_sample.csv",
          ["n", "stratum", "recording", "break", "pos", "start_s", "refine_status", "schedule_brand",
           "unique_id", "truth_s", "brand_ok", "note", "command"], sample)

    lines = [f"run: {a.run}", f"reference: {a.ref or '(none)'}", "",
             "slot dev = |clip length - nearest slot|, clips over 120 s set aside; agreement, not position",
             "offset   = cut point minus end of its silence (audio); past +80 ms the guard should have acted",
             "",
             f"  {'group':<32}{'n':>7} {'slot dev':>9} {'<=0.2s':>7} {'>120s':>6} {'offset ms':>10} {'>+80ms':>7} {'refined':>8} {'reverted':>9}"]
    lines += [f"  {t[0]:<32}{t[1]:>7,} {t[2]:>9} {t[3]:>7} {t[4]:>6} {t[10]:>10} {t[11]:>7} {t[12]:>8} {t[13]:>9}"
              for t in table]
    lines += ["", "own dev = |clip length - the length its own identifier states|",
              f"  {'group':<32}{'n':>7} {'own dev':>9} {'<=0.2s':>7} {'>1s':>7} {'>5s':>7}"]
    lines += [f"  {t[0]:<32}{t[5]:>7,} {t[6]:>9} {t[7]:>7} {t[8]:>7} {t[9]:>7}" for t in table]
    lastc = sorted(x["signed"] for x in items if x["last"] and x["signed"] is not None)
    if lastc:
        q = lambda f: lastc[min(len(lastc) - 1, int(f * len(lastc)))]
        lines += ["", f"break-last clips, clip length minus stated length (s), n={len(lastc):,}: "
                      f"p5 {q(0.05):+.2f}  p25 {q(0.25):+.2f}  median {q(0.5):+.2f}  p75 {q(0.75):+.2f}  p95 {q(0.95):+.2f}"]
    worst = sorted((x for x in items if x["own"] is not None and x["own"] > 5.0),
                   key=lambda x: (x["stem"], x["br"], x["pos"]))
    write(out / "own_dev_over_5s.csv",
          ["recording", "break", "pos", "unique_id", "brand", "start_s", "duration_s", "stated_s",
           "break_last", "break_size", "new", "confirmed"],
          [[x["stem"], x["br"], x["pos"], x["uid"], x["brand"], f'{x["start"]:.3f}', f'{x["dur"]:.3f}',
            id_duration(x["uid"]), int(x["last"]), size[(x["stem"], x["br"])], int(x["new"]),
            int(x["confirmed"])] for x in worst])
    lines += [f"own_dev_over_5s.csv: {len(worst)} clips in "
              f"{len({(x['stem'], x['br']) for x in worst})} breaks"]
    lines += ["", f"manual_sample.csv: {len(sample)} cut points, seed {a.seed}"]
    lines += [f"  {n:<58}{sum(1 for r in sample if r[1] == n):>3}" for n, _ in strata]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
def cmd_selftest(_):
    """Synthetic schedule + ledger with known answers. Touches no real data."""
    with tempfile.TemporaryDirectory() as tmp:
        src, run = Path(tmp) / "src", Path(tmp) / "run"
        (run / "_logs").mkdir(parents=True)
        src.mkdir()
        ad = lambda uid, t: {"unique_id": uid, "brand": uid.lower(), "start_time": t}
        meta = {"ad_breaks": [
            {"index": 1, "start_time": "10:00:00", "adverts": [
                ad("AAA020", "10:00:00"), ad("SKYADSMART000", "10:00:00"),
                ad("BBB030", "10:00:20"), ad("CCC010", "10:00:50")]},
            {"index": 2, "start_time": "10:20:00", "adverts": [
                ad("DDD030", "10:20:00"), ad("AAA020", "10:20:30"), ad("EEE010", "10:20:50")]},
            {"index": 3, "start_time": "10:40:00", "adverts": [
                ad("FFF030", "10:40:00"), ad("GGG030", "10:40:30"), ad("HHH010", "10:41:00")]}]}
        stem = "2024-03-26_5STAR_10-00-00"   # no colon: the self-test must also run on Windows
        with open(src / f"{stem}_metadata.json", "w", encoding="utf-8") as f:
            json.dump(meta, f)
        cols = ["recording", "break", "pos", "unique_id", "brand", "placeholder",
                "start_silence_mid_s", "silence_start_s", "silence_end_s", "start_s", "shift_ms",
                "refine_status", "refine_backend", "min_scene_len", "start_source", "end_s",
                "duration_s", "break_last", "clip"]
        (run / stem).mkdir()
        with open(run / stem / "cut_plan.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            # break 1 kept; labels taken from the RAW list, as cut_adverts.py does today
            for pos, uid, s, e in [(1, "AAA020", 100.3, 120.3), (2, "SKYADSMART000", 120.3, 150.3),
                                   (3, "BBB030", 150.3, 160.3)]:
                w.writerow({"recording": stem + ".mp4", "break": 1, "pos": pos, "unique_id": uid,
                            "brand": uid.lower(), "start_silence_mid_s": f"{s:.3f}",
                            "silence_start_s": f"{s - 0.3:.3f}", "silence_end_s": f"{s + 0.3:.3f}",
                            "start_s": f"{s:.3f}", "shift_ms": "0", "refine_status": "clean",
                            "start_source": "silence" if pos != 2 else "schedule",
                            "end_s": f"{e:.3f}", "duration_s": f"{e - s:.3f}"})
        (run / "_logs" / f"{stem}.log").write_text(
            "[drop] break 2 (10:20:00-None): only 2/3 boundaries matched (67% < 100%, or fewer than 3)\n"
            "[drop] break 3 (10:40:00-None): offset 12.0s inconsistent (expected ~900.0s)\n",
            encoding="utf-8")
        ns = argparse.Namespace(src=str(src), run=str(run), out=str(Path(tmp) / "o1"), only_run=False, common_with=None, exclude=[], clean_schedule=False, own_channel=False)
        cmd_recon(ns)
        text = (Path(tmp) / "o1" / "summary.txt").read_text(encoding="utf-8")
        mism = list(csv.reader(open(Path(tmp) / "o1" / "label_mismatch.csv", encoding="utf-8")))
        fates = {r[1]: r[3] for r in csv.reader(open(Path(tmp) / "o1" / "break_fate.csv", encoding="utf-8"))}
        checks = [
            ("10 records, 1 placeholder", "schedule records                      10" in text),
            ("9 slots after collapsing", "slots after collapsing                 9" in text),
            ("8 unique adverts", "unique adverts after collapsing        8" in text),
            ("3 unique kept (AAA BBB CCC)", "unique adverts kept                    3" in text),
            ("2 label mismatches found", len(mism) - 1 == 2),
            ("break 2 dropped for match", fates.get("2") == "dropped: boundaries unmatched"),
            ("break 3 dropped for offset", fates.get("3") == "dropped: offset inconsistent"),
        ]
        ns2 = argparse.Namespace(src=str(src), run=str(run), out=str(Path(tmp) / "o2"),
                                 seed=1, per_group=5)
        cmd_misses(ns2)
        ns3 = argparse.Namespace(run=str(run), out=str(Path(tmp) / "o3"), back=[0.2, 0.28, 0.35])
        cmd_anchor(ns3)
        print("\nSELFTEST")
        for name, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'} {name}")
        sys.exit(0 if all(ok for _, ok in checks) else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("recon")
    p.add_argument("--src", required=True, help="directory of <name>.mp4 + <name>_metadata.json")
    p.add_argument("--run", required=True, help="batch_cut.py --outroot of the run to audit")
    p.add_argument("--out", required=True)
    p.add_argument("--only-run", action="store_true",
                   help="restrict the schedule to recordings present in the run (sample runs); "
                        "the raw-record lines of the summary are then not counted")
    p.add_argument("--exclude", nargs="+", default=[], metavar="NAME",
                   help="leave out recordings whose name contains any of these strings")
    p.add_argument("--own-channel", action="store_true",
                   help="read the schedule as an --own-channel run did (other channel's run left out)")
    p.add_argument("--clean-schedule", action="store_true",
                   help="read the schedule as a --clean-schedule run did (merged, placeholders "
                        "dropped, split at long gaps); use it to audit such a run")
    p.add_argument("--common-with", metavar="OTHER_RUN",
                   help="count only recordings that this run AND the other run have finished, "
                        "so two sample runs are compared on the same recordings")
    p.set_defaults(fn=cmd_recon)
    p = sub.add_parser("misses")
    p.add_argument("--src", required=True)
    p.add_argument("--run", required=True, help="a run made WITH fill-missing (the default)")
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=20261010)
    p.add_argument("--per-group", type=int, default=12, help="sampled cut points per group")
    p.add_argument("--clean-schedule", action="store_true")
    p.add_argument("--own-channel", action="store_true")
    p.set_defaults(fn=cmd_misses)
    p = sub.add_parser("anchor")
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--back", type=float, nargs="+", default=[0.20, 0.24, 0.28, 0.32, 0.35],
                   help="seconds before the silence end to try as the anchor")
    p.set_defaults(fn=cmd_anchor)
    p = sub.add_parser("offsets")
    p.add_argument("--src", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--tol", type=float, default=5.0,
                   help="seconds within which two breaks count as agreeing (cut_adverts --offset-tol)")
    p.add_argument("--probe", action="store_true",
                   help="read each recording's length with ffprobe and report which scheduled "
                        "breaks fall inside the file")
    p.set_defaults(fn=cmd_offsets)
    p = sub.add_parser("schedule")
    p.add_argument("--src", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_schedule)
    p = sub.add_parser("diff")
    p.add_argument("--a", required=True, help="first run (batch_cut.py --outroot)")
    p.add_argument("--b", required=True, help="second run")
    p.add_argument("--out", required=True)
    p.add_argument("--exclude", nargs="+", default=[], metavar="NAME")
    p.set_defaults(fn=cmd_diff)
    p = sub.add_parser("fillcheck")
    p.add_argument("--src", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--clean-schedule", action="store_true")
    p.add_argument("--own-channel", action="store_true")
    p.set_defaults(fn=cmd_fillcheck)
    p = sub.add_parser("precision")
    p.add_argument("--run", required=True, help="a run made WITH Stage B refinement")
    p.add_argument("--ref", help="an earlier run on the same recordings; its cut points mark what is new")
    p.add_argument("--src", required=True, help="recordings directory (for the frame_strip commands)")
    p.add_argument("--out", required=True)
    p.add_argument("--exclude", nargs="+", default=[], metavar="NAME")
    p.add_argument("--seed", type=int, default=20261012)
    p.add_argument("--per-group", type=int, default=4, help="cut points sampled per stratum")
    p.set_defaults(fn=cmd_precision)
    p = sub.add_parser("selftest")
    p.set_defaults(fn=cmd_selftest)
    a = ap.parse_args()
    global CLEAN, OWN
    CLEAN = bool(getattr(a, "clean_schedule", False))
    OWN = bool(getattr(a, "own_channel", False))
    a.fn(a)


if __name__ == "__main__":
    main()
