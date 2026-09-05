from __future__ import annotations

PRODUCT = "SnipeIT Inventory Gateway"

SUBJECT_TAGS = {
    "relay": "RELAY",
    "computer-report": "REPORT",
    "weekly-report": "WEEKLY",
    "owner-change": "OWNER",
    "warning": "WARNING",
    "error": "ERROR",
    "alert": "ALERT",
    "recovery": "RECOVERY",
}

ROUTING_TAGS = {
    "relay": {"RELAY"},
    "computer-report": {"REPORT"},
    "weekly-report": {"WEEKLY"},
    "owner-change": {"OWNER"},
    "warning": {"WARNING", "RECOVERY"},
    "error": {"ERROR"},
    "alert": {"ALERT"},
}


def mail_subject(category: str, detail: str) -> str:
    """Build one-line, bounded subjects with a stable machine-readable tag."""
    tag = SUBJECT_TAGS[category]
    clean = " ".join(str(detail).split()).strip(" ·—")
    if not clean:
        raise ValueError("mail subject detail is required")
    return f"[{PRODUCT}][{tag}] {clean[:180]}"


def subject_matches_category(subject: str, category: str) -> bool:
    tags = ROUTING_TAGS.get(category, set())
    folded = subject.casefold()
    return any(folded.startswith(f"[{PRODUCT}][{tag}]".casefold()) for tag in tags)
