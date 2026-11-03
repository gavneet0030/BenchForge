"""Reproducible, bounded processing for five public-data case studies."""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import math
import tempfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATASETS = {
    "bike": {
        "label": "UCI Bike Sharing",
        "source_url": "https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset",
        "download_url": "https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip",
        "license": "CC BY 4.0; attribution required.",
        "source_file": "bike-sharing-dataset.zip",
        "max_download_bytes": 2_000_000,
    },
    "retail": {
        "label": "UCI Online Retail",
        "source_url": "https://archive.ics.uci.edu/dataset/352/online+retail",
        "download_url": "https://archive.ics.uci.edu/static/public/352/online+retail.zip",
        "license": "CC BY 4.0; cite Chen (2015), Online Retail, DOI 10.24432/C5BW33, and UCI.",
        "source_file": "online-retail.zip",
        "max_download_bytes": 100_000_000,
    },
    "taxi": {
        "label": "NYC TLC Yellow Taxi Trip Records (2024-01)",
        "source_url": "https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page",
        "download_url": "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet",
        "license": "NYC Open Data lists this dataset's license as unspecified. General NYC Open Data guidance says use is unrestricted, but raw/derived redistribution of this TLC-hosted archive is not confirmed; do not bundle pending clarification.",
        "source_file": "yellow_tripdata_2024-01.parquet",
        "max_download_bytes": 55_000_000,
    },
    "household_power": {
        "label": "UCI Individual Household Electric Power Consumption",
        "source_url": (
            "https://archive.ics.uci.edu/dataset/235/"
            "individual+household+electric+power+consumption"
        ),
        "download_url": (
            "https://archive.ics.uci.edu/static/public/235/"
            "individual+household+electric+power+consumption.zip"
        ),
        "license": "CC BY 4.0; attribute Hebrail and Berard (2006), DOI 10.24432/C58K54.",
        "source_file": "household-power-consumption.zip",
        "max_download_bytes": 21_000_000,
        "max_uncompressed_bytes": 140_000_000,
    },
    "wine_quality": {
        "label": "UCI Wine Quality",
        "source_url": "https://archive.ics.uci.edu/dataset/186/wine+quality",
        "download_url": "https://archive.ics.uci.edu/static/public/186/wine+quality.zip",
        "license": "CC BY 4.0; attribute Cortez et al. (2009), DOI 10.24432/C56S3T.",
        "source_file": "wine-quality.zip",
        "max_download_bytes": 200_000,
        "max_uncompressed_bytes": 400_000,
    },
}

DATASET_CATALOG = {
    **{
        key: {
            "status": "implemented",
            "publisher": "UCI Machine Learning Repository",
            "official_url": metadata["source_url"],
            "license_terms": metadata["license"],
            "format": "ZIP archive",
            "raw_filename": metadata["source_file"],
        }
        for key, metadata in DATASETS.items()
        if key in {"bike", "retail", "household_power", "wine_quality"}
    },
    "taxi": {
        "status": "implemented_sample",
        "publisher": "NYC Taxi and Limousine Commission",
        "official_url": DATASETS["taxi"]["source_url"],
        "license_terms": DATASETS["taxi"]["license"],
        "format": "Parquet",
        "raw_filename": DATASETS["taxi"]["source_file"],
    },
    "citibike": {
        "status": "not_implemented",
        "publisher": "Citi Bike",
        "official_url": "https://citibikenyc.com/system-data",
        "license_terms": "Verify current dataset terms and attribution before use.",
        "format": "Monthly CSV ZIP; January 2024 archive listed at 369,035,302 bytes.",
        "raw_filename": None,
    },
    "student_performance": {
        "status": "not_implemented",
        "publisher": "UCI Machine Learning Repository",
        "official_url": "https://archive.ics.uci.edu/dataset/320/student+performance",
        "license_terms": "Verify repository license and attribution before use.",
        "format": "Two CSV course files; exact download size and license are not verified.",
        "raw_filename": None,
    },
}


