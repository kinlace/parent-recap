# On Windows the evening run needs the family signed in

On Windows the evening jobs run in Task Scheduler as the signed-in user ("run only when user is logged on"), with "wake the computer to run this task", and read their secrets from Credential Manager. The PC may be locked, asleep or switched to another user at 21:00, but not signed out or shut down: the same as on macOS (#16). We chose this over "run whether user is logged on or not" because its passwordless form (S4U) has no network access, which the run needs for every Source and the AI, and its stored-password form would ask for the family's Windows password at setup.

## Consequences

- Any program run as the same Windows user can read the secrets. There is no counterpart to ADR 0009's per-program trust, and SECURITY.md says so.
- Whether a scheduled task wakes a sleeping or hibernating laptop, on battery or with Modern Standby, is undocumented; until a laptop has been tested, Windows families are told to leave the PC plugged in.
- Task Scheduler doesn't catch up a start missed because nobody was signed in, even with "run as soon as possible after a scheduled start is missed". So the evening job has a second task, at the family's sign-in, which runs it only if its latest evening run was missed (`run --if-missed`): that evening's Brief comes at the next sign-in, often the next morning, and one run covers several missed evenings, since the lookback already widens over them (#386).
- A restart for Windows Update leaves nobody signed in, and that night's run waits for the next sign-in unless Windows signs the family back in by itself ("Use my sign-in info to automatically finish setting up after an update"). `doctor` checks that setting; whether it is on by default is still to be tested.
