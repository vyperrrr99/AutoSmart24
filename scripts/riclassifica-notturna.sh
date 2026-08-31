#!/usr/bin/env bash
# Ripulisce le vendite del giro appena concluso. Da eseguire ogni mattina.
#
# Perché esiste come script separato. Fino al 06/08/2026 la riclassificazione
# viveva dentro sync-supabase.sh, che la eseguiva prima di pubblicare. Quel
# giorno la BI ha lasciato Supabase e ha commentato quella riga di crontab --
# scelta giusta per la pubblicazione, ma insieme si sarebbe fermata anche la
# riclassificazione, che con Supabase non c'entra nulla.
#
# La differenza non si sarebbe vista subito. Il database avrebbe continuato a
# riempirsi di annunci `sold` a ritmo normale; solo che una parte non sono
# vendite. Sul giro del 02/08 erano 1.305 su 4.014, un terzo. Il tempo di
# vendita è la metrica su cui la BI è costruita, e ogni sparizione lasciata
# come vendita non è rumore attorno a un valore vero: è un evento inventato.
#
# Ora il database locale è l'unica copia che la BI legge, quindi questa pulizia
# non è più un passo della pubblicazione: è la manutenzione della sorgente.
set -uo pipefail
cd /home/vperrone/AutoSmart24 || exit 1

API=http://localhost:8001
PSQL="sudo -n docker exec -i autosmart24-postgres-1 psql -U autosmart24 -tA -d autosmart24"
ESITO=stato/riclassificazione.json

# Il file di esito, chiesto dalla sessione BI il 07/08/2026. Lo leggono i loro
# due lavori delle 09:15 e 09:30 prima di partire.
#
# Il secondo e' quello che conta. Il rinfresco delle viste su dati non ripuliti
# si ripara da solo il giorno dopo; lo snapshot delle 09:30 no -- scrive la
# fotografia di un giorno passato, irripetibile. Se una notte la scansione
# sfora e questa riclassificazione conclude dopo le 09:30, quel giorno entra
# nel loro storico con un terzo di vendite inventate e resta sbagliato per
# sempre, con un numero perfettamente plausibile al posto di quello vero.
#
# Va scritto a ogni uscita, anche e soprattutto ai fallimenti: leggendo un
# esito diverso da `ok` rinunciano subito, invece di aspettare tre ore un
# lavoro che non arrivera'.
#
# Scrittura atomica: senza il mv finale potrebbero leggere un file a meta'.
scrivi_esito() {
  local tmp
  tmp=$(mktemp "${ESITO}.XXXXXX") || return 0
  printf '{"giorno":"%s","esito":"%s","concluso_at":"%s","dettaglio":"%s"}\n' \
    "$(date '+%Y-%m-%d')" "$1" "$(date '+%Y-%m-%dT%H:%M:%S%:z')" "${2:-}" > "$tmp"
  # mktemp crea a 600: il file lo legge un altro processo, non deve dipendere
  # dal fatto che giri con lo stesso utente.
  chmod 644 "$tmp"
  mv -f "$tmp" "$ESITO"
}

# Aspetta che OGNI marca abbia completato un giro dopo le 22:00 di ieri sera --
# non che la scansione sia "ferma".
#
# Corretto il 01/09/2026 su segnalazione della BI: "current: null" era la
# condizione giusta quando un giro durava due ore, ma da quando la finestra e'
# a 15 anni un giro dura 10-25 ore, e i trigger notturni ripartono ogni sera
# indipendentemente da quello precedente -- quindi la coda puo' restare quasi
# sempre occupata, e "ferma" non arriva piu'. La guardia delle tre ore non era
# rotta: la premessa su cui era tarata (giro breve, coda che si svuota) e'
# diventata falsa. Misurato: sette giorni di storico della BI persi in due
# settimane, cinque per il PC spento e due proprio per questa attesa.
#
# La condizione nuova non dipende da quanto dura un giro ne' da quante volte
# una marca viene ritentata: resta vera finche' ognuna ha almeno un successo
# dopo la soglia, e vale anche se due giri si accavallano.
WHEN="today 22:00"
[ "$(date '+%H')" -lt 22 ] && WHEN="yesterday 22:00"
SOGLIA_UTC=$(date -u -d "@$(date -d "$WHEN" +%s)" '+%Y-%m-%d %H:%M:%S')

giro_completo() {
  local mancanti
  mancanti=$($PSQL -c "
    SELECT count(*) FROM tracked_brands t WHERE NOT t.paused AND NOT EXISTS (
      SELECT 1 FROM scrape_runs r WHERE r.brand = t.display_name
        AND r.status IN ('success','blocked') AND r.started_at >= '$SOGLIA_UTC')
  " 2>/dev/null | tr -d ' ')
  [ "$mancanti" = "0" ]
}

# Limite comunque presente: un giro davvero bloccato (il guard di
# BrandRunGuard incastrato, visto il 31/08 su Fiat) non deve far aspettare
# questo script per sempre. 16,7 ore di margine, molto oltre i giri piu'
# lunghi misurati finora, prima di arrendersi.
for i in $(seq 1 200); do   # x 300s = 16h40
  giro_completo && break
  [ "$i" = "200" ] && {
    echo "$(date '+%H:%M:%S') non tutte le marche hanno completato un giro dopo le 22:00 di ieri — riclassificazione saltata"
    scrivi_esito "giro_incompleto" "non tutte le marche hanno un run completo dopo la soglia dopo 16h40"
    exit 1
  }
  sleep 300
done

echo "=== riclassificazione avviata $(date '+%d/%m %H:%M:%S') ==="

# L'output si cattura, non si incanala: `cmd | tail` restituisce lo stato di
# uscita di tail, quindi un fallimento sembrerebbe un successo.
if ! OUT=$(sudo -n docker compose run --rm --no-deps \
      -v "$PWD/scripts:/scripts" -v "$PWD/config:/app/config" \
      app python /scripts/riclassifica.py 2>&1); then
  echo "  RICLASSIFICAZIONE FALLITA — le vendite di stanotte restano non verificate"
  echo "$OUT" | tail -5 | sed 's/^/    /'
  scrivi_esito "fallita" "riclassifica.py ha restituito un errore"
  exit 1
fi
echo "$OUT" | grep -vE 'Deprecation|warnings.warn' | tail -6 | sed 's/^/  /'
# Dopo la conclusione, mai prima: un file scritto all'avvio direbbe "pronto"
# mentre il lavoro e' a meta'.
scrivi_esito "ok" ""

# Sorveglianza di un mese sul difetto dei redirect chiuso il 25/08/2026: se
# ricompare un altro blocco di "mai arricchiti" lo vediamo qui invece che
# scoprirlo per caso. Solo un report -- non tocca l'esito della notte.
sudo -n docker compose run --rm --no-deps \
  -v "$PWD/scripts:/scripts" -v "$PWD/stato:/app/stato" \
  app python /scripts/controllo-redirect-mensile.py 2>&1 \
  | grep -vE 'Deprecation|warnings.warn' | sed 's/^/  /'

echo "=== conclusa $(date '+%H:%M:%S') ==="
