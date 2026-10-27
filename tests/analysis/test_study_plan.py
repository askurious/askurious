"""Studienplan (app/study_plan.py): research question, hypotheses with their elements, design, sample and rules in a
group of the settings and a step of the wizard, followed by the pre-launch check, the element editor, the
pre-registration and the survey file (concept "Askurious für Studierende und Berufseinsteiger", package A)."""
import io
import zipfile

from werkzeug.datastructures import MultiDict

from app import design, hypotheses, prereg, study_plan
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add, fill_consent


def _questions(client, app):
    add(client, "single_choice")                                                # 2: two groups
    client.post("/surveys/1/editor/items/2", data=MultiDict({"code": "gruppe", "question": "Gruppe?", "option_id": ["a", "b"],
                                                             "option_code": ["1", "2"], "option_label": ["A", "B"]}))
    add(client, "text_input")                                                    # 3: a number
    client.post("/surveys/1/editor/items/3", data={"code": "minuten", "question": "Wie viele Minuten?", "validation": "integer"})


def _plan(client, **extra):
    data = MultiDict({"research_question": "Pendeln Gruppen unterschiedlich lange?", "goal": "difference", "design": "cross",
                      "h_id": ["", ""], "h_text": ["Gruppe A pendelt länger als B", "Offen: was noch auffällt"],
                      "h_kind": ["difference", "open"], "h_dir": ["pos", ""], "h_iv": ["gruppe", ""], "h_dv": ["minuten", ""],
                      "h_analysis": ["", ""], "sample_target": "120", "recruitment": "convenience", "sample_note": "",
                      "rules": "Speeder unter 50 % des Medians", **extra})
    return client.post("/surveys/1/researchdesign", data=data, follow_redirects=True)


def test_plan_is_saved_suggests_the_analysis_and_shows_its_line(app, client, survey):
    _questions(client, app)
    assert "Studienplan gespeichert." in _plan(client).data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        plan = study_plan.plan_of(s)
        assert s.research_question == "Pendeln Gruppen unterschiedlich lange?"
        assert [h["kind"] for h in plan["hypotheses"]] == ["difference", "open"] and plan["sample"]["target"] == 120
        assert study_plan.suggest(s, plan["hypotheses"][0]) == "t"            # two groups, a number
        assert study_plan.status(s)["measured"] == 2 and not study_plan.findings(s)
        # a page of its own since 2026-10-06, no group of the settings any more
        assert "studienplan" not in {k for k, _ in __import__("app.settings_page", fromlist=["GROUPS"]).GROUPS}
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert 'id="studienplan"' in page and "Gruppe A pendelt länger als B" in page and "auto: t-Test (Welch)" in page
    assert "2 Hypothesen, 2 gemessen" in page
    assert "Studienplan:" in client.get("/surveys/1/editor/").data.decode()


