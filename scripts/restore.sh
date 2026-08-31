#!/usr/bin/env bash
set -euo pipefail
slug=snipeit-inventory-gateway
[[ $(id -u) -eq 0 ]] || { echo "root is required" >&2; exit 1; }
[[ $# -eq 1 && -f $1 && -f $1.sha256 ]] || { echo "archive and checksum are required" >&2; exit 2; }
(cd "$(dirname "$1")" && sha256sum -c "$(basename "$1").sha256")
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
tar -C "$tmp" -xzf "$1"
[[ -f $tmp/config.json && -f $tmp/events.sqlite3 ]] || { echo "incomplete backup" >&2; exit 1; }
systemctl stop $slug-api.service $slug-worker.service $slug-mail.timer
install -o root -g "$slug" -m 0640 "$tmp/config.json" /etc/$slug/config.json
install -o "$slug" -g "$slug" -m 0600 "$tmp/events.sqlite3" /var/lib/$slug/events.sqlite3
systemctl start $slug-api.service $slug-worker.service $slug-mail.timer
echo "restore complete"
