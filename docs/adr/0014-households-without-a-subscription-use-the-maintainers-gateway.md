# Households without a subscription use the maintainers' gateway

Most pilot Households have neither a Claude nor a ChatGPT subscription, and creating their own API key (Console account, card, credit, key) is too high a bar. So invited Households get a Gateway Key to a gateway the maintainers run and pay for. The program calls the gateway's Anthropic-format `/v1/messages` itself, from its own Python or Node, so the Household installs neither Claude Code nor Codex. It is saved as its own `backend: gateway` so setup, doctor and `manage` treat it as a third choice; the `claude` and `codex` backends keep running their CLIs, which setup has already checked are installed and signed in. Each Gateway Key is capped at $0.10 a day.

## Considered Options

- **A raw Anthropic API key per Household, with a spend limit set in the Console.** Rejected: the key sits on the Household's Mac and works anywhere, and changing the model means touching every install.
- **A hosted version that runs the whole program for the Household.** Rejected for now: it moves all Sources, not only the AI calls, onto our servers.

## Consequences

- School mail and chat, after the AI filter, now pass through a server the maintainers run. The gateway keeps token counts and cost but no request or reply text, and the server is in the EU.
- The maintainers become a processor of the Households' personal data under GDPR. So before any Household uses the gateway, SECURITY.md and the setup page say what passes through it and what it keeps.
- Only invited Households get a Gateway Key, created by hand. There is no sign-up.
- Switching the model, for example to Haiku once #217 passes, is a change in the gateway's config, not a release.
- The gateway backend has its own model call, so it needs its own handling of busy, timeout and out-of-credit replies from their HTTP status, instead of the CLI output the other two backends are read from.
