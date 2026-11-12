"""Hashing and tokens using only the standard library + PyJWT."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_secret(value: str) -> str:
    """scrypt hash for PINs. Format: scrypt$<salt_b64>$<hash_b64>."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_secret(value: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, salt_b64, hash_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except ValueError:
        return False
    digest = hashlib.scrypt(value.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return hmac.compare_digest(digest, expected)


def validate_pin(pin: str) -> str:
    if not (pin.isdigit() and len(pin) == 4):
        raise ValueError("PIN must be exactly 4 digits")
    if len(set(pin)) == 1 or pin in {"1234", "4321", "0123", "9876"}:
        raise ValueError("PIN is too easy to guess")
    return pin


def pin_lookup_key(pin: str, tenant_id: str, secret: str) -> str:
    """Deterministic, secret-keyed fingerprint so a PIN can be looked up per tenant."""
    return hmac.new(secret.encode(), f"pin:{tenant_id}:{pin}".encode(), hashlib.sha256).hexdigest()


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str, identifier: str, secret: str) -> str:
    """HMAC so a leaked OTP table can't be brute-forced offline without the server secret."""
    return hmac.new(secret.encode(), f"{identifier}:{code}".encode(), hashlib.sha256).hexdigest()


def otp_matches(code: str, identifier: str, secret: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(code, identifier, secret), stored_hash)


def create_token(claims: dict[str, Any], secret: str, hours: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    payload = {**claims, "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=hours)).timestamp())}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError on invalid or expired tokens."""
    return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub", "tid"]})
