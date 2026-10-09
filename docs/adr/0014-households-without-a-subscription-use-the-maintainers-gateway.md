# Households without a subscription use the maintainers' gateway

Most pilot Households have neither a Claude nor a ChatGPT subscription, and creating their own API key (Console account, card, credit, key) is too high a bar. So invited Households get a Gateway Key to a gateway the maintainers run and pay for. The program still runs `claude -p`, pointed at the gateway with `ANTHROPIC_BASE_URL`, saved as its own `backend: gateway` so setup, doctor and `manage` treat it as a third choice. Each Gateway Key is capped at $0.10 a day.

## Considered Options

- **A raw Anthropic API key per Household, with a spend limit set in the Console.** Rejected: the key sits on the Household's Mac and works anywhere, and changing the model means touching every install.
- **A hosted version that runs the whole program for the Household.** Rejected for now: it moves all Sources, not only the AI calls, onto our servers.

## Consequences

- School mail and chat, after the AI filter, now pass through a server the maintainers run. The gateway keeps token counts and cost but no request or reply text, and the server is in the EU.
- Only invited Households get a Gateway Key, created by hand. There is no sign-up.
- Switching the model, for example to Haiku once #217 passes, is a change in the gateway's config, not a release.
