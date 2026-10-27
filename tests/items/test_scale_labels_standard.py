"""Numbers and emoji beside the points of a Likert scale, and the standard of a survey's scales (app/items/likert.py,
app/scale_default.py; user test 2026-10-27, points 9 and 10)."""
from werkzeug.datastructures import MultiDict

from app import design
from app.extensions import db
from app.items.likert import numbering
from app.models import Item, Survey
from tests.conftest import add


def _form(points="5", **extra):
    return MultiDict([("code", "L"), ("question", "Wie sehr?"), ("points", points), ("labeling", "full"),
                      ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Eins"), *extra.items()])


def test_numberings():
    assert numbering("1", 5) == ["1", "2", "3", "4", "5"]
    assert numbering("0", 3) == ["0", "1", "2"]
    assert numbering("center", 5) == ["−2", "−1", "0", "+1", "+2"]
    assert numbering("center", 4) == ["−2", "−1", "+1", "+2"]
    assert numbering("", 3) == ["", "", ""]


def test_numbers_and_emoji_are_shown_the_codes_stay(app, client, survey):
    add(client, "likert")
    form = _form(numbers="1")
    for e in ("😞", "🙁", "😐", "🙂", "😀"):
        form.add("emoji", e)
    client.post("/surveys/1/editor/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["numbers"] == "1" and item.config["emoji"][0] == "😞"
        assert item.kind.export_values(item, {"s1": 4}) == {"L_1": 4}
    page = client.get("/surveys/1/editor/items/2/frame").data.decode()
    assert '<span class="p-point-num">(4)</span>' in page and '<span class="p-point-emoji" aria-hidden="true">🙂</span>' in page


def test_the_standard_for_new_scales_the_check_and_apply_all(app, client, survey):
    add(client, "likert")                                   # item 2: 5 points, before the standard
    client.post("/surveys/1/editor/scale-default", data={"sd_points": "6", "sd_anchors": "agree", "sd_labeling": "full",
                                                        "sd_label_display": "each", "sd_numbers": ""})
    add(client, "likert")                                   # item 3: takes the standard
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert db.session.get(Item, 3).config["points"] == 6 and len(db.session.get(Item, 3).texts["de"]["labels"]) == 6
        warnings = [f for f in design.check(s) if "Standard der Skalen" in f.message]
        assert [f.item_id for f in warnings] == [2] and "5 statt 6 Stufen" in warnings[0].message
    client.post("/surveys/1/editor/items/2", data=_form(own_scale_shown="1", own_scale="1"))
    with app.app_context():
        assert not [f for f in design.check(db.session.get(Survey, 1)) if "Standard der Skalen" in f.message]
    client.post("/surveys/1/editor/items/2", data=_form(own_scale_shown="1"))
    client.post("/surveys/1/editor/scale-default", data={"sd_points": "6", "sd_anchors": "agree", "apply": "1"})
    with app.app_context():
        assert db.session.get(Item, 2).config["points"] == 6
    assert 'id="skalen-standard"' in client.get("/surveys/1").data.decode()
