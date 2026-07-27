import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import * as api from "./api";

vi.mock("./api");

describe("App", () => {
  it("shows the queue panel and filters the brand grid", async () => {
    vi.mocked(api.fetchBrands).mockResolvedValue([
      {
        make_id: 54, brand: "Opel", slug: "opel", paused: false, year_from_years: 10,
        schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0, last_run: null,
      },
    ]);
    vi.mocked(api.fetchQueue).mockResolvedValue({
      halted: false, halted_reason: null, halted_at: null,
      current: {
        slug: "opel", brand: "Opel", phase: "detail", done: 10, total: 100,
        percent: 10, eta_seconds: 600, eta_is_fallback: false, started_at: "2026-07-27T14:00:00",
      },
      pending: [], total_eta_seconds: 600,
    });

    render(<App />);

    await waitFor(() => expect(screen.getByText(/In esecuzione: Opel/)).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: /cerca marca/i })).toBeInTheDocument();
  });
});
