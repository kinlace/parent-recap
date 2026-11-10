# A Local Files Source reads plain files the family puts in a folder, not any app's local database

Supersedes ADR 0043. Parent Recap does not read any app's local database. Reading WeChat's would mean taking its key from the running app's memory, which WeChat's terms forbid, and since July 2026 Tencent has had GitHub take down WeChat key-extraction tools under DMCA §1201, so a Source built on one could disappear, or put the project at risk, at any time. Instead a generic Local Files Source reads plain files the family puts in a folder it picks (`~/ParentRecap/inbox` by default): a `.json` file is a list of messages, each `{"time", "chat", "sender", "text"}` with `chat` and `sender` optional, and a `.txt` or `.md` file is one message, in the chat its name gives, at the time it was last changed (#868). What goes into the folder, and how, is the family's own business; Parent Recap only reads it, never moves, changes or deletes a file, and its messages go through the AI filter like every other Source's (ADR 0013). It is an optional Source a family turns on with the manage skill, not part of the setup page, and a Hosted Household never has it. Decided 2026-11-10 by 赵晗博.

## Considered Options

- **Reading WeChat's local database with wechat-cli (ADR 0043).** Rejected for the reasons above.
- **A Source per app, each reading that app's export format.** Rejected: one simple format the family or any program can write covers every app, and Parent Recap never has to keep up with an app's own files.
- **Moving files to a "done" folder once read.** Rejected: Parent Recap would then change the family's files. It remembers which messages it gave instead, as for WhatsApp.

## Consequences

- A message is given once: within the Source's lookback, widened after missed evenings as for the other Sources, and not seen before. Its id is made from its chat, sender, text and time, so the same message in two files, or a text file saved again unchanged, comes once.
- A file that can't be read is skipped and never stops the Brief; `doctor` names it with why. A missing folder is said on the Brief's coverage line and by `doctor`.
- Uninstall leaves the folder and its files: they are the family's.
