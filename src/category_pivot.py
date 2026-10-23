"""Category pivot from ad_break_source_metadata.csv (read-only).
Output: one long table  dimension x value x category level -> counts,
plus small single-purpose tables t1..t11 (each short enough to copy by hand).
Usage:
  python3 category_pivot.py [SRC_CSV] [OUT_DIR]       build all tables
  python3 category_pivot.py --show t3 [t5 ...] [--out OUT_DIR]
      print only the named tables, each between BEGIN/END lines, nothing else
"""
import csv, collections, os, sys, datetime, statistics, glob

DEFAULT_OUT = os.path.expanduser('~/run_category')

if len(sys.argv) > 1 and sys.argv[1] == '--show':
    args = sys.argv[2:]
    out = DEFAULT_OUT
    if '--out' in args:
        i = args.index('--out')
        out = os.path.expanduser(args[i + 1])
        args = args[:i] + args[i + 2:]
    for name in args or ['t1']:
        hits = sorted(glob.glob(os.path.join(out, name + '_*.csv')))
        if not hits:
            print(f'##### NOT FOUND {name} (run the script first, or pass --out) #####')
            continue
        lines = open(hits[0], encoding='utf-8').read().rstrip('\n').split('\n')
        tag = os.path.basename(hits[0])
        print(f'##### BEGIN {tag} | {len(lines) - 1} rows #####')
        print('\n'.join(lines))
        print(f'##### END {tag} | {len(lines) - 1} rows #####')
    sys.exit(0)

SRC = sys.argv[1] if len(sys.argv) > 1 else \
    '/home/shihan/storage/video/unique_advert_source_videos/ad_break_source_metadata.csv'
OUT = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
os.makedirs(OUT, exist_ok=True)

# channel labels -> 11 channels (ITV1 HD merged into ITV1: provisional)
CH = {'5': 'Channel 5', 'CH4': 'Channel 4', 'ITV1 HD': 'ITV1'}
BC = {'ITV1': 'ITV', 'ITV2': 'ITV', 'ITV3': 'ITV', 'ITV4': 'ITV', 'ITVBe': 'ITV',
      'Channel 4': 'Channel 4', 'E4': 'Channel 4', 'More4': 'Channel 4', 'Film4': 'Channel 4',
      'Channel 5': 'Channel 5', '5STAR': 'Channel 5'}
PSB = {'ITV1', 'Channel 4', 'Channel 5'}
TOP_N = {'programme': 60, 'agency': 40, 'holding_company': 20, 'advertiser': 60}
OLD = 'Oldmedialog'
CHARITY_MAJORS = ['Charities', 'Government social political organisation']

rows = list(csv.DictReader(open(SRC, encoding='utf-8-sig')))
real = [r for r in rows if r['metadata_status'] != 'placeholder']
print('rows:', len(rows), '| non-placeholder:', len(real))

def g(r, c):
    v = (r.get(c) or '').strip()
    return '' if v == '#N/A' else v

# Repair shifted rows. A comma inside a source category name (in the major, mid or
# minor field, sometimes more than one) split it into extra fields, so the later
# source fields moved right by k and the last k values were lost. position_in_break
# and position_number are read from the shifted all_pib fields, so they moved too.
# k = how far right the real position value sits; the 3 + k category pieces are then
# split back into major / mid / minor, choosing the split that matches categories
# seen in unshifted rows (ties: comma in the major, then mid, then minor).
POS_OK = {'First', '2nd', '3rd', 'Middle', '3rd last', '2nd last', 'Last'}
SHIFTED = ['source_major_category', 'source_mid_category', 'source_minor_category',
           'source_all_pib_rel', 'source_all_pib_pos', 'source_log_station',
           'source_impacts_a4_plus']
ok_rows = [r for r in real if g(r, 'position_in_break') in POS_OK]
known_maj = {g(r, 'source_major_category') for r in ok_rows}
known_mid = {(g(r, 'source_major_category'), g(r, 'source_mid_category')) for r in ok_rows}
known_tri = {tuple(g(r, 'source_' + k + '_category') for k in ('major', 'mid', 'minor')) for r in ok_rows}

