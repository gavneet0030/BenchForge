# Implementation status

Status reflects repository inspection and local checks on 2026-10-29. The
checkout is on `feature/ab-comparison` and contains pre-existing staged,
unstaged, and untracked work; no commit, push, release, or deployment was made.
See [ARCHITECTURE.md](./ARCHITECTURE.md) for implemented system boundaries and
[DATASETS.md](./DATASETS.md) for the source inventory and provenance caveats.
The scope is broader than can be considered complete in one pass. Each item
below distinguishes code presence from verified behavior.

## Latest continuation — Phase 6 audit and carryover; 2026-11-02

This continuation audited Phases 1–5, closed the real benchmark-history/dbt
carryover, and evaluated Phase 6. The bounded history export is implemented
and tested. Phase 6 service adapters were not added because no remote,
streaming, object-storage, orchestration, or distributed-compute requirement
is present. Phase 6 remains **PARTIAL overall**: service compatibility is not
verified, Stage 39/42 are documented evaluations only, and the unknown local
PostgreSQL service was not contacted. See
[DISTRIBUTED_INTEGRATIONS.md](./DISTRIBUTED_INTEGRATIONS.md) for detailed
stage decisions and runtime limits.

| Area | Status | Evidence / remaining work |
|---|---|---|
| Phase 4 — Stages 19–28 | Complete locally | Safe atomic JSONL lifecycle and failure-preserving cleanup; seven checksum-gated Python/C++ workload families with homogeneous run IDs; process CPU time and thread observations with explicit unavailable values; truthful peak/current-memory labels; bounded temporary-file and CSV/JSON/JSONL/optional Parquet reads; optional one-thread DuckDB versus SQLite aggregate queries; bounded concurrent atomic-write tests; latency/throughput percentiles with a 100-sample P99 floor; repeatability/noise labels; and historical comparisons with minimum samples, build compatibility, bootstrap intervals, p-values, and threshold tolerance. The latest full suite, native CTest, Ruff, dependency checks, and CLI smokes are recorded in [TESTING.md](./TESTING.md). Measurements are machine-specific; no broad performance claim is made. |
| Phase 5 local dashboard observability | Complete for local scope | `/healthz` is a liveness check, `/readyz` checks the configured SQLite database read-only, `/metrics` returns JSON observations, and `/metrics/prometheus` emits dependency-free Prometheus text exposition for bounded request/error/duration and storage gauges. Structured request events carry method, normalized route, status, and duration while excluding query strings, document IDs, and request bodies. The importable Grafana dashboard is [benchforge-overview.json](../dashboards/benchforge-overview.json). Tests verify metrics, redaction, readiness, and dashboard queries. Metrics reset on restart and are not production monitoring. OpenTelemetry tracing is deferred because the current product is a single local process without a distributed trace consumer. |
| Phase 3 Stage 15 carryover — live history in dbt | Complete for explicit local snapshots | `benchforge export-history` exports fixed columns from SQLite read-only with row/cell/byte limits and no overwrite. dbt reads the CSV only when `BENCHFORGE_HISTORY_CSV_PATH` is set; the synthetic seed remains the default. Unit, CLI, and export-backed dbt checks are recorded in [TESTING.md](./TESTING.md). This is a point-in-time local export, not live synchronization. |
| Phase 6 distributed services | Partial overall; services not required for present product | No optional service clients were installed. PostgreSQL service is running locally but unidentified and was not accessed; no Redis/Kafka/MinIO/Airflow/Spark executables or client packages were found. Docker service is stopped. RAM was about 302 MiB at initial inspection and 19 MiB after dbt verification; disk had 11.04 GiB free. The low final RAM prevented a full-suite rerun. See stage dispositions below; no runtime compatibility is claimed. |
| Phase 7 web frontend | **PARTIAL** — implementation present, frontend verification blocked | Added an optional FastAPI API and `benchforge web` loopback command, a React/Vite 12-route interface, bounded job submission, history/analytics, compatible-run comparisons, and CSV/JSON/PDF exports. The focused API tests pass (11/11) on system Python 3.13.9 with FastAPI 0.139.0, Pydantic 2.12.4, and httpx 0.28.1; targeted Ruff and Python syntax checks pass. Frontend tests/build and browser E2E were not run because npm dependencies are absent and only 197 MiB RAM was free; no packages were installed. No native binary was available for native workload E2E. See the Phase 7 verification section in [TESTING.md](./TESTING.md). |
| Phase 8 security review | Partial; finding fixed | The prior review's loopback DNS-rebinding/Host-header finding is fixed and covered by tests. A Bandit high/medium scan found no high-severity issue; its medium alerts were reviewed as fixed/allow-listed SQL structure or fixed dataset URLs, with existing identifier-injection tests. `pip-audit` is absent, so no dependency vulnerability database scan was run. No penetration test or independent external audit was performed. |
| Phase 9 release and operations | Partial / not released | Docker/Compose, CI, recovery, deployment, and release documentation already exist. Local full tests, native CTest, and repository-wide Ruff have passed; remote CI, Docker runtime, release, and deployment were not run. Release actions remain approval-gated. |

