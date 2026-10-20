# The program installs its own pinned Python and Node under ~/ParentRecap

Superseded for the WhatsApp permission by ADR 0012: on macOS 26 the evening job's Python needs Full Disk Access, not App Management. The pinned runtimes and their versioned folders still follow this ADR.

The evening job ran on whatever Python the installer found, and WhatsApp's App Management permission names that Python's real path, patch version included. A `brew upgrade` or `uv python upgrade` moves or breaks it: the permission then points at a file that no longer exists, or the venv stops working and the evening job doesn't run, with no message in either case (#20). Homebrew is also the first thing a non-technical family hits, and nearly every Finnish family needs Node for Wilma. So `install.sh` downloads a pinned Python (python-build-standalone) and a pinned Node into `~/ParentRecap`, each checked against a checksum written in the script, picks arm64 or x86_64 with `sysctl -n hw.optional.arm64` (`uname -m` lies under Rosetta), and builds the venv on that Python. Nothing outside Parent Recap's own folder can update them.

Each pinned version lives in its own versioned folder. When Parent Recap bumps a pinned runtime, the new one goes into a new folder, and `install.sh` tells the family to grant WhatsApp's permission again if the Python's real path changed. `doctor` says when the Python macOS allowed is no longer the evening job's Python and prints the path to grant. Uninstall removes both runtimes and tells the family to remove the App Management entry by hand. Installs that predate this reinstall once, which the rename (ADR 0010) already forces.

## Considered Options

- **Only record the granted path and have `doctor` flag a mismatch.** Small, but Homebrew stays a prerequisite and a Homebrew upgrade can still break the venv and stop the evening job silently. Most of it would be replaced by this decision anyway, so only the `doctor` message is kept.
- **Copy WhatsApp's database with a stable Apple-signed tool such as `/bin/cp`, and read the copy.** The permission would survive any Python update, but it is given to a general tool, which is broader than a Python dedicated to Parent Recap.
- **A Developer ID signed `.app`.** It could keep the permission across our own Python bumps too. Left for when there are more families.

## Consequences

- We own security updates of both runtimes, and a download of tens of megabytes (not measured). A failed or offline download needs a clear message and a way to retry, and the install tests use a fake tarball.
- macOS doesn't keep the permission (Full Disk Access since ADR 0012) when the Python is replaced by another patch version at the same path, so a bump keeps going into a new folder. Tested on the Mac mini under a LaunchAgent (macOS 26.3.1, 3.13.16 and 3.13.7, #168, results on #20): WhatsApp stops being readable with no question from macOS, and System Settings still shows the entry switched on until the first failed read switches it off. Granting the venv's `python` symlink stores the file it points to, so it doesn't help.
