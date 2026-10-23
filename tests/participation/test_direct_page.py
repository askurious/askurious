"""A page sent by participant.js gets the next page in the answer to its POST (field "_direct"), without the redirect
and the second request; without the script everything stays as before (block-load concept 2026-10-15, stage c)."""
import os

from app import flow
from app.extensions import db
from app.models import Response, Survey
from app.participate import trim_html
from tests.conftest import CONSENT_OK, add, fill_consent, split_node
from tests.helpers.page_pos import page_pos

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def build(client, separated=False):
    """consent | block 2: required text (2), page break (3), info (4)"""
    fill_consent(client)
    add(client, "text_input")                                       # 2
    client.post("/surveys/1/editor/items/2", data={"code": "age", "question": "Alter", "answer_mode": "required"})
    add(client, "page_break")                                       # 3
    add(client, "info")                                             # 4
    client.post("/surveys/1/editor/items/4", data={"code": "bye", "question": "Fast fertig"})
    if separated:
        client.post("/surveys/1/editor/blocks")                     # block 3, behind the split line
        add(client, "text_input", block=3)                          # 5
        client.post("/surveys/1/editor/items/5", data={"code": "mail", "question": "Mail"})
        client.post("/surveys/1/editor/flow/add", data={"kind": "split"})
        with client.application.app_context():
            s = db.session.get(Survey, 1)
            node = split_node(s)["id"]
            s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                                {"t": "split", "id": node}, {"t": "block", "block": 3}]}
            flow.normalize(s)
            db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with client.application.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING
        return "/s/" + s.code


def direct(data):
    return {**data, "_direct": "1"}


def test_next_page_comes_with_the_post(app, client, survey):
    url = build(client)
    p = app.test_client()
    p.get(url)
    r = p.post(url, data=direct(CONSENT_OK))
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Alter" in html and "data-replace" in html
    assert f'name="pos" value="{page_pos(p, 1)}"' in html
    # an error: the same page with its message, in the answer to the POST as before, and its entry also a GET
    r = p.post(url, data=direct({"nav": "next", "pos": page_pos(p, 1)}))
    assert r.status_code == 200 and "has-error" in r.get_data(as_text=True) and "data-replace" in r.get_data(as_text=True)
    r = p.post(url, data=direct({"nav": "next", "pos": page_pos(p, 1), "i2": "41"}))
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Fast fertig" in html and "Absenden" in html
    # reloading is a GET (participant.js turned the entry into one): the same page, nothing sent again
    assert "Fast fertig" in p.get(url).get_data(as_text=True)
    r = p.post(url, data=direct({"nav": "next", "pos": page_pos(p, 2)}))
    assert r.status_code == 200 and "Vielen Dank" in r.get_data(as_text=True)
    with app.app_context():
        response = db.session.scalar(db.select(Response))
        assert response.status == Response.COMPLETED and response.answers["2"] == "41"


def test_without_the_script_the_redirect_stays(app, client, survey):
    url = build(client)
    p = app.test_client()
    p.get(url)
    r = p.post(url, data=CONSENT_OK)
    assert r.status_code in (302, 303) and "data-replace" not in p.get(url).get_data(as_text=True)
    r = p.post(url, data={"nav": "next", "pos": page_pos(p, 1), "i2": "41"})
    assert r.status_code in (302, 303)


def test_page_sent_twice_says_so_on_the_current_page(app, client, survey):
    url = build(client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=direct(CONSENT_OK))
    p.post(url, data=direct({"nav": "next", "pos": page_pos(p, 1), "i2": "41"}))
    # the same page once more (a double click that came through, an older tab): dropped, the current page says so
    r = p.post(url, data=direct({"nav": "next", "pos": page_pos(p, 1), "i2": "99"}))
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Fast fertig" in html and "schon abgeschickt" in html
    with app.app_context():
        assert db.session.scalar(db.select(Response)).answers["2"] == "41"
    assert "schon abgeschickt" not in p.get(url).get_data(as_text=True)  # said once, not again after a reload


def test_back_comes_with_the_post(app, client, survey):
    url = build(client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=direct(CONSENT_OK))
    p.post(url, data=direct({"nav": "next", "pos": page_pos(p, 1), "i2": "41"}))
    r = p.post(url, data=direct({"nav": "back", "pos": page_pos(p, 2)}))
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Alter" in html and 'value="41"' in html


def test_first_page_not_stored_keeps_its_redirects(app, client, survey):
    url = build(client)
    p = app.test_client()
    p.get(url)
    # "Back" or the language switch before anything is stored: nothing to store, the redirect as before
    assert p.post(url, data=direct({"nav": "back", "set_lang": "en"})).status_code in (302, 303)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0


def test_separated_section_keeps_the_redirect(app, client, survey):
    url = build(client, separated=True)
    p = app.test_client()
    p.get(url)
    p.post(url, data=direct(CONSENT_OK))
    p.post(url, data=direct({"nav": "next", "pos": page_pos(p, 1), "i2": "41"}))
    r = p.post(url, data=direct({"nav": "next", "pos": page_pos(p, 2)}))
    # the separated section reads the form of its own pages: it starts with the GET after the redirect
    assert r.status_code in (302, 303)
    assert "Mail" in p.get(url).get_data(as_text=True)


def test_trim_keeps_text_fields_and_marked_passages():
    html = ('<div>\n    <p>a\n\n      b</p>\n  <textarea name="x">  line\n    indented</textarea>\n'
            '<p class="p-hl-text" data-hl-text>first\n\n   second</p>\n  <pre>  x\n   y</pre>\n</div>')
    out = trim_html(html)
    assert "<div>\n<p>a\nb</p>\n<textarea" in out
    assert '<textarea name="x">  line\n    indented</textarea>' in out
    assert '<p class="p-hl-text" data-hl-text>first\n\n   second</p>' in out and "<pre>  x\n   y</pre>" in out


def test_page_leaves_without_indentation(app, client, survey):
    url = build(client)
    html = app.test_client().get(url).get_data(as_text=True)
    assert "\n  " not in html and "\n\n" not in html


def test_script_sends_the_field_and_turns_the_entry_into_a_get():
    js = open(os.path.join(ROOT, "app/static/participant.js"), encoding="utf-8").read()
    replace = js[js.index('hasAttribute("data-replace")') - 200:js.index('hasAttribute("data-replace")') + 300]
    assert "history.replaceState(history.state, \"\", location.href)" in replace
    lock = js[js.index("One page, one submit"):]
    assert 'direct.name = "_direct"' in lock.split("keepPage (above)")[0]
