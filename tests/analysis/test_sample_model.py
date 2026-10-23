"""One sample model for every page (UI/UX audit 2026-10-07, S3, app/sample_model.py): the usable participations
from the power analysis or the target, the expected exclusions, the completions and, for an online panel, the
invitations; the same line on the study plan, the calculator, the data collection, the quota assistant, the export
and the methods section, and the pre-launch check compares against the same function."""
import html
import re

from werkzeug.datastructures import MultiDict

from app import design, sample_model, study_plan
from app.extensions import db
from app.models import Survey
from tests.conftest import add


def _line(page):
    """The lines of the sample model on a page, as plain text (the span of the macro with what it holds)."""
    out = []
    for m in re.finditer(r'<span class="sample-line" data-sample-line>', page):
        depth, at = 1, m.end()
        for tag in re.finditer(r"<(/?)span", page[at:]):
            depth += -1 if tag.group(1) else 1
            if not depth:
                inner = page[at:at + tag.start()]
                inner = re.sub(r"<span data-sm-invite hidden>.*?</span></span>", "", inner, flags=re.S)  # the hidden part
                out.append(" ".join(html.unescape(re.sub(r"<[^>]+>", " ", inner)).split()))
                break
    return out


def _questions(client):
    add(client, "text_input")                                                      # 2
    client.post("/surveys/1/editor/items/2", data={"code": "minuten", "question": "Wie viele Minuten?", "validation": "integer"})
    add(client, "text_input")                                                      # 3
    client.post("/surveys/1/editor/items/3", data={"code": "km", "question": "Kilometer?", "validation": "integer"})


def _plan(client, **extra):
    """A correlation r = .30: the power analysis asks for 84 usable participations (80 %, α = .05 two-sided)."""
    data = MultiDict({"research_question": "", "goal": "", "design": "", "h_id": [""], "h_text": ["Wer weiter fährt, braucht länger"],
                      "h_kind": ["correlation"], "h_dir": [""], "h_iv": ["km"], "h_dv": ["minuten"], "h_analysis": [""],
                      "h_effect": ["0,3"], "h_basis": [""], "h_basis_note": [""], "h_power": [""],
                      "sample_target": "", "recruitment": "", "sample_note": "", "rules": "", **extra})
    return client.post("/surveys/1/researchdesign", data=data, follow_redirects=True)


def test_the_numbers_of_the_model():
    assert sample_model.completions(84, 10) == 94 and sample_model.usable_of(94, 10) == 84
    # a target made from n usable ones gives n back, whatever the share
    for e in (0, 5, 10, 15, 20, 33, 50, 90):
        for n in range(1, 400):
            assert sample_model.usable_of(sample_model.completions(n, e), e) == n
    m = sample_model.numbers(84, 10, 50)
    assert (m["usable"], m["completions"], m["invite"]) == (84, 94, 188)
    assert sample_model.numbers(None, 10, completed=200)["usable"] == 180
    assert sample_model.text(m, "de") == "84 verwertbar · 94 Abschlüsse · bei 10 % Ausschlüssen · 188 einladen bei 50 % Rücklauf"
    assert sample_model.text(m, "en") == "84 usable · 94 completions · at 10% exclusions · invite 188 at a 50% response rate"
    assert sample_model.text(sample_model.numbers(1200, 0), "de") == "1.200 verwertbar · 1.200 Abschlüsse · bei 0 % Ausschlüssen"


