"""The database of the app: what it is, and moving its content into another one (Administration > Datenbank).

SQLite is the default. PostgreSQL takes many writers at once, where SQLite lets one write at a time; the code
runs on both. A move goes in three steps: check the new database (`check`), copy everything into it (`copy`),
then set DATABASE_URL in the server's .env and restart the app. Until the restart the app keeps working on the
old database, and the old database is never changed: going back means removing the line from .env again.

The copy reads a snapshot of the SQLite file (sqlite3's backup), so what participants send meanwhile does not
tear it apart; it is not in the new database either. Hence: copy shortly before the restart, best while no
survey is in the field. Tools: tools/copy_database.py does the same on the command line, for databases too
large for one request.
"""
import os
import sqlite3
import tempfile
import time

from sqlalchemy import JSON, Integer, String, Text, bindparam, cast, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url

from app.extensions import db
from app.lang import de_en

BATCH = 2000  # rows per INSERT; keeps the memory small for the large tables (answers)


class MoveError(Exception):
    """Something the admin can fix (address, rights, a database that is not empty); the text says what."""


def driver_ready():
    """True if the PostgreSQL driver (psycopg, requirements-postgres.txt) is installed."""
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return True


def masked(url):
    """The address without its password, for the page and the log."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "(unlesbar)"


def _tables():
    return db.metadata.sorted_tables  # parents before children


def _version(connection):
    name = connection.dialect.name
    if name == "sqlite":
        return "SQLite " + connection.exec_driver_sql("select sqlite_version()").scalar()
    if name == "postgresql":
        return "PostgreSQL " + connection.exec_driver_sql("show server_version").scalar().split()[0]
    return name


STATUS_SECONDS = 60  # how long Administration > Server shows the same counts and schema comparison


def status(fresh=False):
    """What the app works on now: kind, version, address, size, rows per table. The counts of every table and the
    schema comparison (Alembic reflects the whole database) are kept for STATUS_SECONDS per process and migration
    version, so reloading the tab does not read them again each time (performance audit 2026-10-09b, P6); fresh=True
    (after an action on the page) reads them anew."""
    from flask import current_app

    from app import migrations

    memo = current_app.extensions.setdefault("dbmove_status", {})
    key = (str(db.engine.url), migrations.current())
    kept = memo.get(key)
    if not fresh and kept is not None and time.monotonic() - kept[0] < STATUS_SECONDS:
        return kept[1]
    out = _status()
    memo.clear()
    memo[key] = (time.monotonic(), out)
    return out


def _status():
    engine = db.engine
    out = {"dialect": engine.dialect.name, "url": masked(engine.url), "driver": driver_ready(), "tables": []}
    with engine.connect() as connection:
        out["version"] = _version(connection)
        if out["dialect"] == "sqlite":
            out["journal"] = connection.exec_driver_sql("pragma journal_mode").scalar()
            path = engine.url.database
            out["url"] = path or de_en("(im Arbeitsspeicher)", "(in memory)")
            # with the write-ahead log the newest changes are still in app.db-wal next to the file
            out["size"] = sum(os.path.getsize(p) for p in (path, f"{path}-wal") if p and os.path.exists(p)) or None
        elif out["dialect"] == "postgresql":
            out["size"] = connection.exec_driver_sql("select pg_database_size(current_database())").scalar()
        have = set(inspect(connection).get_table_names())
        for table in _tables():
            rows = connection.scalar(select(func.count()).select_from(table)) if table.name in have else None
            out["tables"].append((table.name, rows))
    out["rows"] = sum(n or 0 for _, n in out["tables"])
    from app import migrations

    with engine.connect() as connection:
        found = migrations.differences(connection)
    out["migration"] = {"current": migrations.current(), "head": migrations.head(), "history": migrations.history(),
                        "differences": [f"{kind} {what}" for kind, what in found]}
    return out


def build_url(host="", port="", name="", user="", password=""):
    """A PostgreSQL address from the fields of the page (special characters in the password are escaped)."""
    from sqlalchemy.engine import URL

    if not (host and name and user):
        raise MoveError(de_en("Bitte Server, Datenbank und Benutzer angeben.", "Please enter server, database and user."))
    try:
        port = int(port or 5432)
    except ValueError:
        raise MoveError(de_en("Der Port ist eine Zahl, meist 5432.", "The port is a number, usually 5432."))
    return URL.create("postgresql+psycopg", username=user, password=password or None, host=host, port=port,
                      database=name).render_as_string(hide_password=False)


def env_line(host="", port="", name="", user="", password="", **_):
    """The line for the server's .env, ready to copy: special characters of the password already written as an
    address needs them (@ as %40 and so on), so nobody has to look them up."""
    from urllib.parse import quote

    if not (host and name and user):
        return ""
    # every character but letters, digits and -._~ as %XX: no blank or # that the .env reader would cut off
    login = quote(user, safe="") + (":" + quote(password, safe="") if password else "")
    url = f"postgresql://{login}@{host}:{int(port) if str(port).isdigit() else 5432}/{quote(name, safe='')}"
    return "DATABASE_URL=" + url


def target_url(url):
    """The address as typed, made usable (config.database_url); only PostgreSQL or an SQLite file."""
    from config import database_url

    url = database_url(url)
    if not url:
        raise MoveError(de_en("Bitte die Adresse der neuen Datenbank eingeben.", "Please enter the address of the new database."))
    try:
        parsed = make_url(url)
    except Exception:
        raise MoveError(de_en("Die Adresse ist nicht lesbar. Form: postgresql://Benutzer:Passwort@Server:5432/Datenbank",
                              "The address cannot be read. Form: postgresql://user:password@server:5432/database"))
    if parsed.get_backend_name() not in ("postgresql", "sqlite"):
        raise MoveError(de_en("Unterstützt werden PostgreSQL und SQLite.", "PostgreSQL and SQLite are supported."))
    if parsed.get_backend_name() == "postgresql" and not driver_ready():
        raise MoveError(de_en("Der PostgreSQL-Treiber fehlt auf diesem Server: psycopg aus requirements-postgres.txt "
                              "installieren (bei Plesk in vendor.zip aufnehmen) und die App neu starten.",
                              "The PostgreSQL driver is missing on this server: install psycopg from "
                              "requirements-postgres.txt (on Plesk, add it to vendor.zip) and restart the app."))
    if parsed.get_backend_name() == "sqlite" and not parsed.database:
        raise MoveError(de_en("Für SQLite eine Datei angeben: sqlite:////pfad/zur/datei.db",
                              "For SQLite, give a file: sqlite:////path/to/file.db"))
    if _same(parsed, make_url(str(db.engine.url))):
        raise MoveError(de_en("Das ist die Datenbank, mit der die App gerade arbeitet.",
                              "That is the database the app is working on right now."))
    return url


def _same(a, b):
    if a.get_backend_name() != b.get_backend_name():
        return False
    if a.get_backend_name() == "sqlite":
        return bool(a.database) and os.path.abspath(a.database or "") == os.path.abspath(b.database or "")
    return (a.host, a.port or 5432, a.database) == (b.host, b.port or 5432, b.database)


def _engine(url):
    options = {"connect_args": {"connect_timeout": 10}} if url.startswith("postgresql") else {}
    return create_engine(url, **options)


def check(url):
    """Connects to the new database and looks at it; changes nothing. Returns a dict for the page:
    version, tables of the app already there, rows in them, and texts too long for the new database."""
    url = target_url(url)
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            version = _version(connection)
            have = set(inspect(connection).get_table_names())
            ours = [t for t in _tables() if t.name in have]
            filled = [t.name for t in ours if connection.scalar(select(func.count()).select_from(t))]
    except MoveError:
        raise
    except Exception as e:
        raise MoveError(de_en("Keine Verbindung: {r}", "No connection: {r}", r=_reason(e)))
    finally:
        engine.dispose()
    return {"url": masked(url), "version": version, "existing": len(ours), "filled": filled,
            "too_long": too_long() if make_url(url).get_backend_name() == "postgresql" else []}


def _reason(error):
    """The first line of a driver's message, without the address (it may carry the password)."""
    message = str(getattr(error, "orig", None) or error).strip().splitlines()
    return message[0][:300] if message else type(error).__name__


