#!/usr/bin/env bash
# Riavvia `app` per applicare la concorrenza del file compose, ma solo quando
# non sta girando nulla.
#
# Due verifiche a 90 secondi di distanza, non una: le marche si susseguono
# subito e un singolo controllo puo' cadere nell'istante fra una e l'altra.
# Il 22/08 un'attesa a controllo singolo e' scattata proprio in quella
# fessura, riavviando mentre il giro doveva continuare.
#
# Scadenza a 20 ore dall'avvio, non a un'ora del giorno.
#
# Correzione del 02/09/2026: prima si arrendeva alle 21:30 "per non interferire
# col giro delle 22:00", che presupponeva un giro finito da un pezzo. Con la
# finestra a 15 anni un giro dura 10-25 ore e la coda e' quasi sempre occupata:
# quella condizione poteva non verificarsi mai, e il riavvio non sarebbe mai
# avvenuto. E' la stessa famiglia di errore corretta lo stesso giorno nella
# riclassificazione notturna -- una soglia tarata su un mondo che poi si e'
# mosso.
#
# Un riavvio fra un giro e l'altro va benissimo: il waiter aspetta comunque
# che nessuna marca sia in corso, quindi non interrompe lavoro a meta'.
set -uo pipefail
cd /home/vperrone/AutoSmart24 || exit 1
PSQL="sudo -n docker exec -i autosmart24-postgres-1 psql -U autosmart24 -tA -d autosmart24"
SCADENZA=$(( $(date +%s) + 20*3600 ))

libera() {
  local n
  n=$($PSQL -c "SELECT count(*) FROM scrape_runs WHERE status='running';" 2>/dev/null | tr -d ' ')
  [ "$n" = "0" ]
}

while true; do
  if [ "$(date +%s)" -ge "$SCADENZA" ]; then
    echo "$(date '+%d/%m %H:%M') venti ore senza una finestra libera — rinuncio"
    exit 1
  fi
  if libera; then
    sleep 90
    if libera; then
      echo "$(date '+%d/%m %H:%M') coda ferma da 90s"
      # Prima la migrazione, poi il riavvio, e il riavvio SOLO se la migrazione
      # riesce. L'immagine nuova scrive colonne che il database potrebbe non
      # avere ancora: riavviare prima significherebbe uno scraper che va in
      # errore su ogni annuncio. Se la migrazione fallisce non si riavvia e
      # l'immagine vecchia continua a lavorare senza accorgersi di nulla.
      if ! sudo -n docker compose run --rm --no-deps -e PGOPTIONS="-c lock_timeout=4000" \
            app alembic upgrade head >/dev/null 2>&1; then
        echo "$(date '+%d/%m %H:%M') migrazione FALLITA — non riavvio, riprovo fra un minuto"
        sleep 60
        continue
      fi
      echo "$(date '+%d/%m %H:%M') migrazione applicata, riavvio"
      sudo -n docker compose up -d app >/dev/null 2>&1
      sleep 25
      C=$(sudo -n docker compose exec -T app printenv SCRAPE_CONCURRENCY 2>/dev/null | tr -d '\r\n')
      echo "$(date '+%d/%m %H:%M') concorrenza attiva: ${C:-?}"
      exit 0
    fi
  fi
  sleep 60
done
