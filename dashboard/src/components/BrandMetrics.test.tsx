import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BrandMetrics } from "./BrandMetrics";

describe("BrandMetrics", () => {
  it("shows per-phase durations and rates", () => {
    render(
      <BrandMetrics
        metrics={[{
          run_id: 42, started_at: "2026-07-27T03:00:00", status: "success",
          search_seconds: 480, search_items: 7200, search_rate_per_min: 900,
          detail_seconds: 6720, detail_items: 6720, detail_rate_per_min: 60,
        }]}
      />,
    );

    expect(screen.getByText(/8m/)).toBeInTheDocument();
    expect(screen.getByText(/900/)).toBeInTheDocument();
    expect(screen.getByText(/60/)).toBeInTheDocument();
  });

  it("explains the empty state", () => {
    render(<BrandMetrics metrics={[]} />);

    expect(screen.getByText(/nessuna run conclusa/i)).toBeInTheDocument();
  });

  it("shows a placeholder instead of the literal string null for missing numeric fields", () => {
    render(
      <BrandMetrics
        metrics={[{
          run_id: 7, started_at: "2026-07-27T03:00:00", status: "running",
          search_seconds: null, search_items: null, search_rate_per_min: null,
          detail_seconds: null, detail_items: null, detail_rate_per_min: null,
        }]}
      />,
    );

    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.queryByText(/null/i)).not.toBeInTheDocument();
  });
});
