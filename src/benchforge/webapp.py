"""Local FastAPI surface for the BenchForge React application."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import logging
import math
import os
import platform
import re
import sqlite3
import statistics
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from benchforge.metrics import summarize_latencies
from benchforge.runner import NATIVE_PROCESS_TIMEOUT_SECONDS
from benchforge.storage import initialize_database, save_samples

_LOGGER = logging.getLogger("benchforge.webapp")
_API_PREFIX = "/api/v1"
_MAX_ACTIVE_JOBS = 4
_MAX_RETAINED_SAMPLES = 1_000
_MAX_EXPORT_BYTES = 10_000_000
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_IDEMPOTENCY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SAFE_CONFIG_KEYS = frozenset(
    {
        "category",
        "dataset",
        "engine",
        "file_size_kib",
        "input_size",
        "iterations",
        "runs",
        "timeout_seconds",
        "warmup",
        "workload",
        "workers",
        "working_set_kib",
    }
)
_OPTIONAL_MODULES = {
    "DuckDB": ("duckdb", "duckdb"),
    "Polars": ("polars", "polars"),
    "PyArrow": ("pyarrow", "pyarrow"),
    "PostgreSQL client": ("psycopg", "psycopg"),
    "Redis client": ("redis", "redis"),
    "Kafka client": ("kafka", "kafka-python"),
    "S3 client": ("boto3", "boto3"),
    "Airflow": ("airflow", "apache-airflow"),
    "Spark": ("pyspark", "pyspark"),
}
_WORKLOAD_BY_CATEGORY = {
    "cpu": "cpu",
    "memory": "memory",
    "storage": "file_io",
}


class BenchmarkRequest(BaseModel):
    """Validated, deliberately bounded settings accepted by the local API."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["cpu", "memory", "storage"] = "cpu"
    engine: Literal["python", "native", "both"] = "both"
    input_size: int = Field(default=10_000, ge=1, le=1_000_000)
    runs: int = Field(default=5, ge=2, le=10)
    warmup: int = Field(default=1, ge=0, le=2)
    workers: Literal[1] = 1
    timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=NATIVE_PROCESS_TIMEOUT_SECONDS,
    )
    dataset: Literal["synthetic"] = "synthetic"

    @model_validator(mode="after")
    def validate_supported_workload(self) -> BenchmarkRequest:
        if self.category != "cpu" and self.engine != "native":
            raise ValueError("Python and equivalent-engine runs are currently CPU-only.")
        if self.category == "memory" and self.input_size > 4_096:
            raise ValueError("Memory input_size must not exceed 4096 KiB per sample.")
        if self.category == "storage" and self.input_size > 8_192:
            raise ValueError("Storage input_size must not exceed 8192 KiB per sample.")
        if self.category == "cpu" and self.engine == "both":
            total_operations = self.input_size * (self.runs + self.warmup) * 2
            if total_operations > 10_000_000:
                raise ValueError(
                    "Equivalent Python/native runs must not exceed 10000000 total operations."
                )
        return self

    def safe_config(self) -> dict[str, Any]:
        values = self.model_dump()
        values["workload"] = _WORKLOAD_BY_CATEGORY[self.category]
        if self.category == "memory":
            values["working_set_kib"] = self.input_size
        elif self.category == "storage":
            values["file_size_kib"] = self.input_size
        else:
            values["iterations"] = self.input_size
        return values


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_run_id: str = Field(min_length=1, max_length=128)
    reference_run_id: str = Field(min_length=1, max_length=128)
    current_variant: Literal["single", "python", "native"] = "single"
    reference_variant: Literal["single", "python", "native"] = "single"
    threshold: float = Field(default=0.03, ge=0, le=1)
    confidence: float = Field(default=0.95, gt=0, lt=1)
    n_resamples: int = Field(default=1_000, ge=100, le=10_000)
    seed: int = Field(default=2026, ge=0, le=2**32 - 1)


def _readonly_connection(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise FileNotFoundError("Benchmark database is not initialized.")
    connection = sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=2,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _initialize_web_jobs(database: Path) -> None:
    with sqlite3.connect(database, timeout=5) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS web_benchmark_jobs (
                run_id TEXT PRIMARY KEY,
                idempotency_hash TEXT UNIQUE,
                submitted_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
                ),
                category TEXT NOT NULL,
                workload TEXT NOT NULL,
                engine TEXT NOT NULL,
                config_json TEXT NOT NULL,
                error_code TEXT,
                error_message TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_jobs_status_date "
            "ON web_benchmark_jobs(status, submitted_at)"
        )


