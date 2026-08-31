#!/usr/bin/env bash
set -euo pipefail
slug=snipeit-inventory-gateway
pip_requirement='pip>=26.2.1,<27'
[[ $(id -u) -eq 0 ]] || { echo "root is required" >&2; exit 1; }
[[ $# -eq 2 ]] || { echo "usage: $0 VERSION WHEEL" >&2; exit 2; }
version=$1
wheel=$2
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && -f $wheel ]] || { echo "invalid version or wheel" >&2; exit 2; }
release=/opt/$slug/releases/$version
[[ ! -e $release ]] || { echo "release already exists" >&2; exit 1; }
install -d -o root -g root -m 0755 "$release"
python3 -m venv "$release/venv"
"$release/venv/bin/pip" install --quiet --upgrade "$pip_requirement"
"$release/venv/bin/pip" install --quiet "$wheel"
"$release/venv/bin/snipeit-inventory-gateway" --config /etc/$slug/config.json check-config
readlink -f /opt/$slug/current > /var/lib/$slug/previous-release
ln -sfn "$release" /opt/$slug/current.new
mv -Tf /opt/$slug/current.new /opt/$slug/current
systemctl restart $slug-api.service $slug-worker.service
systemctl is-active --quiet $slug-api.service $slug-worker.service
echo "updated to $version"
