import json
import subprocess
from pathlib import Path

import pytest

from benchforge.ab import run_ab_benchmark


def make_executable(path: Path) -> Path:
    path.write_text("test executable placeholder", encoding="utf-8")
    return path


def fake_measurement(*, iterations: int, extra_work: int) -> str:
    return json.dumps({
        "schema_version": 1,
        "workload": "hash_lookup",
        "iterations": iterations,
        "extra_work": extra_work,
        "elapsed_ns": 1000 + extra_work,
        "checksum": 42,
    })


def test_ab_produces_paired_records_in_execution_order(
    tmp_path, monkeypatch
):
    baseline = make_executable(tmp_path / "baseline.exe")
    candidate = make_executable(tmp_path / "candidate.exe")
    calls = []

    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if "--iterations" not in command:
            return real_run(command, **kwargs)
        calls.append(command)
        iterations = int(command[command.index("--iterations") + 1])
        extra_work = int(command[command.index("--extra-work") + 1])
        return subprocess.CompletedProcess(
            command, 0,
            fake_measurement(
                iterations=iterations,
                extra_work=extra_work,
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    records = run_ab_benchmark(
        baseline,
        candidate,
        runs=4,
        warmup=1,
        iterations=1234,
        baseline_extra_work=0,
        candidate_extra_work=5,
        seed=17,
    )

    assert len(records) == 8
    assert len(calls) == 10  # 2 warm-ups plus 8 measured invocations
    assert {r["variant"] for r in records} == {"baseline", "candidate"}
    assert {r["run_id"] for r in records}.__len__() == 1
    assert [r["execution_order"] for r in records] == list(range(8))

    for pair_index in range(4):
        pair = [r for r in records if r["pair_index"] == pair_index]
        assert {r["variant"] for r in pair} == {"baseline", "candidate"}
        assert len(pair) == 2
        assert {r["sample_index"] for r in pair} == {pair_index}

    candidate_records = [r for r in records if r["variant"] == "candidate"]
    assert all(r["extra_work"] == 5 for r in candidate_records)
    assert all(r["iterations"] == 1234 for r in records)


def test_seed_reproduces_variant_order(tmp_path, monkeypatch):
    baseline = make_executable(tmp_path / "base.exe")
    candidate = make_executable(tmp_path / "cand.exe")

    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if "--iterations" not in command:
            return real_run(command, **kwargs)
        iterations = int(command[command.index("--iterations") + 1])
        extra_work = int(command[command.index("--extra-work") + 1])
        return subprocess.CompletedProcess(
            command, 0,
            fake_measurement(
                iterations=iterations,
                extra_work=extra_work,
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    def order():
        rows = run_ab_benchmark(
            baseline, candidate, runs=5, warmup=0, seed=42
        )
        return [
            (r["pair_index"], r["variant"])
            for r in rows
        ]

    assert order() == order()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"runs": 1},
        {"runs": 2, "warmup": -1},
        {"runs": 2, "iterations": 0},
        {"runs": 2, "baseline_extra_work": -1},
        {"runs": 2, "candidate_extra_work": -1},
    ],
)
def test_invalid_settings_are_rejected(tmp_path, kwargs):
    baseline = make_executable(tmp_path / "base.exe")
    candidate = make_executable(tmp_path / "cand.exe")

    with pytest.raises(ValueError):
        run_ab_benchmark(baseline, candidate, **kwargs)


def test_missing_binary_is_rejected(tmp_path):
    candidate = make_executable(tmp_path / "candidate.exe")

    with pytest.raises(FileNotFoundError):
        run_ab_benchmark(
            tmp_path / "missing.exe",
            candidate,
            runs=2,
            warmup=0,
        )


def test_invalid_native_json_is_rejected(tmp_path, monkeypatch):
    baseline = make_executable(tmp_path / "base.exe")
    candidate = make_executable(tmp_path / "cand.exe")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "not json", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="invalid JSON"):
        run_ab_benchmark(
            baseline, candidate, runs=2, warmup=0
        )
