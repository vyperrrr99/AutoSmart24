import { RunProgress, formatDuration } from "./RunProgress";
import type { QueueOut } from "../types";

interface QueuePanelProps {
  queue: QueueOut | null;
  onResume: () => void;
}

export function QueuePanel({ queue, onResume }: QueuePanelProps) {
  if (queue === null) return null;

  if (queue.halted) {
    const at = queue.halted_at ? new Date(queue.halted_at).toLocaleTimeString("it-IT") : null;
    return (
      <section className="queue-panel queue-panel-halted">
        <div role="alert">
          <strong>Coda ferma</strong>
          {queue.halted_reason && <span>: {queue.halted_reason}</span>}
          {at && <span> (alle {at})</span>}
        </div>
        <button onClick={onResume}>Riprendi coda</button>
      </section>
    );
  }

  if (queue.current === null) {
    return (
      <section className="queue-panel">
        <span>Nessuna scansione in corso.</span>
        {queue.pending.length > 0 && <span> {queue.pending.length} marche in coda.</span>}
      </section>
    );
  }

  return (
    <section className="queue-panel">
      <div className="queue-panel-current">
        <strong>In esecuzione: {queue.current.brand}</strong>
        <RunProgress
          phase={queue.current.phase}
          done={queue.current.done}
          total={queue.current.total}
          etaSeconds={queue.current.eta_seconds}
          etaIsFallback={queue.current.eta_is_fallback}
        />
      </div>
      {queue.pending.length > 0 && (
        <div className="queue-panel-pending">
          {queue.pending.length} marche in attesa
          {queue.total_eta_seconds !== null && <> — totale ~{formatDuration(queue.total_eta_seconds)}</>}
        </div>
      )}
    </section>
  );
}
