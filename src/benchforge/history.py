"""Compatibility-checked comparisons against a stored reference run."""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from .stats import (
    MINIMUM_PAIRED_OBSERVATIONS,
    bootstrap_median_ratio_ci,
    classify_regression,
    mann_whitney_pvalue,
    median_ratio,
)
from .storage import get_samples

_COMPATIBILITY_FIELDS = (
    "workload",
    "iterations",
    "extra_work",
    "os",
    "schema_version",
    "compiler",
    "compiler_version",
    "build_type",
    "build_generator",
    "build_toolset",
)


def _signature(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    signatures = {
        tuple(row.get(field) for field in _COMPATIBILITY_FIELDS)
        for row in rows
    }
    if len(signatures) != 1:
        raise ValueError(f"{label} run contains mixed or missing workload configurations.")
    return dict(zip(_COMPATIBILITY_FIELDS, next(iter(signatures)), strict=True))


def compare_historical_runs(
    database: Path,
    *,
    run_id: str,
    reference_run_id: str,
    variant: str = "candidate",
    threshold: float = 0.03,
    confidence: float = 0.95,
    n_resamples: int = 5000,
    seed: int = 2026,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Compare two compatible, independent runs and return the evidence."""
    if run_id == reference_run_id:
        raise ValueError("Current and reference run IDs must be different.")
    if variant not in {"baseline", "candidate"}:
        raise ValueError("variant must be 'baseline' or 'candidate'.")

    current_rows = get_samples(database, run_id=run_id, variant=variant)
    reference_rows = get_samples(database, run_id=reference_run_id, variant=variant)
    if not current_rows or not reference_rows:
        raise ValueError(
            f"Both runs must contain stored {variant} observations."
        )
    current_signature = _signature(current_rows, "Current")
    reference_signature = _signature(reference_rows, "Reference")
    if current_signature != reference_signature:
        differences = [
            field
            for field in _COMPATIBILITY_FIELDS
            if current_signature[field] != reference_signature[field]
        ]
        raise ValueError(
            "Runs are incompatible; differing fields: " + ", ".join(differences)
        )

    current = [row["elapsed_ns"] for row in current_rows]
    reference = [row["elapsed_ns"] for row in reference_rows]
    if (
        len(current) < MINIMUM_PAIRED_OBSERVATIONS
        or len(reference) < MINIMUM_PAIRED_OBSERVATIONS
    ):
        raise ValueError(
            "At least "
            f"{MINIMUM_PAIRED_OBSERVATIONS} observations in each run are required "
            "for inferential comparison."
        )

    ratio = median_ratio(reference, current)
    ci_low, ci_high = bootstrap_median_ratio_ci(
        reference,
        current,
        confidence=confidence,
        n_resamples=n_resamples,
        seed=seed,
    )
    p_value = mann_whitney_pvalue(reference, current)
    return {
        "run_id": run_id,
        "reference_run_id": reference_run_id,
        "variant": variant,
        "current_count": len(current),
        "reference_count": len(reference),
        "current_median_ns": float(statistics.median(current)),
        "reference_median_ns": float(statistics.median(reference)),
        "ratio": ratio,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_value": p_value,
        "threshold": threshold,
        "confidence": confidence,
        "alpha": alpha,
        "seed": seed,
        "n_resamples": n_resamples,
        "decision": classify_regression(
            ci_low,
            ci_high,
            threshold=threshold,
            p_value=p_value,
            alpha=alpha,
        ),
        "compatibility": current_signature,
    }
