# -*- coding: utf-8 -*-
"""
Cut individual adverts from a recording using the validated silence boundaries.
Reuses silence_detect.py (must be in the same folder) for detection + alignment.

Cut scheme
----------
Stage A locates each advert's start at the MIDPOINT of the silence preceding it.
Stage B then pushes that start forward to the first scene change within the next
second, because the midpoint typically sits a few frames early and the previous
advert bleeds in (refine_first_frame.py; --no-refine disables it).

A clip ENDS on the next advert's refined start (--end-mode next-start), so its length
is measured between two points of the same kind. Ending on the silence midpoint
instead (--end-mode midpoint) mixes two references and makes the length absorb the
next advert's shift as error; on one recording that made results worse than no
refinement at all.

The LAST advert of a break has no following advert to abut on. Its end comes from the
break-end silence, and is taken at the START of that silence (--last-end
silence-start): audio stops when the advert finishes, whereas the midpoint of that
~2 s transition sits about a second late. Measured 12 Sep 2026 over 40 break-final
clips, the midpoint rule ran 1.3-1.6 s long with a median error of 0.593 s against
the scheduled slot length.

Safeguards
----------
- Video is TRANSCODED (libx264), never stream-copied (pipeline rule).
- Offset-consistency filter: within a recording, each break's fitted offset T must
  be monotonic in wall-clock and differ from the previous kept break by the scheduled
  gap (within --offset-tol). Breaks that fail (e.g. a break not fully in the recording
  that aliases onto another's silences) are dropped, not cut.
- A break is accepted when at least --min-match-rate of its boundaries (and at least
  --min-matches of them) landed on a silence. Boundaries that found no silence are
  then filled from the schedule and the fitted offset, and flagged in the ledger as
  start_source=schedule. Requiring every boundary to match instead (--min-match-rate 1
  --no-fill-missing, the rule before 12 Sep 2026) discarded about half of all adverts.
- Silence guard: a Stage B refinement that lands more than --max-past-silence-end ms
  past the END of the silence is reverted to the Stage A midpoint and flagged in the
  ledger as reverted-past-silence. The silence end is where audio resumes, which comes
  from the audio and owes nothing to the picture detector, so an advert's first frame
  cannot sit far beyond it. Confirmed by eye on 19 Sep 2026 (b41_05, Lidl): the midpoint
  was correct to one frame and both detector backends pushed the cut 17 frames into the
  advert. Over 361 cut points the guard reverts about 5% of refinements and cuts the
  mean slot deviation from 0.208 s to 0.140 s. Pass 0 to reproduce earlier runs.
- Output goes to a NEW folder; no existing file is touched. Run --dry-run first.

Usage
-----
python3 cut_adverts.py RECORDING.mp4 --metadata breaks.json \
    --outdir /home/shihan/storage/video/silence_cut_test --cut-csv plan.csv --dry-run
# review the plan (and plan.csv), then drop --dry-run to actually cut
"""
import argparse
import csv
import json
import os
import re
import subprocess

from silence_detect import run_silencedetect, parse_silences, load_breaks, align, hms_to_s, align_by_recording, null_hits, id_duration, is_placeholder

from refine_first_frame import HAVE_SCENEDETECT, refine_start

FFMPEG = "ffmpeg"


def safe(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s.strip())
    return re.sub(r"_+", "_", s).strip("_")[:60]


