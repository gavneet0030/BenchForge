import sqlite3

import pytest

from benchforge.storage import get_samples, initialize_database, save_samples


def make_records(count=3):
    return [
        {
            "timestamp_utc": f"2026-10-21T10:00:0{i}+00:00",
            "workload": "hash_lookup",
            "sample_index": i,
            "elapsed_ns": 1000 + i,
            "extra_work": 0,
            "os": "test-os",
            "python_version": "3.x",
            "schema_version": 1,
            "binary": "benchforge_lookup.exe",
        }
        for i in range(count)
    ]


def test_initialize_database_creates_expected_columns(tmp_path):
    database = tmp_path / "history.sqlite"

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(benchmark_samples)"
            )
        }

    assert {
        "timestamp_utc", "elapsed_ns", "run_id",
        "variant", "binary", "config_json",
        "pair_index", "execution_order", "seed",
    } <= columns


def test_save_and_retrieve_samples(tmp_path):
    database = tmp_path / "history.sqlite"

    run_id = save_samples(
        database,
        make_records(),
        run_id="run-001",
        variant="baseline",
        config={"iterations": 100000},
    )

    samples = get_samples(database, run_id=run_id, variant="baseline")

    assert len(samples) == 3
    assert [row["sample_index"] for row in samples] == [0, 1, 2]
    assert all(row["variant"] == "baseline" for row in samples)
    assert all(row["config_json"] == '{"iterations": 100000}' for row in samples)


def test_variants_are_isolated_by_query(tmp_path):
    database = tmp_path / "history.sqlite"

    save_samples(database, make_records(2), run_id="run-002", variant="baseline")
    save_samples(database, make_records(2), run_id="run-002", variant="candidate")

    assert len(get_samples(database, run_id="run-002", variant="baseline")) == 2
    assert len(get_samples(database, run_id="run-002", variant="candidate")) == 2
    assert len(get_samples(database, run_id="run-002")) == 4


def test_empty_batch_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        save_samples(tmp_path / "history.sqlite", [])


@pytest.mark.parametrize("elapsed_ns", [0, -1, True, 1.5])
def test_invalid_duration_is_rejected(tmp_path, elapsed_ns):
    record = make_records(1)
    record[0]["elapsed_ns"] = elapsed_ns

    with pytest.raises(ValueError, match="elapsed_ns"):
        save_samples(tmp_path / "history.sqlite", record)


def test_invalid_batch_is_not_partially_inserted(tmp_path):
    database = tmp_path / "history.sqlite"
    records = make_records(2)
    records[1]["elapsed_ns"] = -5

    with pytest.raises(ValueError):
        save_samples(database, records, run_id="invalid-run")

    initialize_database(database)
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM benchmark_samples"
        ).fetchone()[0]

    assert count == 0


def test_migrates_existing_legacy_schema(tmp_path):
    database = tmp_path / "legacy.sqlite"

    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TABLE benchmark_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                workload TEXT NOT NULL,
                sample_index INTEGER NOT NULL,
                elapsed_ns INTEGER NOT NULL,
                extra_work INTEGER NOT NULL,
                os TEXT,
                python_version TEXT,
                schema_version INTEGER NOT NULL
            )
        """)
        connection.execute("""
            INSERT INTO benchmark_samples
            (timestamp_utc, workload, sample_index, elapsed_ns,
             extra_work, schema_version)
            VALUES ('2026-10-21T10:00:00+00:00', 'hash_lookup',
                    0, 1000, 0, 1)
        """)

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        row = connection.execute("""
            SELECT run_id, variant, elapsed_ns
            FROM benchmark_samples
        """).fetchone()

    assert row == ("legacy", "single", 1000)



def test_persists_ab_pair_metadata(tmp_path):
    database = tmp_path / "ab-history.sqlite"
    records = make_records(2)

    for index, record in enumerate(records):
        record["pair_index"] = index
        record["execution_order"] = index * 2
        record["seed"] = 2026

    save_samples(
        database,
        records,
        run_id="ab-run",
        variant="baseline",
        config={"seed": 2026},
    )

    samples = get_samples(database, run_id="ab-run", variant="baseline")

    assert [row["pair_index"] for row in samples] == [0, 1]
    assert [row["execution_order"] for row in samples] == [0, 2]
    assert [row["seed"] for row in samples] == [2026, 2026]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pair_index", -1),
        ("pair_index", True),
        ("execution_order", -1),
        ("execution_order", 1.5),
        ("seed", True),
        ("seed", "2026"),
    ],
)
def test_rejects_invalid_ab_metadata(tmp_path, field, value):
    record = make_records(1)
    record[0][field] = value

    with pytest.raises(ValueError):
        save_samples(tmp_path / "invalid-ab.sqlite", record)
