"""Correctness-gated, bounded Python/native workload comparisons."""

from __future__ import annotations

import json
import platform
import random
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .runner import _compiler_metadata, _execute_native_process, write_jsonl_atomic

_UINT64_MASK = (1 << 64) - 1
COMPARISON_WORKLOADS = (
    "sorting",
    "searching",
    "array_operations",
    "aggregation",
    "hash_lookup",
    "file_parsing",
    "numeric",
)
MAX_COMPARISON_ITEMS = 10_000
MAX_COMPARISON_RUNS = 20
MAX_COMPARISON_WARMUPS = 5
MAX_COMPARISON_SEARCH_OPERATIONS = 2_000_000


def _values(items: int) -> list[int]:
    return [((index * 1_103_515_245 + 12_345) ^ (index >> 3)) for index in range(items)]


def _fold(values: list[int]) -> int:
    checksum = 0
    for value in values:
        checksum = ((checksum * 33) ^ value) & _UINT64_MASK
    return checksum


def _python_comparison_sample(workload: str, items: int) -> tuple[int, int]:
    values = _values(items)
    input_path: Path | None = None
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if workload == "file_parsing":
        temporary_directory = tempfile.TemporaryDirectory(prefix="benchforge-compare-")
        input_path = Path(temporary_directory.name) / "values.txt"
        input_path.write_text("".join(f"{value}\n" for value in values), encoding="ascii")

    try:
        started = time.perf_counter_ns()
        if workload == "sorting":
            values.sort()
            checksum = _fold(values)
        elif workload == "searching":
            checksum = 0
            for index in range(items // 4):
                target = values[(index * 97 + 11) % items]
                checksum = (checksum + values.index(target) + 1) & _UINT64_MASK
        elif workload == "array_operations":
            checksum = sum(value * 3 + 7 for value in values) & _UINT64_MASK
        elif workload == "aggregation":
            totals = [0] * 32
            for index, value in enumerate(values):
                totals[index % len(totals)] = (
                    totals[index % len(totals)] + value
                ) & _UINT64_MASK
            checksum = _fold(totals)
        elif workload == "hash_lookup":
            values_by_key = {index: index * 17 + 3 for index in range(items)}
            checksum = sum(values_by_key[index] for index in range(items)) & _UINT64_MASK
        elif workload == "file_parsing":
            if input_path is None:
                raise RuntimeError("Temporary comparison input was not prepared.")
            checksum = sum(
                int(value)
                for value in input_path.read_text(encoding="ascii").splitlines()
            ) & _UINT64_MASK
        elif workload == "numeric":
            state = 0x9E3779B97F4A7C15
            checksum = 0
            for index in range(items):
                state = (state ^ (state << 13)) & _UINT64_MASK
                state = (state ^ (state >> 7)) & _UINT64_MASK
                state = (state ^ (state << 17)) & _UINT64_MASK
                checksum = (checksum + (state ^ index)) & _UINT64_MASK
        else:
            raise ValueError(f"Unsupported comparison workload: {workload}")
        elapsed_ns = max(time.perf_counter_ns() - started, 1)
        return checksum, elapsed_ns
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()


def _native_comparison_sample(
    binary: Path, workload: str, items: int
) -> dict[str, Any]:
    completed = _execute_native_process(
        [
            str(binary),
            "--workload",
            workload,
            "--items",
            str(items),
        ],
        binary,
        description=f"Native {workload} comparison workload",
    )
    try:
        measurement = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Native {workload} workload returned invalid JSON."
        ) from exc
    if (
        not isinstance(measurement, dict)
        or measurement.get("schema_version") != 1
        or measurement.get("workload") != workload
        or measurement.get("items") != items
    ):
        raise ValueError(f"Native {workload} workload returned incompatible configuration.")
    for field in ("checksum", "elapsed_ns"):
        value = measurement.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Native {workload} workload returned invalid {field}.")
    if measurement["elapsed_ns"] == 0:
        raise ValueError(f"Native {workload} workload returned zero elapsed_ns.")
    return measurement


def run_equivalent_comparisons(
    binary: Path,
    *,
    items: int = 1_000,
    runs: int = 3,
    warmup: int = 1,
    seed: int = 2026,
    workloads: tuple[str, ...] = COMPARISON_WORKLOADS,
    output: Path | None = None,
) -> list[dict[str, Any]]:
    """Compare equivalent Python and native operations after checksum validation."""
    for name, value, minimum, maximum in (
        ("items", items, 1, MAX_COMPARISON_ITEMS),
        ("runs", runs, 2, MAX_COMPARISON_RUNS),
        ("warmup", warmup, 0, MAX_COMPARISON_WARMUPS),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer.")
    if (
        not workloads
        or len(set(workloads)) != len(workloads)
        or any(workload not in COMPARISON_WORKLOADS for workload in workloads)
    ):
        raise ValueError("workloads must be distinct supported comparison workloads.")
    repetitions = runs + warmup
    if items * repetitions * len(workloads) * 2 > 2_000_000:
        raise ValueError("Total comparison input volume must not exceed 2000000 items.")
    if "searching" in workloads and items * items // 8 * repetitions > MAX_COMPARISON_SEARCH_OPERATIONS:
        raise ValueError(
            "Search comparison volume must not exceed "
            f"{MAX_COMPARISON_SEARCH_OPERATIONS} comparisons."
        )

    binary = Path(binary).resolve()
    if not binary.is_file():
        raise FileNotFoundError(f"Native comparison executable not found: {binary}")

    rng = random.Random(seed)
    compiler = _compiler_metadata(binary)
    records: list[dict[str, Any]] = []
    execution_order = 0
    for workload in workloads:
        run_id = str(uuid4())
        expected_checksum, _ = _python_comparison_sample(workload, items)
        correctness = _native_comparison_sample(binary, workload, items)
        if correctness["checksum"] != expected_checksum:
            raise ValueError(
                f"Python/native {workload} correctness mismatch; "
                "no timing comparison was recorded."
            )
        for _ in range(warmup):
            _python_comparison_sample(workload, items)
            warmed = _native_comparison_sample(binary, workload, items)
            if warmed["checksum"] != expected_checksum:
                raise ValueError(f"Native {workload} checksum changed during warm-up.")

        for pair_index in range(runs):
            variants = ["python", "native"]
            rng.shuffle(variants)
            for variant in variants:
                if variant == "python":
                    checksum, elapsed_ns = _python_comparison_sample(workload, items)
                    metadata: dict[str, Any] = {}
                else:
                    measurement = _native_comparison_sample(binary, workload, items)
                    checksum = measurement["checksum"]
                    elapsed_ns = measurement["elapsed_ns"]
                    metadata = compiler
                if checksum != expected_checksum:
                    raise ValueError(
                        f"{variant} {workload} checksum mismatch at pair {pair_index}."
                    )
                records.append({
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "run_id": run_id,
                    "variant": variant,
                    "workload": workload,
                    "schema_version": 1,
                    "sample_index": pair_index,
                    "pair_index": pair_index,
                    "execution_order": execution_order,
                    "seed": seed,
                    "items": items,
                    "checksum": checksum,
                    "elapsed_ns": elapsed_ns,
                    "operations_per_second": items * 1_000_000_000 / elapsed_ns,
                    "os": platform.platform(),
                    "python_version": sys.version.split()[0],
                    "binary": binary.name if variant == "native" else "python",
                    **metadata,
                })
                execution_order += 1

    if output is not None:
        write_jsonl_atomic(Path(output), records)
    return records
