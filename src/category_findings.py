"""Short findings from category_pivot_long.csv (output of category_pivot.py).
Prints conclusions only: which categories are over/under-represented where.
Index = share of the category within a value / share of the category overall x 100
(unique adverts by film code). 100 = as expected, 200 = twice as common.
Usage: python3 category_findings.py [PIVOT_CSV] [--all]
  --all  print every category for every value (count and index), no thresholds
"""
import csv, collections, os, sys

ARGS = [a for a in sys.argv[1:] if a != '--all']
FULL = '--all' in sys.argv
SRC = ARGS[0] if ARGS else os.path.expanduser('~/run_category/category_pivot_long.csv')
MIN_CELL = 15      # min unique adverts in a cell to report it
MIN_VALUE = 200    # min unique adverts in a value (e.g. a channel) to report it
SKIP = {'Oldmedialog'}
HI, LO = 120, 80      # only report clear skews

T = collections.defaultdict(dict)   # (dim, level) -> {(value, cat): n}
TOT = {}                           # (dim, value) -> n
for r in csv.DictReader(open(SRC, encoding='utf-8')):
    n = int(r['unique_ads'])
    if r['level'] == 'total':
        TOT[(r['dimension'], r['value'])] = n
        continue
    cat = r['major'] if r['level'] == 'major' else r['major'] + ' > ' + r['mid'] \
        if r['level'] == 'mid' else None
    if cat is None or r['major'] in SKIP:
        continue
    T[(r['dimension'], r['level'])][(r['value'], cat)] = n

ALL = TOT[('ALL', 'ALL')]

def share_all(level):
    return {c: n / ALL for (v, c), n in T[('ALL', level)].items()}

def section(title):
    print('\n' + '=' * 78 + '\n' + title + '\n' + '=' * 78)

# 1. hierarchy overview
section('1. CATEGORY HIERARCHY (unique adverts, Oldmedialog excluded)')
maj = sorted(T[('ALL', 'major')].items(), key=lambda x: -x[1])
mids = collections.defaultdict(list)
for (v, c), n in T[('ALL', 'mid')].items():
    mids[c.split(' > ')[0]].append((n, c.split(' > ')[1]))
coded = sum(n for _, n in maj)
print(f'total unique adverts: {ALL} | coded (not Oldmedialog): {coded} | majors: {len(maj)} | mids: {sum(len(m) for m in mids.values())}')
print(f'top 10 majors cover {sum(n for _, n in maj[:10]) / coded:.0%} of coded adverts')
for (v, c), n in maj:
    m = sorted(mids[c], reverse=True)
    lead = '; '.join(f'{name} {k}' for k, name in m[:3])
    print(f'  {n:5d} {n / coded:5.1%}  {c}  [{len(m)} mids] -> {lead}')

# 2. over/under-representation per dimension
def skew(dim, level, top=3, low=2, values=None):
    sa = share_all(level)
    cells = collections.defaultdict(list)
    for (v, c), n in T[(dim, level)].items():
        tot = TOT.get((dim, v), 0)
        if (tot < MIN_VALUE and not FULL) or c not in sa or not tot:
            continue
        cells[v].append((n / tot / sa[c] * 100, n, c))
    order = values or sorted(cells, key=lambda v: -TOT[(dim, v)])
    for v in order:
        if v not in cells:
            continue
        if FULL:
            print(f'\n  {v}  (n={TOT[(dim, v)]})')
            for i, n, c in sorted(cells[v], reverse=True):
                print(f'    {i:6.0f}  {n:5d}  {c}')
            continue
        rows = [x for x in cells[v] if x[1] >= MIN_CELL]
        hi = [x for x in sorted(rows, reverse=True) if x[0] >= HI][:top]
        # under-represented: large categories overall that are rare here
        lo = [x for x in sorted((x for x in cells[v] if sa[x[2]] * TOT[(dim, v)] >= 30), key=lambda x: x[0]) if x[0] <= LO][:low]
        if not hi and not lo:
            continue
        print(f'\n  {v}  (n={TOT[(dim, v)]})')
        if hi:
            print('    more : ' + ' | '.join(f'{c} {i:.0f}' for i, n, c in hi))
        if lo:
            print('    fewer: ' + ' | '.join(f'{c} {i:.0f}' for i, n, c in lo))

DIMS = [('broadcaster', 'major'), ('psb', 'major'), ('channel', 'major'),
        ('daypart', 'major'), ('weekday', 'major'), ('month', 'major'), ('year', 'major'),
        ('duration', 'major'), ('position_in_break', 'major'), ('sales_house', 'major'),
        ('daypart', 'mid'), ('month', 'mid'), ('channel', 'mid'), ('duration', 'mid')]
if FULL:
    DIMS = [(d, lv) for lv in ('major', 'mid') for d in
            ('broadcaster', 'psb', 'channel', 'year', 'month', 'weekday', 'daypart', 'hour',
             'duration', 'position_in_break', 'log_station', 'sales_house')]
for i, (dim, level) in enumerate(DIMS, 2):
    if (dim, level) not in T:
        continue
    section(f'{i}. {dim.upper()} x {level} (index; >100 = more than expected)')
    vals = sorted({v for (d, v) in TOT if d == dim}) if dim in ('month', 'year', 'weekday', 'daypart', 'hour') else None
    skew(dim, level, values=vals, low=0 if level == 'mid' else 2)

# 3. programmes: the biggest ones only
section(f'{len(DIMS) + 2}. PROGRAMME ({"all top-60" if FULL else "largest 15"}, mid level, index)')
progs = sorted((v for (d, v) in TOT if d == 'programme' and v not in ('(other)', '', '-none-')),
               key=lambda v: -TOT[('programme', v)])[:None if FULL else 15]
skew('programme', 'mid', values=progs, low=0)

print('\nnotes: index on unique adverts; only index >= %d (more) or <= %d (fewer) shown;' % (HI, LO))
print('       cells < %d adverts and values < %d adverts not shown;'
      ' Oldmedialog excluded; selected-break sample only; values with no clear skew omitted.' % (MIN_CELL, MIN_VALUE))
