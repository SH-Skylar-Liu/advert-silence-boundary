#!/usr/bin/env python3
"""A human review plan for a cut run, after ISSA Workshop 1 §8 (supervision of 20 Oct 2026).
人工复核计划：仿 ISSA WS1 §8，把台账里"值得人看一眼"的情况写成规则，计数，
再按"每次优先看还没见过的问题类型"排出看片顺序。

    python3 review_plan.py --run runs/v17 --src $SRC --out review_v17 --budget 20

A flag means "worth a look", not "wrong" (ISSA's wording). Every rule is computed from
the ledger columns and the metadata JSON only; nothing is decoded.

Ordering: recordings are picked one at a time. A recording scores 1 for each rule it
triggers that has been seen fewer than three times in the recordings already picked,
and 0.1 for a rule already seen three times, plus a small bonus for high-priority
rules. So the reviewer meets every kind of problem early, instead of the same one
twenty times.

Writes OUT/rule_counts.csv, OUT/flags.csv (one row per flag), OUT/review_order.csv
(the viewing order, with a frame_strip.py command for the first flagged cut of each
recording) and OUT/review_plan.md (the table to paste into notes).
"""
import argparse
import csv
import glob
import json
import os
import shlex
from collections import Counter, defaultdict

from corpus_stats import id_duration, placeholder_kind, read_rows

# rule -> (priority, level, meaning) / 规则 -> （优先级、层级、含义）
RULES = {
    "length_off":         ("high",   "clip",      "clip length differs from the identifier's stated length by more than 1 s (not the last clip of a break)"),
    "last_cut_short":     ("high",   "clip",      "last clip of a break ends more than 5 s before its stated length: a pause inside the advert taken as its end?"),
    "overlap":            ("high",   "clip",      "clip starts before the previous clip of the same break has ended"),
    "placeholder_clip":   ("high",   "clip",      "an addressable placeholder slot was planned as a clip of its own"),
    "no_clips":           ("high",   "recording", "recording has metadata but no ledger: no break was accepted"),
    "low_yield":          ("high",   "recording", "fewer than 30% of the recording's scheduled adverts were planned as clips"),
    "length_off_small":   ("medium", "clip",      "clip length differs from the stated length by 0.2-1 s (not the last clip)"),
    "gap_in_break":       ("medium", "clip",      "more than 1 s between the end of one clip and the start of the next in a break: unlogged content?"),
    "reverted":           ("medium", "clip",      "Stage B cut point fell past the end of the silence and was reverted"),
    "large_shift":        ("medium", "clip",      "Stage B moved the cut point by more than 600 ms"),
    "schedule_filled":    ("medium", "clip",      "start filled from the schedule, no silence found there"),
    "unconfirmed_start":  ("low",    "break",     "break placed on its own fit; no other break confirms the recording start"),
    "no_stated_length":   ("low",    "clip",      "identifier does not end in three digits, so the clip cannot be checked against a length"),
}
PRI_BONUS = {"high": 0.3, "medium": 0.1, "low": 0.0}


def f(row: dict, k: str):
    try:
        return float(row.get(k, ""))
    except ValueError:
        return None


