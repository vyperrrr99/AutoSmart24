#!/usr/bin/env bash
# Mette in pausa le marche che tocca all'ALTRA macchina, e riattiva le proprie.
#
#   bash scripts/applica-divisione.sh thinkpad
#   bash scripts/applica-divisione.sh windows
#
# Da eseguire su ciascuna macchina con il PROPRIO nome. Il database e' condiviso
# ma la pausa e' un dato di configurazione per marca, quindi l'ultima esecuzione
# vince: eseguirlo con il nome sbagliato spegne le marche di chi lo lancia e
# accende quelle dell'altro. Per questo il nome e' obbligatorio e non ha un
# valore predefinito.
#
# Le due macchine DEVONO lavorare su marche disgiunte: BrandRunGuard e
# QueueController vivono nella memoria del processo e non si coordinano fra
# host. Sulla stessa marca raddoppierebbero la frequenza di richieste proprio
# dove ci hanno gia' bloccati.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

IO="${1:-}"
case "$IO" in
  thinkpad|windows) ;;
  *) echo "uso: $0 thinkpad|windows"; exit 2 ;;
esac
ALTRA=$([ "$IO" = "thinkpad" ] && echo windows || echo thinkpad)

API="${AUTOSMART24_API:-http://localhost:8001}"
CONF=config/marche-per-macchina.yaml
[ -f "$CONF" ] || { echo "manca $CONF"; exit 1; }

leggi() { python3 -c "
import yaml, sys
print(' '.join(yaml.safe_load(open('$CONF'))['$1']))"; }

MIE=$(leggi "$IO")
LORO=$(leggi "$ALTRA")
[ -z "$MIE" ] && { echo "nessuna marca per '$IO' nel file"; exit 1; }

if ! curl -s -m 10 "$API/brands" >/dev/null 2>&1; then
  echo "API non raggiungibile su $API — il contenitore e' avviato?"; exit 1
fi

echo "=== $(date '+%d/%m %H:%M') · questa macchina e' '$IO' ==="
N=0
for M in $LORO; do
  curl -s -m 15 -X POST "$API/brands/$M/pause" >/dev/null 2>&1 && N=$((N+1))
done
echo "  messe in pausa $N marche di '$ALTRA'"
N=0
for M in $MIE; do
  curl -s -m 15 -X POST "$API/brands/$M/resume" >/dev/null 2>&1 && N=$((N+1))
done
echo "  attivate $N marche mie"

# Verifica letta dall'API, non dedotta dai comandi andati a buon fine.
curl -s -m 15 "$API/brands" | python3 -c "
import sys, json
b = json.load(sys.stdin)
attive = sorted(x['slug'] for x in b if not x.get('paused'))
attese = sorted('''$MIE'''.split())
print('  attive adesso:', ' '.join(attive))
if attive != attese:
    print('  ATTENZIONE: non corrispondono a quelle attese per questa macchina')
    print('  attese:', ' '.join(attese))
    raise SystemExit(1)
print('  corrispondono alla divisione: corretto')"
