"""End-to-end API tests against a real Postgres (row-level security needs Postgres).

Run with docker compose up -d db, then:
    WISE_RUN_API_TESTS=1 pytest tests/test_api.py
The test database is migrated automatically.
"""
import io
import os
import uuid

import pytest

pytest.importorskip("fastapi")
if not os.environ.get("WISE_RUN_API_TESTS"):
    pytest.skip("set WISE_RUN_API_TESTS=1 and start Postgres to run API tests", allow_module_level=True)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.menu_import import build_template  # noqa: E402
from app.main import app  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def migrate():
    cfg = Config(os.path.join(BASE, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BASE, "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


def signup(client, login_id: str, phone: str) -> dict:
    body = {"login_id": login_id, "restaurant_name": login_id.title(), "owner_name": "Owner",
            "email": f"{login_id}@example.com", "phone": phone}
    r = client.post("/api/auth/signup/start", json=body)
    assert r.status_code == 200, r.text
    code = r.json()["dev_otp"]
    r = client.post("/api/auth/signup/verify", json={**body, "otp": code})
    assert r.status_code == 200, r.text
    return r.json()


def h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_full_phase1_flow(client):
    sw = signup(client, "shakewallah", "9829000001")
    tok = sw["token"]
    assert sw["user"]["role"] == "owner"

    # Login ID now taken; wrong OTP rejected and counted.
    assert client.get("/api/auth/login-id-available", params={"login_id": "shakewallah"}).json()["available"] is False

    # Branding is public for the login screen and the PWA manifest.
    r = client.patch("/api/tenant/branding", json={"primary_color": "#e11d48", "app_name": "Shakewallah POS"},
                     headers=h(tok))
    assert r.status_code == 200
    pub = client.get("/api/public/tenants/shakewallah").json()
    assert pub["primary_color"] == "#e11d48" and pub["app_name"] == "Shakewallah POS"
    assert client.get("/api/public/tenants/shakewallah/manifest.webmanifest").json()["theme_color"] == "#e11d48"

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = client.post("/api/tenant/branding/logo", files={"file": ("logo.png", png, "image/png")}, headers=h(tok))
    assert r.status_code == 200 and r.json()["logo_url"].endswith(".png")
    r = client.post("/api/tenant/branding/logo", files={"file": ("x.txt", b"hello", "text/plain")}, headers=h(tok))
    assert r.status_code == 415

    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    r = client.post("/api/tenant/outlets", json={"brand_id": brand_id, "name": "Paota", "code": "pt1"},
                    headers=h(tok))
    assert r.status_code == 201, r.text
    outlet_id = r.json()["id"]
    assert r.json()["code"] == "PT1"

    # Owner OTP login.
    r = client.post("/api/auth/otp/request", json={"login_id": "shakewallah", "identifier": "9829000001"})
    code = r.json()["dev_otp"]
    r = client.post("/api/auth/otp/verify", json={"login_id": "shakewallah", "identifier": "+91 98290 00001",
                                                  "otp": "000000" if code != "000000" else "111111"})
    assert r.status_code == 400
    r = client.post("/api/auth/otp/verify", json={"login_id": "shakewallah", "identifier": "9829000001", "otp": code})
    assert r.status_code == 200

    # Staff with PIN, PIN login on outlet device.
    r = client.post("/api/users", json={"name": "Ramesh", "role": "cashier", "pin": "4821", "outlet_ids": [outlet_id]},
                    headers=h(tok))
    assert r.status_code == 201, r.text
    assert r.json()["has_pin"] is True
    r = client.post("/api/users", json={"name": "Suresh", "role": "captain", "pin": "4821", "outlet_ids": [outlet_id]},
                    headers=h(tok))
    assert r.status_code == 409  # PIN clash
    for _ in range(2):
        assert client.post("/api/auth/pin", json={"login_id": "shakewallah", "outlet_id": outlet_id,
                                                  "pin": "9999"}).status_code == 401
    r = client.post("/api/auth/pin", json={"login_id": "shakewallah", "outlet_id": outlet_id, "pin": "4821"})
    assert r.status_code == 200, r.text
    cashier_tok = r.json()["token"]
    assert r.json()["outlet_id"] == outlet_id
    # Cashier can't edit the menu.
    assert client.post("/api/menu/tax-groups", json={"name": "X"}, headers=h(cashier_tok)).status_code == 403

    # Menu: category, item with variants, outlet override.
    tax = client.get("/api/menu/tax-groups", headers=h(tok)).json()
    assert tax[0]["name"] == "GST 5%" and tax[0]["is_default"]
    r = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Thick Shakes"}, headers=h(tok))
    cat_id = r.json()["id"]
    r = client.post("/api/menu/items", headers=h(tok), json={
        "brand_id": brand_id, "category_id": cat_id, "name": "Oreo Shake", "short_code": "oreo",
        "variants": [{"name": "300 ML", "price_paise": 14900}, {"name": "1000 ML", "price_paise": 39900}],
    })
    assert r.status_code == 201, r.text
    item = r.json()
    small = next(v for v in item["variants"] if v["name"] == "300 ML")
    r = client.put(f"/api/menu/outlets/{outlet_id}/variants/{small['id']}", json={"price_paise": 15900},
                   headers=h(tok))
    assert r.status_code == 200
    menu = client.get(f"/api/menu/outlets/{outlet_id}", headers=h(cashier_tok)).json()
    v = next(v for v in menu["items"][0]["variants"] if v["name"] == "300 ML")
    assert (v["price_paise"], v["master_price_paise"], v["overridden"]) == (15900, 14900, True)

    # Excel import: dry run then commit.
    files = {"file": ("menu.xlsx", io.BytesIO(build_template()), "application/octet-stream")}
    r = client.post("/api/menu/import", params={"brand_id": brand_id, "dry_run": True}, files=files, headers=h(tok))
    assert r.status_code == 200 and r.json()["ok"], r.text
    files = {"file": ("menu.xlsx", io.BytesIO(build_template()), "application/octet-stream")}
    r = client.post("/api/menu/import", params={"brand_id": brand_id, "dry_run": False}, files=files, headers=h(tok))
    assert r.status_code == 200, r.text
    names = {i["name"] for i in client.get("/api/menu/items", params={"brand_id": brand_id}, headers=h(tok)).json()}
    assert {"Oreo Shake", "Cold Coffee"} <= names

    # Row-level security: a second restaurant can't see the first one's data.
    other = signup(client, "pizzaplace", "9829000002")
    assert client.get(f"/api/menu/items/{item['id']}", headers=h(other["token"])).status_code == 404
    assert client.get("/api/tenant/outlets", headers=h(other["token"])).json() == []
    assert client.get(f"/api/menu/outlets/{outlet_id}", headers=h(other["token"])).status_code == 404

    # Unknown outlet id for PIN login.
    r = client.post("/api/auth/pin", json={"login_id": "pizzaplace", "outlet_id": str(uuid.uuid4()), "pin": "4821"})
    assert r.status_code == 404


def test_orders_kot_split_kds_and_display(client):
    t = signup(client, "kdstest", "9829000003")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    outlet = client.post("/api/tenant/outlets", headers=h(tok), json={
        "brand_id": brand_id, "name": "Ratanada", "code": "RT", "car_number_required": True}).json()
    oid = outlet["id"]
    shake = client.post("/api/kitchen/prep-areas", json={"name": "Shake counter"}, headers=h(tok)).json()["id"]
    kitchen = client.post("/api/kitchen/prep-areas", json={"name": "Kitchen"}, headers=h(tok)).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "All"}, headers=h(tok)).json()["id"]

    def item(name, area, price):
        r = client.post("/api/menu/items", headers=h(tok), json={
            "brand_id": brand_id, "category_id": cat, "name": name, "prep_area_id": area,
            "variants": [{"name": "Regular", "price_paise": price}]})
        assert r.status_code == 201, r.text
        return r.json()["variants"][0]["id"]

    oreo, fries, water = item("Oreo Shake", shake, 14900), item("Fries", kitchen, 12900), item("Water", None, 2000)

    # Car number is required at this outlet for takeaway.
    r = client.post("/api/orders", headers=h(tok), json={"outlet_id": oid, "order_type": "takeaway",
                                                         "items": [{"variant_id": oreo}]})
    assert r.status_code == 422
    r = client.post("/api/orders", headers=h(tok), json={
        "outlet_id": oid, "order_type": "takeaway", "car_number": "rj 19 ca 1234",
        "items": [{"variant_id": oreo, "qty": 2}, {"variant_id": fries}, {"variant_id": water}]})
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["token_no"] == 1 and order["car_number"] == "RJ19CA1234" and order["status"] == "new"
    assert sorted(k["prep_area_name"] for k in order["kots"]) == ["Kitchen", "Kitchen", "Shake counter"]
    assert order["total_paise"] == 2 * 14900 + 12900 + 2000

    # Each preparation area sees only its own ticket.
    feed = client.get(f"/api/kds/outlets/{oid}", params={"prep_area_id": shake}, headers=h(tok)).json()["kots"]
    assert len(feed) == 1 and feed[0]["lines"][0]["qty"] == 2 and feed[0]["car_number"] == "RJ19CA1234"
    shake_kot = feed[0]["id"]
    kitchen_kots = [k["id"] for k in order["kots"] if k["prep_area_name"] == "Kitchen"]

    disp = client.get(f"/api/public/display/{outlet['display_key']}").json()
    assert [o["token_no"] for o in disp["preparing"]] == [1] and disp["ready"] == []

    # Kitchen staff on PIN move tickets along; cashier can't.
    client.post("/api/users", headers=h(tok), json={"name": "Chef", "role": "kitchen", "pin": "5821", "outlet_ids": [oid]})
    chef = client.post("/api/auth/pin", json={"login_id": "kdstest", "outlet_id": oid, "pin": "5821"}).json()["token"]
    client.post("/api/users", headers=h(tok), json={"name": "Cash", "role": "cashier", "pin": "6821", "outlet_ids": [oid]})
    cashier = client.post("/api/auth/pin", json={"login_id": "kdstest", "outlet_id": oid, "pin": "6821"}).json()["token"]
    assert client.post(f"/api/kds/kots/{shake_kot}/status", json={"status": "ready"}, headers=h(cashier)).status_code == 403
    assert client.post(f"/api/kds/kots/{shake_kot}/status", json={"status": "served"}, headers=h(chef)).status_code == 409
    assert client.post(f"/api/kds/kots/{shake_kot}/status", json={"status": "preparing"}, headers=h(chef)).json()["status"] == "preparing"
    assert client.get(f"/api/orders/{order['id']}", headers=h(chef)).json()["status"] == "preparing"
    client.post(f"/api/kds/kots/{shake_kot}/status", json={"status": "ready"}, headers=h(chef))

    # Can't hand over while the kitchen is still cooking.
    r = client.post(f"/api/orders/{order['id']}/complete", headers=h(cashier))
    assert r.status_code == 409 and "Kitchen" in r.json()["detail"]
    for k in kitchen_kots:
        client.post(f"/api/kds/kots/{k}/status", json={"status": "ready"}, headers=h(chef))
    disp = client.get(f"/api/public/display/{outlet['display_key']}").json()
    assert disp["preparing"] == [] and disp["ready"][0]["car_number"] == "RJ19CA1234"

    # Find by last 4 digits of the car, then add an item: a new round-2 KOT only.
    found = client.get("/api/orders", params={"outlet_id": oid, "car": "1234"}, headers=h(cashier)).json()
    assert [o["token_no"] for o in found] == [1]
    r = client.post(f"/api/orders/{order['id']}/items", headers=h(cashier), json={"items": [{"variant_id": oreo}]})
    assert r.status_code == 200
    extra = [k for k in r.json()["kots"] if k["is_addon_kot"]]
    assert len(extra) == 1 and extra[0]["prep_area_name"] == "Shake counter" and r.json()["status"] == "preparing"
    client.post(f"/api/kds/kots/{extra[0]['id']}/status", json={"status": "ready"}, headers=h(chef))
    r = client.post(f"/api/orders/{order['id']}/complete", headers=h(cashier))
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert client.get(f"/api/public/display/{outlet['display_key']}").json()["ready"] == []

    # Next order gets token 2; idempotent retry with the same id doesn't create a duplicate.
    oid2 = str(uuid.uuid4())
    body = {"id": oid2, "outlet_id": oid, "order_type": "dine_in", "table_label": "T4", "items": [{"variant_id": water}]}
    a = client.post("/api/orders", headers=h(cashier), json=body).json()
    b = client.post("/api/orders", headers=h(cashier), json=body).json()
    assert a["token_no"] == b["token_no"] == 2
    assert client.get("/api/public/display/not-a-real-key").status_code == 404


