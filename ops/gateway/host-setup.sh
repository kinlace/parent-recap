#!/usr/bin/env bash
# Sets up the gateway's server: an Oracle Cloud Ubuntu 24.04 VM (see README.md).
# Safe to run again; each step checks before it changes anything.
#
#   ssh <vm> "sudo MINI_PUBKEY='<ssh public key>' bash -s" < ops/gateway/host-setup.sh
#
# Ubuntu/Debian only (apt, iptables-persistent). Without MINI_PUBKEY no
# assistant login is created.
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

# ── Read-only login for the AI assistant
# It can read logs and service status but has no sudo and cannot read the
# gateway's state or secrets: the provider key and family messages pass through
# this server, so changes run from the committed scripts by a maintainer.
# Set MINI_PUBKEY to the assistant host's public key.
if [ -n "${MINI_PUBKEY:-}" ]; then
  id mini >/dev/null 2>&1 || useradd --create-home --shell /bin/bash mini
  usermod -aG systemd-journal,adm mini
  install -d -o mini -g mini -m 0700 /home/mini/.ssh
  printf '%s\n' "$MINI_PUBKEY" >/home/mini/.ssh/authorized_keys
  chown mini:mini /home/mini/.ssh/authorized_keys
  chmod 0600 /home/mini/.ssh/authorized_keys
fi

# Caddy's site and the gateway itself are set up by gateway-setup.sh.
echo "done"
