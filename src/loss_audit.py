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
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

SLOTS = [10, 15, 20, 30, 40, 45, 60, 90]
PLACEHOLDER = "SKYADSMART"
GAP_S = 120          # no advert is this long; a longer gap inside a break is two breaks
HIST_KEYS = ["1", "2", "3", "4+", "0 matched", "0 (too few boundaries)"]
DROP_MATCH = re.compile(r"\[drop\] break (\d+) \(.*?\): only (\d+)/(\d+) boundaries")
DROP_OFFSET = re.compile(r"\[drop\] break (\d+) \(.*?\): offset ")
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
        for br in data.get("ad_breaks", []):
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
        ns = argparse.Namespace(src=str(src), run=str(run), out=str(Path(tmp) / "o1"))
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
    p.set_defaults(fn=cmd_recon)
    p = sub.add_parser("misses")
    p.add_argument("--src", required=True)
    p.add_argument("--run", required=True, help="a run made WITH fill-missing (the default)")
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=20261010)
    p.add_argument("--per-group", type=int, default=12, help="sampled cut points per group")
    p.set_defaults(fn=cmd_misses)
    p = sub.add_parser("anchor")
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--back", type=float, nargs="+", default=[0.20, 0.24, 0.28, 0.32, 0.35],
                   help="seconds before the silence end to try as the anchor")
    p.set_defaults(fn=cmd_anchor)
    p = sub.add_parser("selftest")
    p.set_defaults(fn=cmd_selftest)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
