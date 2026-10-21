"""Statistical primitives. Research decisions must be validated experimentally."""

from __future__ import annotations

import numpy as np
from scipy.stats import mannwhitneyu


def median_ratio(baseline, candidate) -> float:
    """Return candidate median divided by baseline median."""
    a = np.asarray(baseline, dtype=float)
    b = np.asarray(candidate, dtype=float)

    if a.size == 0 or b.size == 0:
        raise ValueError("Both samples must be non-empty.")
    if not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        raise ValueError("Samples must contain only finite values.")
    if np.any(a <= 0) or np.any(b <= 0):
        raise ValueError("Latency samples must be positive.")

    return float(np.median(b) / np.median(a))


def bootstrap_median_ratio_ci(
    baseline,
    candidate,
    confidence: float = 0.95,
    n_resamples: int = 5000,
    seed: int = 2026,
) -> tuple[float, float]:
    """Percentile bootstrap CI for the ratio of independent sample medians."""
    a = np.asarray(baseline, dtype=float)
    b = np.asarray(candidate, dtype=float)

    if a.size < 2 or b.size < 2:
        raise ValueError("At least two observations per group are required.")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one.")
    if n_resamples < 100:
        raise ValueError("Use at least 100 resamples.")

    median_ratio(a, b)
    rng = np.random.default_rng(seed)

    a_idx = rng.integers(0, a.size, size=(n_resamples, a.size))
    b_idx = rng.integers(0, b.size, size=(n_resamples, b.size))

    a_medians = np.median(a[a_idx], axis=1)
    b_medians = np.median(b[b_idx], axis=1)
    ratios = b_medians / a_medians

    alpha = 1.0 - confidence
    low, high = np.quantile(ratios, [alpha / 2, 1 - alpha / 2])

    return float(low), float(high)


def mann_whitney_pvalue(baseline, candidate) -> float:
    """Two-sided Mann-Whitney U p-value; it is not a median-ratio test."""
    a = np.asarray(baseline, dtype=float)
    b = np.asarray(candidate, dtype=float)

    if a.size == 0 or b.size == 0:
        raise ValueError("Both samples must be non-empty.")

    return float(mannwhitneyu(a, b, alternative="two-sided").pvalue)


def classify_regression(
    ci_low: float,
    ci_high: float,
    threshold: float = 0.03,
    p_value: float | None = None,
    alpha: float = 0.05,
) -> str:
    """Apply the initial three-way policy to a latency-ratio confidence interval."""
    if threshold < 0 or not 0 < alpha < 1:
        raise ValueError("Invalid threshold or alpha.")
    if ci_low > ci_high:
        raise ValueError("Confidence interval lower bound exceeds upper bound.")

    boundary = 1.0 + threshold

    if ci_low > boundary and p_value is not None and p_value < alpha:
        return "REGRESSION"
    if ci_high < boundary:
        return "PASS"
    return "INCONCLUSIVE"