### Phase 6 integration rationale summary

| Candidate | Decision for this local product | Evidence / condition for revisiting |
|---|---|---|
| PostgreSQL | Not required for current product | SQLite serves local history; no multi-user or remote-database requirement was supplied. A local service exists but is unidentified; it was not accessed. |
| Redis | Not required | No distributed cache, queue, or shared-session requirement. |
| Kafka / Redpanda | Not required | No event stream is part of the current workflow. |
| CDC / Debezium | Architecture documented only | No external transactional source or CDC consumer exists; no connector, broker, event handling, or recovery has been implemented. |
| MinIO / S3 | Not required | Inputs and generated documents use bounded local files; no object-storage target or credentials were requested. |
| Airflow | Not required | CLI-driven local pipelines do not need a shared scheduler. |
| Spark | Evaluation documented only | Existing bounded Python, DuckDB, and Polars cover tested inputs; no distributed scale target or Spark runtime is available. |

These are scope decisions, not runtime compatibility claims. No candidate
service was installed or integration-tested. Per-stage dispositions follow.

### Phase 6 stage disposition (Stages 36–42)

| Stage | Candidate | Decision | Status |
|---|---|---|---|
| 36 | PostgreSQL | No multi-user/remote database requirement. Existing local PostgreSQL service was not contacted because its database/owner are unknown. | NOT REQUIRED; runtime NOT VERIFIED |
| 37 | Redis | No shared cache, queue, or session requirement. | NOT REQUIRED; runtime NOT VERIFIED |
| 38 | Kafka / Redpanda | No streaming workload, topic, or consumer requirement. | NOT REQUIRED; runtime NOT VERIFIED |
| 39 | CDC / Debezium | Detailed flow, idempotency, offsets, recovery, schema evolution, and security are documented; no connector is implemented. | PARTIAL; architecture only, runtime NOT VERIFIED |
| 40 | MinIO / S3 | No object-storage target; local bounded files are sufficient. | NOT REQUIRED; runtime NOT VERIFIED |
| 41 | Airflow | No shared scheduler or DAG requirement. | NOT REQUIRED; runtime NOT VERIFIED |
| 42 | Spark | Conceptual comparison with local engines is documented; no Spark runtime or measured comparison exists. | PARTIAL; evaluation only, runtime NOT VERIFIED |

These are scope decisions rather than runtime compatibility claims. No
candidate was installed or integration-tested. Reopen the relevant stage if a
specific scale, concurrency, event-stream, cloud-storage, or scheduling
requirement is established.

### Phase 8 security and delivery evidence

The local Bandit 1.9.4 high/medium scan summary counted **5 low, 14 medium,
and 0 high alerts**; `-ll` emitted details for the medium alerts. Thirteen
B608 alerts are generated by SQL assembled from fixed query fragments and
validated/quoted identifiers, with data values bound as parameters; the
existing pipeline injection regression test passes. The B310 alert is the
public-data downloader, whose URLs come from the fixed dataset catalog and
whose CLI dataset is an argparse choice; downloads have time and byte limits.
This is a static-scan review of the reported patterns, not proof of absence of
vulnerabilities. `pip-audit` is unavailable, so dependency advisory scanning
remains outstanding. A filename-only check found no tracked `.env`, PEM,
private-key, P12, or PFX artifacts; this is not a content-based secret scan.

## Phase 3 data-platform additions — 2026-11-02

