import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from typer.testing import CliRunner

from benchforge.cli import app
from benchforge.phase4 import (
    assess_repeatability,
    compare_sqlite_duckdb,
    cpu_xorshift_checksum,
    run_cpu_comparison,
)
from benchforge.runner import write_jsonl_atomic

cli_runner = CliRunner()


def _native_cpu_result(command):
    iterations = int(command[command.index("--iterations") + 1])
    return subprocess.CompletedProcess(
        command,
        0,
        json.dumps(
            {
                "schema_version": 1,
                "workload": "cpu",
                "iterations": iterations,
                "elapsed_ns": 200,
                "checksum": cpu_xorshift_checksum(iterations),
            }
        ),
        "",
    )


def test_cpu_comparison_checks_equivalence_before_recording_timing(
    tmp_path, monkeypatch
):
    binary = tmp_path / "benchforge_workloads.exe"
    binary.touch()
    output = tmp_path / "cpu.jsonl"
    calls = []

    def fake_execute(command, executable, *, description):
        calls.append(command)
        return _native_cpu_result(command)

    monkeypatch.setattr("benchforge.phase4._execute_native_process", fake_execute)
    records = run_cpu_comparison(
        binary,
        runs=2,
        warmup=0,
        iterations=100,
        output=output,
    )

    assert len(calls) == 3
    assert len(records) == 4
    assert {record["variant"] for record in records} == {"python", "native"}
    assert len({record["run_id"] for record in records}) == 1
    assert len({record["checksum"] for record in records}) == 1
    assert all(record["elapsed_ns"] > 0 for record in records)
    assert [json.loads(line) for line in output.read_text().splitlines()] == records


def test_cpu_comparison_mismatch_fails_before_emitting_results(tmp_path, monkeypatch):
    binary = tmp_path / "benchforge_workloads.exe"
    binary.touch()
    output = tmp_path / "cpu.jsonl"
    output.write_text("existing output\n", encoding="utf-8")
    result = _native_cpu_result(["native", "--iterations", "20"])
    payload = json.loads(result.stdout)
    payload["checksum"] += 1
    result.stdout = json.dumps(payload)
    monkeypatch.setattr(
        "benchforge.phase4._execute_native_process", lambda *args, **kwargs: result
    )

    with pytest.raises(ValueError, match="correctness mismatch"):
        run_cpu_comparison(binary, runs=2, warmup=0, iterations=20, output=output)
    assert output.read_text(encoding="utf-8") == "existing output\n"


def test_cpu_comparison_rejects_excessive_total_work(tmp_path):
    binary = tmp_path / "missing.exe"
    with pytest.raises(ValueError, match="volume"):
        run_cpu_comparison(
            binary,
            runs=20,
            warmup=0,
            iterations=1_000_000,
        )


def test_concurrent_atomic_writes_never_leave_partial_jsonl(tmp_path):
    output = tmp_path / "concurrent.jsonl"
    batches = [
        [{"writer": index, "sample": sample} for sample in range(250)]
        for index in range(4)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda batch: write_jsonl_atomic(output, batch), batches))

    saved = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(saved) == 250
    assert len({record["writer"] for record in saved}) == 1
    assert {record["sample"] for record in saved} == set(range(250))
    assert not list(tmp_path.glob(".concurrent.jsonl.*.tmp"))


def test_sqlite_duckdb_comparison_is_optional_and_correctness_gated():
    pytest.importorskip("duckdb")
    records = compare_sqlite_duckdb(rows=100, groups=10, runs=2)
    assert len(records) == 4
    assert {record["variant"] for record in records} == {"sqlite", "duckdb"}
    assert {record["checksum"] for record in records} == {43_150}
    assert len({record["run_id"] for record in records}) == 1
    assert all(record["elapsed_ns"] > 0 for record in records)


def test_compare_engines_cli_reports_query_results(tmp_path):
    pytest.importorskip("duckdb")
    output = tmp_path / "engine-comparison.jsonl"

    result = cli_runner.invoke(
        app,
        [
            "compare-engines",
            "--rows", "100",
            "--groups", "10",
            "--runs", "2",
            "--output", str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "SQLite median query time" in result.output
    assert "DuckDB median query time" in result.output
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(records) == 4
    assert {record["workload"] for record in records} == {"group_count_sum"}


def test_repeatability_rejects_invalid_sample_floor():
    with pytest.raises(ValueError, match="at least 2"):
        assess_repeatability([10, 11], minimum_samples=1)
