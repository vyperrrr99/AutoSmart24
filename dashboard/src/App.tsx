import { useEffect, useState } from "react";
import { BrandCard } from "./components/BrandCard";
import { BrandDetail } from "./components/BrandDetail";
import { ManageBrands } from "./components/ManageBrands";
import { fetchBrands, pauseBrand, resumeBrand, runBrandNow } from "./api";
import type { BrandStatusOut } from "./types";

const POLL_INTERVAL_ACTIVE_MS = 3000;
const POLL_INTERVAL_IDLE_MS = 15000;

export function App() {
  const [brands, setBrands] = useState<BrandStatusOut[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [view, setView] = useState<"overview" | "manage">("overview");

  async function reload() {
    setBrands(await fetchBrands());
  }

  useEffect(() => {
    reload();
  }, []);

  useEffect(() => {
    const hasActiveRun = brands.some((b) => b.last_run?.status === "running");
    const interval = hasActiveRun ? POLL_INTERVAL_ACTIVE_MS : POLL_INTERVAL_IDLE_MS;
    const timer = setInterval(reload, interval);
    return () => clearInterval(timer);
  }, [brands]);

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
      <nav className="view-nav">
        <button onClick={() => setView("overview")} disabled={view === "overview"}>
          Panoramica
        </button>
        <button onClick={() => setView("manage")} disabled={view === "manage"}>
          Gestisci marche
        </button>
      </nav>
      {view === "overview" && (
        <>
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
          {selectedSlug && <BrandDetail brandSlug={selectedSlug} onClose={() => setSelectedSlug(null)} />}
        </>
      )}
      {view === "manage" && <ManageBrands trackedBrands={brands} onBrandsChanged={reload} />}
    </div>
  );
}
