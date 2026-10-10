#!/usr/bin/env bash
# Replaces the Anthropic API key the gateway uses, then restarts it.
#
#   scp ops/gateway/set-provider-key.sh <vm>:/tmp/
#   ssh -t <vm> "sudo bash /tmp/set-provider-key.sh"
#
# The key is typed at the prompt, so it stays out of shell history and the repo.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
env=/etc/bifrost/bifrost.env
[ -f "$env" ] || { echo "$env missing: run gateway-setup.sh first" >&2; exit 1; }
[ -t 0 ] || { echo "needs ssh -t to ask for the key" >&2; exit 1; }

read -r -s -p "New Anthropic API key: " key; echo
[ -n "$key" ] || { echo "empty key" >&2; exit 1; }

# awk, not sed: the key is data, not a pattern.
umask 077
tmp=$(mktemp "$env.XXXXXX")
KEY=$key awk '/^ANTHROPIC_API_KEY=/ {print "ANTHROPIC_API_KEY=" ENVIRON["KEY"]; next} {print}' "$env" >"$tmp"
unset key
chmod 0600 "$tmp"
mv "$tmp" "$env"

systemctl restart bifrost
for _ in $(seq 1 20); do
  curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 && { echo "bifrost restarted with the new key"; exit 0; }
  sleep 1
done
echo "bifrost did not answer on /health; see: journalctl -u bifrost" >&2
exit 1
