#!/usr/bin/env bash
set -euo pipefail
slug=snipeit-inventory-gateway
[[ $(id -u) -eq 0 ]] || { echo "root is required" >&2; exit 1; }
previous=$(cat /var/lib/$slug/previous-release)
case "$previous" in
  /opt/$slug/releases/*) ;;
  *) echo "invalid rollback target" >&2; exit 1 ;;
esac
[[ -d $previous && -x $previous/venv/bin/snipeit-inventory-gateway ]] || { echo "rollback target is unavailable" >&2; exit 1; }
ln -sfn "$previous" /opt/$slug/current.new
mv -Tf /opt/$slug/current.new /opt/$slug/current
systemctl restart $slug-api.service $slug-worker.service
systemctl is-active --quiet $slug-api.service $slug-worker.service
echo "rolled back to $(basename "$previous")"
