import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchBrandEvents, fetchBrandMetrics, fetchBrandRuns } from "../api";
import { BrandMetrics } from "./BrandMetrics";
import { RunProgress } from "./RunProgress";
import type { EventOut, RunMetrics, RunOut } from "../types";

interface BrandDetailProps {
  brandSlug: string;
  onClose: () => void;
  pollIntervalMs?: number;
}

const DEFAULT_POLL_MS = 3000;

export function BrandDetail({ brandSlug, onClose, pollIntervalMs = DEFAULT_POLL_MS }: BrandDetailProps) {
  const [runs, setRuns] = useState<RunOut[]>([]);
  const [events, setEvents] = useState<EventOut[]>([]);
  const [metrics, setMetrics] = useState<RunMetrics[]>([]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const [nextRuns, nextEvents, nextMetrics] = await Promise.all([
        fetchBrandRuns(brandSlug),
        fetchBrandEvents(brandSlug),
        fetchBrandMetrics(brandSlug),
      ]);
      // The panel stays mounted across polls; drop late responses from a
      // previous brand so switching brands cannot show the wrong data.
      if (cancelled) return;
      setRuns(nextRuns);
      setEvents(nextEvents);
      setMetrics(nextMetrics);
    }

    load();
    const timer = setInterval(load, pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [brandSlug, pollIntervalMs]);

  const current = runs.find((run) => run.status === "running") ?? null;

  const chartData = [...runs].reverse().map((run) => ({
    date: new Date(run.started_at).toLocaleDateString("it-IT"),
    annunci: run.listings_seen,
    nuovi: run.new_listings,
    errori: run.errors_count,
  }));

  return (
    <div className="brand-detail" data-testid="brand-detail">
      <button onClick={onClose}>Chiudi</button>
      <h2>Dettaglio {brandSlug}</h2>

      {current && (
        <RunProgress
          phase={current.phase}
          done={current.phase === "detail" ? current.detail_enriched : current.listings_seen}
          total={current.phase === "detail" ? current.detail_total : current.search_total}
          etaSeconds={null}
          etaIsFallback={false}
        />
      )}

      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" />
          <YAxis />
          <Tooltip />
          <Line type="monotone" dataKey="annunci" stroke="#60a5fa" />
          <Line type="monotone" dataKey="nuovi" stroke="#34d399" />
          <Line type="monotone" dataKey="errori" stroke="#f87171" />
        </LineChart>
      </ResponsiveContainer>

      <h3>Calibrazione</h3>
      <BrandMetrics metrics={metrics} />

      <h3>Eventi</h3>
      <table>
        <thead>
          <tr><th>Livello</th><th>Messaggio</th><th>Quando</th></tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id} className={`event-${event.level}`}>
              <td>{event.level}</td>
              <td>{event.message}</td>
              <td>{new Date(event.created_at).toLocaleString("it-IT")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
