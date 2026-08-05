#!/usr/bin/env python3
"""Proposes changes to the seller-network registry. Never makes them.

Two AutoScout seller identities that publish the same cars are one business,
or two that share a catalogue. Autohero runs nine ids over one catalogue;
seven unrelated dealers trade as "City Car". The name cannot tell them apart,
the shared stock can.

Two measures, in OR, because neither works alone. The SHARE of the smaller
seller's inventory catches networks among small dealers, where twelve cars out
of seventeen is overwhelming. But it is blind when both parties are large:
Ceccato Automobili holds 568 cars, so twenty-two shared is 4% and the share
test stayed silent on groups whose names are identical. The absolute COUNT
covers that: five listings matching on make, model, year, fuel, drivetrain,
mileage AND price do not coincide between strangers. Pairs sharing exactly one
car number 257 and really are coincidences; those sharing five or more number
48, nearly all with the same name.

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

# Due criteri, in OR, perché nessuno dei due basta da solo.
#
# La quota trova le reti fra venditori piccoli: dodici auto su diciassette è
# schiacciante. Ma si calcola sul più piccolo dei due, quindi quando entrambi
# sono grandi anche una condivisione sostanziosa resta una frazione bassa --
# Ceccato Automobili ha 568 auto, ventidue in comune fanno il 4%, e con la sola
# quota il rilevatore taceva su gruppi il cui nome è identico.
#
# Il numero assoluto copre quel buco: cinque auto identiche per marca, modello,
# anno, alimentazione, trazione, chilometraggio E prezzo non capitano per caso
# fra due venditori estranei. Le coppie a una sola auto in comune sono 257 e
# sono davvero coincidenze; quelle a cinque o più sono 48, quasi tutte con lo
# stesso nome.
SOGLIA_QUOTA = 0.60
SOGLIA_ASSOLUTA = 5
MIN_AUTO = 2

QUERY = text("""
WITH imp AS (
  SELECT dealer_id, seller_company_name nome, brand, model,
         extract(year from first_registration) anno, fuel, drive_train, mileage_km, price
  FROM listings WHERE status='active' AND dealer_id IS NOT NULL AND mileage_km IS NOT NULL
), dim AS (
  SELECT dealer_id, max(nome) nome, count(*) n FROM imp GROUP BY 1
)
SELECT c.d1, x.nome, x.n, c.d2, y.nome, y.n, c.condivise,
       round(c.condivise::numeric / least(x.n, y.n), 2) AS quota,
       c.km_specifici
FROM (SELECT a.dealer_id d1, b.dealer_id d2, count(*) condivise,
             count(*) FILTER (WHERE a.mileage_km % 1000 <> 0) km_specifici
      FROM imp a JOIN imp b
        ON a.brand=b.brand AND a.model IS NOT DISTINCT FROM b.model AND a.anno=b.anno
       AND a.fuel IS NOT DISTINCT FROM b.fuel AND a.drive_train IS NOT DISTINCT FROM b.drive_train
       AND a.mileage_km=b.mileage_km AND a.price=b.price AND a.dealer_id < b.dealer_id
      GROUP BY 1,2) c
JOIN dim x ON x.dealer_id=c.d1
JOIN dim y ON y.dealer_id=c.d2
WHERE c.condivise >= :min_auto
  AND (c.condivise::numeric / least(x.n, y.n) >= :quota
       OR c.condivise >= :assoluta)
""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registro", default="/app/config/reti-venditori.yaml")
    args = ap.parse_args()

    nets = SellerNetworks.load(args.registro)
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        pairs = conn.execute(QUERY, {
            "quota": SOGLIA_QUOTA, "assoluta": SOGLIA_ASSOLUTA, "min_auto": MIN_AUTO,
        }).fetchall()

    nuovi_membri: dict[int, list] = defaultdict(list)
    nuove_reti: list = []
    visti_in_rete: set[int] = set()

    for d1, n1, c1, d2, n2, c2, cond, q, km_spec in pairs:
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
            nuove_reti.append((d1, n1, c1, d2, n2, c2, cond, q, km_spec))

    print(f"reti registrate: {len(nets.networks)} · coppie sopra soglia: {len(pairs)}\n")

    if nuovi_membri:
        print("=== id da AGGIUNGERE a reti gia' registrate (alta confidenza) ===")
        for g, membri in sorted(nuovi_membri.items()):
            capo = nets.networks[g]
            print(f"  rete {g} (id gia' presenti: {', '.join(str(x) for x in capo[:3])}...)")
            # Un id che condivide auto con quattro membri della stessa rete
            # produce quattro righe, e sembrano quattro proposte. Aggregato per
            # id, tenendo l'evidenza piu' forte: la decisione riguarda l'id, non
            # la singola coppia.
            per_id: dict = {}
            for d, nome, n, cond, q in membri:
                prec = per_id.get(d)
                if prec is None or cond > prec[2]:
                    per_id[d] = (nome, n, cond, q, len([m for m in membri if m[0] == d]))
            for d, (nome, n, cond, q, con_quanti) in sorted(per_id.items(), key=lambda kv: -kv[1][2]):
                print(f"    + {d}  {nome[:42]:44} {n:5} auto · fino a {cond} in comune "
                      f"({q}) con {con_quanti} membri della rete")
        print()

    if nuove_reti:
        print("=== coppie NUOVE da esaminare ===")
        print("  auto in comune · quota sul piu' piccolo · quante con km non tondo")
        for d1, n1, c1, d2, n2, c2, cond, q, km_spec in sorted(nuove_reti, key=lambda r: -r[6]):
            # Un chilometraggio come 44.790 e' di fatto un numero di serie;
            # 45.000 puo' coincidere. Mostrato, non usato per filtrare: e' una
            # conferma per chi guarda, non un criterio.
            print(f"  {cond:5} ({q:>4}) km specifici {km_spec:3}/{cond:<3} "
                  f"{d1} {n1[:26]:28} + {d2} {n2[:26]}")
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
