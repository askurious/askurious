"""UI/UX audit 2026-10-05, package A (Studienplan und Hypothesen): V1 to V10."""
from werkzeug.datastructures import MultiDict

from app import design, hypotheses, study_plan
from app.extensions import db
from app.models import Survey
from tests.conftest import add


def _questions(client):
    add(client, "single_choice")                                                # 2: two groups
    client.post("/surveys/1/editor/items/2", data=MultiDict({"code": "gruppe", "question": "Gruppe?", "option_id": ["a", "b"],
                                                             "option_code": ["1", "2"], "option_label": ["Studierende", "B"]}))
    add(client, "text_input")                                                    # 3: a number
    client.post("/surveys/1/editor/items/3", data={"code": "minuten", "question": "Wie viele Minuten?", "validation": "integer"})


def _plan(client, **extra):
    data = MultiDict({"research_question": "", "goal": "", "design": "", "h_id": [""], "h_text": ["Gruppen pendeln verschieden"],
                      "h_kind": ["difference"], "h_dir": [""], "h_iv": ["gruppe"], "h_dv": ["minuten"], "h_analysis": [""],
                      "sample_target": "", "recruitment": "", "sample_note": "", "rules": "", **extra})
    return client.post("/surveys/1/researchdesign", data=data, follow_redirects=True)


def test_v1_v2_a_question_tested_against_itself(app, client, survey):
    _questions(client)
    _plan(client, h_iv=["minuten"], h_dv=["minuten"], h_kind=["correlation"])
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert study_plan.same(h) and study_plan.suggest(s, h) == ""
        assert study_plan.status(s)["measured"] == 0
        errors = [f for f in design.check(s) if f.severity == design.ERROR and "dieselbe Frage" in f.message]
        assert errors and errors[0].item_id is not None
        [res] = hypotheses.evaluate(s, [{"minuten": str(i)} for i in range(40)])
    assert res["verdict"] == "offen" and not res["sentence"] and "Zuordnung prüfen" in res["notes"][0]
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "Die beiden Variablen müssen verschiedene Fragen sein." in page
    assert "<option value=\"gruppe\"" in page


def test_v2_results_name_the_variables_and_refuse_a_perfect_correlation(app, client, survey):
    _questions(client)
    add(client, "text_input")                                                    # 4: a copy of the number
    client.post("/surveys/1/editor/items/4", data={"code": "kopie", "question": "Nochmal Minuten?", "validation": "integer"})
    _plan(client, h_iv=["minuten"], h_dv=["kopie"], h_kind=["correlation"])
    with app.app_context():
        [res] = hypotheses.evaluate(db.session.get(Survey, 1), [{"minuten": str(i), "kopie": str(i)} for i in range(40)])
    assert [(v[0], v[1]) for v in res["vars"]] == [("iv", "minuten"), ("dv", "kopie")]
    assert res["verdict"] == "offen" and not res["sentence"] and "Zuordnung prüfen" in res["notes"][0]


