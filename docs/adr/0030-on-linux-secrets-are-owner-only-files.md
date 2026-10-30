# On Linux, secrets are owner-only files

The secrets interface (ADR 0020) has the Keychain on macOS and Credential Manager on Windows behind it. A Linux server has neither: the usual keyring (libsecret) needs a desktop session and a daemon, which a server's job user doesn't have. So on Linux each secret is a file under the Household's own config folder, readable by that user only, as the wilma CLI already keeps the Wilma login (docs/sources.md). This gives every secret of a Hosted Household the same protection the Wilma password has today, no more, and SECURITY.md says so plainly.

## Considered Options

- **libsecret with a headless keyring daemon per user.** Rejected: a daemon to keep running and unlock per Household, for a store that is itself a file on the same disk, unlocked by a key on the same disk.
- **systemd's credentials (`systemd-creds`), encrypted with the host's key and handed to the job.** The step up when the maintainers want a stolen disk or backup alone to expose nothing: the key then lives in the host's TPM or `/var/lib/systemd`. Left for later; the interface doesn't change.

## Consequences

- Who can read the server's disk as root can read every Hosted Household's secrets. Accepted for the test Households; the server has disk encryption only as Oracle provides it.
- `doctor`'s and the setup page's "where the secret is" lines name the file on Linux, as they name the Keychain and Credential Manager elsewhere.
