"""Own multidimensional scales: statements of a Likert matrix and pairs of a semantic differential get a subscale in
the editor, behind the box "Unterdimensionen zuordnen" (Philipp, 2026-10-07)."""
from werkzeug.datastructures import MultiDict

from app import scales
from app.extensions import db
from app.models import Item
from tests.conftest import add


def likert_form(rows, ticked=True, marker=True):
    """The editor's form of a Likert matrix (item 2): rows [(id, text, subscale)]."""
    form = [("code", "big"), ("question", "Ich bin jemand, der …"), ("points", "5"), ("anchors", "agree"),
            ("labeling", "full")]
    if marker:
        form += [("subscales_shown", "1"), ("checks_shown", "1")]
    if ticked:
        form.append(("subscales", "1"))
    for sid, text, sub in rows:
        form += [("stmt_id", sid), ("stmt_code", ""), ("stmt_text", text), ("stmt_subscale", sub)]
    return MultiDict(form)


ROWS = [("s1", "gesellig", "Extraversion"), ("s2", "zurückhaltend", " Extraversion "), ("s3", "gründlich", "Gewissenhaftigkeit"),
        ("s4", "bequem", "Gewissenhaftigkeit"), ("s5", "fantasievoll", "Offenheit"), ("s6", "pünktlich", "")]


def test_the_box_shows_the_subscale_column(app, client, survey):
    add(client, "likert")                                                                  # 2
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="subscales" value="1" data-toggle-cols="rows2" data-col="sub"' in html and 'off-sub off-check" id="rows2"' in html
    assert 'name="subscales_shown"' in html and 'name="stmt_subscale"' in html
    client.post("/surveys/1/editor/items/2", data=likert_form(ROWS))
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'data-toggle-cols="rows2" data-col="sub" checked' in html and 'off-sub' not in html
    assert '<option value="Extraversion">' in html and 'value="Gewissenhaftigkeit"' in html


def test_own_subscales_are_stored_and_checked(app, client, survey):
    add(client, "likert")                                                                  # 2
    client.post("/surveys/1/editor/items/2", data=likert_form(ROWS))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [s.get("subscale") for s in item.config["statements"]] == \
            ["Extraversion", "Extraversion", "Gewissenhaftigkeit", "Gewissenhaftigkeit", "Offenheit", None]
        assert scales.subscale_names(item) == ["Extraversion", "Gewissenhaftigkeit", "Offenheit"]
        warnings = item.kind.check_warnings(item)
        assert any("„Offenheit“ hat nur eine Aussage" in w for w in warnings)
        assert any("1 Aussage(n) ohne Subskala (big_6)" in w for w in warnings)
        # the scale score of a person per subscale, as the results page, the export and the feedback compute it
        answer = {"s1": 5, "s2": 3, "s3": 2, "s4": 2, "s5": 4, "s6": 1}
        assert scales.answer_score(item, answer, "Extraversion") == 4.0
        assert scales.answer_score(item, answer, "Gewissenhaftigkeit") == 2.0


def test_unticking_removes_the_subscales_and_other_forms_keep_them(app, client, survey):
    add(client, "likert")                                                                  # 2
    client.post("/surveys/1/editor/items/2", data=likert_form(ROWS))
    client.post("/surveys/1/editor/items/2", data=likert_form([(s, t, "") for s, t, _ in ROWS], marker=False, ticked=False))
    with app.app_context():                                                   # a form without the editor's marker
        assert scales.subscale_names(db.session.get(Item, 2)) == ["Extraversion", "Gewissenhaftigkeit", "Offenheit"]
    client.post("/surveys/1/editor/items/2", data=likert_form(ROWS, ticked=False))           # the box unticked
    with app.app_context():
        item = db.session.get(Item, 2)
        assert scales.subscale_names(item) == [] and item.kind.check_warnings(item) == []


def test_semantic_differential_gets_subscales_too(app, client, survey):
    add(client, "semantic")                                                                # 2
    form = [("code", "sd"), ("question", "Der Roboter ist …"), ("points", "7"), ("subscales_shown", "1"), ("subscales", "1")]
    for pid, left, right, sub in (("p1", "kalt", "warm", "Wärme"), ("p2", "unfreundlich", "freundlich", "Wärme"),
                                  ("p3", "unfähig", "fähig", "Kompetenz")):
        form += [("pair_id", pid), ("pair_left", left), ("pair_right", right), ("pair_code", ""), ("pair_subscale", sub)]
    client.post("/surveys/1/editor/items/2", data=MultiDict(form))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert scales.subscale_names(item) == ["Wärme", "Kompetenz"]
        assert any("„Kompetenz“ hat nur ein Gegensatzpaar" in w for w in item.kind.check_warnings(item))
    assert 'data-toggle-cols="rows2" data-col="sub" checked' in client.get("/surveys/1/editor/items/2").data.decode()


def test_a_locked_construct_shows_its_subscales_read_only(app, client, survey):
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:bfi10"})   # 2
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'data-toggle-cols="rows2" data-col="sub" checked disabled' in html and 'name="subscales_shown"' not in html
    assert 'name="stmt_subscale"' in html and 'value="E"' in html
    with app.app_context():
        item = db.session.get(Item, 2)
        assert scales.subscale_names(item) == list("EACNO") and item.kind.check_warnings(item) == []


def test_the_attention_check_column_is_optional_too(app, client, survey):
    """The column "Prüfung" shows with the box "Aufmerksamkeitsprüfung einbauen"; unticked, the check rows go."""
    add(client, "likert")                                                                  # 2
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="checks" value="1" data-toggle-cols="rows2" data-col="check" data-reveal="checkinfo2"' in html
    assert 'off-check" id="rows2"' in html and '<div id="checkinfo2" hidden>' in html
    rows = [("s1", "gesellig", ""), ("s2", "", ""), ("s3", "gründlich", "")]
    form = likert_form(rows, ticked=False)
    form.add("checks", "1")
    form.add("stmt_check_s2", "4")
    client.post("/surveys/1/editor/items/2", data=form)
    with app.app_context():
        assert [s.get("check") for s in db.session.get(Item, 2).config["statements"]] == [None, 4, None]
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'data-col="check" data-reveal="checkinfo2" checked' in html and "off-check" not in html
    client.post("/surveys/1/editor/items/2", data=likert_form([("s1", "gesellig", ""), ("s2", "Bitte wählen", ""),
                                                               ("s3", "gründlich", "")], ticked=False))   # unticked
    with app.app_context():
        assert [s["id"] for s in db.session.get(Item, 2).config["statements"]] == ["s1", "s3"]
