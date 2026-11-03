import hashlib
import json
import zipfile
from datetime import datetime

import pytest

from benchforge.public_data import (
    DATASETS,
    _download,
    _household_rows,
    _retail_rows,
    _wine_rows,
    aggregate_bike_rows,
    aggregate_household_rows,
    aggregate_retail_rows,
    aggregate_taxi_rows,
    aggregate_wine_rows,
    process_dataset,
)


def test_bike_aggregation_uses_actual_rental_and_normalized_temperature_fields():
    result = aggregate_bike_rows(
        [
            {
                "instant": "1",
                "dteday": "2011-01-01",
                "hr": "8",
                "cnt": "12",
                "casual": "2",
                "registered": "10",
                "temp": "0.5",
            },
            {
                "instant": "2",
                "dteday": "2011-01-01",
                "hr": "8",
                "cnt": "18",
                "casual": "3",
                "registered": "15",
                "temp": "0.5",
            },
            {
                "instant": "2",
                "dteday": "2011-01-01",
                "hr": "bad",
                "cnt": "10",
                "casual": "2",
                "registered": "8",
                "temp": "0.5",
            },
        ]
    )

    assert result["kpis"]["rental_count"] == 30
    assert result["kpis"]["registered_rentals"] == 25
    assert result["charts"]["hourly_demand"] == [
        {"hour": 8, "average_rentals": 15.0}
    ]
    assert result["charts"]["monthly_demand"] == [
        {"month": "2011-01", "rentals": 30}
    ]
    assert result["quality"]["invalid_required_values"] == 1


def test_retail_aggregation_excludes_cancellations_and_nonpositive_sales_lines():
    result = aggregate_retail_rows(
        [
            {
                "InvoiceNo": "100",
                "Quantity": 2,
                "UnitPrice": 3.5,
                "InvoiceDate": datetime(2011, 1, 2),
                "CustomerID": None,
                "Country": "UK",
                "Description": "Widget",
            },
            {
                "InvoiceNo": "C101",
                "Quantity": 1,
                "UnitPrice": 20,
                "InvoiceDate": datetime(2011, 1, 2),
                "CustomerID": 1,
                "Country": "UK",
            },
            {
                "InvoiceNo": "102",
                "Quantity": -1,
                "UnitPrice": 10,
                "InvoiceDate": datetime(2011, 1, 3),
                "CustomerID": 2,
                "Country": "France",
            },
            {
                "InvoiceNo": "103",
                "Quantity": 0,
                "UnitPrice": 10,
                "InvoiceDate": datetime(2011, 1, 4),
                "CustomerID": 3,
                "Country": "France",
            },
            {
                "InvoiceNo": "104",
                "Quantity": 1,
                "UnitPrice": 0,
                "InvoiceDate": datetime(2011, 1, 5),
                "CustomerID": None,
                "Country": "France",
            },
        ]
    )

    assert result["kpis"]["gross_sales_gbp"] == 7.0
    assert result["kpis"]["positive_line_count"] == 1
    assert result["kpis"]["cancelled_invoice_count"] == 1
    assert result["quality"]["negative_quantity_lines"] == 1
    assert result["quality"]["zero_quantity_lines"] == 1
    assert result["quality"]["zero_unit_price_lines"] == 1
    assert result["quality"]["missing_customer_id_lines"] == 2
    assert result["notes"][0].endswith("not profit.")


def test_taxi_aggregation_is_bounded_and_flags_invalid_records():
    rows = [
        {
            "tpep_pickup_datetime": datetime(2024, 1, 1, 9),
            "tpep_dropoff_datetime": datetime(2024, 1, 1, 9, 10),
            "trip_distance": 2.5,
            "fare_amount": 12,
        },
        {
            "tpep_pickup_datetime": datetime(2024, 1, 1, 10),
            "tpep_dropoff_datetime": datetime(2024, 1, 1, 10),
            "trip_distance": 0,
            "fare_amount": -1,
        },
        {
            "tpep_pickup_datetime": datetime(2024, 1, 1, 11),
            "tpep_dropoff_datetime": datetime(2024, 1, 1, 11, 5),
            "trip_distance": 1,
            "fare_amount": 5,
        },
    ]
    result = aggregate_taxi_rows(rows, max_rows=2)

    assert result["rows_processed"] == 2
    assert result["kpis"]["valid_trip_count"] == 1
    assert result["quality"]["nonpositive_duration"] == 1
    assert result["charts"]["pickup_hour"] == [{"hour": 9, "trips": 1}]
    with pytest.raises(ValueError, match="positive integer"):
        aggregate_taxi_rows([], max_rows=0)


