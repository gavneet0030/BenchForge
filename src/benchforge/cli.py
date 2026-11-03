"""BenchForge command-line interface."""

import csv
import json
import os
import platform
import sqlite3
import statistics
import sys
from datetime import UTC
from pathlib import Path

import typer
from rich import print

from benchforge import __version__
from benchforge.ab import run_ab_benchmark
from benchforge.documents import MAX_CONFIGURABLE_PDF_BYTES, MAX_PDF_BYTES
from benchforge.metrics import summarize_latencies
from benchforge.runner import (
    MAX_NATIVE_EXTRA_WORK,
    MAX_NATIVE_ITERATIONS,
    MAX_NATIVE_RUNS,
    MAX_NATIVE_WARMUPS,
    run_benchmark,
)
from benchforge.stats import (
    MINIMUM_PAIRED_OBSERVATIONS,
    bootstrap_paired_median_ratio_ci,
    classify_regression,
    paired_median_ratio,
    wilcoxon_paired_pvalue,
)
from benchforge.storage import initialize_database, save_samples

app = typer.Typer(
    name="benchforge",
    help="Statistical performance regression research toolkit.",
    no_args_is_help=True,
)
pipeline_app = typer.Typer(
    name="pipeline",
    help="Run bounded local data pipelines and inspect their persisted metrics.",
    no_args_is_help=True,
)
dataset_app = typer.Typer(
    name="datasets",
    help="Inspect, fetch, and validate bounded public-data sources.",
    no_args_is_help=True,
)
analytics_app = typer.Typer(
    name="analytics",
    help="Inspect and summarize local analytical files with optional DuckDB.",
    no_args_is_help=True,
)
quality_app = typer.Typer(
    name="quality",
    help="Run deterministic, read-only data-quality checks on local files.",
    no_args_is_help=True,
)
catalog_app = typer.Typer(
    name="catalog",
    help="Inspect local dataset metadata and processing lineage.",
    no_args_is_help=True,
)
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(dataset_app, name="datasets")
app.add_typer(analytics_app, name="analytics")
app.add_typer(quality_app, name="quality")
app.add_typer(catalog_app, name="catalog")


def _print_pipeline_result(result: dict) -> None:
    print(f"Pipeline: {result['pipeline_name']}")
    print(f"Run ID: {result['run_id']}")
    print(f"Status: {result['status']}")
    print(f"Rows: {result['input_rows']} input / {result['accepted_rows']} accepted / "
          f"{result['rejected_rows']} rejected / {result['output_rows']} output")
    print(f"Duration: {result['duration_ns'] / 1_000_000:.3f} ms")
    print(f"Throughput: {result['rows_per_second']:.2f} rows/s")
    for check in result["quality"]:
        status = "PASS" if check["passed"] else "FAIL"
        print(f"Quality {status}: {check['name']} ({json.dumps(check['details'], sort_keys=True)})")


def _parse_numeric_ranges(specifications: list[str]) -> dict[str, tuple[float | None, float | None]]:
    ranges: dict[str, tuple[float | None, float | None]] = {}
    for specification in specifications:
        parts = specification.split(":")
        if len(parts) != 3 or not parts[0].strip():
            raise typer.BadParameter(
                "Numeric ranges use COLUMN:MIN:MAX; leave a bound blank for no limit."
            )
        column, lower, upper = parts
        if column in ranges:
            raise typer.BadParameter(f"Numeric range specified more than once for {column!r}.")
        try:
            minimum = float(lower) if lower else None
            maximum = float(upper) if upper else None
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid numeric range: {specification}") from exc
        ranges[column] = (minimum, maximum)
    return ranges


