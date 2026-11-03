"""Bounded local tabular pipelines with SQLite metrics, quality, and lineage."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from benchforge.catalog import initialize_catalog_schema, record_ingestion

MAX_INPUT_BYTES = 20_000_000
MAX_INPUT_ROWS = 100_000
MAX_COLUMNS = 200
MAX_CELL_CHARS = 65_536
MAX_DISTINCT_VALUES = 20_000
MAX_FILES_PER_PIPELINE = 50
MAX_GROUPS = 20_000
MAX_TABLE_NAME_LENGTH = 63
_RESERVED_TABLES = {
    "benchmark_samples",
    "pipeline_lineage",
    "pipeline_quality",
    "pipeline_runs",
}
InputFormat = Literal["csv", "json", "jsonl", "parquet"]


def normalize_column_name(value: str, index: int) -> str:
    """Convert a source header to a stable SQLite-safe identifier."""
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")
    if not normalized:
        normalized = f"column_{index + 1}"
    if normalized[0].isdigit():
        normalized = f"column_{normalized}"
    return normalized[:MAX_TABLE_NAME_LENGTH]


def _identifier(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value)
    ):
        raise ValueError(f"{label} must start with a letter and contain only lowercase letters, digits, or underscores.")
    return value


def _quote_identifier(value: str) -> str:
    return '"' + _identifier(value, "Identifier") + '"'


def _data_table(value: str, label: str) -> str:
    value = _identifier(value, label)
    if value in _RESERVED_TABLES or value.startswith("sqlite_"):
        raise ValueError(f"{label} cannot use reserved table name {value!r}.")
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _connect(database: Path) -> sqlite3.Connection:
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


@contextmanager
def _connection(database: Path) -> Iterator[sqlite3.Connection]:
    connection = _connect(database)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_pipeline_schema(database: Path) -> None:
    """Create pipeline metadata tables alongside existing benchmark history."""
    with _connection(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                pipeline_name TEXT NOT NULL,
                source_name TEXT NOT NULL,
                destination TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at_utc TEXT NOT NULL,
                ended_at_utc TEXT,
                duration_ns INTEGER,
                input_rows INTEGER NOT NULL DEFAULT 0,
                accepted_rows INTEGER NOT NULL DEFAULT 0,
                rejected_rows INTEGER NOT NULL DEFAULT 0,
                output_rows INTEGER NOT NULL DEFAULT 0,
                rows_per_second REAL,
                failure_count INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                config_json TEXT NOT NULL,
                error_detail TEXT
            );
            CREATE TABLE IF NOT EXISTS pipeline_quality (
                run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
                check_name TEXT NOT NULL,
                passed INTEGER NOT NULL,
                details_json TEXT NOT NULL,
                PRIMARY KEY (run_id, check_name)
            );
            CREATE TABLE IF NOT EXISTS pipeline_lineage (
                run_id TEXT PRIMARY KEY REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
                source_name TEXT NOT NULL,
                pipeline_name TEXT NOT NULL,
                destination TEXT NOT NULL,
                transformations_json TEXT NOT NULL,
                recorded_at_utc TEXT NOT NULL
            );
            """
        )
    initialize_catalog_schema(database)


