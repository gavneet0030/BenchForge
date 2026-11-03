"""Statistical summaries for observed benchmark latencies."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable

MINIMUM_RELIABLE_P99_SAMPLES = 100


def _nearest_rank(values: list[float], percentile: float) -> float:
    rank = max(1, math.ceil(percentile * len(values)))
    return values[rank - 1]


def summarize_latencies(samples: Iterable[int | float]) -> dict[str, int | float | None]:
    """Summarize positive latency samples without removing potential outliers.

    Percentiles use the nearest-rank method. P99 is withheld until at least 100
    observations are present, since smaller samples do not adequately resolve
    the upper tail.
    """
    values = list(samples)
    if not values:
        raise ValueError("At least one latency sample is required.")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Latency samples must be numeric.")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Latency samples must be finite and positive.")

    ordered = sorted(values)
    mean = math.fsum(values) / len(values)
    standard_deviation = statistics.stdev(values) if len(values) > 1 else None
    coefficient_of_variation = (
        standard_deviation / mean if standard_deviation is not None else None
    )
    outlier_indices: list[int] = []
    if len(values) >= 4:
        quartiles = statistics.quantiles(values, n=4, method="inclusive")
        lower_fence = quartiles[0] - 1.5 * (quartiles[2] - quartiles[0])
        upper_fence = quartiles[2] + 1.5 * (quartiles[2] - quartiles[0])
        outlier_indices = [
            index
            for index, value in enumerate(values)
            if value < lower_fence or value > upper_fence
        ]

    return {
        "sample_count": len(values),
        "minimum": min(values),
        "maximum": max(values),
        "mean": mean,
        "median": statistics.median(values),
        "standard_deviation": standard_deviation,
        "coefficient_of_variation": coefficient_of_variation,
        "p90_nearest_rank": _nearest_rank(ordered, 0.90),
        "p95_nearest_rank": _nearest_rank(ordered, 0.95),
        "p99_nearest_rank": (
            _nearest_rank(ordered, 0.99)
            if len(values) >= MINIMUM_RELIABLE_P99_SAMPLES
            else None
        ),
        "p99_reliable": len(values) >= MINIMUM_RELIABLE_P99_SAMPLES,
        "potential_outlier_indices": outlier_indices,
        "outliers_removed": False,
    }
