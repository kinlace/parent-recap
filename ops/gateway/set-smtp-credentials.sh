#!/usr/bin/env bash
# Puts the sending domain's SMTP credentials (OCI Email Delivery, README "The sending domain")
# into their own env file on the gateway, replacing it on rotation. Not bifrost.env: Bifrost's
# unit loads that file, and Bifrost has no use for them.
#
#   scp ops/gateway/set-smtp-credentials.sh <vm>:/tmp/
#   ssh -t <vm> "sudo bash /tmp/set-smtp-credentials.sh"
#
# Typed at the prompt, so they stay out of shell history and the repo. The Brief relay reads the
# file when it starts, so it is restarted if it is installed.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
dir=/etc/brief-relay
env=$dir/brief-relay.env
old=/etc/bifrost/bifrost.env
[ -t 0 ] || { echo "needs ssh -t to ask for the credentials" >&2; exit 1; }

read -r -p "SMTP username (ocid1.user…@ocid1.tenancy….com): " user
read -r -s -p "SMTP password: " pass; echo
[ -n "$user" ] && [ -n "$pass" ] || { echo "empty value" >&2; exit 1; }

install -d -m 0700 "$dir"
umask 077
tmp=$(mktemp "$env.XXXXXX")
printf 'BRIEF_SMTP_USERNAME=%s\nBRIEF_SMTP_PASSWORD=%s\n' "$user" "$pass" >"$tmp"
unset user pass
chmod 0600 "$tmp"
mv "$tmp" "$env"

# Before #525 the credentials went into bifrost.env; take them out of there.
if [ -f "$old" ] && grep -q '^BRIEF_SMTP_' "$old"; then
  tmp=$(mktemp "$old.XXXXXX")
  grep -v '^BRIEF_SMTP_' "$old" >"$tmp" || true
  chmod 0600 "$tmp"
  mv "$tmp" "$old"
  echo "removed the SMTP lines from $old"
fi
echo "SMTP credentials written to $env"
if systemctl is-enabled --quiet brief-relay 2>/dev/null; then
  systemctl restart brief-relay
  echo "brief-relay restarted"
fi
