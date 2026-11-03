# Local release-readiness checklist

This checklist records the latest observed local validation. Configured CI is
not the same as a passing remote workflow. The current branch remains
uncommitted and unpublished.

## Implementation checkpoint — 2026-10-29

The current continuation includes bounded CSV ingestion, compatible multi-file
normalization, grouped analytical aggregation, SQLite run/quality/lineage
records, a seven-source catalog, dashboard history APIs, and a bounded local
PDF upload/search/export workflow. Architecture boundaries and dataset
provenance are documented in [ARCHITECTURE.md](./ARCHITECTURE.md) and
[DATASETS.md](./DATASETS.md).

The implementation is **not release complete**. Two catalog sources still
lack a verified integration; broader contracts/analytics, Docker runtime,
remote CI, and static Pages deployment remain outstanding or unverified.

| Area | State | Evidence or remaining action |
|---|---|---|
| Python tests | PASS | Full suite passed 175 tests after JSON/JSONL/Parquet pipeline ingestion was added |
| Ruff, entire repository | PASS | `.\.venv\Scripts\python.exe -m ruff check .` |
| Historical E402 findings | NOT REPRODUCED | `ruff check scripts` passes |
| Native build | PASS | `cmake --build build\ci-final --config Release --parallel 2` |
| Native tests | PASS | `ctest --test-dir build\ci-final -C Release --output-on-failure` — 3/3 |
| Workload CLI | PASS | Bounded CPU, memory, file I/O, and loopback TCP runs completed |
| Database/history | PASS | Workload samples saved and returned by the dashboard API |
| Dashboard routes | PASS | Health, benchmark, provenance, public summaries, CSV, pipeline history, and PDF upload/search/text routes are covered by local HTTP tests |
| Dashboard live HTTP smoke | PASS | Temporary local server passed health, dashboard, upload, search, and text-export checks |
| ETL pipelines | PASS | Bounded CSV, JSON, JSONL, and Parquet ingestion; CSV multi-file normalization; aggregation; failure preservation; quality, lineage, and CLI tests |
| Dataset counts | PASS | Bike 17,379; Retail 541,909; Taxi 100,000-sample; Household Power 2,075,259; Wine Quality 6,497. Source sizes, hashes, and sample caveats are listed in [DATASETS.md](./DATASETS.md). |
| Dataset catalog | PASS | Seven entries reported without network access; five local source files and summaries present, two sources explicitly not implemented |
| Six analysis sources | PARTIAL | Five public datasets and benchmark history are available; local CSV/JSON/JSONL/Parquet ingestion is CLI-only. Synthetic finance analysis remains unimplemented |
| Dataset redistribution terms | BLOCKED | Confirm Online Retail attribution and NYC TLC terms before redistribution; Citi Bike terms require review. Student Performance is listed as CC BY 4.0 but has no processor. |
| Wheel build/install | PASS | Latest 79,883-byte wheel installed in the clean Python 3.14 environment; CLI/public-data/dashboard/pipeline imports, JSON ingest smoke, CLI help, Wine Quality validation, and `pip check` passed |
| Fresh virtual environment installation | PASS | Clean install passed `pip check` |
| Dependency consistency | PASS | `pip check` reported no broken requirements |
| Compose syntax | PASS | `docker compose config` |
| Docker image/runtime | BLOCKED | Docker Desktop Linux daemon unavailable; run image, health, workload, restart, and volume-persistence smoke checks |
| Ubuntu/Linux | BLOCKED | CI matrix is configured, but no remote workflow result was inspected |
| Python 3.12/3.13 | BLOCKED | Not run locally; configured CI results must be observed |
| Windows CI | BLOCKED | Local Windows checks passed, but no remote workflow result was inspected |
| Security review | PARTIAL | Pipeline SQL identifiers are allow-listed; CSV and PDF inputs are bounded; PDF upload/search/export is loopback-only with generated IDs; no dedicated scanner, formal audit, or penetration test was run |
| Project license | PASS | Existing tracked MIT `LICENSE` preserved; dataset terms remain separate |
| Documentation | PASS* | README and architecture, dataset, implementation status, verification, contribution/security, and changelog docs updated; verify examples against the final CLI before publication |
| Git hygiene | PASS | `git diff --check`; raw archives, processed summaries, and pre-existing backups are ignored |
| Release artifact integrity | PARTIAL | Clean install and CLI smoke passed; inspect final artifact contents before publishing |
| Public release | BLOCKED | Requires final legal/CI/container checks and explicit maintainer approval |

`PARTIAL` denotes an incomplete acceptance requirement, not a successful release
check.
No commit, push, package upload, or GitHub release has been made.

## Latest local audit — 2026-10-29

The newer evidence is recorded in
`build/final-production-audit/codex/FINAL_PRODUCTION_READINESS_REPORT.md` and
supersedes the older test-count and wheel claims above for this worktree.

| Gate | Current result |
|---|---|
| Python suite | PASS — 178 passed with an audit-local pytest temp directory |
| Ruff / dependency consistency | PASS — `ruff check .` and `pip check` |
| Pipeline | PASS — 4 input rows, 4 accepted; A totals 30.5 and 5, B totals 20.0 and 5; exported CSV and SQLite integrity asserted |
| Benchmark | PASS — two bounded A/B runs, paired report and historical comparison executed; historical comparison was INCONCLUSIVE |
| Native | PASS — two clean MSYS2 UCRT64 Release builds and CTest 3/3 each |
| Wheel | PARTIAL — wheel and installed CLI pipeline pass; isolated venv imported dependencies through the development environment's `PYTHONPATH` |
| Dashboard | PASS — local health endpoint, populated-history API, and page returned successfully |
| Docker | BLOCKED — daemon pipe denied access; image/runtime gates not run |
| Security | PARTIAL — Bandit found no high-severity items; medium findings were reviewed as guarded/dynamic SQL patterns; dependency and dedicated secret scanners unavailable |
| Remote CI | BLOCKED — `gh` is installed but unauthenticated; no workflow result inspected |

Current verdict: **NOT RELEASE-READY**. Docker runtime, remote CI, and dataset redistribution terms remain blocked or unverified. No commit, push, package publication, deployment, or GitHub settings change was performed.

## Latest Phase 1 local verification — 2026-11-01

The current worktree passed 194 Python tests, repository-wide Ruff, and
`pip check`. A clean external MSVC Release build passed CTest 3/3; fresh native
runner and CLI smoke checks passed. An 80,550-byte wheel was built from an
external source copy and its modules, metadata, entry point, imports, and CLI
help were verified. The wheel was not installed in a separate environment.
Python 3.12.10 is installed but lacks project dependencies, so its package
tests/imports remain unverified. Remote CI, Linux, Docker runtime, and
deployment were not checked.

These are local Phase 1 checks, not release approval. Dataset terms,
container/remote validation, and all prior release gates above remain in
effect.
