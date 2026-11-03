# Security policy

BenchForge is an active research prototype, not a security-certified service.
Use the dashboard locally unless it is protected by a trusted network security
layer.

## Reporting a vulnerability

Do not post details of an unpatched, exploitable vulnerability in a public
issue. If private vulnerability reporting is enabled for the GitHub
repository, use that channel. Otherwise, contact the repository maintainers
privately through the contact information on the repository profile. Include
the affected version or commit, impact, and a minimal reproduction that does
not expose other users' data.

## Deployment boundaries

- The dashboard binds to loopback by default. `--allow-remote` is an explicit
  opt-in, not authentication or encryption.
- PDF upload, search, and extracted-text routes reject non-loopback clients,
  even when the dashboard is started with `--allow-remote`.
- Docker Compose publishes the host port on `127.0.0.1` by default.
- Do not expose the dashboard publicly without a trusted security layer.
- Benchmark executables and dataset processing consume local CPU, disk, and
  memory. Use the documented bounds and do not run untrusted binaries.
- PDF uploads are capped at 10 MiB and 200 pages by default (the upload byte
  cap can be configured up to 50 MiB). The filename is never used as a path;
  documents use generated IDs and are stored under `data/documents/` by
  default, which is ignored by Git.
- PDF extraction uses pypdf in the local dashboard process. It does not execute
  embedded content, perform OCR, or transmit documents externally, but it is
  not an OS-level sandbox. Process only PDFs you trust.
- The dashboard reads its configured database and fixed processed-summary
  files; PDF document routes are limited to generated IDs in the configured
  storage directory. It is not an arbitrary file browser.
- The optional dbt history workflow reads SQLite through a read-only
  connection, exports fixed benchmark columns to a bounded local CSV, and
  rejects an existing output path. `BENCHFORGE_HISTORY_CSV_PATH` must point to
  a local file; dbt rejects URI and UNC paths. No distributed-service
  credentials or remote endpoints are consumed by the current application.
- PostgreSQL, Redis, Kafka/Redpanda, CDC, S3-compatible storage, Airflow, and
  Spark adapters are not implemented. Do not infer safety or compatibility
  with an installed service from the architecture evaluation. In particular,
  never point tests at an unknown database or production service.

## Scope

The project does not currently promise a vulnerability response SLA. Dependency
and code review results apply only to the exact revision and tools actually
checked; they are not a security guarantee.
