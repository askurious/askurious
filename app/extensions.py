from contextlib import contextmanager

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Fixed names for constraints, so that a migration can change or drop one by name on PostgreSQL and SQLite alike
# (app/migrations.py). They are PostgreSQL's own default names: a database made before the convention (Ralf's
# server) already has exactly these, and indexes keep SQLAlchemy's default "ix_<table>_<column>".
NAMING = {"ix": "ix_%(column_0_label)s", "uq": "%(table_name)s_%(column_0_N_name)s_key",
          "fk": "%(table_name)s_%(column_0_N_name)s_fkey", "pk": "%(table_name)s_pkey",
          "ck": "%(table_name)s_%(constraint_name)s_check"}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


db = SQLAlchemy(model_class=Base)


def json_given(column):
    """SQL: the JSON column holds a value. A Python None written to a JSON column is stored as JSON null (the text
    'null'), not as SQL NULL, so `column.is_not(None)` would count it while Survey.kind reads None (audit 2026-10-21,
    B3). For the columns SQL filters on, such as Survey.live, Survey.interview and Survey.sampling."""
    return db.and_(column.is_not(None), db.cast(column, db.Text) != "null")


def json_missing(column):
    """SQL: the JSON column holds nothing: SQL NULL or JSON null (see json_given)."""
    return db.or_(column.is_(None), db.cast(column, db.Text) == "null")


login_manager = LoginManager()
login_manager.login_view = "auth.sign_in"  # the page with both ways in: ORCID, or e-mail and password
csrf = CSRFProtect()


def localize_login_message(message):
    from app.lang import de_en

    if message == "Please log in to access this page.":
        return de_en("Bitte melden Sie sich an, um diese Seite aufzurufen.", "Please sign in to open this page.")  # "sign in" everywhere (UI/UX audit 2026-10-16, 57)
    if message == "Please reauthenticate to access this page.":
        return de_en("Bitte melden Sie sich erneut an, um diese Seite aufzurufen.", "Please reauthenticate to access this page.")
    return message


login_manager.localize_callback = localize_login_message


def get_or_create(model, known_missing=False, **keys):
    """The row of `model` with these values (a unique key), made if there is none. Two requests at the same moment
    (a double click, autosave of two fields) both find none and both write one: the second write fails on the unique
    key. It is made in a savepoint, so only that write is taken back, not what the request did before, and the row
    the other request wrote is read instead (audit 2026-10-10, C4; the pattern of limits.charge_ai). known_missing: the
    caller has just looked (one query for many rows), so the first lookup is left out."""
    from sqlalchemy.exc import IntegrityError

    row = None if known_missing else db.session.scalar(db.select(model).filter_by(**keys))
    if row is not None:
        return row
    try:
        with db.session.begin_nested():
            row = model(**keys)
            db.session.add(row)
    except IntegrityError:
        row = db.session.scalar(db.select(model).filter_by(**keys))
        if row is None:
            raise
    return row

SQLITE_JOURNALS = ("WAL", "DELETE", "TRUNCATE", "PERSIST")


def sqlite_settings(app):
    """Settings for every new SQLite connection of this app (config.py: SQLITE_JOURNAL, SQLITE_BUSY_SECONDS).
    SQLite's defaults let one writer at a time in, make every commit wait for the disk twice and give up
    after five seconds: with many participants answering at once pages were lost with "database is locked".
    Call inside the app context, before the first connection. Other databases are left alone."""
    import sqlite3

    from sqlalchemy import event

    from app.tables import text_key

    journal = app.config.get("SQLITE_JOURNAL") or ""
    busy_ms = max(int(app.config.get("SQLITE_BUSY_SECONDS") or 0), 0) * 1000

    def on_connect(connection, _record):
        if not isinstance(connection, sqlite3.Connection):
            return
        connection.create_function("q_key", 1, text_key, deterministic=True)  # sorting and searching texts (app/tables.py)
        cursor = connection.cursor()
        try:
            if busy_ms:
                cursor.execute(f"PRAGMA busy_timeout = {busy_ms}")
            if journal in SQLITE_JOURNALS:
                cursor.execute(f"PRAGMA journal_mode = {journal}")
                if journal == "WAL":
                    cursor.execute("PRAGMA synchronous = NORMAL")  # safe with WAL: no corruption, the last commits may wait for a checkpoint
        except sqlite3.DatabaseError as e:  # e.g. a file system without shared memory: the app runs on as before
            app.logger.warning("SQLite settings not applied: %s", e)
        finally:
            cursor.close()

    event.listen(db.engine, "connect", on_connect)
    if db.engine.dialect.name == "sqlite" and not app.config.get("TESTING"):
        # SELECT ... FOR UPDATE locks nothing on SQLite, and Python's sqlite3 begins the transaction only at the first
        # write: two processes may read the same row and both write, one change lost without an error. SQLite is
        # therefore run with one server process (README, "Running a server"; audit 2026-10-21, B4)
        app.logger.info("SQLite: run this app with one server process only (Passenger: one instance); "
                        "row locks have no effect on SQLite")


