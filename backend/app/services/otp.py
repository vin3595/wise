"""OTP issue/verify with pluggable delivery. Codes are stored as HMACs, never in plain text."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.security import generate_otp, hash_otp, otp_matches
from ..models import OtpCode

log = logging.getLogger("wise.otp")

MAX_SENDS_PER_WINDOW = 5
SEND_WINDOW_MINUTES = 15


class OtpError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue(db: Session, identifier: str, purpose: str) -> str:
    """Create and deliver an OTP. Returns the code (only exposed to callers in console mode)."""
    s = get_settings()
    since = _now() - timedelta(minutes=SEND_WINDOW_MINUTES)
    recent = db.scalar(
        select(func.count()).select_from(OtpCode).where(
            OtpCode.identifier == identifier, OtpCode.created_at >= since)
    )
    if recent and recent >= MAX_SENDS_PER_WINDOW:
        raise OtpError("Too many codes requested. Try again in 15 minutes.")
    code = generate_otp()
    db.add(OtpCode(
        identifier=identifier, purpose=purpose, code_hash=hash_otp(code, identifier, s.jwt_secret),
        expires_at=_now() + timedelta(minutes=s.otp_ttl_minutes),
    ))
    db.flush()
    _deliver(identifier, code)
    return code


def verify(db: Session, identifier: str, purpose: str, code: str) -> None:
    s = get_settings()
    otp = db.scalar(
        select(OtpCode).where(
            OtpCode.identifier == identifier, OtpCode.purpose == purpose, OtpCode.consumed_at.is_(None))
        .order_by(OtpCode.created_at.desc()).limit(1).with_for_update()
    )
    if otp is None or otp.expires_at < _now():
        raise OtpError("Code expired. Request a new one.")
    if otp.attempts >= s.otp_max_attempts:
        raise OtpError("Too many wrong attempts. Request a new code.")
    if not otp_matches(code, identifier, s.jwt_secret, otp.code_hash):
        otp.attempts += 1
        raise OtpError("Wrong code")
    otp.consumed_at = _now()


def _phone(identifier: str) -> str:
    """Staff and owner sign-in codes are keyed "<tenant id>:<phone>"; SMS goes to the phone part."""
    return identifier.rsplit(":", 1)[-1]


def _deliver(identifier: str, code: str) -> None:
    s = get_settings()
    if s.otp_provider in ("console", "box") or "@" in identifier:
        # Email delivery (SMTP/SES) is a TODO; console mode is for local development.
        log.warning("OTP for %s: %s", identifier, code)
        return
    if s.otp_provider == "msg91":
        # MSG91 OTP API: template must contain the ##OTP## placeholder.
        resp = httpx.post(
            "https://control.msg91.com/api/v5/otp",
            params={"template_id": s.msg91_template_id, "mobile": "91" + _phone(identifier), "otp": code},
            headers={"authkey": s.msg91_auth_key},
            timeout=10,
        )
        if resp.status_code >= 400:
            log.error("MSG91 send failed: %s %s", resp.status_code, resp.text[:200])
            raise OtpError("Couldn't send the code right now. Try again.")
        return
    if s.otp_provider == "fast2sms":
        # Fast2SMS OTP route: their own pre-approved OTP text, no DLT registration needed.
        resp = httpx.get(
            "https://www.fast2sms.com/dev/bulkV2",
            params={"authorization": s.fast2sms_api_key, "route": "otp", "variables_values": code,
                    "numbers": _phone(identifier), "flash": "0"},
            timeout=10,
        )
        ok = resp.status_code < 400
        try:
            ok = ok and bool(resp.json().get("return"))
        except ValueError:
            ok = False
        if not ok:
            log.error("Fast2SMS send failed: %s %s", resp.status_code, resp.text[:200])
            raise OtpError("Couldn't send the code right now. Try again.")
        return
    raise OtpError(f"Unknown OTP provider {s.otp_provider!r}")