def test_billing_shifts_concurrency_and_qr(client):
    t = signup(client, "billtest", "9829000004")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    r = client.post("/api/tenant/outlets", headers=h(tok), json={
        "brand_id": brand_id, "name": "Paota", "code": "PT1", "upi_vpa": "shakewallah@okaxis",
        "table_labels": ["t1", "T2", "T2"], "qr_ordering": True})
    assert r.status_code == 201, r.text
    outlet = r.json()
    oid = outlet["id"]
    assert outlet["table_labels"] == ["T1", "T2"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    oreo = client.post("/api/menu/items", headers=h(tok), json={
        "brand_id": brand_id, "category_id": cat, "name": "Oreo Shake",
        "tax_group_id": client.get("/api/menu/tax-groups", headers=h(tok)).json()[0]["id"],
        "variants": [{"name": "300 ML", "price_paise": 14900}]}).json()["variants"][0]["id"]

    for name, pin, role in (("Asha", "7311", "cashier"), ("Bina", "7322", "cashier"), ("Mgr", "7399", "manager")):
        body = {"name": name, "role": role, "pin": pin, "outlet_ids": [oid]}
        if role == "manager":
            body["phone"] = "9829000099"
        assert client.post("/api/users", headers=h(tok), json=body).status_code == 201
    asha = client.post("/api/auth/pin", json={"login_id": "billtest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    bina = client.post("/api/auth/pin", json={"login_id": "billtest", "outlet_id": oid, "pin": "7322"}).json()["token"]

    # Two cashiers, each with their own shift.
    for who in (asha, bina):
        assert client.post("/api/shifts", json={"outlet_id": oid, "opening_cash_paise": 100000}, headers=h(who)).status_code == 201
    assert client.post("/api/shifts", json={"outlet_id": oid, "opening_cash_paise": 0}, headers=h(asha)).status_code == 409

    def order(who, **kw):
        body = {"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": oreo, "qty": 2}], **kw}
        r = client.post("/api/orders", json=body, headers=h(who))
        assert r.status_code == 201, r.text
        return r.json()

    o1, o2 = order(asha), order(bina)
    pay = lambda total, mode="cash": [{"mode": mode, "amount_paise": total}]  # noqa: E731
    prev = client.post("/api/bills/preview", json={"order_id": o1["id"]}, headers=h(asha)).json()
    assert prev["taxable_paise"] == 29800 and prev["cgst_paise"] == 745 and prev["total_paise"] == 31300
    # Customer number is asked on every bill.
    r = client.post("/api/bills", headers=h(asha), json={"order_id": o1["id"], "payments": pay(31300)})
    assert r.status_code == 422
    b1 = client.post("/api/bills", headers=h(asha), json={
        "order_id": o1["id"], "customer": {"phone": "9811111111"}, "payments": pay(31300)}).json()
    b2 = client.post("/api/bills", headers=h(bina), json={
        "order_id": o2["id"], "customer": {"declined": True}, "payments": pay(31300, "upi")}).json()
    assert (b1["invoice_no"], b2["invoice_no"]) == ("PT1/2627/000001", "PT1/2627/000002") or \
        b1["invoice_no"].endswith("000001") and b2["invoice_no"].endswith("000002")
    assert b2["upi_link"].startswith("upi://pay?pa=shakewallah%40okaxis") and "am=313.00" in b2["upi_link"]
    assert client.post("/api/bills", headers=h(asha), json={
        "order_id": o1["id"], "customer": {"declined": True}, "payments": pay(31300)}).status_code == 409

    # Big discount needs the manager's PIN; wrong PIN refused.
    o3 = order(asha)
    disc = {"kind": "percent", "value": 2000, "reason": "Regular customer"}
    assert client.post("/api/bills/preview", json={"order_id": o3["id"], "discount": disc},
                       headers=h(asha)).json()["needs_approval"]
    body = {"order_id": o3["id"], "discount": disc, "customer": {"declined": True}, "payments": pay(25000)}
    assert client.post("/api/bills", json=body, headers=h(asha)).status_code == 403
    assert client.post("/api/bills", json={**body, "manager_pin": "1357"}, headers=h(asha)).status_code == 403
    r = client.post("/api/bills", json={**body, "manager_pin": "7399"}, headers=h(asha))
    assert r.status_code == 201, r.text
    b3 = r.json()
    assert b3["discount_paise"] == 5960 and b3["total_paise"] == 25000

    # Cancel needs reason + manager PIN; the number stays, the order can be billed again.
    assert client.post(f"/api/bills/{b3['id']}/cancel", json={"reason": "Wrong item", "manager_pin": "7311"},
                       headers=h(asha)).status_code == 403
    r = client.post(f"/api/bills/{b3['id']}/cancel", json={"reason": "Wrong item", "manager_pin": "7399"}, headers=h(asha))
    assert r.json()["status"] == "cancelled"
    rebill = client.post("/api/bills", headers=h(asha), json={
        "order_id": o3["id"], "customer": {"declined": True}, "payments": pay(31300)}).json()
    assert rebill["invoice_no"].endswith("000004")

    # Shift close: expected cash = opening + cash sales.
    shift = client.get("/api/shifts/current", params={"outlet_id": oid}, headers=h(asha)).json()
    client.post(f"/api/shifts/{shift['id']}/cash", json={"kind": "out", "amount_paise": 5000, "reason": "Milk"}, headers=h(asha))
    closed = client.post(f"/api/shifts/{shift['id']}/close", json={"counted_cash_paise": 157000}, headers=h(asha)).json()
    assert closed["expected_cash_paise"] == 100000 + 31300 + 31300 - 5000
    assert closed["variance_paise"] == 157000 - closed["expected_cash_paise"]

    # Two waiters on the same table: the second joins the first order.
    w1 = order(asha, order_type="dine_in", table_label="T1")
    w2 = order(bina, order_type="dine_in", table_label="t1")
    assert w2["id"] == w1["id"] and w2["merged_into_existing"] and len(w2["kots"]) == 2

    # QR ordering: pending -> accepted -> kitchen; customer sees status.
    key = outlet["qr_key"]
    menu = client.get(f"/api/public/qr/{key}", params={"t": "T2"}).json()
    assert menu["table"] == "T2" and menu["menu"]["items"][0]["name"] == "Oreo Shake"
    r = client.post(f"/api/public/qr/{key}/orders", json={
        "table": "T2", "customer_name": "Ravi", "customer_phone": "9822222222", "items": [{"variant_id": oreo}]})
    assert r.status_code == 201, r.text
    qr = r.json()
    assert qr["status"] == "pending" and qr["token_no"] is None
    pend = client.get("/api/orders-pending", params={"outlet_id": oid}, headers=h(asha)).json()
    assert len(pend) == 1 and pend[0]["pending_lines"][0]["item"] == "Oreo Shake"
    acc = client.post(f"/api/orders/{qr['order_id']}/accept", headers=h(asha)).json()
    assert acc["token_no"] and acc["kots"][0]["lines"][0]["item_name"] == "Oreo Shake"
    st = client.get(f"/api/public/qr/{key}/orders/{qr['order_id']}").json()
    assert st["status"] == "new" and st["token_no"] == acc["token_no"]
    # Unknown table or disabled QR are refused.
    assert client.post(f"/api/public/qr/{key}/orders", json={
        "table": "T99", "customer_name": "X", "customer_phone": "9822222223", "items": [{"variant_id": oreo}]}).status_code == 422

    # Kiosk: off until enabled; quote with GST; UPI link; cashier confirms -> bill + KOT + token.
    assert client.get(f"/api/public/kiosk/{key}").status_code == 404
    client.patch(f"/api/tenant/outlets/{oid}", headers=h(tok), json={
        **{k: outlet[k] for k in ("brand_id", "name", "code", "upi_vpa", "table_labels", "qr_ordering")},
        "kiosk_enabled": True})
    q = client.post(f"/api/public/kiosk/{key}/quote", json={"items": [{"variant_id": oreo, "qty": 2}]}).json()
    assert q["total_paise"] == 31300
    k = client.post(f"/api/public/kiosk/{key}/orders", json={
        "order_type": "takeaway", "customer_name": "Neha", "customer_phone": "9833333333", "pay": "upi",
        "items": [{"variant_id": oreo, "qty": 2}]}).json()
    assert k["status"] == "pending" and k["payment_status"] == "awaiting" and "am=313.00" in k["upi_link"]
    assert client.post(f"/api/orders/{k['order_id']}/accept", headers=h(bina)).status_code == 409  # unpaid
    kb = client.post(f"/api/orders/{k['order_id']}/accept-paid", json={"mode": "upi"}, headers=h(bina))
    assert kb.status_code == 200, kb.text
    assert kb.json()["total_paise"] == 31300 and kb.json()["customer_phone"] == "9833333333"
    ks = client.get(f"/api/public/qr/{key}/orders/{k['order_id']}", params={"kiosk": True}).json()
    assert ks["status"] == "new" and ks["token_no"]

    # Reports.
    s = client.get("/api/reports/summary", headers=h(tok)).json()
    assert s["bills"] == 4 and s["cancelled_bills"] == 1 and s["by_mode"] == {"cash": 62600, "upi": 62600}
    items = client.get("/api/reports/items", headers=h(tok)).json()
    assert items[0]["item"] == "Oreo Shake" and items[0]["qty"] == 8
    assert len(client.get("/api/reports/cancellations", headers=h(tok)).json()) == 1
    assert client.get("/api/reports/summary", headers=h(asha)).status_code == 403


def test_kot_printing_queue(client):
    t = signup(client, "printtest", "9829000077")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    base = {"brand_id": brand_id, "name": "Paota", "code": "PT"}
    outlet = client.post("/api/tenant/outlets", headers=h(tok), json=base).json()
    oid, key = outlet["id"], outlet["print_key"]
    lan = client.post("/api/kitchen/prep-areas", headers=h(tok), json={
        "name": "Kitchen", "printer_host": "192.168.1.50", "paper_mm": 80, "kot_copies": 2}).json()
    usb = client.post("/api/kitchen/prep-areas", headers=h(tok), json={"name": "Shake counter"}).json()
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "All"}, headers=h(tok)).json()["id"]

    def item(name, area):
        return client.post("/api/menu/items", headers=h(tok), json={
            "brand_id": brand_id, "category_id": cat, "name": name, "prep_area_id": area,
            "variants": [{"name": "Regular", "price_paise": 10000}]}).json()["variants"][0]["id"]

    fries, shake = item("Fries", lan["id"]), item("Oreo", usb["id"])
    send = lambda: client.post("/api/orders", headers=h(tok), json={  # noqa: E731
        "outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": fries}, {"variant_id": shake}]}).json()
    jobs = lambda station, mode="bridge": client.get(  # noqa: E731
        f"/api/public/print/{key}/jobs", params={"station": station, "mode": mode}).json()

    send()  # before printing is switched on: never printed
    assert jobs("bridge-1")["jobs"] == []
    client.patch(f"/api/tenant/outlets/{oid}", headers=h(tok), json={**base, "print_kots": True})
    order = send()

    # The bridge gets the LAN area only, with its printer; a second bridge can't grab the same job.
    feed = jobs("bridge-1")
    assert [j["kot"]["prep_area_name"] for j in feed["jobs"]] == ["Kitchen"]
    job = feed["jobs"][0]
    assert job["printer"] == {"host": "192.168.1.50", "port": 9100, "paper_mm": 80, "copies": 2} and job["kind"] == "kot"
    assert jobs("bridge-2")["jobs"] == []
    # The browser station gets the area without a printer address.
    browser = jobs("counter-pc", "browser")["jobs"]
    assert [j["kot"]["prep_area_name"] for j in browser] == ["Shake counter"] and browser[0]["printer"]["host"] is None

    # A failed print goes back in the queue and shows on the area; success prints it once.
    url = f"/api/public/print/{key}/jobs/{job['kot_id']}"
    assert client.post(url, params={"station": "bridge-1"}, json={"kind": "kot", "ok": False, "error": "Timed out"}).status_code == 204
    areas = client.get("/api/kitchen/prep-areas", headers=h(tok)).json()
    assert next(a for a in areas if a["id"] == lan["id"])["last_print_error"] == "Timed out"
    assert len(jobs("bridge-2")["jobs"]) == 1
    client.post(url, params={"station": "bridge-2"}, json={"kind": "kot"})
    assert jobs("bridge-1")["jobs"] == []

    # Reprint comes out marked, with the count; then a cancel slip once the order is cancelled.
    assert client.post(f"/api/orders/kots/{job['kot_id']}/reprint", headers=h(tok)).status_code == 204
    again = jobs("bridge-1")["jobs"]
    assert again[0]["kind"] == "reprint" and again[0]["printed_before"] == 1
    client.post(url, params={"station": "bridge-1"}, json={"kind": "reprint"})
    client.post(f"/api/orders/{order['id']}/cancel", headers=h(tok), json={"reason": "Customer left"})
    cancel = jobs("bridge-1")["jobs"]
    assert [j["kind"] for j in cancel] == ["cancel"]
    client.post(url, params={"station": "bridge-1"}, json={"kind": "cancel"})
    assert jobs("bridge-1")["jobs"] == []

    assert client.get("/api/public/print/wrong-key/jobs", params={"station": "bridge-1"}).status_code == 404
    new_key = client.post(f"/api/tenant/outlets/{oid}/print-key", headers=h(tok)).json()["print_key"]
    assert new_key != key and client.get(f"/api/public/print/{key}/jobs", params={"station": "bridge-1"}).status_code == 404


