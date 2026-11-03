import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchforge.cli import app
from benchforge.storage import save_experiment_samples, save_samples

runner = CliRunner()


def test_run_cli_rejects_values_above_execution_limits():
    result = runner.invoke(app, ["run", "--runs", "1001"])

    assert result.exit_code == 2
    assert "1000" in result.output


def test_ab_cli_rejects_values_above_execution_limits():
    result = runner.invoke(app, ["ab", "--runs", "1001"])

    assert result.exit_code == 2
    assert "1000" in result.output


def test_cpu_comparison_cli_reports_only_checksum_verified_samples(
    tmp_path, monkeypatch
):
    records = [
        {
            "run_id": "cpu-run",
            "checksum": 123,
            "variant": variant,
            "elapsed_ns": elapsed,
        }
        for variant, elapsed in (
            ("python", 100),
            ("native", 20),
            ("python", 110),
            ("native", 22),
        )
    ]
    monkeypatch.setattr(
        "benchforge.phase4.run_cpu_comparison",
        lambda *args, **kwargs: records,
    )
    monkeypatch.setattr("benchforge.cli._native_binary", lambda name: tmp_path / name)

    result = runner.invoke(
        app,
        ["compare-cpu", "--runs", "2", "--warmup", "0", "--iterations", "10"],
    )

    assert result.exit_code == 0
    assert "Correctness checksum: 123" in result.output
    assert "Matched sample pairs: 2" in result.output


def test_engine_comparison_cli_keeps_duckdb_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "benchforge.phase4.compare_sqlite_duckdb",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("DuckDB analytics requires optional dependencies")
        ),
    )

    result = runner.invoke(
        app,
        ["compare-engines", "--rows", "100", "--groups", "10", "--runs", "2"],
    )

    assert result.exit_code == 2
    assert "optional dependencies" in result.output


def test_export_history_cli_writes_bounded_read_only_snapshot(tmp_path):
    database = tmp_path / "history.sqlite"
    output = tmp_path / "history.csv"
    save_samples(database, make_records([1000, 1100]), run_id="history-run")
    before = database.stat()

    result = runner.invoke(
        app,
        ["export-history", "--database", str(database), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert "Exported 2 benchmark observations" in result.output
    assert output.is_file()
    assert database.stat().st_mtime_ns == before.st_mtime_ns


def make_records(durations):
    return [
        {
            "timestamp_utc": f"2026-10-21T10:00:{index:02d}+00:00",
            "workload": "hash_lookup",
            "sample_index": index,
            "elapsed_ns": duration,
            "extra_work": 0,
            "os": "test-os",
            "python_version": "3.x",
            "schema_version": 1,
            "binary": "benchforge_lookup.exe",
            "pair_index": index,
            "execution_order": index * 2,
            "seed": 2026,
        }
        for index, duration in enumerate(durations)
    ]


def create_experiment(database, baseline, candidate):
    return save_experiment_samples(
        database,
        run_id="cli-test-run",
        baseline=make_records(baseline),
        candidate=make_records(candidate),
        config={"seed": 2026},
    )


def test_compare_cli_reports_paired_results(tmp_path):
    database = tmp_path / "history.sqlite"
    create_experiment(
        database,
        baseline=[1000, 1010, 990, 1005, 995, 1002],
        candidate=[1001, 1009, 991, 1006, 996, 1003],
    )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "cli-test-run",
            "--database", str(database),
            "--n-resamples", "100",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Matched pairs: 6" in result.output
    assert "paired bootstrap CI" in result.output
    assert "Wilcoxon signed-rank p-value" in result.output
    assert "Decision:" in result.output


def test_compare_cli_rejects_missing_database(tmp_path):
    database = tmp_path / "missing.sqlite"

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "missing-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "Database does not exist" in result.output


def test_compare_cli_rejects_unknown_run_id(tmp_path):
    database = tmp_path / "history.sqlite"
    create_experiment(
        database,
        baseline=[1000, 1010, 990],
        candidate=[1001, 1011, 991],
    )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "unknown-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "both baseline and candidate" in result.output


def test_compare_and_report_reject_fewer_than_five_pairs(tmp_path):
    database = tmp_path / "small-sample.sqlite"
    create_experiment(database, [1000, 1010], [1001, 1011])

    compare_result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "cli-test-run",
            "--database", str(database),
        ],
    )
    report_result = runner.invoke(
        app,
        [
            "report",
            "--run-id", "cli-test-run",
            "--database", str(database),
            "--output", str(tmp_path / "report.md"),
        ],
    )

    assert compare_result.exit_code != 0
    assert "At least 5 paired observations" in compare_result.output
    assert report_result.exit_code != 0
    assert "At least 5 paired observations" in report_result.output



