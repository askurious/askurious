"""Dashboards, quality control and the timeline (UI/UX audit 2026-10-05, package I: V76 to V82)."""
from werkzeug.datastructures import MultiDict

from app import boards, timeline
from app.extensions import db
from app.models import Survey
from tests.conftest import add
from tests.dashboards.test_boards import D, _board, study  # noqa: F401  (fixture)


def test_timeline_marks_today_and_midnights_and_says_when_empty(app, survey):
    with app.app_context():
        tl = timeline.build(db.session.get(Survey, 1))
    svg = str(tl["svg"])
    assert ">heute</span>" in svg and "noch keine Teilnahmen" in svg and 'class="tl-tick"' in svg
    assert "tl-hatch" in svg
    # day labels as "4.10.", no "04.10.26"
    import re

    assert re.search(r">\d{1,2}\.\d{1,2}\.(\d\d)?</span>", svg) and not re.search(r">\d\d\.\d\d\.\d\d</span>", svg)


def test_ring_only_for_few_answers_with_gaps_and_dark_colours(app, client, survey):
    add(client, "single_choice")
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, 2)
        item.config = {**item.config, "options": [{"id": f"o{n}", "code": str(n)} for n in range(1, 8)]}
        db.session.commit()
        rows = [r for g in boards.candidates(db.session.get(Survey, 1)) for r in g["rows"] if r["key"] == "item:2"]
    assert rows and "donut" not in [v for v, _ in rows[0]["views"]]
    parts = boards.donut([{"share": 0.5}, {"share": 0.5}])
    assert parts[0][1] < 50                                            # a white gap between the parts
    assert "#e69f00" not in boards.PALETTE and boards.MAX_RING == 5


def test_designer_warns_of_equal_headings_and_preview_says_preview(app, client, study):  # noqa: F811
    _board(client)
    page = client.get(f"{D}/1").data.decode()
    assert "data-board-box" in page
    frame = client.get(f"{D}/1/view?beispiel=1").data.decode()
    # two tiles under one heading: the designer says so
    client.post(f"{D}/1", headers={"X-Requested-With": "fetch"}, data=MultiDict([
        ("autosave", "1"), ("name", "A"), ("order", "item:2,item:5"), ("pick", "item:2"), ("pick", "item:5"),
        ("view-item:2", "bars"), ("view-item:5", "number"), ("title-item:2", "Kauf"), ("title-item:5", "Kauf")]))
    page = client.get(f"{D}/1").data.decode()
    assert "dieselbe Überschrift" in page
    assert "Vorschau" in frame or "Preview" in frame
