from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrandConfig:
    slug: str
    make_id: int
    display_name: str


MVP_BRANDS: list[BrandConfig] = [
    BrandConfig(slug="fiat", make_id=28, display_name="Fiat"),
    BrandConfig(slug="volkswagen", make_id=74, display_name="Volkswagen"),
    BrandConfig(slug="bmw", make_id=13, display_name="BMW"),
    BrandConfig(slug="audi", make_id=9, display_name="Audi"),
    BrandConfig(slug="mercedes-benz", make_id=47, display_name="Mercedes-Benz"),
]

BASE_URL = "https://www.autoscout24.it"
MAX_RESULTS_PER_QUERY = 4000  # 200 pages x ~20 results/page — autoscout24 pagination cap
RESULTS_PER_PAGE = 20

# Below this mileage a listing is not a used car: new stock, km-0 and
# ex-demo. Measured on 296,685 live listings on 31/07/2026, 11,041 had never
# been registered (49,543 EUR average) and 20,895 were dealer-registered with
# zero mileage at nine months old (33,557 EUR) -- against 23,408 EUR and
# 81,217 km for genuine used cars. Left in, they pull every price statistic
# upward, and unevenly across makes because premium brands carry more of them.
#
# 1000 rather than the 100 first used: a car with 800 km is as much km-0 as one
# with 50, and the figure matches the `is_km_zero` definition in the AutoSmart
# BI spec of 31/07 so the two systems cannot disagree about what a used car is.
#
# A missing mileage counts as below the threshold: only three listings older
# than eighteen months declare none, so treating the gap as new-stock costs
# almost nothing.
MIN_USED_CAR_KM = 1000

# Quante pagine di dettaglio al massimo si leggono in un giro per marca.
#
# Senza tetto il ciclo dell'arretrato gira finche' la coda non e' vuota, quindi
# la durata la decide l'arretrato e non noi. Allargando la finestra a 15 anni
# Fiat si e' ritrovata 11.780 pagine da leggere e ha fatto prendere un 429 due
# volte: a concorrenza 8 dopo 24 minuti, a concorrenza 5 dopo 83. Ogni volta la
# coda si e' fermata, e con essa tutte le marche successive.
#
# Alzato da 2.000 a 4.000 il 02/09/2026. La causa vera dei tre blocchi non era
# il volume ma il difetto dei redirect -- una pagina sparita rimandava alla
# lista del modello e la scaricavamo, migliaia di volte sullo stesso URL --
# corretto il 22/08. Da allora: 114 giri completati, zero blocchi.
#
# A 2.000 il tetto era diventato il collo di bottiglia invece della protezione:
# Fiat aveva 5.162 pagine disponibili e ne leggeva 1.995, fermandosi sul limite
# tre giri di fila senza mai chiudere l'arretrato. Misurato sul giro del 31/08:
# 1.995 pagine in 122 minuti, quindi raddoppiare il tetto costa circa due ore
# in piu' e solo alle due o tre marche che un arretrato ce l'hanno davvero.
#
# NON si alza oltre: il tetto esiste perche' senza di esso la durata del giro
# la decide l'arretrato e non noi, e quella protezione va conservata.
DETAIL_PAGES_PER_RUN = 4000
