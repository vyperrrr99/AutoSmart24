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

STARTED=$($PSQL -c "SELECT count(*) FROM scrape_runs WHERE started_at >= '$SINCE_UTC';" 2>/dev/null | tr -d ' ')
if [ -z "$STARTED" ]; then
  echo "$STAMP database non raggiungibile — non decido nulla"
  exit 1
fi
if [ "$STARTED" -gt 0 ]; then
  exit 0   # il giro è partito: niente da fare, e niente rumore nel log
fi

# Independent of the window: a round in flight is proof enough that one is
# happening, whatever the arithmetic above concluded.
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
    echo "$STAMP coda ferma dopo un blocco — NON avvio. Riprendi con: curl -X POST $API/queue/resume"
    exit 1 ;;
esac

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

echo "$STAMP il giro non è partito da solo — lo avvio io ($(echo "$BRANDS" | wc -w) marche)"
for B in $BRANDS; do
  curl -s -m 15 -X POST "$API/brands/$B/run-now" >/dev/null 2>&1
done
echo "$STAMP avviato"
