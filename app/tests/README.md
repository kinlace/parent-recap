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
that a MyClub link is never repeated, and that progress is saved and read back.
`test_setup_page.py` starts the setup page's server in-process and drives it with HTTP calls, as
the page makes them. It checks that a request without the code, or for another Host or from
another Origin, is refused, that the page loads nothing from the internet, that the server stops
after 30 idle minutes, the Mac's language preselected, that the language picked is saved, that
every page text is in all three languages, and that a second run resumes at the saved phase.
Where no port can be listened on (a sandbox), the server takes each connection through a socket
pair instead.
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
