"""Audit "Daten und Berechnungen" 2026-10-10, package I (operation and privacy page): L-6, L-8, L-10, L-12."""
import importlib.util
import os
import shutil
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Erasure, Response, SealEntry, SignInWait, Survey, utcnow
from tests.platform.test_audit8_b_deploy import _read, _write, needs_bash, server  # noqa: F401 (fixture)
from tests.privacy.test_erasure import take
from tests.exports.test_seal import sealed_survey
from tests.helpers.internal_docs import internal_text_or_skip

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tool():
    spec = importlib.util.spec_from_file_location("reapply_erasures",
                                                  os.path.join(HERE, "deploy", "docker", "reapply_erasures.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- L-8: after pg_restore the deletions since the dump are made again ---------------------------------------------

def _close(app):
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _entries(app):
    with app.app_context():
        uid = db.session.get(Survey, 1).uid
        return [(e.seq, e.kind, e.digest, e.response_id) for e in
                db.session.scalars(db.select(SealEntry).filter_by(survey_uid=uid).order_by(SealEntry.seq))]


def test_l8_deletions_since_the_dump_are_made_again_and_the_chain_comes_back(app, client, survey, tmp_path):
    url = app.config["SQLALCHEMY_DATABASE_URI"]
    if not url.startswith("sqlite"):
        pytest.skip("plays the restore with a copy of the SQLite file")
    uid = sealed_survey(app, client)
    for answer in ("eins", "zwei", "drei"):
        take(app.test_client(), uid, answer)
    live = tmp_path / "test.db"
    _close(app)
    shutil.copyfile(live, tmp_path / "dump.db")                           # the dump before the deploy
    from app import erasure

    with app.app_context():                                               # since the dump: one deleted on request ...
        s = db.session.get(Survey, 1)
        first = db.session.scalar(db.select(Response).filter_by(survey_id=1).order_by(Response.id))
        gone_id = first.id
        erasure.erase(s, [first], "request")
    take(app.test_client(), uid, "vier")                                  # ... and one new participation
    before = _entries(app)
    assert any(kind == "deleted" for _, kind, _, _ in before)
    tool, engine = _tool(), sa.create_engine(url)
    saved = tmp_path / "saved.json"
    tool.save(engine, str(saved))
    engine.dispose()
    _close(app)
    shutil.copyfile(tmp_path / "dump.db", live)                           # pg_restore
    with app.app_context():
        assert db.session.get(Response, gone_id) is not None              # the deleted one is back
    _close(app)

    engine = sa.create_engine(url)
    report = tool.apply(engine, str(saved))                               # dry run: nothing changes
    assert report["responses"] == {1: 1} and report["erasure"] == {1: 1}
    with app.app_context():
        assert db.session.get(Response, gone_id) is not None
    _close(app)
    engine.dispose()
    with app.app_context():                                               # --do: the app's way (audit 2026-10-10, S4)
        tool.apply(db.engine, str(saved), do=True)
    with app.app_context():
        assert db.session.get(Response, gone_id) is None                  # deleted again
        assert sorted(r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(survey_id=1))) \
            == ["drei", "zwei"]                                           # the new one is lost with the restore
        assert [(e.way, e.responses) for e in db.session.scalars(db.select(Erasure).filter_by(survey_id=1))] \
            == [("request", 1)]
    after = _entries(app)
    assert [(seq, kind, digest) for seq, kind, digest, _ in after] == [(seq, kind, digest) for seq, kind, digest, _ in before]
    assert gone_id not in {rid for _, _, _, rid in after}
    assert after[-1][3] is None                                           # the case of the lost participation: no id
    from app import seal

    with app.app_context():
        assert seal.check(db.session.get(Survey, 1))["chain_problems"] == []


def test_l8_betrieb_md_names_the_step_before_the_release():
    text = internal_text_or_skip("deploy", "docker", "BETRIEB.md")
    part = text[text.index("### Rückweg nach einer Migration"):]
    assert part.index("python - save") < part.index("pg_restore -U") < part.index("python - apply --do")
    assert "nicht wieder freigeben" in part


# --- L-10: the waiting list of the sign-in has a period -------------------------------------------------------------

def test_l10_waiting_list_entries_go_after_90_days(app):
    from app import signin

    with app.app_context():
        now = utcnow()
        db.session.add_all([
            SignInWait(orcid="0000-0001-0000-0001", name="Alt", asked_at=now - timedelta(days=91)),
            SignInWait(orcid="0000-0001-0000-0002", name="Neu", asked_at=now - timedelta(days=10)),
            SignInWait(orcid="0000-0001-0000-0003", name="Eingelassen", asked_at=now - timedelta(days=120),
                       admitted_at=now - timedelta(days=5)),
            SignInWait(orcid="0000-0001-0000-0004", name="Nie gekommen", asked_at=now - timedelta(days=200),
                       admitted_at=now - timedelta(days=95))])
        db.session.commit()
        assert signin.sweep_waiting(now) == 2
        assert sorted(w.name for w in db.session.scalars(db.select(SignInWait))) == ["Eingelassen", "Neu"]


def test_l10_housekeeping_runs_it_and_the_privacy_page_names_it(app, client):
    import inspect

    from app import lifecycle

    assert "signin.sweep_waiting" in inspect.getsource(lifecycle._rare_sweeps)
    assert "Warteliste" in client.get("/imprint").data.decode() and "90 Tagen" in client.get("/imprint").data.decode()


# --- L-12: the privacy page names the backups; dumps before deploys go by age too -----------------------------------

def test_l12_privacy_page_names_daily_and_deploy_backups(app, client):
    page = client.get("/imprint").data.decode()
    assert "Sicherungskopie ihrer Datenbank" in page and "täglich" not in page   # Plesk: the copy before updates
    app.config["BACKUP_DAYS"] = 14
    page = client.get("/imprint").data.decode()
    assert "alle vier Stunden; diese Sicherungen werden nach 14 Tagen gelöscht" in page and "höchstens 30 Tagen" in page
    assert "ENV BACKUP_DAYS=14" in _read("deploy", "docker", "Dockerfile")


@needs_bash
def test_l12_dumps_before_deploys_older_than_30_days_go(server):  # noqa: F811
    world = server
    backups = world.path("backups")
    old = backups / "askurious-before-deploy-20260801-1200.dump"
    recent = backups / "askurious-before-deploy-20261001-1200.dump"
    daily = backups / "askurious-20260801-0230.dump"
    for path in (old, recent, daily):
        _write(str(path), "PGDMP-old\n")
    import time

    os.utime(old, (time.time() - 40 * 86400,) * 2)
    os.utime(daily, (time.time() - 40 * 86400,) * 2)
    out = world.run("before-deploy", script=_script(world), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    names = os.listdir(backups)
    assert old.name not in names and recent.name in names and daily.name in names   # the daily ones keep their rule
    assert len([n for n in names if n.startswith("askurious-before-deploy-")]) == 2


def _script(world):
    script = world.tmp / "deploy" / "backup.sh"
    _write(str(script), _read("deploy", "docker", "backup.sh"), 0o755)
    _write(str(script.parent / "compose-env.sh"), _read("deploy", "docker", "compose-env.sh"))
    _write(str(script.parent / "snapshot_uploads.py"), _read("deploy", "docker", "snapshot_uploads.py"))
    return str(script)


# --- L-6: a count before the brand's own database is switched on ---------------------------------------------------

def test_l6_betrieb_md_counts_before_the_switch():
    text = internal_text_or_skip("deploy", "docker", "BETRIEB.md")
    part = text[text.index("## Eigene Datenbank für eine Marke"):text.index("## Nach dem Deploy der nginx")]
    assert part.index("Bestand zählen") < part.index("COMPOSE_PROFILES=brand")
    assert "created_host = 'polltergeist.de'" in part and "created_host IS NULL" in part and "0029" in part
