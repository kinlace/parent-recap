# Tests

From `app/`:

```bash
python -m pip install -e '.[test]'   # once, inside whatever isolated env you use (venv, conda, uv…)
python -m pytest                     # whole suite
```

`test_nightly_run.py` runs the real `family-brief run` with fakes only at the outside edges
(Sources, the `claude`/`codex` process, email, iMessage and Google Calendar); see `conftest.py`.
The fake Sources honor seen-state like the real ones, so multi-night tests see only new Messages.
`test_source_failures.py` runs the real Gmail, Wilma and MyClub Sources against a fake IMAP server,
`wilma` CLI and MyClub feed, for a Source that fails partway through a night or times out.
`test_setup_gmail.py` runs `family-brief setup gmail` against a fake macOS dialog, `open`,
Keychain and IMAP server, and checks the App Password never reaches the output, the logs or a
command line.
`test_setup_wilma.py` runs `family-brief setup wilma` against a fake `wilma` CLI and a Terminal
that runs the sign-in script at once, and checks the Kids, the city from every preset Wilma
address, the window's guide in each language, that the window ends the CLI before its student
picker and menu, and that the Wilma password never reaches the output. Where no pseudo-terminal
can be opened (a sandbox), the window talks to the fake CLI through `no_pty/sitecustomize.py`.
`test_setup_claude.py` runs `family-brief setup claude` against a fake Terminal, macOS dialog,
Keychain and `claude` test call, and checks the token never reaches the output, the logs or a
command line. `test_setup_claude_token.py` keeps the older Terminal script working.
`test_setup_whatsapp.py` runs `family-brief setup whatsapp` against a fake `launchctl` that runs
each `bg` job in-process, and a WhatsApp database only those jobs can read once the fake Mac has
the permission. It checks each permission state, the chats and their Kid hints, and that
WhatsApp is never read outside a `bg` job.
`test_setup_myclub.py` runs `family-brief setup myclub` against a fake macOS dialog, `open` and
MyClub server, and checks the link never reaches the output, the logs or a command line, that a
failed download names only the server and the HTTP status, and that the older Terminal script
still works.
`test_setup_status.py` runs `family-brief setup status` against a fake `launchctl`, `pmset`,
Keychain, IMAP server and `claude` test call, with a real Brief sent through the harness. It checks
each of the five outcomes true and false, and that no secret or message text reaches either form.
`test_setup_save.py` runs `family-brief setup save` with answers on stdin against real config and
progress files in the temporary HOME. It checks the config it writes loads, that answers not given
leave the config as it was, that invalid answers are refused with the reason and nothing saved,
that a MyClub link is never repeated, and that progress is saved and read back, with the partner
Welcome chose kept in it.
`test_setup_page.py` starts the setup page's server in-process and drives it with HTTP calls, as
the page makes them. It checks that a request without the code, or for another Host or from
another Origin, is refused, that the page loads nothing from the internet, that the server stops
after 30 idle minutes, the Mac's language preselected, that the language picked is saved, that
every page text is in all three languages, and that a second run resumes at the saved phase. On
Welcome, it checks each choice's default and that the answers are saved, that the partner's email
and language are asked only with a partner, and that a missing or signed-out Claude Code or Codex
is caught and checked again: the harness answers `claude auth status` and `codex login status`
from `harness.signed_in`, and the test puts a fake `claude` or `codex` on PATH to install one.
On Connect, it checks the Source list's statuses, skipping and coming back, and the page's Gmail
step against a fake IMAP server and Keychain: a valid App Password is stored and Gmail turns done,
each known result has its explanation in all three languages, and the App Password never reaches
a response, the output, the logs, a command line or a file in `~/.family`. Its Wilma step runs
against a fake `npm` and a fake wilma CLI, laid out as `npm install -g` lays out the pinned
version with Wilma's tenant list inside, which reads the profile the page writes the way the
pinned CLI's own code does and signs in to a fake Wilma that knows one account. It checks that
"Espoo" and "Esbo" find the Espoo entries, that the pinned CLI is installed when missing, that a
good login writes the profile in the CLI's format and the page lists the Kids from it, that a
wrong password is reported as such and any other failure offers the Terminal window (whose `open
-a Terminal` the test answers by saving the CLI's profile), that the CLI's earlier profile is put
back when a sign-in fails, that a Household without Wilma saves only its town, and that the Wilma
password never reaches a response, the output, the logs, a command line or `~/.family`.
Its AI sign-in runs a fake `claude` whose `setup-token` draws the token on the pseudo-terminal
the page gives it, with Ink's escape sequences, and a fake `codex` whose `login` the harness's
`codex login status` then reports signed in. It checks that Claude's token is read, tested with
the harness's test call and stored with nothing copied, that a sign-in without a token, or one
that times out, offers the Terminal window and the page takes the pasted token in its field, that
the token never reaches a response, the output, the logs, a command line or `~/.family`, and that
the Codex entry ticks itself once Codex is signed in.
Where no port can be listened on (a sandbox), the server takes each connection through a socket
pair instead, and where no pseudo-terminal can be opened, `claude setup-token` gets a socket pair.
`test_one_line_install.py` runs the README's one-line install, `get.sh --claude` and `--codex`,
against a fake `claude` and a fake `curl` serving a stand-in `stable` tarball. It checks a first
install, a second run updating, and that nothing it didn't put in `~/FamilyBrief/plugin` is replaced.
The Brief's text, HTML, `.ics` and the exact model command line are compared against `golden/`.

When a change to the Brief or the prompt is intended, regenerate the goldens and review the diff:

```bash
python -m pytest --update-goldens
git diff tests/golden
```

## Brief quality eval

The goldens only show that the Brief looks the same. Whether the model got the facts right is
scored by the eval, which calls the real model on each synthetic night in
`src/family_brief/eval/cases/`. It is not part of the suite (real calls, quota, non-determinism).
Run it once per release, on the commit you're about to tag. PRs don't need it (the issue loop's
sandbox can't reach the model anyway), but you can run it before and after a risky prompt change:

```bash
python -m family_brief.eval --backend claude,codex --repeat 2   # or one backend with --model ...
```

It scores Briefs in every reviewed language (English, Chinese and Finnish), one scorecard each (`--language en`, `zh` or `fi` for one).
Each run is saved under `~/FamilyBrief/eval/` and compared with the previous one of the same
language (runs saved before Briefs had a language count as `zh`); the spread column shows
run-to-run noise. `--cases DIR` scores a private folder instead; the case format is described
in `src/family_brief/eval/cases.py`. Keywords in `expect` need Chinese, English and Finnish alternatives; give the Finnish as stems (`lupalap`, `retk`), since a Finnish Brief inflects them.
