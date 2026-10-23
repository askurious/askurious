"""Retention periods (app/retention.py): what a survey collected is deleted a chosen time after its fieldwork ended."""
import os
from datetime import timedelta

from app import retention
from app.extensions import db
from app.models import ContactEntry, Erasure, Item, Response, Survey, utcnow
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"
META = {"title": "Test", "languages": ["de", "en"], "default_language": "de", "allow_back": "1"}


def periods(personal="", answers=""):
    return {**META, "retention_shown": "1", "retention_personal": str(personal), "retention_answers": str(answers)}


def build(app, client, personal=6, answers=120, take=2):
    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/meta", data=periods(personal, answers))
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    for n in range(take):
        p = app.test_client()
        p.get("/s/1")
        p.post("/s/1", data=CONSENT_OK)
        p.post("/s/1", data={"nav": "next", "i2": f"Antwort {n}"})
    with app.app_context():
        for r in db.session.scalars(db.select(Response)):
            r.params = {"pid": f"panel-{r.id}"}
        db.session.add(ContactEntry(survey_id=1, answers={"9": "anna@example.org"}))
        db.session.commit()


def close(app, client, days_ago):
    client.post("/surveys/1/close")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.closed_at = utcnow() - timedelta(days=days_ago)
        db.session.commit()


def state(app):
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=1)).all()
        return (len(rows), sum(1 for r in rows if r.params), db.session.scalar(db.select(db.func.count()).select_from(ContactEntry)),
                [(e.way, e.responses, e.contacts) for e in db.session.scalars(db.select(Erasure))])


