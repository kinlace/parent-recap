#!/usr/bin/env bash
# Sets up the gateway's server: an Oracle Cloud Ubuntu 24.04 VM (see README.md).
# Safe to run again; each step checks before it changes anything.
#
#   ssh <vm> "sudo GATEWAY_HOST=<hostname> bash -s" < ops/gateway/host-setup.sh
#
# Without GATEWAY_HOST, Caddy is installed but no site is configured.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

# ── Packages
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q unattended-upgrades iptables-persistent caddy
systemctl enable --now unattended-upgrades

# ── SSH: keys only
cat >/etc/ssh/sshd_config.d/10-gateway.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sshd -t
systemctl reload ssh

# ── Services nothing here uses
# rpcbind is for NFS. Oracle's image runs it on port 111.
systemctl disable --now rpcbind.socket rpcbind.service 2>/dev/null || true

# ── Firewall: SSH and HTTPS only
# Oracle's image ends INPUT with a REJECT-all rule, so 443 goes in before it.
# Port 80 stays closed: Caddy gets its certificate over 443 (TLS-ALPN-01).
rule=(-p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT)
if ! iptables -C INPUT "${rule[@]}" 2>/dev/null; then
  reject=$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" {print $1; exit}')
  iptables -I INPUT "${reject:-1}" "${rule[@]}"
fi
netfilter-persistent save

# ── Caddy
# Ubuntu's own package (installed above) is older than Caddy's repo, but
# unattended-upgrades keeps it patched. Caddy's Cloudsmith repo answered
# 402 Payment Required in October 2026.
if [ -n "${GATEWAY_HOST:-}" ]; then
  # A placeholder until the gateway itself runs behind Caddy.
  cat >/etc/caddy/Caddyfile <<EOF
$GATEWAY_HOST {
	respond "Parent Recap gateway" 200
}
EOF
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  systemctl reload-or-restart caddy
fi

echo "done"
