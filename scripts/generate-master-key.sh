#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ $# -ne 1 ]]; then
  echo "usage: $0 OUTPUT_FILE" >&2
  exit 2
fi
target=$1
if [[ -e "$target" ]]; then
  echo "refusing to overwrite existing key file" >&2
  exit 1
fi
install -d -m 0700 "$(dirname "$target")"
key=$(openssl rand -base64 32)
printf 'base64:%s\n' "$key" > "$target"
chmod 0600 "$target"
echo "random 256-bit key written with mode 0600" >&2
