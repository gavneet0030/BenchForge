import json
import subprocess

import pytest

from benchforge.runner import _compiler_metadata, run_benchmark


def test_compiler_metadata_reports_visual_studio_generator(tmp_path):
    build_dir = tmp_path / "build"
    binary_dir = build_dir / "Release"
    binary_dir.mkdir(parents=True)
    binary = binary_dir / "benchforge_lookup.exe"
    binary.write_bytes(b"fake")
    (build_dir / "CMakeCache.txt").write_text(
        "CMAKE_GENERATOR:INTERNAL=Visual Studio 17 2022\n"
        "CMAKE_GENERATOR_TOOLSET:INTERNAL=\n",
        encoding="utf-8",
    )

    metadata = _compiler_metadata(binary)

    assert metadata["compiler"] is None
    assert metadata["build_generator"] == "Visual Studio 17 2022"
    assert metadata["build_toolset"] is None


@pytest.mark.parametrize(
    "settings",
    [
        {"runs": 1001},
        {"warmup": 101},
        {"iterations": 100_000_001},
        {"extra_work": 100_001},
        {"runs": 1000, "warmup": 100, "iterations": 1_000_000},
    ],
)
def test_runner_rejects_out_of_bounds_settings(tmp_path, settings):
    with pytest.raises(ValueError):
        run_benchmark(
            binary=tmp_path / "not-needed.exe",
            output=tmp_path / "measurements.jsonl",
            **settings,
        )


def test_runner_rejects_boolean_elapsed_duration(tmp_path, monkeypatch):
    binary = tmp_path / "fake_benchmark.exe"
    binary.write_bytes(b"fake")

    completed = subprocess.CompletedProcess(
        args=[str(binary)],
        returncode=0,
        stdout=json.dumps({
            "schema_version": 1,
            "elapsed_ns": True,
            "workload": "hash_lookup",
            "iterations": 100,
            "extra_work": 0,
        }),
        stderr="",
    )

    monkeypatch.setattr(
        "benchforge.runner.subprocess.run",
        lambda *args, **kwargs: completed,
    )

    with pytest.raises(ValueError, match="invalid duration"):
        run_benchmark(
            binary=binary,
            runs=2,
            warmup=0,
            iterations=100,
            output=tmp_path / "measurements.jsonl",
        )


def test_runner_rejects_non_object_json(tmp_path, monkeypatch):
    binary = tmp_path / "fake_benchmark.exe"
    binary.write_bytes(b"fake")

    completed = subprocess.CompletedProcess(
        args=[str(binary)],
        returncode=0,
        stdout=json.dumps(["not", "an", "object"]),
        stderr="",
    )

    monkeypatch.setattr(
        "benchforge.runner.subprocess.run",
        lambda *args, **kwargs: completed,
    )

    with pytest.raises(TypeError, match="JSON object"):
        run_benchmark(
            binary=binary,
            runs=2,
            warmup=0,
            iterations=100,
            output=tmp_path / "measurements.jsonl",
        )


def test_runner_rejects_missing_or_invalid_checksum(tmp_path, monkeypatch):
    binary = tmp_path / "fake_benchmark.exe"
    binary.write_bytes(b"fake")
    completed = subprocess.CompletedProcess(
        args=[str(binary)],
        returncode=0,
        stdout=json.dumps({
            "schema_version": 1,
            "elapsed_ns": 10,
            "workload": "hash_lookup",
            "iterations": 100,
            "extra_work": 0,
        }),
        stderr="",
    )
    monkeypatch.setattr(
        "benchforge.runner.subprocess.run",
        lambda *args, **kwargs: completed,
    )

    with pytest.raises(ValueError, match="invalid checksum"):
        run_benchmark(
            binary=binary,
            runs=2,
            warmup=0,
            iterations=100,
            output=tmp_path / "measurements.jsonl",
        )


