# A hosted version runs invited Households on the maintainers' Linux server, without WhatsApp

Several Households the maintainers want to invite have no Claude or ChatGPT subscription and no computer that stays on; they use their phone. Until now the program ran only on a Household's own Mac or Windows PC (ADR 0019), and a hosted version was rejected in ADR 0014 because it moves every Source onto our servers. We now build one: the program gets a Linux build, and the maintainers run it for invited Households on the Oracle Cloud server the gateway already runs on (ADR 0014, ADR 0016), sized for the first 10 to 15 test Households, which costs nothing extra at that size. A Hosted Household reads Wilma, MyClub and, later, Gmail, has its Brief written through the gateway and sent from Parent Recap's own address (ADR 0028), and has no WhatsApp: hosting the Linked Device would mean holding a session that reads all of a parent's chats and sends as them (ADR 0026, ADR 0027), far heavier than holding a Wilma login. A Household that misses WhatsApp moves to running the program on its own computer, which stays the way for Households that want their messages to stay home. Decided at the stand-up of 2026-10-30 (#551).

## Considered Options

- **Stay local only, with "No computer? Run it for me" as a demand test first (#551).** Not run: the Households the maintainers already want to invite are the demand, and at 10 to 15 of them the build is cheaper than a test wave.
- **A per-Household container or microVM on a managed host (Fly, Cloud Run, Fargate; #551's research).** Rejected for the first version: at 10 to 15 Households one Linux user per Household on the server we have gives enough separation, and a managed host would be the first cloud bill. It stays the way to grow past the server's capacity, about 25 to 30 Households by the maintainers' estimate.
- **Hosting the Linked Device too.** Rejected, as above; also, many linked devices from one datacenter address are closer to what WhatsApp's terms forbid than one at a family's home (ADR 0025).
- **Running Households' own Claude or ChatGPT logins on the server.** Rejected: Anthropic's published policy forbids collecting or routing through consumer-plan credentials on users' behalf, and OpenAI's steers automation to API keys. Hosted means the gateway's API key, which is what a Gateway Key already is.

## Consequences

- The maintainers hold, for a Hosted Household, its Wilma login, its MyClub links, its config and its archive of Briefs, on a server they administer. This is a larger step than the gateway's (ADR 0014), where nothing of a Household's messages is stored: the maintainers become the processor of children's school messages under GDPR. SECURITY.md gets a section saying exactly what is held, where, and for how long, and only invited Households are hosted; there is no sign-up.
- The program gets a third system, Linux, behind the OS-services interfaces (ADR 0020), and a Linux install. The Linux profile offers no WhatsApp step, no AI choice (the gateway is the AI) and no Gmail step at first.
- The server's capacity is shared with the gateway: each Household's run gets CPU and memory limits so a runaway run can't starve the gateway, which every Mac and Windows Household's Brief goes through too.
- OCI Email Delivery's cap of 100 messages a day (ADR 0028) is the first hard ceiling, at about 65 Households.
- Wilma is signed in to from one datacenter address for every Hosted Household. Whether Visma tolerates that is unknown; the maintainers' own Household goes first.
