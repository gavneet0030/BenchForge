import csv
import json
import sqlite3

import pytest
from typer.testing import CliRunner

from benchforge.cli import app
from benchforge.pipeline import (
    aggregate_table,
    get_pipeline_runs,
    ingest_csv,
    ingest_file,
    normalize_csv_files,
)

runner = CliRunner()


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def test_csv_ingestion_profiles_data_and_persists_metrics_quality_and_lineage(tmp_path):
    database = tmp_path / "benchforge.sqlite"
    source = write_csv(
        tmp_path / "sales.csv",
        ["Region Name", "Amount", "Customer ID"],
        [
            ["North", "12.5", "c-1"],
            ["South", "8", "c-2"],
            ["North", "12.5", "c-1"],
            ["", "", "c-3"],
        ],
    )

    result = ingest_csv(
        database,
        source,
        table="sales",
        required_columns=["Region Name", "Amount"],
    )

    assert result["status"] == "succeeded"
    assert result["input_rows"] == 4
    assert result["accepted_rows"] == result["output_rows"] == 4
    assert result["rejected_rows"] == 0
    assert result["rows_per_second"] > 0
    profile = {column["column"]: column for column in result["profile"]}
    assert profile["region_name"]["inferred_type"] == "string"
    assert profile["amount"]["inferred_type"] == "number"
    assert profile["amount"]["missing_count"] == 1
    assert profile["amount"]["mean"] == pytest.approx(11)
    assert next(item for item in result["quality"] if item["name"] == "no_duplicate_rows")["passed"] is False

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 4
        lineage = connection.execute(
            "SELECT source_name, destination, transformations_json "
            "FROM pipeline_lineage WHERE run_id = ?",
            (result["run_id"],),
        ).fetchone()
    assert lineage[0] == "sales.csv"
    assert lineage[1] == "sales"
    assert "normalize_headers" in json.loads(lineage[2])
    assert get_pipeline_runs(database)[0]["run_id"] == result["run_id"]


def test_multi_file_normalization_aligns_header_order_and_rejects_bad_schema(tmp_path):
    database = tmp_path / "history.sqlite"
    first = write_csv(
        tmp_path / "first.csv", ["Region Name", "Amount"], [["North", "1"]]
    )
    second = write_csv(
        tmp_path / "second.csv", ["amount", "region-name"], [["2", "South"]]
    )

    result = normalize_csv_files(database, [first, second], table="combined")

    assert result["input_rows"] == result["output_rows"] == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT region_name, amount FROM combined ORDER BY region_name"
        ).fetchall() == [("North", "1"), ("South", "2")]

    incompatible = write_csv(
        tmp_path / "incompatible.csv", ["Region", "Other"], [["East", "3"]]
    )
    with pytest.raises(ValueError, match="matching normalized schemas"):
        normalize_csv_files(database, [first, incompatible], table="combined")
    assert get_pipeline_runs(database)[0]["status"] == "failed"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM combined").fetchone()[0] == 2


def test_aggregation_creates_summary_and_rejects_injection_or_nonnumeric_metric(tmp_path):
    database = tmp_path / "history.sqlite"
    source = write_csv(
        tmp_path / "orders.csv",
        ["Region", "Revenue"],
        [["North", "10"], ["North", "20"], ["South", "5"]],
    )
    ingest_csv(database, source, table="orders")

    result = aggregate_table(
        database,
        "orders",
        group_by="region",
        metric="revenue",
        destination="regional_summary",
    )

    assert result["output_rows"] == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT group_key, row_count, metric_sum, metric_mean "
            "FROM regional_summary ORDER BY group_key"
        ).fetchall() == [("North", 2, 30.0, 15.0), ("South", 1, 5.0, 5.0)]

    with pytest.raises(ValueError, match="lowercase letters"):
        aggregate_table(
            database,
            'orders"; DROP TABLE orders;--',
            group_by="region",
            metric="revenue",
        )
    assert get_pipeline_runs(database)[0]["pipeline_name"] == "analytical_aggregation"

    overflow = write_csv(
        tmp_path / "overflow.csv",
        ["Region", "Revenue"],
        [["North", "1e308"], ["North", "1e308"]],
    )
    ingest_csv(database, overflow, table="overflow_orders")
    with pytest.raises(ValueError, match="numeric range"):
        aggregate_table(
            database,
            "overflow_orders",
            group_by="region",
            metric="revenue",
            destination="regional_summary",
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT group_key, metric_sum FROM regional_summary ORDER BY group_key"
        ).fetchall() == [("North", 30.0), ("South", 5.0)]

    invalid = write_csv(
        tmp_path / "invalid.csv", ["Region", "Revenue"], [["North", "unknown"]]
    )
    ingest_csv(database, invalid, table="bad_orders")
    with pytest.raises(ValueError, match="non-numeric"):
        aggregate_table(
            database, "bad_orders", group_by="region", metric="revenue"
        )
    assert get_pipeline_runs(database)[0]["status"] == "failed"


