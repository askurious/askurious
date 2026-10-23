"""A block loaded at once (app/blockload.py) in a survey with two languages: numbers sent in the background are read
in the language the page was shown in, and the language link keeps what was typed on every page of the block
(audit 2026-10-23, DS-02 and DS-04)."""
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.helpers.page_pos import page_pos
from tests.participation.test_blockload_pages import store

E = "/surveys/1/editor"


def _build(client, app):
    """consent | block 2: number a, page break, number b, page break, text c | block 3: text d; German and English"""
    fill_consent(client)
    add(client, "text_input")                                       # 2
    client.post(E + "/items/2", data={"code": "a", "question": "Betrag A", "validation": "decimal"})
    add(client, "page_break")                                       # 3
    add(client, "text_input")                                       # 4
    client.post(E + "/items/4", data={"code": "b", "question": "Betrag B", "validation": "decimal"})
    add(client, "page_break")                                       # 5
    add(client, "text_input")                                       # 6
    client.post(E + "/items/6", data={"code": "c", "question": "Frage C"})
    client.post(E + "/blocks")                                      # block 3
    add(client, "text_input", block=3)                              # 7
    client.post(E + "/items/7", data={"code": "d", "question": "Frage D"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.block_load, s.languages, s.language_choice = True, ["de", "en"], True
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING
        return "/s/" + s.code


def _participant(app, url):
    p = app.test_client()
    p.get(url + "?lang=de")
    p.post(url, data=CONSENT_OK)
    assert 'data-block-start="1"' in p.get(url).get_data(as_text=True)
    return p


def _response(app):
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        db.session.expunge(r)
        return r


def test_a_block_page_reads_numbers_in_the_language_it_was_shown_in(app, client, survey):
    """audit 2026-10-23, DS-02: the background send read "1.000" in the participation's current language (English
    after a switch in another tab: 1.0)."""
    url = _build(client, app)
    p = _participant(app, url)
    with app.app_context():  # another tab switched to English meanwhile
        r = db.session.scalar(db.select(Response))
        r.language = "en"
        db.session.commit()
    assert store(p, url, 1, 1, {"i2": ["1.000"]}, page_lang="de", pos=page_pos(p, 1)).json["ok"]
    assert _response(app).answers["2"] == 1000
    # without the field (a script of before): the participation's language, as before
    store(p, url, 1, 2, {"i2": ["2.5"]}, pos=page_pos(p, 1))
    assert _response(app).answers["2"] == 2.5


def test_the_language_link_keeps_every_page_of_the_block(app, client, survey):
    """audit 2026-10-23, DS-04: the language link sends the whole form of the block without "_block"; only the page
    the server was at kept its inputs, the later pages' typing was lost."""
    url = _build(client, app)
    p = _participant(app, url)
    r = p.post(url, data={"pos": page_pos(p, 1), "page_lang": "de", "set_lang": "en",
                          "i2": "1.000", "i4": "2.500", "i6": "drei"})
    assert r.status_code == 302
    rs = _response(app)
    assert rs.language == "en" and rs.position == 1
    assert rs.answers["2"] == 1000 and rs.answers["4"] == 2500 and rs.answers["6"] == "drei"
    # a form of another participation changes nothing (the token, DS-01)
    other = _participant(app, url)
    other.post(url, data={"pos": page_pos(p, 1), "set_lang": "en", "i4": "9"})
    with app.app_context():
        assert all((x.answers or {}).get("4") != 9 for x in db.session.scalars(db.select(Response)))
