"""Database migrations with Alembic (concept migrationen-konzept.md, M1 to M10 as recommended, 2026-10-04).

Every change of the schema is a numbered file under migrations/versions/ with upgrade() and downgrade(); the
database keeps its state in the table alembic_version. When the app starts (create_app), upgrade() brings the
database to the state of the code, one server process at a time:

  empty database               every migration from the first
  database without a version   made before migrations (both servers in October 2026): the old start-up steps once
                               (missing columns and tables, app/extensions.py), then compared with the models; the
                               tables, columns and types of the models are there: stamped with the newest version
                               (what already runs today, like a foreign key add_missing_columns never made, is only
                               listed); a table, column or type missing: the app does not start, the log names it
  database with a version      the migrations still missing, if any

SQLite gets a copy of the database under instance/backups/ before a pending migration (M7); the housekeeping removes
copies after BACKUP_DAYS (sweep_backups). PostgreSQL runs every migration in a transaction of its own, and
Administration > Datenbank asks for a backup first.

Outside the comparison (M5): SQLite's full-text table of the constructs (constructs.rebuild_index) and the
expression index of linked elements (linked.ensure_index) keep their own ensure functions.

For development: change the model, `flask db migrate -m "..."`, read and fix the new file, `flask db upgrade`;
tests/platform/test_migrations.py fails while models and migrations differ.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app.extensions import db

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRECTORY = os.path.join(HERE, "migrations")
# kept out of the comparison: not in the models (see the module text)
OUTSIDE = ("construct_fts", "alembic_version")
LOCK_KEY = 4713  # pg_advisory_lock of the migrations (START_UP_LOCK + 1)

_head = []


class SchemaMismatch(RuntimeError):
    """A database made before migrations differs from the models: it is not stamped, the app does not start. Also
    for a database at a version the code does not know."""


def include_object(obj, name, type_, reflected, compare_to):
    """Alembic's filter: the full-text table of the constructs (and the tables SQLite makes for it), and indexes
    no model declares (the expression index of linked elements), stay out of comparison and autogenerate."""
    if type_ == "table" and name and (name in OUTSIDE or name.startswith("construct_fts")):
        return False
    if type_ == "index" and reflected and compare_to is None:
        return False  # an index only the database has (linked.ensure_index): not the models' business
    return True


def _config(connection=None):
    from alembic.config import Config

    cfg = Config(os.path.join(DIRECTORY, "alembic.ini"))
    cfg.set_main_option("script_location", DIRECTORY)
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def head():
    """The newest version of the code (read once per process)."""
    if not _head:
        from alembic.script import ScriptDirectory

        _head.append(ScriptDirectory.from_config(_config()).get_current_head())
    return _head[0]


def history():
    """[(revision, title)] of every migration, the newest first (Administration > Datenbank)."""
    from alembic.script import ScriptDirectory

    return [(s.revision, (s.doc or "").strip().splitlines()[0] if s.doc else "")
            for s in ScriptDirectory.from_config(_config()).walk_revisions()]


def current(connection=None):
    """The version the database is at, or None (no migrations yet)."""
    from sqlalchemy import inspect, text

    def read(conn):
        if not inspect(conn).has_table("alembic_version"):
            return None
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if connection is not None:
        return read(connection)
    with db.engine.connect() as conn:
        return read(conn)


# differences that keep a database from being stamped: the code needs these tables, columns and types. Everything
# else already runs today and is only listed (Administration > Datenbank): foreign keys and indexes that
# add_missing_columns never made for a column it added later, extra columns of old manual changes, NOT NULL.
NEEDED = ("add_table", "add_column", "modify_type")


def differences(connection):
    """[(kind, words)] of what the database differs in from the models (empty: the same). kind is Alembic's
    ("add_column", "add_fk", ...)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, inspect

    context = MigrationContext.configure(connection, opts={"compare_type": True, "include_object": include_object})
    inspector = inspect(connection)
    unnamed = {}  # table -> column sets of UNIQUE constraints SQLite made without a name (before the convention)

    def same_unique(step):
        """A UNIQUE of the models that the database has under no name (SQLite, made by create_all before the
        naming convention): the same constraint, not a difference."""
        if step[0] != "add_constraint" or not isinstance(step[1], UniqueConstraint):
            return False
        table = step[1].table.name
        if table not in unnamed:
            found = [set(u["column_names"]) for u in inspector.get_unique_constraints(table) if not u.get("name")]
            found += [set(i["column_names"]) for i in inspector.get_indexes(table) if i.get("unique")]
            unnamed[table] = found
        return {c.name for c in step[1].columns} in unnamed[table]

    def word(part):
        if isinstance(part, ForeignKeyConstraint) and not part.name:
            return _unnamed_fk(part)
        name = getattr(part, "name", None)
        if name is not None:
            table = getattr(getattr(part, "table", None), "name", None)
            return f"{table}.{name}" if table and table != name else str(name)
        return str(part) if isinstance(part, (str, int, bool)) else type(part).__name__

    out = []
    for diff in compare_metadata(context, db.metadata):
        for step in (diff if isinstance(diff, list) else [diff]):
            if isinstance(step, tuple) and same_unique(step):
                continue
            if isinstance(step, tuple):
                out.append((step[0], _words(step[1:], word)))
            else:
                out.append(("other", str(step)))
    return out


