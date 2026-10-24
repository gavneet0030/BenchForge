# BenchForge Research Plan

## Research question

How reliably can meaningful software performance regressions be detected on a noisy local machine, and how many observations are required?

## Primary measurements

- Raw elapsed nanoseconds.
- Paired candidate/baseline median ratio.
- Paired bootstrap confidence interval for the median ratio.
- Wilcoxon signed-rank p-value for paired latency differences.
- Mann-Whitney U p-value for separate method-comparison experiments.
- False-positive rate and detection power.
- Operating system, compiler, build flags and commit metadata.

## Planned experiments

- A/A validation.
- Injected workload perturbations.
- Power curves.
- Method comparison.
- Background-noise stress tests.
- Public historical benchmark case study, conditional on data availability and provenance.

## Limitations

The workload perturbation knob is not a calibrated percentage slowdown. Hardware measurements are noisy. A p-value alone does not prove a practical regression. Change-point detections require external validation before being interpreted as real regressions.
