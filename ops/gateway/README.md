# The maintainers' gateway

The server that invited Households' Gateway Keys go through (ADR 0014). Everything needed to rebuild it is here, and none of it is secret (ADR 0015). Where the running server is and how to reach it are in the maintainers' Bitwarden, not here.

- `host-setup.sh` sets up the VM (Ubuntu/Debian): system updates, SSH with keys only, a firewall that lets in only SSH and HTTPS, Caddy, and a login for the AI assistant that can read logs but has no sudo.
- `gateway-setup.sh` installs and upgrades Bifrost (ADR 0016), the Brief relay and, given a release of the program, the hosted version's sign-up service (ADR 0031) behind Caddy. It works on any systemd Linux with Caddy and `python3` installed.
- `config.json`, `bifrost.service`, `brief_relay.py`, `brief-relay.service`, `brief-stop.html`, `Caddyfile` and `bifrost.env.example` are what it installs, and for the sign-up `parent-recap-signup.service`, `signup.env.example`, `signup.sudoers`, `household-user` and `invite.sh`. The env examples hold only variable names.
- `oci-quota.json` is the Oracle Cloud quota policy that lets the account create only Always Free resources.
- `set-smtp-credentials.sh` puts the sending domain's SMTP credentials into their own env file, whose names are in `brief-relay.env.example` (see "The sending domain").

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

8. **Spend limit.** In the Anthropic Console, create a workspace for the gateway, put the gateway's API key in it and set that workspace's monthly spend limit to $10 (Settings > Workspaces > Spend limits). The default workspace can't be limited.
9. **Record it.** Keep the provider, region, IP, hostname and SSH logins in the maintainers' private notes, not here.

Both scripts are safe to run again.

## What the gateway does

- Only `POST /v1/messages`, `POST /v1/brief` (see "Sending the Brief"), the Brief's stop page `/brief/stop` and the sign-up page `/join/<code>` (see "Hosted Households") are reachable from the internet. Caddy sends `/v1/messages` to Bifrost's Anthropic endpoint, `/v1/brief` to the Brief relay and `/join/` to the sign-up service, serves the stop page as a static file, and answers 404 to everything else, so the admin API and UI are only reachable through an SSH tunnel.
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
scp ops/gateway/set-smtp-credentials.sh ubuntu@<ip>:/tmp/
ssh -t ubuntu@<ip> "sudo bash /tmp/set-smtp-credentials.sh"
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

**The answers.** 200 `{"sent": n, "refused": n}` once Email Delivery has taken the message. 401 for a missing, unknown or inactive key; 429 for a key past its daily cap; 400 or 413 for a malformed or too large request. 502 when Email Delivery refused the message or couldn't be reached, 503 when Bifrost couldn't be asked about the key: a 5xx means "try again later", a 4xx "fix the request or the key". Each error has a short `error` saying why.

**The key.** The relay looks the key up in Bifrost's virtual-key list on localhost with the admin login, so a Household's one Gateway Key covers writing and sending the Brief, and revoking it (below) stops both.

**The cap.** Each key may send 4 messages a UTC day (`DAILY_CAP` in `brief_relay.py`): the Brief, Weekend Picks and room for a retry. At 10 Recipients each, one key can use at most 40 of the tenancy's 100 a day, so a leaked key can neither become a spam relay nor use up every other Household's sending. A message Email Delivery didn't take doesn't count. The counts are in `/var/lib/brief-relay/sent.json`. Before raising the cap or adding Households that send this way, check the total against the 100 a day.

**What it keeps.** Nothing of the message. Each request logs one line to the journal: the key's name, the number of Recipients, the request's size and the outcome (`journalctl -u brief-relay`), never the subject, body, attachments or addresses.

**Check it.** From a maintainer's machine, with a real Gateway Key, send a test with an `.ics` to a Gmail and a Hotmail inbox:

