"""Planned durations of a Leitfrage (app/items/guide_question.py, Ralf 2026-11-03): 1, 2, 3, 5, 10, 15, 20, 30 or
45 minutes; any other number is rounded to the nearest of them (the larger on a tie), a missing or non-numeric
value gives 10."""
import json

import pytest

from app.extensions import db
from app.items.guide_question import MINUTES, round_minutes
from app.models import Item, Survey
from tests.interview.test_interview import _read_in, first, make


@pytest.mark.parametrize("value, expected", [
    (1, 1), (2, 2), (3, 3), (45, 45), (4, 5), (25, 30), (60, 45), (0, 1), (-3, 1), (7.5, 10), (12, 10), (13, 15),
    ("2", 2), ("4", 5), (None, 10), ("", 10), ("viel", 10), (True, 10), (float("nan"), 10)])
def test_round_minutes(value, expected):
    assert round_minutes(value) == expected


def test_short_durations_saved_and_summed(app, client):
    code, sid = make(app, client, template="leer")
    q = first(app, sid)
    for sent, kept in (("1", 1), ("2", 2), ("3", 3), ("4", 5)):
        r = client.post(f"/surveys/{sid}/editor/items/{q}?embed=1", data={"code": "L1", "question": "Kurz?",
                                                                          "minutes": sent})
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(Item, q).config["minutes"] == kept
    with app.app_context():
        from app import interview

        survey = db.session.get(Survey, sid)
        block = next(b for p, b in interview.ordered_blocks(survey) if p == "guide")
        assert interview.planned_minutes(block) == sum(i.kind.minutes(i) for i in block.items
                                                       if i.type == "guide_question")
        assert 5 in [i.kind.minutes(i) for i in block.items if i.id == q]
    page = client.get(f"/surveys/{sid}/editor/items/{q}?embed=1").data.decode()
    for m in MINUTES:
        assert f'<option value="{m}"' in page


def test_import_keeps_or_rounds_minutes(app, client):
    code, sid = make(app, client, template="leer")
    data = json.loads(client.get(f"/surveys/{sid}/export/survey.json").data)
    questions = [i for b in data["blocks"] for i in b["items"] if i["type"] == "guide_question"]
    assert questions
    questions[0]["config"]["minutes"] = 2
    questions.append(dict(questions[0], code="L9", config={**questions[0]["config"], "minutes": 4}))
    next(b for b in data["blocks"] if questions[0] in b["items"])["items"].append(questions[-1])
    _read_in(client, data, title="Kurze Fragen")
    with app.app_context():
        copy = db.session.scalar(db.select(Survey).filter_by(title="Kurze Fragen"))
        got = sorted(i.config.get("minutes") for b in copy.blocks for i in b.items if i.type == "guide_question")
        assert got == [2, 5]
