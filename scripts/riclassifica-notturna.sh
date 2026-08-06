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

# Aspetta la fine della scansione. Con un limite: un'attesa infinita
# resterebbe a girare per sempre se un giro si blocca, e una riclassificazione
# che non parte mai è più difficile da notare di una che dichiara di arrendersi.
for i in $(seq 1 90); do   # x 120s = 3 ore
  CUR=$(curl -s -m 10 "$API/queue" 2>/dev/null | grep -o '"current":null' || true)
  [ -n "$CUR" ] && break
  [ "$i" = "90" ] && { echo "$(date '+%H:%M:%S') scansione ancora in corso dopo 3h — riclassificazione saltata"; exit 1; }
  sleep 120
done

echo "=== riclassificazione avviata $(date '+%d/%m %H:%M:%S') ==="

# L'output si cattura, non si incanala: `cmd | tail` restituisce lo stato di
# uscita di tail, quindi un fallimento sembrerebbe un successo.
if ! OUT=$(sudo -n docker compose run --rm --no-deps \
      -v "$PWD/scripts:/scripts" -v "$PWD/config:/app/config" \
      app python /scripts/riclassifica.py 2>&1); then
  echo "  RICLASSIFICAZIONE FALLITA — le vendite di stanotte restano non verificate"
  echo "$OUT" | tail -5 | sed 's/^/    /'
  exit 1
fi
echo "$OUT" | grep -vE 'Deprecation|warnings.warn' | tail -6 | sed 's/^/  /'
echo "=== conclusa $(date '+%H:%M:%S') ==="