def test_schema_and_resource_failures_preserve_existing_destination(tmp_path, monkeypatch):
    from benchforge import pipeline

    database = tmp_path / "history.sqlite"
    original = write_csv(
        tmp_path / "original.csv", ["Kind", "Value"], [["old", "1"]]
    )
    ingest_csv(database, original, table="events")
    invalid_schema = write_csv(
        tmp_path / "missing-column.csv", ["Other"], [["new"]]
    )
    with pytest.raises(ValueError, match="missing required columns"):
        ingest_csv(
            database,
            invalid_schema,
            table="events",
            required_columns=["kind"],
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT kind, value FROM events").fetchall() == [
            ("old", "1")
        ]
    assert get_pipeline_runs(database)[0]["status"] == "failed"

    too_many_rows = write_csv(
        tmp_path / "too-many.csv",
        ["Kind", "Value"],
        [["a", "1"], ["b", "2"]],
    )
    monkeypatch.setattr(pipeline, "MAX_INPUT_ROWS", 1)
    with pytest.raises(ValueError, match="row limit"):
        ingest_csv(database, too_many_rows, table="events")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT kind, value FROM events").fetchall() == [
            ("old", "1")
        ]
    assert get_pipeline_runs(database)[0]["status"] == "failed"


def test_ingestion_duplicate_policies_ranges_and_repeatable_replacement(tmp_path):
    database = tmp_path / "history.sqlite"
    source = write_csv(
        tmp_path / "measurements.csv",
        ["Site", "Value"],
        [
            ["north", "5"],
            ["north", "5"],
            ["south", "-1"],
            ["east", "not-a-number"],
        ],
    )

    kept = ingest_csv(database, source, table="measurements")
    assert kept["output_rows"] == 4
    assert next(
        item for item in kept["quality"] if item["name"] == "no_duplicate_rows"
    )["passed"] is False

    dropped = ingest_csv(
        database,
        source,
        table="measurements",
        duplicate_policy="drop",
        numeric_ranges={"value": (0, 10)},
    )
    assert dropped["input_rows"] == 4
    assert dropped["accepted_rows"] == dropped["output_rows"] == 1
    assert dropped["rejected_rows"] == 3
    checks = {item["name"]: item for item in dropped["quality"]}
    assert checks["numeric_ranges"]["passed"] is False
    assert checks["duplicate_policy"]["details"]["policy"] == "drop"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT site, value FROM measurements"
        ).fetchall() == [("north", "5")]
        assert connection.execute(
            "SELECT COUNT(*) FROM pipeline_runs WHERE status = 'succeeded'"
        ).fetchone()[0] == 2

    with pytest.raises(ValueError, match="Duplicate CSV row"):
        ingest_csv(database, source, table="measurements", duplicate_policy="error")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT site, value FROM measurements"
        ).fetchall() == [("north", "5")]
    assert get_pipeline_runs(database)[0]["status"] == "failed"


