"""A tiny stand-in for the Wise API, only to check that loadtest.py runs end to end (not for speed)."""
import json, re, sys, threading, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

L = threading.Lock()
S = {"orders": {}, "kots": {}, "bills": [], "seq": 0}
now = lambda: datetime.now(timezone.utc).isoformat()

def route(method, path, body):
    with L:
        if path == "/auth/signup/start": return 200, {"dev_otp": "123456"}
        if path in ("/auth/signup/verify", "/auth/pin"): return 200, {"token": "t"}
        if path == "/tenant/brands": return 200, [{"id": "b"}]
        if path == "/tenant/outlets": return 201, {"id": "o", "qr_key": "q", "display_key": "d", "print_key": "p"}
        if path == "/kitchen/prep-areas": return 201, {"id": str(uuid.uuid4())}
        if path == "/menu/tax-groups": return 200, [{"id": "tg"}]
        if path == "/menu/categories": return 201, {"id": "c"}
        if path == "/menu/items": return 201, {"variants": [{"id": str(uuid.uuid4())}, {"id": str(uuid.uuid4())}]}
        if path in ("/users", "/shifts"): return 201, {}
        if path == "/orders" and method == "POST" or re.fullmatch(r"/public/(qr|kiosk)/q/orders", path):
            oid = str(uuid.uuid4()); kid = str(uuid.uuid4())
            S["orders"][oid] = {"id": oid}; S["kots"][kid] = {"id": kid, "status": "new", "created_at": now(), "printed": False}
            return 201, {"id": oid, "order_id": oid}
        if re.fullmatch(r"/orders/[^/]+/items", path): return 200, {}
        if path.startswith("/orders?") or path.startswith("/orders-pending"): return 200, []
        if path == "/bills/preview": return 200, {"total_paise": 15600}
        if path == "/bills" or path.endswith("/accept-paid"):
            S["seq"] += 1; S["bills"].append({"invoice_no": f"RT/2627/{S['seq']:06d}", "total_paise": 15600}); return 201, {}
        if path.endswith("/accept"): return 200, {}
        if path.startswith("/kds/outlets/"): return 200, {"kots": [k for k in S["kots"].values() if k["status"] != "ready"][:10]}
        m = re.fullmatch(r"/kds/kots/([^/]+)/status", path)
        if m: S["kots"][m.group(1)]["status"] = body["status"]; return 200, {}
        if path.startswith("/public/display/") or path.startswith("/public/qr/q"): return 200, {}
        if path.startswith("/public/kiosk/q/quote"): return 200, {}
        if path.startswith("/public/print/p/jobs?"):
            jobs = [{"kot_id": k["id"], "kind": "kot", "kot": k} for k in S["kots"].values() if not k["printed"]]
            return 200, {"jobs": jobs[:25]}
        m = re.fullmatch(r"/public/print/p/jobs/([^?]+)\?.*", path)
        if m: S["kots"][m.group(1)]["printed"] = True; return 204, None
        if path.startswith("/reports/r/orders"):
            return 200, {"rows": S["bills"], "totals": {"total_paise": sum(b["total_paise"] for b in S["bills"])}}
        if path.startswith("/reports/r/payments"): return 200, {"totals": {"amount_paise": sum(b["total_paise"] for b in S["bills"])}}
    return 404, {"detail": "mock: " + path}

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def handle_one(self, method):
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        st, data = route(method, self.path[4:], body)
        raw = b"" if data is None else json.dumps(data).encode()
        self.send_response(st); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self): self.handle_one("GET")
    def do_POST(self): self.handle_one("POST")
    def log_message(self, *a): pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
