from datetime import datetime

from app import export
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add
from tests.editor.test_flow import E, setup_design


def test_page_order_with_randomizer_end_marker_and_conjoint_pages(app, client, survey):
    """consent | age | [rand: A, B] | [branch: end] | outro (randomized, items 5 and 6).
    The layout is in flow order with an end marker, so layout positions differ from page numbers."""
    setup_design(app, client)
    add(client, "info", block=5)                                    # 6
    client.post(E + "/items/6", data={"code": "extra", "question": "Noch etwas"})
    client.post(E + "/blocks/5", data={"title": "Abschluss", "randomize_items": "1"})
    client.post(E + "/blocks/3", data={"title": "Bild A", "randomize_items": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.responses.append(Response(
            status=Response.COMPLETED, started_at=datetime(2026, 9, 2), completed_at=datetime(2026, 9, 2),
            answers={}, layout={"pages": [
                {"block": 1, "items": [1], "cond": []},
                {"block": 2, "items": [2], "cond": []},
                {"block": 4, "items": [4], "cond": ["k4"]},             # the randomizer picked B: block 3 not shown
                {"end": "e1", "cond": ["b1"]},
                {"block": 5, "items": [6, 5], "cond": []},
            ], "conditions": {"r1": ["k4"]}, "stmt": {}, "cj": {}, "loops": {}}))
        db.session.commit()
        (row,) = export.rows(db.session.get(Survey, 1))
    assert row["framing"] == "Bild B"
    assert row["page3_order"] == ""                                 # block 3 was not shown
    assert row["page5_order"] == "extra|outro"                      # before: empty (item lookup overwritten) or KeyError


def test_page_split_around_a_conjoint_is_joined(app):
    pages = [{"block": 7, "items": [3, 1], "cond": []},
             {"block": 7, "items": [2], "cond": [], "task": 0},
             {"block": 7, "items": [2], "cond": [], "task": 1},
             {"block": 7, "items": [5, 4], "cond": []},
             {"block": 7, "items": [4, 5], "cond": [], "row": "r1", "iter": 0, "iters": 1, "loop": "l1"}]
    assert export._page_order(pages, 7, {1, 2, 3, 4, 5}) == [3, 1, 2, 5, 4]
