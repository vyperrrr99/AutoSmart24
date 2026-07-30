#!/usr/bin/env bash
# First full pass over all 25 brands from a single machine, and the field test
# of the sold-detection fix deployed on 29/07.
#
# Order: the fourteen second-batch brands still owed an update, then the ten
# that came back from the Windows worker, smallest first so the slow ones
# (Audi and Fiat, which arrive with ~13k listings still to enrich) land last
# and the early brands give a clean read on the update rate.
#
# All 26 brands. smart is last because it is new: its first sweep is a full
# ten-year collection from nothing, so it takes longer than an update and
# should not delay the brands whose figures we can compare against yesterday.
#
# Per brand this records what the old runner could not — whether any sale it
# declared carries the signature of the 28/07 incident, a listing marked sold
# less than an hour after the search results showed it alive. With the fix in
# place that count must stay at zero; anything else means the fix is leaking
# and the run should be stopped rather than left to fill the night.
API=http://localhost:8001
QUEUE="toyota nissan alfa-romeo hyundai land-rover kia skoda porsche cupra dacia mini mg volvo lancia opel \
       citroen renault jeep ford peugeot bmw mercedes-benz volkswagen audi fiat smart"
PSQL="sudo -n docker exec -i autosmart24-postgres-1 psql -U autosmart24 -tA -d autosmart24"

run_snapshot() {  # $1=brand -> "id|status" of the most recent run row, or "|"
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_c_runs.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    r = json.load(open('/tmp/_c_runs.json'))[0]
    print(f"{r['id']}|{r['status']}")
except Exception:
    print('|')
PY
}

progress() {
  curl -s -m 10 "$API/queue" 2>/dev/null > /tmp/_c_queue.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    c = json.load(open('/tmp/_c_queue.json')).get('current')
    print(f"{c['phase']} {c['done']:,}" if c else '-')
except Exception: print('?')
PY
}

report_line() {
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_c_fin.json
  BRAND="$1" python3 - <<'PY' 2>/dev/null
import json, os, datetime as dt
b = os.environ['BRAND']
try:
    r = json.load(open('/tmp/_c_fin.json'))[0]
    def ts(s): return dt.datetime.fromisoformat(s) if s else None
    st, sf, fi = ts(r['started_at']), ts(r.get('search_finished_at')), ts(r.get('finished_at'))
    f = lambda s: f"{int(s)//60}m{int(s)%60:02d}s" if s is not None else "-"
    sec = lambda a, b: (b-a).total_seconds() if (a and b) else None
    print(f"{b:14} {r['status']:8} tot {f(sec(st,fi)):>9} = ricerca {f(sec(st,sf)):>8} + dettaglio {f(sec(sf,fi)):>9} "
          f"| visti {r['listings_seen']:>6} nuovi {r['new_listings']:>5} venduti {r['sold_detected']:>4} err {r['errors_count']:>3}")
except Exception as e:
    print(f"{b:14} (report non disponibile: {e})")
PY
}

# The regression check. Counts sales this brand declared in the last two hours
# whose gap between last sighting and sale is under an hour — the pattern that
# produced 139 false sales for Lancia on 28/07.
suspect_count() {
  $PSQL -c "SELECT count(*) FROM listings
            WHERE status='sold' AND sold_at IS NOT NULL
              AND sold_at > now() - interval '2 hours'
              AND sold_at - last_seen_at < interval '60 minutes';" 2>/dev/null | tr -d ' \n'
}

# Reads the queue's halt flag. Waiting for a run that will never be created is
# the one way the wait loop below can hang forever: a halted queue makes
# _run_fn return before it writes any ScrapeRun row, so no new id ever appears.
queue_halted() {
  curl -s -m 10 "$API/queue" 2>/dev/null > /tmp/_c_halt.json
  python3 - <<'PY' 2>/dev/null
import json
try: print("yes" if json.load(open('/tmp/_c_halt.json')).get('halted') else "no")
except Exception: print("unknown")
PY
}

# Ceiling on the wait for a brand's run to start. A requeued retry ahead of us
# can legitimately take a couple of hours, so this is deliberately generous --
# it exists to guarantee the giro terminates, not to police the schedule.
MAX_WAITS=120   # x 120s = 4 hours

