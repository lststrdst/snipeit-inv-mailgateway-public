from __future__ import annotations

import re

PROTECTED_EXACT = {
    "administrator",
    "guest",
    "krbtgt",
    "system",
    "shared-terminal",
    "snipeit",
}
PROTECTED_PREFIXES = ("ad_", "svc_", "service_", "dwm-", "umfd-")


def normalized_username(value: object) -> str:
    username = str(value or "").strip().lower()
    if "\\" in username:
        username = username.rsplit("\\", 1)[1]
    if "@" in username:
        username = username.split("@", 1)[0]
    return username


def safe_owner(identity: dict) -> str | None:
    """Return an assignable username, never a system/service identity."""
    username = normalized_username(identity.get("detected_username"))
    if not username or len(username) > 128 or not re.fullmatch(r"[a-z0-9._-]+", username):
        return None
    if username in PROTECTED_EXACT or username.startswith(PROTECTED_PREFIXES):
        return None
    return username


def disposition(identity: dict) -> tuple[str, str | None]:
    """Propose only a safe owner; Snipe-IT must confirm one exact username match.

    Endpoint-supplied disabled/offboarding flags are intentionally ignored. A client key is
    sufficient to report inventory, but never authoritative enough to check an asset in.
    """
    owner = safe_owner(identity)
    if owner:
        return "assigned", owner
    return "preserve", None
