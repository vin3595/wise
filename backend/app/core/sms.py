"""SMS texts and rules (pure, unit-tested).

In India every business SMS must match a template registered on DLT (TRAI rules) under the
restaurant's own sender ID. MSG91 holds the approved template and fills in the ##variables##, so
Wise sends only the template id and the values. The texts below are what to register; the preview
in Wise and the log use the same text.
"""
from __future__ import annotations

import re
from datetime import timedelta

TEMPLATES: dict[str, dict] = {
    "bill": {
        "label": "E-bill to the customer",
        "text": "Thank you for visiting ##brand##. Your bill ##invoice## is Rs ##amount##. View it here: ##link##",
        "vars": ["brand", "invoice", "amount", "link"],
    },
    "ready": {
        "label": "Order ready (takeaway and delivery)",
        "text": "##brand##: your order, token ##token##, is ready. Please collect it at the counter.",
        "vars": ["brand", "token"],
    },
    "daily": {
        "label": "Day-end sales summary to you",
        "text": "##outlet## sales for ##date##: Rs ##sales## from ##bills## bills. Cash Rs ##cash##, UPI Rs ##upi##, "
                "card Rs ##card##. Discounts Rs ##discount##, cancelled ##cancelled##.",
        "vars": ["outlet", "date", "sales", "bills", "cash", "upi", "card", "discount", "cancelled"],
    },
    "cash": {
        "label": "Cash difference to managers",
        "text": "##outlet##: cash ##kind## by ##cashier## does not match. Expected Rs ##expected##, counted Rs ##counted## "
                "(##diff##). Please check in Wise.",
        "vars": ["outlet", "kind", "cashier", "expected", "counted", "diff"],
    },
    "alert": {
        "label": "Alerts to you (cancelled bills, big discounts, printer or box down)",
        "text": "Wise alert, ##outlet##: ##what##",
        "vars": ["outlet", "what"],
    },
    "test": {
        "label": "Test message",
        "text": "##brand##: SMS from Wise is working.",
        "vars": ["brand"],
    },
}

# Retry after a failed send: quickly at first (a blip), then slower (internet down on a Wise Box).
BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1), timedelta(hours=3),
           timedelta(hours=6)]
GIVE_UP_AFTER = len(BACKOFF)


def render(kind: str, values: dict) -> str:
    t = TEMPLATES[kind]
    missing = [v for v in t["vars"] if values.get(v) in (None, "")]
    if missing:
        raise ValueError(f"Missing {', '.join(missing)} for the {kind} SMS")
    return re.sub(r"##(\w+)##", lambda m: str(values[m.group(1)]), t["text"])


def msisdn(phone: str) -> str:
    """10-digit Indian mobile -> 91XXXXXXXXXX (what MSG91 expects)."""
    digits = re.sub(r"\D", "", phone or "")[-10:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise ValueError("Not an Indian mobile number")
    return "91" + digits


def rupees(paise: int) -> str:
    """1234550 -> '12,345.50'; whole rupees without decimals (SMS length matters)."""
    r, p = divmod(int(paise), 100)
    s = f"{r:,}"
    if r >= 1000:  # Indian grouping: 12,34,567
        head, tail = s.replace(",", "")[:-3], s.replace(",", "")[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = f"{head},{tail}"
    return s if p == 0 else f"{s}.{p:02d}"


def next_try(attempts: int) -> timedelta | None:
    """Wait before the next attempt, or None to give up."""
    return BACKOFF[attempts - 1] if 0 < attempts <= len(BACKOFF) else None


def segments(text: str) -> int:
    """SMS parts the text is charged as (plain GSM text: 160, then 153 per part)."""
    n = len(text)
    return 1 if n <= 160 else -(-n // 153)
