"""Local SQLite catalog metadata and dataset lineage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect(database: Path) -> sqlite3.Connection:
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS dataset_catalog (
            dataset_id TEXT PRIMARY KEY,
            dataset_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT,
            source_type TEXT NOT NULL,
            source_uri TEXT,
            file_format TEXT,
            file_size_bytes INTEGER CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
            row_count INTEGER CHECK (row_count IS NULL OR row_count >= 0),
            column_count INTEGER CHECK (column_count IS NULL OR column_count >= 0),
            schema_json TEXT,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            checksum_sha256 TEXT,
            quality_status TEXT NOT NULL DEFAULT 'UNKNOWN'
                CHECK (quality_status IN ('PASS', 'WARNING', 'FAIL', 'UNKNOWN')),
            quality_report_json TEXT,
            license_identifier TEXT,
            attribution TEXT,
            pipeline_run_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dataset_catalog_name
            ON dataset_catalog(name, updated_at_utc);
        CREATE INDEX IF NOT EXISTS idx_dataset_catalog_pipeline
            ON dataset_catalog(pipeline_run_id);
        CREATE TABLE IF NOT EXISTS dataset_lineage (
            lineage_id TEXT PRIMARY KEY,
            parent_dataset_id TEXT NOT NULL REFERENCES dataset_catalog(dataset_id),
            child_dataset_id TEXT NOT NULL REFERENCES dataset_catalog(dataset_id),
            pipeline_run_id TEXT,
            operation TEXT NOT NULL,
            transformation_config_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            UNIQUE(parent_dataset_id, child_dataset_id, pipeline_run_id),
            CHECK (parent_dataset_id <> child_dataset_id)
        );
        CREATE INDEX IF NOT EXISTS idx_dataset_lineage_parent
            ON dataset_lineage(parent_dataset_id);
        CREATE INDEX IF NOT EXISTS idx_dataset_lineage_child
            ON dataset_lineage(child_dataset_id);
        """
    )


def initialize_catalog_schema(database: Path) -> None:
    """Create catalog tables additively without altering existing application tables."""
    with _connect(database) as connection:
        _create_schema(connection)


