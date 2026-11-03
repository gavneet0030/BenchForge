# Testing BenchForge

Run commands from the repository root. Tests use local deterministic fixtures;
they do not download public datasets.

## Local checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m benchforge.cli --help
cmake --build build\ci-final --config Release --parallel 2
ctest --test-dir build\ci-final -C Release --output-on-failure
git diff --check
```

The Python suite covers benchmark validation and statistics, CSV, JSON, JSONL,
and Parquet ingestion and lineage, dataset summary transforms, dashboard APIs,
and PDF validation,
extraction, storage, search, and text export. PDF tests use generated local
fixtures. The PDF upload cap and page limit are checked before storing files.

Optional Phase 3 integrations have focused tests for DuckDB inspection and
aggregation, Polars lazy summaries, quality rules, catalog metadata and
lineage, projected Parquet ingestion, and CLI behavior. When the optional
extras are absent, the engine-specific tests are skipped. The dbt project uses
a synthetic CSV seed and does not automatically read BenchForge's live SQLite
history. No Iceberg runtime integration or interoperability test exists.

## Phase 3 verification — 2026-11-02

- Python 3.12.10 full suite with optional integrations installed: **240 passed,
  0 failed, 0 skipped**.
- Focused analytics, Polars, quality, catalog, pipeline/Parquet, and CLI suite:
  **81 passed**.
- Native Phase 1/2 CTest regressions: **3/3 passed**.
- Repository-wide Ruff, `pip check` in both the project and external optional
  environments, and `git diff --check`: passed.
- External optional environment: DuckDB 1.5.6, Polars 1.44.2, dbt-duckdb
  1.11.1, dbt-core 1.12.6, PyArrow 26.0.0, pytest 9.2.0, Ruff 0.17.3.
  The environment occupied 0.962 GB and left 19.58 GB free after installation.
- dbt `debug`, `parse`, and `compile`: passed. `dbt build`: **17/17 passed**
  (1 seed, 3 models, 13 data tests; 0 warnings, errors, or skips). The profile,
  DuckDB file, target, and logs were all under the system temporary directory.
  A build first exposed an unquoted reserved identifier (`binary`); quoting it
  in the staging model fixed the failure, and the complete build then passed.

### Optional engine benchmark

This is a local, descriptive comparison—not a general performance claim.
Python 3.12.10 on Windows processed the same deterministic two-column input:
100,000 rows, 100 groups (`group_id = i % 100`,
`value = (17 * i) % 1000`), with an expected value sum of 49,950,000.
The generated CSV was 779,016 bytes and Zstandard-compressed Parquet was
12,742 bytes. Each worker performed 2 warm-ups and 7 measured read-and-group
operations (count and sum); all reported the expected row count, group count,
and sum. Timing excludes process startup and imports. The nearest-rank P95
with seven observations is the maximum sample.

| Engine | Input | Median (ms) | P95 (ms) | Peak working set (MiB) | Peak private commit (MiB) |
|---|---:|---:|---:|---:|---:|
| Python `csv` | CSV | 1,148.4 | 1,515.5 | 12.35 | 7.90 |
| PyArrow | CSV | 21.6 | 38.9 | 63.30 | 168.48 |
| PyArrow | Parquet | 8.3 | 9.4 | 53.75 | 165.11 |
| DuckDB | CSV | 219.3 | 274.4 | 44.12 | 92.61 |
| DuckDB | Parquet | 12.9 | 18.7 | 37.33 | 24.44 |
| Polars | CSV | 18.0 | 29.6 | 60.63 | 125.16 |
| Polars | Parquet | 6.3 | 9.2 | 51.48 | 100.99 |

Memory figures are Windows process-tree peak working set and peak private
commit, sampled at approximately 5 ms intervals; the reported process-tree
value is the largest individual process peak, not the sum of the launcher and
interpreter. They include the Python runtime and engine imports, are not
normalized by an idle baseline, and are not directly comparable to
per-operation allocation measurements. The engines were run sequentially
against the same files, so OS file-cache warmth and run order affect timings.
The small synthetic data set does not establish a winner or predict
performance on production workloads. Decision: retain Polars in its optional
extra because it completed both formats with correct results and competitive
latency here, but do not make it a mandatory dependency or default pipeline
engine based on this single workload.

## Phase 4/5 continuation checks — 2026-11-02

The complete suite passed in the existing external Python 3.12.10 environment:
**277 passed, 0 failed, 0 skipped** in 81.41 seconds after the final test and
script changes. The focused Phase 4 and
related storage/runner/CLI regression set passed **110/110** in 48.55 seconds.
The external environment contains DuckDB 1.5.6, Polars 1.44.2, dbt-duckdb
1.11.1, and dbt-core 1.12.6. `pip check` reported no broken requirements; no
dependencies were installed during this continuation.

The initial full suite passed before the final linter cleanup. The classifier
evaluation script now sets `BENCHFORGE_NATIVE_RUNTIME_DIR` for
native child processes and imports BenchForge modules inside `main`, avoiding
an import-order workaround. After that change, repository-wide Ruff passed,
the classifier CLI `--help` smoke passed, and the runner validation/native
integration tests passed **22/22**. A timing-dependent dashboard log
assertion was updated to check completion events for the two completed data
requests; its focused test passed twice and the subsequent full suite passed.
The workspace Python 3.14.2 environment could not collect focused CLI tests
because `pypdf` is absent, so the complete Python suite used the existing
external environment.

The Windows Release workload target was rebuilt incrementally with one worker
after adding process peak-memory reporting. Native Phase 1/2 CTest passed
**3/3** with `ctest --test-dir build\ci-final -C Release
--output-on-failure`. The only compiler metadata present in the existing CMake
cache was generator `Visual Studio 17 2022`; compiler ID/version and build type
were not available.

#### Python versus native CPU smoke

The CLI ran two paired samples at 1,000 identical xorshift64 iterations and
verified matching checksums (`2151266509743617848`) before recording any
comparison. Median elapsed time was 1,986,600 ns for Python and 3,450 ns for
native C++. This small local run is evidence of matching work and a working
comparison path, not a general performance claim.

#### Process memory and storage smoke

The native memory workload ran two samples with a 64 KiB working set and one
pass. Both reported 131,072 bytes read/written and Windows peak working sets
of 3,526,656 and 3,522,560 bytes. The file workload ran two samples with an
8 KiB temporary file; each reported 16,384 bytes written/read and median
elapsed time was 3,509,650 ns. These two-sample numbers are local smoke
measurements, not capacity or device-performance claims.

#### SQLite versus DuckDB smoke

The same generated 5,000-row/50-group table was queried with the same
group/count/sum aggregation, three times per engine. Aggregate checksums
matched. Median query elapsed time was 2,678,200 ns for in-memory SQLite and
1,978,600 ns for in-memory DuckDB. Table setup was excluded and DuckDB used
one thread. This is a small synthetic measurement and does not establish an
engine winner.

Repeatability tests use deterministic stable and noisy samples; regression
tests use seeded bootstrap fixtures and verify a sub-threshold shift is not
reported as a regression. The concurrent JSONL test limits its executor to two
workers and confirms readers see one complete writer output with no partial
temporary files.

Docker is installed but its Linux engine is unavailable: the Docker API named
pipe `//./pipe/dockerDesktopLinuxEngine` does not exist. Node 24.18.0 and npm
11.16.0 are present, but there is no frontend `package.json`; FastAPI is absent
from the tested optional environment. No frontend packages were installed and
no Docker-backed service was started.

