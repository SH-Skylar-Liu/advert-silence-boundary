# Cutting single adverts out of broadcast recordings

Documentation for the segmentation stage of a PhD project on UK television advertising
(King's College London and the BFI National Archive). The stage cuts each advert out of
the continuous recording it was broadcast in, so that it can be annotated and analysed.
It is described in Chapter 4 of the thesis; the pages below give the code, the settings
and the figures behind that chapter.

| Page | What it is |
|---|---|
| [Pipeline explainer](pipeline_explainer.html) | Single-page walkthrough: how audio, schedule and picture fix each cut, with one break worked through |
| [Chapter 4 results notebook](ch04_results.html) | Table 4.6 of the thesis recomputed from summary data, with the comparisons behind it ([notebook source](https://github.com/SH-Skylar-Liu/advert-silence-boundary/blob/main/analysis/ch04_results.ipynb)) |
| [Results, October 2026](results_2026-10.md) | Every current figure, with its basis and status |
| [Method and settings](method.md) | How the pipeline works, the settings used for each run, known limitations |
| [Code repository](https://github.com/SH-Skylar-Liu/advert-silence-boundary) | Scripts, README and version history |

**How to read the figures.** Most figures compare each clip with the length its identifier
states. This tests consistency with scheduled durations, not whether a cut point is in the
right place; position is checked frame by frame on a sample. Figures marked *sample* come
from 151 of the 1,670 recordings and have not yet been run on the whole delivery.

The recordings and their metadata belong to the BFI National Archive and are not published
here. Only summary figures appear on these pages.

Developed by Shihan Liu. Last updated 16 October 2026.
