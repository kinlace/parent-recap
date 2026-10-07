# The evening job's Python gets Full Disk Access, not App Management

ADR 0011 had the family put Parent Recap's own Python under App Management and click Allow when macOS asks whether python3.x may access data from other apps. On macOS 26 that doesn't last: Allow lets that one read through, and the next run asks again, so the evening job stops on a question nobody answers and the Brief goes out without WhatsApp. Full Disk Access on the same Python reads WhatsApp with no question (#20). So the family grants Full Disk Access to the Python's real path, and setup and `doctor` count WhatsApp as readable only when a read gets through without macOS asking.

## Considered Options

- **App Management and Allow (ADR 0011).** Tested on the Mac mini (macOS 26.3.1, Python 3.13.16+20261003, under a launchd job): with App Management on and Allow clicked, every `bg doctor` run asked again; with Don't Allow, WhatsApp couldn't be read. Setup still passed, because its one read got through on that Allow.
- **`/bin/cp` with Full Disk Access, copying WhatsApp's database.** Still broader than our own Python: every launchd job that runs `/bin/cp` would get it.
- **A small program that only copies WhatsApp's database, with Full Disk Access.** It can do nothing else, and its path doesn't change when Parent Recap bumps its Python, so the family would grant once. It needs a compiled binary built and shipped for each architecture. Left for after the pilot.
- **A Developer ID signed `.app`.** As in ADR 0011, left for when there are more families.

## Consequences

- Anything that can start that Python, or write into `~/ParentRecap/app/.venv`, can read what Full Disk Access covers (Mail, Messages, other apps' data) when it runs. Accepted for the pilot; the single-purpose copy program is the way out.
- Every place that tells the family where to grant says Full Disk Access: the setup page and its pictures, `doctor`, `parent-recap app-management`, and the docs.
- A bump of the pinned Python still needs a new grant. #168 now checks whether Full Disk Access, rather than App Management, survives a Python replaced at the same path.
- Open: only tested on macOS 26.3.1, two runs, no restart in between. Whether macOS 15 behaves the same is not known.
