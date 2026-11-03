"""Interleaved A/B benchmark execution."""

from __future__ import annotations

import json
import platform
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .runner import (
    MAX_NATIVE_EXTRA_WORK,
    MAX_NATIVE_ITERATIONS,
    MAX_NATIVE_OPERATION_VOLUME,
    MAX_NATIVE_RUNS,
    MAX_NATIVE_WARMUPS,
    _compiler_metadata,
    _execute_native_process,
)


def _measure(
    binary: Path,
    *,
    iterations: int,
    extra_work: int,
    delay_ns: int = 0,
) -> dict:
    """Execute one native measurement and validate its JSON response."""
    command = [
        str(binary),
        "--iterations", str(iterations),
        "--extra-work", str(extra_work),
    ]
    if delay_ns > 0:
        command.extend(["--delay-ns", str(delay_ns)])

    completed = _execute_native_process(
        command,
        binary,
        description=f"Native {binary.name}",
    )

    try:
        measurement = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Native workload returned invalid JSON: {binary.name}"
        ) from exc

    if not isinstance(measurement, dict):
        raise TypeError("Native measurement must be a JSON object.")

    if measurement.get("schema_version") != 1:
        raise ValueError("Unsupported native measurement schema.")

    if measurement.get("workload") != "hash_lookup":
        raise ValueError(f"Native workload returned an unexpected workload name: {binary.name}")

    elapsed_ns = measurement.get("elapsed_ns")
    if (
        isinstance(elapsed_ns, bool)
        or not isinstance(elapsed_ns, int)
        or elapsed_ns <= 0
    ):
        raise ValueError("Native workload returned an invalid duration.")

    checksum = measurement.get("checksum")
    if isinstance(checksum, bool) or not isinstance(checksum, int) or checksum < 0:
        raise ValueError(f"Native workload returned an invalid checksum: {binary.name}")

    if measurement.get("iterations") != iterations:
        raise ValueError("Native workload returned unexpected iterations.")

    if measurement.get("extra_work") != extra_work:
        raise ValueError("Native workload returned unexpected extra_work.")

    return measurement


def run_ab_benchmark(
    baseline_binary: Path,
    candidate_binary: Path,
    *,
    runs: int = 30,
    warmup: int = 3,
    iterations: int = 100000,
    baseline_extra_work: int = 0,
    candidate_extra_work: int = 0,
    seed: int = 2026,
    baseline_delay_ns: int = 0,
    candidate_delay_ns: int = 0,
) -> list[dict]:
    """Measure baseline and candidate in reproducibly interleaved pairs.

    Warm-ups are run independently for each variant. For every measured pair,
    a seeded random generator determines which variant executes first.
    Returned records are in actual execution order.
    """
    for name, value, minimum, maximum in (
        ("runs", runs, 2, MAX_NATIVE_RUNS),
        ("warmup", warmup, 0, MAX_NATIVE_WARMUPS),
        ("iterations", iterations, 1, MAX_NATIVE_ITERATIONS),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")

    for value, name in (
        (baseline_extra_work, "baseline_extra_work"),
        (candidate_extra_work, "candidate_extra_work"),
        (baseline_delay_ns, "baseline_delay_ns"),
        (candidate_delay_ns, "candidate_delay_ns"),
    ):
        maximum = (
            MAX_NATIVE_EXTRA_WORK if name.endswith("extra_work") else 5_000_000_000
        )
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= maximum
        ):
            raise ValueError(f"{name} must be an integer between 0 and {maximum}.")

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer.")

    total_samples = runs + warmup
    operations_per_pair = (
        baseline_extra_work + candidate_extra_work + 2
    )
    if (
        iterations
        > MAX_NATIVE_OPERATION_VOLUME // total_samples // operations_per_pair
    ):
        raise ValueError(
            "iterations times total samples times both variants' extra-work "
            f"volume must not exceed {MAX_NATIVE_OPERATION_VOLUME}."
        )
    if (
        (baseline_delay_ns + candidate_delay_ns) * total_samples
        > 60_000_000_000
    ):
        raise ValueError("Total injected delay must not exceed 60000000000 nanoseconds.")

    binaries = {
        "baseline": Path(baseline_binary).resolve(),
        "candidate": Path(candidate_binary).resolve(),
    }
    for variant, binary in binaries.items():
        if not binary.is_file():
            raise FileNotFoundError(
                f"{variant.capitalize()} executable not found: {binary}"
            )
    compiler_metadata = {
        variant: _compiler_metadata(binary)
        for variant, binary in binaries.items()
    }

    extra_work_by_variant = {
        "baseline": baseline_extra_work,
        "candidate": candidate_extra_work,
    }
    delay_by_variant = {
        "baseline": baseline_delay_ns,
        "candidate": candidate_delay_ns,
    }

    def measure_variant(variant: str) -> dict:
        kwargs = {
            "iterations": iterations,
            "extra_work": extra_work_by_variant[variant],
        }
        if delay_by_variant[variant] > 0:
            kwargs["delay_ns"] = delay_by_variant[variant]
        return _measure(binaries[variant], **kwargs)

    # Warm each variant separately before collecting measured pairs.
    for variant in ("baseline", "candidate"):
        for _ in range(warmup):
            measure_variant(variant)

    run_id = str(uuid4())
    rng = random.Random(seed)
    records: list[dict] = []
    execution_order = 0

    for pair_index in range(runs):
        pair_order = ["baseline", "candidate"]
        rng.shuffle(pair_order)
        pair_checksums: dict[str, int] = {}

        for variant in pair_order:
            binary = binaries[variant]
            measurement = measure_variant(variant)

            records.append({
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "run_id": run_id,
                "variant": variant,
                "pair_index": pair_index,
                "execution_order": execution_order,
                "sample_index": pair_index,
                "warmup_runs": warmup,
                "os": platform.platform(),
                "python_version": sys.version.split()[0],
                "binary": binary.name,
                "seed": seed,
                **compiler_metadata[variant],
                **measurement,
            })
            pair_checksums[variant] = measurement["checksum"]
            execution_order += 1

        if (
            baseline_extra_work == candidate_extra_work
            and pair_checksums["baseline"] != pair_checksums["candidate"]
        ):
            raise ValueError(
                f"Correctness mismatch in pair {pair_index}: baseline and candidate "
                "checksums differ."
            )

    return records
