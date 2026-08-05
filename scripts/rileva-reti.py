#!/usr/bin/env python3
"""Proposes changes to the seller-network registry. Never makes them.

Two AutoScout seller identities that publish the same cars are one business,
or two that share a catalogue. Autohero runs nine ids over one catalogue;
seven unrelated dealers trade as "City Car". The name cannot tell them apart,
the shared stock can.

The measure is the SHARE of the smaller seller's inventory that is shared, not
a count: two large dealers coincide more often than two small ones, so an
absolute threshold would flag the big and miss the small. Measured on live
data the two populations separate cleanly -- real networks sit above 60%,
coincidences below 2%.

Three findings, in descending order of how much they matter:

1. an id sharing heavily with a REGISTERED network but absent from it. This is
   the one that decays silently: Autohero has already moved from ids 4614xxxx
   to 5115xxxx, so the registry goes stale while continuing to look correct.
2. a new pair of unknown sellers above the threshold.
3. a registered network whose members no longer share anything -- the merge
   may have stopped being true.

Pairs listed under `scartate` are never reported again: a false positive
examined once should not be re-asked every month.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

from sqlalchemy import create_engine, text

sys.path.insert(0, "/app/src")

from autosmart24.networks import SellerNetworks  # noqa: E402

SOGLIA = 0.60
MIN_AUTO = 5   # sotto questo numero la quota è troppo rumorosa per dire nulla

QUERY = text("""
WITH imp AS (
  SELECT dealer_id, seller_company_name nome, brand, model,
         extract(year from first_registration) anno, fuel, drive_train, mileage_km, price
  FROM listings WHERE status='active' AND dealer_id IS NOT NULL AND mileage_km IS NOT NULL
), dim AS (
  SELECT dealer_id, max(nome) nome, count(*) n FROM imp GROUP BY 1
)
SELECT c.d1, x.nome, x.n, c.d2, y.nome, y.n, c.condivise,
       round(c.condivise::numeric / least(x.n, y.n), 2) AS quota
FROM (SELECT a.dealer_id d1, b.dealer_id d2, count(*) condivise
      FROM imp a JOIN imp b
        ON a.brand=b.brand AND a.model IS NOT DISTINCT FROM b.model AND a.anno=b.anno
       AND a.fuel IS NOT DISTINCT FROM b.fuel AND a.drive_train IS NOT DISTINCT FROM b.drive_train
       AND a.mileage_km=b.mileage_km AND a.price=b.price AND a.dealer_id < b.dealer_id
      GROUP BY 1,2) c
JOIN dim x ON x.dealer_id=c.d1
JOIN dim y ON y.dealer_id=c.d2
WHERE c.condivise >= :min_auto
  AND c.condivise::numeric / least(x.n, y.n) >= :soglia
""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registro", default="/app/config/reti-venditori.yaml")
    args = ap.parse_args()

    nets = SellerNetworks.load(args.registro)
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        pairs = conn.execute(QUERY, {"soglia": SOGLIA, "min_auto": MIN_AUTO}).fetchall()

    nuovi_membri: dict[int, list] = defaultdict(list)
    nuove_reti: list = []
    visti_in_rete: set[int] = set()

    for d1, n1, c1, d2, n2, c2, cond, q in pairs:
        if nets.is_rejected(d1, d2):
            continue
        g1, g2 = nets.group_of(d1), nets.group_of(d2)
        if g1 is not None and g2 is not None:
            visti_in_rete.update((g1, g2))
            continue
        if g1 is not None:
            nuovi_membri[g1].append((d2, n2, c2, cond, q))
        elif g2 is not None:
            nuovi_membri[g2].append((d1, n1, c1, cond, q))
        else:
            nuove_reti.append((d1, n1, c1, d2, n2, c2, cond, q))

    print(f"reti registrate: {len(nets.networks)} · coppie sopra soglia: {len(pairs)}\n")

    if nuovi_membri:
        print("=== id da AGGIUNGERE a reti gia' registrate (alta confidenza) ===")
        for g, membri in sorted(nuovi_membri.items()):
            capo = nets.networks[g]
            print(f"  rete {g} (id gia' presenti: {', '.join(str(x) for x in capo[:3])}...)")
            for d, nome, n, cond, q in sorted(set(membri)):
                print(f"    + {d}  {nome[:44]:46} {n:5} auto · {cond} in comune ({q})")
        print()

    if nuove_reti:
        print("=== coppie NUOVE da esaminare ===")
        for d1, n1, c1, d2, n2, c2, cond, q in sorted(nuove_reti, key=lambda r: -r[6]):
            print(f"  {cond:5} auto ({q})  {d1} {n1[:30]:32} + {d2} {n2[:30]}")
        print()

    stale = [g for g in range(len(nets.networks)) if g not in visti_in_rete]
    if stale:
        print("=== reti registrate che non condividono piu' nulla (da verificare) ===")
        for g in stale:
            print(f"  rete {g}: {', '.join(str(x) for x in nets.networks[g])}")
        print()

    if not (nuovi_membri or nuove_reti or stale):
        print("nessuna proposta: il registro riflette i dati")

    print("Questo strumento non modifica nulla. Le decisioni si scrivono a mano in")
    print(f"{args.registro}, sotto 'reti' se confermate o 'scartate' se no.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
