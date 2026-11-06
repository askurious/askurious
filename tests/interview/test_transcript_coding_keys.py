"""Fixed keys 1 to 9 per person and analysis (feedback 2026-11-06, P10): set at a category of the side, kept on the
server, one key per category, each person their own; the page carries them for the list and the keys."""
import json
import re

from app.extensions import db
from app.models import CodingCategory, CodingKey
from tests.interview.test_interviewer import add, begin, open_link
from tests.interview.test_transcript_coding import _guide, _new, _transcript

FETCH = {"X-Requested-With": "fetch"}


def _data(page):
    return json.loads(re.search(r'<script type="application/json" id="tc-data">(.*?)</script>', page, re.S).group(1))


def test_keys_per_person(app, client):
    code, _sid = _guide(app, client)
    a, _ = open_link(app, code, add(client, code, "Anna Neu"))
    rid = begin(a, code)
    _transcript(app, rid, "I: Wie war das?\nB: Anstrengend.")
    pid = _new(client, code, start="empty")
    base = f"/guide/{code}/interviews/coding/{pid}"
    for name in ("Belastung", "Wünsche"):
        client.post(f"{base}/categories/new", data={"kind": "main", "name": name})
    with app.app_context():
        ids = dict(db.session.execute(db.select(CodingCategory.name, CodingCategory.id).filter_by(project_id=pid)).all())
    load, wish = ids["Belastung"], ids["Wünsche"]
    assert client.post(f"{base}/keys", json={"slot": "3", "cat": load}, headers=FETCH).json["keys"] == {"3": load}
    # a category has one key; a key given again moves
    assert client.post(f"{base}/keys", json={"slot": "5", "cat": load}, headers=FETCH).json["keys"] == {"5": load}
    assert client.post(f"{base}/keys", json={"slot": "5", "cat": wish}, headers=FETCH).json["keys"] == {"5": wish}
    assert client.post(f"{base}/keys", json={"slot": "1", "cat": load}, headers=FETCH).json["keys"] == {"5": wish, "1": load}
    assert client.post(f"{base}/keys", json={"slot": "0", "cat": load}, headers=FETCH).status_code == 400
    assert client.post(f"{base}/keys", json={"slot": "2", "cat": 99999}, headers=FETCH).status_code == 400
    assert client.post(f"{base}/keys", json={"slot": "1", "cat": ""}, headers=FETCH).json["keys"] == {"5": wish}
    page = client.get(f"{base}/i/{rid}").data.decode()
    assert _data(page)["keys"] == {"5": wish} and "data-keys-url" in page
    # someone else has their own keys
    g, _ = open_link(app, code, add(client, code, "Gruppe 1", has_interview="1", may_interview="", may_code="all"))
    assert _data(g.get(f"{base}/i/{rid}").data.decode())["keys"] == {}
    assert g.post(f"{base}/keys", json={"slot": "5", "cat": load}, headers=FETCH).json["keys"] == {"5": load}
    assert _data(client.get(f"{base}/i/{rid}").data.decode())["keys"] == {"5": wish}
    # a removed category frees its key
    client.post(f"{base}/categories/{wish}/remove", data={})
    assert _data(client.get(f"{base}/i/{rid}").data.decode())["keys"] == {}
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(CodingKey).filter_by(project_id=pid)) == 2
