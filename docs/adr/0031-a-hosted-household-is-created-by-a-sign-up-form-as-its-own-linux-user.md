# A Hosted Household is created by a sign-up form on the maintainers' server, as its own Linux user

Superseded for where the program is installed by ADR 0035: every Hosted Household runs one shared copy, so a redeploy upgrades them all. Its own user, sign-up and data still follow this ADR.

The setup page (ADR 0006) runs on the Household's own computer, bound to localhost with a one-time code, and walks through steps a Hosted Household doesn't have: the AI, Gmail, WhatsApp, the wake-up, the chat. A Hosted Household instead signs up on one page served from the maintainers' server under an Invite Link, which the maintainers mint and send. The page asks only what the maintainers can't know: the language, the Wilma town and login, the Recipients' addresses and languages, and optionally each Kid's MyClub link. The service behind it signs in to Wilma with that login and reads the Kids before anything is created, so a wrong password is said at once; then it creates the Household's own Linux user, mints its Gateway Key, writes its config and the wilma CLI's sign-in, installs its evening job with CPU and memory limits, has the first Brief made and sent to the Household, and retires the Invite Link. The family pastes no key: the Invite Link stands for the Household. Decided 2026-10-30.

## Considered Options

- **The setup page itself, one instance per Household behind the web server, under a lasting link.** Rejected: it was written for a desktop browser on the family's own computer, its Host and Origin checks and idle stop are for that, and most of its steps would be hidden on Linux. A page of its own is smaller than the page bent to fit.
- **The maintainers run setup over SSH with the family on a call.** Rejected for invited Households: the Wilma password would pass through the maintainers. Fine for the maintainers' own Household and the first internal check.
- **One shared Unix user for every Hosted Household, with folders per Household.** Rejected: the program is built as one Household per OS user (home folders, the wilma CLI's config, one scheduled job), and a user per Household makes the OS keep them apart and lets each job carry its own limits.

## Consequences

- The service takes passwords from the internet and creates users, so it is reachable only over HTTPS, an Invite Link works once, no request body is logged, a password never reaches a log or a command line, and the Wilma check gates everything.
- The service lives in the program, as a subcommand, so it reuses the Wilma sign-in, config and MyClub modules; the server-side pieces (its systemd unit, the web server's route, the script that mints an Invite Link, the one that removes a Household with its data) live beside the gateway's.
- Creating and removing a Linux user, running a command as it and minting a Gateway Key on the gateway's admin API go behind one interface with an in-memory fake, so the service is tested without a root shell.
- A Hosted Household has no way to change its own settings at first: the maintainers edit the config. A change form comes with more Households.
- The first Brief is emailed rather than previewed on the page, so the service needs no long-lived session with the browser.
- Amended 2026-11-04 (#760, after a pilot family at a private school had no Wilma): Wilma is optional at sign-up. A family without it leaves the Wilma fields empty and types the Kids' names, and the MyClub step then needs at least one link, since that is all the Brief would read. Such a Household has Wilma off and no Wilma sign-in, and the Wilma check gates a sign-up only when Wilma is given.
  Further, 2026-11-05 (#769, for the same family's music school): the MyClub step also takes one Eepos sign-in (ADR 0038), and without Wilma either a MyClub link or the Eepos sign-in is enough. The service checks it as `setup eepos` does, signing in and matching the students to the Kids by name, before anything is made, so a wrong password is said at once and the link stays good; `signup household` gets its password on stdin with the other answers and keeps it in the Household's secret store.
  Further, 2026-11-05 (#790): the MyClub step also takes one eVaka sign-in (ADR 0039), checked the same way before anything is made, and without Wilma a MyClub link, the Eepos sign-in or the eVaka sign-in is enough.
