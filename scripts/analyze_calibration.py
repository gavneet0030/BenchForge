import csv
from collections import defaultdict
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "data" / "processed" / "calibration-summary.csv"
AGGREGATE = ROOT / "data" / "processed" / "calibration-aggregate.csv"
REPORT = ROOT / "reports" / "generated" / "calibration-report.md"

if not SUMMARY.exists():
    raise SystemExit(f"Missing calibration results: {SUMMARY}")

with SUMMARY.open("r", newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    original_fields = list(reader.fieldnames or [])

if not rows:
    raise SystemExit("Calibration CSV contains no results.")

required = {
    "delay_ns", "trial", "run_id", "effect_ratio",
    "ci_low_percent", "ci_high_percent", "wilcoxon_p", "decision",
}
missing = required - set(original_fields)
if missing:
    raise SystemExit(f"Missing required CSV columns: {sorted(missing)}")

fields = list(original_fields)
if "effect_percent" not in fields:
    fields.append("effect_percent")

groups = defaultdict(list)

for row in rows:
    ratio = float(row["effect_ratio"])
    row["effect_percent"] = f"{(ratio - 1.0) * 100.0:.4f}"
    row["delay_ns"] = str(int(row["delay_ns"]))
    row["trial"] = str(int(row["trial"]))
    row["decision"] = row["decision"].strip().upper()

    if row["decision"] not in {"REGRESSION", "PASS", "INCONCLUSIVE"}:
        raise SystemExit(
            f"Unexpected decision for run {row['run_id']}: {row['decision']}"
        )

    groups[int(row["delay_ns"])].append(row)

with SUMMARY.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

aggregate_fields = [
    "delay_ns", "trials", "regressions", "passes", "inconclusive",
    "observed_regression_fraction", "inconclusive_fraction",
    "median_effect_percent", "min_effect_percent", "max_effect_percent",
    "median_wilcoxon_p",
]

aggregates = []

for delay in sorted(groups):
    items = groups[delay]
    effects = [float(r["effect_percent"]) for r in items]
    p_values = [float(r["wilcoxon_p"]) for r in items]

    regressions = sum(r["decision"] == "REGRESSION" for r in items)
    passes = sum(r["decision"] == "PASS" for r in items)
    inconclusive = sum(r["decision"] == "INCONCLUSIVE" for r in items)
    total = len(items)

    aggregates.append({
        "delay_ns": delay,
        "trials": total,
        "regressions": regressions,
        "passes": passes,
        "inconclusive": inconclusive,
        "observed_regression_fraction": f"{regressions / total:.4f}",
        "inconclusive_fraction": f"{inconclusive / total:.4f}",
        "median_effect_percent": f"{median(effects):.4f}",
        "min_effect_percent": f"{min(effects):.4f}",
        "max_effect_percent": f"{max(effects):.4f}",
        "median_wilcoxon_p": f"{median(p_values):.8g}",
    })

with AGGREGATE.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=aggregate_fields)
    writer.writeheader()
    writer.writerows(aggregates)

lines = [
    "# BenchForge Calibration Report",
    "",
    "## Experimental configuration",
    "",
    "- Workload iterations per measurement: 100,000",
    "- Matched pairs per trial: 30",
    "- Trial counts are derived from the input CSV and shown in the results table.",
    "- Practical regression threshold: 3%",
    "- Confidence interval: 95% paired bootstrap CI",
    "- Significance level: alpha = 0.05",
    "",
    "## Results",
    "",
    "| Injected delay (ns) | Trials | Regression | Pass | Inconclusive | Median observed effect |",
    "|---:|---:|---:|---:|---:|---:|",
]

for item in aggregates:
    lines.append(
        f"| {item['delay_ns']} | {item['trials']} | "
        f"{item['regressions']} | {item['passes']} | "
        f"{item['inconclusive']} | {item['median_effect_percent']}% |"
    )

lines.extend([
    "",
    "## Interpretation",
    "",
    ("The observed regression fraction is the proportion of trials classified "
    "as REGRESSION in this experiment. It is not a precise estimate of "
    "statistical power."),
    "",
    "The zero-delay condition is the unchanged-code control. Its observed decision counts are shown in the results table; this batch alone is not sufficient to establish a reliable false-positive rate or confident PASS behavior.",
    "",
    "Injected-delay conditions must be interpreted using the observed counts in the results table. Detection fractions from this experiment are preliminary and do not establish guaranteed detection probability.",
    "",
    ("Injected delay is an absolute time, not a fixed percentage slowdown. "
    "Observed effect sizes depend on workload duration, execution order, "
    "machine load, and timing noise."),
    "",
    "## Reproducibility",
    "",
    ("Trial-level results retain each run ID, injected delay, effect ratio, "
    "confidence-interval bounds, p-value, and decision in the CSV."),
    "",
])

REPORT.parent.mkdir(parents=True, exist_ok=True)
REPORT.write_text("\n".join(lines), encoding="utf-8")

print(f"Trials analyzed: {len(rows)}")
print(f"Enriched results: {SUMMARY.relative_to(ROOT)}")
print(f"Aggregated results: {AGGREGATE.relative_to(ROOT)}")
print(f"Research report: {REPORT.relative_to(ROOT)}")
print()
print("Delay-wise summary:")
for item in aggregates:
    print(
        f"  {item['delay_ns']:>8} ns | "
        f"REGRESSION {item['regressions']}/{item['trials']} | "
        f"PASS {item['passes']}/{item['trials']} | "
        f"INCONCLUSIVE {item['inconclusive']}/{item['trials']}"
    )
