import { useEffect, useState } from "react";
import { BrandCard } from "./components/BrandCard";
import { BrandDetail } from "./components/BrandDetail";
import { BrandFilters, filterBrands, type BrandStatusFilter } from "./components/BrandFilters";
import { ManageBrands } from "./components/ManageBrands";
import { QueuePanel } from "./components/QueuePanel";
import { fetchBrands, fetchQueue, pauseBrand, resumeBrand, resumeQueue, runBrandNow } from "./api";
import type { BrandStatusOut, QueueOut } from "./types";

const POLL_INTERVAL_ACTIVE_MS = 3000;
const POLL_INTERVAL_IDLE_MS = 15000;

export function App() {
  const [brands, setBrands] = useState<BrandStatusOut[]>([]);
  const [queue, setQueue] = useState<QueueOut | null>(null);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [view, setView] = useState<"overview" | "manage">("overview");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<BrandStatusFilter>("all");

  async function reload() {
    const nextBrands = await fetchBrands();
    setBrands(nextBrands);
    // The queue endpoint is best-effort: if it fails, the brand grid must
    // still render (degraded, not blank). QueuePanel already renders
    // nothing for a null queue.
    try {
      setQueue(await fetchQueue());
    } catch {
      setQueue(null);
    }
  }

  useEffect(() => {
    reload();
  }, []);

  useEffect(() => {
    const hasActiveRun = queue?.current != null || brands.some((b) => b.last_run?.status === "running");
    const interval = hasActiveRun ? POLL_INTERVAL_ACTIVE_MS : POLL_INTERVAL_IDLE_MS;
    const timer = setInterval(reload, interval);
    return () => clearInterval(timer);
  }, [brands, queue]);

  async function handleResumeQueue() {
    await resumeQueue();
    await reload();
  }

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
          <QueuePanel queue={queue} onResume={handleResumeQueue} />
          <BrandFilters
            query={query}
            status={statusFilter}
            onQueryChange={setQuery}
            onStatusChange={setStatusFilter}
          />
          <div className="brand-grid">
            {filterBrands(brands, query, statusFilter).map((brand) => (
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
