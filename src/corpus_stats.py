#!/usr/bin/env python3
"""Step 0 descriptive statistics of the advert schedule (supervision of 20 Oct 2026).
Step 0 描述性统计：频道、年份、品类、广告主、时长、时段、广告位、前后节目、impacts、
addressable 占位行，以及（可选）切分台账对各组的覆盖率。

    # BFI schedule exports (richest: major/mid/minor category, impacts, log station)
    python3 corpus_stats.py --csv /path/to/csv_dir --out ~/stats_step0

    # add the recordings (what has been digitised) and the October ledger (what was cut)
    python3 corpus_stats.py --csv CSV_DIR --src $SRC --run ~/runs/v17 --drill "Food" --out ~/stats_step0

    # metadata JSON only: minor category and duration, no major category or impacts
    python3 corpus_stats.py --src $SRC --out ~/stats_json

Writes OUT/report.html (charts; open in Firefox on Data14) and OUT/tables/*.csv
(every number behind every chart). Standard library only, no internet needed.

Counting rules (also printed at the top of the report):
- one AIRING = one (channel, date, start time, identifier); a row repeated across log
  stations or across overlapping recordings is counted once;
- one UNIQUE ADVERT = one identifier (Film Code / unique_id); a 10 s and a 30 s
  version have different identifiers and count as two (Ch04 §4.3);
- addressable placeholders (SKYADSMART / C4ADDRESSABLE / ITVADDRESSABLE) are reported
  on their own and left out of every other count.
"""
import argparse
import csv
import glob
import html
import json
import os
import statistics
from collections import Counter, defaultdict

PLACEHOLDER_KINDS = ("SKYADSMART", "C4ADDRESSABLE", "ITVADDRESSABLE")
JSON_KEYS = Counter()          # advert-level keys seen in the metadata JSON / JSON 里出现过的字段

# BFI export column -> internal field / BFI 导出 CSV 的列名 -> 内部字段名
CSV_FIELDS = {
    "Channel": "channel", "Date": "date", "Start time": "time", "Film Code": "code",
    "Break Code": "break_code", "Advertiser": "advertiser", "Brand": "brand",
    "Agency": "agency", "Holding Company": "holding", "BARB Prog Before": "prog_before",
    "BARB Prog After": "prog_after", "Sales House": "sales_house",
    "Major category": "major", "Mid category": "mid", "Minor category": "minor",
    "All PIB rel": "pib_rel", "All PIB pos": "pib_pos", "Log Station (2010-)": "station",
}


# ---------------------------------------------------------------- loading / 读取数据

def placeholder_kind(code: str) -> str:
    u = str(code or "").upper().replace(" ", "")
    return next((k for k in PLACEHOLDER_KINDS if k in u), "")


def id_duration(code: str):
    """Length the identifier states: its last three characters (silence_detect.id_duration).
    标识符最后三位 = 广告时长（秒）。"""
    tail = str(code or "").strip()[-3:]
    return int(tail) if tail.isdigit() and int(tail) > 0 else None


def iso_date(d: str) -> str:
    d = d.strip()
    if "/" in d:                                   # 26/03/2024 -> 2024-03-26
        dd, mm, yy = d.split("/")
        return f"{yy}-{int(mm):02d}-{int(dd):02d}"
    return d[:10]


def secs(t: str):
    try:
        h, m, s = (int(float(x)) for x in t.strip().split(":"))
        return h * 3600 + m * 60 + s
    except ValueError:
        return None


