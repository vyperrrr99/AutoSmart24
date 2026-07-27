import type { BrandStatusOut } from "../types";

export type BrandStatusFilter = "all" | "running" | "paused" | "error";

interface BrandFiltersProps {
  query: string;
  status: BrandStatusFilter;
  onQueryChange: (value: string) => void;
  onStatusChange: (value: BrandStatusFilter) => void;
}

const STATUS_OPTIONS: { value: BrandStatusFilter; label: string }[] = [
  { value: "all", label: "Tutte" },
  { value: "running", label: "In esecuzione" },
  { value: "paused", label: "In pausa" },
  { value: "error", label: "Con errori" },
];

export function filterBrands(
  brands: BrandStatusOut[],
  query: string,
  status: BrandStatusFilter,
): BrandStatusOut[] {
  const needle = query.trim().toLowerCase();
  return brands.filter((b) => {
    if (needle && !b.slug.toLowerCase().includes(needle) && !b.brand.toLowerCase().includes(needle)) {
      return false;
    }
    if (status === "paused") return b.paused;
    if (status === "running") return b.last_run?.status === "running";
    if (status === "error") return b.last_run?.status === "error" || b.last_run?.status === "blocked";
    return true;
  });
}

export function BrandFilters({ query, status, onQueryChange, onStatusChange }: BrandFiltersProps) {
  return (
    <div className="brand-filters">
      <label>
        Cerca marca
        <input type="text" value={query} onChange={(e) => onQueryChange(e.target.value)} />
      </label>
      <label>
        Stato
        <select value={status} onChange={(e) => onStatusChange(e.target.value as BrandStatusFilter)}>
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
    </div>
  );
}