On the subsequent continuation check, global `tsc`, `vite`, and `eslint`
commands were absent, npm's configured global package directory did not exist,
and available physical memory ranged from **248.7 to 444.4 MiB**. A React/Vite
dependency install/build was not attempted; the existing local dashboard
remains available, but the specified React/TypeScript frontend is not
implemented.

### Phase 5–7 local evaluation

The dashboard observability tests passed **16/16** after adding assertions for
the structured `LogRecord` fields (`http_method`, `route`, `status_code`, and
`duration_seconds`) and existing sensitive-value redaction. `/metrics` reports
process-local counts and monotonic request-duration count/total/maximum; it is
not a histogram or a Prometheus endpoint.

The existing optional environment has OpenTelemetry API 1.45.2 as a transitive
dbt dependency, but no OpenTelemetry SDK, Prometheus client, or Grafana
executable. The current Phase 5 continuation adds the dependency-free
`/metrics/prometheus` exposition endpoint and a Grafana dashboard JSON file;
it does not add a Prometheus client or OTel SDK. PostgreSQL/Redis/Kafka/Airflow/Spark service executables were absent, as were
the queried clients (`asyncpg`, `psycopg`, `redis`, `confluent-kafka`,
`boto3`, `pyspark`, and `apache-airflow`). No distributed service is required
by the current local workflow. These integrations remain unimplemented and
were not runtime-tested. Physical memory was about **513 MiB free** at the
latest check, so no React/Vite or service dependencies were installed.

