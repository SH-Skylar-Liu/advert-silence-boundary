# Measurement log — August–September 2026

Full record of what was measured, including the readings that turned out to be wrong.
Source of record: `Academic/09_Daily_Research_Log/2026-08-21_上线核查_实验报告.md` (vault).

---

## 1. Delivered corpus (measured 21 Aug 2026)

| Quantity | Value | Against the delivery note |
|---|---|---|
| Source files | 1,670 mp4 + 1,670 metadata json | note said 1,671 |
| Ad breaks | 11,254 | exact match |
| Advert records (airings) | 97,213 | exact match |
| Unique adverts by `unique_id` | 19,926 (79.5% repeats) | not stated |
| Advert duration, mean / median | 24.98 s / 30.00 s (n = 62,526) | estimate had used 20 s |
| Mean duration of unique adverts | 26.25 s | — |
| Duration mix | ~30 s 45.9% · ~20 s 26.6% · ~10 s 21.4% · ~60 s 3.1% | — |
| Channels | 5STAR 42,263 · CH4 15,960 · ITV1 HD 13,062 · Channel 5 11,560 · ITV1 4,257 · Film4 2,372 · ITV4 1,980 · ITV2 1,834 | strongly unbalanced |
| Date range | 28/11/2020 – 31/12/2025, 173 distinct dates | — |
| Video | H.264 1024×576, 25 fps, AAC audio; multiple video and data streams per file | — |

Durations are derived, not stored: the metadata carries advert start times but no
duration field, so a duration is the gap to the next advert in the same break. The last
advert of each break therefore has none.

**The re-estimate moved in the unhelpful direction.** A measured mean of 26.25 s is
longer than the 20 s assumption, so the sequential whole-archive estimate rose from
about 76 days to about 100. What actually reduced it was concurrency (§3).

## 2. Sky AdSmart placeholder records

23,239 advert records share a start time with the preceding record. 20,812 of those are
Sky AdSmart addressable-advertising placeholders (`unique_id` = `SKYADSMART000`), and
they appear **only on 5STAR (16,851) and Channel 5 (3,961)** — both Sky-operated.

| | |
|---|---|
| Records after collapsing same-timestamp entries | 97,213 → 73,974 |
| Unique adverts excluding placeholders | **19,503** |
| Mean duration of unique adverts | 26.25 s (essentially unchanged) |

Two loose ends: about 2,400 zero-gap records do **not** involve AdSmart, and collapsing
costs roughly 420 genuine adverts that share a timestamp with another genuine advert
(~2%). The current rule — keep one record per instant, preferring the non-placeholder —
is lossy and should eventually be replaced by one that retains and flags both.

This matters beyond data cleaning. Where addressable advertising is in use, "what was
broadcast on this channel at this time" no longer has a single answer: different
households saw different adverts in the same slot. Any channel- or slot-level claim
about the corpus has to be qualified accordingly from 2020 onward.

## 3. Inference throughput (measured 22 Aug 2026)

Qwen3.8-27B-NVFP4, one H100, 2 fps sampling, eight adverts per run,
`vllm_caption.py bench`:

| Workers | wall (s) | throughput | effective RTFX | speed-up | tok/s |
|---|---|---|---|---|---|
| 1 | 375.8 | 0.45× | 2.199 | 1.00× | 46.4 |
| 2 | 212.6 | 0.80× | 1.244 | 1.78× | 78.0 |
| 4 | 109.8 | 1.56× | 0.642 | **3.47×** | 149.2 |
| 8 | 100.2 | 1.71× | 0.586 | 3.80× | 179.9 |

Single-worker effective RTFX of 2.199 is close to the 2.45 reported by the reference
implementation, which is what makes these figures comparable with it.

Four workers is the knee. Whole archive (134,000 unique adverts × 26.25 s ≈ 977 h of
material): **about 24 days** at the best measured throughput, against 76 days assumed
sequentially. This batch (19,503 unique adverts, 142 h) needs about 83 GPU hours.

Not yet measured: 12 and 16 workers; and the shot-by-shot (`--timestamps`) output mode,
which produces far more tokens — the 2.45 reference figure was probably measured in that
mode, so the two modes must not share one RTFX.

## 4. A reading that was withdrawn

The first end-to-end test used `2020-01-30_ITV2_21:44:58.mp4` and matched only 4 of 14
boundaries. Thresholds from −35 to −25 dB changed the silence count from 10 to 371 but
not the hit count, which suggested the silence cue was simply absent — and, by extension,
that the method's validity varied by channel and era.

Sampling ten delivered files showed that this recording is atypical: the other nine run
1–2.5 hours and carry 28–88 adverts across several breaks, while this one is 7 min 45 s
with a single break. **The generalisation was withdrawn.** Why that file behaves as it
does is still unresolved, and `survey_silence.py` (stratified by channel × year) exists
to settle the general question properly rather than from one sample.

