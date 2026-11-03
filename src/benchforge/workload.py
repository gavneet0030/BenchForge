"""Bounded runner for the additional native CPU, memory, and file workloads."""

from __future__ import annotations

import json
import math
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .runner import (
    NATIVE_PROCESS_TIMEOUT_SECONDS,
    _execute_native_process,
    write_jsonl_atomic,
)

WorkloadName = Literal["cpu", "memory", "file_io", "tcp_loopback"]


def _execute(
    command: list[str],
    binary: Path,
    *,
    description: str = "Native workload",
    timeout_seconds: int = NATIVE_PROCESS_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    return _execute_native_process(
        command,
        binary,
        description=description,
        timeout_seconds=timeout_seconds,
    )


def _validate_settings(
    workload: str,
    runs: int,
    warmup: int,
    iterations: int,
    working_set_kib: int,
    file_size_kib: int,
) -> None:
    if workload not in {"cpu", "memory", "file_io", "tcp_loopback"}:
        raise ValueError("workload must be cpu, memory, file_io, or tcp_loopback.")
    for name, value, minimum in (
        ("runs", runs, 2),
        ("warmup", warmup, 0),
        ("iterations", iterations, 1),
        ("working_set_kib", working_set_kib, 1),
        ("file_size_kib", file_size_kib, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer of at least {minimum}.")
    if runs > 1000 or warmup > 100:
        raise ValueError("runs must not exceed 1000 and warmup must not exceed 100.")
    total_samples = runs + warmup
    if workload == "cpu" and iterations > 100_000_000:
        raise ValueError("CPU iterations must not exceed 100000000.")
    if workload == "cpu" and iterations * total_samples > 1_000_000_000:
        raise ValueError("CPU iterations times total samples must not exceed 1000000000.")
    if workload == "tcp_loopback" and (
        iterations > 100_000 or iterations * total_samples > 100_000
    ):
        raise ValueError("TCP loopback iteration volume must not exceed 100000.")
    if workload == "memory":
        if iterations > 1000 or working_set_kib > 65_536:
            raise ValueError("Memory workload exceeds its per-run resource limit.")
        if working_set_kib * iterations > 262_144:
            raise ValueError(
                "Memory working_set_kib times iterations must not exceed 262144."
            )
        if working_set_kib * iterations * total_samples > 262_144:
            raise ValueError("Total memory-workload volume must not exceed 256 MiB.")
    if workload == "file_io":
        if iterations > 16 or file_size_kib > 16_384:
            raise ValueError("File workload exceeds its per-run resource limit.")
        if file_size_kib * iterations > 131_072:
            raise ValueError(
                "File file_size_kib times iterations must not exceed 131072."
            )
        if file_size_kib * iterations * total_samples > 131_072:
            raise ValueError("Total file-workload volume must not exceed 128 MiB.")


def run_native_workload(
    binary: Path,
    *,
    workload: WorkloadName,
    runs: int = 30,
    warmup: int = 3,
    iterations: int = 100_000,
    working_set_kib: int = 1024,
    file_size_kib: int = 1024,
    output: Path | None = None,
    timeout_seconds: int = NATIVE_PROCESS_TIMEOUT_SECONDS,
) -> list[dict]:
    """Run a bounded native workload repeatedly and atomically save JSONL."""
    _validate_settings(workload, runs, warmup, iterations, working_set_kib, file_size_kib)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= NATIVE_PROCESS_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "timeout_seconds must be an integer between 1 and "
            f"{NATIVE_PROCESS_TIMEOUT_SECONDS}."
        )
    binary = Path(binary).resolve()
    if not binary.is_file():
        raise FileNotFoundError(f"Native workload executable not found: {binary}")

    command = [str(binary), "--workload", workload, "--iterations", str(iterations)]
    if workload == "memory":
        command.extend(["--working-set-kib", str(working_set_kib)])
    elif workload == "file_io":
        command.extend(["--file-size-kib", str(file_size_kib)])

    records: list[dict] = []
    run_id = str(uuid4())
    expected_checksum: int | None = None
    for index in range(warmup + runs):
        try:
            execution_options = (
                {"timeout_seconds": timeout_seconds}
                if timeout_seconds != NATIVE_PROCESS_TIMEOUT_SECONDS
                else {}
            )
            completed = _execute(
                command,
                binary,
                description=f"Native {workload} workload",
                **execution_options,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Native {workload} workload exceeded "
                f"{timeout_seconds} seconds."
            ) from exc

        try:
            measurement = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Native {workload} workload returned invalid JSON.") from exc
        if not isinstance(measurement, dict):
            raise TypeError("Native workload measurement must be a JSON object.")
        schema_version = measurement.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or schema_version != 1
            or measurement.get("workload") != workload
        ):
            raise ValueError("Native workload returned an unsupported schema or workload.")
        result_iterations = measurement.get("iterations")
        if (
            isinstance(result_iterations, bool)
            or not isinstance(result_iterations, int)
            or result_iterations != iterations
        ):
            raise ValueError("Native workload returned an unexpected iteration count.")
        for field in ("elapsed_ns", "checksum", "bytes_processed"):
            value = measurement.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Native workload returned an invalid {field}.")
        if measurement["elapsed_ns"] == 0:
            raise ValueError("Native workload returned a zero-duration measurement.")
        peak_memory = measurement.get("peak_process_memory_bytes")
        memory_source = measurement.get("peak_process_memory_source")
        if (
            isinstance(peak_memory, bool)
            or not isinstance(peak_memory, int)
            or peak_memory <= 0
        ):
            raise ValueError("Native workload returned invalid process peak memory.")
        if not isinstance(memory_source, str) or not memory_source:
            raise ValueError("Native workload returned an invalid memory measurement source.")
        for field in ("current_working_set_bytes", "private_process_bytes"):
            value = measurement.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"Native workload returned invalid {field}.")
        thread_count = measurement.get("thread_count")
        if thread_count is not None and (
            isinstance(thread_count, bool)
            or not isinstance(thread_count, int)
            or thread_count < 1
        ):
            raise ValueError("Native workload returned invalid thread_count.")
        process_cpu_time = measurement.get("process_cpu_time_ns")
        if process_cpu_time is not None and (
            isinstance(process_cpu_time, bool)
            or not isinstance(process_cpu_time, int)
            or process_cpu_time < 0
        ):
            raise ValueError("Native workload returned invalid process_cpu_time_ns.")
        cpu_utilization = measurement.get("cpu_utilization_percent")
        if cpu_utilization is not None and (
            isinstance(cpu_utilization, bool)
            or not isinstance(cpu_utilization, (int, float))
            or not math.isfinite(cpu_utilization)
            or cpu_utilization < 0
        ):
            raise ValueError("Native workload returned invalid cpu_utilization_percent.")

        checksum = measurement["checksum"]
        if expected_checksum is None:
            expected_checksum = checksum
        elif checksum != expected_checksum:
            raise ValueError("Native workload returned a non-deterministic checksum.")

        if index < warmup:
            continue
        records.append({
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "sample_index": index - warmup,
            "warmup_runs": warmup,
            "os": platform.platform(),
            "python_version": sys.version.split()[0],
            "binary": binary.name,
            "schema_version": 1,
            "workload": workload,
            "iterations": iterations,
            "elapsed_ns": measurement["elapsed_ns"],
            "checksum": checksum,
            "bytes_processed": measurement["bytes_processed"],
            "peak_process_memory_bytes": peak_memory,
            "peak_process_memory_source": memory_source,
            "current_working_set_bytes": measurement.get("current_working_set_bytes"),
            "private_process_bytes": measurement.get("private_process_bytes"),
            "thread_count": thread_count,
            "process_cpu_time_ns": process_cpu_time,
            "cpu_utilization_percent": cpu_utilization,
        })

    if output is not None:
        write_jsonl_atomic(output, records)
    return records