def test_compare_cli_rejects_mismatched_pair_indices(tmp_path):
    database = tmp_path / "mismatched.sqlite"

    baseline = make_records([1000, 1010, 990])
    candidate = make_records([1001, 1011, 991])
    candidate[2]["pair_index"] = 99

    save_experiment_samples(
        database,
        run_id="cli-test-run",
        baseline=baseline,
        candidate=candidate,
        config={"seed": 2026},
    )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "cli-test-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "pair_index sets do not match" in result.output


def test_compare_cli_rejects_duplicate_pair_indices(tmp_path):
    database = tmp_path / "duplicate.sqlite"

    baseline = make_records([1000, 1010, 990])
    candidate = make_records([1001, 1011, 991])
    candidate[1]["pair_index"] = candidate[0]["pair_index"]

    save_experiment_samples(
        database,
        run_id="cli-test-run",
        baseline=baseline,
        candidate=candidate,
        config={"seed": 2026},
    )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "cli-test-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "Duplicate candidate pair_index" in result.output

def test_compare_cli_rejects_invalid_stored_duration(tmp_path):
    import sqlite3

    database = tmp_path / "invalid-duration.sqlite"
    create_experiment(
        database,
        baseline=[1000, 1010, 990],
        candidate=[1001, 1011, 991],
    )

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        cursor = connection.execute(
            "UPDATE benchmark_samples SET elapsed_ns = 0 "
            "WHERE run_id = ? AND variant = ? AND sample_index = ?",
            ("cli-test-run", "baseline", 0),
        )
        if cursor.rowcount != 1:
            raise AssertionError(
                f"Expected to alter one sample; updated {cursor.rowcount}."
            )

    result = runner.invoke(
        app,
        [
            "compare",
            "--run-id", "cli-test-run",
            "--database", str(database),
        ],
    )

    assert result.exit_code != 0
    assert "invalid elapsed_ns" in result.output
    assert "Traceback" not in result.output



