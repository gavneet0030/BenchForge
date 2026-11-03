{% macro benchmark_samples_source() %}
    {% set history_csv = env_var('BENCHFORGE_HISTORY_CSV_PATH', '') %}
    {% if history_csv %}
        {% set local_path = history_csv | replace('\\', '/') %}
        {% if '://' in local_path or local_path.startswith('//') or '\n' in local_path or '\r' in local_path %}
            {{ exceptions.raise_compiler_error(
                'BENCHFORGE_HISTORY_CSV_PATH must be a local path without a URI, UNC prefix, or newline.'
            ) }}
        {% endif %}
        {% set escaped_path = local_path | replace("'", "''") %}
        {{ return("read_csv_auto('" ~ escaped_path ~ "', header=true)") }}
    {% else %}
        {{ return(ref('benchmark_samples')) }}
    {% endif %}
{% endmacro %}
