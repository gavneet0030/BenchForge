import math
import random
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

DB = Path('data/benchforge-sample-size.sqlite')
con = sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro', uri=True)
con.row_factory = sqlite3.Row
rows = con.execute('''SELECT run_id, variant, pair_index, execution_order, elapsed_ns FROM benchmark_samples WHERE run_id != ? ORDER BY run_id, pair_index''', ('legacy',)).fetchall()
con.close()
pairs = defaultdict(dict)
for r in rows:
    pairs[(r['run_id'], r['pair_index'])][r['variant']] = r
by_run = defaultdict(list)
for (rid, pair_index), variants in pairs.items():
    if set(variants) != {'baseline', 'candidate'}:
        continue
    b, c = variants['baseline'], variants['candidate']
    if b['elapsed_ns'] <= 0 or c['elapsed_ns'] <= 0:
        continue
    by_run[rid].append((pair_index, math.log(c['elapsed_ns'] / b['elapsed_ns'])))

results = []
for rid, items in by_run.items():
    items.sort(key=lambda x: x[0])
    n = len(items)
    if n < 6:
        continue
    one, two = n // 3, (2 * n) // 3
    chunks = [items[:one], items[one:two], items[two:]]
    if any(not chunk for chunk in chunks):
        continue
    effects = [statistics.median(value for _, value in chunk) for chunk in chunks]
    results.append((rid, n, *effects))

if not results:
    raise SystemExit('No runs had enough complete matched pairs for tercile analysis.')
def pct(v): return 100 * math.expm1(v)
early = [r[2] for r in results]
mid = [r[3] for r in results]
late = [r[4] for r in results]
diffs = [r[4] - r[2] for r in results]
print('=== BENCHFORGE SEQUENCE-DRIFT DIAGNOSTIC ===')
print(f'Samples read: {len(rows)}')
print(f'Runs analysed: {len(results)}')
print('Paired candidate/baseline effect by sequence tercile:')
print(f'  EARLY: median across runs = {pct(statistics.median(early)):.3f}%')
print(f'  MIDDLE: median across runs = {pct(statistics.median(mid)):.3f}%')
print(f'  LATE: median across runs = {pct(statistics.median(late)):.3f}%')
print(f'Median late-minus-early change: {pct(statistics.median(diffs)):.3f}%')
print(f'Runs with late effect greater than early effect: {sum(d > 0 for d in diffs)}/{len(diffs)}')
rng = random.Random(20261022)
boot = sorted(statistics.median([diffs[rng.randrange(len(diffs))] for _ in diffs]) for _ in range(10000))
lo = pct(boot[int(0.025 * len(boot))])
hi = pct(boot[min(int(0.975 * len(boot)), len(boot)-1)])
print(f'95% run-bootstrap CI for late-minus-early change: [{lo:.3f}%, {hi:.3f}%]')
print('Interpretation: descriptive diagnostic; a nonzero trend does not alone establish causation.')
