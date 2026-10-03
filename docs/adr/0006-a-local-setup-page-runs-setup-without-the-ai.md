# A local setup page runs setup without the AI

Up to 0.4.2, setup was a conversation: Claude Code or Codex followed the setup skill and ran the `setup` commands for the family. In the first rehearsal on a fresh Mac it took about 30 minutes, and most of the friction came from the conversation itself: long messages with several questions at once, typed answers where a tick would do, and no clear next step. From 0.5.0 the one-line install opens a setup page in the browser instead. A small server inside Parent Recap serves it and runs the same setup steps directly, so setup no longer goes through the AI at all. The family still needs Claude Code or Codex signed in, because the AI writes the Brief every night. The chat setup stays as a second way in, and the page can hand over to it when a family gets stuck.

## Considered Options

- **Keep setup in the chat and fix the conversation.** Cheaper, and the 0.4.3 fixes do that, but each step still costs a round of reading and typing, and the order depends on how the AI reads the skill that day.
- **The AI drives and the page only shows its progress.** Keeps the AI in charge but needs the page and the chat to stay in sync, and still costs a turn per step.

## Consequences

- The page serves only on 127.0.0.1, behind a one-time code in its address, loads nothing from the internet, and shuts itself down when setup is done or after 30 minutes without activity.
- Writing the config moves from the skill into a command both paths share, so the page and the chat setup save the same answers in the same way.
- 0.5.0 covers the first setup only. Changing settings later and uninstalling stay in the chat.
