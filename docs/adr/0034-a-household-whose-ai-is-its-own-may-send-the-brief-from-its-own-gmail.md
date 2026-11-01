# A Household whose AI is its own may send the Brief from its own Gmail

ADR 0032 sends every Brief through the gateway from `brief@parentrecap.com` and rejected keeping Gmail sending as an option. We now bring that option back for one kind of Household, as an advanced setting: a Household whose AI is its own Claude Code or Codex (not the gateway, ADR 0014) and that reads its Gmail with an App Password may switch its Brief, its first Brief and its Weekend Picks to be sent from that Gmail over SMTP, with the same App Password. Sending through the gateway stays the default, and the setup page's main path still asks nothing about sending. Decided by Zhao on 2026-11-01, after a pilot parent rebuilt Gmail sending in a fork of their own.

The reason is privacy. For such a Household the Messages are read and the Brief is written on its own computer, and the gateway is the only step where the Brief's text, with the Kids' names and the school's news, leaves it for a server the maintainers run. A parent who chose their own AI to keep that data at home should be able to keep it all at home.

## Considered Options

- **Gateway only (ADR 0032).** Rejected for these Households: it makes the maintainers' server see every Brief of a family that chose to keep its data on its own computer, and a pilot parent cared enough to rebuild Gmail sending themselves.
- **A choice on the setup page's main path for everyone (ADR 0028's way).** Rejected: two transports on the main path bring back ADR 0032's problems, two setup paths and parents reading the App Password as "for sending" (#602).
- **Own Gmail for Households whose AI is the gateway too.** Rejected: their Messages already go through the gateway to be written up, so sending elsewhere gains no privacy and splits the support.

## Consequences

- The setting is offered only when the AI isn't the gateway and Gmail is connected with an App Password: on the setup page's Finish ("More settings") and through `manage` in the chat, never as a question during setup. Turning it off goes back to the gateway.
- The email Action gets back a Gmail SMTP transport beside the gateway one, chosen by the config; `doctor` checks the transport the Household uses, and the page's Check and Finish say which it is.
- Such a Household needs no send-only Gateway Key and its Recipients confirm nothing (ADR 0033 applies only to gateway sending). A key it already holds stays stored, unused, so switching back needs no new confirmation.
- The Brief comes from the parent's own address: replies reach the parent, the "nobody reads this address" line is left out, and each Brief stays in that Gmail's Sent folder. Hotmail's junk note for Parent Recap's address doesn't apply.
- If Google stops accepting the App Password, both reading and sending stop; `doctor` and the Brief's coverage note say so, as for reading today.
- The maintainers don't see these Households' sending: the gateway's daily mail count and its alerts leave them out.
- SECURITY.md and the setup page's privacy text say that with this setting nothing leaves the computer except to the family's own AI provider, Gmail and the Sources.
- ADR 0032 stays in force as the default and for every other Household.