def test_v3_editor_rows_change_only_the_clicked_role(app, client, survey):
    _questions(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/4", data={"code": "km", "question": "Wie viele Kilometer?", "validation": "integer"})
    _plan(client, h_id=["", ""], h_text=["H eins", "H zwei"], h_kind=["difference", "correlation"], h_dir=["", ""],
          h_iv=["gruppe", "km"], h_dv=["minuten", "minuten"], h_analysis=["", ""])
    with app.app_context():
        h1, h2 = study_plan.plan_of(db.session.get(Survey, 1))["hypotheses"]
    page = client.get("/surveys/1/editor/items/3").data.decode()
    assert page.count('name="role"') == 4 and f'value="{h1["id"]}:dv" checked' in page and f'value="{h2["id"]}:dv" checked' in page
    # untick H2 Zielgröße: H1 keeps its role
    client.post("/surveys/1/researchdesign/assign", data=MultiDict({"code": "minuten", "h": [h1["id"], h2["id"]],
                                                                    "role": [f"{h1['id']}:dv"]}))
    with app.app_context():
        a, b = study_plan.plan_of(db.session.get(Survey, 1))["hypotheses"]
    assert (a["iv"], a["dv"], b["iv"], b["dv"]) == ("gruppe", "minuten", "km", "")


def test_v4_v5_v7_field_says_what_is_computed(app, client, survey):
    _questions(client)
    add(client, "likert")                                                        # 4: a scale
    client.post("/surveys/1/editor/items/4", data=MultiDict({"code": "zuf", "question": "Wie sehr stimmen Sie zu?",
                                                             "statement": ["Eins", "Zwei", "Drei"]}))
    _plan(client)
    page = client.get("/surveys/1/researchdesign").data.decode()
    # in short in the field, in full in the hint below (UI/UX audit 2026-10-07, W11)
    assert "auto: t-Test (Welch)</option>" in page and "Vorschlag: t-Test (Welch) für zwei Gruppen" in page
    assert "noch offen</option>" not in page.split('name="h_analysis"')[1][:400]
    assert "gruppe · Gruppe?" in page                                            # V5: the name first
    assert "„A“ ist „Studierende“" in page                                       # V4: which group is A
    with app.app_context():
        s = db.session.get(Survey, 1)
        statements = len((db.session.get(__import__("app.models", fromlist=["Item"]).Item, 4).config or {}).get("statements") or [])
        reasons = study_plan.reasons(s, {"iv": "gruppe", "dv": "zuf"})
    if statements >= 2:
        # a matrix without a construct is ordinal: its score is tested by ranks (audit 2026-10-23, M2)
        assert reasons == [f"Zielgröße: Skalenwert aus {statements} Aussagen, als ordinal behandelt (Rangtests, Median); "
                           "metrisch, wenn das Messniveau der Matrix so gesetzt ist"]
        assert "in Hypothesen des Studienplans: metrisch mit Mittelwert" in client.get("/surveys/1/editor/items/4").data.decode()


def test_v4_a_new_row_keeps_its_id_over_saves(app, client, survey):
    _questions(client)
    _plan(client, h_id=["n12345"])
    _plan(client, h_id=["n12345"])
    with app.app_context():
        [h] = study_plan.plan_of(db.session.get(Survey, 1))["hypotheses"]
    assert h["id"] == "n12345"


def test_v6_finder_data_and_take_button(app, client, survey):
    _plan(client, goal="causal", design="cross")
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "data-plan-finder-data" in page and 'data-plan-take="experiment"' in page


def test_v9_rank_tests_show_medians(app, client, survey):
    _questions(client)
    _plan(client, h_analysis=["u"])
    with app.app_context():
        table = [{"gruppe": "1", "minuten": str(40 + i % 5)} for i in range(40)] + \
                [{"gruppe": "2", "minuten": str(20 + i % 4)} for i in range(40)]
        [res] = hypotheses.evaluate(db.session.get(Survey, 1), table)
    assert res["center"] == "Md" and [g["median"] for g in res["groups"]] == [42.0, 21.5] and res["median_places"] == 1


def test_v10_targets_prefill_and_warn(app, client, survey):
    _questions(client)
    _plan(client, sample_target="200")
    page = client.get("/surveys/1/field").data.decode()
    assert 'name="max_responses" min="1" value="200"' in page and "Aus dem Studienplan" in page
    census = client.get("/surveys/1/flow/quotas/assistant").data.decode()
    assert 'name="n" min="1"' in census and 'value="200"' in census and "aus dem Studienplan" in census
    r = client.post("/surveys/1/researchdesign/goal", data={"n": "300"})
    assert r.status_code == 302 and "max_responses=300" in r.headers["Location"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert study_plan.plan_of(s)["sample"]["target"] == 300
        s.max_responses = 250
        db.session.commit()
        assert any("Studienplan 300, Feldende 250" in f.message for f in design.check(s))
