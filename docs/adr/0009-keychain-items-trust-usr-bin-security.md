# Keychain items trust /usr/bin/security, and every secret is written and read with it

Setup stored secrets with Python's `keyring`, so each Keychain item trusted only the venv's Python, while the evening job read them with `/usr/bin/security`. macOS then asked for the login Keychain password on every read, nobody answered it at night, and the job ran without the stored Claude token (#148, #150). Every secret (the Claude token, the Anthropic API key, the Gmail App Password) is now written with `security add-generic-password -U … -T /usr/bin/security`, typed into `security -i` on stdin so it's on no command line (ADR 0005), and read with `security find-generic-password -w`.

## Considered Options

- **Read through `keyring` too, the program that wrote the item.** No prompt today, but the item trusts the venv Python's real path, which changes when Homebrew or uv updates Python, and the prompt comes back in the evening job (the same thing as WhatsApp's permission, #20).
- **A longer read timeout.** Doesn't help: the prompt waits for a person.

## Consequences

- An item an earlier version stored keeps its old access list. Setup deletes it before storing the new one, and `doctor` tells such an item ("macOS asks for the Keychain password") apart from a missing one and says to store it again.
- Reads give up after a few seconds, so a read that times out or is refused is reported as one that needs a prompt, never as a missing secret.
- The program no longer depends on `keyring`.
