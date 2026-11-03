import csv

import pytest

from benchforge.exports import SAMPLE_COLUMNS, export_samples_csv, export_table_csv
from benchforge.storage import save_experiment_samples, save_samples


def records(durations, *, variant_extra=0):
    return [
        {
            "timestamp_utc": f"2026-10-28T10:00:{index:02d}+00:00",
            "workload": "hash_lookup",
            "sample_index": index,
            "elapsed_ns": duration,
            "extra_work": variant_extra,
            "os": "test-os",
            "python_version": "3.14",
            "schema_version": 1,
            "binary": "benchmark.exe",
            "pair_index": index,
        }
        for index, duration in enumerate(durations)
    ]


def test_export_csv_writes_stable_columns_and_selected_run(tmp_path):
    database = tmp_path / "history.sqlite"
    output = tmp_path / "exports" / "samples.csv"
    save_samples(database, records([100, 110]), run_id="first")
    save_samples(database, records([120]), run_id="second")

    count = export_samples_csv(database, output, run_id="first")

    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert count == 2
    assert tuple(rows[0]) == SAMPLE_COLUMNS
    assert {row["run_id"] for row in rows} == {"first"}
    assert [int(row["elapsed_ns"]) for row in rows] == [100, 110]


def test_export_csv_allows_empty_database_history(tmp_path):
    database = tmp_path / "history.sqlite"
    output = tmp_path / "empty.csv"
    save_samples(database, records([100]), run_id="run")

    count = export_samples_csv(database, output, run_id="missing")

    assert count == 0
    assert output.read_text(encoding="utf-8-sig").strip().split(",") == list(SAMPLE_COLUMNS)


def test_export_csv_rejects_missing_database_and_empty_run(tmp_path):
    with pytest.raises(FileNotFoundError, match="Database does not exist"):
        export_samples_csv(tmp_path / "missing.sqlite", tmp_path / "out.csv")
    with pytest.raises(ValueError, match="non-empty"):
        export_samples_csv(tmp_path / "history.sqlite", tmp_path / "out.csv", run_id=" ")


def test_export_csv_escapes_spreadsheet_formulas_and_enforces_cell_limit(tmp_path):
    database = tmp_path / "history.sqlite"
    output = tmp_path / "samples.csv"
    formula_record = records([100])[0]
    formula_record["binary"] = "=HYPERLINK(\"https://example.invalid\")"
    save_samples(database, [formula_record], run_id="formula")

    export_samples_csv(database, output, run_id="formula")

    with output.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["binary"].startswith("'=")

    oversized = records([101])[0]
    save_samples(
        database,
        [oversized],
        run_id="oversized",
        config={"payload": "x" * 65_537},
    )
    output.write_text("preserve existing file", encoding="utf-8")
    with pytest.raises(ValueError, match="text field exceeding"):
        export_samples_csv(database, output, run_id="oversized")
    assert output.read_text(encoding="utf-8") == "preserve existing file"


def test_export_table_csv_writes_deterministic_spreadsheet_safe_rows(tmp_path):
    import sqlite3

    database = tmp_path / "pipeline.sqlite"
    output = tmp_path / "exports" / "summary.csv"
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE summary ("=category" TEXT, row_count INTEGER, metric_sum REAL)'
        )
        connection.executemany(
            "INSERT INTO summary VALUES (?, ?, ?)",
            [("B", 2, 20.0), ("=A1", 1, 3.5), ("A", 2, 30.5)],
        )

    count = export_table_csv(database, "summary", output)

    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert count == 3
    assert rows == [
        ["'=category", "row_count", "metric_sum"],
        ["'=A1", "1", "3.5"],
        ["A", "2", "30.5"],
        ["B", "2", "20.0"],
    ]


def test_export_table_csv_rejects_missing_table_unsafe_name_and_database_output(tmp_path):
    import sqlite3

    database = tmp_path / "pipeline.sqlite"
    output = tmp_path / "summary.csv"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE summary (value TEXT)")

    with pytest.raises(ValueError, match="does not exist"):
        export_table_csv(database, "missing", output)
    with pytest.raises(ValueError, match="lowercase letter"):
        export_table_csv(database, 'summary"; DROP TABLE summary;--', output)
    with pytest.raises(ValueError, match="differ from the database"):
        export_table_csv(database, "summary", database)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'summary'"
        ).fetchone() == ("summary",)


