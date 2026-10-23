"""DP-15: schema-only deployment job, without web routes, credentials or housekeeping."""
import os
import re

from flask import Flask

from app.extensions import db
from config import database_url, engine_options


def grant_runtime(role):
    """Restore/new tables retain DML access; migration marker and DDL remain outside runtime rights."""
    from sqlalchemy import text

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role or ""):
        raise ValueError("Invalid runtime role identifier")
    with db.engine.begin() as conn:
        safe = conn.execute(text("""SELECT NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
            AND NOT rolreplication AND NOT rolbypassrls AND rolname <> current_user
            AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid)
            FROM pg_roles WHERE rolname=:role"""), {"role": role}).scalar()
        if safe is not True:
            raise ValueError("A separate restricted runtime role must be provisioned first")
        database = conn.execute(text("SELECT current_database()")).scalar()
        quoted_db = conn.dialect.identifier_preparer.quote(database)
        conn.execute(text('REVOKE CREATE ON SCHEMA public FROM PUBLIC'))
        conn.execute(text(f'REVOKE CREATE ON SCHEMA public FROM "{role}"'))
        conn.execute(text(f'REVOKE ALL ON DATABASE {quoted_db} FROM PUBLIC'))
        conn.execute(text(f'REVOKE CREATE, TEMP ON DATABASE {quoted_db} FROM "{role}"'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE {quoted_db} TO "{role}"'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'))
        conn.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"'))
        conn.execute(text(f'REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM "{role}"'))
        conn.execute(text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}"'))
        conn.execute(text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO "{role}"'))
        conn.execute(text(f'REVOKE INSERT, UPDATE, DELETE ON public.alembic_version FROM "{role}"'))


def grant_participant(role, runtime_role=None):
    """The participants' own role (audit 2026-10-18, CFG-04; off until PARTICIPANT_DB_USER is in the .env): exactly the
    rights of app/participant_grants.py, nothing by default privileges. Run after every migration, so that a table
    entered there gets its rights before the new code starts, and everything else stays closed. One transaction:
    the rights are taken and given at the same moment, a running participant never sees a gap."""
    from sqlalchemy import text

    from app.participant_grants import GRANTS

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role or ""):
        raise ValueError("Invalid participant role identifier")
    if role == runtime_role:
        raise ValueError("The participant role must differ from the runtime role")
    with db.engine.begin() as conn:
        safe = conn.execute(text("""SELECT NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
            AND NOT rolreplication AND NOT rolbypassrls AND rolname <> current_user
            AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid)
            AND NOT EXISTS (SELECT 1 FROM pg_class WHERE relowner=pg_roles.oid)
            FROM pg_roles WHERE rolname=:role"""), {"role": role}).scalar()
        if safe is not True:
            raise ValueError("A separate restricted participant role must be provisioned first")
        database = conn.execute(text("SELECT current_database()")).scalar()
        quoted_db = conn.dialect.identifier_preparer.quote(database)
        quote = conn.dialect.identifier_preparer.quote
        conn.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"'))
        conn.execute(text(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM "{role}"'))
        conn.execute(text(f'REVOKE CREATE ON SCHEMA public FROM "{role}"'))
        conn.execute(text(f'REVOKE CREATE, TEMP ON DATABASE {quoted_db} FROM "{role}"'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE {quoted_db} TO "{role}"'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        present = set(conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())
        inserting = []
        for table, (privileges, _why) in sorted(GRANTS.items()):
            if table not in present:
                continue  # a table of a later migration: its rights come with the job after that migration
            conn.execute(text(f'GRANT {", ".join(privileges)} ON public.{quote(table)} TO "{role}"'))
            if "INSERT" in privileges:
                inserting.append(table)
        # the sequences that number new rows of the tables it may insert into (serial and identity columns)
        for sequence in conn.execute(text("""SELECT s.relname FROM pg_class s
                JOIN pg_namespace n ON n.oid = s.relnamespace
                JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass
                                 AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
                JOIN pg_class t ON t.oid = d.refobjid
                WHERE s.relkind = 'S' AND n.nspname = 'public' AND t.relname = ANY(:tables)"""),
                                     {"tables": inserting}).scalars():
            conn.execute(text(f'GRANT USAGE, SELECT ON SEQUENCE public.{quote(sequence)} TO "{role}"'))


# The keys of app/secrets_box.py: a migration may lock stored values (0056 the dashboard and quota links of an old
# database). Taken from the job's environment only, never config.py's development default, so a job without them
# stops loudly instead of locking with a wrong key (audit 2026-10-21, B12).
SECRET_KEYS = ("SECRET_KEY", "TOKEN_KEY")
SECRET_FALLBACKS = ("SECRET_KEY_FALLBACKS", "TOKEN_KEY_FALLBACKS")


def secret_settings(environ=None):
    """{name: value} of the keys (and the keys of before) set in the environment."""
    environ = os.environ if environ is None else environ
    out = {name: environ[name] for name in SECRET_KEYS if environ.get(name)}
    for name in SECRET_FALLBACKS:
        values = [v.strip() for v in (environ.get(name) or "").split(",") if v.strip()]
        if values:
            out[name] = values
    return out


def run(url, runtime_role=None, grants_only=False, participant_role=None):
    if not url or not database_url(url).startswith("postgresql+psycopg://"):
        raise ValueError("The deployment migration job requires a PostgreSQL DATABASE_URL")
    if grants_only and not runtime_role:
        raise ValueError("The grants-only job requires RUNTIME_ROLE")
    app = Flask("schema_migrate")
    app.config.update(SQLALCHEMY_DATABASE_URI=database_url(url),
                      SQLALCHEMY_ENGINE_OPTIONS=engine_options(database_url(url)),
                      DATABASE_SCHEMA_MODE="auto", **secret_settings())
    db.init_app(app)
    with app.app_context():
        from app import models, migrations, linked  # noqa: F401
        from app.data_revision import validate

        if grants_only:
            if migrations.current() != migrations.head():
                raise ValueError("Select the code matching the restored schema before repairing runtime grants")
        else:
            migrations.upgrade(app)
            linked.ensure_index()
        if runtime_role is not None:
            grant_runtime(runtime_role)
        if participant_role:
            grant_participant(participant_role, runtime_role)
        with db.engine.connect() as connection:
            validate(connection)
        db.engine.dispose()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--grants-only", action="store_true")
    args = parser.parse_args()
    run(os.environ.get("DATABASE_URL"), os.environ.get("RUNTIME_ROLE"), args.grants_only,
        os.environ.get("PARTICIPANT_ROLE") or None)
