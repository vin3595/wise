#!/usr/bin/env python3
"""Rush-hour test for Wise: your busiest hour, three times over, then a report card.

Run it on the Wise Box with `sudo wise loadtest` (it uses a separate, throwaway copy of Wise, so your
real bills are never touched), or point it at any test server:

    python3 loadtest.py --server http://127.0.0.1:8089 --minutes 5 --scale 3

What it simulates (scale 1 = one restaurant's peak; scale 3 = three times that):
  captains     5 x scale   dine-in orders at random tables, sometimes adding items later
  counters     2 x scale   takeaway orders billed straight away (cash / UPI), the open-orders list refreshing
  QR phones    2 x scale   customer orders that a cashier accepts
  kiosk        1 x scale   orders paid at the counter, confirmed by a cashier
  kitchen      2 x scale   kitchen screens refreshing and moving tickets to preparing / ready
  TV           1 x scale   the order-ready screen refreshing
  printing     1           the print bridge taking KOTs and confirming them

Targets (95% of taps must be faster): bill 0.5 s, order 0.5 s, screens 0.3 s, KOT on a printer 3 s.
Afterwards it checks the books: every bill number used once with no gaps, every KOT printed exactly
once, payments equal bill totals. Needs only Python 3.9+.
"""
from __future__ import annotations

import argparse
import http.client
import json
import random
import statistics
import string
import sys
import threading
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone

TARGETS_MS = {"bill": 500, "order": 500, "add items": 500, "accept QR": 500, "kiosk paid": 700,
              "open orders": 300, "kitchen screen": 300, "TV screen": 300, "ticket move": 300}
KOT_PRINT_TARGET_S = 3.0


class Client:
    """One keep-alive connection per virtual device, like a real browser tab."""

    def __init__(self, base: str, token: str | None = None):
        u = urllib.parse.urlparse(base)
        self.host, self.port, self.https = u.hostname, u.port or (443 if u.scheme == "https" else 80), u.scheme == "https"
        self.token = token
        self.conn: http.client.HTTPConnection | None = None

    def _conn(self) -> http.client.HTTPConnection:
        if self.conn is None:
            cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
            self.conn = cls(self.host, self.port, timeout=30)
        return self.conn

    def call(self, method: str, path: str, body=None):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for attempt in (1, 2):
            try:
                c = self._conn()
                c.request(method, "/api" + path, body=data, headers=headers)
                r = c.getresponse()
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
            except (http.client.HTTPException, OSError):
                self.conn = None
                if attempt == 2:
                    raise


class Stats:
    def __init__(self) -> None:
        self.times: dict[str, list[float]] = defaultdict(list)
        self.errors: dict[str, list[str]] = defaultdict(list)
        self.kot_delays: list[float] = []
        self.lock = threading.Lock()

    def timed(self, name: str, client: Client, method: str, path: str, body=None, ok=(200, 201, 204)):
        t = time.perf_counter()
        try:
            status, data = client.call(method, path, body)
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.errors[name].append(f"no answer: {e.__class__.__name__}")
            return None
        ms = (time.perf_counter() - t) * 1000
        with self.lock:
            self.times[name].append(ms)
            if status not in ok:
                detail = data.get("detail") if isinstance(data, dict) else data
                self.errors[name].append(f"{status}: {str(detail)[:120]}")
                return None
        return data if data is not None else {}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


# ------------------------------------------------------------------ setting up a test restaurant

