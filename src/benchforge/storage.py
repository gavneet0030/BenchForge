"""SQLite persistence for reproducible benchmark measurements."""

from __future__ import annotations

import json
import math
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
                seed INTEGER,
                bytes_processed INTEGER,
                peak_process_memory_bytes INTEGER,
                peak_process_memory_source TEXT,
                current_working_set_bytes INTEGER,
                private_process_bytes INTEGER,
                thread_count INTEGER,
                process_cpu_time_ns INTEGER,
                cpu_utilization_percent REAL,
                compiler TEXT,
                compiler_version TEXT,
                build_type TEXT,
                build_generator TEXT,
                build_toolset TEXT
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
            "bytes_processed": "INTEGER",
            "peak_process_memory_bytes": "INTEGER",
            "peak_process_memory_source": "TEXT",
            "current_working_set_bytes": "INTEGER",
            "private_process_bytes": "INTEGER",
            "thread_count": "INTEGER",
            "process_cpu_time_ns": "INTEGER",
            "cpu_utilization_percent": "REAL",
            "compiler": "TEXT",
            "compiler_version": "TEXT",
            "build_type": "TEXT",
            "build_generator": "TEXT",
            "build_toolset": "TEXT",
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

        bytes_processed = record.get("bytes_processed")
        if bytes_processed is not None and (
            isinstance(bytes_processed, bool)
            or not isinstance(bytes_processed, int)
            or bytes_processed < 0
        ):
            raise ValueError("bytes_processed must be a non-negative integer.")

        peak_memory = record.get("peak_process_memory_bytes")
        if peak_memory is not None and (
            isinstance(peak_memory, bool)
            or not isinstance(peak_memory, int)
            or peak_memory <= 0
        ):
            raise ValueError("peak_process_memory_bytes must be a positive integer.")

        for field in ("current_working_set_bytes", "private_process_bytes"):
            value = record.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{field} must be a positive integer or None.")

        thread_count = record.get("thread_count")
        if thread_count is not None and (
            isinstance(thread_count, bool)
            or not isinstance(thread_count, int)
            or thread_count < 1
        ):
            raise ValueError("thread_count must be a positive integer or None.")

        process_cpu_time = record.get("process_cpu_time_ns")
        if process_cpu_time is not None and (
            isinstance(process_cpu_time, bool)
            or not isinstance(process_cpu_time, int)
            or process_cpu_time < 0
        ):
            raise ValueError("process_cpu_time_ns must be a non-negative integer.")

        cpu_utilization = record.get("cpu_utilization_percent")
        if cpu_utilization is not None and (
            isinstance(cpu_utilization, bool)
            or not isinstance(cpu_utilization, (int, float))
            or not math.isfinite(cpu_utilization)
            or cpu_utilization < 0
        ):
            raise ValueError("cpu_utilization_percent must be finite and non-negative.")

        text_fields = (
            "peak_process_memory_source",
            "compiler",
            "compiler_version",
            "build_type",
            "build_generator",
            "build_toolset",
        )
        for field in text_fields:
            value = record.get(field)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field} must be a non-empty string or None.")

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
                bytes_processed,
                peak_memory,
                record.get("peak_process_memory_source"),
                record.get("current_working_set_bytes"),
                record.get("private_process_bytes"),
                thread_count,
                process_cpu_time,
                cpu_utilization,
                record.get("compiler"),
                record.get("compiler_version"),
                record.get("build_type"),
                record.get("build_generator"),
                record.get("build_toolset"),
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
                pair_index, execution_order, seed, bytes_processed,
                peak_process_memory_bytes, peak_process_memory_source,
                current_working_set_bytes, private_process_bytes, thread_count,
                process_cpu_time_ns, cpu_utilization_percent,
                compiler, compiler_version, build_type, build_generator, build_toolset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            pair_index, execution_order, seed, bytes_processed,
            peak_process_memory_bytes, peak_process_memory_source,
            current_working_set_bytes, private_process_bytes, thread_count,
            process_cpu_time_ns, cpu_utilization_percent,
            compiler, compiler_version, build_type, build_generator, build_toolset
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
