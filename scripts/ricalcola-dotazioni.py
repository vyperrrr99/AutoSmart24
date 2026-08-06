#!/usr/bin/env python3
"""Ricalcola le colonne delle dotazioni dalla lista grezza gia' a database.

Serve quando cambia una definizione in autosmart24.equipment: si aggiunge una
voce, si corregge una regola di riconoscimento, si scopre un'etichetta nuova.
Senza questo script ogni ripensamento costerebbe una riscansione completa --
e gli annunci gia' venduti non sono piu' leggibili, quindi certe auto non si
recupererebbero affatto.

Legge solo `equipment`, non tocca la rete. Le auto senza lista restano a NULL:
non sapere non e' sapere che non c'e'.

  docker compose run --rm --no-deps -v "$PWD/scripts:/scripts" \
    app python /scripts/ricalcola-dotazioni.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, "/app/src")

from autosmart24.equipment import COLUMNS, derive  # noqa: E402

LOTTO = 5000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"])
    aggiornate = invariate = 0
    ultimo = ""

    with engine.connect() as conn:
        while True:
            righe = conn.execute(text(
                f"SELECT id, equipment, {', '.join(COLUMNS)} FROM listings "
                "WHERE equipment IS NOT NULL AND id > :ultimo ORDER BY id LIMIT :n"
            ), {"ultimo": ultimo, "n": LOTTO}).fetchall()
            if not righe:
                break
            ultimo = righe[-1][0]

            for r in righe:
                atteso = derive(list(r[1]))
                attuale = dict(zip(COLUMNS, r[2:]))
                if atteso == attuale:
                    invariate += 1
                    continue
                aggiornate += 1
                if args.dry_run:
                    continue
                conn.execute(
                    text("UPDATE listings SET "
                         + ", ".join(f"{c} = :{c}" for c in COLUMNS)
                         + " WHERE id = :id"),
                    {**atteso, "id": r[0]})
            if not args.dry_run:
                conn.commit()
            print(f"  ...{aggiornate + invariate} esaminate", flush=True)

    print(f"\n  aggiornate {aggiornate} · gia' corrette {invariate}")
    if args.dry_run:
        print("  dry-run: nessuna modifica scritta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
