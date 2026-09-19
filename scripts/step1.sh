#!/usr/bin/env bash
# Backend question, three steps, one command.
# 后端问题的三步检验，一条命令跑完。
#
# 1. Slot deviation PER RECORDING plus the audio cross-check, ffmpeg vs scenedetect,
#    over the twelve recordings already in ~/run_step0.
#    分录制的档位偏差 + 音频交叉检验（12 个录制）。
# 2. The threshold test: rerun 20-00.mp4 with a lower ffmpeg scene threshold. If this
#    lands near the scenedetect result, the backend gap is an uncalibrated threshold
#    rather than a property of the detector.
#    阈值检验：用更低的 ffmpeg 阈值重跑 20-00。若结果接近 scenedetect，说明差距来自
#    未标定的阈值，而不是检测器本身。
# 3. A frame contact sheet for b41_05 (Lidl), where both backends may be wrong.
#    b41_05（Lidl）的逐帧对照页，这是两个后端可能都错的那一条。
#
# Everything is read-only apart from files written under ~/run_compare.
# 除了写入 ~/run_compare，全部只读；不写任何视频。
set -u

PY=~/venv-scd/bin/python
SRC=~/storage/code/advert-silence-boundary/src
OUT=~/run_compare
REC=/home/shihan/storage/video/2024-03-26_itv1_only/20-00.mp4
META=/home/shihan/storage/silencetest/breaks_20.json
LOG=$OUT/step1.log

mkdir -p "$OUT"
exec > >(tee "$LOG") 2>&1          # everything also lands in the log / 同时写入日志

echo "=============================================================="
echo " 1. ffmpeg vs scenedetect, per recording + audio cross-check"
echo "=============================================================="
if [ -f ~/run_step0/R0s.csv ] && [ -f ~/run_step0/R2s.csv ]; then
    $PY "$SRC/compare_runs.py" ~/run_step0/R0s.csv ~/run_step0/R2s.csv \
        --labels ffmpeg scenedetect
else
    echo "SKIPPED: ~/run_step0/R0s.csv or R2s.csv is missing."
    echo "         Rerun the v1.3 batches first (they add the silence columns)."
fi

echo
echo "=============================================================="
echo " 2. threshold test on 20-00.mp4: ffmpeg at 0.15 instead of 0.3"
echo "=============================================================="
$PY "$SRC/cut_adverts.py" "$REC" --metadata "$META" \
    --outdir "$OUT/tmp" --cut-csv "$OUT/20-00_R0b.csv" \
    --backend ffmpeg --refine-threshold 0.15 \
    --min-match-rate 1 --no-fill-missing --dry-run > "$OUT/20-00_R0b.log" 2>&1
tail -n 5 "$OUT/20-00_R0b.log"

echo
echo "--- slot deviation, 20-00.mp4 ---"
for L in 20-00_R0 20-00_R0b 20-00_R2; do
    if [ -f "$OUT/$L.csv" ]; then
        echo "[$L]"
        $PY "$SRC/compare_end_modes.py" "$OUT/$L.csv" | sed -n '3,6p'
    fi
done
echo "R0 = ffmpeg 0.3   R0b = ffmpeg 0.15   R2 = scenedetect min_scene_len 1"

echo
echo "=============================================================="
echo " 3. frame contact sheet for b41_05 (Lidl)"
echo "=============================================================="
$PY "$SRC/frame_strip.py" "$REC" --at 2810.34 --before 0.2 --after 1.0 \
    --out "$OUT/b41_05_lidl.html"
echo
echo "Open it with:  xdg-open $OUT/b41_05_lidl.html"
echo "Question: does the Lidl picture appear at about 46:50.3, or only at 46:51.0?"
echo "问题：Lidl 的画面是 46:50.3 左右就出现，还是要到 46:51.0 才出现？"
echo
echo "log: $LOG"
