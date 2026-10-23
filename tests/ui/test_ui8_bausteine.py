"""Shared building blocks instead of variants (UI/UX audit 2026-10-07, S8): one row list, one card for an answer
set aside, one "Person hinzufügen", one table for the lists outside the results."""
import glob
import os
import re

from werkzeug.datastructures import MultiDict

from app import theme
from app.extensions import db
from app.models import Contact, Design, User
from tests.conftest import add, may_sign_in
from tests.editor.test_linked import _linked_question

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
TEMPLATES = os.path.join(ROOT, "app", "templates")
CSS = open(os.path.join(ROOT, "app/static/app.css"), encoding="utf-8").read()
PCSS = open(os.path.join(ROOT, "app/static/participant.css"), encoding="utf-8").read()
ME = "0000-0000-0000-001X"
OTHER = "0000-0002-1825-0097"


def _read(*parts):
    return open(os.path.join(TEMPLATES, *parts), encoding="utf-8").read()


def test_every_row_list_of_the_editors_has_the_one_note_under_it():
    """W32: every list of rows ordered by hand gets ⋮⋮ ↑ ↓ × from app.js and one note under it from the macro
    row_list_hint, never a copy of its own."""
    lists = [p for p in glob.glob(os.path.join(TEMPLATES, "items", "**", "*.html"), recursive=True)
             if "new-row" in open(p, encoding="utf-8").read() or "row_tools(" in open(p, encoding="utf-8").read()]
    assert len(lists) >= 13
    for path in lists:
        if path.endswith(os.path.join("_attributes", "editor.html")):
            continue  # attributes and their levels: "Leere Merkmale und Stufen werden nicht gespeichert"
        html = open(path, encoding="utf-8").read()
        # one note per list: the card sort has two (cards and groups), each with its own new row
        assert html.count("row_list_hint(") == max(1, html.count('class="new-row"')), path
        assert "Zeilen am Griff" not in html and "Leere Zeilen werden" not in html, path
    assert "row-list-hint" in _read("_macros.html")
    # × of a row: 32 px, at least the 24 px of WCAG 2.5.8
    assert re.search(r"\.icon-btn \{ display: inline-grid; place-items: center; width: 32px; height: 32px;", CSS)


def test_the_note_is_rendered_once_under_the_list(app, client, survey):
    add(client, "semantic")
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert page.count('class="muted small row-list-hint"') == 1 and "leere Zeilen werden nicht gespeichert" in page


def test_answers_set_aside_share_one_card(app, client, survey):
    """W48: "keine Angabe" of a single choice, "Ich wohne nicht in Deutschland" and "Keine davon" use the card of
    "weiß nicht" (items/_fallback.html); names and values stay as they were."""
    for name in ("_dont_know.html", os.path.join("single_choice", "participant.html"), os.path.join("federal_state", "participant.html"),
                 os.path.join("conjoint", "participant.html"), os.path.join("acbc", "participant.html")):
        assert "fallback_option(" in _read("items", name), name
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "wissen"), ("question", "Wie gut?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "schlecht"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "gut"),
        ("option_id", "d"), ("option_code", "99"), ("option_label", "keine Angabe"), ("option_missing", "d")]))
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    card = re.search(r'<label class="p-option p-radio p-dk">\s*<input type="radio" name="i2" value="99">', html)
    assert card and html.count("p-dk") == 1                          # the other options stay ordinary cards
    assert 'class="p-option p-radio">\n      <input type="radio" name="i2" value="1"' in html
    add(client, "federal_state")                                    # 3
    client.post("/surveys/1/editor/items/3", data={"code": "land", "question": "Land?", "abroad": "1", "no_answer": "1"})
    html = client.get("/surveys/1/editor/preview?page=3", follow_redirects=True).data.decode()
    assert re.search(r'p-dk"><input type="radio" name="i3" value="0">', html) and re.search(r'p-dk"><input type="radio" name="i3" value="99">', html)
    # in a list the card is as wide as the others, several in a row keep the usual gap
    assert ".p-options > .p-option.p-dk { max-width: none; }" in PCSS and ".p-options > .p-option.p-dk + .p-option.p-dk { margin-top: 0; }" in PCSS
    assert ".cj-none" not in PCSS                                     # the gap of "Keine davon" is that of every card


def test_person_add_is_one_component_for_all_four_places():
    """W96: members of a survey, of a group, the people of a design and of a linked element: the same macro."""
    for parts in (("surveys", "_members.html"), ("groups", "detail.html"), ("designs", "index.html"), ("constructs", "linked.html")):
        html = _read(*parts)
        assert "person_add(" in html, parts
        assert 'pattern="[0-9]{4}' not in html and "<datalist" not in html, parts   # no ORCID typed by hand
    macro = _read("_people.html")
    assert "Name, ORCID iD oder E-Mail-Adresse" in macro and '<label for="{{ field_id }}">' in macro


