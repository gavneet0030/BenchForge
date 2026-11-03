select distinct run_id
from {{ ref('stg_benchmark_samples') }}
