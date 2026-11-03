# Apache Iceberg compatibility investigation

**Research date:** 2026-11-01
**Status:** Documentation review only; BenchForge does not implement Iceberg.

## Findings

Apache Iceberg is a table format for managing collections of data files. Its
table state is represented by metadata and snapshots, which refer to manifests
and data files; it is not itself a query engine or a replacement for the
underlying files. The current Apache specification says format versions 1, 2,
and 3 are complete and adopted, while version 4 is still under active
development and has not been formally adopted.

DuckDB's Iceberg extension documents two distinct access modes:

- A direct `iceberg_scan` of a table or metadata file is read-only and does
  not require a catalog.
- Catalog-managed access through an Iceberg REST catalog enables broader
  operations, including writes. The write documentation says a catalog is
  required to commit updated table metadata; writing to a direct scan is not
  supported.

This distinction matters to BenchForge: adding a DuckDB dependency does not
automatically provide tested Iceberg support. Catalog authentication,
object-store access, format-version behavior, concurrent commits, and
transaction semantics would each need separate design and verification.

## Compatibility with current architecture

BenchForge's pipeline reads bounded CSV, JSON, JSONL, and Parquet sources into
SQLite. Optional DuckDB currently performs bounded read-only analytics on
local CSV and Parquet files. There is no Iceberg reader, REST-catalog
connection, Iceberg writer, Iceberg-specific configuration, or fixture.
SQLite remains the store for application history, pipeline outputs, quality
reports, and lineage.

Accordingly:

| Capability | Status |
|---|---|
| Read local Iceberg table through DuckDB | Not implemented or tested |
| Read Iceberg through a REST catalog | Not implemented or tested |
| Write or mutate Iceberg tables | Not implemented or tested |
| Object-store Iceberg access | Not implemented or tested |
| Iceberg format-version compatibility claim | None |

## Recommendation

Do not add an Iceberg dependency or advertise Iceberg compatibility as part of
the current phase. If a concrete use case is approved later, first select a
target DuckDB and Iceberg format version, decide whether access is direct and
read-only or catalog-managed, and define credentials and storage boundaries.
Then add small local interoperability fixtures and tests for snapshot reads,
schema evolution, and the intended failure cases before exposing a CLI
workflow. Do not treat successful reads of ordinary Parquet files as proof of
Iceberg compatibility.

## Sources

- [Apache Iceberg table specification](https://iceberg.apache.org/spec/)
  — format-version status, metadata, snapshots, and manifests.
- [DuckDB Iceberg extension overview](https://duckdb.org/docs/2.0/core_extensions/iceberg/overview.html)
  — direct table reads and catalog-managed access.
- [DuckDB writing to Iceberg](https://duckdb.org/docs/2.0/core_extensions/iceberg/writing_to_iceberg.html)
  — catalog requirement and documented write operations.

These pages were reviewed on 2026-11-01. Documentation describes upstream
capabilities, not behavior verified in BenchForge.
