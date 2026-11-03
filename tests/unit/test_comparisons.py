import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchforge.cli import app
from benchforge.comparisons import (
    COMPARISON_WORKLOADS,
    _python_comparison_sample,
    run_equivalent_comparisons,
)
from benchforge.runner import write_jsonl_atomic
from benchforge.storage import get_samples

cli_runner = CliRunner()


def _native_result(command, executable=None, *, description=None):
    workload = command[command.index("--workload") + 1]
    items = int(command[command.index("--items") + 1])
    checksum, _ = _python_comparison_sample(workload, items)
    return subprocess.CompletedProcess(
        command,
        0,
        json.dumps(
            {
                "schema_version": 1,
                "workload": workload,
                "items": items,
                "elapsed_ns": 200,
                "checksum": checksum,
            }
        ),
        "",
    )


@pytest.mark.parametrize("workload", COMPARISON_WORKLOADS)
def test_comparison_requires_matching_python_and_native_checksums(
    tmp_path, monkeypatch, workload
):
    binary = tmp_path / "benchforge_comparisons.exe"
    binary.touch()
    monkeypatch.setattr("benchforge.comparisons._compiler_metadata", lambda _: {})
    monkeypatch.setattr(
        "benchforge.comparisons._execute_native_process", _native_result
    )

    records = run_equivalent_comparisons(
        binary,
        items=128,
        runs=2,
        warmup=0,
        workloads=(workload,),
    )

    assert len(records) == 4
    assert {record["variant"] for record in records} == {"python", "native"}
    assert len({record["run_id"] for record in records}) == 1
    assert len({record["checksum"] for record in records}) == 1
    assert all(record["elapsed_ns"] > 0 for record in records)


def test_comparison_assigns_a_distinct_run_id_to_each_workload(
    tmp_path, monkeypatch
):
    binary = tmp_path / "benchforge_comparisons.exe"
    binary.touch()
    monkeypatch.setattr("benchforge.comparisons._compiler_metadata", lambda _: {})
    monkeypatch.setattr(
        "benchforge.comparisons._execute_native_process", _native_result
    )

    records = run_equivalent_comparisons(
        binary, items=128, runs=2, warmup=0
    )

    run_ids_by_workload = {
        workload: {
            record["run_id"] for record in records if record["workload"] == workload
        }
        for workload in COMPARISON_WORKLOADS
    }
    assert all(len(run_ids) == 1 for run_ids in run_ids_by_workload.values())
    assert len({next(iter(run_ids)) for run_ids in run_ids_by_workload.values()}) == len(
        COMPARISON_WORKLOADS
    )


def test_comparison_cli_persists_each_workload_as_a_homogeneous_run(
    tmp_path, monkeypatch
):
    records = []
    for workload_index, workload in enumerate(COMPARISON_WORKLOADS):
        run_id = f"run-{workload_index}"
        for variant in ("python", "native"):
            for sample_index in range(2):
                records.append(
                    {
                        "timestamp_utc": "2026-01-01T00:00:00+00:00",
                        "run_id": run_id,
                        "variant": variant,
                        "workload": workload,
                        "sample_index": sample_index,
                        "elapsed_ns": 200 + sample_index,
                        "schema_version": 1,
                    }
                )
    binary = tmp_path / "benchforge_comparisons.exe"
    binary.touch()
    database = tmp_path / "history.sqlite"
    output = tmp_path / "comparisons.jsonl"
    monkeypatch.setattr("benchforge.cli._native_binary", lambda _: binary)
    monkeypatch.setattr(
        "benchforge.comparisons.run_equivalent_comparisons",
        lambda *args, **kwargs: records,
    )

    result = cli_runner.invoke(
        app,
        [
            "compare-workloads",
            "--items", "128",
            "--runs", "2",
            "--warmup", "0",
            "--output", str(output),
            "--database", str(database),
        ],
    )

    assert result.exit_code == 0, result.output
    for record in records:
        saved = get_samples(
            database, run_id=record["run_id"], variant=record["variant"]
        )
        assert {sample["workload"] for sample in saved} == {record["workload"]}
        assert len(saved) == 2


def test_checksum_mismatch_preserves_existing_output(tmp_path, monkeypatch):
    binary = tmp_path / "benchforge_comparisons.exe"
    binary.touch()
    output = tmp_path / "comparisons.jsonl"
    output.write_text("existing output\n", encoding="utf-8")
    result = _native_result(["native", "--workload", "sorting", "--items", "128"])
    payload = json.loads(result.stdout)
    payload["checksum"] += 1
    result.stdout = json.dumps(payload)
    monkeypatch.setattr("benchforge.comparisons._compiler_metadata", lambda _: {})
    monkeypatch.setattr(
        "benchforge.comparisons._execute_native_process", lambda *args, **kwargs: result
    )

    with pytest.raises(ValueError, match="correctness mismatch"):
        run_equivalent_comparisons(
            binary,
            items=128,
            runs=2,
            warmup=0,
            workloads=("sorting",),
            output=output,
        )

    assert output.read_text(encoding="utf-8") == "existing output\n"


def test_atomic_jsonl_retries_transient_windows_replace_conflicts(
    tmp_path, monkeypatch
):
    output = tmp_path / "measurements.jsonl"
    original_replace = Path.replace
    attempts = 0

    def replace_with_transient_conflict(source, target):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("transient destination sharing conflict")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", replace_with_transient_conflict)
    monkeypatch.setattr("benchforge.runner.time.sleep", lambda _: None)

    write_jsonl_atomic(output, [{"sample": 1}])

    assert attempts == 2
    assert json.loads(output.read_text(encoding="utf-8")) == {"sample": 1}
    assert not list(tmp_path.glob(".measurements.jsonl.*.tmp"))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"items": 0}, "items must be"),
        ({"runs": 1}, "runs must be"),
        ({"workloads": ("sorting", "sorting")}, "workloads must be"),
        ({"workloads": ("unknown",)}, "workloads must be"),
        ({"items": 10_000, "runs": 20, "warmup": 5}, "input volume"),
    ],
)
def test_comparison_rejects_invalid_or_unbounded_requests(
    tmp_path, kwargs, message
):
    with pytest.raises(ValueError, match=message):
        run_equivalent_comparisons(tmp_path / "not-needed.exe", **kwargs)
