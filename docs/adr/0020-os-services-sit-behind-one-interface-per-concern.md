# OS services sit behind one interface per concern, with an adapter per OS

Secrets, the evening jobs, waking the computer and opening pages, files and terminal windows each go through their own module under `family_brief/system/`, with one adapter per operating system; callers never run an OS tool such as `security`, `launchctl`, `pmset` or `open` themselves. Windows needs a second adapter for each, and Linux may need a third, so the seams are real rather than speculative. We split by concern rather than one `Platform` object so each adapter can be built, tested and replaced on its own, and we shape the interfaces from the macOS and Windows adapters we have, not from a guess at Linux.

## Consequences

- The macOS code moves behind the interfaces first, with no change for families: today's tests, which fake the macOS commands, must keep passing unchanged.
- Features that only exist on macOS (WhatsApp for Mac's database and Full Disk Access, iMessage) are not interfaces; the setup page and `doctor` offer them only on macOS, checked in one place.
- Each interface gets an in-memory fake, so tests of the setup page, `doctor` and uninstall can run on Windows too.
