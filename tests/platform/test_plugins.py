"""Add-on modules (app/plugins.py): an installed package adds a feature, a button and hides its own surveys."""
import types

from flask import Blueprint

from app import experimental, plugins
from conftest import experiments_on


def _fake_module(app):
    bp = Blueprint("demo_plugin", __name__)

    @bp.route("/demo-plugin/")
    def index():
        return "demo"

    app.register_blueprint(bp)
    plugins.add_feature("demo_plugin", "Demo-Modul", "Ein Modul zum Testen.", "Demo module", "A module for testing.")
    plugins.add_button("demo_plugin", "demo_plugin.index", "Demo-Analyse", "Demo analysis")
    plugins.add_tool("demo_plugin", "demo_plugin.index", "Demo-Werkzeug", "Demo tool")
    plugins.hide_surveys(lambda s: s.title == "Versteckt")


def _isolate(monkeypatch):
    monkeypatch.setattr(experimental, "FEATURES", list(experimental.FEATURES))
    monkeypatch.setattr(experimental, "KEYS", list(experimental.KEYS))
    monkeypatch.setattr(experimental, "FEATURES_EN", dict(experimental.FEATURES_EN))
    monkeypatch.setattr(plugins, "BUTTONS", [])
    monkeypatch.setattr(plugins, "TOOLS", [])
    monkeypatch.setattr(plugins, "HIDDEN", [])
    monkeypatch.setattr(plugins, "LOADED", [])


def test_module_feature_button_and_hidden_surveys(app, client, survey, monkeypatch):
    _isolate(monkeypatch)
    ep = types.SimpleNamespace(name="demo", load=lambda: types.SimpleNamespace(init_app=_fake_module))
    monkeypatch.setattr(plugins, "entry_points", lambda group: [ep] if group == plugins.GROUP else [])
    monkeypatch.setattr(app, "_got_first_request", False)  # create_app loads modules before any request
    plugins.load(app)
    assert plugins.LOADED == ["demo"] and "demo_plugin" in experimental.KEYS
    assert client.get("/demo-plugin/").data == b"demo"
    page = client.get("/dashboard/").data.decode()
    assert "Demo-Analyse" not in page and "Demo-Werkzeug" not in page  # off for everyone until switched on
    experiments_on(client, "demo_plugin")
    page = client.get("/dashboard/").data.decode()
    assert "Demo-Analyse" in page and "Demo-Werkzeug" in page  # the button and the entry under "Werkzeuge"

    from app.extensions import db
    from app.models import Survey

    with app.app_context():
        db.session.get(Survey, survey).title = "Versteckt"
        db.session.commit()
    assert "Versteckt" not in client.get("/dashboard/").data.decode()


def test_broken_module_is_left_out(app, monkeypatch):
    _isolate(monkeypatch)

    def boom():
        raise RuntimeError("kaputt")

    ep = types.SimpleNamespace(name="broken", load=boom)
    monkeypatch.setattr(plugins, "entry_points", lambda group: [ep])
    plugins.load(app)
    assert plugins.LOADED == []


