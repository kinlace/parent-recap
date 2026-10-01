# Changelog

What changes for families in each release, collected from the `What changes for families:` line of every PR merged since the previous release (see [Reviews](CONTRIBUTING.md#reviews)).

## Unreleased

- Each family chooses whether their Brief is in English or Chinese (`summary_language: en` or `zh`), and setup asks which. The whole Brief follows it, including the program's own lines. A config without `summary_language` now gets an English Brief; configs copied from the old example already say `zh` and stay Chinese.
- Weekend Picks and the Brief archive in `~/FamilyBrief` follow `summary_language` too.
- The pilot feedback form is now in English for every Household; operators re-run `ops/feedback-form/create_feedback_form.gs` and hand out the new `feedback` section, since links from this version pre-fill the English options.
- In a Household where one parent reads Chinese and the other English, each parent can get the Brief in their own language, with the same content: give a Recipient in `email.to` a `language`. The Brief is written once and translated, so the second language costs one extra model call a night; if the translation fails, that parent gets the Brief in the first parent's language.
- A parent never gets a translated Brief that quietly dropped an item or changed its date or Kid: on a night the translation goes wrong they get the original with a short note at the top instead.
- A parent can pick any language, such as Swedish or Finnish, and the whole Brief comes in it, headings and hints included. English and Chinese stay the reviewed languages; others are best effort.
- Weekend Picks arrive in each Recipient's own language, like the Brief.
- Other accounts on the family Mac, such as a Kid's own, can't read the Brief archive, the raw messages or the logs anymore: `~/FamilyBrief` and everything FamilyBrief writes is owner-only, and existing installs are tightened on the next install or run. The MyClub calendar link, which carries a personal token, no longer shows up in `doctor`, the logs or the archive when it fails to open, and parents now add it in their own Terminal with `scripts/setup_myclub.py` instead of pasting it in the chat.
- Finnish joins English and Chinese as a reviewed language: its headings and hints are checked by a Finnish speaker, and the quality checks cover Finnish Briefs.
- Dates and times in the Brief read the way people write them in each parent's language ("Thu 1 Oct 09:00", "10月1日 周四 09:00", "to 1.10. klo 9.00") instead of `2026-10-01T09:00`: the date at the top, each Action Item's due date and each new calendar event's start, in the Household's timezone. The email subject keeps `YYYY-MM-DD`, so threads still sort by date.
- The product is now called Kinlace Parent Recap. Install it with `/plugin install parent-recap@kinlace` from the `kinlace/parent-recap` repository and start it with `/parent-recap:setup` or `/parent-recap:manage` (`$parent-recap-setup` and `$parent-recap-manage` in Codex). The email subject reads `Parent Recap · <date>`. Families on an earlier version remove the old Family Brief plugin first (see the README); their settings, Briefs and nightly schedule stay as they are.
