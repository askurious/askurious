"""The element editor of a draft saves by itself (concept "Automatisch speichern und Rückgängig", stage 2): the page
sends its form after every change and gets JSON back; app.js shows it in place of "Speichern"."""
from html.parser import HTMLParser

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import EditStep, Item, Survey, SurveyMember
from tests.conftest import add, fill_consent

OTHER = "0000-0002-1825-0097"
E = "/surveys/1/editor"


class _Form(HTMLParser):
    """The fields a browser would send for the element form of the page (form[data-live-form])."""

    def __init__(self):
        super().__init__()
        self.fields, self.inside, self.area, self.select = [], False, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.inside = "data-live-form" in a
        if not self.inside or "disabled" in a:
            return
        if tag == "input" and a.get("name"):
            kind = a.get("type", "text")
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self.fields.append((a["name"], a.get("value", "on")))
            elif kind not in ("file", "submit", "button"):
                self.fields.append((a["name"], a.get("value", "")))
        elif tag == "textarea" and a.get("name"):
            self.area = [a["name"], ""]
        elif tag == "select" and a.get("name"):
            self.select = [a["name"], None, None]  # name, selected value, first value
        elif tag == "option" and self.select:
            value = a.get("value", "")
            if self.select[2] is None:
                self.select[2] = value
            if "selected" in a:
                self.select[1] = value

    def handle_data(self, data):
        if self.area is not None:
            self.area[1] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "textarea" and self.area is not None:
            self.fields.append((self.area[0], self.area[1].lstrip("\n")))
            self.area = None
        elif tag == "select" and self.select is not None:
            name, picked, first = self.select
            if picked is not None or first is not None:
                self.fields.append((name, picked if picked is not None else first))
            self.select = None


def form_of(client, item_id, lang="de"):
    parser = _Form()
    parser.feed(client.get(f"{E}/items/{item_id}?lang={lang}").data.decode())
    return MultiDict(parser.fields)


def auto(client, item_id, form, **extra):
    """Send the form as the page does. Like the page, `form` then carries the fingerprint of the saved state."""
    data = MultiDict(form)
    data.update({"autosave": "1", **extra})
    r = client.post(f"{E}/items/{item_id}", data=data, headers={"X-Requested-With": "fetch"})
    if r.is_json and r.get_json().get("status") == "saved":
        form["base"] = r.get_json()["base"]
    return r


def state(app, item_id=2):
    with app.app_context():
        item = db.session.get(Item, item_id)
        return item.code, item.answer_mode, item.config, item.texts


def labels(app):
    with app.app_context():
        return [s.label for s in db.session.scalars(db.select(EditStep).order_by(EditStep.id.desc()))]


