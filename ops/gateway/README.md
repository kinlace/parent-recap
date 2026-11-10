# The maintainers' gateway

The server that invited Households' Gateway Keys go through (ADR 0014), and where Hosted Households run (ADR 0029). Everything needed to rebuild it is here, and none of it is secret (ADR 0015). Where the running server is and how to reach it are in the maintainers' Bitwarden, not here.

- `host-setup.sh` sets up the VM (Ubuntu/Debian): system updates, SSH with keys only, a firewall that lets in only HTTPS from the internet (logins come over Tailscale SSH, see "Logging in"), Caddy, and a login for the AI assistant that can read logs but has no sudo.
- `gateway-setup.sh` installs and upgrades Bifrost (ADR 0016), the Brief relay and, given a release of the program, the program every Hosted Household runs (ADR 0035) and the hosted version's sign-up service (ADR 0031) behind Caddy. It works on any systemd Linux with Caddy and `python3` installed.
- `config.json`, `bifrost.service`, `brief_relay.py`, `brief-relay.service`, `brief-stop.html`, `Caddyfile`, `bifrost.env.example`, `admin` (the maintainers' one command, see "Runbook") and `parent-recap-check.service` and `.timer` with `maintainers.env.example` (see "The nightly check"), `parent-recap-autodeploy.service` and `.timer` (see "Upgrade, check, switch back") are what it installs, and for the Hosted Households `parent-recap` (the shared program's command), `parent-recap-signup.service`, `signup.env.example`, `signup.sudoers` and `household-user`. The env examples hold only variable names.
- `oci-quota.json` is the Oracle Cloud quota policy that lets the account create only Always Free resources.
- `set-smtp-credentials.sh` puts the sending domain's SMTP credentials into their own env file, whose names are in `brief-relay.env.example` (see "The sending domain").

This is how we run it. Take as much as fits your own setup. `host-setup.sh` works on any Ubuntu 24.04 server; on another Linux, do its steps yourself (updates, SSH keys only, firewall, install Caddy) and go straight to `gateway-setup.sh`. On a cloud other than Oracle's, use that cloud's own firewall too: the script only opens port 443 in front of the reject-all rule Oracle's image ships, and takes out the image's port 22 rule once Tailscale is up; on an image without those rules it adds none of its own. Steps 1–4 and 9 below are Oracle's.

## Building the server

Our VM is an Oracle Cloud Always Free Ampere A1 in Stockholm (`eu-stockholm-1`). Steps 1–4 and 9 are in Oracle's console.

1. **Pay-As-You-Go.** Upgrade the Oracle account to Pay-As-You-Go. Always Free resources stay free, and Oracle doesn't reclaim them as idle.
2. **Spending guards.** In Billing, add a budget of $1 a month that emails the maintainers on any actual spend. Then add the quota policy, which makes anything not Always Free impossible to create:

   ```sh
   oci limits quota create --compartment-id <tenancy OCID> --name free-tier-only \
     --description "Only Always Free compute and storage" \
     --statements file://ops/gateway/oci-quota.json
   ```

