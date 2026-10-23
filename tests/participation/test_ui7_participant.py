"""Pages for participants, languages and right to left (UI/UX audit 2026-10-07, package D: W42 to W57)."""
import glob
import os
import re

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CSS = open(os.path.join(ROOT, "app/static/participant.css"), encoding="utf-8").read()


def _set(app, **values):
    with app.app_context():
        s = db.session.get(Survey, 1)
        for k, v in values.items():
            setattr(s, k, v)
        db.session.commit()


def _start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return f"/s/{db.session.get(Survey, 1).code}"


def test_every_range_input_runs_left_to_right_on_arabic_pages():
    """W42: the fill of a range input is a gradient "to right"; a mirrored thumb would show another amount. Every
    range input of the participant pages carries .p-range, and the right-to-left rule keeps .p-range left to right."""
    rule = re.search(r"([^{}]*)\{ direction: ltr; \}", CSS[CSS.index("/* Right to left"):]).group(1)
    assert '[dir="rtl"] .p-range' in rule
    for path in glob.glob(os.path.join(ROOT, "app/templates/items/*/participant.html")):
        for tag in re.findall(r"<input[^>]*type=\"range\"[^>]*>", open(path, encoding="utf-8").read()):
            assert "p-range" in tag, (path, tag)


def test_endpoint_anchors_and_missing_bar_follow_the_scale():
    """W43: the anchors under each statement end where the scale ends, not under "weiß nicht"; W51: the bar of a
    missing row stands at its start also right to left."""
    assert ".p-likert-dk .p-likert-row > .p-endpoints" in CSS and "padding-inline-end" in CSS
    assert '[dir="rtl"] .p-likert-row.p-missing, [dir="rtl"] .p-sd-row.p-missing { box-shadow: inset -4px 0 0' in CSS


def test_a_language_switched_on_later_shows_its_own_standard_wordings(app, client, survey):
    """W44: buttons of the consent and federal states in Arabic, not the German texts stored when the elements were
    made; a wording the researchers changed stays (it falls back as before)."""
    fill_consent(client)  # stores the former standard button "Fortsetzen"
    add(client, "federal_state")  # 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "en", "ar"]
        consent, states = db.session.get(Item, 1), db.session.get(Item, 2)
        assert consent.t("ar")["agree"] == "التالي"  # "Next" in Arabic
        assert consent.t("ar")["disagree"] == "Nein"  # an own wording stays
        assert states.t("ar")["options"]["s9"] == "بافاريا"
        assert states.t("ar")["question"] != states.t("de")["question"]
        assert consent.text_lang("disagree", "ar") == "de" and consent.text_lang("agree", "ar") == "ar"


def test_untranslated_question_says_its_language(app, client, survey):
    """W45: a question that falls back to German on an Arabic page carries lang="de" and dir="auto"."""
    add(client, "text_input")  # 2
    client.post("/surveys/1/editor/items/2", data={"code": "age", "question": "Wie alt sind Sie?"})
    _set(app, languages=["de", "en", "ar"])
    client.get("/surveys/1/editor/preview?lang=ar&page=2&device=desktop")
    page = client.get("/surveys/1/editor/preview?lang=ar").data.decode()
    # in the preview also marked as not translated yet (S9), in the researcher's language (Ralf, 2026-10-07)
    assert 'lang="de" dir="auto" data-untranslated="noch nicht übersetzt">Wie alt sind Sie?' in page
    assert '[dir="rtl"] .p-question, [dir="rtl"] .p-option span' in CSS