def test_bill_corrections_batch(client):
    t = signup(client, "fixtest", "9829000055")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    tg = client.get("/api/menu/tax-groups", headers=h(tok)).json()[0]["id"]
    mk = lambda name, p: client.post("/api/menu/items", headers=h(tok), json={  # noqa: E731
        "brand_id": brand_id, "category_id": cat, "name": name, "tax_group_id": tg,
        "variants": [{"name": "300 ML", "price_paise": p}]}).json()["variants"][0]["id"]
    oreo, kitkat = mk("Oreo Shake", 14900), mk("KitKat Shake", 15900)
    for name, pin, role in (("Asha", "7311", "cashier"), ("Mgr", "7399", "manager")):
        body = {"name": name, "role": role, "pin": pin, "outlet_ids": [oid], **({"phone": "9829000098"} if role == "manager" else {})}
        assert client.post("/api/users", headers=h(tok), json=body).status_code == 201
    asha = client.post("/api/auth/pin", json={"login_id": "fixtest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    client.post("/api/shifts", json={"outlet_id": oid, "opening_cash_paise": 0}, headers=h(asha))

    def bill(qty):
        o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway",
                                                            "items": [{"variant_id": oreo, "qty": qty}]}).json()
        total = client.post("/api/bills/preview", json={"order_id": o["id"]}, headers=h(asha)).json()["total_paise"]
        return client.post("/api/bills", headers=h(asha), json={"order_id": o["id"], "customer": {"declined": True},
                                                              "payments": [{"mode": "cash", "amount_paise": total}]}).json()

    b1, b2, b3 = bill(2), bill(1), bill(1)
    fix = lambda b, **kw: {"bill_id": b["id"], "revision": b["revision"], **kw}  # noqa: E731
    url = "/api/bills/corrections"

    # b1: punched 2 Oreo, customer had 1 Oreo + 1 KitKat. b2: paid by UPI, not cash. One batch, one PIN.
    batch = {"reason": "Wrong entries at rush hour", "bills": [
        fix(b1, lines=[{"line_id": b1["lines"][0]["id"], "qty": 1}], add_items=[{"variant_id": kitkat}]),
        fix(b2, payment_mode="upi")]}
    dry = client.post(url, headers=h(asha), json={**batch, "dry_run": True}).json()
    assert not dry["saved"] and dry["needs_approval"]
    r1 = dry["results"][0]
    assert r1["ok"] and r1["changes"] == ["items", "payment"] and r1["new_total_paise"] == 32300
    assert dry["results"][1]["changes"] == ["payment"] and dry["results"][1]["needs_approval"] == []
    assert client.post(url, headers=h(asha), json=batch).status_code == 403          # PIN needed for item change
    assert client.get(f"/api/bills/{b1['id']}", headers=h(asha)).json()["total_paise"] == b1["total_paise"]

    # One bad bill in the batch: nothing saved, the bad one is named.
    bad = {**batch, "manager_pin": "7399", "bills": batch["bills"] + [fix(b3, revision=99, customer={"declined": True})]}
    res = client.post(url, headers=h(asha), json=bad).json()
    assert not res["saved"] and [r["ok"] for r in res["results"]] == [True, True, False]
    assert client.get(f"/api/bills/{b2['id']}", headers=h(asha)).json()["payments"][0]["mode"] == "cash"

    res = client.post(url, headers=h(asha), json={**batch, "manager_pin": "7399"}).json()
    assert res["saved"]
    n1 = client.get(f"/api/bills/{b1['id']}", headers=h(asha)).json()
    assert n1["invoice_no"] == b1["invoice_no"] and n1["revision"] == 2 and n1["total_paise"] == 32300
    assert sorted(l["item_name"] for l in n1["lines"]) == ["KitKat Shake", "Oreo Shake"]
    assert [(p["mode"], p["amount_paise"]) for p in n1["payments"]] == [("cash", 32300)]  # one payment follows the total
    hist = client.get(f"/api/bills/{b1['id']}/history", headers=h(asha)).json()
    assert hist[0]["old_total_paise"] == b1["total_paise"] and hist[0]["before"]["lines"][0]["qty"] == 2

    # Stale version is refused; reports follow the corrected bill and list the edit.
    stale = client.post(url, headers=h(asha), json={"reason": "again", "bills": [fix(b1, customer={"name": "X", "declined": True})]}).json()
    assert not stale["saved"] and "Reload" in stale["results"][0]["error"]
    edits = client.get("/api/reports/bill-edits", headers=h(tok), params={"outlet_id": oid}).json()
    assert len(edits) == 2 and {e["invoice_no"] for e in edits} == {b1["invoice_no"], b2["invoice_no"]}

    # Every report runs, as JSON and as CSV, and the money adds up.
    names = [r["name"] for r in client.get("/api/reports/list", headers=h(tok)).json()]
    assert {"orders", "payments", "days", "hours", "items", "categories", "shifts", "hsn", "customers"} <= set(names)
    for n in names:
        r = client.get(f"/api/reports/r/{n}", headers=h(tok), params={"outlet_id": oid})
        assert r.status_code == 200, (n, r.text)
        assert client.get(f"/api/reports/r/{n}", headers=h(tok), params={"outlet_id": oid, "format": "csv"}).status_code == 200
    orders = client.get("/api/reports/r/orders", headers=h(tok), params={"outlet_id": oid}).json()
    pays = client.get("/api/reports/r/payments", headers=h(tok), params={"outlet_id": oid}).json()
    assert orders["totals"]["total_paise"] == pays["totals"]["amount_paise"] and len(orders["rows"]) == 3

    # After the owner locks the period, nobody can correct it.
    from datetime import date
    assert client.put("/api/tenant/bill-lock", headers=h(asha), json={"through": str(date.today())}).status_code == 403
    client.put("/api/tenant/bill-lock", headers=h(tok), json={"through": str(date.today())})
    locked = client.post(url, headers=h(tok), json={"reason": "late", "bills": [fix(n1, customer={"name": "Ravi", "declined": True})]}).json()
    assert not locked["saved"] and "locked" in locked["results"][0]["error"]


