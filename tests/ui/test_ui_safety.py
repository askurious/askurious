"""UI audit F1 to F4: questions before losing access, the consent in the trash, messages that stay in
view, and the start check on the field page."""
from app.extensions import db
from app.models import Survey
from tests.conftest import add, fill_consent

E = "/surveys/1/editor"


def test_members_are_asked_before_leaving_removing_or_changing_the_own_role(app, client, survey):
    with app.app_context():
        from app.models import SurveyMember

        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid="0000-0002-1825-0097", name="Josiah", role="editor", position=1))
        db.session.commit()
    html = client.get("/surveys/1").data.decode()
    assert 'data-confirm="Befragung verlassen?" data-confirm-note="Sie verlieren sofort den Zugriff' in html   # own row
    assert 'data-confirm="Josiah entfernen?" data-confirm-note="Der Zugriff auf diese Befragung endet sofort' in html
    form = lambda name: html.split(f'aria-label="Rolle von {name}"')[0].rsplit("<form", 1)[1]
    assert 'data-confirm="Eigene Rolle ändern?"' in form("Philipp")
    assert 'data-current="admin"' in html.split('aria-label="Rolle von Philipp"')[1][:60]   # "Abbrechen" shows it again
    assert "data-confirm" not in form("Josiah")                                 # other people: no question, an admin can undo it


def test_questions_are_asked_in_the_app_never_by_the_browser():
    """No confirm(), alert() or prompt() of the browser in pages or scripts: questions go through askConfirm
    (data-confirm), errors into the page's messages."""
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[2] / "app"
    files = list((app_dir / "templates").rglob("*.html")) + list((app_dir / "static").glob("*.js"))
    native = re.compile(r"(?<![\w.])(?:window\.)?(confirm|alert|prompt)\s*\(")
    found = [f"{f.relative_to(app_dir)}:{n}" for f in files for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
             if native.search(line) and "Promise.resolve(window.confirm(" not in line]   # askConfirm without <dialog>
    assert found == []


def test_removed_consent_waits_in_the_trash_with_its_text(app, client, survey):
    fill_consent(client)
    add(client, "info")                                                 # 2
    with app.app_context():
        before = db.session.get(Survey, 1).consent_item
        texts, config = dict(before.texts), dict(before.config)
        assert texts["de"]["body"] == "Text"
    assert "Einwilligung entfernen (kommt in den Papierkorb)" in client.get(E + "/").data.decode()   # no question: it can be undone
    html = client.post(E + "/items/1/delete", follow_redirects=True).data.decode()
    assert "Die Einwilligung liegt im Papierkorb" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.consent_item is None and [e.data["type"] for e in s.trash] == ["consent"]
        entry_id = s.trash[0].id
    # a new, empty consent in the meantime: the old one cannot come back next to it
    client.post(E + "/blocks/2/items", data={"type": "consent"})
    html = client.post(f"{E}/trash/{entry_id}/restore", follow_redirects=True).data.decode()
    assert "Es gibt bereits eine Einwilligung" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert len(s.trash) == 1 and s.consent_item.texts["de"].get("body", "") != "Text"
        new_id = s.consent_item.id
    client.post(f"{E}/items/{new_id}/delete")
    with app.app_context():
        assert len(db.session.get(Survey, 1).trash) == 2
    html = client.post(f"{E}/trash/{entry_id}/restore", follow_redirects=True).data.decode()
    assert "Einwilligung mit Text und Eckdaten wiederhergestellt" in html
    with app.app_context():
        from app import flow

        s = db.session.get(Survey, 1)
        item = s.consent_item
        assert item.texts == texts and item.config == config and item.required
        assert [[i.type for i in b.items] for b in s.blocks] == [["consent"], ["info"]]
        assert flow.tree(s)["children"][0] == {"t": "block", "block": item.block_id}
        assert [e.data["type"] for e in s.trash] == ["consent"]            # the empty one stays in the trash


