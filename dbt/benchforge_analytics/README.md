# Local BenchForge dbt workflow

This project uses the optional `dbt-duckdb` adapter and a local DuckDB file;
it needs no server, container, credentials, or cloud account. The checked-in
CSV seed is synthetic and uses the same sample columns as
`benchforge.storage`; it is a reproducible example, not a real benchmark run.
The seed remains the default input. For an explicit snapshot of real local
history, export it with the built-in CLI:

```powershell
$historyCsv = Join-Path $env:TEMP "benchforge-history.csv"
benchforge export-history --database "data/benchforge.sqlite" --output $historyCsv
$env:BENCHFORGE_HISTORY_CSV_PATH = $historyCsv
$env:BENCHFORGE_DUCKDB_PATH = (Join-Path $env:TEMP "benchforge-analytics.duckdb")
```

The export opens SQLite in read-only mode, includes only the benchmark sample
columns used by these models, and is limited to 100,000 rows and 10 MB. The
destination must be a new file; it is not overwritten. The model reads that
local CSV only when `BENCHFORGE_HISTORY_CSV_PATH` is set. Otherwise dbt uses
the synthetic seed. The CSV is a point-in-time snapshot and is not kept in
sync with SQLite.

Install the extra with `python -m pip install -e ".[dbt]"`. Copy
`profiles.yml.example` to a profiles directory outside the repository, such as
`%USERPROFILE%\.dbt\profiles.yml`, and set `BENCHFORGE_DUCKDB_PATH` to an
absolute local `.duckdb` file path if the default `target/` location is not
suitable. The profile stores no secrets.

From this directory, run:

```powershell
dbt debug
dbt parse
dbt compile
dbt build
```

`dbt build` builds typed staging views and a workload/variant summary mart,
then runs schema and relationship tests. It uses the seed by default or the
local exported history snapshot when the environment variable above is set.
dbt target files and logs are generated outputs; direct them outside the
repository when running a verification job. Clear the override to return to
the deterministic seed:

```powershell
Remove-Item Env:BENCHFORGE_HISTORY_CSV_PATH -ErrorAction SilentlyContinue
```

The export is explicit and read-only; there is no automatic database scan,
live synchronization, or remote connection.