echo "=== giro completo avviato $(date '+%d/%m %H:%M:%S') — 26 marche ==="
echo "    fix vendite deployato, immagine verificata prima dell'avvio"
START_ALL=$(date +%s)
SUSPECT_TOTAL=0
SKIPPED_TOTAL=0

for BRAND in $QUEUE; do
  # Captured BEFORE the POST: on the single-worker executor a previous
  # brand's requeued retry can land ahead of this brand's job, so the run
  # row for THIS cycle may not exist yet for a while. Without this, polling
  # runs[0] below would read the PREVIOUS cycle's already-terminal status
  # (e.g. success) and print FINE for a brand that never actually started.
  IFS='|' read -r PREV_ID _ <<< "$(run_snapshot "$BRAND")"
  curl -s -m 15 -X POST "$API/brands/$BRAND/run-now" >/dev/null 2>&1
  echo "AVVIO $BRAND  $(date '+%H:%M:%S')"
  sleep 10
  WAITS=0
  while true; do
    IFS='|' read -r CUR_ID S <<< "$(run_snapshot "$BRAND")"
    if [ -n "$CUR_ID" ] && [ "$CUR_ID" = "$PREV_ID" ]; then
      # Still the previous cycle's run row -- this brand's job has not
      # started yet (queued behind a retry). Wait for a genuinely new run
      # id rather than trusting any status read off it.
      #
      # Bounded, because three situations produce a run row that never
      # arrives: a halted queue, a brand still held by the concurrency
      # guard, and a lost run-now POST. Unbounded, this loop would strand
      # an unattended overnight giro at 120s intervals forever -- worse
      # than the stale-status bug it replaced, which at least terminated.
      if [ "$(queue_halted)" = "yes" ]; then
        echo "  !! CODA FERMA — nessuna run partira'. Riprendi con: curl -X POST $API/queue/resume"
        exit 1
      fi
      WAITS=$((WAITS + 1))
      if [ "$WAITS" -ge "$MAX_WAITS" ]; then
        echo "  !! $BRAND · nessuna run avviata dopo $((MAX_WAITS * 120 / 3600))h — la salto e proseguo"
        SKIPPED_TOTAL=$((SKIPPED_TOTAL + 1))
        break
      fi
      echo "   $BRAND · in coda, in attesa che la run parta (id precedente: $PREV_ID) · $(date '+%H:%M:%S')"
      sleep 120
      continue
    fi
    case "$S" in
      success|error|blocked|partial)
        echo "FINE  $(report_line "$BRAND")  $(date '+%H:%M:%S')"
        N=$(suspect_count)
        if [ "${N:-0}" != "0" ]; then
          SUSPECT_TOTAL=$((SUSPECT_TOTAL + N))
          echo "  !! $N vendite con la firma dei falsi positivi — il fix non sta tenendo."
          echo "  !! Mi fermo qui invece di riempire la notte di dati sporchi."
          echo "  !! Ispeziona con: falsi-venduti (sold_at - last_seen_at < 60 min)"
          exit 2
        fi
        echo "  regressione vendite: 0 sospetti"
        [ "$S" = "blocked" ] && { echo "!! BLOCCO — mi fermo. Riprendi con: curl -X POST $API/queue/resume"; exit 1; }
        # partial requeues itself — not a fault to stop the giro for, but
        # silent here would mean nobody notices the sold check was skipped.
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
echo "=== RIEPILOGO $(date '+%d/%m %H:%M:%S') ==="
echo "durata del giro: $(( (END_ALL-START_ALL)/3600 ))h $(( ((END_ALL-START_ALL)%3600)/60 ))m"
echo "vendite sospette in tutto il giro: $SUSPECT_TOTAL (atteso: 0)"
echo "marche saltate perche' la run non e' mai partita: $SKIPPED_TOTAL (atteso: 0)"
echo
for BRAND in $QUEUE; do report_line "$BRAND"; done
echo
echo "--- totali ---"
$PSQL -c "SELECT 'annunci: '||count(*)||' · attivi: '||count(*) FILTER (WHERE status='active')
          ||' · venduti: '||count(*) FILTER (WHERE status='sold')
          ||' · da arricchire: '||count(*) FILTER (WHERE status='active' AND NOT detail_scraped)
          FROM listings;" 2>/dev/null