def test_ingestion_counts_malformed_rows_and_enforces_file_size_limit(tmp_path, monkeypatch):
    from benchforge import pipeline

    database = tmp_path / "history.sqlite"
    source = tmp_path / "ragged.csv"
    source.write_text("kind,value\na,1\nb,2,extra\nc,3\n", encoding="utf-8")
    result = ingest_csv(database, source, table="events")
    assert result["input_rows"] == 3
    assert result["accepted_rows"] == result["output_rows"] == 2
    assert result["rejected_rows"] == 1
    assert next(
        check for check in result["quality"] if check["name"] == "all_rows_accepted"
    )["passed"] is False

    monkeypatch.setattr(pipeline, "MAX_INPUT_BYTES", 1)
    with pytest.raises(ValueError, match="byte limit"):
        ingest_csv(database, source, table="events")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_json_and_jsonl_ingestion_normalize_records_and_reject_schema_drift(tmp_path):
    database = tmp_path / "history.sqlite"
    json_source = tmp_path / "events.json"
    json_source.write_text(
        '[{"Kind":"a","Value":2},{"Value":4,"Kind":"b"}]',
        encoding="utf-8",
    )

    json_result = ingest_file(
        database,
        json_source,
        table="json_events",
        required_columns=["Kind", "Value"],
        numeric_ranges={"Value": (0, 4)},
    )
    assert json_result["pipeline_name"] == "json_ingestion"
    assert json_result["input_rows"] == json_result["output_rows"] == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT kind, value FROM json_events ORDER BY kind"
        ).fetchall() == [("a", "2"), ("b", "4")]

    jsonl_source = tmp_path / "events.jsonl"
    jsonl_source.write_text(
        '{"Kind":"a","Value":3}\n\n{"Kind":"b"}\n',
        encoding="utf-8",
    )
    jsonl_result = ingest_file(database, jsonl_source, table="jsonl_events")
    assert jsonl_result["pipeline_name"] == "jsonl_ingestion"
    assert jsonl_result["input_rows"] == 2
    assert jsonl_result["accepted_rows"] == jsonl_result["output_rows"] == 1
    assert jsonl_result["rejected_rows"] == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT kind, value FROM jsonl_events"
        ).fetchall() == [("a", "3")]


def test_parquet_ingestion_uses_optional_dataset_dependency(tmp_path):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    database = tmp_path / "history.sqlite"
    source = tmp_path / "readings.parquet"
    parquet.write_table(
        pa.table({"Site": ["north", "south"], "Value": [2.5, 4.0]}),
        source,
    )
    result = ingest_file(database, source, table="parquet_readings")
    assert result["pipeline_name"] == "parquet_ingestion"
    assert result["input_rows"] == result["output_rows"] == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT site, value FROM parquet_readings ORDER BY site"
        ).fetchall() == [("north", "2.5"), ("south", "4.0")]


def test_parquet_ingestion_projects_columns_and_preserves_input_order(tmp_path):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    source = tmp_path / "projected.parquet"
    parquet.write_table(
        pa.table({"Site": ["north", "south"], "Unused": ["x" * 100, "y" * 100], "Value": [2, 4]}),
        source,
    )

    result = ingest_file(
        tmp_path / "history.sqlite",
        source,
        table="projected",
        columns=["Value", "Site"],
    )

    assert [item["column"] for item in result["profile"]] == ["value", "site"]
    with sqlite3.connect(tmp_path / "history.sqlite") as connection:
        assert connection.execute("PRAGMA table_info(projected)").fetchall()[1][1] == "site"
        assert connection.execute(
            "SELECT value, site FROM projected ORDER BY value"
        ).fetchall() == [("2", "north"), ("4", "south")]


def test_parquet_ingestion_processes_multiple_batches(tmp_path):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    database = tmp_path / "history.sqlite"
    source = tmp_path / "multiple-batches.parquet"
    parquet.write_table(
        pa.table({"Row Number": list(range(1_001)), "Label": ["sample"] * 1_001}),
        source,
        row_group_size=257,
    )

    result = ingest_file(database, source, table="parquet_batches")

    assert result["input_rows"] == result["output_rows"] == 1_001
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*), MIN(CAST(row_number AS INTEGER)), "
            "MAX(CAST(row_number AS INTEGER)) FROM parquet_batches"
        ).fetchone() == (1_001, 0, 1_000)


def test_parquet_empty_table_is_ingested_as_zero_rows(tmp_path):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    database = tmp_path / "history.sqlite"
    source = tmp_path / "empty.parquet"
    parquet.write_table(
        pa.table({
            "Kind": pa.array([], type=pa.string()),
            "Value": pa.array([], type=pa.int64()),
        }),
        source,
    )

    result = ingest_file(database, source, table="empty_parquet")

    assert result["status"] == "succeeded"
    assert result["input_rows"] == result["output_rows"] == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM empty_parquet").fetchone()[0] == 0


def test_corrupt_parquet_preserves_existing_destination(tmp_path):
    database = tmp_path / "history.sqlite"
    original = write_csv(
        tmp_path / "original.csv", ["Kind", "Value"], [["preserved", "7"]]
    )
    ingest_csv(database, original, table="events")
    corrupt = tmp_path / "corrupt.parquet"
    corrupt.write_bytes(b"not a parquet file")

    with pytest.raises(ValueError, match="Could not open Parquet source"):
        ingest_file(database, corrupt, table="events")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT kind, value FROM events").fetchall() == [
            ("preserved", "7")
        ]
    assert get_pipeline_runs(database)[0]["status"] == "failed"


