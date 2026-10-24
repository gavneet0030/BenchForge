"""BenchForge command-line interface."""

import json
import platform
import statistics
import sys
from datetime import UTC
from pathlib import Path

import typer
from rich import print

from benchforge import __version__
from benchforge.ab import run_ab_benchmark
from benchforge.runner import run_benchmark
from benchforge.stats import (
    bootstrap_paired_median_ratio_ci,
    classify_regression,
    paired_median_ratio,
    wilcoxon_paired_pvalue,
)
from benchforge.storage import initialize_database, save_samples

app = typer.Typer(
    name="benchforge",
    help="Statistical performance regression research toolkit.",
    no_args_is_help=True,
)


@app.command()
def doctor():
    """Display the local runtime and dependency environment."""
    import matplotlib
    import numpy
    import scipy

    print(f"[bold cyan]BenchForge[/bold cyan] {__version__}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")
    print(f"NumPy: {numpy.__version__}")
    print(f"SciPy: {scipy.__version__}")
    print(f"Matplotlib: {matplotlib.__version__}")
    print("[green]Python environment check completed.[/green]")


@app.command()
def run(
    binary: Path = typer.Option(
        Path("build/native/benchforge_lookup.exe"),
        "--binary",
        help="Path to the compiled C++ workload.",
    ),
    runs: int = typer.Option(30, min=2),
    warmup: int = typer.Option(3, min=0),
    iterations: int = typer.Option(100000, min=1),
    extra_work: int = typer.Option(0, "--extra-work", min=0),
    output: Path = typer.Option(Path("data/raw/latest.jsonl")),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"),
        "--database",
        help="SQLite database for persistent benchmark history.",
    ),
):
    """Run the native hash-map benchmark and persist its measurements."""
    records = run_benchmark(
        binary=binary,
        runs=runs,
        warmup=warmup,
        iterations=iterations,
        extra_work=extra_work,
        output=output,
    )

    config = {
        "binary_path": str(binary.resolve()),
        "runs": runs,
        "warmup": warmup,
        "iterations": iterations,
        "extra_work": extra_work,
    }

    run_id = save_samples(
        database=database,
        records=records,
        variant="single",
        config=config,
    )

    import numpy as np

    durations = [row["elapsed_ns"] for row in records]
    print(f"[green]Saved {len(records)} samples to {output}[/green]")
    print(f"[green]Saved history to {database}[/green]")
    print(f"Run ID: {run_id}")
    print(f"Median: {np.median(durations):,.0f} ns")
    print(f"P95: {np.percentile(durations, 95):,.0f} ns")
    print(f"Minimum: {min(durations):,} ns")
    print(f"Maximum: {max(durations):,} ns")
    print(
        "[yellow]These are local observations, not a "
        "validated regression conclusion.[/yellow]"
    )


@app.command()
def init_db(
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"),
        "--database",
    ),
):
    """Create or migrate the local benchmark history database."""
    initialize_database(database)
    print(f"[green]Database initialized: {database}[/green]")


