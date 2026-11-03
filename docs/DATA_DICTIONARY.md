# BenchForge data dictionary

This dictionary describes fields currently persisted or returned by the local
application. It does not define fields for the four catalog sources that have
not yet been integrated.

## Benchmark observations

`benchmark_samples` is the SQLite measurement table. CSV exports use this
stable column order:

| Field | Meaning |
|---|---|
| `id` | Local SQLite row ID. |
| `timestamp_utc` | Observation timestamp in UTC. |
| `workload` | Workload name. |
| `sample_index` | Sample order within its run/variant. |
| `elapsed_ns` | Measured elapsed time in nanoseconds. |
| `extra_work` | Optional workload-specific work parameter. |
| `os` | Operating system recorded for the observation. |
| `python_version` | Python runtime recorded by orchestration. |
| `schema_version` | Measurement record schema version. |
| `run_id` | Identifier grouping observations in one experiment. |
| `variant` | `baseline`, `candidate`, or `single` measurement. |
| `binary` | Native executable label/path stored in the experiment record. |
| `config_json` | JSON serialization of the recorded configuration. |
| `pair_index` | Matching index used by paired A/B analysis, when present. |
| `execution_order` | Recorded interleaving order, when present. |
| `seed` | Optional deterministic workload seed. |

`elapsed_ns` is latency per sample, not throughput. Byte throughput is derived
from the workload's byte count and elapsed time and is reported in bytes per
second.

## Pipeline table exports

The `pipeline export-csv` command exports a selected ingested or aggregated
SQLite table with its stored column names. Rows are ordered by column values
for repeatable output. Text cells are protected against spreadsheet formulas,
and row, cell, and total output-size limits apply. Binary SQLite cells are
encoded as Base64.

## Pipeline runs

The dashboard's bounded `/api/pipelines` response includes run summaries:

| Field | Meaning |
|---|---|
| `run_id` | Local pipeline execution ID. |
| `pipeline_name` | Ingestion, normalization, or aggregation operation. |
| `source_name` | Source filename label; absolute local paths are omitted. |
| `destination` | SQLite destination table or output label. |
| `status` | `succeeded` or `failed`. |
| `input_rows`, `accepted_rows`, `rejected_rows`, `output_rows` | Row counts at the recorded processing stages. |
| `duration_ns` | Pipeline duration in nanoseconds. |
| `rows_per_second` | Processed-row throughput derived from recorded counts and duration. |
| `quality` | Persisted quality-check names, results, and detail values. |
| `lineage` | Source-to-transformation-to-output references where available. |

## Public-data summaries

Implemented dataset summaries use `dataset`, `label`, `source_url`,
`download_url`, `access_date`, `license`, `rows_processed`, `source_filename`,
`source_size_bytes`, `source_sha256`, `kpis`, `quality`, `charts`, and `notes`.
Taxi summaries additionally identify the bounded sample method. KPI and chart
values are derived from locally processed source data; terms and sample
limitations are documented in [DATASETS.md](./DATASETS.md).

## PDF analysis

The upload response contains `document_id`, a fixed `filename` label
(`uploaded.pdf`), `page_count`, `status`, `extraction_method`,
`character_count`, `word_count`, `text_truncated`, and `text_preview`. Optional
search results contain `matches` with a one-based `page` number and escaped
`snippet`. The stored PDF and extracted text are local files in the configured
documents directory; full text is returned only through the dedicated
attachment endpoint. Scanned/image-only pages do not receive OCR.
