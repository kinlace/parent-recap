# Tests

From `app/`:

```bash
python -m pip install --only-binary :all: -c constraints.txt -e '.[test,google]'   # once, in an isolated env
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
each of the five outcomes true and false, that the first Brief counts once it has reached the setup
parent, and that no secret or message text reaches either form.
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
It also checks that the install box and its Copy button start hidden, that `hidden` wins over the
box's own layout in the CSS, and that a ready AI gets no install line.
On Connect, it checks the Source list's statuses, each row's name with its status in every
language, skipping and coming back, and the page's Gmail
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
Its WhatsApp step runs against `test_setup_whatsapp.py`'s fake Mac, with each `bg` job run
in-process. It checks that the button shows the Python in Finder and opens App Management, that
the entry ticks itself on the read after the permission is given, with the groups found and
their Kid hints kept in setup's progress for the check page, that every other read result is
said without its error, and that each result is explained in all three languages.
Its MyClub step runs against `test_setup_myclub.py`'s fake MyClub server. It checks that the
button opens MyClub's site, that the Kids are listed without their links, that a good link is
downloaded once and saved for the Kid picked, with MyClub done and setup moving on once every Kid
has one, that a link that isn't one, doesn't open or isn't a calendar is explained and not saved,
that a Household without Wilma adds its Kids by name there and one with Wilma can't, that
skipping saves nothing, and that the link never reaches a response, the output, the logs, a
command line or setup's progress.
Working reads the Gmail senders against `test_setup_steps.py`'s fake IMAP server, held after the
first batch so the page can be seen reading. It checks that reading starts only once every Source
is done or skipped, that the page shows how many senders of how many are read, that the senders
are kept in setup's progress and setup moves on to Check by itself, and that a Gmail that can't be
read says so without the error and can be tried again.
Check checks that the Kids come all ticked with their first name as the everyday name, that
WhatsApp groups and Gmail senders come ticked by best guess and a public mail service is never
offered, that each Recipient comes with their language and the evening at 21:00, and that
confirming saves it all through `setup save` and runs the health check, faked so it can be seen
running, except in one test that runs doctor's real checks against the harness's fakes. It checks
that a failing check keeps the page and names only the check, that warnings alone move setup on,
and that answers the page didn't offer are refused with nothing saved.
First Brief makes the real Brief through `run --preview` in a `bg` job on `test_setup_whatsapp.py`'s
fake Mac, with the harness's Sources and model, and one test fakes the job to see its progress.
It checks that the preview returns the email's HTML and sends or records nothing, that the frame
it's shown in is sandboxed, that a quiet three days still make one, that "Send it to me" sends that
Brief to the setup parent only and the outcome check counts it, that a send that fails, or one
during an evening run, says so, and that only a pilot Household gets the feedback button, which
opens the Digest's pre-filled Form link.
Finish runs against a fake `launchctl`, `pmset` and administrator dialog, which sets the wake-up
once the test lets it go, with doctor's checks faked as on the check page. It checks that the
evening job is loaded and the wake-up set through macOS's dialog only, that the API takes no
password, that each outcome comes back true or false without its reason, that another wake schedule
is replaced only once the family agrees, that a closed dialog or jobs that can't be loaded say so
and can be tried again, that a finished Household is only checked, with nothing installed again,
and that the server stops once every outcome is true. Where no port can be listened on, a request
rung in as the server stops times out, as a closed port refuses it.
Its "Continue in the chat" runs the Terminal script the page opens against a fake `claude`, and
checks that it starts Claude Code in the home folder at the setup skill, that a Codex family is
told to type `$parent-recap-setup`, that `setup save --read` gives the chat the same progress the
page shows, that the setup skill says how to carry on from it, and that each result is explained
in all three languages.
Where no port can be listened on (a sandbox), the server takes each connection through a socket
pair instead, and where no pseudo-terminal can be opened, `claude setup-token` gets a socket pair.
`test_one_line_install.py` runs the README's one-line install, `get.sh`, and the chat setup's
`get.sh --claude` and `--codex`, against a fake `claude` and a fake `curl` serving a stand-in
`stable` tarball, whose `install.sh` leaves a fake `family-brief` for the line to open the setup
page with. It checks a first install, a second run updating, and that nothing it didn't put in
`~/FamilyBrief/plugin` is replaced. The setup page's install of the Claude Code plugin or the
Codex skills, once Welcome is saved, is in `test_setup_page.py`, with the harness answering
`claude plugin`.
`test_install.py` runs the real `install.sh` and pip offline, against packages `fake_packages.py`
makes in a folder, for a stand-in program. It checks that the versions in the constraints file are
installed rather than the newest, that a package published only as source stops the install
without being compiled, and that an older prebuilt version is taken over a newer one that would
compile. It checks that every install writes a dated log of pip's output under `logs/` that starts
with one line about the Mac, with the home folder written as `~`, and that a failing pip step
shows a short message and the log's path instead of pip's output. It checks that the `google`
extra is installed, pinned and prebuilt, only when the config's calendar mode is google. It checks
that `app/constraints.txt` pins every dependency `pyproject.toml` names, the `google` extra's
included, and keeps `cryptography` below 49, that the default dependencies bring in no Google
package and no `cryptography`, and that `scripts/check_wheels.py` fails when a pin has no Intel wheel.
`test_without_google_packages.py` blocks Google's packages from import and checks that every
module imports, that the evening Brief with `.ics`, Weekend Picks, setup and doctor run, that google
mode without them sends the events as `.ics`, and that doctor says how to install them.
`test_issue_loop.py` sources `scripts/issue-loop.sh` and runs its merge step against a fake `gh`.
It checks that a PR is merged only once the `test` check has passed, that a missing, failing or
skipped `test` stops the loop with the reason and no merge, and that a merge GitHub refuses stops
the loop with GitHub's message.
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
