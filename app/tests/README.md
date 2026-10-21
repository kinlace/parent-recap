# Tests

What each test checks is in its module's docstring and its name; this page is the map.

## Commands

From `app/`:

```bash
python -m pip install --only-binary :all: -c constraints.txt -e '.[test,google]'   # once, in an isolated env
python -m pytest                                # whole suite
python -m pytest -m unit                        # one layer (or: python -m pytest tests/unit)
python -m pytest tests/e2e/test_nightly_brief.py  # one file
python -m pytest --update-goldens=normal_night  # rewrite one night's goldens (see Golden files)
python -m playwright install --only-shell chromium  # once, for the browser tests
python -m pytest -m "e2e and not browser"       # the e2e layer without them
```

CI runs the layers as separate steps, fastest first (unit, integration, e2e, adapters, scripts),
so a failure in the fast layers shows in a minute. The browser tests run last.

## Layers

Every test file lives in the folder of its layer, and `conftest.py` marks each test with its
folder's name, so `-m <layer>` runs one layer. A file outside these folders stops the run.

| Layer         | Folder               | What it may fake                                                                                                                                                           |
| ------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `unit`        | `tests/unit/`        | Nothing: pure functions, table-driven.                                                                                                                                     |
| `integration` | `tests/integration/` | In-memory fakes around one module, which is called through its interface.                                                                                                  |
| `e2e`         | `tests/e2e/`         | Only the OS and network edge (`conftest.py`'s harness); drives `main()` or the setup page over HTTP.                                                                       |
| `adapters`    | `tests/adapters/`    | The command an OS adapter calls (`launchctl`, `security`, `pmset`), or nothing where the real OS runs (Credential Manager, Task Scheduler); the `_windows` twins are here. |
| `scripts`     | `tests/scripts/`     | The downloads and programs `install.sh`, `install.ps1`, `release.py` and `issue-loop.sh` call; the workflows are read as written.                                          |

A branch a unit test covers gets no end-to-end test of its own (#427). Every layer's folder is on
the import path, so a test may import a helper from a test in another layer.

## Unit tests

| File                          | What it covers                                                   |
| ----------------------------- | ---------------------------------------------------------------- |
| `test_ai_filter.py`           | The AI filter on its own: messages in, masked text and names out |
| `test_brief_text.py`          | The program's own Brief and Weekend Picks text                   |
| `test_citations.py`           | The model's citations checked against what the program supplied  |
| `test_eval_scoring.py`        | The eval's deterministic scoring                                 |
| `test_held_back.py`           | Which texts look sensitive, against `sensitive_words.yaml`       |
| `test_kid_placeholders.py`    | Each Kid as Kid A, Kid B and back                                |
| `test_llm_memory.py`          | The llm package's in-memory adapter (ADR 0023)                   |
| `test_sources.py`             | The Source registry's records, and the Sources a Household uses  |
| `test_summarize_normalise.py` | The model's reply put in the schema's shape                      |
| `test_summarize_parsing.py`   | The model's reply parsed, tier by tier                           |
| `test_weekend_pipeline.py`    | The pure parts of Weekend Picks                                  |

## Integration tests

| File                            | What it covers                                                       |
| ------------------------------- | -------------------------------------------------------------------- |
| `test_collector_labels.py`      | The labels Sources add to Messages are English                       |
| `test_email.py`                 | The Brief email as sent to a fake SMTP server                        |
| `test_eval_runner.py`           | The eval runner end to end, only the model process faked             |
| `test_goldens.py`               | The golden files' check and `--update-goldens`                       |
| `test_llm_claude.py`            | The Claude adapter, run isolated from the family's own Claude Code   |
| `test_llm_codex.py`             | The Codex adapter                                                    |
| `test_llm_gateway.py`           | The gateway adapter                                                  |
| `test_setup_claude_token.py`    | The older Terminal script that stores the Claude token               |
| `test_setup_google_calendar.py` | The Google Calendar authorization script                             |
| `test_weekend_picks.py`         | Weekend Picks through the same model process as the Brief            |
| `test_whatsapp_source.py`       | The WhatsApp Source against a `ChatStorage.sqlite` built in the test |

## End-to-end tests: the evening run

| File                              | What it covers                                                     |
| --------------------------------- | ------------------------------------------------------------------ |
| `test_nightly_brief.py`           | The Brief a normal night makes, failures at the edges              |
| `test_nightly_continuity.py`      | Nights in a row, quiet nights, `run --if-missed`                   |
| `test_nightly_google_calendar.py` | Google Calendar mode                                               |
| `test_nightly_languages.py`       | Recipients in their own languages                                  |
| `test_nightly_placeholders.py`    | The AI filter's placeholders, end to end (ADR 0013)                |
| `test_nightly_held_back.py`       | Held-back Messages, end to end (ADR 0013)                          |
| `test_nightly_new_source.py`      | A Source added only to the registry is read, counted, caught up    |
| `test_any_language.py`            | A Recipient in a language without reviewed program text (ADR 0004) |
| `test_busy_model.py`              | A busy model tried again after pauses on a fake clock              |
| `test_gateway.py`                 | The gateway backend (ADR 0014): Brief, doctor and `setup gateway`  |
| `test_source_failures.py`         | Real Gmail, Wilma and MyClub Sources failing partway or timing out |
| `test_wilma_source.py`            | The Wilma Source against a fake wilma CLI printing 2.x's JSON      |
| `test_overlapping_runs.py`        | Runs never overlap; `bg` says when its run didn't finish           |
| `test_private_files.py`           | Other accounts can't read what Parent Recap keeps                  |
| `test_ai_filter_report.py`        | `parent-recap ai-filter-report` over an archive                    |
| `test_schedule_run_now.py`        | `schedule run-now`                                                 |
| `test_without_google_packages.py` | Everything but google mode runs without Google's packages          |

The nights and helpers more than one `test_nightly_*.py` uses are in `e2e/nights.py`.

## End-to-end tests: setup, doctor and uninstall

| File                                                    | What it covers                                                         |
| ------------------------------------------------------- | ---------------------------------------------------------------------- |
| `test_setup_page.py`                                    | The page's server, its safeguards, the language, resuming, Source list |
| `test_setup_welcome.py`                                 | Welcome                                                                |
| `test_setup_ai.py`                                      | The AI sign-in, the gateway, changing the AI on Finish                 |
| `test_setup_gmail.py`, `_wilma`, `_whatsapp`, `_myclub` | Each Source's `setup` command, then its step on the page               |
| `test_setup_claude.py`                                  | `setup claude` and its terminal window                                 |
| `test_setup_check.py`                                   | Working and Check                                                      |
| `test_setup_first_brief.py`                             | First Brief                                                            |
| `test_setup_finish.py`                                  | Finish: the evening job, the wake-up, the outcomes                     |
| `test_setup_chat.py`                                    | The chat Welcome sets up, "Continue in the chat"                       |
| `test_setup_save.py`                                    | `setup save`: answers and progress                                     |
| `test_setup_status.py`                                  | `setup status`: the outcomes that say setup is done                    |
| `test_setup_steps.py`                                   | Setup steps a family runs by hand                                      |
| `test_doctor.py`                                        | `doctor` and `discover`                                                |
| `test_finding_programs.py`                              | Claude, Codex and Node found off PATH                                  |
| `test_own_node.py`                                      | The wilma CLI on Parent Recap's own Node (ADR 0011)                    |
| `test_uninstall.py`                                     | `uninstall` removes what setup made, and nothing else                  |
| `test_setup_page_browser.py`                            | The page's main paths in a real browser (ADR 0024)                     |

The page's server, its calls and the steps more than one file walks through are in
`e2e/setup_page.py`.

A setup step's answers, results and files get an HTTP test, as the page's script calls the server.
What only the page does gets a browser test: a step it shows or hides, a button it enables, the
text it picks for a result, or a path a family clicks through. These are marked `browser`, run
headless Chromium through Playwright and skip, saying why, where it isn't installed. They are the
slowest tests, so add one for a main path, not for each result.

## Adapter tests

| File                            | What it covers                                            |
| ------------------------------- | --------------------------------------------------------- |
| `test_desktop.py`               | macOS desktop: `open`, Terminal, `defaults`               |
| `test_jobs.py`                  | macOS jobs: LaunchAgents and `launchctl`                  |
| `test_secrets.py`               | macOS secrets: the Keychain through `/usr/bin/security`   |
| `test_wake.py`                  | macOS wake: `pmset`                                       |
| `test_desktop_windows.py`       | Windows desktop: browser, Notepad, PowerShell windows     |
| `test_jobs_windows.py`          | Windows jobs: Task Scheduler tasks                        |
| `test_secrets_windows.py`       | Windows secrets: Credential Manager                       |
| `test_wake_windows.py`          | Windows wake: wake timers and sleep timeouts              |
| `test_windows_only.py`          | What only Windows checks for the evening run              |
| `test_brief_by_hand_windows.py` | A Brief started by hand on Windows                        |
| `test_setup_page_windows.py`    | The setup page from a fresh install to Finish, on Windows |
| `test_setup_wilma_windows.py`   | The Wilma sign-in window on Windows                       |

## Script tests

| File                       | What it covers                                               |
| -------------------------- | ------------------------------------------------------------ |
| `test_install.py`          | `install.sh`: pinned Python and Node, prebuilt packages only |
| `test_install_ps1.py`      | `install.ps1` stays in step with `install.sh`                |
| `test_one_line_install.py` | `get.sh`, the line a family pastes                           |
| `test_codex_install.py`    | `install.sh --codex` installs the skills for Codex           |
| `test_release.py`          | The release zip built from a tag                             |
| `test_version.py`          | The plugin and the package ship the same version             |
| `test_workflows.py`        | Every GitHub Actions workflow parses                         |
| `test_issue_loop.py`       | `issue-loop.sh`'s merge step and its checker                 |
| `test_tests_readme.py`     | This page lists every test file, in short sections           |

## Fakes and fixtures

The shared fakes stay in `tests/`, beside the golden files:

- `conftest.py`: the `harness` fixture, which runs the real program with fakes only at the
  outside edges (Sources, the `claude`/`codex` process and the gateway, email, iMessage, Google
  Calendar, the pauses between model calls); its docstring lists them. Its model says nothing by
  default, so a test that needs the night's Brief to go out has it reply `SAYS_SOMETHING` (#385).
  Also `golden`, `login_shell`, `windows_programs` (programs found as Windows finds them, on any
  system), `credential_manager`, and the builders `msg` and `myclub_event`.
- `fake_system.py`: in-memory secrets, jobs, wake and desktop, as `harness.secrets`, `.jobs`,
  `.wake` and `.desktop`. Only the adapter tests meet these services' commands.
- `fake_node.py`: Parent Recap's own Node and a fake wilma CLI in the test's `~/ParentRecap`.
- `fake_packages.py`: packages for tests that run the real pip offline.
- `goldens.py`: the golden files' check.
- `no_google/`: Google's packages blocked from import. `no_pty/`: the Wilma window's
  pseudo-terminal as a socket pair, where none can be opened (a sandbox).
- `e2e/nights.py` and `e2e/setup_page.py` (`page`, `clock`, `ai`, `health`, `schedule`) are shared
  by their files; `test_setup_whatsapp.py`'s `fake_mac` is the fake Mac other WhatsApp tests import.

Config, state and the archive are real files in a temporary HOME; "now" and the timezone are pinned.

## Markers

The layer markers come from the folder and are never written by hand. Two more say which system
a test needs, and skip it elsewhere, so each CI job runs every other test:

- `@pytest.mark.macos` (a whole module: `pytestmark = pytest.mark.macos`): the macOS adapters,
  macOS-only features (WhatsApp for Mac and Full Disk Access, the Passwords app), `install.sh`
  and the other shell scripts, permission bits, and what Windows can't do yet (finding programs by
  bare name, shell-script stand-ins, pseudo-terminal windows). A Windows ticket that makes one work
  there drops the mark.
- `@pytest.mark.windows`: the Windows adapters and Windows' own layout. The Credential Manager and
  Task Scheduler tests use the real ones, under a test name of their own, and delete what they
  made.

`browser` marks the tests that drive the setup page in a real browser; CI's macOS job runs them in
a step of their own, after Chromium is installed.

CI runs the suite on macOS, and on Windows the unit, integration and adapter layers and the
end-to-end tests marked `windows`. `install.ps1` itself runs for real in CI's windows-install job.

## Golden files

The Brief's text, HTML, `.ics` and the exact model command line are compared against `golden/`.
When a change to the Brief or the prompt is intended, regenerate the goldens and review the diff:

```bash
python -m pytest --update-goldens               # every golden
python -m pytest --update-goldens=normal_night  # one night's: normal_night.*
python -m pytest --update-goldens='*.ics'       # a glob
git diff tests/golden
```

The pattern is a glob, or a name up to a dot. Only goldens that match and changed are rewritten,
each with one line at the end of the run saying how many lines it gained and lost; the rest are
still compared. Write the pattern with `=`: `--update-goldens tests/e2e` is refused.

## Brief quality eval

The goldens only show that the Brief looks the same. Whether the model got the facts right is
scored by the eval, which calls the real model on each synthetic night in
`src/family_brief/eval/cases/`. It is not part of the suite (real calls, quota, non-determinism).
Run it once per release, on the commit you're about to tag. PRs don't need it (the issue loop's
sandbox can't reach the model anyway), but you can run it before and after a risky prompt change:

```bash
python -m family_brief.eval --backend claude,codex --repeat 2   # or one backend with --model ...
```

It scores Briefs in every reviewed language (English, Chinese and Finnish), one scorecard each
(`--language en`, `zh` or `fi` for one). Each run is saved under `~/ParentRecap/eval/` and
compared with the previous one of the same language (runs saved before Briefs had a language count
as `zh`); the spread column shows run-to-run noise. `--cases DIR` scores a private folder instead;
the case format is described in `src/family_brief/eval/cases.py`.

Keywords in `expect` need Chinese, English and Finnish alternatives; give the Finnish as stems
(`lupalap`, `retk`), since a Finnish Brief inflects them. A keyword matches anywhere in the text,
so check that a short stem can't hide in another word: `kuva` (photo) also matches `jatkuvat`
(continue). Start it with a space (`" kuva"`) to match only at a word's start. A `notices` rule
with `languages: [en, zh]` is checked only on Briefs in those languages, such as Finnish words an
English or Chinese Brief must translate. A rule with `where: digest` checks the Digest's lines,
which sit under no Kid, and `where: anywhere` the notices, action items and Digest together: a
line the parents must never read, such as one saying there is nothing new, goes there.

## Eval: trying another model

Before switching the model, or the provider, check that the Brief stays as good, Finnish
especially. Score the backend's own model and the candidate in one run:

```bash
python -m family_brief.eval --backend claude --model default,claude-haiku-5-5 --language fi --repeat 3
```

`--model` takes a comma-separated list; `default` is the model the backend picks itself, the one
Parent Recap uses today. Every case runs on each model in turn, and each language gets one
scorecard with a column per model. With `--repeat`, each cell shows the score and, in brackets, the
spread across runs. A quality score worse than every run of the first model is marked ↓. Each
model's run is also saved on its own, so a later single-model run compares with it.

How to read it:

- **Quality:** a model is good enough when its scores stay within the spread of the current
  default: no ↓, and no case that is clean in every run of the default slips on the candidate
  (Clean runs per case, then What slipped says why). Run at least `--repeat 3`: with fewer runs
  the spread is too narrow to tell noise from a real slip.
- **`placeholders_left`:** texts in the Brief that still show a placeholder (`⟦N1⟧`), plus
  placeholders the model changed beyond repair, which the Brief gives as their kind's words
  ("someone", "a link") where the night's text had the real name or link. A smaller model tends
  to change them more often. It should stay at 0.
- **Cost:** input, cache-write, cached-input and output tokens per night, turned into dollars per
  night and per month (30 nights) from `src/family_brief/eval/prices.yaml`, the Anthropic API's
  prices on the date it gives. A subscription bills none of it; this is what the maintainers' key
  would pay (#186). A night is priced as the model the claude CLI says answered, so `default` gets
  a price too. Codex reports no tokens, and a model missing from the table has no price: the card
  says which. Update the table, and its date, before comparing a model it doesn't list.

## Eval: masking the Kids' names too

The AI filter's `mask_kids` switch, on by default since #300, sends each Kid's names, aliases and
their case forms as Kid A, Kid B too (#268), and the eval does the same. To see what it costs a
backend or language, score it without the switch and then with it, so the second run compares
with the first:

```bash
python -m family_brief.eval --backend claude,codex --repeat 2 --no-mask-kids
python -m family_brief.eval --backend claude,codex --repeat 2
```

The card of a run with it says `Kids as Kid A, Kid B`. Watch `kid_ok` and `cases_clean`
especially, and the cases with a Kid's alias or genitive. ADR 0013 records why it is on.
