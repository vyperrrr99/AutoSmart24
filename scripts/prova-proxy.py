#!/usr/bin/env python3
"""Venti pagine vere, per sapere se l'uscita di rete e' pulita.

Da eseguire PRIMA di lanciare un giro intero da una macchina nuova o da un
proxy nuovo. Le VPN commerciali hanno indirizzi di uscita condivisi fra molti
utenti, e i sistemi anti-bot spesso li conoscono gia': un'uscita segnata viene
respinta piu' in fretta di una connessione domestica, non meno.

Venti su venti significa che si puo' partire. Un BlockedError nei primi
tentativi significa che quell'uscita e' bruciata: cambiala, oppure prova senza
proxy -- la connessione diretta di una seconda macchina e' comunque un indirizzo
diverso dalla prima, che e' tutto cio' che serve.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "/app/src")

from sqlalchemy import create_engine, text  # noqa: E402

from autosmart24.scraping.detail_queue import fetch_detail  # noqa: E402
from autosmart24.scraping.http_client import BlockedError, make_client  # noqa: E402

QUANTE = int(os.environ.get("PROVA_PAGINE", "20"))


def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        urls = [r[0] for r in conn.execute(text(
            "SELECT url FROM listings WHERE status='active' AND detail_scraped "
            "ORDER BY last_seen_at DESC LIMIT :n"), {"n": QUANTE})]
    if not urls:
        print("  nessun annuncio da provare: il database e' raggiungibile?")
        return 1

    client = make_client(3.0, 8.0)
    print(f"  proxy in uso: {client.proxy_url or 'nessuno (connessione diretta)'}")
    ok = 0
    for url in urls:
        try:
            fetch_detail(client, url)
            ok += 1
        except BlockedError as e:
            print(f"  BLOCCATI dopo {ok} pagine su {len(urls)}: {e}")
            print("  quell'uscita e' gia' segnata. Cambiala, o prova senza proxy.")
            return 1
        except Exception as e:
            print(f"  errore alla pagina {ok + 1}: {type(e).__name__}: {e}")
            return 1
    print(f"  {ok} pagine su {len(urls)}: l'uscita e' buona, si puo' partire")
    return 0


if __name__ == "__main__":
    sys.exit(main())
