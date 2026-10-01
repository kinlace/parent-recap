# One Brief per night, translated into each Recipient's language

Each Recipient picks their own language, and any language is allowed. The model writes the night's Brief once, in the first Recipient's language, and one more call per extra language translates it. The program checks that every translation has the same Notices, Action Items, calendar events, dates and sources as the original. If a translation fails or doesn't match, that Recipient gets the original with a one-line note instead of nothing. What the whole Household shares stays in the first Recipient's language: Google Calendar events, the archive that feeds the next night's prompt, and the text carried by feedback links. Weekend Picks work the same way.

The program's own text in the Brief (headings, the coverage line, the fallback, calendar hints, the footer) is reviewed by us for `en`, `zh` and `fi` (checked by a Finnish speaker). For any other language the model translates that text once, when the language is first chosen, and the result is kept on the Mac, so a Brief never comes out half in one language.

## Considered Options

- **One language per Household.** Simplest, but mixed-language couples are common in Finland, and a Brief only one parent can read misses its point.
- **Summarize separately in each language.** Each call reads the raw messages on its own and can pick different Action Items, so two parents could get different to-do lists for the same night.
- **One bilingual Brief.** Doubles its length for everyone.
- **Only a fixed list of languages.** Every language would need our translation and eval work before a family could use it. Allowing any language lets a family start now, with the reviewed languages as the tested path.
- **English for untranslated program text.** Cheaper, but leaves the Brief in two languages.

## Consequences

- Each extra language in a Household costs one extra model call per night, and one per Weekend Picks.
- Only reviewed languages have golden Briefs and eval cases. Other languages are best effort, and the docs say so.
- On a night the one-time translation of a language's program text fails, that language gets the English program text and the translation is tried again on the next run, so the Brief still goes out. `doctor` shows a language still waiting for its text.
- Briefs in languages other than Finnish keep the key Finnish terms from the source messages (reissuvihko, vanhempainilta), because parents search Wilma and talk to teachers with them.
- A Household-wide `summary_language` stays as the default for Recipients who haven't picked one, so existing configs keep working unchanged.
