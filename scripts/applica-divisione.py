#!/usr/bin/env python3
"""Mette in pausa le marche dell'altra macchina e attiva le proprie.

Versione portabile della stessa cosa che fa applica-divisione.sh, pensata per
girare dentro il contenitore: su Windows non si puo' dare per scontato ne' bash
ne' curl, e PowerShell tratta la barra rovescia come un carattere qualunque
invece che come continuazione di riga.

    docker compose -f docker-compose.yml -f docker-compose.seconda-macchina.yml run --rm --no-deps -v ${PWD}/config:/app/config -v ${PWD}/scripts:/scripts app python /scripts/applica-divisione.py windows

Passa dall'API e non dal database perche' mettere in pausa e' due cose, non
una: la riga in `tracked_brands` E il lavoro nello scheduler in memoria.
Aggiornando solo il database la marca ripartirebbe lo stesso alle 22:00.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

# Dentro la rete di compose il contenitore dell'API si chiama `app` e ascolta
# sulla 8000; la 8001 e' solo la porta pubblicata sull'host.
API = os.environ.get("AUTOSMART24_API", "http://app:8000")
CONF = os.environ.get("AUTOSMART24_DIVISIONE", "/app/config/marche-per-macchina.yaml")


def chiama(percorso: str, metodo: str = "GET"):
    req = urllib.request.Request(f"{API}{percorso}", method=metodo)
    with urllib.request.urlopen(req, timeout=20) as r:
        corpo = r.read()
    return json.loads(corpo) if corpo else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("macchina", choices=["thinkpad", "windows"],
                    help="il nome di QUESTA macchina")
    ap.add_argument("--prova", action="store_true",
                    help="mostra cosa farebbe senza toccare nulla")
    args = ap.parse_args()

    import yaml
    with open(CONF) as f:
        divisione = yaml.safe_load(f)
    altra = "windows" if args.macchina == "thinkpad" else "thinkpad"
    mie, loro = divisione[args.macchina], divisione[altra]

    comuni = set(mie) & set(loro)
    if comuni:
        print(f"  ERRORE: marche assegnate a entrambe le macchine: {sorted(comuni)}")
        return 1

    try:
        marche = chiama("/brands")
    except Exception as e:
        print(f"  API non raggiungibile su {API} ({type(e).__name__}) — il contenitore e' avviato?")
        return 1

    note = {b["slug"] for b in marche}
    mancanti = (set(mie) | set(loro)) - note
    if mancanti:
        print(f"  ERRORE: marche nel file ma non a database: {sorted(mancanti)}")
        return 1

    print(f"=== questa macchina e' '{args.macchina}' ===")
    if args.prova:
        print(f"  metterebbe in pausa {len(loro)} marche di '{altra}': {' '.join(sorted(loro))}")
        print(f"  attiverebbe {len(mie)} marche mie: {' '.join(sorted(mie))}")
        print("  prova: non ho toccato nulla")
        return 0

    for slug in loro:
        chiama(f"/brands/{slug}/pause", "POST")
    print(f"  messe in pausa {len(loro)} marche di '{altra}'")
    for slug in mie:
        chiama(f"/brands/{slug}/resume", "POST")
    print(f"  attivate {len(mie)} marche mie")

    # Riletto dall'API, non dedotto dalle chiamate riuscite.
    attive = sorted(b["slug"] for b in chiama("/brands") if not b.get("paused"))
    if attive != sorted(mie):
        print("  ATTENZIONE: le marche attive non corrispondono alla divisione")
        print(f"    attive:  {' '.join(attive)}")
        print(f"    attese:  {' '.join(sorted(mie))}")
        return 1
    print(f"  attive adesso ({len(attive)}): {' '.join(attive)}")
    print("  corrispondono alla divisione: corretto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
