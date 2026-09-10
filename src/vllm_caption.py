#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video captioning client + concurrency benchmark (Q4) for Qwen3.8-27B on vLLM.
STANDARD LIBRARY ONLY - no openai, no requests.

Why a separate client
---------------------
Stephen's qwen38-caption-local.py depends on the `openai` package. On this account
neither venv is usable and the system python3 has no openai (checked 21 Aug 2026).
Calling vLLM is just an HTTP POST, so urllib is enough. The request body is
field-for-field identical to his script (see to_request_args) so that measured
throughput is comparable with the 2.45 RTFX he reported: reproduce first, change after.

Request parameters taken from qwen38-caption-local.py, lines 84-100:
    temperature 1.0 / top_p 0.8 / top_k 20 / presence_penalty 1.5 /
    chat_template_kwargs {"enable_thinking": true}
(The openai client merges extra_body keys into the request root; they are placed
in the root directly here, which is equivalent.)

Two modes
---------
1. caption : caption each video, writing <video>.json
2. bench   : re-run the same videos at several concurrency levels to measure the
             throughput curve (the evidence for Q4)

Server-side constraints (measured 21 Aug 2026, cannot be worked around)
    - vLLM runs under the datadigipres account; I cannot restart it
    - --allowed-local-media-path is pinned to
      /home/shihan/storage/video/silence_cut_test, so clips must be placed there
    - sampling frame rate is fixed at fps=2.0 (server-side --mm-processor-kwargs)
    - enable_thinking IS a client-side parameter, so a thinking ablation is possible

Usage
-----
    # caption a batch
    python3 vllm_caption.py caption /home/shihan/storage/video/silence_cut_test \
        --out ~/caps_v0 --workers 4

    # concurrency benchmark: same clips at 1/2/4/8/16 workers
    python3 vllm_caption.py bench /home/shihan/storage/video/silence_cut_test \
        --sweep 1,2,4,8,16 --limit 16 --out ~/bench_out

    # thinking ablation (client-side; no server restart needed)
    python3 vllm_caption.py caption <dir> --out ~/caps_nothink --no-thinking
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ENDPOINT = "http://localhost:8000"
MODEL = "Qwen3.8-27B-NVFP4"
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}

# van Strien / Stephen's default prompt, written for a silent-film archive
# (the timestamps variant even mentions title cards). An advert-context rewrite
# belongs in a separate prompt file passed with --prompt-file, kept in sync
# between the code layer and the documentation layer with a bumped prompt_version.
DEFAULT_PROMPT = ("Describe this video: the setting, the people, what happens over its course, "
                  "and any text that appears on screen.")


def to_request_args(video: Path, prompt: str, model: str, max_tokens: int,
                    thinking: bool) -> dict:
    """Build the request body. Fields match Stephen's qwen38-caption-local.py exactly."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": f"file://{video.resolve()}"}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 1.0,
        "top_p": 0.8,
        "top_k": 20,
        "presence_penalty": 1.5,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }


def post(endpoint: str, payload: dict, timeout: int = 1800) -> dict:
    """One POST to /v1/chat/completions, standard library only."""
    req = urllib.request.Request(
        f"{endpoint}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer EMPTY"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def probe_duration(path: Path) -> float | None:
    """Video duration, used to compute RTFX (material seconds / wall-clock seconds)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=120)
        return round(float(out.stdout.strip()), 2)
    except (ValueError, subprocess.SubprocessError):
        return None


