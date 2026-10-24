import json
import subprocess

import pytest

from benchforge.runner import run_benchmark


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