def clean(s):
    return s.strip().strip('"').strip()

def split_pieces(p):
    """All ways to cut the pieces into (major, mid, minor), best match first."""
    out = []
    n = len(p)
    for a in range(1, n - 1):
        for b in range(a + 1, n):
            t = (clean(','.join(p[:a])), clean(','.join(p[a:b])), clean(','.join(p[b:])))
            score = 4 * (t[0] in known_maj) + 2 * (t[:2] in known_mid) + (t in known_tri)
            out.append((-score, -a, -(b - a), t))
    return [x[3] for x in sorted(out)]

repairs = collections.Counter()      # (where, k, major, mid, minor) -> rows
repair_fcs = collections.defaultdict(set)
unrepaired = 0
for r in real:
    if g(r, 'position_in_break') in POS_OK:
        continue
    v = [r.get(c) or '' for c in SHIFTED]
    k = next((k for k in (1, 2, 3) if 3 + k < len(v) and v[3 + k].strip() in POS_OK), None)
    if k is None:
        unrepaired += 1
        continue
    t = split_pieces(v[:3 + k])[0]
    rest = v[3 + k:] + [''] * k
    r['source_major_category'], r['source_mid_category'], r['source_minor_category'] = t
    (r['source_all_pib_rel'], r['source_all_pib_pos'], r['source_log_station']) = rest[:3]
    r['source_impacts_a4_plus'] = ''
    r['position_in_break'], r['position_number'] = rest[0], rest[1]
    where = 'major' if ',' in t[0] else 'mid' if ',' in t[1] else 'minor' if ',' in t[2] else '?'
    repairs[(where, k) + t] += 1
    repair_fcs[(where, k) + t].add(g(r, 'source_film_code'))
still_bad = sum(g(r, 'position_in_break') not in POS_OK for r in real)
print('shifted rows repaired:', sum(repairs.values()), '| no position found:', unrepaired,
      '| still unparsed after repair:', still_bad)
by_where = collections.Counter()
for key, n in repairs.items():
    by_where[key[:2]] += n
for (where, k), n in sorted(by_where.items()):
    print(f'  comma in {where:5s} (k={k}): {n} rows')

def parse_date(s):
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y%m%d', '%d-%m-%Y'):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None

def hour(s):
    try:
        return int(s.split(':')[0]) % 24
    except ValueError:
        return None

def daypart(h):
    # common UK TV dayparts (BARB-style broadcast day starts 06:00)
    if h is None: return ''
    if 6 <= h < 9: return '1 Breakfast 06-09'
    if 9 <= h < 12: return '2 Morning 09-12'
    if 12 <= h < 18: return '3 Daytime 12-18'
    if 18 <= h < 21: return '4 Early peak 18-21'
    if 21 <= h < 24: return '5 Late peak 21-24'
    return '6 Night 00-06'

def duration(fc):
    t = fc[-3:]
    return str(int(t)) + 's' if t.isdigit() else ''

recs = []
for r in real:
    ch = CH.get(g(r, 'break_channel'), g(r, 'break_channel'))
    d = parse_date(g(r, 'break_date'))
    h = hour(g(r, 'break_start_time'))
    try:
        imp = float(g(r, 'source_impacts_a4_plus') or 0)
    except ValueError:
        imp = 0.0
    dims = {
        'ALL': 'ALL',
        'channel': ch,
        'broadcaster': BC.get(ch, '?'),
        'psb': 'PSB channel' if ch in PSB else 'non-PSB channel',
        'year': str(d.year) if d else '',
        'month': f'{d.month:02d}' if d else '',
        'weekday': d.strftime('%u %a') if d else '',
        'hour': f'{h:02d}' if h is not None else '',
        'daypart': daypart(h),
        'duration': duration(g(r, 'source_film_code')),
        'position_in_break': g(r, 'position_in_break'),
        'log_station': g(r, 'source_log_station'),
        'sales_house': g(r, 'source_sales_house'),
        'programme': g(r, 'programme_before'),
        'agency': g(r, 'agency_clean') or g(r, 'source_agency'),
        'holding_company': g(r, 'holding_company_clean') or g(r, 'source_holding_company'),
        'advertiser': g(r, 'advertiser_clean') or g(r, 'source_advertiser'),
    }
    cat = tuple(g(r, 'source_' + k + '_category') for k in ('major', 'mid', 'minor'))
    extra = {'ym': f'{d.year}-{d.month:02d}' if d else '',
             # one recording file = one break in this sample
             'break': g(r, 'video_filename') or (ch, g(r, 'break_date'), g(r, 'break_start_time'))}
    recs.append((dims, cat, g(r, 'source_film_code'),
                 g(r, 'advertiser_clean') or g(r, 'source_advertiser'),
                 g(r, 'video_present') == '1', imp, extra))

