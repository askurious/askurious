"""Security audit 2026-10-10, package U7 "Wiederherstellung und Betrieb": reapply_erasures.py refuses a file that does
not belong to the restored database (S3) and deletes accounts, revoked tokens and participations again by the app's
own way (S4); checks for a brand's own database half switched on (B2); rotation of dumps of databases that are gone
(B3); openssl in the nginx image instead of at run time (B7); the demo links of the start page under a brand (B8)."""
import json
import os
import re
import shutil
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import (DataToken, DeletedAccount, Erasure, LoginSession, Response, SealEntry, Survey, User,
                        utcnow)
from tests.platform.test_audit10_betrieb import _close, _entries, _tool
from tests.platform.test_audit8_b_deploy import BASH, BASH_SLOW, World, _read, _write, needs_bash
from tests.privacy.test_erasure import take
from tests.exports.test_seal import sealed_survey
from tests.helpers import bounded_run
from tests.helpers.internal_docs import internal_text_or_skip

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCKER = os.path.join(HERE, "deploy", "docker")


def _sqlite_only(app):
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        pytest.skip("plays the restore with a copy of the SQLite file")


def _count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=1, source="real"))


# --- S3: a file that does not belong to the restored database ---------------------------------------------------------

def test_s3_an_old_file_is_refused_and_nothing_is_deleted(app, client, survey, tmp_path):
    """The repro of the audit (siegel/test_s3_reapply_alt.py): a file from the last time, "save" forgotten. Before,
    apply --do deleted the three participations since the file without a deletion record."""
    _sqlite_only(app)
    uid = sealed_survey(app, client)
    for answer in ("a", "b"):
        take(app.test_client(), uid, answer)
    tool, path = _tool(), str(tmp_path / "reapply-erasures.json")
    with app.app_context():
        tool.save(db.engine, path)
    for answer in ("c", "d", "e"):
        take(app.test_client(), uid, answer)
    before = _entries(app)
    with app.app_context():
        dry = tool.apply(db.engine, path)
        report = tool.apply(db.engine, path, do=True)
    assert dry["refused"] and report["refused"]
    assert any("älter als der eingespielte Stand" in r for r in report["refused"])
    assert any("Siegelkette" in r for r in report["refused"])
    assert _count(app) == 5 and _entries(app) == before
    assert os.path.exists(path)                                             # not renamed: nothing was applied
    _close(app)
    engine = sa.create_engine(app.config["SQLALCHEMY_DATABASE_URI"])
    assert tool.apply(engine, path, do=True, tables_only=True)["refused"]  # the way out refuses as well
    engine.dispose()
    assert _count(app) == 5


