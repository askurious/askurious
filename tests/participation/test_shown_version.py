"""The questionnaire version a page showed (audit storage and calculations 2026-10-28, S-1 and S-2): an option added
after "Zur Bearbeitung öffnen" is answered by a participation that began before it, and the first page of an earlier
version is not stored under the new one."""
import re

from werkzeug.datastructures import MultiDict

from app import export
from app.extensions import db
from app.models import Item, Response, Survey
from app.rows import NOT_SHOWN
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"
START = {"use_end": "1", "field_end": "2031-01-01"}


def _mc(*options):
    data = [("code", "mc"), ("question", "Q")]
    for oid, code, label in options:
        data += [("option_id", oid), ("option_code", code), ("option_label", label)]
    return MultiDict(data)


def _setup(client):
    fill_consent(client)
    add(client, "page_break")      # 2
    add(client, "multiple_choice")  # 3
    client.post(f"{E}/items/3", data=_mc(("o1", "a", "A"), ("o2", "b", "B")))


def _url(app):
    with app.app_context():
        return "/s/" + db.session.get(Survey, 1).code


def _field(html, name):
    return re.search(rf'name="{name}" value="([^"]+)"', html).group(1)


def _reopen_with_option_c(client):
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    with client.application.app_context():
        ids = [o["id"] for o in db.session.get(Item, 3).config["options"]]
    client.post(f"{E}/items/3", data=_mc((ids[0], "a", "A"), (ids[1], "b", "B"), ("", "c", "C")))
    client.post("/surveys/1/start", data=START)


def _row(app):
    with app.app_context():
        return list(export.rows(db.session.get(Survey, 1), with_hb=False))[0]


def test_option_added_while_a_participation_runs_is_exported_as_answered(app, client, survey):
    """S-1: begun in version 1, the page with the new option c answered in version 2: c ticked is 1, b not ticked 0,
    never -77 (not shown)."""
    _setup(client)
    client.post("/surveys/1/start", data=START)
    url = _url(app)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    _reopen_with_option_c(client)
    html = p.get(url).get_data(as_text=True)
    assert 'value="c"' in html and _field(html, "qv") == "2"
    p.post(url, data=MultiDict([("nav", "next"), ("pos", _field(html, "pos")), ("qv", "2"), ("i3", "c")]))
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.qversion == 1 and r.answers.get("3") == ["c"] and r.layout["item_qv"] == {"3": 2}
    row = _row(app)
    assert (row["mc_a"], row["mc_b"], row["mc_c"]) == (0, 0, 1)


def test_question_answered_in_the_first_version_keeps_not_shown(app, client, survey):
    """S-1: the multiple choice answered before the option was added still says -77 for it."""
    _setup(client)
    client.post("/surveys/1/start", data=START)
    url = _url(app)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    html = p.get(url).get_data(as_text=True)
    p.post(url, data=MultiDict([("nav", "next"), ("pos", _field(html, "pos")), ("qv", "1"), ("i3", "a")]))
    _reopen_with_option_c(client)
    row = _row(app)
    assert (row["mc_a"], row["mc_b"], row["mc_c"]) == (1, 0, NOT_SHOWN)


def test_page_of_the_version_before_is_not_taken(app, client, survey):
    """S-1: a page rendered in version 1 and sent after the restart in version 2 is dropped; the current page with
    the new option is shown instead."""
    _setup(client)
    client.post("/surveys/1/start", data=START)
    url = _url(app)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    html = p.get(url).get_data(as_text=True)
    _reopen_with_option_c(client)
    p.post(url, data=MultiDict([("nav", "next"), ("pos", _field(html, "pos")), ("qv", "1"), ("i3", "a")]))
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert "3" not in (r.answers or {})
    assert 'value="c"' in p.get(url).get_data(as_text=True)


def test_first_page_of_an_earlier_version_is_shown_again(app, client, survey):
    """S-2: the first page opened in version 1 (nothing stored yet) and sent after the restart in version 2 stores
    nothing; the page shown again stores the participation in version 2."""
    _setup(client)
    client.post("/surveys/1/start", data=START)
    url = _url(app)
    p = app.test_client()
    p.get(url)
    _reopen_with_option_c(client)
    p.post(url, data=CONSENT_OK)
    with app.app_context():
        assert db.session.scalar(db.select(Response).filter_by(is_preview=False)) is None
    p.get(url)
    p.post(url, data=CONSENT_OK)
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r is not None and r.qversion == 2
