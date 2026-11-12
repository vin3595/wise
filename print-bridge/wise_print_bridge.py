#!/usr/bin/env python3
"""Wise print bridge: prints KOTs on network (LAN / Wi-Fi) thermal printers.

Run it on any always-on PC (or a Raspberry Pi) in the outlet, on the same network as the printers.
It asks Wise every 2 seconds for new KOTs, sends each to its preparation area's printer over
ESC/POS (port 9100) and tells Wise it printed, so nothing prints twice and nothing is missed:
if a printer is off, the KOT waits and prints as soon as the printer is back.

    python wise_print_bridge.py --server https://pos.example.com --key <print key>
    python wise_print_bridge.py --test 192.168.1.50          # prints a test slip

Needs only Python 3.9+; no packages to install. The print key is on Kitchen setup > Printing.
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
ESC, GS = b"\x1b", b"\x1d"
INIT = ESC + b"@"
CUT = GS + b"V\x42\x03"          # feed a little and cut (partial cut on most printers)
ALIGN = {"left": ESC + b"a\x00", "center": ESC + b"a\x01"}
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
SIZE = {1: GS + b"!\x00", 2: GS + b"!\x11", "tall": GS + b"!\x01", 3: GS + b"!\x22"}
ORDER_TYPE = {"dine_in": "Dine-in", "takeaway": "Takeaway", "delivery": "Delivery"}
CHAR_SUBS = {"₹": "Rs", "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
             "×": "x", "…": "..."}


def clean(text: str | None) -> str:
    """Thermal printers' built-in fonts are ASCII: swap common symbols, drop what can't print."""
    s = "".join(CHAR_SUBS.get(c, c) for c in (text or ""))
    return s.encode("ascii", "replace").decode("ascii")


def wrap(text: str, width: int, indent: int = 0) -> list[str]:
    words, lines, cur = clean(text).split(), [], ""
    for w in words:
        while len(w) > width - indent:  # very long word: hard split
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[: width - indent])
            w = w[width - indent:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width - indent:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return [(" " * indent if i else "") + ln for i, ln in enumerate(lines)] or [""]


def columns(paper_mm: int) -> int:
    return 32 if paper_mm == 58 else 48


def local_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(IST).strftime("%d-%m-%y %I:%M %p")
    except ValueError:
        return ""


def kot_slip(job: dict, paper_mm: int = 80, copy_no: int = 1, copies: int = 1) -> bytes:
    """ESC/POS bytes for one KOT slip. Big token and table/car so the kitchen can read it at a glance."""
    k, kind, w = job["kot"], job["kind"], columns(paper_mm)
    out = bytearray(INIT)

    def line(text: str = "", size=1, bold=False, align="left") -> None:
        out.extend(ALIGN[align] + SIZE[size] + (BOLD_ON if bold else BOLD_OFF) + clean(text).encode("ascii") + b"\n")

    line(k["prep_area_name"].upper(), 2, True, "center")
    if kind == "cancel":
        line("*** CANCELLED ***", 2, True, "center")
        line("Do not make this order", 1, True, "center")
    elif kind == "reprint":
        line(f"*** REPRINT {job.get('printed_before', 1) + 1} ***", 1, True, "center")
    if k.get("is_addon_kot") and kind != "cancel":
        line("ADDED ITEMS", 1, True, "center")
    if copies > 1:
        line(f"Copy {copy_no} of {copies}", 1, False, "center")
    line(f"TOKEN {k['token_no']}", 3, True, "center")
    where = k.get("table_label") and f"Table {k['table_label']}" or k.get("car_number") and f"Car {k['car_number']}"
    if where:
        line(where, 2, True, "center")  # double width: up to 16 chars on 58 mm paper, 24 on 80 mm
    line(ORDER_TYPE.get(k["order_type"], k["order_type"]), 1, True, "center")
    line(f"KOT {k['kot_no']}  {local_time(k['created_at'])}", 1, False, "center")
    who = [x for x in (k.get("customer_name") and f"For {k['customer_name']}", job.get("taken_by") and f"By {job['taken_by']}") if x]
    if who:
        line("  ".join(who)[:w], 1, False, "center")
    line("-" * w)
    count = 0
    for ln in k["lines"]:
        count += ln["qty"]
        name = ln["item_name"] + (f" ({ln['variant_name']})" if ln["variant_name"] and ln["variant_name"] != "Regular" else "")
        for part in wrap(f"{ln['qty']} x {name}", w, 4):  # double height, normal width
            line(part, "tall" if kind != "cancel" else 1, True)
        addons = [a.get("name", "") for a in ln.get("addons") or []]
        if addons:
            for part in wrap("+ " + ", ".join(addons), w - 4, 2):
                line("    " + part)
        if ln.get("notes"):
            for part in wrap("Note: " + ln["notes"], w - 4, 2):
                line("    " + part, 1, True)
    line("-" * w)
    if k.get("order_notes"):
        for part in wrap("ORDER NOTE: " + k["order_notes"], w):
            line(part, 1, True)
    line(f"Items: {count}")
    out.extend(b"\n\n" + CUT)
    return bytes(out)


def test_slip(paper_mm: int = 80) -> bytes:
    w = columns(paper_mm)
    return (INIT + ALIGN["center"] + SIZE[2] + BOLD_ON + b"WISE\n" + SIZE[1] + BOLD_OFF + b"Printer test\n"
            + ("-" * w).encode() + b"\n" + datetime.now(IST).strftime("%d-%m-%y %I:%M %p").encode()
            + b"\nIf you can read this, the printer works.\n\n\n" + CUT)


def send_to_printer(host: str, port: int, data: bytes, timeout: float = 6.0) -> None:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(data)


class Wise:
    def __init__(self, server: str, key: str, station: str, areas: str = "") -> None:
        self.base = server.rstrip("/") + "/api/public/print/" + urllib.parse.quote(key)
        self.station, self.areas = station, areas

    def _call(self, method: str, url: str, body: dict | None = None) -> dict | None:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def jobs(self) -> dict:
        q = urllib.parse.urlencode({"station": self.station, "areas": self.areas, "mode": "bridge"})
        return self._call("GET", f"{self.base}/jobs?{q}") or {}

    def report(self, kot_id: str, kind: str, ok: bool, error: str | None = None) -> None:
        q = urllib.parse.urlencode({"station": self.station})
        self._call("POST", f"{self.base}/jobs/{kot_id}?{q}", {"kind": kind, "ok": ok, "error": error})


def station_id() -> str:
    return ("bridge-" + re.sub(r"[^A-Za-z0-9_-]", "", socket.gethostname()))[:40].ljust(4, "x")


def print_job(job: dict) -> str | None:
    """Prints one job; returns an error message or None."""
    p = job.get("printer")
    if not p or not p.get("host"):
        return "This area has no printer address"
    try:
        data = b"".join(kot_slip(job, p["paper_mm"], n, p["copies"]) for n in range(1, p["copies"] + 1))
        send_to_printer(p["host"], p["port"], data)  # all copies in one connection
        return None
    except OSError as e:
        return f"Printer {p['host']} not reachable ({e.__class__.__name__})"


def run(wise: Wise, interval: float) -> None:
    print(f"Wise print bridge running as {wise.station}. Leave this window open. Ctrl+C to stop.", flush=True)
    offline = False
    while True:
        try:
            feed = wise.jobs()
            if offline:
                print("Back online.", flush=True)
                offline = False
            if not feed.get("enabled"):
                time.sleep(max(interval, 10))
                continue
            for job in feed.get("jobs", []):
                err = print_job(job)
                k = job["kot"]
                stamp = datetime.now(IST).strftime("%H:%M:%S")
                print(f"{stamp}  {job['kind']:<7} token {k['token_no']} KOT {k['kot_no']} {k['prep_area_name']}: "
                      + ("printed" if err is None else err), flush=True)
                wise.report(job["kot_id"], job["kind"], err is None, err)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print("The print key is wrong or was changed. Copy the new key from Kitchen setup.", flush=True)
                time.sleep(30)
            else:
                print(f"Wise answered {e.code}; retrying.", flush=True)
        except (urllib.error.URLError, OSError, ValueError) as e:
            if not offline:
                print(f"Can't reach Wise ({e}); KOTs will print when the internet is back.", flush=True)
                offline = True
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Prints Wise KOTs on network thermal printers.")
    ap.add_argument("--server", help="Your Wise web address, e.g. https://pos.shakewallah.in")
    ap.add_argument("--key", help="Print key from Kitchen setup > Printing")
    ap.add_argument("--areas", default="", help="Only these preparation area ids (comma-separated). Default: all")
    ap.add_argument("--interval", type=float, default=2.0, help="Seconds between checks (default 2)")
    ap.add_argument("--test", metavar="PRINTER_IP", help="Print a test slip on this printer and exit")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--paper", type=int, choices=(58, 80), default=80)
    a = ap.parse_args(argv)
    if a.test:
        try:
            send_to_printer(a.test, a.port, test_slip(a.paper))
        except OSError as e:
            print(f"Couldn't reach the printer at {a.test}:{a.port} ({e}).")
            return 1
        print("Test slip sent.")
        return 0
    if not a.server or not a.key:
        ap.error("--server and --key are required (or use --test PRINTER_IP)")
    try:
        run(Wise(a.server, a.key, station_id(), a.areas), a.interval)
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