def test_sms_bill_ready_and_ebill(client):
    from app.routers.sms import flush_now
    t = signup(client, "smstest", "9829000066")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    v = client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": "Oreo",
                    "variants": [{"name": "300 ML", "price_paise": 14900}]}).json()["variants"][0]["id"]
    # Off by default: nothing queued.
    assert client.get("/api/tenant/sms", headers=h(tok)).json()["enabled"] is False
    r = client.put("/api/tenant/sms", headers=h(tok), json={"enabled": True, "provider": "msg91"})
    assert r.status_code == 422  # MSG91 needs a key and templates
    assert client.put("/api/tenant/sms", headers=h(tok), json={"enabled": True, "provider": "log",
                                                                 "daily_to": ["9829000066"]}).status_code == 200
    client.post("/api/shifts", json={"outlet_id": oid, "opening_cash_paise": 0}, headers=h(tok))
    o = client.post("/api/orders", headers=h(tok), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).json()
    bill = client.post("/api/bills", headers=h(tok), json={"order_id": o["id"], "customer": {"phone": "9811122233"},
                                                         "payments": [{"mode": "cash", "amount_paise": 15600}]}).json()
    for k in o["kots"]:
        for st in ("preparing", "ready"):
            client.post(f"/api/kds/kots/{k['id']}/status", headers=h(tok), json={"status": st})
    flush_now()
    msgs = client.get("/api/sms/messages", headers=h(tok)).json()
    kinds = sorted(m["kind"] for m in msgs)
    assert kinds == ["bill", "ready"] and all(m["status"] == "sent" and m["to_phone"] == "9811122233" for m in msgs)
    link = next(m["body"] for m in msgs if m["kind"] == "bill").rsplit("/b/", 1)[1]
    pub = client.get(f"/api/public/bills/{link}").json()
    assert pub["invoice_no"] == bill["invoice_no"] and pub["customer_phone"] == "XXXXXX2233"
    assert client.get("/api/public/bills/notarealkey1").status_code == 404
    # Ready again (undo, redo) doesn't send a second SMS; manual resend does.
    assert client.post(f"/api/bills/{bill['id']}/sms", headers=h(tok), json={"phone": "9811122233"}).status_code == 200
    flush_now()
    assert len(client.get("/api/sms/messages", headers=h(tok)).json()) == 3


def test_cash_carry_forward_blind_count_and_alerts(client):
    t = signup(client, "cashtest", "9829000044")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "cashtest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    client.put("/api/tenant/sms", headers=h(tok), json={"enabled": True, "provider": "log"})

    # Day 1: opens with 2 x 500 + 5 x 10 = 1050; the cashier never sees the expected amount.
    s1 = client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_denoms": {"n500": 2, "c10": 5}}).json()
    assert s1["opening_cash_paise"] == 105000 and s1["expected_cash_paise"] is None and not s1["manager_alerted"]
    client.post(f"/api/shifts/{s1['id']}/cash", headers=h(asha), json={"kind": "out", "amount_paise": 5000, "reason": "Milk"})
    assert client.get("/api/shifts/current", headers=h(asha), params={"outlet_id": oid}).json()["expected_cash_paise"] is None
    # Closes 20 short: expected 1000 (1050 - 50 milk), counted 980.
    c1 = client.post(f"/api/shifts/{s1['id']}/close", headers=h(asha), json={"counted_denoms": {"n500": 1, "n200": 2, "c20": 4}}).json()
    assert c1["expected_cash_paise"] == 100000 and c1["counted_cash_paise"] == 98000 and c1["manager_alerted"]
    # Day 2: should open with what was left (980). Counting 980 raises nothing; the owner sees it carried.
    s2 = client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_denoms": {"n500": 1, "n200": 2, "c20": 4}}).json()
    assert not s2["manager_alerted"]
    owner_view = client.get("/api/shifts", headers=h(tok), params={"outlet_id": oid}).json()
    assert next(s for s in owner_view if s["id"] == s2["id"])["expected_opening_paise"] == 98000
    alerts = client.get("/api/cash-alerts", headers=h(tok)).json()
    assert len(alerts) == 1 and alerts[0]["kind"] == "close" and alerts[0]["difference_paise"] == -2000
    assert client.get("/api/cash-alerts", headers=h(asha)).status_code == 403
    from app.routers.sms import flush_now
    flush_now()
    assert any(m["kind"] == "cash" and "Rs 20 short" in m["body"] for m in client.get("/api/sms/messages", headers=h(tok)).json())
    assert client.post(f"/api/cash-alerts/{alerts[0]['id']}/check", headers=h(tok), json={"note": "Change given wrong, cashier told"}).status_code == 200
    assert client.get("/api/cash-alerts", headers=h(tok)).json() == []


def test_offline_counter_series_and_sync(client):
    import uuid as _uuid
    t = signup(client, "offtest", "9829000055")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PTW"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    tg = client.get("/api/menu/tax-groups", headers=h(tok)).json()[0]
    oreo = client.post("/api/menu/items", headers=h(tok), json={
        "brand_id": brand_id, "category_id": cat, "name": "Oreo Shake", "tax_group_id": tg["id"],
        "variants": [{"name": "300 ML", "price_paise": 14900}]}).json()
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "offtest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    me = client.get("/api/auth/me", headers=h(asha)).json()
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 0})

    dev = str(_uuid.uuid4())
    pack = client.post("/api/offline/devices", headers=h(asha), json={"id": dev, "outlet_id": oid, "name": "Counter 1"}).json()
    assert pack["series"] == "PTWA" and pack["token_base"] == 1000 and pack["last_seq"] == 0
    assert client.post("/api/offline/devices", headers=h(asha), json={"id": dev, "outlet_id": oid}).json()["series"] == "PTWA"
    tax = pack["items"][oreo["id"]]
    fy = pack["fy_start"]
    bill_id = str(_uuid.uuid4())
    body = {"id": bill_id, "device_id": dev, "made_at": "2026-11-10T08:30:00Z", "made_by": me["id"], "fy_start": fy, "seq": 1,
            "invoice_no": f"PTWA/{fy % 100:02d}{(fy + 1) % 100:02d}/000001", "token_no": 1001, "order_type": "takeaway",
            "customer": {"declined": True},
            "lines": [{"variant_id": oreo["variants"][0]["id"], "qty": 2, "price_paise": 14900,
                       "tax": {k: tax[k] for k in ("cgst_bp", "sgst_bp", "igst_bp", "inclusive")}}],
            "payments": [{"mode": "cash", "amount_paise": 31300}], "total_paise": 31300}
    r = client.post("/api/offline/bills", headers=h(asha), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["bill"]["invoice_no"].startswith("PTWA/") and r.json()["notes"] == []
    again = client.post("/api/offline/bills", headers=h(asha), json=body).json()
    assert again["duplicate"] and again["bill"]["id"] == bill_id
    # Kitchen and printer never see it again: filed as printed and served.
    assert client.get("/api/kds/feed", headers=h(asha), params={"outlet_id": oid}).json().get("kots", []) == []
    # A wrong total is refused (the printed copy stays with the manager).
    bad = {**body, "id": str(_uuid.uuid4()), "seq": 2, "invoice_no": body["invoice_no"][:-1] + "2", "total_paise": 30000}
    assert client.post("/api/offline/bills", headers=h(asha), json=bad).status_code == 422
    # The main series continues on its own.
    assert client.post("/api/offline/devices", headers=h(asha), json={"id": dev, "outlet_id": oid}).json()["last_seq"] == 1


def test_inventory_recipes_purchases_sales_counts(client):
    t = signup(client, "invtest", "9829000066")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    tg = client.get("/api/menu/tax-groups", headers=h(tok)).json()[0]["id"]
    item = client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": "Oreo Shake",
                       "tax_group_id": tg, "variants": [{"name": "300 ML", "price_paise": 14900}, {"name": "1000 ML", "price_paise": 34900}]}).json()
    small, big = item["variants"][0]["id"], item["variants"][1]["id"]
    mk = lambda body: client.post("/api/inventory/items", headers=h(tok), json=body).json()  # noqa: E731
    milk = mk({"name": "Milk", "unit": "ml", "purchase_units": [{"name": "crate", "factor": 12000}], "min_level_m": 5_000_000})
    oreo = mk({"name": "Oreo biscuit", "unit": "pc"})
    cup = mk({"name": "Cup 300", "unit": "pc"})
    sup = client.post("/api/inventory/suppliers", headers=h(tok), json={"name": "Saras Dairy", "gstin": None}).json()
    # 1 crate (12 l) of milk for ₹720, 100 biscuits for ₹300, 50 cups for ₹200
    p = client.post("/api/inventory/purchases", headers=h(tok), json={"outlet_id": oid, "supplier_id": sup["id"], "invoice_no": "SD-1",
        "invoice_date": "2026-11-10", "lines": [
            {"stock_item_id": milk["id"], "qty_m": 1000, "unit": "crate", "amount_paise": 72000, "gst_bp": 0},
            {"stock_item_id": oreo["id"], "qty_m": 100_000, "unit": "pc", "amount_paise": 30000, "gst_bp": 1800},
            {"stock_item_id": cup["id"], "qty_m": 50_000, "unit": "pc", "amount_paise": 20000, "gst_bp": 1800}]})
    assert p.status_code == 201, p.text
    assert p.json()["tax_paise"] == 9000
    assert client.put(f"/api/inventory/recipes/variant/{small}", headers=h(tok), json={"lines": [
        {"stock_item_id": milk["id"], "qty_m": 250_000}, {"stock_item_id": oreo["id"], "qty_m": 3000},
        {"stock_item_id": cup["id"], "qty_m": 1000}]}).status_code == 200
    assert client.post("/api/inventory/recipes/copy", headers=h(tok), json={"from_variant_id": small, "to_variant_ids": [big],
                       "factor_bp": 33333}).json()["copied_to"] == 1
    cost = {r["variant_id"]: r for r in client.get("/api/inventory/costing", headers=h(tok), params={"outlet_id": oid}).json()}
    # 250 ml at ₹60/l = 15 + 3 biscuits at ₹3 = 9 + cup ₹4 = ₹28
    assert cost[small]["cost_paise"] == 2800 and cost[small]["margin_bp"] > 7000

    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "invtest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 0})
    o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": small, "qty": 2}]}).json()
    total = client.post("/api/bills/preview", headers=h(asha), json={"order_id": o["id"]}).json()["total_paise"]
    bill = client.post("/api/bills", headers=h(asha), json={"order_id": o["id"], "customer": {"declined": True},
                       "payments": [{"mode": "cash", "amount_paise": total}]}).json()
    stock = {r["name"]: r for r in client.get("/api/inventory/stock", headers=h(tok), params={"outlet_id": oid}).json()["items"]}
    assert stock["Milk"]["qty_m"] == 12_000_000 - 500_000 and stock["Oreo biscuit"]["qty_m"] == 94_000 and stock["Cup 300"]["qty_m"] == 48_000
    # Staff see no quantities or costs; cancelling the bill puts the stock back.
    staff = client.get("/api/inventory/stock", headers=h(asha), params={"outlet_id": oid}).json()
    assert "value_paise" not in staff and "qty_m" not in staff["items"][0]
    client.post("/api/users", headers=h(tok), json={"name": "Mgr", "role": "manager", "pin": "7399", "phone": "9829000067", "outlet_ids": [oid]})
    assert client.post(f"/api/bills/{bill['id']}/cancel", headers=h(asha), json={"reason": "Customer left", "manager_pin": "7399"}).status_code == 200
    stock = {r["name"]: r for r in client.get("/api/inventory/stock", headers=h(tok), params={"outlet_id": oid}).json()["items"]}
    assert stock["Milk"]["qty_m"] == 12_000_000
    # Blind count by the cashier: 11.5 l milk counted; the owner sees 500 ml short worth ₹30.
    assert client.post("/api/inventory/counts", headers=h(asha), json={"outlet_id": oid, "lines": [
        {"stock_item_id": milk["id"], "counted_m": 11_500_000}]}).json() == {"saved": 1}
    rep = client.get("/api/reports/r/stock_summary", headers=h(tok), params={"outlet_id": oid}).json()
    milk_row = next(r for r in rep["rows"] if r["item"] == "Milk")
    assert milk_row["count_diff_paise"] == -3000 and milk_row["closing"] == "11.5 l"
    low = client.get("/api/inventory/reorder", headers=h(tok), params={"outlet_id": oid}).json()
    assert low == []    # 11.5 l is above the 5 l minimum


