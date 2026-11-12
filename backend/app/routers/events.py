"""Live update streams for screens. The database session is closed before streaming starts, so a
screen left open all day doesn't hold a database connection."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..db import system_session, tenant_session
from ..deps import Auth, get_auth
from ..models import Outlet, User, UserOutlet
from ..services.events import hub, stream

router = APIRouter(tags=["events"])
HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}  # nginx: don't buffer the stream


def _check(auth: Auth, outlet_id: uuid.UUID) -> None:
    with tenant_session(auth.tenant_id) as db:
        user = db.get(User, auth.user_id)
        outlet = db.get(Outlet, outlet_id)
        if user is None or not user.is_active or outlet is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Outlet not found")
        if user.role != "owner" and db.get(UserOutlet, {"user_id": user.id, "outlet_id": outlet_id}) is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this outlet")


@router.get("/events")
def staff_events(outlet_id: uuid.UUID, auth: Auth = Depends(get_auth)) -> StreamingResponse:
    if not hub.listening:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Live updates are off; screens refresh by themselves")
    _check(auth, outlet_id)
    return StreamingResponse(stream(str(outlet_id)), media_type="text/event-stream", headers=HEADERS)


@router.get("/public/display/{key}/events")
def display_events(key: str) -> StreamingResponse:
    if not hub.listening:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Live updates are off")
    with system_session() as db:
        outlet_id = db.scalar(select(Outlet.id).where(Outlet.display_key == key, Outlet.is_active))
    if outlet_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Display link not found")
    return StreamingResponse(stream(str(outlet_id)), media_type="text/event-stream", headers=HEADERS)