@pipeline_app.command("ingest-csv")
def pipeline_ingest_csv(
    source: Path = typer.Argument(..., help="Local CSV input file."),
    table: str = typer.Option("ingested_data", "--table", help="SQLite destination table."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite history and analytics database."
    ),
    required_column: list[str] = typer.Option(
        [], "--required-column", help="Required column name; may be supplied more than once."
    ),
    duplicate_policy: str = typer.Option("keep", "--duplicate-policy"),
    numeric_range: list[str] = typer.Option(
        [],
        "--numeric-range",
        help="Range in COLUMN:MIN:MAX form; repeat as needed and leave bounds blank for open limits.",
    ),
):
    """Validate, profile, and load a CSV file into the local analytical database."""
    from benchforge.pipeline import ingest_csv

    if duplicate_policy not in {"keep", "drop", "error"}:
        raise typer.BadParameter("Choose keep, drop, or error for --duplicate-policy.")
    ranges = _parse_numeric_ranges(numeric_range)
    try:
        result = ingest_csv(
            database,
            source,
            table=table,
            required_columns=required_column,
            duplicate_policy=duplicate_policy,
            numeric_ranges=ranges,
        )
    except (OSError, sqlite3.Error, UnicodeError, ValueError, csv.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_pipeline_result(result)
    print(f"Column profile: {json.dumps(result['profile'], sort_keys=True)}")


@pipeline_app.command("ingest")
def pipeline_ingest(
    source: Path = typer.Argument(..., help="Local CSV, JSON, JSONL, or Parquet input file."),
    table: str = typer.Option("ingested_data", "--table", help="SQLite destination table."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite history and analytics database."
    ),
    input_format: str = typer.Option(
        "auto", "--format", help="Input format: auto, csv, json, jsonl, or parquet."
    ),
    required_column: list[str] = typer.Option(
        [], "--required-column", help="Required column name; may be supplied more than once."
    ),
    column: list[str] = typer.Option(
        [], "--column", help="Project selected columns; may be supplied more than once."
    ),
    duplicate_policy: str = typer.Option("keep", "--duplicate-policy"),
    numeric_range: list[str] = typer.Option(
        [],
        "--numeric-range",
        help="Range in COLUMN:MIN:MAX form; repeat as needed and leave bounds blank for open limits.",
    ),
):
    """Validate and load one bounded CSV, JSON, JSONL, or Parquet file."""
    from benchforge.pipeline import ingest_file

    if duplicate_policy not in {"keep", "drop", "error"}:
        raise typer.BadParameter("Choose keep, drop, or error for --duplicate-policy.")
    ranges = _parse_numeric_ranges(numeric_range)
    try:
        result = ingest_file(
            database,
            source,
            input_format=input_format,
            table=table,
            required_columns=required_column,
            duplicate_policy=duplicate_policy,
            numeric_ranges=ranges,
            columns=column or None,
        )
    except (OSError, sqlite3.Error, TypeError, UnicodeError, ValueError, csv.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_pipeline_result(result)
    print(f"Column profile: {json.dumps(result['profile'], sort_keys=True)}")


@pipeline_app.command("normalize")
def pipeline_normalize(
    sources: list[Path] = typer.Argument(..., help="Two or more compatible local CSV files."),
    table: str = typer.Option("normalized_data", "--table", help="SQLite destination table."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite history and analytics database."
    ),
    duplicate_policy: str = typer.Option("keep", "--duplicate-policy"),
):
    """Normalize headers and combine compatible CSV files."""
    from benchforge.pipeline import normalize_csv_files

    try:
        if duplicate_policy not in {"keep", "drop", "error"}:
            raise typer.BadParameter("Choose keep, drop, or error for --duplicate-policy.")
        result = normalize_csv_files(
            database, sources, table=table, duplicate_policy=duplicate_policy
        )
    except (OSError, sqlite3.Error, UnicodeError, ValueError, csv.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_pipeline_result(result)
    print(f"Column profile: {json.dumps(result['profile'], sort_keys=True)}")


@pipeline_app.command("aggregate")
def pipeline_aggregate(
    source_table: str = typer.Option(..., "--source-table", help="Previously ingested SQLite table."),
    group_by: str = typer.Option(..., "--group-by", help="Column used to group records."),
    metric: str = typer.Option(..., "--metric", help="Numeric column to aggregate."),
    destination: str = typer.Option(
        "analytical_summary", "--destination", help="SQLite output table."
    ),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite history and analytics database."
    ),
):
    """Create a grouped count, sum, and mean analytical table."""
    from benchforge.pipeline import aggregate_table

    try:
        result = aggregate_table(
            database,
            source_table,
            group_by=group_by,
            metric=metric,
            destination=destination,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_pipeline_result(result)


@pipeline_app.command("export-csv")
def pipeline_export_csv(
    table: str = typer.Option(..., "--table", help="SQLite table to export."),
    output: Path = typer.Option(..., "--output", help="CSV file to write."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite analytics database."
    ),
):
    """Export a SQLite pipeline table to bounded, spreadsheet-safe CSV."""
    from benchforge.exports import export_table_csv

    try:
        count = export_table_csv(database, table, output)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(f"Exported {count} rows from {table} to {output}")


@dataset_app.command("list")
def datasets_list(
    raw_dir: Path = typer.Option(
        Path("data/public_datasets/raw"), "--raw-dir", help="Local raw-source directory."
    ),
    processed_dir: Path = typer.Option(
        Path("data/public_datasets/processed"),
        "--processed-dir",
        help="Local processed-summary directory.",
    ),
):
    """List dataset provenance and local availability; performs no network access."""
    from benchforge.public_data import list_datasets

    for dataset in list_datasets(raw_dir, processed_dir):
        state = dataset["status"].replace("_", " ").upper()
        raw_state = (
            f"{dataset['local_size_bytes']:,} bytes"
            if dataset["local_available"]
            else "not downloaded"
        )
        processed_state = "processed summary present" if dataset["processed_available"] else "not processed"
        print(
            f"{dataset['id']}: {state} | {raw_state} | {processed_state}\n"
            f"  Publisher: {dataset['publisher']}\n"
            f"  Source: {dataset['official_url']}\n"
            f"  Format: {dataset['format']}\n"
            f"  Terms: {dataset['license_terms']}"
        )


@dataset_app.command("fetch")
def datasets_fetch(
    dataset: str = typer.Argument(..., help="Implemented dataset ID."),
    raw_dir: Path = typer.Option(
        Path("data/public_datasets/raw"), "--raw-dir", help="Local raw-source directory."
    ),
):
    """Fetch one implemented source, enforcing its configured byte limit."""
    from urllib.error import URLError

    from benchforge.public_data import DATASETS, _download

    if dataset not in DATASETS:
        supported = ", ".join(sorted(DATASETS))
        raise typer.BadParameter(f"Dataset is not implemented. Choose one of: {supported}.")
    try:
        source = _download(dataset, raw_dir)
    except (OSError, URLError, ValueError) as exc:
        raise typer.BadParameter(f"Could not fetch {dataset}: {exc}") from exc
    print(f"Fetched {dataset}: {source.name} ({source.stat().st_size:,} bytes)")


@dataset_app.command("validate")
def datasets_validate(
    dataset: str = typer.Argument(..., help="Implemented dataset ID."),
    source: Path | None = typer.Option(None, "--source", help="Existing local source file."),
    download: bool = typer.Option(
        False, "--download", help="Fetch the official source before validating it."
    ),
    raw_dir: Path = typer.Option(
        Path("data/public_datasets/raw"), "--raw-dir", help="Local raw-source directory."
    ),
    output_dir: Path = typer.Option(
        Path("data/public_datasets/processed"),
        "--output-dir",
        help="Directory for the validated aggregate JSON.",
    ),
    max_rows: int = typer.Option(100_000, "--max-rows", min=1),
):
    """Validate a supported source and write its compact analytical summary."""
    import zipfile
    from urllib.error import URLError

    from benchforge.public_data import DATASETS, _download, process_dataset

    if dataset not in DATASETS:
        supported = ", ".join(sorted(DATASETS))
        raise typer.BadParameter(f"Dataset is not implemented. Choose one of: {supported}.")
    if source is not None and download:
        raise typer.BadParameter("Choose either --source or --download, not both.")
    try:
        if download:
            input_path = _download(dataset, raw_dir)
        elif source is not None:
            input_path = source
        else:
            raise typer.BadParameter("Provide --source or use --download.")
        output = output_dir / f"{dataset}.json"
        result = process_dataset(dataset, input_path, output, max_rows=max_rows)
    except (OSError, URLError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        raise typer.BadParameter(f"Could not validate {dataset}: {exc}") from exc
    print(
        f"Validated {result['rows_processed']:,} rows for {dataset}; "
        f"summary: {output}"
    )
    print(f"Source SHA-256: {result['source_sha256']}")


@analytics_app.command("inspect")
def analytics_inspect(
    source: Path = typer.Argument(..., help="Local CSV or Parquet file."),
    database: Path | None = typer.Option(
        None, "--database", help="Optional file-backed DuckDB database; defaults to memory."
    ),
):
    """Inspect a bounded local CSV or Parquet file without modifying it."""
    from benchforge.analytics import inspect_file

    try:
        result = inspect_file(source, database=database)
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))


@analytics_app.command("summarize")
def analytics_summarize(
    source: Path = typer.Argument(..., help="Local CSV or Parquet file."),
    group_by: str | None = typer.Option(None, "--group-by", help="Optional grouping column."),
    metric: str | None = typer.Option(
        None, "--metric", help="Optional numeric metric to sum and average by group."
    ),
    limit: int = typer.Option(100, "--limit", min=1, max=1_000),
    database: Path | None = typer.Option(
        None, "--database", help="Optional file-backed DuckDB database; defaults to memory."
    ),
):
    """Summarize rows or bounded groups using fixed read-only analytical queries."""
    from benchforge.analytics import summarize_file

    try:
        result = summarize_file(
            source,
            group_by=group_by,
            metric=metric,
            limit=limit,
            database=database,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))


def _parse_quality_types(specifications: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for specification in specifications:
        column, separator, expected_type = specification.partition(":")
        if not separator or not column.strip() or not expected_type.strip():
            raise typer.BadParameter("Types use COLUMN:TYPE; repeat for each column.")
        if column in result:
            raise typer.BadParameter(f"Type specified more than once for {column!r}.")
        result[column] = expected_type
    return result


def _parse_quality_pairs(specifications: list[str], option: str) -> dict[str, tuple[int | None, int | None]]:
    result: dict[str, tuple[int | None, int | None]] = {}
    for specification in specifications:
        parts = specification.split(":")
        if len(parts) != 3 or not parts[0].strip():
            raise typer.BadParameter(
                f"{option} uses COLUMN:MIN:MAX; leave a bound blank for no limit."
            )
        try:
            lower = int(parts[1]) if parts[1] else None
            upper = int(parts[2]) if parts[2] else None
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid {option} bounds: {specification}") from exc
        if parts[0] in result:
            raise typer.BadParameter(f"{option} specified more than once for {parts[0]!r}.")
        result[parts[0]] = (lower, upper)
    return result


@quality_app.command("validate")
@catalog_app.command("validate")
def quality_validate(
    source: Path = typer.Argument(..., help="Local CSV, JSON, JSONL, or Parquet file."),
    input_format: str = typer.Option("auto", "--format"),
    required_column: list[str] = typer.Option([], "--required-column"),
    not_null: list[str] = typer.Option([], "--not-null"),
    unique: list[str] = typer.Option([], "--unique"),
    column_type: list[str] = typer.Option([], "--type", help="Expected type in COLUMN:TYPE form."),
    numeric_range: list[str] = typer.Option([], "--numeric-range"),
    allowed_value: list[str] = typer.Option([], "--allowed-value", help="Allowed value as COLUMN:VALUE."),
    string_length: list[str] = typer.Option([], "--string-length"),
    min_rows: int = typer.Option(0, "--min-rows", min=0),
    max_rows: int = typer.Option(100_000, "--max-rows", min=0, max=100_000),
    database: Path | None = typer.Option(
        None, "--database", help="Optionally save the report in the local dataset catalog."
    ),
):
    """Validate a local file; catalog validate also stores the report in SQLite."""
    from benchforge.catalog import record_quality_report
    from benchforge.quality import QualityRules, validate_file

    allowed: dict[str, list[str]] = {}
    for specification in allowed_value:
        column, separator, value = specification.partition(":")
        if not separator or not column.strip():
            raise typer.BadParameter("Allowed values use COLUMN:VALUE; repeat as needed.")
        allowed.setdefault(column, []).append(value)
    try:
        rules = QualityRules(
            required_columns=tuple(required_column),
            column_types=_parse_quality_types(column_type),
            not_null_columns=tuple(not_null),
            unique_columns=tuple(unique),
            numeric_ranges=_parse_numeric_ranges(numeric_range),
            allowed_values={column: tuple(values) for column, values in allowed.items()},
            string_lengths=_parse_quality_pairs(string_length, "--string-length"),
            min_rows=min_rows,
            max_rows=max_rows,
        )
        report = validate_file(source, input_format=input_format, rules=rules)
        if database is not None:
            record_quality_report(
                database,
                source=source,
                file_format=report["format"],
                report=report,
            )
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] == "FAIL":
        raise typer.Exit(code=1)


@catalog_app.command("list")
def catalog_list(
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="Local SQLite metadata database."
    ),
    limit: int = typer.Option(100, "--limit", min=1, max=1_000),
):
    """List locally registered datasets."""
    from benchforge.catalog import list_datasets

    try:
        result = list_datasets(database, limit=limit)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))