def _schema_json(schema: Any) -> str | None:
    if schema is None:
        return None
    try:
        return json.dumps(schema, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Dataset schema is not JSON serializable: {exc}") from exc


def _insert_or_update(
    connection: sqlite3.Connection,
    *,
    dataset_id: str,
    dataset_key: str,
    name: str,
    description: str | None,
    source_type: str,
    source_uri: str | None,
    file_format: str | None,
    file_size_bytes: int | None,
    row_count: int | None,
    column_count: int | None,
    schema_json: str | None,
    checksum_sha256: str | None,
    quality_status: str,
    quality_report_json: str | None,
    license_identifier: str | None,
    attribution: str | None,
    pipeline_run_id: str | None,
) -> None:
    now = _now()
    connection.execute(
        """
        INSERT INTO dataset_catalog (
            dataset_id, dataset_key, name, description, source_type, source_uri,
            file_format, file_size_bytes, row_count, column_count, schema_json,
            created_at_utc, updated_at_utc, checksum_sha256, quality_status,
            quality_report_json, license_identifier, attribution, pipeline_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset_key) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            source_type = excluded.source_type,
            source_uri = excluded.source_uri,
            file_format = excluded.file_format,
            file_size_bytes = excluded.file_size_bytes,
            row_count = excluded.row_count,
            column_count = excluded.column_count,
            schema_json = excluded.schema_json,
            updated_at_utc = excluded.updated_at_utc,
            checksum_sha256 = excluded.checksum_sha256,
            quality_status = excluded.quality_status,
            quality_report_json = excluded.quality_report_json,
            license_identifier = excluded.license_identifier,
            attribution = excluded.attribution,
            pipeline_run_id = excluded.pipeline_run_id
        """,
        (
            dataset_id,
            dataset_key,
            name,
            description,
            source_type,
            source_uri,
            file_format,
            file_size_bytes,
            row_count,
            column_count,
            schema_json,
            now,
            now,
            checksum_sha256,
            quality_status,
            quality_report_json,
            license_identifier,
            attribution,
            pipeline_run_id,
        ),
    )


def _stable_id(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _checksum(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_ingestion(
    connection: sqlite3.Connection,
    *,
    sources: list[Path],
    destination: str,
    input_format: str,
    run_id: str,
    source_row_counts: list[int],
    accepted_rows: int,
    schema: list[dict[str, Any]],
    source_schemas: list[list[dict[str, Any]]],
    output_checksum: str,
    quality_status: str,
    transformation_config: dict[str, Any],
) -> None:
    """Register ingestion inputs, output table, and their lineage in its transaction."""
    child_key = f"sqlite-table:{destination}"
    child_id = _stable_id(child_key)
    _insert_or_update(
        connection,
        dataset_id=child_id,
        dataset_key=child_key,
        name=destination,
        description=None,
        source_type="sqlite_table",
        source_uri=child_key,
        file_format="sqlite",
        file_size_bytes=None,
        row_count=accepted_rows,
        column_count=len(schema),
        schema_json=_schema_json(
            [
                {"name": column["normalized_name"], "type": "TEXT"}
                for column in schema
            ]
        ),
        checksum_sha256=output_checksum,
        quality_status=quality_status,
        quality_report_json=None,
        license_identifier=None,
        attribution=None,
        pipeline_run_id=run_id,
    )

    for source, source_row_count, file_schema in zip(
        sources,
        source_row_counts,
        source_schemas,
        strict=True,
    ):
        source = Path(source)
        source_uri = source.resolve().as_uri()
        source_key = f"file:{source_uri}"
        source_id = _stable_id(source_key)
        _insert_or_update(
            connection,
            dataset_id=source_id,
            dataset_key=source_key,
            name=source.name,
            description=None,
            source_type="local_file",
            source_uri=source_uri,
            file_format=input_format,
            file_size_bytes=source.stat().st_size,
            row_count=source_row_count,
            column_count=len(file_schema),
            schema_json=_schema_json(file_schema),
            checksum_sha256=_checksum(source),
            quality_status=quality_status,
            quality_report_json=None,
            license_identifier=None,
            attribution=None,
            pipeline_run_id=run_id,
        )
        connection.execute(
            """
            INSERT INTO dataset_lineage (
                lineage_id, parent_dataset_id, child_dataset_id, pipeline_run_id,
                operation, transformation_config_json, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(parent_dataset_id, child_dataset_id, pipeline_run_id) DO NOTHING
            """,
            (
                _stable_id(f"lineage:{run_id}:{source_id}:{child_id}"),
                source_id,
                child_id,
                run_id,
                f"{input_format}_ingestion",
                json.dumps(transformation_config, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )


def register_dataset(
    database: Path,
    *,
    dataset_key: str,
    name: str,
    source_type: str,
    description: str | None = None,
    source_uri: str | None = None,
    file_format: str | None = None,
    file_size_bytes: int | None = None,
    row_count: int | None = None,
    column_count: int | None = None,
    schema: Any = None,
    checksum_sha256: str | None = None,
    quality_status: str = "UNKNOWN",
    license_identifier: str | None = None,
    attribution: str | None = None,
) -> dict[str, Any]:
    """Register a dataset once; duplicate keys are rejected rather than overwritten."""
    for label, value in (
        ("dataset_key", dataset_key),
        ("name", name),
        ("source_type", source_type),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string.")
    for label, value in (
        ("file_size_bytes", file_size_bytes),
        ("row_count", row_count),
        ("column_count", column_count),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"{label} must be a non-negative integer.")
    if quality_status not in {"PASS", "WARNING", "FAIL", "UNKNOWN"}:
        raise ValueError("quality_status must be PASS, WARNING, FAIL, or UNKNOWN.")
    if checksum_sha256 is not None and (
        len(checksum_sha256) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in checksum_sha256)
    ):
        raise ValueError("checksum_sha256 must contain exactly 64 hexadecimal characters.")
    schema_json = _schema_json(schema)
    dataset_id = str(uuid.uuid4())
    initialize_catalog_schema(database)
    with _connect(database) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM dataset_catalog WHERE dataset_key = ?",
                (dataset_key,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Dataset key is already registered: {dataset_key}")
            _insert_or_update(
                connection,
                dataset_id=dataset_id,
                dataset_key=dataset_key,
                name=name,
                description=description,
                source_type=source_type,
                source_uri=source_uri,
                file_format=file_format,
                file_size_bytes=file_size_bytes,
                row_count=row_count,
                column_count=column_count,
                schema_json=schema_json,
                checksum_sha256=checksum_sha256,
                quality_status=quality_status,
                quality_report_json=None,
                license_identifier=license_identifier,
                attribution=attribution,
                pipeline_run_id=None,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Dataset key is already registered: {dataset_key}") from exc
    return inspect_dataset(database, dataset_id)


def record_quality_report(
    database: Path,
    *,
    source: Path,
    file_format: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    """Store a validation result and source metadata without changing file contents."""
    source = Path(source)
    initialize_catalog_schema(database)
    source_uri = source.resolve().as_uri()
    dataset_key = f"file:{source_uri}"
    dataset_id = _stable_id(dataset_key)
    with _connect(database) as connection:
        _insert_or_update(
            connection,
            dataset_id=dataset_id,
            dataset_key=dataset_key,
            name=source.name,
            description=None,
            source_type="local_file",
            source_uri=source_uri,
            file_format=file_format,
            file_size_bytes=source.stat().st_size,
            row_count=report["input_rows"],
            column_count=len(report["schema"]),
            schema_json=_schema_json(report["schema"]),
            checksum_sha256=_checksum(source),
            quality_status=report["status"],
            quality_report_json=json.dumps(report, ensure_ascii=False, sort_keys=True),
            license_identifier=None,
            attribution=None,
            pipeline_run_id=None,
        )
    return inspect_dataset(database, dataset_id)


def list_datasets(database: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
        raise ValueError("limit must be an integer between 1 and 1000.")
    if not Path(database).is_file():
        return []
    initialize_catalog_schema(database)
    with _connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT * FROM dataset_catalog
            ORDER BY name COLLATE NOCASE, dataset_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_decode_dataset(dict(row)) for row in rows]


def _decode_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    for field in ("schema_json", "quality_report_json"):
        encoded = dataset.pop(field)
        dataset[field.removesuffix("_json")] = json.loads(encoded) if encoded else None
    return dataset


def inspect_dataset(database: Path, dataset_id: str) -> dict[str, Any]:
    if not Path(database).is_file():
        raise FileNotFoundError(f"Catalog database does not exist: {database}")
    initialize_catalog_schema(database)
    with _connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM dataset_catalog WHERE dataset_id = ? OR dataset_key = ?",
            (dataset_id, dataset_id),
        ).fetchone()
    if row is None:
        raise ValueError(f"Dataset is not registered: {dataset_id}")
    return _decode_dataset(dict(row))


def trace_lineage(database: Path, dataset_id: str) -> dict[str, Any]:
    if not Path(database).is_file():
        raise FileNotFoundError(f"Catalog database does not exist: {database}")
    initialize_catalog_schema(database)
    with _connect(database) as connection:
        connection.row_factory = sqlite3.Row
        root = connection.execute(
            "SELECT dataset_id FROM dataset_catalog WHERE dataset_id = ? OR dataset_key = ?",
            (dataset_id, dataset_id),
        ).fetchone()
        if root is None:
            raise ValueError(f"Dataset is not registered: {dataset_id}")
        root_id = root["dataset_id"]
        rows = connection.execute(
            """
            WITH RECURSIVE connected(dataset_id) AS (
                SELECT ?
                UNION
                SELECT CASE
                    WHEN lineage.parent_dataset_id = connected.dataset_id
                    THEN lineage.child_dataset_id
                    ELSE lineage.parent_dataset_id
                END
                FROM dataset_lineage AS lineage
                JOIN connected ON (
                    lineage.parent_dataset_id = connected.dataset_id
                    OR lineage.child_dataset_id = connected.dataset_id
                )
            )
            SELECT DISTINCT catalog.*
            FROM connected
            JOIN dataset_catalog AS catalog USING (dataset_id)
            ORDER BY catalog.name COLLATE NOCASE, catalog.dataset_id
            """,
            (root_id,),
        ).fetchall()
        edges = connection.execute(
            """
            SELECT lineage_id, parent_dataset_id, child_dataset_id, pipeline_run_id,
                operation, transformation_config_json, created_at_utc
            FROM dataset_lineage
            WHERE parent_dataset_id IN (
                WITH RECURSIVE connected(dataset_id) AS (
                    SELECT ?
                    UNION
                    SELECT CASE
                        WHEN lineage.parent_dataset_id = connected.dataset_id
                        THEN lineage.child_dataset_id
                        ELSE lineage.parent_dataset_id
                    END
                    FROM dataset_lineage AS lineage
                    JOIN connected ON (
                        lineage.parent_dataset_id = connected.dataset_id
                        OR lineage.child_dataset_id = connected.dataset_id
                    )
                )
                SELECT dataset_id FROM connected
            )
            ORDER BY created_at_utc, lineage_id
            """,
            (root_id,),
        ).fetchall()
    return {
        "root_dataset_id": root_id,
        "datasets": [_decode_dataset(dict(row)) for row in rows],
        "edges": [
            {
                **dict(edge),
                "transformation_config": json.loads(edge["transformation_config_json"]),
            }
            for edge in edges
        ],
    }
