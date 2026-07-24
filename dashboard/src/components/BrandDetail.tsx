import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchBrandEvents, fetchBrandRuns } from "../api";
import type { EventOut, RunOut } from "../types";

interface BrandDetailProps {
  brandSlug: string;
  onClose: () => void;
}

export function BrandDetail({ brandSlug, onClose }: BrandDetailProps) {
  const [runs, setRuns] = useState<RunOut[]>([]);
  const [events, setEvents] = useState<EventOut[]>([]);

  useEffect(() => {
    fetchBrandRuns(brandSlug).then(setRuns);
    fetchBrandEvents(brandSlug).then(setEvents);
  }, [brandSlug]);

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

      <table>
        <thead>
          <tr>
            <th>Livello</th>
            <th>Messaggio</th>
            <th>Quando</th>
          </tr>
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
