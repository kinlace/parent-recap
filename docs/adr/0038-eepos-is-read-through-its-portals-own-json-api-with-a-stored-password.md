# Eepos is read through its portal's own JSON API, with a stored password

Music and art schools in Finland run on Eepos, as sports clubs run on MyClub (#737). Eepos has no calendar feed for guardians and no published API, but its guardian portal is an AngularJS app that reads everything as JSON from `https://<school>.eepos.fi/portal/api/` over a cookie session, and a guardian can sign in with a username and password, with no CAPTCHA or second factor (the spike of 2026-11-04 on Zhao's own account). So the Eepos Source signs in as the portal does, `POST portal/api/auth/login` with a JSON body `{"username", "password"}` (confirmed from the portal's public script, not from a captured request), then reads `guardian/students` and `calendar-events/all-by-date-range`, each answer behind Angular's `)]}',` line. Each school is its own address with its own session, so a Household has one Eepos sign-in per school, as it has one Wilma sign-in per town (#647). The password is kept in the system's secret store (ADR 0020, ADR 0030), as setup's Wilma passwords are since #710, and the rest of the sign-in, the school and the username, in the config. Of the students the portal lists, which come with their addresses, phone numbers, birth dates and the start of their henkilötunnus, only the ids and names are read. The first slice reads the calendar only, and a maintainer adds a sign-in from a terminal (`parent-recap setup eepos`), the password typed at the secret store's own prompt; the setup page and the hosted sign-up get an Eepos step later (#755).

## Considered Options

- **Scraping the portal's pages.** Rejected: the pages are drawn by the app from the same JSON, so the JSON is both easier to read and less likely to change than the markup.
- **A command-line client of our own, as the wilma CLI is for Wilma.** Rejected for now: the API is small, and a module beside the MyClub collector, tested against a fake portal, is less to install and keep in step.
- **The emailed one-time sign-in link many families use.** Rejected: it needs someone to open an email each time, which an evening run can't. A family that has never set a password sets one with "Forgot your password?" on the school's login.
- **The password in the config, as MyClub's links are.** Rejected: it is a password to an account with the Kids' personal data, so it gets the secret store's protection, as Wilma's does.

## Consequences

- The API is unofficial and undocumented: Eepos can change it any time. When an answer isn't what the portal gave before, the Source says Eepos "changed" on the Brief's coverage line, the rest of the Brief still goes out, and `doctor` says the same.
- What a wrong password returns wasn't captured; any 4xx answer to the sign-in but 404 and 429, or an answer without `success: true`, is taken as one.
- The portal's script has a `newLoginPage` switch that sends sign-in to `/auth/login`, and bundles a Supabase-style auth client: Eepos may move to a new sign-in, and the password flow could change or go with it. Then this ADR is revisited.
- A Hosted Household's Eepos password is held on the maintainers' server as its Wilma password is, and SECURITY.md says so.
- Until the hosted sign-up has an Eepos step, the hosted Brief's last line lists Eepos among the Sources the hosted version doesn't read, for a Household without it.