def _unnamed_fk(fk):
    """A foreign key without a name (SQLite, made before the naming convention) by what it says:
    "survey_label.survey_id -> survey.id ON DELETE SET NULL"."""
    table = getattr(fk.table, "name", "?")
    columns = ", ".join(f"{table}.{c}" for c in fk.column_keys)
    targets = ", ".join(e.target_fullname for e in fk.elements)
    return f"{columns} -> {targets}" + (f" ON DELETE {fk.ondelete.upper()}" if fk.ondelete else "")


def _words(parts, word):
    """The objects of one difference: "feedback.handmade" for a column, "feedback.message VARCHAR(5) -> TEXT" for a
    type."""
    from sqlalchemy.types import TypeEngine

    objects = [word(p) for p in parts if hasattr(p, "name") and not isinstance(p, (str, TypeEngine))]
    if objects:
        return " ".join(objects)
    names = [p for p in parts if isinstance(p, str)]
    types = [str(p) for p in parts if isinstance(p, TypeEngine)]
    return ".".join(names[:2]) + (" " + " -> ".join(types) if types else "")


def _unique_indexes(app):
    """Creates the unique indexes of the models a database made before the migrations lacks (create_all does not add
    an index to a table that is there): without them two entries with the same answer id or prompt could be stored
    after the stamp (audit 2026-10-17, S-R1). Rows that already break one stop the start, unstamped, with the index
    named, so that someone looks at them first; nothing is deleted."""
    from sqlalchemy import inspect
    from sqlalchemy.exc import IntegrityError

    with db.engine.connect() as connection:
        inspector = inspect(connection)
        names = set(inspector.get_table_names())
        have = {t: {i["name"] for i in inspector.get_indexes(t)} for t in names}
    missing = [ix for table in db.metadata.sorted_tables if table.name in names
               for ix in table.indexes if ix.unique and ix.name and ix.name not in have[table.name]]
    for ix in missing:
        try:
            with db.engine.begin() as connection:
                ix.create(connection)
        except IntegrityError as e:
            app.logger.error("Unique index %s cannot be created, the rows break it; not stamped: %s", ix.name, e)
            raise SchemaMismatch(f"Der eindeutige Index {ix.name} fehlt und lässt sich nicht anlegen, weil Zeilen ihn "
                                 "verletzen. Die Datenbank wurde nicht gestempelt; die doppelten Zeilen bitte zuerst "
                                 "prüfen.") from e
        app.logger.warning("Unique index %s was missing and has been created before the stamp", ix.name)
    _unique_constraints(app)