def setup(base: str, scale: int) -> dict:
    anon = Client(base)
    tag = "".join(random.choices(string.ascii_lowercase, k=6))
    login = f"rush{tag}"
    phone = "9" + "".join(random.choices(string.digits, k=9))
    body = {"login_id": login, "restaurant_name": "Rush Test", "owner_name": "Owner", "email": f"{login}@example.com",
            "phone": phone}
    st, d = anon.call("POST", "/auth/signup/start", body)
    if st != 200:
        sys.exit(f"Couldn't create the test restaurant ({st}: {d}). Is this a test server with sign-up open?")
    if not d.get("dev_otp"):
        sys.exit("This server doesn't show sign-up codes, so it isn't a test copy. Use: sudo wise loadtest")
    st, d = anon.call("POST", "/auth/signup/verify", {**body, "otp": d["dev_otp"]})
    owner = Client(base, d["token"])
    _, brands = owner.call("GET", "/tenant/brands")
    brand = brands[0]["id"]
    tables = [f"T{i}" for i in range(1, 9 * scale + 1)]
    _, outlet = owner.call("POST", "/tenant/outlets", {"brand_id": brand, "name": "Rush", "code": "RT", "qr_ordering": True,
                                                      "qr_auto_accept": False, "kiosk_enabled": True, "print_kots": True,
                                                      "table_labels": tables, "upi_vpa": "rush@okaxis"})
    oid = outlet["id"]
    areas = [owner.call("POST", "/kitchen/prep-areas", {"name": n, "printer_host": f"10.0.0.{i + 50}"})[1]["id"]
             for i, n in enumerate(["Shake counter", "Kitchen"])]
    _, taxes = owner.call("GET", "/menu/tax-groups")
    _, cat = owner.call("POST", "/menu/categories", {"brand_id": brand, "name": "Menu"})
    variants = []
    for i in range(24):
        _, it = owner.call("POST", "/menu/items", {
            "brand_id": brand, "category_id": cat["id"], "name": f"Item {i + 1}", "prep_area_id": areas[i % 2],
            "tax_group_id": taxes[0]["id"] if taxes else None,
            "variants": [{"name": "Regular", "price_paise": 9900 + 1000 * (i % 9)}, {"name": "Large", "price_paise": 19900 + 1000 * (i % 9)}]})
        variants += [v["id"] for v in it["variants"]]

    def staff(role: str, n: int, base_pin: int) -> list[Client]:
        out = []
        for k in range(n):
            pin = str(base_pin + k)
            st, _ = owner.call("POST", "/users", {"name": f"{role.title()} {k + 1}", "role": role, "pin": pin, "outlet_ids": [oid]})
            st, tok = anon.call("POST", "/auth/pin", {"login_id": login, "outlet_id": oid, "pin": pin})
            if st != 200:
                sys.exit(f"Couldn't sign in test {role}: {tok}")
            out.append(Client(base, tok["token"]))
        return out

    cashiers = staff("cashier", 2 * scale, 4100)
    captains = staff("captain", 5 * scale, 6100)
    kitchen = staff("kitchen", 2 * scale, 5100)
    for c in cashiers:
        c.call("POST", "/shifts", {"outlet_id": oid, "opening_denoms": {"n500": 4, "n100": 10}})
    return {"login": login, "outlet": outlet, "oid": oid, "variants": variants, "tables": tables, "owner": owner,
            "cashiers": cashiers, "captains": captains, "kitchen": kitchen, "areas": areas}


# ------------------------------------------------------------------ virtual devices

def items(env: dict, n: int | None = None) -> list[dict]:
    return [{"variant_id": random.choice(env["variants"]), "qty": random.choice([1, 1, 1, 2])}
            for _ in range(n or random.randint(1, 4))]


def captain(env, me: Client, stats: Stats, stop: threading.Event):
    while not stop.is_set():
        o = stats.timed("order", me, "POST", "/orders", {"outlet_id": env["oid"], "order_type": "dine_in",
                                                         "table_label": random.choice(env["tables"]), "items": items(env)})
        if o and random.random() < 0.3:
            stop.wait(random.uniform(2, 5))
            stats.timed("add items", me, "POST", f"/orders/{o['id']}/items", {"items": items(env, 1)})
        stop.wait(random.uniform(4, 8))


def counter(env, me: Client, stats: Stats, stop: threading.Event, pending: list, lock: threading.Lock):
    last_list = 0.0
    while not stop.is_set():
        if time.monotonic() - last_list > 3:
            stats.timed("open orders", me, "GET", f"/orders?outlet_id={env['oid']}")
            stats.timed("waiting QR", me, "GET", f"/orders-pending?outlet_id={env['oid']}")
            last_list = time.monotonic()
        with lock:
            job = pending.pop(0) if pending else None
        if job:
            kind, oid = job
            if kind == "qr":
                stats.timed("accept QR", me, "POST", f"/orders/{oid}/accept", {})
            else:
                stats.timed("kiosk paid", me, "POST", f"/orders/{oid}/accept-paid", {"mode": "cash"})
            continue
        o = stats.timed("order", me, "POST", "/orders", {"outlet_id": env["oid"], "order_type": "takeaway", "items": items(env)})
        if o:
            p = stats.timed("bill preview", me, "POST", "/bills/preview", {"order_id": o["id"]})
            if p:
                mode = random.choice(["cash", "cash", "upi", "card"])
                stats.timed("bill", me, "POST", "/bills", {
                    "order_id": o["id"], "customer": {"phone": "98" + "".join(random.choices(string.digits, k=8))}
                    if random.random() < 0.6 else {"declined": True},
                    "payments": [{"mode": mode, "amount_paise": p["total_paise"]}]})
        stop.wait(random.uniform(3, 6))


