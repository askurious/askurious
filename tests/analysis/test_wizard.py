import re

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Survey


def test_wizard_walkthrough(app, client):
    r = client.post("/surveys/new", data={"title": "Pflegeroboter", "project": "KI", "description": "Wir untersuchen …"})
    assert r.headers["Location"].endswith("/surveys/1/setup/researchdesign")  # not past the study plan (V22)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.setup_step == "studienplan" and s.consent_item is not None
    assert "Anlegen fortsetzen" in client.get("/dashboard/").data.decode()
    W = "/surveys/1/setup/"
    r = client.post(W + "researchdesign", data={"wizard": "skip"})
    assert r.headers["Location"].endswith("/setup/languages")
    html = client.get(W + "languages").data.decode()
    assert "wizard-step current" in html and "Sprachen" in html
    client.post(W + "languages", data=MultiDict([("languages", "de"), ("languages", "en"), ("default_language", "de")]))
    client.post(W + "team", data={"wizard": "skip"})
    html = client.get(W + "consent").data.decode()
    assert 'name="ethics" value="1" checked' not in html and 'name="open_data" value="1" checked' in html  # X1: ethics unticked
    assert "Mit KI" not in html and html.index("Verantwortliche Forschende") < html.index("Kontakt-E-Mail") \
        < html.index("Ethikvotum") < html.index("Vergütung") < html.index("Open Data")
    client.post(W + "consent", data={"duration": "12", "min_age": "18", "researchers": "0000-0000-0000-001X"})
    with app.app_context():                                          # "Next" puts in the standard text from the facts
        body = db.session.get(Survey, 1).consent_item.texts["de"]["body"]
        assert "12 Minuten" in body and "18" in body
    client.post(W + "consent", data={"duration": "12", "min_age": "16", "researchers": "0000-0000-0000-001X"})
    with app.app_context():                                          # untouched standard text follows the facts
        body = db.session.get(Survey, 1).consent_item.texts["de"]["body"]
        assert "16" in body and "18 Jahre" not in body
    client.post(W + "flow", data={"allow_back": "1", "auto_advance": "1"})
    # look of the pages and image style in one step "Erscheinungsbild" (Ralf, 2026-10-07); optional
    step = client.get(W + "design").data.decode()
    assert 'name="preset" value="askurious" checked' in step and 'name="preset" value="askurious-dark"' in step
    assert "Überspringen" in step and step.index("Farben und Schrift") < step.index("Stil der KI-Bilder</h3>")
    r = client.post(W + "design", data={"preset": "askurious-dark", "key": "flat", "custom": ""})
    assert r.headers["Location"].endswith("/setup/done")
    r = client.post(W + "done", data={"wizard": "back"})
    assert r.headers["Location"].endswith("/setup/design")
    client.post(W + "design", data={"wizard": "skip", "preset": "askurious", "key": ""})   # skipped: nothing is taken
    assert client.get(W + "bildstil").headers["Location"].endswith("/setup/design")      # former addresses
    html = client.get(W + "done").data.decode()
    assert "Flache Vektorgrafik" in html and "Deutsch (Sie) (Ausgangssprache), English" in html and "Forschungsfrage" in html
    assert "<dt>Erscheinungsbild</dt><dd>Askurious dunkel</dd>" in html
    summary = html.split('class="summary"')[1].split("</dl>")[0]
    assert "Getrennter Abschnitt" not in summary and "Einwilligung" not in summary
    r = client.post(W + "done", data={"wizard": "next"})
    assert r.headers["Location"].endswith("/surveys/1/editor/")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.setup_step is None and s.languages == ["de", "en"] and s.auto_advance
        assert s.consent_item.config["duration"] == 12 and s.image_style["key"] == "flat"
    assert "Einrichtung fortsetzen" not in client.get("/dashboard/").data.decode()


def test_wizard_team_step_returns_to_wizard(app, client):
    client.post("/surveys/new", data={"title": "T"})
    r = client.post("/surveys/1/members", data={"orcid": "0000-0002-0000-0001", "name": "Kollegin",
                                                "next": "/surveys/1/setup/team"})
    assert r.headers["Location"].endswith("/surveys/1/setup/team")
    r = client.post("/surveys/1/members", data={"orcid": "0000-0002-0000-0002", "name": "X", "next": "https://evil.example"})
    assert "evil" not in r.headers["Location"]
    assert "Kollegin" in client.get("/surveys/1/setup/team").data.decode()


def test_wizard_requires_title(client):
    html = client.post("/surveys/new", data={"title": ""}, follow_redirects=True).data.decode()
    assert "darf nicht leer sein" in html


