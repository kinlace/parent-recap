# The Brief can be sent from Parent Recap's own address

Today the Brief leaves the Household's own Gmail over SMTP, with the same App Password the Gmail Source reads the inbox with. A Household without a usable Gmail can't finish setup: a Gmail made for Parent Recap fails Google's verification for days (#485), and some Finnish parents only have Hotmail or a local provider. So a Household may instead have the Brief sent from Parent Recap's own address: the program posts each Brief, with its Recipients' addresses, to an endpoint on the maintainers' gateway (the same server and Household authentication as ADR 0014), which hands it to a transactional mail service and keeps nothing. Any mailbox can receive it. Sending through the Household's own Gmail stays the default; the gateway is the way out of the Gmail step for Households that read no mail (Wilma, MyClub or WhatsApp only), who then need no Gmail at all. Reading mail stays Gmail-only with the App Password; another provider's inbox (Hotmail needs OAuth) gets its own Source only when a Household needs it.

## Considered Options

- **A mail server of our own on the gateway host (Postfix).** Rejected: the cost is not building it but getting mail delivered. OCI blocks outbound port 25 unless a ticket is granted, and a cloud IP with no history is rejected or junked by Microsoft, the very provider the Households without Gmail use. Keeping SPF, DKIM, DMARC, reverse DNS, bounce handling and blocklists healthy is ongoing work that a transactional service does for us at the pilot's volume for free (Resend: 3,000 a month, 100 a day on the free plan at 2026-10-27; Amazon SES and Postmark are equivalent).
- **A newsletter or list service.** Rejected: the Brief is one message to one Household, sent by an API call carrying that Household's addresses; there are no lists to keep in step with configs.
- **Forwarding the Household's school mail to a Parent Recap inbox, so that reading needs no App Password either.** Rejected: the maintainers would hold the Household's school mail, against the principle that nothing of the Household's messages stays on our servers.

## Consequences

- The Brief's text passes through the gateway and the mail service in transit. The gateway keeps delivery status at most, never the Brief. The mail service must be one whose retention of message bodies can be turned off or is zero; Resend's free plan keeps data for 30 days by default, so the ticket that picks the service checks and configures that. SECURITY.md's "where data goes" and the setup page say which Households send this way and what passes through.
- Sending needs a Gateway Key, so only invited Households (ADR 0014) can use it, whatever AI backend they chose. The key's purpose widens from "writing the Brief" to "the maintainers' gateway does this for the Household".
- `parentrecap.com` gets SPF, DKIM and DMARC records for the service; the sender is an address at that domain that no one reads, and the Brief says so.
- The email Action gets a second transport beside Gmail SMTP, chosen by config, and doctor checks whichever is chosen.
- The Household's Gmail becomes optional for setup: with the gateway sending and no mail Source, the setup page's Gmail step is skipped.