def test_runner_uses_conservative_process_timeout(tmp_path, monkeypatch):
    binary = tmp_path / "fake_benchmark.exe"
    binary.write_bytes(b"fake")
    completed = subprocess.CompletedProcess(
        args=[str(binary)],
        returncode=0,
        stdout=json.dumps({
            "schema_version": 1,
            "elapsed_ns": 10,
            "workload": "hash_lookup",
            "iterations": 100,
            "extra_work": 0,
            "checksum": 42,
        }),
        stderr="",
    )
    timeouts = []

    monkeypatch.setattr("benchforge.runner.platform.platform", lambda: "test-platform")

    def fake_run(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return completed

    monkeypatch.setattr("benchforge.runner.subprocess.run", fake_run)

    run_benchmark(
        binary=binary,
        runs=2,
        warmup=0,
        iterations=100,
        output=tmp_path / "measurements.jsonl",
    )

    assert timeouts == [30, 30]


def test_runner_uses_one_run_id_and_atomic_jsonl_output(tmp_path, monkeypatch):
    binary = tmp_path / "fake_benchmark.exe"
    binary.write_bytes(b"fake")
    output = tmp_path / "samples.jsonl"
    output.write_text("previous output\n", encoding="utf-8")
    measurement = {
        "schema_version": 1,
        "elapsed_ns": 10,
        "workload": "hash_lookup",
        "iterations": 100,
        "extra_work": 0,
        "checksum": 42,
    }
    monkeypatch.setattr(
        "benchforge.runner.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, json.dumps(measurement), ""
        ),
    )
    records = run_benchmark(
        binary=binary,
        runs=2,
        warmup=0,
        iterations=100,
        output=output,
    )

    saved_records = [json.loads(line) for line in output.read_text().splitlines()]
    assert len({record["run_id"] for record in records}) == 1
    assert [record["run_id"] for record in saved_records] == [
        record["run_id"] for record in records
    ]
    assert not list(tmp_path.glob(".samples.jsonl.*.tmp"))


def test_runner_preserves_existing_output_when_measurement_fails(
    tmp_path, monkeypatch
):
    binary = tmp_path / "fake_benchmark.exe"
    binary.write_bytes(b"fake")
    output = tmp_path / "samples.jsonl"
    output.write_text("existing output\n", encoding="utf-8")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1, ["benchmark"], output="", stderr="failed"
        )

    monkeypatch.setattr("benchforge.runner.subprocess.run", fail)
    with pytest.raises(RuntimeError, match="failed"):
        run_benchmark(
            binary=binary,
            runs=2,
            warmup=0,
            iterations=100,
            output=output,
        )

    assert output.read_text(encoding="utf-8") == "existing output\n"


def test_atomic_jsonl_serialization_failure_preserves_output_and_cleans_temp(
    tmp_path,
):
    from benchforge.runner import write_jsonl_atomic

    output = tmp_path / "samples.jsonl"
    output.write_text("existing output\n", encoding="utf-8")

    with pytest.raises(TypeError, match="not JSON serializable"):
        write_jsonl_atomic(output, [{"unsupported": object()}])

    assert output.read_text(encoding="utf-8") == "existing output\n"
    assert not list(tmp_path.glob(".samples.jsonl.*.tmp"))


@pytest.mark.parametrize(
    ("failure", "expected_type", "message"),
    [
        (
            subprocess.TimeoutExpired(["benchmark"], 30),
            TimeoutError,
            "Native benchmark exceeded 30 seconds",
        ),
        (
            subprocess.CalledProcessError(
                7, ["benchmark"], output="", stderr="native diagnostic"
            ),
            RuntimeError,
            "exit code 7: native diagnostic",
        ),
    ],
)
def test_runner_reports_native_process_failures_with_context(
    tmp_path, monkeypatch, failure, expected_type, message
):
    binary = tmp_path / "fake_benchmark.exe"
    binary.write_bytes(b"fake")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("benchforge.runner.subprocess.run", fail)

    with pytest.raises(expected_type, match=message) as caught:
        run_benchmark(
            binary=binary,
            runs=2,
            warmup=0,
            iterations=100,
            output=tmp_path / "measurements.jsonl",
        )

    assert caught.value.__cause__ is failure
