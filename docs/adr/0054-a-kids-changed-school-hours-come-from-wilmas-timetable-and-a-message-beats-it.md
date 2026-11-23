---
status: accepted
---

# A Kid's changed school hours come from Wilma's timetable, in the program's words, and a message beats them

Parents need to know when tomorrow is a short or a long school day, and the Brief only said tomorrow's hours, never that they were unusual (#1036). Wilma's timetable has each day's lessons but marks nothing as changed, and many schools announce a short day only in a message.

So the evening's one `schedule` call reads four weeks of the timetable, two weeks back and two ahead (28 days, within the wilma CLI's 31), instead of the next school day alone. The program works out each Kid's usual hours per weekday: the first lesson's start and the last lesson's end that most of the other days of that weekday share, by at least two of them. With four weeks, one odd week doesn't make a usual time, and when the other days don't agree nothing is usual and nothing is said. When the next school day starts or ends at another time, the Brief shows one line in the program's own words, in each Recipient's language: `🕒 Mia, Fri 9 Oct: 8:15–12:00 (normally 8:15–14:00)`. No model call: the line is the program's, like the Week Ahead (ADR 0053).

The model still gets only the next school day's lessons, as before, now with a line giving each Kid's usual hours on that weekday, and the summarize prompt asks it to give the usual hours whenever a day's hours differ, from the timetable or because a message says so, and that a message about the day's hours beats the timetable. The usual hours go through the AI filter with the rest of the timetable (ADR 0013); the program's own record of the changed hours stays out of the payload.

A message beats the timetable this way: when the model's text about that Kid (their Notices and their part of the Digest) already names a usual time that differs, the model has said it, perhaps with the hours a message gave, and the program's line is left out. Otherwise the line shows.

## Considered Options

- **The model writes the comparison alone.** Rejected: the hours would depend on the model noticing, in words that differ from night to night, and a missed short day is exactly what parents asked not to miss.
- **The program's line always, beside whatever the model says.** Rejected: on a day a message moved the hours, the Brief would show the timetable's hours next to the message's, two answers to one question.
- **A field in the model's reply saying the hours are covered.** Rejected for now: a schema change and an eval for one line; the text check is enough to try.
- **Only the coming two weeks.** Rejected: each weekday then has one other day, too few to say what is usual.

## Consequences

- The check that the model gave the hours is a heuristic: a usual time named in that Kid's text for another reason (a training at 14:00) leaves the line out, and a model that gives a message's hours without the usual ones lets the timetable's line show beside them.
- A school whose timetable doesn't return past weeks gives one other day per weekday, so nothing is usual and no line shows; the model's text is then as before.
- A holiday longer than two weeks has no next school day in the window, so the timetable gives nothing until it is.
- Since #1051 the timetable, and with it the changed hours, reaches the Brief only when the next school day is tomorrow: Monday's in Sunday's Brief, not in Friday's and Saturday's too.
- Only the next school day, only from Wilma: Kids without Wilma get what the messages say. The four weeks could feed the Week Ahead later.
