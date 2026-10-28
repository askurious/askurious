"""The participants' own database role (audit 2026-10-18, CFG-04; prepared, off until PARTICIPANT_DB_USER is in the
.env): every table is classified in app/participant_grants.py, the migration job grants exactly that list, compose
and the isolation check keep the rolling deploy as it was while the role is off, and a participant request that
needs a right outside the list is caught (tests/helpers/participant_sql.py; tests/conftest.py checks the participants'
test folders with it)."""
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from app import participant_grants
from tests.helpers import participant_sql
from tests.platform.test_audit8_b_deploy import DOCKER, World, _write, needs_bash



def _tables_of_the_database(app):
    from sqlalchemy import inspect

    from app.extensions import db

    with app.app_context():
        names = set(inspect(db.engine).get_table_names())
    return {n for n in names if not n.startswith("construct_fts")}  # SQLite's search index, not on PostgreSQL


def test_every_table_is_granted_or_closed_on_purpose(app):
    """A new model or migration table has to be entered in GRANTS (with its rights) or NOT_GRANTED, with a reason."""
    from app import models  # noqa: F401
    from app.extensions import db

    granted, closed = set(participant_grants.GRANTS), set(participant_grants.NOT_GRANTED)
    assert not granted & closed
    every = set(db.metadata.tables) | _tables_of_the_database(app) | {"alembic_version"}
    assert sorted(every - granted - closed) == [], "classify these tables in app/participant_grants.py"
    assert sorted((granted | closed) - every) == [], "tables that no longer exist"
    for table, (privileges, why) in participant_grants.GRANTS.items():
        assert privileges and len(why) > 20, table
        for privilege in privileges:
            m = re.fullmatch(r"(SELECT|INSERT|UPDATE|DELETE)(?: \(([a-z_, ]+)\))?", privilege)
            assert m, (table, privilege)
            if m.group(2):  # column rights name columns of the table
                assert {c.strip() for c in m.group(2).split(",")} <= set(db.metadata.tables[table].columns.keys())
        assert "SELECT" in participant_grants.allowed(table), table  # RETURNING and WHERE need it with every write
    for table, why in participant_grants.NOT_GRANTED.items():
        assert len(why) > 20, table


def test_researchers_secrets_stay_closed():
    # never the password hash, the second factor or the sign-in state; the status, the period and the sign-in version
    # tell whether a pretest link still holds (app/editor/share.py)
    assert participant_grants.columns("user_login") == {"user_id", "email", "kinds", "status", "valid_until",
                                                        "session_version"}
    for table in ("login_session", "auth_token", "agent_token", "data_token", "ai_access",
                  "access_log", "sign_in_orcid", "zenodo_connection", "coscine_connection", "account_job"):
        assert table in participant_grants.NOT_GRANTED, table
    assert participant_grants.GRANTS["alembic_version"][0] == ("SELECT",)
    assert participant_grants.GRANTS["mail_outbox"][0] == ("SELECT (id)", "INSERT")  # never the queued mails


def test_recorder_turns_statements_into_rights():
    pairs = participant_sql.pairs
    assert pairs('INSERT INTO response (survey_id) VALUES (?) RETURNING id') == {
        ("response", "INSERT"), ("response", "SELECT"), ("response_revision", "SELECT"),
        ("response_revision", "INSERT"), ("response_revision", "UPDATE")}
    assert pairs('UPDATE survey SET x=? WHERE survey.id = ?') == {("survey", "UPDATE"), ("survey", "SELECT")}
    assert pairs('SELECT "user".id FROM "user" JOIN survey_member ON 1') == {("user", "SELECT"), ("survey_member", "SELECT")}
    assert ("rate_count", "UPDATE") in pairs("INSERT INTO rate_count (k) VALUES (?) ON CONFLICT (k) DO UPDATE SET n=n+1")
    assert pairs("SELECT 1") == set() and pairs("PRAGMA foreign_keys") == set()


def test_recorder_sees_a_lock_as_update(app):
    from app.extensions import db
    from app.models import Survey

    with app.app_context():
        compiled = db.select(Survey.id).with_for_update().compile(db.engine)
    assert ("survey", "UPDATE") in participant_sql.pairs(str(compiled), compiled)


