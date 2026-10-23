"""Stimulus sampling in loops over a table: several variants per condition (loops.add_variants) and the selection "one row
per level" (mode "level")."""
from collections import Counter

from werkzeug.datastructures import MultiDict

from app import duration, flow, loops
from app.extensions import db
from app.models import Block, Item, Response, Survey
from tests.conftest import CONSENT_OK, add
from tests.editor.test_loops import E, setup


def _loop(app):
    with app.app_context():
        return loops.config(db.session.get(Block, 2))


def _col(loop, name):
    return next(c for c in loop["columns"] if c["name"] == name)


def _form(loop, **settings):
    """The table form of the loop page with these selection settings."""
    form = [("name", loop["name"]), ("code", loop["code"]), ("order", loop["order"])]
    form += [(k, v) for k, v in settings.items() if k != "level_col"]
    form += [("level_col", c) for c in settings.get("level_col", [])]
    for c in loop["columns"]:
        form += [("col_id", c["id"]), ("col_name", c["name"])] + ([("col_hidden", c["id"])] if c["hidden"] else [])
    for r in loop["rows"]:
        form.append(("row_id", r["id"]))
        form += [(f"cell_{r['id']}_{c['id']}", r["values"][c["id"]]["de"]) for c in loop["columns"]]
    return MultiDict(form)


def _messages(app):
    from app import design

    with app.app_context():
        return [f.message for f in design.check(db.session.get(Survey, 1))]


def test_variants_per_condition_and_one_row_per_level(app, client, survey):
    url = setup(client)  # robot x setting: 4 rows, all rows, fixed order
    loop = _loop(app)
    page = client.get(url).data.decode()
    assert "Mehrere Varianten je Versuchsbedingung" in page and "Wells, G. L., &amp; Windschitl" in page
    r = client.post(url, data={"action": "variants", "variant_col": _col(loop, "robot")["id"], "variants": "3"},
                    follow_redirects=True)
    assert "2 Bedingungen mit Varianten; 2 Zeilen als Kopie angelegt" in r.data.decode()
    loop = _loop(app)
    variant = _col(loop, "variante")
    assert variant["hidden"] and loop["mode"] == "level" and loop["balanced"]
    assert loop["level_cols"] == [_col(loop, "robot")["id"]]
    robot = _col(loop, "robot")["id"]
    assert [(r["values"][robot]["de"], r["values"][variant["id"]]["de"]) for r in loop["rows"]] == [
        ("Pflegeroboter", "1"), ("Pflegeroboter", "2"), ("Pflegeroboter", "3"),
        ("Serviceroboter", "1"), ("Serviceroboter", "2"), ("Serviceroboter", "3")]
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert loops.shown_rows(s, loop) == 2
        assert loops.selection_text(s, loop) == "eine Zeile je Stufe von robot (2 von 6 Zeilen)"
        assert duration.block_seconds(db.session.get(Block, 2), "de") > 0
    page = client.get(url).data.decode()
    assert "Die Tabelle hat eine Spalte <code>variante</code>" in page and 'value="level" selected' in page
    assert f'name="level_col" value="{robot}" checked' in page  # else the next autosave of the form drops it
    again = client.post(url, data={"action": "variants", "variant_col": robot, "variants": "2"}, follow_redirects=True)
    assert "schon eine Spalte „variante“" in again.data.decode()
    assert "2 von 6 Zeilen (eine je Stufe)" in client.get("/surveys/1/flow/").data.decode()

    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    for _ in range(9):  # a participation is stored (and counts for balancing) with the first submitted page
        q = app.test_client()
        q.get(f"/s/{uid}")
        q.post(f"/s/{uid}", data=CONSENT_OK)
    with app.app_context():
        layouts = [r.layout for r in db.session.scalars(db.select(Response).filter_by(is_preview=False))]
        lid = loops.node_of(db.session.get(Block, 2))["id"]
    by_row = {r["id"]: r["values"][robot]["de"] for r in loop["rows"]}
    for layout in layouts:  # one Pflegeroboter and one Serviceroboter each, in table order
        assert [by_row[r] for r in layout["loops"][lid]] == ["Pflegeroboter", "Serviceroboter"]
    shown = Counter(r for layout in layouts for r in layout["loops"][lid])
    assert sorted(shown.values()) == [3] * 6  # balanced: every variant equally often
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "eine Zeile je Stufe von robot (2 von 6 Zeilen); balanciert" in book


def test_level_mode_set_by_hand_and_checked(app, client, survey):
    url = setup(client)
    loop = _loop(app)
    client.post(url, data=_form(loop, mode="level", balanced="1"))
    assert any("„eine Zeile je Stufe“ braucht eine Spalte" in m for m in _messages(app))
    client.post(url, data=_form(loop, mode="level", balanced="1", level_col=[_col(loop, "setting")["id"]]))
    loop = _loop(app)
    assert loop["mode"] == "level" and loop["level_cols"] == [_col(loop, "setting")["id"]]
    with app.app_context():
        assert loops.shown_rows(db.session.get(Survey, 1), loop) == 2
    assert not any("Stufe" in m for m in _messages(app))
    # both columns: every combination is a level of its own, one row each
    client.post(url, data=_form(loop, mode="level", level_col=[c["id"] for c in loop["columns"]]))
    assert any("hat nur eine Zeile; das ist dasselbe wie „alle Zeilen“" in m for m in _messages(app))


def test_an_image_has_to_differ_between_the_variants(app, client, survey):
    url = setup(client)
    loop = _loop(app)
    client.post(url, data={"action": "variants", "variant_col": _col(loop, "robot")["id"], "variants": "2"})
    add(client, "image")                                  # 3
    with app.app_context():
        item = db.session.get(Item, 3)
        item.config = {**item.config, "source": "ai", "prompt": "A {{robot}} in a living room"}
        db.session.commit()
    assert any("Das Bild ist für alle Zeilen einer Stufe gleich" in m and "{{variante}}" in m for m in _messages(app))
    with app.app_context():
        item = db.session.get(Item, 3)
        item.config = {**item.config, "prompt": "A {{robot}} in a living room, variant {{variante}}"}
        db.session.commit()
        assert len(item.kind.required_keys(item)) == 4  # an image per robot and variant
    assert not any("einer Stufe gleich" in m for m in _messages(app))


def test_the_survey_file_keeps_the_level_columns(app, client, survey):
    from tests.exports.test_transfer import _file, _upload

    url = setup(client)
    loop = _loop(app)
    client.post(url, data={"action": "variants", "variant_col": _col(loop, "setting")["id"], "variants": "2"})
    data = _file(client)
    data["survey"]["title"] = "Kopie"
    _upload(client, data)
    with app.app_context():
        node = next(n for n, _, _ in flow.walk(flow.tree(db.session.get(Survey, 2))) if n["t"] == "loop")
        cfg = loops.config_node(node)
        assert cfg["mode"] == "level" and [c["name"] for c in loops.level_columns(cfg)] == ["setting"]
