#!/usr/bin/env bash
# Makes a Gateway Key for a Household on its own computer and prints its name and its `sk-bf-…`
# value, to send to the Household over a private channel. Without --send-only the key writes the
# Brief with the gateway's AI and sends it; with it, the key only sends (README "Create a Gateway
# Key"). Run on the server by a maintainer:
#
#   sudo bash /usr/local/lib/brief-relay/new-key.sh <label> "<who holds it>" [--send-only]
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
# Bifrost's admin login makes the key; the provider key in the same file is not read. It goes to
# the command in its environment, never on a command line.
env=/etc/bifrost/bifrost.env
[ -r "$env" ] || { echo "$env is missing: run gateway-setup.sh first" >&2; exit 1; }
while IFS='=' read -r name value || [ -n "$name" ]; do
  case "$name" in
    BIFROST_ADMIN_USERNAME | BIFROST_ADMIN_PASSWORD) export "$name=$value" ;;
  esac
done <"$env"
exec python3 /usr/local/lib/brief-relay/brief_relay.py new-key "$@"
