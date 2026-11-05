"""List the columns of the metadata CSV and check the impacts values."""
import csv, sys

SRC = sys.argv[1] if len(sys.argv) > 1 else \
    '/home/shihan/storage/video/unique_advert_source_videos/ad_break_source_metadata.csv'

rows = list(csv.reader(open(SRC, encoding='utf-8-sig', newline='')))
header, body = rows[0], rows[1:]
n = len(header)

print('columns:')
for i, c in enumerate(header):
    print(f'  {i:2d}  {c}')

for c in header:
    if 'impact' in c.lower():
        i = header.index(c)
        filled = sum(1 for r in body if len(r) == n and r[i].strip())
        print(f'\n{c}: filled in {filled} of {len(body)} rows')

longer = [r for r in body if len(r) > n]
print(f'\nrows longer than the header: {len(longer)}')
for r in longer[:5]:
    print('  last 4 fields:', r[-4:])
