import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import {
  Link,
  NavLink,
  Outlet,
  Route,
  Routes,
  useSearchParams,
} from "react-router-dom";
import {
  ApiError,
  downloadUrl,
  request,
  type Page,
  type RunDetail,
  type RunStatus,
  type RunSummary,
} from "./api";

interface AnalyticsSummary {
  total_runs: number;
  successful_runs: number;
  failed_runs: number;
  active_runs: number;
  sample_count: number;
  latency: {
    median: number;
    p95_nearest_rank: number;
    p99_nearest_rank: number | null;
    p99_reliable: boolean;
  } | null;
  cpu_observations: number;
  memory_observations: number;
  throughput_observations: number;
}

interface TrendPoint {
  run_id: string;
  started_at: string;
  workload: string;
  engine: string;
  median_ns: number;
  p95_ns: number;
  sample_count: number;
  median_throughput_bytes_per_second: number | null;
  median_cpu_utilization_percent: number | null;
  median_peak_memory_bytes: number | null;
}

interface Integration {
  name: string;
  status: string;
  version: string;
  service_status: string;
}

const navigation = [
  ["Overview", "/", "overview"],
  ["Run benchmark", "/run", "run"],
  ["Compare engines", "/compare", "compare"],
  ["Analytics", "/analytics", "analytics"],
  ["CPU and memory", "/resources", "resources"],
  ["Storage and database", "/storage", "storage"],
  ["Benchmark history", "/history", "history"],
  ["Data platform", "/platform", "platform"],
  ["Distributed integrations", "/integrations", "integrations"],
  ["Exports", "/exports", "exports"],
  ["Settings", "/settings", "settings"],
  ["System health", "/health", "health"],
] as const;

