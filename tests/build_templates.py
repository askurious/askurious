"""Builds the study templates of app/data/templates/ (app/study_templates.py) through the app itself, so every element
is saved by its own editor route and every file reads in like a survey file a person exported.

Not part of the test run (the name does not start with test_). To build the files anew:
    python -m pytest tests/build_templates.py -p no:cacheprovider
"""
import json
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from app import constructs, flow, study_templates, transfer
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import fill_consent
from tests.instrument_permissions import grant_instrument

OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "vorlagen"
E = "/surveys/1/editor"


def _new(client, title, description):
    client.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})
    client.post("/surveys/1/meta", data={"part": "befragung", "title": title, "description": description, "languages": ["de"], "default_language": "de"})
    fill_consent(client)
    # no text of its own: a new survey from the template gets the standard text with its creator and the estimated
    # duration (study_templates._fill_consent; UI/UX audit 2026-10-16, 1)
    client.post(f"{E}/items/1", data={"code": "consent", "question": "Einwilligung", "body": "", "duration": "5",
                                      "confirm_voluntary": "1", "confirm_data": "1", "confirm_commitment": "1",
                                      "agree": "Weiter", "disagree": "Ich möchte nicht teilnehmen"})


def _add(client, kind, block=2):
    client.post(f"{E}/blocks/{block}/items", data={"type": kind})
    with client.application.app_context():
        return db.session.scalar(db.select(Item.id).order_by(Item.id.desc()))


def _choice(client, iid, code, question, options):
    rows = [("code", code), ("question", question), ("answer_mode", "nudge")]
    for n, label in enumerate(options, 1):
        rows += [("option_id", f"o{n}"), ("option_code", str(n)), ("option_label", label)]
    client.post(f"{E}/items/{iid}", data=MultiDict(rows))


def _likert(client, iid, code, question, statements):
    rows = [("code", code), ("question", question), ("points", "5"), ("labeling", "full"), ("answer_mode", "nudge")]
    if len(statements) >= 2:  # an attitude scale whose mean the plan tests (a matrix is ordinal by default, 2026-10-23)
        rows.append(("level", "metric"))
    for n, text in enumerate(statements, 1):
        rows += [("stmt_id", f"s{n}"), ("stmt_code", ""), ("stmt_text", text)]
    client.post(f"{E}/items/{iid}", data=MultiDict(rows))


# an example value for the expected effect of every tested hypothesis, so the power analysis shows on the template
# (UI/UX audit 2026-10-07, W16)
EXAMPLE = ("Beispielwert der Vorlage: kleinster Effekt, der praktisch zählt; vor der Erhebung mit Literatur oder einer "
           "Vorstudie begründen")


def _plan(client, question, goal, design, hypotheses, target, recruitment, rules):
    data = MultiDict({"research_question": question, "goal": goal, "design": design, "sample_target": str(target),
                      "recruitment": recruitment, "sample_note": "", "rules": rules})
    for text, kind, direction, iv, dv, *effect in hypotheses:
        for key, value in (("h_id", ""), ("h_text", text), ("h_kind", kind), ("h_dir", direction), ("h_iv", iv),
                           ("h_dv", dv), ("h_analysis", ""), ("h_effect", str(effect[0]) if effect else ""),
                           ("h_basis", "sesoi" if effect else ""), ("h_basis_note", EXAMPLE if effect else ""),
                           ("h_power", "0.8")):
            data.add(key, value)
    client.post("/surveys/1/researchdesign", data=data)


