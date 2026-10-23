"""Security and performance audit 2026-10-09, package P3 "Deploy-Sicherheit": a dump before migrations (BT3), the
participants stopped before a migration without ROLLING = True (BT2), no rollback over a migration (BT3), an alarm
in Slack when something fails unattended (BT6), the age of the last dump on the status page (BT6), an unreadable
second factor locks instead of opening (K1), TOKEN_KEY can be set without losing what was locked before (BT16)."""
import os
import time

import pytest

from app import secrets_box, twofactor
from app.extensions import db
from app.models import User
from app.status_node import check, disrupted, parse_checks
from tests.platform.test_audit8_b_deploy import DOCKER, World, _read, _server_from_before, _write, needs_bash


def _migration(rev, down, rolling=None):
    mark = "" if rolling is None else f"ROLLING = {rolling}\n"
    return f"revision = '{rev}'\ndown_revision = {repr(down)}\n{mark}\ndef upgrade():\n    pass\n"


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    _server_from_before(w)
    return w


def _with_migrations(world, files, text="with migrations"):
    for name, body in files.items():
        _write(str(world.repo / "migrations" / "versions" / name), body)
    world.commit(text, push=True)


def _log(world):
    return world.text("docker.log") or ""


@needs_bash
@pytest.mark.parametrize("force", ["", "1"])
def test_incompatible_migration_stops_before_upload_and_restart(world, force):
    _with_migrations(world, {"0001_a.py": _migration("0001", None), "0002_b.py": _migration("0002", "0001")})
    before = world.text("askurious", "app", "deployed_at.txt")
    out = world.run(FAKE_DB_VERSION="0001", FORCE_ROLLING=force)
    assert out.returncode != 0 and "Deploy refused" in out.stderr
    log = _log(world)
    assert "compose stop" not in log and "compose up" not in log and "pg_dump" not in log
    assert world.text("askurious", "app", "deployed_at.txt") == before


@needs_bash
def test_bt3_rolling_migration_dumps_but_keeps_participants_running(world):
    _with_migrations(world, {"0001_a.py": _migration("0001", None), "0002_b.py": _migration("0002", "0001", True)})
    out = world.run(FAKE_DB_VERSION="0001")
    assert out.returncode == 0, out.stdout + out.stderr
    log = _log(world)
    assert "pg_dump" in log and "compose stop" not in log
    assert "compose up -d --no-deps participant-a" in log


@needs_bash
def test_no_dump_without_a_pending_migration(world):
    _with_migrations(world, {"0001_a.py": _migration("0001", None), "0002_b.py": _migration("0002", "0001", True)})
    assert world.run(FAKE_DB_VERSION="0002").returncode == 0
    assert "pg_dump" not in _log(world) and not world.path("backups").exists()


@needs_bash
def test_bt3_failed_dump_stops_the_deploy_before_any_restart(world):
    _with_migrations(world, {"0001_a.py": _migration("0001", None), "0002_b.py": _migration("0002", "0001", True)})
    out = world.run(FAKE_DB_VERSION="0001", FAKE_DUMP_FAIL="1")
    assert out.returncode != 0 and "dump failed" in out.stderr
    assert "compose up" not in _log(world) and "compose stop" not in _log(world)


@needs_bash
def test_bt3_no_rollback_over_a_migration(world):
    _with_migrations(world, {"0001_a.py": _migration("0001", None)}, "first with migrations")
    assert world.run(FAKE_DB_VERSION="0001").returncode == 0
    first = world.text("askurious", "app", "deployed_at.txt")
    _with_migrations(world, {"0002_b.py": _migration("0002", "0001", True)}, "second")
    assert world.run(FAKE_DB_VERSION="0001").returncode == 0
    second = world.text("askurious", "app", "deployed_at.txt")
    out = world.run("rollback", FAKE_DB_VERSION="0002")                          # the database migrated already
    assert out.returncode != 0 and "does not know" in out.stderr and "Nothing changed" in out.stderr
    assert world.text("askurious", "app", "deployed_at.txt") == second
    out = world.run("rollback", FAKE_DB_VERSION="0001")                          # the old code knows this one
    assert out.returncode == 0, out.stdout + out.stderr
    assert world.text("askurious", "app", "deployed_at.txt") == first


