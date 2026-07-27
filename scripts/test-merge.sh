#!/usr/bin/env bash
# Verifies merge-from-worker.sql against synthetic databases before it is ever
# pointed at real data.
#
# Builds two databases that reproduce the real scenario:
#   merge_test_primary — has all brands; the primary advanced ONE new brand
#   merge_test_worker  — same starting point; the worker advanced the historical brands
# then merges the worker into the primary and asserts on the result.
#
# Safe to run while scraping: it touches only its own throwaway databases.
set -uo pipefail
PSQL="sudo -n docker compose exec -T postgres psql -U autosmart24 -v ON_ERROR_STOP=1"
FAILED=0

check() {  # check <label> <actual> <expected>
  if [ "$2" = "$3" ]; then
    echo "  PASS  $1 ($2)"
  else
    echo "  FAIL  $1 — got '$2', expected '$3'"
    FAILED=1
  fi
}

echo "=== building the two test databases ==="
$PSQL -d postgres -c "DROP DATABASE IF EXISTS merge_test_primary;" >/dev/null 2>&1
$PSQL -d postgres -c "DROP DATABASE IF EXISTS merge_test_worker;"  >/dev/null 2>&1
$PSQL -d postgres -c "CREATE DATABASE merge_test_primary;" >/dev/null 2>&1
$PSQL -d postgres -c "CREATE DATABASE merge_test_worker;"  >/dev/null 2>&1

# Minimal schema: the columns the merge actually moves.
SCHEMA="
CREATE TABLE dealers (id integer PRIMARY KEY, company_name varchar(256), ratings_stars double precision,
  ratings_count integer, recommend_percentage integer, synced_at timestamp NOT NULL);
CREATE TABLE listings (id varchar(36) PRIMARY KEY, brand varchar(64) NOT NULL, price integer,
  url varchar(1024) NOT NULL, first_seen_at timestamp NOT NULL, last_seen_at timestamp NOT NULL,
  last_checked_at timestamp NOT NULL, status varchar(16) NOT NULL, detail_scraped boolean NOT NULL,
  dealer_id integer REFERENCES dealers(id));
CREATE TABLE price_history (id serial PRIMARY KEY, listing_id varchar(36) NOT NULL REFERENCES listings(id),
  price integer NOT NULL, recorded_at timestamp NOT NULL);
CREATE TABLE scrape_runs (id serial PRIMARY KEY, brand varchar(64) NOT NULL, started_at timestamp NOT NULL,
  finished_at timestamp, status varchar(16) NOT NULL, listings_seen integer NOT NULL DEFAULT 0,
  new_listings integer NOT NULL DEFAULT 0, price_changes integer NOT NULL DEFAULT 0,
  sold_detected integer NOT NULL DEFAULT 0, errors_count integer NOT NULL DEFAULT 0,
  phase varchar(16), search_finished_at timestamp, search_total integer, detail_total integer,
  detail_enriched integer NOT NULL DEFAULT 0);
CREATE TABLE scrape_events (id serial PRIMARY KEY, run_id integer REFERENCES scrape_runs(id),
  brand varchar(64), level varchar(16) NOT NULL, message varchar(2048) NOT NULL,
  url varchar(1024), created_at timestamp NOT NULL);
"
$PSQL -d merge_test_primary -c "$SCHEMA" >/dev/null 2>&1
$PSQL -d merge_test_worker  -c "$SCHEMA" >/dev/null 2>&1

