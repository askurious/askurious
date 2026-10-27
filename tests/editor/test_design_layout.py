"""The study design lays out the questionnaire (app/design_layout.py; user test 2026-10-27, point 2)."""
from werkzeug.datastructures import MultiDict

from app import flow
from app.extensions import db
from app.models import Survey


def _plan(client, design):
    return client.post("/surveys/1/researchdesign", data=MultiDict({"research_question": "x", "design": design}),
                       follow_redirects=True).data.decode()


def _flow(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        titles = {b.id: (b.titles or {}).get("de") for b in s.blocks}
        out = []
        for node, parent, depth in flow.walk(flow.tree(s)):
            out.append((depth, node["t"], titles.get(node.get("block")), node.get("k"), node.get("shuffle"), node.get("source")))
        return out


def test_experiment_puts_two_conditions_in_a_randomisation(app, client, survey):
    html = _plan(client, "experiment")
    assert "Bedingung A" in html and "Versuchsbedingung" in html
    nodes = _flow(app)
    rand = [n for n in nodes if n[1] == "rand"]
    assert len(rand) == 1 and rand[0][3] == 1 and not rand[0][4]
    inside = [n[2] for n in nodes if n[0] == 1]
    assert inside == ["Bedingung A", "Bedingung B"]
    # the empty block of the new survey follows the conditions: it takes the questions after them
    top = [n for n in nodes if n[0] == 0]
    i = next(k for k, n in enumerate(top) if n[1] == "rand")
    assert top[i + 1][1] == "block" and top[i + 1][2] not in ("Bedingung A", "Bedingung B")
    # once: choosing it again, or switching away and back, adds nothing
    _plan(client, "cross")
    _plan(client, "experiment")
    assert len([n for n in _flow(app) if n[1] == "rand"]) == 1


def test_within_shows_both_in_random_order(app, client, survey):
    _plan(client, "within")
    rand = [n for n in _flow(app) if n[1] == "rand"]
    assert len(rand) == 1 and rand[0][3] == 2 and rand[0][4] is True


def test_vignettes_get_a_vignette_loop(app, client, survey):
    _plan(client, "vignettes")
    nodes = _flow(app)
    loops = [n for n in nodes if n[1] == "loop"]
    assert len(loops) == 1 and loops[0][5] == "factors"
    assert [n[2] for n in nodes if n[0] == 1] == ["Vignette"]


def test_cross_section_and_started_surveys_stay_as_they_are(app, client, survey):
    before = _flow(app)
    _plan(client, "cross")
    assert _flow(app) == before
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = "running"
        db.session.commit()
    _plan(client, "experiment")
    assert not [n for n in _flow(app) if n[1] == "rand"]
