#!/bin/sh
set -eu

KEYCLOAK_DB="${KEYCLOAK_POSTGRES_DB:-keycloak}"
case "$KEYCLOAK_DB" in
  [A-Za-z_]*)
    ;;
  *)
    echo "KEYCLOAK_POSTGRES_DB must be a valid unquoted PostgreSQL identifier" >&2
    exit 1
    ;;
esac
case "$KEYCLOAK_DB" in
  *[!A-Za-z0-9_]*)
    echo "KEYCLOAK_POSTGRES_DB must be a valid unquoted PostgreSQL identifier" >&2
    exit 1
    ;;
esac

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
SELECT 'CREATE DATABASE ${KEYCLOAK_DB}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${KEYCLOAK_DB}')\gexec
SQL
