#!/usr/bin/env python3
"""Cosa risponde davvero il sito. Da eseguire PRIMA di formulare ipotesi.

    docker compose run --rm --no-deps -v "$PWD/scripts:/scripts" app \
      python /scripts/ispeziona-pagina.py --url https://...
    docker compose run --rm --no-deps -v "$PWD/scripts:/scripts" app \
      python /scripts/ispeziona-pagina.py --marca Fiat --quante 20

Esiste per una ragione precisa. Il 22/08/2026, davanti a tre blocchi 429 su
Fiat, sono state proposte in sequenza tre spiegazioni -- concorrenza, volume
cumulativo, frammentazione dei modelli -- tutte misurando i NOSTRI dati e
nessuna corretta. Bastava una richiesta HTTP: quelle pagine rispondevano 200 ma
redirigevano a una pagina di lista da 9.994 risultati invece che all'annuncio.

I nostri dati dicono cosa abbiamo salvato. Solo la richiesta dice cosa il sito
ha risposto.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, "/app/src")

from sqlalchemy import create_engine, text  # noqa: E402

from autosmart24.scraping.http_client import BlockedError, make_client  # noqa: E402
from autosmart24.scraping.next_data import extract_next_data  # noqa: E402


def esamina(client, url: str) -> tuple[str, str]:
    """Restituisce (categoria, dettaglio). Non solleva su risposte inattese:
    una risposta strana e' il risultato, non un errore."""
    r = client.client.get(url)
    finale = str(r.url)
    if r.status_code >= 400:
        return f"HTTP {r.status_code}", finale
    try:
        pp = extract_next_data(r.text).get("props", {}).get("pageProps", {})
    except Exception as e:
        return f"contenuto illeggibile ({type(e).__name__})", finale
    if "listingDetails" in pp:
        return "annuncio", finale
    if "numberOfResults" in pp:
        return f"LISTA ({pp.get('numberOfResults')} risultati)", finale
    return f"ne' annuncio ne' lista (chiavi: {sorted(pp)[:4]})", finale


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="un URL preciso")
    ap.add_argument("--marca", help="campiona dall'arretrato di questa marca")
    ap.add_argument("--modello", help="restringe al gruppo modello")
    ap.add_argument("--quante", type=int, default=20)
    args = ap.parse_args()

    client = make_client(3.0, 8.0)
    print(f"  proxy: {client.proxy_url or 'nessuno'}")

    if args.url:
        categoria, finale = esamina(client, args.url)
        print(f"  categoria: {categoria}")
        print(f"  URL finale: {finale}")
        if finale.rstrip("/") != args.url.rstrip("/"):
            print("  ATTENZIONE: c'e' stato un redirect. Non e' la pagina chiesta.")
        return 0

    if not args.marca:
        print("  serve --url oppure --marca")
        return 2

    engine = create_engine(os.environ["DATABASE_URL"])
    q = ("SELECT url FROM listings WHERE brand=:m AND status='active' "
         "AND NOT detail_scraped ")
    par = {"m": args.marca, "n": args.quante}
    if args.modello:
        q += "AND model_group=:mo "
        par["mo"] = args.modello
    q += "ORDER BY id LIMIT :n"
    with engine.connect() as conn:
        urls = [r[0] for r in conn.execute(text(q), par)]
    if not urls:
        print("  nessun annuncio da esaminare con questi filtri")
        return 1

    conteggio: Counter = Counter()
    for url in urls:
        try:
            categoria, finale = esamina(client, url)
        except BlockedError as e:
            print(f"  BLOCCATI dopo {sum(conteggio.values())} pagine: {e}")
            break
        conteggio[categoria.split(" (")[0]] += 1

    tot = sum(conteggio.values())
    print(f"  esaminate {tot} pagine di {args.marca}" + (f" / {args.modello}" if args.modello else ""))
    for categoria, n in conteggio.most_common():
        print(f"    {n:4} ({100*n/tot:5.1f}%)  {categoria}")
    if conteggio.get("LISTA"):
        print("  Le pagine di lista sono pesanti e sempre le stesse: scaricarle")
        print("  ripetutamente e' cio' per cui un sito blocca.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
