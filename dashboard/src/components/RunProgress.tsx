interface RunProgressProps {
  phase: string | null;
  done: number;
  total: number | null;
  etaSeconds: number | null;
  etaIsFallback: boolean;
}

const PHASE_LABELS: Record<string, string> = {
  search: "ricerca",
  detail: "dettaglio",
};

export function formatDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

export function RunProgress({ phase, done, total, etaSeconds, etaIsFallback }: RunProgressProps) {
  const label = phase ? PHASE_LABELS[phase] ?? phase : "in corso";
  // A null total is normal early in the search phase, before the crawler has
  // probed every model: show movement, not a made-up percentage.
  const percent = total && total > 0 ? Math.min(100, (done * 100) / total) : null;

  return (
    <div className="run-progress">
      <div className="run-progress-labels">
        <span className="run-progress-phase">{label}</span>
        {percent !== null ? (
          <span>{percent.toFixed(1).replace(".", ",")}%</span>
        ) : (
          <span>{done.toLocaleString("it-IT")} annunci</span>
        )}
        {etaSeconds !== null && <span>resta ~{formatDuration(etaSeconds)}</span>}
      </div>
      <div
        className="run-progress-track"
        data-testid="run-progress-bar"
        data-indeterminate={percent === null ? "true" : "false"}
      >
        <div className="run-progress-fill" style={{ width: percent === null ? "100%" : `${percent}%` }} />
      </div>
      {etaIsFallback && etaSeconds !== null && (
        <small className="run-progress-note">stima approssimativa (nessuno storico per questa marca)</small>
      )}
    </div>
  );
}
