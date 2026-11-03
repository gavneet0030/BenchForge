"""Bounded read-only export of benchmark history for the optional dbt project."""

from __future__ import annotations

import csv
import io
import sqlite3
import tempfile
from pathlib import Path

HISTORY_COLUMNS = (
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
MAX_HISTORY_EXPORT_ROWS = 100_000
MAX_HISTORY_EXPORT_BYTES = 10_000_000
MAX_HISTORY_CELL_CHARS = 65_536


def export_benchmark_history_csv(
    database: Path,
    output: Path,
    *,
    max_rows: int = MAX_HISTORY_EXPORT_ROWS,
) -> int:
    """Export a bounded sample snapshot without modifying the SQLite database."""
    database = Path(database)
    output = Path(output)
    if not database.is_file():
        raise FileNotFoundError(f"Database does not exist: {database}")
    if database.resolve() == output.resolve():
        raise ValueError("CSV output path must differ from the database path.")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"CSV output already exists: {output}")
    if not output.parent.is_dir():
        raise FileNotFoundError(f"CSV output directory does not exist: {output.parent}")
    if (
        isinstance(max_rows, bool)
        or not isinstance(max_rows, int)
        or not 1 <= max_rows <= MAX_HISTORY_EXPORT_ROWS
    ):
        raise ValueError(
            f"max_rows must be an integer between 1 and {MAX_HISTORY_EXPORT_ROWS:,}."
        )

    temporary_path: Path | None = None
    database_uri = f"{database.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as connection:
            connection.execute("PRAGMA query_only = ON")
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'benchmark_samples'"
            ).fetchone()
            if table is None:
                raise ValueError("SQLite database does not contain benchmark_samples.")
            available = {
                row[1] for row in connection.execute("PRAGMA table_info(benchmark_samples)")
            }
            missing = sorted(set(HISTORY_COLUMNS) - available)
            if missing:
                raise ValueError(
                    "Benchmark history is missing required columns: " + ", ".join(missing)
                )

            quoted_columns = ", ".join(f'"{column}"' for column in HISTORY_COLUMNS)
            rows = connection.execute(
                f"SELECT {quoted_columns} FROM benchmark_samples ORDER BY id LIMIT ?",
                (max_rows + 1,),
            )
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=output.parent,
                prefix=".benchforge-history-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                row_buffer = io.StringIO(newline="")
                writer = csv.writer(row_buffer, lineterminator="\n")
                writer.writerow(HISTORY_COLUMNS)
                payload = row_buffer.getvalue().encode("utf-8")
                stream.write(payload)
                written_bytes = len(payload)

                count = 0
                for row in rows:
                    if count >= max_rows:
                        raise ValueError(
                            f"Benchmark history exceeds the {max_rows:,}-row export limit."
                        )
                    if any(isinstance(value, str) and len(value) > MAX_HISTORY_CELL_CHARS
                           for value in row):
                        raise ValueError(
                            "Benchmark history contains a cell exceeding the "
                            f"{MAX_HISTORY_CELL_CHARS:,}-character limit."
                        )
                    row_buffer.seek(0)
                    row_buffer.truncate(0)
                    writer.writerow(row)
                    payload = row_buffer.getvalue().encode("utf-8")
                    written_bytes += len(payload)
                    if written_bytes > MAX_HISTORY_EXPORT_BYTES:
                        raise ValueError(
                            f"Benchmark history exceeds the "
                            f"{MAX_HISTORY_EXPORT_BYTES:,}-byte export limit."
                        )
                    stream.write(payload)
                    count += 1

        temporary_path.replace(output)
        return count
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
