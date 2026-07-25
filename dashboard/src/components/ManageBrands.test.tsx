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

    fireEvent.click(screen.getByRole("checkbox", { name: "BMW" }));
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

  it("does not remove a tracked brand on a single click (requires confirmation)", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);
    vi.mocked(api.removeBrand).mockResolvedValue({ deleted: true });
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={onBrandsChanged} />);

    fireEvent.click(screen.getByText("Rimuovi"));

    expect(api.removeBrand).not.toHaveBeenCalled();
    expect(screen.getByText("Conferma")).toBeInTheDocument();
    expect(screen.getByText("Annulla")).toBeInTheDocument();
  });

  it("removes a tracked brand after confirming", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);
    vi.mocked(api.removeBrand).mockResolvedValue({ deleted: true });
    const onBrandsChanged = vi.fn();

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={onBrandsChanged} />);

    fireEvent.click(screen.getByText("Rimuovi"));
    fireEvent.click(screen.getByText("Conferma"));

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

  it("shows a readable error message when an API call fails with a 422", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);
    const validationError = Object.assign(new Error("Request failed"), { status: 422 });
    vi.mocked(api.applyDefaultsToAllBrands).mockRejectedValue(validationError);

    render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={vi.fn()} />);

    fireEvent.click(screen.getByText(/Applica a tutte le marche monitorate/));

    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent(/ora \(0-23\)/);
      expect(alert).toHaveTextContent(/minuto \(0-59\)/);
    });
  });

  it("selects only the currently-filtered catalog entries via select-all", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([
      { make_id: 1, display_name: "Audi A", slug: "audi-a" },
      { make_id: 2, display_name: "Audi B", slug: "audi-b" },
      { make_id: 13, display_name: "BMW", slug: "bmw" },
    ]);

    render(<ManageBrands trackedBrands={[]} onBrandsChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("BMW")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Cerca marca..."), { target: { value: "audi" } });
    expect(screen.queryByText("BMW")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText(/Seleziona tutte le filtrate/));

    expect(screen.getByText(/Aggiungi selezionate \(2\)/)).toBeInTheDocument();

    // Clear the filter and confirm BMW (not part of the filtered set at
    // selection time) was left unselected.
    fireEvent.change(screen.getByPlaceholderText("Cerca marca..."), { target: { value: "" } });
    expect(screen.getByRole("checkbox", { name: "BMW" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Audi A" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Audi B" })).toBeChecked();
  });

  it("does not clobber an in-progress edit when props are re-rendered with unchanged values", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);

    const { rerender } = render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Ora Fiat"), { target: { value: "7" } });
    expect(screen.getByLabelText("Ora Fiat")).toHaveValue(7);

    // Simulate a background poll returning a fresh object with identical field values.
    const sameValuesBrand: BrandStatusOut = { ...trackedFiat };
    rerender(<ManageBrands trackedBrands={[sameValuesBrand]} onBrandsChanged={vi.fn()} />);

    expect(screen.getByLabelText("Ora Fiat")).toHaveValue(7);
  });

  it("updates the displayed values when props actually change (e.g. after a bulk apply)", async () => {
    vi.mocked(api.fetchBrandCatalog).mockResolvedValue([]);

    const { rerender } = render(<ManageBrands trackedBrands={[trackedFiat]} onBrandsChanged={vi.fn()} />);

    expect(screen.getByLabelText("Ora Fiat")).toHaveValue(3);

    const updatedBrand: BrandStatusOut = { ...trackedFiat, schedule_hour: 9, schedule_minute: 30 };
    rerender(<ManageBrands trackedBrands={[updatedBrand]} onBrandsChanged={vi.fn()} />);

    expect(screen.getByLabelText("Ora Fiat")).toHaveValue(9);
    expect(screen.getByLabelText("Minuto Fiat")).toHaveValue(30);
  });
});