def test_new_surveys_have_periods_and_old_ones_none(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert retention.of(s) == {"personal": 6, "answers": 120}
        s.retention = None                                               # a survey from before
        assert retention.of(s) == {"personal": None, "answers": None} and retention.view(s) == []
        s.retention = {"personal": 7, "answers": "x"}                    # only what the lists offer
        assert retention.of(s) == {"personal": None, "answers": None}
        db.session.rollback()
    page = client.get("/surveys/1").data.decode()
    assert 'name="retention_personal"' in page and '<option value="6" selected>6 Monate nach dem Feldende' in page
    assert '<option value="120" selected>10 Jahre nach dem Feldende' in page and "Leitlinien zur Sicherung guter" in page
    client.post("/surveys/1/meta", data=META)                            # a form without the fields changes nothing
    with app.app_context():
        assert retention.of(db.session.get(Survey, 1)) == {"personal": 6, "answers": 120}
    client.post("/surveys/1/meta", data=periods())
    with app.app_context():
        assert db.session.get(Survey, 1).retention is None


def test_nothing_goes_before_the_end_and_before_the_period(app, client, survey):
    build(app, client, personal=1, answers=12)
    with app.app_context():
        assert retention.run(utcnow() + timedelta(days=5000)) == 0       # running: no clock
    client.post("/surveys/1/close", data={"reason": "paused"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.close_reason, s.closed_at = "paused", utcnow() - timedelta(days=5000)
        db.session.commit()
        assert retention.ended(s) is None and retention.run() == 0       # paused: no clock either
        s.close_reason, s.closed_at = "manual", utcnow() - timedelta(days=20)
        db.session.commit()
        assert retention.run() == 0                                       # closed, but the month is not over
        assert retention.holds(s) == {"personal": 3, "answers": 3}        # two identifiers and one entry; two rows and it
    assert state(app) == (2, 2, 1, [])


def test_identifiers_and_contact_details_go_first_then_everything(app, client, survey):
    build(app, client, personal=1, answers=12)
    client.post("/surveys/1/close")
    client.post("/surveys/1/reopen", data={"confirm": "1"})                # a saved state with the identifiers
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        from app import snapshots

        s = db.session.get(Survey, 1)
        folder = os.path.join(snapshots.folder(s), snapshots.listing(s)[0][0])
        with open(os.path.join(folder, "data.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("﻿resp_id,pid,url_pid,why\r\n1,1-a,panel-1,eins\r\n2,1-b,panel-2,\"zwei, drei\"\r\n")
        hb = os.path.join(app.config["UPLOAD_FOLDER"], "hb", s.uid)
        os.makedirs(hb, exist_ok=True)
        for name in ("results.bin", "item_2.json"):
            open(os.path.join(hb, name), "w").close()
    close(app, client, days_ago=40)
    client.get("/cron/close", headers={"X-Cron-Key": "k"})                # the housekeeping carries the periods out
    assert state(app) == (2, 0, 0, [("retention", 0, 1)])
    data = open(os.path.join(folder, "data.csv"), encoding="utf-8-sig", newline="").read()
    assert data == 'resp_id,pid,url_pid,why\r\n1,1-a,,eins\r\n2,1-b,,"zwei, drei"\r\n'
    assert [name for name in os.listdir(hb) if not name.endswith(".claim")] == ["item_2.json"]                              # answers unchanged: the estimate stays
    with app.app_context():                                               # again: nothing more to do
        assert retention.run() == 0 and retention.holds(db.session.get(Survey, 1)) == {"personal": 0, "answers": 2}
    assert state(app) == (2, 0, 0, [("retention", 0, 1)])
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.closed_at = utcnow() - timedelta(days=370)
        db.session.commit()
    client.get("/cron/close", headers={"X-Cron-Key": "k"})
    assert state(app) == (0, 0, 0, [("retention", 2, 1)])
    with app.app_context():
        assert retention.run() == 0
    assert sorted(os.listdir(folder)) == ["codebook.csv", "questionnaire.md", "survey.json"] and not [name for name in os.listdir(hb) if not name.endswith(".claim")]
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.CLOSED and len(s.blocks) == 2          # the questionnaire stays
    page = client.get("/surveys/1/data").data.decode()
    assert "durch Ablauf einer Aufbewahrungsfrist" in page and "nichts mehr vorhanden" in page


def test_a_survey_whose_answers_are_gone_is_looked_at_once_a_day(app, client, survey, monkeypatch):
    """Code review 2026-10-15, E5: past its period "answers" a survey with nothing left was asked six questions every
    minute for ever; the note "answers_done" keeps it to once a day. Periods stored as JSON null are not loaded."""
    build(app, client, personal=1, answers=12, take=1)
    close(app, client, days_ago=400)
    looked = []
    left = retention._left
    monkeypatch.setattr(retention, "_left", lambda s: looked.append(s.id) or left(s))
    with app.app_context():
        assert retention.run() == 1 and looked == [1]                    # the answers go
        assert retention.run() == 0 and looked == [1, 1]                 # nothing left: noted
        assert retention.run() == 0 and looked == [1, 1]                 # not asked again today
        assert retention.run(utcnow() + timedelta(days=1)) == 0 and looked == [1, 1, 1]  # the next day once more
        s = db.session.get(Survey, 1)
        assert retention.of(s) == {"personal": 1, "answers": 12}         # the note is no period
        s.retention = None                                               # JSON null, not SQL NULL
        db.session.commit()
        seen = []
        monkeypatch.setattr(retention, "due", lambda s: seen.append(s.id) or {"personal": None, "answers": None})
        retention.run()
        assert seen == []


def test_with_real_participations_a_period_only_gets_shorter(app, client, survey):
    build(app, client, personal=6, answers=60, take=0)
    client.post("/surveys/1/meta", data=periods(12, ""))                  # nobody took part yet: anything goes
    with app.app_context():
        assert retention.of(db.session.get(Survey, 1)) == {"personal": 12, "answers": None}
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    page = client.post("/surveys/1/meta", data=periods(24, 120), follow_redirects=True).data.decode()
    with app.app_context():                                               # longer is refused, a new period is shorter than none
        assert retention.of(db.session.get(Survey, 1)) == {"personal": 12, "answers": 120}
    assert "nicht mehr verlängern" in page
    client.post("/surveys/1/meta", data=periods("", 60))                  # none would be longer than 12 months
    with app.app_context():
        assert retention.of(db.session.get(Survey, 1)) == {"personal": 12, "answers": 60}
    client.post("/surveys/1/meta", data=periods(3, 12))
    with app.app_context():
        assert retention.of(db.session.get(Survey, 1)) == {"personal": 3, "answers": 12}


def test_consent_privacy_page_and_export_page_name_the_periods(app, client, survey):
    client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.consent_item
        de, en = consent.kind.template_body(consent, "de"), consent.kind.template_body(consent, "en")
        assert "alle Antworten 10 Jahre nach dem Ende der Befragung gelöscht" in de and "Kennung" not in de
        assert "all answers are deleted 10 years after the end of the survey" in en
        assert retention.consent_sentence(s, "de", True, True).startswith(
            "Ihre Kennung aus dem Aufruf-Link und die getrennt gespeicherten Angaben werden 6 Monate nach dem Ende")
        assert retention.consent_sentence(s, "en", True, False).startswith(
            "Your identifier from the link is deleted 6 months after the end")
        s.retention = {"personal": 1, "answers": 12}
        assert "alle Antworten 1 Jahr nach" in consent.kind.template_body(consent, "de")
        assert "1 Monat nach" in retention.consent_sentence(s, "de", True, False)
        s.retention = None
        # without periods no period, only the criteria (Art. 13(2)(a) GDPR; comparison with the RWTH template, L7)
        body = consent.kind.template_body(consent, "de").split("Löschcode")[0]
        assert "nach dem Ende der Befragung gelöscht" not in body and "Ohne festgelegte Frist" in body
        assert consent.kind.facts(consent, "en")["retention"] is None
        db.session.rollback()
    # the untouched standard text follows the setting
    client.post("/surveys/1/meta", data=periods(3, 24))
    with app.app_context():
        assert "alle Antworten 2 Jahre nach" in db.session.get(Survey, 1).consent_item.texts["de"]["body"]
    build(app, client, personal=3, answers=24)
    privacy = app.test_client().get("/s/1/privacy").data.decode()
    assert "Frist in Jahren: 2" in privacy and "Frist in Monaten" not in privacy   # no identifiers declared, no separated section
    page = client.get("/surveys/1/data").data.decode()
    assert 'id="retention"' in page and "die Frist beginnt, sobald die Befragung geschlossen ist" in page
    close(app, client, days_ago=80)
    page = client.get("/surveys/1/data").data.decode()
    assert "Kennungen und Kontaktangaben werden am" in page and "Alle Teilnahmen werden am" not in page   # only this one is near


def test_duplicate_and_file_take_the_periods(app, client, survey):
    build(app, client, personal=3, answers=36, take=0)
    client.post("/surveys/1/duplicate", data={"title": "Kopie"})
    with app.app_context():
        from app import transfer

        assert retention.of(db.session.get(Survey, 2)) == {"personal": 3, "answers": 36}
        assert transfer.dump(db.session.get(Survey, 1), with_files=False)["survey"]["retention"] == {"personal": 3, "answers": 36}
        assert transfer._settings({"title": "Alt", "languages": ["de"]}, "")["retention"] is None
        assert transfer._settings({"title": "Neu", "languages": ["de"], "retention": {"personal": 5, "answers": 12}}, "")[
            "retention"] == {"personal": None, "answers": 12}


def test_the_sources_of_the_settings_show_no_raw_markup(client, survey):
    # the citations are plain text (the sources macro escapes them); "<em>" showed up literally (UI audit R50)
    assert "<" not in retention.GDPR + retention.DFG
    html = client.get("/surveys/1").get_data(as_text=True)
    assert "&lt;em&gt;" not in html


def test_the_answers_period_takes_what_a_live_vote_collected(app, client, survey):
    """Code review 2026-10-15, D2: the questions from the audience, the "unclear" taps, the reactions and the browser
    receipts of a live vote's runs went neither with the period for the answers nor counted as left over."""
    from app.models import BrowserReceipt, LiveFeedback, LiveQuestion, LiveReaction

    build(app, client, personal=1, answers=12, take=0)
    with app.app_context():
        later = utcnow() + timedelta(days=7)
        db.session.add_all([LiveQuestion(survey_id=1, run_id=1, text="Was heißt das?"),
                            LiveFeedback(survey_id=1, run_id=1, item_id=2), LiveReaction(survey_id=1, emoji="+"),
                            BrowserReceipt(survey_id=1, scope="live:1", key="k", data={}, expires_at=later),
                            BrowserReceipt(survey_id=1, scope="decline", key="k", data={}, expires_at=later)])
        db.session.execute(db.delete(ContactEntry))
        db.session.commit()
    close(app, client, days_ago=370)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert retention._left(s)                                         # only the live vote is left: still purged
        assert retention.run() == 1
        assert not retention._left(s)
        for model in (LiveQuestion, LiveFeedback, LiveReaction):
            assert db.session.scalar(db.select(db.func.count()).select_from(model)) == 0
        assert db.session.scalars(db.select(BrowserReceipt.scope)).all() == ["decline"]   # only the live receipts


def _due_survey(app, client, sid, take=2):
    """Survey `sid` with `take` participations, closed 800 days ago, answers kept 12 months."""
    client.post("/surveys/new", data={"title": f"S{sid}", "languages": ["de", "en"], "default_language": "de"})
    client.post(f"/surveys/{sid}/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X",
                                                     "contact_email": "studie@example.org"})
    with app.app_context():
        s = db.session.get(Survey, sid)
        block = next(b for b in s.blocks if not any(i.type == "consent" for i in b.items))
        consent, bid = s.consent_item.id, block.id
    client.post(f"/surveys/{sid}/editor/items/{consent}", data={
        "code": "consent", "question": "Einwilligung", "body": "Text", "duration": "5", "confirm_voluntary": "1",
        "confirm_data": "1", "confirm_commitment": "1", "agree": "Fortsetzen", "disagree": "Nein"})
    client.post(f"/surveys/{sid}/editor/blocks/{bid}/items", data={"type": "text_input"})
    with app.app_context():
        item = db.session.scalar(db.select(db.func.max(Item.id)))
    client.post(f"/surveys/{sid}/editor/items/{item}", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post(f"/surveys/{sid}/meta", data={**periods(personal=1, answers=12), "title": f"S{sid}"})
    client.post(f"/surveys/{sid}/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, sid)
        assert s.status == Survey.RUNNING
        key = s.public_key
    for n in range(take):
        p = app.test_client()
        p.get(f"/s/{key}")
        p.post(f"/s/{key}", data={f"i{consent}": "1", f"i{consent}_c_voluntary": "1", f"i{consent}_c_data": "1",
                                  f"i{consent}_c_commitment": "1"})
        p.post(f"/s/{key}", data={"nav": "next", f"i{item}": f"Antwort {n}"})
    client.post(f"/surveys/{sid}/close")
    with app.app_context():
        s = db.session.get(Survey, sid)
        s.closed_at = utcnow() - timedelta(days=800)
        db.session.commit()


def _participations(app, sid):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid, is_preview=False))


def test_a_survey_that_keeps_failing_holds_up_no_other(app, client, monkeypatch):
    """Gesamtaudit 2026-10-18, FUNC-02: the periods ran in one transaction for all surveys, so one survey failing
    every time (a broken quota, a file) rolled back the deletions of every other in every round. Now each survey is
    committed on its own; the failing one stays as it was and is logged."""
    from app import lifecycle

    _due_survey(app, client, 1)
    _due_survey(app, client, 2)
    assert _participations(app, 1) == 2 and _participations(app, 2) == 2
    real = retention.purge

    def purge(survey):
        if survey.id == 1:  # the first in the loop: before, nothing after it was ever deleted
            raise RuntimeError("a lasting error in survey 1")
        return real(survey)

    monkeypatch.setattr(retention, "purge", purge)
    reported = []
    monkeypatch.setattr(retention, "_failed", lambda sid, uid, e: reported.append(sid))
    with app.test_request_context():
        for _round in range(2):
            lifecycle._step("retention.run", retention.run)
    assert _participations(app, 2) == 0, "survey 2 (period over) kept its participations: held up by survey 1"
    assert _participations(app, 1) == 2 and reported == [1, 1]
    monkeypatch.setattr(retention, "purge", real)
    with app.test_request_context():
        lifecycle._step("retention.run", retention.run)
    assert _participations(app, 1) == 0  # once the error is gone, it follows
