"""Prepared, not switched on: a further brand with its own database (datenbank-je-marke-konzept.md, Ralf 2026-10-09).
Every setting is empty by default and leaves things as they were; these tests check both sides."""
import os
import re

from app import brand, containers
from app.extensions import db
from app.models import Survey, User
from app.status_node import shown
from tests.platform.test_audit8_b_deploy import BASH, BASH_SLOW, World, _read, _write, needs_bash
from tests.platform.test_containers import NOW, _write as _stats
from tests.helpers import bounded_run


def test_site_brand_holds_also_outside_a_request(app):
    with app.app_context():
        assert brand.current() is None and brand.name() == "Askurious"          # empty: as before
        app.config.update(SITE_BRAND="polltergeist.de=POLLtergeist", PUBLIC_BASE_URL="https://polltergeist.de",
                          PARTICIPANT_BASE_URL="https://ask.polltergeist.de")
        assert brand.name() == "POLLtergeist" and brand.domain() == "polltergeist.de"
        assert brand.apply("Mit Askurious erstellt") == "Mit POLLtergeist erstellt"
        with brand.use(""):                                                    # "the main name" is this one
            assert brand.name() == "POLLtergeist"
        assert brand.public_base() == "https://polltergeist.de"
        assert brand.participant_base_url() == "https://ask.polltergeist.de"
    with app.test_request_context("/", headers={"Host": "polltergeist.de"}):
        assert brand.name() == "POLLtergeist" and brand.site() == "polltergeist"


def test_new_surveys_and_accounts_remember_their_domain(app, client):
    # through the page: the test's own address
    client.post("/surveys/new", data={"title": "page"})
    app.config.update(PUBLIC_BASE_URL="https://askurious.de", BRANDS="polltergeist.de=Polltergeist")
    for host, orcid in (("askurious.de", "0000-0002-0000-0001"), ("evil.example", "0000-0002-0000-0002"),
                        ("ask.polltergeist.de", "0000-0002-0000-0003")):
        with app.test_request_context("/", headers={"Host": host}):
            db.session.add(User(orcid=orcid))
            db.session.add(Survey(title=host))
            db.session.commit()
    with app.app_context():
        hosts = {u.orcid: u.created_host for u in User.query}
        assert hosts["0000-0002-0000-0001"] == "askurious.de"
        assert hosts["0000-0002-0000-0002"] == "askurious.de"                  # a forged Host header decides nothing
        assert hosts["0000-0002-0000-0003"] == "polltergeist.de"
        surveys = {s.title: s.created_host for s in Survey.query}
        assert surveys["page"] == "localhost" and surveys["ask.polltergeist.de"] == "polltergeist.de"
        # outside a request (a script): unknown, never guessed
        s = Survey(title="script")
        db.session.add(s)
        db.session.commit()
        assert s.created_host is None
        # kept when given (a copy between databases)
        s = Survey(title="moved", created_host="polltergeist.de")
        db.session.add(s)
        db.session.commit()
        assert s.created_host == "polltergeist.de"


def test_migration_0029_only_adds():
    text = _read("migrations", "versions", "0029_created_host.py")
    assert "ROLLING = True" in text and "nullable=True" in text and "down_revision = '0028'" in text


def test_pool_size_only_when_set(monkeypatch):
    from config import engine_options

    url = "postgresql+psycopg://u:p@db/x"
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
    assert "pool_size" not in engine_options(url) and "max_overflow" not in engine_options(url)
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "7")
    assert engine_options(url)["pool_size"] == 3 and engine_options(url)["max_overflow"] == 7
    assert engine_options("sqlite:///x.db") == {}


def test_container_page_shows_only_the_named_services(tmp_path):
    path = _stats(tmp_path)
    assert containers.shown("") is None
    every = [r["service"] for r in containers.overview(path, now=NOW)["rows"]]
    assert every == ["participant-a", "db", "nginx"]
    only = containers.overview(path, now=NOW, only=containers.shown("brand-participant, db,nginx"))
    assert [r["service"] for r in only["rows"]] == ["db", "nginx"]


