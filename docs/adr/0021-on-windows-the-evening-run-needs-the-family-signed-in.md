# On Windows the evening run needs the family signed in

On Windows the evening jobs run in Task Scheduler as the signed-in user ("run only when user is logged on"), with "wake the computer to run this task", and read their secrets from Credential Manager. The PC may be locked, asleep or switched to another user at 21:00, but not signed out or shut down: the same as on macOS (#16). We chose this over "run whether user is logged on or not" because its passwordless form (S4U) has no network access, which the run needs for every Source and the AI, and its stored-password form would ask for the family's Windows password at setup.

## Consequences

- Any program run as the same Windows user can read the secrets. There is no counterpart to ADR 0009's per-program trust, and SECURITY.md says so.
- Whether a scheduled task wakes a sleeping or hibernating laptop, on battery or with Modern Standby, is undocumented; until a laptop has been tested, Windows families are told to leave the PC plugged in.
- A restart for Windows Update leaves nobody signed in, and that night's run doesn't happen unless Windows signs the family back in by itself ("Use my sign-in info to automatically finish setting up after an update"). `doctor` checks that setting; whether it is on by default is still to be tested.
