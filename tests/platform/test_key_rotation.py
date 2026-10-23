"""The server's keys after the security audit of 2026-10-13: a key too short to be random stops the start, a weak one
is named in the log and on Administration > Server (KR-5); TOKEN_KEY and SECRET_KEY can be changed with the keys of
before as fallbacks, and app/key_rotation.py locks every stored secret anew (KR-6)."""
import logging
from datetime import datetime
import secrets

import pytest

from app import key_strength, secrets_box
from app.extensions import db
from app.models import Survey, User, UserLogin, ZenodoConnection
from config import Config

STRONG = secrets.token_urlsafe(48)
OLD, NEW = secrets.token_hex(32), secrets.token_hex(32)


def test_key_strength_verdicts():
    """KR-5: a short or made-up key was taken without a word."""
    assert key_strength.verdict("askurious2026") == "short"
    assert key_strength.verdict("a" * 40) == "trivial"                              # long, but no variety at all
    assert key_strength.verdict("askurious2026askurious2026") == "weak"             # 26 characters
    assert key_strength.verdict(secrets.token_hex(32)) is None                      # openssl rand -hex 32
    assert key_strength.verdict(secrets.token_hex(24)) is None                      # POSTGRES_PASSWORD's kind
    assert key_strength.verdict(STRONG) is None
    found = key_strength.findings({"SECRET_KEY": STRONG, "TOKEN_KEY": "", "POOL_KEY": "x" * 40,
                                   "TOKEN_KEY_FALLBACKS": ["short-one"]})
    assert found == [("TOKEN_KEY_FALLBACKS", "short"), ("POOL_KEY", "trivial")]   # names only; empty is not checked


def _live(**keys):
    class Live(Config):
        PUBLIC_BASE_URL = "https://askurious.example"
        SECRET_KEY = STRONG
        DEV_LOGIN = False
        SQLALCHEMY_DATABASE_URI = "sqlite://"
    for name, value in keys.items():
        setattr(Live, name, value)
    return Live


def test_a_server_refuses_a_key_too_short_to_be_random():
    """KR-5: outside this computer the app does not start with a SECRET_KEY or TOKEN_KEY under 16 characters."""
    from app import create_app

    with pytest.raises(RuntimeError, match="SECRET_KEY shorter than 16"):
        create_app(_live(SECRET_KEY="askurious2026"))
    with pytest.raises(RuntimeError, match="TOKEN_KEY shorter than 16"):
        create_app(_live(TOKEN_KEY="geheim"))


def test_a_server_warns_about_a_weak_key_and_refuses_a_trivial_one(caplog):
    """KR-5 and audit 2026-10-18, CFG-02: a weak key that is long enough starts (a deploy must not stop a server whose
    keys worked so far) but says so at every start (names only, never the value); one character over and over (0 bits)
    is refused like a short key."""
    from app import key_strength as ks

    class FakeApp:
        config = {"SECRET_KEY": "askurious-askurious-askurious-01", "TOKEN_KEY": STRONG}
        logger = logging.getLogger("keytest")

    with caplog.at_level(logging.ERROR, logger="keytest"):
        assert ks.check(FakeApp, local=False) == [("SECRET_KEY", "weak")]
    assert "SECRET_KEY is weak" in caplog.text and "askurious-askurious" not in caplog.text
    FakeApp.config = {"SECRET_KEY": "a" * 40}
    with pytest.raises(RuntimeError, match="SECRET_KEY is one character over and over") as refused:
        ks.check(FakeApp, local=False)
    assert "aaaa" not in str(refused.value)
    # on this computer nothing is refused (the development key is checked by create_app itself)
    FakeApp.config = {"SECRET_KEY": "short"}
    assert ks.check(FakeApp, local=True) == [("SECRET_KEY", "short")]


def test_made_up_keys_and_passphrases_are_weak():
    """CFG-02: the estimate by the characters' frequencies took words for random characters. Random keys of the usual
    tools stay strong."""
    for made_up in ("askurious-secret-key-for-production-2026", "correct horse battery staple askurious",
                    "Correct-Horse-Battery-Staple-Zebra-Moon-Light", "mysupersecretflasksessionkey2026xyz",
                    "polltergeist_production_secret_key_2026_10"):
        assert key_strength.verdict(made_up) == "weak", made_up
    assert key_strength.verdict("a" * 40) == "trivial"
    for _ in range(300):
        for key in (secrets.token_hex(32), secrets.token_hex(24), secrets.token_urlsafe(48), secrets.token_urlsafe(32)):
            assert key_strength.verdict(key) is None, key


