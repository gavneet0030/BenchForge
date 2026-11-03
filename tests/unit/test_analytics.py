import builtins
import csv
from pathlib import Path

import pytest

from benchforge import analytics


def _csv(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Region", "Revenue"])
        writer.writerows(rows)
    return path


def test_duckdb_missing_optional_dependency_is_reported(monkeypatch):
    real_import = builtins.__import__

    def without_duckdb(name, *args, **kwargs):
        if name == "duckdb":
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_duckdb)

    with pytest.raises(RuntimeError, match="optional 'analytics' dependencies"):
        analytics._duckdb()


def test_analytics_connection_closes_after_success_and_error(monkeypatch):
    class FakeConnection:
        closed = False

        def close(self):
            self.closed = True

    fake_connection = FakeConnection()
    monkeypatch.setattr(
        analytics, "_duckdb", lambda: type("DuckDB", (), {
            "connect": staticmethod(lambda *args, **kwargs: fake_connection)
        })
    )

    with pytest.raises(ValueError, match="test failure"), analytics._connection():
        raise ValueError("test failure")

    assert fake_connection.closed


def test_analytics_rejects_missing_or_unsupported_sources(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        analytics.inspect_file(tmp_path / "missing.csv")

    unsupported = tmp_path / "source.json"
    unsupported.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="supports local .csv and .parquet"):
        analytics.inspect_file(unsupported)


def test_analytics_rejects_invalid_database_parent(tmp_path):
    pytest.importorskip("duckdb")
    source = _csv(tmp_path / "source.csv", [["north", 2]])
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("file", encoding="utf-8")

    with pytest.raises(OSError):
        analytics.inspect_file(source, database=parent_file / "history.duckdb")


def test_duckdb_inspects_and_aggregates_csv_with_safe_path_parameters(tmp_path):
    pytest.importorskip("duckdb")
    source = _csv(tmp_path / "data ' ; DROP TABLE records.csv", [
        ["north", 10],
        ["north", 20],
        ["south", "not-a-number"],
    ])
    database = tmp_path / "analytics.duckdb"

    inspection = analytics.inspect_file(source, database=database)
    summary = analytics.summarize_file(
        source,
        group_by="Region",
        metric="Revenue",
        database=database,
    )

    assert inspection["row_count"] == 3
    assert inspection["format"] == "csv"
    assert [column["name"] for column in inspection["columns"]] == ["Region", "Revenue"]
    assert summary["groups"] == [
        {
            "group_value": "north",
            "row_count": 2,
            "metric_sum": 30.0,
            "metric_mean": 15.0,
            "invalid_metric_rows": 0,
        },
        {
            "group_value": "south",
            "row_count": 1,
            "metric_sum": None,
            "metric_mean": None,
            "invalid_metric_rows": 1,
        },
    ]
    assert database.is_file()
    with analytics._connection(database) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)


def test_duckdb_supports_in_memory_and_empty_files(tmp_path):
    pytest.importorskip("duckdb")
    with analytics._connection() as connection:
        assert connection.execute("SELECT 40 + 2").fetchone() == (42,)

    source = _csv(tmp_path / "empty.csv", [])
    result = analytics.inspect_file(source)
    assert result["row_count"] == 0
    assert result["columns"] == [
        {"name": "Region", "type": "VARCHAR"},
        {"name": "Revenue", "type": "VARCHAR"},
    ]


def test_duckdb_reads_parquet_and_bounds_group_results(tmp_path):
    pytest.importorskip("duckdb")
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    source = tmp_path / "data.parquet"
    parquet.write_table(
        pa.table({"site": ["north", "north", "south"], "amount": [1, 2, 4]}),
        source,
    )
    result = analytics.summarize_file(
        source,
        group_by="site",
        metric="amount",
        limit=2,
    )
    assert result["format"] == "parquet"
    assert [row["group_value"] for row in result["groups"]] == ["north", "south"]

    with pytest.raises(ValueError, match="exceeds the 1-group limit"):
        analytics.summarize_file(source, group_by="site", limit=1)


def test_duckdb_reports_invalid_parquet(tmp_path):
    pytest.importorskip("duckdb")
    source = tmp_path / "corrupt.parquet"
    source.write_bytes(b"not parquet")

    with pytest.raises(ValueError, match="Could not inspect analytics source"):
        analytics.inspect_file(source)


def test_duckdb_validates_columns_and_summary_arguments(tmp_path):
    pytest.importorskip("duckdb")
    source = _csv(tmp_path / "source.csv", [["north", 2]])

    with pytest.raises(ValueError, match="Group column does not exist"):
        analytics.summarize_file(source, group_by="missing")
    with pytest.raises(ValueError, match="metric requires group_by"):
        analytics.summarize_file(source, metric="Revenue")
