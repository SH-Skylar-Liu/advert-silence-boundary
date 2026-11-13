# advert-silence-boundary

The BFI National Archive holds long recordings of British television. In them the adverts
run together with the programmes, and for my PhD I need each advert on its
own. This code finds where one advert ends and the next begins, so that single adverts
can be cut out of long recordings.

There is a short project page with pictures of each step:
<https://sh-skylar-liu.github.io/advert-silence-boundary/>

I am a PhD researcher at King's College London, working in partnership with the BFI.
Chapter 4 of my thesis describes this method in detail.

![Four steps: find silences, match the schedule, refine to the first frame, silence guard](docs/img/chain.png)

## The idea

Two things help. The broadcast schedule tells us when each advert was meant to start and
how long it should last. And between two adverts there is almost always a brief moment of
silence.

Silence on its own is not much use, because people pause when they talk and a recording
has hundreds of quiet moments. So I take the pattern of advert lengths in a break, say
30, 20 and 30 seconds, and slide it along the recording until every boundary lands on a
silence. I only keep a break when all of its boundaries match.

The schedule and the recording do not start at the same moment, so the code also works
out the offset between them. It trusts an offset only when at least two breaks agree on
it within five seconds. Each break is then looked for near the place that offset
predicts.

The middle of a silence usually comes a few frames before the new advert appears. So the
code then looks at the picture and moves each cut to the first visible change. If that
change comes more than 80 ms after the sound returns, it has probably caught a shot
inside the advert, and the cut goes back to the middle of the silence.

No machine learning is used to decide where the cuts go.

## What you need

You give the code a recording (`.mp4`) and the schedule for it. It gives you back a cut
plan, a spreadsheet with one row per advert showing where it starts and ends. If you want,
it also writes each advert out as a separate video clip.

The recordings and the schedule belong to the BFI National Archive and are not in this
repository. There is a small made-up example in `examples/` so you can see what a cut plan
looks like.

## Trying it

You need Python 3.10 or newer and FFmpeg. Then install the two Python packages:

```bash
pip install -r requirements.txt
```

To plan the cuts for one recording:

```bash
python src/cut_adverts.py path/to/recording.mp4 \
    --metadata path/to/recording_metadata.json \
    --outdir path/to/output --cut-csv path/to/output/cut_plan.csv \
    --min-match-rate 1 --no-fill-missing --align recording \
    --backend scenedetect --min-scene-len 1 --dry-run
```

`--dry-run` writes the plan without cutting any video. Take it out when you want the
clips. `--min-match-rate 1 --no-fill-missing` is the strict rule I use, and
`--align recording` turns on the shared offset described above. For a whole folder of
recordings there is `src/batch_cut.py`, which takes the same options. The settings behind
each run in the thesis are in [docs/method.md](docs/method.md).

## Where it stands

In October 2026 I ran it over 1,669 recordings and produced cut plans, without cutting
the clips yet. Apart from the last advert in each break, 94.5% of the planned clips come
within 0.2 seconds of the length the schedule gives. The details are in
[docs/results_2026-10.md](docs/results_2026-10.md).

That figure needs to be read with care. It tells us the clip lengths agree with the
schedule. It does not yet tell us that every cut lands on exactly the right frame. I have
checked that by eye only for a small sample.

The last advert in a break is the weakest point. Nothing comes after it, so the code has
to guess its end from the next silence, and a pause inside the advert can fool it. Short
breaks of three or four adverts can also be matched in the wrong place now and then, and
the schedule itself sometimes lists adverts that were never shown in that recording.

## Get in touch

I am Shihan Liu ([SH-Skylar-Liu](https://github.com/SH-Skylar-Liu) on GitHub). If you
have a question or spot a problem, please open an issue.

*This is a working draft prepared for my PhD upgrade. I will keep improving the code and
this page.*
