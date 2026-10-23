import pytest
from werkzeug.datastructures import MultiDict

from app import units
from app.extensions import db
from app.items.base import AnswerError
from app.models import Item, Survey
from tests.conftest import add


def setup(client, **cfg):
    add(client, "slider")                                           # 2
    data = {"code": "chg", "question": "Wie verändert sich Ihr Vertrauen?", "answer_mode": "required",
            "min": "-100", "max": "100", "step": "5", "format": "percent", "show_value": "1", **cfg}
    client.post("/surveys/1/editor/items/2", data=MultiDict(list(data.items()) + [
        ("stmt_id", "a"), ("stmt_code", ""), ("stmt_text", "Pflegeroboter"),
        ("stmt_id", "b"), ("stmt_code", ""), ("stmt_text", "Chatbot")]))


def test_units_format():
    assert units.fmt(-25, "percent", lang="de", signed=True) == "−25 %"
    assert units.fmt(25, "percent", lang="en", signed=True) == "+25%"
    assert units.fmt(1234.5, "currency", "EUR", lang="de", places=1) == "1.234,5 €"
    assert units.fmt(1234, "currency", "USD", lang="en") == "$1,234"
    assert units.fmt(3, "number", unit="km") == "3 km"


def test_parse_snap_required_and_export(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == [] and item.config["format"] == "percent" and item.config["min"] == -100
        with pytest.raises(AnswerError):                            # one slider untouched
            item.kind.parse_answer(item, MultiDict({"i2_a": "20"}))
        ans = item.kind.parse_answer(item, MultiDict({"i2_a": "22", "i2_b": "-100"}))
        assert ans == {"a": 20, "b": -100}                          # 22 snaps to the 5-grid
        with pytest.raises(AnswerError):
            item.kind.parse_answer(item, MultiDict({"i2_a": "150", "i2_b": "0"}))
        assert item.kind.export_values(item, ans) == {"chg_1": 20, "chg_2": -100}
        assert "−100 bis 100 %" in item.kind.export_columns(item)[0][1]


def test_preview_shows_units_and_dummy(app, client, survey):
    setup(client)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert 'data-suffix=" %"' in html and "Pflegeroboter" in html and "−100 %" in html and "+100 %" in html
    with app.app_context():
        from app import dummy
        from app.models import Response

        dummy.generate(db.session.get(Survey, 1), 3, seed=1)
        db.session.commit()
        answers = [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert answers and all(-100 <= v <= 100 for a in answers for v in a.values())


def test_numbers_round_half_up_everywhere(app):
    """D-11: displays round half up (kaufmännisch), also where the binary value lies just below: 0,125 -> 0,13,
    1,005 -> 1,01, 12,5 % -> 13 %; Jinja's round and the |fixed filter of the templates do the same."""
    assert units.number(0.125, 2) == "0,13" and units.number(2.5) == "3" and units.number(-2.5) == "−3"
    assert units.fixed(1.005, 2) == "1.01" and units.fixed(2.675, 2) == "2.68" and units.fixed(6.25, 1) == "6.3"
    assert units.fixed(0.125, 2, signed=True) == "+0.13" and units.round_half_up(2.5) == 3
    assert units.fmt(28.125, "currency", "EUR", lang="de", places=2) == "28,13 €"
    env = app.jinja_env
    assert env.from_string("{{ 12.5|round|int }} {{ 0.125|fixed(2) }} {{ (1 / 8)|pct }} {{ 2.675|round(2) }}").render() \
        == "13 0.13 13 2.68"


def test_snap_rounds_ties_up_and_stays_below_the_maximum(app, client, survey):
    """R-8: from 0 to 10 in steps of 4, 2 snaps to 4 and 6 to 8 (half up, as the browser does; half to even gave
    0 and 8); in steps of 6, 9 and 10 snap to 6, the highest value on the grid (they gave 12, above the maximum)."""
    from app.items.slider import snap

    assert [snap(v, 0, 10, 4) for v in (2, 6, 10)] == [4, 8, 8]
    assert [snap(v, 0, 10, 6) for v in (2, 9, 10)] == [0, 6, 6]
    assert snap(0.25, 0, 1, 0.5) == 0.5 and snap(-2.5, -10, 10, 5) == 0
    setup(client, min="0", max="10", step="6")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.parse_answer(item, MultiDict({"i2_a": "9", "i2_b": "10"})) == {"a": 6, "b": 6}
