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
runs = defaultdict(lambda: {'candidate_first': [], 'baseline_first': []})
complete = 0
for (run_id, pair_index), variants in pairs.items():
    if set(variants) != {'baseline', 'candidate'}:
        continue
    complete += 1
    baseline, candidate = variants['baseline'], variants['candidate']
    if baseline['elapsed_ns'] <= 0 or candidate['elapsed_ns'] <= 0:
        continue
    group = 'candidate_first' if candidate['execution_order'] < baseline['execution_order'] else 'baseline_first'
    runs[run_id][group].append(math.log(candidate['elapsed_ns'] / baseline['elapsed_ns']))
eligible = [(rid, statistics.median(v['candidate_first']), statistics.median(v['baseline_first'])) for rid, v in runs.items() if v['candidate_first'] and v['baseline_first']]
if not eligible:
    raise SystemExit('No eligible runs contain both execution orders.')
cf = [x[1] for x in eligible]
bf = [x[2] for x in eligible]
diff = [a-b for a,b in zip(cf,bf)]
def pct(x): return 100 * math.expm1(x)
print('=== BENCHFORGE EXECUTION-ORDER ANALYSIS ===')
print(f'Database: {DB.resolve()}')
print(f'Samples read: {len(rows)}')
print(f'Complete pairs: {complete}/{len(pairs)}')
print(f'Runs with both order groups: {len(eligible)}')
print(f'Median effect when candidate ran first: {pct(statistics.median(cf)):.3f}%')
print(f'Median effect when baseline ran first: {pct(statistics.median(bf)):.3f}%')
print(f'Median order-associated difference: {pct(statistics.median(diff)):.3f}%')
rng = random.Random(20261021)
boot = sorted(statistics.median([diff[rng.randrange(len(diff))] for _ in diff]) for _ in range(10000))
lo = pct(boot[int(0.025 * len(boot))])
hi = pct(boot[min(int(0.975 * len(boot)), len(boot)-1)])
print(f'95% run-bootstrap CI: [{lo:.3f}%, {hi:.3f}%]')
print('Note: exploratory association; not proof of causation.')
