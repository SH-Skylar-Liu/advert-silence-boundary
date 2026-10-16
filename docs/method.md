# Method, results and design notes

Detailed companion to the [README](../README.md). Version history is in
[CHANGELOG.md](../CHANGELOG.md). Current figures, with the basis and status of each, are
in [results_2026-10.md](results_2026-10.md); the August–September measurement log,
including readings that turned out to be wrong, is kept in
[results_2026-09.md](results_2026-09.md).

Every option added in October 2026 is off by default, so a command written before then
still reproduces the run it was written for. Which options each run used is listed under
[Settings used for each run](#settings-used-for-each-run).

---

## Method

**Stage A — coarse location.** `ffmpeg silencedetect` finds every silent segment in the
recording. The schedule (advert start times and their ordering within a break) is then
slid across those silences to find the offset `T` at which the schedule's duration
pattern lands on detected silences. This joining of schedule to silence is what makes
the method work; silence detection on its own produces too many false positives.

**Break acceptance.** Each break's fitted offset must be consistent with the neighbouring
breaks (`--offset-tol 5`, seconds), because a long recording contains enough silences
that a wrong offset can still score well by chance. **The thesis uses the strict rule: a
break is kept only if every one of its boundaries falls on a silence** (`--min-match-rate
1 --no-fill-missing`).

The code still defaults to a looser rule, kept for comparison: a break is accepted when at
least half of its boundaries (`--min-match-rate 0.5`), and at least three
(`--min-matches 3`), landed on a silence, and the missing boundaries are filled from the
schedule and flagged `start_source=schedule`. It was tested on a sample of 151 recordings
in October 2026 and rejected. Each break was looked for again five and seven minutes away
from where it belongs, inside the programme; the share of breaks that matched there by
accident rose from 2.4% under the strict rule to 16% under the half rule, for about a
hundred more slots ([results_2026-10.md](results_2026-10.md#rejected-the-looser-acceptance-rule)).

**Alignment by recording** (`--align recording`, off by default; 11 Oct 2026). The default
(`--align break`) fits each break on its own and drops a break whose offset disagrees with
the break kept before it, so one break placed wrongly can bring down every correct break
after it. In a continuous recording every break must share one offset. With
`--align recording` the start of the recording is fixed only where at least two fully
matched breaks agree on it (the ledger records `start_confirmed_by`), and each break is
then checked only within `--offset-tol` of where it must be. Recordings with no such
agreement fall back to `--align break`. Used in the October full run (v17).

**Schedule cleaning** (`--clean-schedule`, `--own-channel`, off by default; 12–13 Oct 2026).
`--clean-schedule` repairs how the metadata groups records into breaks before alignment:
breaks that start at the same second are merged; addressable-advertising placeholder
records (`SKYADSMART…`, `C4ADDRESSABLE`, `ITVADDRESSABLE`) are dropped where a real advert
starts at the same time, and otherwise keep their boundary but give no clip
(`placeholder=1`); a break is split where the next advert starts more than 30 s after the
previous one should have ended, going by the length in its identifier (two minutes where
no length is given). `--own-channel` keeps, in a file whose break numbers fall into
separate runs, only the run that contains the time in the file name; the other run belongs
to another channel. This last rule is provisional and has not been confirmed with the BFI.

**Length from the identifier.** The last three digits of an advert identifier are its length
in seconds. This is inferred from the data, not from documentation: where an advert is
followed by another in its break, the next starts exactly that many seconds later in 99.6%
of 62,001 cases.

**Stage B — first-frame refinement.** A silence midpoint typically sits a few frames
before the true first frame, so the previous advert bleeds into the start of the clip.
Each start is therefore pushed forward to the first scene change within the following
**one second**. A wider window starts catching cuts inside the advert itself. This is the
default, and the setting of the September 2026 run.

Three options added on 13 Oct 2026 change where Stage B looks. All are off by default;
they were tested on the sample of 151 recordings and on 19 frame-checked cut points, then
used in the October full run (v17):

- `--refine-from silence` searches the whole silence instead of only forward from its
  midpoint, because in 8 of 19 frame-checked cut points the first frame of the new advert
  came one to five frames *before* the midpoint. Silences longer than `--refine-window`
  keep the default search.
- `--refine-mute-frames 6` (with `--refine-from silence`) starts that search six frames
  into the silence, less the two-frame tolerance. The six frames are not fitted: the
  Clearcast Library upload specification asks for 6 frames of audio silence at the start
  and end of each commercial, so a picture change in the first frames of the silence still
  belongs to the previous advert. Not applied to the first boundary of a break, which
  follows a programme rather than an advert.
- `--correct-video-start` (scenedetect only) allows for a video stream that starts later
  than the audio. In every 2026 recording and in 246 of 302 from 2025 the video stream
  starts up to 2.84 s after the audio; no recording from 2020–2024 does. PySceneDetect counts
  from the first video frame while every other step keeps container time, so on these files
  Stage B had been looking about a second late. This, not the material itself, is why
  refinement appeared to fail on 2025–2026 recordings.

**Clip boundaries.** A clip runs from its own refined start to the **next advert's
refined start** (`--end-mode next-start`, the default). Ending on the silence midpoint
instead (`--end-mode midpoint`) mixes two references, so clip length absorbs the next
advert's shift as error. The **last advert of a break** has no following advert; it
ends at the **start** of the break-end silence (`--last-end silence-start`), because
the midpoint of that ~2 s transition sits about a second after the advert has stopped.

This rule has a known fault, still open. The break-end silence is taken to be the first
silence that begins at least `--min-last` (8) seconds after the clip starts, and a pause
inside the advert can be taken for it: in the September run 16% of the 2,899 last clips are
more than five seconds shorter than their stated length. `--last-end stated` (start plus
the length in the identifier) and `--last-end stated-silence` (the silence starting within
2 s of that point) exist in the code but **have not been tested**.

**Silence guard.** A refinement that lands more than `--max-past-silence-end` ms
(default 80, two frames at 25 fps) past the **end** of the silence is reverted to the
Stage A midpoint and flagged `reverted-past-silence`. The silence end is where audio
resumes: it comes from the audio and owes nothing to the picture detector, so an
advert's first frame cannot sit far beyond it, and a refinement that lands there has
found a shot change inside the advert. Pass `0` to reproduce runs from before
19 September 2026.

**Two detector backends.** `--backend ffmpeg` uses ffmpeg's `select='gt(scene,0.3)'`
filter and needs no Python packages. `--backend scenedetect` uses PySceneDetect's
`ContentDetector` and needs OpenCV. `--backend auto` picks scenedetect when it can be
imported. **Use scenedetect with `--min-scene-len 1`**: at PySceneDetect's default of
15 frames no cut in the first 0.6 s of the window can be reported, which is where the
true first frame usually lies, and results come out worse than no refinement at all.

---

## Measured results

### Across the delivered corpus (September 2026 run)

The October full run (v17) supersedes these figures: 94.5% of clips within 0.2 s of their
stated length, last clips aside, and 81.6–84.7% in every year
([results_2026-10.md](results_2026-10.md#latest-full-run-v17)). The September figures
below are kept because Chapter 4 reports them.

All 1,670 delivered source recordings, run 20 September 2026 with the settings listed under
[Settings used for each run](#settings-used-for-each-run): **25,208 cut points**. The main
measure, adopted in October 2026, compares each clip with the length stated in its own
identifier; 24,872 clips have one (the other 336 open a break whose first record is a
placeholder). Median difference **0.042 s (about one frame)**, **73.9%** within 0.2 s;
leaving aside the last clip of each break, **83.0%**. Against the nearest standard slot
length, the earlier measure, the figures are almost the same (0.042 s, 74.2%). Report the
median and the share within 0.2 s rather than the mean: 57 clips (0.2%) run longer than
120 s and drag the mean away from the ordinary case. The ledger identifiers were corrected
from the schedule before this was computed (`loss_audit.py precision --relabel`); 7,929 of
the 25,208 rows had been labelled wrongly, which does not change the cut points.

| Year | clips with a stated length | median | ≤0.2 s | refined | reverted by the guard |
|---|---|---|---|---|---|
| 2020 | 1,979 | 0.042 s | 78.1% | 40.6% | 7.1% |
| 2021 | 4,059 | 0.058 s | 76.6% | 32.4% | 8.1% |
| 2022 | 3,147 | 0.023 s | 80.2% | 66.9% | 5.4% |
| 2023 | 3,512 | 0.002 s | 82.4% | 73.4% | 3.0% |
| 2024 | 4,651 | 0.000 s | 82.0% | 73.7% | 2.2% |
| **2025** | 4,839 | **0.130 s** | **60.3%** | **29.2%** | **18.5%** |
| **2026** | 2,685 | **0.140 s** | **58.8%** | **21.4%** | **22.6%** |

(The refined and reverted shares are over all cut points of the year.) The 2025–2026
drop was first read as a change in the material, with adverts opening without a hard cut.
That reading was wrong. In October 2026 it was traced to the files: their video stream
starts later than the audio, and Stage B was looking at the wrong frames (see
`--correct-video-start` above). The sample results with the correction are in
[results_2026-10.md](results_2026-10.md#stage-b-search-the-silence-and-allow-for-the-video-start).

The last clip of each break is reported separately: only 5.5% of the 2,899 last clips are
within 0.2 s of their stated length, and 16% are more than five seconds short (see *Clip
boundaries* above).

### Development sample (September 2026)

The tables below come from the development sample: 12 recordings from 29–30 Aug 2020,
four channels (ITV1 HD, CH4, Channel 5, ITV2), 361 cut points; strict break acceptance
(`--min-match-rate 1 --no-fill-missing`), dry runs, 15–19 Sep 2026. They measure against
the nearest standard slot length.

### Cut accuracy

UK television advertising is sold in fixed slot lengths, so the scheduled duration of
every clip is known in advance. The error of a cut can therefore be measured without
manual frame inspection: the absolute difference between the cut clip's duration and the
nearest slot length. The slot grid comes from the advertising market, not from the
detector, so this is an independent check rather than self-confirmation.

| Variant | median | mean | max | <0.2 s |
|---|---|---|---|---|
| A — silence midpoint → next midpoint | 0.122 s (3.0 frames) | 0.241 s | 2.369 s | 60% |
| B — refined start → next midpoint | 0.065 s (1.6 frames) | 0.180 s | 2.395 s | 71% |
| **C — refined start → next refined start** | **0.030 s (0.8 frames)** | **0.140 s** | 2.395 s | **77%** |

**A → C is a 4.1× improvement in median error.** Without the silence guard the same
configuration gives a median of 0.044 s and a mean of 0.208 s, and the ffmpeg backend
gives 0.042 s and 0.195 s (A → C 2.9×); the guard reverts about 5% of refinements and
takes roughly 30% off the mean. An earlier single-recording figure of about 9x was a
sample effect and should not be cited.

These figures measure **consistency with the scheduled slot lengths**, not absolute
accuracy: clip length is a difference, so a shift applied to every cut point cancels
out. Absolute position is established by eye on a sample (see *How the cuts are
checked*).

Refinement is not a uniform gain. On recordings whose silence midpoints were already
close (median A error around 0.05 s), B and C are identical; on recordings with a large
midpoint error (around 0.15–0.21 s), C brings the median down to 0.020–0.024 s. On one
recording B was worse than A, which is why both ends of a clip must use the same kind of
point.

The mean stays roughly five times the median. A minority of cuts are far out for
reasons refinement cannot address (alignment errors, or adverts that are not sold at a
standard slot length). This has not yet been diagnosed.

### Detector backend and the guard

Same 12 recordings, same rules, changing only the refinement:

| Run | Refinement | refined | C median | C mean | C <0.2 s |
|---|---|---|---|---|---|
| R0 | ffmpeg, threshold 0.3 | 156 | 0.042 s | 0.195 s | 72% |
| R1 | scenedetect, defaults | 47 | 0.146 s | 0.280 s | 57% |
| R2 | scenedetect, `--min-scene-len 1` | 196 | 0.044 s | 0.208 s | 68% |
| **R2g** | **R2 + guard 80 ms** | **176** | **0.030 s** | **0.140 s** | **77%** |

With PySceneDetect's defaults the refinement is **worse than no refinement at all**
(A median 0.122 s), because `min_scene_len=15` frames hides every cut in the first 0.6 s
of the window — exactly where the true first frame usually lies. `--min-scene-len 1`
removes that constraint.

Pooled medians hide which backend is better: per recording, scenedetect wins 6 of the
12, ffmpeg 1, with 5 draws. Its weakness was the tail (a lower share within 0.2 s), and
that is what the guard fixes. Lowering the ffmpeg threshold from 0.3 to 0.15 changes
almost nothing (median 0.021 s on the 2024 test recording, against 0.000 s for
scenedetect), so the difference between the backends is not sensitivity: ffmpeg reports
small changes at the very start of an advert that scenedetect ignores, and misses the
real boundary a few hundred milliseconds later.

### How the cuts are checked

Three independent layers, because none of them is sufficient alone:

1. **Consistency with stated lengths** (all cut points, automatic). Constrains the distance
   between consecutive cut points, and is blind to a drift shared by every cut point. Since
   October 2026 each clip is compared with the length in its own identifier rather than
   with the nearest standard slot length; the nearest-slot measure lets a 30 s advert cut
   off at 20 s pass.
2. **The end of the silence** (all cut points, automatic). Audio resuming is an
   independent estimate of an advert's start. Measured over 361 cut points, cuts sit a
   median of 280 ms before it in both backends, which a frame-by-frame check attributed
   to audio starting after the picture rather than to cuts being early.
3. **Frame by frame, by eye** (a sample). The only layer that establishes absolute
   position. It found the case the guard now handles: on b41_05 the silence midpoint was
   correct to one frame and both backends pushed the cut 17 frames into the advert.
   Judgements are recorded to +/-2 frames, and the eye is used only where a disagreement
   is much larger than that. 23 cut points have been checked this way so far: enough to
   find faults of this kind, too few to report an accuracy figure.

### Yield

Under strict acceptance the 12 recordings yield 361 of 706 scheduled adverts (51%),
ranging from 13% to 93% per recording. Across the whole delivery, the audit of
10 October 2026 found 12,409 of 19,503 unique adverts (63.6%) obtained at least once in the
September run. This is a baseline, not a final coverage rate: both counts were made when
only Sky AdSmart rows were treated as placeholders, and will be made again. Where the
losses occur is set out in [results_2026-10.md](results_2026-10.md#coverage). The
fill-from-schedule rule, meant to raise yield, was measured in October and rejected (see
*Break acceptance*).

### Inference throughput (annotation stage)

This belongs to the annotation stage that follows segmentation, not to segmentation
itself. Qwen3.8-27B-NVFP4 on one GPU, video captioning at 2 fps:

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

## Reproducing the batch runs

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

The source recordings and their metadata sit in one directory (`$SRC` below), paired by
filename (`YYYY-MM-DD_CHANNEL_HH:MM:SS.mp4` and `..._metadata.json`). These filenames
contain colons, which are legal on Linux but not on Windows.

### Settings used for each run

Every run uses strict acceptance and plans the cuts without writing video
(`--min-match-rate 1 --no-fill-missing --dry-run`). Unless marked `--no-refine`, Stage B
runs with `--backend scenedetect --min-scene-len 1 --max-past-silence-end 80`. The sample
is every eleventh recording (`--stride 11`, 151 recordings); one recording too long to run
was skipped each time and is excluded from the audit with `--exclude`.

| Run | Scope | Additional options | Reported in |
|---|---|---|---|
| September full run (v16) | all 1,670 recordings | none | [results_2026-10.md](results_2026-10.md#september-full-run) |
| `s_own` | sample | `--clean-schedule --own-channel --no-refine` | [results_2026-10.md](results_2026-10.md#alignment-by-recording) |
| `s_own_rec2` | sample | as `s_own`, plus `--align recording` | the same |
| `s_prec` | sample | as `s_own_rec2`, with Stage B on | [results_2026-10.md](results_2026-10.md#stage-b-search-the-silence-and-allow-for-the-video-start) |
| `s_v2` | sample | as `s_prec`, plus `--refine-from silence --refine-mute-frames 6` | the same |
| `s_v3` | sample | as `s_v2`, plus `--correct-video-start` | the same |
| October full run (v17) | 1,669 recordings (one 32-hour file skipped) | as `s_v3` | [results_2026-10.md](results_2026-10.md#latest-full-run-v17) |

`s_own`, `s_own_rec2` and `s_prec` ran before the schedule-cleaning rules were revised on
13 Oct 2026 (only Sky AdSmart treated as a placeholder; breaks split at any gap over two
minutes); `s_v2` and `s_v3` use the revised rules.

```bash
# the sample run s_v3
python3 src/batch_cut.py $SRC --outroot runs/s_v3 --stride 11 \
    --clean-schedule --own-channel --align recording \
    --backend scenedetect --min-scene-len 1 --max-past-silence-end 80 \
    --refine-from silence --refine-mute-frames 6 --correct-video-start \
    --min-match-rate 1 --no-fill-missing --jobs 4 --dry-run

# precision by group, each clip against its own stated length, compared with s_v2
python3 src/loss_audit.py precision --run runs/s_v3 --ref runs/s_v2 --src $SRC \
    --out runs/precision_s_v3 --exclude "<the skipped recording>"
```

The October full run (v17) used the `s_v3` settings on every recording. The rule for the
last clip of a break is unchanged and remains the open problem.

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

- **Absolute accuracy rests on 23 frame-by-frame checks so far** (19 of them used to test
  the October Stage B options). Corpus-wide figures are consistency with stated lengths,
  not position.
- **The last clip of a break is the least reliable clip.** 14.4% of last clips in the
  October run (16% in September) are more than five seconds short; no replacement rule has
  been tested.
- **Breaks of three or four boundaries are weak.** Under the strict rule they still match
  in full at a wrong position about one time in eight (17 of 140 trials), against under 3%
  for breaks of five or more.
- **The ffmpeg scene threshold (0.3) has not been calibrated frame by frame.** The two
  backends disagree on 59 of 361 cut points, which is the natural sample to calibrate
  on.
- **Fill-from-schedule was measured and rejected** (see *Break acceptance*). It remains
  the code default only so that older commands keep their meaning.
- **Several settings are initial values that have not been tested**: the silence noise
  threshold (−40 dB) and shortest silence (0.2 s), the alignment tolerance (0.35 s), the
  offset tolerance (5 s) and the 8 s before the last clip's end silence is looked for.
- **The silence cue is not uniformly present.** One 2020 ITV2 recording matched only
  4 of 14 boundaries at any threshold from −35 to −25 dB. Whether this is a property of
  the channel, the era, or that particular file is unresolved; `survey_silence.py`
  exists to answer it by stratified sampling.
- **Schedule records are not one-to-one with what was broadcast.** 25,975 of the 97,213
  records in the delivered metadata (about 27%) are addressable-advertising placeholders:
  `SKYADSMART…` (20,812), `C4ADDRESSABLE` (2,665) and `ITVADDRESSABLE` (2,498). All but
  three share a start time with a real advert, sometimes in a neighbouring break.
  `silence_detect.py` collapses same-timestamp records when building boundaries, keeping
  the real advert; where two real adverts still share a start time, the case is meant to
  be checked by hand.
- **The schedule lists more than the recording holds.** For about a fifth of the files
  (327, 19.6%) it mixes in the breaks of a second channel, and it lists breaks that are
  not in the recording at all. `--own-channel` handles the first case provisionally.
