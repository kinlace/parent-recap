# WhatsApp can be read as a Linked Device, built on Baileys

Superseded for the opt-in by ADR 0027: the Linked Device is the only way to read WhatsApp on macOS too. Baileys and the group filter still follow this ADR.

Reading WhatsApp for Mac's database needs the desktop app signed in, a logged-in session and Full Disk Access (ADR 0012), and on Windows there is no database to read at all. So a Household can instead read the WhatsApp Source as a Linked Device: Parent Recap pairs with the parent's account through a QR code and keeps the messages of the picked groups. It is opt-in on macOS, where the database stays the default, and the only WhatsApp route on Windows. It is built on Baileys, the WhatsApp Web library for Node, rather than wacli, because Baileys runs on the Node we already ship (ADR 0011) and lets us drop every chat but the picked groups before anything is written, so a family's private chats never reach the disk. Both held a pairing for 14 days with no warning from WhatsApp (#181); the privacy gain is worth the code we write ourselves (decided at the stand-up of 2026-10-26, #453).

## Considered Options

- **wacli**, a finished command-line client on whatsmeow (Go). It reconnects by itself and comes with its own store, but needs a Go binary per OS (macOS 15+, no Windows arm64), and its `sync` mirrors every chat, private ones included, before we could clean up. `--read-only` does not apply to `sync`.
- **The Mac database only.** No Terms-of-Service risk, but leaves Windows without WhatsApp and keeps the three macOS setup steps.

## Consequences

- WhatsApp's terms forbid automated access, so the account could be restricted. Our test (one group, 30 messages, no sends, 14 days) drew nothing, and the risk looks as low as for other third-party clients many people use. The opt-in says so in one line of small print, not as a warning, and setup does not suggest pairing a spare number.
- It is read-only, not invisible: Baileys sends delivery receipts and presence nodes whatever we set (#181, Q2). We send nothing else.
- Ours to write and audit: message storage, the group filter before the write, auth-state files with `0600`, a reconnect loop (about five drops a day; the library does not reconnect), and a re-pair flow when a link ends.
- The npm tree is pinned, at a Baileys release candidate for now.
- Switching to another client later means every family pairs again; the auth state does not carry over.
