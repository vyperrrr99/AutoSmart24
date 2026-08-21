#!/usr/bin/env bash
# Passaggio a due macchine, da eseguire sul ThinkPad quando la coda e' libera.
#
# Fa tre cose che insieme non si possono fare a meta':
#   1. allarga a 15 anni TUTTE le marche che ancora non lo sono
#   2. riporta la concorrenza a 8 (riavvia il contenitore)
#   3. applica la divisione: attiva le marche del ThinkPad, mette in pausa le altre
#
# Perche' tutte insieme e non piu' a scaglioni: con due macchine e concorrenza 8
# ciascuna ha circa cinque ore di margine a notte, e il tetto di 2.000 pagine
# per giro limita comunque ogni marca. Gli scaglioni servivano a proteggere una
# macchina sola a concorrenza 5, dove il margine era di 84 minuti.
#
# Non parte se una scansione e' in corso: riavviare il contenitore a meta' giro
# lascia una riga `running` orfana e perde il lavoro della marca in corso.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

API=http://localhost:8001
PSQL="sudo -n docker exec -i autosmart24-postgres-1 psql -U autosmart24 -tA -d autosmart24"

IN_CORSO=$($PSQL -c "SELECT count(*) FROM scrape_runs WHERE status='running' AND started_at >= now() - interval '12 hours';" | tr -d ' ')
if [ -z "$IN_CORSO" ]; then echo "database non raggiungibile"; exit 1; fi
if [ "$IN_CORSO" != "0" ]; then
  MARCA=$($PSQL -c "SELECT brand FROM scrape_runs WHERE status='running' ORDER BY started_at DESC LIMIT 1;" | tr -d ' ')
  echo "scansione in corso ($MARCA) — aspetta che finisca, poi rilancia"
  exit 1
fi

echo "=== $(date '+%d/%m %H:%M') passaggio a due macchine ==="

PRIMA=$($PSQL -c "SELECT count(*) FROM tracked_brands WHERE year_from_years < 15;" | tr -d ' ')
$PSQL -c "UPDATE tracked_brands SET year_from_years = 15 WHERE year_from_years < 15;" >/dev/null
echo "  allargate a 15 anni: $PRIMA marche (ora tutte e $($PSQL -c 'SELECT count(*) FROM tracked_brands;' | tr -d ' '))"

echo "  riavvio con la concorrenza del file compose..."
sudo -n docker compose up -d app >/dev/null 2>&1
for i in $(seq 1 20); do
  curl -s -m 5 "$API/brands" >/dev/null 2>&1 && break
  sleep 3
done
CONC=$(sudo -n docker compose exec -T app printenv SCRAPE_CONCURRENCY 2>/dev/null | tr -d '\r\n')
echo "  concorrenza attiva: ${CONC:-?}"

bash scripts/applica-divisione.sh thinkpad
