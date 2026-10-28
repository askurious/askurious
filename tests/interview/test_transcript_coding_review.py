"""Interviews kodieren, stage 3 (app/transcript_coding_review.py): the overviews (table, map, passages, categories ×
interviews, history, coding guide), blurring pairs, merging at once with "Passt", "Meine Stellen herauslösen" and
taking it back, the answers to a rename, and the mutual check."""
from app.extensions import db
from app.models import CodingCategory, CodingEvent, Response
from tests.interview.test_interviewer import add as add_interviewer
from tests.interview.test_interviewer import begin, open_link
from tests.interview.test_transcript_coding import BEA, _guide, _new, _person, _transcript

TEXT = "B: Abends bin ich einfach fertig. Manchmal frage ich mich, wie lange ich das noch schaffe."


def _setup(app, client):
    code, sid = _guide(app, client)
    rid = begin(open_link(app, code, add_interviewer(client, code, "Anna Neu"))[0], code)
    _transcript(app, rid, TEXT)
    pid = _new(client, code, start="empty")
    base = f"/guide/{code}/interviews/coding/{pid}"
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Belastung"})
    with app.app_context():
        main = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, kind="main"))
    bea, bea_id = _person(app)
    client.post(f"{base}/members", data={"kind": "person", "person": BEA})
    return code, pid, rid, base, main, bea


def _sub(who, base, main, name, definition="d"):
    r = who.post(f"{base}/categories/new", data={"kind": "sub", "parent": main, "name": name, "definition": definition, "anyway": "1"})
    assert r.status_code == 302
    with who.application.app_context():
        return db.session.scalar(db.select(CodingCategory.id).filter_by(name=name))


def _code(who, base, rid, word, cat, length=None):
    start = TEXT.index(word)
    r = who.post(f"{base}/i/{rid}/codings", json={"start": start, "end": start + (length or len(word)), "cat": cat})
    assert r.json["ok"], r.json
    return r.json["coding"]["id"]


