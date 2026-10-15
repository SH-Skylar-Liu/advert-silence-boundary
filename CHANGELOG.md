# Changelog

**1.8 — 10–15 Oct 2026**

Every new option is off by default, so earlier commands reproduce earlier runs. All of
them are tested on a sample of 151 recordings only. Figures in
[docs/results_2026-10.md](docs/results_2026-10.md).

- `loss_audit.py`: where scheduled adverts are lost (`recon`, `misses`, `schedule`,
  `offsets`), the accidental-match test (`fillcheck`, the chance table), precision by group
  (`precision`), and two runs compared (`diff`). `precision` measures each clip against
  the length in its own identifier; `--relabel` takes identifiers from the schedule by
  position, for ledgers written before the label fixes.
- Ledger rows labelled from the collapsed advert list; 7,929 rows of the September run had
  been labelled wrongly.
- `--align recording`: fix the recording start where at least two fully matched breaks
  agree on it, then check each break only where it must be; `start_confirmed_by` column.
  `--confirm-partial` and `--fill-only-confirmed` for testing the looser rule within it.
- `--clean-schedule`: merge breaks that start at the same second; drop `SKYADSMART…`,
  `C4ADDRESSABLE` and `ITVADDRESSABLE` placeholders that share a start with a real advert,
  keep the boundary of the rest with `placeholder=1` and no clip; split a break where the
  next advert starts more than 30 s after the previous one should have ended.
- `--own-channel`: keep only the run of breaks that belongs to the recording's channel.
- Advert length read from the last three digits of the identifier.
- Stage B: `--refine-from silence` searches the whole silence; `--refine-mute-frames 6`
  starts the search six frames in, from the Clearcast delivery specification;
  `--correct-video-start` allows for a video stream that starts after the audio, which is
  why refinement appeared to fail on 2025–2026 recordings. `refine_check.py` tests Stage B
  against frame-checked cut points. A fitted `--refine-lookback` was added and removed.
- `--last-end stated` and `--last-end stated-silence` for the last clip of a break;
  untested.
- `show_placeholders.py`: breaks holding placeholder records, and where their slots sit.
- Docs: README and `method.md` brought in line with the thesis (Chapter 4, 14 Oct 2026);
  the settings of each run listed; the looser acceptance rule recorded as rejected;
  `results_2026-10.md` added; internal paths removed.

**1.7 — 20 Sep 2026**
- `silence_stats.py`: measures, from the ledger alone, how long the silences are and
  whether the one-second refinement window reaches past their end.
- `docs/pipeline_explainer.html`: single-page walkthrough of the pipeline.
- `batch_cut.py` builds the options it forwards to `cut_adverts.py` from one
  `PASSTHROUGH` table. `--refine-threshold` had been missing from the forwarded command
  line, so a threshold sweep exited at argument parsing and wrote no ledgers.
- `compare_runs.py` imports `deviation()` from `compare_end_modes.py` instead of
  restating it, so the two scripts cannot report different numbers from one ledger.
- Removed the dead `detect_silence` import fallbacks, `scripts/step1.sh`, and the
  Chinese README.

**1.6 — 19 Sep 2026**
- Silence guard: `--max-past-silence-end` (default 80 ms, 0 disables) reverts a
  refinement that lands past the end of the silence. Over 361 cut points it reverts 20
  of them and takes the mean slot deviation from 0.208 s to 0.140 s.
- `frame_strip.py`: every frame around a cut point as one labelled page, for ground
  truth by eye. Times come from ffmpeg's `showinfo`, and from `drawtext` where the
  build has it.
- `compare_runs.py`: ledger-to-ledger comparison, per recording plus the silence-end
  cross-check.
- The ledger records `silence_start_s` and `silence_end_s`.

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