def test_messages_stay_in_view_and_can_be_told_apart(app, client, survey):
    html = client.post("/surveys/1/end-texts", data={}, follow_redirects=True).data.decode()   # lands on #end-texts
    assert '<div class="flashes" data-flashes>' in html
    assert 'class="flash flash-success" role="status"><span class="flash-text">Texte der Endseiten gespeichert.' in html
    plain = client.get("/surveys/1").data.decode()
    assert "data-flashes" not in plain                                    # no empty box without a message
    css = open("app/static/app.css", encoding="utf-8").read()
    assert ".flashes { position: sticky;" in css


def test_field_page_says_before_the_click_whether_the_survey_can_start(app, client, survey):
    add(client, "info")                                                 # 2
    html = client.get("/surveys/1/field").data.decode()
    assert "Noch nicht startklar:" in html and "Einwilligungstext fehlt" in html
    assert 'href="/surveys/1/editor/items/1"' in html                   # the error leads to its question
    start = html.split("</svg> Befragung starten")[0].rsplit("<button", 1)[1]
    assert "disabled" in start and "data-confirm" not in start            # locked, nothing to confirm
    fill_consent(client)
    html = client.get("/surveys/1/field").data.decode()
    assert "Startklar: Die Startprüfung meldet keine Fehler." in html and "Noch nicht startklar" not in html
    start = html.split("</svg> Befragung starten")[0].rsplit("<button", 1)[1]
    assert "disabled" not in start and 'data-confirm="„Test“ starten?"' in start and "Präregistrierung" in start
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    assert "Startklar" not in client.get("/surveys/1/field").data.decode()  # only a draft is checked


def test_lifecycle_words_and_dashboard_lead_to_the_next_step(app, client, survey):
    """UI audit F6, F7, F25: closing is not called final, the locked editor names the real way to change
    the questionnaire, and the dashboard card opens what matters in the survey's state."""
    fill_consent(client)
    add(client, "info")                                                 # 2

    def card():
        return client.get("/dashboard/").data.decode().split('<li class="card')[1].split("</li>")[0]

    draft = card()
    assert 'class="card-title" href="/surveys/1/editor/"' in draft and "/surveys/1/results" in draft  # results also in a draft (Ralf, 2026-10-05)
    editor = client.get(E + "/").data.decode()
    assert "verschiebbar · entfernbar" in editor and "Fehler müssen vor dem Start" in editor     # hints of a draft

    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    running = card()
    assert 'class="card-title" href="/surveys/1/field"' in running and "</svg> Datenerhebung</a>" in running
    assert "Ergebnisse" in running and "Fragebogen" in running
    field = client.get("/surveys/1/field").data.decode()
    assert "Endgültig schließen" not in field and ">Schließen …</button>" in field
    assert "Beides lässt sich später unter „Datenerhebung fortsetzen“ wieder öffnen." in field
    editor = client.get(E + "/").data.decode()
    assert "Zum Ändern zuerst pausieren" not in editor and "verschiebbar · entfernbar" not in editor
    assert "Fehler müssen vor dem Start" not in editor
    assert "brauchen eine neue Fragebogen-Fassung; dafür zuerst pausieren oder schließen" in editor
    assert 'href="/surveys/1/field?edit=1#reopen"' in editor

    client.post("/surveys/1/close", data={"pause": "1"})
    paused = card()
    assert 'class="card-title" href="/surveys/1/field#status"' in paused and "</svg> Datenerhebung fortsetzen</a>" in paused
    field = client.get("/surveys/1/field").data.decode()
    assert "</svg> Datenerhebung fortsetzen</button>" in field and "Wieder öffnen" not in field
    assert '<details class="reopen-box" id="reopen" >' in field           # closed until asked for
    assert '<details class="reopen-box" id="reopen" open>' in client.get("/surveys/1/field?edit=1").data.decode()
    assert "Die Befragung ist pausiert:" in client.get(E + "/").data.decode()

    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post("/surveys/1/close")
    closed = card()
    assert 'class="card-title" href="/surveys/1/results"' in closed and "</svg> Ergebnisse</a>" in closed
    assert "Die Befragung ist geschlossen:" in client.get(E + "/").data.decode()


