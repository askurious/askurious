"""DP-15: preserve old endpoints until the sequential restart has finished. Host Python, stdlib only.

Compose configuration/inspect output contain credentials: capture them, never print them.
No container is stopped/recreated here. Compose owns network creation; existing DB stays alive.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit


class Refused(RuntimeError):
    pass


def command(args, env=None, optional=False):
    result = subprocess.run(args, env=env, capture_output=True, text=True)
    if result.returncode and not optional:
        raise Refused("Docker/Compose operation failed; no captured configuration or credentials logged")
    return result.stdout if result.returncode == 0 else None


def connection(url):
    parts = urlsplit(url or "")
    role, database = unquote(parts.username or ""), parts.path.lstrip("/")
    if parts.scheme not in ("postgresql", "postgresql+psycopg", "postgres") or parts.hostname != "db" \
            or parts.port not in (None, 5432) or not parts.password or parts.query:
        raise Refused("An explicit PostgreSQL connection to db is required")
    for value in (role, database):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
            raise Refused("Unsupported database/role identifier")
    return role, database


class Transition:
    def __init__(self):
        self.env = dict(os.environ)
        files = self.env.get("COMPOSE_FILE", "").split(":")
        files = [f for f in files if not f.endswith("/isolation/transition.yaml")]
        self.env["COMPOSE_FILE"] = ":".join(files)
        self.cfg = json.loads(self.compose("config", "--format", "json"))
        self.jobs = json.loads(self.compose("--profile", "migration", "--profile", "brand-migration",
                                            "config", "--format", "json"))["services"]
        self.project = self.cfg["name"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.project):
            raise Refused("Unsupported Compose project name")
        self.services = self.cfg["services"]
        self.state = Path.home() / "askurious-env" / f"isolation-db-{self.project}.json"

    def compose(self, *args, target=True):
        return command(["docker", "compose", *args], self.env if target else None)

    def container(self, service):
        ids = self.compose("ps", "--all", "-q", service).split()
        if len(ids) > 1:
            raise Refused(f"Multiple containers for {service}; nothing changed")
        if not ids:
            return None
        data = json.loads(command(["docker", "inspect", ids[0]]))[0]
        labels = data["Config"].get("Labels", {})
        if labels.get("com.docker.compose.project") != self.project \
                or labels.get("com.docker.compose.service") != service:
            raise Refused(f"Foreign container for {service}; nothing changed")
        return data

    def network(self, key):
        return self.cfg["networks"][key]["name"]

    def check_networks(self):
        for key, config in self.cfg["networks"].items():
            raw = command(["docker", "network", "inspect", config["name"]], optional=True)
            if raw is None:
                continue
            data = json.loads(raw)[0]
            labels = data.get("Labels") or {}
            if labels.get("com.docker.compose.project") != self.project \
                    or labels.get("com.docker.compose.network") != key \
                    or bool(data.get("Internal")) != bool(config.get("internal")):
                raise Refused("Foreign or incompatible target network; nothing changed")

    def db_digest(self):
        spec = {k: v for k, v in self.services["db"].items() if k != "networks"}
        return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()

    def check_db(self):
        data = self.container("db")
        if not data or not data["State"].get("Running"):
            raise Refused("Provision and start the database/roles before enabling DB_ISOLATION")
        expected = self.services["db"]
        env = dict(v.split("=", 1) for v in data["Config"]["Env"] if "=" in v)
        if data["Config"].get("Cmd") != expected.get("command") \
                or any(env.get(k) != str(v) for k, v in expected.get("environment", {}).items()):
            if os.environ.get("ISOLATION_MAINTENANCE") != "1":
                raise Refused("Database command/environment changed; use the maintenance window")
        for mount in expected.get("volumes", []):
            name = self.cfg["volumes"][mount["source"]]["name"]
            if not any(m.get("Type") == "volume" and m.get("Name") == name and m.get("Destination") == mount["target"]
                       for m in data.get("Mounts", [])):
                raise Refused("Database volume differs; nothing changed")
        actual = data["Config"]["Labels"].get("com.docker.compose.config-hash")
        image = json.loads(command(["docker", "image", "inspect", self.services["db"]["image"]]))[0]["Id"]
        same_image = data["Image"] == image
        if self.state.exists():
            if self.state.is_symlink():
                raise Refused("Unexpected isolation state symlink")
            state = json.loads(self.state.read_text())
            unchanged = state == {"digest": self.db_digest(), "label_hash": actual, "image": data["Image"]}
        else:
            # The first adoption permits only the exact legacy Compose configuration, plus new networks.
            base = self.compose("-f", "compose.yaml", "config", "--hash", "db").split()
            unchanged = bool(base) and actual == base[-1]
        if not (unchanged and same_image) and os.environ.get("ISOLATION_MAINTENANCE") != "1":
            raise Refused("DB image/configuration changed: use the backed-up maintenance window, not a rolling deploy")

    def pairs(self):
        yield "researcher", "migrate"
        if "brand-researcher" in self.services:
            yield "brand-researcher", "brand-migrate"

    def check_roles(self):
        seen = set()
        used_roles = set()
        runtime_urls = set()
        participant_roles = {}  # "" or "brand-": (participant role, database), audit 2026-10-18, CFG-04
        for service, job in self.pairs():
            runtime_env = self.services[service].get("environment", {})
            owner_env = self.jobs[job].get("environment", {})
            # PARTICIPANT_ROLE may be there, empty while the participants share the runtime role
            if not {"DATABASE_URL", "RUNTIME_ROLE"} <= set(owner_env) <= {"DATABASE_URL", "RUNTIME_ROLE", "PARTICIPANT_ROLE"} \
                    or runtime_env.get("DATABASE_SCHEMA_MODE") != "check":
                raise Refused("Migration credentials or runtime schema mode are incomplete")
            runtime, database = connection(runtime_env.get("DATABASE_URL"))
            owner, owner_db = connection(owner_env.get("DATABASE_URL"))
            if owner_env.get("RUNTIME_ROLE") != runtime:
                raise Refused("Migration job must grant rights to the configured runtime role")
            if runtime == owner or database != owner_db or runtime in ("askurious", "postgres") or database in seen \
                    or runtime in used_roles or owner in used_roles:
                raise Refused("Runtime/owner roles or brand databases are not separated")
            seen.add(database)
            used_roles.update((runtime, owner))
            runtime_urls.add(runtime_env["DATABASE_URL"])
            participant = owner_env.get("PARTICIPANT_ROLE") or ""
            if participant:
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", participant) \
                        or participant in ("askurious", "postgres") or participant in used_roles:
                    raise Refused("Participant role is not separated from the runtime/owner roles")
                used_roles.add(participant)
                participant_roles["brand-" if service.startswith("brand-") else ""] = (participant, database)
            # Names are strictly validated; passwords are never passed to psql or printed.
            sql = f"""SELECT json_build_object(
              'role', EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{runtime}' AND rolcanlogin
                AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
                AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid)),
              'owner', (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())='{owner}'
                AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{owner}' AND rolcanlogin
                  AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls),
              'limited', NOT has_schema_privilege('{runtime}','public','CREATE')
                AND NOT has_database_privilege('{runtime}',current_database(),'TEMP')
                AND NOT has_database_privilege('{runtime}',current_database(),'CREATE')
                AND has_database_privilege('{runtime}',current_database(),'CONNECT'),
              'tables', NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
                WHERE n.nspname='public' AND CASE WHEN c.relkind IN ('r','p') THEN
                (pg_get_userbyid(c.relowner)<>'{owner}' OR has_table_privilege('{runtime}',c.oid,'TRUNCATE') OR
                 (c.relname<>'alembic_version' AND NOT
                  (has_table_privilege('{runtime}',c.oid,'SELECT') AND has_table_privilege('{runtime}',c.oid,'INSERT')
                   AND has_table_privilege('{runtime}',c.oid,'UPDATE') AND has_table_privilege('{runtime}',c.oid,'DELETE'))))
                ELSE FALSE END),
              'sequences', NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
                WHERE n.nspname='public' AND CASE WHEN c.relkind='S' THEN
                NOT has_sequence_privilege('{runtime}',c.oid,'USAGE') ELSE FALSE END),
              'marker', has_table_privilege('{runtime}','public.alembic_version','SELECT') AND
                NOT has_table_privilege('{runtime}','public.alembic_version','INSERT,UPDATE,DELETE'))"""
            result = json.loads(self.compose("exec", "-T", "db", "psql", "-U", "askurious", "-d", database,
                                             "-AtX", "-v", "ON_ERROR_STOP=1", "-c", sql))
            if not all(result.values()):
                raise Refused(f"Runtime rights check failed for {service}; nothing restarted")
            if participant:
                self.check_participant_rights(service, participant, database)
        # All permanent main/brand workers must use the same restricted connection as their researcher; the
        # participants' containers instead their own participant role, when the migration job grants one.
        participant_urls = set()
        for prefix, services in (("", ("participant-a", "participant-b", "housekeeping")),
                                  ("brand-", ("brand-participant", "brand-housekeeping"))):
            reference = self.services.get(prefix + "researcher")
            if reference:
                for service in services:
                    env = self.services[service]["environment"]
                    if env.get("DATABASE_SCHEMA_MODE") != "check":
                        raise Refused(f"Runtime credentials differ for {service}")
                    if "participant" in service and prefix in participant_roles:
                        if connection(env.get("DATABASE_URL")) != participant_roles[prefix]:
                            raise Refused(f"{service} must use the participant role the migration job grants")
                        participant_urls.add(env["DATABASE_URL"])
                    elif env.get("DATABASE_URL") != reference["environment"].get("DATABASE_URL"):
                        raise Refused(f"Runtime credentials differ for {service}")
        for service, spec in self.services.items():
            env = spec.get("environment", {})
            if "DATABASE_URL" in env and (env["DATABASE_URL"] not in runtime_urls | participant_urls
                                          or env.get("DATABASE_SCHEMA_MODE") != "check"):
                raise Refused("Every permanent database client must use a configured restricted runtime connection")

    def check_participant_rights(self, service, role, database):
        """The participants' role (audit 2026-10-18, CFG-04): may sign in, owns nothing, inherits nothing, may not
        create anything, has no right beyond SELECT/INSERT/UPDATE/DELETE, reads the migration marker only, and has the
        rights of app/participant_grants.py (two as a sample; sign-in data and sessions stay closed to it)."""
        sql = f"""SELECT json_build_object(
          'role', EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}' AND rolcanlogin
            AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
            AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid)
            AND NOT EXISTS (SELECT 1 FROM pg_class WHERE relowner=pg_roles.oid)),
          'limited', NOT has_schema_privilege('{role}','public','CREATE')
            AND NOT has_database_privilege('{role}',current_database(),'TEMP')
            AND NOT has_database_privilege('{role}',current_database(),'CREATE')
            AND has_database_privilege('{role}',current_database(),'CONNECT'),
          'tables', NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
            WHERE n.nspname='public' AND c.relkind IN ('r','p') AND (has_table_privilege('{role}',c.oid,'TRUNCATE')
              OR has_table_privilege('{role}',c.oid,'REFERENCES') OR has_table_privilege('{role}',c.oid,'TRIGGER'))),
          'marker', has_table_privilege('{role}','public.alembic_version','SELECT') AND
            NOT has_table_privilege('{role}','public.alembic_version','INSERT,UPDATE,DELETE'),
          'granted', has_table_privilege('{role}','public.survey','SELECT')
            AND has_table_privilege('{role}','public.response','INSERT'),
          'closed', NOT has_table_privilege('{role}','public.user_login','SELECT,INSERT,UPDATE,DELETE')
            AND NOT has_table_privilege('{role}','public.login_session','SELECT,INSERT,UPDATE,DELETE'))"""
        result = json.loads(self.compose("exec", "-T", "db", "psql", "-U", "askurious", "-d", database,
                                         "-AtX", "-v", "ON_ERROR_STOP=1", "-c", sql))
        if not all(result.values()):
            raise Refused(f"Participant rights check failed for {service}; nothing restarted")

    def preflight(self):
        self.check_networks()
        self.check_db()
        self.check_roles()
        for service in self.services:
            self.container(service)  # verify project/service labels before any mutation

    def prepare(self):
        self.preflight()
        # Compose creates its own labelled networks/volumes; --no-recreate cannot stop existing containers.
        self.compose("create", "--no-recreate", *self.services, target=False)
        self.check_networks()
        for service, spec in self.services.items():
            data = self.container(service)
            if not data or not data["State"].get("Running"):
                continue
            for key, options in spec["networks"].items():
                name = self.network(key)
                if name in data["NetworkSettings"]["Networks"]:
                    continue
                args = ["docker", "network", "connect", "--alias", service]
                for alias in (options or {}).get("aliases", []):
                    args += ["--alias", alias]
                command([*args, name, data["Id"]])
        print("New network endpoints attached; legacy endpoints retained")

    def finalize(self):
        self.check_roles()
        legacy = self.project + "_default"
        for service, spec in self.services.items():
            data = self.container(service)
            if not data or not data["State"].get("Running"):
                raise Refused(f"{service} is not running; legacy endpoints retained")
            wanted = {self.network(key) for key in spec["networks"]}
            actual = set(data["NetworkSettings"]["Networks"])
            allowed = wanted | ({legacy} if service == "db" else set())
            if not wanted <= actual or not actual <= allowed:
                raise Refused(f"Unexpected networks for {service}; legacy endpoints retained")
            expected_env = spec.get("environment", {})
            if "DATABASE_URL" in expected_env:
                actual_env = dict(v.split("=", 1) for v in data["Config"]["Env"] if "=" in v)
                if any(actual_env.get(k) != expected_env.get(k) for k in ("DATABASE_URL", "DATABASE_SCHEMA_MODE")):
                    raise Refused(f"Old credentials on {service}; legacy endpoints retained")
        for service in ("participant-a", "participant-b", "researcher", "brand-participant", "brand-researcher"):
            if service in self.services:
                self.compose("exec", "-T", "nginx", "wget", "-q", "-T", "5", "-O", "/dev/null", f"http://{service}:8000/healthz")
        data = self.container("db")
        if legacy in data["NetworkSettings"]["Networks"]:
            raw = json.loads(command(["docker", "network", "inspect", legacy]))[0]
            if (raw.get("Labels") or {}).get("com.docker.compose.project") != self.project:
                raise Refused("Foreign legacy network; nothing disconnected")
            command(["docker", "network", "disconnect", legacy, data["Id"]])
        state = {"digest": self.db_digest(), "label_hash": data["Config"]["Labels"].get("com.docker.compose.config-hash"),
                 "image": data["Image"]}
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(dir=self.state.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(state, handle)
            os.replace(path, self.state)
        finally:
            if os.path.exists(path):
                os.unlink(path)
        print("Target networks verified; legacy DB endpoint removed without restart")


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ("preflight", "prepare", "finalize"):
            raise Refused("usage: transition.py preflight|prepare|finalize")
        getattr(Transition(), sys.argv[1])()
    except Refused as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError, OSError):
        # Do not print exception text from parsers/inspect: it could include credential-bearing input.
        print("Isolation step refused or failed; no captured configuration/credentials logged. Check roles, networks and the runbook.", file=sys.stderr)
        sys.exit(1)