def test_module_view_shows_fewer_tabs(app, client, survey, monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(plugins, "VIEWS", [])
    plugins.add_view(lambda s: s.title == "Analyse", ("results", "data"), "dashboard.index", "Zur Liste", "To the list",
                     "Analyse", "Analysis")
    app.jinja_env.globals["plugin_view"] = plugins.view
    from app.extensions import db
    from app.models import Survey

    page = client.get(f"/surveys/{survey}/results").data.decode()
    assert "Fragebogen" in page and "Zur Liste" not in page
    with app.app_context():
        db.session.get(Survey, survey).title = "Analyse"
        db.session.commit()
    page = client.get(f"/surveys/{survey}/results").data.decode()
    nav = page.split('class="survey-tabs"', 1)[1].split("</nav>", 1)[0]
    assert "Ergebnisse" in nav and "Export" in nav and "Fragebogen" not in nav and "Datenerhebung" not in nav
    assert "Zur Liste" in page and "Vorschau" not in page.split('class="survey-tabs"', 1)[0]
    assert "Regeln in der Qualitätssicherung" not in page and "Gewichtung einrichten" not in page  # tabs not shown



def test_module_view_without_fieldwork_has_a_short_basis(app, client, survey, monkeypatch):
    """A survey a module keeps without fieldwork of its own (no tab "quality", e.g. an imported analysis): the panel
    "Grundlage und Ansicht" shows the number and only the choice that can differ there (Ralf, 2026-10-22)."""
    from app.extensions import db
    from app.models import Response, Survey, new_pid

    _isolate(monkeypatch)
    monkeypatch.setattr(plugins, "VIEWS", [])
    plugins.add_view(lambda s: s.title == "Analyse", ("results", "boards", "data"), "dashboard.index", "Zur Liste",
                     "To the list")
    app.jinja_env.globals["plugin_view"] = plugins.view
    with app.app_context():
        s = db.session.get(Survey, survey)
        for status in ("completed", "completed", "excluded"):
            s.responses.append(Response(pid=new_pid(s.code), source="real", is_preview=False, language="de",
                                        status=status, answers={}))
        db.session.commit()
    page = client.get(f"/surveys/{survey}/results?go=1").data.decode()
    assert "Testdurchläufe (" in page and "keine Gruppen" in page  # an ordinary survey: the whole panel
    with app.app_context():
        db.session.get(Survey, survey).title = "Analyse"
        db.session.commit()
    page = client.get(f"/surveys/{survey}/results?go=1").data.decode()
    basis = page.split('id="basis-line"', 1)[1].split('class="res-fold-row"', 1)[0]
    assert "Testdurchläufe" not in basis and "Dummy-Daten" not in basis and "keine Gruppen" not in basis
    assert "ungewichtet" not in basis and "Verwendete Daten" not in page and "Nur diese Ansicht" not in basis
    assert "auch die nicht abgeschlossene Teilnahme einbeziehen" in basis
    client.post(f"/surveys/{survey}/pool", data={"source": "real", "all": "1", "back": f"/surveys/{survey}/results"})
    with app.app_context():
        assert db.session.get(Survey, survey).data_pool["all"] is True

def test_imported_surveys_stay_out_of_the_statistics(app, client, monkeypatch):
    """Analyses an add-on module imports (the conjoint import) are no surveys of the platform: the dashboard,
    the monitoring feed and the totals leave them and their participations out (Ralf, 2026-10-22)."""
    from app import admin_stats
    from app.extensions import db
    from app.models import Response
    from tests.accounts.test_org_survey_dashboard import _survey

    _isolate(monkeypatch)
    monkeypatch.setattr(plugins, "STATS_EXCLUDED", [])
    _survey(app, "Eigene Studie", None, 4)
    _survey(app, "Import", None, 9)
    with app.app_context():
        before = admin_stats.compute("30")
        plugins.exclude_from_statistics(lambda Survey: Survey.title == "Import")
        after = admin_stats.compute("30")
        assert before["parts"]["completed"] == 13 and after["parts"]["completed"] == 4
        assert before["surveys"]["running"] - after["surveys"]["running"] == 1
        real = db.session.scalar(db.select(db.func.count(Response.id)).where(*admin_stats._real()))
        assert real == 4  # neither the imported participations nor the test runs


def test_a_condition_on_an_empty_column_keeps_the_other_surveys(app, client, monkeypatch):
    """The module's marker sits in Survey.data_pool, which most surveys leave empty: NOT (NULL LIKE ...) is NULL, so
    without coalesce every survey without a data pool fell out of the count (RWTH, 2026-10-22: 24 of 58)."""
    from app import admin_stats
    from app.extensions import db
    from app.models import Survey
    from tests.accounts.test_org_survey_dashboard import _survey

    _isolate(monkeypatch)
    monkeypatch.setattr(plugins, "STATS_EXCLUDED", [])
    _survey(app, "Ohne Datenpool", None, 2)
    imported = _survey(app, "Import", None, 5)
    with app.app_context():
        db.session.get(Survey, imported).data_pool = {"sources": ["real"], "origin": "demo"}
        db.session.commit()
        plugins.exclude_from_statistics(
            lambda S: db.cast(S.data_pool, db.String).like('%"origin": "demo"%'))
        figures = admin_stats.compute("30")
    assert figures["surveys"]["all"] == 1 and figures["parts"]["completed"] == 2
