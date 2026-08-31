#!/usr/bin/env bash
set -euo pipefail
umask 077
slug=snipeit-inventory-gateway
target=${1:-/var/backups/$slug}
install -d -m 0700 "$target"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive=$target/$slug-$stamp.tar.gz
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
python3 - /var/lib/$slug/events.sqlite3 "$tmp/events.sqlite3" <<'PY'
import sqlite3, sys
source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
source.backup(target)
target.close(); source.close()
PY
install -m 0600 /etc/$slug/config.json "$tmp/config.json"
tar -C "$tmp" -czf "$archive" config.json events.sqlite3
chmod 0600 "$archive"
sha256sum "$archive" > "$archive.sha256"
find "$target" -type f -mtime +30 -name "$slug-*" -delete
echo "$archive"
