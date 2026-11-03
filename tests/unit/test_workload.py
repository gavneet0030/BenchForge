import json
import subprocess

import pytest

from benchforge.workload import run_native_workload


def _measurement(workload="cpu", checksum=123):
    return json.dumps({
        "schema_version": 1,
        "workload": workload,
        "iterations": 1000,
        "elapsed_ns": 200,
        "checksum": checksum,
        "bytes_processed": 0,
        "peak_process_memory_bytes": 1_048_576,
        "peak_process_memory_source": "test_peak_rss",
        "current_working_set_bytes": 2_097_152,
        "private_process_bytes": 3_145_728,
        "thread_count": 1,
        "process_cpu_time_ns": 150,
        "cpu_utilization_percent": 75.0,
    })


def test_native_workload_records_samples_and_writes_jsonl(tmp_path, monkeypatch):
    binary = tmp_path / "benchforge_workloads.exe"
    binary.write_bytes(b"test executable placeholder")
    output = tmp_path / "nested" / "samples.jsonl"
    calls = []

    def fake_run(command, binary, *, description):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, _measurement(), "")

    monkeypatch.setattr("benchforge.workload._execute", fake_run)

    records = run_native_workload(
        binary,
        workload="cpu",
        runs=2,
        warmup=1,
        iterations=1000,
        output=output,
    )

    assert len(calls) == 3
    assert len(records) == 2
    assert records[0]["sample_index"] == 0
    assert records[0]["workload"] == "cpu"
    assert records[0]["peak_process_memory_bytes"] == 1_048_576
    assert records[0]["peak_process_memory_source"] == "test_peak_rss"
    assert records[0]["current_working_set_bytes"] == 2_097_152
    assert records[0]["private_process_bytes"] == 3_145_728
    assert records[0]["thread_count"] == 1
    assert records[0]["process_cpu_time_ns"] == 150
    assert records[0]["cpu_utilization_percent"] == 75.0
    assert len({record["run_id"] for record in records}) == 1
    assert [json.loads(row) for row in output.read_text(encoding="utf-8").splitlines()] == records


def test_native_workload_keeps_unavailable_short_cpu_metrics_null(
    tmp_path, monkeypatch
):
    binary = tmp_path / "benchforge_workloads.exe"
    binary.touch()
    measurement = json.loads(_measurement())
    measurement["process_cpu_time_ns"] = None
    measurement["cpu_utilization_percent"] = None
    monkeypatch.setattr(
        "benchforge.workload._execute",
        lambda command, binary, *, description: subprocess.CompletedProcess(
            command, 0, json.dumps(measurement), ""
        ),
    )

    records = run_native_workload(
        binary,
        workload="cpu",
        runs=2,
        warmup=0,
        iterations=1000,
    )

    assert all(record["process_cpu_time_ns"] is None for record in records)
    assert all(record["cpu_utilization_percent"] is None for record in records)


@pytest.mark.parametrize(
    ("workload", "iterations", "working_set_kib", "file_size_kib"),
    [
        ("bad", 10, 1024, 1024),
        ("cpu", 0, 1024, 1024),
        ("memory", 1000, 65536, 1024),
        ("file_io", 16, 1024, 16384),
    ],
)
def test_native_workload_rejects_unbounded_or_invalid_settings(
    tmp_path, workload, iterations, working_set_kib, file_size_kib
):
    with pytest.raises(ValueError):
        run_native_workload(
            tmp_path / "not-needed",
            workload=workload,
            runs=2,
            warmup=0,
            iterations=iterations,
            working_set_kib=working_set_kib,
            file_size_kib=file_size_kib,
        )


def test_native_workload_rejects_nondeterministic_checksum(tmp_path, monkeypatch):
    binary = tmp_path / "benchforge_workloads"
    binary.touch()
    checksums = iter((1, 2))
    monkeypatch.setattr(
        "benchforge.workload._execute",
        lambda command, binary, *, description: subprocess.CompletedProcess(
            command, 0, _measurement(checksum=next(checksums)), ""
        ),
    )

    with pytest.raises(ValueError, match="non-deterministic checksum"):
        run_native_workload(
            binary,
            workload="cpu",
            runs=2,
            warmup=0,
            iterations=1000,
        )


def test_native_workload_surfaces_process_timeout(tmp_path, monkeypatch):
    binary = tmp_path / "benchforge_workloads"
    binary.touch()

    def timeout(command, binary, *, description):
        raise subprocess.TimeoutExpired(command, 30)

    monkeypatch.setattr("benchforge.workload._execute", timeout)

    with pytest.raises(TimeoutError, match="exceeded 30 seconds"):
        run_native_workload(
            binary,
            workload="cpu",
            runs=2,
            warmup=0,
            iterations=1000,
        )


def test_native_workload_reports_process_failure_with_context(tmp_path, monkeypatch):
    binary = tmp_path / "benchforge_workloads"
    binary.touch()
    failure = subprocess.CalledProcessError(
        4, ["benchforge_workloads"], output="", stderr="native diagnostic"
    )

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("benchforge.runner.subprocess.run", fail)

    with pytest.raises(
        RuntimeError,
        match="Native cpu workload failed with exit code 4: native diagnostic",
    ) as caught:
        run_native_workload(
            binary,
            workload="cpu",
            runs=2,
            warmup=0,
            iterations=1000,
        )

    assert caught.value.__cause__ is failure
