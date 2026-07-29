#!/usr/bin/env bash
# Overnight queue runner, detached from any terminal session.
#
# Launched with setsid so it survives closing Claude Code, the terminal, or
# logging out. Whatever brand is already running when this starts is waited on
# rather than started again.
#
# Follow along with:  tail -f ~/AutoSmart24/run-notte.log
#
# Python snippets use quoted heredocs, not `python3 -c '...'`: escaped quotes
# inside an f-string do not survive bash's quoting and silently produce a
# SyntaxError, which would leave the log full of blank fields exactly when it
# is needed.
API=http://localhost:8001
QUEUE="hyundai land-rover kia skoda porsche cupra dacia mini mg volvo lancia"
ALREADY_RUNNING="hyundai"

status_of() {
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_runs.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    print(json.load(open('/tmp/_runs.json'))[0]['status'])
except Exception:
    print('')
PY
}

progress() {
  curl -s -m 10 "$API/queue" 2>/dev/null > /tmp/_queue.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    c = json.load(open('/tmp/_queue.json')).get('current')
    print(f"{c['phase']} {c['done']:,}" if c else '-')
except Exception:
    print('?')
PY
}

final_stats() {
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_fin.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    r = json.load(open('/tmp/_fin.json'))[0]
    print(f"seen={r['listings_seen']} new={r['new_listings']} sold={r['sold_detected']} err={r['errors_count']}")
except Exception:
    print('(statistiche non disponibili)')
PY
}

echo "=== avvio $(date '+%H:%M:%S') — coda: $QUEUE ==="

for BRAND in $QUEUE; do
  if [ "$BRAND" != "$ALREADY_RUNNING" ]; then
    curl -s -m 15 -X POST "$API/brands/$BRAND/run-now" >/dev/null 2>&1
    echo "AVVIO $BRAND  $(date '+%H:%M:%S')"
    sleep 10
  else
    echo "$BRAND già in corso, la seguo  $(date '+%H:%M:%S')"
  fi

  while true; do
    S=$(status_of "$BRAND")
    case "$S" in
      success|error|blocked|partial)
        echo "FINE $BRAND -> $S · $(final_stats "$BRAND") · $(date '+%H:%M:%S')"
        # A block means the site is refusing us; pushing on would deepen it.
        if [ "$S" = "blocked" ]; then
          echo "!! BLOCCO RILEVATO — mi fermo qui."
          echo "!! Aspetta almeno un'ora, poi riprendi con:"
          echo "!!   curl -X POST $API/queue/resume"
          echo "!! e rilancia questo script."
          exit 1
        fi
        # partial requeues itself once at the back of the current giro's
        # queue -- not a fault to stop this detached run for, but silent
        # here would hide that the sold check was skipped for this brand.
        if [ "$S" = "partial" ]; then
          echo "!! ATTENZIONE: scansione incompleta, vendite non valutate per $BRAND"
        fi
        break ;;
      "")
        echo "   $BRAND · API non raggiungibile, riprovo · $(date '+%H:%M:%S')" ;;
      *)
        echo "   $BRAND · $(progress) · $(date '+%H:%M:%S')" ;;
    esac
    sleep 300
  done
done

echo "=== COMPLETATO $(date '+%H:%M:%S') — tutte le marche della coda elaborate ==="
