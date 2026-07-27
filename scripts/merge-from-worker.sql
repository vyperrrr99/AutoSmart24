-- Merge the second machine's work back into the primary database.
--
-- CONTRACT (this is what makes the merge safe and unambiguous):
--   * The two machines scrape DISJOINT brand lists.
--   * The primary NEVER touches the ten historical brands listed below.
--   * Therefore, for those ten brands, the worker's database is the single
--     source of truth and can replace the primary's copy wholesale.
--
-- That contract is what lets this be a replace rather than a row-by-row
-- reconciliation: there is no case where both sides changed the same row and
-- something has to decide which version wins.
--
-- Everything outside the ten brands is left untouched, so the primary's own
-- work on the thirteen new brands is never at risk.
--
-- Expects the worker's dump restored into schema `staging` in this database.
-- Run inside a transaction; the caller does BEGIN/COMMIT.

-- The filter the whole merge rests on. Values are display_name, not slug.
CREATE TEMP TABLE worker_brands (brand text PRIMARY KEY);
INSERT INTO worker_brands VALUES
  ('Fiat'), ('Volkswagen'), ('Audi'), ('BMW'), ('Mercedes-Benz'),
  ('Peugeot'), ('Ford'), ('Jeep'), ('Citroen'), ('Renault');

-- Refuse to run if the worker's dump does not actually contain the brands we
-- are about to delete locally. Without this, a wrong or truncated dump would
-- silently wipe ten brands from the primary.
DO $$
DECLARE missing text;
BEGIN
  SELECT string_agg(b.brand, ', ') INTO missing
  FROM worker_brands b
  WHERE NOT EXISTS (SELECT 1 FROM staging.listings l WHERE l.brand = b.brand);
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'Worker dump has no listings for: %. Refusing to merge — this would delete those brands from the primary.', missing;
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 1. Dealers — AutoScout ids, so the same dealer has the same id on both
--    machines. Upsert rather than replace: a dealer can sell cars of brands
--    owned by either machine, so neither side is authoritative for the row.
--    Keep whichever copy was synced more recently.
-- ---------------------------------------------------------------------------
INSERT INTO dealers (id, company_name, ratings_stars, ratings_count, recommend_percentage, synced_at)
SELECT id, company_name, ratings_stars, ratings_count, recommend_percentage, synced_at
FROM staging.dealers
ON CONFLICT (id) DO UPDATE SET
  company_name         = EXCLUDED.company_name,
  ratings_stars        = EXCLUDED.ratings_stars,
  ratings_count        = EXCLUDED.ratings_count,
  recommend_percentage = EXCLUDED.recommend_percentage,
  synced_at            = EXCLUDED.synced_at
WHERE EXCLUDED.synced_at > dealers.synced_at;

-- ---------------------------------------------------------------------------
-- 2. Price history for the ten brands. Deleted before the listings themselves
--    because of the foreign key. The id column is omitted so the primary's own
--    sequence assigns fresh ids — the worker's ids overlap with the primary's
--    and cannot be reused.
-- ---------------------------------------------------------------------------
DELETE FROM price_history ph
USING listings l
WHERE ph.listing_id = l.id
  AND l.brand IN (SELECT brand FROM worker_brands);

-- ---------------------------------------------------------------------------
-- 3. Listings for the ten brands: replace wholesale.
-- ---------------------------------------------------------------------------
DELETE FROM listings WHERE brand IN (SELECT brand FROM worker_brands);

INSERT INTO listings
SELECT * FROM staging.listings
WHERE brand IN (SELECT brand FROM worker_brands);

-- Now the price history, once its listings exist again.
INSERT INTO price_history (listing_id, price, recorded_at)
SELECT ph.listing_id, ph.price, ph.recorded_at
FROM staging.price_history ph
JOIN staging.listings l ON l.id = ph.listing_id
WHERE l.brand IN (SELECT brand FROM worker_brands);

-- ---------------------------------------------------------------------------
-- 4. Scrape runs and their events. Both have serial ids that collide across
--    machines, so rows are reinserted with fresh ids and the events' foreign
--    key is remapped through a lookup of old id -> new id.
-- ---------------------------------------------------------------------------
DELETE FROM scrape_events WHERE brand IN (SELECT brand FROM worker_brands);
DELETE FROM scrape_events
WHERE run_id IN (SELECT id FROM scrape_runs WHERE brand IN (SELECT brand FROM worker_brands));
DELETE FROM scrape_runs WHERE brand IN (SELECT brand FROM worker_brands);

CREATE TEMP TABLE run_id_map (old_id integer PRIMARY KEY, new_id integer NOT NULL);

WITH inserted AS (
  INSERT INTO scrape_runs (brand, started_at, finished_at, status, listings_seen,
                           new_listings, price_changes, sold_detected, errors_count,
                           phase, search_finished_at, search_total, detail_total, detail_enriched)
  SELECT brand, started_at, finished_at, status, listings_seen,
         new_listings, price_changes, sold_detected, errors_count,
         phase, search_finished_at, search_total, detail_total, detail_enriched
  FROM staging.scrape_runs
  WHERE brand IN (SELECT brand FROM worker_brands)
  ORDER BY id
  RETURNING id
),
-- Pair each returned id with the source row in the same order. RETURNING does
-- not expose the source id, so both sides are numbered by the same ORDER BY.
numbered_new AS (
  SELECT id AS new_id, row_number() OVER (ORDER BY id) AS rn FROM inserted
),
numbered_old AS (
  SELECT id AS old_id, row_number() OVER (ORDER BY id) AS rn
  FROM staging.scrape_runs
  WHERE brand IN (SELECT brand FROM worker_brands)
)
INSERT INTO run_id_map (old_id, new_id)
SELECT o.old_id, n.new_id FROM numbered_old o JOIN numbered_new n USING (rn);

INSERT INTO scrape_events (run_id, brand, level, message, url, created_at)
SELECT m.new_id, e.brand, e.level, e.message, e.url, e.created_at
FROM staging.scrape_events e
JOIN staging.scrape_runs r ON r.id = e.run_id
JOIN run_id_map m ON m.old_id = e.run_id
WHERE r.brand IN (SELECT brand FROM worker_brands);

-- Events with no run_id (backoff notices) carry a brand of their own.
INSERT INTO scrape_events (run_id, brand, level, message, url, created_at)
SELECT NULL, e.brand, e.level, e.message, e.url, e.created_at
FROM staging.scrape_events e
WHERE e.run_id IS NULL
  AND e.brand IN (SELECT brand FROM worker_brands);
