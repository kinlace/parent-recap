#!/usr/bin/env bash
# Sets up the gateway's server: an Oracle Cloud Ubuntu 24.04 VM (see README.md).
# Safe to run again; each step checks before it changes anything.
#
#   ssh ubuntu@<server> "sudo bash -s" < ops/gateway/host-setup.sh
#
# <server> is the VM's Tailscale name: logins come over Tailscale SSH only,
# which the VM joins at its first boot (README "Building the server", step 4).
# Ubuntu/Debian only (apt, iptables-persistent). MINI_PUBKEY='<ssh public key>'
# also lets the assistant's login in over plain SSH, for a server without
# Tailscale.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

# ── Packages
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q unattended-upgrades iptables-persistent caddy git
systemctl enable --now unattended-upgrades

# ── SSH: keys only
# Tailscale SSH doesn't go through sshd; this is for a server reached without it.
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

# ── Firewall: HTTPS only from the internet
# Oracle's image ends INPUT with a REJECT-all rule, so 443 goes in before it.
# Port 80 stays closed: Caddy gets its certificate over 443 (TLS-ALPN-01).
rule=(-p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT)
if ! iptables -C INPUT "${rule[@]}" 2>/dev/null; then
  reject=$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" {print $1; exit}')
  iptables -I INPUT "${reject:-1}" "${rule[@]}"
fi
# The image also lets in SSH on port 22. Logins come over Tailscale, whose own
# chain (ts-input) lets the tailnet in, so that rule goes once Tailscale is up;
# without Tailscale it stays, or this script would lock its runner out.
ssh_rule=(-p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT)
if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
  while iptables -C INPUT "${ssh_rule[@]}" 2>/dev/null; do
    iptables -D INPUT "${ssh_rule[@]}"
  done
else
  echo "Tailscale isn't up: port 22 stays open in the firewall (README \"Logging in\")" >&2
fi
netfilter-persistent save

# ── Read-only login for the AI assistant
# It can read logs and service status but has no sudo and cannot read the
# gateway's state or secrets: the provider key and family messages pass through
# this server, so changes run from the committed scripts by a maintainer.
# It has no password: the tailnet's policy lets only the assistant's machine in
# as `mini` (README "Logging in").
id mini >/dev/null 2>&1 || useradd --create-home --shell /bin/bash mini
usermod -aG systemd-journal,adm mini
if [ -n "${MINI_PUBKEY:-}" ]; then
  install -d -o mini -g mini -m 0700 /home/mini/.ssh
  printf '%s\n' "$MINI_PUBKEY" >/home/mini/.ssh/authorized_keys
  chown mini:mini /home/mini/.ssh/authorized_keys
  chmod 0600 /home/mini/.ssh/authorized_keys
fi

# Caddy's site and the gateway itself are set up by gateway-setup.sh.
echo "done"
