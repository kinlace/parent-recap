#!/usr/bin/env bash
# Lists every Gateway Key, one line each: its name, whether it writes the Brief or only sends it,
# whether it is on, and its spend today; a key made on request is followed by its addresses and
# who confirmed. No key's value is printed. Run on the server by a maintainer:
#
#   sudo bash /usr/local/lib/brief-relay/keys.sh
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
# Bifrost's admin login lists the keys; the provider key in the same file is not read. It goes to
# the command in its environment, never on a command line.
env=/etc/bifrost/bifrost.env
[ -r "$env" ] || { echo "$env is missing: run gateway-setup.sh first" >&2; exit 1; }
while IFS='=' read -r name value || [ -n "$name" ]; do
  case "$name" in
    BIFROST_ADMIN_USERNAME | BIFROST_ADMIN_PASSWORD) export "$name=$value" ;;
  esac
done <"$env"
# The relay's unit has a DynamicUser, so its state directory is under /var/lib/private.
export STATE_DIRECTORY=/var/lib/private/brief-relay
exec python3 /usr/local/lib/brief-relay/brief_relay.py keys
