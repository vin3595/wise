"""Outlet order types and default; deletable menu items.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New databases already have these columns (0001 creates every table from the models).
    op.execute("alter table outlets add column if not exists order_types varchar(40) not null "
               "default 'dine_in,takeaway,delivery'")
    op.execute("alter table outlets add column if not exists default_order_type varchar(10) not null "
               "default 'takeaway'")
    op.execute("alter table items add column if not exists deleted_at timestamptz")


def downgrade() -> None:
    op.execute("alter table items drop column if exists deleted_at")
    op.execute("alter table outlets drop column if exists default_order_type")
    op.execute("alter table outlets drop column if exists order_types")