def test_status_page_splits_checks_only_with_a_stack():
    keys = ["participant-a", "researcher", "db", "backup", "brand-participant", "brand-backup"]
    assert shown(keys, None, set(), {"db"}) == keys                          # empty: everything everywhere
    assert shown(keys, "polltergeist.de", set(), {"db"}) == keys
    stack = {"polltergeist.de"}
    assert shown(keys, "polltergeist.de", stack, {"db"}) == ["db", "brand-participant", "brand-backup"]
    assert shown(keys, None, stack, {"db"}) == ["participant-a", "researcher", "db", "backup"]


def test_compose_profile_and_nginx_default():
    compose = _read("deploy", "docker", "compose.yaml")
    for svc in ("brand-participant", "brand-researcher", "brand-compute"):
        head = re.search(rf"^  {svc}:(?: &[^\s]+)?$", compose, re.M)
        block = compose[head.start():]
        block = block[:block.index("\n\n")]
        assert 'profiles: ["brand"]' in block, svc
        assert "env_file: .env\n" not in block and "instance:/app/instance" not in block, svc   # never Askurious' own
    assert "instance-brand:/app/instance" in compose and "07-brand-stack.sh:ro" in compose
    conf = _read("deploy", "docker", "nginx", "askurious.conf.template")
    assert "map $host $researcher_pool  { default researcher;" in conf
    assert "map $host $participant_pool { default participant;" in conf
    assert "proxy_pass http://researcher;" not in conf and "proxy_pass http://participant;" not in conf
    assert "nginx/brand-stack.sh" in _read("deploy", "docker", "restart.sh")


def _nginx_files(tmp_path, domain):
    folder = tmp_path / "brand"
    env = dict(os.environ, BRAND_NGINX_DIR=str(folder).replace("\\", "/"), BRAND_STACK_DOMAIN=domain,
               BRAND_RESEARCHER_HOSTS="polltergeist.de", RESEARCHER_HOST="askurious.de")  # B2: only a listed brand
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "deploy", "docker", "nginx", "brand-stack.sh")
    out = bounded_run.run([BASH, script.replace("\\", "/")], env=env, text=True, timeout=30 * BASH_SLOW)
    assert out.returncode == 0, out.stderr
    return {name: (folder / name).read_text() for name in ("upstreams.conf", "researcher-map.conf", "participant-map.conf")}


@needs_bash
def test_nginx_files_empty_without_a_stack_domain(tmp_path):
    assert set(_nginx_files(tmp_path, "").values()) == {""}
    assert set(_nginx_files(tmp_path, "evil.de; include /etc/x").values()) == {""}


@needs_bash
def test_nginx_files_with_a_stack_domain(tmp_path):
    files = _nginx_files(tmp_path, "Polltergeist.de")
    assert "server brand-researcher:8000 resolve;" in files["upstreams.conf"]
    assert "server brand-participant:8000 resolve max_fails=0;" in files["upstreams.conf"]
    assert files["researcher-map.conf"].strip() == r"~(^|\.)polltergeist\.de$ brand_researcher;"
    assert files["participant-map.conf"].strip() == r"~(^|\.)polltergeist\.de$ brand_participant;"


@needs_bash
def test_backup_dumps_each_database_with_its_own_mark(tmp_path):
    w = World(tmp_path, repo=False)
    folder = w.tmp / "deploy"
    _write(str(folder / "backup.sh"), _read("deploy", "docker", "backup.sh"), 0o755)
    _write(str(folder / "compose-env.sh"), _read("deploy", "docker", "compose-env.sh"))
    _write(str(folder / "snapshot_uploads.py"), _read("deploy", "docker", "snapshot_uploads.py"))
    # no answer from the server: askurious only, as before
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1")
    assert out.returncode == 0, out.stdout + out.stderr
    assert [f[:10] for f in os.listdir(w.path("backups")) if f.endswith(".dump")] == ["askurious-"]
    out = w.run(script=str(folder / "backup.sh"), AS_SERVER="1", FAKE_DATABASES="askurious\npolltergeist\nbad name")
    assert out.returncode == 0, out.stdout + out.stderr
    assert {n.split("-")[0] for n in os.listdir(w.path("backups")) if n.endswith(".dump")} == {"askurious",
                                                                                                "polltergeist"}
    assert w.path("askurious-status", "backup-ok").exists()
    assert w.path("askurious-status", "backup-ok-polltergeist").exists()
