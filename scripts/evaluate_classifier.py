from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ["PATH"] = r"C:\msys64\ucrt64\bin" + os.pathsep + os.environ.get("PATH", "")

from benchforge.ab import run_ab_benchmark
from benchforge.stats import (
    bootstrap_paired_median_ratio_ci,
    classify_regression,
    wilcoxon_paired_pvalue,
)


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate BenchForge's paired A/B regression classifier."
    )
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--pairs", type=int, default=30)
    parser.add_argument("--iterations", type=int, default=100000)
    parser.add_argument("--warmup", type=int, default=2)
    args = parser.parse_args()

    if args.trials < 1 or args.pairs < 2 or args.iterations < 1:
        parser.error("trials >= 1, pairs >= 2 and iterations >= 1 are required")
    if args.warmup < 0:
        parser.error("warmup cannot be negative")

    binary = ROOT / "build" / "native-ucrt64" / "benchforge_lookup.exe"
    if not binary.is_file():
        raise FileNotFoundError(f"Native executable not found: {binary}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw_dir = ROOT / "data" / "raw"
    processed_dir = ROOT / "data" / "processed"
    report_dir = ROOT / "reports" / "generated"

    for folder in (raw_dir, processed_dir, report_dir):
        folder.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"classifier-evaluation-{stamp}.jsonl"
    csv_path = processed_dir / f"classifier-evaluation-{stamp}.csv"
    report_path = report_dir / f"classifier-evaluation-{stamp}.md"

    delay_levels = (0, 250000, 1000000)
    summaries = []
    total = len(delay_levels) * args.trials
    completed = 0

    with raw_path.open("w", encoding="utf-8") as raw_file:
        for delay_ns in delay_levels:
            for trial in range(1, args.trials + 1):
                seed = 20261021 + delay_ns + trial

                records = run_ab_benchmark(
                    binary,
                    binary,
                    runs=args.pairs,
                    warmup=args.warmup,
                    iterations=args.iterations,
                    seed=seed,
                    baseline_delay_ns=0,
                    candidate_delay_ns=delay_ns,
                )

                for record in records:
                    raw_file.write(
                        json.dumps(
                            {
                                "condition_delay_ns": delay_ns,
                                "trial_number": trial,
                                **record,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )

                baseline_map = {
                    r["pair_index"]: r["elapsed_ns"] for r in records if r["variant"] == "baseline"
                }
                candidate_map = {
                    r["pair_index"]: r["elapsed_ns"] for r in records if r["variant"] == "candidate"
                }
                pair_ids = sorted(set(baseline_map) & set(candidate_map))

                if len(pair_ids) != args.pairs:
                    raise RuntimeError(f"Expected {args.pairs} matched pairs; got {len(pair_ids)}")

                baseline = [baseline_map[i] for i in pair_ids]
                candidate = [candidate_map[i] for i in pair_ids]
                ratios = [c / b for b, c in zip(baseline, candidate)]
                median_ratio = statistics.median(ratios)

                ci_low, ci_high = bootstrap_paired_median_ratio_ci(
                    baseline,
                    candidate,
                    confidence=0.95,
                    n_resamples=5000,
                    seed=seed,
                )
                p_value = wilcoxon_paired_pvalue(baseline, candidate)
                decision = classify_regression(
                    ci_low,
                    ci_high,
                    threshold=0.03,
                    p_value=p_value,
                    alpha=0.05,
                )

                summaries.append(
                    {
                        "delay_ns": delay_ns,
                        "trial_number": trial,
                        "run_id": records[0]["run_id"],
                        "pairs": len(pair_ids),
                        "median_effect_pct": (median_ratio - 1) * 100,
                        "ci_low_pct": (ci_low - 1) * 100,
                        "ci_high_pct": (ci_high - 1) * 100,
                        "wilcoxon_p_value": p_value,
                        "decision": decision,
                        "baseline_median_ns": statistics.median(baseline),
                        "candidate_median_ns": statistics.median(candidate),
                    }
                )

                completed += 1
                print(
                    f"[{completed}/{total}] delay={delay_ns} ns "
                    f"trial={trial}/{args.trials} "
                    f"effect={summaries[-1]['median_effect_pct']:.2f}% "
                    f"decision={decision}",
                    flush=True,
                )

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=list(summaries[0].keys()),
        )
        writer.writeheader()
        writer.writerows(summaries)

    lines = [
        "# BenchForge Classifier Evaluation",
        "",
        f"- Generated: {datetime.now(UTC).isoformat()}",
        f"- Trials per condition: {args.trials}",
        f"- Matched pairs per trial: {args.pairs}",
        f"- Iterations per invocation: {args.iterations}",
        f"- Warmup runs: {args.warmup}",
        "- Regression threshold: 3%",
        "- Significance level: 0.05",
        "- Confidence interval: paired bootstrap, 95%, 5,000 resamples",
        "",
        "| Injected delay (ns) | Trials | Median effect | REGRESSION | PASS | INCONCLUSIVE |",
        "|---:|---:|---:|---:|---:|---:|",
    ]

    for delay_ns in delay_levels:
        group = [s for s in summaries if s["delay_ns"] == delay_ns]
        counts = Counter(s["decision"] for s in group)
        effect = statistics.median(s["median_effect_pct"] for s in group)
        lines.append(
            f"| {delay_ns} | {len(group)} | {effect:.2f}% | "
            f"{counts['REGRESSION']} | {counts['PASS']} | "
            f"{counts['INCONCLUSIVE']} |"
        )

    controls = [s for s in summaries if s["delay_ns"] == 0]
    false_positives = sum(s["decision"] == "REGRESSION" for s in controls)

    lines.extend(
        [
            "",
            "## Interpretation and limitations",
            "",
            (
                f"- Zero-delay control regression decisions: {false_positives}/"
                f"{len(controls)} "
                f"({100 * false_positives / len(controls):.1f}% observed)."
            ),
            ("- Injected delay is an intervention, not proof of a particular percentage slowdown."),
            "- These results are exploratory, not definitive calibration.",
            (
                "- Zero observed false positives does not prove a zero "
                "population false-positive rate."
            ),
            "",
            "## Artifacts",
            "",
            f"- Raw records: `{raw_path.relative_to(ROOT).as_posix()}`",
            f"- Summary CSV: `{csv_path.relative_to(ROOT).as_posix()}`",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print("\nEVALUATION COMPLETE")
    print(f"RAW_JSONL: {raw_path}")
    print(f"SUMMARY_CSV: {csv_path}")
    print(f"REPORT_MD: {report_path}")


if __name__ == "__main__":
    main()
