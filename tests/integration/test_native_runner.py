import json
from pathlib import Path

import pytest

from benchforge.runner import run_benchmark

ROOT = Path(__file__).resolve().parents[2]

BINARY_CANDIDATES = [
    ROOT / "build" / "native-ucrt64" / "benchforge_lookup.exe",
    ROOT / "build" / "native" / "benchforge_lookup.exe",
    ROOT / "build" / "native-ucrt64" / "benchforge_lookup",
    ROOT / "build" / "native" / "benchforge_lookup",
]


def find_native_binary() -> Path:
    for candidate in BINARY_CANDIDATES:
        if candidate.is_file():
            return candidate
    pytest.skip("Native executable not built; build the CMake target first.")


def test_native_runner_saves_valid_measurements(tmp_path):
    binary = find_native_binary()
    output = tmp_path / "measurements.jsonl"

    records = run_benchmark(
        binary=binary,
        runs=3,
        warmup=1,
        iterations=1000,
        extra_work=0,
        output=output,
    )

    assert len(records) == 3
    assert output.is_file()

    lines = output.read_text(encoding="utf-8").splitlines()
    saved_records = [json.loads(line) for line in lines]

    assert len(saved_records) == 3
    assert [r["sample_index"] for r in saved_records] == [0, 1, 2]

    for record in saved_records:
        assert record["schema_version"] == 1
        assert record["workload"] == "hash_lookup"
        assert record["iterations"] == 1000
        assert record["extra_work"] == 0
        assert record["elapsed_ns"] > 0
        assert record["warmup_runs"] == 1
        assert record["binary"] == binary.name
        assert record["timestamp_utc"]
        assert record["os"]
        assert record["python_version"]


def test_runner_rejects_fewer_than_two_runs(tmp_path):
    with pytest.raises(ValueError, match="Invalid run"):
        run_benchmark(
            binary=ROOT / "missing-binary.exe",
            runs=1,
            output=tmp_path / "invalid.jsonl",
        )


def test_runner_rejects_zero_iterations(tmp_path):
    with pytest.raises(ValueError, match="Invalid run"):
        run_benchmark(
            binary=ROOT / "missing-binary.exe",
            runs=2,
            iterations=0,
            output=tmp_path / "invalid.jsonl",
        )


def test_runner_rejects_missing_executable(tmp_path):
    with pytest.raises(FileNotFoundError, match="Native executable not found"):
        run_benchmark(
            binary=ROOT / "definitely-missing-benchforge-binary.exe",
            runs=2,
            output=tmp_path / "missing.jsonl",
        )


def test_runtime_environment_preserves_path_and_adds_configured_directory(
    tmp_path, monkeypatch
):
    import os

    from benchforge.runner import _native_runtime_environment

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setenv("PATH", "existing-path")
    monkeypatch.setenv("BENCHFORGE_NATIVE_RUNTIME_DIR", str(runtime_dir))

    environment = _native_runtime_environment(tmp_path / "benchmark.exe")

    assert environment["PATH"].split(os.pathsep)[0] == str(runtime_dir.resolve())
    assert "existing-path" in environment["PATH"]


def test_runtime_environment_ignores_missing_configured_directory(
    tmp_path, monkeypatch
):
    from benchforge.runner import _native_runtime_environment

    monkeypatch.setenv("PATH", "existing-path")
    monkeypatch.setenv(
        "BENCHFORGE_NATIVE_RUNTIME_DIR",
        str(tmp_path / "does-not-exist"),
    )

    environment = _native_runtime_environment(tmp_path / "benchmark.exe")

    assert environment["PATH"] == "existing-path"


def test_runtime_environment_detects_cmake_compiler_directory(
    tmp_path, monkeypatch
):
    import os

    if os.name != "nt":
        pytest.skip("CMake compiler runtime discovery is Windows-specific.")

    from benchforge.runner import _native_runtime_environment

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    runtime_dir = tmp_path / "compiler-bin"
    runtime_dir.mkdir()
    binary = build_dir / "benchmark.exe"
    binary.write_text("", encoding="utf-8")
    (build_dir / "CMakeCache.txt").write_text(
        "CMAKE_CXX_COMPILER:UNINITIALIZED="
        + str(runtime_dir / "g++.exe")
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("PATH", "existing-path")
    monkeypatch.delenv("BENCHFORGE_NATIVE_RUNTIME_DIR", raising=False)

    environment = _native_runtime_environment(binary)

    assert environment["PATH"].split(os.pathsep)[0] == str(runtime_dir.resolve())
    assert "existing-path" in environment["PATH"]
