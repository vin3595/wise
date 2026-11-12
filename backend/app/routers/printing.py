"""KOT printing.

Every KOT is printed automatically on its preparation area's printer, exactly once. Two ways to print:

* the print bridge (print-bridge/wise_print_bridge.py) on any PC in the outlet sends ESC/POS straight to
  LAN / Wi-Fi thermal printers (printer_host set on the preparation area);
* the browser print station (/print/<print_key>) prints on the printer attached to that PC, for areas
  with no printer_host.

Both sign in with the outlet's print key instead of a staff login. A poll claims jobs for a short lease
(SELECT ... FOR UPDATE SKIP LOCKED), so two stations never print the same KOT; a station that dies before
confirming loses the lease and the KOT is printed by the next poll. Reprints and cancellations of
already-printed KOTs print as marked slips, and every reprint is in the audit log.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..core.kds import business_date
from ..core.printing import job_kind
from ..core.permissions import Perm
from ..db import system_session
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import Kot, Order, Outlet, PrepArea, Tenant, User
from ..schemas import OutletOut, PrepAreaOut, PrintFeed, PrintJob, PrintPrinter, PrintResult
from ..services import audit
from ..services.orders import kot_out
from .common import get_or_404

router = APIRouter(tags=["printing"])

LEASE = timedelta(seconds=45)
MAX_JOBS = 25
STATION_ID = r"^[A-Za-z0-9_\-]{4,40}$"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _outlet_by_key(db: Session, key: str) -> Outlet:
    outlet = db.scalar(select(Outlet).where(Outlet.print_key == key))
    if outlet is None or not outlet.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Print link not found")
    return outlet


@router.get("/public/print/{key}/jobs", response_model=PrintFeed)
def print_jobs(key: str, station: str = Query(pattern=STATION_ID),
               areas: str = Query(default="", description="Comma-separated preparation area ids; empty = all"),
               mode: str = Query(default="bridge", pattern=r"^(bridge|browser)$"),
               peek: bool = Query(default=False, description="Only list the areas; claim nothing")) -> PrintFeed:
    """Claims the KOTs this station should print now. `mode=bridge` takes areas with a network printer,
    `mode=browser` takes areas without one (the station's own printer)."""
    try:
        wanted = {uuid.UUID(a) for a in areas.split(",") if a.strip()}
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Bad area id") from None
    now = _now()
    with system_session() as db:
        outlet = _outlet_by_key(db, key)
        tenant = db.get(Tenant, outlet.tenant_id)
        all_areas = list(db.scalars(select(PrepArea).where(PrepArea.tenant_id == outlet.tenant_id,
                                                           PrepArea.is_active).order_by(PrepArea.sort_order)))
        mine = [a for a in all_areas if (not wanted or a.id in wanted)
                and ((a.printer_host is not None) == (mode == "bridge"))]
        jobs: list[PrintJob] = []
        if outlet.print_kots and mine and not peek:
            for a in mine:
                a.station_seen_at = now
            ids = [a.id for a in mine]
            # KOTs with no area go to the browser station (there is no printer address to send them to).
            area_filter = Kot.prep_area_id.in_(ids)
            if mode == "browser" and not wanted:
                area_filter = or_(area_filter, Kot.prep_area_id.is_(None))
            today = business_date()
            todo = or_(
                and_(Kot.printed_at.is_(None), Kot.status != "cancelled"),
                Kot.reprint_pending.is_(True),
                and_(Kot.status == "cancelled", Kot.printed_at.is_not(None), Kot.cancel_printed_at.is_(None)),
            )
            free = or_(Kot.print_claim.is_(None), Kot.print_claim == station, Kot.print_claim_at < now - LEASE)
            kots = list(db.scalars(
                select(Kot).where(Kot.outlet_id == outlet.id, Kot.business_date >= today - timedelta(days=1),
                                  Kot.created_at >= (outlet.print_since or now), area_filter, todo, free)
                .order_by(Kot.created_at).limit(MAX_JOBS).with_for_update(skip_locked=True)
            ))
            by_id = {a.id: a for a in all_areas}
            for k in kots:
                kind = job_kind(k.status, k.printed_at, k.cancel_printed_at, k.reprint_pending)
                if kind is None:
                    continue
                k.print_claim, k.print_claim_at = station, now
                order = db.get(Order, k.order_id)
                area = by_id.get(k.prep_area_id) if k.prep_area_id else None
                taker = db.get(User, order.created_by) if order and order.created_by else None
                printer = None
                if area is not None:
                    printer = PrintPrinter(host=area.printer_host, port=area.printer_port, paper_mm=area.paper_mm,
                                           copies=area.kot_copies)
                jobs.append(PrintJob(kot_id=k.id, kind=kind, prep_area_id=k.prep_area_id, printer=printer,
                                     printed_before=k.print_count, taken_by=taker.name if taker else None,
                                     kot=kot_out(db, k, order, now)))
        return PrintFeed(outlet_name=outlet.name, app_name=tenant.app_name or tenant.name,
                         enabled=outlet.print_kots, areas=[PrepAreaOut.model_validate(a) for a in mine],
                         jobs=jobs, server_time=now)


@router.post("/public/print/{key}/jobs/{kot_id}", status_code=204)
def print_done(key: str, kot_id: uuid.UUID, body: PrintResult, station: str = Query(pattern=STATION_ID)) -> None:
    """The station reports a job as printed (or failed: the KOT goes back in the queue)."""
    now = _now()
    with system_session() as db:
        outlet = _outlet_by_key(db, key)
        kot = db.scalar(select(Kot).where(Kot.id == kot_id, Kot.outlet_id == outlet.id).with_for_update())
        if kot is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "KOT not found")
        area = db.get(PrepArea, kot.prep_area_id) if kot.prep_area_id else None
        if kot.print_claim not in (None, station):
            return  # another station took it over after our lease ran out; its result counts
        kot.print_claim = kot.print_claim_at = None
        if not body.ok:
            if area is not None:
                area.last_print_error, area.last_print_error_at = (body.error or "Printer didn't respond")[:200], now
            return
        if body.kind == "cancel":
            kot.cancel_printed_at = kot.cancel_printed_at or now
        else:
            kot.printed_at = kot.printed_at or now
            kot.print_count += 1
            if body.kind == "reprint":
                kot.reprint_pending = False
        if area is not None:
            area.last_print_ok_at, area.last_print_error = now, None


@router.post("/orders/kots/{kot_id}/reprint", status_code=204)
def reprint_kot(kot_id: uuid.UUID, auth: Auth = Depends(require(Perm.kds_view)), db: Session = Depends(get_db)) -> None:
    """Prints the KOT again, marked REPRINT with the count, so a duplicate can't pass as a new order."""
    kot = get_or_404(db, Kot, kot_id, "KOT")
    ensure_outlet_access(db, auth, kot.outlet_id)
    if kot.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "This KOT was cancelled")
    if kot.printed_at is None:
        return  # not printed yet: it is already in the queue
    kot.reprint_pending = True
    audit.record(db, auth.tenant_id, auth.user_id, "reprint_kot", "kot", kot.id, after={"times_printed": kot.print_count})


@router.post("/tenant/outlets/{outlet_id}/print-key", response_model=OutletOut)
def rotate_print_key(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.outlets_manage)),
                     db: Session = Depends(get_db)):
    """New key for the print bridge / station (the old one stops working)."""
    outlet = get_or_404(db, Outlet, outlet_id, "Outlet")
    outlet.print_key = secrets.token_urlsafe(16)
    audit.record(db, auth.tenant_id, auth.user_id, "rotate_print_key", "outlet", outlet.id)
    return outlet
