#!/usr/bin/env python3
"""Recupera dotazioni e vernice sulle auto lette prima che le colonne esistessero.

Le 250.625 auto attive gia' arricchite non le avrebbero mai: la coda notturna
seleziona solo `detail_scraped = false`, e quel campo diventa true per sempre
alla prima lettura. Sono uscite dalla coda prima che ci fosse qualcosa da
raccogliere.

Questo e' un lavoro una tantum, a blocchi, negli orari in cui lo scraper e'
fermo. Gira per conto suo e si ferma da solo.

SCRIVE SOLO le colonne nuove: equipment, paint_type, body_color_original e le
nove booleane. Non tocca `detail_scraped`, ne' `status`, ne' `sold_at`, ne'
`last_seen_at`. Il motivo e' preciso: quei campi sono l'unica prova che
abbiamo di una vendita, e un processo diurno che li sfiorasse falserebbe la
metrica su cui e' costruita tutta la BI.

In particolare, una pagina che risponde 404 qui NON significa venduto. La
funzione fetch_detail lo segnala, e noi lo ignoriamo: l'annuncio resta com'e'
e lo riprendera' il giro notturno, che sa fare la doppia conferma. Marcare
venduto da qui reintrodurrebbe il difetto che e' costato una settimana a
chiudere -- 139 auto Lancia dichiarate vendute mentre erano tutte in vendita.

Sicurezze, in ordine di quando scattano:

  1. fuori dalla finestra oraria consentita: non parte
  2. una scansione e' in corso, o l'API non risponde: non parte
  3. raggiunta la scadenza a meta' blocco: si ferma e salva il punto
  4. il sito ci blocca (403/429): si ferma subito, senza insistere

Il punto di ripartenza sta in stato/dotazioni-cursore.json: si procede per id
crescente, cosi' un'auto la cui pagina non ha dato nulla non viene ritentata
per sempre.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text

sys.path.insert(0, "/app/src")

from autosmart24.equipment import COLUMNS  # noqa: E402
from autosmart24.scraping.concurrency import run_worker_pool  # noqa: E402
from autosmart24.scraping.detail_queue import fetch_detail  # noqa: E402
from autosmart24.scraping.http_client import BlockedError, make_client  # noqa: E402

CURSORE = "/app/stato/dotazioni-cursore.json"
FUSO = ZoneInfo("Europe/Rome")

# La finestra: lo scraper parte alle 22:00 locali e finisce fra le 05:00 e le
# 07:20; il backup della BI occupa le 07:30-07:45 e satura la banda in salita;
# riclassificazione e lavori BI stanno fra le 09:00 e le 09:30. Restano le
# 10:00-21:00, e ci teniamo un'ora abbondante di margine prima delle 22:00.
ORA_INIZIO = 10
ORA_FINE = 20  # ultimo blocco avviabile alle 20:xx, scadenza alle 21:00
SCADENZA_MINUTI = 50  # un blocco non supera l'ora che gli e' stata data

# Piu' gentile della scansione notturna: il sito e' gia' battuto otto ore a
# notte, e questo lavoro non ha fretta.
#
# Misurato il 09/08 su pagine vere: 6,3 secondi a pagina per worker, cioe' 9,5
# al minuto -- la pausa di 3-8s domina, la richiesta vera pesa poco piu' di un
# secondo. A concorrenza 4: 38 al minuto, 2.280 all'ora.
#
# Da cui il blocco, ridimensionato il 10/08 su undici blocchi veri invece che
# sulla prova a un worker solo. Misurato: 1.500 auto in 36-37 minuti, cioe'
# 40,4-41,6 al minuto -- tre per cento di scarto, perche' la pausa di cortesia
# domina e non varia.
#
# 1.850 auto stanno in 45,8 minuti alla velocita' piu' lenta osservata e in
# 44,7 a quella media, contro una scadenza di 50: restano circa quattro minuti
# di margine. Andare oltre non sarebbe pericoloso -- un blocco che sfora si
# chiude e basta, il cursore non avanza e il blocco dopo riprende esattamente
# da li' senza riscaricare nulla -- ma il margine costa poco e toglie un modo
# di sbagliare.
CONCORRENZA = 4
BLOCCO = 1850
LOTTO_DB = 500


def _ora_locale() -> dt.datetime:
    # Il container e' in UTC, le finestre sono in ora locale. Ricavata dal
    # fuso, non da uno scarto fisso: +2 e' giusto solo da marzo a ottobre, e
    # una finestra sbagliata di un'ora andrebbe a sbattere contro la scansione.
    return dt.datetime.now(dt.timezone.utc).astimezone(FUSO)


def _leggi_cursore() -> str:
    try:
        with open(CURSORE) as f:
            return json.load(f).get("ultimo_id", "")
    except (FileNotFoundError, ValueError):
        return ""


def _scrivi_cursore(ultimo_id: str, fatte: int) -> None:
    try:
        with open(CURSORE, "w") as f:
            json.dump({"ultimo_id": ultimo_id,
                       "aggiornato": _ora_locale().strftime("%d/%m/%Y %H:%M"),
                       "auto_trattate_in_totale": fatte}, f)
    except OSError as e:
        # Detto forte: senza cursore il prossimo blocco ripartirebbe da capo.
        print(f"  ATTENZIONE: cursore NON salvato ({e}). Manca il mount di stato/?")


def scanner_libero(conn) -> tuple[bool, str]:
    """Chiesto al database, non all'API: da dentro il container l'API non e'
    raggiungibile su localhost, e una riga `running` e' comunque la prova piu'
    diretta che una scansione e' viva."""
    riga = conn.execute(text(
        "SELECT brand FROM scrape_runs WHERE status='running' "
        "AND started_at >= now() - interval '12 hours' LIMIT 1")).fetchone()
    if riga:
        return False, f"scansione in corso ({riga[0]}) — non parto"
    return True, ""


