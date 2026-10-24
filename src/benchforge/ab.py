"""Interleaved A/B benchmark execution."""

from __future__ import annotations

import json
import platform
import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .runner import _native_runtime_environment


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

    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
        env=_native_runtime_environment(binary),
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

    elapsed_ns = measurement.get("elapsed_ns")
    if (
        isinstance(elapsed_ns, bool)
        or not isinstance(elapsed_ns, int)
        or elapsed_ns <= 0
    ):
        raise ValueError("Native workload returned an invalid duration.")

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
    if runs < 2 or warmup < 0 or iterations < 1:
        raise ValueError("Invalid run, warm-up, or iteration setting.")

    for value, name in (
        (baseline_extra_work, "baseline_extra_work"),
        (candidate_extra_work, "candidate_extra_work"),
        (baseline_delay_ns, "baseline_delay_ns"),
        (candidate_delay_ns, "candidate_delay_ns"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer.")

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer.")

    binaries = {
        "baseline": Path(baseline_binary).resolve(),
        "candidate": Path(candidate_binary).resolve(),
    }
    for variant, binary in binaries.items():
        if not binary.is_file():
            raise FileNotFoundError(
                f"{variant.capitalize()} executable not found: {binary}"
            )

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
                **measurement,
            })
            execution_order += 1

    return records
