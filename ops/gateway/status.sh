#!/usr/bin/env bash
# Lists every Hosted Household with its last run and doctor's statuses, and nothing of its
# messages (ops/gateway/README.md, "See every Household's status"). Run on the server by a
# maintainer:
#
#   sudo bash /usr/local/lib/parent-recap-signup/status.sh
#
# It runs `signup status` as the sign-up service's user, which runs `signup health` as each
# Household's user, as signup.sudoers lets it.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
cd /
exec sudo -u parent-recap-signup -H -- \
  /var/lib/parent-recap-signup/ParentRecap/app/.venv/bin/parent-recap signup status "$@"