def test_the_model_of_a_survey(app, client, survey):
    with app.app_context():
        assert sample_model.of(db.session.get(Survey, 1)) is None
    _questions(client)
    _plan(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        m = sample_model.of(s)
        assert (m["basis"], m["usable"], m["completions"], m["exclusions"], m["by"]) == ("power", 84, 94, 10, [1])
        assert study_plan.power_summary(s)["completions"] == 94                   # the same formula
        assert m["invite"] is None
    # an online panel with a response rate: the invitations; the rate counts only for a panel
    _plan(client, recruitment="panel", sample_response="40")
    with app.app_context():
        m = sample_model.of(db.session.get(Survey, 1))
        assert (m["response"], m["invite"]) == (40, 235)
    _plan(client, recruitment="convenience", sample_response="40")
    with app.app_context():
        assert sample_model.of(db.session.get(Survey, 1))["invite"] is None
    # without a power analysis the target of the plan is the completions; usable follows from it
    _plan(client, h_effect=[""], sample_target="200", sample_exclusions="20")
    with app.app_context():
        m = sample_model.of(db.session.get(Survey, 1))
        assert (m["basis"], m["usable"], m["completions"], m["exclusions"]) == ("target", 160, 200, 20)


def test_the_same_line_on_every_page(app, client, survey):
    _questions(client)
    _plan(client, recruitment="panel", sample_response="50")
    line = "84 verwertbar · 94 Abschlüsse · bei 10 % Ausschlüssen · 188 einladen bei 50 % Rücklauf"
    with app.app_context():
        assert sample_model.text(sample_model.of(db.session.get(Survey, 1)), "de") == line
    plan = client.get("/surveys/1/researchdesign").data.decode()
    assert line in _line(plan) and 'name="sample_response"' in plan and 'data-show-when="recruitment:panel"' in plan
    field = client.get("/surveys/1/field").data.decode()
    assert "Studienplan: " + line in _line(field)[0]
    # the completions fill the empty end of data collection, and the count says whose participations it means
    assert 'name="max_responses" min="1" value="94"' in field and "Abschlüsse laut Poweranalyse" in field
    assert "gezählt werden echte Teilnahmen" in field
    census = client.get("/surveys/1/flow/quotas/assistant").data.decode()
    assert 'name="n" min="1" max=' in census and 'value="94"' in census and any(line in x for x in _line(census))
    data = client.get("/surveys/1/data").data.decode()
    assert any(line in x for x in _line(data)) and "Echte Teilnahmen:" in data
    methods = client.get("/surveys/1/methods").data.decode()
    assert any(line in x for x in _line(methods))
    # the calculator: the same macro, filled in the browser; the plan's exclusions and response rate go along
    calc = client.get("/sample-size/?survey=1").data.decode()
    assert 'data-response="50"' in calc and 'data-sm="usable"' in calc and 'data-sm="completions"' in calc
    assert '<span data-sm="exclusions">10 %</span>' in calc and 'name="excl"' in calc


def test_the_english_line(app, client, survey):
    _questions(client)
    _plan(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert sample_model.text(sample_model.of(s), "en") == "84 usable · 94 completions · at 10% exclusions"
        assert "- Sample model: 84 usable · 94 completions · at 10% exclusions" in study_plan.markdown(s, "en")
        assert "- Stichprobenmodell: 84 verwertbar · 94 Abschlüsse · bei 10 % Ausschlüssen" in study_plan.markdown(s, "de")


def test_the_calculator_takes_the_exclusions_into_the_plan(app, client, survey):
    _questions(client)
    r = client.post("/surveys/1/researchdesign/goal", data={"n": "125", "excl": "20"})
    assert r.status_code == 302 and "max_responses=125" in r.headers["Location"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        sample = study_plan.plan_of(s)["sample"]
        assert (sample["target"], sample["exclusions"]) == (125, 20)
        m = sample_model.of(s)
        assert (m["usable"], m["completions"]) == (100, 125)
    # without the field the plan's exclusions stay
    client.post("/surveys/1/researchdesign/goal", data={"n": "130"})
    with app.app_context():
        assert study_plan.plan_of(db.session.get(Survey, 1))["sample"]["exclusions"] == 20


def test_the_design_check_compares_against_the_model(app, client, survey):
    _questions(client)
    _plan(client, sample_target="84")
    with app.app_context():
        found = [f.message for f in design.check(db.session.get(Survey, 1)) if "liegt unter dem" in f.message]
    assert found == ["Das Stichprobenziel 84 liegt unter dem, was die Poweranalyse im Studienplan verlangt (H1): "
                     "84 verwertbar · 94 Abschlüsse · bei 10 % Ausschlüssen."]
    _plan(client, sample_target="94")
    with app.app_context():
        assert not [f for f in design.check(db.session.get(Survey, 1)) if "liegt unter dem" in f.message]


def test_the_power_progress_says_what_it_counts(app, client, survey):
    _questions(client)
    _plan(client)
    page = client.get("/surveys/1/researchdesign/state").data.decode()
    assert "gezählt im Datenpool der Ergebnisse" in page
