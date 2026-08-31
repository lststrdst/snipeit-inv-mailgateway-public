#!/usr/bin/env bash
set -euo pipefail
slug=snipeit-inventory-gateway
version=1.0.0
pip_requirement='pip>=26.2.1,<27'
dry_run=false
artifact=
config_source=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) dry_run=true ;;
    --artifact) artifact=$2; shift ;;
    --config) config_source=$2; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ -n "$artifact" && -f "$artifact" ]] || { echo "--artifact is required" >&2; exit 2; }
[[ -z "$config_source" || -f "$config_source" ]] || { echo "config file does not exist" >&2; exit 2; }

release=/opt/$slug/releases/$version
if $dry_run; then
  dry_root=$(mktemp -d -t "$slug-dry-run.XXXXXX")
  trap 'rm -rf -- "$dry_root"' EXIT
  python3 -m venv "$dry_root/venv"
  "$dry_root/venv/bin/pip" install --quiet --upgrade "$pip_requirement"
  "$dry_root/venv/bin/pip" install --quiet "$artifact"
  "$dry_root/venv/bin/snipeit-inventory-gateway" --version
  if [[ -n "$config_source" ]]; then
    "$dry_root/venv/bin/snipeit-inventory-gateway" --config "$config_source" check-config
  fi
  echo "dry-run complete; system state unchanged"
  exit 0
fi

[[ $(id -u) -eq 0 ]] || { echo "root is required" >&2; exit 1; }
getent group "$slug" >/dev/null || groupadd --system "$slug"
id "$slug" >/dev/null 2>&1 || useradd --system --gid "$slug" --home-dir /nonexistent --shell /usr/sbin/nologin "$slug"
install -d -o root -g "$slug" -m 0750 /etc/$slug
install -d -o "$slug" -g "$slug" -m 0750 /var/lib/$slug /var/log/$slug
install -d -o root -g root -m 0755 /opt/$slug/releases "$release"
python3 -m venv "$release/venv"
"$release/venv/bin/pip" install --quiet --upgrade "$pip_requirement"
"$release/venv/bin/pip" install --quiet "$artifact"
if [[ -n "$config_source" && ! -e /etc/$slug/config.json ]]; then
  install -o root -g "$slug" -m 0640 "$config_source" /etc/$slug/config.json
fi
[[ -f /etc/$slug/config.json ]] || { echo "protected config is required" >&2; exit 1; }
"$release/venv/bin/snipeit-inventory-gateway" --config /etc/$slug/config.json check-config
ln -sfn "$release" /opt/$slug/current.new
mv -Tf /opt/$slug/current.new /opt/$slug/current
systemctl daemon-reload
echo "installed $slug $version; services were not enabled or started"