# keep only top-N values (by unique ads) for high-cardinality dims; rest -> "(other)"
for dim, n in TOP_N.items():
    s = collections.defaultdict(set)
    for dims, _, fc, *_ in recs:
        s[dims[dim]].add(fc)
    keep = {v for v, _ in sorted(s.items(), key=lambda x: -len(x[1]))[:n]}
    for dims, *_ in recs:
        if dims[dim] not in keep:
            dims[dim] = '(other)'

# one category triple per film code: the most frequent one, preferring any real
# category over "Oldmedialog" (looks like a holding code for not-yet-coded creatives)
fc_cats = collections.defaultdict(collections.Counter)
for _, cat, fc, *_ in recs:
    fc_cats[fc][cat] += 1
multi = sum(len(v) > 1 for v in fc_cats.values())
canon = {fc: max(c, key=lambda t: (t[0] != OLD, c[t])) for fc, c in fc_cats.items()}
raw_old = {fc for fc, c in fc_cats.items() if any(t[0] == OLD for t in c)}
recs = [(dims, canon[fc], fc, *rest) for dims, cat, fc, *rest in recs]
print('unique film codes:', len(fc_cats), '| with >1 category triple (resolved):', multi,
      '| Oldmedialog after resolving:', sum(t[0] == OLD for t in canon.values()),
      f'(of {len(raw_old)} with any Oldmedialog row)')
print('unparsed date:', sum(not d['year'] for d, *_ in recs),
      '| unparsed hour:', sum(not d['hour'] for d, *_ in recs),
      '| no duration:', sum(not d['duration'] for d, *_ in recs),
      '| unmapped channel:', sorted({d['channel'] for d, *_ in recs} - set(BC)) or 'none')

LEVELS = {'total': 0, 'major': 1, 'mid': 2, 'minor': 3}
occ = collections.Counter()
imp_sum = collections.Counter()
ads = collections.defaultdict(set)
ads_v = collections.defaultdict(set)
advs = collections.defaultdict(set)
for dims, cat, fc, adv, vp, imp, _ in recs:
    for lv, n in LEVELS.items():
        c = cat[:n] + ('',) * (3 - n)
        for dim, val in dims.items():
            k = (dim, val, lv) + c
            occ[k] += 1
            imp_sum[k] += imp
            ads[k].add(fc)
            advs[k].add(adv)
            if vp:
                ads_v[k].add(fc)

out = os.path.join(OUT, 'category_pivot_long.csv')
dim_order = {d: i for i, d in enumerate(recs[0][0])}
with open(out, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['dimension', 'value', 'level', 'major', 'mid', 'minor', 'unique_ads',
                'unique_ads_with_video', 'occurrences', 'unique_advertisers', 'impacts_a4_plus_sum'])
    for k in sorted(occ, key=lambda k: (dim_order[k[0]], k[1], LEVELS[k[2]], k[3:])):
        w.writerow([*k, len(ads[k]), len(ads_v[k]), occ[k], len(advs[k]), round(imp_sum[k], 1)])
print('long table:', len(occ), 'lines (category_pivot_long.csv)')

# ---------------------------------------------------------------------------
# Small single-purpose tables. No film codes, advertisers, agencies or holding
# companies are written to these files.
# ---------------------------------------------------------------------------
written = []
def table(name, header, body):
    path = os.path.join(OUT, name)
    body = list(body)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)
    written.append((name, len(body)))

