# A send-only Gateway Key is issued on request and bound to confirmed Recipients

Since ADR 0032 every Household needs a Gateway Key to send its Brief. Until now a maintainer made each key by hand in Bifrost's admin API and handed it over privately, and the parent pasted it into the setup page. With every invited family needing one, that is work for the maintainers at each invitation and a step that families get stuck on. So the program asks the gateway for a send-only key itself: when the setup page has the Recipients, it posts their addresses to a new endpoint on the gateway, which makes a send-only key bound to those addresses and mails each of them a confirmation link. Once a Recipient confirms, the relay sends to that address with the key; it refuses any other address, and caps the key at a few messages a day. The program stores the key as it stores a pasted one. An installed Household with no key gets one the same way on update. Keys that write the Brief (ADR 0014) still cost the maintainers money and stay handed out by the maintainers; an invitation code that the setup page trades for such a key is left for when a family needs one. Decided by Zhao on 2026-10-30.

## Considered Options

- **Maintainers make every key by hand (today).** Rejected: it costs the maintainers time at every invitation and update, and pasting a key is a step parents get wrong.
- **One shared send-only key built into the program.** Rejected: it would be public in the source, could send to any address, and couldn't be revoked for one Household.
- **A key on request with no confirmation.** Rejected: anyone could then make the gateway mail any address, which turns the relay into a spam source and harms the sending domain's reputation.

## Consequences

- The gateway gets a public endpoint that makes Bifrost keys, so it is rate-limited by address and by caller, and the confirmation mail is the only thing it sends before a confirmation.
- The relay checks each message's Recipients against the key's confirmed addresses, not only that the key exists. A changed Recipient on the setup page or in `manage` asks the gateway to bind and confirm the new address.
- The confirmation mail comes from the same unread address as the Brief, in the Recipient's language, and says who asked for it and how to ignore it.
- Maintainers still see every key in Bifrost, labelled as made on request, and can revoke it there.