def _save(app, key):
    with app.app_context():
        text = transfer.dumps(db.session.get(Survey, 1), with_files=False)
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(text)
    with app.app_context():  # the wording of scales of others stays with the installation (constructs.INSTRUMENTS)
        study_templates.strip_instruments(data)
    data["exported_at"] = "2026-10-05T00:00:00Z"  # the same file whenever it is built again
    (OUT / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


RULES = "Speeder (unter 50 % der Median-Dauer); wer die Aufmerksamkeitsprüfung falsch beantwortet"


def test_kundenzufriedenheit(app, client):
    _new(client, "Zufriedenheit mit der Mensa",
         "Wir möchten wissen, wie zufrieden Sie mit der Mensa sind und was wir verbessern können.")
    nps = _add(client, "rating")
    client.post(f"{E}/items/{nps}", data={"code": "empfehlung", "question": "Wie wahrscheinlich ist es, dass Sie die Mensa weiterempfehlen?",
                                          "style": "numbers", "min": "0", "max": "10", "left": "sehr unwahrscheinlich",
                                          "right": "sehr wahrscheinlich", "answer_mode": "nudge"})
    _likert(client, _add(client, "likert"), "zufriedenheit", "Wie sehr stimmen Sie den folgenden Aussagen zu?",
            ["Das Essen schmeckt mir.", "Die Preise sind angemessen.", "Die Wartezeit ist kurz.", "Es gibt genug vegetarische Gerichte."])
    _choice(client, _add(client, "single_choice"), "gruppe", "Sie essen in der Mensa als …", ["Studierende", "Beschäftigte", "Gast"])
    text = _add(client, "text_input")
    client.post(f"{E}/items/{text}", data={"code": "verbessern", "question": "Was sollten wir als Erstes verbessern?",
                                           "multiline": "1", "answer_mode": "optional"})
    _plan(client, "Wie zufrieden sind die Gäste der Mensa, und was treibt die Weiterempfehlung?", "describe", "cross",
          [("Wer zufriedener ist, empfiehlt die Mensa eher weiter.", "correlation", "pos", "zufriedenheit", "empfehlung", 0.3),
           ("Studierende, Beschäftigte und Gäste unterscheiden sich in der Weiterempfehlung.", "difference", "", "gruppe", "empfehlung", 0.3)],
          150, "convenience", RULES)
    _save(app, "kundenzufriedenheit")


def test_technikakzeptanz(app, client):
    _new(client, "Akzeptanz einer Lern-App",
         "Wir untersuchen, wie Studierende eine Lern-App für die Prüfungsvorbereitung beurteilen und ob sie sie nutzen würden.")
    for key in ("tam_pu", "tam_peou", "tam_bi", "tb"):
        if key in constructs.PERMISSION_REQUIRED:  # only to build the file: _save keeps the structure, not the wording
            grant_instrument(app, key)
        client.post(f"{E}/blocks/2/items", data={"type": f"construct:{key}"})
    with app.app_context():
        codes = [i.code for b in db.session.get(Survey, 1).blocks for i in b.items if i.type != "consent"]
    pu, peou, bi = "PU", "PEOU", "BI"  # the variable names the constructs bring along
    assert {pu, peou, bi} <= set(codes)
    _plan(client, "Was bestimmt, ob Studierende eine Lern-App nutzen wollen?", "correlation", "cross",
          [("Je nützlicher die App erscheint, desto höher die Nutzungsabsicht.", "correlation", "pos", pu, bi, 0.3),
           ("Je einfacher die App zu bedienen ist, desto höher die Nutzungsabsicht.", "correlation", "pos", peou, bi, 0.3)],
          94, "convenience", RULES)   # the completions of the power analysis (UI/UX audit 2026-10-09, X5)
    _save(app, "technikakzeptanz")


def test_experiment(app, client):
    _new(client, "Wirkung eines Plakattextes",
         "Sie lesen einen kurzen Plakattext und beantworten danach einige Fragen.")
    for _ in range(2):
        client.post(f"{E}/blocks")                                               # blocks 3 and 4
    client.post(f"{E}/blocks/2", data={"title": "Text: Nutzen"})
    client.post(f"{E}/blocks/3", data={"title": "Text: Risiken"})
    a = _add(client, "info", block=2)
    client.post(f"{E}/items/{a}", data={"code": "text_nutzen", "question": "**Das Deutschlandticket spart Ihnen jeden Monat Geld und Nerven.** Bus und Bahn statt Stau und Parkplatzsuche."})
    b = _add(client, "info", block=3)
    client.post(f"{E}/items/{b}", data={"code": "text_risiken", "question": "**Wer nicht umsteigt, zahlt drauf.** Steigende Sprit- und Parkkosten treffen vor allem Autofahrende."})
    _likert(client, _add(client, "likert", block=4), "einstellung", "Wie sehr stimmen Sie den folgenden Aussagen zu?",
            ["Ich würde das Deutschlandticket kaufen.", "Das Deutschlandticket ist sein Geld wert.", "Ich würde es Freunden empfehlen."])
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1},
            {"t": "rand", "id": "r1", "code": "bedingung", "name": "Plakattext", "k": 1, "balanced": True, "shuffle": False,
             "children": [{"t": "block", "block": 2}, {"t": "block", "block": 3}]},
            {"t": "block", "block": 4}]}
        flow.normalize(s)
        db.session.commit()
    _plan(client, "Wirkt ein Text, der den Nutzen betont, stärker als einer, der vor Nachteilen warnt?", "causal", "experiment",
          [("Der Text mit dem Nutzen führt zu einer positiveren Einstellung als der Text mit den Risiken.", "causal", "", "bedingung", "einstellung", 0.5)],
          143, "convenience", RULES)
    _save(app, "experiment")


