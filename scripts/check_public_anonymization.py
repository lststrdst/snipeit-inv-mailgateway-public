#!/usr/bin/env python3
"""Fail when a public snapshot contains infrastructure-specific identifiers."""

from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    "",
    ".conf",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".service",
    ".sh",
    ".svg",
    ".timer",
    ".toml",
    ".txt",
    ".xml",
}
BLOCKED_LITERALS = {
    "43" + "brands",
    "bbg" + ".local",
    "lst" + "strdst",
    "roz" + "hkov",
    "trans" + "com",
    "yan" + "dex.ru",
}
PUBLIC_AUTHOR_SIGNATURE = "© " + "lst" + "strdst"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
IP_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
INTERNAL_DIRECTORY_RE = re.compile(r"(?:^|[\s,])(OU|DC)=[^\s,]+", re.IGNORECASE)
PRIVATE_KEY_RE = re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY")
INTERNAL_FIELD_RE = re.compile(r"_snipeit_(?!example_)[a-z0-9_]+_\d+", re.IGNORECASE)


def findings(root: Path, path: Path, text: str) -> list[str]:
    # Я разрешаю публичный ник только в отдельной строке подписи корневого README.
    if path.relative_to(root) == Path("README.md"):
        text = "\n".join(
            "" if line == PUBLIC_AUTHOR_SIGNATURE else line for line in text.splitlines()
        )
    lower = text.lower()
    result = [f"blocked literal: {value}" for value in BLOCKED_LITERALS if value in lower]
    for match in EMAIL_RE.finditer(text):
        if match.group(1).lower() not in {"example.com", "example.test"}:
            result.append(f"non-example email domain: {match.group(1)}")
    for value in IP_RE.findall(text):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        documentation = any(address in network for network in (
            ipaddress.ip_network("192.0.2.0/24"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.0/24"),
        ))
        if (address.is_private or address.is_global) and not documentation and not address.is_loopback:
            result.append(f"non-documentation IP: {address}")
    if INTERNAL_DIRECTORY_RE.search(text):
        result.append("OU/DN fragment")
    if PRIVATE_KEY_RE.search(text):
        result.append("private key material")
    if INTERNAL_FIELD_RE.search(text):
        result.append("non-example Snipe-IT field handle")
    return [f"{path.relative_to(root)}: {item}" for item in result]


def main() -> int:
    errors: list[str] = []
    roots = [Path(value).resolve() for value in sys.argv[1:]] or [ROOT]
    for root in roots:
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if (
                not path.is_file()
                or path.resolve() == Path(__file__).resolve()
                or ".git" in path.parts
                or any(part.startswith(".") for part in relative.parts)
                or "__pycache__" in path.parts
                or path.suffix.lower() not in TEXT_SUFFIXES
            ):
                continue
            errors.extend(
                findings(root, path, path.read_text(encoding="utf-8", errors="strict"))
            )
    if errors:
        print("Public anonymization check failed:", file=sys.stderr)
        print("\n".join(sorted(set(errors))), file=sys.stderr)
        return 1
    print("Public anonymization check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