@catalog_app.command("inspect")
def catalog_inspect(
    dataset_id: str = typer.Argument(..., help="Dataset ID or registered dataset key."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="Local SQLite metadata database."
    ),
):
    """Show a dataset's source, schema, quality, and checksum metadata."""
    from benchforge.catalog import inspect_dataset

    try:
        result = inspect_dataset(database, dataset_id)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))


@catalog_app.command("lineage")
def catalog_lineage(
    dataset_id: str = typer.Argument(..., help="Dataset ID or registered dataset key."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="Local SQLite metadata database."
    ),
):
    """Trace related input and output datasets and their pipeline operations."""
    from benchforge.catalog import trace_lineage

    try:
        result = trace_lineage(database, dataset_id)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))


def _native_binary(target: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    name = target + suffix
    candidates = (
        Path("build/native/Release") / name,
        Path("build/native") / name,
        Path("build/native-ucrt64") / name,
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


@app.command()
def doctor():
    """Display the local runtime and dependency environment."""
    import matplotlib
    import numpy
    import scipy

    print(f"[bold cyan]BenchForge[/bold cyan] {__version__}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")
    print(f"NumPy: {numpy.__version__}")
    print(f"SciPy: {scipy.__version__}")
    print(f"Matplotlib: {matplotlib.__version__}")
    print("[green]Python environment check completed.[/green]")


@app.command()
def run(
    binary: Path | None = typer.Option(None, "--binary", help="Path to the compiled C++ workload."),
    runs: int = typer.Option(30, min=2, max=MAX_NATIVE_RUNS),
    warmup: int = typer.Option(3, min=0, max=MAX_NATIVE_WARMUPS),
    iterations: int = typer.Option(100000, min=1, max=MAX_NATIVE_ITERATIONS),
    extra_work: int = typer.Option(
        0, "--extra-work", min=0, max=MAX_NATIVE_EXTRA_WORK
    ),
    output: Path = typer.Option(Path("data/raw/latest.jsonl")),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"),
        "--database",
        help="SQLite database for persistent benchmark history.",
    ),
):
    """Run the native hash-map benchmark and persist its measurements."""
    binary = binary or _native_binary("benchforge_lookup")
    try:
        records = run_benchmark(
            binary=binary,
            runs=runs,
            warmup=warmup,
            iterations=iterations,
            extra_work=extra_work,
            output=output,
        )
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        raise typer.BadParameter(f"Native benchmark failed: {exc}") from exc

    config = {
        "binary_path": str(binary.resolve()),
        "runs": runs,
        "warmup": warmup,
        "iterations": iterations,
        "extra_work": extra_work,
    }

    run_id = records[0].get("run_id")
    run_id = save_samples(
        database=database,
        records=records,
        run_id=run_id,
        variant="single",
        config=config,
    )

    durations = [row["elapsed_ns"] for row in records]
    summary = summarize_latencies(durations)
    print(f"[green]Saved {len(records)} samples to {output}[/green]")
    print(f"[green]Saved history to {database}[/green]")
    print(f"Run ID: {run_id}")
    print(f"Median: {summary['median']:,.0f} ns")
    print(f"P95 (nearest rank): {summary['p95_nearest_rank']:,.0f} ns")
    if summary["p99_reliable"]:
        print(f"P99 (nearest rank): {summary['p99_nearest_rank']:,.0f} ns")
    else:
        print("P99: not reported (fewer than 100 samples)")
    print(f"Minimum: {summary['minimum']:,} ns")
    print(f"Maximum: {summary['maximum']:,} ns")
    print(
        "[yellow]These are local observations, not a "
        "validated regression conclusion.[/yellow]"
    )


@app.command()
def init_db(
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"),
        "--database",
    ),
):
    """Create or migrate the local benchmark history database."""
    initialize_database(database)
    print(f"[green]Database initialized: {database}[/green]")


