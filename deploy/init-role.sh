#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=pw="$DESK_DB_PASSWORD" <<'SQL'
CREATE ROLE desk_web LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'pw';
SQL
