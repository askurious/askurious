"""A new Docker server signs in to its database with restricted roles from the start, and no start uses a superuser:
deploy-docker.sh NEW_ENV=1 writes the roles and DB_ISOLATION=1,
restart.sh sets them up on the empty database (db-app-role.sh --new-install) and refuses a role in a DATABASE_URL that
is missing, a superuser or may create roles; compose.yaml has no fallback to the superuser any more. The scripts run
with the stand-ins of test_audit8_b_deploy."""
from pathlib import Path
import re

import pytest

from tests.platform.test_audit8_b_deploy import DOCKER, World, _write, needs_bash

PASSWORDS = {"DB_APP_PASSWORD": "apppw1", "DB_RUN_PASSWORD": "runpw2", "PARTICIPANT_DB_PASSWORD": "partpw3"}
NEW_ENV = ("SECRET_KEY=s\nPOSTGRES_PASSWORD=supw0\nDB_ISOLATION=1\n"
           "COMPOSE_FILE=compose.yaml:isolation/roles.yaml:isolation/networks.yaml\n"
           "DB_APP_USER=askurious_app\nDB_APP_PASSWORD=apppw1\nDB_RUN_USER=askurious_run\nDB_RUN_PASSWORD=runpw2\n"
           "PARTICIPANT_DB_USER=askurious_participant\nPARTICIPANT_DB_PASSWORD=partpw3\n")
FAKE_PYTHON = '#!/usr/bin/env bash\necho "isolation $2" >> "$SERVER/docker.log"\n'


def _world(tmp_path, env=NEW_ENV):
    w = World(tmp_path)
    folder = w.repo / "deploy/docker"
    _write(str(w.repo / "migrations/versions/0059.py"), "revision = '0059'\ndown_revision = None\nROLLING = True\n")
    _write(str(folder / ".env"), env)
    _write(str(w.bin / "python3"), FAKE_PYTHON, 0o755)
    # the log names the compose files of each call: the new server's database starts with compose.yaml alone
    docker = (w.bin / "docker").read_text()
    docker = docker.replace('echo "$*" >> "$SERVER/docker.log"', 'echo "$* [${COMPOSE_FILE##*/}]" >> "$SERVER/docker.log"', 1)
    _write(str(w.bin / "docker"), docker, 0o755)
    return w, folder


def _restart(w, folder, **env):
    return w.run("--no-build", script=str(folder / "restart.sh"), AS_SERVER="1", **env)


@needs_bash
def test_a_new_server_gets_its_roles_on_the_empty_database_before_the_first_migration(tmp_path):
    w, folder = _world(tmp_path)
    out = _restart(w, folder)
    assert out.returncode == 0, out.stdout + out.stderr
    log = w.text("docker.log")
    first_db = log.index("up -d --no-deps --no-recreate --wait db [compose.yaml]")
    assert first_db < log.index("'objects:'") < log.index("isolation preflight") < log.index("isolation prepare")
    assert log.index("isolation prepare") < log.index("run --rm --no-deps migrate") < log.index("'roles-checked:'")
    assert log.index("'roles-checked:'") < log.index("up -d --no-deps researcher") < log.index("isolation finalize")
    sql = w.text("psql-stdin.log")
    assert "ALTER ROLE askurious_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in sql
    assert "ALTER DATABASE askurious OWNER TO askurious_app;" in sql
    assert "CREATE ROLE askurious_run NOLOGIN NOINHERIT NOSUPERUSER" in sql             # isolation/runtime-role.sql
    assert "ALTER ROLE askurious_run LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "ALTER ROLE askurious_participant LOGIN NOINHERIT NOSUPERUSER" in sql
    assert "GRANT CONNECT ON DATABASE askurious TO askurious_participant;" in sql
    assert "ALTER ROLE askurious PASSWORD NULL;" in sql
    assert "REVOKE CONNECT, TEMP ON DATABASE postgres FROM PUBLIC;" in sql
    # the passwords only through stdin, never on a command line or on the screen
    for pw in PASSWORDS.values():
        assert f"PASSWORD '{pw}'" in sql and pw not in log and pw not in out.stdout + out.stderr