def test_s3_a_file_of_another_database_is_refused(app, client, survey, tmp_path):
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    tool, path = _tool(), str(tmp_path / "reapply-erasures.json")
    with app.app_context():
        tool.save(db.engine, path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    def write():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    data["database"] = "polltergeist"
    write()
    with app.app_context():
        report = tool.apply(db.engine, path, do=True)
    assert any("polltergeist" in r for r in report["refused"])
    data["database"], data["survey_uids"] = None, ["zzzzzzzzzzzz"]
    write()
    with app.app_context():
        report = tool.apply(db.engine, path, do=True)
    assert any("anderen Datenbank" in r for r in report["refused"]) and _count(app) == 1


def test_s3_code_that_does_not_fit_the_database_is_refused(app, client, survey, tmp_path, monkeypatch, capsys):
    tool, path = _tool(), str(tmp_path / "reapply-erasures.json")
    with app.app_context():
        tool.save(db.engine, path)
    _close(app)
    monkeypatch.setattr(tool, "code_fits", lambda engine: (False, "0040", "0043"))
    engine = sa.create_engine(app.config["SQLALCHEMY_DATABASE_URI"])
    report = tool.apply(engine, path, do=True)                              # outside the app: it would start it
    engine.dispose()
    assert report["refused"] and "rollback --no-start" in report["refused"][0]
    assert "ABGEBROCHEN, nichts geändert" in capsys.readouterr().out


# --- S4: what else comes back with a dump ------------------------------------------------------------------------------

def test_s4_accounts_tokens_and_participations_go_again_by_the_apps_way(app, client, survey, tmp_path, monkeypatch):
    _sqlite_only(app)
    uid = sealed_survey(app, client)
    for answer in ("eins", "zwei", "drei"):
        take(app.test_client(), uid, answer)
    with app.app_context():                                               # an account and a token before the dump
        other = User(orcid="0000-0002-1825-0097", name="Weg", number=99)
        db.session.add(other)
        db.session.flush()
        other_id = other.id
        token = DataToken(survey_id=1, user_id=1, name="R", token_hash="a" * 64,
                          expires_at=utcnow() + timedelta(days=30))
        db.session.add(token)
        db.session.add(LoginSession(id="s" * 40, user_id=other_id))
        s = db.session.get(Survey, 1)
        s.codings = {"x": {"cats": []}}                                   # a coding guide: coding.forget has work
        db.session.commit()
        token_id = token.id
    live = tmp_path / "test.db"
    _close(app)
    shutil.copyfile(live, tmp_path / "dump.db")
    from app import accounts, erasure

    with app.app_context():                                               # since the dump: deleted, revoked, ended
        first = db.session.scalar(db.select(Response).filter_by(survey_id=1).order_by(Response.id))
        gone_id = first.id
        erasure.erase(db.session.get(Survey, 1), [first], "self")
        db.session.delete(db.session.get(DataToken, token_id))
        db.session.commit()
        accounts.delete(db.session.get(User, other_id), "self")
    before = _entries(app)
    tool = _tool()
    saved = str(tmp_path / "saved.json")
    with app.app_context():
        tool.save(db.engine, saved)
    _close(app)
    shutil.copyfile(tmp_path / "dump.db", live)                           # pg_restore
    with app.app_context():
        assert db.session.get(User, other_id) is not None and db.session.get(DataToken, token_id) is not None

    forgotten = []
    from app import coding

    real_forget = coding.forget
    monkeypatch.setattr(coding, "forget", lambda s, rows: forgotten.append([r.id for r in rows]) or real_forget(s, rows))
    with app.app_context():
        dry = tool.apply(db.engine, saved)
    assert dry["accounts_named"] == [other_id] and dry["tokens"] == {"data_token": 1, "auth_token": 0, "login_session": 1}
    assert dry["codings"] == [1] and not forgotten
    with app.app_context():
        report = tool.apply(db.engine, saved, do=True)
    assert not report["refused"] and report["accounts"] == [other_id]
    assert forgotten == [[gone_id]]                                       # the app's way: coding.forget ran
    with app.app_context():
        assert db.session.get(Response, gone_id) is None
        assert db.session.get(User, other_id) is None                     # e-mail, password hash, ORCID iD gone again
        assert db.session.get(DataToken, token_id) is None                # the revoked token does not work again
        assert db.session.get(LoginSession, "s" * 40) is None
        rows = db.session.scalars(db.select(DeletedAccount).filter_by(user_id=other_id)).all()
        assert [r.by for r in rows] == ["self"]                           # as at the first deletion, once
        assert [(e.way, e.responses) for e in db.session.scalars(db.select(Erasure).filter_by(survey_id=1))] \
            == [("self", 1)]                                              # counted once, not again
    after = _entries(app)
    assert [(seq, kind, digest) for seq, kind, digest, _ in after] == [(seq, kind, digest) for seq, kind, digest, _ in before]
    assert not os.path.exists(saved) and os.path.exists(report["file"])   # never applied twice
    from app import seal

    with app.app_context():
        assert seal.check(db.session.get(Survey, 1))["chain_problems"] == []


def test_s4_a_case_the_chain_still_names_gets_a_deletion_record(app, client, survey, tmp_path):
    """A participation gone without a record in the saved chain (e.g. deleted by a faulty migration): the app's way
    writes the record ("erasure_restore"), so the hourly check does not report it missing."""
    _sqlite_only(app)
    uid = sealed_survey(app, client)
    for answer in ("eins", "zwei"):
        take(app.test_client(), uid, answer)
    with app.app_context():
        chain_uid = db.session.get(Survey, 1).uid
    live = tmp_path / "test.db"
    _close(app)
    shutil.copyfile(live, tmp_path / "dump.db")
    with app.app_context():                                               # gone past the app: no record
        gone_id = db.session.scalar(db.select(db.func.min(Response.id)))
        db.session.execute(sa.text("DELETE FROM response WHERE id = :i"), {"i": gone_id})
        db.session.commit()
    tool, saved = _tool(), str(tmp_path / "saved.json")
    with app.app_context():
        tool.save(db.engine, saved)
    _close(app)
    shutil.copyfile(tmp_path / "dump.db", live)
    with app.app_context():
        assert not tool.apply(db.engine, saved, do=True)["refused"]
        entries = db.session.scalars(db.select(SealEntry).filter_by(survey_uid=chain_uid).order_by(SealEntry.seq)).all()
        assert entries[-1].kind == "deleted" and entries[-1].reason == "erasure_restore"
        from app import seal

        assert seal.check(db.session.get(Survey, 1))["chain_problems"] == []


def test_s4_betrieb_md_names_the_new_steps():
    text = internal_text_or_skip("deploy", "docker", "BETRIEB.md")
    part = text[text.index("### Rückweg nach einer Migration"):]
    part = part[:part.index("\n## ")]
    assert "rollback --no-start" in part and "--tables-only" in part and "ABGEBROCHEN" in part
    assert part.index("python - save") < part.index("pg_restore -U") < part.index("rollback --no-start") \
        < part.index("python - apply --do")
    assert "Konten" in part and "Tokens" in part


@needs_bash
def test_s4_rollback_without_start(tmp_path):
    from tests.platform.test_audit8_b_deploy import _server_from_before

    w = World(tmp_path)
    _server_from_before(w)
    assert w.run().returncode == 0                                          # a deploy, then a second one
    first = w.text("askurious", "app", "deployed_at.txt")
    w.commit("second", push=True)
    assert w.run().returncode == 0
    log_before = w.text("docker.log")
    out = w.run("rollback", "--no-start")
    assert out.returncode == 0, out.stdout + out.stderr
    new = w.text("docker.log")[len(log_before):]
    assert "tag askurious:previous askurious:latest" in new and "up -d" not in new and "restart" not in new
    assert "restart.sh --no-build" in out.stdout and "nothing started" in out.stdout
    assert w.text("askurious", "app", "deployed_at.txt") == first           # the code from before is in place
    assert w.run("rollback", "--now").returncode == 2


# --- B2: a brand's own database half switched on ------------------------------------------------------------------------

def _brand_stack(tmp_path, **env):
    folder = tmp_path / "brand"
    full = dict(os.environ, BRAND_NGINX_DIR=str(folder).replace("\\", "/"), RESEARCHER_HOST="askurious.de",
                PARTICIPANT_HOST="ask.askurious.de", STATUS_HOST="status.askurious.de",
                BRAND_RESEARCHER_HOSTS="polltergeist.de", **env)
    out = bounded_run.run([BASH, os.path.join(DOCKER, "nginx", "brand-stack.sh").replace("\\", "/")], env=full,
                          text=True, timeout=30 * BASH_SLOW)
    return out, (folder / "researcher-map.conf").read_text() if (folder / "researcher-map.conf").exists() else None


@needs_bash
@pytest.mark.parametrize("domain", ["askurious.de", "ask.askurious.de", "de", "www.askurious.de", "askurious.com"])
def test_b2_nginx_never_sends_the_main_names_to_the_brand(tmp_path, domain):
    out, mapped = _brand_stack(tmp_path, BRAND_STACK_DOMAIN=domain)
    assert mapped == "" and "BRAND_STACK_DOMAIN" in out.stderr and "left out" in out.stderr


@needs_bash
def test_b2_nginx_takes_a_listed_brand(tmp_path):
    out, mapped = _brand_stack(tmp_path, BRAND_STACK_DOMAIN="polltergeist.de")
    assert out.returncode == 0 and "polltergeist" in mapped


def _restart_world(tmp_path, env_lines, brand_env=None, services=""):
    """restart.sh in a folder of its own with a .env (and .env.brand); `services`: what compose config --services
    names (the profile "brand" on: the brand's services)."""
    w = World(tmp_path, repo=False)
    folder = w.tmp / "code" / "deploy" / "docker"
    for name in ("restart.sh", "compose-env.sh", "alarm.sh", "backup.sh", "snapshot_uploads.py"):
        _write(str(folder / name), _read("deploy", "docker", name), 0o755)
    for name in ("askurious.conf.template", "servers.sh", "self-signed.sh", "participant-upstream.sh", "brand-stack.sh"):
        _write(str(folder / "nginx" / name), _read("deploy", "docker", "nginx", name))
    _write(str(folder / ".env"), "\n".join(env_lines) + "\n")
    if brand_env is not None:
        _write(str(folder / ".env.brand"), brand_env)
    docker = w.bin / "docker"
    text = docker.read_text().replace('case "$*" in', 'case "$*" in\n  "compose config --services") printf "%b" "${FAKE_SERVICES:-}" ;;', 1)
    _write(str(docker), text, 0o755)
    return w, lambda **env: w.run("--no-build", script=str(folder / "restart.sh"), AS_SERVER="1",
                                  FAKE_SERVICES=services, **env)


@needs_bash
@pytest.mark.parametrize("lines,brand_env,services,says", [
    (["BRAND_STACK_DOMAIN=polltergeist.de", "BRAND_RESEARCHER_HOSTS=polltergeist.de", "BRAND_COMPUTE_SECRET=x"],
     "SECRET_KEY=k\nDATABASE_URL=postgresql://a@db/polltergeist\n", "", "profile \"brand\" is off"),
    (["COMPOSE_PROFILES=brand", "BRAND_STACK_DOMAIN=polltergeist.de", "BRAND_RESEARCHER_HOSTS=polltergeist.de",
      "BRAND_COMPUTE_SECRET=x"], None, "brand-researcher\\n", ".env.brand"),
    (["COMPOSE_PROFILES=brand", "BRAND_STACK_DOMAIN=polltergeist.de", "BRAND_RESEARCHER_HOSTS=polltergeist.de",
      "BRAND_COMPUTE_SECRET=x"], "SECRET_KEY=\n", "brand-researcher\\n", ".env.brand"),
    (["COMPOSE_PROFILES=brand", "BRAND_STACK_DOMAIN=polltergeist.de", "BRAND_RESEARCHER_HOSTS=polltergeist.de"],
     "SECRET_KEY=k\nDATABASE_URL=postgresql://a@db/polltergeist\n", "brand-researcher\\n", "BRAND_COMPUTE_SECRET"),
    (["COMPOSE_PROFILES=brand", "BRAND_RESEARCHER_HOSTS=polltergeist.de", "BRAND_COMPUTE_SECRET=x"],
     "SECRET_KEY=k\nDATABASE_URL=postgresql://a@db/polltergeist\n", "brand-researcher\\n", "BRAND_STACK_DOMAIN is empty"),
    (["COMPOSE_PROFILES=brand", "BRAND_STACK_DOMAIN=askurious.de", "BRAND_RESEARCHER_HOSTS=polltergeist.de",
      "BRAND_COMPUTE_SECRET=x"], "SECRET_KEY=k\nDATABASE_URL=postgresql://a@db/polltergeist\n", "brand-researcher\\n",
     "main domain"),
    (["COMPOSE_PROFILES=brand", "BRAND_STACK_DOMAIN=poltergeist.de", "BRAND_RESEARCHER_HOSTS=polltergeist.de",
      "BRAND_COMPUTE_SECRET=x"], "SECRET_KEY=k\nDATABASE_URL=postgresql://a@db/polltergeist\n", "brand-researcher\\n",
     "not in BRAND_RESEARCHER_HOSTS"),
])
def test_b2_restart_refuses_a_brand_half_switched_on(tmp_path, lines, brand_env, services, says):
    w, run = _restart_world(tmp_path, lines, brand_env, services)
    out = run()
    assert out.returncode != 0 and says in out.stderr, out.stdout + out.stderr
    assert "up -d" not in (w.text("docker.log") or "")                    # nothing started


@needs_bash
def test_b2_restart_goes_on_with_a_whole_brand_or_none(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    w, run = _restart_world(tmp_path / "a", ["RESEARCHER_HOST=askurious.de"])
    out = run()
    assert "brand" not in out.stderr, out.stderr
    w, run = _restart_world(tmp_path / "b", ["COMPOSE_PROFILES=brand", "BRAND_STACK_DOMAIN=polltergeist.de",
                                             "BRAND_RESEARCHER_HOSTS=polltergeist.de", "BRAND_COMPUTE_SECRET=x"],
                            "SECRET_KEY=k\nDATABASE_URL=postgresql://a@db/polltergeist\n", "brand-researcher\\n")
    out = run()
    assert "half" not in out.stderr and "refused" not in out.stderr, out.stderr
    assert "up -d" in (w.text("docker.log") or "")


# --- B3: dumps of a database that is gone ------------------------------------------------------------------------------

@needs_bash
def test_b3_dumps_of_a_removed_database_go_by_age(tmp_path):
    w = World(tmp_path, repo=False)
    folder = w.tmp / "deploy"
    for name in ("backup.sh", "compose-env.sh", "alarm.sh", "snapshot_uploads.py"):
        _write(str(folder / name), _read("deploy", "docker", name), 0o755)
    backups = w.path("backups")
    old_daily = backups / "polltergeist-20260801-0230.dump"
    young_daily = backups / "polltergeist-20261009-0230.dump"
    old_deploy = backups / "polltergeist-before-deploy-20260801-1200.dump"
    mid_deploy = backups / "polltergeist-before-deploy-20260920-1200.dump"
    for path in (old_daily, young_daily, old_deploy, mid_deploy):
        _write(str(path), "PGDMP-old\n")
    now = __import__("time").time()
    os.utime(old_daily, (now - 20 * 86400, now - 20 * 86400))
    os.utime(young_daily, (now - 2 * 86400, now - 2 * 86400))
    os.utime(old_deploy, (now - 40 * 86400, now - 40 * 86400))
    os.utime(mid_deploy, (now - 20 * 86400, now - 20 * 86400))
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1", FAKE_DATABASES="askurious")
    assert out.returncode == 0, out.stdout + out.stderr
    assert not old_daily.exists() and young_daily.exists()               # 14 days for the daily ones
    assert not old_deploy.exists() and mid_deploy.exists()               # 30 days for those before a deploy
    assert any(n.startswith("askurious-") for n in os.listdir(backups))


def test_b3_privacy_page_still_names_the_periods(app, client):
    app.config["BACKUP_DAYS"] = 14
    page = client.get("/imprint").data.decode()
    assert "diese Sicherungen werden nach 14 Tagen gelöscht" in page and "höchstens 30 Tagen" in page


# --- B7: openssl in the image ---------------------------------------------------------------------------------------

def test_b7_openssl_comes_with_the_image_not_at_run_time():
    script = _read("deploy", "docker", "nginx", "self-signed.sh")
    assert "apk add" not in script and "command -v openssl" in script
    dockerfile = _read("deploy", "docker", "nginx-image", "Dockerfile")
    assert re.search(r"^FROM nginx:1\.\d+\.\d+-alpine@sha256:[0-9a-f]{64}$", dockerfile, re.M)  # pinned, CFG-05
    assert re.search(r"^RUN apk add --no-cache openssl$", dockerfile, re.M)
    compose = _read("deploy", "docker", "compose.yaml")
    block = compose[compose.index("  nginx:\n"):]
    block = block[:block.index("\n\n")]
    assert "dockerfile: deploy/docker/nginx-image/Dockerfile" in block and "image: askurious-nginx:" in block and "pull_policy: never" in block
    maintenance = _read("deploy", "docker", "maintenance.sh")
    assert "nginx-image/Dockerfile" in maintenance and "build --pull" in maintenance


# --- B8: the demo links of the start page --------------------------------------------------------------------------------

def test_b8_demo_links_stay_with_the_brand(app):
    c = app.test_client()
    page = c.get("/").data.decode()
    assert "https://ask.askurious.de/s/demo-webservices" in page and "https://ask.askurious.de/s/nubv72" in page and "https://ask.askurious.de/s/demo-rueckmeldung" in page
    app.config.update(PUBLIC_BASE_URL="https://polltergeist.de", PARTICIPANT_BASE_URL="https://ask.polltergeist.de",
                      BRANDS="", SITE_BRAND="polltergeist.de=POLLtergeist", SECRET_KEY="y" * 32)
    page = c.get("/", headers={"Host": "polltergeist.de"}).data.decode()
    assert "https://ask.polltergeist.de/s/demo-webservices" in page and "https://ask.polltergeist.de/s/nubv72" in page and "https://ask.polltergeist.de/s/demo-rueckmeldung" in page
    assert "ask.askurious.de" not in page