def dentro_la_finestra(adesso: dt.datetime) -> tuple[bool, str]:
    if not (ORA_INIZIO <= adesso.hour <= ORA_FINE):
        return False, (f"sono le {adesso:%H:%M}: fuori dalla finestra "
                       f"{ORA_INIZIO}:00-{ORA_FINE + 1}:00 — non parto")
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocco", type=int, default=BLOCCO,
                    help="quante auto in questo blocco")
    ap.add_argument("--scadenza-minuti", type=int, default=SCADENZA_MINUTI)
    ap.add_argument("--concorrenza", type=int, default=CONCORRENZA)
    ap.add_argument("--ignora-finestra", action="store_true",
                    help="solo per prove manuali sorvegliate")
    args = ap.parse_args()

    adesso = _ora_locale()
    if not args.ignora_finestra:
        ok, perche = dentro_la_finestra(adesso)
        if not ok:
            print(f"  {perche}")
            return 0
    scadenza = time.monotonic() + args.scadenza_minuti * 60
    cursore = _leggi_cursore()
    engine = create_engine(os.environ["DATABASE_URL"])

    with engine.connect() as conn:
        ok, perche = scanner_libero(conn)
        if not ok:
            print(f"  {perche}")
            return 0
        restanti = conn.execute(text(
            "SELECT count(*) FROM listings WHERE status='active' AND detail_scraped "
            "AND equipment IS NULL AND id > :c"), {"c": cursore}).scalar()
        righe = conn.execute(text(
            "SELECT id, url FROM listings WHERE status='active' AND detail_scraped "
            "AND equipment IS NULL AND id > :c ORDER BY id LIMIT :n"),
            {"c": cursore, "n": args.blocco}).fetchall()

    if not righe:
        print("  nessuna auto da trattare: il recupero e' completo")
        return 0

    print(f"=== blocco avviato {adesso:%d/%m %H:%M} · {len(righe)} auto "
          f"(ne restano {restanti}) ===", flush=True)

    def lavora(job, client):
        lid, url = job
        r = fetch_detail(client, url)
        # 404/410: fetch_detail lo chiama "sold". Qui non lo e': e' solo una
        # pagina che non c'e' piu'. Lo lasciamo al giro notturno.
        if r.sold or not r.data:
            return []
        return [(lid, r.data)]

    aggiornate = 0
    interrotto = False
    bloccati = False
    lotto: list[tuple] = []

    def scarica(lotto):
        if not lotto:
            return
        with engine.connect() as conn:
            for lid, d in lotto:
                conn.execute(text(
                    "UPDATE listings SET equipment = CAST(:eq AS jsonb), "
                    "paint_type = :pt, body_color_original = :bco, "
                    + ", ".join(f"{c} = :{c}" for c in COLUMNS)
                    + " WHERE id = :id"),
                    {"eq": json.dumps(d["equipment"]) if d["equipment"] is not None else None,
                     "pt": d["paint_type"], "bco": d["body_color_original"],
                     **{c: d[c] for c in COLUMNS}, "id": lid})
            conn.commit()

    try:
        for lid, dati in run_worker_pool(
            jobs=[(r[0], r[1]) for r in righe],
            worker_fn=lavora,
            client_factory=lambda: make_client(3.0, 8.0),
            concurrency=args.concorrenza,
            session_refresh_requests=200,
        ):
            lotto.append((lid, dati))
            aggiornate += 1
            if len(lotto) >= LOTTO_DB:
                scarica(lotto); lotto = []
                print(f"  ...{aggiornate} aggiornate", flush=True)
            if time.monotonic() > scadenza:
                print("  scadenza raggiunta: chiudo il blocco qui")
                interrotto = True
                break
    except BlockedError as e:
        # Insistere dopo un blocco peggiora la situazione per la scansione
        # notturna, che e' il lavoro che conta.
        bloccati = True
        print(f"  BLOCCATI dal sito ({e}) — mi fermo subito")
    finally:
        scarica(lotto)

    # Il cursore avanza SOLO se il blocco e' stato percorso tutto. I worker
    # restituiscono fuori ordine, quindi dopo uno stop anticipato l'id piu'
    # alto fra i riusciti non dice nulla su quali id piu' bassi siano rimasti
    # da fare: portare li' il cursore ne salterebbe alcuni per sempre.
    #
    # Lasciandolo fermo non si perde niente e non si rifa' quasi niente: la
    # query esclude gia' chi ha `equipment` valorizzato, quindi il blocco
    # successivo riprende esattamente da dove serve.
    completo = not (interrotto or bloccati)
    if completo:
        _scrivi_cursore(max(r[0] for r in righe), aggiornate)

    print(f"=== chiuso {_ora_locale():%H:%M} · aggiornate {aggiornate}/{len(righe)} · "
          f"blocco completo: {'si' if completo else 'no, il cursore resta fermo'} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
