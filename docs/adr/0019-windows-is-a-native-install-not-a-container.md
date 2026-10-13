---
status: proposed
---

# Windows is a native install, not a container

Families with a Windows PC instead of a Mac get Parent Recap installed natively on Windows 11 x64, the way the macOS version is installed: its own pinned Python and Node under `%LOCALAPPDATA%\ParentRecap`, the evening run in Task Scheduler, secrets in Credential Manager. We chose this over a container because a container asks the family to install Docker Desktop first (large, needs administrator rights, keeps running) and still can't wake the PC for the evening run or read WhatsApp. With the gateway (ADR 0014) a family no longer needs Claude Code or Codex installed, so nothing ties the AI side to macOS either.

## Considered Options

- **A Docker container for Windows, Linux and home servers (#68).** Rejected for the reasons above; it remains an option for a home server later.
- **Windows 10 as well.** Left out: Microsoft ended its support in October 2025, and it would be a second system to test.
- **Windows on ARM64.** Left out for now: few families have one, and the wheels and the Wilma CLI there are untested.

## Consequences

- The install starts unsigned (`irm … | iex`, no administrator rights). Signing comes with a winget or installer channel; until then Smart App Control may block the downloaded Python on some PCs.
