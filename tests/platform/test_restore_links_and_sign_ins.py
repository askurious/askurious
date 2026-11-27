"""After a dump was played back, deploy/docker/reapply_erasures.py also brings back what ended links and sign-ins
since the dump: revoked links and accesses go again, a blocked or renewed interviewer link and a blocked account stay
so, sessions signed out stay signed out, a password or second factor changed since the dump is not replaced by the
old one, a passkey removed since the dump signs nobody in again, and entries of invitation lists that opted out or
were deleted do not come back."""
import shutil
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import (AgentToken, AiChatTurn, InterviewerAccess, MailOutbox, MemberInvite, Passkey, Response,
                        SurveyInvite, User, UserLogin, utcnow)
from tests.platform.test_audit10_betrieb import _close, _tool
from tests.privacy.test_erasure import build, take

LATER = utcnow() + timedelta(days=30)


def _before_the_dump(app):
    """What the dump holds: links, an account with password and factor, an invitation list."""
    with app.app_context():
        user = User(orcid=None, name="Kim", number=77)
        db.session.add(user)
        db.session.flush()
        db.session.add(UserLogin(user_id=user.id, email="kim@example.org", password_hash="old-hash",
                                 password_changed_at=utcnow() - timedelta(days=9), totp_box="old-factor",
                                 session_version=3, status="active"))
        db.session.add_all([
            Passkey(user_id=user.id, credential_id="weg", public_key="k1", user_handle="h", name="Verloren"),
            Passkey(user_id=user.id, credential_id="bleibt", public_key="k2", user_handle="h", name="Laptop"),
            AgentToken(user_id=1, name="weg", token_hash="a" * 64, expires_at=LATER),
            AgentToken(user_id=1, name="bleibt", token_hash="b" * 64, expires_at=LATER),
            MemberInvite(email="x@example.org", role="editor", token_hash="c" * 64, survey_id=1, created_by_id=1,
                         expires_at=LATER),
            InterviewerAccess(survey_id=1, name="Anna", token_hash="d" * 64, revision="r1", expires_at=LATER),
            SurveyInvite(survey_id=1, email="raus@example.org", email_hash="h1", name="Raus", token_hash="t1"),
            SurveyInvite(survey_id=1, email="weg@example.org", email_hash="h2", name="Weg", token_hash="t2"),
            SurveyInvite(survey_id=1, email="bleibt@example.org", email_hash="h3", name="Bleibt", token_hash="t3"),
        ])
        db.session.flush()
        raus = db.session.scalar(db.select(SurveyInvite.id).filter_by(email_hash="h1"))
        db.session.add(MailOutbox(survey_id=1, invite_id=raus, to="raus@example.org", subject="s", body="b",
                                  kind="reminder", sender_id=1))
        db.session.commit()
        return user.id


def _since_the_dump(app, user_id):
    with app.app_context():
        db.session.execute(db.delete(AgentToken).where(AgentToken.name == "weg"))            # revoked
        db.session.execute(db.delete(Passkey).where(Passkey.credential_id == "weg"))        # removed (a lost device)
        db.session.execute(db.delete(MemberInvite))                                         # withdrawn
        access = db.session.scalar(db.select(InterviewerAccess))
        access.blocked, access.revision, access.token_hash = True, "r2", "e" * 64           # blocked, new link
        login = db.session.get(UserLogin, user_id)
        login.password_hash, login.password_changed_at = "new-hash", utcnow()               # new password
        login.totp_box, login.session_version, login.status = None, 5, "blocked"            # factor removed, signed out
        raus = db.session.scalar(db.select(SurveyInvite).filter_by(email_hash="h1"))
        raus.status, raus.email, raus.name = "optout", "", ""
        db.session.execute(db.delete(MailOutbox))
        db.session.execute(db.delete(SurveyInvite).where(SurveyInvite.email_hash == "h2"))
        db.session.commit()


