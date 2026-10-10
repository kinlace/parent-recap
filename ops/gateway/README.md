# The maintainers' gateway

The server that invited Households' Gateway Keys go through (ADR 0014). Everything needed to rebuild it is here, and none of it is secret (ADR 0015). Where the running server is and how to reach it are in the maintainers' Bitwarden, not here.

- `host-setup.sh` sets up the VM (Ubuntu/Debian): system updates, SSH with keys only, a firewall that lets in only SSH and HTTPS, Caddy, and a login for the AI assistant that can read logs but has no sudo.
- `gateway-setup.sh` installs and upgrades Bifrost (ADR 0016) behind Caddy. It works on any systemd Linux with Caddy installed.
- `config.json`, `bifrost.service`, `Caddyfile` and `bifrost.env.example` are what it installs. The env file holds only variable names.
- `oci-quota.json` is the Oracle Cloud quota policy that lets the account create only Always Free resources.

This is how we run it. Take as much as fits your own setup. `host-setup.sh` works on any Ubuntu 24.04 server; on another Linux, do its steps yourself (updates, SSH keys only, firewall, install Caddy) and go straight to `gateway-setup.sh`. On a cloud other than Oracle's, use that cloud's own firewall too: the script only opens port 443 in front of the reject-all rule Oracle's image ships, and adds no rules of its own on an image without one. Steps 1–4 below are Oracle's.

## Building the server

Our VM is an Oracle Cloud Always Free Ampere A1 in Stockholm (`eu-stockholm-1`). Steps 1–4 are in Oracle's console and can't be scripted.

1. **Pay-As-You-Go.** Upgrade the Oracle account to Pay-As-You-Go. Always Free resources stay free, and Oracle doesn't reclaim them as idle.
2. **Spending guards.** In Billing, add a budget of $1 a month that emails the maintainers on any actual spend. Then add the quota policy, which makes anything not Always Free impossible to create:

   ```sh
   oci limits quota create --compartment-id <tenancy OCID> --name free-tier-only \
     --description "Only Always Free compute and storage" \
     --statements file://ops/gateway/oci-quota.json
   ```

3. **Network.** Create a VCN with a public subnet. Its security list lets in only TCP 22, TCP 443 and ICMP types 3 and 4.
4. **VM.** Shape `VM.Standard.A1.Flex`, 1 OCPU and 6 GB, image Ubuntu 24.04 (aarch64), public IP, your SSH public key. Stockholm often has no A1 capacity ("Out of host capacity"). Keep retrying, a few times an hour, until a launch succeeds; it can take a day.
5. **DNS.** Point an A record for the gateway's hostname at the VM's public IP.
6. **Host setup.** From your machine:

   ```sh
   ssh ubuntu@<ip> "sudo MINI_PUBKEY='<assistant host public key>' bash -s" < ops/gateway/host-setup.sh
   ```

7. **Gateway.** The first run asks for the Anthropic API key and the admin login, and keeps them in `/etc/bifrost/bifrost.env` on the server only:

   ```sh
   scp -r ops/gateway ubuntu@<ip>:/tmp/
   ssh -t ubuntu@<ip> "sudo GATEWAY_HOST=<hostname> bash /tmp/gateway/gateway-setup.sh"
   ```

   Caddy gets the hostname's certificate over port 443 (TLS-ALPN-01), so port 80 stays closed. Check with `curl -i https://<hostname>/` (404) and a call with a Gateway Key (below).

8. **Spend limit.** In the Anthropic Console, set the account's monthly spend limit to $20.
9. **Record it.** Keep the provider, region, IP, hostname and SSH logins in the maintainers' private notes, not here.

Both scripts are safe to run again.

## What the gateway does

- Only `POST /v1/messages` is reachable from the internet. Caddy sends it to Bifrost's Anthropic endpoint and answers 404 to everything else, so the admin API and UI are only reachable through an SSH tunnel.
- A request without a Gateway Key is refused. Clients send `model: "parent-recap"`, which `config.json` maps to the real model, so changing model is a config change, not a release.
- Request and reply text is not stored (`disable_content_logging`, and the per-request override is off). Token counts, cost and status are. Caddy keeps no access log.
- A Gateway Key is a Bifrost virtual key with a $0.10 budget that resets daily. A key past its budget gets HTTP 402 `budget_exceeded`.

## Runbook

Open the admin API through a tunnel, with the admin login from the maintainers' password manager. Nothing below is run on the public address.

```sh
ssh -L 8080:localhost:8080 ubuntu@<ip>      # leave open
export ADMIN=<admin user>:<admin password>
```

**Create a Gateway Key and give it to a Household.** The name is a neutral label; who holds which key is kept only in the gateway's database, never in the repo. The reply's `virtual_key.value` (`sk-bf-…`) is shown once to you: send it to the Household over a private channel and don't save it anywhere.

```sh
curl -s -u "$ADMIN" -X POST localhost:8080/api/governance/virtual-keys \
  -H 'content-type: application/json' -d '{
    "name": "household-<label>",
    "is_active": true,
    "provider_configs": [{"provider": "anthropic", "weight": 1,
      "allowed_models": ["parent-recap"], "key_ids": ["*"]}],
    "budgets": [{"max_limit": 0.10, "reset_duration": "1d"}]
  }'
```

**Revoke.** Find the key's `id` in the list, then turn it off (or `DELETE` it):

```sh
curl -s -u "$ADMIN" localhost:8080/api/governance/virtual-keys
curl -s -u "$ADMIN" -X PATCH localhost:8080/api/governance/virtual-keys/<id> \
  -H 'content-type: application/json' -d '{"is_active": false}'
```

**Check spend.** `budgets[].current_usage` in the same list is what each key has spent today. The month's total is in the Anthropic Console, which also enforces the $20 limit.

**Upgrade.** Try the new version on your own machine first (`npx -y @maximhq/bifrost --transport-version vX.Y.Z`). Then, in `gateway-setup.sh`, change `BIFROST_VERSION` and both `SHA256_*` lines (hash the binaries at `https://downloads.getmaxim.ai/bifrost/<version>/linux/<arm64|amd64>/bifrost-http`), and run step 7 again. The old version stays in `/usr/local/lib/bifrost/`; repoint the `current` symlink and restart to roll back.

**Change the model.** Edit the `aliases` entry in `config.json` and run step 7 again.

**Check it is up.** The assistant's login can read logs and status: `journalctl -u bifrost -u caddy`, `systemctl status bifrost`.
