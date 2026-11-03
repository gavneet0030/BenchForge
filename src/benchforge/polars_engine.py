"""Optional lazy Polars summaries over local CSV and Parquet files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_POLARS_GROUPS = 1_000


def _polars():
    try:
        import polars as pl
    except ImportError as exc:
        raise RuntimeError(
            "Polars processing requires the optional 'polars' dependencies; "
            "install BenchForge with the polars extra."
        ) from exc
    return pl


def summarize_file(
    source: Path,
    *,
    group_by: str,
    metric: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Lazily scan and aggregate one local CSV or Parquet file."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"Polars source file does not exist: {source}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_POLARS_GROUPS:
        raise ValueError(f"limit must be an integer between 1 and {MAX_POLARS_GROUPS}.")

    pl = _polars()
    suffix = source.suffix.casefold()
    if suffix == ".csv":
        lazy_frame = pl.scan_csv(source)
        file_format = "csv"
    elif suffix == ".parquet":
        lazy_frame = pl.scan_parquet(source)
        file_format = "parquet"
    else:
        raise ValueError("Polars processing supports local .csv and .parquet files.")

    schema = lazy_frame.collect_schema()
    missing = {group_by, metric} - set(schema.names())
    if missing:
        raise ValueError("Requested columns do not exist: " + ", ".join(sorted(missing)))
    result = (
        lazy_frame.select(group_by, metric)
        .group_by(group_by)
        .agg(
            pl.len().alias("row_count"),
            pl.col(metric).cast(pl.Float64, strict=False).sum().alias("metric_sum"),
            pl.col(metric).cast(pl.Float64, strict=False).mean().alias("metric_mean"),
        )
        .sort(["row_count", group_by], descending=[True, False])
        .limit(limit + 1)
        .collect(engine="streaming")
    )
    if result.height > limit:
        raise ValueError(
            f"Polars result exceeds the {limit:,}-group limit; choose a smaller grouping."
        )
    return {
        "source": source.name,
        "format": file_format,
        "groups": result.to_dicts(),
    }
