#!/usr/bin/env python3
"""Samples detail pages and keeps the WHOLE `vehicle` block, one JSON per line.

Written after answering two questions in a row -- which equipment options
exist, then which paint finishes -- each of which cost its own scan because
the previous sampler kept only the fields that question needed and discarded
the rest.

The detail page carries roughly ninety fields we do not store. Which of them
turn out to matter is not knowable in advance: it emerges from conversations
with people who know the market. So this keeps everything and answers later
questions from disk.

That is the same reasoning behind the `raw_detail` column this project used to
have, which migration 0006 dropped -- a decision that turned what would have
been a query into an hour of scraping. This file is a deliberately narrow
version of it: a sample on disk for exploration, not a column in the hot path.

Sampling is stratified by brand and price quartile: a uniform sample
under-represents premium equipment, because the brands that offer it are not
the ones with the most listings.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PER_BRAND = 20
CONCURRENCY = 2
DELAY = (3.0, 5.0)
OUT = "campione-vehicle.jsonl"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def sample_urls() -> list[tuple[str, str, int]]:
    # Two CTE levels: a window function cannot be nested inside another
    # window function's PARTITION BY.
    sql = f"""
    with quartiles as (
      select brand, url, price, id,
             ntile(4) over (partition by brand order by price) as quartile
      from listings
      where status='active' and detail_scraped and price is not null and price > 0
    ), ranked as (
      select brand, url, price, quartile,
             row_number() over (partition by brand, quartile order by md5(id)) as rn
      from quartiles
    )
    select brand, url, price from ranked where rn <= {PER_BRAND // 4} order by brand, quartile;
    """
    res = subprocess.run(
        ["sudo", "-n", "docker", "exec", "-i", "autosmart24-postgres-1",
         "psql", "-U", "autosmart24", "-tA", "-F", "\t", "-v", "ON_ERROR_STOP=1",
         "-d", "autosmart24", "-c", sql],
        capture_output=True, text=True, timeout=120,
    )
    # An empty sample must not look like a legitimate result: a previous version
    # sent the query error to stderr, never read it, and reported "0 cars".
    if res.returncode != 0 or not res.stdout.strip():
        raise SystemExit(f"query del campione fallita:\n{res.stderr.strip() or 'nessuna riga'}")
    rows = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], int(parts[2])))
    return rows


def fetch(job: tuple[str, str, int]) -> dict | None:
    brand, url, price = job
    time.sleep(random.uniform(*DELAY))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status != 200:
                return None
            html = r.read().decode("utf-8", errors="ignore")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            return None
        ld = json.loads(m.group(1))["props"]["pageProps"]["listingDetails"]
        return {"brand_db": brand, "price_db": price, "url": url, "vehicle": ld.get("vehicle") or {}}
    except Exception:
        return None


def main() -> None:
    jobs = sample_urls()
    print(f"campione: {len(jobs)} auto su {len({b for b, _, _ in jobs})} marche", flush=True)
    kept = 0
    with open(OUT, "w") as f, ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for rec in pool.map(fetch, jobs):
            if rec is None:
                continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            kept += 1
            if kept % 50 == 0:
                print(f"  {kept}/{len(jobs)}", flush=True)
    print(f"\nsalvate {kept} auto in {OUT}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