def test_a_server_starts_with_a_made_up_key_but_not_with_a_trivial_one(caplog):
    from app import create_app

    with pytest.raises(RuntimeError, match="one character over and over"):
        create_app(_live(SECRET_KEY="a" * 40))
    with caplog.at_level(logging.ERROR):
        assert create_app(_live(SECRET_KEY="askurious-secret-key-for-production-2026")) is not None
    assert "SECRET_KEY is weak" in caplog.text


def test_weak_keys_and_admin_totp_on_the_server_page(app, client, monkeypatch):
    """KR-5, SV-4/DP-8 and SV-5/DP-9: Administration > Server says when the second factor for admins is off, which
    keys are weak (names only) and which checks the last deploy skipped, with the steps; nothing once all is fine."""
    import app as app_module
    from app import admin

    monkeypatch.setattr(app_module, "_deployed_at", lambda: {"at": datetime(2026, 10, 13, 6, 43), "id": "392c141f", "skipped": ["tests", "osv"]})
    # a strong SECRET_KEY with the test's key of before as fallback: the session of the sign-in stays (KR-6)
    app.config.update(SECRET_KEY_FALLBACKS=[app.config["SECRET_KEY"]], SECRET_KEY=STRONG, TOKEN_KEY="x" * 40,
                      ADMIN_TOTP=False)
    page = client.get("/admin/database").get_data(as_text=True)
    assert 'id="sicherheit"' in page and "Bei einer ORCID-Anmeldung" in page and "ADMIN_TOTP=1" not in page
    assert "<code>TOKEN_KEY</code>" in page and "x" * 40 not in page
    assert "Prüfungen übersprungen" in page and "Prüfung auf bekannte Schwachstellen" in page
    monkeypatch.setattr(app_module, "_deployed_at", lambda: {"at": datetime(2026, 10, 13, 6, 43), "id": "392c141f", "skipped": []})
    app.config.update(TOKEN_KEY=STRONG, ADMIN_TOTP=True)
    app.config["SECRET_KEY_FALLBACKS"] = [*app.config["SECRET_KEY_FALLBACKS"], "old-short-key"]
    with app.app_context():
        # a short key of before, kept as a fallback, is named as well: an old key opens as much as the current
        assert admin._operation_security(app) == {"admin_totp": True, "keys": [("SECRET_KEY_FALLBACKS", "short")],
                                                  "skipped": []}
    app.config.update(ADMIN_TOTP=False)
    page = client.get("/admin/database").get_data(as_text=True)
    assert 'id="sicherheit"' in page and "<code>TOKEN_KEY</code>" not in page and "übersprungen" not in page


def test_deployed_at_reads_the_skipped_line(tmp_path, monkeypatch):
    """SV-5: the third line of app/deployed_at.txt; files without it (every deploy before) read as before."""
    import app as app_module

    target = tmp_path / "app"
    target.mkdir()
    (target / "deployed_at.txt").write_text("2026-10-13T04:43:14Z\n392c141f\nskipped=tests,osv\n")
    monkeypatch.setattr(app_module, "__file__", str(target / "__init__.py"))
    stamp = app_module._deployed_at()
    assert stamp["id"] == "392c141f" and stamp["skipped"] == ["tests", "osv"]
    (target / "deployed_at.txt").write_text("2026-10-13T04:43:14Z\n392c141f\n")
    assert app_module._deployed_at()["skipped"] == []


