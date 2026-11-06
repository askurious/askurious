"""Moving a coded passage to another category (feedback 2026-11-06, P8): "Andere Kategorie …" and dragging it onto a
category keep its place, author and id; the own passages, the team also those of others, who then see it in their
notices; asked first when it is an anchor example; taken back from the status line or the notice."""
from app.extensions import db
from app.models import CodingCategory, Response
from tests.interview.test_interviewer import add, begin, open_link
from tests.interview.test_transcript_coding import _guide, _new, _transcript

FETCH = {"X-Requested-With": "fetch"}


def _codings(app, rid):
    with app.app_context():
        return {c["id"]: c for c in (db.session.get(Response, rid).interview or {}).get("codings") or []}


def test_move_a_passage_ask_notice_and_back(app, client):
    code, _sid = _guide(app, client)
    a, _ = open_link(app, code, add(client, code, "Anna Neu"))
    rid = begin(a, code)
    text = "I: Wie war das?\nB: Anstrengend, sehr anstrengend.\nI: Und dann?\nB: Dann wurde es besser."
    _transcript(app, rid, text)
    pid = _new(client, code, start="empty")
    base = f"/guide/{code}/interviews/coding/{pid}"
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Belastung"})
    with app.app_context():
        main = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, kind="main"))
    for name in ("Erschöpfung", "Zeitdruck"):
        client.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": name, "definition": "x"})
    with app.app_context():
        ids = dict(db.session.execute(db.select(CodingCategory.name, CodingCategory.id).filter_by(project_id=pid)).all())
    tired, rush = ids["Erschöpfung"], ids["Zeitdruck"]
    g, _ = open_link(app, code, add(client, code, "Gruppe 1", has_interview="1", may_interview="", may_code="all"))
    start = text.index("Anstrengend")
    theirs = g.post(f"{base}/i/{rid}/codings", json={"start": start, "end": start + 11, "cat": tired}, headers=FETCH).json["coding"]
    later = text.index("besser")
    ours = client.post(f"{base}/i/{rid}/codings", json={"start": later, "end": later + 6, "cat": tired}, headers=FETCH).json["coding"]
    assert client.post(f"{base}/i/{rid}/codings/{theirs['id']}/anchor", headers=FETCH).json["ok"]

    # someone of a link moves only their own passages
    assert g.post(f"{base}/i/{rid}/codings/{ours['id']}/recode", json={"cat": rush}, headers=FETCH).status_code == 403
    assert client.post(f"{base}/i/{rid}/codings/{ours['id']}/recode", json={"cat": tired}, headers=FETCH).status_code == 400

    # the team moves someone else's passage; an anchor example asks first
    d = client.post(f"{base}/i/{rid}/codings/{theirs['id']}/recode", json={"cat": rush}, headers=FETCH).json
    assert not d["ok"] and "Ankerbeispiel" in d["ask"]
    assert _codings(app, rid)[theirs["id"]]["cat"] == tired
    d = client.post(f"{base}/i/{rid}/codings/{theirs['id']}/recode", json={"cat": rush, "sure": 1}, headers=FETCH).json
    assert d["ok"] and d["coding"]["cat"] == rush and d["coding"]["id"] == theirs["id"]
    now = _codings(app, rid)[theirs["id"]]
    assert now["cat"] == rush and now["by_name"] == theirs["by_name"] == "Gruppe 1" and now["quote"] == "Anstrengend"
    with app.app_context():
        assert db.session.get(CodingCategory, tired).anchor is None
    assert "verschiebt eine Stelle" in client.get(f"{base}/overview").data.decode()

    # the person whose passage it is sees it among the notices and takes it back
    page = g.get(f"{base}/i/{rid}").data.decode()
    assert "hat Ihre Stelle" in page and "Zurück nach „Erschöpfung“" in page
    with app.app_context():
        from app.models import CodingEvent

        eid = db.session.scalar(db.select(CodingEvent.id).filter_by(project_id=pid, kind="recoded"))
    g.post(f"{base}/events/{eid}/split_off", data={})
    assert _codings(app, rid)[theirs["id"]]["cat"] == tired
    with app.app_context():
        assert (db.session.get(CodingCategory, tired).anchor or {}).get("coding") == theirs["id"]  # its slot was free
    assert "hat Ihre Stelle" not in g.get(f"{base}/i/{rid}").data.decode()

    # "Rückgängig" in the status line
    d = g.post(f"{base}/i/{rid}/codings/{theirs['id']}/recode", json={"cat": rush, "sure": 1}, headers=FETCH).json
    assert d["ok"]
    assert client.post(d["undo_url"], headers=FETCH).status_code == 200  # the team may too
    assert _codings(app, rid)[theirs["id"]]["cat"] == tired
    assert g.post(d["undo_url"], headers=FETCH).status_code == 400  # once

    # the quote in the history goes with the interview (erasure, retention)
    from app import transcript_coding_view
    from app.models import CodingEvent

    with app.app_context():
        transcript_coding_view.forget([db.session.get(Response, rid)])
        db.session.commit()
        assert all(not (e.data or {}).get("quote") and not (e.data or {}).get("where")
                   for e in db.session.scalars(db.select(CodingEvent).filter_by(project_id=pid, kind="recoded")))
