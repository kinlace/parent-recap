# Tests

From `app/`:

```bash
python -m pip install --only-binary :all: -c constraints.txt -e '.[test,google]'   # once, in an isolated env
python -m pytest                     # whole suite
python -m pytest -m unit             # one layer (or: python -m pytest tests/unit)
```

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

The shared fakes (`conftest.py`, `fake_system.py`, `fake_node.py`, `fake_packages.py`), the golden
files and `no_google/` and `no_pty/` stay in `tests/`. Every layer's folder is on the import path,
so a test may import a helper from a test in another layer. CI runs the layers as separate steps,
fastest first (unit, integration, e2e, adapters, scripts), so a failure in the fast layers shows
in a minute.

CI runs the suite on macOS and on Windows. Windows runs the unit, integration and adapter layers,
and the end-to-end tests marked `windows`. A test that needs macOS is marked `@pytest.mark.macos`
(a whole module with `pytestmark = pytest.mark.macos`) and skips itself elsewhere: the macOS
adapters, macOS-only features (WhatsApp for Mac and its Full Disk Access, the Passwords app),
`install.sh` and the other shell scripts, permission bits, and what Windows can't do yet (finding
programs by bare name, shell-script stand-ins, the pseudo-terminal windows). Every other test in
those layers runs on Windows too. When a Windows ticket makes one of these work there, it drops the mark. The
`windows_programs` fixture finds programs the way Windows does, on any system, so the setup page's
Codex and Claude Code installs and doctor's Codex and Claude checks are tested on the Mac too. A test that needs Windows is
marked `@pytest.mark.windows` the same way: the Credential Manager adapter
(`test_secrets_windows.py`), a Brief run by hand with the Gmail App Password and the Gateway Key in
Credential Manager (`test_brief_by_hand_windows.py`), Windows' own layout for programs and for
Parent Recap's own Node, the desktop adapter and the Wilma sign-in window, and the Task Scheduler
tasks, power plan and sign-in setting the evening run needs (`test_jobs_windows.py`,
`test_wake_windows.py`, `test_windows_only.py`), uninstall's removal of a program's folders once
the program running from them has ended (`test_windows_only.py`), the program installed with
`install.ps1`'s venv (`test_setup_status.py`), and the setup page from a fresh install to Finish,
then uninstall leaving none of it (`test_setup_page_windows.py`). The first two and the last store their secrets in the real
Credential Manager, and the Task Scheduler tests and the last register their tasks in the real
Task Scheduler, under a test name of their own, and delete them after. `install.ps1` itself runs in CI's
windows-install job, for real on a clean runner: a failed download; with PSReadLine loaded, as in
a family's PowerShell window, every type it names resolving to .NET's own and the install that
picks up from there; the installed program's setup page listing Wilma, Gmail, the AI and MyClub
with the gateway, ChatGPT and Claude offered, a second install that downloads and installs nothing, and the pinned wilma CLI
installed from npm with the program's own Node, running `--help` and shipping Wilma's town list,
and, after a config, a Credential Manager secret and the evening tasks, `parent-recap uninstall`
run from the installed program leaving no task, credential or folder of Parent Recap's.
`test_install_ps1.py` checks that the line ends on the setup page when pasted, and only installs,
exiting with its result, when run as a file.

`unit/` holds table-driven tests of pure modules, which fake nothing: the Brief's own text, the
Kids' placeholders, citations, the pure parts of Weekend Picks, how summarize parses and
normalises the model's reply, the AI filter and the held-back check on their own, the eval's
scoring and the llm package's in-memory adapter. A branch a unit test covers gets no end-to-end test of its own (#427).