@app.command()
def compare(
    run_id: str = typer.Option(..., "--run-id", help="A/B experiment run ID."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"),
        "--database",
        help="SQLite benchmark history database.",
    ),
    threshold: float = typer.Option(
        0.03, min=0.0, max=1.0,
        help="Practical regression threshold (0.03 = 3%).",
    ),
    confidence: float = typer.Option(
        0.95, min=0.5, max=0.999,
        help="Bootstrap confidence level.",
    ),
    n_resamples: int = typer.Option(
        5000, min=100, help="Paired bootstrap resamples."
    ),
    seed: int = typer.Option(2026, help="Bootstrap random seed."),
    alpha: float = typer.Option(
        0.05, min=0.001, max=0.25,
        help="Significance level for the paired test.",
    ),
):
    """Compare paired baseline/candidate samples from a stored A/B run."""
    from benchforge.storage import get_samples

    if not database.is_file():
        raise typer.BadParameter(f"Database does not exist: {database}")

    baseline_rows = get_samples(database, run_id=run_id, variant="baseline")
    candidate_rows = get_samples(database, run_id=run_id, variant="candidate")

    if not baseline_rows or not candidate_rows:
        raise typer.BadParameter("Run ID must contain both baseline and candidate samples.")

    def index_rows(rows, variant):
        indexed = {}
        for row in rows:
            pair_index = row.get("pair_index")
            elapsed_ns = row.get("elapsed_ns")
            if isinstance(pair_index, bool) or not isinstance(pair_index, int):
                raise typer.BadParameter(f"{variant} samples are missing a valid pair_index.")
            if (
                isinstance(elapsed_ns, bool)
                or not isinstance(elapsed_ns, int)
                or elapsed_ns <= 0
            ):
                raise typer.BadParameter(
                    f"{variant} samples have an invalid elapsed_ns; "
                    "expected a positive integer."
                )
            if pair_index in indexed:
                raise typer.BadParameter(f"Duplicate {variant} pair_index: {pair_index}")
            indexed[pair_index] = elapsed_ns
        return indexed

    baseline_by_pair = index_rows(baseline_rows, "baseline")
    candidate_by_pair = index_rows(candidate_rows, "candidate")

    if set(baseline_by_pair) != set(candidate_by_pair):
        raise typer.BadParameter("Baseline and candidate pair_index sets do not match.")

    pair_indices = sorted(baseline_by_pair)
    baseline = [baseline_by_pair[i] for i in pair_indices]
    candidate = [candidate_by_pair[i] for i in pair_indices]

    ratio = paired_median_ratio(baseline, candidate)
    ci_low, ci_high = bootstrap_paired_median_ratio_ci(
        baseline,
        candidate,
        confidence=confidence,
        n_resamples=n_resamples,
        seed=seed,
    )
    p_value = wilcoxon_paired_pvalue(baseline, candidate)
    decision = classify_regression(
        ci_low,
        ci_high,
        threshold=threshold,
        p_value=p_value,
        alpha=alpha,
    )

    print(f"Run ID: {run_id}")
    print(f"Matched pairs: {len(pair_indices)}")
    print(f"Effect: median paired candidate/baseline ratio = {ratio:.5f}")
    print(f"{confidence:.1%} paired bootstrap CI: [{ci_low:.5f}, {ci_high:.5f}]")
    print(f"Wilcoxon signed-rank p-value: {p_value:.6g}")
    print(f"Practical regression threshold: {threshold:.1%}")
    print(f"Significance level alpha: {alpha:.3f}")
    print(f"Decision: {decision}")
    print(
        "Interpretation: ratios above 1 indicate slower candidate latency. "
        "The verdict depends on this run, its pairing, and the selected policy."
    )


