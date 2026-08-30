#!/usr/bin/env python3
"""Traccia i redirect non verificabili, per decidere fra un mese se il
segnale e' sistematico o e' stato un incidente legato all'arretrato Fiat.

Nato dal 25/08/2026: 374 annunci Fiat marcati venduti via redirect senza mai
essere arricchiti, quindi senza dealer_id per applicare twin_on_sale o
republished. Ripuliti una volta con strip_unverifiable_redirect_sales.

Da qui in avanti quella funzione NON gira piu' ogni notte: se un annuncio
sparisce con un redirect e non ha mai avuto un dealer_id, resta 'sold' e
questo script lo conta. Un annuncio gia' arricchito prima di sparire mantiene
il suo vero dealer_id e viene gia' giudicato ogni mattina da
reclassify_removals col criterio reale -- non serve tracciarlo qui.

Scrive una riga per esecuzione in stato/redirect-mensile.jsonl. Fra un mese si
guarda se il numero cresce (il fenomeno si ripete, va deciso un criterio
strutturale) o resta a zero (era l'arretrato Fiat, chiuso).

  docker compose run --rm --no-deps -v "$PWD/scripts:/scripts" \
    -v "$PWD/stato:/app/stato" app python /scripts/controllo-redirect-mensile.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

from sqlalchemy import create_engine, text

STORICO = "/app/stato/redirect-mensile.jsonl"


def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        nuovi = conn.execute(text(
            "SELECT count(*) FROM listings WHERE status='sold' "
            "AND redirect_to IS NOT NULL AND dealer_id IS NULL")).scalar()
        marche = conn.execute(text(
            "SELECT brand, count(*) FROM listings WHERE status='sold' "
            "AND redirect_to IS NOT NULL AND dealer_id IS NULL "
            "GROUP BY 1 ORDER BY 2 DESC")).fetchall()

    riga = {"data": dt.date.today().isoformat(), "nuovi_non_verificabili": nuovi,
            "per_marca": {m: n for m, n in marche}}
    try:
        with open(STORICO, "a") as f:
            f.write(json.dumps(riga, sort_keys=True) + "\n")
    except OSError as e:
        print(f"  ATTENZIONE: storico non scritto ({e}). Manca il mount di stato/?")

    if nuovi == 0:
        print(f"  {dt.date.today()}: zero redirect senza dealer_id da ripulire — nessuna ricomparsa")
        return 0

    print(f"  {dt.date.today()}: {nuovi} redirect senza dealer_id (mai visti prima della pulizia del 25/08)")
    for m, n in marche:
        print(f"    {m}: {n}")
    print("  Se questo numero cresce notte dopo notte, il fenomeno si sta ripetendo:")
    print("  vale la pena rivederlo prima della scadenza di un mese.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