def test_report_cli_writes_markdown(tmp_path):
    database = tmp_path / "benchforge.sqlite"
    output = tmp_path / "generated" / "report.md"
    create_experiment(database, [100, 110, 105, 108, 103], [90, 95, 92, 94, 91])

    result = runner.invoke(
        app,
        [
            "report", "--run-id", "cli-test-run",
            "--database", str(database), "--output", str(output),
            "--n-resamples", "100",
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.is_file()
    content = output.read_text(encoding="utf-8")
    assert "# BenchForge A/B Experiment Report" in content
    assert "Paired bootstrap confidence interval" in content
    assert "Wilcoxon signed-rank p-value" in content
    assert "cli-test-run" in content


def test_report_cli_refuses_to_overwrite_without_force(tmp_path):
    database = tmp_path / "benchforge.sqlite"
    output = tmp_path / "report.md"
    create_experiment(database, [100, 110, 105, 108, 103], [90, 95, 92, 94, 91])
    output.write_text("keep this report", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "report", "--run-id", "cli-test-run",
            "--database", str(database), "--output", str(output),
            "--n-resamples", "100",
        ],
    )

    assert result.exit_code != 0
    assert "keep this report" == output.read_text(encoding="utf-8")


def test_report_cli_requires_existing_database(tmp_path):
    result = runner.invoke(
        app,
        [
            "report", "--run-id", "missing-run",
            "--database", str(tmp_path / "missing.sqlite"),
            "--output", str(tmp_path / "report.md"),
        ],
    )
    assert result.exit_code != 0
    assert "Database does not exist" in result.output


def test_export_csv_cli_exports_stored_samples(tmp_path):
    database = tmp_path / "history.sqlite"
    output = tmp_path / "export.csv"
    create_experiment(database, [100, 110, 105, 108, 103], [90, 95, 92, 94, 91])

    result = runner.invoke(
        app,
        [
            "export-csv",
            "--database", str(database),
            "--run-id", "cli-test-run",
            "--output", str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Exported 10 observations" in result.output
    assert "run_id" in output.read_text(encoding="utf-8-sig")


def test_compare_history_cli_requires_explicit_compatible_reference(tmp_path):
    database = tmp_path / "history.sqlite"
    save_experiment_samples(
        database,
        run_id="reference",
        baseline=make_records([100, 101, 99, 102, 98]),
        candidate=make_records([100, 101, 99, 102, 98]),
    )
    save_experiment_samples(
        database,
        run_id="current",
        baseline=make_records([100, 101, 99, 102, 98]),
        candidate=make_records([120, 121, 119, 122, 118]),
    )

    result = runner.invoke(
        app,
        [
            "compare-history",
            "--run-id", "current",
            "--reference-run-id", "reference",
            "--database", str(database),
            "--n-resamples", "100",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Reference run: reference" in result.output
    assert "Mann-Whitney U p-value" in result.output
    assert "Decision: REGRESSION" in result.output


def test_dashboard_cli_rejects_non_loopback_host():
    result = runner.invoke(
        app,
        ["dashboard", "--host", "0.0.0.0"],
    )
    assert result.exit_code != 0
    assert "loopback" in result.output


def test_dashboard_cli_reads_environment_configuration(monkeypatch, tmp_path):
    from benchforge import dashboard as dashboard_module

    database = tmp_path / "environment.sqlite"
    data_dir = tmp_path / "processed"
    documents_dir = tmp_path / "documents"
    observed = {}
    monkeypatch.setattr(
        dashboard_module,
        "serve",
        lambda **kwargs: observed.update(kwargs),
    )
    for name in (
        "BENCHFORGE_DATABASE",
        "BENCHFORGE_DATA_DIR",
        "BENCHFORGE_DOCUMENTS_DIR",
        "BENCHFORGE_MAX_PDF_BYTES",
        "BENCHFORGE_HOST",
        "BENCHFORGE_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BENCHFORGE_DATABASE", str(database))
    monkeypatch.setenv("BENCHFORGE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("BENCHFORGE_DOCUMENTS_DIR", str(documents_dir))
    monkeypatch.setenv("BENCHFORGE_MAX_PDF_BYTES", "2097152")
    monkeypatch.setenv("BENCHFORGE_HOST", "127.0.0.1")
    monkeypatch.setenv("BENCHFORGE_PORT", "9876")

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 0, result.output
    assert observed == {
        "database": database,
        "data_dir": data_dir,
        "documents_dir": documents_dir,
        "max_pdf_bytes": 2_097_152,
        "host": "127.0.0.1",
        "port": 9876,
        "allow_remote": False,
    }


def test_dashboard_cli_options_override_environment_configuration(
    monkeypatch, tmp_path
):
    from benchforge import dashboard as dashboard_module

    observed = {}
    monkeypatch.setattr(
        dashboard_module,
        "serve",
        lambda **kwargs: observed.update(kwargs),
    )
    for name in (
        "BENCHFORGE_DATABASE",
        "BENCHFORGE_DATA_DIR",
        "BENCHFORGE_DOCUMENTS_DIR",
        "BENCHFORGE_MAX_PDF_BYTES",
        "BENCHFORGE_HOST",
        "BENCHFORGE_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BENCHFORGE_DATABASE", str(tmp_path / "env.sqlite"))
    monkeypatch.setenv("BENCHFORGE_DATA_DIR", str(tmp_path / "env-data"))
    monkeypatch.setenv("BENCHFORGE_DOCUMENTS_DIR", str(tmp_path / "env-documents"))
    monkeypatch.setenv("BENCHFORGE_MAX_PDF_BYTES", "2097152")
    monkeypatch.setenv("BENCHFORGE_HOST", "127.0.0.1")
    monkeypatch.setenv("BENCHFORGE_PORT", "9876")

    database = tmp_path / "cli.sqlite"
    data_dir = tmp_path / "cli-data"
    documents_dir = tmp_path / "cli-documents"
    result = runner.invoke(
        app,
        [
            "dashboard",
            "--database", str(database),
            "--data-dir", str(data_dir),
            "--documents-dir", str(documents_dir),
            "--max-pdf-bytes", "4194304",
            "--host", "localhost",
            "--port", "8766",
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed == {
        "database": database,
        "data_dir": data_dir,
        "documents_dir": documents_dir,
        "max_pdf_bytes": 4_194_304,
        "host": "localhost",
        "port": 8766,
        "allow_remote": False,
    }


@pytest.mark.parametrize(
    ("command", "monkeypatch_target", "failure", "message"),
    [
        ("run", "benchforge.cli.run_benchmark", TimeoutError("timed out"), "timed out"),
        ("ab", "benchforge.cli.run_ab_benchmark", RuntimeError("exit code 3"), "exit code 3"),
    ],
)
def test_cli_reports_expected_native_failures_without_tracebacks(
    tmp_path, monkeypatch, command, monkeypatch_target, failure, message
):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(monkeypatch_target, fail)
    arguments = [
        command,
        "--runs", "2",
        "--warmup", "0",
        "--iterations", "1",
        "--database", str(tmp_path / "history.sqlite"),
    ]
    if command == "run":
        arguments.extend(["--binary", str(tmp_path / "native.exe")])

    result = runner.invoke(app, arguments)

    assert result.exit_code != 0
    assert message in result.output
    assert "Traceback" not in result.output


def test_ab_cli_defaults_to_optimized_candidate(tmp_path, monkeypatch):
    observed = {}

    def fake_run_ab_benchmark(**kwargs):
        observed.update(kwargs)
        return [
            {
                "run_id": "default-binaries",
                "variant": variant,
                "execution_order": index,
                "elapsed_ns": 1000 + index,
            }
            for index, variant in enumerate(
                ["baseline", "candidate"] * kwargs["runs"]
            )
        ]

    monkeypatch.setattr("benchforge.cli.run_ab_benchmark", fake_run_ab_benchmark)
    monkeypatch.setattr(
        "benchforge.storage.save_experiment_samples",
        lambda *args, **kwargs: None,
    )

    result = runner.invoke(
        app,
        [
            "ab",
            "--runs", "2",
            "--warmup", "0",
            "--iterations", "1",
            "--output", str(tmp_path / "samples.jsonl"),
            "--database", str(tmp_path / "history.sqlite"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed["baseline_binary"].name == "benchforge_lookup.exe"
    assert observed["candidate_binary"].name == "benchforge_lookup_optimized.exe"


def test_workload_cli_persists_bounded_native_samples(tmp_path, monkeypatch):
    observed = {}

    def fake_run_native_workload(binary, **kwargs):
        observed.update(kwargs)
        return [
            {
                "timestamp_utc": f"2026-10-28T10:00:0{index}+00:00",
                "workload": kwargs["workload"],
                "sample_index": index,
                "elapsed_ns": 200 + index,
                "bytes_processed": 131072,
                "schema_version": 1,
                "os": "test-os",
                "python_version": "3.x",
                "binary": Path(binary).name,
            }
            for index in range(kwargs["runs"])
        ]

    monkeypatch.setattr(
        "benchforge.workload.run_native_workload", fake_run_native_workload
    )
    output = tmp_path / "workload.jsonl"
    database = tmp_path / "history.sqlite"
    result = runner.invoke(
        app,
        [
            "workload",
            "--workload", "memory",
            "--binary", str(tmp_path / "native.exe"),
            "--runs", "2",
            "--warmup", "0",
            "--output", str(output),
            "--database", str(database),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Median throughput:" in result.output
    assert observed["iterations"] == 10
    assert observed["working_set_kib"] == 1024
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT workload, elapsed_ns, variant FROM benchmark_samples"
        ).fetchall()
    assert rows == [("memory", 200, "single"), ("memory", 201, "single")]


def test_datasets_list_cli_reports_sources_without_network(tmp_path):
    from benchforge.public_data import DATASET_CATALOG

    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    processed_dir.mkdir()
    result = runner.invoke(
        app,
        [
            "datasets",
            "list",
            "--raw-dir",
            str(raw_dir),
            "--processed-dir",
            str(processed_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(DATASET_CATALOG) == 7
    assert "bike: IMPLEMENTED" in result.output
    assert "not downloaded" in result.output
    assert "not implemented" in result.output.lower()


def test_datasets_fetch_cli_uses_bounded_downloader(tmp_path, monkeypatch):
    source = tmp_path / "bike-sharing-dataset.zip"
    source.write_bytes(b"fixture")
    observed = {}

    def fake_download(dataset, raw_dir):
        observed["dataset"] = dataset
        observed["raw_dir"] = raw_dir
        return source

    monkeypatch.setattr("benchforge.public_data._download", fake_download)
    result = runner.invoke(
        app,
        ["datasets", "fetch", "bike", "--raw-dir", str(tmp_path / "raw")],
    )

    assert result.exit_code == 0, result.output
    assert observed == {"dataset": "bike", "raw_dir": tmp_path / "raw"}
    assert "Fetched bike:" in result.output
    assert "7 bytes" in result.output


def test_datasets_fetch_cli_rejects_metadata_only_sources():
    result = runner.invoke(app, ["datasets", "fetch", "citibike"])

    assert result.exit_code != 0
    assert "Dataset is not implemented" in result.output


def test_datasets_validate_cli_processes_local_source(tmp_path):
    import json
    import zipfile

    source = tmp_path / "bike.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "hour.csv",
            "instant,dteday,hr,cnt,casual,registered,temp\n"
            "1,2011-01-01,8,12,2,10,0.5\n",
        )
    output_dir = tmp_path / "summaries"
    result = runner.invoke(
        app,
        [
            "datasets",
            "validate",
            "bike",
            "--source",
            str(source),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads((output_dir / "bike.json").read_text(encoding="utf-8"))
    assert summary["rows_processed"] == 1
    assert summary["source_sha256"]
    assert "Validated 1 rows for bike" in result.output


def test_analytics_cli_help_lists_bounded_commands():
    result = runner.invoke(app, ["analytics", "--help"])

    assert result.exit_code == 0
    assert "inspect" in result.output
    assert "summarize" in result.output


def test_analytics_cli_reports_missing_optional_engine(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def without_duckdb(name, *args, **kwargs):
        if name == "duckdb":
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_duckdb)
    source = tmp_path / "input.csv"
    source.write_text("region,value\nnorth,5\n", encoding="utf-8")

    result = runner.invoke(app, ["analytics", "inspect", str(source)])

    assert result.exit_code != 0
    assert "install BenchForge with the analytics extra" in result.output


def test_quality_and_catalog_cli_validate_and_list_metadata(tmp_path):
    source = tmp_path / "events.csv"
    source.write_text("region,value\nnorth,5\n", encoding="utf-8")
    database = tmp_path / "catalog.sqlite"

    validate = runner.invoke(
        app,
        [
            "catalog",
            "validate",
            str(source),
            "--required-column",
            "region",
            "--not-null",
            "value",
            "--numeric-range",
            "value:0:10",
            "--database",
            str(database),
        ],
    )
    assert validate.exit_code == 0, validate.output
    assert '"status": "PASS"' in validate.output

    listed = runner.invoke(app, ["catalog", "list", "--database", str(database)])
    assert listed.exit_code == 0, listed.output
    assert '"quality_status": "PASS"' in listed.output
    assert "events.csv" in listed.output

    invalid = tmp_path / "invalid.csv"
    invalid.write_text("region,value\nnorth,bad\n", encoding="utf-8")
    failed = runner.invoke(
        app,
        [
            "quality",
            "validate",
            str(invalid),
            "--numeric-range",
            "value:0:10",
        ],
    )
    assert failed.exit_code == 1
    assert '"status": "FAIL"' in failed.output


def test_catalog_cli_reports_lineage_after_ingestion(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("region,value\nnorth,5\n", encoding="utf-8")
    database = tmp_path / "history.sqlite"
    from benchforge.pipeline import ingest_csv

    ingest_csv(database, source, table="events")
    result = runner.invoke(
        app,
        [
            "catalog",
            "lineage",
            f"file:{source.resolve().as_uri()}",
            "--database",
            str(database),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "events" in result.output
    assert '"edges"' in result.output