def test_no_save_button_and_in_the_field_saved_when_a_field_is_left(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(f"{E}/items/2", data={"code": "Q1", "question": "Frage"})
    page = client.get(f"{E}/items/2").data.decode()
    assert "data-autosave" in page and ">Fertig</a>" in page and "Änderungen werden automatisch gespeichert." in page
    assert "Speichern und schließen" not in page
    assert '<noscript><button class="button" name="after" value="back">Speichern</button></noscript>' in page
    client.post("/surveys/1/start", data={})
    page = client.get(f"{E}/items/2").data.decode()
    # in the field it saves by itself as well, a text when its field is left (UI audit 2026-10-04, R17)
    assert "data-autosave data-autosave-on-leave" in page and "Speichern und schließen" not in page
    assert 'name="opened_running" value="1"' in page
    # a page opened before the start that still saves by itself: the text correction is kept and the page gets JSON
    r = auto(client, 2, MultiDict({"question": "Korrektur"}))
    d = r.get_json()
    assert d["status"] == "saved" and "gestartet" in d["notes"][0]
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Korrektur"


def test_saves_answers_with_json_and_leaves_no_message(app, client, survey):
    add(client, "text_input")
    form = form_of(client, 2)
    form["question"] = "Wie alt sind Sie?"
    r = auto(client, 2, form, what="Fragetext", merge="question#0@a1")
    d = r.get_json()
    assert d["status"] == "saved" and d["code"] == "Q1" and d["code_problem"] is None and not d["stale"]
    assert d["undo"] == "„Q1“: Fragetext geändert" and d["redo"] == "" and len(d["at"]) == 5
    assert state(app)[3]["de"]["question"] == "Wie alt sind Sie?"
    page = client.get(f"{E}/items/2").data.decode()
    assert "Gespeichert." not in page                                      # nothing waits for the next page
    assert f'name="base" value="{d["base"]}"' in page                      # the fingerprint the next save builds on


def test_one_visit_to_a_field_is_one_step(app, client, survey):
    add(client, "text_input")
    form = form_of(client, 2)
    for text in ("Wie", "Wie alt", "Wie alt sind Sie?"):                   # pauses while typing, then leaving the field
        form["question"] = text
        auto(client, 2, form, what="Fragetext", merge="question#0@a1")
    assert labels(app) == ["„Q1“: Fragetext geändert", "„Q1“ hinzugefügt (Freitext)"]
    form["answer_mode"] = "required"
    auto(client, 2, form, what="Antwort erzwingen")                        # a click: its own step
    form["question"] = "Ihr Alter?"
    auto(client, 2, form, what="Fragetext", merge="question#0@a2")         # a second visit: its own step
    assert labels(app)[:2] == ["„Q1“: Fragetext geändert", "„Q1“: Antwort erzwingen geändert"] and len(labels(app)) == 4
    auto(client, 2, form, what="Fragetext", merge="question#0@a2")         # nothing new: no step
    assert len(labels(app)) == 4
    client.post(f"{E}/undo")
    assert state(app)[3]["de"]["question"] == "Wie alt sind Sie?" and state(app)[1] == "required"
    client.post(f"{E}/undo")
    assert state(app)[1] == "optional"


def test_invalid_variable_name_stays_out_the_rest_is_saved(app, client, survey):
    add(client, "text_input"), add(client, "text_input")                   # Q1, Q2
    form = form_of(client, 3)
    form["code"], form["question"] = "Q1", "Zweite Frage"
    d = auto(client, 3, form).get_json()
    assert d["status"] == "saved" and "schon vergeben" in d["code_problem"] and d["code"] == "Q2"
    assert state(app, 3)[0] == "Q2" and state(app, 3)[3]["de"]["question"] == "Zweite Frage"
    form["code"] = "zweite"
    d = auto(client, 3, form, what="Variablenname").get_json()
    assert d["code_problem"] is None and d["code"] == "zweite"


def test_someone_else_saved_in_between(app, client, survey):
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="admin", position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    mine, theirs = form_of(client, 2), form_of(other, 2)
    theirs["question"] = "Von der Kollegin"
    assert auto(other, 2, theirs).get_json()["status"] == "saved"
    mine["question"] = "Von mir"
    d = auto(client, 2, mine).get_json()
    assert d["status"] == "conflict" and d["who"] == "Kollegin" and d["base"] == theirs["base"] != mine["base"]
    assert state(app)[3]["de"]["question"] == "Von der Kollegin"           # nothing overwritten
    mine["base"] = d["base"]                                               # "Meine Änderung behalten"
    assert auto(client, 2, mine).get_json()["status"] == "saved"
    assert state(app)[3]["de"]["question"] == "Von mir"


def test_panels_below_the_form_are_stale_after_a_change(app, client, survey):
    add(client, "conjoint")
    form = form_of(client, 2)
    auto(client, 2, form)
    assert auto(client, 2, form).get_json()["stale"] is False              # nothing changed
    form["question"] = "Welches Angebot wählen Sie?"
    assert auto(client, 2, form).get_json()["stale"] is True


def _types():
    from app import items

    # the Leitfrage belongs to interview guides (tests/interview/test_interview.py)
    return [t for t in items.REGISTRY if t not in ("consent", "page_break") and not getattr(items.REGISTRY[t], "interview_only", False)
             and not getattr(items.REGISTRY[t], "experimental", None)]


@pytest.mark.parametrize("kind", _types())
def test_saving_the_page_again_changes_nothing(app, client, survey, kind):
    """Every element type: the form as the page shows it, sent twice. The second save finds nothing to change:
    no row gets a new number, nothing doubles, and no step is made for it."""
    add(client, kind)
    first = form_of(client, 2)
    assert auto(client, 2, first).get_json()["status"] == "saved"
    one, steps = state(app), len(labels(app))
    d = auto(client, 2, first).get_json()
    assert d["status"] == "saved" and state(app) == one and len(labels(app)) == steps
    again = form_of(client, 2)                                             # and the page drawn from the saved state
    assert auto(client, 2, again).get_json()["status"] == "saved"
    assert state(app) == one and len(labels(app)) == steps


# --- stage 3: the forms of the flow page and the loop table (app/autosave.py) -----------------------------


def post_auto(client, url, data, **extra):
    return client.post(url, data={**data, "autosave": "1", **extra}, headers={"X-Requested-With": "fetch"})


def rand_node(app):
    with app.app_context():
        return next(n for n in db.session.get(Survey, 1).flow["children"] if n["t"] == "rand")


def test_flow_element_saves_by_itself(app, client, survey):
    import re

    client.post(f"{E}/flow/add", data={"kind": "rand"})
    rid = rand_node(app)["id"]
    page = client.get("/surveys/1/flow/").data.decode()
    form = page.split(f'/flow/{rid}/update')[1].split("</form>")[0]
    assert "data-autosave-form" in form and "data-autosave-status" in form and "data-dirty-guard" not in form
    assert "<noscript><button" in form and form.count("Speichern</button>") == 1      # only without JavaScript
    assert f'data-live="head-{rid}"' in page
    base = re.search(r'name="base" value="(\w+)"', form).group(1)
    url, data = f"{E}/flow/{rid}/update", {"base": base, "name": "Var", "code": "var", "k": "1", "balanced": "1"}
    d = post_auto(client, url, data, what="Name", merge="name#0@x1").get_json()
    assert d["status"] == "saved" and d["base"] != base and d["undo"] == "Ablauf: Randomisierung „Randomisierung 1“ geändert"
    assert rand_node(app)["name"] == "Var"
    d2 = post_auto(client, url, {**data, "base": d["base"], "name": "Varianten"}, what="Name", merge="name#0@x1").get_json()
    assert d2["status"] == "saved" and rand_node(app)["name"] == "Varianten"
    # one visit to the field, one step
    assert labels(app) == ["Ablauf: Randomisierung „Var“ geändert", "Ablauf: Randomisierung „Randomisierung 1“ hinzugefügt"]
    page = client.get("/surveys/1/flow/").data.decode()
    assert "Gespeichert." not in page                                      # no message waits for the next page
    # a save from an outdated page changes nothing and says who was there
    d3 = post_auto(client, url, {**data, "base": base, "name": "Alt"}).get_json()
    assert d3["status"] == "conflict" and d3["base"] == d2["base"] and rand_node(app)["name"] == "Varianten"


def test_a_form_the_route_refuses_says_why_and_saves_nothing(app, client, survey):
    client.post(f"{E}/flow/add", data={"kind": "rand"})
    rid = rand_node(app)["id"]
    d = post_auto(client, f"{E}/flow/{rid}/update", {"name": "X", "code": "1 falsch", "k": "1"}).get_json()
    assert d["status"] == "invalid" and d["message"] and rand_node(app)["name"] != "X"
    assert d["message"] not in client.get("/surveys/1/flow/").data.decode()   # said in the form, not on the next page


def test_loop_table_saves_by_itself(app, client, survey):
    add(client, "text_input")
    client.post(f"{E}/blocks/2/loop")
    with app.app_context():
        nid = next(n for n in db.session.get(Survey, 1).flow["children"] if n["t"] == "loop")["id"]
    url = f"/surveys/1/flow/loop/{nid}"
    page = client.get(url).data.decode()
    assert "data-autosave-form" in page and 'data-live="loop-placeholders"' in page
    data = MultiDict([("name", "Roboter"), ("col_id", "c1aaaa"), ("col_name", "robot"), ("row_id", "r1aaaa"),
                      ("cell_r1aaaa_c1aaaa", "humanoid"), ("mode", "all"), ("k", "1"), ("order", "random"), ("code", "loop1"),
                      ("autosave", "1")])
    for _ in range(2):                                                     # sent twice: the numbers from the page stay
        d = client.post(url, data=data, headers={"X-Requested-With": "fetch"}).get_json()
        assert d["status"] == "saved"
        with app.app_context():
            node = next(n for n in db.session.get(Survey, 1).flow["children"] if n["t"] == "loop")
            assert [c["id"] for c in node["columns"]] == ["c1aaaa"] and [r["id"] for r in node["rows"]] == ["r1aaaa"]
    assert labels(app)[0] == "Schleife „Roboter“ bearbeitet"
    assert "<code" in client.get(url).data.decode().split('data-live="loop-placeholders"')[1].split("</div>")[0]


def test_new_end_trigger_keeps_the_number_from_the_page(app, client, survey):
    client.post(f"{E}/flow/add", data={"kind": "end"})
    with app.app_context():
        nid = next(n for n in db.session.get(Survey, 1).flow["children"] if n["t"] == "end")["id"]
    data = {"case": "0", "case0_id": "cabc123", "case0_when": "screenout", "case0_text_de": "Vielen Dank"}
    for _ in range(2):
        assert post_auto(client, f"{E}/flow/{nid}/update", data).get_json()["status"] == "saved"
        with app.app_context():
            node = next(n for n in db.session.get(Survey, 1).flow["children"] if n["t"] == "end")
            assert [c["id"] for c in node["cases"]] == ["cabc123"]


# --- stage 3, part 2: quotas are a part of the history, their form saves by itself -------------------------

Q = "/surveys/1/flow/quotas"


def quotas_of(app):
    with app.app_context():
        return [(q.id, q.name, [(c.id, c.name, c.target) for c in q.cells]) for q in db.session.get(Survey, 1).quotas]


def gender(client):
    add(client, "single_choice")                                           # item 2
    client.post(f"{E}/items/2", data=MultiDict([("code", "gender"), ("question", "Geschlecht"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "männlich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "weiblich")]))


def test_quota_form_saves_by_itself_and_new_rows_keep_their_number(app, client, survey):
    gender(client)
    client.post(Q + "/add")
    page = client.get(Q + "/").data.decode()
    form = page.split('id="quota-1"')[1].split("</form>")[0]
    assert "data-autosave-form" in form and "data-autosave-status" in form and 'data-live="quota-head-1"' in page
    data = MultiDict({"name": "Geschlecht", "code": "q1", "cells": "2", "mode": "hard", "c0_id": "", "c0_k0_item": "2",
                      "c0_target": "50", "c1_id": "", "c1_k0_item": "2", "c1_target": "50", "autosave": "1"})
    data.setlist("c0_k0_codes", ["1"]), data.setlist("c1_k0_codes", ["2"])
    d = client.post(Q + "/1", data=data, headers={"X-Requested-With": "fetch"}).get_json()
    assert d["status"] == "saved" and d["fill"] == {"c0_id": "1", "c1_id": "2"} and d["undo"] == "Quote „Geschlecht“ geändert"
    first = quotas_of(app)
    assert first == [(1, "Geschlecht", [(1, "männlich", 50), (2, "weiblich", 50)])]
    data["c0_id"], data["c1_id"] = "1", "2"                                # as the page does with `fill`
    data["c0_target"] = "60"
    assert client.post(Q + "/1", data=data, headers={"X-Requested-With": "fetch"}).get_json()["status"] == "saved"
    assert quotas_of(app) == [(1, "Geschlecht", [(1, "männlich", 60), (2, "weiblich", 50)])]   # the same rows, changed
    data["code"] = "1 falsch"
    d = client.post(Q + "/1", data=data, headers={"X-Requested-With": "fetch"}).get_json()
    assert d["status"] == "invalid" and d["message"].startswith("Nicht gespeichert: Code der Quote")
    client.post(f"{E}/undo")                                               # the target back to 50
    assert quotas_of(app) == first


def test_deleted_quota_comes_back_with_its_places_in_the_flow(app, client, survey):
    gender(client)
    client.post(Q + "/add")
    data = MultiDict({"name": "Geschlecht", "code": "q1", "cells": "1", "mode": "hard", "c0_k0_item": "2", "c0_target": "50"})
    data.setlist("c0_k0_codes", ["1"])
    client.post(Q + "/1", data=data)
    client.post(f"{E}/flow/add", data={"kind": "quota"})                   # a calculation that names the quota
    with app.app_context():
        flow_before = db.session.get(Survey, 1).flow
        assert any(n["t"] == "quota" and n["quotas"] == [1] for n in flow_before["children"])
    before = quotas_of(app)
    page = client.get(Q + "/").data.decode()
    assert "data-confirm" not in page.split("/quotas/1/delete")[1].split("</form>")[0]   # not asked about
    html = client.post(Q + "/1/delete", follow_redirects=True).data.decode()
    assert "Quote „Geschlecht“ gelöscht." in html and 'class="flash-undo"' in html and quotas_of(app) == []
    r = client.post(f"{E}/undo")
    assert r.headers["Location"].endswith("/flow/quotas/?lang=de#quota-1")
    with app.app_context():
        assert quotas_of(app) == before and db.session.get(Survey, 1).flow == flow_before
    client.post(Q + "/add")                                                # a new quota never takes a number that was in use
    client.post(Q + "/2/delete"), client.post(Q + "/add")
    assert [q[0] for q in quotas_of(app)] == [1, 3]


def test_quota_assistant_is_one_step(app, client, survey):
    before = layout_of(app), quotas_of(app)
    with app.app_context():
        flow_before = db.session.get(Survey, 1).flow
    client.post(Q + "/assistant", data={"n": "1000"})
    assert len(quotas_of(app)) == 3 and layout_of(app) != before[0]
    assert labels(app)[0] == "Quotenassistent: Bevölkerung Deutschland angelegt"
    client.post(f"{E}/undo")
    assert (layout_of(app), quotas_of(app)) == before
    with app.app_context():
        flow = db.session.get(Survey, 1).flow
        assert [n for n in flow["children"] if n["t"] != "split"] == [n for n in (flow_before or flow)["children"] if n["t"] != "split"]
    client.post(f"{E}/redo")
    assert len(quotas_of(app)) == 3


def layout_of(app):
    with app.app_context():
        return [[i.code for i in b.items] for b in db.session.get(Survey, 1).blocks]


# --- stage 4: settings, look, quality control ---------------------------------------------------------------


def test_settings_of_a_draft_save_by_themselves_and_are_steps(app, client, survey):
    import re

    page = client.get("/surveys/1").data.decode()
    assert page.count("data-autosave-form") == 7 and "data-dirty-guard" not in page   # the study plan has its own page   # the links' form is on "Datenerhebung" and 'data-live="survey-title"' in page
    form = page.split('/surveys/1/meta"')[1].split("</form>")[0]
    base = re.search(r'name="base" value="(\w+)"', form).group(1)
    data = {"base": base, "part": "befragung", "title": "Neuer Titel", "languages": ["de", "en"], "default_language": "de", "allow_back": "1"}
    d = post_auto(client, "/surveys/1/meta", data, what="Titel", merge="title#0@s1").get_json()
    assert d["status"] == "saved" and d["base"] != base and d["undo"] == "Befragung: Titel geändert"
    with app.app_context():
        assert db.session.get(Survey, 1).title == "Neuer Titel"
    d = post_auto(client, "/surveys/1/meta", {**data, "base": d["base"], "title": ""}).get_json()   # refused: the reason, nothing saved
    assert d["status"] == "invalid" and d["message"]
    r = client.post(f"{E}/undo")
    assert r.headers["Location"].endswith("/surveys/1")
    with app.app_context():
        assert db.session.get(Survey, 1).title == "Test"
    d = post_auto(client, "/surveys/1/end-texts", {"thanks_de": "Vielen Dank!"}).get_json()
    assert d["status"] == "saved" and d["undo"] == "Texte der Endseiten geändert"
    assert client.post(f"{E}/undo").headers["Location"].endswith("/surveys/1#end-texts")


def test_running_survey_saves_by_itself_texts_when_a_field_is_left(app, client, survey):
    """Ralf, 2026-10-03: the settings save by themselves also in the field; there a text is saved when its field is
    left (data-autosave-on-leave), never half typed, since participants see it at once."""
    fill_consent(client)
    add(client, "text_input")
    client.post(f"{E}/items/2", data={"code": "Q1", "question": "Frage"})
    client.post("/surveys/1/start", data={})
    page = client.get("/surveys/1").data.decode()
    assert page.count("data-autosave-form data-autosave-on-leave") == 6 and "data-dirty-guard" not in page
    assert client.get("/surveys/1/researchdesign").data.decode().count("data-autosave-form data-autosave-on-leave") == 1
    assert ">Speichern</button></noscript>" in page                         # without JavaScript the button stays
    d = post_auto(client, "/surveys/1/end-texts", {"thanks_de": "Danke!"}).get_json()
    assert d["status"] == "saved"
    with app.app_context():
        assert db.session.get(Survey, 1).end_texts["de"]["thanks"] == "Danke!"
    quality = client.get("/surveys/1/quality").data.decode()
    assert 'data-autosave-form' in quality and 'data-autosave-bar="quality-form"' in quality
    d = post_auto(client, "/surveys/1/quality", {"source": ["real", "dummy"]}).get_json()
    assert d["status"] == "saved" and d["undo"] == "Qualitätssicherung geändert"   # in the field too (UI audit R16)
    with app.app_context():
        assert db.session.get(Survey, 1).data_pool["sources"] == ["real", "dummy"]


def test_look_of_a_draft_saves_by_itself(app, client, survey):
    page = client.get("/surveys/1/design").data.decode()
    assert "data-autosave-form" in page and "Auf Standard zurücksetzen" in page


# --- stage 5: pages outside of surveys (no history there) ---------------------------------------------------


def test_group_and_account_details_save_by_themselves(app, client):
    client.post("/groups/new", data={"name": "Lehrstuhl"})
    page = client.get("/groups/1").data.decode()
    form = page.split('/groups/1/edit"')[1].split("</form>")[0]
    assert "data-autosave-form" in form and "data-autosave-status" in form and 'data-live="group-name"' in page
    d = post_auto(client, "/groups/1/edit", {"name": "Institut", "description": "Arbeitsgruppe"}).get_json()
    assert d["status"] == "saved" and d["undo"] == "" and d["redo"] == ""
    assert "<h1 data-live=\"group-name\">Institut</h1>" in client.get("/groups/1").data.decode()
    d = post_auto(client, "/groups/1/edit", {"name": ""}).get_json()
    assert d["status"] == "invalid" and "Bitte einen Namen" in d["message"]
    assert "Bitte einen Namen" not in client.get("/groups/1").data.decode()   # said in the form, not on the next page
    # the account page keeps its buttons where something is done, not saved: password, signing out, deleting
    account = client.get("/account/").data.decode()
    assert "Abmelden" in account



# --- the dashboard designer uses the same module --------------------------------------------------------------


def test_dashboard_designer_saves_through_the_shared_module(app, client, survey):
    client.post("/surveys/1/dashboards/new", data={"template": "empty"})
    page = client.get("/surveys/1/dashboards/1").data.decode()
    assert 'id="board-form"' in page and "data-autosave-form data-autosave-redraw" in page
    assert 'data-autosave-bar="board-form"' in page and "data-board-saved" not in page
    d = post_auto(client, "/surveys/1/dashboards/1", {"name": "Bericht für die Stadt", "order": "", "known": ""}).get_json()
    assert d["status"] == "saved" and 'id="board-form"' in d["form"]   # the name itself stands outside the form (right column)
    # without the page's own saving the route answers as any form does: it leads back to the designer
    r = client.post("/surveys/1/dashboards/1", data={"name": "Anders", "order": "", "known": ""})
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboards/1")


def test_quality_control_and_dashboards_can_be_taken_back(app, client, survey):
    """UI/UX audit 2026-10-03 (U10): in a draft the page "Qualitätssicherung" and the dashboard designer save by
    themselves and keep steps for ↶ like the other pages; a deleted dashboard comes back (without its links)."""
    from app.models import Board

    page = client.get("/surveys/1/quality").data.decode()
    assert "data-undo" in page
    post_auto(client, "/surveys/1/quality", {"source": ["real", "dummy"], "speeder": "40"})
    with app.app_context():
        assert db.session.get(Survey, 1).speeder_pct == 40
    client.post(f"{E}/undo")
    with app.app_context():
        assert db.session.get(Survey, 1).speeder_pct != 40
    client.post("/surveys/1/dashboards/new", data={"template": "field", "name": "Mein Board"})
    with app.app_context():
        board_id = db.session.scalar(db.select(Board.id))
    post_auto(client, f"/surveys/1/dashboards/{board_id}", {"name": "Umbenannt"})
    with app.app_context():
        assert db.session.get(Board, board_id).name == "Umbenannt"
    client.post(f"{E}/undo")
    with app.app_context():
        assert db.session.get(Board, board_id).name == "Mein Board"
    client.post(f"/surveys/1/dashboards/{board_id}/delete")
    with app.app_context():
        assert db.session.get(Board, board_id) is None
    r = client.post(f"{E}/undo")
    assert f"/surveys/1/dashboards/{board_id}" in r.headers["Location"]                  # lands in the designer
    with app.app_context():
        assert db.session.get(Board, board_id).name == "Mein Board"
