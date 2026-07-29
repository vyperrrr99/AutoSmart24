#!/usr/bin/env bash
# Recovers Audi and Fiat, the two brands the 29/07 full pass lost to a network
# timeout — the known resilience defect where one timed-out page makes the
# worker pool discard every remaining job and fail the whole brand.
#
# Fiat had already seen 28,263 listings and found 1,183 new ones when it died,
# so the loss is entirely wasted work rather than missing coverage.
#
# Unlike the full-pass runner this retries a brand once before giving up. The
# failure it recovers from is transient by nature, and a second attempt costs
# nothing on the runs that succeed first time.
API=http://localhost:8001
QUEUE="audi fiat"
PSQL="sudo -n docker exec -i autosmart24-postgres-1 psql -U autosmart24 -tA -d autosmart24"

run_snapshot() {  # $1=brand -> "id|status" of the most recent run row, or "|"
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_r2_runs.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    r = json.load(open('/tmp/_r2_runs.json'))[0]
    print(f"{r['id']}|{r['status']}")
except Exception:
    print('|')
PY
}

progress() {
  curl -s -m 10 "$API/queue" 2>/dev/null > /tmp/_r2_queue.json
  python3 - <<'PY' 2>/dev/null
import json
try:
    c = json.load(open('/tmp/_r2_queue.json')).get('current')
    print(f"{c['phase']} {c['done']:,}" if c else '-')
except Exception: print('?')
PY
}

report_line() {
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_r2_fin.json
  BRAND="$1" python3 - <<'PY' 2>/dev/null
import json, os, datetime as dt
b = os.environ['BRAND']
try:
    r = json.load(open('/tmp/_r2_fin.json'))[0]
    t = lambda s: dt.datetime.fromisoformat(s) if s else None
    st, sf, fi = t(r['started_at']), t(r.get('search_finished_at')), t(r.get('finished_at'))
    f = lambda s: f"{int(s)//60}m{int(s)%60:02d}s" if s is not None else "-"
    sec = lambda a, b: (b-a).total_seconds() if (a and b) else None
    print(f"{b:8} {r['status']:8} tot {f(sec(st,fi)):>9} = ricerca {f(sec(st,sf)):>8} + dettaglio {f(sec(sf,fi)):>9} "
          f"| visti {r['listings_seen']:>6} nuovi {r['new_listings']:>5} venduti {r['sold_detected']:>4} err {r['errors_count']:>3}")
except Exception as e:
    print(f"{b:8} (report non disponibile: {e})")
PY
}

suspect_count() {
  $PSQL -c "SELECT count(*) FROM listings
            WHERE status='sold' AND sold_at IS NOT NULL
              AND sold_at > now() - interval '3 hours'
              AND sold_at - last_seen_at < interval '60 minutes';" 2>/dev/null | tr -d ' \n'
}

# Runs one brand to completion and echoes its final status.
run_once() {
  # Captured BEFORE the POST, on every call (including the retry attempt):
  # on the single-worker executor a requeued retry for another brand can
  # land ahead of this job, so runs[0] may still be the PREVIOUS attempt's
  # already-terminal row for a while. Without this, polling below would
  # read that stale status and return as if this attempt had finished.
  local prev_id cur_id status
  IFS='|' read -r prev_id _ <<< "$(run_snapshot "$1")"
  curl -s -m 15 -X POST "$API/brands/$1/run-now" >/dev/null 2>&1
  sleep 10
  while true; do
    IFS='|' read -r cur_id status <<< "$(run_snapshot "$1")"
    if [ -n "$cur_id" ] && [ "$cur_id" = "$prev_id" ]; then
      echo "   $1 · in coda, in attesa che la run parta (id precedente: $prev_id) · $(date '+%H:%M:%S')" >&2
      sleep 120
      continue
    fi
    case "$status" in
      success|error|blocked|partial) echo "$status"; return ;;
      "") echo "   $1 · API non raggiungibile · $(date '+%H:%M:%S')" >&2 ;;
      *)  echo "   $1 · $(progress) · $(date '+%H:%M:%S')" >&2 ;;
    esac
    sleep 120
  done
}

echo "=== recupero avviato $(date '+%d/%m %H:%M:%S') — $QUEUE ==="
for BRAND in $QUEUE; do
  echo "AVVIO $BRAND  $(date '+%H:%M:%S')"
  S=$(run_once "$BRAND")
  if [ "$S" = "error" ]; then
    echo "  primo tentativo fallito ($(report_line "$BRAND")) — riprovo una volta"
    S=$(run_once "$BRAND")
  fi
  echo "FINE  $(report_line "$BRAND")  $(date '+%H:%M:%S')"
  N=$(suspect_count)
  if [ "${N:-0}" != "0" ]; then
    echo "  !! $N vendite con la firma dei falsi positivi — mi fermo."
    exit 2
  fi
  echo "  regressione vendite: 0 sospetti"
  [ "$S" = "blocked" ] && { echo "!! BLOCCO. Riprendi con: curl -X POST $API/queue/resume"; exit 1; }
  [ "$S" = "error" ] && echo "  !! $BRAND fallita anche al secondo tentativo"
  # partial requeues itself — not the transient failure this script retries
  # for, but silent here would hide that the sold check never ran.
  [ "$S" = "partial" ] && echo "  ATTENZIONE: scansione incompleta, vendite non valutate per $BRAND"
done

echo
echo "=== RECUPERO CONCLUSO $(date '+%d/%m %H:%M:%S') ==="
$PSQL -c "SELECT 'annunci: '||count(*)||' · attivi: '||count(*) FILTER (WHERE status='active')
          ||' · venduti: '||count(*) FILTER (WHERE status='sold')
          ||' · da arricchire: '||count(*) FILTER (WHERE status='active' AND NOT detail_scraped)
          FROM listings;" 2>/dev/null