def test_token_key_changes_without_losing_secrets(app, survey):
    """KR-6: a new TOKEN_KEY made everything locked with the old one unreadable (second factors locked the
    administration). With the old key in TOKEN_KEY_FALLBACKS it still opens; app/key_rotation.py locks it anew, after
    which the fallback can go."""
    from app import key_rotation

    app.config.update(TOKEN_KEY=OLD, TOKEN_KEY_FALLBACKS=[])
    with app.app_context():
        user = db.session.scalars(db.select(User)).first()
        db.session.add(UserLogin(user_id=user.id, totp_box=secrets_box.encrypt("JBSWY3DPEHPK3PXP")))
        db.session.add(ZenodoConnection(user_id=user.id, target="zenodo", access_token=secrets_box.encrypt("tok"),
                                        refresh_token=secrets_box.encrypt("ref")))
        s = db.session.get(Survey, 1)
        s.panel = {**(s.panel or {}), "sign": {"key": secrets_box.encrypt("panel-key")}}
        db.session.commit()
    # the new key without the old one as fallback: nothing opens (as before this change)
    app.config.update(TOKEN_KEY=NEW)
    with app.app_context():
        assert secrets_box.decrypt(db.session.scalars(db.select(UserLogin)).first().totp_box) == ""
    # with the fallback everything opens, and the rotation locks it with the new key
    app.config.update(TOKEN_KEY_FALLBACKS=[OLD])
    with app.app_context():
        assert secrets_box.decrypt(db.session.scalars(db.select(UserLogin)).first().totp_box) == "JBSWY3DPEHPK3PXP"
        counts = key_rotation.rotate()
        assert counts["totp"] == 1 and counts["zenodo"] == 2 and counts["surveys"] == 1
        assert not any(key_rotation.unreadable().values())
        assert key_rotation.rotate()["totp"] == 0                                  # once is enough
    app.config.update(TOKEN_KEY_FALLBACKS=[])
    with app.app_context():
        assert secrets_box.decrypt(db.session.scalars(db.select(UserLogin)).first().totp_box) == "JBSWY3DPEHPK3PXP"
        conn = db.session.scalars(db.select(ZenodoConnection)).first()
        assert secrets_box.decrypt(conn.refresh_token) == "ref"
        assert secrets_box.decrypt(db.session.get(Survey, 1).panel["sign"]["key"]) == "panel-key"


def test_rotation_reaches_the_keys_in_the_flow(app, client, survey):
    """KR-6: the signing key of an external section lives in the survey's flow; the rotation locks it anew too."""
    from tests.platform.test_security_keys import _key, _node, _sign_on
    from tests.editor.test_external import build

    app.config.update(TOKEN_KEY=OLD, TOKEN_KEY_FALLBACKS=[])
    nid = build(app, client)
    _sign_on(client, nid)
    plain = _key(app)
    assert plain
    app.config.update(TOKEN_KEY=NEW, TOKEN_KEY_FALLBACKS=[OLD])
    from app import key_rotation

    with app.app_context():
        assert key_rotation.rotate()["surveys"] == 1
    app.config.update(TOKEN_KEY_FALLBACKS=[])
    assert _key(app) == plain
    with app.app_context():
        assert secrets_box.is_current(_node(app)["sign"]["key"])


def test_secret_key_fallbacks_keep_sessions_and_invitation_links(app, client, survey):
    """KR-6: SECRET_KEY_FALLBACKS was a setting of Flask only, never read from the .env: a new SECRET_KEY signed
    everyone out at once. Now it comes from the .env, and the invitation links made with the key of before still
    count."""
    from app import survey_invites

    assert Config.SECRET_KEY_FALLBACKS == [] and Config.TOKEN_KEY_FALLBACKS == []
    before = app.config["SECRET_KEY"]
    assert client.get("/admin/database").status_code == 200
    app.config.update(SECRET_KEY=STRONG, SECRET_KEY_FALLBACKS=[before])
    assert client.get("/admin/database").status_code == 200                       # still signed in
    app.config.update(TOKEN_KEY=NEW, TOKEN_KEY_FALLBACKS=[OLD])
    with app.app_context():
        keys = survey_invites._keys()
    app.config.update(SECRET_KEY_FALLBACKS=[])
    assert client.get("/admin/database").status_code == 302                       # without it: signed out
    import hashlib

    assert keys[0] == hashlib.sha256(("survey-invite-key:" + NEW).encode()).digest()
    assert hashlib.sha256(("survey-invite-key:" + OLD).encode()).digest() in keys
    assert before.encode() in keys and STRONG.encode() in keys


def test_config_reads_the_fallbacks(monkeypatch):
    """KR-6: both lists come from the .env, comma-separated (config._list)."""
    import config

    monkeypatch.setenv("TOKEN_KEY_FALLBACKS", f"{OLD}, {NEW}")
    assert config._list("TOKEN_KEY_FALLBACKS") == [OLD, NEW]
    source = open(config.__file__, encoding="utf-8").read()
    assert 'TOKEN_KEY_FALLBACKS = _list("TOKEN_KEY_FALLBACKS")' in source
    assert 'SECRET_KEY_FALLBACKS = _list("SECRET_KEY_FALLBACKS")' in source


