#!/usr/bin/env bash
# Installs or upgrades Bifrost behind Caddy on any systemd Linux with Caddy
# already installed (host-setup.sh does that on Ubuntu). Safe to run again.
#
#   scp -r ops/gateway <vm>:/tmp/
#   ssh -t <vm> "sudo GATEWAY_HOST=<hostname> bash /tmp/gateway/gateway-setup.sh"
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

# ── Caddy
install -m 0644 "$here/Caddyfile" /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironment=GATEWAY_HOST=%s\n' "$GATEWAY_HOST" >/etc/systemd/system/caddy.service.d/gateway.conf
systemctl daemon-reload
GATEWAY_HOST=$GATEWAY_HOST caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl restart caddy

# ── Check
for _ in $(seq 1 20); do
  curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 && { echo "bifrost $BIFROST_VERSION is up"; exit 0; }
  sleep 1
done
echo "bifrost did not answer on /health; see: journalctl -u bifrost" >&2
exit 1
