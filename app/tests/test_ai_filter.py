"""The AI filter on its own, as another tool could call it: a list of messages in, the masked
messages and their placeholders out, and a separate step that puts the real values back in a reply.
The evening run's use of it is in test_nightly_run.py."""
from __future__ import annotations

import copy
import json

from family_brief import ai_filter

MESSAGES = [
    {"external_id": "g-1", "sender": "Maija Opettaja <maija.opettaja@kilo.example.fi>",
     "body": "Sign up at https://forms.kilo.example.fi/retki?id=42 or call 040 123 4567. "
             "Questions to maija.opettaja@kilo.example.fi.",
     "url": "https://mail.google.com/mail/u/0/#search/rfc822msgid:abc@kilo.example.fi"},
    {"external_id": "wa-1", "sender": "Anna", "body": "Text me on +358 50 765 4321, or see www.kilo-fc.fi.",
     "metadata": {"student_number": "7731905"}},
]
WORDS = {"phone": "a phone number", "email": "an email address", "link": "a link"}


def test_a_list_of_messages_gets_placeholders_that_a_reply_turns_back_into_the_values():
    before = copy.deepcopy(MESSAGES)

    masked, placeholders = ai_filter.mask(MESSAGES, drop=("url", "student_number"))

    sent = json.dumps(masked, ensure_ascii=False)
    for value in ("maija.opettaja@kilo.example.fi", "forms.kilo.example.fi", "040 123 4567",
                  "+358 50 765 4321", "kilo-fc.fi", "mail.google.com", "7731905"):
        assert value not in sent
    assert masked == [
        {"external_id": "g-1", "sender": "Maija Opettaja <⟦E1⟧>",
         "body": "Sign up at ⟦L1⟧ or call ⟦P1⟧. Questions to ⟦E1⟧."},
        {"external_id": "wa-1", "sender": "Anna", "body": "Text me on ⟦P2⟧, or see ⟦L2⟧.", "metadata": {}},
    ]
    assert MESSAGES == before

    reply = {"what": "Call ⟦P1⟧ or sign up at ⟦L1⟧", "refs": ["g-1"]}
    assert ai_filter.restore(reply, placeholders, WORDS) == {
        "what": "Call 040 123 4567 or sign up at https://forms.kilo.example.fi/retki?id=42", "refs": ["g-1"]}