def list_datasets(
    raw_dir: Path = Path("data/public_datasets/raw"),
    processed_dir: Path = Path("data/public_datasets/processed"),
) -> list[dict[str, Any]]:
    """Report source metadata and local availability without downloading data."""
    raw_dir = Path(raw_dir)
    processed_dir = Path(processed_dir)
    results = []
    for key, metadata in sorted(DATASET_CATALOG.items()):
        filename = metadata["raw_filename"]
        raw_path = raw_dir / filename if filename else None
        processed_path = processed_dir / f"{key}.json"
        results.append({
            "id": key,
            "status": metadata["status"],
            "publisher": metadata["publisher"],
            "official_url": metadata["official_url"],
            "license_terms": metadata["license_terms"],
            "format": metadata["format"],
            "local_available": bool(raw_path and raw_path.is_file()),
            "local_size_bytes": raw_path.stat().st_size if raw_path and raw_path.is_file() else None,
            "processed_available": processed_path.is_file(),
        })
    return results


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _rounded(value: float, digits: int = 2) -> float:
    return round(value, digits)


def _base(dataset: str, rows: int) -> dict[str, Any]:
    metadata = DATASETS[dataset]
    return {
        "dataset": dataset,
        "label": metadata["label"],
        "source_url": metadata["source_url"],
        "download_url": metadata["download_url"],
        "access_date": datetime.now(UTC).date().isoformat(),
        "license": metadata["license"],
        "rows_processed": rows,
    }