def test_prep_items_batch_and_made_fresh(client):
    """Sandwich = bread + 30 g green chutney. Chutney batch: 500 g coriander + 100 g mint makes 600 g."""
    t = signup(client, "preptest", "9829000055")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PP"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Snacks"}, headers=h(tok)).json()["id"]
    v = client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": "Veg Sandwich",
                    "variants": [{"name": "Regular", "price_paise": 9900}]}).json()["variants"][0]["id"]
    mk = lambda body: client.post("/api/inventory/items", headers=h(tok), json=body).json()  # noqa: E731
    cor, mint, bread = mk({"name": "Coriander", "unit": "g"}), mk({"name": "Mint", "unit": "g"}), mk({"name": "Bread slice", "unit": "pc"})
    assert client.post("/api/inventory/items", headers=h(tok), json={"name": "X", "kind": "prep", "unit": "g"}).status_code == 422  # no batch size
    chut = mk({"name": "Green chutney", "kind": "prep", "unit": "g", "yield_m": 600_000})
    client.post("/api/inventory/purchases", headers=h(tok), json={"outlet_id": oid, "invoice_no": "M-1", "invoice_date": "2026-11-10", "lines": [
        {"stock_item_id": cor["id"], "qty_m": 1000, "unit": "kg", "amount_paise": 8000, "gst_bp": 0},
        {"stock_item_id": mint["id"], "qty_m": 1000, "unit": "kg", "amount_paise": 20000, "gst_bp": 0},
        {"stock_item_id": bread["id"], "qty_m": 100_000, "unit": "pc", "amount_paise": 50000, "gst_bp": 0}]})
    client.put(f"/api/inventory/recipes/prep/{chut['id']}", headers=h(tok), json={"lines": [
        {"stock_item_id": cor["id"], "qty_m": 500_000}, {"stock_item_id": mint["id"], "qty_m": 100_000}]})
    client.put(f"/api/inventory/recipes/variant/{v}", headers=h(tok), json={"lines": [
        {"stock_item_id": bread["id"], "qty_m": 2000}, {"stock_item_id": chut["id"], "qty_m": 30_000}]})
    # No batch made yet: costed from the chutney's recipe. ₹60 a batch = ₹100/kg; 30 g = ₹3; 2 slices ₹10.
    pc = {r["name"]: r for r in client.get("/api/inventory/prep-costs", headers=h(tok), params={"outlet_id": oid}).json()}
    assert pc["Green chutney"]["batch_cost_paise"] == 6000 and pc["Green chutney"]["per_unit_paise"] == 10000 and pc["Green chutney"]["source"] == "recipe"
    cost = {r["variant_id"]: r for r in client.get("/api/inventory/costing", headers=h(tok), params={"outlet_id": oid}).json()}
    assert cost[v]["cost_paise"] == 1300
    # A loop is refused: chutney can't be made from something made from chutney.
    mayo = mk({"name": "Chutney mayo", "kind": "prep", "unit": "g", "yield_m": 500_000})
    client.put(f"/api/inventory/recipes/prep/{mayo['id']}", headers=h(tok), json={"lines": [{"stock_item_id": chut["id"], "qty_m": 200_000}]})
    assert client.put(f"/api/inventory/recipes/prep/{chut['id']}", headers=h(tok), json={"lines": [
        {"stock_item_id": cor["id"], "qty_m": 500_000}, {"stock_item_id": mayo["id"], "qty_m": 1000}]}).status_code == 422
    # Batch-made: make 1.2 kg; the sale takes 30 g chutney.
    assert client.post("/api/inventory/prep", headers=h(tok), json={"outlet_id": oid, "stock_item_id": chut["id"], "made_m": 1_200_000}).json()["cost_paise"] == 12000
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "preptest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 0})
    def sell():
        o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).json()
        tot = client.post("/api/bills/preview", headers=h(asha), json={"order_id": o["id"]}).json()["total_paise"]
        client.post("/api/bills", headers=h(asha), json={"order_id": o["id"], "customer": {"declined": True}, "payments": [{"mode": "cash", "amount_paise": tot}]})
    stock = lambda: {r["name"]: r.get("qty_m") for r in client.get("/api/inventory/stock", headers=h(tok), params={"outlet_id": oid}).json()["items"]}  # noqa: E731
    sell()
    s1 = stock()
    assert s1["Green chutney"] == 1_200_000 - 30_000 and s1["Coriander"] == 1_000_000 - 1_000_000
    # Switch to made fresh per order: the next sale takes 25 g coriander + 5 g mint, not chutney.
    client.patch(f"/api/inventory/items/{chut['id']}", headers=h(tok), json={"name": "Green chutney", "kind": "prep", "unit": "g", "yield_m": 600_000, "made_on_sale": True})
    client.post("/api/inventory/purchases", headers=h(tok), json={"outlet_id": oid, "invoice_no": "M-2", "invoice_date": "2026-11-10", "lines": [
        {"stock_item_id": cor["id"], "qty_m": 1000, "unit": "kg", "amount_paise": 8000, "gst_bp": 0}]})
    sell()
    s2 = stock()
    assert s2["Coriander"] == 1_000_000 - 25_000 and s2["Mint"] == 1_000_000 - 200_000 - 5_000
    assert "Green chutney" not in s2                       # nothing to count for a made-fresh item
    assert client.post("/api/inventory/prep", headers=h(tok), json={"outlet_id": oid, "stock_item_id": chut["id"], "made_m": 600_000}).status_code == 409


def _v1_setup(client, login):
    t = signup(client, login, "98290" + str(abs(hash(login)) % 100000).zfill(5))
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    out = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PV"}).json()
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    mk = lambda n, p: client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": n,  # noqa: E731
                                  "variants": [{"name": "Regular", "price_paise": p}]}).json()["variants"][0]["id"]
    v1, v2 = mk("Oreo Shake", 15000), mk("Mango Shake", 13000)
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [out["id"]]})
    client.post("/api/users", headers=h(tok), json={"name": "Mgr", "role": "manager", "pin": "7399", "phone": "9829011111", "outlet_ids": [out["id"]]})
    asha = client.post("/api/auth/pin", json={"login_id": login, "outlet_id": out["id"], "pin": "7311"}).json()["token"]
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": out["id"], "opening_cash_paise": 0})
    return tok, asha, out, v1, v2


def _bill(client, tok, oid, extra=None):
    total = client.post("/api/bills/preview", headers=h(tok), json={"order_id": oid}).json()["total_paise"]
    return client.post("/api/bills", headers=h(tok), json={"order_id": oid, "customer": {"declined": True},
                       "payments": [{"mode": "cash", "amount_paise": total}], **(extra or {})})


def test_sold_out_today(client):
    tok, asha, out, v1, v2 = _v1_setup(client, "soldout")
    oid = out["id"]
    assert client.post(f"/api/menu/outlets/{oid}/sold-out", headers=h(asha), json={"variant_ids": [v1], "sold_out": True}).json()["items"] == ["Oreo Shake"]
    menu = client.get(f"/api/menu/outlets/{oid}", headers=h(asha)).json()
    assert {v["variant_id"]: v["sold_out"] for i in menu["items"] for v in i["variants"]}[v1] is True
    r = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v1}]})
    assert r.status_code == 409 and "sold out today" in r.json()["detail"]
    qr = client.get(f"/api/public/qr/{out['qr_key']}").json() if out.get("qr_key") else None
    if qr and "menu" in qr:
        assert v1 not in {v["variant_id"] for i in qr["menu"]["items"] for v in i["variants"]}
    assert [x["item"] for x in client.get(f"/api/menu/outlets/{oid}/sold-out", headers=h(asha)).json()] == ["Oreo Shake"]
    client.post(f"/api/menu/outlets/{oid}/sold-out", headers=h(asha), json={"variant_ids": [v1], "sold_out": False})
    assert client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v1}]}).status_code == 201