def qr_phone(env, stats: Stats, stop: threading.Event, pending: list, lock: threading.Lock, base: str):
    me = Client(base)
    key = env["outlet"]["qr_key"]
    while not stop.is_set():
        stats.timed("QR menu", me, "GET", f"/public/qr/{key}")
        o = stats.timed("QR order", me, "POST", f"/public/qr/{key}/orders", {
            "table": random.choice(env["tables"]), "customer_name": "Guest", "customer_phone": "9811122233",
            "items": items(env)})
        if o:
            with lock:
                pending.append(("qr", o["order_id"]))
        stop.wait(random.uniform(15, 30))


def kiosk(env, stats: Stats, stop: threading.Event, pending: list, lock: threading.Lock, base: str):
    me = Client(base)
    key = env["outlet"]["qr_key"]
    while not stop.is_set():
        basket = items(env)
        stats.timed("kiosk quote", me, "POST", f"/public/kiosk/{key}/quote", {"items": basket})
        o = stats.timed("kiosk order", me, "POST", f"/public/kiosk/{key}/orders", {
            "order_type": "takeaway", "customer_name": "Kiosk", "customer_phone": "9811122244", "pay": "counter",
            "items": basket})
        if o:
            with lock:
                pending.append(("kiosk", o["order_id"]))
        stop.wait(random.uniform(20, 40))


def kitchen_screen(env, me: Client, area: str, stats: Stats, stop: threading.Event):
    while not stop.is_set():
        feed = stats.timed("kitchen screen", me, "GET", f"/kds/outlets/{env['oid']}?prep_area_id={area}")
        for k in (feed or {}).get("kots", [])[:3]:
            nxt = {"new": "preparing", "preparing": "ready"}.get(k["status"])
            if nxt and random.random() < 0.5:
                stats.timed("ticket move", me, "POST", f"/kds/kots/{k['id']}/status", {"status": nxt}, ok=(200, 409))
        stop.wait(4)


def tv(env, stats: Stats, stop: threading.Event, base: str):
    me = Client(base)
    while not stop.is_set():
        stats.timed("TV screen", me, "GET", f"/public/display/{env['outlet']['display_key']}")
        stop.wait(3)


def printer(env, stats: Stats, stop: threading.Event, base: str, printed: dict):
    me = Client(base)
    key = env["outlet"]["print_key"]
    while not stop.is_set():
        feed = stats.timed("print jobs", me, "GET", f"/public/print/{key}/jobs?station=loadtest&mode=bridge")
        now = datetime.now(timezone.utc)
        for j in (feed or {}).get("jobs", []):
            created = datetime.fromisoformat(j["kot"]["created_at"].replace("Z", "+00:00"))
            if j["kind"] == "kot":
                with stats.lock:
                    stats.kot_delays.append((now - created).total_seconds())
            stats.timed("print done", me, "POST", f"/public/print/{key}/jobs/{j['kot_id']}?station=loadtest", {"kind": j["kind"]})
            printed[j["kot_id"]] = printed.get(j["kot_id"], 0) + (1 if j["kind"] == "kot" else 0)
        stop.wait(2)


# ------------------------------------------------------------------ checks and report

