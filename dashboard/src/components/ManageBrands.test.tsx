import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManageBrands } from "./ManageBrands";
import type { BrandStatusOut } from "../types";
import * as api from "../api";

vi.mock("../api");

const trackedFiat: BrandStatusOut = {
  make_id: 28, brand: "Fiat", slug: "fiat", paused: false,
  year_from_years: 5, schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0,
  last_run: null,
};

describe("ManageBrands", () => {
  it("loads and displays the catalog, excluding already-tracked brands", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([
      { make_id: 28, display_name: "Fiat", slug: "fiat" },
      { make_id: 13, display_name: "BMW", slug: "bmw" },
    ]);

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={vi.fn()} />);

    // Fiat is deliberately still visible elsewhere on the page (in the
    // tracked-brands list, as trackedFiat) -- querying by plain text would
    // find it there too. Query by checkbox role+name instead, since only
    // catalog rows render as checkboxes; the tracked list renders a plain
    // <span>, so this only matches an addable (i.e. not-yet-tracked) entry.
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "BMW" })).toBeInTheDocument());
    expect(screen.queryByRole("checkbox", { name: "Fiat" })).not.toBeInTheDocument();
  });

  it("filters the catalog by search text", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([
      { make_id: 13, display_name: "BMW", slug: "bmw" },
      { make_id: 6, display_name: "Alfa Romeo", slug: "alfa-romeo" },
    ]);

    render(<ManageBrands trackedBrands={[]} onBrandsChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("BMW")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Cerca marca..."), { target: { value: "alfa" } });

    expect(screen.queryByText("BMW")).not.toBeInTheDocument();
    expect(screen.getByText("Alfa Romeo")).toBeInTheDocument();
  });

  it("adds selected brands with the current defaults", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([{ make_id: 13, display_name: "BMW", slug: "bmw" }]);
    vi.mocked(api.addBrands).mockResolvedValue([]);
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[]} onBrandsChanged={onBrandsChanged} />);
    await waitFor(() => expect(screen.getByText("BMW")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByText(/Aggiungi selezionate/));

    await waitFor(() => expect(api.addBrands).toHaveBeenCalledWith([13], {
      year_from_years: 5, schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0,
    }));
    expect(onBrandsChanged).toHaveBeenCalled();
  });

  it("applies defaults to all tracked brands", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);
    vi.mocked(api.applyDefaultsToAllBrands).mockResolvedValue([]);
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={onBrandsChanged} />);

    fireEvent.click(screen.getByText(/Applica a tutte le marche monitorate/));

    await waitFor(() =>
      expect(api.applyDefaultsToAllBrands).toHaveBeenCalledWith({
        year_from_years: 5, schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0,
      })
    );
    expect(onBrandsChanged).toHaveBeenCalled();
  });

  it("saves an individual tracked brand's edited year and schedule", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);
    vi.mocked(api.updateBrand).mockResolvedValue(trackedFiat);
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={onBrandsChanged} />);

    fireEvent.change(screen.getByLabelText("Anno Fiat"), { target: { value: "10" } });
    fireEvent.click(screen.getByTestId("tracked-brand-fiat").querySelector("button")!);

    await waitFor(() =>
      expect(api.updateBrand).toHaveBeenCalledWith("fiat", {
        year_from_years: 10, schedule_day_of_week: null, schedule_hour: 3, schedule_minute: 0,
      })
    );
    expect(onBrandsChanged).toHaveBeenCalled();
  });

  it("removes a tracked brand", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);
    vi.mocked(api.removeBrand).mockResolvedValue({ deleted: true });
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={onBrandsChanged} />);

    fireEvent.click(screen.getByText("Rimuovi"));

    await waitFor(() => expect(api.removeBrand).toHaveBeenCalledWith("fiat"));
    expect(onBrandsChanged).toHaveBeenCalled();
  });

  it("refreshes the catalog and reloads the list", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValueOnce([]).mockResolvedValueOnce([
      { make_id: 13, display_name: "BMW", slug: "bmw" },
    ]);
    vi.mocked(api.refreshBrandCatalog).mockResolvedValue({ count: 1 });

    render(<ManageBrands trackedBrands={[]} onBrandsChanged={vi.fn()} />);

    fireEvent.click(screen.getByText("Aggiorna catalogo"));

    await waitFor(() => expect(screen.getByText("BMW")).toBeInTheDocument());
    expect(api.refreshBrandCatalog).toHaveBeenCalled();
  });
});