def _start_run(
    database: Path,
    *,
    pipeline_name: str,
    source_name: str,
    destination: str,
    config: dict[str, Any],
) -> tuple[str, int]:
    run_id = str(uuid.uuid4())
    started_at = _utc_now()
    started_clock = time.perf_counter_ns()
    with _connection(database) as connection:
        connection.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, pipeline_name, source_name, destination, status,
                started_at_utc, config_json
            ) VALUES (?, ?, ?, ?, 'running', ?, ?)
            """,
            (
                run_id,
                pipeline_name,
                source_name[:512],
                destination,
                started_at,
                json.dumps(config, sort_keys=True),
            ),
        )
    return run_id, started_clock


def _finish_run(
    database: Path,
    *,
    run_id: str,
    started_clock: int,
    pipeline_name: str,
    source_name: str,
    destination: str,
    input_rows: int,
    accepted_rows: int,
    rejected_rows: int,
    output_rows: int,
    quality: list[dict[str, Any]],
    transformations: list[str],
) -> dict[str, Any]:
    duration_ns = max(1, time.perf_counter_ns() - started_clock)
    now = _utc_now()
    rate = accepted_rows * 1_000_000_000 / duration_ns
    with _connection(database) as connection:
        connection.executemany(
            """
            INSERT INTO pipeline_quality (run_id, check_name, passed, details_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    item["name"],
                    int(item["passed"]),
                    json.dumps(item["details"], sort_keys=True),
                )
                for item in quality
            ],
        )
        connection.execute(
            """
            INSERT INTO pipeline_lineage (
                run_id, source_name, pipeline_name, destination,
                transformations_json, recorded_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_name[:512],
                pipeline_name,
                destination,
                json.dumps(transformations),
                now,
            ),
        )
        connection.execute(
            """
            UPDATE pipeline_runs
            SET status = 'succeeded', ended_at_utc = ?, duration_ns = ?,
                input_rows = ?, accepted_rows = ?, rejected_rows = ?,
                output_rows = ?, rows_per_second = ?, failure_count = 0
            WHERE run_id = ?
            """,
            (
                now,
                duration_ns,
                input_rows,
                accepted_rows,
                rejected_rows,
                output_rows,
                rate,
                run_id,
            ),
        )
    return {
        "run_id": run_id,
        "status": "succeeded",
        "pipeline_name": pipeline_name,
        "source_name": source_name,
        "destination": destination,
        "input_rows": input_rows,
        "accepted_rows": accepted_rows,
        "rejected_rows": rejected_rows,
        "output_rows": output_rows,
        "duration_ns": duration_ns,
        "rows_per_second": rate,
        "quality": quality,
    }


def _fail_run(database: Path, run_id: str, started_clock: int, error: Exception) -> None:
    detail = f"{type(error).__name__}: {error}"[:2_000]
    with _connection(database) as connection:
        connection.execute(
            """
            UPDATE pipeline_runs
            SET status = 'failed', ended_at_utc = ?, duration_ns = ?,
                failure_count = 1, error_detail = ?
            WHERE run_id = ?
            """,
            (
                _utc_now(),
                max(1, time.perf_counter_ns() - started_clock),
                detail,
                run_id,
            ),
        )


def _read_headers(path: Path) -> tuple[list[str], list[str]]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"CSV file exceeds the {MAX_INPUT_BYTES:,}-byte limit: {path.name}")
    stream = path.open("r", encoding="utf-8-sig", newline="")
    reader = csv.reader(stream, strict=True)
    try:
        raw_headers = next(reader, None)
    except (csv.Error, UnicodeError) as exc:
        stream.close()
        raise ValueError(f"Could not read CSV header in {path.name}: {exc}") from exc
    if not raw_headers or not any(header.strip() for header in raw_headers):
        stream.close()
        raise ValueError(f"CSV file has no header row: {path.name}")
    if len(raw_headers) > MAX_COLUMNS:
        stream.close()
        raise ValueError(f"CSV exceeds the {MAX_COLUMNS}-column limit: {path.name}")
    headers = [normalize_column_name(header, index) for index, header in enumerate(raw_headers)]
    if len(set(headers)) != len(headers):
        stream.close()
        raise ValueError(f"CSV headers are duplicated after normalization: {path.name}")
    stream.close()
    return raw_headers, headers


def _record_source(
    path: Path,
    input_format: InputFormat,
    columns: Iterable[str] | None = None,
) -> tuple[list[str], Iterator[list[Any] | None]]:
    requested = (
        None
        if columns is None
        else [normalize_column_name(name, index) for index, name in enumerate(columns)]
    )
    if requested is not None and len(set(requested)) != len(requested):
        raise ValueError("Selected columns are duplicated after normalization.")

    def select_headers(raw_headers: list[str]) -> tuple[list[str], list[int]]:
        normalized = [
            normalize_column_name(header, index)
            for index, header in enumerate(raw_headers)
        ]
        if requested is None:
            return raw_headers, list(range(len(raw_headers)))
        missing = [name for name in requested if name not in normalized]
        if missing:
            raise ValueError(
                f"Selected columns are missing from {path.name}: " + ", ".join(missing)
            )
        indices = [normalized.index(name) for name in requested]
        return [raw_headers[index] for index in indices], indices

    def project_rows(
        rows: Iterator[list[Any] | None],
        indices: list[int],
        source_width: int,
    ) -> Iterator[list[Any] | None]:
        for row in rows:
            if row is None or len(row) != source_width:
                yield None
            else:
                yield [row[index] for index in indices]
    if input_format == "csv":
        all_headers, _ = _read_headers(path)
        raw_headers, indices = select_headers(all_headers)

        def csv_rows() -> Iterator[list[Any] | None]:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream, strict=True)
                next(reader, None)
                yield from reader

        return raw_headers, project_rows(csv_rows(), indices, len(all_headers))

    if input_format == "json":
        try:
            with path.open("r", encoding="utf-8-sig") as stream:
                records = json.load(stream)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError(f"Could not parse JSON source {path.name}: {exc}") from exc
        if not isinstance(records, list) or not records or not isinstance(records[0], dict):
            raise ValueError(f"JSON source must contain a non-empty array of objects: {path.name}")
        all_headers = list(records[0])
        if not all(isinstance(header, str) for header in all_headers):
            raise ValueError(f"JSON object keys must be strings: {path.name}")
        raw_headers, indices = select_headers(all_headers)

        def json_rows() -> Iterator[list[Any] | None]:
            expected = set(all_headers)
            for record in records:
                if not isinstance(record, dict) or set(record) != expected:
                    yield None
                else:
                    yield [record[all_headers[index]] for index in indices]

        return raw_headers, json_rows()

    if input_format == "jsonl":
        first_record: dict[str, Any] | None = None
        try:
            with path.open("r", encoding="utf-8-sig") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise TypeError(
                            f"JSONL records must be objects at line {line_number} in {path.name}."
                        )
                    first_record = value
                    break
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError(f"Could not parse JSONL source {path.name}: {exc}") from exc
        if first_record is None:
            raise ValueError(f"JSONL source contains no records: {path.name}")
        all_headers = list(first_record)
        if not all(isinstance(header, str) for header in all_headers):
            raise ValueError(f"JSONL object keys must be strings: {path.name}")
        raw_headers, indices = select_headers(all_headers)

        def jsonl_rows() -> Iterator[list[Any] | None]:
            expected = set(all_headers)
            try:
                with path.open("r", encoding="utf-8-sig") as stream:
                    for line_number, line in enumerate(stream, start=1):
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ValueError(
                                f"Invalid JSONL record at line {line_number} in {path.name}: {exc}"
                            ) from exc
                        if not isinstance(record, dict) or set(record) != expected:
                            yield None
                        else:
                            yield [record[all_headers[index]] for index in indices]
            except UnicodeError as exc:
                raise ValueError(f"Could not decode JSONL source {path.name}: {exc}") from exc

        return raw_headers, jsonl_rows()

    try:
        import pyarrow as pa
        from pyarrow import parquet
    except ImportError as exc:
        raise ValueError(
            "Parquet ingestion requires the optional 'datasets' dependencies; "
            "install BenchForge with the datasets extra."
        ) from exc
    try:
        parquet_file = parquet.ParquetFile(path)
    except (OSError, pa.ArrowException) as exc:
        raise ValueError(f"Could not open Parquet source {path.name}: {exc}") from exc
    all_headers = parquet_file.schema_arrow.names
    raw_headers, _ = select_headers(all_headers)

    def parquet_rows() -> Iterator[list[Any] | None]:
        try:
            for batch in parquet_file.iter_batches(
                batch_size=1_000,
                columns=raw_headers,
            ):
                arrays = [batch.column(index) for index in range(len(raw_headers))]
                for row_index in range(batch.num_rows):
                    yield [array[row_index].as_py() for array in arrays]
        except pa.ArrowException as exc:
            raise ValueError(f"Could not read Parquet source {path.name}: {exc}") from exc

    return raw_headers, parquet_rows()


def _cell_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(value)


class _ColumnProfile:
    def __init__(self) -> None:
        self.missing = 0
        self.non_missing = 0
        self.integers = 0
        self.numbers = 0
        self.numeric_mean: float | None = None
        self.numeric_m2 = 0.0
        self.numeric_stats_valid = True
        self.numeric_min: float | None = None
        self.numeric_max: float | None = None
        self.distinct: set[str] = set()
        self.distinct_truncated = False

    def add(self, value: str | None) -> None:
        if value is None or value.strip() == "":
            self.missing += 1
            return
        self.non_missing += 1
        if len(self.distinct) < MAX_DISTINCT_VALUES:
            self.distinct.add(value)
        else:
            self.distinct_truncated = True
        try:
            numeric = float(value)
            if math.isfinite(numeric):
                self.numbers += 1
                self.numeric_min = numeric if self.numeric_min is None else min(self.numeric_min, numeric)
                self.numeric_max = numeric if self.numeric_max is None else max(self.numeric_max, numeric)
                if self.numeric_stats_valid:
                    if self.numeric_mean is None:
                        self.numeric_mean = numeric
                    else:
                        delta = numeric - self.numeric_mean
                        mean = self.numeric_mean + delta / self.numbers
                        m2 = self.numeric_m2 + delta * (numeric - mean)
                        if math.isfinite(mean) and math.isfinite(m2):
                            self.numeric_mean = mean
                            self.numeric_m2 = m2
                        else:
                            self.numeric_stats_valid = False
                if numeric.is_integer():
                    self.integers += 1
        except ValueError:
            pass

    def as_dict(self, name: str, total_rows: int) -> dict[str, Any]:
        if self.non_missing == 0:
            inferred_type = "unknown"
        elif self.integers == self.non_missing:
            inferred_type = "integer"
        elif self.numbers == self.non_missing:
            inferred_type = "number"
        else:
            inferred_type = "string"
        mean = self.numeric_mean if self.numeric_stats_valid else None
        variance = (
            max(0.0, self.numeric_m2 / self.numbers)
            if self.numeric_stats_valid and self.numbers
            else None
        )
        return {
            "column": name,
            "inferred_type": inferred_type,
            "missing_count": self.missing,
            "missing_percent": (100 * self.missing / total_rows) if total_rows else 0.0,
            "unique_count": len(self.distinct),
            "unique_count_is_lower_bound": self.distinct_truncated,
            "minimum": self.numeric_min,
            "maximum": self.numeric_max,
            "mean": mean,
            "standard_deviation": math.sqrt(variance) if variance is not None else None,
        }


def _load_tabular_sources(
    database: Path,
    sources: list[Path],
    destination: str,
    input_format: InputFormat = "csv",
    required_columns: set[str] | None = None,
    duplicate_policy: Literal["keep", "drop", "error"] = "keep",
    numeric_ranges: dict[str, tuple[float | None, float | None]] | None = None,
    columns: Iterable[str] | None = None,
    run_id: str | None = None,
) -> tuple[int, int, int, list[dict[str, Any]], list[dict[str, Any]]]:
    columns = tuple(columns) if columns is not None else None
    target = _quote_identifier(destination)
    source_headers: list[list[str]] = []
    normalized_headers: list[list[str]] = []
    source_rows: list[Iterator[list[Any] | None]] = []
    for path in sources:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(
                f"{input_format.upper()} file exceeds the {MAX_INPUT_BYTES:,}-byte limit: "
                f"{path.name}"
            )
        raw, rows = _record_source(path, input_format, columns)
        if not raw or len(raw) > MAX_COLUMNS:
            raise ValueError(
                f"{input_format.upper()} source must have between 1 and "
                f"{MAX_COLUMNS} columns: {path.name}"
            )
        normalized = [normalize_column_name(header, index) for index, header in enumerate(raw)]
        if len(set(normalized)) != len(normalized):
            raise ValueError(
                f"{input_format.upper()} headers are duplicated after normalization: "
                f"{path.name}"
            )
        source_headers.append(raw)
        normalized_headers.append(normalized)
        source_rows.append(rows)
    if sum(path.stat().st_size for path in sources) > MAX_INPUT_BYTES:
        raise ValueError(
            f"Combined {input_format.upper()} inputs exceed the {MAX_INPUT_BYTES:,}-byte limit."
        )
    headers = normalized_headers[0]
    if any(set(item) != set(headers) for item in normalized_headers[1:]):
        raise ValueError(f"{input_format.upper()} inputs do not have matching normalized schemas.")
    missing_required = sorted((required_columns or set()) - set(headers))
    if missing_required:
        raise ValueError(
            "CSV schema is missing required columns: " + ", ".join(missing_required)
        )
    ranges = numeric_ranges or {}
    missing_range_columns = sorted(set(ranges) - set(headers))
    if missing_range_columns:
        raise ValueError(
            "CSV schema is missing numeric range columns: "
            + ", ".join(missing_range_columns)
        )

    stage_name = _identifier(f"stage_{uuid.uuid4().hex}", "Temporary table")
    stage = _quote_identifier(stage_name)
    input_rows = accepted_rows = rejected_rows = 0
    profiles = {name: _ColumnProfile() for name in headers}
    seen_rows: set[bytes] = set()
    duplicate_rows = 0
    range_rejected_rows = 0
    content_bytes = 0
    output_digest = hashlib.sha256()
    source_row_counts: list[int] = []
    format_label = input_format.upper()

    with _connection(database) as connection:
        connection.execute("BEGIN")
        connection.execute(f"DROP TABLE IF EXISTS {stage}")
        connection.execute(
            f"CREATE TABLE {stage} ({', '.join(f'{_quote_identifier(name)} TEXT' for name in headers)})"
        )
        placeholders = ", ".join("?" for _ in headers)
        insert_sql = f"INSERT INTO {stage} VALUES ({placeholders})"
        batch: list[list[str | None]] = []
        for path, raw_headers, normalized, rows in zip(
            sources, source_headers, normalized_headers, source_rows, strict=True
        ):
            file_input_rows = 0
            for row in rows:
                input_rows += 1
                file_input_rows += 1
                if input_rows > MAX_INPUT_ROWS:
                    raise ValueError(
                        f"{format_label} pipeline exceeds the {MAX_INPUT_ROWS:,}-row limit."
                    )
                if row is None or len(row) != len(raw_headers):
                    rejected_rows += 1
                    continue
                source_values = [_cell_text(value) for value in row]
                if any(
                    value is not None and len(value) > MAX_CELL_CHARS
                    for value in source_values
                ):
                    rejected_rows += 1
                    continue
                content_bytes += sum(
                    len(value.encode("utf-8")) for value in source_values if value is not None
                )
                if content_bytes > MAX_INPUT_BYTES:
                    raise ValueError(
                        f"{format_label} expanded data exceeds the "
                        f"{MAX_INPUT_BYTES:,}-byte limit."
                    )
                by_normalized = {
                    normalized[index]: source_values[index]
                    for index in range(len(raw_headers))
                }
                values = [by_normalized[name] for name in headers]
                digest = hashlib.sha256(
                    json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).digest()
                is_duplicate = digest in seen_rows
                if is_duplicate:
                    duplicate_rows += 1
                else:
                    seen_rows.add(digest)
                if is_duplicate and duplicate_policy == "error":
                    raise ValueError(f"Duplicate {format_label} row encountered in {path.name}.")
                if is_duplicate and duplicate_policy == "drop":
                    rejected_rows += 1
                    continue
                range_failure = None
                for column, (minimum, maximum) in ranges.items():
                    raw_value = values[headers.index(column)]
                    try:
                        numeric = float(raw_value)
                    except (TypeError, ValueError):
                        range_failure = f"column {column} contains a non-numeric value"
                        break
                    if not math.isfinite(numeric):
                        range_failure = f"column {column} is non-finite"
                        break
                    if minimum is not None and numeric < minimum:
                        range_failure = f"column {column} is below {minimum}"
                        break
                    if maximum is not None and numeric > maximum:
                        range_failure = f"column {column} is above {maximum}"
                        break
                if range_failure is not None:
                    rejected_rows += 1
                    range_rejected_rows += 1
                    continue
                for name, value in zip(headers, values, strict=True):
                    profiles[name].add(value)
                output_digest.update(
                    json.dumps(
                        values,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                batch.append(values)
                accepted_rows += 1
                if len(batch) >= 1_000:
                    connection.executemany(insert_sql, batch)
                    batch.clear()
            source_row_counts.append(file_input_rows)
        if batch:
            connection.executemany(insert_sql, batch)
        profile_rows = [
            profiles[name].as_dict(name, accepted_rows) for name in headers
        ]
        connection.execute(f"DROP TABLE IF EXISTS {target}")
        connection.execute(f"ALTER TABLE {stage} RENAME TO {target}")
        if run_id is not None:
            source_schemas = [
                [
                    {
                        "source_name": raw_name,
                        "normalized_name": normalized_name,
                        "inferred_type": profiles[normalized_name].as_dict(
                            normalized_name, accepted_rows
                        )["inferred_type"],
                    }
                    for raw_name, normalized_name in zip(raw, normalized, strict=True)
                ]
                for raw, normalized in zip(
                    source_headers, normalized_headers, strict=True
                )
            ]
            record_ingestion(
                connection,
                sources=sources,
                destination=destination,
                input_format=input_format,
                run_id=run_id,
                source_row_counts=source_row_counts,
                accepted_rows=accepted_rows,
                schema=source_schemas[0],
                source_schemas=source_schemas,
                output_checksum=output_digest.hexdigest(),
                quality_status=(
                    "FAIL"
                    if accepted_rows == 0
                    else "WARNING"
                    if rejected_rows
                    else "PASS"
                ),
                transformation_config={
                    "input_format": input_format,
                    "duplicate_policy": duplicate_policy,
                    "required_columns": sorted(required_columns or set()),
                    "numeric_ranges": numeric_ranges or {},
                    "columns": list(columns) if columns is not None else None,
                },
            )

    quality = [
        {"name": "has_accepted_rows", "passed": accepted_rows > 0, "details": {"accepted_rows": accepted_rows}},
        {"name": "all_rows_accepted", "passed": rejected_rows == 0, "details": {"rejected_rows": rejected_rows}},
        {"name": "no_duplicate_rows", "passed": duplicate_rows == 0, "details": {"duplicate_rows": duplicate_rows}},
        {"name": "duplicate_policy", "passed": duplicate_policy != "error" or duplicate_rows == 0, "details": {"policy": duplicate_policy, "duplicate_rows": duplicate_rows}},
        {"name": "numeric_ranges", "passed": range_rejected_rows == 0, "details": {"ranges": ranges, "rejected_rows": range_rejected_rows}},
        {"name": "within_input_row_limit", "passed": input_rows <= MAX_INPUT_ROWS, "details": {"input_rows": input_rows, "limit": MAX_INPUT_ROWS}},
    ]
    return input_rows, accepted_rows, rejected_rows, profile_rows, quality


def ingest_file(
    database: Path,
    source: Path,
    *,
    input_format: InputFormat | Literal["auto"] = "auto",
    table: str = "ingested_data",
    required_columns: Iterable[str] = (),
    duplicate_policy: Literal["keep", "drop", "error"] = "keep",
    numeric_ranges: dict[str, tuple[float | None, float | None]] | None = None,
    columns: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate, profile, and atomically replace a SQLite table from one local file."""
    source = Path(source)
    if input_format == "auto":
        input_format = {
            ".csv": "csv",
            ".json": "json",
            ".jsonl": "jsonl",
            ".ndjson": "jsonl",
            ".parquet": "parquet",
        }.get(source.suffix.casefold())
    if input_format not in {"csv", "json", "jsonl", "parquet"}:
        raise ValueError(
            "Could not determine input format. Use a .csv, .json, .jsonl, "
            ".ndjson, or .parquet file, or specify an explicit supported format."
        )
    table = _data_table(table, "Destination table")
    if duplicate_policy not in {"keep", "drop", "error"}:
        raise ValueError("duplicate_policy must be 'keep', 'drop', or 'error'.")
    if not source.is_file():
        raise FileNotFoundError(f"Source file does not exist: {source}")
    selected_columns = tuple(columns) if columns is not None else None
    required = {normalize_column_name(name, index) for index, name in enumerate(required_columns)}
    normalized_ranges: dict[str, tuple[float | None, float | None]] = {}
    for index, (column, bounds) in enumerate((numeric_ranges or {}).items()):
        normalized = normalize_column_name(column, index)
        if not isinstance(bounds, tuple) or len(bounds) != 2:
            raise ValueError(f"Numeric range for {column!r} must be a (minimum, maximum) tuple.")
        minimum, maximum = bounds
        if (
            (minimum is not None and (isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum)))
            or (maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or not math.isfinite(maximum)))
            or (minimum is not None and maximum is not None and minimum > maximum)
        ):
            raise ValueError(f"Numeric range for {column!r} has invalid bounds.")
        normalized_ranges[normalized] = (minimum, maximum)
    config = {
        "input_format": input_format,
        "table": table,
        "required_columns": sorted(required),
        "duplicate_policy": duplicate_policy,
        "numeric_ranges": normalized_ranges,
        "columns": selected_columns,
        "max_rows": MAX_INPUT_ROWS,
    }
    initialize_pipeline_schema(database)
    pipeline_name = f"{input_format}_ingestion"
    run_id, started = _start_run(
        database,
        pipeline_name=pipeline_name,
        source_name=source.name,
        destination=table,
        config=config,
    )
    try:
        input_rows, accepted, rejected, profile, quality = _load_tabular_sources(
            database,
            [source],
            table,
            input_format=input_format,
            required_columns=required,
            duplicate_policy=duplicate_policy,
            numeric_ranges=normalized_ranges,
            columns=selected_columns,
            run_id=run_id,
        )
        quality.append({
            "name": "required_columns_present",
            "passed": True,
            "details": {"required": sorted(required), "missing": []},
        })
        result = _finish_run(
            database,
            run_id=run_id,
            started_clock=started,
            pipeline_name=pipeline_name,
            source_name=source.name,
            destination=table,
            input_rows=input_rows,
            accepted_rows=accepted,
            rejected_rows=rejected,
            output_rows=accepted,
            quality=quality,
            transformations=[
                f"parse_{input_format}_records",
                "normalize_headers",
                "profile_columns",
                "detect_duplicate_rows",
                f"duplicate_policy:{duplicate_policy}",
                "validate_numeric_ranges",
                *(
                    [f"project_columns:{','.join(selected_columns)}"]
                    if selected_columns is not None
                    else []
                ),
            ],
        )
        result["profile"] = profile
        return result
    except Exception as exc:
        _fail_run(database, run_id, started, exc)
        raise


