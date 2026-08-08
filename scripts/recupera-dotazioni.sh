#!/usr/bin/env bash
# Un blocco di recupero dotazioni. Lanciato ogni ora dalle 10:00 alle 20:00.
#
# Lavoro una tantum: riempie equipment e vernice sulle auto lette prima che le
# colonne esistessero. Quelle non tornerebbero mai nella coda notturna, che
# seleziona solo detail_scraped = false.
#
# Tutte le decisioni di sicurezza stanno nello script Python -- finestra
# oraria, scansione in corso, scadenza, blocco del sito -- perche' li' sono
# verificabili. Qui c'e' solo l'avvio.
set -uo pipefail
cd /home/vperrone/AutoSmart24 || exit 1

# L'output si cattura e poi si filtra: `comando | grep` restituisce lo stato
# di grep, quindi un fallimento del recupero sembrerebbe riuscito nel log.
if ! OUT=$(sudo -n docker compose run --rm --no-deps \
      -v "$PWD/scripts:/scripts" -v "$PWD/stato:/app/stato" \
      app python /scripts/arricchisci-dotazioni.py "$@" 2>&1); then
  echo "$(date '+%d/%m %H:%M') BLOCCO FALLITO"
  echo "$OUT" | grep -vE 'DeprecationWarning|warnings.warn' | tail -8
  exit 1
fi
echo "$OUT" | grep -vE 'DeprecationWarning|warnings.warn|^\s*$'