def cut(recording: str, start: float, dur: float, out: str) -> bool:
    # fast seek before -i (quick), transcode video + audio for clean cuts and a/v sync
    # -map 0:v:0 / 0:a:0: the delivered files carry more than one video stream plus
    # several data streams (ffprobe, 21 Aug 2026). Without explicit mapping ffmpeg
    # may pick the wrong stream or drag a data stream into the clip.
    cmd = [FFMPEG, "-y", "-ss", f"{start:.3f}", "-i", recording, "-t", f"{dur:.3f}",
           "-map", "0:v:0", "-map", "0:a:0",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", out]
    r = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
    return r.returncode == 0


def probe_duration(path: str):
    """Recording length in seconds from the container header; None if unreadable."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--noise", type=float, default=-40)
    ap.add_argument("--min-dur", type=float, default=0.2)
    ap.add_argument("--tol", type=float, default=0.35)
    ap.add_argument("--min-last", type=float, default=8.0,
                    help="min seconds after last advert start before searching its end silence")
    ap.add_argument("--offset-tol", type=float, default=5.0,
                    help="max deviation (s) of a break's offset from the expected value")
    ap.add_argument("--align", choices=["break", "recording"], default="break",
                    help="break (default): fit each break on its own, then drop a break "
                         "whose offset disagrees with the previous kept one. recording: fix "
                         "the start of the recording from the fully matched breaks, then "
                         "check every break only where it must be (11 Oct 2026)")
    ap.add_argument("--confirm-partial", action="store_true",
                    help="with --align recording: let breaks that missed a silence also propose "
                         "the recording start; two breaks whose own fits agree confirm it")
    ap.add_argument("--fill-only-confirmed", action="store_true",
                    help="apply --min-match-rate and fill-missing only where the recording "
                         "start is confirmed; elsewhere keep the strict all-boundaries rule")
    ap.add_argument("--own-channel", action="store_true",
                    help="where the schedule lists breaks in more than one run of index, keep "
                         "only the run holding the break whose start time is in the file name; "
                         "the other run is another channel (see silence_detect.own_run)")
    ap.add_argument("--clean-schedule", action="store_true",
                    help="repair the schedule's break segmentation before aligning: merge "
                         "adjacent breaks that start at the same instant, drop Sky AdSmart "
                         "placeholder records, split breaks at gaps over two minutes "
                         "(see silence_detect.clean_breaks; 12 Oct 2026)")
    ap.add_argument("--dry-run", action="store_true", help="print the cut plan, do not cut")
    # --- Stage B (first-frame refinement) and the cut ledger ---
    ap.add_argument("--no-refine", action="store_true",
                    help="skip first-frame refinement; use the Stage A silence midpoints only")
    ap.add_argument("--refine-window", type=float, default=1.0,
                    help="refinement search window in seconds; 1.0 as agreed, do not enlarge")
    ap.add_argument("--refine-threshold", type=float, default=None,
                    help="detector threshold; defaults to the backend default (Stephen tuned nothing)")
    ap.add_argument("--max-past-silence-end", type=float, default=80.0, metavar="MS",
                    help="revert a refinement that lands more than this many ms past the "
                         "end of the silence, where audio resumes: such a refinement found "
                         "a shot change inside the advert (default 80 = two frames at 25 fps; "
                         "0 disables the guard, reproducing runs before 19 Sep 2026)")
    ap.add_argument("--min-scene-len", type=int, default=None,
                    help="scenedetect only, in frames; unset keeps PySceneDetect's default 15, "
                         "which hides any cut in the first 0.6 s of the window (15 Sep 2026)")
    ap.add_argument("--backend", choices=["auto", "ffmpeg", "scenedetect"], default="auto",
                    help="Stage B detector; auto = scenedetect when importable, else ffmpeg. "
                         "Force one to compare backends on the same recordings")
    ap.add_argument("--cut-csv", help="write the cut ledger as CSV, for manual QC")
    ap.add_argument("--end-mode", choices=["next-start", "midpoint"], default="next-start",
                    help="where a clip ends: on the next advert's refined start (default), "
                         "or on the silence midpoint (the pre-6-Sep behaviour, for A/B)")
    ap.add_argument("--fill-missing", dest="fill_missing", action="store_true", default=True,
                    help="fill boundaries the silence detector missed from the schedule "
                         "and the fitted offset (default on; they are flagged in the ledger)")
    ap.add_argument("--no-fill-missing", dest="fill_missing", action="store_false",
                    help="drop adverts whose boundary found no silence (pre-12-Sep behaviour)")
    ap.add_argument("--min-match-rate", type=float, default=0.5,
                    help="accept a break when at least this fraction of its boundaries "
                         "matched a silence (default 0.5; 1.0 = the old all-or-nothing rule)")
    ap.add_argument("--min-matches", type=int, default=3,
                    help="and at least this many matched, so the fitted offset is not "
                         "resting on one or two coincidences (default 3)")
    ap.add_argument("--last-end", choices=["silence-start", "midpoint", "stated", "stated-silence"],
                    default="silence-start",
                    help="where the LAST advert of a break ends: at the start of the "
                         "break-end silence (default), or at its midpoint (pre-12-Sep "
                         "behaviour, which ran systematically ~1.3-1.6 s long); stated = its "
                         "start plus the length in its identifier; stated-silence = the silence "
                         "starting within 2 s of that, else that. Both fall back to "
                         "silence-start where the identifier states no length")
    a = ap.parse_args()

    do_refine = not a.no_refine
    # Backend choice: use scenedetect when importable (same implementation as
    # Stephen's script), otherwise the ffmpeg filter. On the server scenedetect is
    # missing cv2, so the ffmpeg backend is what actually runs. The two thresholds
    # are not on the same scale, so cross-check against Stephen's output on the same
    # adverts before scaling up.
    if a.backend == "auto":
        refine_backend = "scenedetect" if HAVE_SCENEDETECT else "ffmpeg"
    else:
        refine_backend = a.backend
    # A forced scenedetect run must not quietly fall back: refine_start would return
    # "no-backend" for every cut point and the ledger would look like an unrefined run.
    if do_refine and refine_backend == "scenedetect" and not HAVE_SCENEDETECT:
        raise SystemExit("--backend scenedetect requested, but scenedetect/cv2 cannot be imported")
    if a.min_scene_len is not None and refine_backend != "scenedetect":
        raise SystemExit("--min-scene-len only applies to --backend scenedetect")
    # 0 disables the guard, which is how a pre-19-Sep-2026 run is reproduced.
    if a.max_past_silence_end is not None and a.max_past_silence_end <= 0:
        a.max_past_silence_end = None
    if a.max_past_silence_end is not None and not do_refine:
        a.max_past_silence_end = None          # nothing to guard without Stage B
    if do_refine:
        print(f"Stage B refinement: on (backend = {refine_backend}, "
              f"guard {a.max_past_silence_end if a.max_past_silence_end is not None else 'off'}, "
              f"min_scene_len {a.min_scene_len if a.min_scene_len is not None else 'default'}, "
              f"window {a.refine_window}s)\n")

    silences = parse_silences(run_silencedetect(a.recording, a.noise, a.min_dur))
    breaks = load_breaks(a.metadata, clean=a.clean_schedule, own=a.own_channel)
    if a.own_channel:
        n_all = len(load_breaks(a.metadata, clean=a.clean_schedule))
        if len(breaks) != n_all:
            print(f"schedule: kept {len(breaks)} of {n_all} breaks, the index run holding "
                  "the break named in the file name; the rest belong to another channel")
    if a.clean_schedule:
        print("schedule: cleaned (same-instant breaks merged, placeholders dropped, "
              "breaks split at gaps over two minutes)")

    # align every break, attach wall-clock, keep for consistency filtering
    items = []
    for br in breaks:
        n, T, hits = align(br["bounds"], silences, a.tol)
        idx = int(br["index"])
        wt = hms_to_s(br["start_time"])
        items.append({"idx": idx, "wt": wt, "T": T, "hits": hits,
                      "nb": len(br["bounds"]), "n": n, "label": br["label"],
                      "bounds": br["bounds"], "ads": br.get("adverts", [])})
    items.sort(key=lambda x: x["wt"])

    # --- recording-level alignment (opt-in) ---
    # With the start of the recording fixed, each break is fitted only where the
    # schedule puts it, so there is no chain of "agrees with the previous kept break"
    # for one chance fit to poison. Falls back to per-break fitting when no break in
    # the recording matched completely.
    by_recording = False
    if a.align == "recording":
        start, n_anchors, report = align_by_recording(items, silences, a.tol, a.offset_tol,
                                                      a.min_matches, probe_duration(a.recording),
                                                      partial=a.confirm_partial)
        if start is None:
            print("[align] no recording start that two breaks confirm"
                  + (" (no fully matched break)" if not n_anchors else "")
                  + "; falling back to per-break fitting")
            for c, full, total in report[:6]:
                print(f"[align]   candidate {c * -1:9.1f}s: {full} break(s) match completely, "
                      f"{total} boundaries in all")
        else:
            by_recording = True
            print(f"[align] recording start fixed at wall-clock {start * -1:.1f}s "
                  f"from {n_anchors} fully matched break(s)")
            for c, full, total in report[:6]:
                print(f"[align]   candidate {c * -1:9.1f}s: {full} break(s) match completely, "
                      f"{total} boundaries in all")
            for it in items:
                if it.get("place") == "outside":
                    print(f"[outside] {it['label']}: expected at {start + it['wt']:.0f}s, "
                          "not in this recording")
                elif it["nb"] >= a.min_matches:
                    # the same break looked for in programme, five and seven minutes
                    # either side: what chance alone matches (see null_hits)
                    nulls = null_hits(it["bounds"], silences, start + it["wt"], a.offset_tol, a.tol)
                    print(f"[null] {it['label']}: here {it['n']}/{it['nb']}; elsewhere "
                          + " ".join(str(x) for x in nulls))
            items = [it for it in items if it.get("place") != "outside"]

    # --fill-only-confirmed: a break whose position rests on its own fit alone is
    # held to the strict rule; partial matches are accepted, and their missing
    # boundaries filled, only where the recording start is confirmed.
    strict_here = a.fill_only_confirmed and not by_recording
    min_rate = 1.0 if strict_here else a.min_match_rate
    fill_here = a.fill_missing and not strict_here
    if a.fill_only_confirmed:
        print(f"[fill] {'strict rule: start not confirmed' if strict_here else 'partial matches accepted: start confirmed'}")

    # offset-consistency + full-match filter
    kept = []
    for it in items:
        # All-or-nothing acceptance costs about half the corpus. A break is accepted
        # when enough of its boundaries matched for the fitted offset T to be trusted;
        # the remainder are filled from the schedule and flagged. Raise
        # --min-match-rate towards 1.0 to return to the conservative behaviour.
        rate = it["n"] / it["nb"] if it["nb"] else 0.0
        if rate < min_rate or it["n"] < a.min_matches:
            print(f"[drop] {it['label']}: only {it['n']}/{it['nb']} boundaries matched "
                  f"({rate:.0%} < {min_rate:.0%}, or fewer than {a.min_matches})")
            continue
        if kept and not by_recording:
            p = kept[-1]
            expected = p["T"] + (it["wt"] - p["wt"])
            if abs(it["T"] - expected) > a.offset_tol:
                print(f"[drop] {it['label']}: offset {it['T']:.1f}s inconsistent "
                      f"(expected ~{expected:.1f}s) -- likely not in this recording")
                continue
        kept.append(it)

    os.makedirs(a.outdir, exist_ok=True)
    mid = lambda s: (s["start"] + s["end"]) / 2
    planned = made = 0
    plan_rows = []          # cut ledger: one row per advert (Stage A cut, Stage B shift, status)
    n_refined = 0
    n_reverted = 0
    for it in kept:
        idx, T, hits, nb = it["idx"], it["T"], it["hits"], it["nb"]
        # Labels come from the COLLAPSED advert list, which is in step with the
        # boundaries. Until 10 Oct 2026 this read the raw metadata list instead, so in
        # any break where same-instant records had been collapsed every later row
        # carried the unique_id and brand of the wrong advert: 7,213 of 25,208 ledger
        # rows in the full strict run (loss_audit.py recon, LABEL CHECK). Cut points
        # were never affected, only the label written beside them.
        ads = it["ads"]

        # --- Stage B is applied to every START first, so that a clip can end on the
        # NEXT advert's refined start. Refining starts while leaving ends on the
        # silence midpoint mixes two references: the clip length then carries the
        # next advert's shift as an error (measured 6 Sep 2026: 8 of 35 clips drifted
        # further from their scheduled slot length). Abutting refined starts makes the
        # clip length equal the scheduled duration by construction. ---
        starts, shifts, statuses, sources = [], [], [], []
        for i in range(nb):
            if hits[i] is None:
                # --- Filling a boundary the silence detector missed ---
                # The offset T is fitted from the boundaries that DID match, so once a
                # break is accepted the position of every advert in it is already known
                # from the schedule: position = scheduled offset within the break + T.
                # A silence match is a more precise confirmation of that position, not
                # the only way to obtain it. Dropping the whole break over one missing
                # silence discarded about half of all adverts (yield 13-93%, ~50% on
                # average, measured 12 Sep 2026 over 12 recordings).
                # These starts are still refined by Stage B, and are marked
                # start_source=schedule so their precision can be reported separately.
                if not fill_here:
                    starts.append(None)
                    shifts.append(0.0)
                    statuses.append("no-silence")
                    sources.append("")
                    continue
                s_a = it["bounds"][i][1] + T
                sources.append("schedule")
            else:
                s_a = mid(hits[i])
                sources.append("silence")
            s_b, shift, status = (s_a, 0.0, "off")
            if do_refine:
                s_b, shift, status = refine_start(
                    a.recording, s_a, a.refine_window, a.refine_threshold,
                    backend=refine_backend, min_scene_len=a.min_scene_len)
                if status.startswith("error"):
                    print(f"   [refine failed, falling back to silence midpoint] {status}")
                # --- Guard: a refined start must not run far past the silence ---
                # The end of the silence is when audio resumes, which comes from the
                # audio and owes nothing to the picture detector. An advert's first
                # frame cannot sit far beyond it, so a refinement that lands there
                # found a shot change INSIDE the advert, not its boundary. Measured
                # 19 Sep 2026 on b41_05 (Lidl, 20-00.mp4): the silence midpoint was
                # correct to one frame, and both backends pushed the cut 17 frames
                # into the advert, giving its opening to the previous clip. Over 361
                # cut points, reverting the 16-20 cuts that breach this guard cut the
                # mean slot deviation by about 30% (ffmpeg .195 -> .140, scenedetect
                # .208 -> .140) and raised the share within 0.2 s by 6-10 points.
                if (status == "refined" and hits[i] is not None
                        and a.max_past_silence_end is not None
                        and s_b > hits[i]["end"] + a.max_past_silence_end / 1000.0):
                    past_ms = (s_b - hits[i]["end"]) * 1000
                    print(f"   [reverted: refined start ran {past_ms:.0f} ms past the "
                          f"silence end] {os.path.basename(a.recording)} break {idx} pos {i + 1}")
                    s_b, shift, status = s_a, 0.0, "reverted-past-silence"
                    n_reverted += 1
                n_refined += status == "refined"
            starts.append((s_a, s_b))
            shifts.append(shift)
            statuses.append(status)

        for i in range(nb):
            if starts[i] is None:
                continue
            start_a, start_cut = starts[i]
            shift, status = shifts[i], statuses[i]
            if a.end_mode == "next-start" and i < nb - 1 and starts[i + 1] is not None:
                # abut on the next advert's refined start; the 1-3 frame tail loss
                # Stephen accepted comes from scene detection being marginally late
                end_cut = starts[i + 1][1]
            elif i < nb - 1 and hits[i + 1] is not None:
                end_cut = mid(hits[i + 1])
            elif i < nb - 1 and starts[i + 1] is not None:
                end_cut = starts[i + 1][1]
            else:
                # Last advert of a break: its end is the break-end transition, which the
                # schedule does not give. Use the START of that silence, not its midpoint.
                # The break->programme silence runs about two seconds, so its midpoint sits
                # roughly a second after the advert has actually finished: measured
                # 12 Sep 2026 over 40 break-final clips, median error 0.593 s, with
                # durations landing consistently 1.3-1.6 s above their scheduled slot.
                # Audio stops when the advert ends, so the silence start is the boundary.
                #
                # That rule takes the first silence starting --min-last seconds in, and a
                # pause inside the advert qualifies. Against the length the identifier
                # states (id_duration), 341 break-last clips of the 151-recording sample
                # (13 Oct 2026): 12.6% more than 5 s out, nearly all cut short (a 30 s
                # advert ended at 8-22 s); the rest end a median 0.40 s before the stated
                # length. --last-end stated / stated-silence use the stated length instead.
                stated = id_duration(ads[i].get("unique_id")) if i < len(ads) else None
                if a.last_end in ("stated", "stated-silence") and stated:
                    end_cut = start_cut + stated
                    if a.last_end == "stated-silence":
                        near = [s for s in silences if abs(s["start"] - end_cut) <= 2.0]
                        if near:
                            end_cut = min(near, key=lambda s: abs(s["start"] - end_cut))["start"]
                else:
                    floor = it["bounds"][i][1] + T + a.min_last
                    after = [s for s in silences if s["start"] > floor]
                    if not after:
                        print(f"[skip] {it['label']} advert {i + 1}: no end silence found")
                        continue
                    end_sil = min(after, key=lambda s: s["start"])
                    end_cut = mid(end_sil) if a.last_end == "midpoint" else end_sil["start"]
            dur = end_cut - start_cut
            if dur <= 0:
                continue
            ad = ads[i] if i < len(ads) else {}
            name = f"b{idx:02d}_{i + 1:02d}_{safe(ad.get('unique_id', '') + '_' + ad.get('brand', ''))}.mp4"
            out = os.path.join(a.outdir, name)
            planned += 1
            plan_rows.append({
                "recording": os.path.basename(a.recording),
                "break": idx, "pos": i + 1,
                "unique_id": ad.get("unique_id", ""), "brand": ad.get("brand", ""),
                # Sky AdSmart addressable slots carry their own start time, so they
                # survive the same-timestamp collapse and become clips of their own.
                # Flag rather than drop: the slot did broadcast something, but what it
                # broadcast differed per household, so it cannot be treated as one advert.
                "placeholder": "1" if is_placeholder(ad.get("unique_id")) else "",
                "start_silence_mid_s": f"{start_a:.3f}",   # Stage A cut point
                # The silence itself, as ffmpeg reported it. The END of the silence is
                # when audio resumes, i.e. an estimate of the advert's true start that
                # comes from the AUDIO and owes nothing to the picture detector. Stage B
                # can therefore be cross-checked at scale: a refined start that sits
                # systematically later than the silence end is a systematic drift, which
                # the slot-duration metric cannot see (it only measures differences).
                # Empty when the boundary was filled from the schedule (no silence).
                "silence_start_s": "" if hits[i] is None else f"{hits[i]['start']:.3f}",
                "silence_end_s": "" if hits[i] is None else f"{hits[i]['end']:.3f}",
                "start_s": f"{start_cut:.3f}",             # cut point actually used (after Stage B)
                "shift_ms": f"{shift * 1000:.0f}",
                "refine_status": status,
                "refine_backend": refine_backend if do_refine else "",   # thresholds differ per backend
                "min_scene_len": "" if a.min_scene_len is None else a.min_scene_len,
                "start_source": sources[i],   # silence = matched; schedule = filled from T
                # How many breaks match completely under the recording start this break
                # was placed by (--align recording). Empty = placed on its own fit, with
                # no other break to vouch for the position.
                "start_confirmed_by": it.get("confirmed_by", ""),
                "end_s": f"{end_cut:.3f}", "duration_s": f"{dur:.3f}",
                "break_last": "1" if i == nb - 1 else "",   # different end rule; worse precision
                "clip": name,
            })
            tag = f"  +{shift * 1000:.0f}ms" if shift else ""
            print(f"{name}  [{start_cut:8.2f} -> {end_cut:8.2f}]  {dur:5.1f}s{tag}")
            if is_placeholder(ad.get("unique_id")):
                continue                    # in the ledger, flagged; not a clip of the corpus
            if not a.dry_run:
                if cut(a.recording, start_cut, dur, out):
                    made += 1
                else:
                    print(f"   FAILED: {name}")

    tail = "  (dry-run, nothing written)" if a.dry_run else f"; cut {made}"
    print(f"\nkept {len(kept)} breaks; planned {planned} clips{tail}")
    if do_refine:
        print(f"refinement: {n_refined}/{planned} cut points pushed forward "
              f"(the rest were already clean; no scene change means no change)")
        if a.max_past_silence_end is not None:
            print(f"guard: {n_reverted} refinement(s) reverted for running more than "
                  f"{a.max_past_silence_end:.0f} ms past the silence end")
    n_sched = sum(1 for r in plan_rows if r.get("start_source") == "schedule")
    if plan_rows:
        print(f"start provenance: {len(plan_rows) - n_sched} from a matched silence, "
              f"{n_sched} filled from the schedule "
              f"({100 * n_sched / len(plan_rows):.0f}% filled)")
    if not a.dry_run:
        print(f"output folder: {a.outdir}")

    # The cut ledger feeds the QC page and the shift-distribution statistics
    if a.cut_csv and plan_rows:
        with open(a.cut_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(plan_rows[0].keys()))
            w.writeheader()
            w.writerows(plan_rows)
        print(f"cut ledger: {a.cut_csv} ({len(plan_rows)} rows)")


if __name__ == "__main__":
    main()