def aggregate_bike_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate UCI hourly bike records without retaining source rows."""
    hour_count: Counter[int] = Counter()
    hour_sum: Counter[int] = Counter()
    month_sum: Counter[str] = Counter()
    temperature_bins: dict[int, list[float]] = defaultdict(list)
    quality = Counter()
    totals = Counter()
    row_count = 0
    valid_hourly_records = 0
    seen_instants: set[str] = set()

    for row in rows:
        row_count += 1
        instant = str(row.get("instant", ""))
        if not instant or instant in seen_instants:
            quality["missing_or_duplicate_instant"] += 1
        seen_instants.add(instant)
        count = _number(row.get("cnt"))
        hour = _number(row.get("hr"))
        temperature = _number(row.get("temp"))
        try:
            month = datetime.strptime(str(row.get("dteday", "")), "%Y-%m-%d").strftime("%Y-%m")
        except ValueError:
            month = ""
        if (
            count is None
            or hour is None
            or temperature is None
            or not month
        ):
            quality["invalid_required_values"] += 1
            continue
        casual = _number(row.get("casual"))
        registered = _number(row.get("registered"))
        if (
            hour != int(hour)
            or not 0 <= hour <= 23
            or not 0 <= temperature <= 1
            or count != int(count)
            or casual is None
            or registered is None
            or casual < 0
            or registered < 0
            or casual != int(casual)
            or registered != int(registered)
        ):
            quality["invalid_required_values"] += 1
            continue
        if count < 0:
            quality["negative_rental_count"] += 1
            continue
        hour_int = int(hour)
        hour_count[hour_int] += 1
        hour_sum[hour_int] += count
        month_sum[month] += count
        # UCI hourly temp is normalized over [-8, 39] degrees Celsius.
        temp_c = temperature * 47.0 - 8.0
        temp_bin = int(temp_c // 3) * 3
        temperature_bins[temp_bin].append(count)
        if casual + registered != count:
            quality["rider_count_mismatch"] += 1
        totals["casual"] += casual
        totals["registered"] += registered
        valid_hourly_records += 1
        totals["rentals"] += count

    return {
        **_base("bike", row_count),
        "kpis": {
            "rental_count": int(totals["rentals"]),
            "casual_rentals": int(totals["casual"]),
            "registered_rentals": int(totals["registered"]),
            "hourly_records": valid_hourly_records,
        },
        "quality": dict(quality),
        "charts": {
            "hourly_demand": [
                {"hour": hour, "average_rentals": _rounded(hour_sum[hour] / hour_count[hour])}
                for hour in sorted(hour_count)
            ],
            "monthly_demand": [
                {"month": month, "rentals": int(value)}
                for month, value in sorted(month_sum.items())
            ],
            "temperature_demand": [
                {
                    "temperature_c": lower + 1.5,
                    "average_rentals": _rounded(sum(values) / len(values)),
                    "records": len(values),
                }
                for lower, values in sorted(temperature_bins.items())
            ],
            "user_types": [
                {"type": "Registered", "rentals": int(totals["registered"])},
                {"type": "Casual", "rentals": int(totals["casual"])},
            ],
        },
        "notes": [
            "Hourly records cover 2011-2012 in Washington, D.C.; rental counts are observational.",
            "Normalized hourly temperature is converted to Celsius using the UCI data dictionary.",
            "Weather associations are descriptive and do not establish causation.",
        ],
    }


def aggregate_retail_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate UCI Online Retail rows, separating cancellations from sales."""
    monthly: Counter[str] = Counter()
    products: Counter[str] = Counter()
    countries: Counter[str] = Counter()
    orders: Counter[str] = Counter()
    quality = Counter()
    cancelled_invoices: set[str] = set()
    row_count = 0
    included_lines = 0
    revenue = 0.0

    for row in rows:
        row_count += 1
        invoice = str(row.get("InvoiceNo") or "").strip()
        if invoice[:1].casefold() == "c":
            cancelled_invoices.add(invoice)
            quality["cancelled_lines"] += 1
            continue
        quantity = _number(row.get("Quantity"))
        price = _number(row.get("UnitPrice"))
        invoice_date = row.get("InvoiceDate")
        if isinstance(invoice_date, datetime):
            month = invoice_date.strftime("%Y-%m")
        else:
            month = str(invoice_date or "")[:7]
        if quantity is None or price is None or not invoice or not month:
            quality["invalid_or_missing_required_fields"] += 1
            continue
        if quantity < 0:
            quality["negative_quantity_lines"] += 1
            continue
        if price < 0:
            quality["negative_unit_price_lines"] += 1
            continue
        if not row.get("CustomerID"):
            quality["missing_customer_id_lines"] += 1
        if quantity == 0:
            quality["zero_quantity_lines"] += 1
            continue
        if price == 0:
            quality["zero_unit_price_lines"] += 1
            continue

        line_revenue = quantity * price
        included_lines += 1
        revenue += line_revenue
        monthly[month] += line_revenue
        product = str(row.get("Description") or row.get("StockCode") or "Unknown")
        country = str(row.get("Country") or "Unknown")
        products[product] += line_revenue
        countries[country] += line_revenue
        orders[invoice] += line_revenue

    order_histogram = Counter()
    for amount in orders.values():
        if amount < 10:
            bucket = "£0–£10"
        elif amount < 25:
            bucket = "£10–£25"
        elif amount < 50:
            bucket = "£25–£50"
        elif amount < 100:
            bucket = "£50–£100"
        else:
            bucket = "£100+"
        order_histogram[bucket] += 1

    return {
        **_base("retail", row_count),
        "kpis": {
            "gross_sales_gbp": _rounded(revenue),
            "positive_line_count": included_lines,
            "invoice_count": len(orders),
            "cancelled_invoice_count": len(cancelled_invoices),
            "missing_customer_id_lines": quality["missing_customer_id_lines"],
        },
        "quality": dict(quality),
        "charts": {
            "monthly_revenue": [
                {"month": month, "revenue_gbp": _rounded(value)}
                for month, value in sorted(monthly.items())
            ],
            "top_products": [
                {"product": label[:80], "revenue_gbp": _rounded(value)}
                for label, value in products.most_common(10)
            ],
            "country_revenue": [
                {"country": label, "revenue_gbp": _rounded(value)}
                for label, value in countries.most_common(12)
            ],
            "order_value_distribution": [
                {"range": label, "orders": order_histogram[label]}
                for label in ("£0–£10", "£10–£25", "£25–£50", "£50–£100", "£100+")
            ],
        },
        "notes": [
            "Positive-quantity, positive-price, non-cancellation lines are summed as gross sales in GBP; this is not profit.",
            "Cancellation invoices (InvoiceNo beginning with C), nonpositive-quantity lines, and zero-price lines are excluded from gross sales.",
            "Sales without CustomerID remain included in revenue; customer-level analysis is not inferred for those rows.",
        ],
    }


