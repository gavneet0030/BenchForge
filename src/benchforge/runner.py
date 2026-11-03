"""Execute the native workload and preserve raw measurements."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

NATIVE_PROCESS_TIMEOUT_SECONDS = 30
MAX_NATIVE_RUNS = 1_000
MAX_NATIVE_WARMUPS = 100
MAX_NATIVE_ITERATIONS = 100_000_000
MAX_NATIVE_EXTRA_WORK = 100_000
MAX_NATIVE_OPERATION_VOLUME = 1_000_000_000


def _execute_native_process(
    command: list[str],
    binary: Path,
    *,
    description: str = "Native workload",
    timeout_seconds: int = NATIVE_PROCESS_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= NATIVE_PROCESS_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "timeout_seconds must be an integer between 1 and "
            f"{NATIVE_PROCESS_TIMEOUT_SECONDS}."
        )
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=_native_runtime_environment(binary),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"{description} exceeded {timeout_seconds} seconds."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(
            f"{description} failed with exit code "
            f"{exc.returncode}: {detail or 'no diagnostic output'}"
        ) from exc


def _validate_runner_settings(
    runs: int,
    warmup: int,
    iterations: int,
    extra_work: int,
) -> None:
    settings = (
        ("runs", runs, 2, MAX_NATIVE_RUNS),
        ("warmup", warmup, 0, MAX_NATIVE_WARMUPS),
        ("iterations", iterations, 1, MAX_NATIVE_ITERATIONS),
        ("extra_work", extra_work, 0, MAX_NATIVE_EXTRA_WORK),
    )
    for name, value, minimum, maximum in settings:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")

    sample_count = runs + warmup
    operations_per_iteration = extra_work + 1
    if (
        iterations
        > MAX_NATIVE_OPERATION_VOLUME // sample_count // operations_per_iteration
    ):
        raise ValueError(
            "iterations times total samples times (extra_work + 1) "
            f"must not exceed {MAX_NATIVE_OPERATION_VOLUME}."
        )


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


def _compiler_metadata(binary: Path) -> dict[str, str | None]:
    """Read compiler identity from an adjacent CMake cache when available."""
    cache_path = next(
        (parent / "CMakeCache.txt" for parent in binary.parents
         if (parent / "CMakeCache.txt").is_file()),
        None,
    )
    metadata: dict[str, str | None] = {
        "compiler": None,
        "compiler_version": None,
        "build_type": None,
        "build_generator": None,
        "build_toolset": None,
    }
    if cache_path is None:
        return metadata

    keys = {
        "CMAKE_CXX_COMPILER": "compiler",
        "CMAKE_CXX_COMPILER_ID": "compiler",
        "CMAKE_CXX_COMPILER_VERSION": "compiler_version",
        "CMAKE_BUILD_TYPE": "build_type",
        "CMAKE_GENERATOR": "build_generator",
        "CMAKE_GENERATOR_TOOLSET": "build_toolset",
    }
    values: dict[str, str] = {}
    for line in cache_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(("//", "#")) or "=" not in line:
            continue
        entry, value = line.split("=", 1)
        name = entry.split(":", 1)[0]
        if name in keys and value.strip():
            values[name] = value.strip()
    compiler_id = values.get("CMAKE_CXX_COMPILER_ID")
    compiler_path = values.get("CMAKE_CXX_COMPILER")
    metadata["compiler"] = compiler_id or (
        Path(compiler_path).name if compiler_path else None
    )
    metadata["compiler_version"] = values.get("CMAKE_CXX_COMPILER_VERSION")
    metadata["build_type"] = values.get("CMAKE_BUILD_TYPE") or None
    metadata["build_generator"] = values.get("CMAKE_GENERATOR")
    metadata["build_toolset"] = values.get("CMAKE_GENERATOR_TOOLSET") or None
    return metadata


def write_jsonl_atomic(output: Path, records: list[dict]) -> None:
    """Atomically replace a JSONL output, preserving the old file on failure."""
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            for record in records:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
        for attempt in range(5):
            try:
                temporary_path.replace(output)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def run_benchmark(
    binary: Path,
    runs: int = 30,
    warmup: int = 3,
    iterations: int = 100000,
    extra_work: int = 0,
    output: Path | None = None,
) -> list[dict]:
    _validate_runner_settings(runs, warmup, iterations, extra_work)

    binary = binary.resolve()

    if not binary.is_file():
        raise FileNotFoundError(
            f"Native executable not found: {binary}. Build the CMake project first."
        )

    records: list[dict] = []
    run_id = str(uuid4())
    compiler = _compiler_metadata(binary)

    for index in range(warmup + runs):
        command = [
            str(binary),
            "--iterations", str(iterations),
            "--extra-work", str(extra_work),
        ]

        completed = _execute_native_process(
            command,
            binary,
            description="Native benchmark",
        )
        try:
            measurement = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("Native workload returned invalid JSON.") from exc

        if not isinstance(measurement, dict):
            raise TypeError("Native workload measurement must be a JSON object.")

        if measurement.get("schema_version") != 1:
            raise ValueError("Unsupported native measurement schema.")

        if measurement.get("workload") != "hash_lookup":
            raise ValueError("Native workload returned an unexpected workload name.")

        elapsed_ns = measurement.get("elapsed_ns")
        if (
            isinstance(elapsed_ns, bool)
            or not isinstance(elapsed_ns, int)
            or elapsed_ns <= 0
        ):
            raise ValueError("Native workload returned an invalid duration.")

        checksum = measurement.get("checksum")
        if isinstance(checksum, bool) or not isinstance(checksum, int) or checksum < 0:
            raise ValueError("Native workload returned an invalid checksum.")

        if measurement.get("iterations") != iterations:
            raise ValueError("Native workload returned unexpected iteration count.")

        if measurement.get("extra_work") != extra_work:
            raise ValueError("Native workload returned unexpected extra-work setting.")

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
            **compiler,
            **measurement,
        })

    if output is None:
        output = Path("data/raw/latest.jsonl")

    write_jsonl_atomic(output, records)

    return records
