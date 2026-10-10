# The setup page signs in to Wilma by writing the Wilma CLI's profile

Parent Recap reads Wilma through the Wilma CLI (`@wilm-ai/wilma-cli`), which signs in only on its own interactive screen in Terminal. In the first rehearsal that screen was the hardest part of setup: a tenant search with no hint, a long list, a student picker and an error. The CLI has no flag, variable or input for signing in from another program, but every command reads its saved profile from one JSON file. So the setup page asks for the town and the Wilma username and password itself, writes that profile file the way the CLI would, and then runs `wilma kids list --json` to check that the sign-in works. The town list comes from Wilma's public tenant list, which the CLI ships too.

## Considered Options

- **A button that opens the Terminal sign-in window** (the 0.4.3 window, which guides the family and ends the CLI once signed in). Uses only the CLI's own interface, but sends the family out of the page for the hardest step and password managers can't fill a Terminal prompt. Kept as the fallback.
- **Ask the CLI's author for a non-interactive sign-in.** Worth doing, but it can't be counted on for 0.5.0.

## Consequences

- Parent Recap depends on a file format the CLI doesn't document. It was the same from 1.4.2 to 2.1.2. Setup pins the CLI version it installs, a test checks the profile written against the pinned version, and if the check after writing fails, the page offers the Terminal window instead. From 2.0 the CLI's commands read every profile in its config, so the page checks the new profile alone, in a temporary config the CLI reads through `WILMAI_CONFIG_PATH`, before adding it to the CLI's own.
- The password ends up where the CLI keeps it today: in its owner-only file, lightly encoded, not encrypted. This is no change from signing in on the CLI's own screen.