@needs_bash
@pytest.mark.parametrize("case", ["state", "participants", "no-isolation"])
def test_never_on_a_server_that_runs_already(tmp_path, case):
    """askurious.de: participants' containers, a database version and the isolation state; and a server without
    DB_ISOLATION=1 never gets roles set up by a restart."""
    env = NEW_ENV if case != "no-isolation" else re.sub(r"^(DB_ISOLATION|COMPOSE_FILE)=.*\n", "", NEW_ENV, flags=re.M)
    w, folder = _world(tmp_path, env)
    extra = {}
    if case == "state":
        _write(str(w.path("askurious-env", "isolation-db-askurious.json")), "{}")
    else:
        extra = {"FAKE_PARTICIPANTS": "1", "FAKE_DB_VERSION": "0059"}
    out = _restart(w, folder, **extra)
    log = w.text("docker.log")
    assert "'objects:'" not in log and w.text("psql-stdin.log") is None, out.stdout + out.stderr
    assert "[compose.yaml]" not in log


@needs_bash
@pytest.mark.parametrize("bad", ["askurious", "askurious_run"])
def test_restart_refuses_a_superuser_or_a_role_that_may_create_roles(tmp_path, bad):
    w, folder = _world(tmp_path)
    out = _restart(w, folder, FAKE_PARTICIPANTS="1", FAKE_DB_VERSION="0059", FAKE_DB_USERS=bad, FAKE_BAD_ROLES=bad)
    assert out.returncode != 0 and f"sign in to the database as {bad}" in out.stderr, out.stdout + out.stderr
    log = w.text("docker.log")
    assert "up -d --no-deps researcher" not in log and "up -d --no-deps participant-a" not in log


@needs_bash
def test_restart_refuses_when_the_roles_cannot_be_checked(tmp_path):
    w, folder = _world(tmp_path)
    _write(str(w.bin / "sleep"), "#!/usr/bin/env bash\nexit 0\n", 0o755)
    docker = (w.bin / "docker").read_text().replace('''*"'roles-checked:'"*) echo''', '''*"'roles-checked:'"*) exit 2; echo''', 1)
    _write(str(w.bin / "docker"), docker, 0o755)
    out = _restart(w, folder, FAKE_PARTICIPANTS="1", FAKE_DB_VERSION="0059")
    assert out.returncode != 0 and "could not be checked" in out.stderr
    assert "up -d --no-deps researcher" not in w.text("docker.log")


@needs_bash
def test_restart_stops_on_an_incomplete_compose_configuration(tmp_path):
    w, folder = _world(tmp_path)
    docker = (w.bin / "docker").read_text().replace('case "$*" in', 'case "$*" in\n  "compose config --quiet") '
                                                    'echo "required variable DB_APP_USER is missing a value" >&2; exit 1 ;;', 1)
    _write(str(w.bin / "docker"), docker, 0o755)
    out = _restart(w, folder, FAKE_PARTICIPANTS="1", FAKE_DB_VERSION="0059")
    assert out.returncode != 0 and "DB_APP_USER" in out.stderr and "Nothing restarted" in out.stderr
    assert " up " not in w.text("docker.log")


def _new_install(w, folder, **env):
    return w.run("--new-install", script=str(folder / "db-app-role.sh"), AS_SERVER="1", **env)


@needs_bash
def test_new_install_refuses_a_database_that_holds_anything(tmp_path):
    w, folder = _world(tmp_path)
    out = _new_install(w, folder, FAKE_OBJECTS="3")
    assert out.returncode != 0 and "not empty" in out.stderr
    assert w.text("psql-stdin.log") is None