def _unique_constraints(app):
    """Creates the UNIQUE constraints of the models a database made before the migrations lacks (uq_seal_entry_seq,
    the codes of diary_invite, uq_account_supervisor and every column with unique=True): create_all does not add one
    to a table that is there, and differences() only listed them (audit 2026-10-21, B16). PostgreSQL gets the
    constraint, SQLite (which cannot add one to a table) a unique index of the same name and columns. Rows that
    already break one stop the start, unstamped, as in _unique_indexes; nothing is deleted."""
    from sqlalchemy import UniqueConstraint, inspect, text
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.schema import AddConstraint

    with db.engine.connect() as connection:
        inspector = inspect(connection)
        names = set(inspector.get_table_names())
        have = {t: [set(u["column_names"]) for u in inspector.get_unique_constraints(t)]
                + [set(i["column_names"]) for i in inspector.get_indexes(t) if i.get("unique")]
                for t in names}
        sqlite = connection.dialect.name == "sqlite"
    for table in db.metadata.sorted_tables:
        if table.name not in names:
            continue
        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            columns = [c.name for c in constraint.columns]
            if not columns or set(columns) in have[table.name]:
                continue
            name = constraint.name if isinstance(constraint.name, str) else f"{table.name}_{'_'.join(columns)}_key"
            try:
                with db.engine.begin() as connection:
                    if sqlite:
                        quote = connection.dialect.identifier_preparer.quote
                        connection.execute(text(f"CREATE UNIQUE INDEX {quote(name)} ON {quote(table.name)} "
                                                f"({', '.join(quote(c) for c in columns)})"))
                    else:
                        connection.execute(AddConstraint(constraint))
            except IntegrityError as e:
                app.logger.error("Unique constraint %s cannot be created, the rows break it; not stamped: %s", name, e)
                raise SchemaMismatch(f"Die Eindeutigkeitsregel {name} fehlt und lässt sich nicht anlegen, weil Zeilen "
                                     "sie verletzen. Die Datenbank wurde nicht gestempelt; die doppelten Zeilen bitte "
                                     "zuerst prüfen.") from e
            have[table.name].append(set(columns))
            app.logger.warning("Unique constraint %s was missing and has been created before the stamp", name)


def blocking(found):
    return [f for f in found if f[0] in NEEDED]


def described(found):
    return "; ".join(f"{kind} {what}" for kind, what in found)


@contextmanager
def _lock(app):
    """One server process at a time (Passenger starts several after a deploy). PostgreSQL: an advisory lock on a
    connection of its own. SQLite: an exclusive transaction on a small lock file next to the database (the database
    itself must stay free for the migration's own connection)."""
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy import text

        with db.engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": LOCK_KEY})
            connection.commit()
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                connection.commit()
        return
    path = _sqlite_path()
    if path is None:  # in memory
        yield
        return
    lock = sqlite3.connect(path + ".migrate-lock", timeout=300, isolation_level=None)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        yield
    finally:
        if lock.in_transaction:
            lock.execute("ROLLBACK")
        lock.close()


def _sqlite_path():
    database = db.engine.url.database
    return None if not database or database == ":memory:" else os.path.abspath(database)


def backup(app):
    """SQLite: a copy of the database under instance/backups/ before a pending migration (sqlite3's backup, consistent
    also with WAL). Returns the path, or None (PostgreSQL, in memory, empty)."""
    path = _sqlite_path()
    if db.engine.dialect.name != "sqlite" or path is None or not os.path.exists(path):
        return None
    folder = os.path.join(app.instance_path, "backups")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = os.path.join(folder, f"{os.path.splitext(os.path.basename(path))[0]}-{stamp}-vor-migration.db")
    source, copy = sqlite3.connect(path), sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    return target


def known(revision):
    """The migrations of this code know the version (False for one of newer code, e.g. after a rolled back deploy)."""
    from alembic.script import ScriptDirectory
    from alembic.script.revision import RevisionError
    from alembic.util import CommandError

    try:
        return ScriptDirectory.from_config(_config()).get_revision(revision) is not None
    except (CommandError, RevisionError):
        return False


BACKUP_STEP_SECONDS = 3600  # one copy per step (from, to) and hour, however often a failing start is tried again


def _backup_once(app, before, after):
    """backup(), unless a copy for the same step was made less than BACKUP_STEP_SECONDS ago and is still there: a
    migration that fails is tried again by every new server process (code review 2026-10-07, K4). The step is noted
    in a small file next to the copies (its time and the path of the copy)."""
    import time

    marker = os.path.join(app.instance_path, "backups", f".schritt-{before}-{after}")
    try:
        if time.time() - os.path.getmtime(marker) < BACKUP_STEP_SECONDS:
            with open(marker, encoding="utf-8") as f:
                earlier = f.read().strip()
            if earlier and os.path.exists(earlier):
                return earlier
    except OSError:
        pass
    copy = backup(app)
    if copy:
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(copy)
        except OSError as e:
            app.logger.error("Note of the copy before migration not written: %s", e)
    return copy


