import time

import pytest
from fastapi.testclient import TestClient

from benchforge.storage import save_samples
from benchforge.webapp import create_app


def _client(tmp_path, *, native_binary=None, comparison_binary=None):
    app = create_app(
        tmp_path / "web-history.sqlite",
        native_binary=native_binary,
        comparison_binary=comparison_binary,
    )
    return TestClient(app, base_url="http://127.0.0.1")


def _wait_for_status(client, run_id, expected, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}/status")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"Run {run_id} did not finish before the test timeout.")


def test_python_benchmark_persists_and_appears_in_dashboard_analytics(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/runs",
            json={
                "category": "cpu",
                "engine": "python",
                "input_size": 32,
                "runs": 2,
                "warmup": 0,
            },
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        finished = _wait_for_status(client, run_id, "succeeded")
        assert finished["status"] == "succeeded"
        assert finished["sample_count"] == 2

        detail = client.get(f"/api/v1/runs/{run_id}").json()
        assert detail["workload"] == "cpu"
        assert [sample["variant"] for sample in detail["samples"]] == [
            "python",
            "python",
        ]
        assert all(sample["elapsed_ns"] > 0 for sample in detail["samples"])
        assert "binary_path" not in detail["config"]

        listing = client.get("/api/v1/runs?status=succeeded&limit=1").json()
        assert listing["total"] == 1
        assert listing["items"][0]["run_id"] == run_id

        summary = client.get("/api/v1/analytics/summary").json()
        assert summary["total_runs"] == 1
        assert summary["successful_runs"] == 1
        assert summary["sample_count"] == 2
        assert summary["latency"]["sample_count"] == 2

        trends = client.get("/api/v1/analytics/trends").json()
        assert len(trends["items"]) == 1
        assert trends["items"][0]["run_id"] == run_id


def test_export_endpoints_return_actual_selected_run_and_safe_file_names(tmp_path):
    with _client(tmp_path) as client:
        accepted = client.post(
            "/api/v1/runs",
            json={"engine": "python", "input_size": 8, "runs": 2, "warmup": 0},
        ).json()
        run_id = accepted["run_id"]
        _wait_for_status(client, run_id, "succeeded")

        csv_response = client.get(f"/api/v1/exports/{run_id}?format=csv")
        assert csv_response.status_code == 200
        assert csv_response.content.startswith(b"\xef\xbb\xbf")
        assert b"elapsed_ns" in csv_response.content
        assert f'filename="benchforge-{run_id}.csv"' in csv_response.headers[
            "content-disposition"
        ]

        json_response = client.get(f"/api/v1/exports/{run_id}?format=json")
        assert json_response.status_code == 200
        assert json_response.json()["run_id"] == run_id
        assert len(json_response.json()["samples"]) == 2

        pdf_response = client.get(f"/api/v1/exports/{run_id}?format=pdf")
        assert pdf_response.status_code == 200
        assert pdf_response.content.startswith(b"%PDF-1.4")
        assert pdf_response.content.endswith(b"%%EOF\n")


def test_benchmark_submission_is_bounded_and_idempotent(tmp_path):
    with _client(tmp_path) as client:
        body = {
            "category": "cpu",
            "engine": "python",
            "input_size": 16,
            "runs": 2,
            "warmup": 0,
        }
        first = client.post(
            "/api/v1/runs",
            json=body,
            headers={"Idempotency-Key": "safe-request-1"},
        )
        second = client.post(
            "/api/v1/runs",
            json=body,
            headers={"Idempotency-Key": "safe-request-1"},
        )
        assert first.status_code == second.status_code == 202
        assert first.json()["run_id"] == second.json()["run_id"]
        assert second.json()["duplicate"] is True

        too_large = client.post(
            "/api/v1/runs",
            json={**body, "input_size": 1_000_001},
        )
        assert too_large.status_code == 422
        assert "input_size" in str(too_large.json()["error"]["details"])

        unsupported = client.post(
            "/api/v1/runs",
            json={**body, "category": "storage", "engine": "python"},
        )
        assert unsupported.status_code == 422

        unknown_field = client.post(
            "/api/v1/runs",
            json={**body, "command": "not-accepted"},
        )
        assert unknown_field.status_code == 422
        assert _wait_for_status(client, first.json()["run_id"], "succeeded")["status"] == (
            "succeeded"
        )


def test_missing_native_engine_fails_explicitly_without_running_arbitrary_command(
    tmp_path,
):
    missing_binary = tmp_path / "missing.exe"
    with _client(tmp_path, native_binary=missing_binary) as client:
        accepted = client.post(
            "/api/v1/runs",
            json={
                "category": "memory",
                "engine": "native",
                "input_size": 128,
                "runs": 2,
                "warmup": 0,
            },
        )
        assert accepted.status_code == 202
        finished = _wait_for_status(client, accepted.json()["run_id"], "failed")
        assert finished["status"] == "failed"
        detail = client.get(f"/api/v1/runs/{finished['run_id']}").json()
        assert detail["error_code"] == "engine_unavailable"
        assert detail["sample_count"] == 0


def test_run_comparison_rejects_incompatible_configs_and_computes_compatible_runs(
    tmp_path,
):
    database = tmp_path / "web-history.sqlite"
    with _client(tmp_path) as client:
        config = {
            "category": "cpu",
            "dataset": "synthetic",
            "engine": "python",
            "input_size": 10,
            "iterations": 10,
            "workload": "cpu",
        }

        def add_run(run_id, elapsed_values, **overrides):
            save_samples(
                database,
                [
                    {
                        "timestamp_utc": f"2026-11-02T10:00:0{index}+00:00",
                        "workload": "cpu_xorshift64",
                        "sample_index": index,
                        "elapsed_ns": elapsed,
                        "schema_version": 1,
                    }
                    for index, elapsed in enumerate(elapsed_values)
                ],
                run_id=run_id,
                variant="python",
                config={**config, **overrides},
            )

        add_run("run-reference", [100, 101, 102, 103, 104])
        add_run("run-current", [110, 111, 112, 113, 114])
        add_run("run-incompatible", [120, 121, 122, 123, 124], input_size=11)

        compatible = client.post(
            "/api/v1/comparisons",
            json={
                "current_run_id": "run-current",
                "reference_run_id": "run-reference",
                "current_variant": "python",
                "reference_variant": "python",
                "n_resamples": 100,
            },
        )
        assert compatible.status_code == 200
        assert compatible.json()["compatibility"] == "compatible"
        assert compatible.json()["current_count"] == 5
        assert compatible.json()["current_median_ns"] == 112

        incompatible = client.post(
            "/api/v1/comparisons",
            json={
                "current_run_id": "run-incompatible",
                "reference_run_id": "run-reference",
                "current_variant": "python",
                "reference_variant": "python",
                "n_resamples": 100,
            },
        )
        assert incompatible.status_code == 409
        assert "input_size" in incompatible.json()["error"]["details"]


def test_health_readiness_and_integration_states_are_truthful_and_local_only(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["database"] == "available"
        assert ready.json()["native_benchmark"] == "unavailable"

        integrations = client.get("/api/v1/integrations").json()["integrations"]
        assert integrations
        assert all(item["service_status"] == "not_checked" for item in integrations)

        system = client.get("/api/v1/system").json()
        assert system["logical_cpu_count"] >= 1
        assert "python_version" in system

        hostile_host = client.get(
            "/api/v1/options",
            headers={"Host": "attacker.example"},
        )
        assert hostile_host.status_code == 421

        hostile_origin = client.get(
            "/api/v1/options",
            headers={"Origin": "https://attacker.example"},
        )
        assert "access-control-allow-origin" not in hostile_origin.headers


def test_history_pagination_and_date_validation(tmp_path):
    with _client(tmp_path) as client:
        for index in range(3):
            response = client.post(
                "/api/v1/runs",
                json={
                    "engine": "python",
                    "input_size": 8 + index,
                    "runs": 2,
                    "warmup": 0,
                },
            )
            _wait_for_status(client, response.json()["run_id"], "succeeded")
        page = client.get("/api/v1/runs?limit=2&offset=1").json()
        assert page["total"] == 3
        assert len(page["items"]) == 2
        assert client.get(
            "/api/v1/runs?from_date=2026-11-03&to_date=2026-11-02"
        ).status_code == 422


def test_comparison_requires_minimum_sample_count(tmp_path):
    database = tmp_path / "web-history.sqlite"
    with _client(tmp_path) as client:
        config = {
            "category": "cpu",
            "dataset": "synthetic",
            "engine": "python",
            "input_size": 10,
            "iterations": 10,
            "workload": "cpu",
        }
        for run_id in ("few-a", "few-b"):
            save_samples(
                database,
                [
                    {
                        "timestamp_utc": "2026-11-02T10:00:00+00:00",
                        "workload": "cpu_xorshift64",
                        "sample_index": 0,
                        "elapsed_ns": 100,
                        "schema_version": 1,
                    }
                ],
                run_id=run_id,
                variant="python",
                config=config,
            )
        response = client.post(
            "/api/v1/comparisons",
            json={
                "current_run_id": "few-a",
                "reference_run_id": "few-b",
                "current_variant": "python",
                "reference_variant": "python",
                "n_resamples": 100,
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "insufficient_samples"


@pytest.mark.parametrize("run_id", ["../private", "contains space"])
def test_run_id_path_rejects_unsafe_identifiers(tmp_path, run_id):
    with _client(tmp_path) as client:
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code in {400, 404, 422}


def test_runs_collection_trailing_slash_is_not_treated_as_a_run_id(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/api/v1/runs/")
        assert response.status_code == 200
        assert response.json()["items"] == []
