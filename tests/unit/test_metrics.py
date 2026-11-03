import pytest

from benchforge.metrics import summarize_latencies
from benchforge.phase4 import assess_repeatability, summarize_measurements


def test_summary_uses_nearest_rank_and_preserves_outliers():
    summary = summarize_latencies([1, 2, 3, 4, 5, 6, 100])

    assert summary["sample_count"] == 7
    assert summary["minimum"] == 1
    assert summary["maximum"] == 100
    assert summary["p90_nearest_rank"] == 100
    assert summary["p95_nearest_rank"] == 100
    assert summary["p99_nearest_rank"] is None
    assert summary["p99_reliable"] is False
    assert summary["potential_outlier_indices"] == [6]
    assert summary["outliers_removed"] is False


def test_summary_reports_p99_for_one_hundred_observations():
    summary = summarize_latencies(range(1, 101))

    assert summary["p99_nearest_rank"] == 99
    assert summary["p99_reliable"] is True


def test_summary_handles_one_observation_without_inventing_variance():
    summary = summarize_latencies([3])

    assert summary["standard_deviation"] is None
    assert summary["coefficient_of_variation"] is None


@pytest.mark.parametrize("samples", [[], [0], [-1], [float("nan")], [True], ["1"]])
def test_summary_rejects_invalid_samples(samples):
    with pytest.raises((TypeError, ValueError)):
        summarize_latencies(samples)


def test_repeatability_requires_minimum_sample_count():
    result = assess_repeatability([100, 101, 99, 100])
    assert result["status"] == "INSUFFICIENT_SAMPLE"
    assert result["outliers_removed"] is False


def test_repeatability_distinguishes_stable_samples_from_injected_noise():
    stable = assess_repeatability([99, 100, 101, 100, 100])
    noisy = assess_repeatability([50, 100, 101, 99, 100])

    assert stable["status"] == "REPEATABLE"
    assert noisy["status"] == "NOISY"
    assert noisy["outlier_indices"] == [0]
    assert noisy["outliers_removed"] is False


def test_measurement_summary_includes_throughput_without_dropping_samples():
    summary = summarize_measurements(
        [
            {"elapsed_ns": 10, "bytes_processed": 100},
            {"elapsed_ns": 20, "bytes_processed": 100},
        ]
    )
    throughput = summary["throughput_bytes_per_second"]
    assert throughput["sample_count"] == 2
    assert throughput["minimum"] == 5_000_000_000
    assert throughput["maximum"] == 10_000_000_000
    assert throughput["outliers_removed"] is False


def test_measurement_summary_rejects_mixed_byte_throughput_records():
    with pytest.raises(ValueError, match="positive bytes in every sample"):
        summarize_measurements(
            [
                {"elapsed_ns": 10, "bytes_processed": 100},
                {"elapsed_ns": 20, "bytes_processed": 0},
            ]
        )
