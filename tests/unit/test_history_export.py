import csv
import sqlite3

import pytest

from benchforge.history_export import (
    HISTORY_COLUMNS,
    MAX_HISTORY_EXPORT_ROWS,
    export_benchmark_history_csv,
)
from benchforge.storage import save_samples


def _record(index=0):
    return {
        "timestamp_utc": f"2026-11-02T10:00:{index:02d}+00:00",
        "workload": "hash_lookup",
        "sample_index": index,
        "elapsed_ns": 1_000 + index,
        "extra_work": 0,
        "os": "Windows",
        "python_version": "3.12.10",
        "schema_version": 1,
        "binary": "benchforge_lookup.exe",
    }


def test_export_benchmark_history_reads_only_and_writes_dbt_columns(tmp_path):
    database = tmp_path / "history.sqlite"
    output = tmp_path / "history.csv"
    save_samples(database, [_record(0), _record(1)], run_id="history-run")
    before = database.stat()

    count = export_benchmark_history_csv(database, output)

    assert count == 2
    with output.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert tuple(rows[0]) == HISTORY_COLUMNS
    assert [row["elapsed_ns"] for row in rows] == ["1000", "1001"]
    assert [row["run_id"] for row in rows] == ["history-run", "history-run"]
    assert database.stat().st_size == before.st_size
    assert database.stat().st_mtime_ns == before.st_mtime_ns
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_export_benchmark_history_does_not_replace_existing_output(tmp_path):
    database = tmp_path / "history.sqlite"
    output = tmp_path / "history.csv"
    save_samples(database, [_record()], run_id="history-run")
    output.write_text("keep this file", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        export_benchmark_history_csv(database, output)

    assert output.read_text(encoding="utf-8") == "keep this file"


def test_export_benchmark_history_requires_existing_history_table(tmp_path):
    database = tmp_path / "empty.sqlite"
    sqlite3.connect(database).close()

    with pytest.raises(ValueError, match="benchmark_samples"):
        export_benchmark_history_csv(database, tmp_path / "history.csv")


@pytest.mark.parametrize("max_rows", [0, -1, MAX_HISTORY_EXPORT_ROWS + 1, True])
def test_export_benchmark_history_rejects_unbounded_limits(tmp_path, max_rows):
    database = tmp_path / "history.sqlite"
    save_samples(database, [_record()], run_id="history-run")

    with pytest.raises(ValueError, match="max_rows"):
        export_benchmark_history_csv(
            database,
            tmp_path / "history.csv",
            max_rows=max_rows,
        )


def test_export_benchmark_history_cleans_temporary_output_on_row_limit(tmp_path):
    database = tmp_path / "history.sqlite"
    save_samples(database, [_record(0), _record(1)], run_id="history-run")

    with pytest.raises(ValueError, match="row export limit"):
        export_benchmark_history_csv(database, tmp_path / "history.csv", max_rows=1)

    assert not (tmp_path / "history.csv").exists()
    assert list(tmp_path.glob(".benchforge-history-*.tmp")) == []