def test_rotation_preserves_dashboard_and_quota_urls(app, survey):
    from app import key_rotation
    from app.models import Board, BoardShare, QuotaShare, utcnow
    from app.shared_tokens import find

    app.config.update(TOKEN_KEY=OLD, TOKEN_KEY_FALLBACKS=[])
    with app.app_context():
        board = Board(survey_id=survey, name="Rotation", tiles=[])
        db.session.add(board)
        db.session.flush()
        db.session.add_all([BoardShare(board_id=board.id, token="dashboard-secret", expires_at=utcnow()),
                            QuotaShare(survey_id=survey, token="quota-secret", expires_at=utcnow())])
        db.session.commit()
        app.config.update(TOKEN_KEY=NEW, TOKEN_KEY_FALLBACKS=[OLD])
        counts = key_rotation.rotate()
        assert counts["dashboard_links"] == counts["quota_links"] == 1
        app.config["TOKEN_KEY_FALLBACKS"] = []
        assert find(BoardShare, "dashboard-secret").token == "dashboard-secret"
        assert find(QuotaShare, "quota-secret").token == "quota-secret"
        assert not any(key_rotation.unreadable().values())


def test_the_participants_box_opens_without_token_key(app):
    """Audit 2026-10-22, E-01: the survey keys and seals the participants' containers need go in the box PARTICIPANT,
    locked with PARTICIPANT_TOKEN_KEY; there TOKEN_KEY can go. Without PARTICIPANT_TOKEN_KEY it is TOKEN_KEY's box,
    and TOKEN_KEY always opens it, so setting the key loses nothing; key_rotation locks the survey keys anew."""
    from app import key_rotation, secrets_box

    app.config.update(TOKEN_KEY="t" * 40, PARTICIPANT_TOKEN_KEY="")
    with app.app_context():
        old = secrets_box.encrypt("geheim", secrets_box.PARTICIPANT)              # before the key: TOKEN_KEY's box
        sealed_old = secrets_box.seal("diary-entry", b"x", secrets_box.PARTICIPANT)
        app.config["PARTICIPANT_TOKEN_KEY"] = "p" * 40
        assert secrets_box.decrypt(old, secrets_box.PARTICIPANT) == "geheim"         # still opens
        assert secrets_box.unseal("diary-entry", sealed_old, secrets_box.PARTICIPANT) == b"x"
        new = secrets_box.reencrypt(old, secrets_box.PARTICIPANT)
        assert new != old and secrets_box.is_current(new, secrets_box.PARTICIPANT)
        sealed = secrets_box.seal("diary-entry", b"y", secrets_box.PARTICIPANT)
        mine = secrets_box.encrypt("nur Forschende")                                 # the researchers' box
    # a participants' container: no TOKEN_KEY
    app.config.update(TOKEN_KEY="")
    with app.app_context():
        assert secrets_box.decrypt(new, secrets_box.PARTICIPANT) == "geheim"
        assert secrets_box.unseal("diary-entry", sealed, secrets_box.PARTICIPANT) == b"y"
        assert secrets_box.decrypt(mine) == "" and secrets_box.decrypt(mine, secrets_box.PARTICIPANT) == ""
    assert key_rotation  # the rotation locks the survey keys in this box (app/key_rotation.py)


def test_the_log_says_when_the_sides_are_not_separated(app):
    """E-01: on the Docker server (SIDE set) a missing RESEARCHER_SESSION_KEY or PARTICIPANT_TOKEN_KEY is named."""
    from app import key_strength

    assert key_strength.separation_gaps({"SIDE": ""}) == []
    assert key_strength.separation_gaps({"SIDE": "participant", "RESEARCHER_SESSION_KEY": "x"}) == [
        "PARTICIPANT_TOKEN_KEY is empty"]
    # the participants' containers never get RESEARCHER_SESSION_KEY: no warning about it there
    assert key_strength.separation_gaps({"SIDE": "participant", "PARTICIPANT_TOKEN_KEY": "y"}) == []
    assert key_strength.separation_gaps({"SIDE": "researcher", "PARTICIPANT_TOKEN_KEY": "y"}) == [
        "RESEARCHER_SESSION_KEY is empty"]
    assert key_strength.separation_gaps({"SIDE": "researcher", "RESEARCHER_SESSION_KEY": "x",
                                         "PARTICIPANT_TOKEN_KEY": "y"}) == []
