# The gateway's deployment config is public

The maintainers' gateway (ADR 0014) is set up from files in `ops/gateway/` in this repo, so they go public with it: the server's setup script, the web server and gateway config, and the runbook. Hiding its address would not protect it anyway: certificate transparency logs and internet scanners expose any public server, so the gateway's safety rests on its keys and config, not on a secret address. Public config lets parents and reviewers check that the gateway keeps no request or reply text and runs in the EU. And a scripted setup lets a maintainer rebuild the server quickly if it is lost.

## Considered Options

- **A separate private repo.** It would keep the server's details and caps out of view. Rejected: hiding the server is no security measure, and Households would have to trust a gateway whose logging they can't see.

## Consequences

- No secret goes in the repo: the provider's API key, the gateway's master key and every Gateway Key live only on the server. The repo holds a `.env.example` with names and no values.
- Which Household holds which Gateway Key is personal data and stays out of every repo, private or public. It lives only in the gateway's own database. The runbook can show how to give a Household a key, but never lists who got one.
- Request and reply logging is turned off in the committed config, so anyone reading it can see that.
- Which cloud account the server runs under, and how the maintainers log in to it, stays out of the repo. The maintainers keep that in their own private notes.
