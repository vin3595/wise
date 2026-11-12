-- Two roles: wise_owner owns the tables (runs migrations); wise_app is what the API uses.
-- wise_app is not the table owner and not a superuser, so row-level security applies to it.
-- Change these passwords for any non-local deployment.
create role wise_owner login password 'wise_owner';
create role wise_app login password 'wise_app';
create database wise owner wise_owner;
\connect wise
alter default privileges for role wise_owner in schema public
  grant select, insert, update, delete on tables to wise_app;
grant usage on schema public to wise_app;
