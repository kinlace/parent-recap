# Tests

From `app/`:

```bash
python -m pip install --only-binary :all: -c constraints.txt -e '.[test,google]'   # once, in an isolated env
python -m pytest                     # whole suite
```

`test_nightly_run.py` runs the real `parent-recap run` with fakes only at the outside edges
(Sources, the `claude`/`codex` process, email, iMessage and Google Calendar); see `conftest.py`.
The fake Sources honor seen-state like the real ones, so multi-night tests see only new Messages.
`test_ai_filter.py` calls the AI filter on its own with a list of messages, as another tool could.
In `test_nightly_run.py`, a night with phone numbers, email addresses and links in every part of the
payload checks that none reaches the Brief or translation prompt, only placeholders, the same one for
a value in both, with no Gmail message link or Wilma student number. A Chinese night has them inside
Chinese text with no space around them, and its Chinese reply writes the placeholders the same way:
the text around them stays, and the translation prompt has none of the night's values. It checks that
the Brief, the `.ics`, iMessage and the archive get the real values back, that an event keeps a link
only if a message it cites has it, that a placeholder the model wrote in other brackets, another case,
full-width digits or a lookalike letter still gets its value, that one it changed beyond repair is
said as its kind in each Recipient's language with the item kept, and that a bare P1, or a (P2017)
the night's messages have themselves, stays as written. A Finnish night names a coach, a teacher,
other pupils and other parents in Finnish case forms (Maijalle, Virtaselle) and inside Chinese text
with no spaces: none of them reaches the Brief or translation prompt, the Kids' names and aliases
and the partner's own name do, a role in a sender stays beside its placeholder, and the Brief, the
`.ics` and the archive get the names back as the messages wrote them. The night also has a
coach whose sender names his club, a Chinese pupil written by the given name alone (小明), a
pupil named Leon beside the Kid Leo (Leon stays), and a newsletter from a news@ address (no one).
A name from an earlier Brief's night is masked too, and a person placeholder changed beyond
repair is said as "someone" in each Recipient's language. Pilot feedback links carry an item as
the AI saw it: a teacher in a Finnish case form, a teacher and a phone number inside Chinese text
with no spaces, and a name the model wrote out itself go as that night's placeholders, the Kids as
Kid A and Kid B, also with the AI filter off, and the Household as a pseudonym that is the same
the next evening and differs for another label. The Form keeps the fields `ops/feedback-form`
gives it, in their order. `test_ai_filter.py` checks the names on
their own: a full name and its parts as one person, stems that change (Pekan, Niemen, Markuksen),
that a reply copying the placeholders gets back exactly the text they stood for (Ville Virtanen,
Maria, Kevät juhla, Toivon), how a form the text never had is made, a part two people share, a
Kid's name in a case form, roles, senders with an organisation and organisations alone, Chinese
given names, and that an ordinary word that is a name elsewhere (Onni, Toivo, toivo) is masked
only when that night's list has it. With the filter off, the Brief and translation
prompts must match `normal_night.*.model.txt` and `two_languages.translate.model.txt`, the goldens from
before the filter. The prompts with it on are the `.masked` goldens. `test_eval_runner.py` runs an
eval case with a link and a phone number to the end, the model answering with the placeholders it was
given, and scores the real values.
`test_held_back.py` checks which texts look sensitive on their own, against the word lists in
`sensitive_words.yaml`: messages about one person's health, support, bullying or child welfare in
Finnish with its case endings and compounds, Swedish, English, and Chinese with no spaces, and the
routine messages and everyday words that must not be held back (head lice, chickenpox, a sick day, the
school nurse's check-ups, a sports injury, a camp notice asking about medication, a diagnostic test,
the psychology subject, the guidance counsellor, kiusallinen, syllabus, a fire alarm test), and
newsletters in each language whose footer lists the staff with their contact details. It also checks
that a match names the entry that matched and where it stands in the text as written, past the words
the `not` list takes out and a newsletter's contact lines, and in which of a message's subject, body
and sender's name (not its address) it is. Messages sent to everyone are never held back, whatever
their words: a Wilma announcement (the same words in a Wilma message are), an email the Gmail Source
reads with a `List-Id`, `List-Unsubscribe` or `Precedence: bulk` or `list` header, and the
announcements in made-up Wilma notification emails in Finnish and English. The message such an email
copies is held back, and the match points into it. A Wilma email with no announcements heading, or
with a mailing list's headers, is checked whole. In
`test_nightly_run.py`, a night with a sensitive message from each category in each language checks
that none of them reaches the Brief's or the translation's prompt, and the `held_back.*` goldens show
the Brief listing each Held-back Message by Source, sender and subject, with its Gmail link, its page
in the Wilma the CLI's profile in the temporary HOME names, or a note to read it in WhatsApp. It also
checks that a night with only Held-back Messages sends a Brief with no model call and no "Written by",
that such a newsletter and a camp notice about medication still reach the model, that the archive
keeps them, that the rule-based fallback doesn't list them twice, that the setup page's preview and
`parent-recap summarize` hold them back too, and that with the AI filter off they go to the model as
before. A night with a Wilma announcement, a city's mass email and a Wilma notification email with
only announcements, each with a sensitive word, sends them to the model, and the Brief lists only a
Wilma message to the Household and a notification email that copies one. `test_eval_runner.py`
checks that the eval's Brief call and its judge never get one either.
`test_ai_filter_report.py` runs `parent-recap ai-filter-report` on an archive of three evenings in
Finnish, English and Chinese, as the evening run writes it. It checks the counts of evenings, messages
and Held-back Messages by category, of the names that become placeholders in senders and in the text
with each evening's own list (the earlier Brief's people included), and of phone numbers, email
addresses and links, with no Gmail message link or Wilma student number among them. It checks the
estimate of names missed beside a role, a title or a name's placeholder, where a Kid's alias
(小狮妈妈) and 周五开家长会 don't count. It
checks that no name, contact detail, link or message text reaches the output, that the report makes no
network call and starts no program, that with the AI filter off it says what the filter would do, that
an evening whose Brief wasn't delivered and a file that can't be read are skipped, and that a missing
or empty archive gets a friendly answer. With `--held-back` and its output a terminal (the test fakes
`isatty`), it checks that every Held-back Message is listed after the counts, by date, with its Source,
category, the word that matched as the message writes it, sender, subject or WhatsApp chat and the text
around the word, also with the AI filter off. An evening of messages sent to everyone beside one to
the Household counts and lists only that one and the message a Wilma notification email copies, as
the evening run holds them back. A long notice to the whole class, read again a day late,
comes first by its own date and shows about 40 characters on each side, on one line, with no word cut
in two and no newline, tab or terminal escape code, and a message held back for its sender's title
shows the name without the address. Piped or captured, `--held-back` prints the same counts, no name,
contact detail, link or message text, and a line saying to run it in the macOS Terminal app. So does
it in a terminal with `CLAUDECODE`, `AI_AGENT` or one of Codex's variables set, as in the shell of a
command Claude Code or Codex runs, also one run with `!`. The harness clears these, so the suite
runs the same inside Claude Code or Codex.
`test_busy_model.py` runs the evening Brief against a `codex` or `claude` that is busy (at capacity,
rate-limited, overloaded) for a few calls. The harness records the pauses between model calls on a
fake clock instead of sleeping. It checks that the Brief comes once the model is free, after pauses
of 1 and then 3 minutes that the log names, that a model that is signed out, has an expired token or
has used up its plan or its API credit fails at once with the usual Brief without its Digest, that "at capacity" in
a Household's own message echoed back by `codex` isn't taken for a busy model, that the pauses stop
inside the call's time budget, and that doctor and the setup page's preview wait less or not at
all. `test_eval_runner.py` checks the eval runner tries a busy model again the same way.
`test_source_failures.py` runs the real Gmail, Wilma and MyClub Sources against a fake IMAP server,
wilma CLI and MyClub feed, for a Source that fails partway through a night or times out, and the
real WhatsApp Source against `test_setup_whatsapp.py`'s fake Mac without the permission, checking
that the Brief and the log name a changed Python as the likely cause.
`test_doctor.py` checks that doctor speaks English, that `doctor` and `bg doctor` on
`test_setup_whatsapp.py`'s fake Mac fail with the Python's real path and "add it to Full Disk
Access" without Full Disk Access and pass with it, with no question from macOS either way, and
compares the Python given Full Disk Access with the evening job's in its plist: quiet when they
match or with no record (an install from before it), a warning with the path to add when they
differ, and a failure when the job's venv Python points at nothing.
`test_own_node.py` lays out Parent Recap's own Node and a wilma CLI in the test's `~/ParentRecap`
(`fake_node.py`: a stand-in `node` that runs the script it's given, and a shell script answering
as the CLI does) with no Node on PATH, and checks that `doctor` and the evening job run that CLI
on that Node, never a Node or wilma CLI the Mac has, that `doctor` without the CLI says to
connect Wilma again rather than to install Node, and that the newest pinned Node is picked.
`test_setup_gmail.py` runs `parent-recap setup gmail` against a fake macOS dialog, `open`,
Keychain and IMAP server, and checks the App Password never reaches the output, the logs or a
command line.
`test_setup_wilma.py` runs `parent-recap setup wilma` against a fake wilma CLI on Parent Recap's
own Node (`fake_node.py`) and a Terminal
that runs the sign-in script at once, and checks the Kids, the city from every preset Wilma
address, the window's guide in each language, that the window ends the CLI before its student
picker and menu, that the window still closes when the CLI writes something just as it's ended
(macOS can hold up the CLI's exit until that is read), that a missing CLI is installed with
that Node's npm into `~/ParentRecap/wilma` and recorded, that without that Node it says to run
the install line again, and that the Wilma password never reaches the output. A window still
open after 30 seconds is ended, with the processes it started, and its test fails with what the
window showed. Where no pseudo-terminal
can be opened (a sandbox), the window talks to the fake CLI through `no_pty/sitecustomize.py`.
`test_setup_claude.py` runs `parent-recap setup claude` against a fake Terminal, macOS dialog,
Keychain and `claude` test call, and checks the token never reaches the output, the logs or a
command line. `test_setup_claude_token.py` keeps the older Terminal script working.
`test_setup_whatsapp.py` runs `parent-recap setup whatsapp` against a fake `launchctl` that runs
each `bg` job in-process, and a fake Mac in the temporary HOME: a stand-in for the file only Full
Disk Access opens, which those jobs can open once the fake Mac gives them Full Disk Access, and a
WhatsApp database whose every read without it makes the fake macOS ask about data from other apps,
and lets one through on Allow. The real macOS permission database is never touched. It checks
each permission state, that WhatsApp is never read before Full Disk Access is on, so macOS never
asks and an Allow click can't make the step pass, the chats and their Kid hints, that WhatsApp is
never read outside a `bg` job, and that once it's read the real path of the Python with Full Disk
Access is recorded, and nothing without it.
`test_setup_myclub.py` runs `parent-recap setup myclub` against a fake macOS dialog, `open` and
MyClub server, and checks the link never reaches the output, the logs or a command line, that a
failed download names only the server and the HTTP status, and that the older Terminal script
still works.
`test_setup_status.py` runs `parent-recap setup status` against a fake `launchctl`, `pmset`,
Keychain, IMAP server and `claude` test call, with a real Brief sent through the harness. It checks
each of the five outcomes true and false, that the first Brief counts once it has reached the setup
parent, and that no secret or message text reaches either form.
`test_setup_save.py` runs `parent-recap setup save` with answers on stdin against real config and
progress files in the temporary HOME. It checks the config it writes loads, that answers not given
leave the config as it was, that invalid answers are refused with the reason and nothing saved,
that a MyClub link is never repeated, and that progress is saved and read back, with the partner
Welcome chose kept in it.
`test_setup_page.py` starts the setup page's server in-process and drives it with HTTP calls, as
the page makes them. It checks that a request without the code, or for another Host or from
another Origin, is refused, that the page loads nothing from the internet, that the server stops
after 30 idle minutes and Terminal then says, in the language picked, that its window can be
closed, the Mac's language preselected, that the language picked is saved, that
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
against Parent Recap's own Node with a faked npm and a fake wilma CLI, laid out in `~/ParentRecap/wilma`
as `npm install -g --prefix` lays out the pinned version with Wilma's tenant list inside (`fake_node.py`), which reads the profile the page writes the way the
pinned CLI's own code does and signs in to a fake Wilma that knows one account. It checks that
"Espoo" and "Esbo" find the Espoo entries, that the pinned CLI is installed when missing, with that Node and none of the Mac's, that without
that Node it says to run the install line again and no text suggests Homebrew, that a
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
in-process. It checks that the button shows the Python in Finder and opens Full Disk Access, that
the entry ticks itself on the read after Full Disk Access is given and an Allow click alone never
ticks it, that no WhatsApp text or picture names App Management or Allow, with the groups found and
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
password, that each outcome comes back true or false without its reason, that a health check that
isn't OK names only its checks, that another wake schedule
is replaced only once the family agrees, that a closed dialog or jobs that can't be loaded say so
and can be tried again, that a finished Household is only checked, with nothing installed again,
that the server stops once every outcome is true, and that "Finish for now" stops it only at
Finish and never while it's installing. It also checks that every Finish text, with each check's
next step, is in all three languages, and that Finish says a Mac that's shut down makes no Brief. Where no port can be listened on, a request
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
`stable` tarball, whose `install.sh` leaves a fake `parent-recap` for the line to open the setup
page with. It checks a first install, a second run updating, and that nothing it didn't put in
`~/ParentRecap/plugin` is replaced. It also checks that the line, with or without a flag, stops and
changes nothing over an install from before the rename (`~/FamilyBrief/app`, or an old
`com.family.*` job that runs `family_brief`), and that a kept archive in `~/FamilyBrief` alone
doesn't stop it. The setup page's install of the Claude Code plugin or the
Codex skills, once Welcome is saved, is in `test_setup_page.py`, with the harness answering
`claude plugin`.
`test_install.py` runs the real `install.sh` and pip offline, against packages `fake_packages.py`
makes in a folder, for a stand-in program. Its pinned Python comes from a fake `curl` serving a
stand-in python-build-standalone tarball (a copy of the Python running the tests, with its
standard library), its pinned Node from a stand-in nodejs.org tarball, the Mac's kind from a fake `sysctl` and `uname`, and a `python3` on PATH fails.
A framework build such as python.org's can't be copied that way, so these tests skip on one, and
fail on one in CI, which runs on uv's python-build-standalone Python for that reason.
It checks that a fresh install's `parent-recap` runs on the pinned Python, that the build is picked
for Apple Silicon, Intel and a Rosetta terminal by `sysctl`, that a rerun downloads nothing, that a
venv on another Python is rebuilt, that a newly pinned version goes into a new folder, removes the
old one only once pip has succeeded, and asks to grant WhatsApp's permission again only when the real path changed, that the pinned Node
is unpacked next to it, picked by chip the same way and replaced by a newly pinned one without
asking to grant anything, and that a failed download or a wrong checksum of either says so and
leaves the install as it was, the other runtime included.
It also checks that the versions in the constraints file are installed rather than the newest,
that a package published only as source stops the install without being compiled, and that an older prebuilt version is taken over a newer one that would
compile. It checks that every install writes a dated log of pip's output under `logs/` that starts
with one line about the Mac, with the home folder written as `~`, and that a failing pip step
shows a short message and the log's path instead of pip's output. It checks that the `google`
extra is installed, pinned and prebuilt, only when the config's calendar mode is google. It checks
that `app/constraints.txt` pins every dependency `pyproject.toml` names, the `google` extra's
included, and keeps `cryptography` below 49, that the default dependencies bring in no Google
package and no `cryptography`, and that `scripts/check_wheels.py` fails when a pin has no Intel wheel. It checks that `install.sh`
run alone stops over an old `~/FamilyBrief/app` too, and that `parent-recap` and `family-brief`
are the same entry point in `pyproject.toml`.
`test_without_google_packages.py` blocks Google's packages from import and checks that every
module imports, that the evening Brief with `.ics`, Weekend Picks, setup and doctor run, that google
mode without them sends the events as `.ics`, and that doctor says how to install them.
`test_issue_loop.py` sources `scripts/issue-loop.sh` and runs its merge step against a fake `gh`.
It checks that a PR is merged only once the `test` check has passed, also when it shows up after
the other checks, that a missing, failing or skipped `test` stops the loop with the reason and no
merge, that a merge GitHub refuses stops the loop with GitHub's message, and that the script run
rather than sourced still runs its main part.
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
Each run is saved under `~/ParentRecap/eval/` and compared with the previous one of the same
language (runs saved before Briefs had a language count as `zh`); the spread column shows
run-to-run noise. `--cases DIR` scores a private folder instead; the case format is described
in `src/family_brief/eval/cases.py`. Keywords in `expect` need Chinese, English and Finnish alternatives; give the Finnish as stems (`lupalap`, `retk`), since a Finnish Brief inflects them.
