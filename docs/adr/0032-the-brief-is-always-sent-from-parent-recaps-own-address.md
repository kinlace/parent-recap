# The Brief is always sent from Parent Recap's own address

ADR 0028 let a Household have its Brief sent from Parent Recap's own address through the gateway, and kept sending through the Household's own Gmail over SMTP as the default. We now drop Gmail sending: every Household's Brief, its first Brief from setup and its Weekend Picks are sent through the gateway from `brief@parentrecap.com`. Gmail is only a Source, read with the App Password for mail from the senders the family picks. A Household that reads no mail needs no Gmail at all. Agreed by the maintainers at a stand-up and confirmed by Zhao on 2026-10-30.

## Considered Options

- **Keep Gmail as the default and the gateway as the way out (ADR 0028).** Rejected: two transports mean two setup paths, two sets of failures in `doctor` and two explanations for parents. Parents also read the App Password as being "for sending", and then don't expect the program to read their mail (#602). With one transport the Gmail step is only about reading.
- **Gateway by default, Gmail sending kept as an option.** Rejected for the same reasons: the option would still need its setup path, its tests and its help text, for a choice no parent has asked for.

## Consequences

- Every Household needs a Gateway Key, including those whose AI is their own Claude Code or ChatGPT; the maintainers hand one out with each invitation. Supersedes ADR 0028's "Sending through the Household's own Gmail stays the default".
- The Brief's text and its Recipients' addresses pass through the gateway and the mail service for every Household, as ADR 0028 describes for the gateway path; SECURITY.md and the setup page say so for everyone.
- The email Action loses its Gmail SMTP transport, the setup page loses the "how is the Brief sent" choice, and `doctor` checks only the gateway for sending. An existing Household set to send through Gmail moves to the gateway on update, once it has a Gateway Key.
- Replies to the Brief reach no one; the Brief already says who to write to instead (ADR 0028).
