# Pilot feedback form

`create_feedback_form.gs` creates, in your own Google account, the one feedback form (Google Form) shared by the whole pilot and the sheet that collects the responses (Google Sheet), and prints the `feedback` block the program ships. Run it once for the whole pilot: every Household uses the same form and is told apart by the Household field.

## Running it

1. Open <https://script.google.com> and click "New project"
2. Delete the default code in the editor, paste in all of `create_feedback_form.gs`, and save
3. In the function dropdown at the top choose `createFeedbackForm` and click "Run"
4. The first time, an authorization prompt appears: choose your account → "Advanced" → "Go to … (unsafe)" → "Allow". The script only creates one form and one sheet
5. When it finishes, the "Execution log" below contains:
   - The form's edit link and the response sheet's link; keep these for yourself
   - A block starting with `feedback:`; copy it exactly

Running it again creates another new form and leaves the old one alone; delete what you don't need in Google Drive.

## Building it by hand

Some Google accounts refuse the script's authorization with "This app is blocked". Then build the form at <https://forms.google.com> with the same content as the script:

1. Title "Parent Recap pilot feedback", description "The ⭐ and ❌ links in the Brief open this form already filled in. Just press Submit."
2. Questions, in this order:

   | Question | Type | Notes |
   |---|---|---|
   | Feedback | Multiple choice | Required. Options, copied exactly: `⭐ Glad this was here`, `❌ This is wrong`, `❌ The Digest has a mistake` |
   | Item | Paragraph | |
   | Source | Short answer | |
   | AI (backend) | Short answer | |
   | Brief date | Short answer | |
   | Household | Short answer | |
   | Kid | Short answer | |
   | Anything to add? (optional) | Paragraph | Description "What was wrong, and what it should have said" |

   Google Forms sometimes changes a question's type as you type its title, so check every type at the end.
3. Settings → Responses: "Collect email addresses" is "Do not collect", and "Limit to 1 response" is off.
4. Responses → "Link to Sheets" → create a new spreadsheet.
5. Publish, with responders set to anyone with the link.
6. ⋮ → "Pre-fill form", type something in the first seven questions, click "Get link" and copy it. The part before `?` is `prefill_base_url`, and each `entry.<number>` belongs to the question you filled in, in order. Write them out as a `feedback:` block in the same shape the script prints (see `app/src/family_brief/pilot_feedback.yaml`).

## Shipping it to pilot families

Save the `feedback:` block as `app/src/family_brief/pilot_feedback.yaml` and release it. Until a release has this file, setup doesn't ask about pilot feedback at all. With it, the setup page's Welcome and the chat setup ask whether the family is a pilot family; when they agree, `setup save` writes the block into their `config.yaml` with a `household_label` of their own (the setup parent's email user by default, which they can change on the setup page's check step). The links send a pseudonym made from it, such as `Household 3f9a2c`, and the Kids as Kid A and Kid B, so the response sheet groups each Household's feedback without its label or the Kids' names. Nothing needs to be sent to families by hand.

## Checking

Take the `prefill_base_url` from the log, append `?usp=pp_url&`, then write every field as `entry.<number>=<value>` joined with `&`, and open it in a browser. Every field except the optional comment (which is meant to stay empty) should be filled in. The verdict value must be one of the three form options, which are in English for every Household whatever its Brief language: for example `%E2%AD%90%20Glad%20this%20was%20here` is `⭐ Glad this was here`. Submit once, and a new row appears in the response sheet.
