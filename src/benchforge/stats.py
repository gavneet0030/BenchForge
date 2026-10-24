"""Statistical primitives. Research decisions must be validated experimentally."""

from __future__ import annotations

import math

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

    log_ratio = math.log(float(np.median(b))) - math.log(float(np.median(a)))
    return _exp_finite_positive(log_ratio, "Independent median ratio")


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
    log_ratios = np.log(b_medians) - np.log(a_medians)

    alpha = 1.0 - confidence
    low_log, high_log = np.quantile(
        log_ratios, [alpha / 2, 1 - alpha / 2]
    )
    low = _exp_finite_positive(float(low_log), "Independent bootstrap CI lower bound")
    high = _exp_finite_positive(float(high_log), "Independent bootstrap CI upper bound")

    return low, high


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
    """Classify a latency-ratio confidence interval using validated inputs."""

    values = {
        "ci_low": ci_low,
        "ci_high": ci_high,
        "threshold": threshold,
        "alpha": alpha,
    }
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a number.")
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")

    if ci_low <= 0 or ci_high <= 0:
        raise ValueError("Confidence interval bounds must be positive.")
    if ci_low > ci_high:
        raise ValueError("Confidence interval lower bound exceeds upper bound.")
    if threshold < 0 or not 0 < alpha < 1:
        raise ValueError("Invalid threshold or alpha.")

    if p_value is not None:
        if isinstance(p_value, bool) or not isinstance(p_value, (int, float)):
            raise TypeError("p_value must be a number or None.")
        if not math.isfinite(p_value) or not 0 <= p_value <= 1:
            raise ValueError("p_value must be finite and between 0 and 1.")

    boundary = 1.0 + threshold

    if ci_low > boundary and p_value is not None and p_value < alpha:
        return "REGRESSION"
    if ci_high < boundary:
        return "PASS"
    return "INCONCLUSIVE"


def _validate_paired_samples(baseline, candidate):
    """Validate positive, finite, equally sized paired latency samples."""

    baseline = list(baseline)
    candidate = list(candidate)

    if len(baseline) != len(candidate):
        raise ValueError("Paired samples must have equal lengths.")
    if len(baseline) < 2:
        raise ValueError("At least two paired observations are required.")

    for name, values in (("baseline", baseline), ("candidate", candidate)):
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} samples must be numeric.")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} samples must be finite and positive.")

    return baseline, candidate


def _exp_finite_positive(log_value: float, context: str) -> float:
    """Exponentiate a log-ratio, rejecting unrepresentable results."""
    try:
        result = math.exp(log_value)
    except OverflowError as exc:
        raise ValueError(f"{context} is outside the representable float range.") from exc

    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{context} is outside the representable positive float range.")
    return result


def paired_median_ratio(baseline, candidate) -> float:
    """Return the median paired latency ratio using log-space arithmetic."""
    import statistics

    baseline, candidate = _validate_paired_samples(baseline, candidate)
    log_ratios = [
        math.log(candidate_value) - math.log(baseline_value)
        for baseline_value, candidate_value in zip(baseline, candidate)
    ]
    return _exp_finite_positive(
        statistics.median(log_ratios),
        "Median paired ratio",
    )


def bootstrap_paired_median_ratio_ci(
    baseline,
    candidate,
    confidence: float = 0.95,
    n_resamples: int = 5000,
    seed: int = 2026,
) -> tuple[float, float]:
    """Percentile bootstrap CI for the median of paired latency ratios."""
    import numpy as np

    baseline, candidate = _validate_paired_samples(baseline, candidate)

    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1.")
    if isinstance(n_resamples, bool) or not isinstance(n_resamples, int):
        raise TypeError("n_resamples must be an integer.")
    if n_resamples < 100:
        raise ValueError("n_resamples must be at least 100.")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer.")

    log_ratios = (
        np.log(np.asarray(candidate, dtype=float))
        - np.log(np.asarray(baseline, dtype=float))
    )
    rng = np.random.default_rng(seed)
    n = len(log_ratios)
    estimates = np.empty(n_resamples, dtype=float)

    for index in range(n_resamples):
        sampled = log_ratios[rng.integers(0, n, size=n)]
        estimates[index] = np.median(sampled)

    tail = (1.0 - confidence) / 2.0
    low_log, high_log = np.quantile(estimates, [tail, 1.0 - tail])
    low = _exp_finite_positive(float(low_log), "Bootstrap CI lower bound")
    high = _exp_finite_positive(float(high_log), "Bootstrap CI upper bound")
    return low, high


def wilcoxon_paired_pvalue(baseline, candidate) -> float:
    """Return a two-sided Wilcoxon signed-rank p-value for paired latencies."""
    from scipy.stats import wilcoxon

    baseline, candidate = _validate_paired_samples(baseline, candidate)

    differences = [
        candidate_value - baseline_value
        for baseline_value, candidate_value in zip(baseline, candidate)
    ]
    if all(value == 0 for value in differences):
        return 1.0

    result = wilcoxon(
        candidate,
        baseline,
        alternative="two-sided",
        zero_method="wilcox",
        method="auto",
    )
    return float(result.pvalue)
