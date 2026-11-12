"""Instant updates for screens (kitchen display, TV, open orders, waiting QR orders).

Every transaction that changes an order, ticket, bill or shift sends a tiny Postgres NOTIFY at commit
(only "outlet X: orders changed", never order contents). Each API process LISTENs on one connection
and pushes the note to the screens of that outlet over Server-Sent Events; the screen then reloads
just what it shows. NOTIFY is delivered only when the transaction commits, so a screen never sees a
change that was rolled back. If the stream drops, screens fall back to checking every few seconds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from ..config import get_settings

log = logging.getLogger("wise.events")
CHANNEL = "wise_events"

# Which tables count as which kind of change.
TOPICS = {"orders": "orders", "kots": "orders", "order_items": "orders", "bills": "bills", "payments": "bills",
          "shifts": "shift", "cash_movements": "shift", "cash_alerts": "cash",
          "outlet_variant_overrides": "menu", "owner_alerts": "alerts"}


def _collect(session: Session, flush_context) -> None:  # noqa: ARG001  (SQLAlchemy hook signature)
    found: set = session.info.setdefault("wise_events", set())
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        topic = TOPICS.get(getattr(obj, "__tablename__", ""))
        outlet = getattr(obj, "outlet_id", None)
        if topic and outlet is not None:
            found.add((str(outlet), topic))
        elif topic and getattr(obj, "order_id", None) is not None:
            found.add(("order:" + str(obj.order_id), topic))  # resolved to the outlet before commit


def _notify(session: Session) -> None:
    found = session.info.pop("wise_events", None)
    if not found:
        return
    outlets: dict[str, set] = defaultdict(set)
    for outlet, topic in found:
        if outlet.startswith("order:"):
            row = session.execute(text("select outlet_id from orders where id = cast(:i as uuid)"),
                                  {"i": outlet[6:]}).first()
            if row is None:
                continue
            outlet = str(row[0])
        outlets[outlet].add(topic)
    for outlet, topics in outlets.items():
        session.execute(text("select pg_notify(:c, :p)"),
                        {"c": CHANNEL, "p": json.dumps({"o": outlet, "t": sorted(topics)})})


def install() -> None:
    """Hooks every session: collect after each flush, NOTIFY once just before commit."""
    event.listen(Session, "after_flush", _collect)
    event.listen(Session, "before_commit", _notify)
    event.listen(Session, "after_rollback", lambda s: s.info.pop("wise_events", None))


class Hub:
    """Screens connected to this process, by outlet."""

    def __init__(self) -> None:
        self.subs: dict[str, set] = defaultdict(set)
        self.lock = threading.Lock()
        self.listening = False

    def subscribe(self, outlet: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        q.loop = asyncio.get_running_loop()  # type: ignore[attr-defined]
        with self.lock:
            self.subs[outlet].add(q)
        return q

    def unsubscribe(self, outlet: str, q: asyncio.Queue) -> None:
        with self.lock:
            self.subs[outlet].discard(q)

    def publish(self, payload: str) -> None:
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        with self.lock:
            targets = list(self.subs.get(msg.get("o", ""), ()))
        for q in targets:
            def put(q=q) -> None:
                if not q.full():
                    q.put_nowait(msg)
            q.loop.call_soon_threadsafe(put)  # type: ignore[attr-defined]


hub = Hub()


def start_listener() -> None:
    """One LISTEN connection per API process, reconnecting forever."""
    if hub.listening:
        return
    hub.listening = True

    def loop() -> None:
        import psycopg  # the driver SQLAlchemy already uses

        url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
        while True:
            try:
                with psycopg.connect(url, autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    log.info("listening for screen updates")
                    for n in conn.notifies():
                        hub.publish(n.payload)
            except Exception:  # noqa: BLE001  (database restarting: try again)
                log.warning("screen-update listener lost the database; retrying in 3 s")
                time.sleep(3)

    threading.Thread(target=loop, name="wise-events", daemon=True).start()


async def stream(outlet: str):
    """Server-Sent Events for one outlet: a note per change, a keep-alive every 20 s."""
    q = hub.subscribe(outlet)
    try:
        yield "retry: 3000\n\n: connected\n\n"
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=20)
                yield f"data: {json.dumps({'t': msg.get('t', [])})}\n\n"
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        hub.unsubscribe(outlet, q)