def test_design_check_rename_and_the_element_editor(app, client, survey):
    _questions(client, app)
    _plan(client, h_dv=["", ""])
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert any("Studienplan H1" in f.message for f in design.check(s))
        hid = study_plan.plan_of(s)["hypotheses"][0]["id"]
    page = client.get("/surveys/1/editor/items/3").data.decode()
    assert "Im Studienplan" in page and f'value="{hid}:dv"' in page
    client.post("/surveys/1/researchdesign/assign", data={"code": "minuten", "h": hid, "role": f"{hid}:dv",
                                                         "back": "https://evil.example/"})
    client.post("/surveys/1/editor/items/2", data=MultiDict({"code": "team", "question": "Gruppe?", "option_id": ["a", "b"],
                                                             "option_code": ["1", "2"], "option_label": ["A", "B"]}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        h = study_plan.plan_of(s)["hypotheses"][0]
        assert (h["iv"], h["dv"]) == ("team", "minuten")                     # the rename is followed
        assert not any("Studienplan" in f.message for f in design.check(s))


def test_changes_after_the_start_are_deviations_and_the_plan_is_preregistered(app, client, survey):
    _questions(client, app)
    _plan(client)
    fill_consent(client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        s = db.session.get(Survey, 1)
        [(name, *_rest)] = prereg.listing(s)
    with zipfile.ZipFile(io.BytesIO(client.get(f"/surveys/1/export/preregistration/{name}").data)) as z:
        text = z.read("study-plan.md").decode()
    assert "H1: Gruppe A pendelt länger als B" in text and "Verfahren: t-Test (Welch)" in text and "Ziel: 120" in text
    with app.app_context():
        ids = [h["id"] for h in study_plan.plan_of(db.session.get(Survey, 1))["hypotheses"]]
    r = _plan(client, sample_target="150", h_id=ids).data.decode()
    assert "als Abweichung von der Präregistrierung" in r
    with app.app_context():
        [dev] = study_plan.plan_of(db.session.get(Survey, 1))["deviations"]
        assert dev["what"] == "Stichprobe"


def test_wizard_step_and_survey_file_keep_the_plan(app, client, survey):
    from app import transfer

    r = client.post("/surveys/1/setup/researchdesign", data=MultiDict({"research_question": "Warum?", "h_id": [""],
                    "h_text": ["Mehr Vertrauen, mehr Akzeptanz"], "h_kind": ["correlation"], "h_dir": ["pos"], "h_iv": [""],
                    "h_dv": [""], "h_analysis": [""], "wizard": "next"}))
    assert r.status_code == 302
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert study_plan.plan_of(s)["hypotheses"][0]["text"] == "Mehr Vertrauen, mehr Akzeptanz"
        data = transfer.dumps(s)
    assert "Mehr Vertrauen, mehr Akzeptanz" in data
    assert study_plan.clean({"hypotheses": [{"text": "x", "kind": "evil", "analysis": "rm -rf"}]})["hypotheses"][0]["kind"] == "correlation"


def test_method_finder_and_templates(app, client, survey):
    """Package C: the research goal suggests a design; a study template becomes a draft with plan and dummies."""
    from app import study_templates

    _plan(client, goal="causal", design="cross")
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "eine Wirkung prüfen (Experiment): Experiment mit Zufallszuteilung." in page and "Gewählt ist „Querschnittsbefragung“" in page
    assert "Aus einer Vorlage" in client.get("/surveys/new").data.decode()
    for key in study_templates.TEMPLATES:
        r = client.post(f"/surveys/template/{key}", follow_redirects=True)
        assert "Dummy-Teilnahmen" in r.data.decode(), key
        if key == "technikakzeptanz":  # TAM needs a documented permission (PR #1187): left out, and said so (Ralf: A)
            assert "nicht allgemein freigegeben" in r.data.decode()
    with app.app_context():
        made = db.session.scalars(db.select(Survey).filter(Survey.id > 1)).all()
        assert len(made) == len(study_templates.TEMPLATES)
        tam = next(s for s in made if not study_plan.plan_of(s)["hypotheses"])  # its hypotheses were all on TAM
        keys = {(i.config.get("construct") or {}).get("key") for b in tam.blocks for i in b.items}
        assert "tb" in keys and not keys & {"tam_pu", "tam_peou", "tam_bi"}
        assert all(study_plan.plan_of(s)["hypotheses"] for s in made if s.id != tam.id)
        experiment = next(s for s in made if s.title.startswith("Online-Experiment"))
        h = study_plan.plan_of(experiment)["hypotheses"][0]
        assert h["iv"] == "bedingung" and study_plan.suggest(experiment, h) == "t"
        assert sum(1 for r in experiment.responses if r.source == "dummy") == study_templates.DUMMIES
    assert "(2)" in client.post("/surveys/template/experiment", follow_redirects=True).data.decode()


def test_results_per_hypothesis(app, client, survey):
    """Package E: the planned test on the data pool, the verdict and the APA sentence; explorative after the start."""
    from app import hypotheses

    _questions(client, app)
    _plan(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        table = [{"gruppe": "1", "minuten": str(40 + i % 5)} for i in range(40)] + \
                [{"gruppe": "2", "minuten": str(20 + i % 5)} for i in range(40)]
        [h1, h2] = hypotheses.evaluate(s, table)
    assert h1["analysis"] == "t" and h1["verdict"] == "unterstützt" and h1["sentence"].startswith("t(")
    assert "δ* = " in h1["sentence"] and "p < ,001" in h1["sentence"]
    assert h2["verdict"] == "offen" and "Offene Forschungsfrage" in h2["notes"][0]
    with app.app_context():
        flipped = hypotheses.evaluate(db.session.get(Survey, 1), [{**r, "gruppe": "2" if r["gruppe"] == "1" else "1"} for r in table])
    assert flipped[0]["verdict"] == "Gegenteil"
    from app import dummy

    with app.app_context():
        dummy.generate(db.session.get(Survey, 1), 30, seed=1)
        db.session.commit()
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert 'id="hypothesen"' in page and "Gruppe A pendelt länger als B" in page and "Für den Bericht" in page


def test_methods_section_and_exclusions(app, client, survey):
    """Package F: the exclusions as a sentence on Qualitätssicherung, and a draft of the methods section (Word and
    Markdown, German and English) with sample, plan and the results per hypothesis."""
    from app import dummy

    _questions(client, app)
    _plan(client)
    client.post("/surveys/1/pool", data={"source": ["real", "dummy"]})
    with app.app_context():
        dummy.generate(db.session.get(Survey, 1), 40, seed=2)
        db.session.commit()
    page = client.get("/surveys/1/quality").data.decode()
    assert 'id="methodenteil"' in page and ("vollständigen Teilnahmen" in page)
    md = client.get("/surveys/1/export/methods-section.md").data.decode()
    assert "## Stichprobe" in md and "angestrebt: 120" in md and "## Auswertung" in md and "H1: Gruppe A pendelt länger als B" in md
    assert "## Datenbereinigung" in md and "Speeder unter 50 % des Medians" in md
    en = client.get("/surveys/1/export/methods-section.md?lang=en").data.decode()
    assert "## Sample" in en and "Tested with" in en
    r = client.get("/surveys/1/export/methods-section.docx")
    assert r.status_code in (200, 302)


def test_methods_page_is_the_one_methods_section(app, client, survey):
    """UI/UX audit 2026-10-05, V29 to V31: the page counts the data pool of the results (dummies with a note), shows the
    study plan with its hypotheses, offers Word in both languages; demographics say whether questions or only answers
    are missing; the look without colour values."""
    from app import dummy

    _plan(client, goal="describe", design="cross")
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = s.blocks[-1].items[-1]
        item.code, item.config = "alter", {**item.config, "validation": "integer"}
        s.data_pool = {"sources": ["dummy"]}
        dummy.generate(s, 20)
        db.session.commit()
    page = client.get("/surveys/1/methods").data.decode()
    assert "Dummy-Daten im Datenpool" in page and "Abgeschlossen</dt><dd>0<" not in page   # the dummies are counted
    assert "Studienplan und Hypothesen" in page and "Entwurf als Word (Englisch)" in page
    assert "Keine validierten Konstrukte" in page and "#2a5db0" not in page
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.data_pool = {"sources": ["real"]}
        db.session.commit()
    page = client.get("/surveys/1/methods").data.decode()
    assert "Noch keine Antworten zu Alter im Datenpool" in page and "enthält keine Fragen zu Alter" not in page
    data = client.get("/surveys/1/data").data.decode()
    assert "Methodenteil öffnen" in data and "Methodenteil, Entwurf (Englisch, Word)" not in data


def test_the_design_is_the_arrangement_not_a_method():
    """Conjoint, MaxDiff and the price questions are elements, not designs: a survey with them is cross-sectional;
    plans that still say so read as "cross"; repeated measures is a design of its own (Philipp, 2026-10-06)."""
    assert set(study_plan.DESIGNS) == {"cross", "experiment", "within", "vignettes", "panel", "trend"}  # trend: Ralf 2026-10-27 (DDI)
    assert [study_plan.design_key(v) for v in ("conjoint", "maxdiff", "psm", "within", "nonsense", None)] == \
        ["cross", "cross", "cross", "within", "", ""]
    assert all(f[0] in study_plan.DESIGNS for f in study_plan.FINDER.values())


def test_plan_saved_with_a_former_design_still_opens_the_settings(app, client, survey):
    """Plans saved before 2026-10-06 can hold a method as design ("conjoint", "maxdiff", "psm") or a goal the page
    no longer offers; the settings page answered 500 for such a survey (KeyError in settings_page.summaries)."""
    for design_value in ("conjoint", "maxdiff", "psm"):
        with app.app_context():
            db.session.get(Survey, 1).study_plan = {"goal": "gone", "design": design_value}
            db.session.commit()
        page = client.get("/surveys/1/researchdesign")
        assert page.status_code == 200, design_value
        assert "Querschnittsbefragung" in page.get_data(as_text=True)
        with app.app_context():
            plan = study_plan.plan_of(db.session.get(Survey, 1))
            assert (plan["goal"], plan["design"]) == ("", "cross")
            assert "Querschnittsbefragung" in study_plan.markdown(db.session.get(Survey, 1))


def test_several_research_goals(app, client, survey):
    """Several goals in one survey (Ralf, 2026-10-06): ticks, one suggestion each, all in the pre-registration; a plan
    from before with one "goal" reads as that one."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.study_plan = {"goal": "difference", "design": "cross"}
        db.session.commit()
        assert study_plan.plan_of(s)["goals"] == ["difference"]
    data = MultiDict([("research_question", "Q"), ("goal", "preference"), ("goal", "difference"), ("design", "cross")])
    client.post("/surveys/1/researchdesign", data=data)
    with app.app_context():
        s = db.session.get(Survey, 1)
        plan = study_plan.plan_of(s)
        assert plan["goals"] == ["difference", "preference"] and plan["goal"] == "difference"   # in the order of GOALS
        text = study_plan.markdown(s, "de")
        assert "- Erkenntnisziele: Gruppen vergleichen; Präferenzen für Eigenschaften messen" in text
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert page.count('name="goal"') == len(study_plan.GOALS) and page.count("checked") >= 2
    assert "Gruppen vergleichen: Querschnittsbefragung." in page and "Präferenzen für Eigenschaften messen: Querschnittsbefragung." in page


def test_plan_is_a_page_of_its_own(app, client, survey):
    """Between "Einstellungen" and "Fragebogen" (Ralf, 2026-10-06), no longer a group of the settings."""
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert 'aria-current="page"' in page.split("Studienplan</a>")[0].rsplit("<a ", 1)[1] and "Forschungsfrage" in page
    tabs = page.split('class="survey-tabs"')[1]
    assert tabs.index("Einstellungen") < tabs.index("Studienplan") < tabs.index("Fragebogen")
    assert 'id="studienplan"' not in client.get("/surveys/1").data.decode()
    r = client.post("/surveys/1/researchdesign", data={"research_question": "Q", "design": "cross"})
    assert r.headers["Location"].endswith("/surveys/1/researchdesign")


def test_the_variables_are_named_by_the_kind_of_hypothesis(app, client, survey):
    """A relationship has no predictor and outcome (Ralf, 2026-10-07): Variable A/B, Gruppen/Verglichen wird,
    Bedingung/Zielgröße; the form carries all names for the browser to switch."""
    assert study_plan.roles("correlation")["iv"] == "Variable A" and study_plan.roles("difference")["dv"] == "Verglichen wird"
    assert study_plan.roles("causal")["iv"] == "Bedingung" and study_plan.roles("open")["iv"] == "Einflussgröße"
    _questions(client, app)
    _plan(client)
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert '<span data-role="iv">Gruppen</span>' in page and '<span data-role="iv">Einflussgröße</span>' in page   # H1 difference, H2 open
    assert "data-plan-roles" in page and "Variable A" in page
    with app.app_context():
        text = study_plan.markdown(db.session.get(Survey, 1), "de")
        assert "verglichen wird minuten" in text and "Gruppen gruppe" in text, text[text.index("## Hypothesen"):][:400]


def test_hypotheses_of_a_diary_study_are_not_tested(app, client, survey):
    """Code review 2026-10-15, C4: in a diary study the rows are entries, several of one person; a test over them
    takes them as independent cases. The hypothesis keeps its groups and says why there is no test."""
    from app import hypotheses

    _questions(client, app)
    _plan(client)
    rows = [{"gruppe": "1" if i % 2 else "2", "minuten": str(30 + i % 7 + (5 if i % 2 else 0))} for i in range(80)]
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert hypotheses.evaluate(s, rows)[0]["p"] is not None
        s.sampling = {"modules": []}
        h1 = hypotheses.evaluate(s, rows)[0]
        assert h1.get("p") is None and h1["verdict"] == "offen" and not h1["sentence"] and "sensitivity" not in h1
        assert len(h1["groups"]) == 2 and any("Tagebuchstudie: kein Test" in n for n in h1["notes"])


def test_descriptive_assumption_names_one_variable_and_reports_its_value(app, client, survey):
    """Pre-test 2026-10-20, 6: "Level X has the highest part-worth" fitted no type. The kind "Beschreibende Annahme"
    names the measured variable only, is planned as "nur beschreiben", has no test and no power, and the results
    report the value (M, SD, Mdn for a number; the most frequent answer for options)."""
    _questions(client, app)
    _plan(client, h_kind=["descriptive", "descriptive"], h_iv=["gruppe", ""], h_dv=["", "minuten"])
    with app.test_request_context("/"):
        s = db.session.get(Survey, 1)
        h1, h2 = study_plan.plan_of(s)["hypotheses"]
        assert (h1["iv"], h1["dv"], h1["analysis"]) == ("", "gruppe", "describe")   # what explains moved over
        assert (h2["iv"], h2["dv"]) == ("", "minuten")
        assert study_plan.suggest(s, h2) == "describe" and study_plan.tested(s) == []
        assert study_plan.power_of(s, h2)["analysis"] == "" and study_plan.status(s)["measured"] == 2
        assert not [f for f in study_plan.findings(s) if "Studienplan" in f.message]
        table = [{"gruppe": "1", "minuten": "10"}, {"gruppe": "1", "minuten": "20"}, {"gruppe": "2", "minuten": "30"}]
        r1, r2 = hypotheses.evaluate(s, table)
        assert r1["verdict"] == r2["verdict"] == "beschrieben" and r1.get("p") is None
        assert r1["sentence"].startswith("am häufigsten „A“: 66,7 %") and r1["n"] == 3
        assert r2["sentence"] == "M = 20,00, SD = 10,00, Mdn = 20,00"
        assert "Gemessene Größe minuten" in study_plan.markdown(s, "de")
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "Beschreibende Annahme (Wert, Anteil, Rangfolge)" in page and "data-plan-iv hidden" in page
    editor = client.get("/surveys/1/editor/items/3").data.decode()
    assert "Gemessene Größe" in editor and "deren Wert, Anteil oder Rangfolge beschrieben wird" in editor
    assert f'value="{h2["id"]}:iv"' not in editor and f'value="{h2["id"]}:dv"' in editor


def test_element_editor_explains_each_role(app, client, survey):
    """Pre-test 2026-10-20, 11: each box of "Im Studienplan" says what its role means for the type of hypothesis."""
    _questions(client, app)
    _plan(client)
    editor = client.get("/surveys/1/editor/items/3").data.decode()
    assert "Je Hypothese ankreuzen, welche Rolle dieses Element darin spielt" in editor
    assert "teilt die Befragten in Gruppen" in editor and "wird zwischen den Gruppen verglichen" in editor
    assert "Unterschied zwischen Gruppen" in editor


def test_a_label_or_answer_in_a_hypothesis_sentence_is_text(app, client, survey):
    """Audit 2026-10-22, C-01: the sentence of a descriptive assumption quotes the most frequent answer, by its label or
    (without one) the answer itself; it went onto the results page as raw HTML, an overlay or a redirect for everyone
    who opened it. Only the italics and subscripts of the statistics stay HTML."""
    from datetime import datetime, timedelta

    from werkzeug.datastructures import MultiDict

    from app import hypotheses
    from app.extensions import db
    from app.models import Response, Survey
    from tests.conftest import add

    payload = '<meta http-equiv=refresh content=0;url=https://evil.example/>'
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict({"code": "gruppe", "question": "Gruppe?", "option_id": ["a", "b"],
                                                             "option_code": ["1", "2"], "option_label": [payload, "B"]}))
    client.post("/surveys/1/researchdesign", data=MultiDict({
        "research_question": "Q?", "goal": "difference", "design": "cross",
        "h_id": [""], "h_text": ["beschreibend"], "h_kind": ["descriptive"], "h_dir": [""], "h_iv": [""], "h_dv": ["gruppe"],
        "h_analysis": [""], "sample_target": "10", "recruitment": "convenience", "sample_note": "", "rules": ""}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for code in ("1", "1", "2"):
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                        answers={"2": code}))
        db.session.commit()
    page = client.get("/surveys/1/results").data.decode()
    assert "hyp-apa" in page and payload not in page and "&lt;meta http-equiv=refresh" in page
    assert str(hypotheses.sentence_html("<i>t</i>(12) = 2,1, <i>p</i><sub>Holm</sub> = ,04 <b>x</b>")) == (
        "<i>t</i>(12) = 2,1, <i>p</i><sub>Holm</sub> = ,04 &lt;b&gt;x&lt;/b&gt;")


def test_a_deviation_is_dated_in_local_time(app, client, survey, monkeypatch):
    """Audit 2026-10-22, G-07: a change of the plan after the start was dated by the UTC date, at 00:30 summer time the
    day before, while every other date of the app is local (app/timeutil.py)."""
    from datetime import date

    from app import study_plan, timeutil

    monkeypatch.setattr(timeutil, "local_today", lambda now=None: date(2026, 10, 24))
    assert study_plan._today() == "2026-10-24"
