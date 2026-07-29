#!/usr/bin/env bash
# Scraping sequenziale delle 10 nuove marche, con calibrazione dei tempi:
# separa la FASE RICERCA (crawl liste) dalla FASE DETTAGLIO (arricchimento).
API=http://localhost:8001
QUEUE="nissan alfa-romeo hyundai land-rover kia skoda porsche cupra dacia mini mg volvo lancia"

stats() {  # $1=brand -> status|enriched|pages|crawl_s|detail_s|last
  curl -s -m 10 "$API/brands/$1/runs" 2>/dev/null > /tmp/_runs.json
  curl -s -m 10 "$API/brands/$1/events" 2>/dev/null > /tmp/_evs.json
  python3 - "$1" <<'PY'
import json,sys,re,datetime as dt
def ts(s): return dt.datetime.fromisoformat(s) if s else None
try: runs=json.load(open('/tmp/_runs.json'))
except Exception: runs=[]
if not runs: print("pending|0|0|0|0|-"); sys.exit()
r=runs[0]
try: evs=[e for e in json.load(open('/tmp/_evs.json')) if e.get('run_id')==r['id']]
except Exception: evs=[]
bl=[e for e in evs if 'Detail backlog page' in e.get('message','')]
enr=sum(int(m.group(1)) for e in bl if (m:=re.search(r'enriched (\d+)',e['message'])))
start=ts(r['started_at']); end=ts(r.get('finished_at'))
now=dt.datetime.utcnow()
first_bl=ts(bl[-1]['created_at']) if bl else None   # eventi ordinati desc
last_bl=ts(bl[0]['created_at']) if bl else None
crawl=int((first_bl-start).total_seconds()) if first_bl else int(((end or now)-start).total_seconds())
detail=int(((end or last_bl or now)-first_bl).total_seconds()) if first_bl else 0
last=(bl[0]['created_at'][11:19]+' '+bl[0]['message'][:52]) if bl else 'fase ricerca in corso'
print(f"{r['status']}|{enr}|{len(bl)}|{crawl}|{detail}|{last}")
PY
}

fmt() { printf '%dm%02ds' $(( $1/60 )) $(( $1%60 )); }

for BRAND in $QUEUE; do
  [ "$BRAND" = "nissan" ] || curl -s -m 15 -X POST "$API/brands/$BRAND/run-now" >/dev/null 2>&1 || true
  echo "▶ AVVIO $BRAND"
  sleep 8
  prev_enr=0; prev_status=""; tick=0
  while true; do
    IFS='|' read -r status enr pages crawl detail last <<EOF
$(stats "$BRAND")
EOF
    tick=$((tick+1))
    if [ "$status" != "$prev_status" ] || [ $(( ${enr:-0} - prev_enr )) -ge 500 ] || [ $((tick % 8)) -eq 0 ]; then
      echo "[$BRAND] $status · ricerca $(fmt ${crawl:-0}) · dettaglio $(fmt ${detail:-0}) · arricchiti ${enr:-0} (${pages:-0}p) · ${last}"
      prev_enr=${enr:-0}
    fi
    prev_status="$status"
    case "$status" in
      success|error|blocked|partial)
        fin=$(python3 - <<PY
import json
r=json.load(open('/tmp/_runs.json'))[0]
print(f"seen={r['listings_seen']} new={r['new_listings']} price={r['price_changes']} sold={r['sold_detected']} err={r['errors_count']}")
PY
)
        tot=$(( ${crawl:-0} + ${detail:-0} ))
        rate_c=$(python3 -c "print(f'{(${enr:-0} or 0)}')" 2>/dev/null)
        echo "■ FINE $BRAND → $status · TOTALE $(fmt $tot) [ricerca $(fmt ${crawl:-0}) + dettaglio $(fmt ${detail:-0})] · $fin · arricchiti ${enr:-0}"
        # partial requeues itself -- not the fault run-completo.sh stops the
        # giro for, but silent here would hide that the sold check never ran.
        [ "$status" = "partial" ] && echo "  ATTENZIONE: scansione incompleta, vendite non valutate per $BRAND"
        break ;;
    esac
    sleep 30
  done
done
echo "✔ BATCH COMPLETATO: 10 nuove marche — totale tracciate 20"
