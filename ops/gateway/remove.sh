#!/usr/bin/env bash
# Removes a Hosted Household (ADR 0031) with everything of it on the server, and prints each step:
# its evening jobs, its Gateway Key, its Linux user with its home, and its line in the sign-up
# service's store. Run on the server by a maintainer; a removal that stopped half-way is finished
# by running it again.
#
#   sudo bash /usr/local/lib/parent-recap-signup/remove.sh ph-0123abcd
#
# The label is the Household's user, as `ls /home` and the sign-up's log show it.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
[ $# -eq 1 ] || { echo "usage: remove.sh ph-XXXXXXXX" >&2; exit 2; }
# Bifrost's admin login revokes the key, as for the service; the provider key in the same file is
# not read. It goes to the command in its environment, never on a command line.
while IFS='=' read -r name value; do
  case "$name" in
    BIFROST_ADMIN_USERNAME | BIFROST_ADMIN_PASSWORD) export "$name=$value" ;;
  esac
done </etc/bifrost/bifrost.env
cd /
exec sudo --preserve-env=BIFROST_ADMIN_USERNAME,BIFROST_ADMIN_PASSWORD -u parent-recap-signup -H -- \
  /var/lib/parent-recap-signup/ParentRecap/app/.venv/bin/parent-recap signup remove "$1"
