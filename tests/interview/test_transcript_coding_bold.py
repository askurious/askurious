"""Bold in the coding view (Ralf 2026-11-07, "A + B"): a passage made bold is stored beside the codings by place and
wording, the transcript text stays as it is, it follows a correction and is taken back by its author or the team."""
import json
import re

from app.extensions import db
from app.models import Response
from tests.interview.test_interviewer import add, begin, open_link
from tests.interview.test_transcript_coding import _guide, _new, _transcript

FETCH = {"X-Requested-With": "fetch"}
TEXT = "I: Wie war das?\nB: Anstrengend, sehr anstrengend.\nI: Und dann?\nB: Dann wurde es besser."


def _data(page):
    return json.loads(re.search(r'<script type="application/json" id="tc-data">(.*?)</script>', page, re.S).group(1))


def test_bold_passage(app, client):
    code, _sid = _guide(app, client)
    a, _ = open_link(app, code, add(client, code, "Anna Neu"))
    rid = begin(a, code)
    _transcript(app, rid, TEXT)
    pid = _new(client, code, start="empty")
    base = f"/guide/{code}/interviews/coding/{pid}"
    g, _ = open_link(app, code, add(client, code, "Gruppe 1", has_interview="1", may_interview="", may_code="all"))
    start = TEXT.index("sehr")
    d = g.post(f"{base}/i/{rid}/highlights", json={"start": start + 1, "end": start + 10}, headers=FETCH).json
    assert d["ok"] and (d["mark"]["start"], d["mark"]["end"]) == (start, start + 16)  # whole words
    with app.app_context():
        iv = db.session.get(Response, rid).interview
        assert iv["transcript"]["text"] == TEXT and not iv.get("codings")
        assert iv["highlights"][0]["quote"] == "sehr anstrengend"
    seen = _data(client.get(f"{base}/i/{rid}").data.decode())["marks"]
    assert [(m["start"], m["mine"]) for m in seen] == [(start, True)]  # the team may take it back
    # after a correction before it, it is found again at its new place
    from app import transcript_coding_correction as correction

    new = "I: Wie war das denn?\n" + TEXT.split("\n", 1)[1]
    with app.app_context():
        row = db.session.get(Response, rid)
        moved = correction.refit_marks(row, new)
        assert new[moved[0]["start"]:moved[0]["end"]] == "sehr anstrengend"
    # someone else of a link takes back only their own
    h, _ = open_link(app, code, add(client, code, "Gruppe 2", has_interview="1", may_interview="", may_code="all"))
    mid = d["mark"]["id"]
    assert h.post(f"{base}/i/{rid}/highlights/{mid}/delete", headers=FETCH).status_code == 403
    assert g.post(f"{base}/i/{rid}/highlights/{mid}/delete", headers=FETCH).json["ok"]
    assert _data(client.get(f"{base}/i/{rid}").data.decode())["marks"] == []