def nunique(it):
    return len(set(it))

# 1. how each shifted row was repaired
table('t1_repair_report.csv', ['comma_in', 'extra_fields', 'major', 'mid', 'minor', 'rows', 'unique_ads'],
      [(*key, n, len(repair_fcs[key])) for key, n in
       sorted(repairs.items(), key=lambda x: (x[0][0], -x[1]))])

# 2. video coverage per category (the model can only see adverts with video)
cov = []
for lv in ('major', 'mid'):
    for k in sorted(occ, key=lambda k: -len(ads[k])):
        if k[0] == 'ALL' and k[2] == lv:
            a, v = len(ads[k]), len(ads_v[k])
            cov.append((lv, k[3], k[4], a, v, round(v / a, 3) if a else ''))
k_all = ('ALL', 'ALL', 'total', '', '', '')
cov.insert(0, ('total', '', '', len(ads[k_all]), len(ads_v[k_all]), round(len(ads_v[k_all]) / len(ads[k_all]), 3)))
table('t2_video_coverage.csv', ['level', 'major', 'mid', 'unique_ads', 'with_video', 'share_with_video'], cov)

# majors as columns, largest first (keeps the cross tables short)
all_major = collections.defaultdict(set)
for dims, cat, fc, *_ in recs:
    all_major[cat[0]].add(fc)
all_n = nunique(fc for _, _, fc, *_ in recs)
MAJ_ORDER = sorted(all_major, key=lambda m: (m == OLD, -len(all_major[m]), m))

def wide(name, first_cols, cells):
    """cells: {(row_key, major): set of film codes}; major '' = all categories."""
    keys = sorted({rk for rk, _ in cells})
    table(name, [*first_cols, 'all', *MAJ_ORDER],
          [(*rk, len(cells.get((rk, ''), ())), *(len(cells.get((rk, m), ())) for m in MAJ_ORDER))
           for rk in keys])

# 3. channel x daypart, by major (unique ads)
cell = collections.defaultdict(set)
for dims, cat, fc, *_ in recs:
    for m in ('', cat[0]):
        cell[((dims['channel'], dims['daypart']), m)].add(fc)
wide('t3_channel_daypart_major.csv', ['channel', 'daypart'], cell)

# 4. sales house x channel x daypart: size and the largest major in each cell
sc = collections.defaultdict(set)
sc_major = collections.defaultdict(lambda: collections.defaultdict(set))
for dims, cat, fc, *_ in recs:
    key = (dims['sales_house'], dims['channel'], dims['daypart'])
    sc[key].add(fc)
    sc_major[key][cat[0]].add(fc)
rows4 = []
for key, s in sorted(sc.items()):
    top = sorted(((m, len(f)) for m, f in sc_major[key].items() if m != OLD), key=lambda x: -x[1])
    m1 = top[0] if top else ('', 0)
    rows4.append((*key, len(s), m1[0], m1[1]))
table('t4_saleshouse_channel_daypart.csv',
      ['sales_house', 'channel', 'daypart', 'unique_ads', 'largest_major', 'largest_major_ads'], rows4)

# 5. programme before (top 60): size and the most over-represented majors (index, >= 10 ads)
pg =collections.defaultdict(lambda: collections.defaultdict(set))
for dims, cat, fc, *_ in recs:
    pg[dims['programme']][cat[0]].add(fc)
rows5 = []
for p, majors in pg.items():
    n = len(set().union(*majors.values()))
    over = []
    for m, f in majors.items():
        if m == OLD or len(f) < 10:
            continue
        idx = (len(f) / n) / (len(all_major[m]) / all_n) * 100
        over.append((idx, m, len(f)))
    over.sort(reverse=True)
    rows5.append([p, n] + [x for i in range(3) for x in
                           ((over[i][1], round(over[i][0]), over[i][2]) if i < len(over) else ('', '', ''))])
rows5.sort(key=lambda x: -x[1])
table('t5_programme_top_majors.csv',
      ['programme_before', 'unique_ads'] + [f'{a}{i}' for i in (1, 2, 3) for a in ('major', 'index', 'ads')], rows5)