def collect(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found.extend(sorted(q for q in p.rglob("*") if q.suffix.lower() in VIDEO_EXT))
        elif p.is_file() and p.suffix.lower() in VIDEO_EXT:
            found.append(p)
    return found


def one(video: Path, args, prompt: str, out_dir: Path | None) -> dict:
    """Caption one video. Failures also return a record, carrying an `error` field."""
    t0 = time.time()
    rec = {"video": video.name, "path": str(video.resolve()),
           "duration_s": probe_duration(video)}
    try:
        resp = post(args.endpoint,
                    to_request_args(video, prompt, args.model, args.max_tokens,
                                    not args.no_thinking),
                    timeout=args.timeout)
        msg = resp["choices"][0]["message"]
        usage = resp.get("usage", {})
        rec.update({
            "caption": (msg.get("content") or "").strip(),
            "reasoning": (msg.get("reasoning_content") or "").strip(),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "model": resp.get("model", args.model),
        })
    except (urllib.error.URLError, KeyError, TimeoutError, OSError) as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    rec["wall_s"] = round(time.time() - t0, 2)
    if rec.get("duration_s") and rec["wall_s"]:
        # RTFX > 1 means slower than real time (Stephen reported 2.45)
        rec["rtfx"] = round(rec["wall_s"] / rec["duration_s"], 2)
    if out_dir is not None and "error" not in rec:
        (out_dir / f"{video.stem}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def run_batch(videos: list[Path], args, prompt: str, workers: int,
              out_dir: Path | None) -> dict:
    """Run one batch at a given concurrency level; return summary metrics."""
    t0 = time.time()
    recs = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, v, args, prompt, out_dir): v for v in videos}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            recs.append(r)
            tag = "ERR " if "error" in r else "ok  "
            print(f"  [{i}/{len(videos)}] {tag}{r['video']:<48} "
                  f"{r['wall_s']:>7.1f}s  rtfx={r.get('rtfx', '-')}"
                  f"  ct={r.get('completion_tokens', '-')}")
    wall = time.time() - t0
    ok = [r for r in recs if "error" not in r]
    material = sum(r["duration_s"] for r in ok if r.get("duration_s"))
    ctok = sum(r["completion_tokens"] or 0 for r in ok)
    return {
        "workers": workers, "n": len(videos), "ok": len(ok), "failed": len(recs) - len(ok),
        "wall_s": round(wall, 1),
        "material_s": round(material, 1),
        # Effective RTFX: wall clock / total material. Below 1 means faster than
        # real time overall, which is the whole point of concurrency.
        "effective_rtfx": round(wall / material, 3) if material else None,
        "throughput_x": round(material / wall, 2) if wall else None,
        "completion_tokens": ctok,
        "tok_per_s": round(ctok / wall, 1) if wall else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["caption", "bench"])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", help="output directory (required for caption mode)")
    ap.add_argument("--endpoint", default=ENDPOINT)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--prompt-file", help="prompt text file; defaults to van Strien's prompt")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--sweep", default="1,2,4,8,16", help="concurrency levels for bench mode")
    ap.add_argument("--limit", type=int, default=0, help="use only the first N videos")
    ap.add_argument("--no-thinking", action="store_true",
                    help="disable enable_thinking (client-side, so ablation is possible)")
    ap.add_argument("--list", action="store_true", help="list the videos and exit")
    a = ap.parse_args()

    videos = collect(a.paths)
    if a.limit:
        videos = videos[: a.limit]
    print(f"videos: {len(videos)}")
    if a.list or not videos:
        for v in videos[:20]:
            print("  ", v)
        return

    prompt = Path(a.prompt_file).read_text(encoding="utf-8").strip() if a.prompt_file \
        else DEFAULT_PROMPT
    print(f"prompt: {prompt[:70]}{'...' if len(prompt) > 70 else ''}")
    print(f"thinking: {not a.no_thinking}   model: {a.model}   endpoint: {a.endpoint}\n")

    if a.mode == "caption":
        if not a.out:
            sys.exit("caption mode requires --out")
        out_dir = Path(a.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        s = run_batch(videos, a, prompt, a.workers, out_dir)
        print("\n" + json.dumps(s, ensure_ascii=False))
        return

    # ---- bench: same videos, several concurrency levels ----
    out_dir = Path(a.out) if a.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for w in [int(x) for x in a.sweep.split(",")]:
        print(f"\n===== workers = {w} =====")
        # captions are not written here (out_dir=None) so disk IO does not skew timing
        rows.append(run_batch(videos, a, prompt, w, None))

    print("\n===== Q4 concurrency benchmark =====")
    print(f"{'workers':>7} {'ok/n':>8} {'wall_s':>9} {'throughput':>11} {'eff_rtfx':>9} {'tok/s':>8}")
    for r in rows:
        print(f"{r['workers']:>7} {r['ok']}/{r['n']:>5} {r['wall_s']:>9.1f} "
              f"{r['throughput_x']:>10}x {r['effective_rtfx']:>9} {r['tok_per_s']:>8}")
    base = rows[0]["throughput_x"] if rows and rows[0]["throughput_x"] else None
    if base:
        print("\nspeed-up vs 1 worker:",
              "  ".join(f"{r['workers']}w {r['throughput_x'] / base:.2f}x" for r in rows))
        best = max(rows, key=lambda r: r["throughput_x"] or 0)
        # Re-estimate the whole archive from measured throughput:
        # 134,000 unique adverts x 26.25s measured mean duration
        material_h = 134000 * 26.25 / 3600
        days = material_h / best["throughput_x"] / 24
        print(f"\nbest concurrency {best['workers']} -> {best['throughput_x']}x real time; "
              f"whole archive ({material_h:,.0f}h of material) would take about {days:.1f} days "
              f"(Stephen's sequential estimate was 76 days; 99.8 days after the "
              f"measured-duration correction)")
    if out_dir:
        (out_dir / "bench_summary.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwritten: {out_dir / 'bench_summary.json'}")


if __name__ == "__main__":
    main()
