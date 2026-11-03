# Recovery status

Recovery began on 2026-10-29. This checkpoint records the repository state
observed before running a new verification suite. It is not a release approval.

## Repository state

- The expected checkout exists at `C:\Users\PC\source\BenchForge`.
- Current branch: `feature/ab-comparison`, tracking
  `origin/feature/ab-comparison`, at `e6dd6e3` (`Polish BenchForge README`).
- Local `main` tracks `origin/main` and is reported as nine commits behind.
  `git ls-remote --symref origin HEAD` confirms the remote default branch is
  `main` (remote HEAD `a9c3c2e`).
- There are no staged changes. Existing tracked modifications affect CI,
  configuration, README, native CMake/C++ sources, the benchmark CLI and
  analysis, and their tests. Existing untracked work adds container files,
  architecture/data/release documentation, C++ workloads, Python workload,
  pipeline, dashboard, export, history, and public-data modules, and tests.
- Ignored user/local files include virtual environments, caches, build output,
  README backups, SQLite databases, and generated/processed data. These are
  retained and must not be cleaned or published.
- No benchmark, pytest, CMake, CTest, or pip process was running at inspection.

## Environment and existing implementation

- The workspace `.venv` uses Python 3.14.2 and contains pytest 9.1.1, Ruff
  0.17.1, SciPy 1.18.1, NumPy 2.5.4, Typer 0.27.3, and Pydantic 2.14.1.
  The system interpreter is Python 3.13.9 with pip 26.2.1.
- Git 2.56.0 and CMake 4.4.3 are available. Approximately 27 GB is free on C:.
- MinGW GCC 6.3.0 and Ninja are on PATH; MSVC `cl` was not found. Existing
  Visual Studio native build output is present under ignored `build/`.
- The package declares Python >=3.12 and runtime dependencies in
  `pyproject.toml`; pypdf 6.21.4 was installed in the workspace environment
  after adding it as a declared runtime dependency.
- Code and tests for benchmark/A-B execution, native workloads, SQLite
  history, exports, bounded CSV processing, public-data summaries, and
  dashboard APIs are present. Fresh baseline checks cover the benchmark and
  native suite; focused checks now also cover the local PDF upload workflow.
- The PDF dashboard now validates extension/signature/structure, enforces a
  configurable upload size (10 MiB default, 50 MiB maximum) and 200-page cap,
  stores uploaded files under generated IDs, extracts text without OCR,
  searches, previews, reports statistics, and exports text. Its parser is
  in-process, not an OS-level sandbox.
- Two of seven catalog sources remain without processors. Generic
  JSON/Parquet pipeline I/O, a static Pages demo, and verified deployment
  remain incomplete or unverified.

## Prior claims versus current evidence

`docs/IMPLEMENTATION_STATUS.md`, `docs/FINAL_VERIFICATION.md`, and
`docs/RELEASE_CHECKLIST.md` contain earlier reports of 146 passing Python
tests, scoped Ruff success, and a 3/3 native CTest run. Fresh recovery checks
now confirm 146 Python tests passing, repository-wide Ruff passing after
four import-related findings in the new pipeline code/tests were corrected,
and native Release build plus all three CTests passing. CLI help, `pip check`,
and `git diff --check` also passed. After the PDF workflow was added, the
focused PDF/dashboard suite passed (22 tests) and the full suite passed
(172 tests) after adding two public-data processors and dataset CLI commands.
Repository-wide Ruff and `pip check` passed. A live local HTTP
smoke passed health, dashboard, upload, search, and text-export checks using
a temporary directory. The latest 79,883-byte wheel was rebuilt and installed
in the clean Python 3.14 environment; CLI, public-data, dashboard, and pipeline
modules imported, main and pipeline CLI help passed, JSON ingestion succeeded,
Wine Quality validation processed 6,497 records, and `pip check` passed. See
`docs/FINAL_VERIFICATION.md` for exact commands and limitations.

The two historical E402 findings were not reproduced: `ruff check scripts`
passes. Docker runtime, dependency vulnerability scan, remote CI result, and
Pages deployment remain unverified.

## Deployment

No commit, push, release, or deployment was performed. Two bounded public
archives (Household Power and Wine Quality) were downloaded from official
UCI endpoints after their sizes and CC BY 4.0 terms were verified. The remote
default branch is `main`. Repository visibility, Pages availability, and
workflow status have not yet been verified. GitHub CLI cannot query repository
settings because this environment is not logged in; no credential was
requested or added.

## Remaining work

Continue verifying dataset terms, endpoints, sizes, and attribution before
downloads or integrations. The seven-entry catalog currently has five
processed datasets; Citi Bike and Student Performance remain metadata-only
because the first is a 369 MB archive with terms requiring review and the
second is a metadata-only source; UCI lists CC BY 4.0, and no archive was downloaded. Complete the other
project requirements, inspect CI and deployment settings when access permits,
and keep all current tracked, untracked, and ignored work intact. No commit,
push, or deployment has been performed.
