#!/usr/bin/env bash
# Scheduled incremental update of the fifteen second-batch brands.
#
# Two things this run is meant to measure, neither of which the first pass
# could show:
#   1. how long a routine update takes, now that the backlog is already
#      enriched — the number that decides whether the schedule is daily or
#      every two or three days
#   2. whether sold-detection works: listings active in the database but no
#      longer on the site should be confirmed sold, not silently dropped
#
# The ten historical brands are deliberately excluded — the Windows machine
# owns those, and touching them here would collide with its work.
#
# Waits until the target time so every brand has a comparable gap since its
# last run (they finished between 0.5h and 20h apart).
API=http://localhost:8001
QUEUE="opel toyota nissan alfa-romeo hyundai land-rover kia skoda porsche cupra dacia mini mg volvo lancia"
TARGET_TIME="${1:-06:00}"

status_of() {
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_a_runs.json
  python3 - <<'PY' 2>/dev/null
import json
try: print(json.load(open('/tmp/_a_runs.json'))[0]['status'])
except Exception: print('')
PY
}

progress() {
  curl -s -m 10 "$API/queue" 2>/dev/null > /tmp/_a_queue.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    c = json.load(open('/tmp/_a_queue.json')).get('current')
    print(f"{c['phase']} {c['done']:,}" if c else '-')
except Exception: print('?')
PY
}

# Per-brand line for the comparison table: phase durations come from the run's
# own timestamps, so they are measured, not inferred from wall clock.
report_line() {
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_a_fin.json
  BRAND="$1" python3 - <<'PY' 2>/dev/null
import json, os, datetime as dt
b = os.environ['BRAND']
try:
    r = json.load(open('/tmp/_a_fin.json'))[0]
    def ts(s): return dt.datetime.fromisoformat(s) if s else None
    st, sf, fi = ts(r['started_at']), ts(r.get('search_finished_at')), ts(r.get('finished_at'))
    search = int((sf-st).total_seconds()) if sf else None
    detail = int((fi-sf).total_seconds()) if (sf and fi) else None
    total  = int((fi-st).total_seconds()) if fi else None
    fmt = lambda s: f"{s//60}m{s%60:02d}s" if s is not None else "-"
    print(f"{b:12} {r['status']:8} tot {fmt(total):>9} = ricerca {fmt(search):>8} + dettaglio {fmt(detail):>9} "
          f"| visti {r['listings_seen']:>6} nuovi {r['new_listings']:>5} venduti {r['sold_detected']:>4} err {r['errors_count']:>3}")
except Exception as e:
    print(f"{b:12} (report non disponibile: {e})")
PY
}

# --- wait for the target time -----------------------------------------------
# "now" starts as soon as the machine is free; anything else is an HH:MM to
# wait for (rolling to tomorrow if that time has already passed today).
if [ "$TARGET_TIME" = "now" ]; then
  echo "=== avvio immediato richiesto — attendo solo la fine della run in corso ==="
else
  NOW=$(date +%s)
  TARGET=$(date -d "today $TARGET_TIME" +%s)
  [ "$TARGET" -le "$NOW" ] && TARGET=$(date -d "tomorrow $TARGET_TIME" +%s)
  WAIT=$((TARGET - NOW))
  echo "=== programmato per $(date -d "@$TARGET" '+%d/%m %H:%M') — attendo $((WAIT/3600))h $(((WAIT%3600)/60))m ==="
  sleep "$WAIT"
fi

# Do not start on top of a run still in flight (e.g. a recovery that overran).
for _ in $(seq 1 60); do
  [ -z "$(curl -s -m 10 "$API/queue" 2>/dev/null | grep -o '"current":null')" ] || break
  sleep 60
done

echo "=== avvio aggiornamento $(date '+%d/%m %H:%M:%S') ==="
START_ALL=$(date +%s)

for BRAND in $QUEUE; do
  curl -s -m 15 -X POST "$API/brands/$BRAND/run-now" >/dev/null 2>&1
  echo "AVVIO $BRAND  $(date '+%H:%M:%S')"
  sleep 10
  while true; do
    S=$(status_of "$BRAND")
    case "$S" in
      success|error|blocked|partial)
        echo "FINE  $(report_line "$BRAND")  $(date '+%H:%M:%S')"
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

END_ALL=$(date +%s)
echo
echo "=== RIEPILOGO AGGIORNAMENTO $(date '+%d/%m %H:%M:%S') ==="
echo "durata totale del giro: $(( (END_ALL-START_ALL)/3600 ))h $(( ((END_ALL-START_ALL)%3600)/60 ))m"
echo
for BRAND in $QUEUE; do report_line "$BRAND"; done
echo
echo "--- totali ---"
curl -s -m 10 "$API/brands" 2>/dev/null > /tmp/_a_all.json
python3 - <<'PY' 2>/dev/null
import json
batch2 = {'opel','toyota','nissan','alfa-romeo','hyundai','land-rover','kia','skoda',
          'porsche','cupra','dacia','mini','mg','volvo','lancia'}
rows = [b for b in json.load(open('/tmp/_a_all.json')) if b['slug'] in batch2 and b.get('last_run')]
new  = sum(b['last_run']['new_listings']  for b in rows)
sold = sum(b['last_run']['sold_detected'] for b in rows)
seen = sum(b['last_run']['listings_seen'] for b in rows)
print(f"visti {seen:,} · nuovi {new:,} · venduti rilevati {sold:,}")
print()
print("Se 'venduti rilevati' è ~0 su tutte le marche, il meccanismo NON sta")
print("funzionando: in 15-30 ore una parte degli annunci sparisce sempre dal")
print("sito. Se invece è nell'ordine delle decine per marca, funziona.")
PY
