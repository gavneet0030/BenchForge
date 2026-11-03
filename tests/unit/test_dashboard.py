import http.client
import io
import json
import logging
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import quote

from pypdf import PdfWriter

from benchforge.dashboard import create_handler, serve
from benchforge.documents import MAX_PDF_BYTES


def request(server, path, headers=None):
    connection = http.client.HTTPConnection(*server.server_address)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")
    finally:
        connection.close()


def request_post(server, path, body, headers):
    connection = http.client.HTTPConnection(*server.server_address)
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")
    finally:
        connection.close()


def blank_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_dashboard_empty_history_and_missing_public_data_states(tmp_path):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(tmp_path / "missing.sqlite", tmp_path / "processed"),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, content_type = request(server, "/")
        assert status == 200
        assert "Systems Performance &amp; Reliability Intelligence" in body.decode()
        assert 'name="color-scheme" content="light"' in body.decode()
        assert content_type.startswith("text/html")

        status, body, content_type = request(server, "/api/benchmarks")
        assert status == 200
        assert b'"runs": []' in body
        assert content_type.startswith("application/json")

        status, body, _ = request(server, "/api/public?dataset=bike")
        assert status == 200
        assert b'"available": false' in body
        assert b"No processed case-study data found" in body

        status, _, _ = request(server, "/api/public?dataset=unknown")
        assert status == 400

        status, body, _ = request(server, "/healthz")
        assert status == 200
        assert json.loads(body) == {"status": "ok"}

        status, body, _ = request(server, "/readyz")
        assert status == 200
        assert json.loads(body) == {"status": "ready"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_metrics_and_logs_redact_query_values_and_document_ids(
    tmp_path, caplog
):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(tmp_path / "missing.sqlite", tmp_path / "processed"),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    document_id = "0123456789abcdef0123456789abcdef"
    caplog.set_level(logging.INFO, logger="benchforge.dashboard")
    try:
        assert request(
            server, "/api/history?run_id=secret-run&token=secret-token"
        )[0] == 404
        assert request(server, f"/api/documents/{document_id}")[0] == 404

        status, body, _ = request(server, "/metrics")
        assert status == 200
        metrics = json.loads(body)
        request_counts = {
            (item["method"], item["route"], item["status"]): item["count"]
            for item in metrics["requests"]
        }
        assert request_counts[("GET", "/api/history", 404)] == 1
        assert request_counts[("GET", "/api/documents/{id}", 404)] == 1
        assert metrics["request_duration_seconds"]["count"] == 2
        assert "secret-run" not in caplog.text
        assert "secret-token" not in caplog.text
        assert document_id not in caplog.text
        logged_routes = {
            record.route
            for record in caplog.records
            if record.name == "benchforge.dashboard"
        }
        assert "/api/history" in logged_routes
        assert "/api/documents/{id}" in logged_routes
        request_events = [
            record
            for record in caplog.records
            if record.name == "benchforge.dashboard"
            and hasattr(record, "http_method")
            and record.route in {"/api/history", "/api/documents/{id}"}
        ]
        assert len(request_events) == 2
        assert all(record.http_method == "GET" for record in request_events)
        assert all(record.duration_seconds >= 0 for record in request_events)
        assert {
            (record.route, record.status_code) for record in request_events
        } == {
            ("/api/history", 404),
            ("/api/documents/{id}", 404),
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_readiness_fails_for_invalid_database_path(tmp_path):
    database = tmp_path / "database"
    database.mkdir()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), create_handler(database, tmp_path / "processed")
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, _ = request(server, "/readyz")
        assert status == 503
        assert json.loads(body) == {"status": "not_ready"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_rejects_untrusted_host_headers_on_loopback(tmp_path):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(tmp_path / "missing.sqlite", tmp_path / "processed"),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, _ = request(
            server,
            "/api/benchmarks?token=secret",
            headers={"Host": f"attacker.example:{server.server_address[1]}"},
        )
        assert status == 421
        assert b'"runs"' not in body

        status, body, _ = request(
            server,
            "/healthz",
            headers={"Host": f"localhost:{server.server_address[1]}"},
        )
        assert status == 200
        assert json.loads(body) == {"status": "ok"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_csv_endpoint_exports_existing_run(tmp_path):
    from benchforge.storage import save_samples

    database = tmp_path / "history.sqlite"
    save_samples(
        database,
        [
            {
                "timestamp_utc": "2026-10-28T10:00:00+00:00",
                "workload": "hash_lookup",
                "sample_index": 0,
                "elapsed_ns": 100,
                "schema_version": 1,
                "pair_index": 0,
            }
        ],
        run_id="export-run",
        variant="baseline",
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), create_handler(database, tmp_path / "processed")
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, content_type = request(
            server, "/api/export.csv?run_id=export-run"
        )
        assert status == 200
        assert body.startswith(b"\xef\xbb\xbf")
        assert b"elapsed_ns" in body
        assert b",100," in body
        assert content_type.startswith("text/csv")
        status, _, _ = request(server, "/api/export.csv?run_id=not-found")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_bounds_history_samples_and_sanitizes_csv(tmp_path):
    import json
    import sqlite3

    from benchforge.storage import initialize_database, save_samples

    database = tmp_path / "history.sqlite"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO benchmark_samples (
                timestamp_utc, workload, sample_index, elapsed_ns, schema_version,
                run_id, variant, config_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "2026-10-28T10:00:00+00:00",
                    "hash_lookup",
                    0,
                    100,
                    1,
                    f"run-{index:03d}",
                    "single",
                    "{}",
                )
                for index in range(101)
            ],
        )
    many_records = records_for_dashboard(201)
    save_samples(
        database,
        many_records,
        run_id="oversized-run",
        variant="single",
    )
    formula = {
        "timestamp_utc": "2026-10-28T10:00:00+00:00",
        "workload": "hash_lookup",
        "sample_index": 0,
        "elapsed_ns": 100,
        "schema_version": 1,
        "binary": "=HYPERLINK(\"https://example.invalid\")",
    }
    save_samples(database, [formula], run_id="formula-run", variant="single")
    save_samples(
        database,
        [formula],
        run_id="export-too-large",
        variant="single",
        config={"payload": "x" * 65_537},
    )

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), create_handler(database, tmp_path / "processed")
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, _ = request(server, "/api/benchmarks")
        payload = json.loads(body)
        assert status == 200
        assert payload["history_truncated"] is True
        assert len(payload["runs"]) == 100
        oversized_run = next(
            run for run in payload["runs"] if run["run_id"] == "oversized-run"
        )
        assert oversized_run["samples_truncated"] is True
        assert oversized_run["samples"] == []
        assert oversized_run["decision"] is None

        status, body, _ = request(
            server, "/api/export.csv?run_id=formula-run"
        )
        assert status == 200
        assert b"'=HYPERLINK" in body
        status, body, _ = request(
            server, "/api/export.csv?run_id=export-too-large"
        )
        assert status == 413
        assert b"text field exceeding" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def records_for_dashboard(count):
    return [
        {
            "timestamp_utc": f"2026-10-28T10:00:{index % 60:02d}+00:00",
            "workload": "hash_lookup",
            "sample_index": index,
            "elapsed_ns": 100 + index,
            "schema_version": 1,
            "pair_index": index,
        }
        for index in range(count)
    ]


