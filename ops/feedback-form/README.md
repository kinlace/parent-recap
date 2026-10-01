# Pilot feedback form

`create_feedback_form.gs` creates, in your own Google account, the one feedback form (Google Form) shared by the whole pilot and the sheet that collects the responses (Google Sheet), and prints the `feedback` section for `config.yaml`. Run it once for the whole pilot: every Household uses the same form and is told apart by the Household field.

## Running it

1. Open <https://script.google.com> and click "New project"
2. Delete the default code in the editor, paste in all of `create_feedback_form.gs`, and save
3. In the function dropdown at the top choose `createFeedbackForm` and click "Run"
4. The first time, an authorization prompt appears: choose your account → "Advanced" → "Go to … (unsafe)" → "Allow". The script only creates one form and one sheet
5. When it finishes, the "Execution log" below contains:
   - The form's edit link and the response sheet's link; keep these for yourself
   - A section starting with `feedback:`; copy it exactly

Running it again creates another new form and leaves the old one alone; delete what you don't need in Google Drive.

## Sending it to pilot families

Every Household's `config.yaml` gets the same `feedback` section, with only `household_label` changed to that Household's name (such as `"Virtanen family"`), so the response sheet shows who reported what.

## Checking

Take the `prefill_base_url` from the log, append `?usp=pp_url&`, then write every field as `entry.<number>=<value>` joined with `&`, and open it in a browser. Every field except the optional comment (which is meant to stay empty) should be filled in. The verdict value must be one of the three form options, which are in English for every Household whatever its Brief language: for example `%E2%AD%90%20Glad%20this%20was%20here` is `⭐ Glad this was here`. Submit once, and a new row appears in the response sheet.
