#!/usr/bin/env python3
"""Discovers which equipment options AutoScout24 publishes, and how often.

We have never captured `vehicle.equipment` from the detail pages. Before
deciding which options are worth storing, we need to know what exists and --
more usefully -- how common each one is: an option present on 99% of cars
carries no information, one present on 15% separates the market.

Sampling is stratified by brand and by price quartile within the brand. A
uniform random sample would under-represent premium options, because the
brands that offer them are not the ones with the most listings: it would
report a panoramic roof as rarer than it is and hide options that only appear
above a price point.

Deliberately slow. The nightly sweep is usually running when this is used, and
this process has its own connection with no shared rate-limit state, so the
delay is set so the two together stay near the rate one sweep produces alone.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import urllib.request

PER_BRAND = 20          # x 25 brands with listings ~ 500 cars
CONCURRENCY = 2
DELAY = (3.0, 5.0)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def sample_urls() -> list[tuple[str, str, int]]:
    """(brand, url, price) spread across price quartiles within each brand."""
    # Two CTE levels, because a window function cannot be nested inside another
    # window function's PARTITION BY: the quartile has to be materialised first.
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
    # An empty sample used to look like a legitimate result: the query error
    # went to stderr and was never read, so the script reported "0 auto" and
    # exited successfully.
    if res.returncode != 0 or not res.stdout.strip():
        raise SystemExit(f"query del campione fallita:\n{res.stderr.strip() or 'nessuna riga'}")
    out = res.stdout
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], int(parts[2])))
    return rows


def fetch_equipment(job: tuple[str, str, int]) -> tuple[str, int, dict] | None:
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
        d = json.loads(m.group(1))["props"]["pageProps"]["listingDetails"]
        return brand, price, (d.get("vehicle") or {}).get("equipment") or {}
    except Exception:
        return None


def main() -> None:
    jobs = sample_urls()
    print(f"campione: {len(jobs)} auto su {len({b for b, _, _ in jobs})} marche", flush=True)

    # Collect every car first, then derive. Deriving as we go would compare each
    # option's price only against the cars seen AFTER it was first discovered,
    # so the rarest options -- the ones most likely to matter -- would get the
    # least reliable figure.
    cars: list[tuple[str, int, set]] = []

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for res in pool.map(fetch_equipment, jobs):
            if res is None:
                continue
            brand, price, eq = res
            present = {(cat, item.get("id")) for cat, items in eq.items() for item in items if item.get("id")}
            cars.append((brand, price, present))
            if len(cars) % 25 == 0:
                distinct = len({k for _, _, p in cars for k in p})
                print(f"  {len(cars)}/{len(jobs)} · {distinct} opzioni distinte", flush=True)

    seen = len(cars)
    freq: Counter[tuple[str, str]] = Counter()
    by_brand: defaultdict[str, Counter] = defaultdict(Counter)
    for brand, _, present in cars:
        for key in present:
            freq[key] += 1
            by_brand[brand][key] += 1

    print(f"\nauto lette: {seen} · opzioni distinte: {len(freq)}\n", flush=True)

    rows = []
    for (cat, opt), n in freq.most_common():
        pct = 100.0 * n / seen
        w = [p for _, p, present in cars if (cat, opt) in present]
        wo = [p for _, p, present in cars if (cat, opt) not in present]
        delta = (sum(w) / len(w) - sum(wo) / len(wo)) if len(w) >= 10 and len(wo) >= 10 else None
        brands = sum(1 for b in by_brand if by_brand[b][(cat, opt)] > 0)
        rows.append({"categoria": cat, "opzione": opt, "auto": n, "pct": round(pct, 1),
                     "marche": brands, "delta_prezzo": round(delta) if delta is not None else None})

    with open("equipaggiamenti-catalogo.json", "w") as f:
        json.dump({"auto_campionate": seen, "opzioni": rows}, f, ensure_ascii=False, indent=1)
    print("scritto equipaggiamenti-catalogo.json", flush=True)


if __name__ == "__main__":
    sys.exit(main())
