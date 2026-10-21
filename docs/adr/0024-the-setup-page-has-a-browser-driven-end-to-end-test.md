# The setup page has a browser-driven end-to-end test

The setup page is the first thing a new family sees (ADR 0006), and its script is 2000 lines with no test of its own: the 277 page tests call the server over HTTP and check a few strings in the script's source, so a step that renders wrong or a button that does nothing passes CI. From the next refactoring the page's main paths run in a real browser in CI, driven by Playwright from the Python tests: welcome to choosing the AI to connecting Gmail to finishing, skipping a Source, and a failed step showing its error. Everything behind the page stays the existing fakes (IMAP, the model CLI, the Keychain); only the browser is real. The cost is a browser download in the macOS job and a few minutes more per run, which is worth it because onboarding is the part of the product that is changed most and seen first.

## Considered Options

- **Keep checking the script's source with regular expressions.** Breaks on any rename and proves nothing about behaviour; those checks go once the browser tests cover the same paths.
- **Unit tests for the page's script with a JavaScript test runner.** Adds a second toolchain and a second test command; the page has no build step today and the paths that matter cross the server anyway.

## Consequences

- Reordering or reworking a setup step (the refactoring that makes the script data-driven) has a safety net before it starts.
- Browser tests are the slowest layer and run after the unit and integration layers in CI.
