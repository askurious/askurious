"""What the participants' containers hold (audit 2026-10-18, CFG-04): the researchers' sessions can be signed with a
key of their own (RESEARCHER_SESSION_KEY) that the participants' containers do not get, and those containers leave
their mails in the queue instead of logging in to the mail server, so they need no MAIL_PASSWORD."""
import hashlib

from itsdangerous import URLSafeTimedSerializer

from app import mail
from app.extensions import db
from app.models import MailOutbox
from tests.conftest import mail_on

ASK = "http://ask.localhost"
RESEARCHER_KEY = "Wm3-pQ8_vL2xR7tY4nB9cK6hJ1gF5dS0aZ8uE3iO7wQ2"


def _signed(key, data, salt="cookie-session"):
    return URLSafeTimedSerializer(key, salt=salt, serializer=__import__("flask").sessions.session_json_serializer,
                                  signer_kwargs={"key_derivation": "hmac", "digest_method": hashlib.sha1}).dumps(data)


def _read(app, value, base_url="http://localhost"):
    with app.test_request_context("/", base_url=base_url):
        return app.session_interface._read(app, value)


def test_researcher_sessions_take_their_own_key(app):
    """With RESEARCHER_SESSION_KEY a session signed with SECRET_KEY (all a break-in on the participants' side would
    find) is worth nothing on the main address; one signed with the researchers' key is taken."""
    forged = _signed(app.config["SECRET_KEY"], {"_user_id": "1"})
    assert _read(app, forged) == {"_user_id": "1"}                       # without the setting: as before
    app.config.update(RESEARCHER_SESSION_KEY=RESEARCHER_KEY)
    assert _read(app, forged) is None
    assert _read(app, _signed(RESEARCHER_KEY, {"_user_id": "1"})) == {"_user_id": "1"}
    # a key of before as fallback keeps its sessions while the key is changed
    app.config.update(RESEARCHER_SESSION_KEY="Zt8-nB2_qW5xL7yR3mK9cH6jG1fD4sA0pV8uE2iO7rT5",
                      RESEARCHER_SESSION_KEY_FALLBACKS=[RESEARCHER_KEY])
    assert _read(app, _signed(RESEARCHER_KEY, {"_user_id": "1"})) == {"_user_id": "1"}


def test_participant_sessions_keep_secret_key(app):
    """The participants' address goes on signing with SECRET_KEY and its own salt: its containers need nothing else."""
    app.config.update(RESEARCHER_SESSION_KEY=RESEARCHER_KEY, PARTICIPANT_BASE_URL=ASK)
    participant = _signed(app.config["SECRET_KEY"], {"p": 1}, salt="cookie-session-participant")
    assert _read(app, participant, base_url=ASK) == {"p": 1}
    assert _read(app, _signed(RESEARCHER_KEY, {"p": 1}, salt="cookie-session-participant"), base_url=ASK) is None


def test_a_sign_in_works_with_the_researchers_key(app):
    app.config.update(RESEARCHER_SESSION_KEY=RESEARCHER_KEY)
    browser = app.test_client()
    assert browser.get("/auth/dev-login?orcid=0000-0000-0000-001X").status_code == 302
    browser.post("/terms/", data={"accept": "1"})
    assert browser.get("/dashboard/").status_code == 200
    app.config.update(RESEARCHER_SESSION_KEY="")  # the key taken away again: that session is worth nothing
    assert browser.get("/dashboard/").status_code == 302


def test_the_participants_side_leaves_mails_in_the_queue(app):
    """SIDE=participant: no login at the mail server (no MAIL_PASSWORD there), the mail waits in the queue, which the
    housekeeping sends (mailqueue.run)."""
    from app import mailqueue

    sent = mail_on(app)
    app.config.update(SIDE="participant", PARTICIPANT_BASE_URL=ASK)
    with app.test_request_context("/", base_url=ASK):
        assert mail.send("person@example.org", "Ihre Beschwerde ist eingegangen", "Text") is None
        mailqueue.kick()  # does nothing on this side
        assert sent == []
        row = db.session.scalar(db.select(MailOutbox))
        assert (row.to, row.kind, row.sent_at) == ("person@example.org", "notice", None)
    app.config.update(SIDE="")
    with app.app_context():
        assert mailqueue.send_some(10) == 1
    assert [m["To"] for m in sent] == ["person@example.org"]


def test_other_sides_send_at_once_as_before(app):
    sent = mail_on(app)
    with app.test_request_context("/"):
        mail.send("person@example.org", "Betreff", "Text")
        assert db.session.scalar(db.select(db.func.count(MailOutbox.id))) == 0
    assert len(sent) == 1


def test_the_stay_signed_in_cookie_takes_the_researchers_key(app):
    """Flask-Login signs its remember cookie with SECRET_KEY by itself: with RESEARCHER_SESSION_KEY it takes that key,
    so the participants' side cannot forge it either (app/cookies.install_remember_key)."""
    from flask_login.utils import decode_cookie, encode_cookie

    with app.test_request_context("/"):
        forged = encode_cookie("1.0.x", key=app.config["SECRET_KEY"])
        assert decode_cookie(forged) == "1.0.x"                                 # without the setting: as before
        app.config.update(RESEARCHER_SESSION_KEY=RESEARCHER_KEY)
        assert decode_cookie(forged) is None
        assert decode_cookie(encode_cookie("1.0.x")) == "1.0.x"
        assert encode_cookie("1.0.x") == encode_cookie("1.0.x", key=RESEARCHER_KEY)
