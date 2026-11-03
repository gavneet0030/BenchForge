# Distributed integration evaluation

## Status and scope

BenchForge currently runs as a local Python/C++ application. SQLite stores
history, JSONL stores raw observations, and bounded Python, DuckDB, and Polars
paths analyze local files. No PostgreSQL, Redis, Kafka/Redpanda, CDC,
MinIO/S3, Airflow, or Spark adapter is implemented.

The Phase 6 decision is to keep these services optional and not add clients
without a demonstrated workload. A local PostgreSQL Windows service was
visible during this audit, but its database and owner are unknown. BenchForge
did not connect to it, read its configuration, or modify it. Docker Desktop's
service was stopped and no engine-backed integration was attempted.

The only new data-platform carryover completed here is an explicit,
read-only SQLite history snapshot for the existing optional dbt project:
`benchforge export-history` writes bounded local CSV, and dbt consumes that
file only when `BENCHFORGE_HISTORY_CSV_PATH` is set. Without that variable,
the deterministic synthetic seed remains the default. This is a local dbt
workflow, not a distributed service integration.

## Runtime inventory from this audit

- Windows, 4 logical processors, about 302 MiB free RAM, and 11.04 GiB free
  disk. The workspace policy reserves 8 GiB; large environment installs or
  data copies were therefore avoided.
- Docker CLI exists, but the Docker Desktop service is stopped. No container
  was started.
- PostgreSQL 18 Windows service is running. No `psql` executable or Python
  PostgreSQL driver is available from the checked environments. No
  connection was attempted because this is an existing, unidentified service.
- Redis, Kafka/Redpanda, MinIO, Airflow, and Spark executables and corresponding
  Python clients were not found in the checked environment.
- The pre-existing external Python 3.12 environment includes DuckDB 1.5.6,
  Polars 1.44.2, PyArrow 26.0.0, dbt-core 1.12.6, and dbt-duckdb 1.11.1.
  No dependency was installed for this audit.

## Cross-integration architecture boundary

No common adapter base class is introduced. There are no implemented remote
adapters to share, and a speculative abstraction would add maintenance
without a current caller. If at least two concrete service adapters become
necessary, evaluate a small protocol around:

1. explicit configuration validation and dependency availability checks;
2. bounded `run` operations that return ordinary BenchForge sample records
   with one run ID and explicit workload/variant metadata;
3. correctness or delivery verification before timing is accepted;
4. explicit timeout and resource limits;
5. idempotent cleanup limited to resources created by that run; and
6. context-managed connections that redact credentials from errors and logs.

SQLite remains the application store. A future remote adapter must not change
the default storage backend or make its service a core dependency. External
credentials should be read from an approved secret mechanism; there are no
distributed-service environment variables consumed by BenchForge today.

## Stage 36 — PostgreSQL

**Disposition:** Not required for the current single-user local product;
runtime compatibility is not verified.

SQLite already stores benchmark history, comparisons, pipeline runs, and
lineage. No multi-user history, remote database, or concurrent writer
requirement was provided. The Windows PostgreSQL service found during the
audit is treated as user-owned and was not contacted.

If a real use case is approved, keep PostgreSQL behind an optional,
read-only-first adapter. Use a short connection timeout, bounded pool only
when concurrent callers justify it, parameterized values, fixed query shapes,
and explicit transactions for any write benchmark. Isolate integration tests
with a disposable database/schema and a least-privilege role; never point tests
at the existing Windows service by default.

- **Installation/configuration:** no PostgreSQL client extra or configuration
  is currently defined. A future change must add an optional package extra
  and document secret handling; do not put DSNs or passwords in source,
  profiles, sample output, or logs.
- **Security:** use TLS verification for remote endpoints; never accept
  arbitrary SQL from a CLI string; bind values and quote only validated
  identifiers. A read-only mode must use a database role that cannot write,
  rather than relying only on application flags.
- **Resource/tests:** no client, connection validation, timeout test, or
  PostgreSQL integration test exists. Required tests before implementation
  include missing-driver, bad-config, refused connection, timeout, parameter
  binding, read-only role, correctness, and cleanup.
- **Troubleshooting:** verify that an explicitly provisioned test service is
  reachable and that its credentials are scoped to a disposable database.
  Do not diagnose by probing unknown local service databases.

## Stage 37 — Redis

