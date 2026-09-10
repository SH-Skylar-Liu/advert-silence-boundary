# Advert boundary detection — silence + scene-change pipeline
# 广告边界检测 —— 静音 + 场景切换 pipeline

> ⚠️ **PRIVATE REPOSITORY — INTERNAL PATHS INCLUDED.**
> Commands and defaults in this repository refer to real BFI server paths and account
> names so that a collaborator can reproduce a run without translation. **Before this
> repository is made public** (SSH Open Marketplace, JOHD, or any external release),
> replace every `/home/shihan/...` path, account name and hostname with placeholders.
>
> **中文：** 本仓库为私有，保留了真实的 BFI 服务器路径与账户名，目的是让协作者拿到就能直接跑，
> 不必逐条翻译。**若日后要转为公开**（SSH Open Marketplace、JOHD 或任何对外发布），
> 必须先把所有 `/home/shihan/...` 路径、账户名与主机名替换成占位符。

Deterministic segmentation of individual television adverts from full-programme
recordings, using the broadcast schedule as a prior. No machine learning is used to
find the boundaries: `ffmpeg` detects the absence of audio, and a scene-change filter
locates the first frame. A vision-language model is applied only afterwards, to
describe the content of each extracted advert.

That separation is the point. Corpus construction is deterministic and reproducible;
interpretation is where the model enters.

**中文：** 以排期表为先验，从整段节目录制中确定性地切分出每一条电视广告。
边界识别不使用任何机器学习——`ffmpeg` 检测音频缺失，场景切换滤镜定位首帧；
视觉语言模型只用在之后的内容描述环节。**这一分层本身就是方法论主张**：
语料构建层确定性、可复现，模型只进入诠释层。

---

## Method / 方法

**Stage A — coarse location.** `ffmpeg silencedetect` finds every silent segment in the
recording. The schedule (advert start times and their ordering within a break) is then
slid across those silences to find the offset `T` at which the schedule's duration
pattern lands on detected silences. This joining of schedule to silence is what makes
the method work; silence detection on its own produces too many false positives.

**Stage B — first-frame refinement.** A silence midpoint typically sits a few frames
before the true first frame, so the previous advert bleeds into the start of the clip.
Each start is therefore pushed forward to the first scene change within the following
**one second**. A wider window starts catching cuts inside the advert itself.

**Clip boundaries.** A clip runs from its own refined start to the **next advert's
refined start** (`--end-mode next-start`, the default). Ending on the silence midpoint
instead (`--end-mode midpoint`) mixes two references and makes clip length absorb the
next advert's shift as error — see the ablation below.

**Two detector backends.** Scene detection uses PySceneDetect when it can be imported,
and otherwise `ffmpeg`'s `select='gt(scene,T)'` filter. On the BFI workstation
PySceneDetect is installed but OpenCV is not, so the ffmpeg backend is what runs. The
side effect is useful: the whole corpus-construction chain then depends on ffmpeg alone,
with no Python dependencies, which is what makes it portable to another archive.

**Known caveat:** the two backends do not share a threshold scale, and the ffmpeg
threshold (0.3) has not yet been cross-checked frame by frame against the reference
PySceneDetect implementation. Current evidence for the ffmpeg backend is indirect
(duration convergence, below).

> **中文：**
> **A 阶段——粗定位。** 用 `ffmpeg silencedetect` 找出全部静音段，再把排期表
> （每条广告的开始时间与段内顺序）沿录制滑动，找到使排期时长排列正好压在静音上的偏移 `T`。
> **真正让方法成立的是"把排期接到静音上"这一步**，单独做静音检测的误报率过高。
>
> **B 阶段——首帧精修。** 静音中点通常比真首帧早几帧，前一条广告会渗进本条开头。
> 因此每个起点都前推到其后 **1 秒内**的第一个场景切换。窗口再大就会抓到广告内部的镜头切换。
>
> **切片端点。** 每条片子从自己的精修起点到**下一条的精修起点**（默认 `--end-mode next-start`）。
> 若结尾改用静音中点（`--end-mode midpoint`），两端口径不一致，片长会把下一条的修正量当成误差吸收进来——见下方消融实验。
>
> **两个检测后端。** 能 import PySceneDetect 就用它，否则退回 `ffmpeg` 的
> `select='gt(scene,T)'` 滤镜。BFI 工作站上 PySceneDetect 装了但缺 OpenCV，所以实际走 ffmpeg 后端。
> 这个被迫的选择反而有利：**整条语料构建链只依赖 ffmpeg，零 Python 依赖**，因此更容易迁移到其他档案馆。
>
> **已知保留项：** 两个后端的阈值不在同一尺度上，ffmpeg 的 0.3 尚未与参考实现逐帧对拍，
> 目前的证据是间接的（下方的时长收敛）。

---

## Measured results / 实测结果

Test recording: `2020-08-29_ITV1 HD_09:39:00.mp4` — 7,865 s, 8 breaks, 67 adverts.
Alignment keeps 4 breaks (every boundary matched) and produces **35 clips**.