def test_parquet_missing_optional_dependency_is_reported(tmp_path, monkeypatch):
    import builtins

    pytest.importorskip("pyarrow")
    database = tmp_path / "history.sqlite"
    source = tmp_path / "missing-extra.parquet"
    source.write_bytes(b"parquet placeholder")
    original_import = builtins.__import__

    def without_pyarrow(name, *args, **kwargs):
        if name == "pyarrow":
            raise ImportError("simulated missing optional dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pyarrow)

    with pytest.raises(ValueError, match="optional 'datasets' dependencies"):
        ingest_file(database, source, table="parquet_without_extra")

    assert get_pipeline_runs(database)[0]["status"] == "failed"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'parquet_without_extra'"
        ).fetchone() is None


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "message"),
    [
        ("MAX_INPUT_BYTES", 1, "byte limit"),
        ("MAX_COLUMNS", 1, "columns"),
        ("MAX_INPUT_ROWS", 1, "row limit"),
    ],
)
def test_parquet_limits_preserve_existing_destination(
    tmp_path, monkeypatch, limit_name, limit_value, message
):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    from benchforge import pipeline

    database = tmp_path / "history.sqlite"
    original = write_csv(
        tmp_path / "original.csv", ["Kind", "Value"], [["preserved", "7"]]
    )
    ingest_csv(database, original, table="events")
    source = tmp_path / "limited.parquet"
    parquet.write_table(
        pa.table({"Kind": ["one", "two"], "Value": [1, 2]}),
        source,
    )
    monkeypatch.setattr(pipeline, limit_name, limit_value)

    with pytest.raises(ValueError, match=message):
        ingest_file(database, source, table="events")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT kind, value FROM events").fetchall() == [
            ("preserved", "7")
        ]
    assert get_pipeline_runs(database)[0]["status"] == "failed"


def test_parquet_expanded_size_limit_preserves_existing_destination(
    tmp_path, monkeypatch
):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    from benchforge import pipeline

    database = tmp_path / "history.sqlite"
    original = write_csv(
        tmp_path / "original.csv", ["Kind", "Value"], [["preserved", "7"]]
    )
    ingest_csv(database, original, table="events")
    source = tmp_path / "compressed.parquet"
    parquet.write_table(
        pa.table({"Label": ["x" * 20_000]}),
        source,
        compression="gzip",
    )
    limit = source.stat().st_size
    assert limit < 20_000
    monkeypatch.setattr(pipeline, "MAX_INPUT_BYTES", limit)

    with pytest.raises(ValueError, match="expanded data"):
        ingest_file(database, source, table="events")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT kind, value FROM events").fetchall() == [
            ("preserved", "7")
        ]
    assert get_pipeline_runs(database)[0]["status"] == "failed"


def test_parquet_cell_limit_rejects_oversized_values(tmp_path, monkeypatch):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    from benchforge import pipeline

    source = tmp_path / "long-cell.parquet"
    parquet.write_table(pa.table({"Label": ["safe", "too-long"]}), source)
    monkeypatch.setattr(pipeline, "MAX_CELL_CHARS", 4)

    result = ingest_file(
        tmp_path / "history.sqlite",
        source,
        table="bounded_cells",
    )

    assert result["input_rows"] == 2
    assert result["accepted_rows"] == result["output_rows"] == 1
    assert result["rejected_rows"] == 1


def test_parquet_iteration_failure_rolls_back_inserted_batches(
    tmp_path, monkeypatch
):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    database = tmp_path / "history.sqlite"
    original = write_csv(
        tmp_path / "original.csv", ["Kind", "Value"], [["preserved", "7"]]
    )
    ingest_csv(database, original, table="events")
    source = tmp_path / "interrupted.parquet"
    source.write_bytes(b"parquet placeholder")
    batch = pa.record_batch(
        [pa.array(["new"] * 1_000), pa.array([1] * 1_000)],
        names=["Kind", "Value"],
    )

    class InterruptedParquetFile:
        schema_arrow = batch.schema

        def __init__(self, path):
            assert path == source

        def iter_batches(self, *, batch_size, columns):
            assert batch_size == 1_000
            assert columns == ["Kind", "Value"]
            yield batch
            raise pa.ArrowInvalid("simulated mid-stream read failure")

    monkeypatch.setattr(parquet, "ParquetFile", InterruptedParquetFile)

    with pytest.raises(ValueError, match="Could not read Parquet source"):
        ingest_file(database, source, table="events")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT kind, value FROM events").fetchall() == [
            ("preserved", "7")
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
            "AND name GLOB 'stage_*'"
        ).fetchone()[0] == 0
    assert get_pipeline_runs(database)[0]["status"] == "failed"