def test_dashboard_serves_real_case_study_summary_and_history_errors(tmp_path):
    import json

    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "bike.json").write_text(
        json.dumps(
            {
                "dataset": "bike",
                "label": "UCI Bike Sharing",
                "source_url": "https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset",
                "license": "CC BY 4.0",
                "access_date": "2026-10-28",
                "rows_processed": 1,
                "kpis": {"rental_count": 12},
                "quality": {},
                "charts": {"hourly_demand": [{"hour": 8, "average_rentals": 12}]},
                "notes": [],
            }
        ),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(tmp_path / "missing.sqlite", processed),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, _ = request(server, "/api/public?dataset=bike")
        assert status == 200
        assert b'"rental_count": 12' in body
        status, body, _ = request(
            server,
            "/api/history?run_id=current&reference_run_id=previous",
        )
        assert status == 404
        assert b"No benchmark database" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_provenance_links_validate_https_scheme():
    from benchforge.dashboard import _PAGE

    assert 'href="${safeUrl(x.source_url)}"' in _PAGE


def test_dashboard_exposes_household_and_wine_case_studies(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    summaries = {
        "household_power": {
            "dataset": "household_power",
            "label": "Household Power",
            "rows_processed": 1,
            "kpis": {"valid_active_power_readings": 1},
            "charts": {"monthly_active_power": [{"month": "2007-01", "mean_kw": 1.5}]},
        },
        "wine_quality": {
            "dataset": "wine_quality",
            "label": "Wine Quality",
            "rows_processed": 2,
            "kpis": {"valid_sample_count": 2},
            "charts": {"quality_distribution": [{"quality": 6, "samples": 2}]},
        },
    }
    for dataset, summary in summaries.items():
        (processed / f"{dataset}.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(tmp_path / "missing.sqlite", processed),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        for dataset, expected_label in (
            ("household_power", "Household Power"),
            ("wine_quality", "Wine Quality"),
        ):
            status, body, _ = request(
                server, f"/api/public?dataset={dataset}"
            )
            assert status == 200
            payload = json.loads(body)
            assert payload["available"] is True
            assert payload["label"] == expected_label
        status, body, _ = request(server, "/")
        assert status == 200
        assert b'value="household_power"' in body
        assert b'value="wine_quality"' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_server_restricts_host_to_loopback(tmp_path):
    try:
        serve(tmp_path / "db.sqlite", tmp_path, host="0.0.0.0")
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("Dashboard accepted a non-loopback host.")


def test_dashboard_exposes_bounded_pipeline_history_without_local_paths(tmp_path):
    import json

    from benchforge.pipeline import ingest_csv

    database = tmp_path / "history.sqlite"
    source = tmp_path / "private-source.csv"
    source.write_text("kind,value\na,4\n", encoding="utf-8")
    ingest_csv(database, source, table="events")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), create_handler(database, tmp_path / "processed")
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, _ = request(server, "/api/pipelines")
        payload = json.loads(body)
        assert status == 200
        assert payload["limit"] == 100
        assert len(payload["runs"]) == 1
        assert payload["runs"][0]["source_name"] == "private-source.csv"
        assert str(tmp_path) not in body.decode()
        assert payload["runs"][0]["output_rows"] == 1
        assert payload["runs"][0]["rows_per_second"] > 0

        status, body, _ = request(server, "/")
        assert status == 200
        assert "Pipeline Runs" in body.decode()
        assert 'id="pipeline-overview"' in body.decode()
        assert "<th>Throughput</th>" in body.decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_pdf_upload_search_and_local_text_export(tmp_path):
    database = tmp_path / "history.sqlite"
    documents = tmp_path / "documents"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(database, tmp_path / "processed", documents),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, content_type = request_post(
            server,
            "/api/documents",
            blank_pdf(),
            {
                "Content-Type": "application/pdf",
                "X-File-Name": quote(r"..\private.pdf"),
            },
        )
        import json

        payload = json.loads(body)
        assert status == 201
        assert content_type.startswith("application/json")
        assert payload["filename"] == "uploaded.pdf"
        assert payload["status"] == "no_extractable_text"
        document_id = payload["document_id"]
        assert len(document_id) == 32
        assert sorted(path.suffix for path in documents.iterdir()) == [".json", ".pdf"]
        assert not (tmp_path / "private.pdf").exists()

        status, body, _ = request(server, f"/api/documents/{document_id}?q=missing")
        assert status == 200
        result = json.loads(body)
        assert result["matches"] == []
        assert "text" not in result

        status, body, content_type = request(
            server, f"/api/documents/{document_id}/text"
        )
        assert status == 200
        assert body == b""
        assert content_type.startswith("text/plain")

        status, body, _ = request(server, "/api/documents/invalid")
        assert status == 400
        status, _, _ = request(server, f"/api/documents/{'0' * 32}")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)