def test_a_column_outside_a_column_right_is_reported():
    seen = {pair: {"participate.privacy"} for pair in participant_sql.pairs(
        "SELECT user_login.email, user_login.password_hash FROM user_login WHERE user_login.user_id = ?")}
    assert set(participant_sql.violations(seen)) == {("user_login", "SELECT:password_hash")}


def test_an_update_outside_a_column_right_is_reported():
    """seal_entry may only lose salt and participation id (seal._forget); its hashes stay unchangeable."""
    assert participant_grants.columns("seal_entry", "UPDATE") == {"salt", "response_id"}
    forget = participant_sql.pairs("UPDATE seal_entry SET salt=?, response_id=? WHERE seal_entry.response_id = ?")
    assert participant_sql.violations({pair: {"participate.erase"} for pair in forget}) == {}
    rewrite = participant_sql.pairs('UPDATE seal_entry SET "content"=?, salt=? WHERE seal_entry.id = ?')
    assert set(participant_sql.violations({pair: {"participate.erase"} for pair in rewrite})) == {
        ("seal_entry", "UPDATE:content")}


def test_a_right_outside_the_list_is_reported():
    assert participant_sql.violations({("login_session", "SELECT"): {"participate.page"}}) == {
        ("login_session", "SELECT"): ["participate.page"]}
    assert participant_sql.violations({("survey", "DELETE"): {"participate.page"}}) == {
        ("survey", "DELETE"): ["participate.page"]}
    assert participant_sql.violations({("response", "INSERT"): {"participate.survey"}}) == {}


def test_participation_stays_within_the_list(app, client, survey):
    """A whole participation with a split section (consent, answers, separated contact), recorded like on the
    participants' side, needs nothing outside the list."""
    from tests.quotas.test_split import build, participate

    uid = build(app, client)
    participant_sql.start()
    try:
        participate(app, uid, "1", "a@example.org", "Hallo")
    finally:
        seen = participant_sql.stop()
    assert {("response", "INSERT"), ("response", "UPDATE"), ("contact_entry", "INSERT")} <= set(seen)
    assert participant_sql.violations(seen) == {}


def test_participant_start_leaves_the_data_upkeep_to_the_researchers(app, tmp_path, monkeypatch):
    """The participants' role reads no accounts or organisations: create_app with SIDE=participant runs none of the
    start-up data steps (the researchers' container does, restart.sh starts it first)."""
    from app import account_types, create_app, orgs, quotas
    from app.extensions import db
    from tests.conftest import _config

    with app.app_context():
        address = str(db.engine.url)
    for module, name in ((quotas, "upgrade_drafts"), (account_types, "ensure_seed"),
                         (orgs, "ensure_seed")):
        monkeypatch.setattr(module, name, lambda *a, **k: pytest.fail("data upkeep on the participants' side"))
    config = _config(tmp_path, address)
    config.SIDE = "participant"
    config.PARTICIPANT_BASE_URL = "https://ask.example.org"
    side = create_app(config)
    with side.app_context():
        db.engine.dispose()


def test_grant_identifier_and_separation_are_checked_before_sql():
    from app.schema_migrate import grant_participant, run

    for role in ("", "x; DROP TABLE survey", 'p"', "Participant"):
        with pytest.raises(ValueError, match="identifier"):
            grant_participant(role)
    with pytest.raises(ValueError, match="differ"):
        grant_participant("askurious_run", "askurious_run")
    with pytest.raises(ValueError, match="PostgreSQL"):
        run("sqlite://", participant_role="askurious_participant")


def test_grants_are_given_in_one_transaction_without_default_privileges():
    text = Path(__file__).resolve().parents[2].joinpath("app", "schema_migrate.py").read_text("utf-8")
    body = text[text.index("def grant_participant"):text.index("def run(")]
    assert "DEFAULT PRIVILEGES" not in body and "ALL TABLES IN SCHEMA public TO" not in body
    assert body.count("db.engine.begin()") == 1
    assert 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"' in body


# compose: off by default, the same URL as before; on, the participants' own role

