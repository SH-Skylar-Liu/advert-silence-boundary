"""Category pivot from ad_break_source_metadata.csv (read-only).
Output: one long table  dimension x value x category level -> counts.
Usage: python3 category_pivot.py [SRC_CSV] [OUT_DIR]
"""
import csv, collections, os, sys, datetime

SRC = sys.argv[1] if len(sys.argv) > 1 else \
    '/home/shihan/storage/video/unique_advert_source_videos/ad_break_source_metadata.csv'
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser('~/run_category')
os.makedirs(OUT, exist_ok=True)

# channel labels -> 11 channels (ITV1 HD merged into ITV1: provisional)
CH = {'5': 'Channel 5', 'CH4': 'Channel 4', 'ITV1 HD': 'ITV1'}
BC = {'ITV1': 'ITV', 'ITV2': 'ITV', 'ITV3': 'ITV', 'ITV4': 'ITV', 'ITVBe': 'ITV',
      'Channel 4': 'Channel 4', 'E4': 'Channel 4', 'More4': 'Channel 4', 'Film4': 'Channel 4',
      'Channel 5': 'Channel 5', '5STAR': 'Channel 5'}
PSB = {'ITV1', 'Channel 4', 'Channel 5'}
TOP_N = {'programme': 60, 'agency': 40, 'holding_company': 20, 'advertiser': 60}

rows = list(csv.DictReader(open(SRC, encoding='utf-8-sig')))
real = [r for r in rows if r['metadata_status'] != 'placeholder']
print('rows:', len(rows), '| non-placeholder:', len(real))

g = lambda r, c: (r.get(c) or '').strip()

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
        'advert_type': g(r, 'advert_type'),
        'programme': g(r, 'programme_before'),
        'agency': g(r, 'agency_clean') or g(r, 'source_agency'),
        'holding_company': g(r, 'holding_company_clean') or g(r, 'source_holding_company'),
        'advertiser': g(r, 'advertiser_clean') or g(r, 'source_advertiser'),
    }
    cat = tuple(g(r, 'source_' + k + '_category') for k in ('major', 'mid', 'minor'))
    recs.append((dims, cat, g(r, 'source_film_code'),
                 g(r, 'advertiser_clean') or g(r, 'source_advertiser'),
                 g(r, 'video_present') == '1', imp))

# keep only top-N values (by unique ads) for high-cardinality dims; rest -> "(other)"
for dim, n in TOP_N.items():
    s = collections.defaultdict(set)
    for dims, _, fc, *_ in recs:
        s[dims[dim]].add(fc)
    keep = {v for v, _ in sorted(s.items(), key=lambda x: -len(x[1]))[:n]}
    for dims, *_ in recs:
        if dims[dim] not in keep:
            dims[dim] = '(other)'

# sanity checks
fc_cats = collections.defaultdict(set)
for _, cat, fc, *_ in recs:
    fc_cats[fc].add(cat)
print('unique film codes:', len(fc_cats),
      '| with >1 category triple:', sum(len(v) > 1 for v in fc_cats.values()))
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
for dims, cat, fc, adv, vp, imp in recs:
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
print('written:', out, '|', len(occ), 'lines')

# overview: values per dimension (unique ads at total level)
print('\n[dimensions] value: unique_ads')
for dim in dim_order:
    vals = sorted(((k[1], len(ads[k])) for k in occ if k[0] == dim and k[2] == 'total'),
                  key=lambda x: -x[1])
    shown = ', '.join(f'{v}:{n}' for v, n in vals[:8])
    print(f'  {dim:18s} {len(vals):3d} values | {shown}')