@app.command()
def report(
    run_id: str = typer.Option(..., "--run-id", help="Experiment run ID to report."),
    database: Path = typer.Option(
        Path("data/benchforge.sqlite"), "--database", help="SQLite experiment database."
    ),
    output: Path = typer.Option(
        Path("reports/generated/report.md"), "--output", help="Markdown report path."
    ),
    threshold: float = typer.Option(0.03, min=0.0, max=1.0),
    confidence: float = typer.Option(0.95, min=0.5, max=0.999),
    n_resamples: int = typer.Option(5000, min=100),
    seed: int = typer.Option(2026),
    alpha: float = typer.Option(0.05, min=0.001, max=0.25),
    force: bool = typer.Option(False, "--force", help="Allow replacing an existing report."),
):
    """Generate a reproducible Markdown report for a stored A/B experiment."""
    from datetime import datetime

    from benchforge.storage import get_samples

    if not database.is_file():
        raise typer.BadParameter(f"Database does not exist: {database}")

    if output.exists() and not force:
        raise typer.BadParameter(
            f"Output already exists: {output}. Use --force to replace it."
        )

    baseline_rows = get_samples(database, run_id=run_id, variant="baseline")
    candidate_rows = get_samples(database, run_id=run_id, variant="candidate")

    if not baseline_rows or not candidate_rows:
        raise typer.BadParameter(
            f"Run '{run_id}' must contain both baseline and candidate samples."
        )

    def validate_rows(rows, variant):
        pairs = {}
        durations = {}
        for row in rows:
            pair_index = row.get("pair_index")
            elapsed_ns = row.get("elapsed_ns")
            if isinstance(pair_index, bool) or not isinstance(pair_index, int):
                raise typer.BadParameter(
                    f"{variant} contains an invalid pair_index."
                )
            if isinstance(elapsed_ns, bool) or not isinstance(elapsed_ns, int) or elapsed_ns <= 0:
                raise typer.BadParameter(
                    f"{variant} contains an invalid elapsed_ns value."
                )
            if pair_index in pairs:
                raise typer.BadParameter(
                    f"{variant} contains duplicate pair_index {pair_index}."
                )
            pairs[pair_index] = elapsed_ns
            durations[pair_index] = elapsed_ns
        return durations

    baseline_by_pair = validate_rows(baseline_rows, "baseline")
    candidate_by_pair = validate_rows(candidate_rows, "candidate")

    if set(baseline_by_pair) != set(candidate_by_pair):
        raise typer.BadParameter(
            "Baseline and candidate samples have mismatched pair_index values."
        )

    pair_indices = sorted(baseline_by_pair)
    baseline = [baseline_by_pair[index] for index in pair_indices]
    candidate = [candidate_by_pair[index] for index in pair_indices]

    ratio = paired_median_ratio(baseline, candidate)
    ci_low, ci_high = bootstrap_paired_median_ratio_ci(
        baseline, candidate, confidence=confidence,
        n_resamples=n_resamples, seed=seed,
    )
    p_value = wilcoxon_paired_pvalue(baseline, candidate)
    decision = classify_regression(
        ci_low, ci_high, threshold=threshold, p_value=p_value, alpha=alpha
    )

    baseline_median = statistics.median(baseline)
    candidate_median = statistics.median(candidate)
    generated = datetime.now(UTC).isoformat()
    config_json = baseline_rows[0].get("config_json")
    try:
        config = json.loads(config_json) if config_json else {}
    except (TypeError, json.JSONDecodeError):
        config = {"warning": "Stored configuration was not valid JSON."}

    def unique_values(rows, key):
        values = sorted({str(row.get(key)) for row in rows if row.get(key) is not None})
        return ", ".join(values) if values else "Not recorded"

    report_text = f"""# BenchForge A/B Experiment Report

- Generated (UTC): {generated}
- Run ID: `{run_id}`
- Database: `{database}`
- Matched pairs: {len(pair_indices)}
- Decision: **{decision}**

## Experimental configuration

- Practical regression threshold: {threshold:.2%}
- Confidence level: {confidence:.1%}
- Bootstrap resamples: {n_resamples}
- Random seed for bootstrap: {seed}
- Significance level (alpha): {alpha}
- Stored configuration:

```json
{json.dumps(config, indent=2, sort_keys=True)}
```

## Environment and provenance

- Workload: {unique_values(baseline_rows + candidate_rows, "workload")}
- Operating system: {unique_values(baseline_rows + candidate_rows, "os")}
- Python version: {unique_values(baseline_rows + candidate_rows, "python_version")}
- Baseline binary: {unique_values(baseline_rows, "binary")}
- Candidate binary: {unique_values(candidate_rows, "binary")}
- Schema version: {unique_values(baseline_rows + candidate_rows, "schema_version")}

## Results

| Metric | Baseline | Candidate |
|---|---:|---:|
| Sample count | {len(baseline)} | {len(candidate)} |
| Median latency | {baseline_median:.0f} ns | {candidate_median:.0f} ns |

- Paired median candidate/baseline ratio: **{ratio:.5f}**
- Relative change: **{(ratio - 1) * 100:+.2f}%**
- Paired bootstrap confidence interval ({confidence:.1%}): [{ci_low:.5f}, {ci_high:.5f}]
- Wilcoxon signed-rank p-value: {p_value:.8g}
- Practical regression threshold: {threshold:.2%}
- Classification: **{decision}**

## Interpretation and limitations

A ratio below 1 indicates lower candidate latency; a ratio above 1 indicates higher candidate latency. The classification uses the configured practical threshold, confidence interval, p-value, and significance level.

This result applies to the stored run, workload, binaries, and execution environment. It is not a guarantee of performance on other machines or workloads. Statistical tests do not eliminate measurement noise, environmental bias, or limitations in experimental design.

## Reproducibility

Re-run the analysis with the same stored experiment and parameters:

```powershell
benchforge report --run-id "{run_id}" --database "{database}" --output "{output}" --threshold {threshold} --confidence {confidence} --n-resamples {n_resamples} --seed {seed} --alpha {alpha}
```
"""

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report_text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise typer.BadParameter(f"Could not write report: {exc}") from exc

    print(f"Report written: {output}")
    print(f"Run ID: {run_id}")
    print(f"Matched pairs: {len(pair_indices)}")
    print(f"Decision: {decision}")


