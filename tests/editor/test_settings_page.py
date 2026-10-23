"""The settings page in seven folded groups (concept "Einstellungen: Bestandsaufnahme und Gliederung", Ralf,
2026-10-03): one line per group says what is set, each group saves on its own, the consent line lists what the
consent names, the test runs are kept on "Datenerhebung", "Duplizieren" is gone from the page."""
import re

from app.extensions import db
from app.models import Survey, SurveyMember
from tests.conftest import fill_consent

OTHER = "0000-0002-1825-0097"


def _page(client):
    return client.get("/surveys/1").data.decode()


def _summary(page, gid):
    return re.sub(r"<[^>]+>|\s+", " ", page.split(f'data-live="sum-{gid}">')[1].split("</span>\n")[0]).strip()


def test_seven_groups_in_order_with_their_lines(app, client, survey):
    page = _page(client)
    ids = re.findall(r'<details class="panel settings-group" id="(\w+)"', page)
    assert ids == ["befragung", "beteiligte", "teilnahme", "einwilligung", "abschlusstexte", "gestaltung"]   # links: "Datenerhebung"; plan: own page
    assert "settings-pair" not in page and "Projektangaben" not in page and 'id="duplicate"' not in page
    assert "Test" in _summary(page, "befragung") and "English" in _summary(page, "befragung")
    assert _summary(page, "beteiligte").startswith("1 Person")
    assert _summary(page, "abschlusstexte") == "Standardtexte"
    assert page.count("data-open-now") == 0 and page.count("data-settings-group open") == 6   # all open (Ralf, 2026-10-03)
    assert 'class="fixed-mark"' in page and 'class="fixed-mark is-fixed"' not in page   # a draft: "wird beim Start fest"