def test_questions_only_where_nothing_comes_back_and_with_the_objects_name(app, client, survey):
    """UI audit F5: what can be undone needs no question (the message says how); what cannot be undone asks
    and names the object."""
    fill_consent(client)
    add(client, "info")                                                 # 2
    client.post(E + "/items/2", data={"code": "hallo", "question": "Hallo"})
    client.post(E + "/blocks/2", data={"title": "Szenario A"})
    editor = client.get(E + "/").data.decode()
    assert "Einwilligung entfernen?" not in editor                        # goes to the trash
    # deleting a block is not asked about either: "Rückgängig" brings it back whole (tests/editor/test_undo.py)
    assert "data-confirm" not in editor.split("/blocks/2/delete")[1].split("</form>")[0]
    html = client.post(E + "/blocks/2/delete", follow_redirects=True).data.decode()
    assert "„Szenario A“ gelöscht. 1 Frage liegt zusätzlich 7 Tage im Papierkorb" in html and ">Rückgängig</button>" in html
    assert "hallo" in html.split('id="trash"')[1]
    assert 'data-confirm="\u201ehallo\u201c endg\u00fcltig l\u00f6schen?" data-confirm-note="Das l\u00e4sst sich nicht' in html
    # archiving: no question on the card, no latch on the field page, and a message that says where it went
    card = client.get("/dashboard/").data.decode().split('class="card ')[1].split("</li>")[0]
    assert "data-confirm" not in card.split('/archive"')[1].split("</form>")[0]   # archiving; deleting from the archive does ask
    html = client.post("/surveys/1/archive", data={"archived": "1"}, follow_redirects=True).data.decode()
    assert "„Test“ ist archiviert. Unter „Archiv“ am Ende der Übersicht" in html
    assert "ist wieder in der Übersicht" in client.post("/surveys/1/archive", data={"archived": "0"}, follow_redirects=True).data.decode()


def test_refused_settings_come_back_as_entered_and_say_so_once(app, client, survey):
    """UI audit F9 to F11: an error keeps what was typed and is shown inside its form; no "Gespeichert."
    next to an error; forms with their own save button are marked for the unsaved-changes guard (a running survey;
    the settings of a draft save by themselves, tests/editor/test_autosave.py)."""
    page = client.get("/surveys/1").data.decode()
    assert page.count("data-dirty-guard") == 0 and page.count("data-autosave-form") == 7
    # panel parameters: a wrong name used to throw away everything that was typed
    html = client.post("/surveys/1/panel", data={"param_name": ["pid", "zwei worte"], "param_mode": ["store", "redirect"],
                                                  "param_required": ["0", "0"], "param_allowed": ["", "a, b"],
                                                  "redirect_completed": "https://example.org/done"}).data.decode()
    assert "Nicht gespeichert:" in html and "Ungültiger Parametername „zwei worte“" in html and "data-scroll-here" in html
    assert 'value="zwei worte"' in html and 'value="a, b"' in html and 'value="https://example.org/done"' in html
    assert "Teilnahmelinks gespeichert" not in html
    with app.app_context():
        assert not (db.session.get(Survey, 1).panel or {}).get("params")   # nothing was saved
    # project data without a title
    html = client.post("/surveys/1/meta", data={"title": " ", "description": "Neue Beschreibung", "languages": ["de"],
                                                 "default_language": "de"}).data.decode()
    assert "Nicht gespeichert:" in html and "Der Titel der Befragung darf nicht leer sein." in html and "Neue Beschreibung" in html
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.title == "Test" and (s.description or "") == ""
    # a taken variable name: one message, and it says what happened to the rest
    add(client, "info")                                                 # 2
    add(client, "info")                                                 # 3
    client.post(E + "/items/2", data={"code": "eins", "question": "A"})
    client.get(E + "/")                                                  # shows (and so clears) that save's message
    html = client.post(E + "/items/3", data={"code": "eins", "question": "Neuer Text"}, follow_redirects=True).data.decode()
    assert "Der Variablenname „eins“ ist schon vergeben. Der bisherige Name „Q2“ bleibt; alles andere wurde gespeichert." in html
    assert "Gespeichert." not in html.split("data-flashes")[1].split("</div>")[0]
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, 3)
        assert item.code == "Q2" and item.texts["de"]["question"] == "Neuer Text"
    # the block head has no button to forget: ticks save at once
    editor = client.get(E + "/").data.decode()
    assert "data-block-option" in editor and "<noscript><button" in editor