def too_long(source=None):
    """[(table, column, limit, rows)]: texts longer than their column allows. SQLite stores them anyway,
    PostgreSQL refuses the whole copy; such rows need shortening first."""
    out = []
    with (source or db.engine).connect() as connection:
        have = set(inspect(connection).get_table_names())
        for table in _tables():
            if table.name not in have:
                continue
            for column in table.columns:
                limit = getattr(column.type, "length", None)
                if isinstance(column.type, String) and limit:
                    n = connection.scalar(select(func.count()).select_from(table)
                                          .where(func.length(column) > limit))
                    if n:
                        out.append((table.name, column.name, limit, n))
    return out


COPY_PREFIX = "askurious-copy-"
OLD_COPY_PREFIX = "queryous-copy-"  # copies left before the rename to Askurious are found as well
STALE_COPY_SECONDS = 24 * 3600  # a copy of the database left in the temp folder longer than any move takes


def stale_copies(now=None):
    """Snapshots of copies that ended hard (process killed, restart) and left a full copy of the database in the
    system's temp folder: [{"path", "size", "at"}] older than STALE_COPY_SECONDS (audit 2026-10-05, L-5)."""
    folder, now = tempfile.gettempdir(), now or time.time()
    out = []
    try:
        names = [n for n in os.listdir(folder) if n.startswith((COPY_PREFIX, OLD_COPY_PREFIX)) and n.endswith(".db")]
    except OSError:
        return []
    for name in names:
        path = os.path.join(folder, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if now - st.st_mtime > STALE_COPY_SECONDS:
            out.append({"path": path, "size": st.st_size, "at": _utc(st.st_mtime)})
    return out


def sweep_copies(now=None):
    """Remove the stale snapshots (see stale_copies): at the start of every copy and by the housekeeping. Never raises."""
    gone = 0
    for c in stale_copies(now):
        try:
            os.remove(c["path"])
            gone += 1
        except OSError:
            pass
    return gone


def old_sqlite(app):
    """While the app works on PostgreSQL: the SQLite file it worked on before the move, if it is still there
    (instance/app.db with its write-ahead log): {"path", "size", "at"}, else None. It holds every answer and
    address up to the move."""
    if _on_sqlite():
        return None
    path = os.path.join(app.instance_path, "app.db")
    parts = [p for p in (path, f"{path}-wal", f"{path}-shm") if os.path.exists(p)]
    if path not in parts:
        return None
    return {"path": path, "size": sum(os.path.getsize(p) for p in parts), "at": _utc(os.path.getmtime(path))}


def _on_sqlite():
    return db.engine.dialect.name == "sqlite"


def _utc(timestamp):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)