3. **Network.** Create a VCN with a public subnet. Give the subnet one security list of its own, whose only ingress rules are TCP 443 and ICMP type 3 code 4, both from `0.0.0.0/0`: no SSH, no port 80. Don't attach the VCN's default security list to the subnet, and give the VM's VNIC no network security group.
4. **VM.** Shape `VM.Standard.A1.Flex`, 1 OCPU and 6 GB, image Ubuntu 24.04 (aarch64), public IP, your SSH public key (only for a rescue: port 22 stays closed). Under "Advanced options" → "Management", give the cloud-init script below as the "Initialization script" ([Oracle: Creating an Instance](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm)), so the VM joins the maintainers' tailnet with Tailscale SSH on at its first boot: nothing else can log in to it (see "Logging in"). Stockholm often has no A1 capacity ("Out of host capacity"). Keep retrying, a few times an hour, until a launch succeeds; it can take a day.

   ```sh
   #!/bin/bash
   curl -fsSL https://tailscale.com/install.sh | sh
   tailscale up --ssh --auth-key='<auth key>'
   ```

   Make the auth key on the Keys page of Tailscale's admin console: not reusable, pre-approved (needed only if the tailnet has device approval on), tagged `tag:gateway`, expiring in 1 day, the shortest it allows ([Tailscale: Auth keys](https://tailscale.com/kb/1085/auth-keys)). The script stays in the VM's metadata as `user_data`, which anything on the VM can read from Oracle's metadata service ([Oracle: Getting Instance Metadata](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/gettingmetadata.htm)), so the key must be spent and expired by then. A tagged device's key expiry is disabled by default, so the VM stays on the tailnet ([Tailscale: Tags](https://tailscale.com/kb/1068/tags)).

5. **DNS.** Point an A record for the gateway's hostname at the VM's public IP. The IP is ephemeral: a VM made again, such as from a backup, gets a new one (see "Backups").
6. **Host setup.** From your machine, over Tailscale SSH. `<server>` here and below is the VM's name on the tailnet, never its public IP:

   ```sh
   ssh ubuntu@<server> "sudo bash -s" < ops/gateway/host-setup.sh
   ```

   It also takes out the image's own firewall rule for port 22, once Tailscale is up, and makes the assistant's read-only login `mini`.

7. **Gateway.** The first run asks for the Anthropic API key and the admin login, and keeps them in `/etc/bifrost/bifrost.env` on the server only, and for the maintainers' address the nightly check mails (see "The nightly check"):

   ```sh
   scp -r ops/gateway ubuntu@<server>:/tmp/
   ssh -t ubuntu@<server> "sudo GATEWAY_HOST=<hostname> bash /tmp/gateway/gateway-setup.sh"
   ```

   Caddy gets the hostname's certificate over port 443 (TLS-ALPN-01), so port 80 stays closed. Check with `curl -i https://<hostname>/` (404) and a call with a Gateway Key (below).

8. **Spend limit.** In the Anthropic Console, create a workspace for the gateway, put the gateway's API key in it and set that workspace's monthly spend limit to $10 (Settings > Workspaces > Spend limits). The default workspace can't be limited.
9. **Backups.** Assign the weekly backup policy to the VM's boot volume (see "Backups").
10. **Record it.** Keep the provider, region, IP, hostname, tailnet name and SSH logins in the maintainers' private notes, not here.
11. **Hosted Households** (ADR 0029), once the gateway answers. All on the server, as in "Hosted Households" below:
    - **The program and the service.** Run step 7 again with `PARENT_RECAP_RELEASE` (see "Install it"). Check that `systemctl status parent-recap-signup` is active and an unknown `https://<hostname>/join/x` gets 404. From then on the server deploys each new release by itself the next morning, which upgrades every Household and checks one; any other deploy is `sudo admin deploy` on the server (see "Upgrade, check, switch back").
    - **Invite.** `sudo admin invite` prints an Invite Link (see "Invite a Household"). Sign up your own Household with it first: it is the first to sign in to Wilma from the server.
    - **Status.** `sudo admin households` lists every Household's last run and checks (see "See every Household's status").
    - **Remove.** `sudo admin remove ph-…` removes a Household with its data (see "Remove a Household").

    A server built again from these steps has no Households: their homes, the Invite Links and `households.json` are on the old server and in its backups only. To keep them, make the new VM from a backup instead (see "Backups"). Otherwise invite each family again; its Wilma login and MyClub links come with the new sign-up, and deleting the old VM deletes the old Households with it.

Both scripts are safe to run again.

## Logging in

Nothing logs in from the internet: port 22 is closed in the security list and in the host's firewall, and only 443 is open. Every login comes over Tailscale SSH: `tailscaled` intercepts the tailnet's traffic to port 22 on the VM's Tailscale address and lets it in by the tailnet's policy, without `authorized_keys` ([Tailscale: Tailscale SSH](https://tailscale.com/kb/1193/tailscale-ssh)), and the firewall chain it adds (`ts-input`) lets the tailnet in. Tailscale isn't set up by `host-setup.sh`: the VM joins at its first boot (step 4). On a VM made without that script, run its two lines over plain SSH, check that `ssh ubuntu@<server>` works over the tailnet, run `host-setup.sh` again and then close port 22 in the security list.

The tailnet's policy (Tailscale's admin console → Access controls) says who gets which login. One that gives what "Who holds access" below says, with tag names of your choosing (this is an example, not a copy of the live policy):

```json
"tagOwners": { "tag:gateway": ["<maintainer>"], "tag:assistant": ["<maintainer>"] },
"ssh": [
  { "action": "check", "src": ["<maintainer>"], "dst": ["tag:gateway"], "users": ["ubuntu"] },
  { "action": "accept", "src": ["tag:assistant"], "dst": ["tag:gateway"], "users": ["mini"] }
]
```

A tagged machine, such as the assistant's, has no user identity ([Tailscale: Tags](https://tailscale.com/kb/1068/tags)), so the maintainer's rule doesn't cover it and it gets only `mini`. `check` makes the maintainer sign in again with their identity provider, after which logins work for the check period, 12 hours unless `checkPeriod` sets another ([Tailscale: Tailscale SSH](https://tailscale.com/kb/1193/tailscale-ssh)). The policy must also let those sources reach the gateway on TCP 22 in its `acls` or `grants`, beside the `ssh` rules (same page).

## Who holds access

One maintainer runs the server.

- **Oracle's console**, with MFA on, is that maintainer's alone. Its recovery codes are in the maintainers' password manager. The console can read the VM's disk and its backups (ADR 0037), so who holds it can read everything on the server.
- **The admin login** (`ubuntu`, with sudo) is that maintainer's too, from their own devices only (the `check` rule in "Logging in"). It runs `sudo admin …` (see "Runbook") and the setup scripts.
- **The AI assistant's login** (`mini`) reads only: logs and service status (it is in `systemd-journal` and `adm`), no sudo, and nothing of the gateway's state or secrets. It comes from the assistant's tagged machine, which isn't the maintainer's device and so can't use the admin login.
- **The tailnet's admin console** decides who gets which login, so it stays with the same maintainer.

## Backups

The VM's boot volume is backed up weekly by Oracle (ADR 0037), for losing the VM. A bad deploy is switched back with the deploy's own snapshot (see "Upgrade, check, switch back"), not these.

**The policy.** `gateway-weekly`, assigned to the boot volume: an incremental backup every Sunday at 03:00 UTC, kept 28 days, so at most four exist at a time, within the tenancy's quota of 5 (`oci-quota.json`). No retention lock or delete protection, and no copy to another region (Stockholm has no paired region). They are Always Free. The backups hold everything the disk does, the Wilma sign-ins, Gateway Keys, the provider key and the SMTP credentials among it, encrypted with Oracle's keys.

Set it up in the console, or from the CLI (check the flags with `--help` first; these haven't been run as written):

```sh
P=$(oci bv volume-backup-policy create --compartment-id <tenancy OCID> \
  --display-name gateway-weekly \
  --schedules '[{"backupType":"INCREMENTAL","period":"ONE_WEEK","dayOfWeek":"SUNDAY","hourOfDay":3,"timeZone":"UTC","retentionSeconds":2419200}]' \
  --query data.id --raw-output)
oci bv volume-backup-policy-assignment create --asset-id <boot volume OCID> --policy-id "$P"
```

**Restore.** When the VM is lost:

1. In the console, Boot Volume Backups → the backup → Restore boot volume, in the same availability domain, with no backup policy.
2. Create an instance from that boot volume, as in step 4 but without the cloud-init script: the copy holds the old VM's Tailscale identity, so it comes up on the tailnet as the old VM did (not yet tried: #666).
3. Point the gateway's A record at the new VM's public IP (step 5): it isn't the old one.
4. Assign `gateway-weekly` to the new boot volume, and delete the old VM if it is still there.

Never boot a copy while the old VM still runs: it would run the Households' evening runs too, and take over the gateway's Tailscale identity. A restore loses what changed since the backup: Households that signed up since are invited again, Gateway Keys made since are made again, and Briefs archived since are gone.

Tried on 2026-11-05: a backup was restored to a new boot volume, which reached Available, and then deleted unbooted. Booting a restored copy, in a subnet with no way out, is left for before a public launch (#666).

## What the gateway does

- Only `POST /v1/messages`, `POST /v1/brief` (see "Sending the Brief"), `POST /v1/brief/key`, `POST /v1/brief/recipients` and the confirmation page `/brief/confirm` (see "Send-only keys on request"), the Brief's stop page `/brief/stop` and the sign-up page `/join/<code>` (see "Hosted Households") are reachable from the internet. Caddy sends `/v1/messages` to Bifrost's Anthropic endpoint, the `/v1/brief` paths and `/brief/confirm` to the Brief relay and `/join/` to the sign-up service, serves the stop page as a static file, and answers 404 to everything else, so the admin API and UI are only reachable through an SSH tunnel.
- A request without a Gateway Key is refused. Clients send `model: "parent-recap"`, which `config.json` maps to the real model, so changing model is a config change, not a release.
- Request and reply text is not stored (`disable_content_logging`, and the per-request override is off). Token counts, cost and status are. Caddy keeps no access log.
- A Gateway Key is a Bifrost virtual key with a $0.10 budget that resets daily. A key past its budget gets HTTP 402 `budget_exceeded`.

## The sending domain

Briefs sent from Parent Recap's own address (ADR 0028) leave as `brief@parentrecap.com` through OCI Email Delivery in the same tenancy and region as the VM (`eu-stockholm-1`), so there is no second cloud account or card. Nobody reads that address: `parentrecap.com`'s mail is not forwarded anywhere, and the Brief says replies go unread. Set up 2026-10-28 (#524) with the `oci` CLI; the same commands rebuild it.

**In OCI (Email Delivery, tenancy compartment).** An email domain `parentrecap.com` with a DKIM key (selector `prk2026`) and an email return path `rp.parentrecap.com`, so SPF and DKIM both align with the From address under DMARC; an approved sender `brief@parentrecap.com`. The sender identity is the IAM user `brief-sender` in group `email-senders`, whose only policy is `Allow group email-senders to use email-family in tenancy`: it can send mail and nothing else, and it has no console password. Its SMTP credentials are the only secret.

**At Porkbun (DNS).** Four records, added through Porkbun's API with the maintainers' key:

| Record | Name                 | Value                                                                        |
| ------ | -------------------- | ---------------------------------------------------------------------------- |
| CNAME  | `prk2026._domainkey` | `prk2026.parentrecap.com.dkim.arn1.oracleemaildelivery.com`                  |
| CNAME  | `rp`                 | `arn1.rp.oracleemaildelivery.com`                                            |
| TXT    | `@` (existing SPF)   | `v=spf1 include:_spf.porkbun.com include:eu.rp.oracleemaildelivery.com ~all` |
| TXT    | `_dmarc`             | `v=DMARC1; p=quarantine; adkim=r; aspf=r`                                    |

**What it keeps and what it costs.** Email Delivery stores no message content; it keeps bounces, complaints (a suppression list) and delivery metrics. There is no archiving to turn off, and none must ever be turned on. Sending is inside Always Free (3,000 messages a month, 100 a day), and `oci-quota.json` has `set email-delivery quota max-emails-day to 100 in tenancy` so the tenancy cannot be billed for more. Oracle accepted that statement but its limits API reports no effective value for it, so the cap is not proven to be enforced: the `$1` budget from step 2 is the backstop.

**Hotmail and Outlook.com junk the domain at first.** On 2026-10-28 the test sends passed SPF, DKIM and DMARC at both Gmail and Microsoft (`compauth=pass`), landed in Gmail's inbox, and were still scored spam by Microsoft (`SCL 5`), with a Brief-shaped message as much as a two-line test. The sending IP is clean and the authentication is complete, so what is missing is history for `parentrecap.com`: Microsoft's filter learns from how recipients treat the mail. Decided (#524): accept it and warm up. A Hotmail or Outlook.com Recipient finds the first Brief in Junk and marks it "Not junk" once, which puts the sender on that mailbox's trusted list; the setup page and the first Brief say so (#527). Email Delivery adds a `List-Unsubscribe` header of its own unless the message carries an HTTPS one, so the Brief relay sets its own.

**Put the credentials on the host.** Prompts for the SMTP username and password and writes them to `/etc/brief-relay/brief-relay.env` (`BRIEF_SMTP_USERNAME`, `BRIEF_SMTP_PASSWORD`); the Brief relay reads them from there, and the script restarts it. It is not `bifrost.env`, because Bifrost's unit loads that file into Bifrost's environment and Bifrost only proxies model calls. SMTP endpoint `smtp.email.eu-stockholm-1.oci.oraclecloud.com`, port 465 (TLS) or 587 (STARTTLS), AUTH PLAIN.

```sh
scp ops/gateway/set-smtp-credentials.sh ubuntu@<server>:/tmp/
ssh -t ubuntu@<server> "sudo bash /tmp/set-smtp-credentials.sh"
```

**Rotate the SMTP credentials.** Make a new pair for `brief-sender`, put it on the host, then delete the old one. New credentials take a few minutes to work.

```sh
U=$(oci iam user list --compartment-id <tenancy OCID> --name brief-sender --query 'data[0].id' --raw-output)
oci iam smtp-credential list --user-id "$U"                 # note the old one's id
oci iam smtp-credential create --user-id "$U" --description "gateway, <date>"   # shown once
oci iam smtp-credential delete --user-id "$U" --smtp-credential-id <old id>
```

**Rotate the DKIM key.** Create a second DKIM on the email domain with a new selector, add its CNAME at Porkbun, wait until it is `ACTIVE`, then delete the old DKIM and its record.

**Check it.** `oci email domain get --email-domain-id <id>` shows `is-spf` and the active DKIM; `oci email suppression list --compartment-id <tenancy OCID>` shows addresses Email Delivery has stopped sending to (bounced or complained) and is where to look when a family says the Brief stopped coming.

## Sending the Brief

`brief_relay.py` (the `brief-relay` unit) sends a Household's Brief from `brief@parentrecap.com` (ADR 0028). It is Python's standard library on the host's own `python3`, listens on `127.0.0.1:8081`, and Caddy sends `POST /v1/brief` to it. Bifrost only proxies model calls, so the relay is its own service. It runs as a throwaway system user and reads Bifrost's admin login from `bifrost.env` (without the Anthropic key) and the SMTP credentials from their own `/etc/brief-relay/brief-relay.env`.

**The request.** The program posts JSON with its Gateway Key in `x-api-key`, as for `/v1/messages`. One request is one message to all of the Household's Recipients, in `To`:

```json
{
  "to": ["parent@example.com", "partner@example.fi"],
  "subject": "…",
  "text": "…",
  "html": "…",
  "attachments": [
    {
      "filename": "events.ics",
      "content_type": "text/calendar",
      "data": "<base64>"
    }
  ]
}
```

`html` and `attachments` are optional. At most 10 Recipients, 5 attachments and 2 MB a request; an address must be a plain `name@domain`. The From is `Parent Recap <brief@parentrecap.com>` and there is no Reply-To: the Brief's footer, which the program adds, says the address is unread. `List-Unsubscribe` points at `https://<hostname>/brief/stop` (`brief-stop.html`), which says how a Household stops the Brief in its own config.

**The answers.** 200 `{"sent": n, "refused": n}` once Email Delivery has taken the message, with `"unconfirmed": [address, …]` when a key made on request left out Recipients who haven't confirmed. 401 for a missing, unknown or inactive key; 403 `recipient not confirmed` with `unconfirmed` when such a key has no confirmed Recipient in `to`; 429 for a key past its daily cap; 400 or 413 for a malformed or too large request. 502 when Email Delivery refused the message or couldn't be reached, 503 when Bifrost couldn't be asked about the key: a 5xx means "try again later", a 4xx "fix the request or the key". Each error has a short `error` saying why.

**The key.** The relay looks the key up in Bifrost's virtual-key list on localhost with the admin login, so a Household's one Gateway Key covers writing and sending the Brief, and revoking it (below) stops both.

**The cap.** Each key may send 4 messages a UTC day (`DAILY_CAP` in `brief_relay.py`, and `ON_REQUEST_DAILY_CAP` for a key made on request): the Brief, Weekend Picks and room for a retry, or both in two languages on a Friday. At 10 Recipients each, one key can use at most 40 of the tenancy's 100 a day, so a leaked key can neither become a spam relay nor use up every other Household's sending. A message Email Delivery didn't take doesn't count. The counts are in `/var/lib/brief-relay/sent.json`. Before raising the cap or adding Households that send this way, check the total against the 100 a day.

**What it keeps.** Nothing of the message. Each request logs one line to the journal: the key's name, the number of Recipients, the request's size and the outcome (`journalctl -u brief-relay`), never the subject, body, attachments or addresses. For each key it keeps the date it last sent a Brief, for `admin keys` (`last-sent.json` beside `sent.json`: the key's id and a date, written at most once a day a key, and nothing written to the journal). For keys made on request it keeps the addresses bound to each (see the next section).

**Check it.** From a maintainer's machine, with a real Gateway Key, send a test with an `.ics` to a Gmail and a Hotmail inbox:

```sh
ics=$(printf 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Parent Recap//test//EN\r\nMETHOD:PUBLISH\r\nBEGIN:VEVENT\r\nUID:relay-test@parentrecap.com\r\nDTSTAMP:20261028T120000Z\r\nDTSTART;VALUE=DATE:20261030\r\nSUMMARY:Relay test\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n' | base64 | tr -d '\n')
curl -s https://<hostname>/v1/brief -H "x-api-key: $GATEWAY_KEY" -H 'content-type: application/json' -d '{
  "to": ["<gmail address>", "<hotmail address>"], "subject": "Relay test", "text": "Relay test.",
  "html": "<p>Relay test.</p>",
  "attachments": [{"filename": "events.ics", "content_type": "text/calendar", "data": "'"$ics"'"}]}'
```

## Send-only keys on request

Every Brief is sent through the relay (ADR 0032), so every Household needs a Gateway Key. A Household whose AI is its own Claude Code or Codex needs one only for sending, and the program asks the relay for it itself (ADR 0033): the setup page once the Recipients are saved, `parent-recap setup confirm` from the chat, and `doctor` and the evening run of an installed Household that has none. Keys that write the Brief still come from the maintainers (Runbook below).

**Asking for a key.** `POST /v1/brief/key`, no key, JSON `{"recipients": [{"address": "…", "language": "fi"}, …]}`, the setup parent first, at most 6. The relay makes a Bifrost virtual key named `on-request-<8 hex>`, described `made on request <date>`, with no `provider_configs` and no budget, so Bifrost refuses its model calls; binds the addresses to it, none confirmed; and mails each a link to confirm, from `brief@parentrecap.com`, in its language (Finnish, English or Chinese; any other gets English). The email says who asked (the setup parent's address) and that ignoring it is enough. The answer is 200 `{"key": "sk-bf-…", "recipients": [{"address", "confirmed": false}], "mailed": [address, …]}`, 400 for a malformed request, 429 over a limit, 503 when Bifrost couldn't be asked.

**Confirming.** The link is `https://<hostname>/brief/confirm?t=<token>`. Opening it shows a small page with a Confirm button, in the Recipient's language; only pressing it (a `POST`) confirms, so a mail scanner that opens links confirms nothing. A link stays good as long as its address is bound, and sending it again sends the same link; a token that isn't bound gets a page saying to ask for a new email.

**Binding changed Recipients and sending the link again.** `POST /v1/brief/recipients` with the key in `x-api-key` and `{"recipients": […], "resend": false}`. The key is then bound to exactly those addresses: one already bound keeps whether it confirmed, a new one is mailed its link, one left out is unbound. With `"resend": true` each still unconfirmed address is mailed its link again. It answers the same `recipients` and `mailed` (429 when `resend` could mail nobody for the limits). A key made by hand isn't bound: the relay answers that every address is confirmed and changes nothing. The program sends this from the setup page and from `doctor`, which `manage` runs after changing the Recipients.

**Sending with it.** `POST /v1/brief` with a key made on request goes only to the addresses in `to` that are bound to it and confirmed; the others are left out and named in `unconfirmed`, and with none left it gets 403 `recipient not confirmed`. Keys made by hand send to any address, as before.

**Limits**, per UTC day (`KEYS_PER_CALLER`, `KEYS_PER_DAY`, `MAILS_PER_ADDRESS` and `MAILS_PER_DAY` in `brief_relay.py`): 3 keys per caller (by IP address, from Caddy's `X-Forwarded-For`), 20 keys in all, 3 confirmation emails per address, whoever asks, and 30 confirmation emails in all. So the confirmation emails, the only mail sent before a Recipient says yes, use at most 30 of the tenancy's 100 a day. A key request over a limit makes no key.

**What it keeps.** `bindings.json` in the relay's state directory (`/var/lib/private/brief-relay/`, owner-only, the unit's dynamic user): for each key made on request its Bifrost id and name, the date, and each bound address with its language, its link's token and when it confirmed. The day's counts keep a caller's IP address and each email address only as a truncated SHA-256, and start again each UTC day. Nothing else: no message and no key value. The journal has `key-request`, `bind` and `confirm` lines with the key's name, a count and the outcome, never an address, a token or a caller.

**Check it.** From a maintainer's machine, with your own address:

```sh
curl -s https://<hostname>/v1/brief/key -H 'content-type: application/json' \
  -d '{"recipients": [{"address": "<your address>", "language": "en"}]}'
```

The email comes from `brief@parentrecap.com`; open its link and press Confirm. Then send a test Brief to that address with the key from the answer, as in "Sending the Brief" (it arrives), and to another address (403). Revoke the key afterwards (Runbook).

## Email Delivery's quota

Briefs, confirmation emails and Hosted Households' Briefs all leave through the relay, and the nightly check's mail with the relay's credentials; they share the tenancy's 100 messages a day, the `max-emails-day` quota in `oci-quota.json` that keeps sending inside Always Free's 3,000 a month ([Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)). Email Delivery counts each Recipient of a message as one email, and each 2 MB of a message as another ([Limits by Service, Email Delivery](https://docs.oracle.com/en-us/iaas/Content/General/service-limits/default.htm#Email_Delivery_Limits)), so a Brief to 4 Recipients is 4. It isn't split between them (agreed 2026-10-30, #627): confirmations are capped at 30 a day (`MAILS_PER_DAY`), which leaves at least 70 for Briefs.

**The warning.** The relay counts every message Email Delivery took, Briefs and confirmations alike, per UTC day in `/var/lib/brief-relay/quota.json` (the day and the count, nothing else). The first time a day's count passes 70 (`QUOTA_WARN_AT` in `brief_relay.py`) it logs one warning line, at journald's warning priority, and no more that day:

```
quota day=2026-11-12 sent=71 of 100: past 70, see ops/gateway/README.md "Email Delivery's quota"
```

See whether any day passed it, with the assistant's login too:

```sh
journalctl -u brief-relay -p warning --grep '^quota '
```

**When it shows up.** One day past 70 is a busy Friday; past 70 on most days means the Households have outgrown the 100, and a day's mail past it may be refused (502) if the quota is enforced (see "The sending domain"). Oracle counts its own limit over the last 24 hours, the relay per UTC day, so the warning is an early sign, not the exact edge. At about 30 Households, before that happens, raise the limit. The 100 is our own quota, not Oracle's limit: Oracle's default `max-emails-day` for a Pay-As-You-Go tenancy, which this is (step 1 of "Building the server"), is 50,000, and Always Free's can't be raised ([Limits by Service, Email Delivery](https://docs.oracle.com/en-us/iaas/Content/General/service-limits/default.htm#Email_Delivery_Limits)). Always Free covers 3,000 emails a month (link above); before raising the quota past that, look up what Oracle charges for more.

1. See the tenancy's own Email Delivery limits on the Console's Limits, Quotas and Usage page ([Service Limits](https://docs.oracle.com/en-us/iaas/Content/General/Concepts/servicelimits.htm)).
2. If `max-emails-day` there is below the new number (the next Households' Briefs at 10 Recipients each, with the 30 confirmations on top), ask Oracle for more: in the Console, Help > Request a limit increase, add the item service Email Delivery, limit `max-emails-day`, with the reason "transactional email to opted-in Recipients, about N a day" ([Creating a Limit Increase Request](https://docs.oracle.com/en-us/iaas/Content/General/service-limits/create-request.htm)). It needs SPF and DKIM on the sending domain, which `parentrecap.com` has, and Oracle evaluates it on questions about the sending domain, practices and volume ([Overview of Email Delivery](https://docs.oracle.com/en-us/iaas/Content/Email/Concepts/overview.htm)). Oracle answers in minutes to days, by email to the account's primary contact when it is granted or needs more information (same page as the request).
3. Then change `set email-delivery quota max-emails-day to 100` in `oci-quota.json` to the new number and update the `free-tier-only` quota policy with it (step 2 of "Building the server"), raise the `$1` budget to the expected spend, and set `QUOTA_PER_DAY` and `QUOTA_WARN_AT` in `brief_relay.py` to match, then run step 7 again.

## Hosted Households

The hosted version runs invited Households on this server, each as its own Linux user (ADR 0029, ADR 0031). A Household is made by the sign-up service from an Invite Link the maintainers send: the family opens it on their phone, picks Finnish, English or Chinese, types their Wilma town and login, checks the Recipients the link prefilled, and goes on to paste each Kid's MyClub calendar link (more under "Add another club" for a Kid in several clubs, #810), or, with Wilma, leave them all empty if they don't use MyClub, and press Done (Valmis, 完成).

**Install it.** Copy an unpacked release of the program (or a checkout) to the server beside `ops/gateway`, and run step 7 with `PARENT_RECAP_RELEASE` pointing at it:

```sh
scp -r <unpacked release> ubuntu@<server>:/tmp/parent-recap
ssh -t ubuntu@<server> "sudo GATEWAY_HOST=<hostname> PARENT_RECAP_RELEASE=/tmp/parent-recap bash /tmp/gateway/gateway-setup.sh"
```

It installs the program once for the whole server (ADR 0035): the release's `install.sh`, run as root, puts it in `/usr/local/lib/parent-recap/<version>` with its pinned Python and Node (ADR 0011), and the wilma CLI (ADR 0008) goes beside them; it is owned by root and readable by all. `/usr/local/lib/parent-recap/current` then points at that release, and `/usr/local/lib/parent-recap/bin/parent-recap` (`parent-recap` here) runs the program through it. The sign-up service and every Household run that command, each as its own user, and a Household's evening job runs `current`'s Python, so whatever `current` points at is what runs next. A Household's user holds only its own data: its config, the wilma CLI's sign-in, the Gateway Key file, the archive and the logs, all in its home. The script then makes the service's user `parent-recap-signup` (home `/var/lib/parent-recap-signup`); installs the `household-user` helper in `/usr/local/lib/parent-recap-signup`, the sudoers file and `/etc/parent-recap-signup/signup.env`; and starts the `parent-recap-signup` unit (`parent-recap signup serve`) on `127.0.0.1:8082`, which reads Bifrost's admin login from `bifrost.env`.

**Upgrade, check, switch back.** Once the server is built, it deploys each new release by itself the next morning (ADR 0045, below: "The morning's automatic deploy"). Any other deploy is one command on the server (ADR 0036), a release tag or `main`:

```sh
sudo admin deploy v0.12.0
```

The server fetches that commit of the public repo itself (`github.com` over HTTPS, into `/var/lib/parent-recap-deploy/repo.git`); nothing is copied from your machine. When a fix can't wait for a release, deploy `main`, or a commit on `main` by its hash, instead: `sudo admin deploy main`. Anything else, such as another branch or a hash `main` doesn't have, is refused, and so is an unreleased commit whose `test` check hasn't passed on GitHub (asked of GitHub's public API, without a login; a `test` skipped because the commit changed only docs counts as passed, as it does for merging). A refused deploy changes nothing.

An unreleased commit's check is its own: of a pull request with several commits, only the last one ran `test` on `main`, so deploy that one. A merged PR doesn't wait for that check to run again on `main`, though: while the commit's own `test` is missing or still running, it deploys at once when it has the same code (the same tree) as the head of the PR it came from and that head's `test` passed, and says so (`✅ <commit> has the same code as <head>, whose test check passed`). The PR's `test` ran on its head merged into `main` as it was then, so this holds only when the head was built on `main` as it was when the PR landed (rebased onto it); a head built on an older `main` waits for the commit's own check. A commit whose own `test` failed is refused even then.

It is refused too while any Household's evening run is still running, and while one is due within 30 minutes, at the time its own config says (see "The time a Household's Brief comes"), or `admin households`, which it runs first, marks a Household, unless you give `--anyway`, for the fix for exactly what it marks. Nothing deploys under a running evening run: it ends within minutes.

It checks out the whole commit, `ops/` included, into `/usr/local/lib/parent-recap-deploy/<commit>` (its first 12 hex digits), and runs that commit's `gateway-setup.sh` with it: Bifrost, the Brief relay, `admin` itself, the units, the Caddyfile and the sudoers file come from that commit, and the program is installed beside the one in use, in `/usr/local/lib/parent-recap/<commit>`. `current` then points at it and the services restart: every Household runs it from its next run, its evening Brief included. Deploys are kept by commit, not by version, since an unreleased commit carries the version of the release before it: the deploy before stays on disk, to switch back to. Each deploy is recorded in `/var/lib/parent-recap-deploy/deploys.json` (its commit, its tag or `unreleased`, the time, its snapshot, and whether its install failed), and `admin households` first says which commit runs, marked unreleased until a release tag is deployed.

A bad deploy breaks every Household's next Brief at once, so it then checks itself: each service's `/health`, that `https://<hostname>/` still answers 404 outside its routes, and one Household's evening run, which it starts as the evening job's timer would (sending that Household's Brief to its Recipients), waits for, and reports how it ended. That Household is named once on the server; until it is, a server with Households refuses to deploy:

```sh
sudo mkdir -p /etc/parent-recap-deploy
echo CHECK_HOUSEHOLD=ph-XXXXXXXX | sudo tee /etc/parent-recap-deploy/deploy.env
```

Make it a Household of its own, signed up through an Invite Link whose only Recipient is a mailbox the maintainers keep for these checks, so every deploy mails that mailbox and no family. Ours is `ph-e106d09d` (signed up 2026-11-04). Don't remove it, and leave it out when counting Hosted Households.

A failed check exits 1 and says so; the run's logs are in `~ph-…/ParentRecap/logs/run-stderr.log`, and `sudo admin households` shows every Household.

Before a deploy changes anything, it takes a snapshot of the server's state: Bifrost's (`/var/lib/bifrost`, the Gateway Keys and their spend), the Brief relay's (`/var/lib/private/brief-relay`), the sign-up service's (`/var/lib/parent-recap-signup`, `households.json` and the Invite Links), their config in `/etc` (`bifrost`, `brief-relay`, `parent-recap-signup`) and every Household's home. It is one archive, `/var/lib/parent-recap-deploy/snapshots/<time>-<commit>.tar.gz`, that only root can read, since it holds every secret those do. The last 3 are kept, and also the one taken before the deploy that runs first came, which `--restore` (below) needs. When there isn't room for it and the deploy (1 GB beyond the snapshot), or it can't be written, the deploy stops there and changes nothing. The three services are stopped while their own state is archived, so a database isn't caught mid-write, and started again before the homes are, which are taken with no evening run running.

`sudo admin deploys` lists the deploys on disk, the last first, with `*` beside the one that runs, which one `admin rollback` would switch to, and the snapshots.

To switch back, from a deploy whose check failed, whose `gateway-setup.sh` failed partway (recorded as failed, as it may have changed some of the server), or one found bad later:

```sh
sudo admin rollback
```

It switches back to the deploy before the one that runs, by the record: it runs that commit's `gateway-setup.sh` again from its folder, so the program, Bifrost, the Brief relay, `admin`, the units, the Caddyfile and the sudoers file are that commit's again, the services restart, and every Household runs it from its next run. Its program is installed already, so it isn't installed again, unless `/usr/local/lib/parent-recap/<commit>` is gone. It says from which deploy to which, records the switch, and checks each service's `/health` and the 404; it doesn't run a Household's evening run, so read `sudo admin households` after the next one. Run again, it goes one further back; after a rollback and a new deploy, it goes back to what ran before that deploy. It refuses, changing nothing, when there is nothing to switch back to: no deploy recorded before the one that runs, a `current` set by hand, or the deploy before's checkout no longer on disk (deploy that one again by its commit). It refuses too while an evening run is running. Switched back to a commit from before `admin rollback` existed, its own `admin` has no `rollback`: deploy forward again with `sudo admin deploy`.

A release must not leave a Household's data unreadable to the release before it (ADR 0036); one that does says so in its changelog. Switching back from it also needs the state from before it:

```sh
sudo admin rollback --restore
```

It says what it will do and asks you to type `yes`. Then it stops Bifrost, the Brief relay and the sign-up service and each Household's evening timers, puts back the snapshot taken before the deploy that runs (each folder in it whole, unpacked beside it first and then swapped in), switches back as above, which starts the services again, and starts the timers again. What changed since that snapshot is lost: Briefs archived since, keys made since, and a Household that signed up since loses its line in `households.json`; its home, not in the snapshot, is kept, and it says so. A Household's home in the snapshot is skipped, by its label with the reason, when its user no longer exists (the Household was removed since) or holds another uid than the one the home was owned by: unpacked with that uid, the home would be readable by whoever holds it now, such as a Household that signed up since. The other folders are restored as usual. Without that deploy's snapshot on disk it refuses before asking. When a service won't stop, it restores nothing; when the restore or the switch fails, it starts the services and timers again and says what is restored, and running it again finishes it. A folder a restore cut short (by a power cut, say) left set aside, `.<name>.before-restore`, is put back by the next one.

Remove a deploy that isn't `current` and that you won't switch back to with `sudo rm -rf /usr/local/lib/parent-recap/<commit> /usr/local/lib/parent-recap-deploy/<commit>`, and an old snapshot with `sudo rm /var/lib/parent-recap-deploy/snapshots/<name>`. Deploying the same commit again installs it again in place, so there is nothing to switch back to.

**The morning's automatic deploy** (ADR 0045). Every morning at 10:00 Finnish time, `parent-recap-autodeploy.timer` runs `admin autodeploy run` as root. It fetches `stable` from the public repo, which the release workflow moves only to a tag whose tests passed and that is the highest version so far. When `stable` points at a release tag (`vX.Y.Z`) the server doesn't run, nor a later commit (a `deploy main` since, say), it runs `admin deploy vX.Y.Z` as you would, with the same refusals, snapshot and checks, and mails the maintainers (`MAINTAINERS_ADDRESS`, as the nightly check):

- **The checks passed:** the version it now runs, the one before, how the check Household's evening run ended, and that release's section of `CHANGELOG.md`.
- **A check failed, or `gateway-setup.sh` failed partway:** it runs `admin rollback` (without `--restore`) and mails the version it tried, which check failed, and whether the switch-back's own checks passed. A release that failed a check, or that you switched back from, isn't deployed again by itself, even when the switch-back was refused (an evening run still running): deploy it or its fix by hand.
- **The release needs the snapshot to switch back** (its changelog section carries the marker in CONTRIBUTING.md's "Releasing"): nothing is deployed, and it mails, once, that the release waits for a deploy by hand. Deploy it with `sudo admin deploy vX.Y.Z` when you can watch it.
- **Refused** (an evening run running or due, `admin households` marking a Household, no room for the snapshot, GitHub not answering): nothing changed, and it tries again the next morning. It mails only on the third day running a deploy was refused, and every third day after while it still is, with the last reason.

When `stable` is what runs, it does nothing and mails nothing. The mails carry no address, Kid's name or message text: versions, commits, check results and Household labels only. The deploys it makes are recorded in `admin deploys` as any other.

```sh
sudo admin autodeploy pause    # each morning's timer checks nothing and mails nothing
sudo admin autodeploy resume
sudo admin autodeploy status   # paused or not, and the last automatic attempt: when and how it ended
```

`admin households` says when automatic deploys are paused. Pause them before deploying by hand something you don't want replaced (it never replaces a later commit of `main`, only an older one), or while a bad release is being fixed. `autodeploy status`'s state is `/var/lib/parent-recap-deploy/autodeploy.json`. See the attempts with `journalctl -u parent-recap-autodeploy` and the next with `systemctl list-timers parent-recap-autodeploy`; `sudo admin autodeploy run` runs one now. A morning the server was down waits for the next morning. `gateway-setup.sh` installs the timer and turns it on, so a server deployed once by hand with a commit that has it deploys the next release by itself.

**Move a Household made before the shared program.** Until 2026-11-02 the sign-up installed the program in each Household's home (`~/ParentRecap/app` with its `runtime`, `wilma` and `bin`), and its evening job ran that copy. `admin households` and `admin remove` already work for it, through the shared program. To have it run the shared program too, on the server, with its label:

```sh
cd / && sudo -u ph-0123abcd -H -- /usr/local/lib/parent-recap/bin/parent-recap schedule install
sudo -u ph-0123abcd sh -c 'grep -q "^hosted:" /home/ph-0123abcd/.family/config.yaml || sed -i "1i hosted: true" /home/ph-0123abcd/.family/config.yaml'
sudo cat /home/ph-0123abcd/.config/systemd/user/parent-recap-daily.service    # ExecStart is /usr/local/lib/parent-recap/current/…
sudo rm -rf /home/ph-0123abcd/ParentRecap/{app,runtime,wilma,baileys,bin,plugin} /home/ph-0123abcd/.local/bin/parent-recap
```

The first line rewrites its evening job, at the same time, to run the shared program. The second adds `hosted: true` at the top of its config (`~/.family/config.yaml`, owner-only) when the line isn't there, as its user so the file keeps its owner and mode: a Household made before the sign-up wrote it (#669) reads as one on a family's own computer, and its Brief's footer says so. The last removes its own copy, and the link to it install.sh made, and keeps its archive and logs (`ParentRecap/*.md` and `ParentRecap/logs`). Its `~/.family/install-record.json` still names the removed copy, which nothing on the server reads first: the program looks beside itself. Then run its evening run and read `admin households`, as after a deploy.

**Invite a Household.** On the server, with the Recipients the family gave you and the language each reads the Brief in. It prints the link; send it over a private channel:

```sh
sudo admin invite --recipient parent@example.com:fi --recipient partner@example.com:en --days 14 --note "Virtanen family"
```

`sudo admin invite --help` lists its options: `--language` is the page's until the family picks another, and the link works for `--days`.

`--note` is optional, for the maintainers only: say whose link it is, such as `via Zhao 2026-11-06 #3` for one of a batch handed to whoever introduces families. It goes with the link into the Household's line in `households.json` and beside its user in `admin households` and `admin find`, and is never shown on the family's page. Keep it to what you would put in a log: a name, not an address or a password.

**The family's name.** The page also asks the family, as its first field and in its language, what to call them: a short name of their choosing, such as `the Virtanens` or `王家`, which it says only the maintainers see (#814). It is required, kept on one line and to 60 characters, and an address (anything with an `@`) is refused, so `admin households` stays free of addresses. When the Household is made it joins the note: its line in `households.json` holds `via Zhao 2026-11-06 #3 · 王家`, or the name alone for a link minted without a note, and `admin households` and `admin find` show it in the brackets. Links minted before this get it too, since the note is written at sign-up.

The link is a random code, kept with the Recipients and the note in the service's owner-only store (`/var/lib/parent-recap-signup/signup/invites/`). It works once, until it expires, and a Household made from it retires it, Recipients and all. A link that isn't good, or any other address under `/join/`, gets the same 404 as the rest of the server.

**What a sign-up does.** The service signs in to Wilma with the login typed and reads the Kids before anything is made: a wrong password is said on the page, and the link stays good. The page then asks for each Kid's MyClub link; the checked answers, the Wilma password among them, wait for it in the service's memory only, for up to 30 minutes, after which the family signs in again. Each link given is downloaded once to check it, and one that doesn't open is said on the page; an empty one leaves MyClub off for that Kid. Then it makes the Household's user (`ph-` and 8 random hex digits, with a home only it can read, lingering so its timers run), mints its Gateway Key named after that user, and runs the shared `parent-recap signup household` as it (nothing is installed for it) with the answers, the Wilma login, the MyClub links and the key on its stdin. That writes the config (`hosted: true`, so each Brief's footer says it comes from the hosted version; Kids from Wilma with their MyClub links, the Recipients, the gateway as AI and sender, Wilma on, WhatsApp off, the calendar as an `.ics` in the email), the wilma CLI's sign-in, the key as an owner-only file (ADR 0030), and the evening job, which runs the shared program. Last, it runs `parent-recap run --first-brief` as the user, which makes the first Brief from the last three days, as the setup page's first Brief step does, even when nothing is new, and sends it to every Recipient in their language from `brief@parentrecap.com`. Each Household's Brief comes in the next free slot from 21:00 in 5-minute steps, so the runs don't all start at once. A step that fails, the first Brief included, takes back the user with its home and jobs and the key, and the link stays good. The page then says the first Brief was sent and when the next ones come, and, when a Recipient is on Hotmail or Outlook.com, to look in Junk and mark it "Not junk" once. Only the user name, its evening time and the note, the link's joined with the name the family gave, are kept in the store's `households.json`. Last, the service runs `sudo admin signed-up ph-…`, which mails the maintainers (`MAINTAINERS_ADDRESS`, as the nightly check does, from `brief@parentrecap.com` with the relay's SMTP credentials, counted in the day's quota) the new label, its note with the family's name and the time its Brief comes, and nothing else: no address, no Kid's name, no Source login. A mail that can't go is logged in the service's journal (`The maintainers weren't mailed of Household ph-…`) and the Household stays made; `admin households` lists it all the same.

**See every Household's status.** On the server; it reads nothing of a Household's messages, so any maintainer may run it:

```sh
sudo admin households
```

For each Household in `households.json` it runs `parent-recap signup health` as the Household's user, and prints its user with its Invite Link's note in brackets, such as `ph-0123abcd (Virtanen family)`, the time its Brief comes, when its last run ended and how, how long it took and its peak memory (from `last-run.json` beside its `state.json`), such as `last run 2026-10-30 18:00 UTC, ok, 7 s, 180 MB`, and each of `doctor`'s checks by its key with ✅, ⚠️ or ❌, run then without the model's test call. A last run that failed is marked ❌, a run not ended an hour after its time ⚠️ overdue, and a Household whose program is older than the command ❓. It prints no message text, subject, address or Kid's name, not even doctor's words, which name them. It exits 1 when any Household is marked. Last, when the server waits for a reboot to apply updates (the file `/run/reboot-required` exists, which Ubuntu's [Server documentation](https://ubuntu.com/server/docs/how-to/software/upgrade-your-release/) says means a reboot is needed), it says `Reboot pending: the server waits for a reboot to apply updates`. Every night the server checks this for you and mails what is wrong (see "The nightly check").

**See and change one Household.** A Household's Recipients and MyClub links are set by its sign-up; a family without a computer asks the maintainers to change them (#782). On the server, with its label:

```sh
sudo admin household ph-0123abcd             # what it has
sudo admin household ph-0123abcd recipients  # change its Recipients
sudo admin household ph-0123abcd myclub      # change its Kids' MyClub links
```

Each runs the shared program as the Household's own user (`signup show`, `signup recipients`, `signup myclub`), so its config keeps its owner and mode. Root runs it, not the sign-up service's user, which may read no MyClub link. A label that is no Household's in `households.json` is refused.

- **What it has.** Its Recipients, each with its language, and the Weekend Picks' own (`weekend_to`) when it has any; its Kids as Wilma gave them (or as the family named them, without Wilma), each with its MyClub links, one a line, or `none`. It holds addresses, Kids' names and links, which are secrets: read it on the server, never paste it (see "What can be pasted").
- **Recipients.** It prints the list it has and asks for the new one, one `ADDRESS[:LANGUAGE]` a line (`fi`, `en` or `zh`; without one, the Household's language), the parent first, an empty line to end. They are asked for, never given on the command line, because `sudo` logs each command line to the journal, as `key new` asks for the holder's email. It shows the change and writes it only after `yes`, as the sign-up writes it; `weekend_to` stays as it was. Then it binds the Household's Gateway Key to the new list as `doctor` does (see "Binding changed Recipients and sending the link again"): a key the sign-up made isn't bound, so nothing changes at the relay.
- **MyClub.** It asks for each Kid in turn: paste their links, one per club (#810), separated by spaces or one a line until an empty line, which take the place of what the Kid has; press Enter to keep what the Kid has, or type `none` to turn MyClub off for that Kid. Each new link is downloaded once to check it, as the sign-up does; one that doesn't download, isn't MyClub's or is another Kid's stops it with nothing written. It shows the change and writes it only after `yes`.

The Household's next run, and its next `admin households`, use what was written. A Kid comes from Wilma, so none is added or removed here; the evening time is "Move a Household's Brief to another time".

**The time a Household's Brief comes.** The time `admin households` shows is the Household's own, from its config (`schedule` in `~/.family/config.yaml`, in its `timezone`), which its evening timer follows and `signup health` reports; `admin deploy`'s refusal while a run is due soon reads the same. The time in its line in `households.json` is only the slot the sign-up handed out, which later sign-ups skip. It is shown instead only when `signup health` says no time: a program older than #780, or a config that won't load.

**Move a Household's Brief to another time.** There is no `admin` task for it. On the server, with its label and the new time (here 16:00, in the Household's `timezone`), as the Household's user, so its config keeps its owner and mode:

```sh
cd / && sudo -u ph-0123abcd -H -- /usr/local/lib/parent-recap/current/app/.venv/bin/python -I -c 'import yaml; p = "/home/ph-0123abcd/.family/config.yaml"; d = yaml.safe_load(open(p, encoding="utf-8")); d.setdefault("schedule", {}).update(daily_hour=16, daily_minute=0); open(p, "w", encoding="utf-8").write(yaml.safe_dump(d, allow_unicode=True, sort_keys=False))'
cd / && sudo -u ph-0123abcd -H -- /usr/local/lib/parent-recap/bin/parent-recap schedule install
```

The first line sets the time in its config, written as the sign-up writes it. The second reinstalls its evening job at that time and prints it (`Brief: every day at 16:00`). `sudo admin households` then shows the new time. Its line in `households.json` keeps its old slot, which stays taken; that is harmless.

**Find a Household.** Only its note, its Invite Link's with the name the family gave, says which family a label is. When a parent asks about their Brief, or a family on its own computer about its Gateway Key, find it from a Recipient address they give, or the holder's name:

```sh
sudo admin find parent@example.com
sudo admin find Korhonen
```

It searches two things and prints what matched in each:

- **Hosted Households.** It reads each Household's config (`/home/ph-…/.family/config.yaml`, which root can read) with the shared program's Python, and prints the label of each Household whose Brief or Weekend Picks Recipients include the address (the whole address), ignoring case, with its Invite Link's note in brackets, such as `ph-0123abcd (Virtanen family)`, and nothing else of its config. It follows no link in a Household's home, `.family` or config, since a Household's user can make one.
- **Gateway Keys.** Each key whose `description` (who holds it, with the holder's email when `key new` was given one) or one of the addresses bound to a key made on request (`bindings.json` in the relay's state directory) holds the text, ignoring case, is printed with its `admin keys --addresses` line (made, last used, spend) and, for a key made on request, its addresses. A Hosted Household's key (named `ph-…`) is followed by that Household's lines from `admin households`. It never prints a key's value.

When one of the two can't be searched, such as Bifrost not answering, it says why and still prints the other. It exits 1 only when neither a Household nor a key matched.

**Remove a Household.** On the server, with the Household's label, its user as `ls /home`, `admin households` and the sign-up's log show it:

```sh
sudo admin remove ph-0123abcd
```

It prints each step as it goes: the evening jobs removed by the shared program run as the Household's user, its Gateway Key revoked on the admin API, its user deleted with its home (and so its config with its MyClub links, Wilma sign-in, key file, archive of Briefs, and the `state.json` and `last-run.json` that `admin households` reads), and its line taken off the store's `households.json`, which frees its evening slot and takes it off `admin households`' list. The shared program stays, for the other Households. Outside the backups below, nothing of the Household stays on the server but log lines that held nothing of the family: the journal's lines naming its label, and Bifrost's per-request token counts and cost. Its home, with everything in it, stays in the deploy snapshots taken before the removal, which only root can read, until they are pruned, up to 4 deploys later (see "Upgrade, check, switch back"); `rollback --restore` never brings it back. It also stays in the weekly boot volume backups (ADR 0037) for up to 4 weeks, until the last one taken before the removal expires 28 days after it was taken (see "Backups"). A label that is no Household's is refused and nothing is touched. If a step fails, such as the admin API not answering, it stops there and says so; the Household stays on the list, and running the command again does the rest. Jobs its program couldn't remove go with its user anyway, since deleting the user ends everything running as it.

**Sudo.** The service's user may run, as root, only `household-user add|remove ph-…`, which refuses any other name, and `admin signed-up ph-…`, which takes only a Household's label in `households.json` and mails what is above (#814); it can't read the SMTP credentials or the maintainers' address itself. As a member of `parent-recap-households`, the Households' group, it may run only its own commands, with exactly their arguments (`signup.sudoers`): the shared `parent-recap` with `signup household`, `run --first-brief`, `signup health` or `schedule uninstall`. A Household's user can't change that program, which root owns. So a bug in the sign-up service can't read a Household's Wilma password, MyClub links or Gateway Key; it could still rewrite a Household's config, its Recipients among them, through `signup household`, and so have a first Brief, made from the last three days' messages, sent to an address of its choosing. A password or key never goes on a command line.

**What it logs.** One line a sign-up (`journalctl -u parent-recap-signup`): the Household's user and evening time, or why the sign-up wasn't taken. Never a request's address, which carries the code, or its body, which carries the Wilma password.

## The nightly check

Every night at 23:00 Finnish time, after the last evening slot (21:55) and the hour `admin households` gives a run before calling it overdue, `parent-recap-check.timer` runs `admin check --mail` as root (#745). It checks:

- **Every Hosted Household**, by running `admin households`' status: each one marked ❌ (its last run failed), ⚠️ (overdue) or ❓ (its status couldn't be read). A status that couldn't be run at all is said too. Skipped when the sign-up service isn't installed.
- **The gateway's services**: `caddy`, `bifrost`, `brief-relay` and `parent-recap-signup`, each one that is enabled and not running.
- **A pending reboot**: the file `/run/reboot-required`, which [Ubuntu's Server documentation](https://ubuntu.com/server/docs/how-to/software/upgrade-your-release/) says exists when a reboot is needed after updates.

When anything is wrong it mails the maintainers one message from `brief@parentrecap.com`, through Email Delivery with the relay's SMTP credentials, so it counts toward the day's quota (see "Email Delivery's quota"). When nothing is wrong it sends nothing. The mail holds only the status lines of the Households marked (their labels, Invite Link notes, times, exits and check statuses), the names of the services down and the reboot: never message text, an address or a Kid's name, which the status never prints.

**The address** is the server's, not the repo's, and the sign-up's mail of each new Household (see "What a sign-up does") and the automatic deploy's mails (see "The morning's automatic deploy") go to it too: `/etc/brief-relay/maintainers.env` (`MAINTAINERS_ADDRESS`, commas between several; `maintainers.env.example`). The first run of `gateway-setup.sh` asks for it; to set or change it, run step 7 again with `MAINTAINERS_ADDRESS=<address>` before `bash`. Without it the check still runs and says in its journal that it mailed nobody.

**By hand.** `sudo admin check` prints what the mail would say, or `nothing wrong`, and mails nothing; `sudo admin check --mail` mails it now, as at night. See the last runs with `journalctl -u parent-recap-check` and the next with `systemctl list-timers parent-recap-check`. The check's own run fails (`systemctl --failed`) only when its mail couldn't go (no address, no SMTP credentials, or Email Delivery refused it) or it ran past 2 hours. A night the server was down at 23:00 is checked when it is up again.

## Runbook

What you want, and the command for it. Each runs on the server as `sudo admin …`; the sections named say more.

| What you want                                           | Command                                                                                                          |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| A family runs hosted on the server                      | `invite --recipient ADDRESS[:LANGUAGE] … --note "…"` (see "Invite a Household")                                  |
| A Hosted Household's Gateway Key                        | None: the sign-up makes it; never make one by hand (see "What a sign-up does")                                   |
| A send-only key for a Household on its own computer     | `key new LABEL "WHO HOLDS IT" --send-only`, only if it can't get one by itself (see "Send-only keys on request") |
| A key that writes the Brief and sends it                | `key new LABEL "WHO HOLDS IT"` (see "Create a Gateway Key and give it to a Household")                           |
| Every key, who holds it, its use and spend              | `keys`; this month's spend: `cost`                                                                               |
| Stop a key, or start it again                           | `key off NAME-OR-ID`, `key on NAME-OR-ID`                                                                        |
| Every Hosted Household's status                         | `households`                                                                                                     |
| A Hosted Household's Recipients, Kids and MyClub links  | `household ph-XXXXXXXX` (see "See and change one Household")                                                     |
| Add or change a Hosted Household's Recipients           | `household ph-XXXXXXXX recipients`, asks for the list (see "See and change one Household")                       |
| Add, fix or turn off a Kid's MyClub link                | `household ph-XXXXXXXX myclub`, asks for each Kid (see "See and change one Household")                           |
| A Hosted Household's Brief at another time              | None: two commands as its user (see "Move a Household's Brief to another time")                                  |
| Which Household has this address                        | `find ADDRESS` (see "Find a Household")                                                                          |
| Which Gateway Key a family holds, by name or address    | `find TEXT` (see "Find a Household")                                                                             |
| Take a Hosted Household off the server                  | `remove ph-XXXXXXXX` (see "Remove a Household")                                                                  |
| A new release on the server                             | None: the server deploys it the next morning and mails you (see "The morning's automatic deploy")                |
| A release that waits for a deploy by hand, or `main`    | `deploy vX.Y.Z` or `deploy main` (see "Upgrade, check, switch back")                                             |
| Stop the automatic deploys, or start them again         | `autodeploy pause`, `autodeploy resume`; how the last one ended: `autodeploy status`                             |
| Undo the last deploy, the program alone or the data too | `rollback`, `rollback --restore` (see "Upgrade, check, switch back")                                             |
| What is wrong tonight                                   | `check` (see "The nightly check")                                                                                |

**What can be pasted.** The output of `households`, `keys`, `cost`, `check`, `deploys`, `autodeploy` and `--help` carries no address, no Kid's name and no key's value, so it can be pasted into a chat, such as one with an AI assistant, as it is. `households` (and `find`) now show beside each Household the name the family chose to be called at sign-up, such as `王家`, after the Invite Link's note: a name the family gave for the maintainers, still never an address, which the page refuses there. `find`, `invite` and `keys --addresses` echo addresses, and `key new` prints a key's value: read those on the server, or take the addresses and the key out before pasting. `household` prints a Household's addresses, its Kids' names and their MyClub links, which are secrets: read it on the server only, never paste it.

Every routine task is one command on the server: SSH in, then `sudo admin <task>`. Nothing runs on the server from a laptop, and nothing below is run on the public address. `gateway-setup.sh` installs it as `/usr/local/sbin/admin` from `ops/gateway/admin`. It reads the admin login from `/etc/bifrost/bifrost.env` itself, never the provider key beside it, and calls Bifrost's admin API on localhost, so no tunnel is needed. `sudo admin --help` lists its tasks:

| Task                                         | What it does                                                                                                         |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `invite --recipient ADDRESS[:LANGUAGE] …`    | Mints an Invite Link and prints it (see "Invite a Household")                                                        |
| `households`                                 | Every Hosted Household's last run and checks (see "See every Household's status")                                    |
| `keys [--addresses]`                         | Every Gateway Key: who holds it, what it can do, made, last used, spend; `--addresses` prints its addresses (below)  |
| `cost`                                       | This month's model spend per key and in all (below)                                                                  |
| `key new LABEL "WHO HOLDS IT" [--send-only]` | Asks for the holder's email, makes a Gateway Key and prints its value (below)                                        |
| `key off NAME-OR-ID`, `key on NAME-OR-ID`    | Turns a Gateway Key off or back on (below)                                                                           |
| `find TEXT`                                  | Households with this Recipient, keys holding it in who holds them or their addresses (see "Find a Household")        |
| `household ph-XXXXXXXX [recipients\|myclub]` | One Household's Recipients, Kids and MyClub links, or changes them (see "See and change one Household")              |
| `remove ph-XXXXXXXX`                         | Removes a Hosted Household with everything of it (see "Remove a Household")                                          |
| `deploy vX.Y.Z\|main\|COMMIT [--anyway]`     | Snapshots the state, fetches that commit and installs it all (see "Upgrade, check, switch back")                     |
| `deploys`                                    | The deploys on disk, marking the current one (see "Upgrade, check, switch back")                                     |
| `rollback [--restore]`                       | Switches back whole to the deploy before; `--restore` also restores its snapshot (see "Upgrade, check, switch back") |
| `autodeploy pause\|resume\|status`           | Stops or starts the morning's automatic deploy, or says how it last ended (see "The morning's automatic deploy")     |
| `check [--mail]`                             | What is wrong tonight; `--mail` mails it, as at night (see "The nightly check")                                      |
| `signed-up ph-XXXXXXXX`                      | Mails the maintainers a new Household's label, note and time; the sign-up runs it (see "What a sign-up does")        |

Run without root it refuses and says why. It prints only to the terminal and writes no log of its own, and no key's value is printed except by `key new`. `sudo` logs each command line to the system journal, as it did for the scripts `admin` replaced, so an address given to `invite` or `find` is there, and a note should stay a name. Those scripts (`keys.sh` and `new-key.sh` in `/usr/local/lib/brief-relay/`, `invite.sh`, `status.sh` and `remove.sh` in `/usr/local/lib/parent-recap-signup/`) are gone: the next run of `gateway-setup.sh` deletes them from the server.

**Create a Gateway Key and give it to a Household.** The `name` is a neutral label, because it is written to Bifrost's request log and the relay's journal, which more people can read. It starts with what the key can do, so a maintainer reading a list or a log tells the kinds apart: `ai-` for a key that writes the Brief and sends it, `send-` for one that only sends, each followed by the Household's label. Keys made by the program carry their own: a Hosted Household's is its user (`ph-…`), and one made on request starts `on-request-`. The key's value is the same kind of `sk-bf-…` for all of them; `admin keys` (below) says what each can do from what Bifrost holds, not from its name. Who holds the key goes in its `description` (the Household, its AI backend, the date), which only the admin API returns; it is kept only in the gateway's database, never in the repo. The printed `sk-bf-…` value is the key: send it to the Household over a private channel and don't save it anywhere. Bifrost keeps it in plaintext and the key list returns it too, so a lost key can be read back, and the admin login (kept only in the maintainers' password manager) can read every Household's key.

`admin key new` makes it: the label (lowercase letters, digits and dashes) becomes `ai-<label>`, who holds it and today's date go into the `description` (`<Household>, gateway AI, <date>`), and it prints the name and the `sk-bf-…` value. First it asks for the holder's email, so `admin find` finds the key by it later: type it, or press Enter to skip. It must be a plain `name@domain` (it asks again otherwise) and goes only into the `description`, after who holds it (`<Household>, <email>, gateway AI, <date>`). It is asked for, never given on the command line, because `sudo` writes each command line to the system journal, which the assistant's read-only login can read. Without a terminal to ask on, it skips the question. The key may call only the `parent-recap` model through the gateway's Anthropic provider, with a $0.10 budget that resets daily. When Bifrost made no key, it says so with Bifrost's reply and prints no key:

```sh
sudo admin key new pilot-a "<Household>"
```

**Create a send-only Gateway Key.** A Household whose AI backend is its own Claude Code or Codex gets one on request by itself (see "Send-only keys on request"), so this is only for a Household that can't, such as one whose Recipients can't confirm by email. A key made here isn't bound to addresses, so it sends to any. It has no `provider_configs` and no budget: Bifrost refuses its model calls (400 or 403) and the relay accepts it, since the relay only checks that the key exists and is active. Same handover and the same question for the holder's email, named `send-<label>` and described `<Household>, own Claude Code or Codex, <date>` (the email, if given, after `<Household>`), with `--send-only`:

```sh
sudo admin key new pilot-b "<Household>" --send-only
```

**Say who holds a key made without a description.** Not an `admin` task: find its `id` with `admin keys`, then, through a tunnel to the admin API with the admin login from the maintainers' password manager, set the `description` alone (`PUT` changes only the fields it is given; Bifrost has no `PATCH` here):

```sh
ssh -L 8080:localhost:8080 <server>      # leave open
curl -s -u "<admin user>:<admin password>" -X PUT localhost:8080/api/governance/virtual-keys/<id> \
  -H 'content-type: application/json' -d '{"description": "<Household>, <AI backend>, <date>"}'
```

**See every key.** One line a key: its name and `id`; who holds it (its `description`) when it has one; `writes+sends` or `sends only` (whether Bifrost has a provider behind it); `on` or `off`; `made <date>`; `last used <date>` or `never`; and its spend today, against its daily budget if it has one, and over the last 30 days. A key made on request (name `on-request-…`, description `made on request <date>`) is followed by how many addresses are bound to it and how many of them confirmed. It prints no address, so its output can be pasted (see "What can be pasted"): an email in who holds a key (the holder's, when `key new` was given one) is printed as `<address>`. It prints no key's value and writes nothing:

```sh
sudo admin keys
```

```text
ai-pilot-a  id=…  Korhonen family, <address>, gateway AI, 2026-10-01  writes+sends  on  made 2026-10-01  last used 2026-11-04  today $0.02 of $0.10  30 days $0.33
on-request-…  id=…  made on request 2026-10-28  sends only  on  made 2026-10-28  last used 2026-10-29  today $0.00  30 days $0.00
  2 addresses, 1 confirmed
```

With `--addresses` it prints who holds each key as it is, and under a key made on request each address bound to it, its language and whether it confirmed, as `find` does; read that on the server rather than pasting it:

```sh
sudo admin keys --addresses
```

```text
on-request-…  id=…  made on request 2026-10-28  sends only  on  made 2026-10-28  last used 2026-10-29  today $0.00  30 days $0.00
  parent@example.com (fi): confirmed 2026-10-28T19:02:11Z
  partner@example.fi (zh): not confirmed
```

Days are UTC days. `made` is when Bifrost made the key. `last used` is the later of the key's last model call in Bifrost's request log, which keeps each request's key, time, token counts and cost for 90 days (`log_retention_days` in `config.json`), and the last Brief the relay sent with it, which the relay keeps per key (see "What it keeps" under "Sending the Brief"); so a key last used over 90 days ago only for model calls shows `never`. A model call Bifrost refused, such as a send-only key's, isn't use. The spend is the sum of the request log's cost for the key. When the request log can't be read, the keys are still listed, with `?` for last use and spend, and it says why.

A key whose Recipients never confirmed sends nothing; revoke it when tidying up. A key made on request is revoked as any other, below: the relay then answers 401 to it, and its entry in `bindings.json` is no longer used.

**Revoke, or turn a key back on.** A Hosted Household's key goes with it (see "Remove a Household"). For any other, turn it off by its name or its `id` from `admin keys`; `key on` turns it back on. It says what it changed, or that the key already was so. A name no key has, or one two keys share, is refused and nothing is changed; give the `id` then. A key off can neither write nor send the Brief: Bifrost refuses its model calls and the relay answers 401.

```sh
sudo admin key off ai-pilot-a
sudo admin key on ai-pilot-a
```

**Check spend.** `admin keys` prints what each key spent today and over the last 30 days. `admin cost` prints this month's (UTC) spend per key from the same request log, a key since deleted by its `id`, and the total:

```sh
sudo admin cost
```

These are Bifrost's own figures, from its price list. What is billed is the Anthropic Console's, for the gateway's workspace, which also enforces its $10 monthly limit; `admin cost` says so.

**Upgrade.** Try the new version on your own machine first (`npx -y @maximhq/bifrost --transport-version vX.Y.Z`). Then, in `gateway-setup.sh`, change `BIFROST_VERSION` and both `SHA256_*` lines (hash the binaries at `https://downloads.getmaxim.ai/bifrost/<version>/linux/<arm64|amd64>/bifrost-http`), land that on `main`, and deploy it on the server with `sudo admin deploy main` (once its `test` check passed) or with the next release's tag (see "Upgrade, check, switch back"). The old version stays in `/usr/local/lib/bifrost/`; `sudo admin rollback` switches back to it with the deploy before.

**Rotate the provider key.** Create the new key in the Console first (in the gateway's workspace, whose monthly limit is the gateway's ceiling), then:

```sh
scp ops/gateway/set-provider-key.sh ubuntu@<server>:/tmp/
ssh -t ubuntu@<server> "sudo bash /tmp/set-provider-key.sh"
```

Make a test call, then delete the old key in the Console.

**Change the model.** Edit the `aliases` entry in `config.json`, land it on `main`, and deploy it with `sudo admin deploy` (see "Upgrade, check, switch back").

**Check it is up.** The assistant's login can read logs and status: `journalctl -u bifrost -u brief-relay -u parent-recap-signup -u caddy -u parent-recap-check -u parent-recap-autodeploy`, `systemctl status bifrost brief-relay parent-recap-signup`. The nightly check mails the maintainers when one isn't running (see "The nightly check").
