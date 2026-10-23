"""Role rules of the security audit 2026-10-03, package 2 (S7, S8, S9, H9, H10): what "Bearbeiten" may do
next to "Admin" is the same on every way in (undo, dashboards, quality control, translation, designs)."""
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from app import boards
from app.extensions import db
from app.models import Board, BoardShare, Design, EditStep, Response, Survey, SurveyMember, User, utcnow
from tests.conftest import add

OTHER = "0000-0002-1825-0097"
E = "/surveys/1/editor"
D = "/surveys/1/dashboards"


def colleague(app, role):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role=role, position=1))
        db.session.commit()
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    c.post("/terms/", data={"accept": "1"})
    return c


def set_role(app, role):
    with app.app_context():
        db.session.scalar(db.select(SurveyMember).filter_by(survey_id=1, orcid=OTHER)).role = role
        db.session.commit()


def labels(app):
    with app.app_context():
        return [s.label for s in db.session.scalars(db.select(EditStep).filter_by(survey_id=1))]


def end_texts(app):
    with app.app_context():
        return db.session.get(Survey, 1).end_texts or {}


# --- S7: undo and redo of admin settings ----------------------------------------------------------------


def test_steps_with_admin_settings_need_admin_to_be_taken_back(app, client, survey):
    other = colleague(app, "admin")
    add(other, "text_input")                                                # item 2: a step "Bearbeiten" may take back
    other.post("/surveys/1/end-texts", data={"thanks_de": "Vielen Dank, wirklich!"})
    with app.app_context():
        needs = {s.label: s.need for s in db.session.scalars(db.select(EditStep).filter_by(survey_id=1))}
    assert sorted(needs.values()) == ["admin", "editor"]
    set_role(app, "editor")                                                 # the admin is now "Bearbeiten"
    assert "Texte der Endseiten geändert" in labels(app)
    assert "Texte der Endseiten geändert" not in other.get(f"{E}/").data.decode()   # the arrows do not offer the step
    with app.app_context():
        from app import undo

        steps = undo.history(db.session.get(Survey, 1), db.session.scalar(db.select(User).filter_by(orcid=OTHER)))
        assert [s.need for s in steps["undo"]] == ["editor"]
    # asking for exactly that step is refused, and the end text stays
    with app.app_context():
        admin_step = db.session.scalar(db.select(EditStep).filter_by(need="admin")).id
    html = other.post(f"{E}/undo", data={"to": admin_step}, follow_redirects=True).data.decode()
    assert "Dafür fehlen die Rechte" in html and end_texts(app)["de"]["thanks"] == "Vielen Dank, wirklich!"
    # the step before it (the question) can still be taken back
    other.post(f"{E}/undo")
    with app.app_context():
        assert db.session.get(Survey, 1).blocks[1].items == [] and end_texts(app)["de"]["thanks"] == "Vielen Dank, wirklich!"
    # back to "Admin": the step is offered again
    set_role(app, "admin")
    other.post(f"{E}/undo", data={"to": admin_step})
    assert (end_texts(app).get("de") or {}).get("thanks") != "Vielen Dank, wirklich!"


def test_only_admin_steps_left_says_why(app, client, survey):
    other = colleague(app, "admin")
    other.post("/surveys/1/end-texts", data={"thanks_de": "Danke"})
    set_role(app, "editor")
    assert "Dafür fehlen die Rechte" in other.post(f"{E}/undo", follow_redirects=True).data.decode()
    assert end_texts(app)["de"]["thanks"] == "Danke"


def test_which_steps_need_admin():
    """Derived from the routes: a settings section whose route is not open to "Bearbeiten" (EDIT_ENDPOINTS),
    and the key facts of the consent."""
    from app import edit_guard, surveys, undo

    assert set(surveys.SECTION_ENDPOINTS) == set(edit_guard.SETTINGS_FIELDS)
    assert {s for s in edit_guard.SETTINGS_FIELDS if surveys.section_role(s) == "admin"} == {"meta", "panel", "end_texts", "interview"}  # interview: the settings of an interview guide
    assert undo._need_of({"parts": {"settings:theme": [1, 2], "item:3": [1, 2]}}) == "editor"
    assert undo._need_of({"parts": {"settings:meta": [1, 2]}}) == "admin"
    assert undo._need_of({"parts": {"item:1": [1, 2]}, "page": "settings", "node": "consent-facts"}) == "admin"
    step = EditStep(label="alt", data={"parts": {"settings:panel": [1, 2]}})   # a step from before the column
    assert undo.need(step) == "admin"


# --- S9: end texts and contact on the translation page --------------------------------------------------


def test_translation_of_end_texts_is_for_admins(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.end_texts = {"de": {"thanks": "Danke fürs Mitmachen!"}}
        db.session.commit()
    other = colleague(app, "editor")
    html = other.get("/surveys/1/translation/?lang=en").data.decode()
    assert 'data-unit="end"' not in html and "data-tr-locked" in html and "nur die Rolle „Admin“" in html
    r = other.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "end", "path": "thanks", "text": "Gekapert"})
    assert r.status_code == 403 and "Rechte" in r.json["error"]
    assert "en" not in end_texts(app)
    # the questionnaire stays theirs, and the consent with it
    r = other.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "item:1", "path": "question", "text": "Consent"})
    assert r.status_code == 200
    # the admin translates the end texts as before
    client.post("/surveys/1/translation/fieldwork?lang=en", data={"unit": "end", "path": "thanks", "text": "Thanks!"})
    assert end_texts(app)["en"]["thanks"] == "Thanks!"
    assert 'data-unit="end"' in client.get("/surveys/1/translation/?lang=en").data.decode()


