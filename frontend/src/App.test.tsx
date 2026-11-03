import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("BenchForge application states", () => {
  it("renders dashboard values returned by the real API and honest empty charts", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/analytics/summary")) {
        return Promise.resolve(jsonResponse({
          total_runs: 3,
          successful_runs: 2,
          failed_runs: 1,
          active_runs: 0,
          sample_count: 10,
          latency: {
            median: 1_000,
            p95_nearest_rank: 1_100,
            p99_nearest_rank: null,
            p99_reliable: false,
          },
          cpu_observations: 4,
          memory_observations: 2,
          throughput_observations: 0,
        }));
      }
      if (url.includes("/analytics/trends")) {
        return Promise.resolve(jsonResponse({ items: [] }));
      }
      if (url.includes("/runs?")) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 8, offset: 0 }));
      }
      return Promise.resolve(jsonResponse({ error: { message: "Not found" } }, 404));
    }));

    render(<App />, { wrapper: MemoryRouter });

    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByText("Successful")).toBeInTheDocument();
    expect(screen.getByText(/No observations for median runtime/)).toBeInTheDocument();
    expect(screen.getByText(/No benchmark history matches these filters/)).toBeInTheDocument();
  });

  it("shows a clear offline recovery state instead of a fake dashboard", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("fetch failed"))));

    render(<App />, { wrapper: MemoryRouter });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Backend is offline");
    });
    expect(screen.getByText(/Start the FastAPI server/)).toBeInTheDocument();
  });

  it("passes the selected date range to the run-history API", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname.endsWith("/runs")) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 20, offset: 0 }));
      }
      return Promise.resolve(jsonResponse({ error: { message: "Not found" } }, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/history"]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(await screen.findByLabelText("From date"), {
      target: { value: "2026-11-01" },
    });
    fireEvent.change(screen.getByLabelText("Through date"), {
      target: { value: "2026-11-02" },
    });

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([input]) =>
        new URL(String(input), window.location.origin),
      );
      expect(urls.some((url) =>
        url.searchParams.get("from_date") === "2026-11-01"
        && url.searchParams.get("to_date") === "2026-11-02",
      )).toBe(true);
    });
  });
});
