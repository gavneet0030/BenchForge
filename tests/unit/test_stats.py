import numpy as np
import pytest

from benchforge.stats import (
    bootstrap_median_ratio_ci,
    classify_regression,
    mann_whitney_pvalue,
    median_ratio,
)


def test_identical_samples_have_ratio_one():
    sample = np.array([10, 11, 12, 13, 14], dtype=float)
    assert median_ratio(sample, sample) == pytest.approx(1.0)


def test_shifted_sample_has_larger_median_ratio():
    baseline = np.array([10, 11, 12, 13, 14], dtype=float)
    candidate = baseline * 1.5
    assert median_ratio(baseline, candidate) == pytest.approx(1.5)


def test_bootstrap_ci_contains_ratio_for_stable_identical_samples():
    baseline = np.arange(100, 200, dtype=float)
    low, high = bootstrap_median_ratio_ci(
        baseline, baseline, n_resamples=1000, seed=7
    )
    assert low <= 1.0 <= high


def test_clear_regression_requires_both_conditions():
    assert classify_regression(
        ci_low=1.10,
        ci_high=1.20,
        threshold=0.03,
        p_value=0.001,
    ) == "REGRESSION"


def test_overlapping_threshold_is_inconclusive():
    assert classify_regression(
        ci_low=1.01,
        ci_high=1.08,
        threshold=0.03,
        p_value=0.01,
    ) == "INCONCLUSIVE"


def test_clear_pass():
    assert classify_regression(
        ci_low=0.98,
        ci_high=1.02,
        threshold=0.03,
    ) == "PASS"


def test_mann_whitney_returns_valid_p_value():
    a = [10, 11, 12, 13, 14]
    b = [20, 21, 22, 23, 24]
    p = mann_whitney_pvalue(a, b)
    assert 0 <= p <= 1


def test_empty_samples_are_rejected():
    with pytest.raises(ValueError):
        median_ratio([], [1, 2])
