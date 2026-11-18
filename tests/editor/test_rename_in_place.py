"""The pencil beside a variable name on the questionnaire page (editor.rename_item, app.js data-code-edit): the name
changes in place, with the check and the followers of the element's page (elements._rename is the one code for both),
under the same conditions (a draft, its source language, the right to edit), as one step that undo takes back."""
from pathlib import Path
import inspect

from app.editor import elements
from app.extensions import db
from app.models import Item, Survey, SurveyMember
from tests.conftest import add
from tests.editor.test_piping import build

E = "/surveys/1/editor"
OTHER = "0000-0000-0000-002X"


def _rename(client, item_id, code, lang=None):
    return client.post(f"{E}/items/{item_id}/code" + (f"?lang={lang}" if lang else ""), data={"code": code})


def _two_languages(app):
    with app.app_context():
        db.session.get(Survey, 1).languages = ["de", "en"]
        db.session.commit()


def _code(app, item_id):
    with app.app_context():
        return db.session.get(Item, item_id).code


def test_the_questionnaire_page_offers_the_pencil_per_question(app, client, survey):
    add(client, "text_input")
    add(client, "page_break")
    page = client.get(f"{E}/").data.decode()
    assert page.count("data-code-edit ") == 1  # the question; not the consent in its pinned block, not the page break
    # its address is the name's link + "/code" (app.js), the link of the name right before the pencil
    assert f'href="{E}/items/2?lang=de" class="item-code" draggable="false">' in page
    js = (Path(__file__).resolve().parents[2] / "app/static/app.js").read_text(encoding="utf-8")
    assert r'url.pathname.replace(/\/$/, "") + "/code"' in js
    assert "Variablennamen „Q1“ ändern" in page


def test_a_new_name_is_saved_and_undone_as_one_step(app, client, survey):
    add(client, "text_input")
    r = _rename(client, 2, " alter ")
    assert r.get_json() == {"done": True, "code": "alter"} and _code(app, 2) == "alter"
    assert "„Q1“ in „alter“ umbenannt" in client.get(f"{E}/").data.decode()  # the label of ↶
    client.post(f"{E}/undo")
    assert _code(app, 2) == "Q1"


def test_the_same_name_again_changes_nothing(app, client, survey):
    add(client, "text_input")
    before = client.get(f"{E}/").data.decode()
    assert _rename(client, 2, "Q1").get_json() == {"done": True, "code": "Q1"}
    assert "umbenannt" not in client.get(f"{E}/").data.decode() and "umbenannt" not in before


def test_what_speaks_against_a_name_comes_back_for_the_field(app, client, survey):
    add(client, "text_input")
    add(client, "text_input")
    assert "schon vergeben" in _rename(client, 3, "Q1").get_json()["problem"]
    for bad in ("1alter", "mein alter", "größe", "", "a" * 65):
        assert "Buchstabe am Anfang" in _rename(client, 3, bad).get_json()["problem"], bad
    assert _code(app, 3) == "Q2"
    assert _rename(client, 3, "a" * 64).get_json()["done"]


def test_the_check_is_the_one_of_the_elements_page(app, client, survey):
    """Both ways call elements._rename, and neither checks a name by itself."""
    for view in (elements.edit_item, elements.rename_item):
        source = inspect.getsource(view)
        assert "_rename(survey, item, " in source and "CODE_RE" not in source and "taken_codes" not in source
    add(client, "text_input")
    add(client, "text_input")
    for name in ("Q1", "1alter"):
        page = client.post(f"{E}/items/3", data={"code": name, "question": "?"}, follow_redirects=True).data.decode()
        assert _rename(client, 3, name).get_json()["problem"] in page


def test_the_texts_that_show_the_answer_follow(app, client, survey):
    build(app, client)
    assert _rename(client, 2, "brand").get_json()["done"]
    with app.app_context():
        assert db.session.get(Item, 5).texts["de"]["question"] == "Zu {{brand}}:"
        assert db.session.get(Item, 7).texts["de"]["question"].endswith("{{ brand }} nutzen.")
    assert "zeigen die Antwort weiter" in client.get(f"{E}/").data.decode()
    client.post(f"{E}/undo")
    with app.app_context():
        assert db.session.get(Item, 2).code == "marke" and db.session.get(Item, 5).texts["de"]["question"] == "Zu {{marke}}:"


def test_only_a_draft_in_its_source_language_and_only_who_may_edit(app, client, survey):
    add(client, "text_input")
    _two_languages(app)
    assert "problem" in _rename(client, 2, "alter", lang="en").get_json()  # the page itself is always in the source language
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="viewer", position=1))
        db.session.commit()
    viewer = app.test_client()
    viewer.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    viewer.post("/terms/", data={"accept": "1"})
    assert "data-code-edit " not in viewer.get(f"{E}/").data.decode()
    assert _rename(viewer, 2, "alter").status_code in (302, 403) and _code(app, 2) == "Q1"
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    assert "data-code-edit " not in client.get(f"{E}/").data.decode()
    assert "Nur Textkorrekturen" in _rename(client, 2, "alter").get_json()["problem"]
    assert _code(app, 2) == "Q1"
    assert _rename(client, 99, "alter").status_code == 404
