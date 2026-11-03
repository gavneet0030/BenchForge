import json

import pytest
from typer.testing import CliRunner

from benchforge.cli import app
from benchforge.formats import run_format_benchmarks
from benchforge.storage import get_samples

cli_runner = CliRunner()


def test_format_benchmarks_verify_contents_and_keep_runs_homogeneous(
    tmp_path,
):
    output = tmp_path / "formats.jsonl"

    records = run_format_benchmarks(
        rows=50,
        runs=2,
        warmup=0,
        output=output,
    )

    assert len(records) == 6
    assert {record["file_format"] for record in records} == {"csv", "json", "jsonl"}
    assert all(record["elapsed_ns"] > 0 for record in records)
    assert all(record["bytes_processed"] > 0 for record in records)
    assert all(record["cache_state"] == "warm_after_generation" for record in records)
    for file_format in ("csv", "json", "jsonl"):
        matching = [record for record in records if record["file_format"] == file_format]
        assert len({record["checksum"] for record in matching}) == 1
        assert len({record["run_id"] for record in matching}) == 1
    assert [json.loads(line) for line in output.read_text().splitlines()] == records


def test_format_benchmark_cli_persists_each_format_in_its_own_run(tmp_path):
    output = tmp_path / "formats.jsonl"
    database = tmp_path / "history.sqlite"

    result = cli_runner.invoke(
        app,
        [
            "benchmark-formats",
            "--rows", "50",
            "--runs", "2",
            "--warmup", "0",
            "--output", str(output),
            "--database", str(database),
        ],
    )

    assert result.exit_code == 0, result.output
    saved_records = [json.loads(line) for line in output.read_text().splitlines()]
    for file_format in ("csv", "json", "jsonl"):
        samples = [
            record for record in saved_records if record["file_format"] == file_format
        ]
        assert len(samples) == 2
        stored = get_samples(
            database, run_id=samples[0]["run_id"], variant="python"
        )
        assert len(stored) == 2
        assert {record["workload"] for record in stored} == {
            f"file_read_{file_format}"
        }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rows": 0}, "rows must be"),
        ({"runs": 1}, "runs must be"),
        ({"warmup": 6}, "warmup must be"),
        ({"include_parquet": 1}, "include_parquet must be"),
    ],
)
def test_format_benchmark_rejects_unbounded_or_invalid_settings(kwargs, message):
    with pytest.raises((TypeError, ValueError), match=message):
        run_format_benchmarks(**kwargs)


def test_parquet_benchmark_fails_explicitly_when_optional_extra_is_missing(
    monkeypatch, tmp_path
):
    import builtins

    real_import = builtins.__import__

    def missing_arrow(name, *args, **kwargs):
        if name.startswith("pyarrow"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_arrow)

    with pytest.raises(RuntimeError, match="optional 'datasets' dependencies"):
        run_format_benchmarks(
            rows=10,
            runs=2,
            warmup=0,
            include_parquet=True,
            output=tmp_path / "parquet.jsonl",
        )