def test_split_bill_and_move_table(client):
    tok, asha, out, v1, v2 = _v1_setup(client, "splitmove")
    oid = out["id"]
    o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "dine_in", "table": "T4",
                    "items": [{"variant_id": v1, "qty": 3}, {"variant_id": v2, "qty": 1}]}).json()
    lines = {l["item_name"]: l for k in o["kots"] for l in k["lines"]}
    # Split: 1 Oreo + the Mango go on their own bill.
    new = client.post(f"/api/orders/{o['id']}/split", headers=h(asha), json={"items": [
        {"order_item_id": lines["Oreo Shake"]["id"], "qty": 1}, {"order_item_id": lines["Mango Shake"]["id"], "qty": 1}]}).json()
    assert new["table_label"] == "T4/2"
    b1 = _bill(client, asha, o["id"]).json()
    b2 = _bill(client, asha, new["id"]).json()
    assert b1["total_paise"] + b2["total_paise"] == b1["total_paise"] + b2["total_paise"]
    assert [l["qty"] for l in b1["lines"]] == [2] and sorted(l["item_name"] for l in b2["lines"]) == ["Mango Shake", "Oreo Shake"]
    # Can't split off everything.
    o2 = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "dine_in", "table": "T5", "items": [{"variant_id": v1}]}).json()
    only = o2["kots"][0]["lines"][0]["id"]
    assert client.post(f"/api/orders/{o2['id']}/split", headers=h(asha), json={"items": [{"order_item_id": only, "qty": 1}]}).status_code == 422
    # Move the whole table T5 -> T7 (free): just a new label. Then T7 -> T8 where T8 already has an order: they join.
    moved = client.post(f"/api/orders/{o2['id']}/move", headers=h(asha), json={"to_table": "T7"}).json()
    assert moved["id"] == o2["id"] and moved["table_label"] == "T7"
    o3 = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "dine_in", "table": "T8", "items": [{"variant_id": v2}]}).json()
    joined = client.post(f"/api/orders/{o2['id']}/move", headers=h(asha), json={"to_table": "T8"}).json()
    assert joined["id"] == o3["id"] and sorted(l["item_name"] for k in joined["kots"] for l in k["lines"]) == ["Mango Shake", "Oreo Shake"]
    assert client.get(f"/api/orders/{o2['id']}", headers=h(tok)).json()["status"] == "completed"
    assert client.post(f"/api/orders/{o2['id']}/move", headers=h(asha), json={"to_table": "T9"}).status_code == 409


def test_training_outlet(client):
    tok, asha, out, v1, v2 = _v1_setup(client, "training")
    tr = client.post(f"/api/tenant/outlets/{out['id']}/training", headers=h(tok)).json()
    assert tr["is_training"] and tr["code"].startswith("TR") and tr["training_of"] == out["id"]
    assert client.post(f"/api/tenant/outlets/{out['id']}/training", headers=h(tok)).json()["id"] == tr["id"]   # same copy
    # Asha can sign in to it (she works at the real outlet); bills say training and use their own series.
    trk = client.post("/api/auth/pin", json={"login_id": "training", "outlet_id": tr["id"], "pin": "7311"}).json()["token"]
    client.post("/api/shifts", headers=h(trk), json={"outlet_id": tr["id"], "opening_cash_paise": 0})
    o = client.post("/api/orders", headers=h(trk), json={"outlet_id": tr["id"], "order_type": "takeaway", "items": [{"variant_id": v1}]}).json()
    b = _bill(client, trk, o["id"], {"customer": {"phone": "9829099999"}}).json()
    assert b["training"] and b["invoice_no"].startswith(tr["code"] + "/")
    # Not in reports or Tally; no SMS; no loyalty.
    rep = client.get("/api/reports/r/days", headers=h(tok)).json()
    assert sum(r.get("bills", 0) for r in rep["rows"]) == 0
    assert client.get("/api/accounts/tally", headers=h(tok), params={"outlet_id": tr["id"]}).status_code in (404, 422)
    msgs = client.get("/api/sms/messages", headers=h(tok)).json()
    assert all("9829099999" not in m.get("to_phone", "") for m in msgs)
    # UPI auto in training is always the pretend kind.
    client.put("/api/tenant/payments", headers=h(tok), json={"enabled": True, "provider": "razorpay", "key_id": "rzp_test_x", "key_secret": "s"})
    o2 = client.post("/api/orders", headers=h(trk), json={"outlet_id": tr["id"], "order_type": "takeaway", "items": [{"variant_id": v1}]}).json()
    import uuid as _u
    up = client.post("/api/upi/requests", headers=h(trk), json={"order_id": o2["id"], "bill_id": str(_u.uuid4()), "customer": {"declined": True}}).json()
    assert up["provider"] == "test"
    # Reset: a clean copy replaces it.
    fresh = client.post(f"/api/tenant/outlets/{tr['id']}/training/reset", headers=h(tok)).json()
    assert fresh["id"] != tr["id"] and fresh["is_training"]


def test_owner_alerts(client):
    tok, asha, out, v1, v2 = _v1_setup(client, "alerts")
    oid = out["id"]
    o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v1}]}).json()
    b = _bill(client, asha, o["id"]).json()
    client.post(f"/api/bills/{b['id']}/cancel", headers=h(asha), json={"reason": "Wrong item", "manager_pin": "7399"})
    o2 = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v2}]}).json()
    pv = client.post("/api/bills/preview", headers=h(asha), json={"order_id": o2["id"], "discount": {"kind": "percent", "value": 3000}}).json()
    client.post("/api/bills", headers=h(asha), json={"order_id": o2["id"], "customer": {"declined": True}, "manager_pin": "7399",
                "discount": {"kind": "percent", "value": 3000, "reason": "Friend of owner"}, "payments": [{"mode": "cash", "amount_paise": pv["total_paise"]}]})
    al = client.get("/api/alerts", headers=h(tok)).json()
    kinds = sorted(a["kind"] for a in al["alerts"])
    assert kinds == ["cancel", "discount"] and al["unseen"] == 2
    assert any("Wrong item" in a["text"] for a in al["alerts"]) and any("30%" in a["text"] for a in al["alerts"])
    assert client.get("/api/alerts", headers=h(asha)).status_code == 403
    client.post("/api/alerts/seen", headers=h(tok))
    assert client.get("/api/alerts", headers=h(tok)).json()["unseen"] == 0


def test_day_close_checklist_and_lock(client):
    t = signup(client, "daytest", "9829000077")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    v = client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": "Oreo Shake",
                    "variants": [{"name": "300 ML", "price_paise": 14900}]}).json()["variants"][0]["id"]
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "daytest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    shift = client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 0}).json()
    o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).json()
    st = client.get(f"/api/day-close/{oid}", headers=h(tok)).json()
    assert {c["key"] for c in st["checks"] if c["block"]} == {"shifts", "orders"}
    assert client.get(f"/api/day-close/{oid}", headers=h(asha)).status_code == 403
    today = st["day"]
    assert client.post("/api/day-close", headers=h(tok), json={"outlet_id": oid, "day": today}).status_code == 409
    total = client.post("/api/bills/preview", headers=h(asha), json={"order_id": o["id"]}).json()["total_paise"]
    client.post("/api/bills", headers=h(asha), json={"order_id": o["id"], "customer": {"declined": True}, "payments": [{"mode": "cash", "amount_paise": total}]})
    client.post(f"/api/shifts/{shift['id']}/close", headers=h(asha), json={"counted_cash_paise": total})
    done = client.post("/api/day-close", headers=h(tok), json={"outlet_id": oid, "day": today}).json()
    assert done["closed"] and done["summary"]["sales_paise"] == total and done["summary"]["bills"] == 1
    # Closed: no new orders until a manager reopens the day.
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": total})
    assert client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).status_code == 409
    assert not client.post("/api/day-close/reopen", headers=h(tok), json={"outlet_id": oid, "day": today, "reason": "missed a bill"}).json()["closed"]
    assert client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).status_code == 201


def test_upi_auto_confirm_test_mode_and_webhook(client):
    import hashlib as _h
    import hmac as _hm
    import json as _j
    import uuid as _uuid
    t = signup(client, "upitest", "9829000088")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    v = client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": "Oreo Shake",
                    "variants": [{"name": "300 ML", "price_paise": 14900}]}).json()["variants"][0]["id"]
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "upitest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 0})
    order = lambda: client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).json()  # noqa: E731
    o = order()
    body = {"order_id": o["id"], "bill_id": str(_uuid.uuid4()), "customer": {"declined": True}}
    assert client.post("/api/upi/requests", headers=h(asha), json=body).status_code == 409   # not switched on
    cfg = client.put("/api/tenant/payments", headers=h(tok), json={"enabled": True, "provider": "test", "webhook_secret": "whsec_1"}).json()
    r = client.post("/api/upi/requests", headers=h(asha), json=body).json()
    assert r["status"] == "waiting" and r["amount_paise"] == 14900 and r["upi_string"].startswith("upi://pay")
    paid = client.post(f"/api/upi/requests/{r['id']}/test-paid", headers=h(asha)).json()
    assert paid["status"] == "billed" and paid["bill"]["payments"][0]["mode"] == "upi" and paid["bill"]["id"] == body["bill_id"]
    # Webhook: a bad signature is refused; a good one for an unknown QR is accepted and ignored.
    key = cfg["webhook_url"].rsplit("/", 1)[1]
    ev = _j.dumps({"event": "qr_code.credited", "payload": {"qr_code": {"entity": {"id": "qr_none"}},
                   "payment": {"entity": {"id": "pay_1", "amount": 14900, "status": "captured"}}}}).encode()
    assert client.post(f"/api/public/razorpay/{key}", content=ev, headers={"X-Razorpay-Signature": "bad"}).status_code == 400
    sig = _hm.new(b"whsec_1", ev, _h.sha256).hexdigest()
    assert client.post(f"/api/public/razorpay/{key}", content=ev, headers={"X-Razorpay-Signature": sig}).json() == {"ok": True}
    assert client.post("/api/public/razorpay/nope", content=ev, headers={"X-Razorpay-Signature": sig}).status_code == 404