def _as_datetime(value: Any) -> datetime | None:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def aggregate_taxi_rows(
    rows: Iterable[dict[str, Any]],
    *,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Aggregate a bounded, file-order sample from one TLC monthly Parquet file."""
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise ValueError("max_rows must be a positive integer.")

    hour_counts: Counter[int] = Counter()
    distance_bins: Counter[str] = Counter()
    fare_distance: dict[int, list[tuple[float, float]]] = defaultdict(list)
    quality = Counter()
    count = 0
    valid_count = 0
    total_distance = 0.0
    total_fare = 0.0

    for row in rows:
        if count >= max_rows:
            break
        count += 1
        pickup = _as_datetime(row.get("tpep_pickup_datetime"))
        dropoff = _as_datetime(row.get("tpep_dropoff_datetime"))
        distance = _number(row.get("trip_distance"))
        fare = _number(row.get("fare_amount"))
        if not pickup or not dropoff:
            quality["missing_or_invalid_timestamps"] += 1
            continue
        if dropoff <= pickup:
            quality["nonpositive_duration"] += 1
            continue
        if distance is None or distance <= 0:
            quality["nonpositive_or_invalid_distance"] += 1
            continue
        if fare is None or fare < 0:
            quality["negative_or_invalid_fare"] += 1
            continue

        valid_count += 1
        total_distance += distance
        total_fare += fare
        hour_counts[pickup.hour] += 1
        if distance < 1:
            bucket = "0–1 mi"
        elif distance < 3:
            bucket = "1–3 mi"
        elif distance < 5:
            bucket = "3–5 mi"
        elif distance < 10:
            bucket = "5–10 mi"
        else:
            bucket = "10+ mi"
        distance_bins[bucket] += 1
        distance_group = min(int(distance), 19)
        fare_distance[distance_group].append((distance, fare))

    return {
        **_base("taxi", count),
        "sample_method": f"First {max_rows:,} records in Parquet file order, or fewer if file is shorter.",
        "kpis": {
            "valid_trip_count": valid_count,
            "mean_trip_distance_miles": _rounded(total_distance / valid_count) if valid_count else None,
            "mean_base_fare_usd": _rounded(total_fare / valid_count) if valid_count else None,
            "invalid_trip_count": count - valid_count,
        },
        "quality": dict(quality),
        "charts": {
            "pickup_hour": [
                {"hour": hour, "trips": hour_counts[hour]}
                for hour in sorted(hour_counts)
            ],
            "trip_distance_distribution": [
                {"range": label, "trips": distance_bins[label]}
                for label in ("0–1 mi", "1–3 mi", "3–5 mi", "5–10 mi", "10+ mi")
            ],
            "fare_distance": [
                {
                    "distance_miles": _rounded(
                        sum(distance for distance, _ in values) / len(values)
                    ),
                    "base_fare_usd": _rounded(sum(fare for _, fare in values) / len(values)),
                    "trips": len(values),
                }
                for _, values in sorted(fare_distance.items())
            ],
        },
        "notes": [
            "One 2024-01 Yellow Taxi Parquet period is processed; the source file is not bundled.",
            "The first records in file order are used as a bounded sample, not a random or seasonally representative sample.",
            "Fare is base fare in USD; total charges, tips and profitability are not inferred.",
        ],
    }


def aggregate_household_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize household electricity readings without retaining source rows."""
    monthly_sum: Counter[str] = Counter()
    monthly_count: Counter[str] = Counter()
    hourly_sum: Counter[int] = Counter()
    hourly_count: Counter[int] = Counter()
    meter_totals: Counter[str] = Counter()
    quality = Counter()
    row_count = 0
    valid_power_count = 0
    voltage_sum = 0.0
    voltage_count = 0
    month_days: dict[tuple[int, int], int] = {}
    measurement_fields = (
        "Global_active_power",
        "Global_reactive_power",
        "Voltage",
        "Global_intensity",
        "Sub_metering_1",
        "Sub_metering_2",
        "Sub_metering_3",
    )

    for row in rows:
        row_count += 1
        try:
            day, month, year = map(int, str(row.get("Date", "")).split("/"))
            hour, minute, second = map(int, str(row.get("Time", "")).split(":"))
            if not 1 <= month <= 12 or not 1 <= day <= 31:
                raise ValueError
            maximum_day = month_days.get((year, month))
            if maximum_day is None:
                maximum_day = calendar.monthrange(year, month)[1]
                month_days[year, month] = maximum_day
            if (
                day > maximum_day
                or not 0 <= hour <= 23
                or not 0 <= minute <= 59
                or not 0 <= second <= 59
            ):
                raise ValueError
        except (ValueError, TypeError):
            quality["invalid_timestamp"] += 1
            continue

        values: dict[str, float | None] = {}
        missing_measurement = False
        for field in measurement_fields:
            raw_value = row.get(field)
            normalized_value = "" if raw_value is None else str(raw_value).strip()
            if normalized_value in {"", "?"}:
                quality[f"missing_{field.casefold()}"] += 1
                missing_measurement = True
                values[field] = None
                continue
            value = _number(normalized_value)
            if value is None:
                quality["invalid_numeric_values"] += 1
                missing_measurement = True
            values[field] = value
        if missing_measurement:
            quality["rows_with_missing_measurements"] += 1

        voltage = values["Voltage"]
        if voltage is not None and voltage > 0:
            voltage_sum += voltage
            voltage_count += 1
        for field in ("Sub_metering_1", "Sub_metering_2", "Sub_metering_3"):
            meter_value = values[field]
            if meter_value is not None and meter_value >= 0:
                meter_totals[field] += meter_value

        power = values["Global_active_power"]
        if power is None or power < 0:
            quality["invalid_or_missing_active_power"] += 1
            continue
        valid_power_count += 1
        month_key = f"{year:04d}-{month:02d}"
        monthly_sum[month_key] += power
        monthly_count[month_key] += 1
        hourly_sum[hour] += power
        hourly_count[hour] += 1

    return {
        **_base("household_power", row_count),
        "kpis": {
            "valid_active_power_readings": valid_power_count,
            "rows_with_missing_measurements": quality["rows_with_missing_measurements"],
            "mean_active_power_kw": (
                _rounded(sum(monthly_sum.values()) / valid_power_count)
                if valid_power_count else None
            ),
            "mean_voltage_v": (
                _rounded(voltage_sum / voltage_count) if voltage_count else None
            ),
            "sub_metering_energy_wh": {
                field.casefold(): _rounded(value, 3)
                for field, value in sorted(meter_totals.items())
            },
        },
        "quality": dict(quality),
        "charts": {
            "monthly_active_power": [
                {
                    "month": month,
                    "mean_kw": _rounded(monthly_sum[month] / monthly_count[month]),
                    "readings": monthly_count[month],
                }
                for month in sorted(monthly_sum)
            ],
            "hourly_active_power": [
                {
                    "hour": hour,
                    "mean_kw": _rounded(hourly_sum[hour] / hourly_count[hour]),
                    "readings": hourly_count[hour],
                }
                for hour in sorted(hourly_sum)
            ],
        },
        "notes": [
            "Active power is the household's one-minute average, reported in kilowatts.",
            "Missing measurement fields are counted and excluded only from the affected metric.",
            "Sub-metering sums are source watt-hour readings; this summary does not infer household behavior.",
        ],
    }


def aggregate_wine_rows(
    rows: Iterable[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Summarize UCI red and white wine quality records."""
    color_rows: Counter[str] = Counter()
    color_quality: Counter[str] = Counter()
    color_alcohol: Counter[str] = Counter()
    quality_count: Counter[int] = Counter()
    quality_alcohol: Counter[int] = Counter()
    quality = Counter()
    row_count = 0

    for color, row in rows:
        row_count += 1
        score = _number(row.get("quality"))
        alcohol = _number(row.get("alcohol"))
        volatile_acidity = _number(row.get("volatile acidity"))
        if (
            color not in {"red", "white"}
            or score is None
            or score != int(score)
            or not 0 <= score <= 10
            or alcohol is None
            or alcohol < 0
            or volatile_acidity is None
            or volatile_acidity < 0
        ):
            quality["invalid_required_values"] += 1
            continue
        score_int = int(score)
        color_rows[color] += 1
        color_quality[color] += score_int
        color_alcohol[color] += alcohol
        quality_count[score_int] += 1
        quality_alcohol[score_int] += alcohol

    valid_count = sum(color_rows.values())
    return {
        **_base("wine_quality", row_count),
        "kpis": {
            "valid_sample_count": valid_count,
            "samples_by_color": dict(sorted(color_rows.items())),
            "mean_quality": (
                _rounded(sum(color_quality.values()) / valid_count)
                if valid_count else None
            ),
            "mean_alcohol_percent": (
                _rounded(sum(color_alcohol.values()) / valid_count)
                if valid_count else None
            ),
        },
        "quality": dict(quality),
        "charts": {
            "quality_distribution": [
                {"quality": score, "samples": quality_count[score]}
                for score in sorted(quality_count)
            ],
            "quality_by_color": [
                {
                    "color": color,
                    "mean_quality": _rounded(color_quality[color] / color_rows[color]),
                    "mean_alcohol_percent": _rounded(
                        color_alcohol[color] / color_rows[color]
                    ),
                    "samples": color_rows[color],
                }
                for color in sorted(color_rows)
            ],
            "alcohol_by_quality": [
                {
                    "quality": score,
                    "mean_alcohol_percent": _rounded(
                        quality_alcohol[score] / quality_count[score]
                    ),
                    "samples": quality_count[score],
                }
                for score in sorted(quality_count)
            ],
        },
        "notes": [
            "Quality is a sensory score, not a direct measure of preference or commercial value.",
            "Red and white Vinho Verde samples are summarized separately where color is relevant.",
            "Associations between chemistry and score are descriptive, not causal.",
        ],
    }


def _bike_rows(source: Path) -> Iterable[dict[str, Any]]:
    with zipfile.ZipFile(source) as archive:
        name = next(
            (
                entry
                for entry in archive.namelist()
                if entry == "hour.csv" or entry.endswith("/hour.csv")
            ),
            None,
        )
        if name is None:
            raise ValueError("Bike archive does not contain hour.csv.")
        with archive.open(name) as stream:
            reader = csv.DictReader(line.decode("utf-8-sig") for line in stream)
            required = {"instant", "dteday", "hr", "cnt", "casual", "registered", "temp"}
            missing = required - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"Bike CSV schema is missing required columns: {sorted(missing)}")
            yield from reader


def _household_rows(source: Path) -> Iterable[dict[str, Any]]:
    with zipfile.ZipFile(source) as archive:
        name = next(
            (
                entry for entry in archive.namelist()
                if Path(entry).name.casefold() == "household_power_consumption.txt"
            ),
            None,
        )
        if name is None:
            raise ValueError("Household archive does not contain household_power_consumption.txt.")
        maximum = DATASETS["household_power"]["max_uncompressed_bytes"]
        if archive.getinfo(name).file_size > maximum:
            raise ValueError(
                f"Household source exceeds the {maximum:,}-byte extraction limit."
            )
        with archive.open(name) as stream:
            reader = csv.DictReader(
                (line.decode("utf-8-sig") for line in stream),
                delimiter=";",
            )
            required = {
                "Date",
                "Time",
                "Global_active_power",
                "Global_reactive_power",
                "Voltage",
                "Global_intensity",
                "Sub_metering_1",
                "Sub_metering_2",
                "Sub_metering_3",
            }
            missing = required - set(reader.fieldnames or ())
            if missing:
                raise ValueError(
                    f"Household source schema is missing required columns: {sorted(missing)}"
                )
            yield from reader


def _wine_rows(source: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    with zipfile.ZipFile(source) as archive:
        entries = {
            Path(name).name.casefold(): name
            for name in archive.namelist()
            if Path(name).name.casefold() in {
                "winequality-red.csv",
                "winequality-white.csv",
            }
        }
        if set(entries) != {"winequality-red.csv", "winequality-white.csv"}:
            raise ValueError("Wine archive must contain red and white quality CSV files.")
        total_size = sum(archive.getinfo(name).file_size for name in entries.values())
        maximum = DATASETS["wine_quality"]["max_uncompressed_bytes"]
        if total_size > maximum:
            raise ValueError(
                f"Wine source files exceed the {maximum:,}-byte extraction limit."
            )
        required = {"quality", "alcohol", "volatile acidity"}
        for color in ("red", "white"):
            name = entries[f"winequality-{color}.csv"]
            with archive.open(name) as stream:
                reader = csv.DictReader(
                    (line.decode("utf-8-sig") for line in stream),
                    delimiter=";",
                )
                missing = required - set(reader.fieldnames or ())
                if missing:
                    raise ValueError(
                        f"Wine {color} schema is missing required columns: {sorted(missing)}"
                    )
                yield from ((color, row) for row in reader)


def _retail_rows(source: Path) -> Iterable[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError(
            "Online Retail processing requires the 'datasets' extra: "
            "python -m pip install -e \".[datasets]\""
        ) from exc

    with zipfile.ZipFile(source) as archive:
        name = next(
            (entry for entry in archive.namelist() if entry.lower().endswith(".xlsx")),
            None,
        )
        if name is None:
            raise ValueError("Retail archive does not contain an .xlsx workbook.")
        max_uncompressed_bytes = DATASETS["retail"]["max_download_bytes"]
        if archive.getinfo(name).file_size > max_uncompressed_bytes:
            raise ValueError(
                "Retail workbook exceeds the "
                f"{max_uncompressed_bytes:,}-byte extraction limit."
            )
        with tempfile.TemporaryDirectory(prefix="benchforge-retail-") as directory:
            extracted = Path(directory) / "source.xlsx"
            with archive.open(name) as source_stream, extracted.open("wb") as target:
                while chunk := source_stream.read(1024 * 1024):
                    target.write(chunk)
            workbook = load_workbook(extracted, read_only=True, data_only=True)
            try:
                sheet = workbook.active
                first_row = next(sheet.iter_rows(values_only=True), None)
                if not first_row:
                    raise ValueError("Retail workbook is empty.")
                headers = [str(value) for value in first_row]
                required = {
                    "InvoiceNo", "Quantity", "UnitPrice", "InvoiceDate",
                    "CustomerID", "Country", "Description",
                }
                missing = required - set(headers)
                if missing:
                    raise ValueError(
                        f"Retail workbook schema is missing columns: {sorted(missing)}"
                    )
                for values in sheet.iter_rows(min_row=2, values_only=True):
                    yield dict(zip(headers, values))
            finally:
                workbook.close()


def _taxi_rows(source: Path, max_rows: int) -> Iterable[dict[str, Any]]:
    try:
        from pyarrow import parquet
    except ImportError as exc:
        raise RuntimeError(
            "Taxi Parquet processing requires the 'datasets' extra: "
            "python -m pip install -e \".[datasets]\""
        ) from exc

    file = parquet.ParquetFile(source)
    required = {
        "tpep_pickup_datetime",
        "tpep_dropoff_datetime",
        "trip_distance",
        "fare_amount",
    }
    missing = required - set(file.schema.names)
    if missing:
        raise ValueError(f"Taxi Parquet schema is missing required columns: {sorted(missing)}")
    for batch in file.iter_batches(
        batch_size=min(8192, max_rows),
        columns=sorted(required),
    ):
        yield from batch.to_pylist()


def _download(dataset: str, raw_dir: Path) -> Path:
    metadata = DATASETS[dataset]
    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / metadata["source_file"]
    if destination.exists():
        return destination
    request = urllib.request.Request(
        metadata["download_url"],
        headers={"User-Agent": "BenchForge public-data case study/1.0"},
    )
    temporary = destination.with_suffix(destination.suffix + ".part")
    maximum = metadata["max_download_bytes"]
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            temporary.open("wb") as output,
        ):
            content_length = response.headers.get("Content-Length")
            expected_size = int(content_length) if content_length else None
            if expected_size is not None:
                if expected_size < 0:
                    raise ValueError("Dataset server returned an invalid Content-Length.")
                if expected_size > maximum:
                    raise ValueError(
                        f"Dataset download exceeds the {maximum:,}-byte safety limit."
                    )
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise ValueError(
                        f"Dataset download exceeds the {maximum:,}-byte safety limit."
                    )
                output.write(chunk)
            if expected_size is not None and total != expected_size:
                raise ValueError(
                    f"Incomplete dataset download: expected {expected_size:,} bytes, "
                    f"received {total:,}."
                )
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def process_dataset(
    dataset: str,
    source: Path,
    output: Path,
    *,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Process a local official archive and write compact aggregate JSON."""
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise ValueError("max_rows must be a positive integer.")
    if dataset == "bike":
        result = aggregate_bike_rows(_bike_rows(source))
    elif dataset == "retail":
        result = aggregate_retail_rows(_retail_rows(source))
    elif dataset == "taxi":
        result = aggregate_taxi_rows(_taxi_rows(source, max_rows), max_rows=max_rows)
        result["sample_method"] = (
            f"First {max_rows:,} records in Parquet file order, or fewer if file is shorter."
        )
    elif dataset == "household_power":
        result = aggregate_household_rows(_household_rows(source))
    elif dataset == "wine_quality":
        result = aggregate_wine_rows(_wine_rows(source))
    else:
        raise ValueError(f"Unknown dataset: {dataset}")
    digest = hashlib.sha256()
    with source.open("rb") as source_stream:
        while chunk := source_stream.read(1024 * 1024):
            digest.update(chunk)
    result.update({
        "source_filename": source.name,
        "source_size_bytes": source.stat().st_size,
        "source_sha256": digest.hexdigest(),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATASETS), required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--max-rows", type=int, default=100_000)
    parser.add_argument(
        "--raw-dir", type=Path, default=Path("data/public_datasets/raw")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/public_datasets/processed")
    )
    args = parser.parse_args()
    source = _download(args.dataset, args.raw_dir) if args.download else args.source
    if source is None:
        parser.error("Provide --source or use --download.")
    output = args.output_dir / f"{args.dataset}.json"
    result = process_dataset(
        args.dataset, source, output, max_rows=args.max_rows
    )
    print(
        f"Processed {result['rows_processed']:,} source rows for "
        f"{result['label']} into {output}"
    )


if __name__ == "__main__":
    main()