```sh
ics=$(printf 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Parent Recap//test//EN\r\nMETHOD:PUBLISH\r\nBEGIN:VEVENT\r\nUID:relay-test@parentrecap.com\r\nDTSTAMP:20261028T120000Z\r\nDTSTART;VALUE=DATE:20261030\r\nSUMMARY:Relay test\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n' | base64 | tr -d '\n')
curl -s https://<hostname>/v1/brief -H "x-api-key: $GATEWAY_KEY" -H 'content-type: application/json' -d '{
  "to": ["<gmail address>", "<hotmail address>"], "subject": "Relay test", "text": "Relay test.",
  "html": "<p>Relay test.</p>",
  "attachments": [{"filename": "events.ics", "content_type": "text/calendar", "data": "'"$ics"'"}]}'
```

## Hosted Households

The hosted version runs invited Households on this server, each as its own Linux user (ADR 0029, ADR 0031). A Household is made by the sign-up service from an Invite Link the maintainers send: the family opens it on their phone, picks Finnish, English or Chinese, types their Wilma town and login, checks the Recipients the link prefilled, and goes on to paste each Kid's MyClub calendar link, or leave it empty, and sign up.

**Install it.** Copy an unpacked release of the program (or a checkout) to the server beside `ops/gateway`, and run step 7 with `PARENT_RECAP_RELEASE` pointing at it:

```sh
scp -r <unpacked release> ubuntu@<ip>:/tmp/parent-recap
ssh -t ubuntu@<ip> "sudo GATEWAY_HOST=<hostname> PARENT_RECAP_RELEASE=/tmp/parent-recap bash /tmp/gateway/gateway-setup.sh"
```

It copies the release to `/usr/local/lib/parent-recap-signup/release`, readable by all; makes the service's user `parent-recap-signup` (home `/var/lib/parent-recap-signup`) and installs the program under it with the release's `install.sh`; installs the `household-user` helper and `invite.sh` beside the release, the sudoers file and `/etc/parent-recap-signup/signup.env`; and starts the `parent-recap-signup` unit (`parent-recap signup serve`) on `127.0.0.1:8082`, which reads Bifrost's admin login from `bifrost.env`. Running it again with a newer release upgrades the service; Households already made keep the program they were installed with.

**Invite a Household.** On the server, with the Recipients the family gave you and the language each reads the Brief in. It prints the link; send it over a private channel:

```sh
sudo bash /usr/local/lib/parent-recap-signup/invite.sh --recipient parent@example.com:fi --recipient partner@example.com:en --days 14
```

The link is a random code, kept with the Recipients in the service's owner-only store (`/var/lib/parent-recap-signup/signup/invites/`). It works once, until it expires, and a Household made from it retires it, Recipients and all. A link that isn't good, or any other address under `/join/`, gets the same 404 as the rest of the server.

**What a sign-up does.** The service signs in to Wilma with the login typed and reads the Kids before anything is made: a wrong password is said on the page, and the link stays good. The page then asks for each Kid's MyClub link; the checked answers, the Wilma password among them, wait for it in the service's memory only, for up to 30 minutes, after which the family signs in again. Each link given is downloaded once to check it, and one that doesn't open is said on the page; an empty one leaves MyClub off for that Kid. Then it makes the Household's user (`ph-` and 8 random hex digits, with a home only it can read, lingering so its timers run), mints its Gateway Key named after that user, installs the program under it with the release's `install.sh`, and runs `parent-recap signup household` as it with the answers, the Wilma login, the MyClub links and the key on its stdin. That writes the config (Kids from Wilma with their MyClub links, the Recipients, the gateway as AI and sender, Wilma on, WhatsApp off, the calendar as an `.ics` in the email), the wilma CLI's sign-in, the key as an owner-only file (ADR 0030), and the evening job. Last, it runs `parent-recap run --first-brief` as the user, which makes the first Brief from the last three days, as the setup page's first Brief step does, even when nothing is new, and sends it to every Recipient in their language from `brief@parentrecap.com`. Each Household's Brief comes in the next free slot from 21:00 in 5-minute steps, so the runs don't all start at once. A step that fails, the first Brief included, takes back the user with its home and jobs and the key, and the link stays good. The page then says the first Brief was sent and when the next ones come, and, when a Recipient is on Hotmail or Outlook.com, to look in Junk and mark it "Not junk" once. Only the user name and its evening time are kept in the store's `households.json`.

