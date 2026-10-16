# Results — October 2026

Current figures for the segmentation pipeline. The latest full run (v17, 15 October 2026)
applies every October change to all recordings; the sections after it record how the
pipeline got there from the first full run (v16, September 2026), which is the run
reported in Chapter 4 of the thesis (14 October 2026). Method and run settings are in
[method.md](method.md#settings-used-for-each-run); the August–September log is kept in
[results_2026-09.md](results_2026-09.md).

Each clip is compared with the length stated in its own identifier. "Sample" means every
eleventh recording of the delivery (151 recordings), used to test each change before the
full run.

## Summary

| Result | v16, September | v17, October |
|---|---|---|
| Recordings processed | 1,670 | 1,669 (one 32-hour file skipped) |
| Cut points | 25,208 | 30,632 |
| Clips within 0.2 s of stated length, all | 73.9% | **83.2%** |
| Clips within 0.2 s, last clip of each break aside | 83.0% | **94.5%** |
| Last clips aside: more than 1 s / 5 s off | 2.6% / 0.3% | 1.7% / 0.1% |
| 2025 / 2026 within 0.2 s | 60.3% / 58.8% | **83.0% / 83.1%** |
| Ledger rows with a wrong label | 7,929 | 0 |
| Unique adverts obtained | 12,409 of 19,503 (63.6%) | **14,189 of 18,890 (75.1%)** |
| Last clips more than 5 s short | 16.0% | 14.4% (rule unchanged) |

The two coverage figures use different denominators: v17 counts the metadata after repair
(placeholders removed, breaks of a second channel set aside), so 18,890 unique adverts
rather than 19,503. On the old denominator v17 would be 72.8%.

---

## Latest full run (v17)

Run of 15 October 2026 with the `s_v3` settings ([method.md](method.md#settings-used-for-each-run))
on every recording except one of 32 hours, which the new alignment takes hours to process.
No recording failed.

| Group | clips | median | ≤0.2 s | >1 s | >5 s |
|---|---|---|---|---|---|
| All | 30,630 | 0.000 s | 83.2% | 4.2% | 1.9% |
| Not the last clip of a break | 26,813 | 0.000 s | 94.5% | 1.7% | 0.1% |
| Last clip of a break | 3,817 | 0.391 s | 4.2% | 22.0% | 14.4% |
| First clip of a break | 3,814 | 0.000 s | 85.2% | 7.3% | 0.2% |
| Break of 3–4 boundaries | 1,614 | 0.000 s | 63.7% | 11.8% | 7.2% |
| Break of 5 or more | 29,016 | 0.000 s | 84.3% | 3.8% | 1.6% |
| Recording start confirmed by two breaks | 27,097 | 0.000 s | 83.9% | 3.8% | 1.5% |
| Recording start not confirmed | 3,533 | 0.000 s | 78.2% | 7.7% | 4.8% |
| Cut points new in v17 | 6,319 | 0.000 s | 77.7% | 6.2% | 2.6% |
| 2020 | 2,503 | 0.000 s | 83.1% | 4.4% | 2.4% |
| 2021 | 5,034 | 0.000 s | 81.6% | 4.8% | 2.5% |
| 2022 | 4,043 | 0.000 s | 82.1% | 4.2% | 1.8% |
| 2023 | 4,224 | 0.000 s | 84.7% | 3.8% | 1.6% |
| 2024 | 5,871 | 0.000 s | 84.7% | 3.8% | 1.3% |
| 2025 | 5,731 | 0.000 s | 83.0% | 4.5% | 2.0% |
| 2026 | 3,224 | 0.000 s | 83.1% | 4.2% | 2.2% |

- A median of 0.000 s means that more than half of the clips have exactly their stated
  length: both ends fall on a picture change, and picture changes sit on the frame grid.
- 94% of cut points were moved by the picture stage (49% in v16); the guardrail reverted
  none, because the search now stops at the end of the silence.
- Every year falls between 81.6% and 84.7%.

**Coverage.** 115 recordings were delivered under two or three file names, each with a
different part of the metadata; slots are therefore counted once per recording.

| Step | slots | breaks |
|---|---|---|
| Real advert slots in the repaired metadata | 54,515 | 6,896 |
| of which in breaks not in the recording | 9,826 | 1,282 |
| inside the recording | 44,689 | 5,614 |
| **kept** | **28,632 (64.1% of those inside)** | **3,566** |

64.1% is a lower bound: breaks outside the recording can only be identified where the
recording start was fixed (994 of 1,669 recordings). Of the 18,890 unique adverts,
14,189 were obtained (75.1%); of the 4,701 not obtained, 3,103 appear only once in the
delivery.

Where breaks were lost (counted file by file): 1,990 because a boundary found no silence
(545 of them missing a single boundary), 1,423 outside the recording, 171 placed
inconsistently with the recording start.

---

## September full run

Run of 20 September 2026 (v16), settings in [method.md](method.md#settings-used-for-each-run).
The ledger holds 25,208 cut points. Before measuring, the identifiers were taken again from
the schedule by position, because 7,929 ledger rows had been labelled wrongly
(`loss_audit.py precision --relabel`); this changes labels, not cut points.

**Measure.** Clip length minus the length stated in the clip's own identifier (the last
three digits, in seconds). 24,872 clips have such a length; the other 336 open a break
whose first record is a placeholder.

| Group | clips | median | ≤0.2 s | >1 s | >5 s |
|---|---|---|---|---|---|
| All | 24,872 | 0.042 s | 73.9% | 5.1% | 2.1% |
| Not the last clip of a break | 21,973 | 0.031 s | 83.0% | 2.6% | 0.3% |
| Last clip of a break | 2,899 | 0.403 s | 5.5% | 23.7% | 16.0% |
| Break of 3–4 boundaries | 549 | 0.282 s | 42.8% | 23.7% | 16.6% |
| Break of 5 or more | 24,323 | 0.040 s | 74.6% | 4.7% | 1.8% |
| 2020 | 1,979 | 0.042 s | 78.1% | 5.7% | 2.6% |
| 2021 | 4,059 | 0.058 s | 76.6% | 6.3% | 2.8% |
| 2022 | 3,147 | 0.023 s | 80.2% | 5.5% | 2.3% |
| 2023 | 3,512 | 0.002 s | 82.4% | 4.7% | 1.7% |
| 2024 | 4,651 | 0.000 s | 82.0% | 4.7% | 1.5% |
| 2025 | 4,839 | 0.130 s | 60.3% | 4.8% | 2.1% |
| 2026 | 2,685 | 0.140 s | 58.8% | 4.2% | 2.3% |

- Against the nearest standard slot length instead, the overall figures are almost the
  same: median 0.042 s, 74.2% within 0.2 s.
- 57 clips (0.2%) run longer than two minutes. Some may be genuine, since the metadata
  lists adverts of 120 and 180 s; the rest have not been diagnosed. This is why the median
  is reported and the mean is not.
- **Audio check.** Across all 25,208 cut points, the median cut point falls 277 ms before
  the sound returns. On 361 cut points where both picture detectors were run, the two
  differ by 5 ms, so there is no sign that either has drifted.
- The 2025–2026 figures are lower because of the video start offset described
  [below](#stage-b-search-the-silence-and-allow-for-the-video-start), not because of the
  material.

## Coverage

Audit of 10 October 2026, over the September run: **12,409 of the 19,503 unique adverts**
in the first delivery had been obtained at least once (63.6%). Both counts were made when
only Sky AdSmart rows were treated as placeholders and will be made again. Of the 7,094
missing adverts, 3,994 appear only once in the delivery, so for them a lost slot is a lost
advert.

Slots are lost at three points. How many of the 7,094 each accounts for has not yet been
counted.

1. **The slot was never in the file.** The metadata lists breaks that are not in the
   recording, and for 327 files (19.6%) it mixes in the breaks of a second channel.
2. **The metadata groups its records wrongly.** Counts over all 1,670 files, before repair:

   | Fault | Count | Repair (`--clean-schedule`) |
   |---|---|---|
   | A break holds only a placeholder row, and a real break starts at the same second | 1,758 breaks | Breaks that start at the same second are merged |
   | The first real advert is split off on its own, and the rest of its break starts at the same second | 958 breaks | The same |
   | Two breaks are written as one, with a long gap in the middle | 200 breaks | Split where the next advert starts more than 30 s after the previous one should have ended |
   | Break numbers fall into separate runs belonging to different channels | 327 files | `--own-channel`: keep the run containing the time in the file name (319 of 327); provisional |

3. **The strict rule.** One missed boundary removes a whole break of eight or nine adverts,
   and the offset check removes most breaks placed in the wrong position.

## Rejected: the looser acceptance rule

Sample, 151 recordings. Each break was looked for again five and seven minutes away from
where it belongs, inside the programme, where silences have nothing to do with the
schedule; a full match there is an accident. 344 breaks, 1,376 displaced trials.

| Rule (and at least 3 matched) | passes at the right position | passes at a wrong position |
|---|---|---|
| **Every boundary matched (thesis rule)** | 92.2% | **2.4%** |
| At most 1 missing | 92.7% | 3.8% |
| At most 2 missing | 95.3% | 6.9% |
| At most 3 missing | 95.6% | 11.2% |
| Two thirds matched | 95.1% | 7.6% |
| Half matched (code default) | 96.5% | **16.0%** |

The gain from the half rule was 112 slots on the sample (2,913 → 3,025). Under the strict
rule, breaks of 3–4 boundaries still pass at a wrong position in 17 of 140 trials (12.1%),
against 2.3% for 5–7 boundaries and 0.9% for 8 or more.

## Alignment by recording

Sample, Stage B off (`--no-refine`), so these figures are about how much is kept, not about
precision. Compared with `loss_audit.py diff`.

| | `s_own` (each break on its own) | `s_own_rec2` (`--align recording`) |
|---|---|---|
| Breaks kept | 275 | 355 |
| Cut points kept | 2,368 | **2,877** |
| Unique adverts | 1,950 | 2,313 |

2,316 cut points are in both runs; 561 are new and 52 are lost (net +21.5%). The 52 lost
cut points correspond to 7 unique adverts. In 91 of the 151 recordings two or more breaks
agreed on the recording start; the other 60 fell back to aligning each break on its own.

Example: in one Channel 5 recording from April 2021 the old method kept one break, placed wrongly,
and dropped the three that agreed with each other; the new ordering kept four breaks and
31 cut points.

## Stage B: search the silence and allow for the video start

**On 19 frame-checked cut points** (`src/refine_check.py`; frames from the true first frame,
positive = late):

| Stage B | exactly on the first frame | within 2 frames | late by >2 | early by >2 | worst |
|---|---|---|---|---|---|
| As in the September run | 8 | 13 | 3 | 3 | 7 |
| `--refine-from silence --refine-mute-frames 6` | 14 | 16 | 0 | 3 | 7 |
| … plus `--correct-video-start` | **16** | **19** | 0 | 0 | 1 |

The three that remained wrong after the first change all came from 2025–2026 recordings,
in which the video stream starts 0.64–1.32 s after the audio. Across the delivery:

| Year | files | video stream starts after 0.1 s | maximum |
|---|---|---|---|
| 2020–2024 | 1,208 | 0 | 0.00 s |
| 2025 | 302 | 246 (81%) | 2.84 s |
| 2026 | 160 | 160 (100%) | 2.68 s |

**On the sample** (151 recordings, 2,882 cut points; each clip against its own stated
length):

| | `s_prec` | `s_v2` (search the silence, 6-frame bound) | `s_v3` (plus video start) |
|---|---|---|---|
| Cut points moved by Stage B | 48.7% | 75.6% | 93.3% |
| All clips: ≤0.2 s | 72.5% | 75.7% | 83.4% |
| **Last clips aside: ≤0.2 s** | **82.4%** | 85.5% | **94.5%** |
| Last clips aside: >1 s / >5 s | 2.9% / 0.2% | 2.4% / 0.1% | 2.1% / 0.2% |
| 2025: ≤0.2 s / moved | 62.9% / 29.3% | 61.3% / 38.7% | 86.2% / 95.7% |
| 2026: ≤0.2 s / moved | 50.8% / 19.3% | 52.1% / 24.5% | 79.2% / 88.0% |
| Last clips: >5 s short | 12.6% | 13.3% | 13.2% |

The 2020–2024 rows are identical in `s_v2` and `s_v3`, as expected, since those files'
video starts at zero. 2026 (79.2%) is still a little below the other years; the cause has
not been looked into. For position, the evidence is still the 19 cut points above.

## Last clip of a break

The last advert of a break ends at the first silence that begins at least 8 s after it
starts, and a pause inside the advert can be taken for that silence. In the September run
16% of the 2,899 last clips are more than five seconds shorter than their stated length
(in the sample, 30 s adverts cut to 8–22 s, and one 180 s advert cut to 29.5 s). The length
in the identifier is the obvious replacement (`--last-end stated`, `--last-end
stated-silence`); neither has been tested. Until then the last clip of each break is
reported separately.
