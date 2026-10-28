#!/usr/bin/env bash
# Puts the sending domain's SMTP credentials (OCI Email Delivery, README "The sending domain")
# into the gateway's env file, adding the lines on first use and replacing them on rotation.
#
#   scp ops/gateway/set-smtp-credentials.sh <vm>:/tmp/
#   ssh -t <vm> "sudo bash /tmp/set-smtp-credentials.sh"
#
# Typed at the prompt, so they stay out of shell history and the repo. Nothing is restarted:
# the service that sends Briefs (#525) reads the file when it starts.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
env=/etc/bifrost/bifrost.env
[ -f "$env" ] || { echo "$env missing: run gateway-setup.sh first" >&2; exit 1; }
[ -t 0 ] || { echo "needs ssh -t to ask for the credentials" >&2; exit 1; }

read -r -p "SMTP username (ocid1.user…@ocid1.tenancy….com): " user
read -r -s -p "SMTP password: " pass; echo
[ -n "$user" ] && [ -n "$pass" ] || { echo "empty value" >&2; exit 1; }

# awk, not sed: the values are data, not patterns.
umask 077
tmp=$(mktemp "$env.XXXXXX")
SMTP_USER=$user SMTP_PASS=$pass awk '
  /^BRIEF_SMTP_USERNAME=/ { print "BRIEF_SMTP_USERNAME=" ENVIRON["SMTP_USER"]; seen_u = 1; next }
  /^BRIEF_SMTP_PASSWORD=/ { print "BRIEF_SMTP_PASSWORD=" ENVIRON["SMTP_PASS"]; seen_p = 1; next }
  { print }
  END {
    if (!seen_u) print "BRIEF_SMTP_USERNAME=" ENVIRON["SMTP_USER"]
    if (!seen_p) print "BRIEF_SMTP_PASSWORD=" ENVIRON["SMTP_PASS"]
  }' "$env" >"$tmp"
unset user pass
chmod 0600 "$tmp"
mv "$tmp" "$env"
echo "SMTP credentials written to $env"