Two smaller observations from the same check: the fitted offset for that file was
consistent at ≈101.7 s across two parameter settings, meaning the recording does **not**
begin at the break — the timestamp in the filename is the break's wall-clock time, not
the file's start. And 127 of the 1,670 files (7.6%, 212 GB) are byte-identical in size to
another file; the five sharing one size are all the same date and channel with different
break timestamps, i.e. one recording duplicated per break. Batch processing should group
by unique recording rather than decode the same material repeatedly.

## 5. End-to-end run (10 Sep 2026)

Recording `2020-08-29_ITV1 HD_09:39:00.mp4`, 7,865 s, 8 breaks, 67 adverts,
`silencedetect` at −40 dB / 0.2 s → 908 silent segments.

Per-break hit rates: 4/7, 9/9, 9/9, 7/8, 7/9, 8/8, 7/8, 9/9 — 60/67 = 90% overall.
**That 90% is inflated.** The fitted offsets are not monotonic (1452 → 1814 → 2815 →
1331 → 1280 → 5651 → 1453 → 7628), and offset must increase with break order; with 908
silences in the recording, a wrong offset can still score well by chance. The
offset-consistency filter in `cut_adverts.py` drops those breaks.

After filtering: four breaks kept, **35 clips planned from 67 adverts (52% yield)**.

Clip durations land on the standard slot lengths within a few tenths of a second, which
is independent evidence that the surviving alignments are correct: a mis-fitted offset
would not produce durations that sit on the market's slot grid.

Stage B moved **23 of 35** cut points, by 45–787 ms — the same magnitude as the reference
implementation's measurements (120 ms and 800 ms). The four break-opening adverts moved
by 750/764/785/787 ms, consistently, because the programme→break silence runs about two
seconds and its midpoint therefore sits ~0.75 s before the true first frame.

### Ablation (`compare_end_modes.py`)

Same 35 cut points; only the definition of a clip's endpoints differs.

| Variant | median | mean | max | <0.2 s |
|---|---|---|---|---|
| A — silence midpoint → next midpoint | 0.206 s (≈5 frames) | 0.277 s | 0.943 s | 49% |
| B — refined start → next midpoint | 0.065 s (≈1.6 frames) | 0.195 s | 0.993 s | 69% |
| **C — refined start → next refined start** | **0.022 s (≈0.55 frames)** | **0.138 s** | **0.702 s** | **77%** |

B improves the median but worsens the maximum: refining starts while leaving ends on the
silence midpoint mixes two references, so a clip's length carries the *next* advert's
shift as error. Eight of 35 clips drifted further from their slot length for exactly that
reason. C eliminates it by construction, and is now the default (`--end-mode next-start`).

The ablation required no re-cutting: the ledger stores the Stage A midpoint and the
refined point for every clip, so all three variants are arithmetic over one CSV.

## 5b. Independent reproduction from a clean clone (10 Sep 2026)

The repository was cloned into an empty directory on the BFI workstation
(`/home/shihan/storage/code/advert-silence-boundary`) and the pipeline re-run from
scratch, with no files carried over from the working copy.

| Step | Result |
|---|---|
| Ablation self-test on the bundled example ledger (no archive data) | passed, identical to the development machine |
| Ledger for `2020-08-29_ITV1 HD_09:39:00.mp4` | 35 clips, 23 cut points refined |
| A / B / C ablation | 0.206 s / 0.065 s / **0.022 s**, A→C **9.4×** |

All three figures match those measured on the development machine between 6 and 10
September. The bundled synthetic ledger means the ablation can be verified without
access to the archive at all, which is what makes the result checkable by a reviewer
who has no BFI data.

## 6. Environment constraints (BFI workstation, as of 10 Sep 2026)

- The vLLM service runs under a different service account; it cannot be restarted from
  the project account. `--allowed-local-media-path` and the 2 fps sampling rate are
  server-side, so clips must be placed inside the whitelisted directory and the frame
  rate cannot be varied without help.
- `enable_thinking`, by contrast, is a client-side parameter, so that ablation is
  available without a restart.
- The delivered virtualenv is not executable from the project account, and a second one
  is broken (dangling `python3` symlink). PySceneDetect is present under `~/.local` but
  OpenCV is not, and Python 3.14 may have no OpenCV wheel — hence the ffmpeg backend.
- ffmpeg 9.0.1 and mpv are available; frame-accurate manual checking is possible.

## 7. Open items

1. **Yield.** Fill missed boundaries from scheduled durations and flag them; expected to
   lift 52% substantially.
2. **Threshold calibration.** Cross-check the ffmpeg scene threshold (0.3) frame by frame
   against the reference PySceneDetect script on the same adverts.
3. **Validity survey.** Run `survey_silence.py` across channel × year and report hit-rate
   distribution; this decides where the silence route applies and where a fallback is
   needed.
4. **Duplicate recordings.** Group the 7.6% duplicates before batch processing.
5. **The atypical 7-minute file.** Establish why it matches so poorly.
