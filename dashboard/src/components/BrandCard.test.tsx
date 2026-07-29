import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BrandCard } from "./BrandCard";
import type { BrandStatusOut } from "../types";

const brand: BrandStatusOut = {
  make_id: 28,
  brand: "Fiat",
  slug: "fiat",
  paused: false,
  year_from_years: null,
  schedule_day_of_week: null,
  schedule_hour: 3,
  schedule_minute: 0,
  last_run: {
    id: 1, brand: "Fiat", started_at: "2026-07-24T10:00:00Z", finished_at: "2026-07-24T10:05:00Z",
    status: "success", listings_seen: 100, new_listings: 5, price_changes: 3, sold_detected: 2, errors_count: 0,
    phase: null, search_finished_at: null, search_total: null, detail_total: null, detail_enriched: 0,
  },
};

describe("BrandCard", () => {
  it("shows brand name and last run stats", () => {
    render(<BrandCard brand={brand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByText("Fiat")).toBeInTheDocument();
    expect(screen.getByText(/Nuovi annunci: 5/)).toBeInTheDocument();
  });

  it("calls onPause when pause button clicked", () => {
    const onPause = vi.fn();
    render(<BrandCard brand={brand} onPause={onPause} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByText("Metti in pausa"));
    expect(onPause).toHaveBeenCalledWith("fiat");
  });

  it("shows resume button and paused status when brand is paused", () => {
    const pausedBrand = { ...brand, paused: true };
    render(<BrandCard brand={pausedBrand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByText("In pausa")).toBeInTheDocument();
    expect(screen.getByText("Riprendi")).toBeInTheDocument();
  });

  it("shows Errore status when last run status is error", () => {
    const erroredBrand = { ...brand, last_run: { ...brand.last_run!, status: "error", errors_count: 1 } };
    render(<BrandCard brand={erroredBrand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByText("Errore")).toBeInTheDocument();
  });

  it("does not show a progress bar when no run is active", () => {
    render(<BrandCard brand={brand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("run-progress-bar")).not.toBeInTheDocument();
  });

  it("shows Parziale status when last run status is partial", () => {
    const partialBrand = { ...brand, last_run: { ...brand.last_run!, status: "partial" } };
    render(<BrandCard brand={partialBrand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByText(/parziale/i)).toBeInTheDocument();
  });

  it("does not present a partial run as a successful one", () => {
    // Pre-fix statusLabel fell through to "Attivo" for any status it didn't
    // recognise, so a run that skipped the sold check looked healthy.
    const partialBrand = { ...brand, last_run: { ...brand.last_run!, status: "partial" } };
    render(<BrandCard brand={partialBrand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.queryByText("Attivo")).not.toBeInTheDocument();
  });

  it("gives the Parziale badge a style distinct from Errore", () => {
    const partialBrand = { ...brand, last_run: { ...brand.last_run!, status: "partial" } };
    const { unmount } = render(
      <BrandCard brand={partialBrand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />,
    );
    const partialClass = screen.getByText(/parziale/i).className;
    unmount();
    const erroredBrand = { ...brand, last_run: { ...brand.last_run!, status: "error", errors_count: 1 } };
    render(<BrandCard brand={erroredBrand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByText(/errore/i).className).not.toBe(partialClass);
  });

  it("explains that sales were not evaluated when a run is partial", () => {
    const partialBrand = { ...brand, last_run: { ...brand.last_run!, status: "partial" } };
    render(<BrandCard brand={partialBrand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByText(/vendite non valutate/i)).toBeInTheDocument();
  });

  it("shows a progress bar with detail-phase counts while a run is in progress", () => {
    const runningBrand = {
      ...brand,
      last_run: {
        ...brand.last_run!,
        status: "running",
        phase: "detail",
        search_total: 100,
        detail_total: 100,
        detail_enriched: 40,
      },
    };
    render(<BrandCard brand={runningBrand} onPause={vi.fn()} onResume={vi.fn()} onRunNow={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByTestId("run-progress-bar")).toBeInTheDocument();
    expect(screen.getByText("40,0%")).toBeInTheDocument();
  });
});
