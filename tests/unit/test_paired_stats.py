import pytest

from benchforge.stats import (
    bootstrap_paired_median_ratio_ci,
    paired_median_ratio,
    wilcoxon_paired_pvalue,
)


def test_paired_median_ratio_uses_within_pair_ratios():
    assert paired_median_ratio([100, 200, 300], [110, 220, 330]) == pytest.approx(1.1)


def test_paired_bootstrap_ci_is_reproducible():
    baseline = [100, 200, 300, 400, 500]
    candidate = [110, 220, 330, 440, 550]
    first = bootstrap_paired_median_ratio_ci(
        baseline, candidate, n_resamples=500, seed=42
    )
    second = bootstrap_paired_median_ratio_ci(
        baseline, candidate, n_resamples=500, seed=42
    )
    assert first == second
    assert first[0] <= 1.1 <= first[1]


def test_paired_wilcoxon_detects_consistent_increase():
    baseline = [100, 200, 300, 400, 500, 600]
    candidate = [130, 260, 390, 520, 650, 780]
    assert wilcoxon_paired_pvalue(baseline, candidate) < 0.05


def test_all_equal_pairs_have_p_value_one():
    values = [100, 200, 300]
    assert wilcoxon_paired_pvalue(values, values) == 1.0


@pytest.mark.parametrize(
    "baseline,candidate",
    [
        ([1], [1]),
        ([1, 2], [1]),
        ([0, 2], [1, 2]),
        ([1, float("nan")], [1, 2]),
    ],
)
def test_paired_ratio_rejects_invalid_samples(baseline, candidate):
    with pytest.raises(ValueError):
        paired_median_ratio(baseline, candidate)


def test_bootstrap_rejects_too_few_resamples():
    with pytest.raises(ValueError):
        bootstrap_paired_median_ratio_ci(
            [1, 2], [1, 2], n_resamples=10
        )
