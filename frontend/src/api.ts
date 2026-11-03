export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface RunSummary {
  run_id: string;
  started_at: string;
  workload: string;
  engine: string;
  status: RunStatus;
  sample_count: number;
  error_code: string | null;
  error_message: string | null;
  config: Record<string, unknown>;
}

export interface Sample {
  timestamp_utc: string;
  workload: string;
  sample_index: number;
  elapsed_ns: number;
  variant: string;
  pair_index: number | null;
  bytes_processed: number | null;
  peak_process_memory_bytes: number | null;
  peak_process_memory_source: string | null;
  current_working_set_bytes: number | null;
  private_process_bytes: number | null;
  thread_count: number | null;
  process_cpu_time_ns: number | null;
  cpu_utilization_percent: number | null;
}

export interface RunDetail extends RunSummary {
  submitted_at: string;
  started_at: string;
  finished_at: string | null;
  category: string;
  samples_truncated: boolean;
  samples: Sample[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
}

export class ApiError extends Error {
  readonly kind: "offline" | "timeout" | "http";
  readonly status?: number;
  readonly code?: string;

  constructor(
    message: string,
    kind: "offline" | "timeout" | "http",
    status?: number,
    code?: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.code = code;
  }
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");

export async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 8_000,
): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          ...init.headers,
        },
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new ApiError("The local API request timed out.", "timeout");
      }
      throw new ApiError(
        "BenchForge API is offline. Start the local backend and retry.",
        "offline",
      );
    }
    if (!response.ok) {
      let payload: ApiErrorPayload = {};
      try {
        payload = (await response.json()) as ApiErrorPayload;
      } catch {
        payload = {};
      }
      throw new ApiError(
        payload.error?.message ?? `The API returned HTTP ${response.status}.`,
        "http",
        response.status,
        payload.error?.code,
      );
    }
    return (await response.json()) as T;
  } finally {
    window.clearTimeout(timer);
  }
}

export function downloadUrl(runId: string, format: "csv" | "json" | "pdf"): string {
  return `${API_BASE}/exports/${encodeURIComponent(runId)}?format=${format}`;
}
