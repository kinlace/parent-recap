---
status: accepted
---

# Sunday's Brief lists the Week Ahead from what the program already has, without the model

Parents plan by the week: what to pack, which days end early, what is due when. On Sunday evening they had to piece the coming week together from past Briefs (#1037). So Sunday's evening Brief adds a short Week Ahead: day by day, Monday to Friday, the Action Items due and the calendar events, exams and changed school hours among them, that Parent Recap already knows.

The program builds it itself. Its items come from three places, none of them new: the Briefs of the last 28 evenings in the archive that reached the parents (their Action Items' `by` dates and the model's calendar events), tonight's Brief, and the events tonight's Sources gave, those the Brief would list as new (in ics mode only a Source the `.ics` takes, so MyClub's only when asked for). A Source gives its events again every evening within its window, Wilma's exams for four weeks (ADR 0040), so a moved exam is where the Source says it is now. The same thing is listed once: tonight's Source events come first, then tonight's Brief, then the earlier Briefs from the latest back, and an item is left out when one already taken is the same: an Action Item due the same day for the same Kid in the same words (a re-reminder's prefix aside), or an event on the same day for the same Kid, at the same time when both have one, whose title shares its words' stems with the other's. A day shows five items, then how many more.

A Sunday with nothing new but a Week Ahead with something in it sends its Brief: a quiet Sunday is the common case, and the week is why the parents open it.

The section is the program's text and the archive's, so it costs no model call in a Household with one language. For each other language its model-written text goes into the translation call that already runs (ADR 0004), with the people of the earlier evenings it reads added to the night's placeholders first (ADR 0013); a Source's own event titles stay as the Source wrote them, as in the list of new events. A translation that lacks the week leaves it as written instead of failing the whole Brief's translation.

## Considered Options

- **The model writes the week.** Rejected: one more call or a longer prompt every Sunday, a list that could differ from what earlier Briefs said, and Third Parties' names from up to four weeks of archive sent again. The items are already structured, with dates, Kids and refs.
- **Monday morning.** Rejected for now: the Brief is an evening message, and Sunday evening is when families pack for Monday.
- **Read the calendar instead of the archive.** Rejected: in ics mode the program has no calendar to read, and Google Calendar holds the family's own events too.
- **A store of its own for known items.** Rejected: the archive already keeps every evening's Brief, and a store would be one more file to keep in step and to migrate.
- **Leave tonight's items out, since tonight's Brief lists them above.** Rejected: the week is the one place a parent sees the whole Monday to Friday, so it repeats them briefly.

## Consequences

- An Action Item done or cancelled after its Brief still shows on its day: nothing records it as done. A model event an earlier Brief told, then moved by a later message, can show on both days unless the two match as the same thing. A Source event cancelled at its Source no longer shows, since the week takes the Source's events from tonight.
- A Source that couldn't be read tonight is missing from the week, as it is from the rest of the Brief, and the coverage line says so.
- An event of several days shows on its first day only.
- The match that keeps an item once is a heuristic: two different events of one Kid on one day whose titles share a longer word show as one.
- The archive matters to the Brief beyond the last three evenings: deleting it empties the week until new Briefs fill it.
- The translation prompt mentions `week_ahead` only on a Sunday with one, so every other evening's prompt stays as it was.
