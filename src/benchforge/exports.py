"""Portable exports for persisted benchmark observations."""

from __future__ import annotations

import csv
import re
import sqlite3
import tempfile
from base64 import b64encode
from pathlib import Path

SAMPLE_COLUMNS = (
    "id",
    "timestamp_utc",
    "workload",
    "sample_index",
    "elapsed_ns",
    "extra_work",
    "os",
    "python_version",
    "schema_version",
    "run_id",
    "variant",
    "binary",
    "config_json",
    "pair_index",
    "execution_order",
    "seed",
)
MAX_EXPORT_ROWS = 100_000
MAX_EXPORT_CELL_CHARS = 65_536
MAX_EXPORT_BYTES = 10_000_000
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def spreadsheet_safe_cell(value: object) -> object:
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def validate_export_size(
    connection: sqlite3.Connection,
    *,
    run_id: str | None,
) -> None:
    where_clause = " WHERE run_id = ?" if run_id is not None else ""
    parameters = (run_id,) if run_id is not None else ()
    row_count = connection.execute(
        "SELECT COUNT(*) FROM benchmark_samples" + where_clause,
        parameters,
    ).fetchone()[0]
    if row_count > MAX_EXPORT_ROWS:
        raise ValueError(f"CSV export exceeds the {MAX_EXPORT_ROWS:,}-row limit.")

    text_columns = (
        "timestamp_utc",
        "workload",
        "os",
        "python_version",
        "run_id",
        "variant",
        "binary",
        "config_json",
    )
    long_cell_query = "SELECT 1 FROM benchmark_samples" + where_clause
    long_cell_query += " AND " if run_id is not None else " WHERE "
    long_cell_query += (
        "MAX("
        + ", ".join(f"length(COALESCE({column}, ''))" for column in text_columns)
        + ") > ? LIMIT 1"
    )
    if connection.execute(
        long_cell_query,
        (*parameters, MAX_EXPORT_CELL_CHARS),
    ).fetchone():
        raise ValueError(
            "CSV export contains a text field exceeding the "
            f"{MAX_EXPORT_CELL_CHARS:,}-character safety limit."
        )


def export_samples_csv(
    database: Path,
    output: Path,
    *,
    run_id: str | None = None,
) -> int:
    """Write stored observations to UTF-8 CSV and return the exported row count."""
    database = Path(database)
    output = Path(output)
    if run_id is not None and not run_id.strip():
        raise ValueError("run_id must be a non-empty string.")
    if not database.is_file():
        raise FileNotFoundError(f"Database does not exist: {database}")
    if database.resolve() == output.resolve():
        raise ValueError("CSV output path must differ from the database path.")

    query = (
        "SELECT "
        + ", ".join(SAMPLE_COLUMNS)
        + " FROM benchmark_samples"
    )
    parameters: tuple[str, ...] = ()
    if run_id is not None:
        query += " WHERE run_id = ?"
        parameters = (run_id,)
    query += " ORDER BY timestamp_utc, run_id, variant, sample_index, id"

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with sqlite3.connect(database) as connection:
            validate_export_size(connection, run_id=run_id)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, parameters)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8-sig",
                newline="",
                dir=output.parent,
                prefix=".benchforge-export-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                writer = csv.DictWriter(stream, fieldnames=SAMPLE_COLUMNS)
                writer.writeheader()
                count = 0
                for row in rows:
                    writer.writerow({
                        key: spreadsheet_safe_cell(value)
                        for key, value in dict(row).items()
                    })
                    if stream.tell() > MAX_EXPORT_BYTES:
                        raise ValueError(
                            f"CSV export exceeds the {MAX_EXPORT_BYTES:,}-byte limit."
                        )
                    count += 1
        temporary_path.replace(output)
        return count
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def export_table_csv(database: Path, table: str, output: Path) -> int:
    """Write a SQLite table to bounded UTF-8 CSV and return its row count."""
    database = Path(database)
    output = Path(output)
    if not database.is_file():
        raise FileNotFoundError(f"Database does not exist: {database}")
    if database.resolve() == output.resolve():
        raise ValueError("CSV output path must differ from the database path.")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table):
        raise ValueError(
            "Table name must start with a lowercase letter and contain only "
            "lowercase letters, digits, or underscores."
        )

    quoted_table = f'"{table}"'
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with sqlite3.connect(database) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = ? AND name NOT LIKE 'sqlite_%'",
                (table,),
            ).fetchone()
            if exists is None:
                raise ValueError(f"SQLite table does not exist: {table}")

            columns = [
                row[1] for row in connection.execute(f"PRAGMA table_info({quoted_table})")
            ]
            if not columns:
                raise ValueError(f"SQLite table has no exportable columns: {table}")
            row_count = connection.execute(
                f"SELECT COUNT(*) FROM {quoted_table}"
            ).fetchone()[0]
            if row_count > MAX_EXPORT_ROWS:
                raise ValueError(f"CSV export exceeds the {MAX_EXPORT_ROWS:,}-row limit.")
            quoted_columns = [
                '"' + column.replace('"', '""') + '"' for column in columns
            ]
            query = (
                f"SELECT * FROM {quoted_table} "
                f"ORDER BY {', '.join(quoted_columns)}"
            )
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8-sig",
                newline="",
                dir=output.parent,
                prefix=".benchforge-export-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                writer = csv.writer(stream)
                writer.writerow([spreadsheet_safe_cell(column) for column in columns])
                count = 0
                for row in connection.execute(query):
                    values = []
                    for value in row:
                        if isinstance(value, bytes):
                            value = b64encode(value).decode("ascii")
                        if isinstance(value, str) and len(value) > MAX_EXPORT_CELL_CHARS:
                            raise ValueError(
                                "CSV export contains a text field exceeding the "
                                f"{MAX_EXPORT_CELL_CHARS:,}-character safety limit."
                            )
                        values.append(spreadsheet_safe_cell(value))
                    writer.writerow(values)
                    if stream.tell() > MAX_EXPORT_BYTES:
                        raise ValueError(
                            f"CSV export exceeds the {MAX_EXPORT_BYTES:,}-byte limit."
                        )
                    count += 1
        temporary_path.replace(output)
        return count
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
