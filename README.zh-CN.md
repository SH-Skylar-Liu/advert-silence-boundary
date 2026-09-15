# 广告边界检测：静音 + 场景切换 pipeline

**版本 1.2**（2026-09-15）· [English](README.md) · 中文

> ⚠️ **私有仓库，含内部路径。**
> 本仓库的命令和默认值使用的是真实的 BFI 服务器路径与账户名，这样协作者拿到后可以直接运行，
> 不必逐条替换。**如果将来要公开**（SSH Open Marketplace、JOHD 或任何对外发布），
> 必须先把所有 `/home/shihan/...` 路径、账户名和主机名替换成占位符。

本工具以播出排期表为先验，从整段节目录制中确定性地切分出每一条电视广告。
找边界时不使用任何机器学习：`ffmpeg` 检测音频缺失，场景切换滤镜定位首帧。
视觉语言模型只在切分完成后使用，用来描述每条广告的内容。

**这种分层本身就是方法论主张**：语料构建是确定性的、可复现的，模型只在诠释环节介入。

---

## 方法

**A 阶段：粗定位。** 用 `ffmpeg silencedetect` 找出录制中所有静音段，再把排期表
（每条广告的开始时间和在广告段内的顺序）沿录制滑动，找到让排期的时长排列恰好落在静音上的偏移量 `T`。
**方法之所以成立，靠的是把排期和静音对应起来这一步**；单独做静音检测，误报太多。

**广告段的接受规则。** 每个广告段拟合出的偏移量必须和相邻广告段保持一致，因为长录制里静音很密，
错误的偏移量也可能碰巧命中很多边界。在此基础上，至少一半边界（`--min-match-rate 0.5`）、
且至少 3 个边界（`--min-matches 3`）命中静音，这个广告段才会被接受。
没有命中静音的边界，用排期和拟合出的偏移量补齐，并在台账中标记为 `start_source=schedule`。
原来"所有边界都命中才接受"的规则仍然可以用：`--min-match-rate 1 --no-fill-missing`。

**B 阶段：首帧精修。** 静音中点通常比真正的首帧早几帧，前一条广告的画面会混进本条开头。
所以每个起点都会前推到其后 **1 秒内**出现的第一个场景切换。窗口再大，就会误抓到广告内部的镜头切换。

**切片的起止点。** 每个切片从本条广告精修后的起点，到**下一条广告精修后的起点**
（`--end-mode next-start`，默认）。如果改为以静音中点作为结尾（`--end-mode midpoint`），
起止两端的参照不一致，下一条广告的修正量就会作为误差算进本条的时长里。
**每个广告段的最后一条广告**后面没有下一条，它的结尾取段末静音的**开始点**（`--last-end silence-start`）。
原因是段末过渡静音约 2 秒，取中点时，广告实际已经结束约 1 秒。

**两个检测后端。** `--backend ffmpeg` 使用 ffmpeg 的 `select='gt(scene,0.3)'` 滤镜，不需要安装任何 Python 包。
`--backend scenedetect` 使用 PySceneDetect 的 `ContentDetector`，需要 OpenCV。
`--backend auto` 在能导入 scenedetect 时使用它。
**下文所有公布的数字都来自 ffmpeg 后端。** scenedetect 后端如果使用默认的 `min_scene_len`，
结果比完全不精修还差（见下文），不加 `--min-scene-len` 时不要使用。

---

## 实测结果

**样本：** 2020 年 8 月 29–30 日的 12 个录制，4 个频道（ITV1 HD、CH4、Channel 5、ITV2），共 361 个切点。
运行编号 `R0`，2026-09-15，使用"全部命中才接受"的规则（`--min-match-rate 1 --no-fill-missing`），只生成计划、不切视频。

### 切点精度

英国电视广告按固定时长档位售卖，所以每个切片**应有的时长事先就知道**。
因此不用人工逐帧核对也能测量误差：切出的时长与最近档位之差的绝对值就是误差。
**档位来自广告市场，不来自检测器本身，所以这是独立验证，不是自我印证。**

| 变体 | 中位 | 平均 | 最大 | <0.2 s |
|---|---|---|---|---|
| A：静音中点 → 下一条的静音中点 | 0.122 s（3.0 帧） | 0.241 s | 2.369 s | 60% |
| B：精修起点 → 下一条的静音中点 | 0.077 s（1.9 帧） | 0.205 s | 2.395 s | 69% |
| **C：精修起点 → 下一条的精修起点** | **0.042 s（1.0 帧）** | **0.195 s** | 2.395 s | **72%** |

**A → C 的中位误差改善 2.9 倍。** 样本从 8 个录制扩大到 12 个，这个估计保持不变。
早先单个录制测得的约 9 倍是样本效应，不要引用。

精修并不是对所有录制都一样有效。静音中点本来就准的录制（A 的中位误差约 0.05 s）上，B 和 C 完全相同；
静音中点偏差大的录制（约 0.15–0.21 s）上，C 能把中位误差压到 0.020–0.024 s。
有一个录制上 B 比 A 还差，这就是切片两端必须使用同一类参照点的原因。

