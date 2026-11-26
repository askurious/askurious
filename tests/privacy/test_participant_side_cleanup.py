"""Deleting and opting out on the participants' side of its own (SIDE=participant), whose database role has only the
rights of app/participant_grants.py: a second deletion of the day, a deletion in a survey with a coding guide, the
personal link of an invitation mail and "no further mails" stay within those rights (tests/conftest.py checks every
statement of these requests against the list), and what the role may not write is noted for the housekeeping
(app/participant_cleanup.py), which carries it out."""
import re
from contextlib import contextmanager

from app import coding, participant_cleanup, participant_grants
from app.extensions import db
from app.models import MailOutbox, ParticipantCleanup, Response, Survey, SurveyInvite, utcnow
from tests.conftest import mail_on
from tests.privacy.test_erasure import CODE, build, log, take


@contextmanager
def participants_side(app):
    """The requests inside run as the participants' containers do."""
    before = app.config.get("SIDE")
    app.config["SIDE"] = "participant"
    try:
        yield
    finally:
        app.config["SIDE"] = before


def _delete_with_code(app, uid, code):
    with participants_side(app):
        return app.test_client().post(f"/s/{uid}/delete", data={"sure": "1", "code": code}, follow_redirects=True)


def test_the_rights_name_the_columns(app):
    assert participant_grants.columns("erasure", "UPDATE") == {"responses", "contacts"}
    assert participant_grants.columns("survey_invite") == {"id", "survey_id", "status", "pending_status", "token_hash"}
    assert participant_grants.columns("survey_invite", "UPDATE") == {"status", "pending_status", "email", "name"}
    assert participant_grants.allowed("participant_cleanup") == ("SELECT", "INSERT")
    assert participant_grants.columns("mail_outbox") == {"id"}             # the queue stays closed
    assert "survey_invite" not in participant_grants.NOT_GRANTED


def test_a_second_deletion_of_the_day_counts_on_the_first_row(app, client, survey):
    uid = build(app, client)
    codes = [CODE.search(take(app.test_client(), uid, text)).group(1) for text in ("eins", "zwei")]
    for code in codes:
        assert _delete_with_code(app, uid, code).status_code == 200
    assert log(app) == [("self", 2, 0)]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1, is_preview=False)) == 0


def _with_guide(app, quoted):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        key = next(k for item, _col, _label, k in coding.targets(survey) if item.id == 2)
        survey.codings = {key: {"cats": [{"id": 1, "label": "Grund", "example": quoted},
                                         {"id": 2, "label": "Anderes", "example": "frei formuliert"}],
                                "proposals": [{"label": "Vorschlag", "example": quoted}], "next": 3}}
        db.session.commit()
        return key


def test_a_deletion_leaves_the_coding_guide_to_the_housekeeping(app, client, survey):
    uid = build(app, client)
    code = CODE.search(take(app.test_client(), uid, "weil es mir wichtig ist")).group(1)
    take(app.test_client(), uid, "etwas anderes")
    key = _with_guide(app, "weil es mir wichtig ist")
    assert _delete_with_code(app, uid, code).status_code == 200
    with app.app_context():
        guide = db.session.get(Survey, 1).codings[key]
        assert guide["cats"][0]["example"] == "weil es mir wichtig ist"     # not written from this side
        row, = db.session.scalars(db.select(ParticipantCleanup)).all()
        assert (row.kind, row.survey_id) == (participant_cleanup.CODING, 1)
        assert "wichtig" not in str(row.payload) and list(row.payload["hits"]) == [key]   # digests, no answer
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1, is_preview=False)) == 1
        assert participant_cleanup.drain() == 1                             # the housekeeping
        guide = db.session.get(Survey, 1).codings[key]
        assert [c["example"] for c in guide["cats"]] == ["", "frei formuliert"] and "proposals" not in guide
        assert db.session.scalar(db.select(ParticipantCleanup)) is None


def test_on_one_address_the_coding_guide_is_cleaned_at_once(app, client, survey):
    uid = build(app, client)
    code = CODE.search(take(app.test_client(), uid, "weil es mir wichtig ist")).group(1)
    key = _with_guide(app, "weil es mir wichtig ist")
    with app.app_context():
        survey_row = db.session.get(Survey, 1)
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=1, is_preview=False)).all()
        coding.forget(survey_row, rows)                                     # as erasure does, SIDE unset
        db.session.commit()
        assert db.session.get(Survey, 1).codings[key]["cats"][0]["example"] == ""
        assert db.session.scalar(db.select(ParticipantCleanup)) is None
    assert code


