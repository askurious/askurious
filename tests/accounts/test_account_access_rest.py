"""What a password change, "Überall abmelden" and a first password do to an account's other credentials, what the
security log and Slack hear of, and the lock after 100 wrong passwords (Abgleich 2026-10-15: AU-2 rest, T01-N2,
AU-7, T01-N1, AU-9 rest)."""
import json
import logging
import re
from datetime import timedelta

from app import account_access, limits, passwords, twofactor
from app.extensions import db
from app.models import (AiAccess, CoscineConnection, DataToken, User, UserLogin, ZenodoConnection, utcnow)
from tests.accounts.test_audit12_v7_anmeldung import GOOD, MAIL, guest, told  # noqa: F401 (fixtures)
from tests.conftest import mail_on


def _connect_all(user_id):
    db.session.add_all([ZenodoConnection(user_id=user_id, target="sandbox", access_token="x"),
                        CoscineConnection(user_id=user_id, token="x"),
                        AiAccess(user_id=user_id, provider="custom", base_url="https://llm.example.org/v1", key_enc="x")])
    db.session.add(DataToken(user_id=user_id, survey_id=1, name="Skript", token_hash="d" * 64,
                             expires_at=utcnow() + timedelta(days=30)))


def _connections(user_id):
    return [db.session.scalar(db.select(db.func.count()).select_from(m).where(m.user_id == user_id))
            for m in (ZenodoConnection, CoscineConnection, AiAccess)]


def test_sign_out_everywhere_disconnects_zenodo_coscine_and_own_ai(app, client, survey):
    """AU-2 rest: whoever had the account could have left their own token or base URL there."""
    with app.app_context():
        _connect_all(1)
        db.session.commit()
    page = client.post("/account/sign-out-everywhere", follow_redirects=True).get_data(as_text=True)
    assert "diese Verbindungen getrennt: Zenodo, Coscine, eigener KI-Zugang" in page
    with app.app_context():
        assert _connections(1) == [0, 0, 0] and db.session.scalar(db.select(DataToken)) is None


def test_password_change_disconnects_too(app, client, survey):
    with app.app_context():
        user = db.session.get(User, 1)
        passwords.set_password(user, GOOD)
        _connect_all(1)
        db.session.commit()
        passwords.set_password(user, GOOD + " neu")
        db.session.commit()
        assert _connections(1) == [0, 0, 0]


def test_first_password_by_confirmation_link_revokes_nothing(app, client, survey):
    """T01-N2: an ORCID account adding its first password keeps its tokens, invitations and connections."""
    with app.app_context():
        _connect_all(1)
        db.session.commit()
    sent = mail_on(app)
    client.post("/account/password", data={"email": "neu@example.org", "password": GOOD, "repeat": GOOD})
    link = re.search(r"(/account/confirm-email/[\w-]+)", sent[-1].get_content())[1]
    assert "Datenzugänge, Einladungen und Verbindungen bleiben" in client.get(link).get_data(as_text=True)
    assert client.post(link).status_code == 302
    with app.app_context():
        assert passwords.matches(db.session.get(User, 1), GOOD)
        assert _connections(1) == [1, 1, 1] and db.session.scalar(db.select(DataToken)) is not None


def _events(caplog):
    return [json.loads(r.getMessage().split(" ", 1)[1]) for r in caplog.records if r.name == "askurious.security"]


def test_security_log_names_rights_experiments_and_signing_out(app, client, survey, caplog):
    """AU-7: may_create, experimental features, "Überall abmelden", invitations and join links made."""
    caplog.set_level(logging.INFO, logger="askurious.security")
    logging.getLogger("askurious.security").propagate = True
    try:
        with app.app_context():
            other = User(orcid="0000-0002-1825-0097", name="Andere")
            db.session.add(other)
            db.session.commit()
            other_id = other.id
        client.post(f"/admin/accounts/{other_id}", data={"action": "may_create", "value": "0"})
        client.post("/admin/experimental", data={"feature": "coding", "target": f"u:{other_id}", "action": "blocked"})
        client.post("/account/sign-out-everywhere")
        with app.test_request_context():
            from app import invites, joinlinks
            from app.models import Survey, WorkGroup

            group = WorkGroup(name="Team")
            db.session.add(group)
            db.session.commit()
            me = db.session.get(User, 1)
            joinlinks.create(group, me)
            mail_on(app)
            invites.create("gast@example.org", "viewer", me, survey=db.session.get(Survey, 1))
    finally:
        logging.getLogger("askurious.security").propagate = False
    names = {e["event"]: e for e in _events(caplog)}
    assert names["may_create_changed"]["account"] == other_id and names["may_create_changed"]["by"] == 1
    assert names["experiment_changed"]["kind"] == "coding" and names["experiment_changed"]["reason"] == "blocked"
    assert "signed_out_everywhere" in names and "join_link_created" in names
    assert names["invitation_created"]["role"] == "viewer"


