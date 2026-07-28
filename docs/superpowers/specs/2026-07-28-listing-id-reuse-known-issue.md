# Listing ID reuse crashes cross-brand sweeps

## Context

Discovered while running the 10-year backfill for the 10 historic brands on
the Windows machine. Audi's sweep crashed three times in a row on the same
listing id (`c56aac2b-479a-4761-a605-8c7f34404ed1`) with:

```
psycopg.errors.UniqueViolation: duplicate key value violates unique
constraint "listings_pkey"
Key (id)=(c56aac2b-479a-4761-a605-8c7f34404ed1) already exists.
```

## Root cause

AutoScout24 reuses the same listing id (the trailing UUID segment of the URL)
for an unrelated new ad once the old one is delisted. Confirmed empirically:
the URL we had on file,

`.../annunci/mercedes-benz-180-diesel-grigio-cat_ma47mo20401-c56aac2b-479a-4761-a605-8c7f34404ed1`

now returns an HTTP 308 permanent redirect to

`.../annunci/audi-q3-sportback-diesel-grigio-cat_ma9mo19715-c56aac2b-479a-4761-a605-8c7f34404ed1`

Same trailing id, different category (`ma47` Mercedes-Benz -> `ma9` Audi),
different car entirely. This is not a site indexing glitch -- the id is a
recycled slot, not a permanent 1:1 key for one physical car ad.

Our schema currently assumes `Listing.id` (AutoScout's id) is a stable,
permanent identity for one car. It is not, over a long enough time horizon.

## Impact

`run_brand_sweep`'s existing-id lookup is (was) scoped to the brand being
swept. When brand A's crawl encounters an id that was previously inserted
under brand B (because AutoScout24 reused the id for a new brand-A car after
the old brand-B car was delisted), the code treats it as a fresh INSERT --
which crashes on the `listings_pkey` unique constraint, taking down the
entire run (not just that one row).

## Interim mitigation (deployed on the Windows machine, NOT committed)

`run_manager.py`'s `run_brand_sweep` now loads existing listing ids globally
(id -> brand) instead of scoped to the current brand, and when a "new" id
already belongs to a *different* brand, it logs a warning event and skips
that single row instead of inserting (which crashed) or updating in place
(which would misattribute the other brand's car).

This stops the crash, but is **not semantically correct**:

- The stale row (the delisted car) stays `status='active'` forever -- it is
  never recognized as sold/removed, because the brand whose sweep now
  "owns" that id skips it entirely, and the original brand's own sweep has
  no reason to suspect its id was reassigned.
- The new car (the one that now actually lives at that id) is **never
  captured** in our data at all -- silently skipped.

Given the workflow split between machines, this fix was made only on the
Windows machine to unblock the overnight backfill and was **not committed**
(uncommitted local change to `scraper/src/autosmart24/run_manager.py` and a
new test in `scraper/tests/test_run_manager.py`,
`test_run_brand_sweep_skips_a_listing_id_that_already_exists_under_another_brand`).
Treat that local diff as a reference sketch, not something to pull as-is.

## What a proper fix needs to do

1. Detect id reuse rather than just "id exists under another brand" --
   e.g. compare the crawled snippet's actual make/model against what is on
   file for that id, or follow the detail-page redirect and compare its
   destination category to the stored brand.
2. Close out the stale row: mark it `sold` (or a new `status` value for
   "delisted/reassigned" if `sold` is misleading) rather than leaving it
   `active` forever.
3. Capture the new car's data under a key that does not collide -- the
   original `id` is taken. Options: a synthetic composite key, a separate
   "generation"/source-brand disambiguator column, or re-keying the old row
   and giving the *new* arrival the bare AutoScout id (probably preferable,
   since new data should get the canonical id going forward).

## Frequency

Currently observed once across ~200k+ listings in the DB, so likely rare
(natural inventory churn), but not provably a one-off -- it will keep
recurring at whatever rate AutoScout24 recycles ids.
