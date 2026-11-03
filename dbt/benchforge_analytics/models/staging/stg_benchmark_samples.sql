with source as (
    select * from {{ benchmark_samples_source() }}
)

select
    cast(timestamp_utc as timestamptz) as timestamp_utc,
    cast(workload as varchar) as workload,
    cast(sample_index as integer) as sample_index,
    cast(elapsed_ns as bigint) as elapsed_ns,
    cast(extra_work as bigint) as extra_work,
    cast(os as varchar) as os,
    cast(python_version as varchar) as python_version,
    cast(schema_version as integer) as schema_version,
    cast(run_id as varchar) as run_id,
    cast(variant as varchar) as variant,
    cast("binary" as varchar) as "binary",
    cast(config_json as varchar) as config_json,
    cast(pair_index as integer) as pair_index,
    cast(execution_order as integer) as execution_order,
    cast(seed as bigint) as seed,
    md5(concat_ws('|', run_id, workload, variant, cast(sample_index as varchar))) as record_key
from source