# 6. charities vs government: unique ads by month, and category switches per film code
ym = collections.defaultdict(lambda: collections.defaultdict(set))
for dims, cat, fc, adv, vp, imp, extra in recs:
    ym[extra['ym']]['ALL'].add(fc)
    if cat[0] in CHARITY_MAJORS:
        ym[extra['ym']][cat[0]].add(fc)
table('t6_charity_government_by_month.csv', ['year_month', *CHARITY_MAJORS, 'all_unique_ads'],
      [(m, *(len(ym[m][c]) for c in CHARITY_MAJORS), len(ym[m]['ALL'])) for m in sorted(ym)])

sw = collections.Counter()
for fc, c in fc_cats.items():
    pairs = sorted({t[:2] for t in c})
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            sw[(pairs[i], pairs[j])] += 1
table('t7_category_switches.csv', ['major_a', 'mid_a', 'major_b', 'mid_b', 'film_codes'],
      [(*a, *b, n) for (a, b), n in sw.most_common()])

# 7. Oldmedialog by month: before and after resolving film codes with a real category
old_m = collections.defaultdict(lambda: [set(), set(), set()])
for dims, cat, fc, adv, vp, imp, extra in recs:
    e = old_m[extra['ym']]
    e[2].add(fc)
    if fc in raw_old:
        e[0].add(fc)
    if cat[0] == OLD:
        e[1].add(fc)
table('t8_oldmedialog_by_month.csv',
      ['year_month', 'ads_with_any_oldmedialog_row', 'ads_still_oldmedialog', 'all_unique_ads'],
      [(m, len(e[0]), len(e[1]), len(e[2])) for m, e in sorted(old_m.items())])

# 8. airings per advert, by major
air = collections.defaultdict(collections.Counter)
for dims, cat, fc, *_ in recs:
    air[cat[0]][fc] += 1
rows9 = []
for m, c in sorted(air.items(), key=lambda x: -len(x[1])):
    v = sorted(c.values())
    rows9.append((m, len(v), sum(v), round(sum(v) / len(v), 2), round(float(statistics.median(v)), 1),
                  v[min(len(v) - 1, int(0.9 * len(v)))], v[-1]))
table('t9_airings_per_ad.csv', ['major', 'unique_ads', 'occurrences', 'mean', 'median', 'p90', 'max'], rows9)

# 9. long formats (60s and longer) by year and major: "60s+/all" per year
lf = collections.defaultdict(lambda: [set(), set()])
for dims, cat, fc, *_ in recs:
    dur = dims['duration']
    e = lf[(cat[0], dims['year'])]
    e[1].add(fc)
    if dur and int(dur[:-1]) >= 60:
        e[0].add(fc)
years = sorted({y for _, y in lf if y})
table('t10_long_formats_by_year.csv', ['major', *years],
      [(m, *(f'{len(lf[(m, y)][0])}/{len(lf[(m, y)][1])}' for y in years)) for m in MAJ_ORDER
       if any(lf[(m, y)][0] for y in years)])

# 10. position in break by break size (adverts in the break), by major
bsize = collections.Counter(extra['break'] for *_, extra in recs)
def band(n):
    return '01-06' if n <= 6 else '07-10' if n <= 10 else '11+'
pb = collections.defaultdict(set)
for dims, cat, fc, adv, vp, imp, extra in recs:
    pos = dims['position_in_break']
    slot = pos if pos in ('First', 'Last') else 'other' if pos in POS_OK else 'unknown'
    for m in ('', cat[0]):
        pb[((band(bsize[extra['break']]), slot), m)].add(fc)
wide('t11_position_by_breaksize.csv', ['break_size', 'slot'], pb)

print('\n[small tables] rows')
for name, n in written:
    print(f'  {name:38s} {n:5d}')
out_arg = '' if os.path.abspath(OUT) == os.path.abspath(DEFAULT_OUT) else f' --out {OUT}'
print(f'\nTo copy a table, print it alone (BEGIN/END lines show the row count):'
      f'\n  python3 {sys.argv[0]} --show t1{out_arg}'
      f'\n  python3 {sys.argv[0]} --show t3 t4{out_arg}')