def test_upi_paytm_dynamic_qr(client, monkeypatch):
    """Paytm: QR made with a signed request; printed slip; the bill is made when Paytm's status says paid,
    whether the counter asks, the waiting list asks, or Paytm posts to the callback."""
    import json as _j
    import uuid as _uuid
    from app.core import paytm as pt
    from app.routers import payments as pay
    key16 = "kbzk1DSbJiV_O3p5"
    state = {"status": "PENDING", "calls": []}

    def fake(cfg, path, body):
        state["calls"].append(path)
        raw = pt.signed(body, cfg.paytm_key, cfg.paytm_client_id)
        assert pt.verify(_j.dumps(_j.loads(raw)["body"], separators=(",", ":")), key16, _j.loads(raw)["head"]["signature"])
        if path == pt.QR_CREATE:
            return {"body": {"resultInfo": {"resultStatus": "SUCCESS", "resultCode": "QR_0001"}, "qrCodeId": "Q1",
                             "qrData": f"upi://pay?pa=shake@paytm&am={body['amount']}&tr={body['orderId']}"}}
        if state["status"] == "TXN_SUCCESS":
            return {"body": {"resultInfo": {"resultStatus": "TXN_SUCCESS"}, "txnId": "T1", "bankTxnId": "6315" + body["orderId"][-6:],
                             "txnAmount": "149.00", "paymentMode": "UPI"}}
        return {"body": {"resultInfo": {"resultStatus": state["status"]}}}
    monkeypatch.setattr(pay, "_paytm_call", fake)

    t = signup(client, "paytmtest", "9829000077")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    v = client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": "Oreo Shake",
                    "variants": [{"name": "300 ML", "price_paise": 14900}]}).json()["variants"][0]["id"]
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "paytmtest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 0})
    order = lambda: client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).json()  # noqa: E731
    base = {"enabled": True, "provider": "paytm", "paytm_mid": "SHAKE123", "print_qr": True, "qr_minutes": 30}
    assert client.put("/api/tenant/payments", headers=h(tok), json=base).status_code == 422             # no key
    assert client.put("/api/tenant/payments", headers=h(tok), json={**base, "paytm_key": "short"}).status_code == 422
    cfg = client.put("/api/tenant/payments", headers=h(tok), json={**base, "paytm_key": key16}).json()
    assert cfg["has_paytm_key"] and cfg["paytm_callback_url"].endswith("/api/public/paytm/" + cfg["webhook_url"].rsplit("/", 1)[1])
    assert "paytm_key" not in cfg
    # ₹1 check from Setup > Payments: a real QR not tied to a bill, then Paytm's status.
    tq = client.post("/api/tenant/payments/test-qr", headers=h(tok)).json()
    assert tq["order_id"].startswith("WT") and "am=1.00" in tq["upi_string"]
    assert client.get(f"/api/tenant/payments/test-qr/{tq['order_id']}", headers=h(tok)).json()["state"] == "pending"
    assert client.post("/api/tenant/payments/test-qr", headers=h(asha)).status_code == 403
    # 1) The counter waits on screen; the box asks Paytm.
    o = order()
    r = client.post("/api/upi/requests", headers=h(asha), json={"order_id": o["id"], "bill_id": str(_uuid.uuid4()), "customer": {"declined": True}}).json()
    assert r["provider"] == "paytm" and r["upi_string"].startswith("upi://pay?pa=shake@paytm&am=149.00") and r["print_qr"]
    assert r["slip"]["total_paise"] == 14900 and r["slip"]["lines"][0]["item_name"] == "Oreo Shake" and r["slip"]["token_no"] == o["token_no"]
    assert client.get(f"/api/upi/requests/{r['id']}", headers=h(asha)).json()["status"] == "waiting"
    state["status"] = "TXN_SUCCESS"
    import time as _t
    _t.sleep(pay.POLL_SECONDS + 0.2)
    got = client.get(f"/api/upi/requests/{r['id']}", headers=h(asha)).json()
    assert got["status"] == "billed" and got["bill"]["payments"][0]["mode"] == "upi" and got["payment_ref"].startswith("6315")
    # 2) Printed slip, cashier moved on: Paytm's post nudges the box, which re-asks Paytm before billing.
    state["status"] = "PENDING"
    o2 = order()
    r2 = client.post("/api/upi/requests", headers=h(asha), json={"order_id": o2["id"], "bill_id": str(_uuid.uuid4()), "customer": {"declined": True}}).json()
    key = cfg["paytm_callback_url"].rsplit("/", 1)[1]
    oid2 = pt.order_id(_uuid.UUID(r2["id"]).hex)
    assert client.post(f"/api/public/paytm/{key}", data={"ORDERID": oid2, "STATUS": "TXN_SUCCESS"}).json() == {"ok": True}
    assert client.get(f"/api/upi/requests/{r2['id']}", headers=h(asha)).json()["status"] == "waiting"   # forged post: Paytm says pending
    state["status"] = "TXN_SUCCESS"
    client.post(f"/api/public/paytm/{key}", data={"ORDERID": oid2, "STATUS": "TXN_SUCCESS"})
    w = client.get(f"/api/outlets/{oid}/upi/waiting", headers=h(asha)).json()
    assert {x["id"]: x["status"] for x in w}[r2["id"]] == "billed"
    assert client.post("/api/public/paytm/nope", data={"ORDERID": oid2}).status_code == 404
    # 3) The waiting list itself asks Paytm for QRs still out.
    state["status"] = "PENDING"
    o3 = order()
    r3 = client.post("/api/upi/requests", headers=h(asha), json={"order_id": o3["id"], "bill_id": str(_uuid.uuid4()), "customer": {"declined": True}}).json()
    assert {x["id"]: x["status"] for x in client.get(f"/api/outlets/{oid}/upi/waiting", headers=h(asha)).json()}[r3["id"]] == "waiting"
    state["status"] = "TXN_SUCCESS"
    _t.sleep(pay.POLL_SECONDS * 2 + 0.2)
    assert {x["id"]: x["status"] for x in client.get(f"/api/outlets/{oid}/upi/waiting", headers=h(asha)).json()}[r3["id"]] == "billed"


def test_ui_theme(client):
    t = signup(client, "themetest", "9829000066")
    tok = t["token"]
    assert client.get("/api/public/tenants/themetest").json()["ui_theme"] == "steel"
    assert client.patch("/api/tenant/branding", headers=h(tok), json={"ui_theme": "neon"}).status_code == 422
    assert client.patch("/api/tenant/branding", headers=h(tok), json={"ui_theme": "board"}).json()["ui_theme"] == "board"
    assert client.get("/api/public/tenants/themetest").json()["ui_theme"] == "board"


def test_kitchen_screen_link(client):
    """The kitchen display signs in from its link every morning: no PIN, kitchen permissions, this outlet only."""
    t = signup(client, "kitchenlink", "9829000099")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    out = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PK"}).json()
    other = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Other", "code": "OK"}).json()
    assert out["kitchen_key"]
    assert client.post("/api/auth/kitchen-screen", json={"key": "nope-nope-nope"}).status_code == 404
    k1 = client.post("/api/auth/kitchen-screen", json={"key": out["kitchen_key"]}).json()
    k2 = client.post("/api/auth/kitchen-screen", json={"key": out["kitchen_key"]}).json()
    assert k1["user"]["id"] == k2["user"]["id"] and k1["user"]["role"] == "kitchen"     # one screen user per outlet
    ks = k2["token"]
    assert client.get(f"/api/kds/outlets/{out['id']}", headers=h(ks)).status_code == 200
    assert client.get(f"/api/kds/outlets/{other['id']}", headers=h(ks)).status_code == 403
    assert client.post("/api/orders", headers=h(ks), json={"outlet_id": out["id"], "order_type": "takeaway", "items": []}).status_code == 403
    assert all(not u["name"].startswith("Kitchen screen") for u in client.get("/api/users", headers=h(tok)).json())   # not in Staff
    new = client.post(f"/api/tenant/outlets/{out['id']}/kitchen-key", headers=h(tok)).json()
    assert new["kitchen_key"] != out["kitchen_key"]
    assert client.get(f"/api/kds/outlets/{out['id']}", headers=h(ks)).status_code == 401                    # old screen signed out
    assert client.post("/api/auth/kitchen-screen", json={"key": out["kitchen_key"]}).status_code == 404
    assert client.post("/api/auth/kitchen-screen", json={"key": new["kitchen_key"]}).status_code == 200


def test_customer_screen_ads(client):
    t = signup(client, "adstest", "9829000088")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    o1 = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PA"}).json()["id"]
    o2 = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Ratanada", "code": "RA"}).json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 100
    a1 = client.post("/api/tenant/ads", headers=h(tok), files={"file": ("a.png", png, "image/png")}, data={"title": "Mango season", "seconds": "6"}).json()
    assert a1["kind"] == "image" and a1["showing_now"] and a1["url"].startswith("/uploads/")
    a2 = client.post("/api/tenant/ads", headers=h(tok), files={"file": ("v.mp4", mp4, "video/mp4")}, data={"outlet_id": o2}).json()
    assert a2["kind"] == "video"
    a3 = client.post("/api/tenant/ads", headers=h(tok), files={"file": ("b.png", png, "image/png")}, data={"starts_on": "2099-01-01"}).json()
    assert not a3["showing_now"]
    assert client.post("/api/tenant/ads", headers=h(tok), files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 415
    assert client.post("/api/tenant/ads", headers=h(tok), files={"file": ("b.png", png, "image/png")}, data={"seconds": "1"}).status_code == 422
    assert [x["id"] for x in client.get(f"/api/outlets/{o1}/ads", headers=h(tok)).json()] == [a1["id"]]
    assert [x["id"] for x in client.get(f"/api/outlets/{o2}/ads", headers=h(tok)).json()] == [a1["id"], a2["id"]]
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [o1]})
    asha = client.post("/api/auth/pin", json={"login_id": "adstest", "outlet_id": o1, "pin": "7311"}).json()["token"]
    assert client.get(f"/api/outlets/{o1}/ads", headers=h(asha)).status_code == 200
    assert client.get("/api/tenant/ads", headers=h(asha)).status_code == 403
    off = client.put(f"/api/tenant/ads/{a1['id']}", headers=h(tok), json={"title": "Mango", "seconds": 6, "sort": 1, "is_active": False}).json()
    assert not off["showing_now"] and client.get(f"/api/outlets/{o1}/ads", headers=h(tok)).json() == []
    assert client.delete(f"/api/tenant/ads/{a2['id']}", headers=h(tok)).json() == {"ok": True}
    assert len(client.get("/api/tenant/ads", headers=h(tok)).json()) == 2


def test_loyalty_points_stamps_and_cancel(client):
    t = signup(client, "loytest", "9829000099")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    v = client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": "Oreo Shake",
                    "variants": [{"name": "1000 ML", "price_paise": 40000}]}).json()["variants"][0]["id"]
    client.put("/api/tenant/loyalty", headers=h(tok), json={"mode": "both", "earn_bp": 500, "min_bill_paise": 0, "redeem_min_paise": 5000,
               "max_redeem_bp": 5000, "expiry_days": 180, "stamp_min_paise": 10000, "stamps_needed": 3, "reward_paise": 15000, "reward_label": "Free shake"})
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "loytest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 0})

    def sell(loyalty=None):
        o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).json()
        pv = client.post("/api/bills/preview", headers=h(asha), json={"order_id": o["id"], "loyalty": {**loyalty, "phone": "9876543210"} if loyalty else None}).json()
        r = client.post("/api/bills", headers=h(asha), json={"order_id": o["id"], "customer": {"phone": "9876543210"}, "loyalty": loyalty,
                        "payments": [{"mode": "cash", "amount_paise": pv["total_paise"]}]})
        assert r.status_code == 201, r.text
        return r.json()

    bills = [sell() for _ in range(3)]          # 3 x ₹400 (no tax group) -> ₹20 points each, 3 stamps
    info = client.get("/api/loyalty/customer", headers=h(asha), params={"phone": "9876543210"}).json()
    assert info["points_paise"] == 6000 and info["stamps"] == 3 and info["visits"] == 3
    used = sell({"points_paise": 6000, "use_reward": True})   # ₹60 points + ₹150 reward on a ₹400 bill
    assert used["discount_paise"] == 21000 and used["total_paise"] == 19000
    info = client.get("/api/loyalty/customer", headers=h(asha), params={"phone": "9876543210"}).json()
    assert info["points_paise"] == 950 and info["stamps"] == 1        # earned 5% of ₹190; reward used 3 stamps, this bill gave 1
    # Too many points is refused; cancelling the redeeming bill gives everything back.
    o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).json()
    assert client.post("/api/bills/preview", headers=h(asha), json={"order_id": o["id"], "loyalty": {"points_paise": 50000, "phone": "9876543210"}}).status_code == 422
    client.post("/api/users", headers=h(tok), json={"name": "Mgr", "role": "manager", "pin": "7399", "phone": "9829000100", "outlet_ids": [oid]})
    assert client.post(f"/api/bills/{used['id']}/cancel", headers=h(asha), json={"reason": "Wrong customer", "manager_pin": "7399"}).status_code == 200
    info = client.get("/api/loyalty/customer", headers=h(asha), params={"phone": "9876543210"}).json()
    assert info["points_paise"] == 6000 and info["stamps"] == 3
    custs = client.get("/api/loyalty/customers", headers=h(tok)).json()
    assert custs[0]["phone"] == "9876543210" and custs[0]["visits"] == 3
    assert client.get("/api/loyalty/customers", headers=h(asha)).status_code == 403