def test_results_and_export_share_the_pool_and_texts_name_real_places(app, client, survey):
    """UI audit F12, F14, F26: the data pool chosen on one page can be taken to the other; changed results
    are marked; guidance texts name real places; background actions report a failure."""
    fill_consent(client)
    add(client, "info")                                                 # 2
    client.post("/surveys/1/quality", data={"source": ["real", "dummy"], "drop": "1"})   # the pool is set once, for the survey
    results = client.get("/surveys/1/results").data.decode()
    assert "Datenpool: Echte Teilnahmen, Dummy-Daten" in results and 'href="/surveys/1/quality"' in results
    # pre-test 2026-10-20, 12: German capitalises the phrase after the colon, English keeps lower case
    assert "Nicht gespeichert: Ändert nur diese Seite" in results
    assert results.split("<strong>Verwendete Daten:</strong>")[1].split()[:2] == ["Echte", "Teilnahmen,"]
    client.post("/account/language", data={"lang": "en"})
    english = client.get("/surveys/1/results").data.decode()
    assert "Data pool: real participations, dummy data" in english
    assert english.split("<strong>Data used:</strong>")[1].split()[:2] == ["real", "participations,"]
    client.post("/account/language", data={"lang": "de"})
    assert "Neu berechnen" not in results and "data-results-stale" not in results  # computed by itself when needed
    data = client.get("/surveys/1/data").data.decode()
    assert 'value="dummy" checked' in data and 'name="drop" value="0" checked' in data   # leaving speeders out: chosen here
    assert "unter die Linie" in data and "Anlegen im Fragebogen unter" not in data
    script = open("app/static/app.js", encoding="utf-8").read()
    assert script.count("actionFailed(") >= 5 and "if (!r.ok) { actionFailed(r.status); return; }" in script


def test_only_drafts_without_real_participations_can_be_deleted(app, client, survey):
    """UI audit F8: abandoned set-ups and try-outs can go; whatever collected real answers can only be archived."""
    import os

    from app import lifecycle
    from app.models import Item, Response, SurveyLabel, TrashedItem

    fill_consent(client)
    add(client, "info")                                                 # 2
    client.post(E + "/items/2/delete")                                  # something in the trash
    add(client, "info")                                                 # 3
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.responses.append(Response(source="dummy", status="completed", answers={}, is_preview=True))
        db.session.add(SurveyLabel(label="meine-studie", survey_id=1))
        folder = os.path.join(app.config["UPLOAD_FOLDER"], s.uid)
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, "bild.png"), "wb").write(b"x")
        db.session.commit()
        assert lifecycle.deletable(s)
    # the overview only archives; deleting is offered in the archive, and refused anywhere else
    assert 'action="/surveys/1/delete"' not in client.get("/dashboard/").data.decode()
    html = client.post("/surveys/1/delete", follow_redirects=True).data.decode()
    assert "Entwürfe lassen sich nur aus dem Archiv löschen" in html
    html = client.post("/surveys/1/archive", data={"archived": "1"}, follow_redirects=True).data.decode()
    assert "lässt sie sich wiederherstellen oder endgültig löschen" in html
    card = client.get("/dashboard/?archived=1").data.decode().split('class="card ')[1].split("</li>")[0]
    assert "Endgültig löschen …</button>" in card                              # in the menu "⋯" (UI/UX audit 2026-10-16)
    # asked, with the survey's name
    assert 'action="/surveys/1/delete"' in card and 'data-confirm="Entwurf \u201eTest\u201c endg\u00fcltig l\u00f6schen?"' in card
    html = client.post("/surveys/1/delete", follow_redirects=True).data.decode()
    assert "„Test“ wurde endgültig gelöscht." in html and "Keine archivierten Befragungen." in html
    with app.app_context():
        assert db.session.get(Survey, 1) is None
        assert not db.session.scalars(db.select(Item)).all() and not db.session.scalars(db.select(Response)).all()
        assert not db.session.scalars(db.select(TrashedItem)).all()
        label = db.session.scalar(db.select(SurveyLabel))
        assert label.label == "meine-studie" and label.survey_id is None   # the label stays taken
    assert not os.path.exists(folder)
    # a survey that has real participations, or is not a draft, stays
    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey))
        sid, code = s.id, s.code
        s.responses.append(Response(source="real", status="active", answers={}))
        db.session.commit()
        assert not lifecycle.deletable(s)
    assert f'action="/surveys/{code}/delete"' not in client.get("/dashboard/").data.decode()
    html = client.post(f"/surveys/{code}/delete", follow_redirects=True).data.decode()
    assert "Nur Entwürfe ohne echte Teilnahmen lassen sich löschen" in html
    with app.app_context():
        assert db.session.get(Survey, sid) is not None


