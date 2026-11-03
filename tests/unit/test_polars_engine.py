import builtins
import csv
from pathlib import Path

import pytest

from benchforge import polars_engine


def _csv(path: Path):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Region", "Revenue"])
        writer.writerows([["north", 10], ["north", 20], ["south", 5]])
    return path


def test_polars_missing_optional_dependency_is_reported(monkeypatch):
    real_import = builtins.__import__

    def without_polars(name, *args, **kwargs):
        if name == "polars":
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_polars)

    with pytest.raises(RuntimeError, match="optional 'polars' dependencies"):
        polars_engine._polars()


def test_polars_lazy_csv_group_summary(tmp_path):
    pytest.importorskip("polars")
    source = _csv(tmp_path / "source.csv")

    result = polars_engine.summarize_file(
        source,
        group_by="Region",
        metric="Revenue",
    )

    assert result["format"] == "csv"
    assert result["groups"] == [
        {"Region": "north", "row_count": 2, "metric_sum": 30.0, "metric_mean": 15.0},
        {"Region": "south", "row_count": 1, "metric_sum": 5.0, "metric_mean": 5.0},
    ]


def test_polars_lazy_parquet_group_summary(tmp_path):
    pytest.importorskip("polars")
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    source = tmp_path / "source.parquet"
    parquet.write_table(
        pa.table({"site": ["north", "south"], "value": [1, 3]}),
        source,
    )

    result = polars_engine.summarize_file(
        source,
        group_by="site",
        metric="value",
    )
    assert result["format"] == "parquet"
    assert result["groups"][0]["metric_mean"] == 1.0
    assert result["groups"][1]["metric_sum"] == 3.0


def test_polars_validates_source_columns_and_group_limit(tmp_path):
    pytest.importorskip("polars")
    source = _csv(tmp_path / "source.csv")

    with pytest.raises(ValueError, match="Requested columns do not exist"):
        polars_engine.summarize_file(source, group_by="missing", metric="Revenue")

    with pytest.raises(ValueError, match="exceeds the 1-group limit"):
        polars_engine.summarize_file(
            source,
            group_by="Region",
            metric="Revenue",
            limit=1,
        )
