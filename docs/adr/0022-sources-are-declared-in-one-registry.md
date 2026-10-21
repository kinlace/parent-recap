# Sources are declared in one registry

Up to 0.6.2 the program has no single list of its Sources. The evening run, the Brief's coverage line, the AI filter report, the setup page, `doctor` and uninstall each keep their own table of Source names, and whether a Source is connected is decided afresh in each of them. Adding MyClub touched about 25 files, and nothing tells a developer which branch they missed. From the next refactoring, every Source is one record in a registry: its name and label, how to tell it is connected, how to collect, what `doctor` checks, what uninstall clears, and the hooks the Brief needs that differ per Source (whether a message was sent to everyone, how a Held-back Message is labelled and linked). The run loop, `doctor`, the catch-up lookback, the coverage line and uninstall iterate the registry rather than naming Sources; the setup page reads the list and its order from it. A new Source is then its collector module, its config model and its registry record, plus its own panel on the setup page.

## Considered Options

- **A `Collector` base class every Source subclasses.** Covers collecting only; the setup, `doctor` and uninstall knowledge would stay where it is. The registry record carries all of it, and a collector stays a module with one `collect` function.
- **The registry also describes the setup page's panel.** Rejected: each Source's panel differs (a password, a pasted link, a list of groups to tick), so a generic description would either be as long as the panel or hide the differences. The page's HTML, script and text stay hand-written per Source.

## Consequences

- Every Source collects the same way and returns both messages and events, so an event feed such as MyClub is no longer a special case in the run loop or in the test harness.
- The per-Source branches in the Held-back logic (ADR 0013) move into the record, with a default that holds a message back when in doubt.
- Tests fake a Source at its transport (IMAP, HTTP, the Node CLI, the local store), one fake per record, instead of replacing `collect` wholesale.