def scheduled_count(meta_path: str) -> int:
    """Broadcast adverts the recording's metadata lists / metadata 里列出的真实广告数。"""
    d = json.load(open(meta_path, encoding="utf-8"))
    return sum(1 for b in d.get("ad_breaks", []) for ad in b.get("adverts", [])
               if not placeholder_kind(ad.get("unique_id")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run directory with */cut_plan.csv")
    ap.add_argument("--src", help="recordings + <name>_metadata.json; enables no_clips and low_yield")
    ap.add_argument("--budget", type=int, default=20, help="recordings in the viewing order")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    # ---- per-recording ledgers / 逐个录像读台账 ----
    ledgers = {}
    for fp in sorted(glob.glob(os.path.join(os.path.expanduser(a.run), "*", "cut_plan.csv"))):
        rows = read_rows(fp)
        if rows:                                   # key by the recording named in the ledger, not the folder
            rec = os.path.splitext(os.path.basename(rows[0].get("recording", "")))[0]
            ledgers[rec or os.path.basename(os.path.dirname(fp))] = rows
    print(f"{len(ledgers):,} recordings with a ledger in {a.run}")

    flags = []                                     # (recording, rule, break, clip, at_s, detail)

    def flag(rec, rule, br="", clip="", at="", detail=""):
        flags.append((rec, rule, br, clip, at, detail))

    for rec, rows in ledgers.items():
        by_break = defaultdict(list)
        for r in rows:
            by_break[r.get("break", "")].append(r)
        for br, rs in by_break.items():
            rs.sort(key=lambda r: f(r, "start_s") or 0)
            if any(not r.get("start_confirmed_by") for r in rs):
                flag(rec, "unconfirmed_start", br, rs[0].get("clip", ""), rs[0].get("start_s", ""))
            prev = None
            for r in rs:
                clip, at = r.get("clip", ""), r.get("start_s", "")
                dur, stated = f(r, "duration_s"), id_duration(r.get("unique_id"))
                last = r.get("break_last") == "1"
                if r.get("placeholder") == "1" or placeholder_kind(r.get("unique_id")):
                    flag(rec, "placeholder_clip", br, clip, at, r.get("unique_id", ""))
                elif stated is None:
                    flag(rec, "no_stated_length", br, clip, at, r.get("unique_id", ""))
                elif dur is not None:
                    diff = dur - stated
                    if last and diff < -5:
                        flag(rec, "last_cut_short", br, clip, at, f"{dur:.1f}s of {stated}s")
                    elif not last and abs(diff) > 1:
                        flag(rec, "length_off", br, clip, at, f"{diff:+.2f}s")
                    elif not last and abs(diff) > 0.2:
                        flag(rec, "length_off_small", br, clip, at, f"{diff:+.2f}s")
                if r.get("refine_status", "").startswith("reverted"):
                    flag(rec, "reverted", br, clip, at)
                shift = f(r, "shift_ms")
                if shift is not None and abs(shift) > 600:
                    flag(rec, "large_shift", br, clip, at, f"{shift:.0f} ms")
                if r.get("start_source") == "schedule":
                    flag(rec, "schedule_filled", br, clip, at)
                if prev is not None:
                    gap = (f(r, "start_s") or 0) - (f(prev, "end_s") or 0)
                    if gap < -0.04:                               # one frame of tolerance
                        flag(rec, "overlap", br, clip, at, f"{gap:+.2f}s")
                    elif gap > 1:
                        flag(rec, "gap_in_break", br, clip, at, f"{gap:.1f}s")
                prev = r

    # ---- recordings with no or few clips / 没切出或切出太少的录像 ----
    if a.src:
        for meta in sorted(glob.glob(os.path.join(os.path.expanduser(a.src), "*_metadata.json"))):
            rec = os.path.basename(meta)[: -len("_metadata.json")]
            n = scheduled_count(meta)
            if n == 0:
                continue
            got = len({r.get("unique_id") for r in ledgers.get(rec, []) if not r.get("placeholder")})
            if rec not in ledgers:
                flag(rec, "no_clips", detail=f"0 of {n} scheduled adverts")
            elif got / n < 0.3:
                flag(rec, "low_yield", detail=f"{got} of {n} scheduled adverts")

    # ---- counts / 计数 ----
    n_rule = Counter(fl[1] for fl in flags)
    rec_rule = defaultdict(set)
    for fl in flags:
        rec_rule[fl[0]].add(fl[1])
    recs_per_rule = Counter(r for rules in rec_rule.values() for r in rules)
    with open(os.path.join(a.out, "rule_counts.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["priority", "rule", "level", "meaning", "flags", "recordings"])
        for rule, (pri, lvl, meaning) in RULES.items():
            w.writerow([pri, rule, lvl, meaning, n_rule[rule], recs_per_rule[rule]])
    with open(os.path.join(a.out, "flags.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["recording", "rule", "priority", "break", "clip", "start_s", "detail"])
        for rec, rule, br, clip, at, det in flags:
            w.writerow([rec, rule, RULES[rule][0], br, clip, at, det])

    # ---- viewing order: new problem types first / 看片顺序：新问题类型优先 ----
    seen, order, left = Counter(), [], dict(rec_rule)
    while left and len(order) < a.budget:
        def score(rec):
            return sum((1.0 if seen[r] < 3 else 0.1) + PRI_BONUS[RULES[r][0]] for r in left[rec])
        best = max(sorted(left), key=score)
        order.append((best, score(best), sorted(left.pop(best), key=lambda r: list(RULES).index(r))))
        for r in order[-1][2]:
            seen[r] += 1
    first_cut = {}
    for rec, rule, br, clip, at, det in flags:
        if at and (rec, rule) not in first_cut:
            first_cut[(rec, rule)] = (clip, at)
    src = os.path.expanduser(a.src or "$SRC")
    with open(os.path.join(a.out, "review_order.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["order", "recording", "score", "rules", "first_flagged_clip", "frame_strip_command"])
        for i, (rec, sc, rules) in enumerate(order, 1):
            clip, at = next((first_cut[(rec, r)] for r in rules if (rec, r) in first_cut), ("", ""))
            cmd = (f"python3 frame_strip.py {shlex.quote(os.path.join(src, rec + '.mp4'))} --at {at} "
                   f"--out strip_{i:02d}.html") if at else ""
            w.writerow([i, rec, f"{sc:.1f}", " ".join(rules), clip, cmd])

    # ---- markdown summary / 摘要 ----
    n_rec = len(set(ledgers) | {fl[0] for fl in flags})
    lines = [f"# Human review plan · {os.path.basename(os.path.normpath(a.run))}", "",
             f"{len(rec_rule):,} of {n_rec:,} recordings trigger at least one rule; {len(flags):,} flags in all. "
             "A flag means worth a look, not wrong.", "",
             "| priority | rule | level | meaning | flags | recordings |", "|---|---|---|---|---|---|"]
    for rule, (pri, lvl, meaning) in RULES.items():
        lines.append(f"| {pri} | {rule} | {lvl} | {meaning} | {n_rule[rule]:,} | {recs_per_rule[rule]:,} |")
    lines += ["", f"## Viewing order (first {len(order)})", "", "| # | recording | rules |", "|---|---|---|"]
    lines += [f"| {i} | {rec} | {' '.join(rules)} |" for i, (rec, _, rules) in enumerate(order, 1)]
    with open(os.path.join(a.out, "review_plan.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