The following additions were made after the historical status snapshot below.
The full Python 3.12 suite passed **240/240**, including the optional engines;
the focused Phase 3 suite passed **81/81**. Native regression tests passed
**3/3**, repository-wide Ruff and `pip check` passed, and dbt build passed
**17/17** nodes. Detailed commands, package versions, benchmark measurements,
and limitations are recorded in [TESTING.md](./TESTING.md).

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Stage 12 — DuckDB analytics | Verified | Optional, lazy-loaded DuckDB supports bounded read-only inspection and grouped CSV/Parquet summaries; integration tests pass. SQLite remains the application store. |
| Stage 13 — Polars analytics | Verified | Optional lazy CSV/Parquet summary adapter with a bounded group count; integration tests pass. |
| Stage 14 — Projection and engine evaluation | Verified | CSV/JSON/JSONL column projection and batched PyArrow Parquet projection; malformed CSV widths remain rejected. A deterministic 100,000-row Python/PyArrow/DuckDB/Polars comparison is recorded in [TESTING.md](./TESTING.md); results are descriptive, not a winner claim. |
| Stage 15 — dbt-duckdb project | Verified locally; explicit real-history export added | Synthetic seed, staging views, summary mart, schema and relationship tests. dbt can now read an explicitly exported SQLite history CSV; no live synchronization is performed. Seed-based `debug`, `parse`, `compile`, and `build` evidence and the new export-backed build are recorded in [TESTING.md](./TESTING.md). |
| Stage 16 — File-quality rules | Verified | Required columns, types, nulls, uniqueness, numeric ranges, allowed values, string lengths, and row count; reports PASS/WARNING/FAIL without editing source files. |
| Stage 17 — SQLite catalog and lineage | Verified | Additive metadata, source/output checksums, quality results, schemas, and recursive lineage. Ingestion records catalog information transactionally. |
| Stage 18 — Iceberg compatibility | Research complete; support not implemented | Official format and DuckDB documentation reviewed; no Iceberg code, catalog, fixture, or interoperability test is included. See [ICEBERG_COMPATIBILITY.md](./ICEBERG_COMPATIBILITY.md). |

## Systems performance

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| C++20 lookup and CPU/memory/file/TCP workloads | Verified locally | Existing CMake targets and CTest suite; native build and test commands are recorded in [FINAL_VERIFICATION.md](./FINAL_VERIFICATION.md). |
| Python orchestration, bounded arguments, timeouts, JSONL | Verified locally | Existing runner and workload tests; retain limitations on machine-specific timing. |
| A/B comparison and persisted SQLite history | Verified locally | Existing paired runner, statistical tests, report and history CLI. |
| CSV benchmark export | Verified locally | Existing bounded export implementation and dashboard route tests. |
| Comparative benchmark dashboard | Verified locally | API tests pass; live local HTTP health/page and PDF upload/search/export smoke passed. |

## Local data engineering

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Bounded CSV, JSON, JSONL, and Parquet ingestion | Verified locally | 20 MB source/expanded-text, 100,000 rows, 200 columns, and 65,536 characters/cell limits; normalized identifiers, schema checks, and optional PyArrow dependency for Parquet. |
| Required columns, duplicate policies, numeric ranges | Verified locally | `keep`/`drop`/`error` policies; numeric range limits; tests for duplicates and rejected rows. |
| Repeatable load and failure preservation | Verified locally | Staging table swap occurs in a SQLite transaction; tests check prior destination survives invalid load. |
| Multi-file normalization | Verified locally | 2–50 compatible inputs; normalized columns are aligned across differing source order. |
| Profiling and quality | Partially verified | Missing values, type inference, numeric min/max/mean/population SD, distinct-count bound, duplicate and range checks. No reusable declarative data-contract model or quantiles yet. |
| Aggregation | Verified locally | Bounded grouped count/sum/mean with numeric validation and identifier allow-listing. |
| Run metrics and lineage | Verified locally | Persisted SQLite run status, row counts, duration, rows/s, quality JSON and transformation lineage. |
| JSON/JSONL pipeline formats | Verified locally | Bounded JSON arrays and line-delimited JSON objects use the common profiling, validation, and SQLite loader; malformed records and schema drift are tested. |
| Parquet pipeline I/O | Verified locally | Optional PyArrow Parquet batch reader uses shared bounds and text normalization; fixture-backed ingestion test passes. |
| DuckDB integration | Not implemented | SQLite remains the local pipeline store. |
| Pipeline timeout/cancellation/retry scheduler | Not implemented | Row and byte bounds exist; no timeout/cancellation/retry orchestration. |
| Dashboard pipeline observability | Partially verified | Read-only bounded run list and overview metrics are covered by HTTP tests; browser smoke remains unverified. |

## Datasets and documents

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| UCI Online Retail | Partially verified | Local ZIP exists and processing summary is present; 541,909 source rows were previously recorded. Verify current UCI terms before redistribution. |
| UCI Bike Sharing | Partially verified | Local ZIP and summary exist; 17,379 hourly rows were previously recorded; CC BY 4.0 attribution is documented. |
| NYC TLC Yellow Taxi Jan 2024 | Partially verified | Local Parquet and summary exist; 100,000-row file-order sample was previously recorded. Verify current terms before redistribution. |
| Citi Bike trips | Not implemented | Official S3 listing exposes the 2024-01 archive at 369,035,302 bytes; terms and a safe bounded-download policy remain unresolved. |
| UCI Student Performance | Not implemented | UCI lists CC BY 4.0; the dataset processor is not implemented and no archive was downloaded. |
| UCI household electricity | Verified locally | CC BY 4.0 source; 2,075,259 rows processed with field-level missing-value counts and a 140 MB uncompressed-size limit. |
| UCI Wine Quality | Verified locally | CC BY 4.0 source; 6,497 records processed across red and white CSV files with a 400 KB uncompressed-size limit. |
| Seven-source catalog and local availability CLI | Implemented and tested | `benchforge datasets list` reports metadata and local availability without network access; `fetch` and `validate` operate on five supported processors. Citi Bike and Student Performance remain metadata-only. Wine Quality replaces the inaccessible NYC survey source in the seven-entry catalog. |
| PDF upload and analysis | Verified locally | Loopback-only upload, signature/structure and configurable size checks (10 MiB default, 50 MiB maximum), 200-page cap, generated storage IDs, pypdf text extraction, search, preview, statistics, and local text export. No OCR; parser runs in-process, not in an OS sandbox. |

