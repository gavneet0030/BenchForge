select
    run_id,
    workload,
    variant,
    count(*) as sample_count,
    median(elapsed_ns) as median_elapsed_ns,
    quantile_cont(elapsed_ns, 0.95) as p95_elapsed_ns
from {{ ref('stg_benchmark_samples') }}
group by run_id, workload, variant