@needs_bash
def test_second_participant_waits_for_the_first_to_be_healthy(world):
    _with_migrations(world, {"0001_a.py": _migration("0001", None)})
    out = world.run(FAKE_DB_VERSION="0001", FAKE_PARTICIPANTS="1")
    assert out.returncode == 0, out.stdout + out.stderr
    log = _log(world)
    a = log.index("compose up -d --no-deps participant-a")
    a_healthy = log.index("compose exec -T participant-a python", a)
    a_back = log.index("06-participant-upstream.sh participant-a up --handoff", a_healthy)
    b = log.index("compose up -d --no-deps participant-b")
    assert a < a_healthy < a_back < b
    assert "compose stop participant-a participant-b" not in log


@needs_bash
@pytest.mark.parametrize("unhealthy", ["participant-a", "participant-b"])
def test_failed_participant_leaves_the_other_running(world, unhealthy):
    _with_migrations(world, {"0001_a.py": _migration("0001", None)})
    _write(str(world.bin / "sleep"), "#!/usr/bin/env bash\nexit 0\n", 0o755)
    _write(str(world.bin / "seq"), "#!/usr/bin/env bash\necho 1\n", 0o755)
    out = world.run(FAKE_DB_VERSION="0001", FAKE_PARTICIPANTS="1", FAKE_UNHEALTHY=unhealthy)
    assert out.returncode != 0
    log = _log(world)
    assert ("compose up -d --no-deps participant-a" in log) == (unhealthy == "participant-a")
    assert "compose up -d --no-deps participant-b" not in log and "compose stop" not in log


# --- BT6: alarm.sh ------------------------------------------------------------------------------------------------

FAKE_CURL = '#!/usr/bin/env bash\n{ echo "ARGS $*"; echo "STDIN"; cat; } >> "$SERVER/curl.log"\n'


@needs_bash
def test_bt6_alarm_goes_to_slack_without_showing_the_address(tmp_path):
    w = World(tmp_path, repo=False)
    _write(str(w.bin / "curl"), FAKE_CURL, 0o755)
    folder = w.tmp / "deploy"
    for name in ("alarm.sh", "compose-env.sh", "backup.sh", "snapshot_uploads.py"):
        _write(str(folder / name), _read("deploy", "docker", name), 0o755)
    url = "https://hooks.slack.com/services/T0/B0/geheim"
    _write(str(folder / ".env"), f"SECRET_KEY=x\nSLACK_WEBHOOK_URL={url}\n")
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1", FAKE_DUMP_FAIL="1")
    assert out.returncode == 1 and "dump failed" in out.stderr and "ALARM: backup.sh" in out.stderr
    assert url not in out.stdout + out.stderr
    log = w.text("curl.log")
    args = [line for line in log.splitlines() if line.startswith("ARGS")]
    assert len(args) == 1 and url not in args[0] and "backup.sh  failed (exit 1)" in args[0]
    assert f'url = "{url}"' in log                                               # through stdin (-K -)
    # without an address: only stderr, never an error
    _write(str(folder / ".env"), "SECRET_KEY=x\n")
    out = w.run("a test", script=str(folder / "alarm.sh"), AS_SERVER="1")
    assert out.returncode == 0 and "ALARM: a test" in out.stderr
    assert len([line for line in w.text("curl.log").splitlines() if line.startswith("ARGS")]) == 1


@needs_bash
def test_bt6_good_daily_dump_leaves_a_mark_for_the_status_page(tmp_path):
    w = World(tmp_path, repo=False)
    folder = w.tmp / "deploy"
    _write(str(folder / "backup.sh"), _read("deploy", "docker", "backup.sh"), 0o755)
    _write(str(folder / "compose-env.sh"), _read("deploy", "docker", "compose-env.sh"))
    _write(str(folder / "snapshot_uploads.py"), _read("deploy", "docker", "snapshot_uploads.py"))
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    assert w.path("askurious-status", "backup-ok").exists()
    assert w.run("weekly", script=str(folder / "backup.sh"), AS_SERVER="1").returncode == 2