### Phase 8 security scan

Bandit 1.9.4 scanned 7,115 application lines. The scan summary reported **5
low, 14 medium, and 0 high** findings; the high/medium (`-ll`) output
contained 13 B608 dynamic-SQL alerts and one B310 URL-open alert. The SQL
identifiers/fragments were checked against validators, quoting, parameter
binding, and existing injection tests. Dataset downloads use catalog-fixed
HTTPS URLs, CLI choices, timeouts, and byte limits. This static scan is not a
full security audit. `pip-audit` was not installed, so no dependency advisory
scan ran; `pip check` only verified dependency consistency.

## Latest Phase 1 verification — 2026-11-01

The existing workspace virtual environment (Python 3.14.2) passed the full
Python suite: **194 passed, 0 failed, 0 skipped** in 129.65 seconds. Pytest
used `-p no:cacheprovider`, disabled bytecode writes, and wrote temporary
files under the system temporary directory. Repository-wide Ruff passed, and
`pip check` reported no broken requirements.

A fresh external MSVC Release build (MSVC 19.44.35228.0, CMake 4.4.3) passed
CTest **3/3**. The clean build emitted MSB8029 warnings because MSBuild
intermediate files were under the Windows temporary directory; compilation
and tests still completed successfully. Direct Python runners and the `run`,
`ab`, and `workload` CLI commands all completed against those fresh binaries.

A wheel built from an external copy of the package (80,550 bytes; SHA-256
`24dad0d95c48102360b3952b695d161ad460371670df2da50ffe60a97b3402c9`) included
all 13 package modules. Metadata, the Python requirement, the console entry
point, imports from the wheel archive, and CLI help were verified. The wheel
was not installed into a separate environment. Python 3.12.10 is installed,
but its interpreter has none of the project dependencies, so package imports
and tests were not run there; no dependencies were installed.

The six dashboard environment variables and command-line override precedence
were covered by focused tests. The timeout-test isolation fix and consistent
native-process error reporting passed the focused and full suites.

## Current recovery evidence

On 2026-10-29, the existing Python 3.14.2 workspace environment passed the
146-test recovery baseline. After the PDF workflow was added, the focused
PDF/dashboard suite passed 22 tests and the full suite passed 172 tests after
the public-data processors and dashboard updates. The latest full suite passed
175 tests after bounded JSON/JSONL/Parquet ingestion was added.
Repository-wide Ruff, `pip check`, and a live local HTTP smoke also passed.
The wheel and clean-install evidence in this earlier checkpoint is historical.
Docker runtime, remote CI, and deployment remain separate checks; see
[FINAL_VERIFICATION.md](./FINAL_VERIFICATION.md) for that earlier evidence and
limitations. A local pass does not imply a successful remote workflow or
deployment.

## Latest local audit — 2026-10-29

