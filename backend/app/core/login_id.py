"""Restaurant login IDs double as subdomains (shakewallah.<wise-domain>)."""
from __future__ import annotations

import re

RESERVED = {
    "www", "api", "app", "admin", "wise", "support", "help", "billing", "status",
    "mail", "static", "assets", "cdn", "login", "signup", "dashboard", "docs",
}

_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,29}$")


def normalize_login_id(raw: str) -> str:
    return raw.strip().lower()


def validate_login_id(raw: str) -> str:
    """Return the normalized login ID or raise ValueError with a user-readable reason."""
    value = normalize_login_id(raw)
    if not _PATTERN.match(value):
        raise ValueError(
            "Login ID must be 3-30 characters: lowercase letters, numbers and hyphens, starting with a letter"
        )
    if value.endswith("-") or "--" in value:
        raise ValueError("Login ID can't end with a hyphen or contain two hyphens in a row")
    if value in RESERVED:
        raise ValueError("That login ID is reserved")
    return value


def login_id_from_host(host: str, base_domain: str) -> str | None:
    """'shakewallah.wise.in' with base 'wise.in' -> 'shakewallah'."""
    host = host.split(":")[0].lower()
    base_domain = base_domain.lower().lstrip(".")
    if not base_domain or not host.endswith("." + base_domain):
        return None
    label = host[: -(len(base_domain) + 1)]
    if "." in label or label in RESERVED:
        return None
    return label or None
