# Advert boundary detection — silence + scene-change pipeline

**Version 1.2** (15 Sep 2026) · English · [中文](README.zh-CN.md)

> ⚠️ **PRIVATE REPOSITORY — INTERNAL PATHS INCLUDED.**
> Commands and defaults in this repository refer to real BFI server paths and account
> names so that a collaborator can reproduce a run without translation. **Before this
> repository is made public** (SSH Open Marketplace, JOHD, or any external release),
> replace every `/home/shihan/...` path, account name and hostname with placeholders.

Deterministic segmentation of individual television adverts from full-programme
recordings, using the broadcast schedule as a prior. No machine learning is used to
find the boundaries: `ffmpeg` detects the absence of audio, and a scene-change filter
locates the first frame. A vision-language model is applied only afterwards, to
describe the content of each extracted advert.

That separation is the point. Corpus construction is deterministic and reproducible;
interpretation is where the model enters.

---

## Method

**Stage A — coarse location.** `ffmpeg silencedetect` finds every silent segment in the
recording. The schedule (advert start times and their ordering within a break) is then
slid across those silences to find the offset `T` at which the schedule's duration
pattern lands on detected silences. This joining of schedule to silence is what makes
the method work; silence detection on its own produces too many false positives.

**Break acceptance.** Each break's fitted offset must be consistent with the neighbouring
breaks, because a long recording contains enough silences that a wrong offset can still
score well by chance. A break is then accepted when at least half of its boundaries
(`--min-match-rate 0.5`), and at least three (`--min-matches 3`), landed on a silence.
Boundaries that found no silence are filled from the schedule and the fitted offset,
and flagged in the ledger as `start_source=schedule`. The earlier all-or-nothing rule
is still available as `--min-match-rate 1 --no-fill-missing`.

**Stage B — first-frame refinement.** A silence midpoint typically sits a few frames
before the true first frame, so the previous advert bleeds into the start of the clip.
Each start is therefore pushed forward to the first scene change within the following
**one second**. A wider window starts catching cuts inside the advert itself.

**Clip boundaries.** A clip runs from its own refined start to the **next advert's
refined start** (`--end-mode next-start`, the default). Ending on the silence midpoint
instead (`--end-mode midpoint`) mixes two references, so clip length absorbs the next
advert's shift as error. The **last advert of a break** has no following advert; it
ends at the **start** of the break-end silence (`--last-end silence-start`), because
the midpoint of that ~2 s transition sits about a second after the advert has stopped.

**Two detector backends.** `--backend ffmpeg` uses ffmpeg's `select='gt(scene,0.3)'`
filter and needs no Python packages. `--backend scenedetect` uses PySceneDetect's
`ContentDetector` and needs OpenCV. `--backend auto` picks scenedetect when it can be
imported. **All published figures below use the ffmpeg backend.** With its default
`min_scene_len`, the scenedetect backend makes results worse than no refinement at all
(see below); do not use it without `--min-scene-len`.

---

## Measured results

**Sample:** 12 recordings from 29–30 Aug 2020, four channels (ITV1 HD, CH4, Channel 5,
ITV2), 361 cut points. Run `R0` on 15 Sep 2026, all-or-nothing break acceptance
(`--min-match-rate 1 --no-fill-missing`), dry run.

### Cut accuracy

UK television advertising is sold in fixed slot lengths, so the scheduled duration of
every clip is known in advance. The error of a cut can therefore be measured without
manual frame inspection: the absolute difference between the cut clip's duration and the
nearest slot length. The slot grid comes from the advertising market, not from the
detector, so this is an independent check rather than self-confirmation.

| Variant | median | mean | max | <0.2 s |
|---|---|---|---|---|
| A — silence midpoint → next midpoint | 0.122 s (3.0 frames) | 0.241 s | 2.369 s | 60% |
| B — refined start → next midpoint | 0.077 s (1.9 frames) | 0.205 s | 2.395 s | 69% |
| **C — refined start → next refined start** | **0.042 s (1.0 frame)** | **0.195 s** | 2.395 s | **72%** |

**A → C is a 2.9× improvement in median error.** The estimate was stable between 8 and
12 recordings. An earlier single-recording figure of about 9× was a sample effect and
should not be cited.

Refinement is not a uniform gain. On recordings whose silence midpoints were already
close (median A error around 0.05 s), B and C are identical; on recordings with a large
midpoint error (around 0.15–0.21 s), C brings the median down to 0.020–0.024 s. On one
recording B was worse than A, which is why both ends of a clip must use the same kind of
point.

