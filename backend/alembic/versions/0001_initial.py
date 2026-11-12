"""Initial schema: tenancy, users, auth, menu, audit + row-level security.

Revision ID: 0001
Revises:
Create Date: 2026-11-10
"""
from alembic import op

from app.models import TENANT_TABLES, Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "wise_app"

CURRENT_TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "coalesce(current_setting('app.bypass_rls', true), 'off') = 'on'"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    # Tenants: a session sees only its own tenant row (or all, in system sessions).
    op.execute("alter table tenants enable row level security")
    op.execute("alter table tenants force row level security")
    op.execute(
        f"create policy tenant_isolation on tenants using (id = {CURRENT_TENANT} or {BYPASS}) "
        f"with check (id = {CURRENT_TENANT} or {BYPASS})"
    )
    for table in TENANT_TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(
            f"create policy tenant_isolation on {table} "
            f"using (tenant_id = {CURRENT_TENANT} or {BYPASS}) "
            f"with check (tenant_id = {CURRENT_TENANT} or {BYPASS})"
        )

    # Audit log is append-only for the app role.
    op.execute(f"""
        do $$ begin
          if exists (select 1 from pg_roles where rolname = '{APP_ROLE}') then
            grant usage on schema public to {APP_ROLE};
            grant select, insert, update, delete on all tables in schema public to {APP_ROLE};
            revoke update, delete on audit_log from {APP_ROLE};
          end if;
        end $$;
    """)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