function useApi<T>(path: string | null, refresh = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(Boolean(path));

  useEffect(() => {
    let active = true;
    if (!path) {
      setLoading(false);
      setData(null);
      setError(null);
      return () => {
        active = false;
      };
    }
    setLoading(true);
    setError(null);
    request<T>(path)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((cause: unknown) => {
        if (active) {
          setError(cause instanceof Error ? cause : new Error("The API request failed."));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, refresh]);

  return { data, error, loading };
}

function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Not measured";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value);
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

function formatDuration(ns: number | null | undefined): string {
  if (ns === null || ns === undefined || !Number.isFinite(ns)) return "Not measured";
  if (ns < 1_000) return `${formatNumber(ns)} ns`;
  if (ns < 1_000_000) return `${formatNumber(ns / 1_000, 2)} us`;
  if (ns < 1_000_000_000) return `${formatNumber(ns / 1_000_000, 2)} ms`;
  return `${formatNumber(ns / 1_000_000_000, 2)} s`;
}

function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  const offline = error instanceof ApiError && error.kind === "offline";
  const timeout = error instanceof ApiError && error.kind === "timeout";
  return (
    <section className="notice notice-error" role="alert">
      <strong>{offline ? "Backend is offline" : timeout ? "Request timed out" : "Request failed"}</strong>
      <p>{error.message}</p>
      {offline && <p>Start the FastAPI server or verify that the local API port is reachable.</p>}
      {retry && (
        <button type="button" className="button button-secondary" onClick={retry}>
          Retry
        </button>
      )}
    </section>
  );
}

function LoadingState({ label = "Loading saved benchmark data" }: { label?: string }) {
  return (
    <div className="loading-state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      {label}…
    </div>
  );
}

function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

function MetricCard({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <article className="metric-card">
      <p className="eyebrow">{label}</p>
      <strong>{value}</strong>
      {note && <span>{note}</span>}
    </article>
  );
}

function StatusBadge({ status }: { status: string }) {
  const known = ["queued", "running", "succeeded", "failed", "cancelled"].includes(status);
  return (
    <span className={`status-badge ${known ? `status-${status}` : "status-neutral"}`}>
      {known ? status : "unknown"}
    </span>
  );
}

function TrendChart({
  points,
  value,
  label,
  unit,
}: {
  points: TrendPoint[];
  value: (point: TrendPoint) => number | null;
  label: string;
  unit: string;
}) {
  const plotted = points
    .map((point) => ({ point, amount: value(point) }))
    .filter((entry): entry is { point: TrendPoint; amount: number } =>
      typeof entry.amount === "number" && Number.isFinite(entry.amount),
    );
  if (!plotted.length) {
    return <EmptyState>No observations for {label.toLowerCase()} are stored yet.</EmptyState>;
  }
  const width = 720;
  const height = 260;
  const left = 64;
  const right = 18;
  const top = 24;
  const bottom = 46;
  const values = plotted.map((entry) => entry.amount);
  const minimum = Math.min(0, ...values);
  const maximum = Math.max(...values) || 1;
  const x = (index: number) =>
    left + (index * (width - left - right)) / Math.max(1, plotted.length - 1);
  const y = (amount: number) =>
    top + ((maximum - amount) * (height - top - bottom)) / (maximum - minimum || 1);
  const polyline = plotted.map((entry, index) => `${x(index)},${y(entry.amount)}`).join(" ");
  return (
    <div className="chart-frame">
      <svg
        className="trend-chart"
        role="img"
        aria-label={`${label} over recorded benchmark runs`}
        viewBox={`0 0 ${width} ${height}`}
      >
        <line className="chart-axis" x1={left} y1={height - bottom} x2={width - right} y2={height - bottom} />
        <line className="chart-axis" x1={left} y1={top} x2={left} y2={height - bottom} />
        <line
          className="chart-grid"
          x1={left}
          y1={y((minimum + maximum) / 2)}
          x2={width - right}
          y2={y((minimum + maximum) / 2)}
        />
        <polyline className="chart-line" points={polyline} />
        {plotted.map((entry, index) => (
          <circle
            className="chart-point"
            key={entry.point.run_id}
            cx={x(index)}
            cy={y(entry.amount)}
            r="5"
          >
            <title>
              {entry.point.workload} · {formatDate(entry.point.started_at)} ·{" "}
              {formatNumber(entry.amount, 2)} {unit}
            </title>
          </circle>
        ))}
        <text className="chart-label" x={left} y={height - 12}>
          {plotted[0].point.workload}
        </text>
        <text className="chart-label" x={width - right} y={height - 12} textAnchor="end">
          {plotted[plotted.length - 1].point.workload}
        </text>
        <text className="chart-value" x={left - 8} y={top + 4} textAnchor="end">
          {formatNumber(maximum, 2)}
        </text>
        <text className="chart-value" x={left - 8} y={height - bottom} textAnchor="end">
          {formatNumber(minimum, 2)}
        </text>
      </svg>
      <p className="chart-caption">{label} ({unit}); each point is a stored run summary.</p>
    </div>
  );
}

function RunTable({
  runs,
  selected = [],
  onSelect,
}: {
  runs: RunSummary[];
  selected?: string[];
  onSelect?: (runId: string, checked: boolean) => void;
}) {
  if (!runs.length) return <EmptyState>No benchmark history matches these filters.</EmptyState>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {onSelect && <th scope="col">Compare</th>}
            <th scope="col">Run</th>
            <th scope="col">Workload</th>
            <th scope="col">Engine</th>
            <th scope="col">Status</th>
            <th scope="col">Samples</th>
            <th scope="col">Started</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.run_id}>
              {onSelect && (
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Select run ${run.run_id} for comparison`}
                    checked={selected.includes(run.run_id)}
                    disabled={!selected.includes(run.run_id) && selected.length >= 2}
                    onChange={(event) => onSelect(run.run_id, event.currentTarget.checked)}
                  />
                </td>
              )}
              <td>
                <Link className="run-link" to={`/history?run_id=${encodeURIComponent(run.run_id)}`}>
                  {run.run_id.slice(0, 12)}
                </Link>
              </td>
              <td>{run.workload}</td>
              <td>{run.engine}</td>
              <td><StatusBadge status={run.status} /></td>
              <td>{formatNumber(run.sample_count)}</td>
              <td>{formatDate(run.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OverviewPage() {
  const summary = useApi<AnalyticsSummary>("/analytics/summary");
  const trend = useApi<{ items: TrendPoint[] }>("/analytics/trends?limit=30");
  const recent = useApi<Page<RunSummary>>("/runs?limit=8&sort_by=started_at");
  if (summary.error) return <ErrorState error={summary.error} />;
  if (summary.loading || !summary.data) return <LoadingState />;
  const metrics = summary.data;
  return (
    <div className="page-stack">
      <section className="metric-grid" aria-label="Recorded benchmark totals">
        <MetricCard label="Total runs" value={formatNumber(metrics.total_runs)} note="Persisted run records" />
        <MetricCard label="Successful" value={formatNumber(metrics.successful_runs)} note="Stored with results" />
        <MetricCard label="Failed" value={formatNumber(metrics.failed_runs)} note="Failed execution records" />
        <MetricCard label="Active" value={formatNumber(metrics.active_runs)} note="Queued or running now" />
      </section>
      <section className="panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Measured history</p>
            <h2>Runtime trend</h2>
          </div>
          <Link className="text-link" to="/analytics">Open analytics</Link>
        </div>
        {trend.error ? <ErrorState error={trend.error} /> : trend.loading || !trend.data
          ? <LoadingState label="Loading recorded trends" />
          : <TrendChart points={trend.data.items} value={(point) => point.median_ns} label="Median runtime" unit="ns" />}
      </section>
      <section className="metric-grid metric-grid-three">
        <MetricCard label="Median latency" value={metrics.latency ? formatDuration(metrics.latency.median) : "No samples"} />
        <MetricCard label="CPU observations" value={formatNumber(metrics.cpu_observations)} note="Stored process measurements only" />
        <MetricCard label="Memory observations" value={formatNumber(metrics.memory_observations)} note="Stored process measurements only" />
      </section>
      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">Latest records</p><h2>Recent executions</h2></div>
          <Link className="text-link" to="/history">View full history</Link>
        </div>
        {recent.error ? <ErrorState error={recent.error} /> : recent.loading || !recent.data
          ? <LoadingState label="Loading recent executions" />
          : <RunTable runs={recent.data.items} />}
      </section>
    </div>
  );
}

interface RunOptions {
  categories: string[];
  engines: Record<string, boolean>;
  limits: Record<string, number>;
  optional_integrations: Record<string, { status: string; version: string }>;
}

interface RunJobStatus {
  run_id: string;
  status: RunStatus;
  sample_count: number;
  error_code: string | null;
  error_message: string | null;
}

function RunPage() {
  const options = useApi<RunOptions>("/options");
  const [category, setCategory] = useState("cpu");
  const [engine, setEngine] = useState("both");
  const [inputSize, setInputSize] = useState(10_000);
  const [runs, setRuns] = useState(5);
  const [warmup, setWarmup] = useState(1);
  const [timeout, setTimeout] = useState(10);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<RunJobStatus | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = job?.status === "queued" || job?.status === "running";
  const maxInput = category === "memory" ? 4_096 : category === "storage" ? 8_192 : 1_000_000;
  const engineAvailable = options.data?.engines[engine] ?? false;

  useEffect(() => {
    if (options.data && engine === "both" && !options.data.engines.both) {
      setEngine("python");
    }
  }, [engine, options.data]);

  useEffect(() => {
    if (!jobId || !busy) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const result = await request<RunJobStatus>(`/runs/${encodeURIComponent(jobId)}/status`);
        if (!active) return;
        setJob(result);
        if (result.status === "succeeded" || result.status === "failed") {
          setBusy(false);
          const run = await request<RunDetail>(`/runs/${encodeURIComponent(jobId)}`);
          if (active) setDetail(run);
          return;
        }
      } catch (cause) {
        if (active) {
          setError(cause instanceof Error ? cause.message : "Could not refresh run status.");
          setBusy(false);
        }
        return;
      }
      if (active) timer = window.setTimeout(() => void poll(), 1_000);
    };
    void poll();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId, busy]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !engineAvailable) return;
    setError(null);
    setDetail(null);
    setJob(null);
    try {
      const result = await request<{ run_id: string; status: string }>("/runs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({
          category,
          engine,
          input_size: inputSize,
          runs,
          warmup,
          workers: 1,
          timeout_seconds: timeout,
          dataset: "synthetic",
        }),
      });
      setJobId(result.run_id);
      setJob({
        run_id: result.run_id,
        status: "queued",
        sample_count: 0,
        error_code: null,
        error_message: null,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not submit the benchmark.");
    }
  }

  async function cancelQueued() {
    if (!jobId || job?.status !== "queued") return;
    try {
      const result = await request<RunJobStatus>(`/runs/${encodeURIComponent(jobId)}`, {
        method: "DELETE",
      });
      setJob(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Queued run could not be cancelled.");
    }
  }

  return (
    <div className="page-stack">
      <section className="panel form-panel">
        <p className="eyebrow">Bounded local execution</p>
        <h2>Configure a benchmark</h2>
        <p className="muted">
          BenchForge validates these values again on the server. Runs are queued one at a time.
          Only the synthetic input currently exists for this web workflow.
        </p>
        {options.error && <ErrorState error={options.error} />}
        {error && <div className="notice notice-error" role="alert">{error}</div>}
        {options.data && !engineAvailable && (
          <p className="notice notice-info" role="status">
            The selected engine is not available. Install the native benchmark executable for
            memory, storage, and C++ runs, or choose Python for a CPU run.
          </p>
        )}
        <form className="benchmark-form" onSubmit={submit}>
          <label>
            Category
            <select value={category} disabled={Boolean(busy)} onChange={(event) => {
              const next = event.target.value;
              setCategory(next);
              if (next !== "cpu") setEngine("native");
            }}>
              <option value="cpu">CPU</option>
              <option value="memory">Memory</option>
              <option value="storage">Storage / file I/O</option>
            </select>
          </label>
          <label>
            Engine
            <select value={engine} disabled={Boolean(busy)} onChange={(event) => setEngine(event.target.value)}>
              <option value="python">Python</option>
              <option value="native" disabled={!options.data?.engines.native}>C++ native</option>
              <option value="both" disabled={category !== "cpu" || !options.data?.engines.both}>
                Equivalent Python + C++
              </option>
            </select>
          </label>
          <label>
            {category === "cpu" ? "Input size (operations)" : category === "memory" ? "Working set (KiB)" : "File size (KiB)"}
            <input
              type="number"
              min={1}
              max={maxInput}
              required
              value={inputSize}
              disabled={Boolean(busy)}
              onChange={(event) => setInputSize(Number(event.target.value))}
            />
          </label>
          <label>
            Measured repetitions
            <input
              type="number"
              min={2}
              max={10}
              required
              value={runs}
              disabled={Boolean(busy)}
              onChange={(event) => setRuns(Number(event.target.value))}
            />
          </label>
          <label>
            Warm-up repetitions
            <input
              type="number"
              min={0}
              max={2}
              required
              value={warmup}
              disabled={Boolean(busy)}
              onChange={(event) => setWarmup(Number(event.target.value))}
            />
          </label>
          <label>
            Per-process timeout (seconds)
            <input
              type="number"
              min={1}
              max={options.data?.limits.max_timeout_seconds ?? 30}
              required
              value={timeout}
              disabled={Boolean(busy)}
              onChange={(event) => setTimeout(Number(event.target.value))}
            />
          </label>
          <label>
            Worker count
            <input type="number" value={1} readOnly aria-describedby="worker-limit" />
            <span id="worker-limit" className="field-note">The server allows one benchmark worker.</span>
          </label>
          <label>
            Input dataset
            <input value="Deterministic synthetic workload" readOnly />
          </label>
          <div className="form-actions">
            <button type="submit" className="button button-primary" disabled={Boolean(busy) || options.loading || !engineAvailable}>
              {busy ? "Benchmark queued or running…" : "Submit benchmark"}
            </button>
            {job?.status === "queued" && (
              <button type="button" className="button button-secondary" onClick={cancelQueued}>
                Cancel queued run
              </button>
            )}
          </div>
        </form>
        {job && (
          <section className="run-result" aria-live="polite">
            <div className="section-heading">
              <div><p className="eyebrow">Execution status</p><h3>{job.run_id}</h3></div>
              <StatusBadge status={job.status} />
            </div>
            {job.status === "running" && (
              <p className="notice notice-info">
                The engine does not expose a measurable progress counter. The UI will not invent a
                percentage.
              </p>
            )}
            {job.status === "failed" && (
              <div className="notice notice-error" role="alert">
                {job.error_message ?? "Benchmark failed."} ({job.error_code ?? "unknown error"})
              </div>
            )}
            {detail && detail.status === "succeeded" && (
              <>
                <p>{formatNumber(detail.sample_count)} saved measurements · {detail.workload} · {detail.engine}</p>
                <Link className="text-link" to={`/history?run_id=${encodeURIComponent(detail.run_id)}`}>
                  Open run history
                </Link>
              </>
            )}
          </section>
        )}
      </section>
    </div>
  );
}

function HistoryPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [offset, setOffset] = useState(0);
  const [sortBy, setSortBy] = useState("started_at");
  const [descending, setDescending] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [params] = useSearchParams();
  const initialRun = params.get("run_id");
  const query = new URLSearchParams({
    limit: "20",
    offset: String(offset),
    status,
    sort_by: sortBy,
    descending: String(descending),
  });
  if (search) query.set("search", search);
  if (fromDate) query.set("from_date", fromDate);
  if (toDate) query.set("to_date", toDate);
  const { data, loading, error } = useApi<Page<RunSummary>>(`/runs?${query.toString()}`);
  const { data: selectedRun } = useApi<RunDetail>(
    initialRun ? `/runs/${encodeURIComponent(initialRun)}` : null,
  );
  const pageCount = data ? Math.max(1, Math.ceil(data.total / data.limit)) : 1;
  function choose(runId: string, checked: boolean) {
    setSelected((current) => checked
      ? [...current.filter((item) => item !== runId), runId].slice(-2)
      : current.filter((item) => item !== runId));
  }
  return (
    <div className="page-stack">
      {selectedRun && (
        <section className="panel">
          <div className="section-heading">
            <div><p className="eyebrow">Selected execution</p><h2>{selectedRun.run_id}</h2></div>
            <StatusBadge status={selectedRun.status} />
          </div>
          <p>{selectedRun.workload} · {selectedRun.engine} · {formatNumber(selectedRun.sample_count)} samples</p>
          {selectedRun.samples_truncated
            ? <p className="notice notice-info">Sample details exceed the interactive response limit.</p>
            : <TrendChart
                points={selectedRun.samples.map((sample) => ({
                  run_id: `${selectedRun.run_id}-${sample.variant}-${sample.sample_index}`,
                  started_at: sample.timestamp_utc,
                  workload: `${sample.variant} sample ${sample.sample_index + 1}`,
                  engine: sample.variant,
                  median_ns: sample.elapsed_ns,
                  p95_ns: sample.elapsed_ns,
                  sample_count: 1,
                  median_throughput_bytes_per_second: null,
                  median_cpu_utilization_percent: sample.cpu_utilization_percent,
                  median_peak_memory_bytes: sample.peak_process_memory_bytes,
                }))}
                value={(point) => point.median_ns}
                label="Observed sample runtime"
                unit="ns"
              />}
          <div className="export-links">
            {(["csv", "json", "pdf"] as const).map((format) => (
              <a className="button button-secondary" key={format} href={downloadUrl(selectedRun.run_id, format)}>
                Download {format.toUpperCase()}
              </a>
            ))}
          </div>
        </section>
      )}
      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">Persistent SQLite history</p><h2>Benchmark runs</h2></div>
          <Link className="button button-secondary" to="/run">Run benchmark</Link>
        </div>
        <div className="filter-bar">
          <label>
            Search run or workload
            <input value={search} onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
            }} maxLength={100} type="search" />
          </label>
          <label>
            Status
            <select value={status} onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}>
              {["all", "queued", "running", "succeeded", "failed", "cancelled"].map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            From date
            <input type="date" value={fromDate} onChange={(event) => {
              setFromDate(event.target.value);
              setOffset(0);
            }} />
          </label>
          <label>
            Through date
            <input type="date" value={toDate} onChange={(event) => {
              setToDate(event.target.value);
              setOffset(0);
            }} />
          </label>
          <label>
            Sort by
            <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
              <option value="started_at">Start time</option>
              <option value="sample_count">Sample count</option>
              <option value="workload">Workload</option>
            </select>
          </label>
          <label className="checkbox-label">
            <input type="checkbox" checked={descending} onChange={(event) => setDescending(event.target.checked)} />
            Descending
          </label>
        </div>
        {selected.length === 2 && (
          <p className="selection-bar">
            Two runs selected.{" "}
            <Link to={`/compare?current=${encodeURIComponent(selected[0])}&reference=${encodeURIComponent(selected[1])}`}>
              Compare compatible runs
            </Link>
          </p>
        )}
        {error ? <ErrorState error={error} /> : loading || !data
          ? <LoadingState label="Loading benchmark history" />
          : <RunTable runs={data.items} selected={selected} onSelect={choose} />}
        <div className="pagination">
          <span>Page {data ? Math.floor(data.offset / data.limit) + 1 : 1} of {pageCount} · {formatNumber(data?.total)} runs</span>
          <div>
            <button type="button" className="button button-secondary" disabled={!data || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>
              Previous
            </button>
            <button type="button" className="button button-secondary" disabled={!data || offset + data.limit >= data.total} onClick={() => setOffset(offset + 20)}>
              Next
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

interface ComparisonResult {
  current_run_id: string;
  reference_run_id: string;
  current_count: number;
  reference_count: number;
  current_median_ns: number;
  reference_median_ns: number;
  ratio: number;
  ci_low: number;
  ci_high: number;
  p_value: number;
  decision: string;
  compatibility: string;
}

function ComparePage() {
  const [params] = useSearchParams();
  const [current, setCurrent] = useState(params.get("current") ?? "");
  const [reference, setReference] = useState(params.get("reference") ?? "");
  const [currentVariant, setCurrentVariant] = useState("python");
  const [referenceVariant, setReferenceVariant] = useState("python");
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const { data } = useApi<Page<RunSummary>>("/runs?limit=100&status=succeeded");

  async function compare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await request<ComparisonResult>("/comparisons", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_run_id: current,
          reference_run_id: reference,
          current_variant: currentVariant,
          reference_variant: referenceVariant,
        }),
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error("Comparison failed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <p className="eyebrow">Compatible observations only</p>
        <h2>Compare historical runs</h2>
        <p className="muted">
          Workload, input size, and dataset must match. At least five complete samples per side are
          required. The ratio is current divided by reference; values over 1 are slower.
        </p>
        <form className="benchmark-form" onSubmit={compare}>
          <label>
            Current run
            <select required value={current} onChange={(event) => setCurrent(event.target.value)}>
              <option value="">Choose a run</option>
              {(data?.items ?? []).map((run) => <option key={run.run_id} value={run.run_id}>{run.run_id} · {run.workload}</option>)}
            </select>
          </label>
          <label>
            Reference run
            <select required value={reference} onChange={(event) => setReference(event.target.value)}>
              <option value="">Choose a run</option>
              {(data?.items ?? []).map((run) => <option key={run.run_id} value={run.run_id}>{run.run_id} · {run.workload}</option>)}
            </select>
          </label>
          <label>
            Current engine variant
            <select value={currentVariant} onChange={(event) => setCurrentVariant(event.target.value)}>
              <option value="python">Python</option><option value="native">C++ native</option><option value="single">Single engine</option>
            </select>
          </label>
          <label>
            Reference engine variant
            <select value={referenceVariant} onChange={(event) => setReferenceVariant(event.target.value)}>
              <option value="python">Python</option><option value="native">C++ native</option><option value="single">Single engine</option>
            </select>
          </label>
          <div className="form-actions">
            <button className="button button-primary" type="submit" disabled={busy || !current || !reference || current === reference}>
              {busy ? "Comparing…" : "Compare runs"}
            </button>
          </div>
        </form>
        {error && <ErrorState error={error} />}
        {result && (
          <section className="comparison-result" aria-live="polite">
            <span className="decision-badge">{result.decision}</span>
            <div className="metric-grid metric-grid-three">
              <MetricCard label="Current median" value={formatDuration(result.current_median_ns)} />
              <MetricCard label="Reference median" value={formatDuration(result.reference_median_ns)} />
              <MetricCard label="Current / reference" value={`${formatNumber(result.ratio, 4)}x`} />
            </div>
            <p>Bootstrap interval: [{formatNumber(result.ci_low, 4)}, {formatNumber(result.ci_high, 4)}] · Mann–Whitney p={formatNumber(result.p_value, 5)}</p>
            <p>Observations: {result.current_count} current / {result.reference_count} reference. Decision: {result.decision}.</p>
          </section>
        )}
      </section>
    </div>
  );
}

function AnalyticsPage() {
  const summary = useApi<AnalyticsSummary>("/analytics/summary");
  const trends = useApi<{ items: TrendPoint[] }>("/analytics/trends?limit=100");
  if (summary.error) return <ErrorState error={summary.error} />;
  if (summary.loading || !summary.data) return <LoadingState />;
  const points = trends.data?.items ?? [];
  return (
    <div className="page-stack">
      <section className="metric-grid metric-grid-three">
        <MetricCard label="Samples" value={formatNumber(summary.data.sample_count)} />
        <MetricCard label="Median latency" value={summary.data.latency ? formatDuration(summary.data.latency.median) : "No samples"} />
        <MetricCard label="P95 latency" value={summary.data.latency ? formatDuration(summary.data.latency.p95_nearest_rank) : "No samples"} />
        {summary.data.latency && !summary.data.latency.p99_reliable && (
          <p className="notice notice-info">P99 is withheld until at least 100 observations are available.</p>
        )}
      </section>
      {trends.error ? <ErrorState error={trends.error} /> : trends.loading
        ? <LoadingState label="Loading analytics trends" />
        : <section className="panel"><h2>Runtime and throughput</h2>
            <TrendChart points={points} value={(point) => point.median_ns} label="Median runtime" unit="ns" />
            <TrendChart points={points} value={(point) => point.median_throughput_bytes_per_second} label="Median throughput" unit="bytes/s" />
          </section>}
    </div>
  );
}

function ResourcePage() {
  const trends = useApi<{ items: TrendPoint[] }>("/analytics/trends?limit=100");
  if (trends.error) return <ErrorState error={trends.error} />;
  if (trends.loading || !trends.data) return <LoadingState />;
  return (
    <div className="page-stack">
      <section className="panel">
        <h2>CPU utilization observations</h2>
        <TrendChart points={trends.data.items} value={(point) => point.median_cpu_utilization_percent} label="Measured process CPU utilization" unit="%" />
        <h2>Peak process memory observations</h2>
        <TrendChart points={trends.data.items} value={(point) => point.median_peak_memory_bytes} label="Recorded peak process memory" unit="bytes" />
        <p className="muted">Unavailable metrics stay empty; missing values are never replaced with estimates.</p>
      </section>
    </div>
  );
}

function StoragePage() {
  const platform = useApi<{
    database: string;
    benchmark_sample_count: number;
    registered_dataset_count: number;
    optional_integrations: Record<string, { status: string; version: string }>;
  }>("/platform");
  const trends = useApi<{ items: TrendPoint[] }>("/analytics/trends?limit=100");
  if (platform.error) return <ErrorState error={platform.error} />;
  if (platform.loading || !platform.data) return <LoadingState />;
  const storagePoints = trends.data?.items.filter((point) => point.workload === "file_io") ?? [];
  return (
    <div className="page-stack">
      <section className="metric-grid metric-grid-three">
        <MetricCard label="SQLite history" value={platform.data.database} />
        <MetricCard label="Persisted samples" value={formatNumber(platform.data.benchmark_sample_count)} />
        <MetricCard label="Registered datasets" value={formatNumber(platform.data.registered_dataset_count)} />
      </section>
      <section className="panel">
        <h2>Storage workload history</h2>
        {trends.error ? <ErrorState error={trends.error} /> : trends.loading
          ? <LoadingState />
          : <TrendChart points={storagePoints} value={(point) => point.median_throughput_bytes_per_second} label="Measured file workload throughput" unit="bytes/s" />}
      </section>
    </div>
  );
}

function DataPlatformPage() {
  const platform = useApi<{
    database: string;
    benchmark_sample_count: number;
    registered_dataset_count: number;
    optional_integrations: Record<string, { status: string; version: string }>;
  }>("/platform");
  if (platform.error) return <ErrorState error={platform.error} />;
  if (platform.loading || !platform.data) return <LoadingState />;
  return (
    <section className="panel">
      <p className="eyebrow">Local data services</p>
      <h2>Data platform</h2>
      <dl className="status-list">
        <div><dt>SQLite history</dt><dd>{platform.data.database}</dd></div>
        <div><dt>Stored samples</dt><dd>{formatNumber(platform.data.benchmark_sample_count)}</dd></div>
        <div><dt>Registered datasets</dt><dd>{formatNumber(platform.data.registered_dataset_count)}</dd></div>
      </dl>
      <p className="muted">Optional analytical packages are reported separately from the required SQLite history store.</p>
      <IntegrationTable integrations={platform.data.optional_integrations} />
    </section>
  );
}

function IntegrationTable({ integrations }: { integrations: Record<string, { status: string; version: string }> }) {
  return (
    <div className="table-scroll">
      <table>
        <thead><tr><th scope="col">Optional component</th><th scope="col">Package state</th><th scope="col">Version</th></tr></thead>
        <tbody>{Object.entries(integrations).map(([name, info]) => (
          <tr key={name}><th scope="row">{name}</th><td>{info.status}</td><td>{info.version || "Not installed"}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function IntegrationsPage() {
  const { data, loading, error } = useApi<{ integrations: Integration[] }>("/integrations");
  if (error) return <ErrorState error={error} />;
  if (loading || !data) return <LoadingState />;
  return (
    <section className="panel">
      <p className="eyebrow">No remote probes</p>
      <h2>Distributed integrations</h2>
      <p className="notice notice-info">
        Service health is not probed. Installed client packages do not establish that a database,
        broker, or cluster is running or healthy.
      </p>
      <div className="table-scroll">
        <table>
          <thead><tr><th scope="col">Integration</th><th scope="col">Client package</th><th scope="col">Version</th><th scope="col">Service status</th></tr></thead>
          <tbody>{data.integrations.map((item) => (
            <tr key={item.name}><th scope="row">{item.name}</th><td>{item.status}</td><td>{item.version || "Not installed"}</td><td>{item.service_status}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}

function ExportsPage() {
  const { data, loading, error } = useApi<Page<RunSummary>>("/runs?limit=100&status=succeeded");
  const [runId, setRunId] = useState("");
  const available = useMemo(() => data?.items ?? [], [data]);
  useEffect(() => {
    if (!runId && available.length) setRunId(available[0].run_id);
  }, [runId, available]);
  if (error) return <ErrorState error={error} />;
  if (loading || !data) return <LoadingState />;
  return (
    <section className="panel">
      <p className="eyebrow">Bounded, in-memory downloads</p>
      <h2>Export selected benchmark results</h2>
      {!available.length ? <EmptyState>Run a benchmark before exporting results.</EmptyState> : (
        <>
          <label className="export-select">
            Benchmark run
            <select value={runId} onChange={(event) => setRunId(event.target.value)}>
              {available.map((run) => <option key={run.run_id} value={run.run_id}>{run.run_id} · {run.workload}</option>)}
            </select>
          </label>
          <div className="export-links">
            {(["csv", "json", "pdf"] as const).map((format) => (
              <a className="button button-primary" key={format} href={downloadUrl(runId, format)} download>
                Download {format.toUpperCase()}
              </a>
            ))}
          </div>
          <p className="muted">Exports contain measured observations for this run only. No arbitrary path is accepted.</p>
        </>
      )}
    </section>
  );
}

function SettingsPage({ theme, toggleTheme }: { theme: string; toggleTheme: () => void }) {
  const { data, loading, error } = useApi<RunOptions>("/options");
  return (
    <section className="panel">
      <p className="eyebrow">Local preferences</p>
      <h2>Settings</h2>
      <label className="checkbox-label">
        <input type="checkbox" checked={theme === "light"} onChange={toggleTheme} />
        Use light theme
      </label>
      <p className="muted">Theme preference is stored in this browser only.</p>
      {error ? <ErrorState error={error} /> : loading || !data
        ? <LoadingState label="Loading server limits" />
        : <dl className="status-list">
            <div><dt>Maximum worker processes</dt><dd>{data.limits.max_workers}</dd></div>
            <div><dt>Maximum measured repetitions</dt><dd>{data.limits.max_runs}</dd></div>
            <div><dt>Native engine</dt><dd>{data.engines.native ? "Available" : "Unavailable"}</dd></div>
            <div><dt>Equivalent comparison engine</dt><dd>{data.engines.both ? "Available" : "Unavailable"}</dd></div>
          </dl>}
    </section>
  );
}

function HealthPage() {
  const ready = useApi<{
    status: string;
    database: string;
    native_benchmark: string;
    comparison_benchmark: string;
  }>("/ready");
  const system = useApi<{
    python_version: string;
    platform: string;
    processor: string;
    logical_cpu_count: number | null;
    native_benchmark: boolean;
    comparison_benchmark: boolean;
  }>("/system");
  if (ready.error) return <ErrorState error={ready.error} />;
  if (ready.loading || !ready.data) return <LoadingState label="Checking local application readiness" />;
  return (
    <div className="page-stack">
      <section className="panel">
        <p className="eyebrow">Actual local checks</p>
        <h2>System health</h2>
        <dl className="status-list">
          <div><dt>API health</dt><dd>{ready.data.status}</dd></div>
          <div><dt>SQLite readiness</dt><dd>{ready.data.database}</dd></div>
          <div><dt>Native workload binary</dt><dd>{ready.data.native_benchmark}</dd></div>
          <div><dt>Comparison binary</dt><dd>{ready.data.comparison_benchmark}</dd></div>
          {system.data && <>
            <div><dt>Python</dt><dd>{system.data.python_version}</dd></div>
            <div><dt>Platform</dt><dd>{system.data.platform}</dd></div>
            <div><dt>Logical CPUs</dt><dd>{system.data.logical_cpu_count ?? "Not reported"}</dd></div>
          </>}
        </dl>
        {system.error && <ErrorState error={system.error} />}
      </section>
    </div>
  );
}

function DashboardLayout({ theme, toggleTheme }: { theme: string; toggleTheme: () => void }) {
  const currentPage = navigation.find((item) => item[1] === window.location.pathname)?.[0] ?? "Overview";
  return (
    <div className={`app-shell theme-${theme}`}>
      <aside className="sidebar">
        <Link className="brand" to="/" aria-label="BenchForge overview">
          <span className="brand-mark" aria-hidden="true">BF</span>
          <span><strong>BenchForge</strong><small>Performance lab</small></span>
        </Link>
        <nav aria-label="Main navigation">
          {navigation.map(([label, path, key]) => (
            <NavLink key={key} to={path} end={path === "/"} className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>
              <span className={`nav-icon icon-${key}`} aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="local-indicator" />
          Local data stays on this machine
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div><p className="eyebrow">BenchForge / local workspace</p><h1>{currentPage}</h1></div>
          <button type="button" className="button button-secondary theme-toggle" onClick={toggleTheme}>
            {theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
        </header>
        <div className="route-content"><Outlet /></div>
        <footer className="app-footer">Measured results only · Local, single-worker execution · Optional integrations are not probed</footer>
      </main>
    </div>
  );
}

export default function App() {
  const [theme, setTheme] = useState(() => {
    try {
      return window.localStorage.getItem("benchforge-theme") === "light" ? "light" : "dark";
    } catch {
      return "dark";
    }
  });
  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      window.localStorage.setItem("benchforge-theme", next);
    } catch {
      // The application remains usable when browser storage is disabled.
    }
  }
  return (
    <Routes>
      <Route element={<DashboardLayout theme={theme} toggleTheme={toggleTheme} />}>
        <Route index element={<OverviewPage />} />
        <Route path="run" element={<RunPage />} />
        <Route path="compare" element={<ComparePage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="resources" element={<ResourcePage />} />
        <Route path="storage" element={<StoragePage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="platform" element={<DataPlatformPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="exports" element={<ExportsPage />} />
        <Route path="settings" element={<SettingsPage theme={theme} toggleTheme={toggleTheme} />} />
        <Route path="health" element={<HealthPage />} />
        <Route path="*" element={<EmptyState>The requested page does not exist. Use the navigation to return to a BenchForge view.</EmptyState>} />
      </Route>
    </Routes>
  );
}