def ingest_csv(
    database: Path,
    source: Path,
    *,
    table: str = "ingested_data",
    required_columns: Iterable[str] = (),
    duplicate_policy: Literal["keep", "drop", "error"] = "keep",
    numeric_ranges: dict[str, tuple[float | None, float | None]] | None = None,
) -> dict[str, Any]:
    """Validate, profile, and atomically replace a SQLite table from one CSV."""
    return ingest_file(
        database,
        source,
        input_format="csv",
        table=table,
        required_columns=required_columns,
        duplicate_policy=duplicate_policy,
        numeric_ranges=numeric_ranges,
    )


def normalize_csv_files(
    database: Path,
    sources: Iterable[Path],
    *,
    table: str = "normalized_data",
    duplicate_policy: Literal["keep", "drop", "error"] = "keep",
) -> dict[str, Any]:
    """Combine two or more bounded CSVs with compatible normalized headers."""
    source_list = [Path(source) for source in sources]
    if not 2 <= len(source_list) <= MAX_FILES_PER_PIPELINE:
        raise ValueError(f"Select between 2 and {MAX_FILES_PER_PIPELINE} CSV files.")
    missing_sources = [source.name for source in source_list if not source.is_file()]
    if missing_sources:
        raise FileNotFoundError("CSV sources do not exist: " + ", ".join(missing_sources))
    table = _data_table(table, "Destination table")
    if duplicate_policy not in {"keep", "drop", "error"}:
        raise ValueError("duplicate_policy must be 'keep', 'drop', or 'error'.")
    source_name = ", ".join(source.name for source in source_list)
    config = {
        "table": table,
        "source_count": len(source_list),
        "duplicate_policy": duplicate_policy,
        "max_rows": MAX_INPUT_ROWS,
    }
    initialize_pipeline_schema(database)
    run_id, started = _start_run(
        database,
        pipeline_name="multi_file_normalization",
        source_name=source_name,
        destination=table,
        config=config,
    )
    try:
        input_rows, accepted, rejected, profile, quality = _load_tabular_sources(
            database,
            source_list,
            table,
            input_format="csv",
            duplicate_policy=duplicate_policy,
            run_id=run_id,
        )
        result = _finish_run(
            database,
            run_id=run_id,
            started_clock=started,
            pipeline_name="multi_file_normalization",
            source_name=source_name,
            destination=table,
            input_rows=input_rows,
            accepted_rows=accepted,
            rejected_rows=rejected,
            output_rows=accepted,
            quality=quality,
            transformations=[
                "normalize_headers",
                "combine_compatible_files",
                "profile_columns",
                f"duplicate_policy:{duplicate_policy}",
            ],
        )
        result["profile"] = profile
        return result
    except Exception as exc:
        _fail_run(database, run_id, started, exc)
        raise


