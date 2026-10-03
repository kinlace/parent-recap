# The setup page takes secrets in its own fields

ADR 0005 sent every secret to a macOS dialog, so the AI running the chat setup would never see one, and it rejected a local web form as a web server for no gain. With the setup page (ADR 0006) the server already exists and no AI sits between the family and the form, so the page asks for the Gmail App Password and the Wilma username and password in its own fields (ADR 0008). The values go from the browser to Parent Recap on the same Mac and on to where they are kept today, and nothing else sees them. Fields in a page also let a password manager fill them, which a macOS dialog can't, and parents often don't remember their Wilma login. The chat setup keeps following ADR 0005.

## Considered Options

- **A button on the page that opens the macOS dialog.** Keeps one rule for both paths, but loses password managers and puts a second window in the middle of the page.
- **Reading the logins the browser has saved, with the family's consent.** Rejected: the browser's key opens every saved password, not only Wilma's, it looks like what malware does, and a parent can't judge that risk at setup time.

## Consequences

- The fields are plain username and current-password fields with no site look-alike, next to a button that opens the Passwords app, so a family can copy a login their password manager won't offer on this page.
- The Mac's own administrator password, which the wake schedule needs, still goes into macOS's administrator dialog. It opens every part of the Mac, so the page never handles it.
- The same rules as ADR 0005 apply on the server: a secret is never printed, logged, returned to the page or put on a command line, and tests check that a value typed into a field never shows up in output or logs.