def test_a_step_that_fails_stays_for_the_next_round(app, client, survey, monkeypatch):
    with app.app_context():
        participant_cleanup.queue(participant_cleanup.CODING, 1, {"hits": {}})
        participant_cleanup.queue(participant_cleanup.TRANSCRIPTS, 1, {"interviews": []})
        participant_cleanup.queue(participant_cleanup.CODING, 999, {"hits": {}})   # its survey is gone
        db.session.commit()
        monkeypatch.setattr(coding, "empty_quoted", lambda *a: 1 / 0)
        assert participant_cleanup.drain() == 2
        left, = db.session.scalars(db.select(ParticipantCleanup)).all()
        assert (left.kind, left.survey_id) == (participant_cleanup.CODING, 1)


# --- the invitation list -----------------------------------------------------------------------------------------


def _invited(app, client):
    from tests.editor.test_survey_invites import INV, _start

    sent = mail_on(app)
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "email;name\nanna@example.org;Anna\nben@example.org;Ben"})
    client.post(INV + "/send", data={"kind": "invite"})
    return sent


def test_the_personal_link_and_no_further_mails_need_no_address(app, client, survey):
    from tests.conftest import CONSENT_OK

    sent = _invited(app, client)
    anna = next(m for m in sent if m["To"] == "anna@example.org").get_content()
    ben = next(m for m in sent if m["To"] == "ben@example.org").get_content()
    link = re.search(r"https?://[^/\s]+(/s/\S+\?einladung=\S+)", anna).group(1)
    no_mail = re.search(r"https?://[^/\s]+(/s/\S+/no-mail/\S+)", ben).group(1)
    with app.app_context():                                                  # a reminder to Ben waits in the queue
        ben_id = db.session.scalar(db.select(SurveyInvite.id).filter_by(email="ben@example.org"))
        db.session.add(MailOutbox(survey_id=1, invite_id=ben_id, to="ben@example.org", subject="s", body="b",
                                  kind="reminder", sender_id=1))
        db.session.commit()
    with participants_side(app):
        p = app.test_client()
        assert p.get(link).status_code == 200
        assert p.post(link.split("?")[0], data=CONSENT_OK).status_code in (200, 302)
        q = app.test_client()
        assert q.get(no_mail).status_code == 200                             # only asks
        assert "keine weiteren E-Mails" in q.post(no_mail).data.decode()
    with app.app_context():
        states = {i.email_hash: (i.status, i.pending_status, i.email, i.name) for i in db.session.scalars(db.select(SurveyInvite))}
        assert sorted(states.values()) == [("open", "started", "anna@example.org", "Anna"), ("optout", None, "", "")]
        # the waiting mail is left to the queue, which drops the mail of an entry that opted out
        assert db.session.scalar(db.select(db.func.count(MailOutbox.id)).filter(MailOutbox.sent_at.is_(None))) == 1


def test_the_queue_drops_the_waiting_mail_of_an_entry_that_opted_out(app, client, survey):
    """Opting out leaves the mails still waiting to the queue (the participants' side cannot read it): the next pass
    drops the mail instead of sending it."""
    from app import mailqueue

    sent = _invited(app, client)
    ben = next(m for m in sent if m["To"] == "ben@example.org").get_content()
    no_mail = re.search(r"https?://[^/\s]+(/s/\S+/no-mail/\S+)", ben).group(1)
    with app.app_context():
        ben_id = db.session.scalar(db.select(SurveyInvite.id).filter_by(email="ben@example.org"))
        db.session.add(MailOutbox(survey_id=1, invite_id=ben_id, to="ben@example.org", subject="s", body="b",
                                  kind="reminder", sender_id=1, created_at=utcnow()))
        db.session.commit()
    before = len(sent)
    app.test_client().post(no_mail)
    with app.app_context():
        assert db.session.get(SurveyInvite, ben_id).status == "optout"
        mailqueue.run(utcnow())
        assert db.session.scalar(db.select(db.func.count(MailOutbox.id)).filter(MailOutbox.sent_at.is_(None))) == 0
    assert len(sent) == before                                              # nothing went out