def add_missing_columns(app):
    """`create_all` creates missing tables, never a missing column: a server whose database is older than the
    code answered every page with "no such column". Adds the columns the models have and the tables lack,
    before the first query. Only what cannot go wrong on a filled table: columns that may be empty, and
    required ones whose default is a plain value (the existing rows get it). Anything else, and indexes or
    constraints on a new column, still need a hand-written ALTER TABLE; such a column is named in the log.
    Never changes or drops a column."""
    from sqlalchemy import Boolean, inspect, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.schema import CreateColumn

    inspector = inspect(db.engine)
    preparer = db.engine.dialect.identifier_preparer
    for table in db.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue  # create_all makes it, complete
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            ddl = str(CreateColumn(column).compile(dialect=db.engine.dialect))
            if not column.nullable and column.server_default is None:
                value = column.default.arg if column.default is not None and column.default.is_scalar else None
                if not isinstance(value, (bool, int, float, str)):
                    app.logger.error("Column %s.%s is missing and cannot be added automatically", table.name, column.name)
                    continue
                if isinstance(value, bool):  # PostgreSQL wants true/false for a boolean column, SQLite 1/0
                    boolean = isinstance(column.type, Boolean) and db.engine.dialect.name == "postgresql"
                    literal = ("true" if value else "false") if boolean else str(int(value))
                else:
                    literal = repr(value) if not isinstance(value, str) else "'" + value.replace("'", "''") + "'"
                ddl += f" DEFAULT {literal}"
            try:
                with db.engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE {preparer.format_table(table)} ADD COLUMN {ddl}"))
                app.logger.warning("Added missing column %s.%s", table.name, column.name)
            except DBAPIError:  # OperationalError in SQLite, ProgrammingError in PostgreSQL
                # another server process of the same start was faster
                if column.name not in {c["name"] for c in inspect(db.engine).get_columns(table.name)}:
                    raise


# pg_advisory_lock key of start_up_lock (any number, only used there)
START_UP_LOCK = 4712


@contextmanager
def start_up_lock():
    """PostgreSQL: the steps at start-up (missing columns and tables, index, seed and data upgrades) one server
    process at a time. CREATE TABLE and CREATE INDEX IF NOT EXISTS are not safe there when two processes run
    them at the same moment (one fails on the catalogue), which happens when a deploy brings a new table. A
    session lock on a connection of its own, held until the steps are done; SQLite needs none (one writer)."""
    if db.engine.dialect.name != "postgresql":
        yield
        return
    from sqlalchemy import text

    with db.engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": START_UP_LOCK})
        connection.commit()
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": START_UP_LOCK})
            connection.commit()


_SEQUENCE_LISTENERS = {}


def catch_up(connection, sequence, top):
    """Move `sequence` on so that its next value lies above `top` (the highest id in its table), with nextval only.
    The runtime role of the Docker server may use the sequences but not set them (deploy/docker/isolation/
    runtime-role.sql: USAGE, SELECT), so setval failed there with "permission denied" and undoing a deleted block
    ended in a server error (Ralf, 2026-10-22). Mostly nothing is to do: undo puts back ids the sequence gave out
    long ago. A new account (auth.new_account_ids) is one above the highest id, so a few steps at most."""
    from sqlalchemy import text

    if top is None:
        return
    last, called = connection.execute(text(f"select last_value, is_called from {sequence}")).one()
    # is_called false: the next nextval gives last_value itself, so it counts as one below
    behind = top - (last if called else last - 1)
    if behind > 0:
        connection.execute(text("select nextval(:s) from generate_series(1, :n)"), {"s": sequence, "n": behind})


def postgres_settings(app):
    """PostgreSQL counts new ids with a sequence per table, which rows inserted with a given id (new accounts,
    app/auth.py new_account_ids; a question put back by undo) do not move: the next row without one would get
    a taken id. After each flush that gave ids itself, the sequence of that table is moved up to them. SQLite
    needs nothing (it continues after the highest id). Call inside the app context; other databases are left
    alone."""
    if db.engine.dialect.name != "postgresql":
        return
    from sqlalchemy import event, inspect, text
    from sqlalchemy.orm import Session

    def given_ids(session, _context, _instances):
        for obj in session.new:
            mapper = inspect(obj).mapper
            keys = mapper.primary_key
            if len(keys) == 1 and keys[0].autoincrement in (True, "auto") and getattr(obj, mapper.get_property_by_column(keys[0]).key) is not None:
                session.info.setdefault("given_ids", set()).add((keys[0].table.name, keys[0].name))

    def move_sequences(session, _context):
        given = session.info.pop("given_ids", None)
        if not given or session.get_bind().dialect.name != "postgresql":
            return
        connection = session.connection()
        preparer = connection.dialect.identifier_preparer
        for table, column in sorted(given):
            sequence = connection.scalar(text("select pg_get_serial_sequence(:t, :c)"), {"t": preparer.quote(table), "c": column})
            if sequence:
                top = connection.scalar(text(f"select max({preparer.quote(column)}) from {preparer.quote(table)}"))
                catch_up(connection, sequence, top)

    if not _SEQUENCE_LISTENERS:  # once per process, not per app
        _SEQUENCE_LISTENERS.update(before=given_ids, after=move_sequences)
        event.listen(Session, "before_flush", given_ids)
        event.listen(Session, "after_flush", move_sequences)
