"""UI/UX audit 2026-10-09, packages A (planning and creating, X1 to X16) and F (the English pages and the colours,
X81 to X91): what changed for the researchers, and the tests that guard the English pages against fixed decimal
commas in templates and per cent signs in scripts."""
from tests.instrument_permissions import grant_instrument
import glob
import os
import re

import pytest
from werkzeug.datastructures import MultiDict

from app import hypotheses, sample_model, study_plan
from app.extensions import db
from app.models import Survey
from tests.conftest import add, experiments_on
from tests.ui.test_researcher_i18n import _strip_calls
from tests.analysis.test_sample_model import _plan, _questions

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EN = {"headers": {"Accept-Language": "en"}}


def english(client):
    client.post("/account/language", data={"lang": "en"})


# --- package A -------------------------------------------------------------------------------------------------


def test_x1_a_new_survey_claims_no_ethics_review(app, client, survey):
    """The consent of a new survey does not say "reviewed by an ethics committee" until someone ticks it."""
    with app.test_request_context():
        consent = db.session.get(Survey, 1).consent_item
        assert consent.config["ethics"] is False
        assert "Ethikkommission" not in consent.kind.template_body(consent, "de")
    page = client.get("/surveys/1/setup/consent").data.decode()
    assert 'name="ethics" value="1" checked' not in page


def test_x2_dummy_texts_name_the_research_plan(client, survey):
    page = client.get("/surveys/1/field").data.decode()
    assert "folgen dem Studienplan (erwarteter Effekt), alles andere ist zufällig" in page
    assert "zufälligen, gültigen Antworten" not in page


def test_x4_names_of_the_variables_by_kind(app, client, survey):
    _questions(client)
    _plan(client, h_iv=["km"], h_dv=[""])
    with app.test_request_context():
        found = [f.message for f in study_plan.findings(db.session.get(Survey, 1))]
    assert any("(Variable A und Variable B)" in m for m in found), found
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "Einfluss- und Zielgröße" not in page and "die beiden Variablen aus dem Fragebogen" in page


def test_x5_x7_x10_target_reserve_exclusions_and_one_name(app, client, survey):
    _questions(client)
    _plan(client, sample_target="120")
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "Ziel 120 liegt über den 94 nötigen Abschlüssen (Reserve +26)." in page               # X5
    assert re.search(r'name="sample_exclusions"[^>]*value="10"', page)                          # X7: a real value
    assert "Die Zahl rechnet der" not in page and "Planer" not in page                        # X10
    assert "Ziel 120 liegt über" in client.get("/surveys/1/field").data.decode()               # the status says it too
    m = {"basis": "power", "target": 80, "completions": 94}
    with app.test_request_context(**EN):
        assert sample_model.reserve_text(m) == "Target 80 is below the 94 completions needed (14 short)."
        assert sample_model.reserve_text({**m, "target": 94}) == ""
        assert sample_model.reserve_text({**m, "basis": "target"}) == ""
    _plan(client, h_effect=[""], sample_target="200")                                           # no power analysis
    assert "Rechner Stichprobengröße" in client.get("/surveys/1/researchdesign").data.decode()


def test_x5_the_template_targets_the_power_analysis():
    import json

    with open(os.path.join(ROOT, "app", "data", "vorlagen", "technikakzeptanz.json"), encoding="utf-8") as f:
        assert json.load(f)["survey"]["study_plan"]["sample"]["target"] == 94


def test_x6_standard_questions_and_names_in_the_language_of_the_text(app, client, survey):
    add(client, "construct:age")
    add(client, "construct:gender")
    with app.test_request_context():
        consent = db.session.get(Survey, 1).consent_item
        copy = consent.kind.pool_copy(consent)
        assert "demografische Standardfragen (Alter, Geschlecht)" in copy["de"]
        assert "standard demographic questions (Age, Gender)" in copy["en"] and "Alter" not in copy["en"]
        assert "operator" not in copy["en"] and "provider of Askurious (see legal notice)" in copy["en"]
    add(client, "construct:bfi10")
    with app.test_request_context():
        consent = db.session.get(Survey, 1).consent_item
        copy = consent.kind.pool_copy(consent)
        assert "standardisierte Fragebögen (" in copy["de"] and "Alter" not in copy["de"].split(")")[0]
        assert "Big Five" in copy["en"]


def test_x9_results_name_the_construct_not_the_instruction(app, client, survey):
    grant_instrument(client.application, "tam_pu")
    add(client, "construct:tam_pu")
    grant_instrument(client.application, "tam_bi")
    add(client, "construct:tam_bi")
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        labels = [label for _, _, label, _ in hypotheses._vars(s, {"iv": "PU", "dv": "BI"})]
    assert labels == ["Wahrgenommene Nützlichkeit", "Nutzungsabsicht"], labels


def test_x12_the_wizard_says_which_step_of_how_many(client):
    client.post("/surveys/new", data={"title": "Assistent", "languages": ["de"], "default_language": "de"})
    page = client.get("/surveys/1/setup/consent").data.decode()
    assert re.search(r'class="wizard-count">Schritt \d+ von \d+ · ', page)


def test_x14_open_question_without_direction_and_method_and_directions_by_kind(app, client, survey):
    with app.test_request_context():
        assert study_plan.directions("correlation")["pos"] == "positiv"
        assert study_plan.directions("difference")["neg"] == "A kleiner"
    with app.test_request_context(**EN):
        assert study_plan.directions("correlation")["neg"] == "negative"
    _questions(client)
    _plan(client, h_kind=["open"])
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert "<label data-plan-dir hidden>" in page and '<label class="plan-method" hidden>' in page
    assert "data-plan-dirs" in page