The current worktree audit passed the full suite with 178 tests, repository-wide
Ruff, and `pip check`. Pytest used a build-directory `--basetemp` because the
default Windows temp directory was inaccessible to the sandbox account. Two
independent clean MSYS2 UCRT64 Release builds passed CTest (3/3 each). A bounded
CSV-to-SQLite ingestion, aggregation, UTF-8 export, negative-input checks, and
SQLite integrity checks passed. A/B and history analysis were exercised on two
8-pair runs; the historical comparison was inconclusive. A local dashboard
health/API/page smoke passed.

A wheel built from an isolated staging copy of the current package and its
installed CLI completed a pipeline smoke. The clean venv used the existing
development environment's dependencies through `PYTHONPATH`; a fully isolated
dependency installation was not established. Docker could not connect to its
named-pipe daemon. GitHub CLI had no authenticated account, so remote CI was not
inspected. See `build/final-production-audit/codex/FINAL_PRODUCTION_READINESS_REPORT.md`
for commands, exact results, security findings, and remaining gates.

## Latest Phase 4–5 verification — 2026-11-02

- Full Python suite: **312 passed, 0 failed, 0 skipped** in 73.07 seconds
  using the existing Python 3.12.10 analytics environment.
- Native Release CTest: **4/4 passed**.
- Repository-wide Ruff and `pip check` in both the project and analytics
  environments: passed. No dependencies were installed.
- Analytics environment versions: DuckDB 1.5.6, Polars 1.44.2, PyArrow 26.0.0,
  pytest 9.2.0, and pypdf 6.21.4. The workspace Ruff environment is separate.
- The Stage 20 CLI ran seven matching Python/native workloads at 128 items and
  two measured pairs per workload. Every checksum matched before timing.
  The run produced 28 records, seven homogeneous workload run IDs, and
  separate Python/native medians in the external temporary directory.
- A bounded Stage 21 CPU smoke at 100,000,000 iterations observed
  3,025,595,400 ns wall time and 3,026,000,000 ns process CPU time
  (100.013% utilization). The reported Windows peak working set was
  3,436,544 bytes; current working set was 3,432,448 bytes, private commit
  was 638,976 bytes, and observed process thread count was 3. These are one
  machine-specific sample, not performance or capacity claims.
- Stage 23 file-format CLI smoke validated 100 generated rows in CSV, JSON, and
  JSONL with two reads per format; optional Parquet was also run with PyArrow
  and produced matching checksums. File creation was excluded from timed reads.
- Stage 24 compared a verified 5,000-row/50-group aggregate in in-memory
  SQLite and one-thread DuckDB over three queries each. Checksum was 2,497,500;
  median query time was 5,657,600 ns for SQLite and 2,645,600 ns for DuckDB.
  Table setup was excluded. This synthetic result is descriptive only.
- Stage 25's concurrent atomic JSONL tests use two worker threads and bounded
  batches; transient replacement conflicts are retried a fixed number of times
  and permanent errors remain visible. Stages 26–28 retain nearest-rank
  percentiles, the 100-observation P99 floor, deterministic noise fixtures,
  minimum historical samples, and noise-tolerant decisions.
- Phase 5 tests verify structured redacted request records, health/readiness,
  request/error/duration and read-only storage metrics, Prometheus exposition,
  and all four queries in the Grafana dashboard JSON. The metrics are
  process-local and reset on restart. OpenTelemetry tracing remains deferred:
  no distributed trace consumer exists for this local application.

## Phase 6 audit and dbt history carryover — 2026-11-02

- The first bounded local verification run passed
  `tests/unit/test_history_export.py` and `tests/unit/test_cli.py`:
  **40 passed** in 46.45 seconds using the pre-existing Python 3.12.10
  environment. It verifies read-only SQLite access, CSV schema and values,
  output non-overwrite, row bounds, cleanup on failure, and CLI behavior.
- Focused Ruff for the modified exporter, CLI, and tests passed. Both staged
  and unstaged `git diff --check` passed after the final documentation edits,
  with existing LF-to-CRLF notices.