def test_ai_and_confirm_leave_the_admin_texts_to_admins(app, client, survey, monkeypatch):
    import json

    from app import ai, translations

    with app.app_context():
        s = db.session.get(Survey, 1)
        s.end_texts = {"de": {"thanks": "Danke fürs Mitmachen!"}}
        db.session.commit()

    def fake(instructions, prompt, purpose="text", item_type=None):
        entries = json.loads(prompt.split("Texts (JSON):\n", 1)[1])
        return json.dumps({e["id"]: "EN " + e["text"] for e in entries})

    monkeypatch.setattr(ai, "generate_text", fake)
    other = colleague(app, "editor")
    while other.post("/surveys/1/translation/ai?lang=en").json.get("left"):
        pass
    assert "en" not in end_texts(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [u.key for u, _ in translations.pending(s, "en", admin=False)] == []
        assert "end" in [u.key for u, _ in translations.pending(s, "en")]


# --- S8 and H10: what a dashboard link shows ------------------------------------------------------------


def _answer(app, n, source="real", answers=None):
    with app.app_context():
        now = utcnow()
        for _ in range(n):
            db.session.add(Response(survey_id=1, source=source, is_preview=source != "real", status=Response.COMPLETED,
                                    answers=answers or {"2": "1"}, started_at=now - timedelta(minutes=5), completed_at=now))
        db.session.commit()


def _choice(client, item=2):
    add(client, "single_choice")
    client.post(f"{E}/items/{item}", data=MultiDict([("code", f"q{item}"), ("question", f"Frage {item}?"), ("option_id", "a"),
                                                    ("option_code", "1"), ("option_label", "Ja")]))


def _link(client):
    client.post(f"{D}/1/link", data={"valid": "30"})
    with client.application.app_context():
        return db.session.scalars(db.select(BoardShare).order_by(BoardShare.id.desc())).first().token


def pool(app):
    with app.app_context():
        return db.session.get(Survey, 1).data_pool


def test_data_pool_is_fixed_for_editors_while_a_link_is_valid(app, client, survey):
    app.config["BOARD_CHECK_SECONDS"] = 0
    _choice(client)
    client.post(f"{D}/new", data={"template": "results"})
    other = colleague(app, "editor")
    other.post("/surveys/1/pool", data={"source": ["real", "dummy"]})      # no link yet: "Bearbeiten" may
    assert pool(app)["sources"] == ["real", "dummy"]
    _link(client)
    html = other.get("/surveys/1/results").data.decode()
    assert "gültige Links" in html and "disabled" in html
    other.post("/surveys/1/pool", data={"source": ["real", "test"], "all": "1"})
    assert pool(app) == {"sources": ["real", "dummy"], "all": False}
    other.post("/surveys/1/quality", data={"source": ["real", "test"], "all": "1"})   # nor through the old way
    assert pool(app) == {"sources": ["real", "dummy"], "all": False}
    # the page sends no pool (disabled boxes): the threshold is saved, the pool stays
    other.post("/surveys/1/quality", data={"speeder": "40"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.speeder_pct == 40 and s.data_pool == {"sources": ["real", "dummy"], "all": False}
    client.post("/surveys/1/quality", data={"source": ["real"]})           # the admin may
    assert pool(app)["sources"] == ["real"]


def test_links_never_count_test_runs(app, client, survey):
    app.config["BOARD_CHECK_SECONDS"] = 0
    _choice(client)
    client.post(f"{D}/new", data={"template": "results"})
    token = _link(client)
    _answer(app, 12)
    _answer(app, 30, source="test")
    client.post("/surveys/1/quality", data={"source": ["real", "test"]})
    page = app.test_client().get(f"/d/{token}").data.decode()
    assert "12</span> Teilnahmen" in page
    with app.app_context():
        assert boards.pool_of(db.session.get(Survey, 1))["sources"] == ["real"]
    client.post("/surveys/1/quality", data={"source": ["test"]})             # test runs alone: the real ones
    with app.app_context():
        assert boards.pool_of(db.session.get(Survey, 1))["sources"] == ["real"]
    assert "ohne Testdurchläufe" in client.get(f"{D}/1").data.decode()


# --- H9: designs only for who may create surveys -----------------------------------------------------------


def test_invited_accounts_make_no_designs(app, client, survey):
    client.post("/surveys/1/design/save", data={"name": "Lehrstuhl"})      # the admin (may create) keeps it
    other = colleague(app, "editor")                                        # invited only: may not create surveys
    html = other.get("/designs/").data.decode()
    assert "Eigene Designs legt an, wer eigene Befragungen anlegen darf" in html and "Neues Design" not in html
    other.post("/designs/new", data={"name": "Meins"})
    other.post("/surveys/1/design/save", data={"name": "Kopie"})
    with app.app_context():
        assert [d.name for d in db.session.scalars(db.select(Design))] == ["Lehrstuhl"]
    assert "Eigene Designs legt an" in other.get("/surveys/1/design").data.decode()
    assert "Eigene Designs legt an" in other.post("/designs/new", data={"name": "x"}, follow_redirects=True).data.decode()