def test_x15_ainstein_label_in_both_languages(client):
    experiments_on(client, "ainstein")
    page = client.get("/ainstein/").data.decode()
    assert "in Entwicklung" in page and "IN ACTIVE DEVELOPMENT" not in page
    assert "AInstein erkennt in Ihrem Text:" in page


def test_x16_cards_show_when_they_changed(client, survey):
    assert re.search(r"geändert \d\d\.\d\d\.\d{4}", client.get("/dashboard/").data.decode())


# --- package F -------------------------------------------------------------------------------------------------


COMMENT = re.compile(r"\{#.*?#\}", re.S)
# what holds numbers that are no shown decimals: a pattern's quantifier {1,39}, lists of targets in data attributes
NOT_SHOWN = re.compile(r'\b(?:data-[\w-]+|pattern|style|class|id)="[^"]*"')


def template_decimal_commas(source):
    """Numbers with a decimal comma written into a researcher's template outside de_en()/ui() (X81): on an English
    page they would stay German ("±1,96 SE", "p < 0,001")."""
    text = NOT_SHOWN.sub("", _strip_calls(COMMENT.sub("", source), ("de_en", "ui")))
    return re.findall(r"(?<![\w.,])\d+,\d+(?![\w,])", text)


def test_x81_no_decimal_comma_fixed_in_a_researchers_template():
    found = []
    for path in glob.glob(os.path.join(ROOT, "app", "templates", "**", "*.html"), recursive=True):
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        if rel.startswith(("app/templates/participant/", "app/templates/public/")):
            continue
        with open(path, encoding="utf-8") as f:
            found += [f"{rel}: {n}" for n in template_decimal_commas(f.read())]
    assert not found, found
    assert template_decimal_commas('(±1,96 SE) {{ de_en("0,5", "0.5") }} {# 2,5 #} pattern="[a]{1,39}"') == ["1,96"]


def script_spaced_per_cent(source):
    """String literals of a script outside de_en() that put " %" after a number (X82): English writes 50%."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"(?m)^\s*//.*$", "", source)
    found = []
    for m in re.finditer(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'', _strip_calls(source, ("de_en", "uiT"))):
        text = m.group(1) if m.group(1) is not None else m.group(2)
        if re.match(r" %", text) or re.search(r"\d %", text):
            found.append(text)
    return found


def test_x82_scripts_write_per_cent_by_the_language_of_the_page():
    found = []
    for path in glob.glob(os.path.join(ROOT, "app", "static", "*.js")):
        if os.path.basename(path) == "participant.js" or os.path.basename(path).startswith("participant-"):
            continue
        with open(path, encoding="utf-8") as f:
            found += [f"{os.path.basename(path)}: {t}" for t in script_spaced_per_cent(f.read())]
    assert not found, found
    assert script_spaced_per_cent('x + " %"; y + " → 90 %"; de_en(" %", "%"); \'"> %</label>\'') == [" %", " → 90 %"]


def test_x82_x83_samplesize_sentence_and_speed_line():
    with open(os.path.join(ROOT, "app", "static", "samplesize.js"), encoding="utf-8") as f:
        js = f.read()
    assert '"{n} usable participations → {p} power"' in js and '+ " Power"' not in js
    with open(os.path.join(ROOT, "app", "static", "app.js"), encoding="utf-8") as f:
        assert '+ " Min."' not in f.read()


def test_x85_messages_from_python_follow_the_page(app):
    from app import ai, svg_safe, vignettes

    with app.test_request_context(**EN):
        with pytest.raises(svg_safe.Refused) as e:
            svg_safe.clean(b'<svg xmlns="http://www.w3.org/2000/svg"><style>a{}</style><rect/></svg>')
        assert e.value.args[0].startswith("The SVG file sets colours")
        assert vignettes.problems(None, {"attributes": [{"code": "f1", "levels": ["a"]}]}) == [
            "Factor f1: at least two levels."]
        assert ai._failure(ai.de_en("Bildgenerierung", "Image generation"), ValueError()).startswith("Image generation failed: ")
    with app.test_request_context():
        assert vignettes.problems(None, {"attributes": []}) == ["Mindestens einen Faktor anlegen."]


def test_x86_sources_and_conditions_in_english(app):
    from app import apa, retention

    with app.test_request_context(**EN):
        shown = str(apa.ref(retention.GDPR)["html"])
        assert "Regulation (EU) 2016/679" in shown and "Verordnung" not in shown
        assert "Documentation: " in str(apa.ref("Dokumentation: Rammstedt, B. (2014). Titel. Zeitschrift, 1(2), 3–4.")["html"])
    with app.test_request_context():
        assert "Verordnung (EU)" in str(apa.ref(retention.GDPR)["html"])


def test_x87_content_of_the_survey_carries_its_language(client, survey):
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "f1", "question": "Wie alt sind Sie?"})
    english(client)
    page = client.get("/surveys/1/editor/").data.decode()
    assert re.search(r'class="item-text" lang="de">Wie alt sind Sie\?', page)


def test_x88_x90_contrast_and_focus_of_the_small_parts():
    with open(os.path.join(ROOT, "app", "static", "app.css"), encoding="utf-8") as f:
        css = f.read()
    rule = re.search(r"ul\.refs \.ref-use \{[^}]*\}", css).group(0)
    assert "opacity" not in rule
    assert ".comment-shot:focus-visible { outline: var(--focus-outline);" in css