def test_slack_hears_of_admin_accounts_only(app, client, monkeypatch):
    """T01-N1: a researcher with a voluntary factor is no "Admin-Konto" in Slack."""
    from app import notify

    sent = []
    for name in ("wrong_code", "backup_code_used", "admin_password_reset", "factor_off", "hard_locked"):
        monkeypatch.setattr(notify, name, lambda user, name=name: sent.append(name))
    with app.app_context():
        researcher = User(orcid="0000-0002-1825-0097", name="Forscherin")
        db.session.add(researcher)
        db.session.commit()
        for what in ("backup_code_used", "admin_password_reset", "factor_off"):
            twofactor.tell(what, researcher)
        twofactor.alert_wrong_code(researcher)
        twofactor.tell("hard_locked", researcher)  # not an admin message: every account
        assert sent == ["hard_locked"]
        twofactor.tell("factor_off", db.session.get(User, 1))  # the site admin of tests/conftest.py
        assert sent == ["hard_locked", "factor_off"]


def test_a_known_browser_is_not_held_by_the_lock_after_100_wrong_tries(app, guest, told, monkeypatch):  # noqa: F811
    """AU-9 rest: whoever only knows the address locks every other browser, not the one the owner signed in from."""
    monkeypatch.setattr(limits, "login_paused", lambda *a, **k: False)
    owner = app.test_client()
    assert owner.post("/auth/sign-in", data={"email": MAIL, "password": GOOD}).status_code == 302
    owner.get("/auth/sign-out")
    with app.app_context():
        row = db.session.get(UserLogin, guest)
        row.failed_streak, row.hard_locked_at = passwords.HARD_LIMIT, utcnow()
        db.session.commit()
    stranger = app.test_client()
    assert stranger.post("/auth/sign-in", data={"email": MAIL, "password": GOOD}).status_code == 401
    assert owner.post("/auth/sign-in", data={"email": MAIL, "password": GOOD}).status_code == 302
    with app.app_context():
        assert db.session.get(UserLogin, guest).failed_streak == 0  # the right password ends the row
        row = db.session.get(UserLogin, guest)
        row.failed_streak, row.hard_locked_at = passwords.HARD_LIMIT, utcnow()
        passwords.set_password(db.session.get(User, guest), GOOD + " neu")  # a new password: the old cookie is void
        row.failed_streak, row.hard_locked_at = passwords.HARD_LIMIT, utcnow()
        db.session.commit()
    assert owner.post("/auth/sign-in", data={"email": MAIL, "password": GOOD + " neu"}).status_code == 401


def test_revoke_tells_nobody_when_nothing_was_connected(app):
    with app.test_request_context():
        assert account_access.disconnect_services(12345) == []


def test_sign_out_everywhere_blocks_the_interviewer_accesses_the_account_made(app, client):
    """Audit 2026-10-22, A-04: an interviewer's access (up to a year) made by whoever had the account stayed valid
    after "Überall abmelden" and a new password. It is blocked now; the guide's admins unblock a real one."""
    from datetime import timedelta

    from app.models import InterviewerAccess, utcnow

    with app.app_context():
        mine = InterviewerAccess(survey_id=1, name="Selbst angelegt", token_hash="a" * 64, revision="r", created_by_id=1,
                                 expires_at=utcnow() + timedelta(days=365))
        other = InterviewerAccess(survey_id=1, name="Von jemand anderem", token_hash="b" * 64, revision="r",
                                  created_by_id=999, expires_at=utcnow() + timedelta(days=365))
        db.session.add_all([mine, other])
        db.session.commit()
    page = client.post("/account/sign-out-everywhere", follow_redirects=True).get_data(as_text=True)
    assert "Interviewlinks gesperrt (1)" in page
    with app.app_context():
        rows = {a.name: a.blocked for a in db.session.scalars(db.select(InterviewerAccess))}
        assert rows == {"Selbst angelegt": True, "Von jemand anderem": False}
