import { useEffect, useState } from "react";
import {
  addBrands,
  applyDefaultsToAllBrands,
  fetchBrandCatalog,
  refreshBrandCatalog,
  removeBrand,
  updateBrand,
} from "../api";
import type { BrandCatalogEntryOut, BrandDefaultsPatch, BrandStatusOut } from "../types";

interface ManageBrandsProps {
  trackedBrands: BrandStatusOut[];
  onBrandsChanged: () => void;
}

const DAYS: { value: string; label: string }[] = [
  { value: "", label: "Ogni giorno" },
  { value: "mon", label: "Lunedì" },
  { value: "tue", label: "Martedì" },
  { value: "wed", label: "Mercoledì" },
  { value: "thu", label: "Giovedì" },
  { value: "fri", label: "Venerdì" },
  { value: "sat", label: "Sabato" },
  { value: "sun", label: "Domenica" },
];

function dayLabel(day: string | null): string {
  return DAYS.find((d) => d.value === (day ?? ""))?.label ?? "Ogni giorno";
}

interface TrackedBrandRowProps {
  brand: BrandStatusOut;
  onSave: (slug: string, patch: BrandDefaultsPatch) => void;
  onRemove: (slug: string) => void;
}

function TrackedBrandRow({ brand, onSave, onRemove }: TrackedBrandRowProps) {
  const [year, setYear] = useState(brand.year_from_years === null ? "" : String(brand.year_from_years));
  const [day, setDay] = useState(brand.schedule_day_of_week ?? "");
  const [hour, setHour] = useState(String(brand.schedule_hour));
  const [minute, setMinute] = useState(String(brand.schedule_minute));

  function handleSave() {
    onSave(brand.slug, {
      year_from_years: year === "" ? null : Number(year),
      schedule_day_of_week: day === "" ? null : day,
      schedule_hour: Number(hour),
      schedule_minute: Number(minute),
    });
  }

  return (
    <li data-testid={`tracked-brand-${brand.slug}`}>
      <span>{brand.brand}</span>
      <label>
        Anno {brand.brand}
        <input
          type="number"
          min={0}
          aria-label={`Anno ${brand.brand}`}
          value={year}
          onChange={(e) => setYear(e.target.value)}
        />
      </label>
      <select aria-label={`Giorno ${brand.brand}`} value={day} onChange={(e) => setDay(e.target.value)}>
        {DAYS.map((d) => (
          <option key={d.value} value={d.value}>{d.label}</option>
        ))}
      </select>
      <input type="number" min={0} max={23} aria-label={`Ora ${brand.brand}`} value={hour} onChange={(e) => setHour(e.target.value)} />
      <input type="number" min={0} max={59} aria-label={`Minuto ${brand.brand}`} value={minute} onChange={(e) => setMinute(e.target.value)} />
      <button onClick={handleSave}>Salva</button>
      <button onClick={() => onRemove(brand.slug)}>Rimuovi</button>
    </li>
  );
}

export function ManageBrands({ trackedBrands, onBrandsChanged }: ManageBrandsProps) {
  const [catalog, setCatalog] = useState<BrandCatalogEntryOut[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [defaultYear, setDefaultYear] = useState("5");
  const [defaultDay, setDefaultDay] = useState("");
  const [defaultHour, setDefaultHour] = useState("3");
  const [defaultMinute, setDefaultMinute] = useState("0");

  async function loadCatalog() {
    setCatalog(await fetchBrandCatalog());
  }

  useEffect(() => {
    loadCatalog();
  }, []);

  const trackedMakeIds = new Set(trackedBrands.map((b) => b.make_id));
  const filtered = catalog.filter(
    (entry) => !trackedMakeIds.has(entry.make_id) && entry.display_name.toLowerCase().includes(query.toLowerCase())
  );

  function toggleSelected(makeId: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(makeId)) next.delete(makeId);
      else next.add(makeId);
      return next;
    });
  }

  function currentDefaults(): BrandDefaultsPatch {
    return {
      year_from_years: defaultYear === "" ? null : Number(defaultYear),
      schedule_day_of_week: defaultDay === "" ? null : defaultDay,
      schedule_hour: Number(defaultHour),
      schedule_minute: Number(defaultMinute),
    };
  }

  async function handleRefreshCatalog() {
    await refreshBrandCatalog();
    await loadCatalog();
  }

  async function handleAddSelected() {
    if (selected.size === 0) return;
    await addBrands(Array.from(selected), currentDefaults());
    setSelected(new Set());
    onBrandsChanged();
  }

  async function handleApplyDefaults() {
    await applyDefaultsToAllBrands(currentDefaults());
    onBrandsChanged();
  }

  async function handleSaveBrand(slug: string, patch: BrandDefaultsPatch) {
    await updateBrand(slug, patch);
    onBrandsChanged();
  }

  async function handleRemoveBrand(slug: string) {
    await removeBrand(slug);
    onBrandsChanged();
  }

  return (
    <div className="manage-brands">
      <h2>Gestisci marche</h2>

      <section className="brand-defaults">
        <h3>Predefiniti</h3>
        <label>
          Anno (ultimi N anni, vuoto = nessun filtro)
          <input type="number" min={0} value={defaultYear} onChange={(e) => setDefaultYear(e.target.value)} />
        </label>
        <label>
          Giorno
          <select value={defaultDay} onChange={(e) => setDefaultDay(e.target.value)}>
            {DAYS.map((d) => (
              <option key={d.value} value={d.value}>{d.label}</option>
            ))}
          </select>
        </label>
        <label>
          Ora
          <input type="number" min={0} max={23} value={defaultHour} onChange={(e) => setDefaultHour(e.target.value)} />
        </label>
        <label>
          Minuto
          <input type="number" min={0} max={59} value={defaultMinute} onChange={(e) => setDefaultMinute(e.target.value)} />
        </label>
        <button onClick={handleApplyDefaults} disabled={trackedBrands.length === 0}>
          Applica a tutte le marche monitorate
        </button>
      </section>

      <section className="brand-picker">
        <h3>Aggiungi marche</h3>
        <button onClick={handleRefreshCatalog}>Aggiorna catalogo</button>
        <input type="text" placeholder="Cerca marca..." value={query} onChange={(e) => setQuery(e.target.value)} />
        <ul className="catalog-list">
          {filtered.map((entry) => (
            <li key={entry.make_id}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.has(entry.make_id)}
                  onChange={() => toggleSelected(entry.make_id)}
                />
                {entry.display_name}
              </label>
            </li>
          ))}
        </ul>
        <button onClick={handleAddSelected} disabled={selected.size === 0}>
          Aggiungi selezionate ({selected.size})
        </button>
      </section>

      <section className="tracked-list">
        <h3>Marche monitorate</h3>
        <ul>
          {trackedBrands.map((brand) => (
            <TrackedBrandRow key={brand.slug} brand={brand} onSave={handleSaveBrand} onRemove={handleRemoveBrand} />
          ))}
        </ul>
      </section>
    </div>
  );
}

export { dayLabel };