The mean stays roughly five times the median. A minority of cuts are far out for
reasons refinement cannot address (alignment errors, or adverts that are not sold at a
standard slot length). This has not yet been diagnosed.

### Detector backend

Same 12 recordings, same rules, only the refinement backend changed:

| Run | Backend | refined | C median | C <0.2 s | shift of refined cuts |
|---|---|---|---|---|---|
| R0 | ffmpeg, threshold 0.3 | 156 | **0.042 s** | 72% | 41–988 ms, median 211 ms; 118 below 600 ms |
| R1 | scenedetect, defaults | 47 | 0.146 s | 57% | 597–988 ms, median 785 ms; 1 below 600 ms |

With defaults, PySceneDetect is **worse than no refinement** (A median 0.122 s). The
cause is `ContentDetector`'s default `min_scene_len=15` frames: combined with treating
the window start as the start of a scene, no cut in the first 0.6 s of the window can be
reported. That is exactly where the true first frame usually lies, so the small
corrections are lost and the cuts that remain fall inside the advert.
`--min-scene-len 1` removes this constraint; its measured effect (`R2`) is pending.

### Yield

Under all-or-nothing acceptance the 12 recordings yield 361 of 706 scheduled adverts
(51%), ranging from 13% to 93% per recording, even between time slots of the same
channel on the same day. The fill-from-schedule rule (default since v1.1) exists to
address this; its yield and the precision of `start_source=schedule` cuts have **not
yet been measured** on the server.

### Inference throughput

Qwen3.8-27B-NVFP4 on one H100, video captioning at 2 fps:

| Concurrency | throughput | effective RTFX | speed-up |
|---|---|---|---|
| 1 | 0.45× real time | 2.199 | 1.00× |
| 2 | 0.80× | 1.244 | 1.78× |
| 4 | 1.56× | 0.642 | **3.47×** |
| 8 | 1.71× | 0.586 | 3.80× |

Four workers is the knee; eight is saturated, so the bottleneck is the GPU rather than
the server's concurrency limit. Whole archive (134,000 unique adverts × 26.25 s ≈ 977 h
of material): about **24 days**, against 76 assumed sequentially.

---

## Repository layout

```
src/
  csv_to_metadata.py      schedule CSV -> metadata JSON (break grouping, advert starts)
  silence_detect.py       Stage A: silencedetect + schedule alignment (importable)
  refine_first_frame.py   Stage B: first scene change within 1 s, two backends
  cut_adverts.py          one recording: align, refine, plan/cut clips, write the ledger
  batch_cut.py            many recordings: parallel, resumable, one ledger per recording
  compare_end_modes.py    ablation over one or more ledgers: variants A / B / C
  survey_silence.py       stratified sample by channel x year; where does the cue hold?
  vllm_caption.py         captioning client + concurrency benchmark (standard library only)
examples/
  example_cut_ledger.csv  synthetic ledger so the ablation runs without archive data
docs/
  results_2026-09.md      full measurement log, including what failed
```

## Requirements

`ffmpeg` and `ffprobe` on `PATH`, and Python 3.10+. **The ffmpeg backend needs no Python
packages.**

The scenedetect backend needs PySceneDetect and OpenCV. Install the headless OpenCV build
only, into a virtual environment on local disk (imports stall when a venv lives on NFS):

```bash
uv venv ~/venv-scd --python 3.13
uv pip install --python ~/venv-scd/bin/python scenedetect==0.7.1 opencv-python-headless==5.0.0.93
```

Never install `opencv-python` and `opencv-python-headless` into the same environment.

## Reproducing

### One recording

```bash
# 1. schedule CSV -> metadata JSON (only needed if you do not already have one)
python3 src/csv_to_metadata.py "2024-03-26_ITV1HD.csv" -o breaks.json

# 2. inspect the silence structure of a recording, and how the schedule aligns to it
python3 src/silence_detect.py RECORDING.mp4 --metadata breaks.json

# 3. plan the cuts without writing any video, and save the ledger
python3 src/cut_adverts.py RECORDING.mp4 --metadata breaks.json \
    --outdir /tmp/cuts --cut-csv plan.csv --backend ffmpeg --dry-run

# 4. reproduce the A/B/C ablation from that ledger (no re-decoding needed)
python3 src/compare_end_modes.py plan.csv --per-clip

# 5. cut for real once the plan looks right
python3 src/cut_adverts.py RECORDING.mp4 --metadata breaks.json \
    --outdir /path/to/output --cut-csv plan.csv --backend ffmpeg
```

