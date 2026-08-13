#!/bin/sh
# Applies migrations, ensures the Grafana read-only role, and (re)applies seeds.
# Runs inside the db container: `docker compose exec -T db sh /db/apply.sh`.
#
# Migrations are applied once each, tracked in schema_migrations.
# Seeds and grants are idempotent and re-applied on every run, so catalog edits
# propagate without a `make reset`.

set -eu

PSQL="psql --username=$POSTGRES_USER --dbname=$POSTGRES_DB --no-psqlrc --set=ON_ERROR_STOP=1"

echo "==> waiting for postgres"
until pg_isready --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --quiet; do
  sleep 1
done

echo "==> ensuring schema_migrations"
$PSQL --quiet --command "
  SET client_min_messages = warning;
  CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text        PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
  );"

echo "==> migrations"
for file in /db/migrations/*.sql; do
  name=$(basename "$file")
  applied=$($PSQL --tuples-only --no-align --command \
    "SELECT 1 FROM schema_migrations WHERE filename = '$name';")
  if [ "$applied" = "1" ]; then
    echo "    skip    $name"
    continue
  fi
  echo "    apply   $name"
  # Single transaction: a failed migration leaves nothing half-applied.
  $PSQL --quiet --single-transaction --file "$file"
  $PSQL --quiet --command \
    "INSERT INTO schema_migrations (filename) VALUES ('$name');"
done

echo "==> grafana read-only role"
role_exists=$($PSQL --tuples-only --no-align --command \
  "SELECT 1 FROM pg_roles WHERE rolname = '$GRAFANA_DB_USER';")
if [ "$role_exists" = "1" ]; then
  $PSQL --quiet --command \
    "ALTER ROLE \"$GRAFANA_DB_USER\" LOGIN PASSWORD '$GRAFANA_DB_PASSWORD';"
  echo "    updated $GRAFANA_DB_USER"
else
  $PSQL --quiet --command \
    "CREATE ROLE \"$GRAFANA_DB_USER\" LOGIN PASSWORD '$GRAFANA_DB_PASSWORD';"
  echo "    created $GRAFANA_DB_USER"
fi

echo "==> seed"
for file in /db/seed/*.sql; do
  echo "    apply   $(basename "$file")"
  $PSQL --quiet --single-transaction --file "$file"
done

echo "==> grants"
$PSQL --quiet --single-transaction \
  --set=grafana_user="$GRAFANA_DB_USER" --file /db/grants.sql

echo "==> catalog"
$PSQL --command "SELECT kind, count(*) AS metrics FROM metric GROUP BY kind ORDER BY kind;"
echo "==> done"
