"""Code review 2026-10-15, F1: the random displays per participant (sides of "Zuordnung", order of the conjoint
options, the crowd's sample) were drawn from the response id, which the first page, not stored yet, does not have:
it showed no swap while the export later named the sides drawn from the id. Now they come from the pid, known before
the first save; participations whose layout was drawn before keep their id (models.display_seed)."""
import random

from app import runtime
from app.extensions import db
from app.models import Item, Response, Survey, display_seed
from tests.conftest import add


def _new(survey):
    return Response(survey_id=survey.id, language="de", answers={}, qversion=survey.qversion, status=Response.ACTIVE,
                    position=0, source="real", is_preview=False)


def test_the_first_page_and_the_export_draw_the_same_sides(app, client, survey):
    add(client, "assign")                                                         # 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        ids = [e["id"] for e in item.config["elements"]]
        sides = {}
        # at least twelve people, more until both sides were drawn: the pid is random, and twelve alike happened
        # (about 1 in 2000 runs, full suite 2026-10-30); 200 alike would be about 1 in 10^60
        for n in range(200):
            if n >= 12 and set(sides.values()) == {False, True}:
                break
            r = _new(s)
            runtime.ensure_layout(s, r, random.Random(1))
            assert r.id is None and r.layout["seed"] == "pid" and display_seed(r) == r.pid
            shown = item.kind.swapped(item, display_seed(r), ids)     # what the first page shows (runtime: crowd_seed)
            db.session.add(r)
            db.session.flush()                                # stored on the first "Next": now it has its id
            r.answers = {"2": [{"s": i, "c": "L", "rt": 500} for i in ids]}
            exported = item.kind.export_response_values(item, r.answers["2"], r)
            assert exported == {f"{item.code}_{e['code']}_swap": int(shown[e["id"]]) for e in item.config["elements"]}
            sides[r.id] = shown[ids[0]]
        assert set(sides.values()) == {False, True}


def test_a_layout_drawn_before_keeps_the_id(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        r = _new(s)
        runtime.ensure_layout(s, r, random.Random(1))
        db.session.add(r)
        db.session.flush()
        r.layout = {k: v for k, v in r.layout.items() if k != "seed"}    # drawn by the code before
        assert display_seed(r) == r.id
        r.layout = {**r.layout, "sig": "changed"}                         # the questionnaire changed: drawn anew
        runtime.ensure_layout(s, r, random.Random(2))
        assert "seed" not in r.layout and display_seed(r) == r.id
        fresh = _new(s)
        runtime.ensure_layout(s, fresh, random.Random(1))
        db.session.add(fresh)
        db.session.flush()
        fresh.layout = {**fresh.layout, "sig": "changed"}
        runtime.ensure_layout(s, fresh, random.Random(2))
        assert fresh.layout["seed"] == "pid" and display_seed(fresh) == fresh.pid
        db.session.rollback()