def test_parquet_unicode_headers_are_normalized(tmp_path):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    source = tmp_path / "unicode.parquet"
    parquet.write_table(pa.table({"Café": ["value"], "区域": ["north"]}), source)

    result = ingest_file(
        tmp_path / "history.sqlite",
        source,
        table="unicode_headers",
    )

    assert result["output_rows"] == 1
    profile_names = {column["column"] for column in result["profile"]}
    assert profile_names == {"caf", "column_2"}


def test_generic_ingest_cli_detects_json_and_reports_parse_errors(tmp_path):
    database = tmp_path / "history.sqlite"
    source = tmp_path / "events.json"
    source.write_text('[{"Kind":"a","Value":3}]', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "pipeline",
            "ingest",
            str(source),
            "--table",
            "events",
            "--database",
            str(database),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Pipeline: json_ingestion" in result.output
    assert "1 input / 1 accepted" in result.output

    source.write_text("[invalid JSON", encoding="utf-8")
    result = runner.invoke(
        app,
        ["pipeline", "ingest", str(source), "--database", str(database)],
    )
    assert result.exit_code != 0
    assert "Could not parse JSON source" in result.output


def test_generic_ingest_cli_projects_selected_columns(tmp_path):
    database = tmp_path / "history.sqlite"
    source = write_csv(
        tmp_path / "events.csv",
        ["Kind", "Unused", "Value"],
        [["a", "not stored", "3"]],
    )
    result = runner.invoke(
        app,
        [
            "pipeline",
            "ingest",
            str(source),
            "--table",
            "projected_events",
            "--database",
            str(database),
            "--column",
            "Value",
            "--column",
            "Kind",
        ],
    )

    assert result.exit_code == 0, result.output
    with sqlite3.connect(database) as connection:
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(projected_events)")
        ]
        assert columns == ["value", "kind"]
        assert connection.execute(
            "SELECT value, kind FROM projected_events"
        ).fetchall() == [("3", "a")]


def test_pipeline_cli_ingests_and_aggregates_local_data(tmp_path):
    database = tmp_path / "history.sqlite"
    source = write_csv(
        tmp_path / "events.csv",
        ["Kind", "Value"],
        [["a", "3"], ["b", "5"]],
    )
    ingest = runner.invoke(
        app,
        [
            "pipeline",
            "ingest-csv",
            str(source),
            "--table",
            "events",
            "--database",
            str(database),
            "--required-column",
            "Kind",
        ],
    )
    assert ingest.exit_code == 0, ingest.output
    assert "Quality PASS: required_columns_present" in ingest.output

    aggregate = runner.invoke(
        app,
        [
            "pipeline",
            "aggregate",
            "--source-table",
            "events",
            "--group-by",
            "kind",
            "--metric",
            "value",
            "--database",
            str(database),
        ],
    )
    assert aggregate.exit_code == 0, aggregate.output
    assert "analytical_aggregation" in aggregate.output
    assert "2 output" in aggregate.output

    export_path = tmp_path / "regional-summary.csv"
    export = runner.invoke(
        app,
        [
            "pipeline",
            "export-csv",
            "--table",
            "analytical_summary",
            "--database",
            str(database),
            "--output",
            str(export_path),
        ],
    )
    assert export.exit_code == 0, export.output
    with export_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert {row["group_key"]: float(row["metric_sum"]) for row in rows} == {
        "a": 3.0,
        "b": 5.0,
    }

    second = write_csv(
        tmp_path / "events-second.csv",
        ["Value", "Kind"],
        [["7", "c"]],
    )
    normalize = runner.invoke(
        app,
        [
            "pipeline",
            "normalize",
            str(source),
            str(second),
            "--table",
            "all_events",
            "--database",
            str(database),
        ],
    )
    assert normalize.exit_code == 0, normalize.output
    assert "multi_file_normalization" in normalize.output
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM all_events").fetchone()[0] == 3