BACKUP_DAYS = 30  # copies before a migration hold every answer and address: kept this long (audit 2026-10-05, L-5)
_BACKUP_END = "-vor-migration.db"


def backups(app):
    """The copies before migrations still in instance/backups/, newest first: [{"name", "path", "size", "at",
    "until": when the housekeeping removes it, or None for the one that stays}]. While the app works on SQLite the
    newest copy stays whatever its age (the way back after the last migration); on PostgreSQL every copy is of a
    database no longer in use and goes after BACKUP_DAYS like the others."""
    from datetime import timedelta

    folder = os.path.join(app.instance_path, "backups")
    try:
        names = [n for n in os.listdir(folder) if n.endswith(_BACKUP_END)]
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        stamp = name[:-len(_BACKUP_END)].rsplit("-", 2)[-2:]  # <database>-YYYYmmdd-HHMMSS-vor-migration.db
        try:
            at = datetime.strptime("-".join(stamp), "%Y%m%d-%H%M%S")
        except ValueError:
            at = datetime.fromtimestamp(st.st_mtime, timezone.utc).replace(tzinfo=None)
        out.append({"name": name, "path": path, "size": st.st_size, "at": at, "until": at + timedelta(days=BACKUP_DAYS)})
    out.sort(key=lambda b: b["at"], reverse=True)
    if out and db.engine.dialect.name == "sqlite":
        out[0]["until"] = None
    return out


