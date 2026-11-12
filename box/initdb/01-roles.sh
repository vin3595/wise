#!/bin/bash
# Runs once, when the Wise Box database is first created. Passwords come from /opt/wise/.env.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
  -v owner_pw="$WISE_OWNER_PASSWORD" -v app_pw="$WISE_APP_PASSWORD" <<'SQL'
create role wise_owner login password :'owner_pw';
create role wise_app login password :'app_pw';
create database wise owner wise_owner;
\connect wise
alter default privileges for role wise_owner in schema public
  grant select, insert, update, delete on tables to wise_app;
alter default privileges for role wise_owner in schema public
  grant usage, select on sequences to wise_app;
grant usage on schema public to wise_app;
SQL