def _interpolate(text, env):
    """Compose's ${A}, ${A:-default} and ${A:?error}, defaults nested (enough for these lines)."""
    def value(expr):
        m = re.fullmatch(r"([A-Z_][A-Z0-9_]*)(?::([-?])(.*))?", expr, re.S)
        name, op, rest = m.group(1), m.group(2), m.group(3)
        if env.get(name):
            return env[name]
        if op == "-":
            return _interpolate(rest, env)
        if op == "?":
            raise KeyError(rest)
        return ""

    out, i = "", 0
    while i < len(text):
        if text.startswith("${", i):
            depth, j = 1, i + 2
            while depth:
                if text.startswith("${", j):
                    depth, j = depth + 1, j + 2
                    continue
                depth -= text[j] == "}"
                j += 1
            out += value(text[i + 2:j - 1])
            i = j
        else:
            out += text[i]
            i += 1
    return out


def _line(text, anchor, key="DATABASE_URL"):
    block = text[text.index(anchor):]
    return re.search(rf"^\s+{key}: (.*)$", block, re.M).group(1).strip()


COMPOSE = Path(DOCKER, "compose.yaml").read_text("utf-8")
ROLES = Path(DOCKER, "isolation", "roles.yaml").read_text("utf-8")
BASE = {"DB_APP_USER": "askurious_app", "DB_APP_PASSWORD": "app", "POSTGRES_PASSWORD": "su",
        "DB_RUN_USER": "askurious_run", "DB_RUN_PASSWORD": "run"}
ON = dict(BASE, PARTICIPANT_DB_USER="askurious_participant", PARTICIPANT_DB_PASSWORD="part")


def test_without_the_app_role_compose_stops_instead_of_the_superuser():
    """No fallback to the superuser "askurious" any more (2026-10-24): the researchers' and the participants' lines."""
    for anchor in ("x-app-fixed: &app-fixed", "  participant-a: &participant"):
        with pytest.raises(KeyError, match="DB_APP_USER missing"):
            _interpolate(_line(COMPOSE, anchor), {"POSTGRES_PASSWORD": "su"})


@pytest.mark.parametrize("env", [BASE, {"POSTGRES_PASSWORD": "su", "DB_APP_USER": "askurious_app", "DB_APP_PASSWORD": "a"}])
def test_off_the_participants_keep_the_url_of_before(env):
    app_url = _interpolate(_line(COMPOSE, "x-app-fixed: &app-fixed"), env)
    assert _interpolate(_line(COMPOSE, "  participant-a: &participant"), env) == app_url
    if "DB_RUN_USER" in env:
        runtime = _interpolate(_line(ROLES, "x-runtime: &runtime"), env)
        assert _interpolate(_line(ROLES, "x-participant-runtime: &participant-runtime"), env) == runtime
        assert _interpolate(_line(ROLES, "  migrate:", "PARTICIPANT_ROLE"), env) == ""


def test_on_the_participants_use_their_own_role():
    assert _interpolate(_line(COMPOSE, "  participant-a: &participant"), ON) == \
        "postgresql://askurious_participant:part@db/askurious"
    assert _interpolate(_line(ROLES, "x-participant-runtime: &participant-runtime"), ON) == \
        "postgresql://askurious_participant:part@db/askurious"
    assert _interpolate(_line(ROLES, "  migrate:", "PARTICIPANT_ROLE"), ON) == "askurious_participant"
    # researcher and housekeeping keep the runtime role
    assert "askurious_run" in _interpolate(_line(ROLES, "x-runtime: &runtime"), ON)
    assert "participant-a:\n    environment: *participant-runtime" in ROLES
    assert "housekeeping:\n    environment: *runtime" in ROLES and "researcher:\n    environment: *runtime" in ROLES
    # the brand's participants read their own file after .env.brand
    brand = COMPOSE[COMPOSE.index("  brand-participant:"):COMPOSE.index("  brand-researcher:")]
    assert brand.index("path: .env.brand ") <brand.index("path: .env.brand-participant")


# isolation/transition.py: the rolling deploy is refused neither off nor on, but a half or crossed configuration is

spec = importlib.util.spec_from_file_location("transition_participant", Path(DOCKER) / "isolation/transition.py")
transition = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transition)

RUN = "postgresql://askurious_run:run@db/askurious"
OWNER = "postgresql://askurious_app:app@db/askurious"
PART = "postgresql://askurious_participant:part@db/askurious"


