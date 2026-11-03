"""Deterministic, read-only validation for bounded local tabular files."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from benchforge.pipeline import (
    MAX_INPUT_BYTES,
    MAX_INPUT_ROWS,
    InputFormat,
    _cell_text,
    _record_source,
    normalize_column_name,
)

_SUPPORTED_TYPES = {"boolean", "integer", "number", "string"}
_INPUT_FORMATS = {"csv", "json", "jsonl", "parquet"}


@dataclass(frozen=True)
class QualityRules:
    required_columns: tuple[str, ...] = ()
    column_types: Mapping[str, str] = field(default_factory=dict)
    not_null_columns: tuple[str, ...] = ()
    unique_columns: tuple[str, ...] = ()
    numeric_ranges: Mapping[str, tuple[float | None, float | None]] = field(
        default_factory=dict
    )
    allowed_values: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    string_lengths: Mapping[str, tuple[int | None, int | None]] = field(
        default_factory=dict
    )
    min_rows: int = 0
    max_rows: int = MAX_INPUT_ROWS


def _column_map(headers: list[str]) -> dict[str, int]:
    normalized = [
        normalize_column_name(header, index)
        for index, header in enumerate(headers)
    ]
    return {name: index for index, name in enumerate(normalized)}


def _is_type(value: str, expected: str) -> bool:
    if expected == "string":
        return True
    if expected == "boolean":
        return value.casefold() in {"true", "false", "0", "1"}
    if expected == "integer":
        try:
            int(value)
        except ValueError:
            return False
        return True
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _validate_rules(rules: QualityRules) -> None:
    if not isinstance(rules.min_rows, int) or isinstance(rules.min_rows, bool):
        raise TypeError("min_rows must be a non-negative integer.")
    if not isinstance(rules.max_rows, int) or isinstance(rules.max_rows, bool):
        raise TypeError("max_rows must be an integer within the pipeline row limit.")
    if not 0 <= rules.min_rows <= rules.max_rows <= MAX_INPUT_ROWS:
        raise ValueError(f"row bounds must satisfy 0 <= min_rows <= max_rows <= {MAX_INPUT_ROWS}.")
    for column, expected in rules.column_types.items():
        if not isinstance(expected, str) or expected not in _SUPPORTED_TYPES:
            raise ValueError(
                f"Unsupported expected type for {column!r}; choose "
                + ", ".join(sorted(_SUPPORTED_TYPES))
                + "."
            )
    for column, bounds in rules.numeric_ranges.items():
        if (
            not isinstance(bounds, tuple)
            or len(bounds) != 2
            or any(
                value is not None
                and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                )
                for value in bounds
            )
            or (
                bounds[0] is not None
                and bounds[1] is not None
                and bounds[0] > bounds[1]
            )
        ):
            raise ValueError(f"Numeric range for {column!r} has invalid bounds.")
    for column, bounds in rules.string_lengths.items():
        if (
            not isinstance(bounds, tuple)
            or len(bounds) != 2
            or any(
                value is not None
                and (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                )
                for value in bounds
            )
            or (
                bounds[0] is not None
                and bounds[1] is not None
                and bounds[0] > bounds[1]
            )
        ):
            raise ValueError(f"String length rule for {column!r} has invalid bounds.")


def _format(source: Path, input_format: InputFormat | Literal["auto"]) -> InputFormat:
    if input_format == "auto":
        input_format = {
            ".csv": "csv",
            ".json": "json",
            ".jsonl": "jsonl",
            ".ndjson": "jsonl",
            ".parquet": "parquet",
        }.get(source.suffix.casefold())
    if input_format not in _INPUT_FORMATS:
        raise ValueError("Choose CSV, JSON, JSONL, or Parquet input.")
    return input_format


def validate_file(
    source: Path,
    *,
    input_format: InputFormat | Literal["auto"] = "auto",
    rules: QualityRules | None = None,
) -> dict[str, Any]:
    """Validate a local file and return a stable, machine-readable report."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"Quality source file does not exist: {source}")
    selected_rules = rules or QualityRules()
    _validate_rules(selected_rules)
    file_format = _format(source, input_format)
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    def add_check(name: str, passed: bool, failures: int, message: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "passed": passed,
                "failures": failures,
                "message": message,
            }
        )

    file_size = source.stat().st_size
    add_check(
        "file_size",
        file_size <= MAX_INPUT_BYTES,
        int(file_size > MAX_INPUT_BYTES),
        f"File size is {file_size:,} bytes; maximum is {MAX_INPUT_BYTES:,} bytes.",
    )
    if file_size > MAX_INPUT_BYTES:
        add_check("row_count", False, 0, "Rows were not read because the file-size limit was exceeded.")
        return _report(source, file_format, checks, warnings, 0, [])

    raw_headers, rows = _record_source(source, file_format)
    indexes = _column_map(raw_headers)
    schema = [
        {"source_name": header, "normalized_name": name}
        for name, header in zip(indexes, raw_headers, strict=True)
    ]
    configured_columns: set[str] = set()
    for columns in (
        selected_rules.required_columns,
        selected_rules.not_null_columns,
        selected_rules.unique_columns,
        selected_rules.column_types.keys(),
        selected_rules.numeric_ranges.keys(),
        selected_rules.allowed_values.keys(),
        selected_rules.string_lengths.keys(),
    ):
        configured_columns.update(
            normalize_column_name(column, index)
            for index, column in enumerate(columns)
        )
    missing_columns = sorted(configured_columns - indexes.keys())
    add_check(
        "configured_columns_exist",
        not missing_columns,
        len(missing_columns),
        "All rule columns exist."
        if not missing_columns
        else "Missing rule columns: " + ", ".join(missing_columns),
    )
    required = {
        normalize_column_name(column, index)
        for index, column in enumerate(selected_rules.required_columns)
    }
    missing_required = sorted(required - indexes.keys())
    add_check(
        "required_columns",
        not missing_required,
        len(missing_required),
        "All required columns are present."
        if not missing_required
        else "Missing required columns: " + ", ".join(missing_required),
    )

    typed = {
        normalize_column_name(column, index): expected
        for index, (column, expected) in enumerate(selected_rules.column_types.items())
    }
    not_null = {
        normalize_column_name(column, index)
        for index, column in enumerate(selected_rules.not_null_columns)
    }
    unique = {
        normalize_column_name(column, index)
        for index, column in enumerate(selected_rules.unique_columns)
    }
    numeric_ranges = {
        normalize_column_name(column, index): bounds
        for index, (column, bounds) in enumerate(selected_rules.numeric_ranges.items())
    }
    allowed_values = {
        normalize_column_name(column, index): set(values)
        for index, (column, values) in enumerate(selected_rules.allowed_values.items())
    }
    string_lengths = {
        normalize_column_name(column, index): bounds
        for index, (column, bounds) in enumerate(selected_rules.string_lengths.items())
    }
    failure_counts: dict[str, int] = {}
    seen: dict[str, set[str]] = {column: set() for column in unique}
    input_rows = 0
    for row in rows:
        input_rows += 1
        if input_rows > MAX_INPUT_ROWS:
            break
        if row is None or len(row) != len(raw_headers):
            failure_counts["well_formed_rows"] = failure_counts.get("well_formed_rows", 0) + 1
            continue
        for column, index in indexes.items():
            value = _cell_text(row[index])
            if value is None or value.strip() == "":
                if column in not_null:
                    failure_counts[f"not_null:{column}"] = failure_counts.get(
                        f"not_null:{column}", 0
                    ) + 1
                continue
            if column in typed and not _is_type(value, typed[column]):
                failure_counts[f"type:{column}"] = failure_counts.get(
                    f"type:{column}", 0
                ) + 1
            if column in unique:
                if value in seen[column]:
                    failure_counts[f"unique:{column}"] = failure_counts.get(
                        f"unique:{column}", 0
                    ) + 1
                else:
                    seen[column].add(value)
            if column in numeric_ranges:
                minimum, maximum = numeric_ranges[column]
                try:
                    number = float(value)
                except ValueError:
                    number = math.nan
                if (
                    not math.isfinite(number)
                    or (minimum is not None and number < minimum)
                    or (maximum is not None and number > maximum)
                ):
                    failure_counts[f"numeric_range:{column}"] = failure_counts.get(
                        f"numeric_range:{column}", 0
                    ) + 1
            if column in allowed_values and value not in allowed_values[column]:
                failure_counts[f"allowed_values:{column}"] = failure_counts.get(
                    f"allowed_values:{column}", 0
                ) + 1
            if column in string_lengths:
                minimum_length, maximum_length = string_lengths[column]
                if (
                    (minimum_length is not None and len(value) < minimum_length)
                    or (maximum_length is not None and len(value) > maximum_length)
                ):
                    failure_counts[f"string_length:{column}"] = failure_counts.get(
                        f"string_length:{column}", 0
                    ) + 1

    exceeded_row_limit = input_rows > MAX_INPUT_ROWS
    add_check(
        "row_count",
        not exceeded_row_limit
        and selected_rules.min_rows <= input_rows <= selected_rules.max_rows,
        int(exceeded_row_limit)
        + int(input_rows < selected_rules.min_rows or input_rows > selected_rules.max_rows),
        f"Observed {input_rows:,} rows; configured range is "
        f"{selected_rules.min_rows:,}–{selected_rules.max_rows:,} and the hard limit is "
        f"{MAX_INPUT_ROWS:,}.",
    )
    if exceeded_row_limit:
        warnings.append(
            f"Validation stopped after the {MAX_INPUT_ROWS:,}-row safety limit."
        )
    add_check(
        "well_formed_rows",
        failure_counts.get("well_formed_rows", 0) == 0,
        failure_counts.get("well_formed_rows", 0),
        "All rows match the source schema."
        if failure_counts.get("well_formed_rows", 0) == 0
        else "One or more rows do not match the source schema.",
    )
    for column in sorted(not_null):
        failures = failure_counts.get(f"not_null:{column}", 0)
        add_check(
            f"not_null:{column}",
            failures == 0,
            failures,
            f"Column {column} contains no null or blank values."
            if failures == 0
            else f"Column {column} has {failures} null or blank values.",
        )
    for column in sorted(unique):
        failures = failure_counts.get(f"unique:{column}", 0)
        add_check(
            f"unique:{column}",
            failures == 0,
            failures,
            f"Column {column} contains unique non-null values."
            if failures == 0
            else f"Column {column} has {failures} duplicate values.",
        )
    for column in sorted(typed):
        failures = failure_counts.get(f"type:{column}", 0)
        add_check(
            f"type:{column}:{typed[column]}",
            failures == 0,
            failures,
            f"Values in {column} match type {typed[column]}."
            if failures == 0
            else f"Column {column} has {failures} values that are not {typed[column]}.",
        )
    for column in sorted(numeric_ranges):
        failures = failure_counts.get(f"numeric_range:{column}", 0)
        add_check(
            f"numeric_range:{column}",
            failures == 0,
            failures,
            f"Values in {column} are within {numeric_ranges[column]}."
            if failures == 0
            else f"Column {column} has {failures} values outside its numeric range.",
        )
    for column in sorted(allowed_values):
        failures = failure_counts.get(f"allowed_values:{column}", 0)
        add_check(
            f"allowed_values:{column}",
            failures == 0,
            failures,
            f"Values in {column} are allowed."
            if failures == 0
            else f"Column {column} has {failures} values outside its allowed set.",
        )
    for column in sorted(string_lengths):
        failures = failure_counts.get(f"string_length:{column}", 0)
        add_check(
            f"string_length:{column}",
            failures == 0,
            failures,
            f"Values in {column} satisfy string-length bounds."
            if failures == 0
            else f"Column {column} has {failures} values outside its string-length bounds.",
        )
    if input_rows == 0:
        warnings.append("Dataset contains no data rows.")
    return _report(source, file_format, checks, warnings, input_rows, schema)


def _report(
    source: Path,
    file_format: InputFormat,
    checks: list[dict[str, Any]],
    warnings: list[str],
    input_rows: int,
    schema: list[dict[str, str]],
) -> dict[str, Any]:
    failures = sum(not check["passed"] for check in checks)
    status = "FAIL" if failures else "WARNING" if warnings else "PASS"
    return {
        "dataset": source.name,
        "format": file_format,
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "status": status,
        "input_rows": input_rows,
        "rules_executed": len(checks),
        "rules_passed": len(checks) - failures,
        "rules_failed": failures,
        "warnings": warnings,
        "errors": [check["message"] for check in checks if not check["passed"]],
        "schema": schema,
        "checks": checks,
    }
