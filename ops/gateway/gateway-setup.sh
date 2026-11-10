#!/usr/bin/env bash
# Installs or upgrades Bifrost, the Brief relay and, given PARENT_RECAP_RELEASE,
# the program every Hosted Household runs and the hosted version's sign-up
# service behind Caddy on any systemd Linux with Caddy and python3 already
# installed (host-setup.sh does that on Ubuntu). Safe to run again.
#
# Once the server is built, `sudo admin deploy vX.Y.Z` runs it on the server
# from the commit it fetched (README "Upgrade, check, switch back"). By hand,
# for the first install:
#
#   scp -r ops/gateway <vm>:/tmp/
#   ssh -t <vm> "sudo GATEWAY_HOST=<hostname> bash /tmp/gateway/gateway-setup.sh"
#
# With the program and the sign-up service, from an unpacked release (or a
# checkout) copied over:
#
#   ssh -t <vm> "sudo GATEWAY_HOST=<hostname> PARENT_RECAP_RELEASE=/tmp/parent-recap bash /tmp/gateway/gateway-setup.sh"
#
# -t is needed: the first run asks for the secrets, which never touch the repo.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
: "${GATEWAY_HOST:?set GATEWAY_HOST to the gateway hostname}"
here=$(cd "$(dirname "$0")" && pwd)

# ── Pinned release
# Bifrost publishes no checksums, so these were computed on first download.
# To upgrade: try the new version locally, then change all three lines.
BIFROST_VERSION=v2.2.6
SHA256_arm64=29a0efb60ab7579708ea1e659134c18eacf0ff33bd4e136c12a519854f59589b
SHA256_amd64=d6aea755654e5c92d1f68f2d1ee27a6e14776e1cb8b888813073e2853407c645

case "$(uname -m)" in
  aarch64) arch=arm64 ;;
  x86_64) arch=amd64 ;;
  *) echo "unsupported CPU $(uname -m)" >&2; exit 1 ;;
esac
want=SHA256_$arch

# ── Binary
dir=/usr/local/lib/bifrost/$BIFROST_VERSION
if [ ! -x "$dir/bifrost-http" ]; then
  mkdir -p "$dir"
  curl -fsSL "https://downloads.getmaxim.ai/bifrost/$BIFROST_VERSION/linux/$arch/bifrost-http" -o "$dir/bifrost-http.part"
  echo "${!want}  $dir/bifrost-http.part" | sha256sum -c -
  chmod 755 "$dir/bifrost-http.part"
  mv "$dir/bifrost-http.part" "$dir/bifrost-http"
fi
ln -sfn "$dir" /usr/local/lib/bifrost/current

# ── User and state
id bifrost >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin bifrost
install -d -o bifrost -g bifrost -m 0700 /var/lib/bifrost
install -o bifrost -g bifrost -m 0600 "$here/config.json" /var/lib/bifrost/config.json

# ── Secrets
install -d -m 0755 /etc/bifrost
if [ ! -f /etc/bifrost/bifrost.env ]; then
  [ -t 0 ] || { echo "first run needs ssh -t to ask for the secrets" >&2; exit 1; }
  read -r -s -p "Anthropic API key: " key; echo
  read -r -p "Bifrost admin username: " admin
  read -r -s -p "Bifrost admin password: " pass; echo
  umask 077
  printf 'ANTHROPIC_API_KEY=%s\nBIFROST_ADMIN_USERNAME=%s\nBIFROST_ADMIN_PASSWORD=%s\n' \
    "$key" "$admin" "$pass" >/etc/bifrost/bifrost.env
  unset key admin pass
fi
chmod 0600 /etc/bifrost/bifrost.env

# ── Service
install -m 0644 "$here/bifrost.service" /etc/systemd/system/bifrost.service
systemctl daemon-reload
systemctl enable bifrost
systemctl restart bifrost