def _controller(participant_url=RUN, role="", housekeeping_url=RUN):
    obj = transition.Transition.__new__(transition.Transition)
    env = lambda url: {"DATABASE_URL": url, "DATABASE_SCHEMA_MODE": "check"}  # noqa: E731
    obj.services = {"researcher": {"environment": env(RUN)}, "housekeeping": {"environment": env(housekeeping_url)},
                    "participant-a": {"environment": env(participant_url)},
                    "participant-b": {"environment": env(participant_url)}, "db": {}}
    job = {"DATABASE_URL": OWNER, "RUNTIME_ROLE": "askurious_run"}
    if role is not None:
        job["PARTICIPANT_ROLE"] = role
    obj.jobs = {"migrate": {"environment": job}}
    obj.sql = []

    def compose(*args, **kw):
        obj.sql.append(args[-1])
        return json.dumps({"ok": True})

    obj.compose = compose
    return obj


@pytest.mark.parametrize("role", [None, ""])
def test_isolation_check_unchanged_while_off(role):
    obj = _controller(role=role)
    obj.check_roles()
    assert len(obj.sql) == 1 and "askurious_participant" not in obj.sql[0]


def test_isolation_check_accepts_the_participant_role_and_checks_its_rights():
    obj = _controller(PART, "askurious_participant")
    obj.check_roles()
    assert len(obj.sql) == 2 and "'askurious_participant'" in obj.sql[1] and "'askurious_participant'" not in obj.sql[0]
    assert "TRUNCATE" in obj.sql[1] and "alembic_version" in obj.sql[1] and "public.user_login" in obj.sql[1]


@pytest.mark.parametrize("participant_url, role, housekeeping_url", [
    (PART, "", RUN),                                   # participants on a role the job does not grant
    (RUN, "askurious_participant", RUN),               # granted, but the participants still on the runtime role
    (PART, "askurious_participant", PART),             # the housekeeping must keep the runtime role
    (PART, "askurious_run", RUN),                      # the participant role is the runtime role
    ("postgresql://askurious_participant:part@db/other", "askurious_participant", RUN),  # another database
])
def test_isolation_check_refuses_half_or_crossed_configuration(participant_url, role, housekeeping_url):
    with pytest.raises(transition.Refused):
        _controller(participant_url, role, housekeeping_url).check_roles()


def test_isolation_check_refuses_a_participant_role_without_its_rights():
    obj = _controller(PART, "askurious_participant")
    answers = iter([json.dumps({"ok": True}), json.dumps({"role": True, "closed": False})])
    obj.compose = lambda *a, **kw: next(answers)
    with pytest.raises(transition.Refused, match="Participant rights"):
        obj.check_roles()


def test_db_digest_does_not_see_the_participants():
    """The isolation's database check hashes only the db service: the role changes nothing there."""
    obj = transition.Transition.__new__(transition.Transition)
    obj.services = {"db": {"image": "postgres:17", "command": ["postgres"]}}
    before = obj.db_digest()
    obj.services["participant-a"] = {"environment": {"DATABASE_URL": PART}}
    assert obj.db_digest() == before
    db_block = COMPOSE[COMPOSE.index("\n  db:\n"):COMPOSE.index("\n  housekeeping:\n")]
    assert "PARTICIPANT" not in db_block


@needs_bash
@pytest.mark.parametrize("env, message", [
    ("PARTICIPANT_DB_USER=askurious_participant\n", "both or neither"),
    ("PARTICIPANT_DB_USER=askurious_participant\nPARTICIPANT_DB_PASSWORD=abc\n", "needs DB_ISOLATION=1"),
])
def test_restart_refuses_a_half_switched_participant_role(tmp_path, env, message):
    w = World(tmp_path)
    folder = w.repo / "deploy/docker"
    _write(str(folder / ".env"), env)
    out = w.run("--no-build", script=str(folder / "restart.sh"), AS_SERVER="1", FAKE_PARTICIPANTS="1",
                FAKE_DB_VERSION="0059")
    assert out.returncode != 0 and message in out.stderr, out.stdout + out.stderr
    assert "up -d" not in (w.text("docker.log") or "")


def test_role_script_prints_no_secret_and_needs_isolation():
    text = Path(DOCKER, "participant-db-role.sh").read_text("utf-8")
    assert "Needs DB_ISOLATION=1" in text and "ISOLATION_ACTIVE" in text
    assert "--grants-only" in text and "transition.py preflight" in text
    assert not re.search(r"echo[^\n]*\$pw", text)  # the password only through stdin and into the protected files
    assert os.access(Path(DOCKER, "participant-db-role.sh"), os.X_OK) or os.name == "nt"