**Disposition:** Not required; no shared cache, queue, rate-limit, or
cross-process session currently exists.

There are no Redis operations in the benchmark path. If a repeatable cache
workload is later requested, use a unique run prefix, bounded key/value sizes,
short operation timeouts, an explicit serialization format, and TTLs. Cleanup
must delete only keys created under that run prefix; it must never issue a
database-wide flush.

- **Installation/configuration:** no Redis client, extra, or environment
  variables are defined. A future adapter must keep Redis optional and accept
  endpoint/credentials only through approved runtime configuration.
- **Security:** require TLS and authentication for remote endpoints; redact
  usernames, passwords, and connection URLs; reject unknown hosts by default.
- **Resource/tests:** no live or mocked Redis adapter test exists. Required
  tests include serialization round trips, SET/GET, bounded batches, expiry,
  timeout, connection failure, and prefix-scoped cleanup. Live testing requires
  a specifically provisioned isolated local instance.
- **Troubleshooting:** check the configured test endpoint and TTL with a
  disposable namespace; do not connect to an unknown production Redis.

## Stage 38 — Kafka / Redpanda

**Disposition:** Not required; the current workflow has no event stream,
consumer, or streaming ingestion source.

No producer/consumer adapter, topic, schema registry, or offset store exists.
If event throughput becomes a requirement, define message schemas and a
bounded producer batch, verify consumed message IDs/checksums before accepting
latency, and include end-to-end delay separately from producer acknowledgment.
Consumer offsets must be scoped to an isolated group and test topic.

- **Installation/configuration:** no Kafka client or broker dependency is
  installed or declared. Any future integration must remain optional and
  document local broker, topic, group, TLS, and authentication settings.
- **Security:** restrict bootstrap servers to explicitly configured hosts,
  require TLS/authentication remotely, bound message bytes and queue depth,
  and do not emit secrets or full payloads to logs.
- **Semantics:** no exactly-once behavior is implemented or claimed. A future
  consumer must document its delivery guarantees and duplicate handling.
- **Resource/tests:** no serialization, producer/consumer failure, offset, or
  broker integration tests exist. Required tests include bounded batch size,
  delivery verification, retry/duplicate behavior, offset recovery, shutdown,
  and a real isolated broker test when available.
- **Troubleshooting:** distinguish broker reachability from schema, topic, and
  consumer-group errors; do not retry an unbounded send or consume loop.

## Stage 39 — CDC / Debezium

**Disposition:** Architecture documented only; no CDC runtime or compatibility
claim.

CDC is not required because BenchForge has no external transactional source
whose changes must be streamed. A future evaluation would require a
specifically provisioned PostgreSQL source, logical replication publication,
Debezium connector, Kafka/Redpanda broker, and a consumer. The intended flow
would be:

`PostgreSQL WAL -> Debezium source connector -> broker topic -> bounded consumer -> validated BenchForge records`

Events should preserve source partition and log sequence/transaction metadata.
Consumers must tolerate at-least-once delivery by recording an idempotency key
derived from source identity and position; duplicates must be detected rather
than silently counted twice. Offset recovery must coordinate durable consumer
progress with persisted output. Schema evolution needs compatibility rules
and a tested recovery path for incompatible events. Snapshot and streaming
phases must not be double-counted.

- **Installation/configuration:** no connector, broker, topic, or CDC settings
  are defined. No live CDC or exactly-once claim is made.
- **Security:** least-privilege replication role, TLS, connector secret
  provider, restricted topic ACLs, bounded retention, and redacted connector
  logs are prerequisites.
- **Tests:** unit fixtures should cover create/update/delete events, duplicate
  delivery, offset restart, schema changes, malformed records, and snapshot
  recovery. End-to-end verification requires isolated PostgreSQL, Debezium,
  and broker services; mocks alone cannot verify CDC behavior.
- **Troubleshooting:** inspect connector state, source LSN/offset progress,
  broker lag, and consumer checkpoints separately. Recovery must be rehearsed
  from a disposable fixture before production data is considered.

## Stage 40 — MinIO / S3-compatible storage

**Disposition:** Not required; current inputs, summaries, and generated
documents use bounded local files.

