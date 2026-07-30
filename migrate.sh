#!/usr/bin/env bash
set -uo pipefail
PROJECT=/home/vperrone/AutoSmart24
cd "$PROJECT"

export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1

# Tutto l'output va nel log direttamente (NIENTE | tee): evita i freeze da SIGTTOU/SIGTTIN
# quando docker/needrestart toccano il terminale dentro una pipeline.
exec > "$PROJECT/migrate.log" 2>&1

echo "################ compose version ################"
docker compose version

echo "################ 2/6  BUILD IMAGES (cache se già fatta) ################"
docker compose build

echo "################ 3/6  START POSTGRES ################"
docker compose up -d postgres
CID=$(docker compose ps -q postgres)
echo "container postgres = $CID"

echo "-- attendo che postgres sia pronto --"
for i in $(seq 1 40); do
  if docker exec "$CID" pg_isready -U autosmart24 -d autosmart24 >/dev/null 2>&1; then
    echo "postgres PRONTO (dopo ~$((i*2))s)"; break
  fi
  sleep 2
  [ "$i" = 40 ] && { echo "!! postgres non pronto in tempo"; exit 1; }
done

echo "################ 4/6  RESTORE DUMP ################"
echo "-- pg_restore (eventuali warning su DROP di oggetti inesistenti sono NORMALI) --"
docker exec -i "$CID" pg_restore -U autosmart24 -d autosmart24 \
  --no-owner --no-privileges --clean --if-exists < "$PROJECT/autosmart24.dump"
echo "pg_restore exit code: $? (0 = ok, 1 = completato con warning)"

echo "-- tabelle ripristinate --"
docker exec "$CID" psql -U autosmart24 -d autosmart24 -c "\dt"
echo "-- versione alembic registrata --"
docker exec "$CID" psql -U autosmart24 -d autosmart24 -c "SELECT version_num FROM alembic_version;" || echo "(nessuna tabella alembic_version nel dump)"
echo "-- conteggio righe per tabella --"
docker exec "$CID" psql -U autosmart24 -d autosmart24 -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;" || true

echo "################ 5/6  START APP + DASHBOARD ################"
docker compose up -d
sleep 6

echo "################ 6/6  STATO FINALE ################"
docker compose ps
echo "-- log recenti app --"
docker compose logs --tail=30 app 2>&1 || true
echo "################ FATTO ################"