# ── Brief relay (POST /v1/brief, README "Sending the Brief")
# Starts without the SMTP credentials only to exit and say so: set them with
# set-smtp-credentials.sh, which restarts it.
install -d -m 0755 /usr/local/lib/brief-relay /usr/local/share/brief-relay
install -m 0644 "$here/brief_relay.py" /usr/local/lib/brief-relay/brief_relay.py
# `sudo admin <task>`, the maintainers' one command (README "Runbook"), uses
# brief_relay.py's Gateway Key functions; the scripts it replaced go.
install -o root -g root -m 0755 "$here/admin" /usr/local/sbin/admin
rm -f /usr/local/lib/brief-relay/keys.sh /usr/local/lib/brief-relay/new-key.sh
install -m 0644 "$here/brief-stop.html" /usr/local/share/brief-relay/stop.html
install -m 0644 "$here/brief-relay.service" /etc/systemd/system/brief-relay.service
mkdir -p /etc/systemd/system/brief-relay.service.d
printf '[Service]\nEnvironment=GATEWAY_HOST=%s\n' "$GATEWAY_HOST" >/etc/systemd/system/brief-relay.service.d/gateway.conf
systemctl daemon-reload
systemctl enable brief-relay
systemctl restart brief-relay

# ── The nightly check (README "The nightly check", #745)
# `admin check --mail` at 23:00 Finnish time, after the evening runs: it mails
# the maintainers, from the Brief's address, only when something is wrong. Their
# address is the server's, never the repo's: MAINTAINERS_ADDRESS sets or changes
# it; the first run asks for it.
maintainers=/etc/brief-relay/maintainers.env
install -d -m 0700 /etc/brief-relay
if [ -z "${MAINTAINERS_ADDRESS:-}" ] && [ ! -f "$maintainers" ] && [ -t 0 ]; then
  read -r -p "Maintainers' address for the nightly check's mail (commas between several): " MAINTAINERS_ADDRESS