- Export-backed dbt full project build: **17/17 nodes passed**, including one
  seed, three models, and 13 data tests, using dbt-core 1.12.6 and dbt-duckdb
  1.11.1 with one thread. The exporter created a 3-row temporary CSV from a
  synthetic SQLite fixture; the profile, CSV, DuckDB database, target, and
  logs were all placed under a unique system-temp directory and cleaned after
  the run. The profile did not connect to any service.
- A prior narrowed dbt selector omitted the staging-runs view required by a
  relationship test and failed that test; the full-project build then passed
  all 17 nodes. A separate post-build SQL assertion was not completed because
  the inline PowerShell/Python quoting was malformed; therefore the exact
  summary-row value was not independently queried. The build and dbt data
  tests themselves did succeed against the exported-history source.
- Phase 6 candidate services were not tested. PostgreSQL service was running
  but unidentified; it was not contacted. Redis, Kafka/Redpanda, MinIO,
  Airflow, and Spark clients/runtime were absent. Docker Desktop service was
  stopped. No dependencies were installed.
- At the final resource check, available RAM had fallen to **19 MiB**. The full
  Python regression suite, additional dbt commands, `pip check`, and native
  CTest were not run in this continuation. The previously recorded Phase 4/5
  full suite (312 passed), native CTest (4/4), Ruff, and `pip check` are
  historical evidence from before the history-export changes, not a fresh
  full-suite result for this continuation.

Only the measured aggregate query is compared between SQLite and DuckDB;
database row-ingestion speed is not claimed. Optional engine/setup time,
Windows timer granularity, file-cache warmth, and machine scheduling constrain
interpretation. Git/data baseline verification and the no-commit/no-push state
are recorded in [FINAL_VERIFICATION.md](./FINAL_VERIFICATION.md).

## Phase 7 web application verification — 2026-11-02

The optional web API and React/Vite interface are separate from the existing
standard-library dashboard. Start from a clean Python environment with the
optional server and development dependencies installed:

```powershell
python -m pip install -e ".[web,dev]"
$env:PYTHONPATH = "src"
python -m pytest -q tests/unit/test_webapp.py
python -m ruff check src/benchforge/webapp.py src/benchforge/cli.py `
  src/benchforge/runner.py src/benchforge/workload.py src/benchforge/phase4.py `
  tests/unit/test_webapp.py
python -m compileall -q src/benchforge
```

In `frontend/`, run `npm install`, `npm test`, and `npm run build`. For the
development UI, run `benchforge web --database <temporary-or-selected-path>`
in one terminal and `npm run dev` in another. API tests should use a temporary
database; application startup initializes its configured SQLite database.
Native execution tests additionally require the corresponding C++ executable.

Verification performed in this continuation:

- `tests/unit/test_webapp.py`: **11 passed** on Python 3.13.9 with FastAPI
  0.139.0, Pydantic 2.12.4, and httpx 0.28.1. The test client emitted one
  Starlette deprecation warning about httpx; there were no test failures.
- `tests/unit/test_phase4.py`, `tests/unit/test_runner_validation.py`, and
  `tests/unit/test_cli.py`: **52 passed, 2 skipped** in the project virtual
  environment. The two optional DuckDB tests skipped because DuckDB is not
  installed there.
- Targeted Ruff and `compileall` checks passed. `git diff --check` and
  `git diff --cached --check` passed.
- The initial web API run found a FastAPI route response-model construction
  error for mixed dictionary/response return annotations. The affected routes
  now disable inferred response models explicitly; the focused API suite then
  passed. The empty trailing-slash collection URL is tested as a collection
  request rather than an empty run identifier.
- Frontend tests, TypeScript/Vite production build, browser E2E, full Python
  suite, `pip check`, and native CTest were **not run** for this continuation.
  `frontend/node_modules` is absent, no native binary was available, and only
  197 MiB RAM was free during inspection; dependency installation/build was
  avoided to protect the machine. Therefore Phase 7 remains **PARTIAL** and
  the frontend and native runtime behavior are not verified.
