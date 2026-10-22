"""Execute the native workload and preserve raw measurements."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def _native_runtime_environment(binary: Path) -> dict[str, str]:
    """Prepare the child environment for native runtime DLL resolution."""
    import os

    environment = os.environ.copy()
    runtime_dirs: list[Path] = []

    configured_dir = environment.get("BENCHFORGE_NATIVE_RUNTIME_DIR", "").strip()
    if configured_dir:
        runtime_dirs.append(Path(configured_dir).expanduser())

    if os.name == "nt":
        for parent in binary.parents:
            cache = parent / "CMakeCache.txt"
            if not cache.is_file():
                continue

            try:
                lines = cache.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except OSError:
                break

            for line in lines:
                if line.startswith("CMAKE_CXX_COMPILER:") and "=" in line:
                    compiler_text = line.split("=", 1)[1].strip()
                    compiler = Path(compiler_text)
                    if compiler.is_absolute():
                        runtime_dirs.append(compiler.parent)
                    break
            break

    existing_path = environment.get("PATH", "")
    prefixes: list[str] = []
    seen: set[str] = set()

    for directory in runtime_dirs:
        if not directory.is_dir():
            continue
        normalized = str(directory.resolve())
        key = os.path.normcase(normalized)
        if key not in seen:
            prefixes.append(normalized)
            seen.add(key)

    if prefixes:
        environment["PATH"] = os.pathsep.join(
            prefixes + ([existing_path] if existing_path else [])
        )

    return environment

def run_benchmark(
    binary: Path,
    runs: int = 30,
    warmup: int = 3,
    iterations: int = 100000,
    extra_work: int = 0,
    output: Path | None = None,
) -> list[dict]:
    if runs < 2 or warmup < 0 or iterations < 1 or extra_work < 0:
        raise ValueError("Invalid run, warm-up, iteration, or extra-work setting.")

    binary = binary.resolve()

    if not binary.is_file():
        raise FileNotFoundError(
            f"Native executable not found: {binary}. Build the CMake project first."
        )

    records: list[dict] = []

    for index in range(warmup + runs):
        command = [
            str(binary),
            "--iterations", str(iterations),
            "--extra-work", str(extra_work),
        ]

        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
            env=_native_runtime_environment(binary),
        )
        measurement = json.loads(completed.stdout)

        if measurement.get("schema_version") != 1:
            raise ValueError("Unsupported native measurement schema.")
        if measurement.get("elapsed_ns", 0) <= 0:
            raise ValueError("Native workload returned an invalid duration.")

        if index < warmup:
            continue

        records.append({
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "sample_index": index - warmup,
            "warmup_runs": warmup,
            "os": platform.platform(),
            "python_version": sys.version.split()[0],
            "binary": binary.name,
            **measurement,
        })

    if output is None:
        output = Path("data/raw/latest.jsonl")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    return records
