"""UI/UX audit 2026-10-07, packages A and B (Studienplan, Stichprobe, Startliste; Quoten, Datenerhebung, Qualität,
Export): W1 to W23."""
from tests.instrument_permissions import grant_instrument
from werkzeug.datastructures import MultiDict

from app import constructs, design, hypotheses, study_plan
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add, fill_consent


def _plan(client, **extra):
    data = MultiDict({"research_question": "", "goal": "", "design": "", "h_id": [""], "h_text": ["Frauen und Männer pendeln verschieden lange"],
                      "h_kind": ["difference"], "h_dir": [""], "h_iv": ["gender"], "h_dv": ["minuten"], "h_analysis": [""],
                      "sample_target": "", "recruitment": "", "sample_note": "", "rules": "", **extra})
    return client.post("/surveys/1/researchdesign", data=data, follow_redirects=True)


def _gender_and_minutes(client):
    add(client, "construct:gender")                                                # 2
    add(client, "text_input")                                                      # 3: a number
    client.post("/surveys/1/editor/items/3", data={"code": "minuten", "question": "Wie viele Minuten?", "validation": "integer"})


def test_w1_new_gender_question_marks_no_answer_as_missing(app, client, survey):
    with app.app_context():
        spec = constructs._gender_spec()
    options = {o["id"]: o for o in spec["config"]["options"]}
    assert options["o4"].get("missing") is True and not options["o5"].get("missing")   # self-description stays a group
    _gender_and_minutes(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [o["code"] for o in item.config["options"] if o.get("missing")] == ["99"]
        s = db.session.get(Survey, 1)
        assert not [f for f in design.check(s) if "nicht als „fehlend“ markiert" in f.message and f.item_id == 2]


def test_w1_the_groups_of_a_comparison_and_only_these_groups(app, client, survey):
    _gender_and_minutes(client)
    _plan(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert [label for _, label, kept in study_plan.group_options(s, h) if kept] == [
            "weiblich", "männlich", "divers", "Ich beschreibe mich selbst"]
        assert study_plan.suggest(s, h) == "anova" and study_plan.names_two(s, h)
        hid = h["id"]
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "Gruppen im Test" in page and f'name="h_grp_{hid}" value="1" checked' in page
    assert "Die Hypothese nennt zwei Gruppen, der Test vergleicht 4." in page
    # only women and men
    _plan(client, h_id=[hid], **{f"h_grp_shown_{hid}": "1", f"h_grp_{hid}": ["1", "2"]})
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert h["groups"] == ["1", "2"] and study_plan.suggest(s, h) == "t"
        assert study_plan.power_of(s, h)["groups"] == 2 and not study_plan.names_two(s, h)
        rows = [{"gender": str(1 + i % 4), "minuten": str(10 + i % 7 + (i % 4 == 1) * 3)} for i in range(160)]
        [res] = hypotheses.evaluate(s, rows)
        assert res["tested"] == "t" and [g["code"] for g in res["groups"]] == ["1", "2"]
        assert any("Verglichen nur: weiblich, männlich" in n for n in res["notes"])
        assert "verglichen nur: weiblich, männlich" in study_plan.markdown(s, "de")
    # all ticked again: nothing stored
    _plan(client, h_id=[hid], **{f"h_grp_shown_{hid}": "1", f"h_grp_{hid}": ["1", "2", "3", "4"]})
    with app.app_context():
        assert "groups" not in study_plan.plan_of(db.session.get(Survey, 1))["hypotheses"][0]


def test_w2_the_calculator_takes_the_exclusions_of_the_plan_and_names_completions(app, client, survey):
    _gender_and_minutes(client)
    _plan(client, h_kind=["correlation"], h_iv=["minuten"], h_dv=["minuten"], sample_exclusions="20")
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = {"kind": "correlation", "analysis": "r", "effect": 0.3}
        assert study_plan.calculator_args(s, study_plan.power_of(s, h))["excl"] == 20
    page = client.get("/sample-size/?survey=1").data.decode()
    assert 'value="20" data-ss="dropout"' in page and "aus dem Studienplan" in page
    assert "erwartete Ausschlüsse (%)" in page and "Ausfälle" not in page and 'data-sm="completions"' in page
    assert 'value="15" data-ss="dropout"' in client.get("/sample-size/?survey=1&excl=15").data.decode()


def test_w3_one_form_for_power_alpha_and_chi2(client, survey):
    page = client.get("/sample-size/").data.decode()
    assert ">80 %</option>" in page and ">,05</option>" in page and "χ²-Test" in page
    assert "0,80" not in page and "Chi²" not in page and "Teststärke" not in page


def test_w4_the_wizard_asks_for_the_questions_later(app, client, survey):
    with app.app_context():
        assert study_plan.questions_later(db.session.get(Survey, 1))
    client.post("/surveys/1/researchdesign", data=MultiDict({"h_id": [""], "h_text": ["H eins"], "h_kind": ["correlation"],
                                                          "h_dir": [""], "h_iv": [""], "h_dv": [""], "h_analysis": [""]}))
    page = client.get("/surveys/1/setup/researchdesign").data.decode()
    assert "Die beiden Variablen wählen Sie, sobald der Fragebogen steht" in page and 'name="h_iv" value=""' in page
    assert "– später, wenn der Fragebogen steht –" not in page
    fertig = client.get("/surveys/1/setup/done").data.decode()
    assert "1 davon noch ohne Fragen" in fertig and "Danach die Hypothesen mit ihren Fragen verbinden." in fertig
    add(client, "text_input")
    with app.app_context():
        assert not study_plan.questions_later(db.session.get(Survey, 1))
    assert 'name="h_iv" value=""' not in client.get("/surveys/1/setup/researchdesign").data.decode()


def test_w5_the_pilot_link_for_a_hypothesis_added_on_the_page(app, client, survey):
    _gender_and_minutes(client)
    add(client, "text_input")                                                      # 4
    client.post("/surveys/1/editor/items/4", data={"code": "km", "question": "Kilometer?", "validation": "integer"})
    _plan(client, h_id=["n57090405"], h_kind=["correlation"], h_iv=["km"], h_effect=["0,3"])
    with app.app_context():
        h = study_plan.plan_of(db.session.get(Survey, 1))["hypotheses"][0]
    assert h["id"] == "n57090405"
    assert "/surveys/1/researchdesign/pilot?h=n57090405" in client.get("/surveys/1/researchdesign").data.decode()


def test_w9_w17_findings_lead_where_they_are_fixed(app, client, survey):
    _gender_and_minutes(client)
    _plan(client, h_iv=["minuten"], h_dv=["minuten"], h_kind=["correlation"])
    client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "", "contact_email": ""})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not s.consent_item.kind.researchers(s.consent_item)
        found = design.check(s)
        same = next(f for f in found if "dieselbe Frage" in f.message)
        assert (same.link, same.anchor) == ("study_plan.page", "h1") and "im Reiter „Studienplan“" in same.message
        consent = [f for f in found if f.message.startswith("Einwilligung") or "verantwortliche Person" in f.message]
        joined = [f for f in consent if "Verantwortliche Person und Kontakt-E-Mail fehlen" in f.message]
        assert len(joined) == 1 and (joined[0].link, joined[0].anchor) == ("surveys.detail", "einwilligung")
        assert not [f for f in consent if "keine verantwortliche Person" in f.message]
    page = client.get("/surveys/1/field").data.decode()
    assert 'href="/surveys/1/researchdesign#h1"' in page and 'href="/surveys/1#einwilligung"' in page
    assert 'id="h1"' in client.get("/surveys/1/researchdesign").data.decode()
    fill_consent(client)
    with app.app_context():
        assert not [f for f in design.check(db.session.get(Survey, 1)) if "Kontakt-E-Mail" in f.message]