def test_each_group_saves_only_its_own_fields(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.allow_back, s.erase_code = True, True
        db.session.commit()
    # "Teilnahme" without the title and the languages: they stay
    client.post("/surveys/1/meta", data={"part": "teilnahme", "repeat_mode": "multi", "show_progress": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.title == "Test" and s.languages == ["de", "en"] and s.erase_code
        assert not s.allow_back and s.show_progress and s.panel["multi"]
    # "Befragung" leaves the options alone
    client.post("/surveys/1/meta", data={"part": "befragung", "title": "Neu", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.title == "Neu" and s.languages == ["de"] and s.show_progress and s.panel["multi"] and s.erase_code
    # "Datenschutz": deletion code and periods, nothing else
    client.post("/surveys/1/meta", data={"part": "datenschutz", "erase_code_shown": "1", "retention_shown": "1",
                                         "retention_personal": "3", "retention_answers": "60"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not s.erase_code and s.retention == {"personal": 3, "answers": 60} and s.title == "Neu" and s.show_progress
    page = _page(client)
    assert "Daten 5 Jahre" in _summary(page, "einwilligung") and "beliebig oft" in _summary(page, "teilnahme")


def test_a_refused_title_opens_its_group(app, client, survey):
    html = client.post("/surveys/1/meta", data={"part": "befragung", "title": " ", "languages": ["de"],
                                                "default_language": "de"}).data.decode()
    assert "Nicht gespeichert:" in html
    assert re.search(r'id="befragung" data-settings-group open data-open-now', html)
    assert re.search(r'id="teilnahme" data-settings-group open>', html)          # open as well, the browser may fold it


def test_the_consent_line_names_what_the_consent_says_and_where_it_is_set(app, client, survey):
    fill_consent(client)
    line = lambda: _page(client).split('data-live="consent-mentions"')[1].split("</div>")[0]
    assert "Löschcode" not in line() and "technische Angaben" not in line()  # a new survey: periods, no deletion code (pre-test 2026-10-16)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.erase_code, s.retention, s.paradata, s.repeat_marker = False, None, False, False
        db.session.commit()
    assert "nichts davon" in line()
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.erase_code, s.paradata, s.repeat_marker = True, True, True
        s.panel = {"params": [{"name": "PID", "mode": "store", "required": True, "allowed": []}]}
        db.session.commit()
    text = line()
    assert "Löschcode" in text and "technische Angaben" in text and "/surveys/1/quality#prevent" in text
    assert "Cookie gegen erneute Teilnahme" in text and "#teilnahme" in text and "(PID)" in text and "#panel" in text


def test_locks_after_the_start(app, client, survey):
    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "text_input"})
    client.post("/surveys/1/editor/items/2", data={"code": "Q1", "question": "Frage"})
    client.post("/surveys/1/start", data={})
    page = _page(client)
    assert 'class="fixed-mark is-fixed"' in page and "Seit dem Start fest" in page
    assert "Sprachen sind nach dem Start festgelegt" not in page and "Das Mindestalter ist nach dem Start fest" not in page


def test_keeping_test_runs_moved_to_the_field_page(app, client, survey):
    assert 'name="keep_test_runs"' not in _page(client)
    field = client.get("/surveys/1/field").data.decode()
    assert 'action="/surveys/1/test-runs"' in field and 'name="keep_test_runs"' in field
    client.post("/surveys/1/test-runs", data={"keep_test_runs": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1).keep_test_runs
    # role "Bearbeiten" may switch it, like deleting the test runs next to it
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="editor", position=1))
        db.session.commit()
    client.get("/auth/logout")
    client.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    client.post("/terms/", data={"accept": "1"})
    client.post("/surveys/1/test-runs", data={})
    with app.app_context():
        assert not db.session.get(Survey, 1).keep_test_runs


def test_old_anchors_still_land_in_their_group(app, client, survey):
    """Links of messages and of other pages (#panel, #end-texts, #members, #image-style, #design, #consent-facts,
    #languages) still find their place: app.js opens the group around the anchor."""
    fill_consent(client)
    page = _page(client)
    starts = [m.start() for m in re.finditer(r'<details class="panel settings-group"', page)] + [page.index("Literatur zu diesen")]
    parts = {re.match(r'<details class="panel settings-group" id="(\w+)"', page[a:]).group(1): page[a:b]
             for a, b in zip(starts, starts[1:])}
    for anchor, group in (("end-texts", "abschlusstexte"), ("members", "beteiligte"), ("image-style", "gestaltung"),
                          ("design", "gestaltung"), ("consent-facts", "einwilligung"), ("languages", "befragung")):
        assert f'id="{anchor}"' in parts[group], anchor


def test_end_texts_in_the_source_language_the_others_are_kept(app, client, survey):
    """Package 2: the settings show the end texts of the source language only; the other languages are on the
    page "Übersetzung", and saving the settings leaves them alone."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.end_texts = {"en": {"thanks": "Thanks!"}}
        db.session.commit()
    page = _page(client)
    assert 'name="thanks_de"' in page and 'name="thanks_en"' not in page and "/surveys/1/translation/" in page
    client.post("/surveys/1/end-texts", data={"thanks_de": "Danke!"})
    with app.app_context():
        texts = db.session.get(Survey, 1).end_texts
        assert texts["de"]["thanks"] == "Danke!" and texts["en"] == {"thanks": "Thanks!"}


def test_links_to_copy_are_on_the_field_page(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.panel = {"params": [{"name": "PID", "mode": "store", "required": True, "allowed": []},
                              {"name": "src", "mode": "redirect", "required": False, "allowed": ["a", "b"]}]}
        db.session.commit()
    page = _page(client)
    assert 'id="panel-link"' not in page and "group-link-url" not in page and 'id="panel"' not in page
    field = client.get("/surveys/1/field").data.decode()
    assert 'id="panel-link"' in field and "PID=…&amp;src=…" in field
    assert "src=a" in field and "src=b" in field          # links per value of a parameter that is not stored