@pytest.mark.parametrize("tables_only", [False, True])
def test_what_ended_links_and_sign_ins_since_the_dump_holds_after_the_restore(app, client, survey, tmp_path, tables_only,
                                                                               capsys):
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        pytest.skip("plays the restore with a copy of the SQLite file")
    uid = build(app, client)
    take(app.test_client(), uid, "eins")
    user_id = _before_the_dump(app)
    with app.app_context():
        rid = db.session.scalar(db.select(Response.id).filter_by(survey_id=1, is_preview=False))
        db.session.add(AiChatTurn(survey_id=1, response_id=rid, item_id=2, seq=1, role="user", text="hallo"))
        db.session.commit()
    live = tmp_path / "test.db"
    _close(app)
    shutil.copyfile(live, tmp_path / "dump.db")
    _since_the_dump(app, user_id)
    if tables_only:
        from app import erasure
        from app.models import Survey

        with app.app_context():
            erasure.erase(db.session.get(Survey, 1), [db.session.get(Response, rid)], "request")
    tool, saved = _tool(), str(tmp_path / "saved.json")
    with app.app_context():
        tool.save(db.engine, saved)
    with open(saved, encoding="utf-8") as f:
        text = f.read()
    assert "new-hash" not in text and "old-hash" not in text and "example.org" not in text   # no hashes, no addresses
    _close(app)
    shutil.copyfile(tmp_path / "dump.db", live)                                              # pg_restore
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(AgentToken.id))) == 2               # everything is back
        dry = tool.apply(db.engine, saved)
        assert dry["links"]["agent_token"] == 1 and dry["passwords"] == [user_id] and dry["optouts"] == 1
        assert dry["tokens"]["passkey"] == 1
        assert db.session.scalar(db.select(db.func.count(Passkey.id))) == 2                  # the removed one is back
        assert db.session.scalar(db.select(db.func.count(AgentToken.id))) == 2               # a dry run changes nothing
    if tables_only:
        _close(app)
        import sqlalchemy as sa

        engine = sa.create_engine(app.config["SQLALCHEMY_DATABASE_URI"])
        report = tool.apply(engine, saved, do=True, tables_only=True)
        engine.dispose()
    else:
        with app.app_context():
            report = tool.apply(db.engine, saved, do=True)
    assert not report["refused"]
    with app.app_context():
        assert [t.name for t in db.session.scalars(db.select(AgentToken))] == ["bleibt"]
        assert db.session.scalar(db.select(MemberInvite)) is None
        assert [p.name for p in db.session.scalars(db.select(Passkey))] == ["Laptop"]   # the removed passkey is gone again
        access = db.session.scalar(db.select(InterviewerAccess))
        assert (access.blocked, access.revision, access.token_hash) == (True, "r2", "e" * 64)
        login = db.session.get(UserLogin, user_id)
        assert login.status == "blocked" and login.session_version == 5
        assert login.password_hash is None and login.totp_box is None       # the old ones do not hold again
        states = {i.email_hash: (i.status, i.email, i.name) for i in db.session.scalars(db.select(SurveyInvite))}
        assert states == {"h1": ("optout", "", ""), "h3": ("open", "bleibt@example.org", "Bleibt")}
        assert db.session.scalar(db.select(MailOutbox)) is None             # no reminder to an address that opted out
        if tables_only:
            assert db.session.get(Response, rid) is None
            assert db.session.scalar(db.select(AiChatTurn)) is None         # what the app's way takes along
    out = capsys.readouterr().out
    assert f"Konto {user_id} hat sein Passwort seit dem Dump geändert" in out and "zweiten Faktor" in out
    assert "Passkeys: 1" in out


def test_a_sign_out_before_the_dump_is_never_taken_back(app, client, survey, tmp_path):
    """The restored database is newer in one respect (a sign-in version raised there): the higher number stays."""
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        pytest.skip("plays the restore with a copy of the SQLite file")
    user_id = _before_the_dump(app)
    tool, saved = _tool(), str(tmp_path / "saved.json")
    with app.app_context():
        tool.save(db.engine, saved)
        db.session.get(UserLogin, user_id).session_version = 9
        db.session.commit()
        report = tool.apply(db.engine, saved, do=True)
        assert not report["refused"]
        db.session.expire_all()
        login = db.session.get(UserLogin, user_id)
        assert login.session_version == 9 and login.password_hash == "old-hash" and login.totp_box == "old-factor"
