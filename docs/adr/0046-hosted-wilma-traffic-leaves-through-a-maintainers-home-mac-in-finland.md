# Hosted Wilma traffic leaves through a maintainer's home Mac in Finland

The Helsinki and Vantaa Wilmas answer a request from outside Finland with a Cloudflare challenge the wilma CLI can't pass, so from the server in Stockholm a Hosted Household with a Kid in those cities couldn't sign in or be read (#856). For now the server sends its Wilma requests, the sign-up's and the evening runs', through an HTTP CONNECT proxy on Xi's Mac mini at home in Finland, reached over the maintainers' tailnet. The proxy passes only connections to `*.inschool.fi` on port 443, and only from the server. TLS runs end to end between the wilma CLI and Wilma, so the Mac relays ciphertext: it sees which Wilma is contacted and when, never a password, session or message. Everything else, the data included, stays on the server in Stockholm. All `*.inschool.fi` traffic goes this way, not only Helsinki's and Vantaa's, so a city that adds the same check later is covered; when the proxy can't be reached, the wilma CLI goes direct, which still works for a Wilma without the check. Then the family's Brief says only that Wilma couldn't be reached and the maintainers know, and the nightly check names the cause for the maintainers. Decided by Xi and 赵晗博 on 2026-11-10.

## Considered Options

- **A small VPS in Helsinki as the egress** (Hetzner, UpCloud). Rejected for now, not ruled out: it costs a few euros a month and is one more server to keep. It is the move once the Mac proves unreliable or the hosted version grows.
- **Moving the server, or the evening job, to Finland.** Rejected: Oracle's Always Free A1 has no Finnish region, and moving the data needs a new home for it, the backups (ADR 0037) and SECURITY.md's account of where it lives.
- **Telling families in Helsinki and Vantaa the hosted version can't read their Wilma.** Rejected as the answer: those are two of the three cities our families are in, and the hosted version is what we offer families without a computer.
- **Only Helsinki's and Vantaa's traffic through the Mac.** Rejected: each newly blocked city would need a change, found only when a family's sign-in fails.

## Consequences

- Hosted Wilma for Helsinki and Vantaa depends on one home connection and one Mac being up. When either is down that night, those Households' Briefs come without Wilma.
- Wilma sees every Hosted Household's sign-in from one Finnish home address, not the datacenter's.
- The Mac's owner can see which Wilma the server contacts and when, though not for which Household, and could block it; the Mac holds no Household's data.
- SECURITY.md says the Wilma requests leave through this proxy, and why.