def test_dashboard_document_routes_reject_remote_clients(tmp_path):
    class RemoteClientHTTPServer(ThreadingHTTPServer):
        def finish_request(self, request_socket, client_address):
            simulated_remote_address = ("192.0.2.10", client_address[1])
            super().finish_request(request_socket, simulated_remote_address)

    server = RemoteClientHTTPServer(
        ("127.0.0.1", 0),
        create_handler(
            tmp_path / "history.sqlite",
            tmp_path / "processed",
            tmp_path / "documents",
        ),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    try:
        document_id = "a" * 32
        paths = (
            f"/api/documents/{document_id}?q=private",
            f"/api/documents/{document_id}/text",
        )

        for path in paths:
            status, body, content_type = request(server, path)
            assert status == 403
            assert content_type.startswith("application/json")
            assert b"loopback clients" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

def test_dashboard_pdf_upload_rejects_bad_signatures_and_oversize_requests(tmp_path):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(tmp_path / "history.sqlite", tmp_path / "processed"),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, _ = request_post(
            server,
            "/api/documents",
            b"not a PDF",
            {
                "Content-Type": "application/pdf",
                "X-File-Name": "invalid.pdf",
            },
        )
        assert status == 422
        assert b"PDF signature" in body

        connection = http.client.HTTPConnection(*server.server_address)
        try:
            connection.putrequest("POST", "/api/documents")
            connection.putheader("Content-Type", "application/pdf")
            connection.putheader("X-File-Name", "large.pdf")
            connection.putheader("Content-Length", str(MAX_PDF_BYTES + 1))
            connection.endheaders()
            response = connection.getresponse()
            assert response.status == 413
            assert b"upload limit" in response.read()
        finally:
            connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_pdf_upload_limit_is_configurable(tmp_path):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(
            tmp_path / "history.sqlite",
            tmp_path / "processed",
            max_pdf_bytes=1,
        ),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body, _ = request(server, "/")
        assert status == 200
        assert 'data-max-bytes="1"' in body.decode()

        status, body, _ = request_post(
            server,
            "/api/documents",
            blank_pdf(),
            {
                "Content-Type": "application/pdf",
                "X-File-Name": "small-limit.pdf",
            },
        )
        assert status == 413
        assert b"1-byte upload limit" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_document_routes_handle_corrupted_metadata_safely(tmp_path):
    database = tmp_path / "history.sqlite"
    processed = tmp_path / "processed"
    documents = tmp_path / "documents"

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(database, processed, documents),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    try:
        status, body, content_type = request_post(
            server,
            "/api/documents",
            blank_pdf(),
            {
                "Content-Type": "application/pdf",
                "X-File-Name": "integrity-test.pdf",
            },
        )

        assert status == 201
        assert content_type.startswith("application/json")

        payload = json.loads(body)
        document_id = payload["document_id"]
        assert len(document_id) == 32

        metadata_path = documents / f"{document_id}.json"
        assert metadata_path.is_file()

        metadata_path.write_text(
            "{deliberately-corrupted-json",
            encoding="utf-8",
        )

        paths = (
            f"/api/documents/{document_id}",
            f"/api/documents/{document_id}/text",
        )

        for path in paths:
            status, body, content_type = request(server, path)

            assert status == 500
            assert content_type.startswith("application/json")

            error_payload = json.loads(body)
            assert error_payload == {
                "error": "Stored PDF analysis could not be read."
            }

            assert b"JSONDecodeError" not in body
            assert b"Traceback" not in body
            assert b"deliberately-corrupted" not in body

    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)