def test_first_content_block_is_block_1(app, client):
    from app.extensions import db
    from app.models import Survey

    client.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    client.post("/surveys/1/editor/blocks")
    with app.app_context():
        titles = [b.title() for b in db.session.get(Survey, 1).blocks]
        assert titles[1:] == ["Block 1", "Block 2"]              # the consent block does not count


def test_team_step_returns_to_wizard_with_real_codes(app, client, monkeypatch):
    from app import models
    from app.extensions import db
    from app.models import Survey

    monkeypatch.setattr(models, "new_code", models.random_code)          # like on the server: /surveys/<code>/...
    client.post("/surveys/new", data={"title": "T"})
    with app.app_context():
        code = db.session.get(Survey, 1).code
    back = f"/surveys/{code}/setup/team"
    r = client.post(f"/surveys/{code}/members", data={"orcid": "0000-0002-0000-0001", "name": "Kollegin", "next": back})
    assert r.headers["Location"].endswith(back)                            # stays in the wizard
    r = client.post(f"/surveys/{code}/members", data={"orcid": "0000-0002-0000-0003", "name": "Y",
                                                      "next": f"/surveys/{code}x/setup/team"})
    assert r.headers["Location"].endswith(f"/surveys/{code}#members")       # another survey's path: settings



def test_consent_is_block_0_in_the_editor(app, client):
    client.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    html = client.get("/surveys/1/editor/").data.decode()
    # the line under a block names its pages only: no second count that differs from the names (V101)
    assert "Block 1 · " not in html and "Block 2 · " not in html and 'class="hint block-meta">0 Seiten' in html


def test_new_survey_starts_with_demographics(app, client):
    from app.extensions import db
    from app.models import Survey

    app.config["NEW_SURVEY_DEMOGRAPHICS"] = True
    client.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [b.title() for b in s.blocks][1:] == ["Demographische Angaben", "Block 1"] and s.blocks[0].items[0].type == "consent"
        demo = s.blocks[1]
        assert demo.title() == "Demographische Angaben"
        # gender: GES_1 of the RatSWD (decision 6b of the concept Demografie-Standards 2026-10-15)
        assert [(i.code, i.config["construct"]["key"]) for i in demo.items] == [("age", "age"), ("GES_1", "ratswd_ges1")]
        assert demo.items[0].text("suffix") == "Jahre"                         # age first, then gender
    html = client.get("/surveys/1/editor/").data.decode()
    assert "Demographische Angaben" in html and "Welches Geschlecht haben Sie?" in html and "Wie alt sind Sie?" in html
    client.post("/surveys/1/editor/blocks")                                  # a new block goes to the end
    with app.app_context():
        titles = [b.title() for b in db.session.get(Survey, 1).blocks]
        assert titles[1:] == ["Demographische Angaben", "Block 1", "Block 2"]


def test_consent_step_details_only_with_their_box(app, client):
    from app.extensions import db
    from app.models import Survey

    client.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    W = "/surveys/1/setup/consent"
    html = client.get(W).data.decode()
    assert 'data-reveal="ethics-details"' in html and 'id="compensation-details" class="reveal" hidden' in html
    assert html.index('name="compensation_on"') < html.index('name="compensation"') < html.index('name="open_data"')
    full = {"researchers": "0000-0000-0000-001X", "compensation_box": "1", "ethics": "1", "ethics_ref": "EK 1/26",
            "compensation_on": "1", "compensation": "5 × 20 € Gutscheine", "open_data": "1", "repository": "OSF", "license": ""}
    client.post(W, data=full)
    with app.app_context():
        c = db.session.get(Survey, 1).consent_item
        assert (c.config["ethics_ref"], c.config["repository"], c.config["license"]) == ("EK 1/26", "OSF", "CC BY 4.0")
        assert c.texts["de"]["compensation"] == "5 × 20 € Gutscheine"
    client.post(W, data={k: v for k, v in full.items() if k not in ("ethics", "compensation_on", "open_data")})
    with app.app_context():                                          # boxes off: their texts are empty
        c = db.session.get(Survey, 1).consent_item
        assert (c.config["ethics_ref"], c.config["repository"], c.config["license"]) == ("", "", "")
        assert c.texts["de"]["compensation"] == "" and "Gutschein" not in c.texts["de"]["body"]


