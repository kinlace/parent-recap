# The maintainers' gateway

The server that invited Households' Gateway Keys go through (ADR 0014). Everything needed to rebuild it is here, and none of it is secret (ADR 0015). Where the running server is and how to reach it are in the maintainers' Bitwarden, not here.

- `host-setup.sh` sets up the VM: system updates, SSH with keys only, a firewall that lets in only SSH and HTTPS, and Caddy for HTTPS.
- `oci-quota.json` is the Oracle Cloud quota policy that lets the account create only Always Free resources.

This is how we run it. Take as much as fits your own setup. `host-setup.sh` works on any Ubuntu 24.04 server. On a cloud other than Oracle's, use that cloud's own firewall too: the script only opens port 443 in front of the reject-all rule Oracle's image ships, and adds no rules of its own on an image without one. Steps 1–4 below are Oracle's.

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
   ssh ubuntu@<ip> "sudo GATEWAY_HOST=<hostname> bash -s" < ops/gateway/host-setup.sh
   ```

   Caddy gets the hostname's certificate over port 443 (TLS-ALPN-01), so port 80 stays closed. Check with `curl https://<hostname>`.

7. **Record it.** Put the provider, region, IP, hostname, SSH user and which SSH key in the maintainers' Bitwarden.

`host-setup.sh` is safe to run again: after changing it, or to bring an existing VM up to date.
