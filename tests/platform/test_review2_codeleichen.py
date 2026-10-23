"""Code review of 2026-10-03, package 6 "Codeleichen" (D-1 to D-13): what is left after the clean-up keeps working."""
import glob
import os
import warnings

from app.extensions import db
from app.models import SignInGrant, User

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ME = "0000-0000-0000-001X"       # site admin of the fixtures (tests/conftest.py)
OTHER = "0000-0002-1825-0097"


def test_every_python_file_compiles_without_warnings():
    """D-1: tools/build_plz_areas.py had real line breaks inside two strings; D-12: invalid escape sequences
    (a SyntaxWarning today, an error in later Python versions)."""
    files = [os.path.join(ROOT, "config.py"), os.path.join(ROOT, "passenger_wsgi.py")]
    for folder in ("app", "tools", "tests"):
        files += glob.glob(os.path.join(ROOT, folder, "**", "*.py"), recursive=True)
    files = [f for f in files if "vendor" not in f.split(os.sep)]
    assert any(f.endswith(os.path.join("tools", "build_plz_areas.py")) for f in files)
    for path in files:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            compile(source, path, "exec")


def test_number_macros_in_one_place(app):
    """D-10: num, one, signed and pct come from _macros.html; a value that shows as zero has no sign."""
    with app.app_context():
        m = app.jinja_env.get_template("_macros.html").module
        assert [str(m.num(v)) for v in (None, 2.5, -0.001, -1.234, 0)] == ["–", "2,50", "0,00", "−1,23", "0,00"]  # minus sign (V70)
        assert [str(m.num(v, 0)) for v in (3, 3.0, -0.2, 2.5)] == ["3", "3", "0", "3"]
        assert [str(m.one(v)) for v in (None, -0.04, 1.25)] == ["–", "0,0", "1,3"]
        assert [str(m.signed(v)) for v in (None, -0.001, 0.5, -1.5)] == ["–", "0,00", "+0,50", "−1,50"]
        assert [str(m.pct(v)) for v in (None, 0.125)] == ["–", "13 %"]


def test_one_reader_for_numbers_and_ai_answers(app):
    from app import ai, omv, rds

    assert rds.finite_number is omv.finite_number
    assert [omv.finite_number(v) for v in (None, "", "2", "nan", "inf", "x")] == [None, None, 2.0, None, None, None]
    assert ai.json_answer('Hier: {"a": 1} fertig') == {"a": 1}
    for bad in ("kein JSON", "[1, 2]", "{kaputt}"):
        try:
            ai.json_answer(bad)
        except ai.AIError:
            continue
        raise AssertionError(bad)


def test_the_grant_names_the_admin_who_made_the_account(app, client):
    """D-9: SignInGrant.granted_by_id was never set."""
    from app import accounts

    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ME))
        user = accounts.create_local("Lokale Person", "lokal@example.org", "", False, admin)
        db.session.commit()
        accounts.link_orcid(user, OTHER)
        db.session.commit()
        assert db.session.get(SignInGrant, user.id).granted_by_id == admin.id


def test_removed_routes_templates_and_settings(app):
    """D-5, D-6, D-13: the old live list page and the question route are gone (the list address still leads to
    "Meine Befragungen"); the settings the code reads stand in config.py."""
    from config import Config

    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/live/<code>/frage" not in rules and "/presenter/" in rules
    assert not os.path.exists(os.path.join(ROOT, "app", "templates", "live", "index.html"))
    assert Config.HB_SYNC is False and Config.TEMPO_MEDIAN_SECONDS == 60 and Config.AI_PRICES_FILE == ""
    assert not hasattr(Config, "DEFAULT_LANGUAGE") and not hasattr(Config, "LANGUAGES")


def test_the_old_live_list_address_still_works(app, client):
    r = client.get("/presenter/")
    assert r.status_code == 302 and "live=1" in r.headers["Location"]
