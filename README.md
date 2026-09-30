# Advert boundary detection — silence + scene-change pipeline

**Version 1.7** (20 Sep 2026)

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


**Headline result.** Across all 1,670 delivered source recordings: 24,944 cut points,
median deviation from the nearest advert slot length **0.041 s (1.0 frame)**, **74%**
within 0.2 s. Strongest on 2022–2024 material; degrades from 2025. Details, ablations
and caveats in [docs/method.md](docs/method.md).

---

## Documentation

| File | Contents |
|---|---|
| [docs/method.md](docs/method.md) | Method, measured results, batch reproduction, design notes, known limitations |
| [docs/results_2026-09.md](docs/results_2026-09.md) | Full measurement log, including what failed |
| [docs/pipeline_explainer.html](docs/pipeline_explainer.html) | Single-page interactive walkthrough (open in a browser) |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

## Repository layout

```
src/
  # pipeline
  csv_to_metadata.py      schedule CSV -> metadata JSON (break grouping, advert starts)
  silence_detect.py       Stage A: silencedetect + schedule alignment (importable)
  refine_first_frame.py   Stage B: first scene change within 1 s, two backends
  cut_adverts.py          one recording: align, refine, plan/cut clips, write the ledger
  batch_cut.py            many recordings: parallel, resumable, one ledger per recording
  # evaluation
  compare_end_modes.py    ablation over one or more ledgers: variants A / B / C
  compare_runs.py         two runs compared per recording, plus the silence-end check
  silence_stats.py        silence lengths vs. the one-second refinement window
  frame_strip.py          every frame around a cut point as one page, for ground truth
  survey_silence.py       stratified sample by channel x year; where does the cue hold?
  # captioning
  vllm_caption.py         captioning client + concurrency benchmark (standard library only)
examples/
  example_cut_ledger.csv  synthetic ledger so the ablation runs without archive data
docs/                     see the table above
```

The scripts import each other by plain module name, so run them from `src/` or as
`python3 src/<script>.py`; do not move them into subfolders.

## Requirements

`ffmpeg` and `ffprobe` on `PATH`, and Python 3.10+. **The ffmpeg backend needs no Python
packages.**

The scenedetect backend needs PySceneDetect and OpenCV. Install the headless OpenCV build
only, into a virtual environment on local disk (imports stall when a venv lives on NFS):

```bash
uv venv ~/venv-scd --python 3.13
uv pip install --python ~/venv-scd/bin/python -r requirements.txt
```

[requirements.txt](requirements.txt) pins `scenedetect==0.7.1` and
`opencv-python-headless==5.0.0.93`.

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

Batch runs over many recordings, and the exact commands behind the published figures,
are in [docs/method.md](docs/method.md#reproducing-the-batch-runs).

---

Developed by Shihan Liu, King's College London × BFI collaborative PhD.