def test_consent_text_follows_what_is_recorded_and_wizard_asks_in_that_order(app, client, survey):
    """UI audit F16: the options the consent names come before the consent step; an untouched standard
    text follows them later too; an edited text gets a reminder in the pre-launch check."""
    from app import design
    from app.wizard import KEYS

    assert KEYS.index("ablauf") < KEYS.index("einwilligung")
    client.post("/surveys/1/setup/consent", data={"min_age": "18", "researchers": "0000-0000-0000-001X"})
    with app.app_context():
        body = db.session.get(Survey, 1).consent_item.texts["de"]["body"]
        assert body and "technische Angaben" not in body
    # switched on later in the settings: the untouched standard text follows
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de", "en"], "default_language": "de",
                                         "allow_back": "1", "paradata": "1", "page_timing": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        body = s.consent_item.texts["de"]["body"]
        assert "technische Angaben" in body and "wie lange Sie auf jeder Seite" in body
        assert not [f for f in design.check(s) if "der Text nennt das nicht" in f.message]
    # an edited text is not touched; the pre-launch check says what is missing
    client.post(E + "/items/1", data={"code": "consent", "question": "Einwilligung", "body": "Eigener Text ohne diese Angaben.",
                                      "researchers": "0000-0000-0000-001X", "agree": "Ja", "disagree": "Nein"})
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de", "en"], "default_language": "de",
                                         "allow_back": "1", "paradata": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.consent_item.texts["de"]["body"] == "Eigener Text ohne diese Angaben."
        notes = [f.message for f in design.check(s) if "der Text nennt das nicht" in f.message]
        assert any("technische Angaben" in n and "Deutsch (Sie)" in n for n in notes) and not any("Zeit je Seite" in n for n in notes)
    # steps already reached stay open for a visit after going back
    html = client.get("/surveys/1/setup/languages").data.decode()
    assert 'aria-label="Schritt 1: Befragung"' in html


def test_consent_duration_far_from_the_estimate_is_reported(app, client, survey):
    """UI audit F15: the consent names a duration (default 10 minutes); the pre-launch check reminds when the
    questionnaire is estimated far shorter or longer."""
    from app import design

    fill_consent(client)                                                # duration 5
    add(client, "single_choice")                                        # 2: a few seconds
    with app.app_context():
        s = db.session.get(Survey, 1)
        notes = [f for f in design.check(s) if "Genannt sind etwa" in f.message]
        assert len(notes) == 1 and "5 Minuten" in notes[0].message and notes[0].item_id == 1
        consent = s.consent_item
        consent.config = {**consent.config, "duration": 1}
        db.session.commit()
        assert not [f for f in design.check(s) if "Genannt sind etwa" in f.message]
    script = open("app/static/app.js", encoding="utf-8").read()
    assert "inner.scrollTop" in script                                   # F27: the editor's own scroll area is remembered