def test_bt6_status_page_checks_the_age_of_the_last_dump(tmp_path):
    mark = tmp_path / "backup-ok"
    mark.write_text("now")
    assert parse_checks("backup=age:/stamps/backup-ok:9") == [("backup", "age", "/stamps/backup-ok:9")]
    ok, _, detail = check("age", f"{mark}:26")
    assert ok and detail == "0"
    old = time.time() - 30 * 3600
    os.utime(mark, (old, old))
    ok, _, detail = check("age", f"{mark}:26")
    assert not ok and detail == "30"
    assert check("age", f"{tmp_path / 'none'}:26")[0] is False
    services = [{"key": "backup", "last": {"ok": False}}, {"key": "db", "last": {"ok": True}}]
    assert disrupted(services, [], {"backup"}) == []                             # no disruption for participants
    assert disrupted(services, []) == services[:1]
    compose = _read("deploy", "docker", "compose.yaml")
    assert "backup=age:/stamps/backup-ok:9" in compose and "STATUS_INFO: backup" in compose
    assert "- ${HOME}/askurious-status:/stamps:ro" in compose
    assert 'mkdir -p "$HOME/askurious-status"' in _read("deploy", "docker", "restart.sh")


def test_bt6_maintenance_and_restart_raise_the_alarm():
    assert "./alarm.sh" in _read("deploy", "docker", "maintenance.sh")
    restart = _read("deploy", "docker", "restart.sh")
    assert restart.count("./alarm.sh") >= 3 and "$C start $PARTICIPANTS" not in restart
    assert os.path.exists(os.path.join(DOCKER, "alarm.sh"))


# --- K1: an unreadable second factor locks ---------------------------------------------------------------------

ADMIN = "0000-0000-0000-001X"


def test_k1_unreadable_factor_locks_instead_of_opening(app, client):
    app.config["ADMIN_TOTP"] = True
    page = client.get("/account/second-factor/set-up").data.decode()
    import re

    secret = re.search(r"Schlüssel: <code>([A-Z2-7]+)</code>", page).group(1)
    code = twofactor.code_at(secret, int(time.time() // 30))
    client.post("/account/second-factor/set-up", data={"code": code, "next": "/admin/people"})
    app.config["SECRET_KEY"] = "a-new-secret-key-after-an-incident"           # without TOKEN_KEY
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        assert twofactor.enabled(user) and twofactor.unreadable(user)
        empty = twofactor.code_at("", int(time.time() // 30))
        assert not twofactor.check(user, empty)                                  # no code from an empty key
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={ADMIN}&name=Philipp")
    other.post("/terms/", data={"accept": "1"})
    with other.session_transaction() as sess:
        sess[twofactor.SIGNED_IN_VIA] = twofactor.VIA_LINK
    r = other.get("/admin/people")
    assert r.status_code == 302 and "/account/second-factor/code" in r.headers["Location"]   # not to the setup
    assert "nicht mehr lesbar" in other.get("/account/second-factor/code").data.decode()
    assert other.get("/account/second-factor/set-up").status_code == 302         # no new factor with the password
    assert other.get("/admin/people").status_code == 302


def test_k1_empty_secret_never_matches():
    assert twofactor.matching_step("", twofactor.code_at("", 5), now=5 * 30) is None


# --- BT16: TOKEN_KEY set later loses nothing -------------------------------------------------------------------

def test_bt16_token_key_set_later_still_opens_what_was_locked_before(app):
    app.config["TOKEN_KEY"] = ""
    with app.app_context():
        old = secrets_box.encrypt("zenodo-token")
        sealed = secrets_box.seal("external-return", b"payload")
    app.config["TOKEN_KEY"] = "a-lasting-token-key"
    with app.app_context():
        assert secrets_box.decrypt(old) == "zenodo-token"
        assert secrets_box.unseal("external-return", sealed) == b"payload"
        new = secrets_box.encrypt("zenodo-token")
        new_seal = secrets_box.seal("external-return", b"payload")
        assert new_seal != sealed and secrets_box.unseal("other", new_seal) is None
    app.config["SECRET_KEY"] = "a-new-secret-key-after-an-incident"
    with app.app_context():
        assert secrets_box.decrypt(new) == "zenodo-token"                        # survives the new SECRET_KEY
        assert secrets_box.unseal("external-return", new_seal) == b"payload"
        assert secrets_box.decrypt(old) == ""                                     # locked with the old one only
        assert secrets_box.unseal("external-return", "!!") is None
