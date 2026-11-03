"""Bounded read benchmarks for common local tabular file formats."""

from __future__ import annotations

import csv
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .runner import write_jsonl_atomic

MAX_FORMAT_ROWS = 10_000
MAX_FORMAT_RUNS = 20
MAX_FORMAT_WARMUPS = 5
MAX_FORMAT_READ_OPERATIONS = 1_000_000
_FORMATS = ("csv", "json", "jsonl", "parquet")


def _checksum(rows: list[dict[str, int]]) -> int:
    return sum(
        row["record_id"] * 31 + row["value"] * 17 + row["group_id"]
        for row in rows
    )


def _read_records(path: Path, file_format: str) -> list[dict[str, int]]:
    if file_format == "csv":
        with path.open(newline="", encoding="utf-8") as stream:
            return [
                {key: int(value) for key, value in row.items()}
                for row in csv.DictReader(stream)
            ]
    if file_format == "json":
        return json.loads(path.read_text(encoding="utf-8"))
    if file_format == "jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
    if file_format == "parquet":
        try:
            from pyarrow import parquet
        except ImportError as exc:
            raise RuntimeError(
                "Parquet format benchmarking requires the optional 'datasets' "
                "dependencies; install BenchForge with the datasets extra."
            ) from exc
        return parquet.read_table(path).to_pylist()
    raise ValueError(f"Unsupported file format: {file_format}")


def _write_records(path: Path, file_format: str, rows: list[dict[str, int]]) -> None:
    if file_format == "csv":
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=("record_id", "value", "group_id"))
            writer.writeheader()
            writer.writerows(rows)
    elif file_format == "json":
        path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    elif file_format == "jsonl":
        path.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8",
        )
    elif file_format == "parquet":
        try:
            import pyarrow as arrow
            from pyarrow import parquet
        except ImportError as exc:
            raise RuntimeError(
                "Parquet format benchmarking requires the optional 'datasets' "
                "dependencies; install BenchForge with the datasets extra."
            ) from exc
        parquet.write_table(
            arrow.Table.from_pylist(rows),
            path,
            compression="NONE",
        )
    else:
        raise ValueError(f"Unsupported file format: {file_format}")


def run_format_benchmarks(
    *,
    rows: int = 1_000,
    runs: int = 3,
    warmup: int = 1,
    include_parquet: bool = False,
    output: Path | None = None,
) -> list[dict[str, Any]]:
    """Measure bounded warm-cache reads after verifying each format's contents."""
    for name, value, minimum, maximum in (
        ("rows", rows, 1, MAX_FORMAT_ROWS),
        ("runs", runs, 2, MAX_FORMAT_RUNS),
        ("warmup", warmup, 0, MAX_FORMAT_WARMUPS),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    if not isinstance(include_parquet, bool):
        raise TypeError("include_parquet must be a boolean.")

    formats = _FORMATS if include_parquet else _FORMATS[:-1]
    total_reads = rows * (runs + warmup) * len(formats)
    if total_reads > MAX_FORMAT_READ_OPERATIONS:
        raise ValueError(
            "Total format read volume must not exceed "
            f"{MAX_FORMAT_READ_OPERATIONS} rows."
        )

    expected_rows = [
        {
            "record_id": index,
            "value": (index * 17) % 1000,
            "group_id": index % 32,
        }
        for index in range(rows)
    ]
    expected_checksum = _checksum(expected_rows)
    all_records: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="benchforge-format-bench-") as directory:
        root = Path(directory)
        paths = {file_format: root / f"records.{file_format}" for file_format in formats}
        for file_format, path in paths.items():
            _write_records(path, file_format, expected_rows)

        for file_format, path in paths.items():
            for _ in range(warmup):
                warm_rows = _read_records(path, file_format)
                if len(warm_rows) != rows or _checksum(warm_rows) != expected_checksum:
                    raise ValueError(f"{file_format} warm-up content validation failed.")

            run_id = str(uuid4())
            bytes_processed = path.stat().st_size
            for sample_index in range(runs):
                started = time.perf_counter_ns()
                measured_rows = _read_records(path, file_format)
                elapsed_ns = max(time.perf_counter_ns() - started, 1)
                if (
                    len(measured_rows) != rows
                    or _checksum(measured_rows) != expected_checksum
                ):
                    raise ValueError(
                        f"{file_format} content validation failed; "
                        "the sample was not accepted."
                    )
                all_records.append(
                    {
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                        "run_id": run_id,
                        "variant": "python",
                        "workload": f"file_read_{file_format}",
                        "file_format": file_format,
                        "sample_index": sample_index,
                        "schema_version": 1,
                        "rows": rows,
                        "elapsed_ns": elapsed_ns,
                        "bytes_processed": bytes_processed,
                        "checksum": expected_checksum,
                        "cache_state": "warm_after_generation",
                    }
                )

    if output is not None:
        write_jsonl_atomic(Path(output), all_records)
    return all_records