def aggregate_table(
    database: Path,
    source_table: str,
    *,
    group_by: str,
    metric: str,
    destination: str = "analytical_summary",
) -> dict[str, Any]:
    """Create a grouped count/sum/mean table from a previously ingested CSV."""
    source_table = _data_table(source_table, "Source table")
    group_by = _identifier(group_by, "Group column")
    metric = _identifier(metric, "Metric column")
    destination = _data_table(destination, "Destination table")
    if source_table == destination:
        raise ValueError("Source and destination tables must differ.")
    initialize_pipeline_schema(database)
    source_name = source_table
    config = {"source_table": source_table, "group_by": group_by, "metric": metric, "destination": destination}
    run_id, started = _start_run(
        database,
        pipeline_name="analytical_aggregation",
        source_name=source_name,
        destination=destination,
        config=config,
    )
    try:
        source_sql = _quote_identifier(source_table)
        group_sql = _quote_identifier(group_by)
        metric_sql = _quote_identifier(metric)
        stage_name = _identifier(f"stage_{uuid.uuid4().hex}", "Temporary table")
        stage_sql = _quote_identifier(stage_name)
        groups: dict[str, list[float]] = {}
        input_rows = 0
        accepted_rows = 0
        rejected_rows = 0
        with _connection(database) as connection:
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({source_sql})")
            }
            if not columns:
                raise ValueError(f"Source table does not exist: {source_table}")
            missing = {group_by, metric} - columns
            if missing:
                raise ValueError("Source table is missing columns: " + ", ".join(sorted(missing)))
            for group_value, metric_value in connection.execute(
                f"SELECT {group_sql}, {metric_sql} FROM {source_sql}"
            ):
                input_rows += 1
                if input_rows > MAX_INPUT_ROWS:
                    raise ValueError(f"Aggregation exceeds the {MAX_INPUT_ROWS:,}-row limit.")
                if metric_value is None or str(metric_value).strip() == "":
                    rejected_rows += 1
                    continue
                try:
                    numeric = float(metric_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Metric column {metric} contains a non-numeric value.") from exc
                if not math.isfinite(numeric):
                    raise ValueError(f"Metric column {metric} contains a non-finite value.")
                key = "" if group_value is None else str(group_value)
                if key not in groups and len(groups) >= MAX_GROUPS:
                    raise ValueError(f"Aggregation exceeds the {MAX_GROUPS:,}-group limit.")
                groups.setdefault(key, []).append(numeric)
                accepted_rows += 1
            if not groups:
                raise ValueError("No numeric metric values are available for aggregation.")
            aggregate_rows = []
            for key, values in sorted(groups.items()):
                total = sum(values)
                mean = total / len(values)
                if not math.isfinite(total) or not math.isfinite(mean):
                    raise ValueError("Aggregated metric exceeds the supported numeric range.")
                aggregate_rows.append((key, len(values), total, mean))
            connection.execute(
                f"CREATE TABLE {stage_sql} (group_key TEXT NOT NULL, row_count INTEGER NOT NULL, "
                "metric_sum REAL NOT NULL, metric_mean REAL NOT NULL)"
            )
            connection.executemany(
                f"INSERT INTO {stage_sql} VALUES (?, ?, ?, ?)",
                aggregate_rows,
            )
            destination_sql = _quote_identifier(destination)
            connection.execute(f"DROP TABLE IF EXISTS {destination_sql}")
            connection.execute(f"ALTER TABLE {stage_sql} RENAME TO {destination_sql}")
        quality = [
            {"name": "numeric_metric_values_present", "passed": True, "details": {"groups": len(groups)}},
            {"name": "all_rows_have_numeric_metric", "passed": rejected_rows == 0, "details": {"rejected_rows": rejected_rows}},
            {"name": "within_input_row_limit", "passed": input_rows <= MAX_INPUT_ROWS, "details": {"input_rows": input_rows, "limit": MAX_INPUT_ROWS}},
        ]
        return _finish_run(
            database,
            run_id=run_id,
            started_clock=started,
            pipeline_name="analytical_aggregation",
            source_name=source_name,
            destination=destination,
            input_rows=input_rows,
            accepted_rows=accepted_rows,
            rejected_rows=rejected_rows,
            output_rows=len(groups),
            quality=quality,
            transformations=[f"group_by:{group_by}", f"count_sum_mean:{metric}"],
        )
    except Exception as exc:
        _fail_run(database, run_id, started, exc)
        raise


def get_pipeline_runs(database: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return bounded pipeline run and quality metadata without local file paths."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100.")
    if not Path(database).is_file():
        return []
    database_uri = Path(database).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(database_uri, uri=True, timeout=5)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "pipeline_runs" not in tables:
            return []
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT run_id, pipeline_name, source_name, destination, status,
                started_at_utc, ended_at_utc, duration_ns, input_rows,
                accepted_rows, rejected_rows, output_rows, rows_per_second,
                failure_count, retry_count
            FROM pipeline_runs ORDER BY started_at_utc DESC, run_id LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["quality"] = [
                {
                    "name": quality["check_name"],
                    "passed": bool(quality["passed"]),
                    "details": json.loads(quality["details_json"]),
                }
                for quality in connection.execute(
                    """
                    SELECT check_name, passed, details_json
                    FROM pipeline_quality WHERE run_id = ? ORDER BY check_name
                    """,
                    (item["run_id"],),
                )
            ]
            result.append(item)
        return result
    finally:
        connection.close()
