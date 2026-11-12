-- Run inside the "wise" database after rds-init.sql.
alter schema public owner to wise_owner;
alter default privileges for role wise_owner in schema public
  grant select, insert, update, delete on tables to wise_app;
alter default privileges for role wise_owner in schema public
  grant usage, select on sequences to wise_app;
grant usage on schema public to wise_app;
