"""Several members editing at once: a save based on an outdated page is not applied (app/edit_guard.py)."""
import re

from app.extensions import db
from app.models import Item, Survey, SurveyMember
from tests.conftest import add

OTHER = "0000-0002-1825-0097"
E = "/surveys/1/editor"


def base(html):
    return re.search(r'name="base" value="(\w+)"', html).group(1)


def colleague(app):
    """A second client, logged in as an editor of survey 1."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="admin", position=1))
        db.session.commit()
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    c.post("/terms/", data={"accept": "1"})
    return c


def question(app, item_id=2):
    with app.app_context():
        return db.session.get(Item, item_id).texts["de"]["question"]


def test_item_saved_in_between_is_not_overwritten(app, client, survey):
    add(client, "text_input")                                              # item 2
    other = colleague(app)
    mine = base(client.get(f"{E}/items/2").data.decode())                  # both open the element
    theirs = base(other.get(f"{E}/items/2").data.decode())
    assert mine == theirs
    other.post(f"{E}/items/2", data={"base": theirs, "code": "Q1", "question": "Von der Kollegin"})
    assert question(app) == "Von der Kollegin"

    r = client.post(f"{E}/items/2", data={"base": mine, "code": "Q1", "question": "Meine Fassung"})
    html = r.data.decode()
    assert r.status_code == 200 and "Nicht gespeichert" in html and "Kollegin" in html
    assert "Meine Fassung" in html                                         # the inputs are still there
    assert question(app) == "Von der Kollegin"                             # nothing saved

    # saving again from the page shown now deliberately replaces the colleague's version
    client.post(f"{E}/items/2", data={"base": base(html), "code": "Q1", "question": "Meine Fassung"})
    assert question(app) == "Meine Fassung"


def test_own_saves_one_after_another_and_forms_without_base(app, client, survey):
    add(client, "text_input")
    for text in ("eins", "zwei"):
        b = base(client.get(f"{E}/items/2").data.decode())
        client.post(f"{E}/items/2", data={"base": b, "code": "Q1", "question": text})
        assert question(app) == text
    client.post(f"{E}/items/2", data={"code": "Q1", "question": "ohne"})    # wizard, scripts: no check
    assert question(app) == "ohne"


def test_translation_saved_in_between_counts_as_change(app, client, survey):
    add(client, "text_input")
    other = colleague(app)
    mine = base(client.get(f"{E}/items/2?lang=en").data.decode())
    other.post(f"{E}/items/2?lang=en", data={"base": mine, "question": "Colleague"})
    r = client.post(f"{E}/items/2?lang=en", data={"base": mine, "question": "Mine"})
    assert "Nicht gespeichert" in r.data.decode()
    with app.app_context():
        assert db.session.get(Item, 2).texts["en"]["question"] == "Colleague"


def test_settings_conflict(app, client, survey):
    other = colleague(app)
    mine = base(client.get("/surveys/1").data.decode())
    theirs = base(other.get("/surveys/1").data.decode())
    other.post("/surveys/1/meta", data={"base": theirs, "title": "Titel der Kollegin", "languages": ["de", "en"],
                                        "default_language": "de"})
    r = client.post("/surveys/1/meta", data={"base": mine, "title": "Mein Titel", "languages": ["de", "en"],
                                             "default_language": "de"})
    html = r.data.decode()
    assert "Nicht gespeichert" in html and "Mein Titel" in html
    with app.app_context():
        assert db.session.get(Survey, 1).title == "Titel der Kollegin"


def test_end_texts_are_a_section_of_their_own(app, client, survey):
    other = colleague(app)
    page = client.get("/surveys/1").data.decode()
    end_base = re.findall(r'name="base" value="(\w+)"', page)[-1]        # the last form: end texts
    other.post("/surveys/1/meta", data={"base": base(page), "title": "Neu", "languages": ["de", "en"],
                                        "default_language": "de"})
    client.post("/surveys/1/end-texts", data={"base": end_base, "thanks_de": "Danke!"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.title == "Neu" and s.end_texts["de"]["thanks"] == "Danke!"


def test_loop_table_conflict(app, client, survey):
    url = client.post(f"{E}/blocks/2/loop").headers["Location"].split("?")[0]
    other = colleague(app)
    mine = base(client.get(url).data.decode())
    other.post(url, data={"base": mine, "name": "Kollegin-Schleife", "col_id": "c1", "col_name": "robot"})
    r = client.post(url, data={"base": mine, "name": "Meine Schleife", "col_id": "c1", "col_name": "robot"})
    html = r.data.decode()
    assert "Nicht gespeichert" in html and "Meine Schleife" in html
    assert "Kollegin-Schleife" in client.get(url).data.decode()


def test_flow_node_conflict(app, client, survey):
    from app import flow

    client.post(f"{E}/flow/add", data={"kind": "rand"})
    with app.app_context():
        node = next(n for n, _, _ in flow.walk(flow.tree(db.session.get(Survey, 1))) if n["t"] == "rand")
    nid = node["id"]
    mine = base(client.get("/surveys/1/flow/").data.decode())             # the only settings form
    other = colleague(app)
    other.post(f"{E}/flow/{nid}/update", data={"base": mine, "code": "cond_k", "name": "Kollegin"})
    r = client.post(f"{E}/flow/{nid}/update", data={"base": mine, "code": "cond_m", "name": "Ich"},
                    follow_redirects=True)
    assert "Nicht gespeichert" in r.data.decode()
    with app.app_context():
        node, _ = flow.find(flow.tree(db.session.get(Survey, 1)), nid)
        assert node["code"] == "cond_k"


def test_presence(app, client, survey):
    add(client, "text_input")
    other = colleague(app)
    assert "Kollegin" not in client.get(f"{E}/items/2").data.decode()
    r = other.post(f"{E}/presence", data={"key": "item:2"})
    assert r.get_json() == {"others": []}
    html = client.get(f"{E}/items/2").data.decode()
    assert re.search(r'data-presence[^>]*data-key="item:2"', html) and "Kollegin" in html
    assert client.post(f"{E}/presence", data={"key": "item:2"}).get_json() == {"others": ["Kollegin"]}
    other.post(f"{E}/presence", data={"key": "item:2", "leave": "1"})
    assert client.post(f"{E}/presence", data={"key": "item:2"}).get_json() == {"others": []}


def test_beacon_after_own_save_under_way_is_saved(app, client, survey):
    """Code review 2026-10-15, I1: leaving the page while the own autosave is still under way sends a beacon with
    the fingerprint from before that save (`after_own`); it builds on the own save instead of being dropped."""
    add(client, "text_input")
    mine = base(client.get(f"{E}/items/2").data.decode())
    client.post(f"{E}/items/2", data={"base": mine, "code": "Q1", "question": "Das letzte", "autosave": "1"})
    r = client.post(f"{E}/items/2", data={"base": mine, "code": "Q1", "question": "Das letzte Wort",
                                          "autosave": "1", "after_own": "1"})
    assert r.get_json()["status"] == "saved"
    assert question(app) == "Das letzte Wort"


def test_beacon_after_own_does_not_hide_a_colleagues_save(app, client, survey):
    """I1: the marker only covers the own save; a colleague's save in between stays a conflict."""
    add(client, "text_input")
    other = colleague(app)
    mine = base(client.get(f"{E}/items/2").data.decode())
    other.post(f"{E}/items/2", data={"base": mine, "code": "Q1", "question": "Von der Kollegin"})
    r = client.post(f"{E}/items/2", data={"base": mine, "code": "Q1", "question": "Meins", "autosave": "1",
                                          "after_own": "1"})
    assert r.get_json()["status"] == "conflict"
    assert question(app) == "Von der Kollegin"

    # own save first, then the colleague's (from the fresh page), then the own beacon: still a conflict
    fresh = base(client.get(f"{E}/items/2").data.decode())
    client.post(f"{E}/items/2", data={"base": fresh, "code": "Q1", "question": "Meins 1", "autosave": "1"})
    other.post(f"{E}/items/2", data={"base": base(other.get(f"{E}/items/2").data.decode()), "code": "Q1",
                                     "question": "Kollegin 2"})
    r = client.post(f"{E}/items/2", data={"base": fresh, "code": "Q1", "question": "Meins 2", "autosave": "1",
                                          "after_own": "1"})
    assert r.get_json()["status"] == "conflict"
    assert question(app) == "Kollegin 2"


