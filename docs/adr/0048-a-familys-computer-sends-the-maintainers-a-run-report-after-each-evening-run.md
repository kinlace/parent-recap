# A family's computer sends the maintainers a Run Report after each evening run

When a Brief is late or missing on a family's own computer, the maintainers can only guess: they ask the parent to type commands, read a log and send screenshots (#901), which the families we build for can't do. Now the program tells them itself. After each evening run it sends the maintainers' gateway a Run Report: how the run ended, in fixed words and numbers and nothing of the Household's. And when a parent wants to send more, Parent Recap Check has a button that shows a Problem Report and sends it only when they press Send. The Run Report is on for every Household, new or already installed, and the setup page says so with a switch to turn it off. Decided by Xi on 2026-11-12.

A program that sends data home in the background is what spyware does, and many families have a coding agent read the program before they trust it (the same concern as ADR 0047). So the Run Report is built to be read:

- It holds only fields the program names in one place: the version, the system and its version, when the run ended and how it was started, its exit code, duration and peak memory, each Source's result (read, with a count, or failed, with a cause's fixed key such as `wilma-egress`), the AI's result, the Brief's outcome (sent, nothing to tell, or failed with a cause's key) and `doctor`'s mark per item. No free text ever: no error message, log line, name, address, subject or path. An exception is its type and the line of Parent Recap's own code, without its message, which can quote a Message.
- The gateway refuses a report with a field or a value outside that list, so a later version can't send more without the server and this ADR changing too.
- The program writes each report to a file in its folder before sending it, so a parent, or their agent, reads exactly what left.
- It goes to the gateway's address, the one the Brief already goes to each evening, with the Household's Gateway Key, from inside the program: no new host, no shell, no PowerShell or `curl` (#591).
- It is stated on the setup page, in the README and in `SECURITY.md`.

## Considered Options

- **Send the log.** Rejected: the evening run's log can quote mail and message text, and no filter over free text can promise it doesn't.
- **Off until the family turns it on, asked once on the setup page.** Rejected by Xi: most Households would leave it off, installed ones all of them, and those are the families whose missing Briefs we can't explain today. For a Household on the gateway the server already handles every Brief, and the Run Report says far less than a Brief does. Shown with a switch at setup, it is a stated choice, not a hidden one.
- **Tell installed Households in the first Brief after the update.** Rejected by Xi: they are two or three test families, whom the maintainers tell directly.
- **Leave out Households that send the Brief from their own Gmail (ADR 0034).** Rejected by Xi: what those families keep at home is their Messages and their Brief, and the Run Report holds neither. Theirs are also the runs the gateway otherwise never hears of.
- **Ask the family before each report (a dialog or an email).** Rejected for the Run Report: the evening run has nobody at the computer, and a report that waits for a click doesn't come on the evenings that matter. It is how the Problem Report works.
- **A ready-made service (Sentry, PostHog).** Rejected: a second company would get the data, its client collects free text by default (error messages, paths), and it is one more host for a firewall and a reader of the code to ask about.
- **A GitHub issue the family's own AI writes from the log.** Rejected: it needs a GitHub account and an AI session, and puts a summary of a private log in a public place.

## Consequences

- This narrows what ADR 0034 promised a Household that sends its Brief from its own Gmail. `SECURITY.md` and the setup page said that nothing leaves its computer except to its own AI provider, Gmail and the Sources; they now say that none of its Messages and no Brief reach the maintainers' server, and that a Run Report does unless it is turned off. Both texts change in the release that first sends one.
- A Household without a Gateway Key, as one sending from its own Gmail can be, reports under a random id its program makes for this, which names nobody.
- The maintainers can now see which Households run Parent Recap, on which version, and whether each evening's Brief went out. A report is kept under the Household's Gateway Key for 30 days, then deleted. `SECURITY.md` says so.
- The gateway knows which Households should report, so an evening without a report is itself the finding: the computer was off, asleep or signed out, or the job never started. `admin` lists those Households.
- A run with no network sends its report with the next run's.
- A Problem Report is `doctor`'s output and the last lines of Parent Recap's logs, shown in full before it is sent. Unlike the Run Report it can hold free text, which is why a person reads it and presses Send each time. It works without a Gateway Key, since an install that failed has none, and is kept for 30 days.
- A new kind of failure gets a new cause key in the program and in the gateway's list before it can be reported; until then it reports as an unknown failure.
- The Hosted Household's status (`signup status`) keeps reading the same file on the server and sends nothing.
