#!/usr/bin/env bash
# Recovers the two brands that failed with a network timeout overnight.
#
# MINI got as far as 5,988 listings with 5,499 enriched before timing out, so
# its rerun only has to drain the ~488 left in the detail backlog. MG timed out
# before inserting anything and starts from scratch.
#
# Detached with setsid, same as the overnight runner.
API=http://localhost:8001
QUEUE="mini mg"

status_of() {
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_r_runs.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    print(json.load(open('/tmp/_r_runs.json'))[0]['status'])
except Exception:
    print('')
PY
}

progress() {
  curl -s -m 10 "$API/queue" 2>/dev/null > /tmp/_r_queue.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    c = json.load(open('/tmp/_r_queue.json')).get('current')
    print(f"{c['phase']} {c['done']:,}" if c else '-')
except Exception:
    print('?')
PY
}

final_stats() {
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_r_fin.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    r = json.load(open('/tmp/_r_fin.json'))[0]
    print(f"seen={r['listings_seen']} new={r['new_listings']} sold={r['sold_detected']} err={r['errors_count']}")
except Exception:
    print('(statistiche non disponibili)')
PY
}

echo "=== recupero avviato $(date '+%H:%M:%S') — marche: $QUEUE ==="

for BRAND in $QUEUE; do
  curl -s -m 15 -X POST "$API/brands/$BRAND/run-now" >/dev/null 2>&1
  echo "AVVIO $BRAND  $(date '+%H:%M:%S')"
  sleep 10
  while true; do
    S=$(status_of "$BRAND")
    case "$S" in
      success|error|blocked|partial)
        echo "FINE $BRAND -> $S · $(final_stats "$BRAND") · $(date '+%H:%M:%S')"
        [ "$S" = "blocked" ] && { echo "!! BLOCCO — mi fermo. Riprendi con: curl -X POST $API/queue/resume"; exit 1; }
        # partial requeues itself once at the back of the current giro's
        # queue -- not a fault to stop this detached run for, but silent
        # here would hide that the sold check was skipped for this brand.
        [ "$S" = "partial" ] && echo "  ATTENZIONE: scansione incompleta, vendite non valutate per $BRAND"
        break ;;
      "") echo "   $BRAND · API non raggiungibile · $(date '+%H:%M:%S')" ;;
      *)  echo "   $BRAND · $(progress) · $(date '+%H:%M:%S')" ;;
    esac
    sleep 120
  done
done

echo "=== RECUPERO COMPLETATO $(date '+%H:%M:%S') ==="