The ablation runs out of the box on the bundled example:

```bash
python3 src/compare_end_modes.py examples/example_cut_ledger.csv
```

### The R0 run above

```bash
cd src
python3 batch_cut.py ~/run_step0/src --outroot ~/run_step0/R0_ffmpeg \
    --backend ffmpeg --min-match-rate 1 --no-fill-missing --jobs 8 --dry-run

# pool the per-recording ledgers, then run the ablation once
awk 'FNR==1 && NR!=1 {next} 1' ~/run_step0/R0_ffmpeg/*/cut_plan.csv > R0_ffmpeg.csv
python3 compare_end_modes.py R0_ffmpeg.csv
```

For R1 use `--backend scenedetect`; for R2 add `--min-scene-len 1`. Both need the venv
python (`~/venv-scd/bin/python`). Pooled ledgers need `compare_end_modes.py` from v1.2 or
later: earlier versions grouped rows by break number alone, so breaks from different
recordings were merged.

On the BFI workstation the source recordings and their metadata are in
`/home/shihan/storage/video/unique_advert_source_videos/`, paired by filename
(`YYYY-MM-DD_CHANNEL_HH:MM:SS.mp4` and `..._metadata.json`). These filenames contain
colons, which are legal on Linux but not on Windows.

## Design notes worth keeping

**The ledger records raw and derived values side by side.** `--cut-csv` writes the
Stage A midpoint (`start_silence_mid_s`), the refined cut point (`start_s`), how the
start was obtained (`start_source`) and which detector settings refined it
(`refine_backend`, `min_scene_len`). Because of that, the A/B/C ablation needs no
re-cutting and no re-decoding, and every figure can be traced to the settings that
produced it.

**Forced settings fail loudly.** `--backend scenedetect` without an importable
scenedetect, or `--min-scene-len` with the ffmpeg backend, stops the run instead of
silently falling back. A silent fallback would produce a ledger that looks like a valid
run of the requested configuration.

**Video is always transcoded, never stream-copied.** Stream copy leaves
`duration`/`source_duration` inconsistent in the MP4, which breaks frame/time alignment
in downstream vLLM inference. Clips are also mapped explicitly (`-map 0:v:0 -map 0:a:0`)
because the delivered files carry more than one video stream plus data streams.

## Known limitations

- **The scenedetect backend is not yet validated.** Defaults are harmful (R1); the effect
  of `--min-scene-len 1` is pending (R2). Until then, use `--backend ffmpeg`.
- **The ffmpeg scene threshold (0.3) has not been calibrated frame by frame**; the
  evidence for it is the duration convergence above.
- **Fill-from-schedule is unmeasured.** It is the default, but its yield gain and the
  precision of filled cuts have not been measured. Report `start_source=schedule` cuts
  separately.
- **The silence cue is not uniformly present.** One 2020 ITV2 recording matched only
  4 of 14 boundaries at any threshold from −35 to −25 dB. Whether this is a property of
  the channel, the era, or that particular file is unresolved; `survey_silence.py`
  exists to answer it by stratified sampling.
- **Schedule records are not one-to-one with what was broadcast.** About 21% of the
  advert records in the delivered metadata are Sky AdSmart addressable-advertising
  placeholders sharing a timestamp with a real advert (only on 5STAR and Channel 5).
  `silence_detect.py` collapses same-timestamp records when building boundaries, keeping
  the real advert; the collapse is lossy for the ~2% of cases where two real adverts
  share a timestamp.

## Changelog

**1.2 — 15 Sep 2026**
- `--min-scene-len` for the scenedetect backend (unset keeps PySceneDetect's default);
  recorded in the ledger. Rejected with the ffmpeg backend.
- `compare_end_modes.py` groups by recording and break, so pooled ledgers are correct.
- README split into English and Chinese versions; results replaced by the pooled
  12-recording figures and the backend comparison.

**1.1 — 14 Sep 2026**
- `--backend auto|ffmpeg|scenedetect`; forcing scenedetect without OpenCV fails loudly;
  `refine_backend` column in the ledger.
- Break acceptance by match rate, with missing boundaries filled from the schedule
  (`--min-match-rate`, `--min-matches`, `--no-fill-missing`; `start_source` column).
- `batch_cut.py` passes all of the above through to `cut_adverts.py`.

**1.0 — 10–12 Sep 2026**
- Silence + scene-change pipeline, batch driver, A/B/C ablation.
- Break-final clips end at the silence start rather than its midpoint.
