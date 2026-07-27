#!/usr/bin/env bash
# ONE-OFF: merge the second machine's work back into this database.
#
# After this runs, the second machine has served its purpose. This Linux
# machine goes back to owning all 25 brands and the worker is retired — do not
# keep the two databases running in parallel, because nothing coordinates them.
#
# Usage:
#   bash scripts/merge-from-worker.sh /path/to/worker.dump
#
# The dump is the worker's whole database (pg_dump -Fc). Only the ten
# historical brands are taken from it; everything this machine scraped is left
# untouched.
set -uo pipefail
cd "$(dirname "$0")/.."

DUMP="${1:?usage: merge-from-worker.sh <worker.dump>}"
[ -f "$DUMP" ] || { echo "dump not found: $DUMP"; exit 1; }

PSQL="sudo -n docker compose exec -T postgres psql -U autosmart24 -v ON_ERROR_STOP=1"
DB=autosmart24

echo "### 1/5  safety checks"
RUNNING=$($PSQL -d $DB -tAc "SELECT count(*) FROM scrape_runs WHERE status='running';" 2>/dev/null | tr -d ' ')
if [ "${RUNNING:-1}" != "0" ]; then
  echo "  A scrape is still running. Let it finish first — merging mid-sweep would"
  echo "  delete rows the running job is about to write. Aborting."
  exit 1
fi
echo "  no run in flight"

echo "### 2/5  backing up this database first"
TS=$(date +%Y%m%d-%H%M)
sudo -n docker compose exec -T postgres pg_dump -U autosmart24 -Fc $DB > "pre-merge-$TS.dump" 2>/dev/null
echo "  wrote pre-merge-$TS.dump ($(du -h "pre-merge-$TS.dump" | cut -f1)) — restore point if anything goes wrong"

echo "### 3/5  loading the worker dump into a staging schema"
$PSQL -d $DB -c "DROP SCHEMA IF EXISTS staging CASCADE; CREATE SCHEMA staging;" >/dev/null 2>&1
# --no-owner keeps the restore independent of role names on the other machine.
sudo -n docker compose exec -T postgres pg_restore -U autosmart24 -d $DB \
  --no-owner --no-privileges --schema=public --data-only < "$DUMP" 2>/dev/null && true
# pg_restore cannot retarget a schema, so the tables are recreated under
# staging and filled from a plain-text export of the dump instead.
sudo -n docker compose exec -T postgres pg_restore --no-owner --no-privileges -f - < "$DUMP" 2>/dev/null \
  | sed -e 's/^CREATE TABLE public\./CREATE TABLE staging./' \
        -e 's/^COPY public\./COPY staging./' \
        -e 's/^ALTER TABLE ONLY public\./ALTER TABLE ONLY staging./' \
  | $PSQL -d $DB >/dev/null 2>&1
STAGED=$($PSQL -d $DB -tAc "SELECT count(*) FROM staging.listings;" 2>/dev/null | tr -d ' ')
echo "  staged $STAGED listings from the worker"
[ "${STAGED:-0}" -gt 0 ] || { echo "  staging is empty — the dump did not load. Aborting."; exit 1; }

echo "### 4/5  merging (single transaction; nothing is left half-applied)"
OUT=$( { echo "BEGIN;"; cat scripts/merge-from-worker.sql; echo "COMMIT;"; } | $PSQL -d $DB 2>&1 )
if echo "$OUT" | grep -qi "error"; then
  echo "  MERGE FAILED — the transaction rolled back, the database is unchanged:"
  echo "$OUT" | grep -i error | head -5 | sed 's/^/    /'
  echo "  (restore point: pre-merge-$TS.dump)"
  exit 1
fi
echo "  merged"

echo "### 5/5  verifying"
$PSQL -d $DB -tAc "
SELECT '  '||brand||': '||count(*)||' listings, '||count(*) FILTER (WHERE detail_scraped)||' enriched'
FROM listings GROUP BY brand ORDER BY brand;" 2>/dev/null
echo "  --- integrity ---"
ORPHAN_EV=$($PSQL -d $DB -tAc "SELECT count(*) FROM scrape_events e LEFT JOIN scrape_runs r ON r.id=e.run_id WHERE e.run_id IS NOT NULL AND r.id IS NULL;" 2>/dev/null | tr -d ' ')
ORPHAN_PH=$($PSQL -d $DB -tAc "SELECT count(*) FROM price_history ph LEFT JOIN listings l ON l.id=ph.listing_id WHERE l.id IS NULL;" 2>/dev/null | tr -d ' ')
echo "  orphaned events: ${ORPHAN_EV:-?} (must be 0)"
echo "  orphaned price history: ${ORPHAN_PH:-?} (must be 0)"

$PSQL -d $DB -c "DROP SCHEMA IF EXISTS staging CASCADE;" >/dev/null 2>&1
echo "  staging dropped"

echo
echo "### done"
echo "Next, since this was a one-off:"
echo "  1. Stop the worker on the second machine — nothing coordinates two writers."
echo "  2. This machine now owns all 25 brands again."
echo "  3. To resume nightly scraping, un-pause the brands from the dashboard."
echo "     They are all paused right now, so nothing runs until you do."
