import math

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
    low, high = bootstrap_median_ratio_ci(baseline, baseline, n_resamples=1000, seed=7)
    assert low <= 1.0 <= high


def test_clear_regression_requires_both_conditions():
    assert (
        classify_regression(
            ci_low=1.10,
            ci_high=1.20,
            threshold=0.03,
            p_value=0.001,
        )
        == "REGRESSION"
    )


def test_overlapping_threshold_is_inconclusive():
    assert (
        classify_regression(
            ci_low=1.01,
            ci_high=1.08,
            threshold=0.03,
            p_value=0.01,
        )
        == "INCONCLUSIVE"
    )


def test_clear_pass():
    assert (
        classify_regression(
            ci_low=0.98,
            ci_high=1.02,
            threshold=0.03,
        )
        == "PASS"
    )


def test_mann_whitney_returns_valid_p_value():
    a = [10, 11, 12, 13, 14]
    b = [20, 21, 22, 23, 24]
    p = mann_whitney_pvalue(a, b)
    assert 0 <= p <= 1


def test_empty_samples_are_rejected():
    with pytest.raises(ValueError):
        median_ratio([], [1, 2])




@pytest.mark.parametrize(
    ("ci_low", "ci_high", "p_value"),
    [
        (math.nan, 1.2, 0.001),
        (1.1, math.inf, 0.001),
        (-0.5, 0.9, 0.001),
        (0.9, 0.0, 0.001),
    ],
)
def test_classifier_rejects_invalid_intervals(ci_low, ci_high, p_value):
    with pytest.raises(ValueError):
        classify_regression(
            ci_low=ci_low,
            ci_high=ci_high,
            p_value=p_value,
        )


@pytest.mark.parametrize("p_value", [-0.1, 1.5, math.nan, math.inf])
def test_classifier_rejects_invalid_p_values(p_value):
    with pytest.raises(ValueError):
        classify_regression(
            ci_low=1.1,
            ci_high=1.2,
            p_value=p_value,
        )



@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"ci_low": 1.10, "ci_high": 1.20},
            "INCONCLUSIVE",
        ),
        (
            {"ci_low": 0.90, "ci_high": 1.03},
            "INCONCLUSIVE",
        ),
        (
            {"ci_low": 0.90, "ci_high": 1.02},
            "PASS",
        ),
    ],
)
def test_classifier_boundary_policy(kwargs, expected):
    assert classify_regression(**kwargs) == expected


def test_classifier_rejects_reversed_positive_interval():
    with pytest.raises(
        ValueError,
        match="lower bound exceeds upper bound",
    ):
        classify_regression(ci_low=1.2, ci_high=1.1)

def test_classifier_rejects_boolean_inputs():
    with pytest.raises(TypeError):
        classify_regression(ci_low=True, ci_high=1.2)

    with pytest.raises(TypeError):
        classify_regression(ci_low=1.1, ci_high=1.2, p_value=True)

def test_independent_ratio_rejects_unrepresentable_extreme_values():
    with pytest.raises(ValueError, match="representable"):
        median_ratio([1e-300, 2e-300], [1e300, 2e300])

    with pytest.raises(ValueError, match="representable"):
        median_ratio([1e300, 2e300], [1e-300, 2e-300])


def test_independent_bootstrap_rejects_unrepresentable_extreme_ci():
    with pytest.raises(ValueError, match="representable"):
        bootstrap_median_ratio_ci(
            [1e-300, 2e-300, 3e-300],
            [1e300, 2e300, 3e300],
            n_resamples=100,
            seed=42,
        )


def test_independent_ratio_remains_valid_for_normal_values():
    assert median_ratio([100, 200, 300], [110, 220, 330]) == pytest.approx(1.1)
