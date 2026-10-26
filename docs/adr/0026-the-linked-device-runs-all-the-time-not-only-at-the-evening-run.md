# The Linked Device runs all the time, not only at the evening run

Everything else Parent Recap does starts with the evening run and ends with it. The Linked Device (ADR 0025) is different: it is a process that stays connected to WhatsApp whenever the family's user is signed in, keeps the picked groups' messages as they arrive, and is restarted by the OS when it exits. The evening run then reads what it kept. We chose this because it is what held for 14 days in #181, where the link dropped about five times a day and came back within seconds each time. Whether WhatsApp delivers a day's messages to a linked device that connects only once a day, and whether a link survives that pattern, was not tested.

## Considered Options

- **Connect only during the evening run**, catch up and disconnect. No background process, but it rests on WhatsApp queuing messages for an offline linked device, untested, and a link seen once a day may look more like an abandoned one.

## Consequences

- The OS-services layer (ADR 0020) gets a second kind of job beside the calendar one: an always-running process (a launchd agent with KeepAlive on macOS, a Task Scheduler task at sign-in that restarts on failure on Windows). Like the evening run (ADR 0021, #16), it runs only while the family's user is signed in.
- Messages that arrive while the computer is off or nobody is signed in reach the Linked Device when it reconnects, as far as WhatsApp still delivers them; how far back that goes is untested.
