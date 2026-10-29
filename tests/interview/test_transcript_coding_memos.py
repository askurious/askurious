"""Interviews kodieren: memos (app/transcript_coding_memos.py; Ralf 2026-10-29) at a coded passage, a category and an
interview: who writes, sees, changes and deletes them, "Rückgängig", blind double coding, the list of the overview,
the exports (.qdpx notes, Excel sheet "Memos") and that they go with the passage, the interview, the analysis and
the author's account."""
import io
import json
import re
import zipfile
from xml.etree import ElementTree as ET

from app.extensions import db
from app.models import CodingMemo, CodingProject, Response, User
from tests.interview.test_transcript_coding import _person
from tests.interview.test_transcript_coding_categories_xlsx import _sheets
from tests.interview.test_transcript_coding_review import BEA, _code, _setup, _sub

P = "{urn:QDA-XML:project:1.0}"
FETCH = {"X-Requested-With": "fetch"}


def _memo(who, base, **data):
    return who.post(f"{base}/memos", json=data, headers=FETCH)


def _list(page):
    """The section "Memos" of the overview, without the data of the dialog."""
    return page[page.index('id="memos"'):page.index("</section>", page.index('id="memos"'))]


def _memos_on_page(page):
    return json.loads(re.search(r'id="tc-memos">(.*?)</script>', page, re.S).group(1))