# Shared starting point, as if both machines restored the same dump. All ten
# historical brands must be present: the merge refuses to run against a dump
# that is missing any of them, so a test fixture with only Fiat would trip the
# guard rather than exercise the merge.
COMMON="
INSERT INTO dealers VALUES (900,'Dealer A',4.5,10,90,'2026-07-27 10:00:00');
INSERT INTO listings VALUES ('fiat-1','Fiat',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('fiat-2','Fiat',11000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('vw-1','Volkswagen',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('audi-1','Audi',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('bmw-1','BMW',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('mb-1','Mercedes-Benz',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('peu-1','Peugeot',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('ford-1','Ford',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('jeep-1','Jeep',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('citr-1','Citroen',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('ren-1','Renault',10000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO listings VALUES ('nissan-1','Nissan',9000,'u','2026-07-01','2026-07-01','2026-07-01','active',true,900);
INSERT INTO price_history (listing_id,price,recorded_at) VALUES ('fiat-1',10000,'2026-07-01');
INSERT INTO price_history (listing_id,price,recorded_at) VALUES ('nissan-1',9000,'2026-07-01');
INSERT INTO scrape_runs (brand,started_at,status) VALUES ('Fiat','2026-07-01','success');
INSERT INTO scrape_runs (brand,started_at,status) VALUES ('Nissan','2026-07-01','success');
INSERT INTO scrape_events (run_id,brand,level,message,created_at) VALUES (1,'Fiat','info','old fiat','2026-07-01');
INSERT INTO scrape_events (run_id,brand,level,message,created_at) VALUES (2,'Nissan','info','old nissan','2026-07-01');
"
$PSQL -d merge_test_primary -c "$COMMON" >/dev/null 2>&1
$PSQL -d merge_test_worker  -c "$COMMON" >/dev/null 2>&1

# The primary advances Nissan only. Note the serial ids it consumes: these are
# the ones that would collide with the worker's.
$PSQL -d merge_test_primary -c "
INSERT INTO listings VALUES ('nissan-2','Nissan',9500,'u','2026-07-27','2026-07-27','2026-07-27','active',true,900);
INSERT INTO price_history (listing_id,price,recorded_at) VALUES ('nissan-2',9500,'2026-07-27');
INSERT INTO scrape_runs (brand,started_at,status,listings_seen) VALUES ('Nissan','2026-07-27','success',7228);
INSERT INTO scrape_events (run_id,brand,level,message,created_at) VALUES (3,'Nissan','info','new nissan','2026-07-27');
" >/dev/null 2>&1

# The worker advances Fiat: a new listing, a price change, an updated dealer.
$PSQL -d merge_test_worker -c "
INSERT INTO listings VALUES ('fiat-3','Fiat',12000,'u','2026-07-27','2026-07-27','2026-07-27','active',true,900);
UPDATE listings SET price=10500 WHERE id='fiat-1';
INSERT INTO price_history (listing_id,price,recorded_at) VALUES ('fiat-3',12000,'2026-07-27');
INSERT INTO price_history (listing_id,price,recorded_at) VALUES ('fiat-1',10500,'2026-07-27');
INSERT INTO scrape_runs (brand,started_at,status,listings_seen) VALUES ('Fiat','2026-07-27','success',24585);
INSERT INTO scrape_events (run_id,brand,level,message,created_at) VALUES (3,'Fiat','info','new fiat','2026-07-27');
UPDATE dealers SET company_name='Dealer A renamed', synced_at='2026-07-27 20:00:00' WHERE id=900;
" >/dev/null 2>&1

echo "=== moving the worker into a staging schema on the primary ==="
$PSQL -d merge_test_primary -c "CREATE SCHEMA staging;" >/dev/null 2>&1
sudo -n docker compose exec -T postgres pg_dump -U autosmart24 -d merge_test_worker \
  --data-only --schema=public 2>/dev/null > /tmp/worker_data.sql
$PSQL -d merge_test_primary -c "$SCHEMA" --set=search_path=staging >/dev/null 2>&1 || true
# Recreate the schema inside staging, then load the worker's rows into it.
$PSQL -d merge_test_primary -c "SET search_path=staging; $SCHEMA" >/dev/null 2>&1
sed 's/^COPY public\./COPY staging./' /tmp/worker_data.sql | \
  $PSQL -d merge_test_primary >/dev/null 2>&1

echo "=== running the merge ==="
MERGE_OUT=$( { echo "BEGIN;"; cat /home/vperrone/AutoSmart24/scripts/merge-from-worker.sql; echo "COMMIT;"; } | \
  $PSQL -d merge_test_primary 2>&1 )
if echo "$MERGE_OUT" | grep -qi "ERROR"; then
  echo "  MERGE FAILED:"; echo "$MERGE_OUT" | grep -i error | head -5 | sed 's/^/    /'
  FAILED=1
fi

echo "=== assertions ==="
q() { $PSQL -tAc "$1" -d merge_test_primary 2>/dev/null | tr -d ' \n'; }

check "Fiat listings came across (3)"        "$(q "SELECT count(*) FROM listings WHERE brand='Fiat';")" "3"
check "worker's Fiat price update applied"   "$(q "SELECT price FROM listings WHERE id='fiat-1';")" "10500"
check "primary's Nissan work survived (2)"   "$(q "SELECT count(*) FROM listings WHERE brand='Nissan';")" "2"
check "Fiat price history merged (3)"        "$(q "SELECT count(*) FROM price_history ph JOIN listings l ON l.id=ph.listing_id WHERE l.brand='Fiat';")" "3"
check "Nissan price history untouched (2)"   "$(q "SELECT count(*) FROM price_history ph JOIN listings l ON l.id=ph.listing_id WHERE l.brand='Nissan';")" "2"
check "Fiat runs merged (2)"                 "$(q "SELECT count(*) FROM scrape_runs WHERE brand='Fiat';")" "2"
check "Nissan runs untouched (2)"            "$(q "SELECT count(*) FROM scrape_runs WHERE brand='Nissan';")" "2"
check "no duplicate run ids"                 "$(q "SELECT count(*) FROM (SELECT id FROM scrape_runs GROUP BY id HAVING count(*)>1) x;")" "0"
check "events still point at a real run"     "$(q "SELECT count(*) FROM scrape_events e LEFT JOIN scrape_runs r ON r.id=e.run_id WHERE e.run_id IS NOT NULL AND r.id IS NULL;")" "0"
check "Fiat events merged (2)"               "$(q "SELECT count(*) FROM scrape_events WHERE brand='Fiat';")" "2"
check "Nissan events untouched (2)"          "$(q "SELECT count(*) FROM scrape_events WHERE brand='Nissan';")" "2"
check "each Fiat event on the right run"     "$(q "SELECT count(*) FROM scrape_events e JOIN scrape_runs r ON r.id=e.run_id WHERE e.brand='Fiat' AND r.brand<>'Fiat';")" "0"
check "newer dealer record won"              "$(q "SELECT company_name FROM dealers WHERE id=900;")" "DealerArenamed"

echo "=== guard: a dump missing the brands must be refused ==="
# Audi, not Fiat: Fiat's rows are referenced by staging.price_history, so
# deleting them fails on the foreign key and the fixture would silently still
# contain the brand — the guard would then have nothing to catch.
$PSQL -d merge_test_primary -c "DELETE FROM staging.listings WHERE brand='Audi';" 2>&1 | grep -qi error && echo "  (setup) delete failed — fixture unusable"
GUARD=$( { echo "BEGIN;"; cat /home/vperrone/AutoSmart24/scripts/merge-from-worker.sql; echo "COMMIT;"; } | \
  $PSQL -d merge_test_primary 2>&1 )
if echo "$GUARD" | grep -q "Refusing to merge"; then
  echo "  PASS  refused a dump with no Fiat listings"
else
  echo "  FAIL  the guard did not fire — a bad dump could wipe brands"; FAILED=1
fi
check "Audi survived the refused merge (1)" "$(q "SELECT count(*) FROM listings WHERE brand='Audi';")" "1"

echo "=== cleanup ==="
$PSQL -d postgres -c "DROP DATABASE IF EXISTS merge_test_primary;" >/dev/null 2>&1
$PSQL -d postgres -c "DROP DATABASE IF EXISTS merge_test_worker;"  >/dev/null 2>&1
rm -f /tmp/worker_data.sql

[ "$FAILED" = "0" ] && echo "TUTTI I TEST PASSATI" || echo "CI SONO TEST FALLITI"
exit "$FAILED"
