# The gateway is Bifrost

The maintainers' gateway (ADR 0014) runs the open-source Bifrost. It serves Anthropic's `/v1/messages`, which the program calls directly, and its Apache-2.0 version caps each Gateway Key with a budget that resets daily. It is a single Go binary that keeps its config in SQLite, so it fits the 1 OCPU / 6 GB server without a separate database. Comparison in #227.

## Considered Options

- **LiteLLM.** Meets the same needs and has far more users. Rejected: it is Python and needs Postgres, which is heavier to run and keep patched on a small server, and it has had security issues of its own.
- **Portkey gateway.** Rejected: no code pushed since May 2026 and no release since January 2026.

## Consequences

- Bifrost started in March 2025, so it has fewer users than LiteLLM and fewer known answers when something breaks. Upgrades are pinned and tried before they reach the server.
