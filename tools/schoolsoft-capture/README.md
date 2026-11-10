# SchoolSoft capture

A tool a Swedish guardian runs on their own Mac, so the Parent Recap team learns what SchoolSoft's guardian data looks like and how long its sign-in lasts, without anyone else ever holding the guardian's account ([#876](https://github.com/kinlace/parent-recap/issues/876), from the spike [#871](https://github.com/kinlace/parent-recap/issues/871)). It is not part of Parent Recap and needs nothing of it installed.

## For the guardian

Paste this into Terminal:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/kinlace/parent-recap/stable/tools/schoolsoft-capture/get.sh)"
```

Your browser opens SchoolSoft. Choose **Extern inloggning**, then **Mobilt BankID**, and confirm on your phone. The tool then reads, and writes one file, `~/SchoolSoftCapture/schoolsoft-capture.json`, which opens for you to look at. Send it to the Parent Recap team only if you are happy with it.

If you agree, it also checks every evening for 14 days whether the sign-in still works, and writes `~/SchoolSoftCapture/refresh-log.txt`. Send that log too after 14 days.

To remove everything it stored (the sign-in, the evening check, the files), once you have sent what you want to send:

```bash
~/SchoolSoftCapture/remove
```

## What it does

1. Downloads its own Python (the build `install.sh` pins, checked against its checksum) and two files from this repository's `stable` branch into `~/SchoolSoftCapture`: `schoolsoft_capture.py` and Parent Recap's AI filter, `app/src/family_brief/ai_filter.py`.
2. Asks for the school's SchoolSoft address name (`sms.schoolsoft.se/<name>/`, default `academediagrundskolor`) and checks SchoolSoft knows it.
3. Signs in as SchoolSoft's guardian app does: OAuth with PKCE, client `vApp`, a `127.0.0.1` callback. The guardian does Extern inloggning and BankID in their own browser; the tool never sees anything but the code SchoolSoft sends back.
4. Reads, for each child: news, the next calendar event, the message inbox (and one message that was already read), the web session cookies for that child, this week's and next week's lessons, the two weeks' lesson and event agendas, this week's assignments (and one already read), plannings and the activity log.
5. Writes the file: for each answer, its status, how many items, its fields with the kind of each, and two samples, redacted. The access token's claims that name no one (`user_type`, `login_method`, `client_id`, `aud`, `iss`, its lifetime) are in it too, never a token.
6. If the guardian agrees, stores the refresh token in the Keychain and installs the launchd job `com.kinlace.schoolsoft-capture`, which runs `schoolsoft_capture.py refresh` at 20:15 every evening. Each run renews the sign-in once, saves the new refresh token at once, and logs the date, whether it worked and the token's age. A `RESULT:` line says on which day it stopped working, or that it still worked on day 14; then the token and the job remove themselves, and the log stays.

## What keeps it safe

- **Read-only.** Every request is one of `ENDPOINTS` in `schoolsoft_capture.py`: GETs, and three POSTs that change nothing (the two token requests and the activity log's list, which SchoolSoft's own page asks for with a POST). Redirects are never followed. A message or an assignment is opened only if it is already marked read, so opening it can't mark anything read; news is only listed. Nothing is ever sent that reports an absence, confirms a news item or marks anything read.
- **Only SchoolSoft.** The tool sends to `https://sms.schoolsoft.se` only; Grandid and BankID happen in the guardian's browser. No telemetry. `get.sh` downloads from GitHub and Astral's mirror of python-build-standalone, as Parent Recap's own install does.
- **Redacted.** People named in the answers' person fields (first and last names, authors, teachers, senders, recipients) become placeholders such as `⟦N1⟧` wherever their names stand, with the Swedish genitive (`⟦N1⟧:s`). Phone numbers, email addresses and links become `⟦P1⟧`, `⟦E1⟧` and `⟦L1⟧` through Parent Recap's AI filter, Swedish personal and coordination numbers `⟦S1⟧`, and any other capitalised word inside a sentence, which in Swedish is a name or a place, `⟦W1⟧`. Addresses, pictures and contact fields are left out whole, ids are renumbered, and long texts are cut at 600 characters. A name at the start of a sentence that no person field gives is the one thing this can miss, which is why the guardian looks at the file before sending it.
- **The token stays in the Keychain.** It is typed into `security -i`, never on a command line, and is in no log, file or terminal line: every one of them is scrubbed of the run's tokens, codes and cookies. A lock keeps two refreshes from ever running at once.

The sign-in flow and the endpoints follow [grimen/schoolsoft-agent](https://github.com/grimen/schoolsoft-agent)'s notes and code (MIT), which were verified on a guardian account; see [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).

## Tests

The tests run it against a fake SchoolSoft (`app/tests/fake_schoolsoft.py`); they are listed in [`app/tests/README.md`](../../app/tests/README.md).
