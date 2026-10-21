"""BenchForge command-line interface."""

import platform
import sys
from pathlib import Path

import typer
from rich import print

from benchforge import __version__
from benchforge.runner import run_benchmark
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
def compare():
    """Reserved for the baseline/candidate comparison workflow."""
    print(
        "[yellow]Comparison workflow is not enabled yet. "
        "It will be implemented after statistical validation tests pass.[/yellow]"
    )


@app.command()
def report():
    """Reserved for reproducible research report generation."""
    print(
        "[yellow]Report generation is not enabled yet. "
        "It will be implemented after experiment outputs are defined.[/yellow]"
    )


if __name__ == "__main__":
    app()
