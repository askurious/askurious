"""Numbers and labels of a randomizer's conditions in the data (concept "Befragungsmaterial mit sichtbarer Randomisierung",
package F): a code per condition, 0/1 columns where several are drawn, fixed at the start."""
import json

from app import export, flow, lifecycle, transfer
from app.extensions import db
from app.models import Block, Item, Survey
from tests.conftest import fill_consent


def _flow(app, client, k=1, n=3):
    fill_consent(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        ids = []
        for i in range(n):
            block = Block(survey=s, position=10 + i, titles={s.default_language: f"Fassung {i + 1}"}, show_title=True)
            db.session.add(block)
            db.session.flush()
            ids.append(block.id)
        rest = [c for c in flow.tree(s).get("children", []) if c.get("block") not in ids]
        s.flow = {"t": "root", "children": rest + [
            {"t": "rand", "id": "r1", "code": "framing", "name": "Rahmung", "k": k, "balanced": True, "shuffle": False,
             "children": [{"t": "block", "block": b} for b in ids]}]}
        db.session.commit()
    return ids


def _node(survey):
    return flow.walk_nodes(survey, "rand")[0]


def test_one_drawn_gives_a_code_column_with_its_values(app, client, survey):
    ids = _flow(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        node = _node(s)
        assert flow.level_columns(s, node) == [("framing_level", None, "")]
        assert flow.level_values(s, node, [f"k{ids[1]}"]) == {"framing_level": 2}
        assert flow.level_values(s, node, []) == {"framing_level": ""}  # not reached
        book = {row[0]: row for row in export.codebook(s)}
        assert book["framing_level"][3] == "1 = Fassung 1; 2 = Fassung 2; 3 = Fassung 3"


def test_several_drawn_give_one_0_1_column_each(app, client, survey):
    ids = _flow(app, client, k=2)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        node = _node(s)
        assert [c for c, _, _ in flow.level_columns(s, node)] == ["framing_1", "framing_2", "framing_3"]
        assert flow.level_values(s, node, [f"k{ids[0]}", f"k{ids[2]}"]) == {"framing_1": 1, "framing_2": 0, "framing_3": 1}
        assert set(flow.level_values(s, node, []).values()) == {""}
        assert {row[0]: row for row in export.codebook(s)}["framing_2"][3].startswith("0 = nicht gezeigt")


def test_all_shown_assigns_nothing(app, client, survey):
    _flow(app, client, k=3)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        assert flow.level_columns(s, _node(s)) == []


def test_codes_and_labels_are_set_in_the_dialog(app, client, survey):
    _flow(app, client)
    form = {"action": "update", "name": "Rahmung", "code": "framing", "k": "1", "balanced": "1", "level_0": "0",
            "label_0": "Kontrolle; neutral", "level_1": "1", "label_1": "", "level_2": "2", "label_2": ""}
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert flow.update(s, "r1", {**form, "level_1": "0"}, s.languages) == "Jede Bedingung braucht einen eigenen Code."
        assert flow.update(s, "r1", {**form, "level_0": "x"}, s.languages)
        assert flow.update(s, "r1", form, s.languages) is None
        found = flow.levels(s, _node(s))
        assert [(n, label) for _, n, label in found] == [(0, "Kontrolle, neutral"), (1, "Fassung 2"), (2, "Fassung 3")]


def test_the_dialog_shows_them_and_a_form_without_them_changes_nothing(app, client, survey):
    _flow(app, client)
    assert 'name="level_2"' in client.get("/surveys/1/flow/").data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert flow.update(s, "r1", {"code": "framing", "k": "1"}, s.languages) is None
        assert not any("level" in c for c in _node(s)["children"])


def test_fixed_at_the_start_the_data_keep_the_label_when_a_title_changes(app, client, survey):
    ids = _flow(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        flow.fix_levels(s)
        db.session.commit()
        block = db.session.get(Block, ids[0])
        block.titles = {s.default_language: "Korrigierter Titel"}
        db.session.commit()
        assert flow.levels(s, _node(s))[0][1:] == (1, "Fassung 1")
        assert _node(s)["fixed"] is True
        form = {"code": "framing", "k": "1", "level_0": "7", "level_1": "8", "level_2": "9", "label_0": "neu"}
        assert flow.update(s, "r1", form, s.languages) is None
        assert flow.levels(s, _node(s))[0][1:] == (1, "Fassung 1")  # locked


def test_the_start_fixes_them(app, client, survey, monkeypatch):
    _flow(app, client)
    monkeypatch.setattr(lifecycle, "check_start", lambda *a, **k: None)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        lifecycle.start(s, None, None)
        db.session.commit()
        assert all(c["label"] and isinstance(c["level"], int) for c in _node(s)["children"]) and _node(s)["fixed"]


def test_taken_out_of_the_randomizer_it_is_no_condition(app, client, survey):
    ids = _flow(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        tree = json.loads(json.dumps(s.flow))
        tree["children"] += [{"t": "block", "block": 999}]
        s.flow = tree
        for node in s.flow["children"]:
            node.update(level=3, label="alt") if node.get("t") == "block" else None
        cleaned = flow.normalize(s)
        assert not any("label" in c for c in cleaned["children"] if c.get("t") == "block") and ids


def test_the_survey_file_keeps_them_and_a_copy_can_change_them(app, client, survey):
    _flow(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        flow.fix_levels(s)
        db.session.commit()
        dumped = json.loads(transfer.dumps(s, with_files=False))
    rand = next(n for n in dumped["flow"]["children"] if n.get("t") == "rand")
    assert [c["level"] for c in rand["children"]] == [1, 2, 3] and rand["children"][0]["label"] == "Fassung 1"


def _people(app, client, monkeypatch):
    """A survey in the field with four real participations, a randomisation of two conditions."""
    from tests.conftest import CONSENT_OK, add, fill_questions

    add(client, "text_input")
    fill_questions(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        ids = []
        for i in range(2):
            block = Block(survey=s, position=10 + i, titles={s.default_language: f"Fassung; {i + 1}"}, show_title=True)
            db.session.add(block)
            db.session.flush()
            db.session.add(Item(block=block, type="info", code=f"T{i}", answer_mode="optional", position=1, config={},
                                texts={s.default_language: {"question": f"Text {i}"}}))
            ids.append(block.id)
        rest = [c for c in flow.tree(s).get("children", []) if c.get("block") not in ids]
        s.flow = {"t": "root", "children": rest + [
            {"t": "rand", "id": "r1", "code": "framing", "name": "Rahmung", "k": 1, "balanced": True, "shuffle": False,
             "children": [{"t": "block", "block": b} for b in ids]}]}
        flow.normalize(s)
        db.session.commit()
    with app.test_request_context():  # the checks of the start are not what this is about
        monkeypatch.setattr(lifecycle, "check_start", lambda *a, **k: None)
        lifecycle.start(db.session.get(Survey, 1), None, None)
        db.session.commit()
    app.config["BALANCE_SECONDS"] = 0
    for _ in range(4):
        person = app.test_client()
        person.get("/s/1")
        person.post("/s/1", data=CONSENT_OK)
        for _ in range(5):
            if "Vielen Dank" in person.get("/s/1").data.decode():
                break
            person.post("/s/1", data={"nav": "next"})


def test_the_data_hold_the_code_and_spss_its_labels(app, client, survey, tmp_path, monkeypatch):
    """From a real participation to the file: the column, and its value labels in SPSS."""
    import pyreadstat

    fill_consent(client)
    _people(app, client, monkeypatch)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        rows = export.rows(s, include_incomplete=True)
        assert len(rows) == 4
        assert {(r["framing_level"], r["framing"]) for r in rows} == {(1, "Fassung; 1"), (2, "Fassung; 2")}
        body = export.spss_sav(s, include_incomplete=True)
    path = tmp_path / "data.sav"
    path.write_bytes(body)
    frame, meta = pyreadstat.read_sav(str(path))
    assert sorted(set(frame["framing_level"].dropna())) == [1.0, 2.0]
    assert meta.variable_value_labels["framing_level"] == {1.0: "Fassung, 1", 2.0: "Fassung, 2"}


def test_an_imported_file_and_a_copy_keep_them_and_can_change_them_again(app, client, survey):
    from app import duplicate
    from app.models import User

    _flow(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        flow.fix_levels(s)
        db.session.commit()
        wanted = [n for _, n, _ in flow.levels(s, _node(s))]
        for copy in (transfer.create(json.loads(transfer.dumps(s, with_files=False)), db.session.get(User, 1))[0],
                     duplicate.create(s, s.members[0], "Kopie")[0]):
            db.session.flush()
            node = _node(copy)
            assert [n for _, n, _ in flow.levels(copy, node)] == wanted
            assert flow.levels(copy, node)[0][2] == "Fassung 1" and "fixed" not in node