@needs_bash
def test_new_install_keeps_a_runtime_role_there_already(tmp_path):
    """A second start after a stop in between: runtime-role.sql ran whole (one transaction), only the password again."""
    w, folder = _world(tmp_path)
    out = _new_install(w, folder, FAKE_RUNTIME_ROLE="1")
    assert out.returncode == 0, out.stdout + out.stderr
    sql = w.text("psql-stdin.log")
    assert "CREATE ROLE askurious_run NOLOGIN" not in sql and "ALTER ROLE askurious_run LOGIN" in sql


@needs_bash
@pytest.mark.parametrize("env, says", [
    (NEW_ENV.replace("PARTICIPANT_DB_PASSWORD=partpw3\n", ""), "both or neither"),
    (NEW_ENV.replace("DB_RUN_USER=askurious_run", "DB_RUN_USER=askurious"), "DB_RUN_USER is not askurious_run"),
    (NEW_ENV.replace("DB_APP_USER=askurious_app", "DB_APP_USER=askurious"), "DB_APP_USER is not askurious_app"),
    (NEW_ENV.replace("DB_RUN_PASSWORD=runpw2", "DB_RUN_PASSWORD="), "not letters and digits"),
])
def test_new_install_refuses_incomplete_settings(tmp_path, env, says):
    w, folder = _world(tmp_path, env)
    out = _new_install(w, folder)
    assert out.returncode != 0 and says in out.stderr, out.stdout + out.stderr
    assert w.text("psql-stdin.log") is None


@needs_bash
def test_new_install_without_participant_role(tmp_path):
    w, folder = _world(tmp_path, re.sub(r"^PARTICIPANT_DB_.*\n", "", NEW_ENV, flags=re.M))
    out = _new_install(w, folder)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "askurious_participant" not in w.text("psql-stdin.log")


def test_isolation_checks_look_at_single_tables_only_once_the_schema_holds_any():
    """isolation/transition.py on the new server's empty database: the migration marker and the participants' sample
    tables are checked as before as soon as anything is in the schema (askurious.de: always)."""
    text = Path(DOCKER, "isolation", "transition.py").read_text("utf-8")
    assert "c.relkind IN ('r','p','v','m','S'))" in text.split("EMPTY_SCHEMA = ")[1].split('"""')[1]
    assert text.count("CASE WHEN {EMPTY_SCHEMA} THEN TRUE ELSE") == 4
    for check in ("has_table_privilege('{runtime}','public.alembic_version','SELECT')",
                  "NOT has_table_privilege('{runtime}','public.alembic_version','INSERT,UPDATE,DELETE')",
                  "has_table_privilege('{role}','public.survey','SELECT')",
                  "NOT has_table_privilege('{role}','public.user_login','SELECT,INSERT,UPDATE,DELETE')"):
        assert check in text, check
    sql = Path(DOCKER, "isolation", "runtime-role.sql").read_text("utf-8")
    assert "IF to_regclass('public.alembic_version') IS NOT NULL THEN" in sql
    assert "REVOKE INSERT, UPDATE, DELETE ON TABLE public.alembic_version FROM askurious_run;" in sql


def test_new_env_writes_the_roles_and_the_isolation():
    deploy = Path(DOCKER, "deploy-docker.sh").read_text("utf-8")
    block = deploy[deploy.index("cat > $ENV_DIR/.env <<EOF"):]
    block = block[:block.index("\nEOF\n")]
    for line in ("DB_ISOLATION=1", "COMPOSE_FILE=compose.yaml:isolation/roles.yaml:isolation/networks.yaml",
                 "DB_APP_USER=askurious_app", "DB_RUN_USER=askurious_run", "PARTICIPANT_DB_USER=askurious_participant"):
        assert line in block.splitlines(), line
    for name in ("DB_APP_PASSWORD", "DB_RUN_PASSWORD", "PARTICIPANT_DB_PASSWORD", "POSTGRES_PASSWORD"):
        assert f"{name}=\\$(openssl rand -hex 24)" in block, name
