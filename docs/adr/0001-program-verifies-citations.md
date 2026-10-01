# The model cites input ids; the program verifies them and derives the Source

The Brief shows which Source each Action Item came from, and the archive counts entries no input supports, as a per-night hallucination rate for the pilot. The model writes only `refs` (the ids of the Messages, calendar events or earlier Brief items an entry is based on) and never a Source name. After parsing, the program resolves `refs` against the ids it actually put in that night's prompt and derives `source` and `verified` itself, because a label the model writes could be as made up as the item it labels.

## Considered Options

- **The model writes the Source.** Simpler, but an invented item would come with an invented Source, and nothing could be counted as unsupported.
- **The Brief flags unverified items.** Rejected for the pilot: an unverified item looks like any other minus its Source label, so the Brief stays uncluttered while the rate is measured in the archive.

## Consequences

- The model's output schema changed (Notices are `{text, refs}` objects; Action Items have `refs`), so every reader of archived summaries must accept both the old and the new shape.
- Re-reminders must carry the earlier item's `refs` forward. Re-reminders of items archived before citations existed cannot verify; they are counted as legacy, not unverified, so the upgrade does not inflate the rate.
- The rule-based fallback summary has no citations and is left out of the counts.
- Calendar events are held to more than Notices and Action Items, because they are written into the family calendar and can invite the partner: one whose refs name none of that night's Messages is dropped rather than counted, its Source and id come from the earliest Message it cites (so citing one more on a rerun doesn't make it a new event), and a link in it is kept only if a Message it cites has that link (#89).
