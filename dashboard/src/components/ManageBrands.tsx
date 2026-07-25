import { useEffect, useRef, useState } from "react";
import {
  addBrands,
  applyDefaultsToAllBrands,
  fetchBrandCatalog,
  refreshBrandCatalog,
  removeBrand,
  updateBrand,
} from "../api";
import type { BrandBulkAddPatch, BrandCatalogEntryOut, BrandDefaultsPatch, BrandStatusOut } from "../types";

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

// Maps a thrown error (ideally an ApiError from api.ts, but anything with a
// numeric `status` property duck-types the same way) into Italian, user-facing
// prose. Falls back to a generic message when no status is available.
function describeError(err: unknown): string {
  const status =
    typeof err === "object" && err !== null && "status" in err
      ? (err as { status?: unknown }).status
      : undefined;

  if (status === 422) {
    return "Impossibile salvare: controlla che ora (0-23) e minuto (0-59) siano validi.";
  }
  if (typeof status === "number") {
    return `Operazione non riuscita (errore ${status}).`;
  }
  return "Operazione non riuscita.";
}

interface TrackedBrandRowProps {
  brand: BrandStatusOut;
  onSave: (slug: string, patch: BrandDefaultsPatch) => void;
  onRemove: (slug: string) => void;
}

interface RowScheduleFields {
  year_from_years: number | null;
  schedule_day_of_week: string | null;
  schedule_hour: number;
  schedule_minute: number;
}

function rowFieldsFrom(brand: BrandStatusOut): RowScheduleFields {
  return {
    year_from_years: brand.year_from_years,
    schedule_day_of_week: brand.schedule_day_of_week,
    schedule_hour: brand.schedule_hour,
    schedule_minute: brand.schedule_minute,
  };
}

function TrackedBrandRow({ brand, onSave, onRemove }: TrackedBrandRowProps) {
  const [year, setYear] = useState(brand.year_from_years === null ? "" : String(brand.year_from_years));
  const [day, setDay] = useState(brand.schedule_day_of_week ?? "");
  const [hour, setHour] = useState(String(brand.schedule_hour));
  const [minute, setMinute] = useState(String(brand.schedule_minute));
  const [confirmingRemove, setConfirmingRemove] = useState(false);

  // Tracks the last prop values we've actually synced local state from, so we
  // can tell "props changed" apart from "local state differs from props"
  // (the latter is just normal, in-progress editing). App.tsx polls every 3s
  // while a scrape is running; without this guard, a poll that returns
  // unchanged data would still stomp on whatever the user is mid-typing.
  const lastSyncedRef = useRef<RowScheduleFields>(rowFieldsFrom(brand));

  useEffect(() => {
    const prev = lastSyncedRef.current;
    const next = rowFieldsFrom(brand);
    const changed =
      prev.year_from_years !== next.year_from_years ||
      prev.schedule_day_of_week !== next.schedule_day_of_week ||
      prev.schedule_hour !== next.schedule_hour ||
      prev.schedule_minute !== next.schedule_minute;

    if (changed) {
      setYear(next.year_from_years === null ? "" : String(next.year_from_years));
      setDay(next.schedule_day_of_week ?? "");
      setHour(String(next.schedule_hour));
      setMinute(String(next.schedule_minute));
      lastSyncedRef.current = next;
    }
    // Depend on the `brand` object reference itself (not the destructured
    // primitives): App.tsx's poll produces a brand new object on every
    // fetch, even when nothing changed, so this effect must run every time
    // and let the ref comparison above decide whether to actually resync.
  }, [brand]);

  function handleSave() {
    onSave(brand.slug, {
      year_from_years: year === "" ? null : Number(year),
      schedule_day_of_week: day === "" ? null : day,
      schedule_hour: Number(hour),
      schedule_minute: Number(minute),
    });
  }

  function handleRemoveClick() {
    if (confirmingRemove) {
      setConfirmingRemove(false);
      onRemove(brand.slug);
    } else {
      setConfirmingRemove(true);
    }
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
      {confirmingRemove ? (
        <>
          <button onClick={handleRemoveClick}>Conferma</button>
          <button onClick={() => setConfirmingRemove(false)}>Annulla</button>
        </>
      ) : (
        <button onClick={handleRemoveClick}>Rimuovi</button>
      )}
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
  const [error, setError] = useState<string | null>(null);

  async function loadCatalog() {
    try {
      setCatalog(await fetchBrandCatalog());
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }

  useEffect(() => {
    loadCatalog();
  }, []);

  const trackedMakeIds = new Set(trackedBrands.map((b) => b.make_id));
  const filtered = catalog.filter(
    (entry) => !trackedMakeIds.has(entry.make_id) && entry.display_name.toLowerCase().includes(query.toLowerCase())
  );
  const allFilteredSelected = filtered.length > 0 && filtered.every((entry) => selected.has(entry.make_id));

  function toggleSelected(makeId: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(makeId)) next.delete(makeId);
      else next.add(makeId);
      return next;
    });
  }

  function toggleSelectAllFiltered() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        filtered.forEach((entry) => next.delete(entry.make_id));
      } else {
        filtered.forEach((entry) => next.add(entry.make_id));
      }
      return next;
    });
  }

  function currentDefaults(): BrandBulkAddPatch {
    return {
      year_from_years: defaultYear === "" ? null : Number(defaultYear),
      schedule_day_of_week: defaultDay === "" ? null : defaultDay,
      schedule_hour: Number(defaultHour),
      schedule_minute: Number(defaultMinute),
    };
  }

  async function handleRefreshCatalog() {
    try {
      await refreshBrandCatalog();
      await loadCatalog();
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }

  async function handleAddSelected() {
    if (selected.size === 0) return;
    try {
      await addBrands(Array.from(selected), currentDefaults());
      setSelected(new Set());
      onBrandsChanged();
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }

  async function handleApplyDefaults() {
    try {
      await applyDefaultsToAllBrands(currentDefaults());
      onBrandsChanged();
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }

  async function handleSaveBrand(slug: string, patch: BrandDefaultsPatch) {
    try {
      await updateBrand(slug, patch);
      onBrandsChanged();
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }

  async function handleRemoveBrand(slug: string) {
    try {
      await removeBrand(slug);
      onBrandsChanged();
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }

  return (
    <div className="manage-brands">
      <h2>Gestisci marche</h2>

      {error && (
        <div role="alert" className="manage-brands-error">
          {error}
        </div>
      )}

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
        <div className="catalog-select-all">
          <button onClick={toggleSelectAllFiltered} disabled={filtered.length === 0}>
            {allFilteredSelected ? "Deseleziona tutte le filtrate" : "Seleziona tutte le filtrate"}
          </button>
        </div>
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
