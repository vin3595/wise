"""What each cash in / out was for: expense (with category), drawing (to owner or bank), supplier.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table cash_movements add column if not exists purpose varchar(10)")
    op.execute("alter table cash_movements add column if not exists category varchar(40)")
    op.execute("alter table cash_movements add column if not exists handed varchar(6)")
    op.execute("alter table cash_movements add column if not exists paid_to varchar(80)")
    op.execute("alter table cash_movements add column if not exists note varchar(120)")


def downgrade() -> None:
    for c in ("note", "paid_to", "handed", "category", "purpose"):
        op.execute(f"alter table cash_movements drop column if exists {c}")
