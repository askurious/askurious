"""The page token ("pos") names the participation as well as the page: a tab still open from the person before on a
shared device (after "Neue Teilnahme an diesem Gerät") never writes into the next participation at the same
position, neither page by page nor in a block loaded at once (audit 2026-10-23, DS-01)."""
import json

from app import runtime
from app.extensions import db
from app.models import Response
from tests.helpers.page_pos import page_pos
from tests.participation.test_blockload_pages import build, start, store


def _answers(app):
    with app.app_context():
        return [dict(r.answers or {}) for r in db.session.scalars(db.select(Response).order_by(Response.id))]


def test_two_participations_have_different_tokens_for_the_same_page(app):
    with app.test_request_context():
        a, b = Response(pid="a" * 12, layout={}), Response(pid="b" * 12, layout={})
        assert runtime.page_token(a, 1) != runtime.page_token(b, 1)
        assert runtime.page_token(a, 1).startswith("r1-") and runtime.page_token(a, 1) == runtime.page_token(a, 1)
        assert runtime.keep_key(a, 1).endswith("-r1")  # the name of the kept inputs stays as it was


def test_a_page_of_the_person_before_is_not_stored_in_the_next_participation(app, client, survey):
    """audit 2026-10-23, DS-01: the old tab of A posted pos=r1 and its answer went into B's participation."""
    url = build(client, app, block_load=False)
    before, after = start(app, url), start(app, url)  # the same device: A's tab, then B's new participation
    old = page_pos(before, 1)
    r = after.post(url, data={"pos": old, "nav": "next", "i2": "von A"})
    assert r.status_code == 302
    assert all(a.get("2") != "von A" for a in _answers(app))
    after.post(url, data={"pos": page_pos(after, 1), "nav": "next", "i2": "von B"})
    assert sorted(a.get("2") or "" for a in _answers(app)) == ["", "von B"]


def test_a_block_page_of_the_person_before_is_refused(app, client, survey):
    """audit 2026-10-23, DS-01: the block endpoint only compared the start of the block."""
    url = build(client, app)
    before, after = start(app, url), start(app, url)
    refused = store(after, url, 1, 1, {"i2": ["von A"]}, pos=page_pos(before, 1))
    assert refused.status_code == 409 and refused.json == {"error": "stale", "gone": True}
    assert all(a.get("2") != "von A" for a in _answers(app))
    ok = store(after, url, 1, 2, {"i2": ["von B"]}, pos=page_pos(after, 1))
    assert ok.status_code == 200 and json.loads(ok.data)["ok"] is True
    assert sorted(a.get("2") or "" for a in _answers(app)) == ["", "von B"]