def test_export_table_csv_enforces_limits_without_replacing_existing_output(
    tmp_path, monkeypatch
):
    import sqlite3

    from benchforge import exports

    database = tmp_path / "pipeline.sqlite"
    output = tmp_path / "summary.csv"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE summary (value TEXT)")
        connection.executemany("INSERT INTO summary VALUES (?)", [("first",), ("second",)])
    output.write_text("preserve existing output", encoding="utf-8")
    monkeypatch.setattr(exports, "MAX_EXPORT_ROWS", 1)

    with pytest.raises(ValueError, match="row limit"):
        export_table_csv(database, "summary", output)

    assert output.read_text(encoding="utf-8") == "preserve existing output"


def test_history_comparison_uses_reference_and_rejects_incompatible_runs(tmp_path):
    from benchforge.history import compare_historical_runs

    database = tmp_path / "history.sqlite"
    save_experiment_samples(
        database,
        run_id="reference",
        baseline=records([100, 101, 99, 102, 98]),
        candidate=records([100, 101, 99, 102, 98]),
    )
    save_experiment_samples(
        database,
        run_id="current",
        baseline=records([100, 101, 99, 102, 98]),
        candidate=records([120, 121, 119, 122, 118]),
    )

    result = compare_historical_runs(
        database,
        run_id="current",
        reference_run_id="reference",
        n_resamples=200,
    )

    assert result["ratio"] == pytest.approx(1.2)
    assert result["reference_run_id"] == "reference"
    assert result["decision"] == "REGRESSION"

    incompatible = records([120, 121, 119, 122, 118], variant_extra=5)
    save_experiment_samples(
        database,
        run_id="incompatible",
        baseline=records([100, 101, 99, 102, 98]),
        candidate=incompatible,
    )
    with pytest.raises(ValueError, match="incompatible"):
        compare_historical_runs(
            database,
            run_id="incompatible",
            reference_run_id="reference",
            n_resamples=100,
        )


def test_history_comparison_rejects_missing_or_small_reference(tmp_path):
    from benchforge.history import compare_historical_runs

    database = tmp_path / "history.sqlite"
    save_experiment_samples(
        database,
        run_id="reference",
        baseline=records([100, 101, 99, 102, 98]),
        candidate=records([100, 101, 99, 102, 98]),
    )
    save_experiment_samples(
        database,
        run_id="small",
        baseline=records([100, 101]),
        candidate=records([102, 103]),
    )
    with pytest.raises(ValueError, match="5 observations"):
        compare_historical_runs(
            database, run_id="small", reference_run_id="reference"
        )


def test_history_comparison_treats_subthreshold_noisy_shift_as_non_regression(
    tmp_path,
):
    from benchforge.history import compare_historical_runs

    database = tmp_path / "history.sqlite"
    baseline_values = [90, 95, 98, 100, 102, 105, 110] * 2
    candidate_values = [round(value * 1.02) for value in baseline_values]
    save_experiment_samples(
        database,
        run_id="reference",
        baseline=records(baseline_values),
        candidate=records(baseline_values),
    )
    save_experiment_samples(
        database,
        run_id="current",
        baseline=records(candidate_values),
        candidate=records(candidate_values),
    )

    comparison = compare_historical_runs(
        database,
        run_id="current",
        reference_run_id="reference",
        threshold=0.03,
        n_resamples=500,
        seed=17,
    )

    assert comparison["current_count"] == 14
    assert comparison["reference_count"] == 14
    assert comparison["ratio"] == pytest.approx(1.02, abs=0.01)
    assert comparison["decision"] in {"PASS", "INCONCLUSIVE"}


def test_history_comparison_rejects_different_compiler_builds(tmp_path):
    from benchforge.history import compare_historical_runs

    database = tmp_path / "history.sqlite"
    reference = records([100, 101, 99, 102, 98])
    current = records([100, 101, 99, 102, 98])
    for record in reference:
        record.update({"compiler": "MSVC", "compiler_version": "19.40"})
    for record in current:
        record.update({"compiler": "MSVC", "compiler_version": "19.44"})
    save_experiment_samples(
        database,
        run_id="reference",
        baseline=reference,
        candidate=reference,
    )
    save_experiment_samples(
        database,
        run_id="current",
        baseline=current,
        candidate=current,
    )

    with pytest.raises(ValueError, match="compiler_version"):
        compare_historical_runs(
            database,
            run_id="current",
            reference_run_id="reference",
            n_resamples=100,
        )
