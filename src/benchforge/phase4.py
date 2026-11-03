"""Bounded comparisons and reliability checks for Phase 4 benchmarks."""

from __future__ import annotations

import json
import platform
import random
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .analytics import _duckdb
from .metrics import summarize_latencies
from .runner import (
    NATIVE_PROCESS_TIMEOUT_SECONDS,
    _compiler_metadata,
    _execute_native_process,
    write_jsonl_atomic,
)

_UINT64_MASK = (1 << 64) - 1
MAX_CPU_COMPARISON_RUNS = 20
MAX_CPU_COMPARISON_WARMUPS = 5
MAX_CPU_COMPARISON_ITERATIONS = 1_000_000
MAX_CPU_COMPARISON_OPERATIONS = 10_000_000
MAX_DATABASE_BENCHMARK_ROWS = 100_000
MAX_DATABASE_BENCHMARK_RUNS = 20
MAX_DATABASE_BENCHMARK_QUERY_OPERATIONS = 1_000_000
DEFAULT_REPEATABILITY_SAMPLE_FLOOR = 5
DEFAULT_REPEATABILITY_MAX_CV = 0.05


def cpu_xorshift_checksum(iterations: int) -> int:
    """Return the unsigned 64-bit checksum used by the native CPU workload."""
    if (
        isinstance(iterations, bool)
        or not isinstance(iterations, int)
        or not 1 <= iterations <= MAX_CPU_COMPARISON_ITERATIONS
    ):
        raise ValueError(
            "iterations must be an integer between 1 and "
            f"{MAX_CPU_COMPARISON_ITERATIONS}."
        )

    state = 0x9E3779B97F4A7C15
    checksum = 0
    for index in range(iterations):
        state = (state ^ (state << 13)) & _UINT64_MASK
        state = (state ^ (state >> 7)) & _UINT64_MASK
        state = (state ^ (state << 17)) & _UINT64_MASK
        checksum = (checksum + (state ^ index)) & _UINT64_MASK
    return checksum


def _python_cpu_sample(iterations: int) -> tuple[int, int]:
    started = time.perf_counter_ns()
    checksum = cpu_xorshift_checksum(iterations)
    elapsed_ns = time.perf_counter_ns() - started
    return checksum, max(elapsed_ns, 1)