def remove_old_sqlite(app):
    """Delete the old SQLite file (old_sqlite) for good; only while the app works on PostgreSQL. Raises MoveError."""
    old = old_sqlite(app)
    if old is None:
        raise MoveError(de_en("Es liegt keine alte SQLite-Datei vor, oder die App arbeitet noch mit ihr.",
                              "There is no old SQLite file, or the app is still working on it."))
    for path in (f"{old['path']}-wal", f"{old['path']}-shm", old["path"]):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise MoveError(de_en("Die Datei ließ sich nicht löschen: {e}", "The file could not be deleted: {e}", e=e.strerror or e))
    return old


def _snapshot():
    """A copy of the SQLite file as of now (sqlite3's backup API: consistent while others write)."""
    source = db.engine.url.database
    sweep_copies()  # what a copy that ended hard left behind
    handle, path = tempfile.mkstemp(suffix=".db", prefix=COPY_PREFIX)
    os.close(handle)
    with sqlite3.connect(source) as old, sqlite3.connect(path) as new:
        old.backup(new)
    return path


def copy(url, report=None):
    """Copies every table of the app into the new database, in one transaction: afterwards it holds all rows or
    none. The new database must not hold rows of the app yet; missing tables are created. Returns
    {"rows": copied rows, "tables": number, "seconds": duration}. Raises MoveError."""
    report = report or (lambda share, text: None)
    url = target_url(url)
    started = time.monotonic()
    snapshot, source = None, db.engine
    if db.engine.dialect.name == "sqlite" and db.engine.url.database:
        report(0.01, "Momentaufnahme der Datenbank")
        snapshot = _snapshot()
        source = create_engine(f"sqlite:///{snapshot}")
    target = _engine(url)
    try:
        if make_url(url).get_backend_name() == "postgresql":
            overlong = too_long(source)
            if overlong:
                table, column, limit, n = overlong[0]
                raise MoveError(de_en("{n} Einträge in {table}.{column} sind länger als {limit} Zeichen; "
                                      "PostgreSQL nimmt sie nicht an (insgesamt {k} Spalten betroffen).",
                                      "{n} entries in {table}.{column} are longer than {limit} characters; "
                                      "PostgreSQL does not accept them ({k} columns affected in all).",
                                      n=n, table=table, column=column, limit=limit, k=len(overlong)))
        tables = _tables()
        with source.connect() as reading, target.begin() as writing:
            db.metadata.create_all(writing)
            # the copy has the schema of the code: it gets the version of the migrations (app/migrations.py)
            from alembic.migration import MigrationContext
            from alembic.script import ScriptDirectory

            from app import migrations

            MigrationContext.configure(writing).stamp(ScriptDirectory.from_config(migrations._config()), migrations.head())
            filled = [t.name for t in tables if writing.scalar(select(func.count()).select_from(t))]
            if filled:
                raise MoveError(de_en("Die neue Datenbank enthält schon Daten der App ({t}). Bitte eine leere Datenbank angeben.",
                                      "The new database already holds data of the app ({t}). Please give an empty database.",
                                      t=", ".join(filled[:5])))
            # Copy the source's revision counters too; target INSERT triggers must not count the copy again.
            from app.data_revision import install, uninstall

            uninstall(writing)
            have = set(inspect(reading).get_table_names())
            total = sum(reading.scalar(select(func.count()).select_from(t)) for t in tables if t.name in have) or 1
            done = 0
            for table in tables:
                if table.name not in have:
                    continue
                order = list(table.primary_key.columns) or list(table.columns)
                query, insert, renamed = _raw_json(table, writing.dialect.name)
                result = reading.execution_options(yield_per=BATCH).execute(query.order_by(*order))
                for rows in result.partitions():
                    batch = [{renamed.get(k, k): v for k, v in r._mapping.items()} for r in rows]
                    if renamed and writing.dialect.name == "postgresql":
                        _without_nan(batch, renamed.values())
                    writing.execute(insert, batch)
                    done += len(rows)
                    report(0.05 + 0.9 * done / total, f"{table.name}: {done} von {total} Zeilen")
            install(writing)
            if writing.dialect.name == "postgresql":
                report(0.97, "Zähler der Nummern")
                _counters(writing, tables)
    except MoveError:
        raise
    except Exception as e:
        raise MoveError(de_en("Kopieren abgebrochen, die neue Datenbank ist unverändert: {r}",
                              "Copying stopped, the new database is unchanged: {r}", r=_reason(e)))
    finally:
        target.dispose()
        if snapshot:
            source.dispose()
            try:
                os.remove(snapshot)
            except OSError:
                pass
    return {"rows": done, "tables": len([t for t in tables if t.name in have]),
            "seconds": round(time.monotonic() - started, 1)}


