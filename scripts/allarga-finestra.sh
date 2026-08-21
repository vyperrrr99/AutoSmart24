#!/usr/bin/env bash
# Porta le marche da 10 a 15 anni di finestra, poche per notte.
#
# Perche' a scaglioni. Allargare tutto insieme farebbe scoprire 43.857 annunci
# in una volta, tutti da arricchire: il ciclo dell'arretrato gira finche' non
# e' vuoto, senza tetto, e a ~70 pagine al minuto sono 10 ore e mezza in piu'.
# La notte passerebbe da 6 a 16 ore, finendo verso mezzogiorno -- oltre la
# riclassificazione delle 09:00, che si arrenderebbe scrivendo
# `scansione_in_corso`, e la BI salterebbe lo snapshot lasciando un buco
# permanente nel suo storico.
#
# Con un tetto di 9.000 annunci a notte si aggiungono circa due ore per volta:
# i giri finiscono oggi fra le 02:18 e le 04:16, quindi restano abbondantemente
# dentro la notte.
#
# Gli incrementi sono misurati, non stimati: sonda su autoscout24.it del
# 19/08/2026, numberOfResults con year_from 2016 contro 2011, marca per marca.
# CUPRA e MG valgono zero perche' non esistevano prima del 2016.
set -uo pipefail
cd /home/vperrone/AutoSmart24 || exit 1

TETTO=${1:-9000}
PSQL="sudo -n docker exec -i autosmart24-postgres-1 psql -U autosmart24 -tA -d autosmart24"

# marca:annunci in piu' passando da 10 a 15 anni
# Underscore al posto dello spazio: "Alfa Romeo" iterato come parola verrebbe
# spezzato in due token e la marca non si troverebbe mai.
INCREMENTI="Fiat:6684 Audi:3827 Volkswagen:3779 BMW:3663 Mercedes-Benz:3216 Ford:2193
Opel:2082 MINI:1905 Peugeot:1811 Renault:1696 Alfa_Romeo:1584 Citroen:1533
Nissan:1330 Lancia:1315 Land_Rover:1162 smart:981 Jeep:871 Porsche:862
Toyota:830 Kia:668 Hyundai:646 Volvo:501 Dacia:453 Skoda:265 CUPRA:0 MG:0"

# Uno scaglione per notte, non uno per esecuzione. Senza questa guardia un
# avvio a mano piu' il cron della sera raddoppierebbero il lavoro notturno,
# che e' esattamente cio' che stiamo evitando.
STAMPO=stato/allarga-finestra.ultimo
if [ -f "$STAMPO" ] && [ "$(cat "$STAMPO")" = "$(date '+%Y-%m-%d')" ]; then
  echo "$(date '+%d/%m %H:%M') uno scaglione e' gia' stato applicato oggi — non ne aggiungo un altro"
  exit 0
fi

# Non si allarga sopra un guasto aperto. Il 19/08 il primo scaglione ha fatto
# prendere un 429 a Fiat: la coda si e' fermata e ci sono costate due notti.
# Aggiungere altre marche mentre il sito ci sta ancora respingendo peggiora un
# problema invece di aspettare che passi.
BLOCCHI=$($PSQL -c "SELECT count(*) FROM scrape_runs WHERE status='blocked' AND started_at >= now() - interval '24 hours';" | tr -d ' ')
if [ -n "$BLOCCHI" ] && [ "$BLOCCHI" != "0" ]; then
  echo "$(date '+%d/%m %H:%M') $BLOCCHI blocchi del sito nelle ultime 24h — non allargo, riprovo domani"
  exit 0
fi

RESTANTI=$($PSQL -c "SELECT count(*) FROM tracked_brands WHERE year_from_years < 15;" | tr -d ' ')
if [ -z "$RESTANTI" ]; then echo "$(date '+%d/%m %H:%M') database non raggiungibile"; exit 1; fi
if [ "$RESTANTI" = "0" ]; then
  echo "$(date '+%d/%m %H:%M') tutte le marche sono gia' a 15 anni — questa riga di crontab si puo' togliere"
  exit 0
fi

echo "=== $(date '+%d/%m %H:%M') · $RESTANTI marche ancora a 10 anni, tetto $TETTO ==="
SPESO=0
SCELTE=""

# Le marche piu' pesanti per prime: riempiono il tetto con meno marche, e le
# code leggere si accorpano bene negli scaglioni successivi.
for VOCE in $(echo "$INCREMENTI" | tr '\n' ' '); do
  MARCA="${VOCE%:*}"; N="${VOCE##*:}"
  MARCA="${MARCA//_/ }"
  GIA=$($PSQL -c "SELECT year_from_years FROM tracked_brands WHERE display_name = '$MARCA';" | tr -d ' ')
  [ "$GIA" = "15" ] && continue
  [ -z "$GIA" ] && { echo "  ATTENZIONE: marca '$MARCA' non trovata a database — salto"; continue; }
  if [ $((SPESO + N)) -gt "$TETTO" ] && [ -n "$SCELTE" ]; then continue; fi
  $PSQL -c "UPDATE tracked_brands SET year_from_years = 15 WHERE display_name = '$MARCA';" >/dev/null
  SPESO=$((SPESO + N))
  SCELTE="$SCELTE $MARCA($N)"
done

if [ -z "$SCELTE" ]; then echo "  nessuna marca allargata"; exit 0; fi
date '+%Y-%m-%d' > "$STAMPO"
echo "  allargate:$SCELTE"
echo "  annunci nuovi attesi stanotte: ~$SPESO"
RESTA=$($PSQL -c "SELECT count(*) FROM tracked_brands WHERE year_from_years < 15;" | tr -d ' ')
echo "=== restano $RESTA marche a 10 anni ==="
