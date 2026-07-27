import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BrandDetail } from "./BrandDetail";
import * as api from "../api";

vi.mock("../api");

describe("BrandDetail", () => {
  // Mocked calls otherwise accumulate across tests in this file, which would
  // let the polling assertion below pass even without an interval (the first
  // test's single call plus this test's initial load already exceeds 1).
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders events after loading", async () => {
    vi.mocked(api.fetchBrandRuns).mockResolvedValue([]);
    vi.mocked(api.fetchBrandEvents).mockResolvedValue([
      {
        id: 1, run_id: 1, brand: "Fiat", level: "warning", message: "Test event",
        url: null, created_at: "2026-07-24T10:00:00Z",
      },
    ]);
    vi.mocked(api.fetchBrandMetrics).mockResolvedValue([]);

    render(<BrandDetail brandSlug="fiat" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("Test event")).toBeInTheDocument());
  });

  it("refetches while the panel stays open", async () => {
    vi.mocked(api.fetchBrandRuns).mockResolvedValue([]);
    vi.mocked(api.fetchBrandEvents).mockResolvedValue([]);
    vi.mocked(api.fetchBrandMetrics).mockResolvedValue([]);

    render(<BrandDetail brandSlug="fiat" onClose={vi.fn()} pollIntervalMs={20} />);

    await waitFor(() => expect(vi.mocked(api.fetchBrandEvents).mock.calls.length).toBeGreaterThan(1));
  });
});
