import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BrandFilters, filterBrands } from "./BrandFilters";
import type { BrandStatusOut, RunOut } from "../types";

function brand(slug: string, over: Partial<BrandStatusOut> = {}): BrandStatusOut {
  return {
    make_id: 1, brand: slug.toUpperCase(), slug, paused: false, year_from_years: 10,
    schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0, last_run: null,
    ...over,
  };
}

function run(status: string): RunOut {
  return {
    id: 1, brand: "X", started_at: "2026-07-27T03:00:00", finished_at: null, status,
    listings_seen: 0, new_listings: 0, price_changes: 0, sold_detected: 0, errors_count: 0,
    phase: null, search_finished_at: null, search_total: null, detail_total: null, detail_enriched: 0,
  };
}

describe("filterBrands", () => {
  const brands = [
    brand("opel"),
    brand("toyota", { paused: true }),
    brand("kia", { last_run: run("running") }),
    brand("skoda", { last_run: run("error") }),
  ];

  it("matches on slug case-insensitively", () => {
    expect(filterBrands(brands, "OPE", "all").map((b) => b.slug)).toEqual(["opel"]);
  });

  it("filters paused brands", () => {
    expect(filterBrands(brands, "", "paused").map((b) => b.slug)).toEqual(["toyota"]);
  });

  it("filters running brands", () => {
    expect(filterBrands(brands, "", "running").map((b) => b.slug)).toEqual(["kia"]);
  });

  it("filters brands whose last run errored", () => {
    expect(filterBrands(brands, "", "error").map((b) => b.slug)).toEqual(["skoda"]);
  });

  it("returns everything with no filters", () => {
    expect(filterBrands(brands, "", "all")).toHaveLength(4);
  });

  it("includes blocked runs when filtering for error status", () => {
    const brandsWithBlocked = [
      brand("audi", { last_run: run("error") }),
      brand("bmw", { last_run: run("blocked") }),
    ];
    expect(filterBrands(brandsWithBlocked, "", "error").map((b) => b.slug)).toEqual(["audi", "bmw"]);
  });

  it("combines query and status filters with AND logic", () => {
    const testBrands = [
      brand("toyota", { paused: true }),
      brand("toyota-gr", { paused: false }),
      brand("kia", { paused: true }),
    ];
    const result = filterBrands(testBrands, "toy", "paused");
    expect(result.map((b) => b.slug)).toEqual(["toyota"]);
  });

  it("matches query against display name (brand field)", () => {
    const testBrands = [
      brand("opel", { brand: "OPEL GROUP" }),
      brand("toyota"),
    ];
    expect(filterBrands(testBrands, "group", "all").map((b) => b.slug)).toEqual(["opel"]);
  });
});

describe("BrandFilters", () => {
  it("reports typing", async () => {
    const onQueryChange = vi.fn();
    render(<BrandFilters query="" status="all" onQueryChange={onQueryChange} onStatusChange={vi.fn()} />);

    await userEvent.type(screen.getByRole("textbox", { name: /cerca marca/i }), "op");

    expect(onQueryChange).toHaveBeenCalled();
  });

  it("reports status changes", async () => {
    const onStatusChange = vi.fn();
    render(<BrandFilters query="" status="all" onQueryChange={vi.fn()} onStatusChange={onStatusChange} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: /stato/i }), "paused");

    expect(onStatusChange).toHaveBeenCalledWith("paused");
  });
});
