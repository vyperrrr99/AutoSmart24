#!/usr/bin/env bash
# Monitor avanzamento scraping basato sugli EVENTI (non su listings_seen, che
# resta 0 fino a fine run). Elabora una coda di marche in sequenza.
API=http://localhost:8001
QUEUE="citroen renault"

progress() {  # $1=brand -> "status|enriched|pages|lastmsg|blocked|errors"
  local st ev
  st=$(curl -s -m 8 "$API/brands/$1/runs" 2>/dev/null | python3 -c "
import sys,json
try: print(json.load(sys.stdin)[0]['status'])
except Exception: print('pending')" 2>/dev/null)
  curl -s -m 8 "$API/brands/$1/events" 2>/dev/null | python3 -c "
import sys,json,re
st='''$st'''
try: evs=json.load(sys.stdin)
except Exception: evs=[]
enr=0; pages=0; blk=0; err=0
for e in evs:
    m=re.search(r'enriched (\d+)', e.get('message',''))
    if m: enr+=int(m.group(1)); pages+=1
    lv=e.get('level')
    if lv=='blocked': blk+=1
    if lv=='error': err+=1
last=evs[0]['created_at'][11:19]+' '+evs[0]['message'][:70] if evs else '-'
print(f\"{st}|{enr}|{pages}|{last}|{blk}|{err}\")
" 2>/dev/null
}

for BRAND in $QUEUE; do
  if [ "$BRAND" != "citroen" ]; then
    curl -s -m 10 -X POST "$API/brands/$BRAND/run-now" >/dev/null 2>&1 || true
    echo "▶ AVVIO $BRAND"; sleep 6
  fi
  prev_enr=-1; prev_status=""; tick=0
  while true; do
    IFS='|' read -r status enr pages last blk err <<EOF
$(progress "$BRAND")
EOF
    tick=$((tick+1))
    # emetti su: cambio stato, +200 arricchiti, evento blocked/error, o heartbeat 3min
    if [ "$status" != "$prev_status" ] || [ "${blk:-0}" -gt 0 ] || [ "${err:-0}" -gt 0 ] \
       || [ $(( ${enr:-0} - (prev_enr<0?0:prev_enr) )) -ge 200 ] || [ $((tick % 6)) -eq 0 ]; then
      echo "[$BRAND] $status · arricchiti≈${enr:-0} (${pages:-0} pagine) · blk=${blk:-0} err=${err:-0} · ultimo: ${last:-'-'}"
      prev_enr=${enr:-0}
    fi
    prev_status="$status"
    case "$status" in
      success|error|blocked|partial)
        # riporta i contatori finali reali della run
        fin=$(curl -s -m 8 "$API/brands/$BRAND/runs" 2>/dev/null | python3 -c "
import sys,json
r=json.load(sys.stdin)[0]
print(f\"seen={r['listings_seen']} new={r['new_listings']} price={r['price_changes']} sold={r['sold_detected']} err={r['errors_count']}\")" 2>/dev/null)
        echo "■ FINE $BRAND → $status · $fin"
        # partial requeues itself -- not a fault to stop for, but silent
        # here would hide that the sold check never ran for this brand.
        [ "$status" = "partial" ] && echo "  ATTENZIONE: scansione incompleta, vendite non valutate per $BRAND"
        break ;;
    esac
    sleep 30
  done
done
echo "✔ Coda completata (citroen + renault) — le altre 8 marche erano già arricchite (last_run=success)"
