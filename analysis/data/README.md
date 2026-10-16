# Summary data for `ch04_results.ipynb`

Every file here is a **summary** of a run, never a row of the archive's metadata: counts,
medians and percentages by group. No identifier, brand, file name or path appears.

**Provenance.** These CSVs were transcribed by hand on 15 October 2026 from the screen
output of the runs, as recorded in the project's experiment notes at the time. They are
not files exported by the scripts. Where a figure was transcribed, it is given exactly as
the script printed it (percentages to one decimal place, seconds to three). They are to be
replaced by the CSVs that `loss_audit.py` writes when the runs are next exported.

| File | Run | Command that printed it |
|---|---|---|
| `own_length_by_group.csv` | full runs `v16` (September) and `v17` (October); sample runs `s_prec`, `s_v2`, `s_v3` | `loss_audit.py precision` (own-length table) |
| `chance_test.csv` | sample run `s_fill` | `loss_audit.py fillcheck` (CHANCE table) |
| `yield_by_rule.csv` | sample run `s_fill` | `loss_audit.py misses` (percentage columns dropped: their denominator is the whole delivery) |
| `alignment_runs.csv` | sample runs `s_own`, `s_own_rec2` | `loss_audit.py recon` / `diff` (summary figures only; screen output not kept) |
| `refine_check_19.csv` | 19 frame-checked cut points | `refine_check.py` (summary lines) |
| `video_start_by_year.csv` | all 1,670 recordings | `ffprobe` per file, summed by year |
| `coverage.csv` | audit of 10 Oct 2026 over the September run | `loss_audit.py recon` |
| `coverage_v17.csv` | October full run, each recording counted once | `loss_audit.py recon`, `dedup_recon.py` |

Run settings are in [../../docs/method.md](../../docs/method.md#settings-used-for-each-run).
