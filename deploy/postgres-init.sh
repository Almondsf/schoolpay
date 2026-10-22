#!/bin/sh
# Runs once when the Postgres volume is first created (docker-entrypoint-initdb.d).
# POSTGRES_USER (wardpay) owns the schema and runs migrations.
# wardpay_app is what the API and workers use: rows only, no DDL.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE wardpay_app LOGIN PASSWORD '${APP_DB_PASSWORD}';
GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO wardpay_app;
GRANT USAGE ON SCHEMA public TO wardpay_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE "${POSTGRES_USER}" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO wardpay_app;
ALTER DEFAULT PRIVILEGES FOR ROLE "${POSTGRES_USER}" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO wardpay_app;
SQL