def _native_cpu_sample(
    binary: Path,
    iterations: int,
    *,
    timeout_seconds: int = NATIVE_PROCESS_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    timeout_option = (
        {"timeout_seconds": timeout_seconds}
        if timeout_seconds != NATIVE_PROCESS_TIMEOUT_SECONDS
        else {}
    )
    completed = _execute_native_process(
        [str(binary), "--workload", "cpu", "--iterations", str(iterations)],
        binary,
        description="Native CPU comparison workload",
        **timeout_option,
    )
    try:
        measurement = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Native CPU workload returned invalid JSON.") from exc
    if not isinstance(measurement, dict):
        raise TypeError("Native CPU measurement must be a JSON object.")
    if (
        measurement.get("schema_version") != 1
        or measurement.get("workload") != "cpu"
        or measurement.get("iterations") != iterations
    ):
        raise ValueError("Native CPU workload returned incompatible configuration.")
    checksum = measurement.get("checksum")
    if isinstance(checksum, bool) or not isinstance(checksum, int) or checksum < 0:
        raise ValueError("Native CPU workload returned invalid checksum.")
    elapsed_ns = measurement.get("elapsed_ns")
    if isinstance(elapsed_ns, bool) or not isinstance(elapsed_ns, int) or elapsed_ns <= 0:
        raise ValueError("Native CPU workload returned invalid elapsed_ns.")
    return measurement


def run_cpu_comparison(
    binary: Path,
    *,
    runs: int = 3,
    warmup: int = 1,
    iterations: int = 10_000,
    seed: int = 2026,
    output: Path | None = None,
    timeout_seconds: int = 30,
) -> list[dict[str, Any]]:
    """Compare equivalent Python and native CPU loops after checksum validation."""
    for name, value, minimum, maximum in (
        ("runs", runs, 2, MAX_CPU_COMPARISON_RUNS),
        ("warmup", warmup, 0, MAX_CPU_COMPARISON_WARMUPS),
        ("iterations", iterations, 1, MAX_CPU_COMPARISON_ITERATIONS),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= NATIVE_PROCESS_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "timeout_seconds must be an integer between 1 and "
            f"{NATIVE_PROCESS_TIMEOUT_SECONDS}."
        )
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer.")
    total_operations = iterations * (runs + warmup) * 2
    if total_operations > MAX_CPU_COMPARISON_OPERATIONS:
        raise ValueError(
            "Python/native comparison volume must not exceed "
            f"{MAX_CPU_COMPARISON_OPERATIONS} operations."
        )

    binary = Path(binary).resolve()
    if not binary.is_file():
        raise FileNotFoundError(f"Native executable not found: {binary}")

    expected_checksum = cpu_xorshift_checksum(iterations)
    correctness_sample = _native_cpu_sample(
        binary, iterations, timeout_seconds=timeout_seconds
    )
    if correctness_sample["checksum"] != expected_checksum:
        raise ValueError(
            "Python/native CPU correctness mismatch; no timing comparison was recorded."
        )

    for _ in range(warmup):
        _python_cpu_sample(iterations)
        if (
            _native_cpu_sample(
                binary, iterations, timeout_seconds=timeout_seconds
            )["checksum"]
            != expected_checksum
        ):
            raise ValueError("Native CPU workload checksum changed during warm-up.")

    run_id = str(uuid4())
    rng = random.Random(seed)
    compiler = _compiler_metadata(binary)
    records: list[dict[str, Any]] = []
    execution_order = 0
    for pair_index in range(runs):
        variants = ["python", "native"]
        rng.shuffle(variants)
        for variant in variants:
            if variant == "python":
                checksum, elapsed_ns = _python_cpu_sample(iterations)
                measurement: dict[str, Any] = {
                    "checksum": checksum,
                    "elapsed_ns": elapsed_ns,
                    "peak_process_memory_bytes": None,
                    "peak_process_memory_source": None,
                }
            else:
                measurement = _native_cpu_sample(
                    binary, iterations, timeout_seconds=timeout_seconds
                )
                elapsed_ns = measurement["elapsed_ns"]
            if measurement["checksum"] != expected_checksum:
                raise ValueError(
                    f"{variant} CPU checksum mismatch at pair {pair_index}."
                )
            records.append({
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "run_id": run_id,
                "variant": variant,
                "workload": "cpu_xorshift64",
                "schema_version": 1,
                "sample_index": pair_index,
                "pair_index": pair_index,
                "execution_order": execution_order,
                "seed": seed,
                "iterations": iterations,
                "checksum": expected_checksum,
                "elapsed_ns": elapsed_ns,
                "bytes_processed": 0,
                "os": platform.platform(),
                "python_version": sys.version.split()[0],
                "operations_per_second": iterations * 1_000_000_000 / elapsed_ns,
                "binary": binary.name if variant == "native" else "python",
                **(compiler if variant == "native" else {}),
                "peak_process_memory_bytes": measurement.get(
                    "peak_process_memory_bytes"
                ),
                "peak_process_memory_source": measurement.get(
                    "peak_process_memory_source"
                ),
            })
            execution_order += 1

    if output is not None:
        write_jsonl_atomic(Path(output), records)
    return records


def compare_sqlite_duckdb(
    *,
    rows: int = 10_000,
    groups: int = 100,
    runs: int = 3,
) -> list[dict[str, Any]]:
    """Compare identical bounded group aggregates; DuckDB remains optional."""
    for name, value, minimum, maximum in (
        ("rows", rows, 1, MAX_DATABASE_BENCHMARK_ROWS),
        ("groups", groups, 1, min(1_000, rows)),
        ("runs", runs, 2, MAX_DATABASE_BENCHMARK_RUNS),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    if rows * runs * 2 > MAX_DATABASE_BENCHMARK_QUERY_OPERATIONS:
        raise ValueError(
            "SQLite/DuckDB query volume must not exceed "
            f"{MAX_DATABASE_BENCHMARK_QUERY_OPERATIONS} row-visits."
        )
    duckdb = _duckdb()

    records = [(index % groups, (17 * index) % 1000) for index in range(rows)]
    expected_by_group: dict[int, tuple[int, int]] = {}
    for group, value in records:
        count, total = expected_by_group.get(group, (0, 0))
        expected_by_group[group] = (count + 1, total + value)
    expected = [
        (group, count, total)
        for group, (count, total) in sorted(expected_by_group.items())
    ]
    sqlite_connection = sqlite3.connect(":memory:")
    duckdb_connection = None
    try:
        duckdb_connection = duckdb.connect(":memory:", config={"threads": "1"})
        sqlite_connection.execute(
            "CREATE TABLE benchmark_data (group_id INTEGER NOT NULL, value INTEGER NOT NULL)"
        )
        sqlite_connection.executemany(
            "INSERT INTO benchmark_data VALUES (?, ?)", records
        )
        duckdb_connection.execute(
            """
            CREATE TABLE benchmark_data AS
            SELECT (sample_index % ?)::INTEGER AS group_id,
                   ((17 * sample_index) % 1000)::INTEGER AS value
            FROM range(?) AS samples(sample_index)
            """,
            [groups, rows],
        )

        query = (
            "SELECT group_id, COUNT(*), SUM(value) FROM benchmark_data "
            "GROUP BY group_id ORDER BY group_id"
        )
        sqlite_result = sqlite_connection.execute(query).fetchall()
        duckdb_result = duckdb_connection.execute(query).fetchall()
        if sqlite_result != expected or duckdb_result != expected:
            raise ValueError(
                "SQLite/DuckDB aggregate correctness check failed; "
                "no timing comparison was recorded."
            )

        run_id = str(uuid4())
        result_records: list[dict[str, Any]] = []
        for sample_index in range(runs):
            for engine, connection in (
                ("sqlite", sqlite_connection),
                ("duckdb", duckdb_connection),
            ):
                started = time.perf_counter_ns()
                measured_result = connection.execute(query).fetchall()
                elapsed_ns = max(time.perf_counter_ns() - started, 1)
                if measured_result != expected:
                    raise ValueError(f"{engine} aggregate changed during measurement.")
                result_records.append({
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "run_id": run_id,
                    "variant": engine,
                    "workload": "group_count_sum",
                    "schema_version": 1,
                    "sample_index": sample_index,
                    "rows": rows,
                    "groups": groups,
                    "elapsed_ns": elapsed_ns,
                    "bytes_processed": 0,
                    "rows_per_second": rows * 1_000_000_000 / elapsed_ns,
                    "checksum": sum(total for _, _, total in expected),
                })
        return result_records
    finally:
        sqlite_connection.close()
        if duckdb_connection is not None:
            duckdb_connection.close()


def summarize_measurements(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize latencies and, for byte workloads, throughput without filtering."""
    if not records:
        raise ValueError("At least one measurement is required.")
    latency = summarize_latencies([record["elapsed_ns"] for record in records])
    summary: dict[str, Any] = {"latency": latency}
    byte_counts = [record.get("bytes_processed", 0) for record in records]
    if any(byte_counts):
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in byte_counts
        ):
            raise ValueError("Byte throughput requires positive bytes in every sample.")
        rates = [
            count * 1_000_000_000 / record["elapsed_ns"]
            for count, record in zip(byte_counts, records, strict=True)
        ]
        summary["throughput_bytes_per_second"] = summarize_latencies(rates)
    return summary


def assess_repeatability(
    samples: list[int | float],
    *,
    minimum_samples: int = DEFAULT_REPEATABILITY_SAMPLE_FLOOR,
    maximum_coefficient_of_variation: float = DEFAULT_REPEATABILITY_MAX_CV,
) -> dict[str, Any]:
    """Classify a run's repeatability without removing outliers or hiding noise."""
    if (
        isinstance(minimum_samples, bool)
        or not isinstance(minimum_samples, int)
        or minimum_samples < 2
    ):
        raise ValueError("minimum_samples must be an integer of at least 2.")
    if (
        isinstance(maximum_coefficient_of_variation, bool)
        or not isinstance(maximum_coefficient_of_variation, (int, float))
        or not 0 <= maximum_coefficient_of_variation < 1
    ):
        raise ValueError("maximum_coefficient_of_variation must be in [0, 1).")
    summary = summarize_latencies(samples)
    coefficient = summary["coefficient_of_variation"]
    if len(samples) < minimum_samples:
        status = "INSUFFICIENT_SAMPLE"
    elif coefficient is not None and coefficient <= maximum_coefficient_of_variation:
        status = "REPEATABLE"
    else:
        status = "NOISY"
    return {
        "status": status,
        "sample_count": len(samples),
        "minimum_samples": minimum_samples,
        "coefficient_of_variation": coefficient,
        "maximum_coefficient_of_variation": maximum_coefficient_of_variation,
        "outlier_indices": summary["potential_outlier_indices"],
        "outliers_removed": False,
    }