@app.command()
def workload(
    workload_name: str = typer.Option(
        ..., "--workload", help="Native workload: cpu, memory, file_io, or tcp_loopback."
    ),
    binary: Path | None = typer.Option(
        None, "--binary", help="Path to the compiled native workload executable."
    ),
    runs: int = typer.Option(10, min=2, max=MAX_NATIVE_RUNS),
    warmup: int = typer.Option(2, min=0, max=MAX_NATIVE_WARMUPS),
    iterations: int | None = typer.Option(
        None, min=1, max=MAX_NATIVE_ITERATIONS
    ),
    working_set_kib: int = typer.Option(1024, min=1, help="Memory workload size."),
    file_size_kib: int = typer.Option(1024, min=1, help="File workload size."),
    output: Path = typer.Option(Path("data/raw/workload-latest.jsonl")),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"),
        "--database",
        help="SQLite database for persistent benchmark history.",
    ),
):
    """Run a bounded CPU, memory, or temporary-file I/O workload."""
    from benchforge.workload import run_native_workload

    binary = binary or _native_binary("benchforge_workloads")
    defaults = {"cpu": 100_000, "memory": 10, "file_io": 1, "tcp_loopback": 100}
    if workload_name not in defaults:
        raise typer.BadParameter("Choose cpu, memory, file_io, or tcp_loopback for --workload.")
    selected_iterations = iterations or defaults[workload_name]
    try:
        records = run_native_workload(
            binary,
            workload=workload_name,
            runs=runs,
            warmup=warmup,
            iterations=selected_iterations,
            working_set_kib=working_set_kib,
            file_size_kib=file_size_kib,
            output=output,
        )
        config = {
            "binary_path": str(binary.resolve()),
            "workload": workload_name,
            "runs": runs,
            "warmup": warmup,
            "iterations": selected_iterations,
            "working_set_kib": working_set_kib if workload_name == "memory" else None,
            "file_size_kib": file_size_kib if workload_name == "file_io" else None,
            "bytes_processed_per_sample": records[0]["bytes_processed"],
        }
        run_id = records[0].get("run_id")
        run_id = save_samples(
            database=database,
            records=records,
            run_id=run_id,
            variant="single",
            config=config,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc

    from benchforge.phase4 import summarize_measurements

    summary = summarize_measurements(records)
    latency = summary["latency"]
    print(f"Workload: {workload_name}")
    print(f"Run ID: {run_id}")
    print(f"Saved {len(records)} measurements to {output}")
    print(f"Saved history to {database}")
    print(f"Median: {latency['median']:,.0f} ns")
    print(f"P95 (nearest rank): {latency['p95_nearest_rank']:,.0f} ns")
    if latency["p99_reliable"]:
        print(f"P99 (nearest rank): {latency['p99_nearest_rank']:,.0f} ns")
    else:
        print("P99: not reported (fewer than 100 samples)")
    throughput = summary.get("throughput_bytes_per_second")
    if throughput is not None:
        print(
            "Median throughput: "
            f"{throughput['median']:,.0f} bytes/s"
        )
        print(
            "P95 throughput (nearest rank): "
            f"{throughput['p95_nearest_rank']:,.0f} bytes/s"
        )


@app.command("compare-cpu")
def compare_cpu(
    binary: Path | None = typer.Option(
        None, "--binary", help="Path to the compiled native workloads executable."
    ),
    runs: int = typer.Option(3, min=2, max=20),
    warmup: int = typer.Option(1, min=0, max=5),
    iterations: int = typer.Option(10_000, min=1, max=1_000_000),
    seed: int = typer.Option(2026),
    output: Path = typer.Option(Path("data/raw/cpu-comparison.jsonl")),
):
    """Compare equivalent Python and native CPU loops after checksum validation."""
    from benchforge.phase4 import run_cpu_comparison

    binary = binary or _native_binary("benchforge_workloads")
    try:
        records = run_cpu_comparison(
            binary,
            runs=runs,
            warmup=warmup,
            iterations=iterations,
            seed=seed,
            output=output,
        )
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        raise typer.BadParameter(f"CPU comparison failed: {exc}") from exc

    print(f"Run ID: {records[0]['run_id']}")
    print(f"Correctness checksum: {records[0]['checksum']}")
    print(f"Matched sample pairs: {runs}")
    for variant in ("python", "native"):
        durations = [
            record["elapsed_ns"] for record in records if record["variant"] == variant
        ]
        print(f"{variant.title()} median: {statistics.median(durations):,.0f} ns")
    print(f"Raw JSONL: {output}")
    print("Measurements are local and descriptive, not a general performance claim.")


@app.command("compare-workloads")
def compare_workloads(
    binary: Path | None = typer.Option(
        None, "--binary", help="Path to the compiled native comparisons executable."
    ),
    items: int = typer.Option(1_000, min=1, max=10_000),
    runs: int = typer.Option(3, min=2, max=20),
    warmup: int = typer.Option(1, min=0, max=5),
    seed: int = typer.Option(2026),
    output: Path = typer.Option(Path("data/raw/workload-comparisons.jsonl")),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite benchmark history."
    ),
):
    """Compare equivalent Python/native workloads after validating their checksums."""
    from benchforge.comparisons import run_equivalent_comparisons

    binary = binary or _native_binary("benchforge_comparisons")
    try:
        records = run_equivalent_comparisons(
            binary,
            items=items,
            runs=runs,
            warmup=warmup,
            seed=seed,
            output=output,
        )
        run_ids: dict[str, str] = {}
        for workload_name in sorted({record["workload"] for record in records}):
            workload_records = [
                record for record in records if record["workload"] == workload_name
            ]
            run_ids[workload_name] = workload_records[0]["run_id"]
            for variant in ("python", "native"):
                variant_records = [
                    record for record in workload_records if record["variant"] == variant
                ]
                save_samples(
                    database,
                    variant_records,
                    run_id=run_ids[workload_name],
                    variant=variant,
                    config={
                        "binary_path": str(binary.resolve()),
                        "items": items,
                        "runs": runs,
                        "warmup": warmup,
                        "seed": seed,
                        "workload": workload_name,
                    },
                )
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(f"Workload comparison failed: {exc}") from exc

    print(f"Matched sample pairs per workload: {runs}")
    for workload_name in sorted({record["workload"] for record in records}):
        medians = {}
        for variant in ("python", "native"):
            durations = [
                record["elapsed_ns"]
                for record in records
                if record["workload"] == workload_name and record["variant"] == variant
            ]
            medians[variant] = statistics.median(durations)
        print(
            f"{workload_name} (run {run_ids[workload_name]}): "
            f"Python median {medians['python']:,.0f} ns; "
            f"native median {medians['native']:,.0f} ns"
        )
    print(f"Raw JSONL: {output}")
    print(f"History: {database}")
    print("Measurements are local and descriptive, not a general performance claim.")