def to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def finish(r: dict) -> dict:
    """Derived fields shared by both sources / 两种来源共用的派生字段。"""
    r["year"] = r["date"][:4]
    s = secs(r["time"])
    r["t"] = s
    r["hour"] = (s // 3600) % 24 if s is not None else None   # BARB days may run past 24:00
    r["kind"] = placeholder_kind(r["code"])
    if not r.get("duration"):
        r["duration"] = id_duration(r["code"])
    return r


def read_rows(path: str) -> list:
    for enc in ("utf-8-sig", "cp1252"):            # BFI exports are sometimes cp1252
        try:
            with open(path, encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    return []


def load_csv(paths: list) -> tuple:
    """BFI schedule exports, one row per airing. Returns (records, impacts column names).
    读取 BFI 排期 CSV；同时找出所有以 Impacts 开头的受众列。"""
    files = []
    for p in paths:
        p = os.path.expanduser(p)
        files += sorted(glob.glob(os.path.join(p, "**", "*.csv"), recursive=True)) if os.path.isdir(p) else [p]
    recs, imp_cols, skipped = [], [], 0
    for fp in files:
        rows = read_rows(fp)
        if not rows or "Film Code" not in rows[0]:
            skipped += 1                           # not a schedule export / 不是排期表
            continue
        for c in rows[0]:
            if c.lower().startswith("impacts") and c not in imp_cols:
                imp_cols.append(c)
        for row in rows:
            if not (row.get("Film Code") or "").strip():
                continue
            r = {v: (row.get(k) or "").strip() for k, v in CSV_FIELDS.items()}
            r["date"] = iso_date(r["date"])
            r["impacts"] = {c: to_float(row.get(c, "")) for c in imp_cols if row.get(c, "").strip()}
            r["file"] = os.path.basename(fp)
            recs.append(finish(r))
    print(f"CSV: {len(files) - skipped} schedule files read, {skipped} other CSV files skipped, {len(recs):,} rows")
    return recs, imp_cols


def load_json(src: str) -> tuple:
    """<name>_metadata.json beside the recordings. Returns (records, recordings).
    读取录像旁的 metadata JSON；同时记下每个录像文件的频道、日期、时长。"""
    recs, recordings = [], []
    for fp in sorted(glob.glob(os.path.join(os.path.expanduser(src), "*_metadata.json"))):
        d = json.load(open(fp, encoding="utf-8"))
        vi = d.get("video_info", {})
        name = os.path.basename(fp)[: -len("_metadata.json")]
        recordings.append({"name": name, "channel": vi.get("channel", ""), "date": vi.get("date", ""),
                           "year": str(vi.get("date", ""))[:4], "hours": (vi.get("duration_seconds") or 0) / 3600})
        for br in d.get("ad_breaks", []):
            for ad in br.get("adverts", []):
                JSON_KEYS.update(ad.keys())
                # pick up richer fields if a delivery carries them / 若 JSON 带更多字段则一并读入
                extra = {"major": next((str(v) for k, v in ad.items() if "major" in k.lower()), ""),
                         "mid": next((str(v) for k, v in ad.items() if "mid" in k.lower()), ""),
                         "impacts": {k: to_float(v) for k, v in ad.items() if "impact" in k.lower() and to_float(v) is not None}}
                recs.append(finish({**extra,
                    "channel": vi.get("channel", ""), "date": vi.get("date", ""),
                    "time": ad.get("start_time", ""), "code": ad.get("unique_id", ""),
                    "advertiser": ad.get("advertiser", ""), "brand": ad.get("brand", ""),
                    "minor": ad.get("category", ""), "pib_rel": ad.get("position_in_break", ""),
                    "duration": ad.get("duration_seconds"),
                    "prog_before": (br.get("programme_before") or {}).get("title", ""),
                    "prog_after": (br.get("programme_after") or {}).get("title", ""),
                    "file": name,
                }))
    print(f"JSON: {len(recordings):,} recordings, {len(recs):,} advert records")
    return recs, recordings


def dedup(recs: list) -> list:
    """One airing per (channel, date, time, identifier) / 同一次播出只算一次。"""
    seen, out = set(), []
    for r in recs:
        k = (r["channel"], r["date"], r["time"], r["code"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def load_records(csv_paths=None, src=None) -> tuple:
    """Used by viewing_sample.py too. CSV wins when both are given (it has more fields)."""
    imp_cols, recordings = [], []
    if src:
        jrecs, recordings = load_json(src)
    if csv_paths:
        raw, imp_cols = load_csv(csv_paths)
    else:
        raw = jrecs
        imp_cols = sorted({k for r in raw for k in r["impacts"]})
    return raw, dedup(raw), imp_cols, recordings


def load_ledgers(run: str) -> list:
    rows = []
    for fp in sorted(glob.glob(os.path.join(os.path.expanduser(run), "*", "cut_plan.csv"))):
        rows += read_rows(fp)
    print(f"Ledger: {len(rows):,} planned clips in {run}")
    return rows


# ---------------------------------------------------------------- charts / 画图（纯 SVG）

def esc(s) -> str:
    return html.escape(str(s))


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.1f}" if abs(v) < 100 else f"{v:,.0f}"
    return f"{v:,}"


def hbar(items: list, unit: str = "", label_w: int = 280, width: int = 820) -> str:
    """Horizontal bars, one series. items = [(label, value)] / 横向条形图（单一系列）。"""
    if not items:
        return "<p class=note>(no data)</p>"
    vmax = max(v for _, v in items) or 1
    bw, rh = width - label_w - 90, 22
    out = [f'<svg viewBox="0 0 {width} {len(items) * rh + 4}" class=chart role=img>']
    for i, (lab, v) in enumerate(items):
        y, w = i * rh + 2, max(2, bw * v / vmax)
        out.append(f'<g><title>{esc(lab)}: {fmt(v)}{unit}</title>'
                   f'<rect x={label_w - 4} y={y} width={bw + 90} height={rh} class=hit />'
                   f'<text x={label_w - 10} y={y + 15} class=lbl text-anchor=end>{esc(str(lab)[:42])}</text>'
                   f'<rect x={label_w} y={y + 3} width={w:.1f} height={rh - 8} rx=2 class=bar />'
                   f'<text x={label_w + w + 6:.1f} y={y + 15} class=val>{fmt(v)}{unit}</text></g>')
    return "\n".join(out) + "</svg>"


def columns(items: list, unit: str = "", width: int = 820, height: int = 220) -> str:
    """Vertical columns for ordered x (hour, length, bins) / 有序横轴的柱状图。"""
    if not items:
        return "<p class=note>(no data)</p>"
    vmax = max(v for _, v in items) or 1
    pad_l, pad_b, top = 10, 34, 18
    cw = (width - 2 * pad_l) / len(items)
    out = [f'<svg viewBox="0 0 {width} {height}" class=chart role=img>',
           f'<line x1={pad_l} x2={width - pad_l} y1={height - pad_b} y2={height - pad_b} class=axis />']
    for i, (lab, v) in enumerate(items):
        h = (height - pad_b - top) * v / vmax
        x = pad_l + i * cw
        out.append(f'<g><title>{esc(lab)}: {fmt(v)}{unit}</title>'
                   f'<rect x={x:.1f} y={top} width={cw:.1f} height={height - pad_b - top} class=hit />'
                   f'<rect x={x + 1:.1f} y={height - pad_b - h:.1f} width={max(1, cw - 2):.1f} height={h:.1f} rx=2 class=bar />'
                   f'<text x={x + cw / 2:.1f} y={height - pad_b + 14} class=tick text-anchor=middle>{esc(lab)}</text></g>')
    return "\n".join(out) + "</svg>"


def heatmap(rows: list, cols: list, val, cell_fmt=fmt, label_w: int = 230, width: int = 820) -> str:
    """Matrix as shaded cells; shade = value / max of the matrix. val(r, c) -> number.
    热力图：颜色深浅 = 数值 / 全表最大值；鼠标悬停显示数值。"""
    if not rows or not cols:
        return "<p class=note>(no data)</p>"
    vals = {(r, c): val(r, c) for r in rows for c in cols}
    vmax = max([v for v in vals.values() if v] or [1])
    cw, rh = max(34, (width - label_w) / len(cols)), 22
    w = label_w + cw * len(cols)
    out = [f'<svg viewBox="0 0 {w:.0f} {rh * (len(rows) + 1) + 4}" class=chart role=img>']
    for j, c in enumerate(cols):
        out.append(f'<text x={label_w + j * cw + cw / 2:.1f} y=15 class=tick text-anchor=middle>{esc(str(c)[:8])}</text>')
    for i, r in enumerate(rows):
        y = rh * (i + 1)
        out.append(f'<text x={label_w - 8} y={y + 15} class=lbl text-anchor=end>{esc(str(r)[:36])}</text>')
        for j, c in enumerate(cols):
            v = vals[(r, c)]
            t = (v or 0) / vmax
            txt = cell_fmt(v) if v else ""
            out.append(f'<g><title>{esc(r)} × {esc(c)}: {txt or 0}</title>'
                       f'<rect x={label_w + j * cw + 1:.1f} y={y + 1} width={cw - 2:.1f} height={rh - 2} rx=2 '
                       f'class=cell fill-opacity={0.06 + 0.94 * t:.2f} />'
                       + (f'<text x={label_w + j * cw + cw / 2:.1f} y={y + 15} text-anchor=middle '
                          f'class="{"inv" if t > 0.5 else "val"}">{esc(txt)}</text>' if cw >= 40 else "")
                       + "</g>")
    return "\n".join(out) + "</svg>"


# ---------------------------------------------------------------- tables / 输出表格

class Report:
    def __init__(self, out: str):
        self.out, self.parts = out, []
        os.makedirs(os.path.join(out, "tables"), exist_ok=True)

    def table(self, name: str, header: list, rows: list) -> str:
        with open(os.path.join(self.out, "tables", name + ".csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        body = "".join("<tr>" + "".join(f"<td>{esc(fmt(x) if isinstance(x, (int, float)) else x)}</td>" for x in r)
                       + "</tr>" for r in rows[:60])
        more = f"<p class=note>first 60 of {len(rows):,} rows; full table in tables/{name}.csv</p>" if len(rows) > 60 else ""
        return (f"<details><summary>table · tables/{esc(name)}.csv</summary><table><tr>"
                + "".join(f"<th>{esc(h)}</th>" for h in header) + f"</tr>{body}</table>{more}</details>")

    def section(self, title: str, question: str, *blocks: str):
        q = f"<p class=q>{esc(question)}</p>" if question else ""
        self.parts.append(f"<section><h2>{esc(title)}</h2>{q}{''.join(blocks)}</section>")

    def write(self, title: str, intro: str):
        css = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--c1:#2a78d6}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--c1:#3987e5}}
body{background:var(--surface);color:var(--ink);font:14px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px}
h1{font-size:22px}h2{font-size:17px;margin-top:36px;border-top:1px solid var(--grid);padding-top:14px}
.q{color:var(--ink2);margin:4px 0 10px}.note{color:var(--muted);font-size:12px}
svg.chart{width:100%;height:auto;display:block;margin:8px 0}
.bar{fill:var(--c1)}.cell{fill:var(--c1)}.hit{fill:transparent}.hit:hover{fill:var(--grid)}
.lbl{fill:var(--ink2);font-size:12px}.val{fill:var(--ink2);font-size:11px}.inv{fill:#fff;font-size:11px}
.tick{fill:var(--muted);font-size:11px}.axis{stroke:var(--grid)}
table{border-collapse:collapse;font-size:12px;margin:6px 0}td,th{border-bottom:1px solid var(--grid);padding:2px 8px;text-align:left}
summary{cursor:pointer;color:var(--ink2);font-size:12px}ul{padding-left:20px}"""
        page = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>{esc(title)}</title><style>{css}</style><h1>{esc(title)}</h1>{intro}{''.join(self.parts)}")
        with open(os.path.join(self.out, "report.html"), "w", encoding="utf-8") as f:
            f.write(page)


# ---------------------------------------------------------------- statistics / 统计

def uniq_by(rows: list, f: str) -> dict:
    """field value -> set of identifiers / 每个取值下的唯一广告集合。"""
    d = defaultdict(set)
    for r in rows:
        d[r.get(f, "")].add(r["code"])
    return d


def top(counter: Counter, n: int) -> list:
    return [(k or "(blank)", v) for k, v in counter.most_common(n)]


def quantiles(xs: list) -> list:
    if not xs:
        return ["", "", "", "", "", 0]
    xs = sorted(xs)
    q = statistics.quantiles(xs, n=4) if len(xs) > 1 else [xs[0]] * 3
    return [round(xs[0], 1), round(q[0], 1), round(q[1], 1), round(q[2], 1), round(xs[-1], 1), len(xs)]


def breaks_of(ads: list) -> list:
    """Group airings into breaks: a new break at every 'First' (as csv_to_metadata.py does).
    按 First 标记把播出记录分成广告时段，返回每个时段的广告数（不含占位行）。"""
    sizes, cur, last_key = [], 0, None
    for r in sorted(ads, key=lambda r: (r.get("station") or r["channel"], r["date"], r["t"] or 0)):
        key = (r.get("station") or r["channel"], r["date"])
        first = r["pib_rel"].lower() == "first" or r.get("pib_pos") == "1"
        if (first or key != last_key) and cur:
            sizes.append(cur)
            cur = 0
        last_key = key
        cur += 0 if r["kind"] else 1
    if cur:
        sizes.append(cur)
    return sizes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", nargs="+", help="BFI schedule export CSV files or directories (searched recursively)")
    ap.add_argument("--src", help="directory of <name>_metadata.json (the recordings' metadata)")
    ap.add_argument("--run", help="run directory with */cut_plan.csv, e.g. the October full run")
    ap.add_argument("--drill", nargs="*", default=[], help="major (or minor) categories to drill into")
    ap.add_argument("--top", type=int, default=25, help="rows in the top-N charts")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not (a.csv or a.src):
        ap.error("give --csv and/or --src")

    raw, recs, imp_cols, recordings = load_records(a.csv, a.src)
    rep, N = Report(os.path.expanduser(a.out)), a.top
    ads = [r for r in recs if not r["kind"]]                   # broadcast airings / 真实播出
    ph = [r for r in recs if r["kind"]]                        # addressable placeholders / 占位行
    has_major = any(r.get("major") for r in ads)
    cat = "major" if has_major else "minor"                    # JSON has only the minor category
    years = sorted({r["year"] for r in recs})
    chans = [c for c, _ in Counter(r["channel"] for r in ads).most_common()]

    # ---- 0. overview / 总览 ----
    uniq = {r["code"] for r in ads}
    ov = [
        ("source", "BFI schedule CSV" if a.csv else "metadata JSON (no major category, no impacts)"),
        ("rows read", len(raw)), ("airings after de-duplication", len(recs)),
        ("rows collapsed as duplicates", len(raw) - len(recs)),
        ("addressable placeholder airings", len(ph)), ("broadcast airings", len(ads)),
        ("unique adverts (identifiers)", len(uniq)),
        ("brands", len({r["brand"] for r in ads})), ("advertisers", len({r["advertiser"] for r in ads})),
        ("channels", len(chans)), ("log stations", len({r.get("station") for r in ads if r.get("station")})),
        ("first date", min(r["date"] for r in recs)), ("last date", max(r["date"] for r in recs)),
        ("days with any airing", len({r["date"] for r in recs})),
    ]
    if recordings:
        ov += [("recordings (metadata JSON)", len(recordings)),
               ("hours recorded", round(sum(x["hours"] for x in recordings), 1))]
    if JSON_KEYS:
        ov.append(("fields in metadata JSON adverts", ", ".join(f"{k} ({v:,})" for k, v in JSON_KEYS.most_common())))
    miss = [(f, f"{sum(1 for r in ads if not r.get(f)) / max(1, len(ads)):.1%}")
            for f in ("advertiser", "brand", "major", "mid", "minor", "prog_before", "pib_rel", "station")]
    print("\nOVERVIEW")
    for k, v in ov:
        print(f"  {k:<36} {fmt(v) if isinstance(v, int) else v}")
    rep.section("0 · Overview / 总览", "",
                "<table>" + "".join(f"<tr><td>{esc(k)}</td><td>{esc(fmt(v) if isinstance(v, int) else v)}</td></tr>" for k, v in ov) + "</table>",
                "<p class=note>Blank rate among broadcast airings / 字段空缺率：" + ", ".join(f"{k} {v}" for k, v in miss) + "</p>",
                rep.table("overview", ["item", "value"], ov))

    # ---- 1. channels and stations / 频道与地区台 ----
    by_ch = Counter(r["channel"] for r in ads)
    uq_ch = {c: len(s) for c, s in uniq_by(ads, "channel").items()}
    st = Counter((r["channel"], r.get("station", "")) for r in raw if not r["kind"] and r.get("station"))
    rep.section("1 · Channels / 频道分布",
                "Daniel: how many channels, and are they UK-wide or England only? The log stations show which regional feed each export row comes from.",
                hbar(top(by_ch, N)),
                rep.table("channels", ["channel", "airings", "unique_adverts", "placeholder_airings"],
                          [(c, by_ch[c], uq_ch[c], sum(1 for r in ph if r["channel"] == c)) for c in chans]),
                "<h3>Log stations / 地区台（判断全英还是仅英格兰）</h3>" if st else "",
                hbar(sorted(((f"{c} · {s}", v) for (c, s), v in st.items()), key=lambda x: -x[1])[:40]) if st else "",
                rep.table("stations", ["channel", "log_station", "rows"], [(c, s, v) for (c, s), v in st.most_common()]) if st else "")

    # ---- 2. coverage by year / 年份覆盖 ----
    days = defaultdict(set)
    for r in recs:
        days[(r["channel"], r["year"])].add(r["date"])
    yc = Counter((r["channel"], r["year"]) for r in ads)
    blocks = [heatmap(chans, years, lambda c, y: yc[(c, y)]),
              "<p class=note>Broadcast airings per channel and year. Next: days with any airing.</p>",
              heatmap(chans, years, lambda c, y: len(days[(c, y)])),
              rep.table("channel_year", ["channel", "year", "airings", "days_with_airings"],
                        [(c, y, yc[(c, y)], len(days[(c, y)])) for c in chans for y in years])]
    if recordings:
        rh_ = defaultdict(float)
        for x in recordings:
            rh_[(x["channel"], x["year"])] += x["hours"]
        rch = sorted({x["channel"] for x in recordings})
        ryr = sorted({x["year"] for x in recordings})
        blocks += ["<h3>Hours of recording delivered / 已交付录像小时数（= 已数字化可用部分）</h3>",
                   heatmap(rch, ryr, lambda c, y: rh_[(c, y)]),
                   rep.table("recordings_channel_year", ["channel", "year", "recordings", "hours"],
                             [(c, y, sum(1 for x in recordings if (x["channel"], x["year"]) == (c, y)), round(rh_[(c, y)], 1))
                              for c in rch for y in ryr])]
    rep.section("2 · Years covered / 年份覆盖", "Daniel: which years do you have access to now, and how much of each? (2016–2020 still on tape.)", *blocks)

    # ---- 3. addressable placeholders / 可寻址广告占位行 ----
    pk = Counter((r["channel"], r["kind"]) for r in ph)
    rep.section("3 · Addressable placeholders (smart ads) / 占位行",
                "How many, which channels, which years? Their share decides whether airing-weighted figures need a flag.",
                hbar([(f"{c} · {k}", v) for (c, k), v in pk.most_common()]),
                heatmap(chans, years, lambda c, y: sum(1 for r in ph if r["channel"] == c and r["year"] == y)),
                rep.table("placeholders", ["channel", "kind", "airings", "share_of_channel_rows"],
                          [(c, k, v, f"{v / (v + by_ch[c]):.1%}") for (c, k), v in pk.most_common()]))

    # ---- 4. categories / 品类 ----
    cat_air = Counter(r[cat] for r in ads)
    cat_uq = Counter()
    for code, c in {r["code"]: r[cat] for r in ads}.items():
        cat_uq[c] += 1
    top_cats = [c for c, _ in cat_air.most_common(15)]
    yr_tot = Counter(r["year"] for r in ads)
    ch_tot = Counter(r["channel"] for r in ads)
    blocks = [f"<h3>{cat} category, airings / 播出次数</h3>", hbar(top(cat_air, N)),
              f"<h3>{cat} category, unique adverts / 唯一广告数</h3>", hbar(top(cat_uq, N)),
              rep.table(f"{cat}_category", ["category", "airings", "unique_adverts", "airings_per_advert"],
                        [(c, v, cat_uq[c], round(v / max(1, cat_uq[c]), 1)) for c, v in cat_air.most_common()]),
              "<h3>Share of each year's airings / 各年份内占比（%）</h3>",
              heatmap(top_cats, years, lambda c, y: 100 * sum(1 for r in ads if r[cat] == c and r["year"] == y) / max(1, yr_tot[y]),
                      cell_fmt=lambda v: f"{v:.0f}"),
              "<h3>Share of each channel's airings / 各频道内占比（%）</h3>",
              heatmap(top_cats, chans, lambda c, ch: 100 * sum(1 for r in ads if r[cat] == c and r["channel"] == ch) / max(1, ch_tot[ch]),
                      cell_fmt=lambda v: f"{v:.0f}")]
    if has_major:
        for lvl in ("mid", "minor"):
            cnt = Counter(r[lvl] for r in ads)
            parent, uq = {r[lvl]: r["major"] for r in ads}, uniq_by(ads, lvl)
            blocks += [f"<h3>{lvl} category, airings (top {N})</h3>", hbar(top(cnt, N)),
                       rep.table(f"{lvl}_category", ["major", lvl, "airings", "unique_adverts"],
                                 [(parent[k], k, v, len(uq[k])) for k, v in cnt.most_common()])]
    rep.section("4 · Product categories / 品类分布", "Daniel: which industries and categories dominate — is it retail? Top categories, then one drill-down (section 12).", *blocks)

    # ---- 5. advertisers, holding companies, agencies / 广告主与代理 ----
    blocks = []
    for f, lab in (("advertiser", "Advertisers"), ("holding", "Holding companies"), ("agency", "Media agencies"), ("sales_house", "Sales houses")):
        cnt = Counter(r.get(f, "") for r in ads)
        if len(cnt) > 1 or "" not in cnt:
            uq = uniq_by(ads, f)
            blocks += [f"<h3>{lab} (airings)</h3>", hbar(top(cnt, N)),
                       rep.table(f, [f, "airings", "unique_adverts"], [(k, v, len(uq[k])) for k, v in cnt.most_common(500)])]
    rep.section("5 · Who paid / 谁付钱", "Concentration of spend: how much of the corpus do the largest advertisers account for?", *blocks)

    # ---- 6. length and versions / 时长与版本 ----
    dur = Counter(r["duration"] for r in ads if r["duration"])
    durs_by_brand = defaultdict(set)
    for r in ads:
        if r["duration"]:
            durs_by_brand[r["brand"]].add(r["duration"])
    multi = {b: d for b, d in durs_by_brand.items() if len(d) > 1}
    rep.section("6 · Advert length and versions / 时长与多版本",
                "Length stated by the identifier. Brands aired in more than one length (e.g. 10 s and 30 s) are where 'one advert or two?' arises.",
                columns([(f"{k}s", v) for k, v in sorted(dur.items())]),
                f"<p>{len(multi):,} of {len(durs_by_brand):,} brands appear in more than one length.</p>",
                rep.table("lengths", ["seconds", "airings"], sorted(dur.items())),
                rep.table("brands_multi_length", ["brand", "lengths"], sorted((b, " / ".join(map(str, sorted(d)))) for b, d in multi.items())))

    # ---- 7. time of day / 播出时段 ----
    hr = Counter(r["hour"] for r in ads if r["hour"] is not None)
    bands = [f"{h:02d}-{h + 3:02d}" for h in range(0, 24, 3)]
    band = lambda r: bands[r["hour"] // 3] if r["hour"] is not None else ""
    bt = Counter(band(r) for r in ads)
    rep.section("7 · Time of day / 播出时段",
                "When are adverts aired, and does the category mix change across the day?",
                columns([(f"{h:02d}", hr[h]) for h in range(24)]),
                heatmap(top_cats, bands, lambda c, b: 100 * sum(1 for r in ads if r[cat] == c and band(r) == b) / max(1, bt[b]),
                        cell_fmt=lambda v: f"{v:.0f}"),
                "<p class=note>Lower chart: % of the airings in each three-hour band.</p>",
                rep.table("hour", ["hour", "airings"], [(h, hr[h]) for h in range(24)]))

    # ---- 8. breaks and positions / 广告时段与位置 ----
    sizes = Counter(breaks_of(recs))
    rel = Counter(r["pib_rel"] for r in ads)
    bc = Counter(r.get("break_code", "") for r in ads)
    rep.section("8 · Breaks and position in break / 时段长度与广告位",
                "How many adverts per break, and which positions (First / Last) are recorded? Break Code values are listed for checking with Stephen.",
                columns([(str(k), v) for k, v in sorted(sizes.items()) if k <= 30]),
                hbar(top(rel, 15)),
                rep.table("break_sizes", ["adverts_in_break", "breaks"], sorted(sizes.items())),
                rep.table("position_in_break", ["pib_rel", "airings"], rel.most_common()),
                rep.table("break_code", ["break_code", "airings"], bc.most_common()) if len(bc) > 1 else "")

    # ---- 9. programme context / 前后节目 ----
    pb = Counter(r["prog_before"] for r in ads)
    pb_cat = defaultdict(Counter)
    for r in ads:
        pb_cat[r["prog_before"]][r[cat]] += 1
    rep.section("9 · Programme context / 前后节目",
                "Which programmes carry the most adverts? Context the audience-proxy experiments may condition on.",
                hbar(top(pb, N)),
                rep.table("programme_before", ["programme_before", "airings", f"top_{cat}_category"],
                          [(p, v, pb_cat[p].most_common(1)[0][0]) for p, v in pb.most_common(300)]))

    # ---- 10. impacts / 受众触达 ----
    if imp_cols:
        blocks = [f"<p>Audience columns found in the export / CSV 中的受众列：<b>{esc(', '.join(imp_cols))}</b>. "
                  "Meaning of the code and the unit (thousands?) still to confirm with Stephen.</p>"]
        for col in imp_cols:
            vals = [r["impacts"].get(col) for r in ads if r["impacts"].get(col) is not None]
            rows = [("all", *quantiles(vals))]
            rows += [(c, *quantiles([r["impacts"].get(col) for r in ads if r["channel"] == c and r["impacts"].get(col) is not None])) for c in chans]
            rows += [(y, *quantiles([r["impacts"].get(col) for r in ads if r["year"] == y and r["impacts"].get(col) is not None])) for y in years]
            edges = [0, 1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, float("inf")]
            hist = [(f"{edges[i]:g}–{edges[i + 1]:g}" if edges[i + 1] != float("inf") else f"{edges[i]:g}+",
                     sum(1 for v in vals if edges[i] <= v < edges[i + 1])) for i in range(len(edges) - 1)]
            blocks += [f"<h3>{esc(col)}</h3>", columns(hist),
                       f"<p class=note>{len(vals):,} airings with a value, {len(ads) - len(vals):,} blank.</p>",
                       rep.table(f"impacts_{col.replace(' ', '_').replace('+', 'plus')}",
                                 ["group", "min", "q1", "median", "q3", "max", "n"], rows)]
        rep.section("10 · Audience impacts / 受众触达", "Daniel: find out what this number and the group code mean.", *blocks)

    # ---- 11. repetition / 重复播出 ----
    per = Counter(r["code"] for r in ads)
    rb = Counter(min(v, 50) for v in per.values())
    meta_of = {r["code"]: r for r in ads}
    rep.section("11 · Repetition / 每条广告播了几次",
                "Airing-weighted and advert-weighted figures can diverge: a few adverts aired hundreds of times dominate airing counts.",
                columns([(str(k) if k < 50 else "50+", v) for k, v in sorted(rb.items())]),
                rep.table("most_aired", ["identifier", "brand", "advertiser", cat, "airings"],
                          [(c, meta_of[c]["brand"], meta_of[c]["advertiser"], meta_of[c][cat], v) for c, v in per.most_common(200)]))

    # ---- 12. drill-down / 单类深挖 ----
    for d in a.drill:
        sub = [r for r in ads if d.lower() in (r.get("major", "") + "|" + r.get("minor", "")).lower()]
        if not sub:
            print(f"drill: no airings match {d!r}")
            continue
        sub_tot = Counter(r["year"] for r in ads)
        rep.section(f"12 · Drill-down: {d}", f"{len(sub):,} airings, {len({r['code'] for r in sub}):,} unique adverts.",
                    "<h3>Minor categories</h3>", hbar(top(Counter(r["minor"] for r in sub), N)),
                    "<h3>Brands</h3>", hbar(top(Counter(r["brand"] for r in sub), N)),
                    "<h3>% of each year's airings</h3>",
                    columns([(y, round(100 * sum(1 for r in sub if r["year"] == y) / max(1, sub_tot[y]), 1)) for y in years], unit="%"),
                    rep.table(f"drill_{d.replace(' ', '_').replace('/', '_')}", ["identifier", "brand", "advertiser", "minor", "airings"],
                              [(c, meta_of[c]["brand"], meta_of[c]["advertiser"], meta_of[c]["minor"], v)
                               for c, v in Counter(r["code"] for r in sub).most_common()]))

    # ---- 13. what the ledger captured / 切分台账覆盖率 ----
    if a.run:
        cut = {(row.get("unique_id") or "").strip() for row in load_ledgers(a.run)}
        cov = lambda grp: (sum(1 for c in grp if c in cut), len(grp))
        rows = []
        for f, keys in ((cat, top_cats), ("channel", chans), ("year", years)):
            for k in keys:
                got, n = cov({r["code"] for r in ads if r[f] == k})
                rows.append((f, k, got, n, round(100 * got / max(1, n), 1)))
        rep.section("13 · What the cut captured / 切分覆盖率",
                    "Share of unique adverts that appear at least once in the ledger. Is the ~1/4 that is lost spread evenly, or concentrated in some groups?",
                    hbar([(f"{f}: {k}", p) for f, k, _, _, p in rows], unit="%"),
                    rep.table("ledger_coverage", ["grouping", "group", "unique_captured", "unique_total", "percent"], rows))

    intro = ("<p class=note>Counting rules: one airing = one (channel, date, start time, identifier); one unique advert = one "
             "identifier, so a 10 s and a 30 s version count twice; addressable placeholders appear only in section 3. "
             f"Category level used in charts: <b>{cat}</b>. Hover a bar or cell for its value; every chart has a CSV table.</p>")
    rep.write("Advert corpus · Step 0 descriptive statistics", intro)
    print(f"\nwrote {os.path.join(rep.out, 'report.html')} and {len(os.listdir(os.path.join(rep.out, 'tables')))} tables")


if __name__ == "__main__":
    main()