def test_pages_of_the_overview(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung")
    _code(client, base, rid, "Abends", sub, 29)
    for url in (f"{base}/overview", f"{base}/passages?cat={sub}", f"{base}/matrix",
                f"{base}/history", f"{base}/check"):
        r = client.get(url)
        assert r.status_code == 200, url
    assert "Abends bin ich einfach fertig" in client.get(f"{base}/passages?cat={sub}").data.decode()
    assert client.get(f"{base}/map.svg").data.startswith(b"<svg")
    # the map is in the view "Kategoriensystem" (Liste / Karte, the list by default); an old link to it leads there
    old = client.get(f"{base}/overview?view=map")
    assert old.status_code == 302 and old.headers["Location"].endswith(f"{base}?view=categories&show=map")
    listed, mapped = client.get(f"{base}?view=categories").data.decode(), client.get(f"{base}?view=categories&show=map").data.decode()
    assert 'class="tc-tree"' in listed and "tc-map" not in listed and "Karte als SVG" not in listed
    assert "tc-map" in mapped and "<svg" in mapped and "Karte als SVG" in mapped and 'class="tc-tree"' not in mapped
    assert "tc-map" not in client.get(f"{base}/overview").data.decode()
    assert "tc-map" in bea.get(f"{base}?view=categories&show=map").data.decode()   # members too
    assert "Erschöpfung;1" in client.get(f"{base}/matrix?format=csv").data.decode("utf-8-sig")
    docx = client.get(f"{base}/kodierleitfaden.docx")
    assert docx.status_code == 200 and docx.data[:2] == b"PK"
    assert bea.get(f"{base}/overview").status_code == 200                         # members too
    assert app.test_client().get(f"{base}/overview").status_code == 302            # nobody signed in


def test_merge_answers_and_taking_back(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(bea, base, main, "Müdigkeit")
    exhausted = _sub(client, base, main, "Erschöpfung")
    mine = _code(bea, base, rid, "Abends", tired, 29)
    _code(client, base, rid, "Manchmal", exhausted)
    # overlapping passages in two categories make them a blurring pair
    _code(client, base, rid, "Abends", exhausted, 29)
    page = client.get(f"{base}/overview").data.decode()
    assert "Müdigkeit" in page and "1 Stellen in beiden" in page
    # a merge needs a reason and takes effect at once
    client.post(f"{base}/merge", data={"source": tired, "target": exhausted, "reason": ""})
    with app.app_context():
        assert db.session.get(CodingCategory, tired).merged_into is None
    client.post(f"{base}/merge", data={"source": tired, "target": exhausted, "reason": "gleiche Definition"})
    with app.app_context():
        assert db.session.get(CodingCategory, tired).merged_into == exhausted
        assert db.session.get(CodingCategory, exhausted).former_names == ["Müdigkeit"]
        event = db.session.scalar(db.select(CodingEvent).filter_by(kind="merged"))
        coded = db.session.get(Response, rid).interview["codings"]
        assert next(c for c in coded if c["id"] == mine)["cat"] == tired          # nothing rewritten
    # Bea's passage now counts for "Erschöpfung"; she sees the change and takes her passages out
    page = bea.get(f"{base}/overview").data.decode()
    assert "Änderung betrifft Ihre Stellen" in page and "Meine Stellen herauslösen" in page
    assert "Änderung betrifft Ihre Stellen" not in client.get(f"{base}/overview").data.decode()  # not the one who merged
    bea.post(f"{base}/events/{event.id}/split_off")
    with app.app_context():
        new = db.session.scalar(db.select(CodingCategory).filter_by(project_id=pid, name="Müdigkeit", merged_into=None))
        assert new is not None and new.id != tired and new.author_key.startswith("u:")
        coded = db.session.get(Response, rid).interview["codings"]
        assert next(c for c in coded if c["id"] == mine)["cat"] == new.id
    assert "Änderung betrifft Ihre Stellen" not in bea.get(f"{base}/overview").data.decode()  # answered
    # the merge taken back by whoever made it
    assert bea.post(f"{base}/events/{event.id}/undo").status_code == 403
    client.post(f"{base}/events/{event.id}/undo")
    with app.app_context():
        assert db.session.get(CodingCategory, tired).merged_into is None
        assert not db.session.get(CodingCategory, exhausted).former_names


def test_fits_and_rename(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    cat = _sub(client, base, main, "Angst vor später")
    _code(bea, base, rid, "Manchmal", cat)
    client.post(f"{base}/categories/{cat}", data={"name": "Zukunftssorgen", "definition": "d", "delimitation": ""})
    page = bea.get(f"{base}/overview").data.decode()
    assert "umbenannt" in page and "Alten Namen behalten" in page
    with app.app_context():
        event = db.session.scalar(db.select(CodingEvent).filter_by(kind="changed"))
        assert db.session.get(CodingCategory, cat).former_names == ["Angst vor später"]
    bea.post(f"{base}/events/{event.id}/fits")
    assert "umbenannt" not in bea.get(f"{base}/overview").data.decode()
    assert bea.post(f"{base}/events/{event.id}/fits").status_code == 404           # once


def test_main_categories_are_merged_by_the_team_only(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Last", "anyway": "1"})
    with app.app_context():
        other = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, name="Last"))
    sub = _sub(client, base, main, "Erschöpfung")
    bea.post(f"{base}/merge", data={"source": other, "target": main, "reason": "dasselbe"})
    with app.app_context():
        assert db.session.get(CodingCategory, other).merged_into is None
    client.post(f"{base}/merge", data={"source": main, "target": other, "reason": "dasselbe"})
    with app.app_context():
        assert db.session.get(CodingCategory, main).merged_into == other
        assert db.session.get(CodingCategory, sub).parent_id == other                # its subcategories go along


def test_mutual_check(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    a, b = _sub(client, base, main, "Erschöpfung"), _sub(client, base, main, "Zukunftssorgen")
    coding = _code(client, base, rid, "Abends", a, 29)
    page = bea.get(f"{base}/check").data.decode()
    assert "Abends bin ich einfach fertig" in page and f'value="{coding}"' in page
    bea.post(f"{base}/check", data={"rid": rid, "coding": coding, "cat": b})
    assert "Keine Stellen mehr zum Prüfen" in bea.get(f"{base}/check").data.decode()
    assert "Keine Stellen mehr zum Prüfen" in client.get(f"{base}/check").data.decode()  # own passages are not checked
    with app.app_context():
        checks = db.session.get(Response, rid).interview["checks"]
        assert checks[0]["coding"] == coding and checks[0]["cat"] == b
    page = client.get(f"{base}/overview").data.decode()
    assert "0 %" in page and "unscharf" in page                                     # disagreeing check
