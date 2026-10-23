import json

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.base import AnswerError
from app.models import Asset, Item, Survey
from tests.conftest import add


def setup(client, app, mode="points", **extra):
    add(client, "image_mark")                                       # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict({
        "code": "look", "question": "Was fällt Ihnen auf?", "answer_mode": "required", "mode": mode,
        "alt": "Roboter", **extra}))
    with app.app_context():                                         # an approved image, as after an upload
        item = db.session.get(Item, 2)
        item.assets.append(Asset(key="", filename="x.png", source="upload", status="approved"))
        db.session.commit()


def test_points_limits_and_export(app, client, survey):
    setup(client, app, max="2", min="2", color="#ff0000")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == []
        assert item.config["color"] == "#ff0000" and item.config["max"] == 2 and item.kind.collects_answer
        with pytest.raises(AnswerError):                            # required: nothing marked
            item.kind.parse_answer(item, MultiDict({"i2": ""}))
        with pytest.raises(AnswerError, match="mindestens 2"):
            item.kind.parse_answer(item, MultiDict({"i2": "[[0.5, 0.5]]"}))
        marks = item.kind.parse_answer(item, MultiDict({"i2": "[[0.1, 0.2], [1.7, -1], [0.3, 0.3], \"x\"]"}))
        assert marks == [[0.1, 0.2], [1.0, 0.0]]                    # clamped, capped at max, junk dropped
        assert item.kind.export_values(item, marks) == {"look_n": 2, "look": "[[0.1,0.2],[1.0,0.0]]"}


def test_lines_and_default_color(app, client, survey):
    setup(client, app, mode="lines")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["color"] == "#ffd400" and item.config["max"] is None
        raw = json.dumps([[[0.1, 0.1], [0.2, 0.2], [0.3, 0.25]], [[0.5, 0.5], [0.6, 0.6]]])
        assert len(item.kind.parse_answer(item, MultiDict({"i2": raw}))) == 2


def test_preview_editor_and_dummy(app, client, survey):
    setup(client, app)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-mark="2"' in html and "x.png" in html and "data-mark-undo" in html
    assert 'id="assets"' in client.get("/surveys/1/editor/items/2").data.decode()   # image pipeline reused
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 3, seed=2)
        db.session.commit()
        from app.models import Response

        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(isinstance(a, list) and a for a in answers)


def test_editor_shows_pooled_marks_of_real_participants(app, client, survey):
    setup(client, app)
    with app.app_context():
        from app.models import Response

        s = db.session.get(Survey, 1)
        s.responses.append(Response(answers={"2": [[0.2, 0.3], [0.4, 0.5]]}, status=Response.COMPLETED))
        s.responses.append(Response(answers={"2": [[0.9, 0.9]]}, is_preview=True, status=Response.COMPLETED))   # previews do not count
        s.responses.append(Response(answers={"2": [[0.8, 0.8]]}))   # not completed: not in the data pool (D-24)
        db.session.commit()
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert "data-mark-pool" in html and "1 abgeschlossene Teilnahme," in html
    pool = html.split("data-mark-pool")[1][:300]
    assert "0.9" not in pool and "0.8" not in pool


def test_editor_pool_leaves_out_what_quality_control_marks(app, client, survey):
    """D-24: the overview in the editor uses the data pool of the results page, without marked participations."""
    from datetime import timedelta

    from app.models import utcnow

    setup(client, app)
    t0 = utcnow() - timedelta(hours=1)
    with app.app_context():
        from app.models import Response

        s = db.session.get(Survey, 1)
        for mark, seconds in ((0.2, 300), (0.3, 310), (0.4, 320), (0.7, 30)):   # the last one a speeder
            s.responses.append(Response(answers={"2": [[mark, mark]]}, status=Response.COMPLETED, started_at=t0,
                                        completed_at=t0 + timedelta(seconds=seconds)))
        db.session.commit()
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert "3 abgeschlossene Teilnahmen," in html and "0.7" not in html.split("data-mark-pool")[1][:300]




# --- hot spot: areas set beforehand (modes "areas" and "rate") ------------------------------------------------------

AREAS = {"area_id": ["a1", "a2", "a3", "a4"], "area_code": ["logo", "", "preis", ""],
         "area_label": ["Logo", "Foto", "Preis", "ohne Rechteck"],
         "area_box": ["0.1,0.1,0.3,0.2", "0.5,0.5,0.4,0.4", "0.9,0.9,0.5,0.5", ""]}


def setup_hot(client, app, mode="areas", **extra):
    setup(client, app, mode=mode)
    client.post("/surveys/1/editor/items/2", data=MultiDict({
        "code": "look", "question": "Was fällt Ihnen auf?", "answer_mode": "required", "mode": mode, "alt": "Roboter",
        **AREAS, **extra}))


