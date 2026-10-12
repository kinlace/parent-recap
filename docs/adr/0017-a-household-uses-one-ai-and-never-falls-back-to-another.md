# A Household uses one AI and never falls back to another

A Household's Briefs are written by exactly one AI, the `llm.backend` it chose (`claude`, `codex` or `gateway`), even when the Mac also holds a sign-in or a Gateway Key for another. When that AI fails, the night's Brief fails through the usual failure notice, which with `doctor` points the parent to switching, and the setup page lets them switch or enter a new Gateway Key after setup is done. We don't fall back automatically because the gateway sends the Household's messages through the maintainers' server (ADR 0014), which a Household on its own subscription never agreed to, the maintainers pay for every gateway call, and each Brief should have one known writer when something in it is wrong.

## Considered Options

- **Fall back to the gateway, or from it, whenever the chosen AI fails.** Rejected for the reasons above.
- **An opt-in fallback the parent ticks.** It answers consent and cost, and keeps the Brief coming when a subscription lapses. Rejected for the pilot: once the setup page offers the switch, it takes a minute, and a second path doubles what `doctor` and the failure notice must explain.
