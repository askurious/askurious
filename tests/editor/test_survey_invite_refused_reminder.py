"""A reminder the mail server refused MAX_ATTEMPTS times is not queued anew (app/survey_invites.py, due): before, the
entry stayed at its step with `failed` set, so every round of the queue took the reminder up again as long as the
survey ran, about 240 attempts a day for one wrong address (function audit 2026-11-11)."""
from datetime import timedelta

from app import mailqueue, survey_invites
from app.extensions import db
from app.models import MailOutbox, SurveyInvite, utcnow
from tests.conftest import mail_on
from tests.editor.test_survey_invites import INV, _start


def test_a_refused_reminder_is_not_queued_again(app, client, survey):
    mail_on(app)
    _start(app, client)
    client.post(INV + "/addresses", data={"addresses": "bad@example.org"})
    client.post(INV + "/send", data={"kind": "invite"})
    client.post(INV + "/texts", data={"reminder1": "3"})
    with app.app_context():
        invite = db.session.scalar(db.select(SurveyInvite))
        invite.last_sent_at = utcnow() - timedelta(days=4)
        db.session.commit()
        survey_invites.due(utcnow())
        row = db.session.scalar(db.select(MailOutbox).filter(MailOutbox.sent_at.is_(None)))
        assert row is not None and row.kind == "reminder"
        for _ in range(mailqueue.MAX_ATTEMPTS):  # the mail server refuses it every time
            mailqueue._failed(db.session.get(MailOutbox, row.id), db.session.get(SurveyInvite, invite.id))
        assert db.session.get(SurveyInvite, invite.id).failed
        assert db.session.scalars(db.select(MailOutbox).filter(MailOutbox.sent_at.is_(None))).all() == []
        survey_invites.due(utcnow())  # the next round
        assert db.session.scalars(db.select(MailOutbox).filter(MailOutbox.sent_at.is_(None))).all() == []
