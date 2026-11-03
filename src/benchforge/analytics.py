"""Optional, bounded DuckDB queries over local analytical files."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MAX_ANALYTICS_FILE_BYTES = 500_000_000
MAX_ANALYTICS_GROUPS = 1_000


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "DuckDB analytics requires the optional 'analytics' dependencies; "
            "install BenchForge with the analytics extra."
        ) from exc
    return duckdb


@contextmanager
def _connection(database: Path | None = None) -> Iterator[Any]:
    duckdb = _duckdb()
    if database is None:
        location = ":memory:"
    else:
        database = Path(database)
        database.parent.mkdir(parents=True, exist_ok=True)
        location = str(database)
    connection = duckdb.connect(location, config={"threads": "2"})
    try:
        yield connection
    finally:
        connection.close()


def _scanner(source: Path) -> tuple[str, str]:
    suffix = source.suffix.casefold()
    if suffix == ".parquet":
        return "parquet", "read_parquet(?)"
    if suffix == ".csv":
        return "csv", "read_csv_auto(?)"
    raise ValueError("Analytics supports local .csv and .parquet files.")


def _validate_source(source: Path) -> tuple[str, str]:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"Analytics source file does not exist: {source}")
    if source.stat().st_size > MAX_ANALYTICS_FILE_BYTES:
        raise ValueError(
            f"Analytics source exceeds the {MAX_ANALYTICS_FILE_BYTES:,}-byte limit."
        )
    file_format, scanner = _scanner(source)
    return file_format, scanner


def _schema(connection: Any, scanner: str, source: Path) -> list[dict[str, str]]:
    duckdb = _duckdb()
    try:
        rows = connection.execute(
            f"DESCRIBE SELECT * FROM {scanner}", [str(source)]
        ).fetchall()
    except duckdb.Error as exc:
        raise ValueError(f"Could not inspect analytics source {source.name}: {exc}") from exc
    return [{"name": str(row[0]), "type": str(row[1])} for row in rows]


def inspect_file(source: Path, *, database: Path | None = None) -> dict[str, Any]:
    """Inspect one bounded local CSV or Parquet file without modifying it."""
    source = Path(source)
    file_format, scanner = _validate_source(source)
    with _connection(database) as connection:
        columns = _schema(connection, scanner, source)
        duckdb = _duckdb()
        try:
            row_count = connection.execute(
                f"SELECT COUNT(*) FROM {scanner}", [str(source)]
            ).fetchone()[0]
        except duckdb.Error as exc:
            raise ValueError(f"Could not count analytics source {source.name}: {exc}") from exc
    return {
        "source": source.name,
        "format": file_format,
        "file_size_bytes": source.stat().st_size,
        "row_count": row_count,
        "columns": columns,
    }


def summarize_file(
    source: Path,
    *,
    group_by: str | None = None,
    metric: str | None = None,
    limit: int = 100,
    database: Path | None = None,
) -> dict[str, Any]:
    """Return bounded row/group summaries using fixed, parameterized read queries."""
    source = Path(source)
    file_format, scanner = _validate_source(source)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ANALYTICS_GROUPS:
        raise ValueError(f"limit must be an integer between 1 and {MAX_ANALYTICS_GROUPS}.")
    if metric is not None and group_by is None:
        raise ValueError("metric requires group_by.")

    with _connection(database) as connection:
        columns = _schema(connection, scanner, source)
        names = {column["name"] for column in columns}
        if group_by is not None and group_by not in names:
            raise ValueError(f"Group column does not exist: {group_by}")
        if metric is not None and metric not in names:
            raise ValueError(f"Metric column does not exist: {metric}")
        if group_by is None:
            row_count = connection.execute(
                f"SELECT COUNT(*) FROM {scanner}", [str(source)]
            ).fetchone()[0]
            return {
                "source": source.name,
                "format": file_format,
                "row_count": row_count,
                "columns": columns,
                "groups": [],
            }

        group_sql = '"' + group_by.replace('"', '""') + '"'
        if metric is None:
            aggregate_sql = "COUNT(*) AS row_count"
        else:
            metric_sql = '"' + metric.replace('"', '""') + '"'
            aggregate_sql = (
                "COUNT(*) AS row_count, "
                f"SUM(TRY_CAST({metric_sql} AS DOUBLE)) AS metric_sum, "
                f"AVG(TRY_CAST({metric_sql} AS DOUBLE)) AS metric_mean, "
                f"COUNT(*) FILTER (WHERE {metric_sql} IS NOT NULL "
                f"AND TRY_CAST({metric_sql} AS DOUBLE) IS NULL) AS invalid_metric_rows"
            )
        duckdb = _duckdb()
        try:
            rows = connection.execute(
                f"SELECT {group_sql} AS group_value, {aggregate_sql} "
                f"FROM {scanner} GROUP BY {group_sql} "
                f"ORDER BY row_count DESC, group_value LIMIT ?",
                [str(source), limit + 1],
            ).fetchall()
        except duckdb.Error as exc:
            raise ValueError(f"Could not summarize analytics source {source.name}: {exc}") from exc
        if len(rows) > limit:
            raise ValueError(
                f"Analytics result exceeds the {limit:,}-group limit; choose a smaller grouping."
            )
        keys = (
            ("group_value", "row_count")
            if metric is None
            else (
                "group_value",
                "row_count",
                "metric_sum",
                "metric_mean",
                "invalid_metric_rows",
            )
        )
        return {
            "source": source.name,
            "format": file_format,
            "columns": columns,
            "groups": [dict(zip(keys, row, strict=True)) for row in rows],
        }
