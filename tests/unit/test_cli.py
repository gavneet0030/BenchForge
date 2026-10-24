from typer.testing import CliRunner

from benchforge.cli import app
from benchforge.storage import save_experiment_samples

runner = CliRunner()


def make_records(durations):
    return [
        {
            "timestamp_utc": f"2026-10-21T10:00:{index:02d}+00:00",
            "workload": "hash_lookup",
            "sample_index": index,
            "elapsed_ns": duration,
            "extra_work": 0,
            "os": "test-os",
            "python_version": "3.x",
            "schema_version": 1,
            "binary": "benchforge_lookup.exe",
            "pair_index": index,
            "execution_order": index * 2,
            "seed": 2026,
        }
        for index, duration in enumerate(durations)
    ]


def create_experiment(database, baseline, candidate):
    return save_experiment_samples(
        database,
        run_id="cli-test-run",
        baseline=make_records(baseline),
        candidate=make_records(candidate),
        config={"seed": 2026},
    )


def test_compare_cli_reports_paired_results(tmp_path):
    database = tmp_path / "history.sqlite"
    create_experiment(
        database,
        baseline=[1000, 1010, 990, 1005, 995, 1002],
        candidate=[1001, 1009, 991, 1006, 996, 1003],
    )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "cli-test-run",
            "--database", str(database),
            "--n-resamples", "100",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Matched pairs: 6" in result.output
    assert "paired bootstrap CI" in result.output
    assert "Wilcoxon signed-rank p-value" in result.output
    assert "Decision:" in result.output


def test_compare_cli_rejects_missing_database(tmp_path):
    database = tmp_path / "missing.sqlite"

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "missing-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "Database does not exist" in result.output


def test_compare_cli_rejects_unknown_run_id(tmp_path):
    database = tmp_path / "history.sqlite"
    create_experiment(
        database,
        baseline=[1000, 1010, 990],
        candidate=[1001, 1011, 991],
    )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "unknown-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "both baseline and candidate" in result.output



def test_compare_cli_rejects_mismatched_pair_indices(tmp_path):
    database = tmp_path / "mismatched.sqlite"

    baseline = make_records([1000, 1010, 990])
    candidate = make_records([1001, 1011, 991])
    candidate[2]["pair_index"] = 99

    save_experiment_samples(
        database,
        run_id="cli-test-run",
        baseline=baseline,
        candidate=candidate,
        config={"seed": 2026},
    )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "cli-test-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "pair_index sets do not match" in result.output


def test_compare_cli_rejects_duplicate_pair_indices(tmp_path):
    database = tmp_path / "duplicate.sqlite"

    baseline = make_records([1000, 1010, 990])
    candidate = make_records([1001, 1011, 991])
    candidate[1]["pair_index"] = candidate[0]["pair_index"]

    save_experiment_samples(
        database,
        run_id="cli-test-run",
        baseline=baseline,
        candidate=candidate,
        config={"seed": 2026},
    )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "cli-test-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "Duplicate candidate pair_index" in result.output

def test_compare_cli_rejects_invalid_stored_duration(tmp_path):
    import sqlite3

    database = tmp_path / "invalid-duration.sqlite"
    create_experiment(
        database,
        baseline=[1000, 1010, 990],
        candidate=[1001, 1011, 991],
    )

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        cursor = connection.execute(
            "UPDATE benchmark_samples SET elapsed_ns = 0 "
            "WHERE run_id = ? AND variant = ? AND sample_index = ?",
            ("cli-test-run", "baseline", 0),
        )
        if cursor.rowcount != 1:
            raise AssertionError(
                f"Expected to alter one sample; updated {cursor.rowcount}."
            )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "cli-test-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "invalid elapsed_ns" in result.output
    assert "Traceback" not in result.output



def test_report_cli_writes_markdown(tmp_path):
    database = tmp_path / "benchforge.sqlite"
    output = tmp_path / "generated" / "report.md"
    create_experiment(database, [100, 110, 105, 108, 103], [90, 95, 92, 94, 91])

    result = runner.invoke(
        app,
        [
            "report", "--run-id", "cli-test-run",
            "--database", str(database), "--output", str(output),
            "--n-resamples", "100",
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.is_file()
    content = output.read_text(encoding="utf-8")
    assert "# BenchForge A/B Experiment Report" in content
    assert "Paired bootstrap confidence interval" in content
    assert "Wilcoxon signed-rank p-value" in content
    assert "cli-test-run" in content


def test_report_cli_refuses_to_overwrite_without_force(tmp_path):
    database = tmp_path / "benchforge.sqlite"
    output = tmp_path / "report.md"
    create_experiment(database, [100, 110, 105, 108, 103], [90, 95, 92, 94, 91])
    output.write_text("keep this report", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "report", "--run-id", "cli-test-run",
            "--database", str(database), "--output", str(output),
            "--n-resamples", "100",
        ],
    )

    assert result.exit_code != 0
    assert "keep this report" == output.read_text(encoding="utf-8")


def test_report_cli_requires_existing_database(tmp_path):
    result = runner.invoke(
        app,
        [
            "report", "--run-id", "missing-run",
            "--database", str(tmp_path / "missing.sqlite"),
            "--output", str(tmp_path / "report.md"),
        ],
    )
    assert result.exit_code != 0
    assert "Database does not exist" in result.output