def check_books(env, printed: dict) -> tuple[list[str], int]:
    owner = env["owner"]
    problems = []
    _, r = owner.call("GET", f"/reports/r/orders?outlet_id={env['oid']}")
    rows = (r or {}).get("rows", [])
    seqs = sorted(int(x["invoice_no"].rsplit("/", 1)[1]) for x in rows)
    if len(seqs) != len(set(seqs)):
        problems.append("a bill number was used twice")
    if seqs and seqs != list(range(seqs[0], seqs[0] + len(seqs))):
        problems.append("gaps in the bill numbers")
    _, pays = owner.call("GET", f"/reports/r/payments?outlet_id={env['oid']}")
    total_bills = (r or {}).get("totals", {}) or {}
    total_pay = (pays or {}).get("totals", {}) or {}
    if total_bills.get("total_paise") != total_pay.get("amount_paise"):
        problems.append(f"payments {total_pay.get('amount_paise')} don't equal bill totals {total_bills.get('total_paise')}")
    twice = [k for k, n in printed.items() if n > 1]
    if twice:
        problems.append(f"{len(twice)} KOTs printed more than once")
    return problems, len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Rush-hour test for Wise (use a test copy, never the live restaurant)")
    ap.add_argument("--server", default="http://127.0.0.1:8089")
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--scale", type=int, default=3, help="how many times your peak hour (default 3)")
    a = ap.parse_args()

    print(f"Setting up a test restaurant on {a.server} ...", flush=True)
    env = setup(a.server, a.scale)
    stats, stop, lock, pending, printed = Stats(), threading.Event(), threading.Lock(), [], {}
    threads = []
    for c in env["captains"]:
        threads.append(threading.Thread(target=captain, args=(env, c, stats, stop)))
    for c in env["cashiers"]:
        threads.append(threading.Thread(target=counter, args=(env, c, stats, stop, pending, lock)))
    for _ in range(2 * a.scale):
        threads.append(threading.Thread(target=qr_phone, args=(env, stats, stop, pending, lock, a.server)))
    for _ in range(a.scale):
        threads.append(threading.Thread(target=kiosk, args=(env, stats, stop, pending, lock, a.server)))
    for i, k in enumerate(env["kitchen"]):
        threads.append(threading.Thread(target=kitchen_screen, args=(env, k, env["areas"][i % 2], stats, stop)))
    for _ in range(a.scale):
        threads.append(threading.Thread(target=tv, args=(env, stats, stop, a.server)))
    threads.append(threading.Thread(target=printer, args=(env, stats, stop, a.server, printed)))
    print(f"Running {len(threads)} devices for {a.minutes:g} minutes ({a.scale}x a busy hour) ...", flush=True)
    for t in threads:
        t.daemon = True
        t.start()
    end = time.monotonic() + a.minutes * 60
    while time.monotonic() < end:
        time.sleep(min(15, max(0, end - time.monotonic())))
        done = sum(len(v) for v in stats.times.values())
        print(f"  {int(end - time.monotonic())} s left, {done} requests so far", flush=True)
    stop.set()
    for t in threads:
        t.join(timeout=35)

    print("\nAction            count   median    95%      worst   errors   target  result")
    failed = False
    for name in sorted(stats.times, key=lambda n: -len(stats.times[n])):
        v = stats.times[name]
        p95 = pct(v, 95)
        target = TARGETS_MS.get(name)
        bad = (target is not None and p95 > target) or bool(stats.errors.get(name))
        failed |= bad
        print(f"{name:<16} {len(v):>6} {statistics.median(v):>7.0f}ms {p95:>6.0f}ms {max(v):>7.0f}ms {len(stats.errors.get(name, [])):>6}   "
              f"{(str(target) + 'ms') if target else '':>7}  {'SLOW / ERRORS' if bad else 'ok'}")
    if stats.kot_delays:
        p95 = pct(stats.kot_delays, 95)
        bad = p95 > KOT_PRINT_TARGET_S
        failed |= bad
        print(f"{'KOT to printer':<16} {len(stats.kot_delays):>6} {statistics.median(stats.kot_delays):>8.1f}s {p95:>6.1f}s "
              f"{max(stats.kot_delays):>7.1f}s {'':>6}   {KOT_PRINT_TARGET_S:>6.0f}s  {'SLOW' if bad else 'ok'}")
    for name, errs in stats.errors.items():
        print(f"\n{name}: {len(errs)} errors, e.g. " + "; ".join(sorted(set(errs))[:3]))
    problems, bills = check_books(env, printed)
    print(f"\nBooks: {bills} bills. " + ("All bill numbers in order, every KOT printed once, payments match."
                                       if not problems else "PROBLEMS: " + "; ".join(problems)))
    failed |= bool(problems)
    print("\nRESULT: " + ("PASS. Wise keeps up with this rush." if not failed else
                          "NOT YET. Send this report to whoever maintains Wise."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
