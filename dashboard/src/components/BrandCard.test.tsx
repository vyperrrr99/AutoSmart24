import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BrandCard } from "./BrandCard";
import type { BrandStatusOut } from "../types";

const brand: BrandStatusOut = {
  brand: "Fiat",
  slug: "fiat",
  paused: false,
  last_run: {
    id: 1, brand: "Fiat", started_at: "2026-07-24T10:00:00Z", finished_at: "2026-07-24T10:05:00Z",
    status: "success", listings_seen: 100, new_listings: 5, price_changes: 3, sold_detected: 2, errors_count: 0,
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
});
