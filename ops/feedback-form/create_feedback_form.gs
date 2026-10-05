// Creates the single pilot feedback Form and its response Sheet, then logs the
// `feedback` block the program ships. Run once in the operator's own Google account;
// see README.md next to this file.

const FORM_TITLE = 'Parent Recap pilot feedback';

// The Brief pre-fills the verdict, so these strings must match feedback.py exactly.
// They are English for every Household; the links' labels follow the Brief's language.
const VERDICTS = ['⭐ Glad this was here', '❌ This is wrong', '❌ The Digest has a mistake'];

// The add* calls return the typed item, so it can answer createResponse directly.
const ADD_ITEM = {
  choice: (form) => form.addMultipleChoiceItem().setChoiceValues(VERDICTS),
  paragraph: (form) => form.addParagraphTextItem(),
  text: (form) => form.addTextItem(),
};

// key = name under `feedback.fields`; sample is a throwaway answer used only to
// read the field's entry id back out of a pre-filled URL.
const PREFILL_FIELDS = [
  { key: 'verdict', title: 'Feedback', kind: 'choice', required: true, sample: VERDICTS[0] },
  { key: 'item_text', title: 'Item', kind: 'paragraph', sample: 'x' },
  { key: 'source', title: 'Source', kind: 'text', sample: 'x' },
  { key: 'backend', title: 'AI (backend)', kind: 'text', sample: 'x' },
  { key: 'date', title: 'Brief date', kind: 'text', sample: 'x' },
  { key: 'household', title: 'Household', kind: 'text', sample: 'x' },
  { key: 'kid', title: 'Kid', kind: 'text', sample: 'x' },
];

function createFeedbackForm() {
  const form = FormApp.create(FORM_TITLE)
    .setDescription(
      'The ⭐ and ❌ links in the Brief open this form already filled in. Just press Submit.',
    )
    .setCollectEmail(false)
    .setAllowResponseEdits(false)
    .setShowLinkToRespondAgain(false);
  // Forms created by script may start unpublished; older runtimes lack the method.
  if (typeof form.setPublished === 'function') form.setPublished(true);

  const items = PREFILL_FIELDS.map((field) => ({
    field,
    item: ADD_ITEM[field.kind](form).setTitle(field.title).setRequired(Boolean(field.required)),
  }));
  form
    .addParagraphTextItem()
    .setTitle('Anything to add? (optional)')
    .setHelpText('What was wrong, and what it should have said')
    .setRequired(false);

  const sheet = SpreadsheetApp.create(FORM_TITLE + ' (responses)');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());

  let baseUrl = null;
  const fieldLines = [];
  for (const { field, item } of items) {
    const url = prefilledUrl(form, field, item);
    baseUrl = baseUrl || url.split('?')[0];
    fieldLines.push('    ' + field.key + ': ' + entryId(url));
  }

  console.log(
    [
      'Edit the form: ' + form.getEditUrl(),
      'Response sheet: ' + sheet.getUrl(),
      '',
      'Save this as app/src/family_brief/pilot_feedback.yaml and release it (setup adds each',
      "Household's household_label when it opts in):",
      '',
      'feedback:',
      '  prefill_base_url: ' + baseUrl,
      '  fields:',
      ...fieldLines,
    ].join('\n'),
  );
}

// Item.getId() is not the `entry.N` pre-fill id; the supported way to learn it is
// to pre-fill one answer and read it back from the URL.
function prefilledUrl(form, field, item) {
  const answer = item.createResponse(field.sample);
  return form.createResponse().withItemResponse(answer).toPrefilledUrl();
}

function entryId(url) {
  const match = url.match(/[?&](entry\.\d+)=/);
  if (!match) throw new Error('No entry id in pre-filled URL: ' + url);
  return match[1];
}
