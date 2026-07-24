import { useEffect, useState } from "react";
import { BrandCard } from "./components/BrandCard";
import { fetchBrands, pauseBrand, resumeBrand, runBrandNow } from "./api";
import type { BrandStatusOut } from "./types";

const POLL_INTERVAL_MS = 15000;

export function App() {
  const [brands, setBrands] = useState<BrandStatusOut[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  async function reload() {
    setBrands(await fetchBrands());
  }

  useEffect(() => {
    reload();
    const timer = setInterval(reload, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);

  async function handlePause(slug: string) {
    await pauseBrand(slug);
    await reload();
  }

  async function handleResume(slug: string) {
    await resumeBrand(slug);
    await reload();
  }

  async function handleRunNow(slug: string) {
    await runBrandNow(slug);
    await reload();
  }

  return (
    <div className="app">
      <h1>AutoSmart24 — Monitoraggio Scraper</h1>
      <div className="brand-grid">
        {brands.map((brand) => (
          <BrandCard
            key={brand.slug}
            brand={brand}
            onPause={handlePause}
            onResume={handleResume}
            onRunNow={handleRunNow}
            onSelect={setSelectedSlug}
          />
        ))}
      </div>
      {selectedSlug && (
        <p style={{ opacity: 0.7 }}>
          Dettaglio per "{selectedSlug}" — vedi BrandDetail (Task 18).
        </p>
      )}
    </div>
  );
}