def test_household_aggregation_keeps_metrics_with_independent_missing_values():
    result = aggregate_household_rows(
        [
            {
                "Date": "01/01/2007",
                "Time": "00:00:00",
                "Global_active_power": "1.5",
                "Global_reactive_power": "0.2",
                "Voltage": "230",
                "Global_intensity": "7",
                "Sub_metering_1": "1",
                "Sub_metering_2": "2",
                "Sub_metering_3": "3",
            },
            {
                "Date": "01/01/2007",
                "Time": "01:00:00",
                "Global_active_power": "?",
                "Global_reactive_power": "0.3",
                "Voltage": "235",
                "Global_intensity": "8",
                "Sub_metering_1": "",
                "Sub_metering_2": "3",
                "Sub_metering_3": "4",
            },
        ]
    )

    assert result["rows_processed"] == 2
    assert result["kpis"]["valid_active_power_readings"] == 1
    assert result["kpis"]["mean_active_power_kw"] == 1.5
    assert result["kpis"]["mean_voltage_v"] == 232.5
    assert result["kpis"]["sub_metering_energy_wh"] == {
        "sub_metering_1": 1.0,
        "sub_metering_2": 5.0,
        "sub_metering_3": 7.0,
    }
    assert result["quality"]["missing_global_active_power"] == 1
    assert result["quality"]["rows_with_missing_measurements"] == 1
    assert result["quality"].get("invalid_numeric_values", 0) == 0


def test_wine_aggregation_reports_samples_by_color_and_quality():
    result = aggregate_wine_rows(
        [
            ("red", {"quality": "6", "alcohol": "10.5", "volatile acidity": "0.3"}),
            ("white", {"quality": "7", "alcohol": "11", "volatile acidity": "0.2"}),
            ("red", {"quality": "bad", "alcohol": "10", "volatile acidity": "0.4"}),
        ]
    )

    assert result["rows_processed"] == 3
    assert result["kpis"]["samples_by_color"] == {"red": 1, "white": 1}
    assert result["kpis"]["mean_quality"] == 6.5
    assert result["quality"]["invalid_required_values"] == 1


def test_household_archive_processing_streams_semicolon_delimited_rows(tmp_path):
    source = tmp_path / "household.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "household_power_consumption.txt",
            "Date;Time;Global_active_power;Global_reactive_power;Voltage;"
            "Global_intensity;Sub_metering_1;Sub_metering_2;Sub_metering_3\n"
            "01/01/2007;00:00:00;1.5;0.2;230;7;1;2;3\n",
        )
    output = tmp_path / "processed" / "household_power.json"

    result = process_dataset("household_power", source, output)

    assert result["rows_processed"] == 1
    assert result["charts"]["monthly_active_power"] == [
        {"month": "2007-01", "mean_kw": 1.5, "readings": 1}
    ]
    assert output.is_file()
    assert next(iter(_household_rows(source)))["Date"] == "01/01/2007"


def test_household_archive_rejects_oversized_extracted_source(tmp_path, monkeypatch):
    source = tmp_path / "household.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("household_power_consumption.txt", b"fixture")

    original_getinfo = zipfile.ZipFile.getinfo

    def oversized_getinfo(archive, name):
        info = original_getinfo(archive, name)
        if name.endswith(".txt"):
            info.file_size = DATASETS["household_power"]["max_uncompressed_bytes"] + 1
        return info

    monkeypatch.setattr(zipfile.ZipFile, "getinfo", oversized_getinfo)
    with pytest.raises(ValueError, match="extraction limit"):
        list(_household_rows(source))


