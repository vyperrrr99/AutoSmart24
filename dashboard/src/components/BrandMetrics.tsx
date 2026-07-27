import { formatDuration } from "./RunProgress";
import type { RunMetrics } from "../types";

interface BrandMetricsProps {
  metrics: RunMetrics[];
}

function rate(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)}/min`;
}

function duration(value: number | null): string {
  return value === null ? "—" : formatDuration(value);
}

export function BrandMetrics({ metrics }: BrandMetricsProps) {
  if (metrics.length === 0) {
    return <p className="brand-metrics-empty">Nessuna run conclusa: le metriche compaiono al primo giro completato.</p>;
  }

  return (
    <table className="brand-metrics">
      <thead>
        <tr>
          <th>Run</th><th>Stato</th>
          <th>Ricerca</th><th>Vel. ricerca</th>
          <th>Dettaglio</th><th>Vel. dettaglio</th>
        </tr>
      </thead>
      <tbody>
        {metrics.map((m) => (
          <tr key={m.run_id}>
            <td>{new Date(m.started_at).toLocaleString("it-IT")}</td>
            <td>{m.status}</td>
            <td>{duration(m.search_seconds)}</td>
            <td>{rate(m.search_rate_per_min)}</td>
            <td>{duration(m.detail_seconds)}</td>
            <td>{rate(m.detail_rate_per_min)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
