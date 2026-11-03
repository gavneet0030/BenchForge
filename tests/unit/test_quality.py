import csv

import pytest

from benchforge.quality import QualityRules, validate_file


def _csv(path, headers, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def test_quality_report_passes_valid_data_and_is_stable_except_timestamp(tmp_path):
    source = _csv(
        tmp_path / "valid.csv",
        ["ID", "Score", "Status"],
        [["1", "3.5", "ready"], ["2", "7", "ready"]],
    )
    rules = QualityRules(
        required_columns=("ID", "Score"),
        column_types={"ID": "integer", "Score": "number"},
        not_null_columns=("ID",),
        unique_columns=("ID",),
        numeric_ranges={"Score": (0, 10)},
        allowed_values={"Status": ("ready", "done")},
        min_rows=2,
        max_rows=2,
    )

    first = validate_file(source, rules=rules)
    second = validate_file(source, rules=rules)

    assert first["status"] == "PASS"
    assert first["rules_failed"] == 0
    assert first["validated_at_utc"]
    assert first["checks"] == second["checks"]
    assert first["schema"] == [
        {"source_name": "ID", "normalized_name": "id"},
        {"source_name": "Score", "normalized_name": "score"},
        {"source_name": "Status", "normalized_name": "status"},
    ]


def test_quality_reports_schema_null_duplicate_type_and_value_failures(tmp_path):
    source = _csv(
        tmp_path / "invalid.csv",
        ["ID", "Score", "Status", "Label"],
        [["1", "10", "ready", "ok"], ["1", "bad", "unknown", "too long"]],
    )
    rules = QualityRules(
        required_columns=("id", "missing"),
        column_types={"ID": "integer", "Score": "number"},
        not_null_columns=("ID",),
        unique_columns=("ID",),
        numeric_ranges={"Score": (0, 5)},
        allowed_values={"Status": ("ready",)},
        string_lengths={"Label": (1, 4)},
    )

    result = validate_file(source, rules=rules)
    failed = {check["name"] for check in result["checks"] if not check["passed"]}

    assert result["status"] == "FAIL"
    assert {
        "configured_columns_exist",
        "required_columns",
        "unique:id",
        "type:score:number",
        "numeric_range:score",
        "allowed_values:status",
        "string_length:label",
    } <= failed
    assert result["rules_failed"] == len(failed)
    assert result["errors"]


def test_quality_reports_nulls_and_malformed_rows(tmp_path):
    source = tmp_path / "malformed.csv"
    source.write_text("ID,Value\n1,2\n2\n", encoding="utf-8")

    result = validate_file(
        source,
        rules=QualityRules(not_null_columns=("Value",)),
    )

    assert result["status"] == "FAIL"
    check_results = {check["name"]: check for check in result["checks"]}
    assert check_results["well_formed_rows"]["failures"] == 1
    assert check_results["not_null:value"]["failures"] == 0


def test_quality_empty_dataset_is_warning_and_rules_are_deterministic(tmp_path):
    source = _csv(tmp_path / "empty.csv", ["ID", "Value"], [])

    result = validate_file(source)

    assert result["status"] == "WARNING"
    assert result["input_rows"] == 0
    assert result["warnings"] == ["Dataset contains no data rows."]
    assert [check["name"] for check in result["checks"]] == [
        "file_size",
        "configured_columns_exist",
        "required_columns",
        "row_count",
        "well_formed_rows",
    ]


def test_quality_enforces_file_and_row_limits(tmp_path, monkeypatch):
    source = _csv(tmp_path / "bounded.csv", ["ID"], [["1"], ["2"]])
    monkeypatch.setattr("benchforge.quality.MAX_INPUT_BYTES", 1)

    oversize = validate_file(source)
    assert oversize["status"] == "FAIL"
    assert oversize["input_rows"] == 0
    assert oversize["checks"][0]["name"] == "file_size"

    monkeypatch.setattr("benchforge.quality.MAX_INPUT_BYTES", 20_000_000)
    monkeypatch.setattr("benchforge.quality.MAX_INPUT_ROWS", 1)
    row_limit = validate_file(source, rules=QualityRules(max_rows=1))
    assert row_limit["status"] == "FAIL"
    assert row_limit["input_rows"] == 2
    assert any("stopped after" in warning for warning in row_limit["warnings"])


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        (QualityRules(max_rows=100_001), "row bounds"),
        (QualityRules(column_types={"score": "decimal"}), "Unsupported expected type"),
        (QualityRules(numeric_ranges={"score": (5, 1)}), "invalid bounds"),
        (QualityRules(string_lengths={"label": (4, 2)}), "invalid bounds"),
    ],
)
def test_quality_rejects_invalid_rule_definitions(rules, message, tmp_path):
    source = _csv(tmp_path / "data.csv", ["score"], [["1"]])

    with pytest.raises(ValueError, match=message):
        validate_file(source, rules=rules)


def test_quality_parquet_validation(tmp_path):
    pa = pytest.importorskip("pyarrow")
    from pyarrow import parquet

    source = tmp_path / "data.parquet"
    parquet.write_table(pa.table({"value": [1, None, 3]}), source)

    result = validate_file(
        source,
        rules=QualityRules(not_null_columns=("value",), numeric_ranges={"value": (0, 2)}),
    )

    assert result["format"] == "parquet"
    assert result["input_rows"] == 3
    assert result["status"] == "FAIL"