def test_wizard_saves_by_itself_and_marks_skipped_steps(app, client):
    """UI/UX audit 2026-10-05, V22 to V24: the steps save like the settings page ("Weiter" only moves on), a skipped
    step gets a dash instead of a tick, and the step "Teilnahme" asks only how people take part."""
    client.post("/surveys/new", data={"title": "T"})
    W = "/surveys/1/setup/"
    page = client.get(W + "researchdesign").data.decode()
    assert "data-autosave-form" in page and "Änderungen werden automatisch gespeichert" in page
    r = client.post(W + "languages", data=MultiDict([("languages", "de"), ("languages", "en"), ("default_language", "de"),
                                                    ("autosave", "1")]))
    assert r.get_json()["status"] == "saved"
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.languages == ["de", "en"] and s.setup_step == "studienplan"   # saved, but not moved on
    client.post(W + "researchdesign", data={"wizard": "skip"})
    client.post(W + "languages", data=MultiDict([("languages", "de"), ("languages", "en"), ("default_language", "de")]))
    page = client.get(W + "team").data.decode()
    steps = page.split('class="wizard-steps"')[1].split("</ol>")[0]
    assert 'class="wizard-step done skipped"' in steps and "(übersprungen)" in steps and steps.count("✓") == 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.erase_code, s.geo_ip = True, True
        db.session.commit()
    page = client.get(W + "flow").data.decode()
    assert 'name="allow_back"' in page and 'name="geo_ip"' not in page and 'name="paradata"' not in page
    assert "Region aus der IP an" in page and "ändern ↗" in page
    assert "data-dirty-guard" in page.split("data-label-form", 1)[1].split(">", 1)[0]
    client.post(W + "flow", data={"allow_back": "1", "autosave": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.allow_back and s.geo_ip and s.erase_code                  # the rest keeps its settings


def test_new_questionnaire_starts_with_three_cards(app, client):
    """UI/UX audit 2026-10-05, V25: "Leer beginnen · Aus einer Vorlage · Aus einer Datei" at the top; each shows its part."""
    page = client.get("/surveys/new").data.decode()
    cards = page.split('class="start-cards"')[1].split("</div>")[0]
    assert ["Leer beginnen", "Aus einer Vorlage", "Aus einer Datei"] == [t for t in ("Leer beginnen", "Aus einer Vorlage", "Aus einer Datei") if t in cards]
    assert 'aria-current="page"' in cards.split("Aus einer Vorlage")[0] and 'name="title"' in page and 'id="vorlagen"' not in page
    page = client.get("/surveys/new?start=vorlage").data.decode()
    # a title of its own per template (UI/UX audit 2026-10-07, W16), no form of the first step
    assert 'id="vorlagen"' in page and 'name="title" value="Panelbefragung mit Quoten"' in page and 'name="project"' not in page
    page = client.get("/surveys/new?start=datei").data.decode()
    assert 'id="import"' in page and 'id="vorlagen"' not in page
    r = client.post("/surveys/new/import", data={})
    assert r.headers["Location"].endswith("/surveys/new?start=datei#import")


def test_source_language_offers_only_the_ticked_languages(app, client, survey):
    """UI/UX audit 2026-10-05, V26: unticked languages are not offered; a change asks once the questionnaire has an element."""
    page = client.get("/surveys/1").data.decode()
    pick = page.split('name="default_language"')[1].split("</select>")[0]
    assert 'value="de" selected' in pick and 'value="pl"  hidden disabled' in pick and "data-lang-confirm" not in pick
    from tests.conftest import add

    add(client, "info")
    page = client.get("/surveys/1").data.decode()
    assert "data-lang-confirm" in page.split('name="default_language"')[1].split(">")[0]


def test_image_style_step_offers_no_ai_images(app, client):
    """Without AI images there is nothing to choose: "Keine KI-Bilder" is a card of its own, ticked for a new survey,
    and picking it after a style takes the style away (Ralf, 2026-10-06)."""
    client.post("/surveys/new", data={"title": "Ohne Bilder"})
    W = "/surveys/1/setup/"
    step = client.get(W + "design").data.decode()
    assert "Keine KI-Bilder" in step and re.search(r'name="key" value=""[^>]* checked', step)  # labelled by id since #1190
    client.post(W + "design", data={"key": "flat", "custom": ""})
    client.post(W + "design", data={"key": "", "custom": ""})
    with app.app_context():
        assert (db.session.get(Survey, 1).image_style or {}).get("key") is None


def test_a_wizard_left_on_a_former_step_continues(app, client):
    """setup_step "bildstil" or "aussehen" from before the merge goes on to "Erscheinungsbild"."""
    client.post("/surveys/new", data={"title": "Alt"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.setup_step = "aussehen"
        db.session.commit()
    assert client.get("/surveys/1/setup/aussehen").headers["Location"].endswith("/setup/design")
    assert client.get("/surveys/1/setup/design").status_code == 200
    with app.app_context():
        assert db.session.get(Survey, 1).setup_step == "gestaltung"