def _raw_json(table, dialect):
    """Select and insert for one table that carry JSON columns as their stored text: an empty value (SQL NULL)
    stays empty and JSON null stays JSON null; read as Python values both would come back as None and go in
    as JSON null. Returns (select, insert, {column: name of its parameter})."""
    columns, values, renamed = [], {}, {}
    for column in table.columns:
        if isinstance(column.type, JSON):
            columns.append(cast(column, Text).label(column.name))  # the stored text (psycopg would parse json)
            renamed[column.name] = f"raw_{column.name}"
            parameter = bindparam(renamed[column.name], type_=Text)
            values[column.name] = cast(parameter, column.type) if dialect == "postgresql" else parameter
        else:
            columns.append(column)
    return select(*columns), table.insert().values(**values) if values else table.insert(), renamed


def _without_nan(batch, names):
    """SQLite keeps NaN and Infinity in stored JSON, PostgreSQL refuses them: written as null (config.json_safe)."""
    import json

    from config import json_dumps

    for row in batch:
        for name in names:
            value = row.get(name)
            if value and ("NaN" in value or "Infinity" in value):
                try:
                    row[name] = json_dumps(json.loads(value))
                except ValueError:
                    pass  # not JSON at all: PostgreSQL names the row


def _counters(connection, tables):
    """PostgreSQL counts new ids with a sequence per table, which the copy (ids given) leaves at 1: set each to
    the highest id, or to the floor of deleted ids (models.IdFloor), whichever is higher."""
    from app.models import IdFloor

    floors = dict(connection.execute(select(IdFloor.name, IdFloor.highest)).all())
    for table in tables:
        keys = list(table.primary_key.columns)
        if len(keys) != 1 or keys[0].autoincrement is False or not isinstance(keys[0].type, Integer):
            continue
        sequence = connection.scalar(text("select pg_get_serial_sequence(:t, :c)"),
                                     {"t": table.name, "c": keys[0].name})
        if not sequence:
            continue
        highest = max(connection.scalar(select(func.max(keys[0]))) or 0, floors.get(table.name) or 0)
        if highest:
            connection.execute(text("select setval(:s, :n)"), {"s": sequence, "n": highest})