def test_wine_archive_processing_requires_both_color_files(tmp_path):
    source = tmp_path / "wine.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "winequality-red.csv",
            "quality;alcohol;volatile acidity\n6;10.5;0.3\n",
        )
        archive.writestr(
            "winequality-white.csv",
            "quality;alcohol;volatile acidity\n7;11;0.2\n",
        )
    output = tmp_path / "processed" / "wine_quality.json"

    result = process_dataset("wine_quality", source, output)

    assert result["rows_processed"] == 2
    assert result["kpis"]["valid_sample_count"] == 2
    assert result["kpis"]["samples_by_color"] == {"red": 1, "white": 1}
    assert output.is_file()
    assert len(list(_wine_rows(source))) == 2


def test_bike_archive_processing_writes_offline_summary(tmp_path):
    source = tmp_path / "bike.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "Bike-Sharing-Dataset/hour.csv",
            "instant,dteday,hr,cnt,casual,registered,temp\n"
            "1,2011-01-01,8,12,2,10,0.5\n",
        )
    output = tmp_path / "processed" / "bike.json"

    result = process_dataset("bike", source, output)

    assert output.is_file()
    assert result["rows_processed"] == 1
    assert result["source_filename"] == source.name
    assert result["source_size_bytes"] == source.stat().st_size
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert json.loads(output.read_text(encoding="utf-8"))["kpis"]["rental_count"] == 12


def test_dataset_download_enforces_content_length_and_writes_atomically(tmp_path, monkeypatch):
    class FakeResponse:
        def __init__(self, content):
            self.headers = {"Content-Length": "4"}
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _size):
            content, self.content = self.content, b""
            return content

    response = FakeResponse(b"data")
    monkeypatch.setattr(
        "benchforge.public_data.urllib.request.urlopen",
        lambda request, timeout: response,
    )

    destination = _download("bike", tmp_path / "raw")

    assert destination.read_bytes() == b"data"
    assert not destination.with_suffix(".zip.part").exists()


def test_dataset_download_discards_incomplete_transfer(tmp_path, monkeypatch):
    class FakeResponse:
        def __init__(self):
            self.headers = {"Content-Length": "4"}
            self.content = b"dat"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _size):
            content, self.content = self.content, b""
            return content

    monkeypatch.setattr(
        "benchforge.public_data.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(),
    )

    with pytest.raises(ValueError, match="Incomplete dataset download"):
        _download("bike", tmp_path / "raw")

    assert not (tmp_path / "raw" / DATASETS["bike"]["source_file"]).exists()
    assert not list((tmp_path / "raw").glob("*.part"))


def test_retail_archive_rejects_oversized_extracted_workbook(tmp_path, monkeypatch):
    source = tmp_path / "retail.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Online Retail.xlsx", b"placeholder")

    original_getinfo = zipfile.ZipFile.getinfo

    def oversized_getinfo(archive, name):
        info = original_getinfo(archive, name)
        if name.lower().endswith(".xlsx"):
            info.file_size = DATASETS["retail"]["max_download_bytes"] + 1
        return info

    monkeypatch.setattr(zipfile.ZipFile, "getinfo", oversized_getinfo)
    with pytest.raises(ValueError, match="extraction limit"):
        list(_retail_rows(source))


def test_dataset_catalog_reports_local_availability_and_unverified_sources(tmp_path):
    from benchforge.public_data import list_datasets

    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    raw.mkdir()
    processed.mkdir()
    (raw / "bike-sharing-dataset.zip").write_bytes(b"fixture")
    (processed / "bike.json").write_text('{"dataset":"bike"}', encoding="utf-8")

    catalog = list_datasets(raw, processed)
    assert len(catalog) == 7
    bike = next(item for item in catalog if item["id"] == "bike")
    assert bike["local_available"] is True
    assert bike["local_size_bytes"] == 7
    assert bike["processed_available"] is True
    assert next(item for item in catalog if item["id"] == "citibike")[
        "status"
    ] == "not_implemented"
