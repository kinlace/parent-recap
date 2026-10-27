# The Linked Device is the only way to read WhatsApp

ADR 0025 made the Linked Device an opt-in beside the WhatsApp for Mac database on macOS. The database route costs a family the three hardest setup steps on a Mac (WhatsApp for Mac installed and signed in, a desktop session, Full Disk Access for the evening job's Python), and Full Disk Access is needed for nothing else. So the Linked Device replaces it: on macOS and Windows alike, the setup page offers only the Linked Device, and the database route, the Full Disk Access step and their `doctor` checks are removed from the program rather than kept as a hidden fallback (decided at the stand-up of 2026-10-27, #504). Mac Households already reading the database are switched over at the next release and pair again: there are a handful of pilot families, and their pairing is the first real test of the new route.

## Considered Options

- **Keep both, database as default** (ADR 0025 as written). Every Mac family still meets Full Disk Access unless they find the opt-in.
- **Keep both, Linked Device as default.** Two routes to test, document and support for a database route nobody new would pick.
- **Linked Device only, database code kept as a hidden fallback.** Code no family can reach, with tests to maintain. Git has it if it is ever needed.

## Consequences

- Full Disk Access leaves the product: the setup page, `doctor`, `parent-recap full-disk-access`, the pictures and the docs. ADR 0012 is superseded.
- Every family takes the Terms-of-Service risk of ADR 0025; there is no risk-free way to have WhatsApp in the Brief. The small print stays.
- Baileys is the single path to WhatsApp on every OS. A change on WhatsApp's side stops WhatsApp for every family at once, and the re-pair flow is the only lever.
- Bringing the database route back means recovering it from git history (last on `main` before this ADR landed), not flipping a flag.