**Sudo.** The service's user may run, as root, only `household-user add|remove ph-…`, which refuses any other name, and anything as a member of `parent-recap-households`, the Households' group (`signup.sudoers`). A password or key never goes on a command line.

**What it logs.** One line a sign-up (`journalctl -u parent-recap-signup`): the Household's user and evening time, or why the sign-up wasn't taken. Never a request's address, which carries the code, or its body, which carries the Wilma password.

## Runbook

Open the admin API through a tunnel, with the admin login from the maintainers' password manager. Nothing below is run on the public address.

```sh
ssh -L 8080:localhost:8080 ubuntu@<ip>      # leave open
export ADMIN=<admin user>:<admin password>
```

**Create a Gateway Key and give it to a Household.** The name is a neutral label; who holds which key is kept only in the gateway's database, never in the repo. The reply's `virtual_key.value` (`sk-bf-…`) is the key: send it to the Household over a private channel and don't save it anywhere. Bifrost keeps it in plaintext and the key list returns it too, so a lost key can be read back, and the admin login (kept only in the maintainers' password manager) can read every Household's key.

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

**Create a send-only Gateway Key.** A Household whose AI backend is its own Claude Code or Codex still needs a Gateway Key for the gateway to send its Brief (ADR 0028). Give it a key with no `provider_configs` and no budget: Bifrost refuses its model calls (400 or 403) and the relay accepts it, since the relay only checks that the key exists and is active. Same naming, same handover:

```sh
curl -s -u "$ADMIN" -X POST localhost:8080/api/governance/virtual-keys \
  -H 'content-type: application/json' -d '{"name": "household-<label>", "is_active": true}'
```

**Revoke.** Find the key's `id` in the list, then turn it off (or `DELETE` it). `PUT` changes only the fields it is given (Bifrost has no `PATCH` here):

```sh
curl -s -u "$ADMIN" localhost:8080/api/governance/virtual-keys
curl -s -u "$ADMIN" -X PUT localhost:8080/api/governance/virtual-keys/<id> \
  -H 'content-type: application/json' -d '{"is_active": false}'
```

**Check spend.** `budgets[].current_usage` in the same list is what each key has spent today. The month's total is in the Anthropic Console, which also enforces the gateway workspace's $10 limit.

**Upgrade.** Try the new version on your own machine first (`npx -y @maximhq/bifrost --transport-version vX.Y.Z`). Then, in `gateway-setup.sh`, change `BIFROST_VERSION` and both `SHA256_*` lines (hash the binaries at `https://downloads.getmaxim.ai/bifrost/<version>/linux/<arm64|amd64>/bifrost-http`), and run step 7 again. The old version stays in `/usr/local/lib/bifrost/`; repoint the `current` symlink and restart to roll back.

**Rotate the provider key.** Create the new key in the Console first (in the gateway's workspace, whose monthly limit is the gateway's ceiling), then:

```sh
scp ops/gateway/set-provider-key.sh ubuntu@<ip>:/tmp/
ssh -t ubuntu@<ip> "sudo bash /tmp/set-provider-key.sh"
```

Make a test call, then delete the old key in the Console.

**Change the model.** Edit the `aliases` entry in `config.json` and run step 7 again.

**Check it is up.** The assistant's login can read logs and status: `journalctl -u bifrost -u brief-relay -u parent-recap-signup -u caddy`, `systemctl status bifrost brief-relay parent-recap-signup`.