def _configuration(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        candidate = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(candidate, dict):
        return {}
    return {key: value for key, value in candidate.items() if key in _SAFE_CONFIG_KEYS}


def _safe_run_id(run_id: str) -> str:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Run ID must contain only letters, numbers, dots, underscores, or hyphens.")
    return run_id


def _job_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "submitted_at": row["submitted_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "category": row["category"],
        "workload": row["workload"],
        "engine": row["engine"],
        "config": _configuration(row["config_json"]),
        "error_code": row["error_code"],
        "error_message": row["error_message"],
    }


def _run_page(
    database: Path,
    *,
    limit: int,
    offset: int,
    status_filter: str,
    workload: str | None,
    engine: str | None,
    query: str | None,
    from_date: date | None,
    to_date: date | None,
    sort_by: str,
    descending: bool,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if status_filter != "all":
        clauses.append("status = ?")
        parameters.append(status_filter)
    if workload:
        clauses.append("workload = ?")
        parameters.append(workload)
    if engine:
        clauses.append("engine = ?")
        parameters.append(engine)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(run_id LIKE ? ESCAPE '\\' OR workload LIKE ? ESCAPE '\\')")
        parameters.extend((f"%{escaped}%", f"%{escaped}%"))
    if from_date:
        clauses.append("started_at >= ?")
        parameters.append(from_date.isoformat())
    if to_date:
        clauses.append("started_at < ?")
        parameters.append(date.fromordinal(to_date.toordinal() + 1).isoformat())
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    order_column = {
        "started_at": "started_at",
        "sample_count": "sample_count",
        "workload": "workload",
    }[sort_by]
    direction = "DESC" if descending else "ASC"
    cte = """
        WITH stored_runs AS (
            SELECT run_id, MIN(timestamp_utc) AS started_at,
               MIN(workload) AS workload, MIN(variant) AS engine,
               MIN(config_json) AS config_json, COUNT(*) AS sample_count
        FROM benchmark_samples GROUP BY run_id
    ),
        all_runs AS (
            SELECT j.run_id, COALESCE(s.started_at, j.submitted_at) AS started_at,
                   j.workload, j.engine, j.status,
                   COALESCE(s.sample_count, 0) AS sample_count,
                   j.error_code, j.error_message, j.config_json
            FROM web_benchmark_jobs AS j
            LEFT JOIN stored_runs AS s ON s.run_id = j.run_id
            UNION ALL
            SELECT s.run_id, s.started_at, s.workload, s.engine, 'succeeded',
                   s.sample_count, NULL, NULL, s.config_json
            FROM stored_runs AS s
            LEFT JOIN web_benchmark_jobs AS j ON j.run_id = s.run_id
            WHERE j.run_id IS NULL
        ),
        filtered AS (SELECT * FROM all_runs
    """
    with _readonly_connection(database) as connection:
        total = connection.execute(
            cte + where + ") SELECT COUNT(*) FROM filtered",
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            cte
            + where
            + f") SELECT * FROM filtered ORDER BY {order_column} {direction}, run_id "
            "LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "started_at": row["started_at"],
            "workload": row["workload"],
            "engine": row["engine"],
            "status": row["status"],
            "sample_count": row["sample_count"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "config": _configuration(row["config_json"]),
        }
        for row in rows
    ], total


def _run_detail(database: Path, run_id: str) -> dict[str, Any] | None:
    _safe_run_id(run_id)
    with _readonly_connection(database) as connection:
        job = connection.execute(
            "SELECT * FROM web_benchmark_jobs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        sample_count = connection.execute(
            "SELECT COUNT(*) FROM benchmark_samples WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        if job is None and not sample_count:
            return None
        samples = connection.execute(
            """
            SELECT timestamp_utc, workload, sample_index, elapsed_ns, variant,
                   pair_index, bytes_processed, peak_process_memory_bytes,
                   peak_process_memory_source, current_working_set_bytes,
                   private_process_bytes, thread_count, process_cpu_time_ns,
                   cpu_utilization_percent
            FROM benchmark_samples
            WHERE run_id = ?
            ORDER BY variant, sample_index, id
            LIMIT ?
            """,
            (run_id, _MAX_RETAINED_SAMPLES + 1),
        ).fetchall()
        if job is not None:
            result = _job_record(job)
        else:
            first = connection.execute(
                "SELECT MIN(timestamp_utc), MIN(workload), MIN(config_json), "
                "MIN(variant) FROM benchmark_samples "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            stored_config = _configuration(first[2])
            result = {
                "run_id": run_id,
                "status": "succeeded",
                "submitted_at": first[0],
                "started_at": first[0],
                "finished_at": first[0],
                "category": stored_config.get("category", "unknown"),
                "workload": first[1],
                "engine": stored_config.get("engine", first[3]),
                "config": stored_config,
                "error_code": None,
                "error_message": None,
            }
    result["sample_count"] = sample_count
    result["samples_truncated"] = len(samples) > _MAX_RETAINED_SAMPLES
    if result["samples_truncated"]:
        samples = []
    result["samples"] = [dict(sample) for sample in samples]
    return result


def _optional_integrations() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for label, (module_name, package_name) in _OPTIONAL_MODULES.items():
        if importlib.util.find_spec(module_name) is None:
            result[label] = {"status": "not_installed", "version": ""}
            continue
        try:
            version = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            version = "installed; version unavailable"
        result[label] = {"status": "installed; service not checked", "version": version}
    return result


def _python_cpu_samples(request: BenchmarkRequest) -> list[dict[str, Any]]:
    from benchforge.phase4 import cpu_xorshift_checksum

    expected = cpu_xorshift_checksum(request.input_size)
    results: list[dict[str, Any]] = []
    for index in range(request.warmup + request.runs):
        started = time.perf_counter_ns()
        checksum = cpu_xorshift_checksum(request.input_size)
        elapsed_ns = max(time.perf_counter_ns() - started, 1)
        if checksum != expected:
            raise ValueError("Python CPU workload failed its deterministic checksum check.")
        if index < request.warmup:
            continue
        results.append(
            {
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "workload": "cpu_xorshift64",
                "sample_index": index - request.warmup,
                "elapsed_ns": elapsed_ns,
                "schema_version": 1,
                "os": platform.platform(),
                "python_version": sys.version.split()[0],
                "binary": "python",
            }
        )
    return results


def _store_benchmark(
    database: Path,
    binary: Path | None,
    comparison_binary: Path | None,
    run_id: str,
    request: BenchmarkRequest,
) -> int:
    config = request.safe_config()
    if request.engine == "python":
        records_by_variant = {"python": _python_cpu_samples(request)}
    elif request.engine == "both":
        if comparison_binary is None or not comparison_binary.is_file():
            raise FileNotFoundError("The native comparison engine is unavailable.")
        from benchforge.phase4 import run_cpu_comparison

        measured = run_cpu_comparison(
            comparison_binary,
            runs=request.runs,
            warmup=request.warmup,
            iterations=request.input_size,
            timeout_seconds=request.timeout_seconds,
        )
        records_by_variant = {
            variant: [record for record in measured if record["variant"] == variant]
            for variant in ("python", "native")
        }
    else:
        if binary is None or not binary.is_file():
            raise FileNotFoundError("The selected native benchmark engine is unavailable.")
        from benchforge.workload import run_native_workload

        workload_name = _WORKLOAD_BY_CATEGORY[request.category]
        measured = run_native_workload(
            binary,
            workload=workload_name,
            runs=request.runs,
            warmup=request.warmup,
            iterations=request.input_size if request.category == "cpu" else 1,
            working_set_kib=request.input_size if request.category == "memory" else 1,
            file_size_kib=request.input_size if request.category == "storage" else 1,
            timeout_seconds=request.timeout_seconds,
        )
        if request.category == "cpu":
            for record in measured:
                record["workload"] = "cpu_xorshift64"
        records_by_variant = {"native": measured}

    sample_count = 0
    for variant, records in records_by_variant.items():
        save_samples(
            database,
            records,
            run_id=run_id,
            variant=variant,
            config=config,
        )
        sample_count += len(records)
    return sample_count


def _set_job_status(
    database: Path,
    run_id: str,
    *,
    status: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    with sqlite3.connect(database, timeout=5) as connection:
        connection.execute(
            """
            UPDATE web_benchmark_jobs
            SET status = ?,
                started_at = COALESCE(?, started_at),
                finished_at = COALESCE(?, finished_at),
                error_code = ?,
                error_message = ?
            WHERE run_id = ?
            """,
            (status, started_at, finished_at, error_code, error_message, run_id),
        )


def _build_pdf(run: dict[str, Any]) -> bytes:
    samples = run["samples"]
    lines = [
        "BenchForge benchmark report",
        f"Run ID: {run['run_id']}",
        f"Status: {run['status']}",
        f"Workload: {run['workload']}",
        f"Engine: {run['engine']}",
        f"Observations: {run['sample_count']}",
    ]
    if samples:
        summary = summarize_latencies(sample["elapsed_ns"] for sample in samples)
        lines.extend(
            [
                f"Median runtime: {summary['median']:.0f} ns",
                f"P95 nearest-rank runtime: {summary['p95_nearest_rank']:.0f} ns",
                f"P99 reliable: {'yes' if summary['p99_reliable'] else 'no'}",
                "These are local observations, not a validated regression conclusion.",
            ]
        )
    text_commands = ["BT", "/F1 14 Tf", "50 790 Td"]
    for index, line in enumerate(lines):
        if index:
            text_commands.append("0 -22 Td")
            text_commands.append("/F1 11 Tf")
        escaped = (
            line.encode("ascii", "replace")
            .decode("ascii")
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        text_commands.append(f"({escaped}) Tj")
    text_commands.append("ET")
    stream = "\n".join(text_commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    if len(pdf) > _MAX_EXPORT_BYTES:
        raise ValueError("Generated PDF exceeds the export size limit.")
    return bytes(pdf)


def create_app(
    database: Path = Path("data/benchforge.sqlite"),
    *,
    native_binary: Path | None = None,
    comparison_binary: Path | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    """Create the loopback-oriented web API and optional built frontend."""
    database = Path(database).expanduser().resolve()
    native_binary = Path(native_binary).expanduser().resolve() if native_binary else None
    comparison_binary = (
        Path(comparison_binary).expanduser().resolve() if comparison_binary else None
    )
    frontend_root = Path(frontend_dist).expanduser().resolve() if frontend_dist else None
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="benchforge-benchmark")
    job_lock = threading.Lock()
    futures: dict[str, Future[None]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize_database(database)
        _initialize_web_jobs(database)
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="BenchForge local API",
        version="0.1.0",
        docs_url=f"{_API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=f"{_API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.database = database

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
        max_age=600,
    )

    @app.middleware("http")
    async def local_host_and_request_logging(request: Request, call_next):
        hostname = (request.url.hostname or "").lower()
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse(
                status_code=421,
                content={"error": {"code": "invalid_host", "message": "Use a loopback host."}},
            )
        started = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "other")
        _LOGGER.info(
            "web_request_completed method=%s route=%s status=%d duration_seconds=%.6f",
            request.method,
            route,
            response.status_code,
            time.perf_counter() - started,
            extra={
                "http_method": request.method,
                "route": route,
                "status_code": response.status_code,
                "duration_seconds": time.perf_counter() - started,
            },
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"] if part != "body"),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "One or more request values are invalid.",
                    "details": details,
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request could not be completed."
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": "request_error",
                    "message": message,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        _LOGGER.exception(
            "web_request_failed route=%s error_type=%s",
            getattr(request.scope.get("route"), "path", "other"),
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The request failed. Check the local server log.",
                }
            },
        )

    @app.get("/healthz")
    @app.get(f"{_API_PREFIX}/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    @app.get(f"{_API_PREFIX}/ready")
    async def readiness() -> JSONResponse:
        try:
            with _readonly_connection(database) as connection:
                connection.execute("SELECT 1").fetchone()
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if "benchmark_samples" not in tables or "web_benchmark_jobs" not in tables:
                    raise sqlite3.OperationalError("Required history tables are unavailable.")
        except (OSError, sqlite3.Error):
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "database": "unavailable",
                },
            )
        return JSONResponse(
            content={
                "status": "ready",
                "database": "available",
                "native_benchmark": (
                    "available" if native_binary and native_binary.is_file() else "unavailable"
                ),
                "comparison_benchmark": (
                    "available"
                    if comparison_binary and comparison_binary.is_file()
                    else "unavailable"
                ),
            }
        )

    @app.get(f"{_API_PREFIX}/options")
    async def options() -> dict[str, Any]:
        return {
            "categories": ["cpu", "memory", "storage"],
            "engines": {
                "python": True,
                "native": bool(native_binary and native_binary.is_file()),
                "both": bool(comparison_binary and comparison_binary.is_file()),
            },
            "limits": {
                "max_runs": 10,
                "max_warmups": 2,
                "max_cpu_input_size": 1_000_000,
                "max_memory_input_kib": 4_096,
                "max_storage_input_kib": 8_192,
                "max_workers": 1,
                "max_timeout_seconds": NATIVE_PROCESS_TIMEOUT_SECONDS,
            },
            "optional_integrations": _optional_integrations(),
        }

    @app.get(f"{_API_PREFIX}/system")
    async def system_info() -> dict[str, Any]:
        return {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor() or "Not reported by the operating system",
            "logical_cpu_count": os.cpu_count(),
            "optional_integrations": _optional_integrations(),
            "native_benchmark": bool(native_binary and native_binary.is_file()),
            "comparison_benchmark": bool(
                comparison_binary and comparison_binary.is_file()
            ),
        }

    @app.get(f"{_API_PREFIX}/integrations")
    async def integrations() -> dict[str, Any]:
        packages = _optional_integrations()
        return {
            "integrations": [
                {
                    "name": name,
                    **info,
                    "service_status": "not_checked",
                }
                for name, info in packages.items()
            ]
        }

    @app.get(f"{_API_PREFIX}/platform")
    async def data_platform() -> dict[str, Any]:
        try:
            with _readonly_connection(database) as connection:
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                dataset_count = 0
                if "dataset_catalog" in table_names:
                    dataset_count = connection.execute(
                        "SELECT COUNT(*) FROM dataset_catalog"
                    ).fetchone()[0]
                sample_count = connection.execute(
                    "SELECT COUNT(*) FROM benchmark_samples"
                ).fetchone()[0]
        except (OSError, sqlite3.Error):
            dataset_count = 0
            sample_count = 0
            database_status = "unavailable"
        else:
            database_status = "available"
        return {
            "database": database_status,
            "benchmark_sample_count": sample_count,
            "registered_dataset_count": dataset_count,
            "optional_integrations": _optional_integrations(),
        }

    @app.get(f"{_API_PREFIX}/runs")
    async def list_runs(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
        status_filter: Annotated[
            Literal["all", "queued", "running", "succeeded", "failed", "cancelled"],
            Query(alias="status"),
        ] = "all",
        workload: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
        engine: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
        search: Annotated[str | None, Query(max_length=100)] = None,
        from_date: date | None = None,
        to_date: date | None = None,
        sort_by: Literal["started_at", "sample_count", "workload"] = "started_at",
        descending: bool = True,
    ) -> dict[str, Any]:
        if from_date and to_date and from_date > to_date:
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "invalid_date_range",
                        "message": "from_date must not be after to_date.",
                    }
                },
            )
        try:
            runs, total = _run_page(
                database,
                limit=limit,
                offset=offset,
                status_filter=status_filter,
                workload=workload,
                engine=engine,
                query=search,
                from_date=from_date,
                to_date=to_date,
                sort_by=sort_by,
                descending=descending,
            )
        except (OSError, sqlite3.Error) as exc:
            _LOGGER.error("run_history_read_failed error_type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "history_unavailable",
                        "message": "Benchmark history is currently unavailable.",
                    }
                },
            )
        return {
            "items": runs,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get(f"{_API_PREFIX}/analytics/summary")
    async def analytics_summary() -> dict[str, Any]:
        try:
            with _readonly_connection(database) as connection:
                total_samples = connection.execute(
                    "SELECT COUNT(*) FROM benchmark_samples"
                ).fetchone()[0]
                measurements = connection.execute(
                    """
                    SELECT elapsed_ns, bytes_processed, cpu_utilization_percent,
                           peak_process_memory_bytes
                    FROM benchmark_samples
                    ORDER BY id DESC LIMIT 10000
                    """
                ).fetchall()
                job_counts = {
                    row["status"]: row["count"]
                    for row in connection.execute(
                        "SELECT status, COUNT(*) AS count FROM web_benchmark_jobs "
                        "GROUP BY status"
                    )
                }
        except (OSError, sqlite3.Error) as exc:
            _LOGGER.error("analytics_read_failed error_type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "analytics_unavailable",
                        "message": "Benchmark analytics are currently unavailable.",
                    }
                },
            )
        elapsed = [int(row["elapsed_ns"]) for row in measurements if row["elapsed_ns"]]
        latency = summarize_latencies(elapsed) if elapsed else None
        successful_legacy_runs = connectionless_legacy_run_count(database)
        return {
            "total_runs": sum(job_counts.values()) + successful_legacy_runs,
            "successful_runs": job_counts.get("succeeded", 0) + successful_legacy_runs,
            "failed_runs": job_counts.get("failed", 0),
            "active_runs": job_counts.get("queued", 0) + job_counts.get("running", 0),
            "sample_count": total_samples,
            "latency": latency,
            "cpu_observations": sum(
                row["cpu_utilization_percent"] is not None for row in measurements
            ),
            "memory_observations": sum(
                row["peak_process_memory_bytes"] is not None for row in measurements
            ),
            "throughput_observations": sum(
                row["bytes_processed"] is not None and row["bytes_processed"] > 0
                for row in measurements
            ),
        }

    @app.get(f"{_API_PREFIX}/analytics/trends")
    async def analytics_trends(
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> dict[str, Any]:
        try:
            runs, total = _run_page(
                database,
                limit=limit,
                offset=0,
                status_filter="succeeded",
                workload=None,
                engine=None,
                query=None,
                from_date=None,
                to_date=None,
                sort_by="started_at",
                descending=True,
            )
            points: list[dict[str, Any]] = []
            for run in reversed(runs):
                detail = _run_detail(database, run["run_id"])
                if detail is None:
                    continue
                samples = detail["samples"]
                if not samples:
                    continue
                summary = summarize_latencies(
                    sample["elapsed_ns"] for sample in samples
                )
                points.append(
                    {
                        "run_id": run["run_id"],
                        "started_at": run["started_at"],
                        "workload": run["workload"],
                        "engine": run["engine"],
                        "median_ns": summary["median"],
                        "p95_ns": summary["p95_nearest_rank"],
                        "sample_count": run["sample_count"],
                        "median_throughput_bytes_per_second": _median_throughput(samples),
                        "median_cpu_utilization_percent": _median_field(
                            samples, "cpu_utilization_percent"
                        ),
                        "median_peak_memory_bytes": _median_field(
                            samples, "peak_process_memory_bytes"
                        ),
                    }
                )
            return {"items": points, "total_runs": total}
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            _LOGGER.error("analytics_trend_read_failed error_type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "analytics_unavailable",
                        "message": "Benchmark trends are currently unavailable.",
                    }
                },
            )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}", response_model=None)
    async def run_detail(run_id: str) -> dict[str, Any] | JSONResponse:
        try:
            detail = _run_detail(database, run_id)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "invalid_run_id", "message": str(exc)}},
            )
        except (OSError, sqlite3.Error) as exc:
            _LOGGER.error("run_detail_read_failed error_type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "history_unavailable",
                        "message": "Benchmark history is currently unavailable.",
                    }
                },
            )
        if detail is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "run_not_found", "message": "Run was not found."}},
            )
        return detail

    @app.post(f"{_API_PREFIX}/runs", status_code=202)
    async def submit_run(
        benchmark: BenchmarkRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key", max_length=128),
        ] = None,
    ) -> JSONResponse:
        key_hash = None
        if idempotency_key:
            if not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "invalid_idempotency_key",
                            "message": "Idempotency-Key contains unsupported characters.",
                        }
                    },
                )
            key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()

        config = benchmark.safe_config()
        run_id = str(uuid4())
        submitted_at = datetime.now(UTC).isoformat()
        with job_lock:
            try:
                with sqlite3.connect(database, timeout=5) as connection:
                    if key_hash:
                        existing = connection.execute(
                            "SELECT run_id FROM web_benchmark_jobs WHERE idempotency_hash = ?",
                            (key_hash,),
                        ).fetchone()
                        if existing:
                            existing_id = existing[0]
                            return JSONResponse(
                                status_code=202,
                                content={
                                    "run_id": existing_id,
                                    "status": "accepted",
                                    "duplicate": True,
                                },
                            )
                    active = connection.execute(
                        "SELECT COUNT(*) FROM web_benchmark_jobs "
                        "WHERE status IN ('queued', 'running')"
                    ).fetchone()[0]
                    if active >= _MAX_ACTIVE_JOBS:
                        return JSONResponse(
                            status_code=429,
                            content={
                                "error": {
                                    "code": "benchmark_queue_full",
                                    "message": "The local benchmark queue is full; retry later.",
                                }
                            },
                        )
                    connection.execute(
                        """
                        INSERT INTO web_benchmark_jobs (
                            run_id, idempotency_hash, submitted_at, status,
                            category, workload, engine, config_json
                        ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            key_hash,
                            submitted_at,
                            benchmark.category,
                            _WORKLOAD_BY_CATEGORY[benchmark.category],
                            benchmark.engine,
                            json.dumps(config, sort_keys=True),
                        ),
                    )
            except sqlite3.IntegrityError:
                if key_hash:
                    with sqlite3.connect(database) as connection:
                        existing = connection.execute(
                            "SELECT run_id FROM web_benchmark_jobs WHERE idempotency_hash = ?",
                            (key_hash,),
                        ).fetchone()
                    if existing:
                        return JSONResponse(
                            status_code=202,
                            content={
                                "run_id": existing[0],
                                "status": "accepted",
                                "duplicate": True,
                            },
                        )
                raise
            try:
                future = executor.submit(
                    _execute_job,
                    database,
                    native_binary,
                    comparison_binary,
                    run_id,
                    benchmark,
                )
            except RuntimeError:
                _set_job_status(
                    database,
                    run_id,
                    status="failed",
                    finished_at=datetime.now(UTC).isoformat(),
                    error_code="executor_unavailable",
                    error_message="The benchmark worker is unavailable.",
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": {
                            "code": "executor_unavailable",
                            "message": "The benchmark worker is unavailable.",
                        }
                    },
                )
            futures[run_id] = future
        return JSONResponse(
            status_code=202,
            content={"run_id": run_id, "status": "queued", "duplicate": False},
        )

    @app.get(f"{_API_PREFIX}/runs/{{run_id}}/status", response_model=None)
    async def run_status(run_id: str) -> dict[str, Any] | JSONResponse:
        try:
            detail = _run_detail(database, run_id)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "invalid_run_id", "message": str(exc)}},
            )
        except (OSError, sqlite3.Error) as exc:
            _LOGGER.error("run_status_read_failed error_type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "history_unavailable",
                        "message": "Benchmark history is currently unavailable.",
                    }
                },
            )
        if detail is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "run_not_found", "message": "Run was not found."}},
            )
        return {
            key: detail[key]
            for key in (
                "run_id",
                "status",
                "submitted_at",
                "started_at",
                "finished_at",
                "sample_count",
                "error_code",
                "error_message",
            )
        }

    @app.delete(f"{_API_PREFIX}/runs/{{run_id}}")
    async def cancel_run(run_id: str) -> JSONResponse:
        try:
            _safe_run_id(run_id)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "invalid_run_id", "message": str(exc)}},
            )
        with job_lock:
            future = futures.get(run_id)
            if future is None or not future.cancel():
                detail = _run_detail(database, run_id)
                if detail is None:
                    return JSONResponse(
                        status_code=404,
                        content={
                            "error": {
                                "code": "run_not_found",
                                "message": "Run was not found.",
                            }
                        },
                    )
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": {
                            "code": "run_not_cancellable",
                            "message": "Only a queued benchmark can be cancelled.",
                        }
                    },
                )
            _set_job_status(
                database,
                run_id,
                status="cancelled",
                finished_at=datetime.now(UTC).isoformat(),
                error_code="cancelled",
                error_message="Cancelled before execution started.",
            )
        _LOGGER.info("benchmark_run_cancelled run_id=%s", run_id)
        return JSONResponse(content={"run_id": run_id, "status": "cancelled"})

    @app.post(f"{_API_PREFIX}/comparisons", response_model=None)
    async def compare_runs(comparison: ComparisonRequest) -> dict[str, Any] | JSONResponse:
        if comparison.current_run_id == comparison.reference_run_id:
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "invalid_comparison",
                        "message": "Choose two different run IDs.",
                    }
                },
            )
        try:
            current_detail = _run_detail(database, comparison.current_run_id)
            reference_detail = _run_detail(database, comparison.reference_run_id)
        except (OSError, sqlite3.Error, ValueError):
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "invalid_comparison",
                        "message": "One or both run IDs could not be read.",
                    }
                },
            )
        if current_detail is None or reference_detail is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "run_not_found",
                        "message": "Both benchmark runs must exist.",
                    }
                },
            )
        current_config = current_detail["config"]
        reference_config = reference_detail["config"]
        compatibility_fields = (
            "category",
            "dataset",
            "input_size",
            "iterations",
            "workload",
            "working_set_kib",
            "file_size_kib",
        )
        differences = [
            field
            for field in compatibility_fields
            if current_config.get(field) != reference_config.get(field)
        ]
        if current_detail["workload"] != reference_detail["workload"]:
            differences.append("stored workload")
        if differences:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "incompatible_runs",
                        "message": "Runs are not comparable.",
                        "details": sorted(set(differences)),
                    }
                },
            )
        current_values = [
            sample["elapsed_ns"]
            for sample in current_detail["samples"]
            if sample["variant"] == comparison.current_variant
        ]
        reference_values = [
            sample["elapsed_ns"]
            for sample in reference_detail["samples"]
            if sample["variant"] == comparison.reference_variant
        ]
        if (
            len(current_values) < 5
            or len(reference_values) < 5
            or current_detail["samples_truncated"]
            or reference_detail["samples_truncated"]
        ):
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "insufficient_samples",
                        "message": "Comparison requires at least five complete samples per run.",
                    }
                },
            )
        try:
            from benchforge.stats import (
                bootstrap_median_ratio_ci,
                classify_regression,
                mann_whitney_pvalue,
                median_ratio,
            )

            ratio = median_ratio(reference_values, current_values)
            ci_low, ci_high = bootstrap_median_ratio_ci(
                reference_values,
                current_values,
                confidence=comparison.confidence,
                n_resamples=comparison.n_resamples,
                seed=comparison.seed,
            )
            p_value = mann_whitney_pvalue(reference_values, current_values)
        except (TypeError, ValueError):
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "invalid_samples",
                        "message": "Stored observations could not be compared.",
                    }
                },
            )
        return {
            "current_run_id": comparison.current_run_id,
            "reference_run_id": comparison.reference_run_id,
            "current_variant": comparison.current_variant,
            "reference_variant": comparison.reference_variant,
            "current_count": len(current_values),
            "reference_count": len(reference_values),
            "current_median_ns": statistics.median(current_values),
            "reference_median_ns": statistics.median(reference_values),
            "ratio": ratio,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "p_value": p_value,
            "threshold": comparison.threshold,
            "decision": classify_regression(
                ci_low,
                ci_high,
                threshold=comparison.threshold,
                p_value=p_value,
            ),
            "compatibility": "compatible",
        }

    @app.get(f"{_API_PREFIX}/exports/{{run_id}}")
    async def export_run(
        run_id: str,
        file_format: Literal["csv", "json", "pdf"] = Query(alias="format"),
    ) -> Response:
        try:
            detail = _run_detail(database, run_id)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "invalid_run_id", "message": str(exc)}},
            )
        except (OSError, sqlite3.Error) as exc:
            _LOGGER.error("run_export_read_failed error_type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "history_unavailable",
                        "message": "Benchmark history is currently unavailable.",
                    }
                },
            )
        if detail is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "run_not_found", "message": "Run was not found."}},
            )
        if detail["samples_truncated"]:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "export_limit_exceeded",
                        "message": "This run exceeds the bounded export limit.",
                    }
                },
            )
        filename = f"benchforge-{run_id}.{file_format}"
        if file_format == "json":
            payload = json.dumps(
                {
                    key: detail[key]
                    for key in (
                        "run_id",
                        "status",
                        "submitted_at",
                        "started_at",
                        "finished_at",
                        "category",
                        "workload",
                        "engine",
                        "config",
                        "sample_count",
                        "samples",
                    )
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            media_type = "application/json"
        elif file_format == "csv":
            output = io.StringIO(newline="")
            fields = (
                "timestamp_utc",
                "workload",
                "sample_index",
                "elapsed_ns",
                "variant",
                "pair_index",
                "bytes_processed",
                "peak_process_memory_bytes",
                "peak_process_memory_source",
                "current_working_set_bytes",
                "private_process_bytes",
                "thread_count",
                "process_cpu_time_ns",
                "cpu_utilization_percent",
            )
            writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for sample in detail["samples"]:
                writer.writerow(
                    {
                        key: _spreadsheet_safe(sample.get(key))
                        for key in fields
                    }
                )
            payload = output.getvalue().encode("utf-8-sig")
            media_type = "text/csv; charset=utf-8"
        else:
            payload = _build_pdf(detail)
            media_type = "application/pdf"
        if len(payload) > _MAX_EXPORT_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "export_limit_exceeded",
                        "message": "Export exceeds the 10 MB limit.",
                    }
                },
            )
        return Response(
            content=payload,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    if frontend_root and (frontend_root / "index.html").is_file():
        assets = frontend_root / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        async def frontend_index() -> FileResponse:
            return FileResponse(frontend_root / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
        async def frontend_fallback(full_path: str) -> FileResponse | JSONResponse:
            if full_path.startswith("api/") or full_path in {
                "healthz",
                "readyz",
            }:
                return JSONResponse(
                    status_code=404,
                    content={"error": {"code": "not_found", "message": "Route was not found."}},
                )
            return FileResponse(frontend_root / "index.html")

    return app


def _execute_job(
    database: Path,
    native_binary: Path | None,
    comparison_binary: Path | None,
    run_id: str,
    request: BenchmarkRequest,
) -> None:
    started_at = datetime.now(UTC).isoformat()
    _set_job_status(database, run_id, status="running", started_at=started_at)
    _LOGGER.info(
        "benchmark_run_started run_id=%s workload=%s engine=%s",
        run_id,
        _WORKLOAD_BY_CATEGORY[request.category],
        request.engine,
        extra={"run_id": run_id},
    )
    try:
        sample_count = _store_benchmark(
            database,
            native_binary,
            comparison_binary,
            run_id,
            request,
        )
    except TimeoutError:
        _set_job_status(
            database,
            run_id,
            status="failed",
            finished_at=datetime.now(UTC).isoformat(),
            error_code="benchmark_timeout",
            error_message="The benchmark exceeded its configured timeout.",
        )
        _LOGGER.warning("benchmark_run_failed run_id=%s error_type=TimeoutError", run_id)
        return
    except FileNotFoundError:
        _set_job_status(
            database,
            run_id,
            status="failed",
            finished_at=datetime.now(UTC).isoformat(),
            error_code="engine_unavailable",
            error_message="The selected benchmark engine is unavailable.",
        )
        _LOGGER.warning("benchmark_run_failed run_id=%s error_type=FileNotFoundError", run_id)
        return
    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
        _set_job_status(
            database,
            run_id,
            status="failed",
            finished_at=datetime.now(UTC).isoformat(),
            error_code="benchmark_failed",
            error_message="The benchmark failed; check the local server log.",
        )
        _LOGGER.error(
            "benchmark_run_failed run_id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        return
    except Exception as exc:
        _set_job_status(
            database,
            run_id,
            status="failed",
            finished_at=datetime.now(UTC).isoformat(),
            error_code="internal_error",
            error_message="The benchmark failed; check the local server log.",
        )
        _LOGGER.exception(
            "benchmark_run_failed run_id=%s error_type=%s",
            run_id,
            type(exc).__name__,
        )
        return
    _set_job_status(
        database,
        run_id,
        status="succeeded",
        finished_at=datetime.now(UTC).isoformat(),
    )
    _LOGGER.info(
        "benchmark_run_completed run_id=%s sample_count=%d",
        run_id,
        sample_count,
        extra={"run_id": run_id},
    )


def _median_field(
    samples: list[dict[str, Any]],
    field: str,
) -> float | None:
    values = [
        float(sample[field])
        for sample in samples
        if isinstance(sample.get(field), (int, float))
        and not isinstance(sample[field], bool)
        and math.isfinite(sample[field])
    ]
    return float(statistics.median(values)) if values else None


def _median_throughput(samples: list[dict[str, Any]]) -> float | None:
    values = [
        sample["bytes_processed"] * 1_000_000_000 / sample["elapsed_ns"]
        for sample in samples
        if isinstance(sample.get("bytes_processed"), int)
        and sample["bytes_processed"] > 0
        and isinstance(sample.get("elapsed_ns"), int)
        and sample["elapsed_ns"] > 0
    ]
    return float(statistics.median(values)) if values else None


def _spreadsheet_safe(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def connectionless_legacy_run_count(database: Path) -> int:
    with _readonly_connection(database) as connection:
        return connection.execute(
            """
            SELECT COUNT(DISTINCT sample.run_id)
            FROM benchmark_samples AS sample
            LEFT JOIN web_benchmark_jobs AS job ON job.run_id = sample.run_id
            WHERE job.run_id IS NULL
            """
        ).fetchone()[0]


__all__ = ["BenchmarkRequest", "ComparisonRequest", "create_app"]
