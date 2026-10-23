"""SP13 TN-22/TN-23: server crowd count and a global budget for drawn points."""
import json

from flask import g
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response
from tests.items.test_image_mark import setup as image_setup
from tests.analysis.test_sample_crowd import setup as crowd_setup


def test_line_budget_preserves_every_stroke_and_its_endpoints(app, client, survey):
    image_setup(client, app, mode="lines")
    with app.app_context():
        item = db.session.get(Item, 2)
        raw = [[[j / 399, i / 199] for j in range(400)] for i in range(200)]
        marks = item.kind._parse(item, json.dumps(raw))
        assert len(marks) == 200 and sum(map(len, marks)) <= 5000
        from app.items.image_mark import _xy

        assert all(line[0] == _xy(original[0]) and line[-1] == _xy(original[-1]) for line, original in zip(marks, raw))


def test_crowd_does_not_trust_the_hidden_simulation_count(app, client, survey):
    crowd_setup(client)
    with app.test_request_context():
        item = db.session.get(Item, 4)
        g.participation = Response(id=123, is_preview=False)
        expected = item.kind.crowd(item, "de", {}, seed=123)["simulated"]
        answer = item.kind.parse_answer(item, MultiDict({"i4_n_2": "0", "i4_sim": "999999999"}))
        assert answer["sim"] == expected == 40
        assert item.kind.parse_answer(item, MultiDict({"i4_sim": "999"})) is None
