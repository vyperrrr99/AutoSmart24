#!/usr/bin/env python3
"""Toglie dal database le auto fuori dal periodo di riferimento.

NON E' UNA POLITICA RICORRENTE. E' una rimedio una tantum, e va tenuto tale.

La finestra e' MOBILE: "quindici anni indietro da oggi". Trasformare questo
strumento in un lavoro pianificato significherebbe cancellare ogni anno gli
annunci piu' vecchi di quindici anni -- fra dodici mesi le auto del 2011, che
oggi stiamo raccogliendo apposta e che sono dati veri, correttamente ottenuti.
Una regola che cambia significato da sola mentre nessuno la guarda.

Cosa e' servito a togliere: 4.234 annunci immatricolati fra il 1941 e il 2010,
tutti Fiat e **nessuno arricchito**, entrati quando la finestra era diversa e
poi rimasti in un limbo -- fuori dal filtro per anno della coda, quindi mai
letti, ma ancora a database. Righe a meta' raccolte per errore.

Se un domani il database diventasse troppo grande, la decisione su cosa
conservare si prende guardando la dimensione e l'uso che si vuole fare dei
dati, non applicando automaticamente la finestra di raccolta.

Nasce da un residuo reale: 4.234 annunci immatricolati fra il 1941 e il 2010,
tutti attivi e **nessuno arricchito**. Erano entrati quando la finestra era
diversa, poi il filtro per anno della coda di arricchimento ha smesso di
accettarli, e da allora nessuno li leggeva piu' -- ma la ricerca continuava a
ricontrollarli ogni notte. Righe a meta', mantenute a spese di richieste.

Il periodo si ricava per marca da `tracked_brands.year_from_years`, quindi lo
strumento resta valido se la finestra cambia.

  docker compose run --rm --no-deps -v "$PWD/scripts:/scripts" app \
    python /scripts/pota-fuori-periodo.py --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from sqlalchemy import create_engine, text

# Le vendute non si toccano: sono lo storico su cui la BI calcola i tempi di
# vendita, e cancellarle riscriverebbe il passato invece di ripulire il
# presente. Se un giorno servisse potarle anche quelle, va deciso a parte.
STATI_POTABILI = ("active", "quarantine", "removed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--conferma-una-tantum", action="store_true",
                    help="obbligatorio per cancellare: vedi il perche' in testa al file")
    args = ap.parse_args()
    if not args.dry_run and not args.conferma_una_tantum:
        print("  Rifiuto di cancellare senza --conferma-una-tantum.")
        print("  Questo strumento NON va pianificato: la finestra e' mobile, e fra un")
        print("  anno cancellerebbe le auto del 2011 che oggi raccogliamo apposta.")
        return 2

    engine = create_engine(os.environ["DATABASE_URL"])
    anno_corrente = dt.date.today().year

    with engine.connect() as conn:
        marche = conn.execute(text(
            "SELECT display_name, year_from_years FROM tracked_brands")).fetchall()
        if not marche:
            print("  nessuna marca configurata")
            return 1

        totale = 0
        righe = []
        for nome, anni in marche:
            if anni is None:
                continue
            soglia = dt.date(anno_corrente - anni, 1, 1)
            n = conn.execute(text(
                "SELECT count(*) FROM listings WHERE brand=:b AND status = ANY(:s) "
                "AND first_registration IS NOT NULL AND first_registration < :d"),
                {"b": nome, "s": list(STATI_POTABILI), "d": soglia}).scalar()
            if n:
                righe.append((nome, anni, soglia.year, n))
                totale += n

        vendute = conn.execute(text(
            "SELECT count(*) FROM listings l JOIN tracked_brands t ON t.display_name = l.brand "
            "WHERE l.status='sold' AND l.first_registration IS NOT NULL "
            "AND extract(year from l.first_registration) < :anno - t.year_from_years"),
            {"anno": anno_corrente}).scalar()

    if not totale:
        print("  niente da potare: il database e' dentro il periodo")
        if vendute:
            print(f"  ({vendute} vendute fuori periodo, lasciate: sono storico)")
        return 0

    print(f"  {'marca':18} {'finestra':>9} {'da':>6} {'da togliere':>12}")
    for nome, anni, dal, n in sorted(righe, key=lambda r: -r[3]):
        print(f"  {nome:18} {str(anni)+' anni':>9} {dal:>6} {n:12,}".replace(",", "."))
    print(f"  {'TOTALE':18} {'':>9} {'':>6} {totale:12,}".replace(",", "."))
    if vendute:
        print(f"\n  {vendute} vendute fuori periodo: NON toccate, sono lo storico della BI")

    if args.dry_run:
        print("\n  dry-run: nessuna riga cancellata")
        return 0

    with engine.connect() as conn:
        soglie = {n: dt.date(anno_corrente - a, 1, 1) for n, a in marche if a is not None}
        prezzi = listati = 0
        for nome, soglia in soglie.items():
            par = {"b": nome, "s": list(STATI_POTABILI), "d": soglia}
            prezzi += conn.execute(text(
                "DELETE FROM price_history WHERE listing_id IN ("
                "  SELECT id FROM listings WHERE brand=:b AND status = ANY(:s) "
                "  AND first_registration IS NOT NULL AND first_registration < :d)"), par).rowcount
            listati += conn.execute(text(
                "DELETE FROM listings WHERE brand=:b AND status = ANY(:s) "
                "AND first_registration IS NOT NULL AND first_registration < :d"), par).rowcount
        conn.commit()
        rimasti = conn.execute(text(
            "SELECT count(*) FROM listings l JOIN tracked_brands t ON t.display_name = l.brand "
            "WHERE l.status = ANY(:s) AND l.first_registration IS NOT NULL "
            "AND extract(year from l.first_registration) < :anno - t.year_from_years"),
            {"s": list(STATI_POTABILI), "anno": anno_corrente}).scalar()

    print(f"\n  cancellati {listati:,} annunci e {prezzi:,} righe di storico prezzi".replace(",", "."))
    print(f"  fuori periodo rimasti (atteso 0): {rimasti}")
    return 0 if rimasti == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