def test_option_lists_are_edited_the_same_way_and_rows_keep_their_order(app, client, survey):
    """UI audit F23: every list of options, statements or elements gets the same row controls from app.js
    (rows with class new-row mark such a list); the server stores the rows in the order they are sent."""
    from werkzeug.datastructures import MultiDict

    from app.models import Item

    for kind in ("single_choice", "ranking", "likert", "slider", "constant_sum"):
        add(client, kind)
    for item_id in range(2, 7):
        html = client.get(f"{E}/items/{item_id}").data.decode()
        assert 'class="new-row"' in html and "mit × entfernen" in html, item_id
    script = open("app/static/app.js", encoding="utf-8").read()
    assert 'rows.querySelector("tr.new-row")' in script and 'data-row' in script
    # moved rows arrive in their new order, a removed one is simply not sent, a new one brings its own id
    client.post(f"{E}/items/2", data=MultiDict([
        ("code", "farbe"), ("question", "Farbe?"),
        ("option_id", "neu123"), ("option_code", "3"), ("option_label", "grün"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "rot")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert [(o["id"], o["code"]) for o in item.config["options"]] == [("neu123", "3"), ("a", "1")]
        assert item.texts["de"]["options"] == {"neu123": "grün", "a": "rot"}


def test_small_things_on_researcher_pages(app, client, survey):
    """UI audit F29 to F33, F39, F40: hints that say what happens, the consent without the answer-mode
    switch, the status first in a draft, language changes with a message."""
    editor = client.get(E + "/").data.decode()
    assert "den Sie zuletzt angeklickt haben" in editor
    consent = client.get(E + "/items/1").data.decode()
    assert "Antwort erzwingen?" not in consent
    add(client, "text_input")                                           # 2
    client.post(E + "/items/2", data={"code": "t", "question": "Text"})
    assert "Antwort erzwingen?" in client.get(E + "/items/2").data.decode()
    field = client.get("/surveys/1/field").data.decode()
    assert field.index('id="status"') < field.index("Teilnahmelink")      # draft: the start comes first
    assert "Befragung starten" in field and "beginnend mit einem Buchstaben" in field
    fill_consent(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    field = client.get("/surveys/1/field").data.decode()
    assert field.index('id="status"') < field.index("Teilnahmelink")      # running: status and timeline first (Ralf, 2026-10-11)
    assert "Pausieren</button>" in field and "Schließen …</button>" in field
    assert "button[title], a[title]" in open("app/static/app.js", encoding="utf-8").read()


def test_language_changes_say_what_happened(app, client, survey):
    html = client.post("/surveys/1/meta", data={"title": "Test", "languages": ["en"], "default_language": "de",
                                                "allow_back": "1"}, follow_redirects=True).data.decode()
    assert "Deutsch (Sie) bleibt als Ausgangssprache ausgewählt" in html
    html = client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de"], "default_language": "de",
                                                "allow_back": "1"}, follow_redirects=True).data.decode()
    assert "Sprache entfernt: English. Die Übersetzungen bleiben gespeichert" in html
    with app.app_context():
        assert db.session.get(Survey, 1).languages == ["de"]


def test_an_archived_survey_with_data_goes_only_when_meant(app, client):
    from app import lifecycle
    from app.models import Response

    client.post("/surveys/new", data={"title": "Mit Daten", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Mit Daten"))
        sid, code = s.id, s.code
        s.responses.append(Response(source="real", status="completed", answers={}))
        s.archived = True
        db.session.commit()
        assert not lifecycle.deletable(s)
    card = client.get("/dashboard/?archived=1").data.decode()
    assert 'name="with_data"' in card and "Mit allen Daten löschen" in card and f'data-confirm-type="{code}"' in card   # type the code
    client.post(f"/surveys/{code}/delete")                                               # not from that button
    with app.app_context():
        assert db.session.get(Survey, sid) is not None
    with client.session_transaction() as sess:
        sess["signed_in_at"] = __import__("time").time()                                 # a fresh sign-in
    client.post(f"/surveys/{code}/delete", data={"with_data": "1"})
    with app.app_context():
        assert db.session.get(Survey, sid) is None and not db.session.scalar(db.select(Response).filter_by(survey_id=sid))