def test_study_plan_conflict(app, client, survey):
    """Code review 2026-10-15, I3: the page "Studienplan" carries a fingerprint like the settings; two people no longer
    overwrite each other's hypotheses and exclusion rules."""
    other = colleague(app)
    mine = base(client.get("/surveys/1/researchdesign").data.decode())
    theirs = base(other.get("/surveys/1/researchdesign").data.decode())
    assert mine == theirs
    r = other.post("/surveys/1/researchdesign", data={"base": theirs, "research_question": "Frage der Kollegin",
                                                      "autosave": "1"}, headers={"X-Requested-With": "fetch"})
    assert r.get_json()["status"] == "saved"
    r = client.post("/surveys/1/researchdesign", data={"base": mine, "research_question": "Meine Frage", "autosave": "1"},
                    headers={"X-Requested-With": "fetch"})
    assert r.get_json()["status"] == "conflict" and r.get_json()["who"] == "Kollegin"
    with app.app_context():
        assert db.session.get(Survey, 1).research_question == "Frage der Kollegin"
    html = client.post("/surveys/1/researchdesign", data={"base": mine, "research_question": "Meine Frage"}).data.decode()
    assert "Nicht gespeichert" in html and "Meine Frage" in html                       # the inputs are still there
    with app.app_context():
        assert db.session.get(Survey, 1).research_question == "Frage der Kollegin"
    assert client.post("/surveys/1/researchdesign", data={"base": base(html), "research_question": "Meine Frage"}).status_code == 302
    with app.app_context():
        assert db.session.get(Survey, 1).research_question == "Meine Frage"


def test_the_beacon_names_a_save_under_way():
    """I1: the page marks the beacon it sends while its own save is still under way."""
    from pathlib import Path

    js = (Path(__file__).parents[2] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    last = js[js.index("last: function () {"):]
    assert 'if (saving) data.append("after_own", "1");' in last[:900]