@app.command("benchmark-formats")
def benchmark_formats(
    rows: int = typer.Option(1_000, min=1, max=10_000),
    runs: int = typer.Option(3, min=2, max=20),
    warmup: int = typer.Option(1, min=0, max=5),
    include_parquet: bool = typer.Option(
        False, "--include-parquet", help="Include Parquet using the optional datasets extra."
    ),
    output: Path = typer.Option(Path("data/raw/file-format-benchmarks.jsonl")),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite benchmark history."
    ),
):
    """Benchmark bounded warm-cache reads of CSV, JSON, JSONL, and optional Parquet."""
    from benchforge.formats import run_format_benchmarks

    try:
        records = run_format_benchmarks(
            rows=rows,
            runs=runs,
            warmup=warmup,
            include_parquet=include_parquet,
            output=output,
        )
        for file_format in sorted({record["file_format"] for record in records}):
            format_records = [
                record for record in records if record["file_format"] == file_format
            ]
            save_samples(
                database,
                format_records,
                run_id=format_records[0]["run_id"],
                variant="python",
                config={
                    "rows": rows,
                    "runs": runs,
                    "warmup": warmup,
                    "file_format": file_format,
                    "cache_state": "warm_after_generation",
                },
            )
    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(f"File-format benchmark failed: {exc}") from exc

    print(f"Matched reads per format: {runs}")
    for file_format in sorted({record["file_format"] for record in records}):
        samples = [
            record
            for record in records
            if record["file_format"] == file_format
        ]
        latency = summarize_latencies([record["elapsed_ns"] for record in samples])
        throughput = summarize_latencies(
            [
                record["bytes_processed"] * 1_000_000_000 / record["elapsed_ns"]
                for record in samples
            ]
        )
        print(
            f"{file_format} (run {samples[0]['run_id']}): "
            f"median {latency['median']:,.0f} ns; "
            f"median read throughput {throughput['median']:,.0f} bytes/s"
        )
    print(f"Raw JSONL: {output}")
    print(f"History: {database}")
    print("Measurements use generated local files and warm-cache reads.")


@app.command("compare-engines")
def compare_engines(
    rows: int = typer.Option(10_000, min=1, max=100_000),
    groups: int = typer.Option(100, min=1, max=1_000),
    runs: int = typer.Option(3, min=2, max=20),
    output: Path = typer.Option(Path("data/raw/database-comparison.jsonl")),
):
    """Compare equivalent SQLite and optional DuckDB aggregate queries."""
    from benchforge.phase4 import compare_sqlite_duckdb
    from benchforge.runner import write_jsonl_atomic

    try:
        records = compare_sqlite_duckdb(rows=rows, groups=groups, runs=runs)
        write_jsonl_atomic(output, records)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise typer.BadParameter(f"Database-engine comparison failed: {exc}") from exc

    print(f"Rows: {rows:,}; groups: {groups:,}; matched queries: {runs}")
    for engine in ("sqlite", "duckdb"):
        durations = [
            record["elapsed_ns"] for record in records if record["variant"] == engine
        ]
        engine_name = "SQLite" if engine == "sqlite" else "DuckDB"
        print(f"{engine_name} median query time: {statistics.median(durations):,.0f} ns")
    print(f"Raw JSONL: {output}")
    print("Database setup is excluded; results are local and descriptive.")


