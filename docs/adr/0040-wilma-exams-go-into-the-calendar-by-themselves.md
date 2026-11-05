# Wilma exams go into the calendar by themselves, known by their exam id

Some parents plan from their calendar's week view and want each exam there as soon as the teacher adds it, three or four weeks ahead; others don't want their Kids' exams in their own calendar at all (#773). Until then an exam reached the Brief only within the coming week, and whether it became a calendar event was up to the model. Following the Eepos and eVaka calls ("we prepare the data, how each family uses it is up to them"), the Wilma Source now gives every upcoming exam in Wilma's exam list within four weeks as a calendar event of its own, every evening, beside its Message. The run treats it as it treats Eepos's and eVaka's events: it goes into the `.ics` attachment, or Google Calendar, the first evening it is seen, on by default, and the model is told it is already queued. The event is all day, or from its start time when Wilma gives one, for the Kid the student is, titled in Finnish as Wilma shows it (`Koe: Historia – Luvut 3–5`), with what to study as its description.

An exam can move. Every other event is known by its Source, id, start and Kid (`calendar.event_hash`), so a moved one is a new event. An exam's event is known by its exam id alone (`CalendarEvent.same_when_moved`): its `.ics` UID leaves the start out, and carries a SEQUENCE that grows with each Brief, so a calendar app takes the moved one as an update; in google mode the event is found by its `family_brief_uid` property and moved. The local state still keys on the start, so a moved exam is sent again. The id has the Wilma's tag and a hash of the student number, never the number itself, so two Kids in one class get one each and the model never sees the number (ADR 0013).

The model must not add an exam of its own. The prompt says so, and as a backstop the program drops any calendar event the model cites an exam Message for: the Message names its queued event in `metadata.calendar_event`. A teacher's ordinary message about the same exam is left to the prompt.

## Considered Options

- **A calendar subscription link per Household, a separate "Kids" calendar.** Rejected for now: it needs a feed served from somewhere the calendar app can reach every few hours, which the family's own computer isn't. The attachment works the same for every Source, and a family can import it into a calendar of its own.
- **Off by default.** Rejected: Eepos's and eVaka's events are on, and a family who doesn't want them keeps a separate calendar. A switch can come if families ask.
- **Leaving it to the model, with a longer window.** Rejected: the model decided differently from night to night, and couldn't keep one UID for a moved exam.
- **Each exam once, whatever its date.** Rejected: four weeks covers how far ahead teachers add exams, and keeps the Brief from listing a term's worth on the first evening.

## Consequences

- An exam cancelled in Wilma stays in the calendar: nothing removes an event the `.ics` sent.
- An exam whose subject, name or topic changes without moving isn't sent again.
- An exam Wilma lists without an id (`examId` 0) is known by its date, subject and name, so moving it makes a second event.
