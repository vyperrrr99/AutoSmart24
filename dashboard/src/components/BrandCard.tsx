import { RunProgress } from "./RunProgress";
import type { BrandStatusOut } from "../types";

interface BrandCardProps {
  brand: BrandStatusOut;
  onPause: (slug: string) => void;
  onResume: (slug: string) => void;
  onRunNow: (slug: string) => void;
  onSelect: (slug: string) => void;
}

function statusLabel(brand: BrandStatusOut): string {
  if (brand.paused) return "In pausa";
  if (brand.last_run?.status === "blocked") return "Bloccato";
  if (brand.last_run?.status === "error") return "Errore";
  if (brand.last_run?.status === "running") return "In esecuzione";
  return "Attivo";
}

export function BrandCard({ brand, onPause, onResume, onRunNow, onSelect }: BrandCardProps) {
  const status = statusLabel(brand);

  return (
    <div className="brand-card" data-testid={`brand-card-${brand.slug}`}>
      <h3 onClick={() => onSelect(brand.slug)}>{brand.brand}</h3>
      <span className={`status-badge status-${status.toLowerCase().replace(" ", "-")}`}>{status}</span>
      {brand.last_run?.status === "running" && (
        <RunProgress
          phase={brand.last_run.phase}
          done={brand.last_run.phase === "detail" ? brand.last_run.detail_enriched : brand.last_run.listings_seen}
          total={brand.last_run.phase === "detail" ? brand.last_run.detail_total : brand.last_run.search_total}
          etaSeconds={null}
          etaIsFallback={false}
        />
      )}
      {brand.last_run && (
        <ul>
          <li>Ultimo run: {new Date(brand.last_run.started_at).toLocaleString("it-IT")}</li>
          <li>Nuovi annunci: {brand.last_run.new_listings}</li>
          <li>Prezzi aggiornati: {brand.last_run.price_changes}</li>
          <li>Venduti rilevati: {brand.last_run.sold_detected}</li>
          <li>Errori: {brand.last_run.errors_count}</li>
        </ul>
      )}
      <div className="brand-card-actions">
        {brand.paused ? (
          <button onClick={() => onResume(brand.slug)}>Riprendi</button>
        ) : (
          <button onClick={() => onPause(brand.slug)}>Metti in pausa</button>
        )}
        <button onClick={() => onRunNow(brand.slug)}>Forza run ora</button>
      </div>
    </div>
  );
}
