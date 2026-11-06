# A Kid's MyClub links stay under one key, as a string or a list

A MyClub calendar subscription link holds a Kid's groups in one club, so a Kid in two clubs, or a family whose Kids' clubs are on different MyClub sites, needs several links (#810). Until then each Kid had one link, `myclub_ical_url`, a string, in every config written so far, including the Hosted Households' on the server. The key now holds either one link, as before, or a list of links, one per club. The program reads both as a list; it writes one link as a string, so a family with one club keeps the config it has and an older program still reads it, and several as a list, in flow style (`["webcal://…", "webcal://…"]`) where the program edits the file in place, so the links stay on the key's own line.

## Considered Options

- **A new list key, `myclub_ical_urls`, with the old one still read.** Rejected: two keys for one thing means deciding which wins when both are there, and every writer would have to move a Kid's link from one to the other. The plural name reads better, but a config that holds one link stays exactly as it is under the old key.
- **Always writing a list.** Rejected: it would change every family's config the first time a link is saved, for nothing, and a program older than #810 couldn't read it.

## Consequences

- An older program can't read a config with a Kid's several links: it says the config is wrong until each Kid has one link again.
- A link's place among a Kid's links is how the setup page names it, to remove it, since the page never has the link itself (ADR 0007).
- Viewing the config without its links now needs `grep -vE 'myclub_ical_url|myclub\.fi/'`, as the skills say, since a whole-file rewrite (`setup save`) writes a list one link a line.