def test_memos_at_three_places_with_rights_and_undo(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    cat = _sub(client, base, main, "Erschöpfung")
    cid = _code(bea, base, rid, "Abends", cat, 29)
    r = _memo(bea, base, kind="passage", rid=rid, coding=cid, text="Klingt nach Dauerzustand, nicht nach einem Abend.")
    assert r.status_code == 200 and r.json["ok"] and r.json["memo"]["mine"]
    passage = r.json["memo"]["id"]
    assert _memo(client, base, kind="category", cat=cat, text="Abgrenzung zu Krankheit schärfen?").json["ok"]
    assert _memo(client, base, kind="interview", rid=rid, text="Fall: pflegt die Mutter seit drei Jahren.").json["ok"]
    assert _memo(bea, base, kind="interview", rid=rid, text="   ").status_code == 400            # empty: refused
    assert _memo(bea, base, kind="passage", rid=rid, coding="nope", text="x").status_code == 404
    # the coding view shows the memos of the interview and its passages, the category system those of categories
    shown = _memos_on_page(bea.get(f"{base}/i/{rid}").data.decode())
    assert {m["kind"] for m in shown} == {"passage", "interview"} and len(shown) == 2
    page = client.get(f"{base}?view=categories").data.decode()
    assert [m["kind"] for m in _memos_on_page(page)] == ["category"] and "tc-memo-btn has-memos" in page
    # only the author and the team change or delete; the others get 403
    interview_memo = next(m for m in shown if m["kind"] == "interview")
    assert not interview_memo["mine"]
    assert bea.post(f"{base}/memos/{interview_memo['id']}", json={"text": "meins"}, headers=FETCH).status_code == 403
    assert client.post(f"{base}/memos/{passage}", json={"text": "vom Team ergänzt"}, headers=FETCH).json["memo"]["edited"]
    # delete and "Rückgängig"
    assert bea.post(f"{base}/memos/{passage}/delete", headers=FETCH).json["ok"]
    assert len(_memos_on_page(bea.get(f"{base}/i/{rid}").data.decode())) == 1
    assert bea.post(f"{base}/memos/{passage}/restore", headers=FETCH).json["ok"]
    assert len(_memos_on_page(bea.get(f"{base}/i/{rid}").data.decode())) == 2
    # the list of the overview: all three, filtered by kind, paged
    overview = client.get(f"{base}/overview").data.decode()
    assert 'id="memos"' in overview and "Fall: pflegt die Mutter" in overview and "vom Team ergänzt" in overview
    only = _list(client.get(f"{base}/overview?mk=category").data.decode())
    assert "Abgrenzung zu Krankheit" in only and "Fall: pflegt die Mutter" not in only
    for n in range(12):
        _memo(client, base, kind="category", cat=cat, text=f"Gedanke {n:02d}")
    first = _list(client.get(f"{base}/overview?mn=10&ms=old").data.decode())
    assert "Seite 1 von 2" in first and "Gedanke 11" not in first
    assert "Gedanke 11" in client.get(f"{base}/overview?mn=10&ms=old&mp=2").data.decode()
    # removing the coded passage takes its memos with it
    bea.post(f"{base}/i/{rid}/codings/{cid}/delete")
    with app.app_context():
        assert db.session.get(CodingMemo, passage) is None


def test_memos_stay_blind_in_double_coding(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    with app.app_context():
        bea_id = db.session.scalar(db.select(User).filter_by(orcid=BEA)).id
    carl, carl_id = _person(app, "0000-0001-5109-3700", "Carl")
    client.post(f"{base}/members", data={"kind": "person", "person": "0000-0001-5109-3700"})
    client.post(f"{base}/i/{rid}/double", data={"coder": [f"u:{bea_id}", f"u:{carl_id}"]})
    assert _memo(carl, base, kind="interview", rid=rid, text="Carls Eindruck").json["ok"]
    assert _memo(bea, base, kind="interview", rid=rid, text="Beas Eindruck").json["ok"]
    texts = lambda who: {m["text"] for m in _memos_on_page(who.get(f"{base}/i/{rid}").data.decode())}  # noqa: E731
    assert texts(bea) == {"Beas Eindruck"} and texts(carl) == {"Carls Eindruck"}
    assert texts(client) == {"Beas Eindruck", "Carls Eindruck"}                      # the team sees everything
    assert "Carls Eindruck" not in bea.get(f"{base}/overview").data.decode()  # neither list nor dialog
    bea.post(f"{base}/i/{rid}/done")
    carl.post(f"{base}/i/{rid}/done")
    assert texts(bea) == {"Beas Eindruck", "Carls Eindruck"}


def test_memos_in_the_exports_and_where_they_go(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    cat = _sub(client, base, main, "Erschöpfung")
    cid = _code(client, base, rid, "Abends", cat, 29)
    _memo(client, base, kind="passage", rid=rid, coding=cid, text="Stellen-Memo")
    _memo(client, base, kind="category", cat=cat, text="Kategorie-Memo")
    _memo(bea, base, kind="interview", rid=rid, text="Interview-Memo")
    with zipfile.ZipFile(io.BytesIO(client.get(f"{base}/analysis.qdpx").data)) as z:
        root = ET.fromstring(z.read("project.qde"))
    notes = {n.get("guid"): n.find(f"{P}PlainTextContent").text for n in root.iter(f"{P}Note")}
    assert set(notes.values()) == {"Stellen-Memo", "Kategorie-Memo", "Interview-Memo"}
    source = root.find(f"{P}Sources/{P}TextSource")
    assert notes[source.find(f"{P}PlainTextSelection/{P}NoteRef").get("targetGUID")] == "Stellen-Memo"
    assert notes[source.findall(f"{P}NoteRef")[-1].get("targetGUID")] == "Interview-Memo"
    code_el = next(c for c in root.iter(f"{P}Code") if c.get("name") == "Erschöpfung")
    assert notes[code_el.find(f"{P}NoteRef").get("targetGUID")] == "Kategorie-Memo"
    assert "Bea" in {u.get("name") for u in root.iter(f"{P}User")}
    sheets = _sheets(client.get(f"{base}/categories.xlsx").data)
    name, rows, _root = sheets[2]
    assert name == "Memos" and rows[0][:6] == ["Art", "Nummer", "Kategorie", "Interview", "Fundstelle", "Memo"]
    assert {r[5] for r in rows[1:]} == {"Stellen-Memo", "Kategorie-Memo", "Interview-Memo"}
    passage_row = next(r for r in rows[1:] if r[5] == "Stellen-Memo")
    assert passage_row[1] == "1.1" and ", Pos. 1" in passage_row[4]
    # with the interview (erasure, retention) the memos at it go; the category memo stays
    with app.app_context():
        from app import transcript_coding_view

        transcript_coding_view.forget([db.session.get(Response, rid)])
        db.session.commit()
        assert [m.text for m in db.session.scalars(db.select(CodingMemo))] == ["Kategorie-Memo"]
    # with the analysis everything goes
    client.post(f"{base}/delete")
    with app.app_context():
        assert db.session.get(CodingProject, pid) is None
        assert db.session.scalar(db.select(db.func.count()).select_from(CodingMemo)) == 0


def test_memos_go_with_the_authors_account(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    _memo(bea, base, kind="interview", rid=rid, text="Beas Notiz")
    _memo(client, base, kind="interview", rid=rid, text="Team-Notiz")
    with app.app_context():
        from app import accounts

        user = db.session.scalar(db.select(User).filter_by(orcid=BEA))
        accounts.delete(user, by="self")
        assert [m.text for m in db.session.scalars(db.select(CodingMemo))] == ["Team-Notiz"]
