#!/usr/bin/env bash
# Mints an Invite Link (ADR 0031) in the sign-up service's owner-only store and prints it.
# Run on the server by a maintainer; send the link to the family over a private channel.
#
#   sudo bash /usr/local/lib/parent-recap-signup/invite.sh \
#     --recipient parent@example.com:fi --recipient partner@example.com:en [--language fi] [--days 14]
#
# Each --recipient is prefilled on the page, with the language it reads the Brief in (fi, en or
# zh); --language is the page's until the family picks another. The link works once, for --days.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
# shellcheck source=/dev/null
. /etc/parent-recap-signup/signup.env
: "${GATEWAY_HOST:?GATEWAY_HOST is missing from /etc/parent-recap-signup/signup.env}"
cd /
exec sudo -u parent-recap-signup -H -- \
  /var/lib/parent-recap-signup/ParentRecap/app/.venv/bin/parent-recap \
  signup invite --url "https://$GATEWAY_HOST" "$@"
