-- Run once on a new RDS instance by install.sh, as the RDS master user.
-- wise_owner owns the tables; wise_app is what the API uses. wise_app is not the owner and not a
-- superuser, so row-level security keeps every restaurant's data separate.
select format('create role wise_owner login password %L', :'owner_pw')
  where not exists (select 1 from pg_roles where rolname = 'wise_owner') \gexec
select format('create role wise_app login password %L', :'app_pw')
  where not exists (select 1 from pg_roles where rolname = 'wise_app') \gexec
alter role wise_owner password :'owner_pw';
alter role wise_app password :'app_pw';
-- On RDS the master user must be a member of a role to create a database owned by it.
grant wise_owner to :"master";
select 'create database wise owner wise_owner'
  where not exists (select 1 from pg_database where datname = 'wise') \gexec
