#!/usr/bin/env bash
# Starts the nightly round if the scheduler failed to.
#
# On 01/08 the round never began. The scheduler was healthy, the timezone was
# right, nothing had restarted -- the laptop had simply slept for eight hours
# and woken at 21:25, and APScheduler's sleeping thread never got its wake-up.
# A machine that closes its lid in the afternoon will do this again.
#
# Deliberately idempotent and run repeatedly rather than once: cron does not
# fire during suspend either, so a single check at a fixed time would be missed
# by exactly the event it exists to catch. Firing every half hour through the
# night means whenever the machine is awake, the check happens.
#
# It only ever starts a round that has not started. It cannot start a second
# one, and it cannot interrupt one in progress.
set -uo pipefail
cd /home/vperrone/AutoSmart24

API=http://localhost:8001
# Quanto si aspetta prima di riprendere da soli dopo un blocco del sito.
RAFFREDDAMENTO_MINUTI=180
PSQL="sudo -n docker exec -i autosmart24-postgres-1 psql -U autosmart24 -tA -d autosmart24"
STAMP=$(date '+%d/%m %H:%M:%S')

# The window opens at tonight's trigger. Computed in UTC because that is what
# the database stores, from local 22:00 -- so it follows the clock across the
# daylight-saving change instead of drifting an hour twice a year.
# Converted through the epoch: `date -u -d "22:00"` reads the input as UTC
# rather than converting local 22:00 into it, which put the window two hours
# late and made a round that started on time look as though it never had.
WHEN="today 22:00"
# Before 22:00 local the window belongs to yesterday evening; without this the
# early-hours checks would look at a trigger that has not happened yet.
[ "$(date '+%H')" -lt 22 ] && WHEN="yesterday 22:00"
SINCE_UTC=$(date -u -d "@$(date -d "$WHEN" +%s)" '+%Y-%m-%d %H:%M:%S')

# L'ordine conta, ed e' costato una notte. Prima questo controllo veniva dopo
# quello sul giro gia' partito, che esce in silenzio: un blocco a meta' giro
# capita proprio quando il giro E' partito, quindi la ripresa automatica era
# codice irraggiungibile. Il 22/08 la coda e' rimasta ferma tredici ore con il
# recupero che girava ogni mezz'ora senza mai guardarla.
#
# Lo stato della coda si legge sempre, e un blocco si gestisce sempre.
QUEUE=$(curl -s -m 10 "$API/queue" 2>/dev/null)
if [ -z "$QUEUE" ]; then
  echo "$STAMP API non raggiungibile — non avvio alla cieca"
  exit 1
fi
case "$QUEUE" in
  *'"current":null'*) ;;                       # coda libera, si può procedere
  *) echo "$STAMP una scansione e' gia' in corso — non avvio"; exit 0 ;;
esac

case "$QUEUE" in
  *'"halted":true'*)
    # Un 429 non e' un divieto, e' una limitazione di frequenza: passa da solo.
    # Fermarsi e' giusto, restare fermi per sempre no. Lo stato del blocco vive
    # nella memoria del processo e nessuno lo toglie mai: il 19/08 un 429 alle
    # 23:57 su Fiat ha fermato la coda, e senza qualcuno che se ne accorgesse
    # sarebbe rimasta ferma indefinitamente. Una notte gia' persa, e tutte le
    # successive.
    #
    # Dopo un periodo di silenzio si riprende da soli. Tre ore: abbondanti per
    # una finestra di rate limiting, e abbastanza brevi da recuperare la stessa
    # notte se il blocco arriva presto.
    FERMA_DA=$(echo "$QUEUE" | python3 -c "
import sys, json, datetime as dt
q = json.load(sys.stdin)
t = q.get('halted_at')
if not t:
    print(-1); raise SystemExit
print(int((dt.datetime.utcnow() - dt.datetime.fromisoformat(t)).total_seconds() // 60))" 2>/dev/null)

    if [ -z "$FERMA_DA" ] || ! [ "$FERMA_DA" -ge 0 ] 2>/dev/null; then
      echo "$STAMP coda ferma, ora del blocco illeggibile - non decido da solo. Riprendi con: curl -X POST $API/queue/resume"
      exit 1
    fi
    if [ "$FERMA_DA" -lt "$RAFFREDDAMENTO_MINUTI" ]; then
      echo "$STAMP coda ferma da ${FERMA_DA} min per un blocco del sito - attendo, riprendo dopo $RAFFREDDAMENTO_MINUTI min"
      exit 0
    fi
    echo "$STAMP coda ferma da ${FERMA_DA} min: raffreddamento finito, riprendo da solo"
    if ! curl -s -m 15 -X POST "$API/queue/resume" >/dev/null 2>&1; then
      echo "$STAMP ripresa FALLITA - la coda resta ferma"
      exit 1
    fi
    # Riprendere sgancia il blocco ma NON riavvia niente: `pending` e' l'elenco
    # delle marche configurate, non lavoro in attesa. Verificato sulla coda
    # vera il 22/08 -- ripresa riuscita, `current` nullo, nessun giro partito.
    # Si prosegue quindi ad accodare le marche rimaste.
    echo "$STAMP ripresa riuscita: accodo le marche rimaste"
    ;;
esac


# Quali marche mancano ancora stanotte. Non "e' partito qualcosa": dopo un
# blocco a meta' giro alcune marche sono andate e altre no, e ripartire da capo
# rifarebbe il lavoro gia' fatto -- cioe' altro carico sul sito che ci ha
# appena respinti.
FATTE=$($PSQL -c "SELECT string_agg(DISTINCT lower(replace(brand,' ','-')), ' ') FROM scrape_runs WHERE started_at >= '$SINCE_UTC' AND status='success';" 2>/dev/null)
if [ -z "$FATTE" ] && [ "$?" != "0" ]; then
  echo "$STAMP database non raggiungibile — non decido nulla"
  exit 1
fi

BRANDS=$(curl -s -m 15 "$API/brands" 2>/dev/null | python3 -c "
import sys, json
try:
    print(' '.join(b['slug'] for b in json.load(sys.stdin) if not b.get('paused')))
except Exception:
    pass" 2>/dev/null)
if [ -z "$BRANDS" ]; then
  echo "$STAMP nessuna marca attiva o API non raggiungibile — non avvio"
  exit 1
fi

# Toglie quelle gia' riuscite stanotte.
DA_FARE=""
for B in $BRANDS; do
  case " $FATTE " in *" $B "*) ;; *) DA_FARE="$DA_FARE $B" ;; esac
done
if [ -z "$DA_FARE" ]; then
  exit 0   # tutte fatte: niente da fare, e niente rumore nel log
fi
BRANDS="$DA_FARE"

echo "$STAMP il giro non è partito da solo — lo avvio io ($(echo "$BRANDS" | wc -w) marche)"
for B in $BRANDS; do
  curl -s -m 15 -X POST "$API/brands/$B/run-now" >/dev/null 2>&1
done
echo "$STAMP avviato"
