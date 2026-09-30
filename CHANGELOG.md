# Changelog

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
