import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from benchforge.dashboard import create_handler
from benchforge.storage import save_samples


def _request(server, path):
    connection = http.client.HTTPConnection(*server.server_address)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")
    finally:
        connection.close()


def test_prometheus_metrics_include_redacted_http_and_storage_metrics(tmp_path):
    database = tmp_path / "history.sqlite"
    save_samples(
        database,
        [
            {
                "timestamp_utc": "2026-01-01T00:00:00+00:00",
                "workload": "cpu",
                "sample_index": 0,
                "elapsed_ns": 10,
                "schema_version": 1,
            }
        ],
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), create_handler(database, tmp_path / "data")
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        assert _request(server, "/healthz")[0] == 200
        assert _request(
            server, "/api/history?run_id=private-run&token=private-token"
        )[0] == 409

        status, body, content_type = _request(server, "/metrics/prometheus")
        exposition = body.decode("utf-8")
        assert status == 200
        assert content_type.startswith("text/plain; version=0.0.4")
        assert (
            'benchforge_http_requests_total{method="GET",route="/healthz",status="200"} 1'
            in exposition
        )
        assert (
            'benchforge_http_requests_total{method="GET",route="/api/history",status="409"} 1'
            in exposition
        )
        assert "benchforge_http_errors_total 1" in exposition
        assert "benchforge_storage_database_ready 1" in exposition
        assert "benchforge_benchmark_samples 1" in exposition
        assert "private-run" not in exposition
        assert "private-token" not in exposition

        status, body, _ = _request(server, "/metrics")
        metrics = json.loads(body)
        assert status == 200
        assert metrics["storage"] == {
            "database_ready": 1,
            "benchmark_samples": 1,
            "pipeline_runs": 0,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_grafana_dashboard_uses_metrics_exposed_by_dashboard():
    dashboard = json.loads(
        (Path(__file__).parents[2] / "dashboards" / "benchforge-overview.json")
        .read_text(encoding="utf-8")
    )

    assert dashboard["title"] == "BenchForge Local Observability"
    expressions = {
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel["targets"]
    }
    assert any("benchforge_http_requests_total" in expr for expr in expressions)
    assert any("benchforge_http_errors_total" in expr for expr in expressions)
    assert any("benchforge_http_request_duration_seconds_max" in expr for expr in expressions)
    assert "benchforge_benchmark_samples" in expressions