The local raw-source inventory now contains five datasets and corresponding
summaries. The Household Power and Wine Quality archives were downloaded only
after their official endpoints, size bounds, and CC BY 4.0 terms were verified.

## Dashboard, security, and release

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Local dashboard and health API | Verified by tests | Loopback default, read-only history access, benchmark and CSV endpoints. |
| PDF upload validation and text download | Implemented; focused tests pass | Upload and document routes are loopback-only, bounded, and use generated filenames. Parser is in-process, not an OS sandbox; no OCR is claimed. |
| Static GitHub Pages demo | Not implemented | No static artifact/workflow; do not imply GitHub Pages can run the Python backend. |
| CI definition | Configured, remote result unverified | `.github/workflows/ci.yml` defines Windows/Ubuntu and Python 3.12–3.14 test matrix plus container job. |
| Docker | Unverified locally | Docker Desktop daemon availability has not been established in this pass. |
| Package | Verified locally | Latest 79,883-byte wheel installed in a clean Python 3.14 environment; CLI, public-data, dashboard, and pipeline modules imported; JSON ingestion, CLI help, Wine Quality validation, and `pip check` passed. |
| Security review | Partial | Local bounds, path handling, output escaping, and loopback-only PDF routes reviewed and tested; no formal audit, penetration test, or current dependency scan. |
| Cost/privacy | Verified by action | No paid service enabled and no additional public deployment or data upload occurred. |

## Latest checks

- Recovery baseline: Python 3.14.2, `.\.venv\Scripts\python.exe -m pytest -q` — 146 passed before the PDF feature.
- PDF checkpoint: `.\.venv\Scripts\python.exe -m pytest -q tests\unit\test_documents.py tests\unit\test_dashboard.py` — 22 passed.
- Latest format integration checkpoint: full Python suite — 175 passed; `ruff check .`, `pip check`, and `git diff --check` passed.
- Recovery baseline: native Release build and CTest — 3/3 passed.
- Post-PDF checkpoint: live local HTTP health/page/upload/search/text-export smoke passed.
- Latest wheel checkpoint: rebuilt the 79,883-byte wheel from current source, installed it in the clean Python 3.14 environment, imported CLI/public-data/dashboard/pipeline modules, ran main and pipeline CLI help, smoke-tested JSON ingestion, validated the local Wine Quality archive (6,497 rows), and passed `pip check`.
- Focused pipeline/catalog/dashboard tests: first run found one catalog-output assertion mismatch (CLI prints lowercase stable IDs); corrected the test and replaced a Unicode separator with ASCII for Windows console compatibility. The subsequent full suite passed.
- Dataset catalog command executed against local data: seven entries reported; five local raw files and processed summaries were present, two sources were reported as not implemented.
- The two historical `E402` findings were not reproduced by `ruff check scripts`.
- `pypdf` 6.21.4 is installed in the workspace environment after adding the
  runtime dependency.

## Phase 1 continuation — 2026-11-01

The full current Python suite passed **194/194** on the existing Windows
Python 3.14.2 environment. Repository-wide Ruff and `pip check` passed. A
fresh external MSVC 19.44.35228.0 Release build passed CTest 3/3, and direct
Python runners plus the run, A/B, and workload CLI commands completed against
the new binaries. An 80,550-byte wheel built from an external source copy and
its metadata, 13 modules, imports, and CLI entry point were verified.

This continuation also added dashboard environment/CLI-precedence tests,
corrected the timeout-test isolation, and made native subprocess failures
consistent and context-preserving across runner types. Python 3.12.10 is
available, but its interpreter lacks the project dependencies; only metadata
parsing was verified there. Remote CI, Linux runtime behavior, Docker, and
deployment remain unverified. No configuration framework or logging
dependency was added.

## Immediate continuation

1. Verify additional official dataset endpoints, terms, sizes, and attribution before integrating or downloading sources.
2. Complete dataset-specific integrations and fixture-backed validation for the remaining catalog entries.
3. Continue the remaining pipeline, CI, security, and deployment work; do not imply remote verification without evidence.
