"""Four decisions of 2026-11-12 (Ralf, data protection audit 2026-11-11): the link "Meine Angaben löschen" at the foot
of the participant pages from the first stored page on, with the deletion code on its page; the AI reads handwriting
only when the consent on the sheet is clearly ticked; the KI-Chat with an own address names its host; and the name and
address of a complaint go three years after it was settled."""
from datetime import timedelta

import pytest

from app import ai_access, feedback
from app.extensions import db
from app.items import consent
from app.models import Feedback, utcnow


def test_the_ai_chat_names_the_host_of_an_own_address():
    assert ai_access.recipient_name("custom", "https://llm.example.org/v1") == "llm.example.org"
    assert ai_access.recipient_name("openai", "https://api.openai.com/v1") == "OpenAI"
    assert ai_access.recipient_name("custom", "") == ai_access.provider_label("custom")


def test_the_early_code_sentence_is_in_every_language():
    de, en = consent.EARLY_CODE
    for lang in ("fr", "tr", "zh-Hans", "de-du"):
        assert consent.tr(lang, de, en) != en


def _complaint(app, done_days=None, contact="Kim <kim@example.org>"):
    with app.app_context():
        item = Feedback(severity=Feedback.COMPLAINT, message="m", contact=contact,
                        status="done" if done_days is not None else "new",
                        done_at=(utcnow() - timedelta(days=done_days)) if done_days else None)
        db.session.add(item)
        db.session.commit()
        return item.id


def test_the_contact_of_a_complaint_goes_three_years_after_it_was_settled(app):
    old, recent, open_one, before = (_complaint(app, 365 * 3 + 5), _complaint(app, 30), _complaint(app),
                                     _complaint(app, 0))  # done_days 0: settled before the column existed
    with app.app_context():
        db.session.get(Feedback, before).done_at = None
        db.session.commit()
        assert feedback.forget_contacts() == 1
        got = {i: db.session.get(Feedback, i) for i in (old, recent, open_one, before)}
        assert got[old].contact == "" and got[old].message == "m"            # the record stays
        assert got[recent].contact and got[open_one].contact and got[before].contact
        assert got[before].done_at is not None                                # its three years start now


@pytest.mark.parametrize("state, reads", [("yes", True), ("unclear", False), ("no", False)])
def test_the_ai_reads_handwriting_only_with_a_clear_tick(app, monkeypatch, state, reads):
    from app import ai, paper

    monkeypatch.setattr(paper, "ai_access_own", lambda: object())
    monkeypatch.setattr(paper, "evaluate", lambda survey, sheet: {"fields": [], "issues": 0, "missing": False})
    monkeypatch.setattr(paper, "_consent_state", lambda survey, found: state)
    with app.test_request_context():
        if reads:
            assert paper.read_with_ai(object(), object()) in (0, None)  # nothing to read here, but it was allowed
        else:
            with pytest.raises(ai.AIError):
                paper.read_with_ai(object(), object())


def test_the_foot_of_the_page_offers_deleting_with_the_code_from_the_first_stored_page(app, client, survey):
    import re

    from tests.privacy.test_erasure import CONSENT_OK, build

    uid = build(app, client)
    person = app.test_client()
    first = person.get(f"/s/{uid}").data.decode()
    assert "Meine Angaben löschen" not in first                      # nothing stored yet
    page = person.post(f"/s/{uid}", data=CONSENT_OK, follow_redirects=True).data.decode()
    assert "Meine Angaben löschen" in page
    link = re.search(r'href="([^"]*/delete[^"]*)"[^>]*class="p-footer-legal">Meine Angaben löschen', page)
    assert link, page[-1500:]
    erase = person.get(link.group(1).replace("&amp;", "&")).data.decode()
    assert "Ihr Löschcode:" in erase and re.search(r"\d+-[2-9A-Z]{4}-[2-9A-Z]{4}-[2-9A-Z]{4}", erase)


def test_without_codes_the_foot_has_no_link(app, client, survey):
    from tests.privacy.test_erasure import CONSENT_OK, build

    uid = build(app, client, codes=False)
    person = app.test_client()
    person.get(f"/s/{uid}")
    page = person.post(f"/s/{uid}", data=CONSENT_OK, follow_redirects=True).data.decode()
    assert "Meine Angaben löschen" not in page