fi
MAINTAINERS_ADDRESS=${MAINTAINERS_ADDRESS:-}
MAINTAINERS_ADDRESS=${MAINTAINERS_ADDRESS// /}
if [ -n "$MAINTAINERS_ADDRESS" ]; then
  address='[^@[:space:],]+@[^@[:space:],]+\.[^@[:space:],]+'
  [[ "$MAINTAINERS_ADDRESS" =~ ^$address(,$address)*$ ]] ||
    { echo "MAINTAINERS_ADDRESS must be name@domain addresses with commas between them" >&2; exit 1; }
  (umask 077 && printf 'MAINTAINERS_ADDRESS=%s\n' "$MAINTAINERS_ADDRESS" >"$maintainers")
fi
grep -qs '^MAINTAINERS_ADDRESS=.' "$maintainers" ||
  echo "the nightly check mails nobody: run this again with MAINTAINERS_ADDRESS=<address>"
install -m 0644 "$here/parent-recap-check.service" /etc/systemd/system/parent-recap-check.service
install -m 0644 "$here/parent-recap-check.timer" /etc/systemd/system/parent-recap-check.timer
systemctl daemon-reload
systemctl enable --now parent-recap-check.timer

# ── The morning's automatic deploy (README "Upgrade, check, switch back", #872)
# `admin autodeploy run` at 10:00 Finnish time deploys the release `stable`
# points at when the server doesn't run it, and mails the maintainers at the
# address above how it went. `sudo admin autodeploy pause` stops it.
install -m 0644 "$here/parent-recap-autodeploy.service" /etc/systemd/system/parent-recap-autodeploy.service
install -m 0644 "$here/parent-recap-autodeploy.timer" /etc/systemd/system/parent-recap-autodeploy.timer
systemctl daemon-reload
systemctl enable --now parent-recap-autodeploy.timer

# ── The Finnish egress for Wilma (README "The Finnish egress for Wilma", ADR 0046)
# The HTTP CONNECT proxy in Finland the wilma CLI's requests go through, which
# every Household's user and the sign-up service read: so readable by all, and
# not secret, but the server's, never the repo's, as it names a maintainer's
# machine. Made empty when missing (no egress: the CLI goes direct);
# WILMA_EGRESS_PROXY=http://HOST:PORT sets it, and WILMA_EGRESS_PROXY=none
# empties it again.
egress=/etc/parent-recap/wilma-egress.env
install -d -m 0755 /etc/parent-recap
if [ "${WILMA_EGRESS_PROXY:-}" = none ]; then
  printf 'WILMA_EGRESS_PROXY=\n' >"$egress"
elif [ -n "${WILMA_EGRESS_PROXY:-}" ]; then
  [[ "$WILMA_EGRESS_PROXY" =~ ^http://[A-Za-z0-9.-]+:[0-9]{1,5}/?$ ]] ||
    { echo "WILMA_EGRESS_PROXY must be http://HOST:PORT (or none)" >&2; exit 1; }
  printf 'WILMA_EGRESS_PROXY=%s\n' "$WILMA_EGRESS_PROXY" >"$egress"
elif [ ! -f "$egress" ]; then
  printf 'WILMA_EGRESS_PROXY=\n' >"$egress"
fi
chown root:root "$egress"
chmod 0644 "$egress"

# ── The shared program and the sign-up service (/join/, README "Hosted
# Households", ADR 0031, ADR 0035)
# Installed from PARENT_RECAP_RELEASE, the folder of an unpacked release of the
# program (or a checkout), once for the whole server: its install.sh puts it in
# /usr/local/lib/parent-recap/<version> with its own Python and Node, and the
# wilma CLI goes beside them. `current` then points at that release, and
# bin/parent-recap runs the program through it: the sign-up service now, and
# every Hosted Household from its next run. The release `current` pointed at
# before stays, to switch back to. `admin deploy` sets PARENT_RECAP_DEPLOY to
# the commit's folder name, used in place of the version. Without
# PARENT_RECAP_RELEASE both are left as
# they are, and with no service yet the Caddyfile's /join/ route answers 502.
shared=/usr/local/lib/parent-recap
signup_lib=/usr/local/lib/parent-recap-signup
signup_home=/var/lib/parent-recap-signup
signup=no
# Their tasks are `sudo admin invite`, `households` and `remove` now, on every
# run, with or without a release.
rm -f "$signup_lib/invite.sh" "$signup_lib/status.sh" "$signup_lib/remove.sh"
if [ -n "${PARENT_RECAP_RELEASE:-}" ]; then
  [ -f "$PARENT_RECAP_RELEASE/install.sh" ] || { echo "PARENT_RECAP_RELEASE has no install.sh" >&2; exit 1; }
  version=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$PARENT_RECAP_RELEASE/.claude-plugin/plugin.json" | head -1)
  [[ "$version" =~ ^[0-9][0-9A-Za-z.+-]*$ ]] ||
    { echo "PARENT_RECAP_RELEASE's .claude-plugin/plugin.json has no version" >&2; exit 1; }
  signup=yes
  # `admin deploy` (ADR 0036) names each deploy by its commit: an unreleased
  # commit carries the version of the release before it, which it mustn't
  # overwrite. By hand, a release goes under its version.
  deploy=${PARENT_RECAP_DEPLOY:-$version}
  [[ "$deploy" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]*$ ]] ||
    { echo "PARENT_RECAP_DEPLOY isn't a folder name" >&2; exit 1; }
  release=$shared/$deploy
  install -d -m 0755 "$shared" "$shared/bin"
  # As root, with a home of its own for this run only, so what install.sh notes
  # for a family's uninstall, and its PATH line, go with it. Run again for the
  # same version, it updates that release in place.
  # install.sh keeps a family's folder to itself; this one holds only the
  # program, which every Household's user runs. A Household's data stays in its
  # own home. Made readable again however this ends, so a failed install of the
  # version in use doesn't lock every Household out of it.
  # `admin rollback` sets PARENT_RECAP_SWITCH=yes: the deploy it switches back
  # to is installed already, so only the pointers and the gateway's files
  # change. One whose program is gone is installed again.
  if [ "${PARENT_RECAP_SWITCH:-}" = yes ] && [ -x "$release/app/.venv/bin/python" ]; then
    echo "switching to $deploy, installed before: its install is skipped"
  else
    setup_home=$(mktemp -d)
    shared_readable() {
      rm -rf "$setup_home"
      if [ -d "$release" ]; then chown -R root:root "$release" && chmod -R u=rwX,go=rX "$release"; fi
    }
    trap shared_readable EXIT
    (cd / && HOME=$setup_home PARENT_RECAP_HOME=$release bash "$PARENT_RECAP_RELEASE/install.sh")
    (cd / && HOME=$setup_home "$release/app/.venv/bin/python" -m family_brief.setup_wilma install)
    # Compiled now, as no Household's user may write beside the program's code.
    "$release/app/.venv/bin/python" -m compileall -q "$release/app/src" >/dev/null
    shared_readable
    trap - EXIT
  fi
  install -o root -g root -m 0755 "$here/parent-recap" "$shared/bin/parent-recap"
  previous=$(readlink "$shared/current" || true)
  ln -sfn "$deploy" "$shared/current.incoming"
  mv -T "$shared/current.incoming" "$shared/current"
  if [ -n "$previous" ] && [ "$previous" != "$deploy" ]; then
    echo "the shared program is now $deploy ($version), was $previous; to switch back: sudo admin rollback"
  fi

  getent group parent-recap-households >/dev/null || groupadd --system parent-recap-households
  id parent-recap-signup >/dev/null 2>&1 ||
    useradd --system --create-home --home-dir "$signup_home" --shell /usr/sbin/nologin parent-recap-signup
  chmod 700 "$signup_home"
  # Before ADR 0035: the copy of the release each Household's install.sh ran
  # from, and the program that installed under the service's own user. Households
  # made then keep theirs until moved (README "Hosted Households").
  rm -rf "$signup_lib/release" "$signup_home/ParentRecap"
  install -d -m 0755 "$signup_lib"
  install -o root -g root -m 0755 "$here/household-user" "$signup_lib/household-user"
  install -o root -g root -m 0440 "$here/signup.sudoers" /etc/sudoers.d/parent-recap-signup.incoming
  visudo -cf /etc/sudoers.d/parent-recap-signup.incoming
  mv /etc/sudoers.d/parent-recap-signup.incoming /etc/sudoers.d/parent-recap-signup
  install -d -m 0755 /etc/parent-recap-signup
  (umask 077 && printf 'GATEWAY_HOST=%s\n' "$GATEWAY_HOST" >/etc/parent-recap-signup/signup.env)
  install -m 0644 "$here/parent-recap-signup.service" /etc/systemd/system/parent-recap-signup.service
  systemctl daemon-reload
  systemctl enable parent-recap-signup
  systemctl restart parent-recap-signup
else
  echo "sign-up service skipped: set PARENT_RECAP_RELEASE to install or upgrade it"
fi

# ── Caddy
install -m 0644 "$here/Caddyfile" /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironment=GATEWAY_HOST=%s\n' "$GATEWAY_HOST" >/etc/systemd/system/caddy.service.d/gateway.conf
systemctl daemon-reload
GATEWAY_HOST=$GATEWAY_HOST caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl restart caddy

# ── Check
up() {
  for _ in $(seq 1 20); do
    curl -fsS "http://127.0.0.1:$1/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
up 8080 || { echo "bifrost did not answer on /health; see: journalctl -u bifrost" >&2; exit 1; }
echo "bifrost $BIFROST_VERSION is up"
if ! grep -qs '^BRIEF_SMTP_PASSWORD=.' /etc/brief-relay/brief-relay.env; then
  echo "brief-relay waits for the SMTP credentials: run set-smtp-credentials.sh"
  exit 0
fi
up 8081 || { echo "brief-relay did not answer on /health; see: journalctl -u brief-relay" >&2; exit 1; }
echo "brief-relay is up"
if [ "$signup" = yes ]; then
  up 8082 || { echo "the sign-up service did not answer on /health; see: journalctl -u parent-recap-signup" >&2; exit 1; }
  echo "the sign-up service is up on Parent Recap $version; mint an Invite Link with: sudo admin invite --help"
  echo "every Hosted Household runs $version from its next run: run your own Household's now, before 21:00 (README \"Hosted Households\")"
fi