def _design(app):
    with app.app_context():
        owner = db.session.scalar(db.select(User).filter_by(orcid=ME))
        d = Design(owner_id=owner.id, name="Hausfarben", data=theme.standard())
        db.session.add(d)
        db.session.add(Contact(owner_id=owner.id, orcid=OTHER, name="Kollegin Beispiel"))
        db.session.commit()
        return d.id


def test_a_design_is_shared_by_searching_a_name_with_the_right(app, client, survey):
    did = _design(app)
    page = client.get("/designs/").data.decode()
    assert f'id="design-share-{did}"' in page and "Person hinzufügen" in page and "Kollegin Beispiel" not in page
    page = client.get(f"/designs/?share={did}&q=Kollegin").data.decode()
    line = page.split('class="plain results person-results"', 1)[1].split("</ul>", 1)[0]
    assert "Kollegin Beispiel" in line and f'action="/designs/{did}/share"' in line and 'name="right"' in line
    assert '<option value="use" title="anwenden und duplizieren, nicht ändern" selected>Anwenden</option>' in line
    may_sign_in(app, OTHER)  # sharing needs someone who can sign in (H4)
    r = client.post(f"/designs/{did}/share", data={"orcid": OTHER, "name": "Kollegin Beispiel", "right": "edit"}, follow_redirects=True)
    assert "anwenden, duplizieren und bearbeiten" in r.data.decode()
    with app.app_context():
        d = db.session.get(Design, did)
        assert [s.orcid for s in d.shares] == [OTHER] and [e.orcid for e in d.editors] == [OTHER]
    # shared already: no longer among the matches; the own iD never is
    page = client.get(f"/designs/?share={did}&q=Kollegin").data.decode()
    assert "Keine Treffer." in page
    # an ORCID iD the registry does not answer for is still offered as it is
    page = client.get(f"/designs/?share={did}&q=0000-0000-0000-0124").data.decode()
    assert 'name="orcid" value="0000-0000-0000-0124"' in page


def test_a_design_of_someone_else_gets_no_search(app, client, survey):
    with app.app_context():
        other = User(orcid=OTHER, name="Kollegin")
        db.session.add(other)
        db.session.flush()
        d = Design(owner_id=other.id, name="Fremd", data=theme.standard())
        db.session.add(d)
        db.session.commit()
        did = d.id
    assert "person-results" not in client.get(f"/designs/?share={did}&q=Kolleg").data.decode()
    assert client.post(f"/designs/{did}/share", data={"orcid": ME, "right": "edit"}).status_code == 404


def test_a_linked_element_is_shared_by_searching_a_name(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        owner = db.session.scalar(db.select(User).filter_by(orcid=ME))
        db.session.add(Contact(owner_id=owner.id, orcid=OTHER, name="Kollegin Beispiel"))
        db.session.commit()
    page = client.get(f"/constructs/linked/{key}?q=Kollegin").data.decode()
    line = page.split('class="plain results person-results"', 1)[1].split("</ul>", 1)[0]
    assert "Kollegin Beispiel" in line and 'name="action" value="share_person"' in line and 'name="right"' not in line
    may_sign_in(app, OTHER)  # sharing needs someone who can sign in (H4)
    client.post(f"/constructs/linked/{key}", data={"action": "share_person", "orcid": OTHER, "name": "Kollegin Beispiel"})
    page = client.get(f"/constructs/linked/{key}").data.decode()
    shares = page.split('id="teilen"', 1)[1].split("</section>", 1)[0]
    assert "Kollegin Beispiel" in shares and "darf einfügen" in shares and "orcid-link" in shares


def test_lists_outside_the_results_use_the_one_table():
    """W93: the lists of administration, account, interviews and editors are .list-table with variants, inside the
    scroll wrapper of the macro; the old variants are left only in the results (ui8-r)."""
    old = re.compile(r'<table class="(aic-table|zebra|data-table|edit-table|sessions-table|quota-table)')
    for path in glob.glob(os.path.join(TEMPLATES, "**", "*.html"), recursive=True):
        rel = os.path.relpath(path, TEMPLATES).replace(os.sep, "/")
        if rel == "surveys/results.html" or (rel.startswith("surveys/_") and rel != "surveys/_panel_form.html"):
            continue
        assert not old.search(open(path, encoding="utf-8").read()), rel
    assert '<div class="table-scroll"><table class="list-table' in _read("_macros.html")
    for parts in (("admin", "researchers.html"), ("admin", "log.html"), ("admin", "construct_pool.html"), ("accounts", "index.html"),
                  ("interview", "results.html")):
        assert "{% call list_table(" in _read(*parts), parts
    assert ".list-table:where(:not(.is-edit, .is-lines)) tbody tr:nth-child(even) { background: var(--surface-2); }" in CSS
    assert ".edit-table" not in CSS and ".data-table" not in CSS and ".aic-table" not in CSS
