"""Database sessions with Postgres row-level security context.

Each request runs in one transaction. Before any query we call
    set_config('app.tenant_id', <tenant uuid>, true)
so RLS policies only expose that tenant's rows. `system_session` sets
app.bypass_rls instead; use it ONLY for cross-tenant lookups that happen
before a tenant is known (signup, login, public branding).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        # 2 API workers x (10 + 10 overflow) stays well under Postgres max_connections=100.
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True,
                                pool_size=10, max_overflow=10, pool_timeout=10, pool_recycle=1800)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def _new_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def set_tenant(session: Session, tenant_id: uuid.UUID | str) -> None:
    session.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
    session.execute(text("select set_config('app.bypass_rls', 'off', true)"))


@contextmanager
def tenant_session(tenant_id: uuid.UUID | str) -> Iterator[Session]:
    session = _new_session()
    try:
        with session.begin():
            set_tenant(session, tenant_id)
            yield session
    finally:
        session.close()


@contextmanager
def system_session() -> Iterator[Session]:
    session = _new_session()
    try:
        with session.begin():
            session.execute(text("select set_config('app.bypass_rls', 'on', true)"))
            yield session
    finally:
        session.close()
