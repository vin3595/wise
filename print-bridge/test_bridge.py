import socket
import threading

import wise_print_bridge as b

JOB = {"kind": "kot", "printed_before": 0, "taken_by": "Captain Ravi", "kot_id": "k1", "printer": None,
       "kot": {"prep_area_name": "Kitchen", "token_no": 12, "order_type": "dine_in", "table_label": "T4",
               "car_number": None, "kot_no": 34, "created_at": "2026-11-10T10:50:00Z", "customer_name": None,
               "is_addon_kot": False, "order_notes": "Serve together",
               "lines": [{"item_name": "Cheese Garlic Bread with extra jalapeno and olives", "variant_name": "Regular",
                          "qty": 2, "addons": [{"name": "Extra cheese"}], "notes": "Less spicy"},
                         {"item_name": "Oreo Shake", "variant_name": "1000 ML", "qty": 1, "addons": [], "notes": None}]}}


def text(data: bytes) -> str:
    # Drop ESC/GS command bytes so we can read the slip text.
    out, i = [], 0
    while i < len(data):
        c = data[i]
        if c in (0x1B, 0x1D):
            i += 3 if data[i + 1:i + 2] in (b"!", b"a", b"E") else 2
            if data[i - 1:i] == b"V":
                i += 2
            continue
        out.append(chr(c))
        i += 1
    return "".join(out)


def test_slip_has_what_the_kitchen_needs():
    t = text(b.kot_slip(JOB, 80))
    assert "TOKEN 12" in t and "Table T4\nDine-in" in t and "KOT 34  10-11-26 04:20 PM" in t
    assert "By Captain Ravi" in t and "1 x Oreo Shake (1000 ML)" in t and "Regular" not in t
    assert "+ Extra cheese" in t and "Note: Less spicy" in t and "ORDER NOTE: Serve together" in t and "Items: 3" in t
    assert all(len(ln) <= 48 for ln in t.splitlines())


def test_narrow_paper_wraps():
    t = text(b.kot_slip(JOB, 58))
    assert all(len(ln) <= 32 for ln in t.splitlines())
    assert "2 x Cheese Garlic Bread with" in t


def test_reprint_cancel_and_addon_marks():
    assert "*** REPRINT 3 ***" in text(b.kot_slip({**JOB, "kind": "reprint", "printed_before": 2}))
    c = text(b.kot_slip({**JOB, "kind": "cancel"}))
    assert "*** CANCELLED ***" in c and "Do not make this order" in c
    assert "ADDED ITEMS" in text(b.kot_slip({**JOB, "kot": {**JOB["kot"], "is_addon_kot": True}}))
    assert "Copy 2 of 2" in text(b.kot_slip(JOB, 80, 2, 2))


def test_non_ascii_is_safe():
    assert b.clean("Paneer ₹149 – spicy") == "Paneer Rs149 - spicy"
    b.kot_slip({**JOB, "kot": {**JOB["kot"], "customer_name": "रवि"}})  # must not raise


def test_sends_bytes_to_a_network_printer():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = []

    def accept():
        c, _ = srv.accept()
        with c:
            while chunk := c.recv(4096):
                got.append(chunk)

    th = threading.Thread(target=accept)
    th.start()
    port = srv.getsockname()[1]
    err = b.print_job({**JOB, "printer": {"host": "127.0.0.1", "port": port, "paper_mm": 80, "copies": 2}})
    th.join(timeout=3)
    srv.close()
    data = b"".join(got)
    assert err is None and data.count(b.CUT) == 2 and b"TOKEN 12" in data


def test_offline_printer_reports_error():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listening
    err = b.print_job({**JOB, "printer": {"host": "127.0.0.1", "port": port, "paper_mm": 80, "copies": 1}})
    assert err and "not reachable" in err
