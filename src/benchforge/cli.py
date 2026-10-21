"""BenchForge command-line interface."""

import json
import platform
import statistics
import sys
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
        raise typer.BadParameter(
            "Run ID must contain both baseline and candidate samples."
        )

    def index_rows(rows, variant):
        indexed = {}
        for row in rows:
            pair_index = row.get("pair_index")
            elapsed_ns = row.get("elapsed_ns")
            if isinstance(pair_index, bool) or not isinstance(pair_index, int):
                raise typer.BadParameter(
                    f"{variant} samples are missing a valid pair_index."
                )
            if pair_index in indexed:
                raise typer.BadParameter(
                    f"Duplicate {variant} pair_index: {pair_index}"
                )
            indexed[pair_index] = elapsed_ns
        return indexed

    baseline_by_pair = index_rows(baseline_rows, "baseline")
    candidate_by_pair = index_rows(candidate_rows, "candidate")

    if set(baseline_by_pair) != set(candidate_by_pair):
        raise typer.BadParameter(
            "Baseline and candidate pair_index sets do not match."
        )

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
    print(
        f"{confidence:.1%} paired bootstrap CI: "
        f"[{ci_low:.5f}, {ci_high:.5f}]"
    )
    print(f"Wilcoxon signed-rank p-value: {p_value:.6g}")
    print(f"Practical regression threshold: {threshold:.1%}")
    print(f"Significance level alpha: {alpha:.3f}")
    print(f"Decision: {decision}")
    print(
        "Interpretation: ratios above 1 indicate slower candidate latency. "
        "The verdict depends on this run, its pairing, and the selected policy."
    )


@app.command()
def report():
    """Reserved for reproducible research report generation."""
    print(
        "[yellow]Report generation is not enabled yet. "
        "It will be implemented after experiment outputs are defined.[/yellow]"
    )


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

    from benchforge.storage import save_samples

    save_samples(
        database, baseline, run_id=run_id, variant="baseline", config=config
    )
    save_samples(
        database, candidate, run_id=run_id, variant="candidate", config=config
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