def test_w10_fields_that_do_not_apply_are_hidden(app, client, survey):
    _gender_and_minutes(client)
    _plan(client, h_kind=["open"])
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "data-plan-power hidden" in page and 'name="h_effect"' in page        # hidden, the value stays


def test_w11_constructs_are_named_by_their_name(app, client, survey):
    grant_instrument(client.application, "tam_pu")
    add(client, "construct:tam_pu")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert study_plan.label(item) == f"{item.code} · Wahrgenommene Nützlichkeit"


def test_w13_a_hidden_explanation_comes_back(app, client, survey):
    client.post("/learn/why/sample_size/hide")
    with app.app_context():
        from app.models import User

        user = db.session.scalar(db.select(User))
        assert "sample_size" in (user.learning_hidden or [])
    assert client.post("/learn/why/sample_size/show").get_json() == {"ok": True}
    with app.app_context():
        from app.models import User

        assert "sample_size" not in (db.session.scalar(db.select(User)).learning_hidden or [])
    assert 'data-unhide-url="/learn/why/sample_size/show"' in client.get("/sample-size/").data.decode()


def test_w14_every_hypothesis_says_why_this_test(app, client, survey):
    _gender_and_minutes(client)
    _plan(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert study_plan.test_reason(s, h).startswith("Warum dieser Test? 4 Gruppen, die verglichene Größe metrisch")
        [res] = hypotheses.evaluate(s, [{"gender": str(1 + i % 2), "minuten": str(i % 9)} for i in range(60)])
        assert res["why"] and hypotheses.VERSION >= 3
        assert study_plan.test_reason(s, {**h, "analysis": "kw"}).startswith("Im Studienplan so gewählt (vorgeschlagen wäre:")


def test_w15_power_progress_stands_with_the_status(app, client, survey):
    _gender_and_minutes(client)
    _plan(client, h_kind=["correlation"], h_iv=["minuten"], h_dv=["minuten"])
    add(client, "text_input")                                                      # 4
    client.post("/surveys/1/editor/items/4", data={"code": "km", "question": "Kilometer?", "validation": "integer"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        plan = study_plan.plan_of(s)
        plan["hypotheses"][0].update(iv="km", effect=0.3)
        s.study_plan = plan
        db.session.commit()
    page = client.get("/surveys/1/field").data.decode()
    # the timeline is part of the status since Ralf, 2026-10-11, and a draft without participations shows none
    status = page.split('id="status"')[1].split("</details>")[0]
    assert "data-power-progress" in status and 'id="timeline"' not in status


def test_w16_a_template_gets_its_own_title_and_goes_on_in_the_wizard(app, client):
    r = client.post("/surveys/template/experiment", data={"title": "Mein Plakattest"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/setup/researchdesign")
    with app.app_context():
        s = db.session.scalar(db.select(Survey).order_by(Survey.id.desc()))
        assert s.title == "Mein Plakattest"
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert h["effect"] == 0.5 and h["basis"] == "sesoi" and study_plan.power_of(s, h)["n"]
        assert not [f for f in study_plan.findings(s) if "Stichprobenziel" in f.message]
    page = client.get("/surveys/new?start=vorlage").data.decode()
    assert 'name="title" value="Online-Experiment mit zwei Bedingungen"' in page
    assert "<h2>Aus einer Vorlage" not in page


def test_w19_export_counts_say_they_are_real_and_one_methods_section(client, survey):
    page = client.get("/surveys/1/data").data.decode()
    assert "Echte Teilnahmen:" in page and page.count('href="/surveys/1/methods"') == 1


def test_w20_the_flow_shows_no_messages_on_single_elements(app, client, survey):
    add(client, "single_choice")
    with app.app_context():
        s = db.session.get(Survey, 1)
        per_item = [f for f in design.check(s) if f.item_id == 2]
        assert per_item
    page = client.get("/surveys/1/flow/").data.decode()
    side = page.split("Startprüfung")[-1]
    assert all(f.message not in side for f in per_item)


def test_w21_plus_quota_stands_under_the_quotas(client, survey):
    client.post("/surveys/1/flow/quotas/add")
    page = client.get("/surveys/1/flow/quotas/").data.decode()
    assert page.index("quota-add-form") < page.index('id="quota-marked"') < page.index('id="weighting"')


def test_w22_w23_labels_say_what_they_mean(client, survey):
    page = client.get("/surveys/1/quality").data.decode()
    assert "x % des Medians" not in page and "über y" not in page
    assert "Gesamtdauer: Schneller als ein Anteil der Median-Zeit" in page
    field = client.get("/surveys/1/field").data.decode()
    assert "simulierte Personen" in field