def sweep_backups(app, now=None):
    """Remove the copies before migrations older than BACKUP_DAYS (see backups). Returns their names; never raises."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    gone = []
    for b in backups(app):
        if b["until"] is not None and b["until"] <= now:
            try:
                os.remove(b["path"])
            except OSError as e:
                app.logger.error("Copy before migration %s not removed: %s", b["name"], e)
                continue
            gone.append(b["name"])
    if gone:
        app.logger.warning("Copies before migrations older than %s days removed: %s", BACKUP_DAYS, ", ".join(gone))
    return gone


# PostgreSQL's time limits for the migrations' own connection (audit 2026-10-09b, B3). The server's statement_timeout
# (300 s, compose.yaml) is meant for the app's requests: an index built on a large table of answers may take longer,
# so the migration has none. lock_timeout instead: an ALTER TABLE that waits for a long read would hold every later
# access to the table (also the participants') for that time; after 10 s it gives up, and _run tries again.
MIGRATION_LIMITS = ("SET statement_timeout = 0", "SET lock_timeout = '10s'")
LOCK_TRIES = 3          # attempts of a migration that ran into lock_timeout
LOCK_PAUSE = 5          # seconds between them


def _lock_timeout(error):
    """Did PostgreSQL give up waiting for a lock (lock_timeout, SQLSTATE 55P03)?"""
    return getattr(getattr(error, "orig", None), "sqlstate", None) == "55P03"


def _run(step, target="head"):
    """An Alembic command (upgrade, stamp, downgrade) on a connection of the app's engine; Alembic opens a
    transaction per migration (PostgreSQL rolls a failed one back completely). A migration that waited too long for a
    lock is tried again: the ones before it are committed, the next attempt starts with it."""
    import time

    from sqlalchemy.exc import DBAPIError

    for attempt in range(1, LOCK_TRIES + 1):
        try:
            with db.engine.connect() as connection:
                _run_on(connection, step, target)
            return
        except DBAPIError as error:
            if attempt == LOCK_TRIES or not _lock_timeout(error):
                raise
            from flask import current_app

            current_app.logger.warning("Migration waited longer than lock_timeout for a lock (attempt %s of %s), "
                                       "trying again", attempt, LOCK_TRIES)
            time.sleep(LOCK_PAUSE)


@contextmanager
def _sqlite_transactions(connection):
    """Real transactions on this SQLite connection while the migrations run (audit 2026-10-21, B15). Python's sqlite3
    opens a transaction only before INSERT, UPDATE and DELETE, so a migration's ALTER TABLE and CREATE ran outside
    Alembic's transaction: one that failed in the middle left its first steps committed while alembic_version stayed,
    and the next start skipped the rest behind its "already there" checks. SQLAlchemy's pysqlite recipe: the driver
    left without its own transaction handling (isolation_level None) and an explicit BEGIN whenever SQLAlchemy begins
    one. Only for the migrations' connection; the app's connections stay as they are."""
    if connection.dialect.name != "sqlite":
        yield
        return
    from sqlalchemy import event

    if connection.in_transaction():
        connection.commit()
    raw = connection.connection.dbapi_connection
    before = raw.isolation_level

    def begin(conn):
        conn.exec_driver_sql("BEGIN")

    raw.isolation_level = None
    event.listen(connection, "begin", begin)
    try:
        yield
    finally:
        event.remove(connection, "begin", begin)
        if connection.in_transaction():
            connection.rollback()
        raw = connection.connection.dbapi_connection if not connection.invalidated else None
        if raw is not None:
            raw.isolation_level = before


def _run_on(connection, step, target="head"):
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        for statement in MIGRATION_LIMITS:     # for this session only, the app's connections keep their limits
            connection.exec_driver_sql(statement)
        connection.commit()
    try:
        with _sqlite_transactions(connection):
            step(_config(connection), target)
            connection.commit()
    finally:
        if postgres:
            # never back into the pool with the migrations' limits: the next request would run without a time limit
            connection.invalidate()


def upgrade(app):
    """Bring the database to the state of the code (see the module text). Raises SchemaMismatch for a database made
    before migrations that differs from the models."""
    from alembic import command
    from sqlalchemy import inspect

    newest = head()
    mode = app.config.get("DATABASE_SCHEMA_MODE", "auto")
    if mode not in ("auto", "check"):
        raise SchemaMismatch("DATABASE_SCHEMA_MODE must be auto or check")
    if mode == "check":
        now = current()
        if now != newest:
            raise SchemaMismatch("Runtime schema differs from this code; run the separate migration job first")
        return "current"
    if current() == newest:  # the usual start: nothing to do, no lock
        return "current"
    with _lock(app):
        tables = set(inspect(db.engine).get_table_names()) - set(OUTSIDE)
        tables = {t for t in tables if not t.startswith("construct_fts")}
        now = current()
        if now == newest:  # another process was faster
            return "current"
        if not tables:
            _run(command.upgrade)
            app.logger.info("Database built by the migrations (%s)", newest)
            return "built"
        if now is None:
            from app.extensions import add_missing_columns

            # the steps below change the tables: a copy first, as before every migration (audit 2026-10-21, B16)
            copy = _backup_once(app, "ohne-version", newest)
            if copy:
                app.logger.warning("Database without a version: copy before comparing and stamping: %s", copy)
            add_missing_columns(app)  # the old start-up steps, a last time (M4)
            db.metadata.create_all(db.engine)
            with db.engine.connect() as connection:
                found = differences(connection)
            if blocking(found):
                app.logger.error("The database differs from the models and is not stamped: %s", described(blocking(found)))
                raise SchemaMismatch("Die Datenbank weicht vom Schema des Codes ab und wurde nicht gestempelt: "
                                     + described(blocking(found)))
            _unique_indexes(app)  # before the stamp: the migrations made them, create_all does not (audit 2026-10-17, S-R1)
            # A copied create_all schema may have columns/tables but no cache-revision triggers.
            from app.data_revision import install

            with db.engine.begin() as connection:
                install(connection)
            _run(command.stamp)
            app.logger.warning("Database made before migrations: compared with the models and stamped %s%s", newest,
                               f"; left as they are: {described(found)}" if found else "")
            return "stamped"
        if not known(now):
            # a version the code does not know (newer code ran here, then the deploy was rolled back): no copy, as
            # every start attempt made one and Passenger tries with every request (code review 2026-10-07, K4)
            app.logger.error("The database is at version %s, which this code does not know (newest: %s): start "
                             "the code that made it, or move the database back", now, newest)
            raise SchemaMismatch(f"Die Datenbank steht auf der Version {now}, die dieser Code nicht kennt (neueste: "
                                 f"{newest}). Den Code starten, der sie angelegt hat, oder die Datenbank zurückholen "
                                 "(Kopien vor Migrationen unter instance/backups/).")
        copy = _backup_once(app, now, newest)
        _run(command.upgrade)
        app.logger.warning("Database migrated from %s to %s%s", now, newest, f" (copy before: {copy})" if copy else "")
        return "upgraded"