def test_dual_response_names_the_chosen_option():
    """W46: the follow-up waits for the choice and names it (participant.js); without the script it stays as it was."""
    html = open(os.path.join(ROOT, "app/templates/items/conjoint/participant.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "app/static/participant.js"), encoding="utf-8").read()
    assert "data-cj-dual=" in html and "data-cj-named=" in html and "data-cj-name=" in html
    assert 'ui("Option", "Option")' in html and 'or "Option"' not in html
    assert "dual.hidden = !checked && !answered" in js and ".cj-dual[hidden] { display: none; }" in CSS


def test_preview_shows_progress_and_language_switch_as_the_participation(app, client, survey):
    """W47: the progress bar only when the survey shows it; the language switch for participants."""
    fill_consent(client)
    add(client, "info")
    client.post("/surveys/1/editor/items/2", data={"code": "hi", "question": "Hallo"})
    _set(app, show_progress=False, language_choice=True)
    client.get("/surveys/1/editor/preview?page=2&device=desktop")
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert 'class="p-progress"' not in page and 'class="p-langs"' in page
    _set(app, show_progress=True, language_choice=False)
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert 'class="p-progress"' in page and 'class="p-langs"' not in page
    # the switch sends the page along and comes back in the other language
    r = client.post("/surveys/1/editor/preview?lang=de", data={"set_lang": "en", "pos": ""})
    assert r.status_code == 302 and "lang=en" in r.headers["Location"]


def test_preview_end_shows_the_deletion_note(app, client, survey):
    """W47: the end of the preview shows the deletion code (an example) as participants see it."""
    fill_consent(client)
    add(client, "info")
    client.post("/surveys/1/editor/items/2", data={"code": "hi", "question": "Hallo"})
    _set(app, erase_code=True)
    client.get("/surveys/1/editor/preview?restart=1")
    client.post("/surveys/1/editor/preview?lang=de", data=CONSENT_OK)
    client.post("/surveys/1/editor/preview?lang=de", data={"nav": "next"})
    page = client.get("/surveys/1/editor/preview?lang=de").data.decode()
    assert "Vorschau beendet" in page
    assert "K7MP-2QXD-9RTW" in page and "Beispielcode" in page
    _set(app, erase_code=False)
    client.get("/surveys/1/editor/preview?restart=1")
    client.post("/surveys/1/editor/preview?lang=de", data=CONSENT_OK)
    client.post("/surveys/1/editor/preview?lang=de", data={"nav": "next"})
    page = client.get("/surveys/1/editor/preview?lang=de").data.decode()
    assert "in diesem Browser auf der Seite „Datenschutz“ löschen" in page


def test_dont_know_is_one_dashed_card(app, client, survey):
    """W48: the tick of a rating or a date looks like the column of a matrix and says it replaces the answer."""
    html = open(os.path.join(ROOT, "app/templates/items/_dont_know.html"), encoding="utf-8").read()
    assert 'ui("(statt einer Angabe)", "(instead of an answer)")' in html
    assert re.search(r"\.p-option\.p-dk \{[^}]*border-style: dashed", CSS)


def test_many_points_and_preview_bar_on_phones():
    """W49: 8 to 10 points put the statement above its answers; W50: the tools of the preview bar wrap on a phone."""
    html = open(os.path.join(ROOT, "app/templates/items/likert/participant.html"), encoding="utf-8").read()
    assert "p-likert-many" in html and ".p-likert-many.p-likert-full .p-statement" in CSS
    assert re.search(r"\.p-preview-bar \.p-preview-tools \{[^}]*flex-wrap: wrap", CSS)


def test_consent_and_pages_name_the_step_the_same(app, client, survey):
    """W53: the standard button of the consent is "Weiter", as on every other page."""
    from app.items.consent import DEFAULT_TEXTS

    assert DEFAULT_TEXTS["de"]["agree"] == "Weiter" and DEFAULT_TEXTS["en"]["agree"] == "Next"
    assert "„Weiter“" in DEFAULT_TEXTS["de"]["agree_note"]


def test_erase_page_with_own_participation_folds_the_code_form(app, client, survey):
    """W52: with a participation in this browser its form comes first, the one for a code is folded."""
    fill_consent(client)
    add(client, "info")
    client.post("/surveys/1/editor/items/2", data={"code": "hi", "question": "Hallo"})
    _set(app, erase_code=True)
    url = _start(app, client)
    p = app.test_client()
    page = p.get(url + "/delete").data.decode()
    assert "p-erase-other" not in page and 'name="code"' in page  # no participation here: the code form as before
    p.get(url)
    p.post(url, data=CONSENT_OK)
    page = p.get(url + "/delete").data.decode()
    assert '<details class="p-erase-other">' in page and "Eine andere Teilnahme mit Löschcode löschen" in page
    assert page.index('name="here"') < page.index('name="code"')


def test_messages_say_what_to_do(app, client, survey):
    """W56: a number with a range says the range once; a matrix says how many statements are open."""
    from app.items.base import AnswerError

    add(client, "text_input")  # 2
    client.post("/surveys/1/editor/items/2", data={"code": "age", "validation": "integer", "min": "14", "max": "99",
                                                   "answer_mode": "required", "question": "Alter"})
    add(client, "likert")  # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "TA"), ("answer_mode", "required"), ("points", "5"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"), ("stmt_id", "s2"), ("stmt_code", ""),
        ("stmt_text", "B"), ("stmt_id", "s3"), ("stmt_code", ""), ("stmt_text", "C")]))
    with app.app_context():
        age, matrix = db.session.get(Item, 2), db.session.get(Item, 3)
        try:
            age.kind.parse_answer(age, {"i2": ""}, "de")
        except AnswerError as e:
            assert str(e) == "Bitte geben Sie eine Zahl von 14 bis 99 ein."
        else:
            raise AssertionError("no message")
        try:
            matrix.kind.parse_answer(matrix, {"i3_s1": "2"}, "de")
        except AnswerError as e:
            assert str(e) == "Bitte beantworten Sie alle Aussagen (noch offen: 2)."
        else:
            raise AssertionError("no message")


def test_privacy_page_in_short_points(app, client, survey):
    """W57: one point per statement, the protection against mass requests folded away."""
    fill_consent(client)
    add(client, "info")
    client.post("/surveys/1/editor/items/2", data={"code": "hi", "question": "Hallo"})
    url = _start(app, client)
    page = app.test_client().get(url + "/privacy").data.decode()
    assert '<ul class="p-privacy-points">' in page and '<details class="p-privacy-tech">' in page
    assert page.index("Was gespeichert wird") < page.index("Technische Einzelheiten") < page.index("Antworten löschen")


def test_language_switch_looks_like_links():
    """W54: the languages to pick in accent and semi-bold, the current one framed (since U44 of the UI audit
    2026-10-12 with the stronger edge and a filled ground)."""
    assert re.search(r"\.p-langs a \{ color: var\(--accent\);[^}]*font-weight: 600", CSS)
    assert re.search(r"\.p-langs a\[aria-current\] \{[^}]*border-color: var\(--control-edge\)", CSS)


def test_equal_heights_of_buttons_and_cards():
    """W55: both buttons with a 1-px border, the choice field of a CBC as tall as an answer card."""
    assert re.search(r"\.p-button \{[^}]*border: 1px solid transparent", CSS)
    assert re.search(r"\.cj-pick \{[^}]*padding: 12px 10px; font-size: 1rem", CSS)
    assert re.search(r"\.p-csum-num input \{[^}]*padding: 12px 14px; [^}]*border-radius: var\(--radius\)", CSS)
