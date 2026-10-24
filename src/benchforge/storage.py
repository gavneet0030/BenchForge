"""SQLite persistence for reproducible benchmark measurements."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4


def initialize_database(database: Path) -> None:
    """Create or additively migrate the local benchmark history database."""
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                workload TEXT NOT NULL,
                sample_index INTEGER NOT NULL,
                elapsed_ns INTEGER NOT NULL CHECK (elapsed_ns > 0),
                extra_work INTEGER NOT NULL DEFAULT 0,
                os TEXT,
                python_version TEXT,
                schema_version INTEGER NOT NULL,
                run_id TEXT NOT NULL DEFAULT 'legacy',
                variant TEXT NOT NULL DEFAULT 'single',
                binary TEXT,
                config_json TEXT NOT NULL DEFAULT '{}',
                pair_index INTEGER,
                execution_order INTEGER,
                seed INTEGER
            )
        """)

        existing = {row[1] for row in connection.execute("PRAGMA table_info(benchmark_samples)")}

        migrations = {
            "run_id": "TEXT NOT NULL DEFAULT 'legacy'",
            "variant": "TEXT NOT NULL DEFAULT 'single'",
            "binary": "TEXT",
            "config_json": "TEXT NOT NULL DEFAULT '{}'",
            "pair_index": "INTEGER",
            "execution_order": "INTEGER",
            "seed": "INTEGER",
        }

        for column, declaration in migrations.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE benchmark_samples ADD COLUMN {column} {declaration}"
                )

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_samples_run_variant
            ON benchmark_samples (run_id, variant, sample_index)
        """)


def _prepare_sample_rows(
    records: list[dict[str, Any]],
    *,
    run_id: str,
    variant: str,
    config_json: str,
) -> list[tuple[Any, ...]]:
    """Validate a batch and prepare rows without writing to the database."""
    if not records:
        raise ValueError("Cannot save an empty sample batch.")

    if not isinstance(variant, str) or not variant.strip():
        raise ValueError("variant must be a non-empty string.")

    rows: list[tuple[Any, ...]] = []
    required = (
        "timestamp_utc",
        "workload",
        "sample_index",
        "elapsed_ns",
        "schema_version",
    )

    for record in records:
        missing = [key for key in required if key not in record]
        if missing:
            raise ValueError(f"Sample is missing required fields: {missing}")

        elapsed_ns = record["elapsed_ns"]
        sample_index = record["sample_index"]
        schema_version = record["schema_version"]
        extra_work = record.get("extra_work", 0)

        if isinstance(elapsed_ns, bool) or not isinstance(elapsed_ns, int) or elapsed_ns <= 0:
            raise ValueError("elapsed_ns must be a positive integer.")
        if isinstance(sample_index, bool) or not isinstance(sample_index, int) or sample_index < 0:
            raise ValueError("sample_index must be a non-negative integer.")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer.")
        if not isinstance(record["timestamp_utc"], str) or not record["timestamp_utc"]:
            raise ValueError("timestamp_utc must be a non-empty string.")
        if not isinstance(record["workload"], str) or not record["workload"]:
            raise ValueError("workload must be a non-empty string.")
        if isinstance(extra_work, bool) or not isinstance(extra_work, int) or extra_work < 0:
            raise ValueError("extra_work must be a non-negative integer.")

        for key in ("pair_index", "execution_order"):
            value = record.get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{key} must be a non-negative integer.")

        seed = record.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ValueError("seed must be an integer.")

        rows.append(
            (
                record["timestamp_utc"],
                record["workload"],
                sample_index,
                elapsed_ns,
                extra_work,
                record.get("os"),
                record.get("python_version"),
                schema_version,
                run_id,
                variant,
                record.get("binary"),
                config_json,
                record.get("pair_index"),
                record.get("execution_order"),
                seed,
            )
        )

    return rows


def save_samples(
    database: Path,
    records: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    variant: str = "single",
    config: dict[str, Any] | None = None,
) -> str:
    """Persist a batch atomically and return its run identifier."""
    resolved_run_id = run_id or str(uuid4())
    if not isinstance(resolved_run_id, str) or not resolved_run_id.strip():
        raise ValueError("run_id must be a non-empty string.")

    config_json = json.dumps(config or {}, sort_keys=True)
    rows = _prepare_sample_rows(
        records,
        run_id=resolved_run_id,
        variant=variant,
        config_json=config_json,
    )

    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO benchmark_samples (
                timestamp_utc, workload, sample_index, elapsed_ns,
                extra_work, os, python_version, schema_version,
                run_id, variant, binary, config_json,
                pair_index, execution_order, seed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            rows,
        )

    return resolved_run_id


def save_experiment_samples(
    database: Path,
    *,
    run_id: str,
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> str:
    """Persist both A/B variants in one transaction."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string.")

    config_json = json.dumps(config or {}, sort_keys=True)
    baseline_rows = _prepare_sample_rows(
        baseline,
        run_id=run_id,
        variant="baseline",
        config_json=config_json,
    )
    candidate_rows = _prepare_sample_rows(
        candidate,
        run_id=run_id,
        variant="candidate",
        config_json=config_json,
    )

    initialize_database(database)
    insert_sql = """
        INSERT INTO benchmark_samples (
            timestamp_utc, workload, sample_index, elapsed_ns,
            extra_work, os, python_version, schema_version,
            run_id, variant, binary, config_json,
            pair_index, execution_order, seed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    with sqlite3.connect(database) as connection:
        connection.executemany(insert_sql, baseline_rows)
        connection.executemany(insert_sql, candidate_rows)

    return run_id


def get_samples(
    database: Path,
    *,
    run_id: str,
    variant: str | None = None,
) -> list[dict[str, Any]]:
    """Return stored samples in deterministic sample-index order."""
    query = "SELECT * FROM benchmark_samples WHERE run_id = ?"
    parameters: list[Any] = [run_id]

    if variant is not None:
        query += " AND variant = ?"
        parameters.append(variant)

    query += " ORDER BY variant, sample_index, id"

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, parameters).fetchall()

    return [dict(row) for row in rows]
