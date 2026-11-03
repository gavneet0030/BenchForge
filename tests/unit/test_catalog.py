import csv
import hashlib
import sqlite3

import pytest

from benchforge.catalog import (
    initialize_catalog_schema,
    inspect_dataset,
    list_datasets,
    record_quality_report,
    register_dataset,
    trace_lineage,
)
from benchforge.pipeline import ingest_csv, initialize_pipeline_schema
from benchforge.quality import QualityRules, validate_file


def _csv(path, rows):
    path.write_text(
        "Region,Value\n" + "".join(f"{region},{value}\n" for region, value in rows),
        encoding="utf-8",
    )
    return path


def test_catalog_registers_metadata_and_rejects_duplicate_keys(tmp_path):
    database = tmp_path / "catalog.sqlite"
    checksum = hashlib.sha256(b"example").hexdigest()

    registered = register_dataset(
        database,
        dataset_key="example:retail",
        name="Retail Example",
        description="A local validation fixture.",
        source_type="local_file",
        source_uri="file:///example.csv",
        file_format="csv",
        file_size_bytes=7,
        row_count=2,
        column_count=2,
        schema=[{"name": "region", "type": "string"}],
        checksum_sha256=checksum,
        license_identifier="CC-BY-4.0",
        attribution="Example source",
    )

    assert registered["dataset_key"] == "example:retail"
    assert registered["schema"] == [{"name": "region", "type": "string"}]
    assert registered["checksum_sha256"] == checksum
    assert registered["license_identifier"] == "CC-BY-4.0"
    assert list_datasets(database)[0]["dataset_id"] == registered["dataset_id"]
    assert inspect_dataset(database, "example:retail")["name"] == "Retail Example"
    with pytest.raises(ValueError, match="already registered"):
        register_dataset(
            database,
            dataset_key="example:retail",
            name="Duplicate",
            source_type="unknown",
        )


def test_ingestion_records_source_output_schema_checksums_and_lineage(tmp_path):
    database = tmp_path / "history.sqlite"
    source = _csv(tmp_path / "source.csv", [("north", 2), ("south", 4)])

    result = ingest_csv(database, source, table="summary_source")
    datasets = list_datasets(database)
    source_record = next(item for item in datasets if item["name"] == "source.csv")
    output_record = next(item for item in datasets if item["name"] == "summary_source")
    trace = trace_lineage(database, source_record["dataset_id"])

    assert source_record["checksum_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert source_record["row_count"] == 2
    assert source_record["column_count"] == 2
    assert source_record["quality_status"] == "PASS"
    assert output_record["pipeline_run_id"] == result["run_id"]
    assert output_record["row_count"] == 2
    assert output_record["checksum_sha256"]
    assert output_record["schema"] == [
        {"name": "region", "type": "TEXT"},
        {"name": "value", "type": "TEXT"},
    ]
    assert len(trace["edges"]) == 1
    assert trace["edges"][0]["pipeline_run_id"] == result["run_id"]
    assert trace["edges"][0]["transformation_config"]["input_format"] == "csv"
    assert {item["dataset_id"] for item in trace["datasets"]} == {
        source_record["dataset_id"],
        output_record["dataset_id"],
    }


def test_quality_report_updates_catalog_without_modifying_source(tmp_path):
    database = tmp_path / "catalog.sqlite"
    source = _csv(tmp_path / "quality.csv", [("north", "bad")])
    original = source.read_bytes()
    report = validate_file(
        source,
        rules=QualityRules(numeric_ranges={"Value": (0, 10)}),
    )

    recorded = record_quality_report(
        database,
        source=source,
        file_format=report["format"],
        report=report,
    )

    assert recorded["quality_status"] == "FAIL"
    assert recorded["quality_report"]["status"] == "FAIL"
    assert recorded["checksum_sha256"] == hashlib.sha256(original).hexdigest()
    assert source.read_bytes() == original


def test_catalog_migration_preserves_existing_pipeline_records(tmp_path):
    database = tmp_path / "legacy.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE benchmark_samples (id INTEGER PRIMARY KEY, workload TEXT)"
        )
        connection.execute(
            "INSERT INTO benchmark_samples (workload) VALUES ('preserved')"
        )

    initialize_pipeline_schema(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT workload FROM benchmark_samples"
        ).fetchone() == ("preserved",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'dataset_catalog'"
        ).fetchone() == ("dataset_catalog",)


def test_failed_ingestion_does_not_leave_catalog_or_partial_destination(tmp_path):
    database = tmp_path / "history.sqlite"
    original = _csv(tmp_path / "original.csv", [("preserved", 7)])
    ingest_csv(database, original, table="events")
    corrupt = tmp_path / "corrupt.csv"
    corrupt.write_text('region,value\nnew,"unterminated\n', encoding="utf-8")

    with pytest.raises(csv.Error):
        ingest_csv(database, corrupt, table="events")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT region, value FROM events").fetchall() == [
            ("preserved", "7")
        ]
    assert not any(item["name"] == "corrupt.csv" for item in list_datasets(database))


def test_catalog_rejects_invalid_checksum_and_unknown_dataset(tmp_path):
    database = tmp_path / "catalog.sqlite"
    with pytest.raises(ValueError, match="64 hexadecimal"):
        register_dataset(
            database,
            dataset_key="invalid",
            name="Invalid",
            source_type="local_file",
            checksum_sha256="not-a-checksum",
        )
    initialize_catalog_schema(database)
    with pytest.raises(ValueError, match="not registered"):
        inspect_dataset(database, "missing")