@app.command("export-csv")
def export_csv(
    output: Path = typer.Option(..., "--output", help="CSV file to write."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite history database."
    ),
    run_id: str | None = typer.Option(
        None, "--run-id", help="Export one run; omit to export all stored samples."
    ),
):
    """Export stored benchmark observations to stable UTF-8 CSV."""
    from benchforge.exports import export_samples_csv

    try:
        count = export_samples_csv(database, output, run_id=run_id)
    except (OSError, sqlite3.Error, ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(f"Exported {count} observations to {output}")


@app.command("export-history")
def export_history(
    output: Path = typer.Option(..., "--output", help="New CSV path for dbt input."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite benchmark history database."
    ),
    max_rows: int = typer.Option(100_000, "--max-rows", min=1, max=100_000),
):
    """Export a bounded, read-only benchmark-history snapshot for dbt."""
    from benchforge.history_export import export_benchmark_history_csv

    try:
        count = export_benchmark_history_csv(database, output, max_rows=max_rows)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(f"Exported {count} benchmark observations to {output}")


@app.command("compare-history")
def compare_history(
    run_id: str = typer.Option(..., "--run-id", help="Current run to inspect."),
    reference_run_id: str = typer.Option(
        ..., "--reference-run-id", help="Explicit historical reference run."
    ),
    variant: str = typer.Option(
        "candidate", help="Variant to compare in both runs."
    ),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite history database."
    ),
    threshold: float = typer.Option(0.03, min=0.0, max=1.0),
    confidence: float = typer.Option(0.95, min=0.5, max=0.999),
    n_resamples: int = typer.Option(5000, min=100),
    seed: int = typer.Option(2026),
    alpha: float = typer.Option(0.05, min=0.001, max=0.25),
):
    """Compare a stored run with an explicit compatible historical reference."""
    from benchforge.history import compare_historical_runs

    if not database.is_file():
        raise typer.BadParameter(f"Database does not exist: {database}")
    try:
        result = compare_historical_runs(
            database,
            run_id=run_id,
            reference_run_id=reference_run_id,
            variant=variant,
            threshold=threshold,
            confidence=confidence,
            n_resamples=n_resamples,
            seed=seed,
            alpha=alpha,
        )
    except (ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc

    print(f"Current run: {result['run_id']} ({result['variant']})")
    print(f"Reference run: {result['reference_run_id']} ({result['variant']})")
    print(f"Observations: {result['current_count']} current / {result['reference_count']} reference")
    print(f"Current/reference median ratio: {result['ratio']:.5f}")
    print(
        f"{confidence:.1%} independent bootstrap CI: "
        f"[{result['ci_low']:.5f}, {result['ci_high']:.5f}]"
    )
    print(f"Mann-Whitney U p-value: {result['p_value']:.6g}")
    print(f"Decision: {result['decision']}")
    print("Interpretation: the ratio compares current to reference; >1 means current is slower.")


@app.command()
def dashboard(
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"),
        "--database",
        help="SQLite history database.",
        envvar="BENCHFORGE_DATABASE",
    ),
    data_dir: Path = typer.Option(
        Path("data/public_datasets/processed"),
        "--data-dir",
        help="Directory containing processed public-data JSON summaries.",
        envvar="BENCHFORGE_DATA_DIR",
    ),
    documents_dir: Path | None = typer.Option(
        None,
        "--documents-dir",
        help="Local PDF storage directory (defaults to the database directory/documents).",
        envvar="BENCHFORGE_DOCUMENTS_DIR",
    ),
    max_pdf_bytes: int = typer.Option(
        MAX_PDF_BYTES,
        "--max-pdf-bytes",
        min=1,
        max=MAX_CONFIGURABLE_PDF_BYTES,
        help="PDF upload limit in bytes (default 10 MiB; maximum 50 MiB).",
        envvar="BENCHFORGE_MAX_PDF_BYTES",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        help="Loopback host unless --allow-remote is explicitly set.",
        envvar="BENCHFORGE_HOST",
    ),
    port: int = typer.Option(8765, min=1, max=65535, envvar="BENCHFORGE_PORT"),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="Allow a non-loopback bind; use only behind trusted network controls.",
    ),
):
    """Start the local analytics dashboard."""
    from benchforge.dashboard import serve

    try:
        serve(
            database=database,
            data_dir=data_dir,
            host=host,
            port=port,
            allow_remote=allow_remote,
            documents_dir=documents_dir,
            max_pdf_bytes=max_pdf_bytes,
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Could not start dashboard: {exc}") from exc


@app.command()
def web(
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"),
        "--database",
        help="SQLite benchmark history database.",
        envvar="BENCHFORGE_DATABASE",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Loopback bind address; remote hosts are not accepted.",
    ),
    port: int = typer.Option(8766, min=1, max=65535),
    native_binary: Path | None = typer.Option(
        None,
        "--native-binary",
        help="Path to the compiled native CPU/memory/storage workload.",
    ),
    comparison_binary: Path | None = typer.Option(
        None,
        "--comparison-binary",
        help="Path to the compiled native equivalent-workload comparison binary.",
    ),
    frontend_dist: Path = typer.Option(
        Path("frontend/dist"),
        "--frontend-dist",
        help="Built React frontend directory; use Vite separately during development.",
    ),
):
    """Start the local FastAPI application."""
    if host.lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise typer.BadParameter("The web application may only bind to a loopback host.")
    try:
        from uvicorn import run as run_uvicorn
        from benchforge.webapp import create_app
    except ModuleNotFoundError as exc:
        if exc.name in {"fastapi", "uvicorn"}:
            raise typer.BadParameter(
                "Install the optional web dependencies with: "
                "python -m pip install 'benchforge[web]'"
            ) from exc
        raise

    app_instance = create_app(
        database,
        native_binary=native_binary or _native_binary("benchforge_workloads"),
        comparison_binary=comparison_binary or _native_binary("benchforge_comparisons"),
        frontend_dist=frontend_dist,
    )
    run_uvicorn(
        app_instance,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


@app.command()
def compare(
    run_id: str = typer.Option(..., "--run-id", help="A/B experiment run ID."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"),
        "--database",
        help="SQLite benchmark history database.",
    ),
    threshold: float = typer.Option(
        0.03, min=0.0, max=1.0,
        help="Practical regression threshold (0.03 = 3%).",
    ),
    confidence: float = typer.Option(
        0.95, min=0.5, max=0.999,
        help="Bootstrap confidence level.",
    ),
    n_resamples: int = typer.Option(
        5000, min=100, help="Paired bootstrap resamples."
    ),
    seed: int = typer.Option(2026, help="Bootstrap random seed."),
    alpha: float = typer.Option(
        0.05, min=0.001, max=0.25,
        help="Significance level for the paired test.",
    ),
):
    """Compare paired baseline/candidate samples from a stored A/B run."""
    from benchforge.storage import get_samples

    if not database.is_file():
        raise typer.BadParameter(f"Database does not exist: {database}")

    baseline_rows = get_samples(database, run_id=run_id, variant="baseline")
    candidate_rows = get_samples(database, run_id=run_id, variant="candidate")

    if not baseline_rows or not candidate_rows:
        raise typer.BadParameter("Run ID must contain both baseline and candidate samples.")

    def index_rows(rows, variant):
        indexed = {}
        for row in rows:
            pair_index = row.get("pair_index")
            elapsed_ns = row.get("elapsed_ns")
            if isinstance(pair_index, bool) or not isinstance(pair_index, int):
                raise typer.BadParameter(f"{variant} samples are missing a valid pair_index.")
            if (
                isinstance(elapsed_ns, bool)
                or not isinstance(elapsed_ns, int)
                or elapsed_ns <= 0
            ):
                raise typer.BadParameter(
                    f"{variant} samples have an invalid elapsed_ns; "
                    "expected a positive integer."
                )
            if pair_index in indexed:
                raise typer.BadParameter(f"Duplicate {variant} pair_index: {pair_index}")
            indexed[pair_index] = elapsed_ns
        return indexed

    baseline_by_pair = index_rows(baseline_rows, "baseline")
    candidate_by_pair = index_rows(candidate_rows, "candidate")

    if set(baseline_by_pair) != set(candidate_by_pair):
        raise typer.BadParameter("Baseline and candidate pair_index sets do not match.")

    pair_indices = sorted(baseline_by_pair)
    if len(pair_indices) < MINIMUM_PAIRED_OBSERVATIONS:
        raise typer.BadParameter(
            f"At least {MINIMUM_PAIRED_OBSERVATIONS} paired observations are required "
            "for inferential comparison."
        )
    baseline = [baseline_by_pair[i] for i in pair_indices]
    candidate = [candidate_by_pair[i] for i in pair_indices]

    ratio = paired_median_ratio(baseline, candidate)
    ci_low, ci_high = bootstrap_paired_median_ratio_ci(
        baseline,
        candidate,
        confidence=confidence,
        n_resamples=n_resamples,
        seed=seed,
    )
    p_value = wilcoxon_paired_pvalue(baseline, candidate)
    decision = classify_regression(
        ci_low,
        ci_high,
        threshold=threshold,
        p_value=p_value,
        alpha=alpha,
    )

    print(f"Run ID: {run_id}")
    print(f"Matched pairs: {len(pair_indices)}")
    print(f"Effect: median paired candidate/baseline ratio = {ratio:.5f}")
    print(f"{confidence:.1%} paired bootstrap CI: [{ci_low:.5f}, {ci_high:.5f}]")
    print(f"Wilcoxon signed-rank p-value: {p_value:.6g}")
    print(f"Practical regression threshold: {threshold:.1%}")
    print(f"Significance level alpha: {alpha:.3f}")
    print(f"Decision: {decision}")
    print(
        "Interpretation: ratios above 1 indicate slower candidate latency. "
        "The verdict depends on this run, its pairing, and the selected policy."
    )


@app.command()
def report(
    run_id: str = typer.Option(..., "--run-id", help="Experiment run ID to report."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite experiment database."
    ),
    output: Path = typer.Option(
        Path("reports/generated/report.md"), "--output", help="Markdown report path."
    ),
    threshold: float = typer.Option(0.03, min=0.0, max=1.0),
    confidence: float = typer.Option(0.95, min=0.5, max=0.999),
    n_resamples: int = typer.Option(5000, min=100),
    seed: int = typer.Option(2026),
    alpha: float = typer.Option(0.05, min=0.001, max=0.25),
    force: bool = typer.Option(False, "--force", help="Allow replacing an existing report."),
):
    """Generate a reproducible Markdown report for a stored A/B experiment."""
    from datetime import datetime

    from benchforge.storage import get_samples

    if not database.is_file():
        raise typer.BadParameter(f"Database does not exist: {database}")

    if output.exists() and not force:
        raise typer.BadParameter(
            f"Output already exists: {output}. Use --force to replace it."
        )

    baseline_rows = get_samples(database, run_id=run_id, variant="baseline")
    candidate_rows = get_samples(database, run_id=run_id, variant="candidate")

    if not baseline_rows or not candidate_rows:
        raise typer.BadParameter(
            f"Run '{run_id}' must contain both baseline and candidate samples."
        )

    def validate_rows(rows, variant):
        pairs = {}
        durations = {}
        for row in rows:
            pair_index = row.get("pair_index")
            elapsed_ns = row.get("elapsed_ns")
            if isinstance(pair_index, bool) or not isinstance(pair_index, int):
                raise typer.BadParameter(
                    f"{variant} contains an invalid pair_index."
                )
            if isinstance(elapsed_ns, bool) or not isinstance(elapsed_ns, int) or elapsed_ns <= 0:
                raise typer.BadParameter(
                    f"{variant} contains an invalid elapsed_ns value."
                )
            if pair_index in pairs:
                raise typer.BadParameter(
                    f"{variant} contains duplicate pair_index {pair_index}."
                )
            pairs[pair_index] = elapsed_ns
            durations[pair_index] = elapsed_ns
        return durations

    baseline_by_pair = validate_rows(baseline_rows, "baseline")
    candidate_by_pair = validate_rows(candidate_rows, "candidate")

    if set(baseline_by_pair) != set(candidate_by_pair):
        raise typer.BadParameter(
            "Baseline and candidate samples have mismatched pair_index values."
        )

    pair_indices = sorted(baseline_by_pair)
    if len(pair_indices) < MINIMUM_PAIRED_OBSERVATIONS:
        raise typer.BadParameter(
            f"At least {MINIMUM_PAIRED_OBSERVATIONS} paired observations are required "
            "to generate an inferential report."
        )
    baseline = [baseline_by_pair[index] for index in pair_indices]
    candidate = [candidate_by_pair[index] for index in pair_indices]

    ratio = paired_median_ratio(baseline, candidate)
    ci_low, ci_high = bootstrap_paired_median_ratio_ci(
        baseline, candidate, confidence=confidence,
        n_resamples=n_resamples, seed=seed,
    )
    p_value = wilcoxon_paired_pvalue(baseline, candidate)
    decision = classify_regression(
        ci_low, ci_high, threshold=threshold, p_value=p_value, alpha=alpha
    )

    baseline_median = statistics.median(baseline)
    candidate_median = statistics.median(candidate)
    generated = datetime.now(UTC).isoformat()
    config_json = baseline_rows[0].get("config_json")
    try:
        config = json.loads(config_json) if config_json else {}
    except (TypeError, json.JSONDecodeError):
        config = {"warning": "Stored configuration was not valid JSON."}

    def unique_values(rows, key):
        values = sorted({str(row.get(key)) for row in rows if row.get(key) is not None})
        return ", ".join(values) if values else "Not recorded"

    report_text = f"""# BenchForge A/B Experiment Report

- Generated (UTC): {generated}
- Run ID: `{run_id}`
- Database: `{database}`
- Matched pairs: {len(pair_indices)}
- Decision: **{decision}**

## Experimental configuration

- Practical regression threshold: {threshold:.2%}
- Confidence level: {confidence:.1%}
- Bootstrap resamples: {n_resamples}
- Random seed for bootstrap: {seed}
- Significance level (alpha): {alpha}
- Stored configuration:

```json
{json.dumps(config, indent=2, sort_keys=True)}
```

## Environment and provenance

- Workload: {unique_values(baseline_rows + candidate_rows, "workload")}
- Operating system: {unique_values(baseline_rows + candidate_rows, "os")}
- Python version: {unique_values(baseline_rows + candidate_rows, "python_version")}
- Baseline binary: {unique_values(baseline_rows, "binary")}
- Candidate binary: {unique_values(candidate_rows, "binary")}
- Schema version: {unique_values(baseline_rows + candidate_rows, "schema_version")}

## Results

| Metric | Baseline | Candidate |
|---|---:|---:|
| Sample count | {len(baseline)} | {len(candidate)} |
| Median latency | {baseline_median:.0f} ns | {candidate_median:.0f} ns |

- Paired median candidate/baseline ratio: **{ratio:.5f}**
- Relative change: **{(ratio - 1) * 100:+.2f}%**
- Paired bootstrap confidence interval ({confidence:.1%}): [{ci_low:.5f}, {ci_high:.5f}]
- Wilcoxon signed-rank p-value: {p_value:.8g}
- Practical regression threshold: {threshold:.2%}
- Classification: **{decision}**

## Interpretation and limitations

A ratio below 1 indicates lower candidate latency; a ratio above 1 indicates higher candidate latency. The classification uses the configured practical threshold, confidence interval, p-value, and significance level.

This result applies to the stored run, workload, binaries, and execution environment. It is not a guarantee of performance on other machines or workloads. Statistical tests do not eliminate measurement noise, environmental bias, or limitations in experimental design.

## Reproducibility

Re-run the analysis with the same stored experiment and parameters:

```powershell
benchforge report --run-id "{run_id}" --database "{database}" --output "{output}" --threshold {threshold} --confidence {confidence} --n-resamples {n_resamples} --seed {seed} --alpha {alpha}
```
"""

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report_text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise typer.BadParameter(f"Could not write report: {exc}") from exc

    print(f"Report written: {output}")
    print(f"Run ID: {run_id}")
    print(f"Matched pairs: {len(pair_indices)}")
    print(f"Decision: {decision}")


@app.command()
def ab(
    baseline_binary: Path | None = typer.Option(
        None, help="Path to the baseline native executable."
    ),
    candidate_binary: Path | None = typer.Option(
        None, help="Path to the candidate native executable."
    ),
    runs: int = typer.Option(
        30, min=2, max=MAX_NATIVE_RUNS, help="Number of measured pairs."
    ),
    warmup: int = typer.Option(
        3, min=0, max=MAX_NATIVE_WARMUPS, help="Warmup runs per variant."
    ),
    iterations: int = typer.Option(
        100000,
        min=1,
        max=MAX_NATIVE_ITERATIONS,
        help="Workload iterations.",
    ),
    baseline_extra_work: int = typer.Option(
        0, min=0, max=MAX_NATIVE_EXTRA_WORK
    ),
    candidate_extra_work: int = typer.Option(
        0, min=0, max=MAX_NATIVE_EXTRA_WORK
    ),
    baseline_delay_ns: int = typer.Option(
        0,
        min=0,
        max=5_000_000_000,
        help="Artificial delay injected into each baseline measurement (ns).",
    ),
    candidate_delay_ns: int = typer.Option(
        0,
        min=0,
        max=5_000_000_000,
        help="Artificial delay injected into each candidate measurement (ns).",
    ),
    seed: int = typer.Option(2026, help="Seed for randomized execution order."),
    output: Path = typer.Option(Path("data/raw/latest-ab.jsonl")),
    database: Path = typer.Option(Path("data/benchforge.sqlite")),
):
    """Run interleaved A/B benchmarks and persist raw samples."""
    baseline_binary = baseline_binary or _native_binary("benchforge_lookup")
    candidate_binary = candidate_binary or _native_binary("benchforge_lookup_optimized")
    try:
        records = run_ab_benchmark(
            baseline_binary=baseline_binary,
            candidate_binary=candidate_binary,
            runs=runs,
            warmup=warmup,
            iterations=iterations,
            baseline_extra_work=baseline_extra_work,
            candidate_extra_work=candidate_extra_work,
            baseline_delay_ns=baseline_delay_ns,
            candidate_delay_ns=candidate_delay_ns,
            seed=seed,
        )
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        raise typer.BadParameter(f"A/B benchmark failed: {exc}") from exc

    if not records:
        raise typer.BadParameter("A/B runner returned no records.")

    run_ids = {record.get("run_id") for record in records}
    if len(run_ids) != 1 or None in run_ids:
        raise typer.BadParameter("A/B records must share one valid run_id.")

    run_id = next(iter(run_ids))
    baseline = [r for r in records if r.get("variant") == "baseline"]
    candidate = [r for r in records if r.get("variant") == "candidate"]

    if len(baseline) != runs or len(candidate) != runs:
        raise typer.BadParameter("Unexpected number of baseline/candidate samples.")

    config = {
        "baseline_binary": str(baseline_binary.resolve()),
        "candidate_binary": str(candidate_binary.resolve()),
        "runs": runs,
        "warmup": warmup,
        "iterations": iterations,
        "baseline_extra_work": baseline_extra_work,
        "candidate_extra_work": candidate_extra_work,
        "baseline_delay_ns": baseline_delay_ns,
        "candidate_delay_ns": candidate_delay_ns,
        "seed": seed,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    ordered_records = sorted(records, key=lambda r: r["execution_order"])
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for record in ordered_records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    from benchforge.storage import save_experiment_samples

    save_experiment_samples(
        database,
        run_id=run_id,
        baseline=baseline,
        candidate=candidate,
        config=config,
    )

    baseline_median = statistics.median(r["elapsed_ns"] for r in baseline)
    candidate_median = statistics.median(r["elapsed_ns"] for r in candidate)
    ratio = candidate_median / baseline_median

    print(f"Run ID: {run_id}")
    print(f"Baseline samples: {len(baseline)}")
    print(f"Candidate samples: {len(candidate)}")
    print(f"Baseline median: {baseline_median:.0f} ns")
    print(f"Candidate median: {candidate_median:.0f} ns")
    print(f"Candidate/baseline median ratio: {ratio:.5f} ({(ratio - 1) * 100:+.2f}%)")
    print(f"Raw JSONL: {output}")
    print(f"SQLite database: {database}")
    print("Descriptive result only: no statistical regression verdict has been calculated.")


if __name__ == "__main__":
    app()
