"""Accessibility of the participant pages (UI/UX audit of 2026-10-04, package F, R53 to R65)."""
from tests.static_sources import participant_js

import re
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Asset, Item
from tests.conftest import add

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"


def preview(client, page=2):
    return client.get(f"/surveys/1/editor/preview?page={page}", follow_redirects=True).data.decode()


def test_image_mark_can_be_answered_without_marking(app, client, survey):
    """R54: "Ich kann hier nichts markieren" answers a required image question, as 0 marks."""
    add(client, "image_mark")                                                           # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict({
        "code": "look", "question": "Was fällt Ihnen auf?", "answer_mode": "required", "mode": "points", "alt": "Roboter"}))
    with app.app_context():
        item = db.session.get(Item, 2)
        item.assets.append(Asset(key="", filename="x.png", source="upload", status="approved"))
        db.session.commit()
        assert item.kind.parse_answer(item, MultiDict({"i2": "", "i2_none": "1"})) == []
        assert item.kind.export_values(item, [])["look_n"] == 0
        # a mark wins over the tick
        assert item.kind.parse_answer(item, MultiDict({"i2": "[[0.5, 0.5]]", "i2_none": "1"})) == [[0.5, 0.5]]
    assert 'name="i2_none"' in preview(client) and "Ich kann hier nichts markieren" in preview(client)


def test_likert_is_a_group_named_by_its_question_and_back_comes_first(client, survey):
    """R57: the statements form one group the question names; R65: Back before Next in the source, Enter still
    means Next through an unseen first submit button."""
    add(client, "likert")                                                               # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict({"code": "l", "question": "Wie sehr stimmen Sie zu?",
                                                             "statements": "Eins\nZwei"}))
    add(client, "likert")                                                               # 3, second page
    html = preview(client)
    assert re.search(r'class="p-likert[^"]*"[^>]*role="group" aria-labelledby="q2"', html)
    nav = html[html.index('class="p-nav"'):]
    if "p-back" in nav:
        assert nav.index("p-default-submit") < nav.index("p-back") < nav.index("p-next")


def test_cloze_gaps_are_described_by_their_text(client, survey):
    """R62: a gap says which text it stands in."""
    add(client, "cloze")                                                                # 2
    client.post("/surveys/1/editor/items/2", data={"code": "cl", "question": "Ergänzen Sie.", "text": "Ein Roboter ist [] ."})
    html = preview(client)
    assert 'id="cloze2"' in html and 'aria-describedby="cloze2"' in html


def test_participant_pages_have_a_main_heading_and_erasing_is_red_and_asked():
    """R63: h1 on the end, error and forwarding pages; R64: deleting for good is red and needs a tick; R65: e-mail
    questions let the browser fill in the address."""
    for name in ("end.html", "error.html", "external.html", "share_expired.html"):
        assert "<h1" in (TEMPLATES / "participant" / name).read_text(encoding="utf-8"), name
    erase = (TEMPLATES / "participant" / "erase.html").read_text(encoding="utf-8")
    assert erase.count("p-button p-button-danger") == 2 and 'name="sure" value="1" required' in erase
    text = (TEMPLATES / "items" / "text_input" / "participant.html").read_text(encoding="utf-8")
    assert "autocomplete=\"{{ purpose or 'email' }}\"" in text  # unless the researcher named another purpose (UI/UX audit 2026-10-16, 103)


def test_announcements_for_moving_on_by_itself():
    """R59: the page says beforehand that it moves on after a choice; R60: the time limit warns 20 s before, the
    forward of the end page can be stopped."""
    page = (TEMPLATES / "participant" / "page.html").read_text(encoding="utf-8")
    assert "data-auto-note" in page and "Nach der Auswahl geht es automatisch weiter." in page
    nav = (TEMPLATES / "participant" / "_nav.html").read_text(encoding="utf-8")
    assert "data-hold-warn" in nav and 'aria-live="assertive"' in nav
    js = participant_js()
    assert "[data-forward-stop]" in js and "data-hold-warn" in js and "p-hl-multi" in js