def test_conjoint(app, client):
    from tests.analysis.test_conjoint import conjoint_form

    _new(client, "Präferenzen für einen Pflegeroboter",
         "Sie sehen mehrmals zwei Pflegeroboter und wählen jeweils den, den Sie eher nutzen würden.")
    cbc = _add(client, "conjoint")
    client.post(f"{E}/items/{cbc}", data=conjoint_form(none=True))
    client.post(f"{E}/items/{cbc}/conjoint/design")
    _plan(client, "Welche Eigenschaften eines Pflegeroboters zählen für die Wahl am meisten?", "preference", "cross",
          [("Welche Eigenschaft bestimmt die Wahl am stärksten: Preis, Datenschutz oder Aussehen?", "open", "", "", "")],
          200, "panel", RULES)
    _save(app, "conjoint")


def test_vignetten(app, client):
    from tests.analysis.test_vignettes import FORM

    _new(client, "Urteile über Pflegeroboter",
         "Sie lesen kurze Beschreibungen von Pflegerobotern und beurteilen jeweils, wie gut Sie den Einsatz finden.")
    urteil = _add(client, "likert")
    r = client.post(f"{E}/blocks/2/loop")
    nid = r.headers["Location"].split("/loop/")[1].split("?")[0]
    client.post(f"/surveys/1/flow/loop/{nid}", data={"action": "source_factors"})
    page = f"/surveys/1/flow/vignettes/{nid}"
    client.post(page, data={**FORM, "attr_id": ["a1", "a2"], "attr_label": ["Aussehen", "Einsatzort"], "attr_code": ["", ""],
                            "lvl_a3_id": [], "lvl_a3_label": [], "lvl_a3_def": [], "lvl_a3_hint": []})
    client.post(page, data={"action": "generate"})
    # the question names the factors of the vignette ({{aussehen}}, {{einsatzort}}: the columns of the loop)
    _likert(client, urteil, "urteil", "Ein Pflegeroboter mit dem Aussehen „{{aussehen}}“ hilft im Einsatzort „{{einsatzort}}“. "
            "Wie sehr stimmen Sie zu?", ["Diesen Roboter fände ich an diesem Ort gut eingesetzt."])
    _plan(client, "Wie hängt das Urteil über einen Pflegeroboter von Aussehen und Einsatzort ab?", "causal", "vignettes",
          [("Wie hängt die Beurteilung humanoider Roboter vom Einsatzort (Pflegeheim oder zu Hause) ab?", "open", "", "", "")],
          100, "convenience", RULES)
    _save(app, "vignetten")


def test_panelbefragung(app, client):
    """A panel survey with quotas (UI/UX audit 2026-10-05, V25): the panel's ID stored once per value, completion
    links for completed, screen-out and quota full, and the quota assistant "Bevölkerung Deutschland"."""
    _new(client, "Mobilität in Deutschland (Panel)",
         "Wir möchten wissen, wie Sie im Alltag unterwegs sind.")
    _choice(client, _add(client, "single_choice"), "verkehrsmittel", "Welches Verkehrsmittel nutzen Sie am häufigsten?",
            ["Auto", "Bus und Bahn", "Fahrrad", "zu Fuß"])
    _likert(client, _add(client, "likert"), "einstellung", "Wie sehr stimmen Sie den folgenden Aussagen zu?",
            ["Ich würde gern öfter ohne Auto unterwegs sein.", "Bus und Bahn sind in meiner Gegend gut erreichbar."])
    client.post("/surveys/1/flow/quotas/assistant", data={"n": "500", "hi": "74"})
    url = "https://panel.example/end?id={{PID}}&status="
    client.post("/surveys/1/panel", data=MultiDict([
        ("param_name", "PID"), ("param_mode", "once"), ("param_required", "1"), ("param_allowed", ""),
        ("redirect_completed", url + "complete"), ("redirect_screenout", url + "screenout"),
        ("redirect_quotafull", url + "quotafull")]))
    _plan(client, "Wie unterscheiden sich Verkehrsmittel und Einstellung nach Alter und Bundesland?", "describe", "cross",
          [("Ältere nutzen seltener das Fahrrad als Jüngere.", "difference", "", "age", "verkehrsmittel", 0.15)],
          500, "quota", RULES)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert len(s.quotas) == 3 and (s.panel or {}).get("redirects", {}).get("quotafull")
    _save(app, "panelbefragung")


@pytest.fixture(autouse=True)
def _built(app):
    yield