def test_hot_spot_areas_from_editor(app, client, survey):
    setup_hot(client, app, max="2")
    with app.app_context():
        item = db.session.get(Item, 2)
        areas = item.kind.areas(item)
        # the row without a rectangle is dropped, an empty code gets the next free number, the box is cut to the image
        assert [(a["code"], a["label"]) for a in areas] == [("logo", "Logo"), ("1", "Foto"), ("preis", "Preis")]
        assert areas[2]["box"] == [0.9, 0.9, 0.1, 0.1]
        assert item.kind.validate(item) == []
        assert [c for c, _ in item.kind.export_columns(item)] == ["look_n", "look_logo", "look_1", "look_preis"]
        with pytest.raises(AnswerError, match="mindestens einen Bereich"):
            item.kind.parse_answer(item, MultiDict())
        with pytest.raises(AnswerError, match="höchstens 2"):
            item.kind.parse_answer(item, MultiDict([("i2", "logo"), ("i2", "1"), ("i2", "preis")]))
        chosen = item.kind.parse_answer(item, MultiDict([("i2", "preis"), ("i2", "logo"), ("i2", "fremd")]))
        assert chosen == ["logo", "preis"]                          # unknown codes dropped, in the order of the areas
        assert item.kind.export_values(item, chosen) == {"look_n": 2, "look_logo": 1, "look_1": 0, "look_preis": 1}
        assert item.kind.export_values(item, None)["look_logo"] is None
        assert item.kind.codebook_info(item, "look_logo", "de")[1] == "1 = gewählt; 0 = nicht gewählt"
        assert item.kind.column_level(item, "look_logo") == "nominal"


def test_hot_spot_rate(app, client, survey):
    setup_hot(client, app, mode="rate")
    with app.app_context():
        item = db.session.get(Item, 2)
        answer = item.kind.parse_answer(item, MultiDict({"i2_logo": "1", "i2_1": "-1", "i2_preis": "0"}))
        assert answer == {"logo": 1, "1": -1}
        assert item.kind.export_values(item, answer) == {"look_n": 2, "look_logo": 1, "look_1": -1, "look_preis": 0}
        assert "-1 = gefällt nicht" in item.kind.codebook_info(item, "look_1", "de")[1]
        assert item.kind.column_level(item, "look_1") == "ordinal"
        table = [item.kind.export_values(item, a) for a in ({"logo": 1}, {"logo": -1, "1": 1}, {"logo": 1, "1": 1})]
        r = item.kind.results(item, table, "de")
        logo = next(row for row in r["rows"] if row["code"] == "logo")
        assert r["persons"] == 3 and r["rate"] and abs(logo["like"] - 2 / 3) < 1e-9 and abs(logo["net"] - 1 / 3) < 1e-9


def test_hot_spot_checks(app, client, survey):
    setup_hot(client, app, min="5", area_label=["Logo", "", "Preis", ""], area_code=["n", "", "preis", ""])
    with app.app_context():
        item = db.session.get(Item, 2)
        problems = " ".join(item.kind.validate(item))
        assert "ohne Beschriftung" in problems and "nicht: n" in problems and "nur 3 Bereiche" in problems


def test_hot_spot_pages_dummy_and_results(app, client, survey):
    setup_hot(client, app, mode="rate")
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-hot="2"' in html and "data-hot-rate" in html and 'name="i2_logo"' in html and 'data-box="0.1,0.1,0.3,0.2"' in html
    assert "data-mark-undo" not in html
    editor = client.get("/surveys/1/editor/items/2").data.decode()
    assert "data-hot-editor" in editor and 'name="area_box" value="0.5,0.5,0.4,0.4"' in editor
    with app.app_context():
        from app import dummy
        from app.models import Response

        dummy.generate(db.session.get(Survey, 1), 4, seed=3)
        db.session.commit()
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(isinstance(a, dict) and a and set(a.values()) <= {1, -1} for a in answers)
        s = db.session.get(Survey, 1)
        s.responses.append(Response(answers={"2": {"logo": 1}}, status=Response.COMPLETED))
        db.session.commit()
    results = client.get("/surveys/1/results").data.decode()
    assert "hot-res-stage" in results and "Saldo" in results
    rows = list(__import__("csv").DictReader(__import__("io").StringIO(
        client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert "look_logo" in rows[0] and "look_preis" in rows[0]


def test_hot_spot_survey_file_round_trip(app, client, survey):
    setup_hot(client, app)
    with app.app_context():
        from app import transfer

        item = db.session.get(Item, 2)
        data = transfer.dump(db.session.get(Survey, 1), with_files=False)
        exported = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "image_mark")
        assert [a["code"] for a in exported["config"]["areas"]] == ["logo", "1", "preis"]
        assert item.kind.import_rules["areas"](exported["config"]["areas"])[0]["box"] == [0.1, 0.1, 0.3, 0.2]