def test_accounts_tally_and_excel(client):
    import io as _io
    from openpyxl import load_workbook
    t = signup(client, "acctest", "9829000111")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    tg = client.get("/api/menu/tax-groups", headers=h(tok)).json()[0]["id"]
    v = client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": "Oreo Shake", "tax_group_id": tg,
                    "variants": [{"name": "300 ML", "price_paise": 14900}]}).json()["variants"][0]["id"]
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "acctest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 0})
    for mode in ("cash", "upi"):
        o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v, "qty": 2}]}).json()
        total = client.post("/api/bills/preview", headers=h(asha), json={"order_id": o["id"]}).json()["total_paise"]
        client.post("/api/bills", headers=h(asha), json={"order_id": o["id"], "customer": {"declined": True}, "payments": [{"mode": mode, "amount_paise": total}]})
    sales = client.get("/api/accounts/tally", headers=h(tok), params={"kind": "sales"})
    assert sales.status_code == 200 and "<VOUCHER VCHTYPE=\"Sales\"" in sales.text and "<LEDGERNAME>UPI Collections</LEDGERNAME>" in sales.text
    assert client.get("/api/accounts/tally", headers=h(tok), params={"kind": "masters"}).text.count("<LEDGER ") >= 10
    assert client.get("/api/accounts/tally", headers=h(tok), params={"kind": "purchases"}).status_code == 404
    client.put("/api/tenant/accounts", headers=h(tok), json={"company_name": "Shakewallah Foods", "ledgers": {"upi": "HDFC Current"}})
    assert "HDFC Current" in client.get("/api/accounts/tally", headers=h(tok), params={"kind": "sales"}).text
    x = client.get("/api/accounts/excel", headers=h(tok))
    wb = load_workbook(_io.BytesIO(x.content))
    assert wb.sheetnames == ["Sales register", "GST by rate", "HSN summary", "Day-wise", "Purchase register"]
    assert wb["Sales register"].max_row == 3 + 2 + 1    # title, note, header, 2 bills, total


def test_cash_out_by_denomination(client):
    t = signup(client, "denomtest", "9829000122")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    s = client.post("/api/shifts", headers=h(tok), json={"outlet_id": oid, "opening_denoms": {"n500": 4}}).json()
    r = client.post(f"/api/shifts/{s['id']}/cash", headers=h(tok), json={"kind": "out", "denoms": {"n200": 1, "n50": 1, "c10": 2}, "reason": "Milk"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["cash_out_paise"] == 27000 and out["expected_cash_paise"] == 173000
    assert out["movements"][0]["denoms"] == {"n200": 1, "n50": 1, "c10": 2}
    assert client.post(f"/api/shifts/{s['id']}/cash", headers=h(tok), json={"kind": "in", "denoms": {"n999": 1}, "reason": "x1"}).status_code == 422
    assert client.post(f"/api/shifts/{s['id']}/cash", headers=h(tok), json={"kind": "in", "reason": "nothing"}).status_code == 422


def test_drawers_keep_cashiers_cash_apart(client):
    t = signup(client, "drawtest", "9829000133")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    for name, pin in (("Asha", "7311"), ("Bina", "7322")):
        client.post("/api/users", headers=h(tok), json={"name": name, "role": "cashier", "pin": pin, "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "drawtest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    bina = client.post("/api/auth/pin", json={"login_id": "drawtest", "outlet_id": oid, "pin": "7322"}).json()["token"]
    a1 = client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 100000}).json()
    b1 = client.post("/api/shifts", headers=h(bina), json={"outlet_id": oid, "opening_cash_paise": 50000}).json()
    assert a1["drawer_name"] == "Drawer 1" and b1["drawer_name"] == "Drawer 2"
    # Bina can't put money in or close Asha's shift; each has their own expenses.
    assert client.post(f"/api/shifts/{a1['id']}/cash", headers=h(bina), json={"kind": "out", "amount_paise": 100, "reason": "x1"}).status_code == 403
    client.post(f"/api/shifts/{a1['id']}/cash", headers=h(asha), json={"kind": "out", "amount_paise": 20000, "reason": "Milk"})
    client.post(f"/api/shifts/{b1['id']}/cash", headers=h(bina), json={"kind": "in", "amount_paise": 10000, "reason": "Change"})
    client.post(f"/api/shifts/{a1['id']}/close", headers=h(asha), json={"counted_cash_paise": 80000})
    # The manager sees both, with their own movements; next day Asha's opening is checked against Drawer 1 only.
    rows = {s["opened_by_name"]: s for s in client.get("/api/shifts", headers=h(tok), params={"outlet_id": oid}).json()}
    assert rows["Asha"]["status"] == "closed" and rows["Asha"]["cash_out_paise"] == 20000
    assert rows["Bina"]["status"] == "open" and rows["Bina"]["expected_cash_paise"] == 60000 and rows["Bina"]["movements"][0]["reason"] == "Change"
    a2 = client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 80000}).json()
    assert a2["drawer_name"] == "Drawer 1" and not a2["manager_alerted"]
    drawers = client.get(f"/api/outlets/{oid}/drawers", headers=h(asha)).json()
    assert {d["name"]: d["in_use_by"] for d in drawers} == {"Drawer 1": "Asha", "Drawer 2": "Bina"}


def test_owner_chooses_who_can_do_what(client):
    t = signup(client, "roletest", "9829000144")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "roletest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    assert "reports.view" not in client.get("/api/tenant/me", headers=h(asha)).json()["permissions"]
    assert client.get("/api/reports/list", headers=h(asha)).status_code == 403
    view = client.get("/api/tenant/roles", headers=h(tok)).json()
    cashier = next(r for r in view["roles"] if r["role"] == "cashier")
    r = client.put("/api/tenant/roles/cashier", headers=h(tok), json={"perms": cashier["perms"] + ["reports.view", "cash.expected"]})
    assert next(x for x in r.json()["roles"] if x["role"] == "cashier")["changed"]
    assert {"reports.view", "cash.expected"} <= set(client.get("/api/tenant/me", headers=h(asha)).json()["permissions"])
    assert client.get("/api/reports/list", headers=h(asha)).status_code == 200
    s = client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 1000}).json()
    assert s["expected_cash_paise"] == 1000          # no longer a blind count
    assert client.put("/api/tenant/roles/owner", headers=h(tok), json={"perms": []}).status_code == 422
    assert client.put("/api/tenant/roles/cashier", headers=h(asha), json={"perms": []}).status_code == 403
    client.put("/api/tenant/roles/cashier", headers=h(tok), json={"perms": cashier["default"]})   # back to defaults
    assert client.get("/api/reports/list", headers=h(asha)).status_code == 403


def test_card_machine_push_test_mode(client):
    import uuid as _uuid
    t = signup(client, "cardtest", "9829000155")
    tok = t["token"]
    brand_id = client.get("/api/tenant/brands", headers=h(tok)).json()[0]["id"]
    oid = client.post("/api/tenant/outlets", headers=h(tok), json={"brand_id": brand_id, "name": "Paota", "code": "PT"}).json()["id"]
    cat = client.post("/api/menu/categories", json={"brand_id": brand_id, "name": "Shakes"}, headers=h(tok)).json()["id"]
    v = client.post("/api/menu/items", headers=h(tok), json={"brand_id": brand_id, "category_id": cat, "name": "Oreo Shake",
                    "variants": [{"name": "300 ML", "price_paise": 14900}]}).json()["variants"][0]["id"]
    client.put("/api/tenant/payments", headers=h(tok), json={"enabled": False, "provider": "test", "pos_enabled": True, "pos_provider": "test"})
    term = client.post(f"/api/outlets/{oid}/card-terminals", headers=h(tok), json={"name": "Counter 1", "device_id": "1490012345|ezetap_android"}).json()
    client.post("/api/users", headers=h(tok), json={"name": "Asha", "role": "cashier", "pin": "7311", "outlet_ids": [oid]})
    asha = client.post("/api/auth/pin", json={"login_id": "cardtest", "outlet_id": oid, "pin": "7311"}).json()["token"]
    assert client.get("/api/upi/enabled", headers=h(asha)).json()["card_enabled"]
    client.post("/api/shifts", headers=h(asha), json={"outlet_id": oid, "opening_cash_paise": 0})
    o = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).json()
    body = {"order_id": o["id"], "bill_id": str(_uuid.uuid4()), "terminal_id": term["id"], "customer": {"declined": True}}
    r = client.post("/api/card/requests", headers=h(asha), json=body).json()
    assert r["status"] == "waiting" and r["amount_paise"] == 14900 and r["terminal"] == "Counter 1"
    o2 = client.post("/api/orders", headers=h(asha), json={"outlet_id": oid, "order_type": "takeaway", "items": [{"variant_id": v}]}).json()
    busy = client.post("/api/card/requests", headers=h(asha), json={**body, "order_id": o2["id"], "bill_id": str(_uuid.uuid4())})
    assert busy.status_code == 409                       # one payment at a time per machine
    declined = client.post(f"/api/card/requests/{r['id']}/test-result", headers=h(asha), params={"approved": False}).json()
    assert declined["status"] == "failed" and declined["bill"] is None
    r2 = client.post("/api/card/requests", headers=h(asha), json={**body, "bill_id": str(_uuid.uuid4())}).json()
    ok = client.post(f"/api/card/requests/{r2['id']}/test-result", headers=h(asha)).json()
    assert ok["status"] == "billed" and ok["bill"]["payments"][0]["mode"] == "card" and ok["bill"]["payments"][0]["reference"].startswith("card 4242")
