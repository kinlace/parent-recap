# WeChat groups are read from the computer's own WeChat database with wechat-cli

Many Chinese families get their kids' activity news (Chinese school, dance, piano, Go) only in WeChat groups, and families in Finland and Sweden asked for them in the Brief (2026-11-10). WeChat has no API for a parent's own groups and no pairing like WhatsApp Web's, so a WeChat Source reads the picked groups from the encrypted database WeChat for Windows and WeChat for Mac keep on the family's computer. The key comes from the running WeChat's memory, once, through the open-source wechat-cli (Apache-2.0), which the program calls as a separate tool rather than copying its code. It is read-only. It is an optional Source a family turns on with the manage skill, never part of the standard setup, and a Hosted Household never has it. Windows comes first, since its one-time key step needs no change to WeChat; the Mac follows, where the step needs `sudo` and, on some Macs, an ad hoc re-sign of WeChat.app, both shown to the family to run themselves. Only the groups the family picks are read, and their messages go through the AI filter like every other Source. Decided 2026-11-10 by 赵晗博.

## Considered Options

- **agent-wechat.** Rejected: GPL-3.0, Mac only, and it re-signs WeChat and needs the key step again after every WeChat update.
- **A WeChat bot account added to each group.** Rejected: every group admin would have to let it in, and bot accounts on personal WeChat risk being banned.
- **Asking families to forward messages.** Rejected: it's the manual work the Brief exists to remove.

## Consequences

- WeChat versions newer than wechat-cli supports can break the Source until wechat-cli catches up; `doctor` must say so plainly instead of the Brief going quiet.
- The setup and the uninstall list what this Source adds (wechat-cli and its saved key), and the uninstall removes them.