平均误差始终是中位误差的五倍左右。说明有少数切点偏得很远，原因不是精修能解决的
（可能是对齐错误，也可能是广告本身不是标准档位时长）。这一点尚未诊断。

### 检测后端对比

同样 12 个录制、同样规则，只换精修后端：

| 运行 | 后端 | 精修条数 | C 中位 | C <0.2 s | 被精修切点的偏移 |
|---|---|---|---|---|---|
| R0 | ffmpeg，阈值 0.3 | 156 | **0.042 s** | 72% | 41–988 ms，中位 211 ms；118 条小于 600 ms |
| R1 | scenedetect，默认参数 | 47 | 0.146 s | 57% | 597–988 ms，中位 785 ms；1 条小于 600 ms |

使用默认参数时，PySceneDetect **比完全不精修还差**（A 的中位误差是 0.122 s）。
原因是 `ContentDetector` 默认 `min_scene_len=15` 帧，再加上窗口起点被当作一个场景的开始，
窗口前 0.6 秒内的任何切换都报不出来。而真正的首帧通常就在这个区间，
于是小幅修正全部丢失，剩下能报出来的切换则落在了广告内部。
`--min-scene-len 1` 可以去掉这个限制，其实际效果（`R2`）尚待测量。

### 产出率

在"全部命中才接受"的规则下，12 个录制共排期 706 条广告，切出 361 条（51%）。
单个录制从 13% 到 93% 不等，同一天同一频道的不同时段也差距很大。
按排期补齐的规则（v1.1 起默认开启）就是为了解决这个问题，但它能提高多少产出率、
补齐的切点（`start_source=schedule`）精度如何，**都还没有在服务器上测量**。

### 推理吞吐

单张 H100 上运行 Qwen3.8-27B-NVFP4，按 2 fps 采样生成视频描述：

| 并发 | 吞吐 | 有效 RTFX | 加速 |
|---|---|---|---|
| 1 | 0.45× 实时 | 2.199 | 1.00× |
| 2 | 0.80× | 1.244 | 1.78× |
| 4 | 1.56× | 0.642 | **3.47×** |
| 8 | 1.71× | 0.586 | 3.80× |

**4 路是拐点，8 路已经饱和**，瓶颈在 GPU，不在服务端的并发上限。
全库（134,000 条唯一广告 × 26.25 秒 ≈ 977 小时素材）约需 **24 天**，原先按顺序推理估算为 76 天。

---

## 仓库结构

```
src/
  csv_to_metadata.py      排期 CSV -> metadata JSON（按广告段分组、广告起点）
  silence_detect.py       A 阶段：silencedetect + 排期对齐（可作为模块导入）
  refine_first_frame.py   B 阶段：1 秒内的第一个场景切换，两个后端
  cut_adverts.py          单个录制：对齐、精修、生成切片计划或切片、写台账
  batch_cut.py            批量录制：并行、可断点续跑，每个录制一份台账
  compare_end_modes.py    对一份或多份台账做消融：变体 A / B / C
  survey_silence.py       按频道 × 年份分层抽样，看静音线索在哪些情况下成立
  vllm_caption.py         视频描述客户端 + 并发基准测试（只用标准库）
examples/
  example_cut_ledger.csv  合成台账，没有档案数据也能跑消融
docs/
  results_2026-09.md      完整测量记录，包括失败的尝试
```

## 依赖

`PATH` 上需要有 `ffmpeg` 和 `ffprobe`，以及 Python 3.10+。**ffmpeg 后端不需要安装任何 Python 包。**

scenedetect 后端需要 PySceneDetect 和 OpenCV。只安装 headless 版本的 OpenCV，
并且虚拟环境要建在本地盘上（venv 放在 NFS 上时 import 会卡住）：

```bash
uv venv ~/venv-scd --python 3.13
uv pip install --python ~/venv-scd/bin/python scenedetect==0.7.1 opencv-python-headless==5.0.0.93
```

不要在同一个环境里同时安装 `opencv-python` 和 `opencv-python-headless`。

## 复现步骤

### 单个录制

```bash
# 1. 排期 CSV -> metadata JSON（已有 metadata 时跳过）
python3 src/csv_to_metadata.py "2024-03-26_ITV1HD.csv" -o breaks.json

# 2. 查看录制的静音结构，以及排期如何与之对齐
python3 src/silence_detect.py RECORDING.mp4 --metadata breaks.json

# 3. 只生成切片计划、不写视频，并保存台账
python3 src/cut_adverts.py RECORDING.mp4 --metadata breaks.json \
    --outdir /tmp/cuts --cut-csv plan.csv --backend ffmpeg --dry-run

# 4. 用台账复现 A/B/C 消融（不需要重新解码）
python3 src/compare_end_modes.py plan.csv --per-clip

# 5. 计划确认无误后，真正切片
python3 src/cut_adverts.py RECORDING.mp4 --metadata breaks.json \
    --outdir /path/to/output --cut-csv plan.csv --backend ffmpeg
```

