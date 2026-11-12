"""Normalise phone numbers and emails used as login identifiers."""
from __future__ import annotations

import re

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def normalize_phone(raw: str) -> str:
    """Indian mobile numbers -> 10 digits. Accepts +91, 91, 0 prefixes, spaces and dashes."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise ValueError("Enter a valid 10-digit Indian mobile number")
    return digits


def normalize_email(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not _EMAIL.match(value) or len(value) > 254:
        raise ValueError("Enter a valid email address")
    return value


def normalize_identifier(raw: str) -> tuple[str, str]:
    """Returns ('email', value) or ('phone', value)."""
    if "@" in (raw or ""):
        return "email", normalize_email(raw)
    return "phone", normalize_phone(raw)


def mask_identifier(kind: str, value: str) -> str:
    if kind == "phone":
        return f"******{value[-4:]}"
    name, _, domain = value.partition("@")
    return f"{name[:2]}***@{domain}"


def validate_hex_color(raw: str) -> str:
    if not _HEX_COLOR.match(raw or ""):
        raise ValueError("Colour must look like #1a2b3c")
    return raw.lower()


def validate_gstin(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip().upper()
    if not _GSTIN.match(value):
        raise ValueError("GSTIN must be 15 characters, e.g. 08ABCDE1234F1Z5")
    return value
