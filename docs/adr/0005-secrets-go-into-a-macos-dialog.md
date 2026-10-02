# Setup asks for secrets in a macOS dialog, not in the chat or a Terminal window

Setup needs a few secrets: the Gmail App Password, the Claude token and each Kid's MyClub calendar link. They must never pass through the chat, where the AI would see them and the transcript would keep them. Up to 0.4.1 the family typed each one into a script in their own Terminal window. That kept secrets out of the chat, but every secret meant opening a new window and copy-pasting a command, which testers found the slowest and most confusing part of setup. From the one-click setup on, the setup command asks for the secret itself in a native macOS dialog with hidden input, checks it where it can (a Gmail sign-in, a test call), stores it, and prints only that it was saved. The assistant runs the command and never sees the value.

Two steps stay as they are. Wilma's sign-in stays in Terminal, because the Wilma CLI only signs in on its own interactive screen. The wake schedule's `sudo pmset` moves to macOS's administrator password dialog, which is the same idea for the Mac password.

## Considered Options

- **Type the secret in the chat.** The fastest for the family, but the AI sees it and the transcript keeps it. Ruled out from the start.
- **The family's own Terminal window** (up to 0.4.1). Safe, but slow, and families lose track of which window is which.
- **A small local web page with a form.** Works, but adds a web server, a port and a browser tab, for no gain over a dialog the Mac already has.

## Consequences

- No setup command may print, log or return a secret, or put it on a command line where `ps` would show it. It is handed to the Keychain (or written to the owner-only config, for MyClub links) directly. Tests check that a value entered in the dialog never shows up in the command's output.
- A dialog needs a desktop session. When there is none (for example over SSH), the command falls back to a hidden-input prompt in Terminal, as before.
- In Codex, the command runs outside the sandbox, after the approval Codex asks for.
- `SECURITY.md`'s table of where each secret lives and how it's typed changes with this.