There is no object-storage adapter, bucket, or cloud credential configuration.
If object storage becomes necessary, use a dedicated test bucket and a unique
per-run prefix; benchmark bounded upload, download, and listing operations;
verify checksums before timing is reported; and limit multipart concurrency.
Cleanup may delete only object keys recorded as created by that run. It must
not delete a bucket or enumerate/delete unrelated objects.

- **Installation/configuration:** no S3 client or MinIO client is installed or
  declared. A future optional dependency must not change local-file defaults.
- **Security:** credentials belong in a secret provider, not command output;
  enforce TLS for remote endpoints, least-privilege bucket/prefix policies,
  size limits, and host allowlists. Do not follow arbitrary endpoint URLs.
- **Tests:** mocked tests should cover object checksums, short/failed reads,
  multipart bounds, timeout, and prefix-only cleanup. They are not live
  service tests. Live tests require a dedicated disposable bucket.
- **Troubleshooting:** separate credentials, endpoint, bucket policy,
  multipart, and checksum failures; leave unrecognized objects untouched.

## Stage 41 — Apache Airflow

**Disposition:** Not required; current users invoke local CLI workflows and
do not need a shared scheduler.

No DAG, scheduler, task runner, or Airflow dependency exists. If scheduling is
requested, a DAG should invoke the supported BenchForge CLI in an isolated
worker, set one bounded resource pool, store the run ID as task metadata, and
use retries only for idempotent orchestration failures. A retry must not
silently create a duplicate benchmark and label it as the original run.

- **Installation/configuration:** no Airflow package or executor is
  configured. Airflow is a heavyweight service and should be evaluated on a
  supported Linux/container host rather than installed on this constrained
  Windows laptop.
- **Security:** use a dedicated worker identity, secret backend, restricted
  DAG import/write access, and sanitized task logs. Never pass credentials as
  visible command-line arguments.
- **Tests:** DAG parse, dependency ordering, retry/idempotency behavior, task
  failure propagation, resource limits, and output/run-ID persistence are
  required before an example DAG is useful. No Airflow runtime test exists.
- **Troubleshooting:** verify worker environment and native binary paths
  separately from scheduler state; do not infer a benchmark failure from a
  scheduler retry alone.

## Stage 42 — Apache Spark

**Disposition:** Distributed Spark execution is not required for the verified
local datasets. No Spark adapter or performance measurement is implemented.

| Engine | Current role | Likely fit | Limitation for this project |
|---|---|---|---|
| Python / csv | Simple bounded CSV work | Small local inputs and transparent transformations | Python row loops have interpreter overhead |
| PyArrow | CSV/Parquet parsing and projection | Columnar formats and memory-efficient interchange | Not a distributed query scheduler |
| DuckDB | Optional local SQL analytics | Local columnar scans and aggregation | One host; not a cluster compute service |
| Polars | Optional lazy local dataframe operations | Local multi-threaded scans and transformations | Does not itself provide Spark-style distributed scheduling |
| Pandas | Not part of the current optional-engine evidence | Familiar in-memory dataframe workflows | Not distributed; no comparable current benchmark in this checkout |
| Spark | Not installed or measured | Cluster-scale processing with a configured runtime | JVM/cluster startup and operational overhead are unjustified without a scale target |

No comparative Spark latency, memory, or startup measurement was run. Existing
engine results in `TESTING.md` are bounded local synthetic workloads and are
not a Spark comparison. A future adapter needs representative CSV and Parquet
fixtures, correctness checks, an explicit memory/worker budget, startup time
reported separately, and a real Spark runtime; mocks cannot establish
distributed performance.

- **Installation/configuration:** no PySpark, Spark distribution, or Java
  runtime is provisioned for BenchForge. Do not install a large runtime on the
  current laptop as part of this phase.
- **Security:** pin approved input locations, avoid arbitrary remote URI
  schemes, bound partitions and output, and redact cloud credentials from
  Spark configuration and logs.
- **Troubleshooting:** report Java/runtime, master/worker, input access, and
  executor-memory errors independently. No Spark runtime verification exists.

## Phase 6 test and revisit policy

No optional service packages were installed, no unknown service was contacted,
and no service integration was mocked and reported as live. The phase's
resource-safe decision is to defer clients until a concrete workload,
isolation plan, credentials, and runtime budget are approved. Reopen an
individual stage only when its required service can be provisioned without
touching user data. The local dbt history export has separate unit and
export-backed dbt verification; it does not change this distributed-services
decision.