用自带的示例台账可以直接跑消融：

```bash
python3 src/compare_end_modes.py examples/example_cut_ledger.csv
```

### 上文的 R0 运行

```bash
cd src
python3 batch_cut.py ~/run_step0/src --outroot ~/run_step0/R0_ffmpeg \
    --backend ffmpeg --min-match-rate 1 --no-fill-missing --jobs 8 --dry-run

# 把各录制的台账拼接成一份，再统一跑消融
awk 'FNR==1 && NR!=1 {next} 1' ~/run_step0/R0_ffmpeg/*/cut_plan.csv > R0_ffmpeg.csv
python3 compare_end_modes.py R0_ffmpeg.csv
```

R1 改用 `--backend scenedetect`；R2 在此基础上再加 `--min-scene-len 1`。这两次都要用 venv 里的 Python（`~/venv-scd/bin/python`）。
拼接后的台账必须用 v1.2 及以后的 `compare_end_modes.py`：早期版本只按广告段编号分组，
不同录制里编号相同的广告段会被合并在一起。

BFI 工作站上，源录制和对应的 metadata 位于 `/home/shihan/storage/video/unique_advert_source_videos/`，
按文件名配对（`YYYY-MM-DD_CHANNEL_HH:MM:SS.mp4` 与 `..._metadata.json`）。
**文件名含冒号**，在 Linux 上合法，在 Windows 上不合法。

## 值得保留的设计决定

**台账同时记录原始值和派生值。** `--cut-csv` 会写入 A 阶段的静音中点（`start_silence_mid_s`）、
精修后的切点（`start_s`）、起点的来源（`start_source`），以及精修时用的检测设置（`refine_backend`、`min_scene_len`）。
因此 A/B/C 消融**不需要重新切片、也不需要重新解码**，而且每个数字都能追溯到产生它的设置。

**强制指定的设置无法生效时，直接报错。** 指定 `--backend scenedetect` 但 scenedetect 无法导入，
或者在 ffmpeg 后端上指定 `--min-scene-len`，程序都会停止运行，不会悄悄退回默认行为。
如果悄悄退回，产出的台账看起来像是按要求的配置跑出来的，实际上并不是。

**视频一律转码，绝不 stream copy。** stream copy 会让 MP4 的 `duration` 与 `source_duration` 不一致，
导致下游 vLLM 推理时帧与时间对不上。切片时还显式指定了 `-map 0:v:0 -map 0:a:0`，
因为交付的文件里有不止一条视频流，还有若干数据流。

## 已知局限

- **scenedetect 后端尚未验证。** 默认参数有害（R1）；`--min-scene-len 1` 的效果待测（R2）。在此之前请用 `--backend ffmpeg`。
- **ffmpeg 场景阈值（0.3）没有逐帧标定过**，目前的依据只有上文的时长收敛结果。
- **按排期补齐尚未测量。** 它是默认开启的，但产出率提升多少、补齐切点的精度如何都还不知道。报告结果时，`start_source=schedule` 的切点要单独统计。
- **静音线索并非处处存在。** 一个 2020 年的 ITV2 录制，阈值从 −35 dB 调到 −25 dB，14 个边界始终只命中 4 个。
  这是频道的特性、年代的特性，还是这个文件的个例，尚不清楚；`survey_silence.py` 就是为了通过分层抽样回答这个问题。
- **排期记录与实际播出并非一一对应。** 交付的 metadata 中约 21% 的广告记录是 Sky AdSmart 定向投放的占位记录，
  和真实广告共用同一个时间戳（只出现在 5STAR 和 Channel 5）。`silence_detect.py` 构建边界时会合并同一时间戳的记录，
  优先保留真实广告；但如果两条真实广告共用一个时间戳（约 2%），合并会丢失其中一条。

## 更新记录

**1.2 — 2026-09-15**
- scenedetect 后端新增 `--min-scene-len`（不指定时沿用 PySceneDetect 默认值），并记入台账；与 ffmpeg 后端同时使用会报错。
- `compare_end_modes.py` 改为按"录制 + 广告段"分组，拼接的台账可以正确计算。
- README 拆分为英文版和中文版；实测结果更新为 12 个录制的合并数字和后端对比。

**1.1 — 2026-09-14**
- 新增 `--backend auto|ffmpeg|scenedetect`；强制使用 scenedetect 但没有 OpenCV 时直接报错；台账新增 `refine_backend` 列。
- 广告段按命中率接受，缺失边界按排期补齐（`--min-match-rate`、`--min-matches`、`--no-fill-missing`；台账新增 `start_source` 列）。
- `batch_cut.py` 把以上参数全部传给 `cut_adverts.py`。

**1.0 — 2026-09-10 至 09-12**
- 静音 + 场景切换 pipeline、批量运行脚本、A/B/C 消融。
- 每个广告段最后一条广告的结尾改为段末静音的开始点，不再用中点。
