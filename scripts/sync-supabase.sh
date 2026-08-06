#!/usr/bin/env bash
# Publishes the scraper's data to Supabase, where the BI application reads it.
#
# One way only. The scraper owns these tables; nothing on Supabase writes back.
#
# Runs after the nightly sweep rather than on a fixed clock: a copy taken
# mid-sweep captures a moving target, with some brands refreshed and others
# still holding yesterday's rows -- and nothing downstream could tell which.
# So this waits for the queue to be free before it starts.
#
# The load is a single transaction: TRUNCATE and reload together, so the BI
# either sees yesterday's complete data or today's, never a half-filled table.
# A failure part-way leaves the previous copy untouched.
set -uo pipefail
cd /home/vperrone/AutoSmart24

API=http://localhost:8001
ENVFILE=.env.supabase
TABLES="-t listings -t price_history -t dealers -t brand_catalog -t tracked_brands"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

[ -f "$ENVFILE" ] || { echo "manca $ENVFILE"; exit 1; }
URL=$(sed 's/^SUPABASE_URL=//' "$ENVFILE")

# Waiting for the sweep and reclassifying now live in one place, called from
# here and from cron independently. Reclassification is not a publication step
# -- it is maintenance of the source database, and it must keep running when
# publication does not, which is exactly what happened on 06/08/2026.
#
# Reclassify BEFORE publishing. A disappearance that is not a sale must never
# reach the BI as one: once published, a fabricated sale is indistinguishable
# from a real one downstream. If this fails, nothing is published -- yesterday's
# copy is better than today's with invented sales in it.
if ! bash "$PWD/scripts/riclassifica-notturna.sh"; then
  echo "  riclassificazione FALLITA — non pubblico"
  exit 1
fi


# Only data travels, never schema. So a column added here and not there makes
# the COPY fail inside the transaction -- safe, but with an error that says
# nothing about the cause. Checked first, so the message names the problem.
COLS_LOCAL=$(sudo -n docker exec -i autosmart24-postgres-1 psql -U autosmart24 -tAc \
  "SELECT count(*) FROM information_schema.columns WHERE table_name='listings';" -d autosmart24 2>/dev/null | tr -d ' ')
COLS_REMOTE=$(sudo -n docker run --rm --network host -e PGCONNECT_TIMEOUT=20 -e U="$URL" postgres:17 \
  sh -c "psql \"\$U\" -tAc \"SELECT count(*) FROM information_schema.columns WHERE table_name='listings';\"" 2>/dev/null | tr -d ' \r')
if [ "$COLS_LOCAL" != "$COLS_REMOTE" ]; then
  echo "  SCHEMI DIVERSI: listings ha $COLS_LOCAL colonne qui e $COLS_REMOTE su Supabase."
  echo "  Applica la migrazione anche là prima di pubblicare — non tocco nulla."
  exit 1
fi

sudo -n docker exec -i autosmart24-postgres-1 pg_dump -U autosmart24 -d autosmart24 \
  --data-only --no-owner $TABLES > "$WORK/dati.sql" 2>/dev/null
[ -s "$WORK/dati.sql" ] || { echo "estrazione vuota — interrompo senza toccare Supabase"; exit 1; }
echo "  estratti $(du -h "$WORK/dati.sql" | cut -f1)"

{
  echo "TRUNCATE price_history, listings, dealers, brand_catalog, tracked_brands;"
  cat "$WORK/dati.sql"
} | sudo -n docker run --rm -i --network host -e PGCONNECT_TIMEOUT=30 -e U="$URL" postgres:17 \
      sh -c 'psql "$U" -v ON_ERROR_STOP=1 --single-transaction -q' 2>&1 | tail -3

# Verify against the source rather than trusting the exit code: a COPY that
# loaded a truncated stream can still commit.
LOCAL=$(sudo -n docker exec -i autosmart24-postgres-1 psql -U autosmart24 -tAc \
  "select count(*) from listings;" -d autosmart24 2>/dev/null | tr -d ' ')
REMOTE=$(sudo -n docker run --rm --network host -e PGCONNECT_TIMEOUT=20 -e U="$URL" postgres:17 \
  sh -c 'psql "$U" -tAc "select count(*) from listings;"' 2>/dev/null | tr -d ' \r')

echo "  locale $LOCAL · Supabase $REMOTE"
if [ "$LOCAL" != "$REMOTE" ]; then
  echo "  !! DISALLINEATI — la copia non riflette la sorgente"
  exit 2
fi
echo "=== conclusa $(date '+%H:%M:%S') ==="
