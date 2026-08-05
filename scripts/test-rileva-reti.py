#!/usr/bin/env python3
"""Verifica lo storico del rilevatore. Si esegue dentro il container:

  docker compose run --rm --no-deps -v "$PWD/scripts:/scripts" \
    -v "$PWD/config:/app/config" app python /scripts/test-rileva-reti.py

Lo storico esiste per una sola ragione: le coppie su cui la decisione e'
rinviata vanno guardate nel tempo. Dodici auto in comune ferme da tre mesi
sono una coincidenza stabile; dodici che diventano venti sono un catalogo
condiviso. Se il confronto fra i giri sbaglia, la differenza sparisce.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("ril", "/scripts/rileva-reti.py")
ril = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ril)


def coppia(d1, d2, cond, quota, n1="Tizio", n2="Caio"):
    """La forma esatta che la query restituisce: gli indici contano."""
    return (d1, n1, 100, d2, n2, 100, cond, quota, cond)


def righe(giri, coppie):
    out = []
    ril.andamento(giri, coppie, out=out.append)
    return "\n".join(out)


fallimenti = []


def verifica(nome, condizione, dettaglio=""):
    if condizione:
        print(f"  ok    {nome}")
    else:
        print(f"  FALLITO  {nome}  {dettaglio}")
        fallimenti.append(nome)


giro_prec = [{"data": "01/07/2026", "coppie": {"10-20": [12, 0.30], "30-40": [8, 0.16]}}]

# --- il caso per cui lo storico esiste -------------------------------------
r = righe(giro_prec, [coppia(10, 20, 19, 0.47)])
verifica("una coppia che sale e' segnalata come tale", "sale" in r, r)
verifica("mostra di quanto e' salita in auto", "+7 auto" in r, r)
verifica("mostra di quanto e' salita in quota", "+0.17" in r, r)

r = righe(giro_prec, [coppia(10, 20, 12, 0.30)])
verifica("una coppia ferma non sembra in crescita", "ferma" in r and "sale" not in r, r)
# " 0 auto, 0.00 " per intero: il solo "0 auto" lo soddisfarebbe anche "+10 auto"
verifica("una coppia ferma mostra zero variazione", " 0 auto, 0.00 " in r, r)

r = righe(giro_prec, [coppia(10, 20, 5, 0.12)])
verifica("una coppia che cala e' segnalata come tale", "scende" in r, r)
verifica("il calo ha segno negativo", "-7 auto" in r, r)

# --- comparse e sparizioni --------------------------------------------------
r = righe(giro_prec, [coppia(77, 88, 9, 0.20)])
verifica("una coppia mai vista prima e' NUOVA", "NUOVA" in r, r)

r = righe(giro_prec, [coppia(10, 20, 12, 0.30)])
verifica("una coppia scesa sotto soglia non sparisce in silenzio",
         "uscita" in r and "30-40" in r, r)

# --- l'ordine degli id non deve creare una coppia diversa -------------------
r = righe([{"data": "01/07/2026", "coppie": {"10-20": [12, 0.30]}}],
          [coppia(20, 10, 12, 0.30)])
verifica("la stessa coppia con gli id invertiti e' riconosciuta",
         "ferma" in r and "NUOVA" not in r, r)

# --- omonimi ----------------------------------------------------------------
# due coppie con gli stessi nomi ma id diversi non devono stampare uguale:
# tre venditori si chiamano esattamente "Gino Spa"
r = righe(giro_prec, [coppia(10, 20, 12, 0.30, "Gino Auto", "Gino Spa"),
                      coppia(10, 99, 5, 0.06, "Gino Auto", "Gino Spa")])
a, b = [x for x in r.splitlines() if "Gino" in x]
verifica("coppie omonime restano distinguibili", a != b and "99" in r, r)

# --- il primo giro non ha nulla con cui confrontarsi ------------------------
r = righe([], [coppia(10, 20, 12, 0.30)])
verifica("senza storico lo dice invece di inventare un confronto",
         "primo giro" in r and "sale" not in r, r)

# --- lettura e scrittura ----------------------------------------------------
with tempfile.TemporaryDirectory() as d:
    f = str(Path(d) / "s.jsonl")
    ril.scrivi_storico(f, [coppia(10, 20, 12, 0.30)], "01/07/2026")
    ril.scrivi_storico(f, [coppia(10, 20, 19, 0.47)], "05/08/2026")
    giri = ril.leggi_storico(f)
    verifica("scrivere due volte conserva entrambi i giri", len(giri) == 2, giri)
    verifica("il giro piu' recente e' l'ultimo",
             giri[-1]["data"] == "05/08/2026", giri)
    verifica("i valori sono quelli scritti",
             giri[-1]["coppie"]["10-20"] == [19, 0.47], giri)

    # una riga rovinata da un'interruzione non deve fermare il rilevatore
    with open(f, "a") as fh:
        fh.write("{meta riga\n")
    verifica("una riga illeggibile viene saltata, non fa crollare tutto",
             len(ril.leggi_storico(f)) == 2, "")

    verifica("un file inesistente vale storico vuoto",
             ril.leggi_storico(str(Path(d) / "manca.jsonl")) == [], "")

    # il confronto deve funzionare sui dati appena riletti, non solo sui finti
    r = righe(ril.leggi_storico(f), [coppia(10, 20, 25, 0.60)])
    verifica("il confronto funziona su uno storico riletto da disco",
             "sale" in r and "+6 auto" in r, r)

print()
if fallimenti:
    print(f"FALLITI {len(fallimenti)}/{len(fallimenti)} : {fallimenti}")
    sys.exit(1)
print("tutti i controlli passati")
