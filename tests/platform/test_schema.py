"""A database from before the migrations (app/migrations.py) that is older than the code: columns the models have
and the tables lack are added at the start (app/extensions.py, add_missing_columns), then it is stamped."""
import sqlite3

from sqlalchemy import Column, Integer, String

from app.extensions import add_missing_columns, db
from tests.conftest import _config, needs_sqlite


def _columns(path, table):
    con = sqlite3.connect(path)
    try:
        return {row[1]: row for row in con.execute(f'PRAGMA table_info("{table}")')}
    finally:
        con.close()


def _without_column(path, table, column):
    """The column gone, and the database as it was before the migrations (no alembic_version)."""
    con = sqlite3.connect(path)
    con.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    con.execute("DROP TABLE alembic_version")
    con.commit()
    con.close()


def test_a_database_from_before_a_new_column_runs(tmp_path):
    """The deploy of 2026-10-01: survey.title_translations was new, every page answered with an error."""
    from app import create_app

    first = create_app(_config(tmp_path))
    c = first.test_client()
    c.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    c.post("/terms/", data={"accept": "1"})
    c.post("/surveys/new", data={"title": "Bestand", "languages": ["de", "en"], "default_language": "de"})
    with first.app_context():
        db.engine.dispose()
    _without_column(tmp_path / "test.db", "survey", "title_translations")

    app = create_app(_config(tmp_path))
    assert "title_translations" in _columns(tmp_path / "test.db", "survey")
    c = app.test_client()
    c.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    r = c.get("/dashboard/")
    assert r.status_code == 200 and "Bestand" in r.get_data(as_text=True)
    with app.app_context():
        db.engine.dispose()


@needs_sqlite
def test_required_columns_get_their_plain_default_others_are_left(app, caplog):
    table = db.metadata.tables["feedback"]
    plain = Column("extra_plain", String(10), nullable=False, default="o'k")
    flag = Column("extra_flag", Integer, nullable=False, default=True)
    computed = Column("extra_computed", String(10), nullable=False, default=lambda: "x")
    path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    try:
        for column in (plain, flag, computed):
            table.append_column(column)
        with app.app_context():
            add_missing_columns(app)
            add_missing_columns(app)  # a second start changes nothing
            db.engine.dispose()
        columns = _columns(path, "feedback")
        assert columns["extra_plain"][3] == 1 and columns["extra_plain"][4] == "'o''k'"
        assert columns["extra_flag"][4] == "1"
        assert "extra_computed" not in columns
        assert "feedback.extra_computed is missing" in caplog.text
    finally:
        for column in (plain, flag, computed):
            table._columns.remove(column)


@needs_sqlite
def test_surveys_from_before_the_speeder_rule_keep_the_duration(tmp_path):
    """speeder_rule: a survey that existed when the column came keeps the rule it ran with ("duration"); a new
    survey gets the default rule ("", the relative speed index when it records page times)."""
    from app import create_app
    from app.models import Survey

    first = create_app(_config(tmp_path))
    c = first.test_client()
    c.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    c.post("/terms/", data={"accept": "1"})
    c.post("/surveys/new", data={"title": "Bestand", "languages": ["de"], "default_language": "de"})
    with first.app_context():
        db.engine.dispose()
    _without_column(tmp_path / "test.db", "survey", "speeder_rule")

    app = create_app(_config(tmp_path))
    c = app.test_client()
    c.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    c.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        rules = {s.title: s.speeder_rule for s in db.session.scalars(db.select(Survey))}
        db.engine.dispose()
    assert rules == {"Bestand": "duration", "Neu": ""}
