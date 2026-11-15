---
status: accepted
---

# A family's Windows PC updates itself too, with its installer in Python

Decided by Xi on 2026-11-15 for #953.

ADR 0047 gave a Household on a Mac an update of its own after the evening Brief and left Windows out, saying a background download there is what Defender's rules stop (#591). #591 was something else: an attack surface reduction rule blocked `parent-recap.exe`, a launcher pip makes on the PC, and let the venv's `python.exe` and every download through. Tried on a home Windows 11 PC (#954), a scheduled task's `pythonw.exe` downloaded a release zip, unpacked it, made and remade a junction and started a second venv's `python.exe` with nothing stopped and nothing on screen. So a Household on a Windows PC gets the same update as one on a Mac: the same look after the evening Brief, the same day's wait, the same signed zip from the same address, the same `doctor` check and switch back, the same sentence at the top of the next Brief, and the same switch on the setup page. Everything ADR 0047 promises a reader of the code holds on Windows. What differs is below.

- **The update runs no `.ps1` file.** On a Mac the update runs the verified release's `install.sh` as a file. Windows' default execution policy refuses a `.ps1` file, and the way round it, `powershell.exe -ExecutionPolicy Bypass -File`, is what malware does and what a company's Group Policy may refuse. So the steps of the Windows install are a Python program the release ships, which uses only the standard library until the new version's venv exists. The update runs it from the verified zip with the installed version's Python. `install.ps1`, the line a family pastes, only fetches the pinned Python and hands over to the same program, so a pasted install and an update are one set of steps.
- **`app` is a junction.** Each release is installed in a folder of its own under `versions`, as on a Mac, and `app` points at the current one. A symlink needs an administrator or developer mode on Windows, and the install has neither (ADR 0019); a junction needs nothing. A junction can't be renamed over another, so the switch isn't one step: for a moment `app` isn't there. The update switches under the run lock, and the Linked Device's task is stopped before the switch and started after it.
- **The signature is checked with Windows' own `ssh-keygen`**, at its full path in `System32\OpenSSH`, as a Mac's is with `/usr/bin/ssh-keygen`. Windows 11 has it unless someone removed it. A PC without it doesn't update itself: `doctor`, the setup page and the Brief say so, and the family pastes the install line to update.
- **It ships without a test on a PC that a company manages.** None of us has one. Whether a managed Defender stops a background `pythonw.exe` that downloads a zip and starts a child process is left for a piloting Household to show. For that to be safe, an update that is stopped at any step leaves the installed version working and the evening task running, and the step that was stopped and the error, word for word, go to the update log and show in `doctor`, so that Household's Problem Report names the rule.

## Considered Options

- **Start PowerShell with `-ExecutionPolicy Bypass` on the release's `install.ps1`.** Rejected: it worked on the home PC of #954, but it is the one step a policy is known to refuse, and a reader of the code sees a background job going round a Windows safeguard.
- **Keep `install.ps1` as it is and write the update's steps a second time in Python.** Rejected: the two would drift, and the one a family rarely runs would be the one that breaks.
- **Verify the signature in Python with `cryptography`.** Rejected for now: it is pinned but installed only with Google's packages, so it would become a package of every Household, and the check would be code of our own where Windows ships a tool that does it. It is what to do if PCs without `ssh-keygen` turn out to be common.
- **Wait for a PC that a company manages.** Rejected: there is none to be had, and the families we build for have home PCs.
- **Replace the program in `app` in place, as `install.ps1` did.** Rejected: there is no version before to switch back to, and the evening task's own `python.exe` is locked while it would be replaced.

## Consequences

- An install from before this has the program in `app` itself. The first install after it moves that folder into `versions` as the previous version. Windows refuses to move a folder a running program was started from, so the Linked Device's task is stopped for the move.
- The scheduled tasks and the Start menu entries name `pythonw.exe` under `app`, so an update leaves them as they are.
- ADR 0051's skip of a release that changes nothing the Household uses now applies on Windows too.
- A Windows PC runs the evening task only while the family is signed in (ADR 0021), so a PC nobody signs in to takes neither a Brief nor an update.