**中文：** 测试录制为 `2020-08-29_ITV1 HD_09:39:00.mp4`（7,865 秒、8 个广告段、67 条广告）。
对齐后保留 4 个全中的广告段，产出 **35 条切点**。

### Cut accuracy / 切点精度

UK television advertising is sold in fixed slot lengths, so the scheduled duration of
every clip is known in advance. The error of a cut can therefore be measured without
manual frame inspection: the absolute difference between the cut clip's duration and the
nearest slot length. The slot grid comes from the advertising market, not from the
detector, so this is an independent check rather than self-confirmation.

**中文：** 英国电视广告按固定档位售卖，因此每条切片的**应有时长是先验已知的**。
于是切点误差可以不靠人眼逐帧核对来测量：取切出时长与最近档位之差的绝对值即可。
**档位来自广告市场而非检测器本身，所以这是独立验证，不是自我印证。**

| Variant / 变体 | median | mean | max | <0.2 s |
|---|---|---|---|---|
| A — silence midpoint → next midpoint | 0.206 s (≈5 frames) | 0.277 s | 0.943 s | 49% |
| B — refined start → next midpoint | 0.065 s (≈1.6 frames) | 0.195 s | 0.993 s | 69% |
| **C — refined start → next refined start** | **0.022 s (≈0.55 frames)** | **0.138 s** | **0.702 s** | **77%** |

**A → C is a ~9× improvement in median error.** B improves the median but worsens the
maximum, for the reason given above; C removes that by construction.

Stage B moved 23 of 35 cut points, by 45–787 ms. The four break-opening adverts were
shifted by 750/764/785/787 ms — consistently, because the programme→break silence lasts
about two seconds, so its midpoint falls roughly 0.75 s before the true first frame.
Adverts inside a break are separated by 0.5–0.7 s of silence and shift far less.

**中文：** **A → C 的中位误差改善约 9 倍。** B 组中位数改善但最大值恶化，原因即上文所述的
两端口径不一致；C 组从构造上消除了这一点。Stage B 前推了 35 个切点中的 23 个，幅度 45–787 ms。
四个广告段的**首条**广告修正量高度一致（750/764/785/787 ms），机制清楚：
节目→广告段的过渡静音约 2 秒，取中点即落在真首帧前约 0.75 秒；
段内广告之间静音仅 0.5–0.7 秒，修正量因此小得多。

### Throughput / 推理吞吐

Qwen3.8-27B-NVFP4 on one H100, video captioning at 2 fps:

| Concurrency / 并发 | throughput | effective RTFX | speed-up |
|---|---|---|---|
| 1 | 0.45× real time | 2.199 | 1.00× |
| 2 | 0.80× | 1.244 | 1.78× |
| 4 | 1.56× | 0.642 | **3.47×** |
| 8 | 1.71× | 0.586 | 3.80× |

Four workers is the knee; eight is saturated, so the bottleneck is the GPU rather than
the server's concurrency limit. Whole archive (134,000 unique adverts × 26.25 s ≈ 977 h
of material): about **24 days**, against 76 assumed sequentially.

**中文：** 单 H100 上跑 Qwen3.8-27B-NVFP4，2 fps 采样。**4 路是拐点，8 路已饱和**——
瓶颈在 GPU，不在服务端的并发上限。全库（134,000 条唯一广告 × 26.25 秒 ≈ 977 小时素材）
约需 **24 天**，而按顺序推理的原估算是 76 天。

---

## Repository layout / 仓库结构

```
src/
  csv_to_metadata.py      schedule CSV -> metadata JSON (break grouping, advert starts)
  silence_detect.py       Stage A: silencedetect + schedule alignment (importable)
  refine_first_frame.py   Stage B: first scene change within 1 s, two backends
  cut_adverts.py          end-to-end: align, refine, plan/cut clips, write the ledger
  compare_end_modes.py    ablation over a ledger: variants A / B / C
  survey_silence.py       stratified sample by channel x year; where does the cue hold?
  vllm_caption.py         captioning client + concurrency benchmark (standard library only)
examples/
  example_cut_ledger.csv  synthetic ledger so the ablation runs without archive data
docs/
  results_2026-09.md      full measurement log, including what failed
```

## Requirements / 依赖

`ffmpeg` and `ffprobe` on `PATH`, and Python 3.10+. **No Python packages are required.**
PySceneDetect is optional; without it the ffmpeg backend is used automatically.

**中文：** 只需 `PATH` 上有 `ffmpeg` 与 `ffprobe`，以及 Python 3.10+。
**不需要安装任何 Python 包。** PySceneDetect 可选，没有则自动使用 ffmpeg 后端。

## Reproducing / 复现步骤

