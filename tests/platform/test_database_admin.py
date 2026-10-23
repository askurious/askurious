"""Administration > Datenbank: status of the database, check and copy into another one (app/dbmove.py), and the
pieces that let the app run on PostgreSQL (config.database_url, sequences after given ids)."""
import os

import pytest
from sqlalchemy import create_engine, func, inspect, select, text

from app.extensions import db
from app.models import Response, Survey, User
from config import database_url
from tests.conftest import PG_SERVER, PG_URL, _pg_admin, _pg_url, add, fill_consent


def test_addresses_of_hosts_use_psycopg():
    assert database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert database_url("postgresql://u:p@h:5433/db") == "postgresql+psycopg://u:p@h:5433/db"
    assert database_url("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"
    assert database_url(" sqlite:///x.db ") == "sqlite:///x.db"
    assert database_url(None) == ""


def test_env_line_carries_the_password_as_the_address_needs_it():
    """The line is copied as it is: no special character of the password has to be looked up by hand."""
    from sqlalchemy.engine import make_url

    from app import dbmove

    secret = "p@ss:w/rd#1 %ä'\""
    line = dbmove.env_line(host="db.example.org", port="5433", name="queryous", user="q", password=secret)
    assert line.startswith("DATABASE_URL=postgresql://q:") and "@db.example.org:5433/queryous" in line
    assert " " not in line and "#" not in line  # nothing the .env reader could cut off
    url = make_url(database_url(line.split("=", 1)[1]))
    assert (url.password, url.host, url.port, url.database) == (secret, "db.example.org", 5433, "queryous")


def test_page_shows_the_database_and_only_to_admins(app, client):
    page = client.get("/admin/database").get_data(as_text=True)
    assert 'aria-current="page" href="/admin/database">Server' in page
    assert ("SQLite " if not PG_URL else "PostgreSQL ") in page
    assert "Einträge je Tabelle" in page
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Other")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/admin/database").status_code == 403


@pytest.mark.skipif(bool(PG_URL), reason="the page offers the move only while the app runs on SQLite")
def test_check_names_what_is_missing_and_never_echoes_the_password_in_errors(client):
    page = client.post("/admin/database", data={"action": "check", "host": "", "name": "x", "user": "u"}).get_data(as_text=True)
    assert "Bitte Server, Datenbank und Benutzer angeben." in page
    page = client.post("/admin/database", data={"action": "check", "host": "127.0.0.1", "port": "1", "name": "x",
                                                  "user": "u", "password": "geheim-123"}).get_data(as_text=True)
    assert "Keine Verbindung" in page or "Treiber fehlt" in page
    assert page.count("geheim-123") == 1  # only in the password field the admin typed into


def _filled(app, client):
    """A running survey with one participation; a JSON column left empty (SQL NULL) next to one holding null."""
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        db.session.add(Response(survey_id=survey.id, answers={"2": "weil"}, source="real", is_preview=False))
        db.session.commit()
        db.session.execute(text("update response set layout = null, params = null"))
        db.session.execute(text("update survey set panel = 'null'"))
        db.session.commit()


def _counts(engine):
    with engine.connect() as connection:
        have = set(inspect(connection).get_table_names())
        return {t.name: connection.scalar(select(func.count()).select_from(t))
                for t in db.metadata.sorted_tables if t.name in have}


def test_copy_into_an_empty_database_takes_every_row_and_keeps_empty_apart_from_null(app, client, survey, tmp_path):
    from app import dbmove

    _filled(app, client)
    target_url = f"sqlite:///{tmp_path / 'neu.db'}"
    with app.app_context():
        done = dbmove.copy(target_url)
        source = _counts(db.engine)
    target = create_engine(target_url)
    assert _counts(target) == source and done["rows"] == sum(source.values())
    with target.connect() as connection:
        assert connection.execute(text("select layout is null, params is null from response")).one() == (1, 1)
        assert connection.execute(text("select panel from survey")).scalar() == "null"
        assert connection.execute(text("select answers from response")).scalar().replace(" ", "") == '{"2":"weil"}'
        from app import migrations

        assert migrations.current(connection) == migrations.head()  # the copy carries the version of the migrations
        from app.data_revision import validate

        validate(connection)
        before = connection.scalar(text("SELECT sum(revision) FROM response_revision"))
        connection.execute(text("UPDATE response SET status='completed'"))
        assert connection.scalar(text("SELECT sum(revision) FROM response_revision")) > before
    target.dispose()
    with app.app_context():  # a second time: the new database is no longer empty
        with pytest.raises(dbmove.MoveError, match="schon Daten"):
            dbmove.copy(target_url)
        with pytest.raises(dbmove.MoveError, match="gerade arbeitet"):
            dbmove.copy(str(db.engine.url.render_as_string(hide_password=False)))


def test_texts_too_long_for_postgresql_are_named(app, client, survey):
    from app import dbmove

    _filled(app, client)
    with app.app_context():
        if db.engine.dialect.name != "sqlite":
            pytest.skip("only SQLite stores texts longer than their column")
        db.session.execute(text("update survey set title = :t"), {"t": "x" * 400})
        db.session.commit()
        assert ("survey", "title", 300, 1) in dbmove.too_long()


@pytest.mark.skipif(not PG_SERVER, reason="needs a PostgreSQL server: TEST_PG_SERVER or TEST_DATABASE_URL")
def test_copy_into_postgresql_and_go_on_there(app, client, survey, tmp_path):
    """The whole move: copy, then the app on the new database keeps the data and gives new ids above the old."""
    from app import create_app, dbmove
    from tests.conftest import _config

    _filled(app, client)
    with app.app_context():  # stored before the input check rejected it; PostgreSQL takes no NaN
        db.session.add(Response(survey_id=1, answers={"2": float("nan")}, source="real", is_preview=False))
        db.session.commit()
    name = f"askurious_move_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
    _pg_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", f"CREATE DATABASE {name}")
    url = _pg_url(name)
    with app.app_context():
        assert dbmove.check(url)["existing"] == 0
        done = dbmove.copy(url)
        source = _counts(db.engine)
        highest = db.session.scalar(select(func.max(User.id)))
    assert done["rows"] == sum(source.values())
    moved = create_app(_config(tmp_path, url))
    with moved.app_context():
        assert _counts(db.engine) == source
        user = User(orcid="0000-0001-5555-0001", name="Neu")
        db.session.add(user)
        db.session.commit()
        assert user.id > highest
        assert db.session.execute(text("select layout is null, params is null from response order by id")).first() == (True, True)
        assert db.session.scalars(select(Response.answers).order_by(Response.id)).all()[-1] == {"2": None}
        db.engine.dispose()
        db.engines.clear()
    _pg_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