@app.command()
def ab(
    baseline_binary: Path = typer.Option(
        Path("build/native-ucrt64/benchforge_lookup.exe"),
        help="Path to the baseline native executable.",
    ),
    candidate_binary: Path = typer.Option(
        Path("build/native-ucrt64/benchforge_lookup.exe"),
        help="Path to the candidate native executable.",
    ),
    runs: int = typer.Option(30, min=2, help="Number of measured pairs."),
    warmup: int = typer.Option(3, min=0, help="Warmup runs per variant."),
    iterations: int = typer.Option(100000, min=1, help="Workload iterations."),
    baseline_extra_work: int = typer.Option(0, min=0),
    candidate_extra_work: int = typer.Option(0, min=0),
    baseline_delay_ns: int = typer.Option(
        0, min=0, help="Artificial delay injected into each baseline measurement (ns)."
    ),
    candidate_delay_ns: int = typer.Option(
        0, min=0, help="Artificial delay injected into each candidate measurement (ns)."
    ),
    seed: int = typer.Option(2026, help="Seed for randomized execution order."),
    output: Path = typer.Option(Path("data/raw/latest-ab.jsonl")),
    database: Path = typer.Option(Path("data/benchforge.sqlite")),
):
    """Run interleaved A/B benchmarks and persist raw samples."""
    records = run_ab_benchmark(
        baseline_binary=baseline_binary,
        candidate_binary=candidate_binary,
        runs=runs,
        warmup=warmup,
        iterations=iterations,
        baseline_extra_work=baseline_extra_work,
        candidate_extra_work=candidate_extra_work,
        baseline_delay_ns=baseline_delay_ns,
        candidate_delay_ns=candidate_delay_ns,
        seed=seed,
    )

    if not records:
        raise typer.BadParameter("A/B runner returned no records.")

    run_ids = {record.get("run_id") for record in records}
    if len(run_ids) != 1 or None in run_ids:
        raise typer.BadParameter("A/B records must share one valid run_id.")

    run_id = next(iter(run_ids))
    baseline = [r for r in records if r.get("variant") == "baseline"]
    candidate = [r for r in records if r.get("variant") == "candidate"]

    if len(baseline) != runs or len(candidate) != runs:
        raise typer.BadParameter("Unexpected number of baseline/candidate samples.")

    config = {
        "baseline_binary": str(baseline_binary.resolve()),
        "candidate_binary": str(candidate_binary.resolve()),
        "runs": runs,
        "warmup": warmup,
        "iterations": iterations,
        "baseline_extra_work": baseline_extra_work,
        "candidate_extra_work": candidate_extra_work,
        "baseline_delay_ns": baseline_delay_ns,
        "candidate_delay_ns": candidate_delay_ns,
        "seed": seed,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    ordered_records = sorted(records, key=lambda r: r["execution_order"])
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for record in ordered_records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    from benchforge.storage import save_experiment_samples

    save_experiment_samples(
        database,
        run_id=run_id,
        baseline=baseline,
        candidate=candidate,
        config=config,
    )

    baseline_median = statistics.median(r["elapsed_ns"] for r in baseline)
    candidate_median = statistics.median(r["elapsed_ns"] for r in candidate)
    ratio = candidate_median / baseline_median

    print(f"Run ID: {run_id}")
    print(f"Baseline samples: {len(baseline)}")
    print(f"Candidate samples: {len(candidate)}")
    print(f"Baseline median: {baseline_median:.0f} ns")
    print(f"Candidate median: {candidate_median:.0f} ns")
    print(f"Candidate/baseline median ratio: {ratio:.5f} ({(ratio - 1) * 100:+.2f}%)")
    print(f"Raw JSONL: {output}")
    print(f"SQLite database: {database}")
    print("Descriptive result only: no statistical regression verdict has been calculated.")


if __name__ == "__main__":
    app()