```bash
# 1. schedule CSV -> metadata JSON (only needed if you do not already have one)
python3 src/csv_to_metadata.py "2024-03-26_ITV1HD.csv" -o breaks.json

# 2. inspect the silence structure of a recording, and how the schedule aligns to it
python3 src/silence_detect.py RECORDING.mp4 --metadata breaks.json

# 3. plan the cuts without writing any video, and save the ledger
python3 src/cut_adverts.py RECORDING.mp4 --metadata breaks.json \
    --outdir /tmp/cuts --cut-csv plan.csv --dry-run

# 4. reproduce the A/B/C ablation from that ledger (no re-decoding needed)
python3 src/compare_end_modes.py plan.csv --per-clip

# 5. cut for real once the plan looks right
python3 src/cut_adverts.py RECORDING.mp4 --metadata breaks.json \
    --outdir /path/to/output --cut-csv plan.csv
```

The ablation runs out of the box on the bundled example:

```bash
python3 src/compare_end_modes.py examples/example_cut_ledger.csv
```

On the BFI workstation the source recordings and their metadata are in
`/home/shihan/storage/video/unique_advert_source_videos/`, paired by filename
(`YYYY-MM-DD_CHANNEL_HH:MM:SS.mp4` and `..._metadata.json`). Note that these filenames
contain colons, which are legal on Linux but not on Windows.

**中文：** 步骤 3 的 `--dry-run` 只写台账、不动任何视频，可放心先跑。
第 4 步的消融不需要重新解码，用自带的示例台账即可开箱验证。
BFI 工作站上的源录制与 metadata 在 `/home/shihan/storage/video/unique_advert_source_videos/`，
按文件名配对。**注意文件名含冒号**，Linux 合法但 Windows 非法，拷回本地前需批量改名。

## Design notes worth keeping / 值得保留的设计决定

**The ledger records raw and derived values side by side.** `--cut-csv` writes both the
Stage A midpoint (`start_silence_mid_s`) and the refined cut point (`start_s`). Because
of that, the A/B/C ablation needed no re-cutting and no re-decoding — only arithmetic
over an existing CSV. Keeping raw alongside derived values at every stage is what makes
this kind of comparison cheap enough to actually run.

**Breaks are accepted whole or not at all.** A break is cut only if every one of its
boundaries matched a silence, and only if its fitted offset is consistent with the
neighbouring breaks (a long recording contains enough silences that a wrong offset can
still score well by chance). On the test recording this yields 35 clips from 67 adverts:
a **52% yield**, traded for precision.

**Video is always transcoded, never stream-copied.** Stream copy leaves
`duration`/`source_duration` inconsistent in the MP4, which breaks frame/time alignment
in downstream vLLM inference. Clips are also mapped explicitly (`-map 0:v:0 -map 0:a:0`)
because the delivered files carry more than one video stream plus data streams.

> **中文：**
> **台账同时记录原始值与派生值。** `--cut-csv` 会把 Stage A 中点（`start_silence_mid_s`）
> 和精修后切点（`start_s`）都写进去。正因如此，A/B/C 消融**不需要重切、也不需要重新解码**，
> 只是对一份 CSV 做算术。**每个环节都保留原始值与派生值，是这类对照实验能便宜到真的会去做的前提。**
>
> **广告段要么整段接受、要么整段丢弃。** 只有当一个段内每个边界都命中静音、
> 且拟合偏移与相邻段一致时才切（长录制里静音够密，错误偏移也可能凑出高命中）。
> 测试录制上因此从 67 条广告产出 35 条，**产出率 52%**——用产出率换精度。
>
> **视频一律 transcode，绝不 stream copy。** stream copy 会导致 MP4 的
> `duration` 与 `source_duration` 不一致，使下游 vLLM 的帧/时间对齐失败。
> 切片还显式指定了 `-map 0:v:0 -map 0:a:0`，因为交付文件里含不止一条 video 流和若干 data 流。

## Known limitations / 已知局限

- **52% yield** on the test recording. Filling missed boundaries from the scheduled
  durations (and flagging them as lower-confidence) would raise this substantially; not
  yet implemented.
- **The ffmpeg scene threshold is not yet calibrated** against the reference
  PySceneDetect implementation.
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

> **中文：**
> - **产出率 52%**：用排期时长补齐未命中的边界并标为低置信度，可显著提升，尚未实现。
> - **ffmpeg 场景阈值未标定**：尚未与参考的 PySceneDetect 实现逐帧对拍。
> - **静音线索并非普遍存在**：一个 2020 年 ITV2 录制在 −35 至 −25 dB 的任何阈值下都只命中 14 个边界中的 4 个。
>   这究竟是频道属性、年代属性还是该文件的个例，尚未查清；`survey_silence.py` 就是为分层抽样回答这个问题而写的。
> - **排期记录与实际播出不是一一对应**：交付 metadata 中约 21% 是 Sky AdSmart 定向投放占位记录，
>   与真实广告共享时间戳（仅出现在 5STAR 与 Channel 5）。`silence_detect.py` 在构建边界时折叠同刻记录、
>   优先保留真实广告；对于两条真实广告共享时间戳的约 2% 情形，该折叠是有损的。
