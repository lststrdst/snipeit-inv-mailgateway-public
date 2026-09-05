#!/usr/bin/env python3
"""Build a v1 config without ever printing inherited secrets."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    old_path, output = Path(args.old_config), Path(args.output)
    old = json.loads(old_path.read_text(encoding="utf-8"))
    required = ["imap_password", "snipe_token"]
    missing = [name for name in required if not old.get(name)]
    if missing:
        raise SystemExit("legacy config is missing required protected values")
    if args.dry_run:
        print("legacy config can be migrated; no secrets were displayed and no file was written")
        return 0
    if output.exists():
        raise SystemExit("refusing to overwrite output")
    master = base64.b64encode(os.urandom(32)).decode()
    config = {
        "schema_version": 1,
        "environment": "staging",
        "keys": [
            {"key_id": "fleet-2026-01", "master_key": f"base64:{master}", "decrypt_only": False}
        ],
        "queue": {"path": "/var/lib/snipeit-inventory-gateway/events.sqlite3"},
        "api": {},
        "snipeit": {
            "url": old.get("snipe_url", "https://127.0.0.1"),
            "host_header": old.get("snipe_host_header", "snipeit.local"),
            "api_token": old["snipe_token"],
            "verify_tls": old.get("verify_tls", False),
        },
        "imap": {
            "user": "inventory@example.com",
            "password": old["imap_password"],
            "allowed_from": ["notification@example.com"],
        },
        "smtp": {
            "user": "notification@example.com",
            "password": old.get("smtp_password", "REPLACE_AT_DEPLOYMENT"),
            "from_address": "notification@example.com",
            "to_address": "inventory@example.com",
            "starttls": True,
        },
        "notifications": {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(config, stream, indent=2)
        stream.write("\n")
    print("migration config written with mode 0600; secrets were not displayed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