The `test_nightly_*.py` files run the real `parent-recap run` with fakes only at the outside edges
(Sources, the `claude`/`codex` process, email, iMessage and Google Calendar); see `conftest.py`.
Each holds one topic, which its name says: the Brief a night makes (`test_nightly_brief.py`), Google
Calendar mode, nights in a row, Recipients' languages, the AI filter's placeholders and held-back
messages. The nights and helpers more than one of them uses are in `e2e/nights.py`.
The fake Sources honor seen-state like the real ones, so multi-night tests see only new Messages.
`test_nightly_continuity.py`'s `run --if-missed` tests sign in after an evening run that was missed or ran, and before or
after the first evening since `schedule install`.
A night whose only new Message is the next school day's timetable, and that the model finds nothing to
say about, sends no Brief, by email or iMessage, and its Messages aren't sent to the model again the
next night (#385). It still sends one with tonight's events, still says when no Source could be
read, and still shows in the setup page's preview. The harness's model says nothing by default, so a
test that needs the night's Brief to go out has the model reply `SAYS_SOMETHING` (`conftest.py`).
A Gmail with no sender ticked, beside another Source, is never read and isn't in the Brief's
coverage line; with no other Source it's still read and fails as the real collector does, so the
night says no Source could be read (#361).
`test_ai_filter.py` calls the AI filter on its own with a list of messages, as another tool could.
In `test_nightly_placeholders.py`, a night with phone numbers, email addresses and links in every part of the
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
with no spaces: none of them reaches the Brief or translation prompt, the partner's own name does,
and so do the Kids' names and aliases with the `mask_kids` switch off, a role in a sender stays
beside its placeholder, and the Brief, the `.ics` and the archive get the names back as the
messages wrote them. The night also has a coach whose sender names his club, a Chinese pupil
written by the given name alone (小明), a
pupil named Leon beside the Kid Leo (Leon stays), and a newsletter from a news@ address (no one).
A name from an earlier Brief's night is masked too, and a person placeholder changed beyond
repair is said as "someone" in each Recipient's language. Pilot feedback links carry an item as
the AI saw it: a teacher in a Finnish case form, a teacher and a phone number inside Chinese text
with no spaces, and a name the model wrote out itself go as that night's placeholders, the Kids as
Kid A and Kid B, also with the AI filter off, and the Household as a pseudonym that is the same
the next evening and differs for another label. The Form keeps the fields `ops/feedback-form`
gives it, in their order. With the AI filter's `mask_kids` switch on (#268), as by default, the
same night's Brief and translation prompts have no Kid's name or alias either, only Kid A and Kid B
(Leon, Leo's genitive, as Kid B:n), and the Brief and archive get the Kids' names back; Kids called
Elias and Pekka go as Kid A and Kid B in the forms their stems change in (Eliaksen, Pekan) and come
back as Pekalle. With the switch off, the Kids' names reach the model as before.
`test_ai_filter.py` checks the names on their own: a full name and its parts as one person, stems that change (Pekan, Niemen, Markuksen),
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
their words: a Wilma announcement (the same words in a Wilma message to the Household are), a Wilma
message to 15 or more addressees (one to 14, to two, to twelve, or to addressees Wilma hides is
checked), an email the Gmail Source
reads with a `List-Id`, `List-Unsubscribe` or `Precedence: bulk` or `list` header, and the
announcements in made-up Wilma notification emails in Finnish and English. The message such an email
copies is held back, and the match points into it. A Wilma email with no announcements heading, or
with a mailing list's headers, is checked whole. In
`test_nightly_held_back.py`, a night with a sensitive message from each category in each language checks
that none of them reaches the Brief's or the translation's prompt, and the `held_back.*` goldens show
the Brief listing each Held-back Message by Source, sender and subject, with its Gmail link, its page
in the Wilma the CLI's profile in the temporary HOME names, or a note to read it in WhatsApp. It also
checks that a night with only Held-back Messages sends a Brief with no model call and no "Written by",
that such a newsletter and a camp notice about medication still reach the model, that the archive
keeps them, that the rule-based fallback doesn't list them twice, that the setup page's preview and
`parent-recap summarize` hold them back too, and that with the AI filter off they go to the model as
before. The setup page's preview leaves one line in `run-stderr.log` with each Source's count, the
held-back count and the size of the Digest, Notices, Action Items and new events, and no message
text, sender, subject, Kid's name or error text. A night with a Wilma announcement, a city's mass
email, a Wilma message to the class's 34 guardians and a Wilma notification email with
only announcements, each with a sensitive word, sends them to the model, and the Brief lists only a
Wilma message to the Household's two guardians and a notification email that copies one. `test_eval_runner.py`
checks that the eval's Brief call and its judge never get one either.
`test_ai_filter_report.py` runs `parent-recap ai-filter-report` on an archive of three evenings in
Finnish, English and Chinese, as the evening run writes it. It checks the counts of evenings, messages
and Held-back Messages by category, of the names that become placeholders in senders and in the text
with each evening's own list (the earlier Brief's people included), and of phone numbers, email
addresses and links, with no Gmail message link or Wilma student number among them. It checks the
estimate of names missed beside a role, a title or a name's placeholder, where a Kid's alias
(小狮妈妈) and 周五开家长会 don't count. It
checks that no name, contact detail, link or message text reaches the output, with or without
`--held-back` and with the output captured or a terminal (the test fakes `isatty`), that the report
makes no network call and starts no program, opening only the list, in the text editor, that
with the AI filter off it says what the filter would do, that an evening whose Brief wasn't
delivered and a file that can't be read are skipped, and that a missing or empty archive gets a
friendly answer. A held-back message and one with a name, a phone number and a link, archived again
on later evenings, count once, so the counts and the list are the same as without the copies. With
`--held-back`, it checks that every Held-back Message is listed in `held-back-list.txt` in the
archive folder, by date, with its Source, category, the word that matched as the message writes it,
sender, subject or WhatsApp chat and the text around the word, also with the AI filter off. The file
is owner-only, also when it replaces an older list others could read, and the output is the counts
and one line with how many messages it lists and where, now open in the text editor. When nothing
opens, the line says where the file is instead. An evening of messages sent to everyone beside one
to the Household counts and lists only that one and the message a Wilma notification email copies,
as the evening run holds them back. A long notice to the whole class, read again a day late,
comes first by its own date and shows about 40 characters on each side, on one line, with no word cut
in two and no newline, tab or terminal escape code, and a message held back for its sender's title
shows the name without the address. With nothing held back, it says so and opens nothing.
`test_busy_model.py` runs the evening Brief against a `codex` or `claude` that is busy (at capacity,
rate-limited, overloaded) for a few calls. The harness records the pauses between model calls on a
fake clock instead of sleeping. It checks that the Brief comes once the model is free, after pauses
of 1 and then 3 minutes that the log names, that a model that is signed out, has an expired token or
has used up its plan or its API credit fails at once with the usual Brief without its Digest, that "at capacity" in
a Household's own message echoed back by `codex` isn't taken for a busy model, that the pauses stop
inside the call's time budget, and that doctor and the setup page's preview wait less or not at
all. `test_eval_runner.py` checks the eval runner tries a busy model again the same way.
`test_llm_claude.py`, `test_llm_codex.py`, `test_llm_gateway.py` and `test_llm_memory.py` test each
adapter of the `llm` package through its Backend interface (ADR 0023), the first three at the
harness's fake `claude`, `codex` and gateway. The Claude adapter's tests are where Claude is shown to
run isolated: no tools, MCP or user settings, an empty working dir, and none of the family's own
Claude Code session in its environment.
`test_source_failures.py` runs the real Gmail, Wilma and MyClub Sources against a fake IMAP server,
wilma CLI and MyClub feed, for a Source that fails partway through a night or times out, and the
real WhatsApp Source against `test_setup_whatsapp.py`'s fake Mac without the permission, checking
that the Brief and the log name a changed Python as the likely cause.
`test_wilma_source.py` runs the real Wilma Source against a fake wilma CLI that prints the pinned
2.x's JSON, all of it made up. It checks that a message's text, sender and the number of different
names it is addressed to are read, and that no addressee's name reaches the AI or the archive.
A hidden, empty or blank list counts as none. A message to a class's 34 guardians reaches the AI,
and one to the Household's two is held back. A news item's text comes from its own JSON, and a
pinned one with no date is marked seen and never read. A message with no time is left as old. The
CLI's `login_failed` error on stdout is said in the Brief as a failed login, and it alone counts as
a wrong password. A sign-in that didn't answer beside one that did says Wilma was partly read. What
a CLI before 2.0 printed is still read, with no addressees. With two made-up Wilmas, two items with
one id are both read, each linked to its own Wilma, and neither address reaches the AI. Ids kept
before 2.0, without their Wilma, still count as seen for the Wilma signed in with last. A single
sign-in's items link to its Wilma. A failing sign-in for none of the Kids stays out of the Brief
and is logged by its username and Wilma, and one for a Kid makes the Brief say Wilma was partly
read. Doctor names a sign-in setup didn't make for none of the Kids, with how to remove it.
`test_whatsapp_source.py` runs the real WhatsApp Source against a small `ChatStorage.sqlite` it
builds in the temporary HOME with WhatsApp's own tables and columns for chats, messages, group
members and push names, with Full Disk Access given. It checks that only the configured chats are
read, by exact name, and a configured one missing from the database is a warning; that only
messages inside the lookback (26 hours, or the config's) are read, in time order, with their
times converted from Core Data's 2001 epoch; that system events are skipped; that media without
text gets its placeholder and a row with neither is marked seen and not returned; that text is
trimmed and a long message cut; that a message read one night isn't read the next though it is
still inside the lookback; that each Message has its chat, sender (contact name, first name, push
name, "me" or "(group member)") and Kid hint, none for a chat without a Kid; and that the
snapshot is removed and the live database left as it was.
`test_doctor.py` checks that doctor speaks English, that `doctor` and `bg doctor` on
`test_setup_whatsapp.py`'s fake Mac fail with the Python's real path and "add it to Full Disk
Access" without Full Disk Access and pass with it, with no question from macOS either way, and
compares the Python given Full Disk Access with the one the evening job runs: quiet when they
match or with no record (an install from before it), a warning with the path to add when they
differ, and a failure when the job's venv Python points at nothing. On that fake Mac it also
checks each reason the WhatsApp check gives when it isn't OK: no Full Disk Access, a `bg` job that
hangs or doesn't start, a ticked group with a new name, and groups that can't be listed. The
reason, with the group's old name, comes back from the `bg` job as a line of JSON, doctor's own
line stays as it was, and the setup skill's advice for each reason quotes doctor's words. No skill
or doc sends the parent to a file called python3, since Finder selects one such as python3.13.
A Gmail with no sender ticked is OK beside another Source, signed in without a search, and fails
with no other Source (#361).
Off macOS, doctor has no WhatsApp check and never reads WhatsApp.
The OS services in `family_brief.system` (secrets, the evening jobs, the daily wake, the desktop)
are in-memory fakes in every test that runs the program (`fake_system.py`, in place of each
interface's adapter, as `harness.secrets`, `harness.jobs`, `harness.wake` and `harness.desktop`),
so those tests fake none of these services' commands; only macOS-only features, which have no
interface, still meet theirs there (the secret dialog, iMessage, Finder and the Passwords app).
Only the macOS adapters' own tests fake the services' commands:
`test_desktop.py` checks against a fake `open` and `defaults` that pages open in the browser and
files in TextEdit, that the Mac's languages are read, and that a terminal window's script, run as
Terminal runs it, runs its command in the home folder with the variables it was given, and leaves
nothing behind.
`test_secrets.py` reads, stores and deletes secrets against a fake `/usr/bin/security` and its
Keychain: each goes in on `security -i`'s stdin, on no command line, trusting `/usr/bin/security`,
one an earlier version stored is replaced, a typed one is asked for by `security` itself, and it
checks the reason given when the Keychain asks first, refuses, or can't be reached from tmux or SSH.
`test_jobs.py` installs and removes the evening jobs against a fake `launchctl`: Weekend Picks' job
goes when they're turned off, a job not loaded after a restart waits for a login, removing them all
takes an older version's job from setup's record too, a job started by hand tells one not loaded
from one already running or refused, a job's logs are owner-only and its PATH has the native
installer's folder and what the login shell finds, and a `bg` run that is stopped by a signal or
times out leaves no job or output behind.
`test_wake.py` sets and cancels the daily wake against a fake `pmset` and administrator dialog,
telling setup's wake apart from the Mac's others and one-off events, that the dialog says who asks,
that a Mac sleeping on battery still needs the wake, and that with no dialog to ask in, or over
SSH, it isn't set.
`test_jobs_windows.py` checks on any system the Task Scheduler task each job becomes (its time, a
missed start caught up, the PC woken, run only while signed in, also on battery, and started through
pythonw with its log files), the evening job's sign-in task that runs `run --if-missed`, and that `bg` runs as a hidden child process; on Windows it registers,
lists, starts and removes real tasks. A runner can't show that a task wakes a sleeping PC.
`test_wake_windows.py` reads "Allow wake timers" and the sleep timeouts from a fake `powercfg`
worded in Finnish, and never changes them. `test_windows_only.py` checks that `doctor` on Windows
reports the tasks, the wake timers and the sign-in after an update restart, each with its fix, and
how that sign-in is read from the registry.
`test_own_node.py` lays out Parent Recap's own Node and a wilma CLI in the test's `~/ParentRecap`
(`fake_node.py`: a stand-in `node` that runs the script it's given, and a shell script answering
as the CLI does) with no Node on PATH, and checks that `doctor` and the evening job run that CLI
on that Node, never a Node or wilma CLI the Mac has, and with `WILMAI_NO_UPDATE_CHECK=1`, so it
never looks for a newer version, that `doctor` without the CLI says to
connect Wilma again rather than to install Node, and that the newest pinned Node is picked. On
Windows, `fake_node.py` lays them out as Node's zip and npm do there (`node.exe` with npm beside
it, the CLI's package in `node_modules`), and the tests check that `doctor` runs the package's own
script on that `node.exe` and that npm runs beside it. `test_finding_programs.py` checks on Windows
that a `claude.exe` from Claude Code's own installer and a `codex.cmd` in npm's global folder are
found off PATH. `test_install_ps1.py` checks that `install.ps1` pins the Python and Node versions
`install.sh` pins, and is ASCII only, as Windows PowerShell 5.1 needs.
`test_email.py` runs the real email action against a fake SMTP server and fake secrets, and
checks the message handed to the server: UTF-8 plain-text and HTML parts with Finnish and Chinese
intact, an `.ics` attachment with `method=PUBLISH` beside them or the alternative alone, Subject,
From and every Recipient in To, a login with the stored App Password after STARTTLS, and that
nothing is sent with no App Password (a clear error) or no Recipients.
`test_setup_gmail.py` runs `parent-recap setup gmail` against a fake macOS dialog, desktop,
secrets and IMAP server, and checks the App Password never reaches the output, the logs or a
command line.
`test_setup_wilma.py` runs `parent-recap setup wilma` against a fake wilma CLI on Parent Recap's
own Node (`fake_node.py`) and a desktop whose terminal window runs the sign-in command at once,
and checks the Kids, the city from every preset Wilma address, the window's guide in each language,
that the window ends the CLI before its student picker and menu, that the window still closes when
the CLI writes something just as it's ended (macOS can hold up the CLI's exit until that is read),
that a missing CLI is installed with that Node's npm into a new folder next to
`~/ParentRecap/wilma`, which then takes its place, and recorded, that without that Node it says to
run the install line again, that the window's CLI never looks for a newer version, and that the
Wilma password never reaches the output. It checks that `python -m family_brief.setup_wilma update`,
which `install.sh` runs, with the program's umask, installs the pinned version over an older one
with that Node's npm, leaves the pinned one and a Household without the CLI alone, and says so
when npm or the Node fails, keeping the CLI that was there, without failing the install. An npm
ended at the time limit halfway through leaves the CLI as it was, still signing in, and nothing
beside it, and on a first install leaves nothing at all. A CLI left half installed, with no
`bin/wilma`, is installed again when the CLI's config has a sign-in, and left alone without one. A window still
open after 30 seconds is ended, with the processes it started, and its test fails with what the
window showed. Where no pseudo-terminal
can be opened (a sandbox), the window talks to the fake CLI through `no_pty/sitecustomize.py`.
`test_setup_wilma_windows.py` checks on Windows that the window runs the CLI's own sign-in page
(`wilma login`) on Parent Recap's own Node under the guide, ends it once it has saved its profile,
and otherwise ends with the CLI's own exit status. `test_desktop_windows.py` checks on Windows that
pages open with the default browser, files in Notepad, and that a PowerShell window, whose command
is run by Windows PowerShell itself, runs it in the home folder with its variables and every word
as given, that the Claude sign-in window runs `claude setup-token` there, and that a word with a
double quote, which Windows PowerShell would drop, is refused. The setup page's Wilma tests run on
Windows too, with the fake CLI on CI's Python.
`test_setup_claude.py` runs `parent-recap setup claude` against a fake terminal window, macOS
dialog, secrets and `claude` test call, and checks the token never reaches the output, the logs or a
command line, and that the window clears the token from its screen and scrollback once the family
presses Enter. `test_setup_claude_token.py` keeps the older Terminal script working, against fake
secrets.
`test_gateway.py` runs the evening Brief, doctor and `parent-recap setup gateway` with
`backend: gateway` against a fake gateway (`requests.post` to its `/v1/messages`, answered from the
harness's model replies), secrets and macOS dialog. It checks the Brief's and translations' calls
go to the configured address with the Gateway Key and `model: parent-recap`, and no `claude` or
`codex` runs; that 429 and 5xx are tried again after the usual pauses, and 401, 402, a refusal,
a reply that isn't the gateway's and the gateway's own credit run out fail at once with the usual
Brief without its Digest; that a key past today's budget, said with a 402 or any error status,
says so in each Recipient's Brief and when its credit comes back, though a Brief that mentions a
budget exceeded is no such error; that a timeout is tried once more and an unreachable gateway
isn't; that doctor says whether the gateway answers and whether the key works, is past today's
budget or is missing; that setup tests the key before storing it; and that the key never reaches
the output, the logs, a command line, an email or a file in HOME, even when the gateway echoes it
back.
`test_setup_whatsapp.py` runs `parent-recap setup whatsapp` against the harness's jobs, which run
each `bg` job in-process, and a fake Mac in the temporary HOME: a stand-in for the file only Full
Disk Access opens, which those jobs can open once the fake Mac gives them Full Disk Access, and a
WhatsApp database whose every read without it makes the fake macOS ask about data from other apps,
and lets one through on Allow. The real macOS permission database is never touched. It checks
each permission state, that WhatsApp is never read before Full Disk Access is on, so macOS never
asks and an Allow click can't make the step pass, the chats and their Kid hints, that WhatsApp is
never read outside a `bg` job, and that once it's read the real path of the Python with Full Disk
Access is recorded, and nothing without it.
`test_setup_myclub.py` runs `parent-recap setup myclub` against a fake macOS dialog, desktop and
MyClub server, and checks the link never reaches the output, the logs or a command line, that a
failed download names only the server and the HTTP status, and that the older Terminal script
still works.
`test_setup_status.py` runs `parent-recap setup status` against the harness's jobs, wake and
secrets, a fake IMAP server and `claude` test call, with a real Brief sent through the harness. It
checks each of the five outcomes true and false, that the first Brief counts once it has reached the
setup parent, and that no secret or message text reaches either form.
`test_setup_save.py` runs `parent-recap setup save` with answers on stdin against real config and
progress files in the temporary HOME. It checks the config it writes loads, that answers not given
leave the config as it was, that invalid answers are refused with the reason and nothing saved,
that a MyClub link is never repeated, and that progress is saved and read back, with the partner
Welcome chose kept in it.
`test_setup_page_windows.py` walks the setup page from a fresh install to Finish, as a family
without Wilma and with a Gateway Key does: the language, Welcome, each Source the page lists (no
WhatsApp off macOS), Working, Check, the first Brief sent to the setup parent, and Finish with
Weekend Picks on, against the harness's IMAP server, gateway, MyClub and Sources. It checks both
evening jobs are installed and the secrets stored where the evening run reads them; on Windows a
second run does the same with the real Credential Manager, Task Scheduler and power settings, and
no macOS dialog or app.
The setup page's tests start its server in-process and drive it with HTTP calls, as the page makes
them. They are split by step, each file named for it: the command, its safeguards, the language,
resuming, the Source list and the page on Windows in `test_setup_page.py`, then
`test_setup_welcome.py`, `test_setup_ai.py` (with the gateway and changing the AI on Finish),
`test_setup_check.py` (Working and Check), `test_setup_first_brief.py`, `test_setup_finish.py` and
`test_setup_chat.py` (the chat Welcome sets up, and "Continue in the chat"). Each Source's step is in
that Source's one file, after the tests of its `setup` command: `test_setup_gmail.py`,
`test_setup_wilma.py`, `test_setup_whatsapp.py` (with why WhatsApp isn't OK on Check and Finish) and
`test_setup_myclub.py`. The server, the calls and the steps more than one file walks through are in
`e2e/setup_page.py`. They check that a request without the code, or for another Host or from
another Origin, is refused, that the page loads nothing from the internet, that the server stops
after 30 idle minutes and Terminal then says, in the language picked, that its window can be
closed, the Mac's language preselected, that the language picked is saved, that
every page text is in all three languages, that a second run resumes at the saved phase, and that
off macOS the page offers neither WhatsApp nor the Passwords app. With `sys.platform` set to
Windows, it checks that the page words itself for Windows and offers the gateway, ChatGPT and Claude
as the AIs, installing Codex with OpenAI's installer, and Claude Code with its own Windows
installer before opening `claude setup-token` in a PowerShell window, whose pasted token it
tests and stores, that every text the page shows there names the PC,
Credential Manager and PowerShell through its `.windows` twin and nothing only macOS has, that the
window says in PowerShell's words that it can be closed, and that Finish names Windows'
sign-in-after-update check and leaves its Wake check to the wake outcome. On
Welcome, it checks each choice's default and that the answers are saved, that the partner's email
and language are asked only with a partner, and that a missing or signed-out Claude Code or Codex
is caught and checked again: the harness answers `claude auth status` and `codex login status`
from `harness.signed_in`, and the test puts a fake `claude` or `codex` on PATH to install one.
It also checks that the install box and its Copy button start hidden, that `hidden` wins over the
box's own layout in the CSS, and that a ready AI gets no install line. Welcome's "Have these
ready" list starts folded, and in all three languages names the Wilma login with Wilma's own link
for a forgotten password, one MyClub link per Kid with the buttons that make it, 2-Step
Verification for Gmail's App Password, and the phone that signs in WhatsApp for Mac, in place of
the old Wilma sentence.
On Connect, it checks the Source list's statuses, each row's name with its status in every
language, skipping and coming back, and the page's Gmail
step against a fake IMAP server and secrets: a valid App Password is stored and Gmail turns done,
each known result has its explanation in all three languages, and the App Password never reaches
a response, the output, the logs, a command line or a file in `~/.family`. Its Wilma step runs
against Parent Recap's own Node with a faked npm and a fake wilma CLI, laid out in `~/ParentRecap/wilma`
as `npm install -g --prefix` lays out the pinned version with Wilma's tenant list inside (`fake_node.py`), which reads the profiles the page writes the way the
pinned CLI's own code does, every one of them as 2.x does, signs in to a fake Wilma that knows one
account, saves the Kids and a session back as 2.x does, and answers in 2.x's JSON, errors too. It checks that
"Espoo" and "Esbo" find the Espoo entries, that the pinned CLI is installed when missing, with that Node and none of the Mac's, into a new folder that then takes its place, that without
that Node it says to run the install line again and no text suggests Homebrew, that while
the CLI installs, or if it doesn't, the greyed-out town box says why right under it, that
while it installs a family without Wilma reads that the town list is being got ready, not
Wilma, and the text follows a changed answer, that a
good login writes the profile in the CLI's format and the page lists the Kids from it, that the
CLI checked it alone in a temporary config that is gone afterwards, with the session it saved, and
never looked for a newer version, that a wrong password is reported as such, also when an earlier
sign-in works, that signing in again as the same account in another case keeps that profile's
place, id and two-step key, and any other failure offers the Terminal window (which the test
answers by saving the CLI's profile), that a sign-in that fails leaves the
CLI's config as it was, an earlier profile included, that a Household without Wilma saves only its town, and that the Wilma
password never reaches a response, the output, the logs, a command line or `~/.family`.
Its AI sign-in runs a fake `claude` whose `setup-token` draws the token on the pseudo-terminal
the page gives it, with Ink's escape sequences, and a fake `codex` whose `login` the harness's
`codex login status` then reports signed in. It checks that Claude's token is read, tested with
the harness's test call and stored with nothing copied, that a sign-in without a token, or one
that times out, offers the Terminal window and the page takes the pasted token in its field, that
the token never reaches a response, the output, the logs, a command line or `~/.family`, and that
the Codex entry ticks itself once Codex is signed in. For the gateway (ADR 0014), answered by the
harness's fake `/v1/messages`, it checks that Welcome offers it as ready with no Claude Code or
Codex run and no chat installed, that a Gateway Key pasted in its field is tested with the gateway
the config names and stored, that a key the gateway refuses or can't test is explained without
what the gateway said and not kept, that the key never reaches a response, the output, the logs,
a command line or `~/.family`, and that the page says the messages go through the maintainers'
gateway in all three languages.
Its WhatsApp step runs against `test_setup_whatsapp.py`'s fake Mac, with each `bg` job run
in-process. It checks that the button shows the Python in Finder and opens Full Disk Access, that
the entry ticks itself on the read after Full Disk Access is given and an Allow click alone never
ticks it, that no WhatsApp text or picture names App Management or Allow, with the groups found and
their Kid hints kept in setup's progress for the check page, that every other read result is
said without its error, and that each result is explained in all three languages. It checks that
the texts naming the Python file, on this step, Check and Finish, and the step's pictures give the
name of the file the button selects in Finder, such as python3.13, also for a newly pinned
python3.14, and that no text names a python3 file of its own.
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
offered, that a connected Wilma's or MyClub's own mail isn't ticked (a skipped one's is), that the
no-sender note doesn't say the Briefs come out empty when another Source brings the news, that
each Recipient comes with their language and the evening at 21:00, and that
confirming saves it all through `setup save` and runs the health check, faked so it can be seen
running, except in one test that runs doctor's real checks against the harness's fakes. It checks
that a failing check keeps the page and names only the check, that warnings alone move setup on,
and that answers the page didn't offer are refused with nothing saved. For each reason the
WhatsApp check gives on `test_setup_whatsapp.py`'s fake Mac, run for real through the `bg` job,
it checks that the page gets the reason, and the groups not found, with no path or error, and has
its own text in all three languages: Full Disk Access with the button that opens it, saying only
the parent can turn it on, or the groups named with the way back to WhatsApp's step, which reads
the groups again for Check. A reason the page doesn't know gets the general text. With a Source
skipped, the check page offers one link back to Connect's list, where it can be opened, never
while the health check runs.
First Brief makes the real Brief through `run --preview` in a `bg` job on `test_setup_whatsapp.py`'s
fake Mac, with the harness's Sources and model, and one test fakes the job to see its progress.
It checks that the preview returns the email's HTML and sends or records nothing, that the frame
it's shown in is sandboxed, that a quiet three days still make one, that "Send it to me" sends that
Brief to the setup parent only and the outcome check counts it, that a send that fails, or one
during an evening run, says so, and that only a pilot Household gets the feedback button, which
opens the Digest's pre-filled Form link.
Finish runs against the harness's jobs and wake, whose dialog sets the wake-up once the test lets
it go, with doctor's checks faked as on the check page. It checks that the
evening job is loaded and the wake-up set through macOS's dialog only, that the API takes no
password, that each outcome comes back true or false without its reason, that a health check that
isn't OK names only its checks, with why WhatsApp isn't and its own text in all three languages,
that Finish leads back to WhatsApp's step, or to Connect's list for a skipped Source, while the
page is open, that a page opened again after setup with a Source skipped stays open for that
once every outcome is true, and one with none skipped stops, that another wake schedule
is replaced only once the family agrees, that a closed dialog or jobs that can't be loaded say so
and can be tried again, that a finished Household is only checked, with nothing installed again,
that the server stops once every outcome is true, and that "Finish for now" stops it only at
Finish and never while it's installing.
It checks that Change the AI on Finish (ADR 0017) switches `llm.backend` to Claude, ChatGPT or
the gateway only once the new AI's test call passes, a call to Codex too there but not in Connect,
that a failed one keeps the old AI, and that a gateway Household can enter a new Gateway Key.
It also checks that every Finish text, with each check's next step, is in all three languages, and that Finish says a Mac that's shut down makes no Brief. Where no port can be listened on, a request
rung in as the server stops times out, as a closed port refuses it.
Its "Continue in the chat" runs the terminal window's command the page opens against a fake
`claude`, and
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
Codex skills, once Welcome is saved, is in `test_setup_chat.py`, with the harness answering
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
package and no `cryptography`, and that `scripts/check_wheels.py` fails when a pin has no Intel wheel. It checks that every install asks
`family_brief.setup_wilma` to move the wilma CLI to the pinned version. It checks that `install.sh`
run alone stops over an old `~/FamilyBrief/app` too, and that `parent-recap` and `family-brief`
are the same entry point in `pyproject.toml`.
`test_without_google_packages.py` blocks Google's packages from import and checks that every
module imports, that the evening Brief with `.ics`, Weekend Picks, setup and doctor run, that google
mode without them sends the events as `.ics`, and that doctor says how to install them.
`test_issue_loop.py` sources `scripts/issue-loop.sh` and runs its merge step against a fake `gh`.
It checks that a PR is merged only once the `test` check has passed, also when it shows up after
the other checks, that a missing, failing or skipped `test` stops the loop with the reason and no
merge, that a merge GitHub refuses stops the loop with GitHub's message, and that the script run
rather than sourced still runs its main part. With a fake agent standing in for the checker, it
checks that the checker gets the implementer's report and only the review subagents' last words,
that a pass resumes the implementer's session for the PR, and that a failed check, no verdict, an
unreadable one or a checker that changed the branch stops the loop.
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
in `src/family_brief/eval/cases.py`. Keywords in `expect` need Chinese, English and Finnish alternatives; give the Finnish as stems (`lupalap`, `retk`), since a Finnish Brief inflects them. A keyword matches anywhere in the text, so check that a short stem can't hide in another word: `kuva` (photo) also matches `jatkuvat` (continue). Start it with a space (`" kuva"`) to match only at a word's start. A `notices` rule with `languages: [en, zh]` is checked only on Briefs in those languages, such as Finnish words an English or Chinese Brief must translate. A rule with `where: digest` checks the Digest's lines, which sit under no Kid, and `where: anywhere` the notices, action items and Digest together: a line the parents must never read, such as one saying there is nothing new, goes there.

`test_eval_scoring.py` checks the scoring on its own, and that a plain Finnish Brief scores clean
on the Finnish cases. `test_eval_runner.py` runs the eval with only the model process faked,
also with several models side by side, their cost, a placeholder the model changed beyond repair,
and the Kids as Kid A, Kid B by default, whose prompt has no Kid's name and whose run compares
with one with `--no-mask-kids`.

### Trying another model

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

### Masking the Kids' names too

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
