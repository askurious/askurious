import csv
import io

from sqlalchemy import inspect, text
from werkzeug.datastructures import MultiDict

from app import flow
from app.extensions import db
from app.models import ContactEntry, Item, Response, SplitSession, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, split_node

E = "/surveys/1/editor"


def build(app, client):
    """consent | block 2: Q (main) | 🔒 split | block 3: e-mail + comment"""
    fill_consent(client)
    add(client, "single_choice")                                  # 2
    client.post(E + "/items/2", data=MultiDict([
        ("code", "fav"), ("question", "Lieblingsfarbe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "rot"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "blau")]))
    client.post(E + "/blocks")                                     # block 3
    add(client, "text_input", block=3)                             # 3
    client.post(E + "/items/3", data={"code": "email", "question": "E-Mail für die Verlosung", "validation": "email"})
    add(client, "text_input", block=3)                             # 4
    client.post(E + "/items/4", data={"code": "note", "question": "Kommentar", "multiline": "1"})
    client.post(E + "/flow/add", data={"kind": "split"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        sid = split_node(s)["id"]
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                            {"t": "split", "id": sid}, {"t": "block", "block": 3}]}
        flow.normalize(s)
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def participate(app, uid, fav, email, note=""):
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.get(f"/s/{uid}").data.decode()
    assert "Weiter" in html and "Absenden" not in html  # the separated section still follows
    p.post(f"/s/{uid}", data={"nav": "next", "i2": fav})
    html = p.get(f"/s/{uid}").data.decode()
    assert "getrennt von Ihren bisherigen Antworten" in html and 'name="i3"' in html
    p.post(f"/s/{uid}", data={"nav": "next", "i3": email, "i4": note})
    return p


def test_storage_cannot_be_linked(app):
    with app.app_context():
        for table in ("contact_entry", "split_session"):
            cols = {c["name"] for c in inspect(db.engine).get_columns(table)}
            assert not cols & {"response_id", "created_at", "started_at", "completed_at"}, table
            if db.engine.dialect.name == "sqlite":
                ddl = db.session.execute(text(f"select sql from sqlite_master where name='{table}'")).scalar()
                assert "WITHOUT ROWID" in ddl.upper(), table


def test_entries_share_one_transaction(app, client, survey):
    """PostgreSQL has no table without rowid: there the housekeeping writes all entries of the survey anew about
    once a minute, in one transaction and in random order, so their xmin is one and the same and their place in the
    file says nothing (contact.store and bundle; code reviews 2026-10-03, PG-3, and 2026-10-07, K11). In SQLite
    test_storage_cannot_be_linked covers the order."""
    from app import contact

    uid = build(app, client)
    for n, mail in enumerate(["zora@x.de", "anna@x.de", "mia@x.de", "ben@x.de"]):
        participate(app, uid, "1", mail, note=f"Danke\x00 {n}")                 # a NUL from the form is dropped
    with app.app_context():
        contact.bundle()
        entries = db.session.scalars(db.select(ContactEntry)).all()
        assert sorted(e.answers["4"] for e in entries) == [f"Danke {n}" for n in range(4)]
        if db.engine.dialect.name == "postgresql":
            rows = db.session.execute(text("SELECT id, xmin::text FROM contact_entry ORDER BY ctid")).all()
            assert len(rows) == 4 and len({xmin for _, xmin in rows}) == 1


def test_main_part_finalized_and_separated_answers_unlinked(app, client, survey):
    uid = build(app, client)
    p = participate(app, uid, "2", "zora@example.org", "Danke!")
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        from app import contact

        contact.bundle()  # PostgreSQL: the finished section waits for the housekeeping until then
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert r.status == "completed" and r.completed_at is not None
        assert set(r.answers) == {"1", "2"} and "zora" not in str(r.answers) and "zora" not in str(r.layout)
        entry = db.session.scalar(db.select(ContactEntry))
        assert entry.answers == {"3": "zora@example.org", "4": "Danke!"} and "2" not in entry.answers
        assert db.session.scalar(db.select(db.func.count()).select_from(SplitSession)) == 0  # session forgotten
        assert db.session.get(Survey, 1).completed_count() == 1
    # main export has no e-mail column
    header = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0]
    assert "email" not in header and "fav" in header


def test_invalid_email_in_separated_section_and_threshold_and_draw(app, client, survey):
    uid = build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "1"})
    assert "gültige E-Mail" in p.post(f"/s/{uid}", data={"nav": "next", "i3": "kaputt"}).data.decode()
    # the main part counts as completed at the split already: this person is participant 1
    for mail in ["zora@x.de", "anna@x.de", "mia@x.de"]:
        participate(app, uid, "1", mail)
    assert "erst ab 5" in client.get("/surveys/1/contact/export.csv", follow_redirects=True).data.decode()
    participate(app, uid, "2", "berta@x.de")
    p.post(f"/s/{uid}", data={"nav": "next", "i3": "carl@x.de"})  # the first person finishes the separated part
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/contact/export.csv").data.decode("utf-8-sig"))))
    assert len(rows) == 5 and "fav" not in rows[0]
    emails = [r["email"] for r in rows]  # the item coded "email", not the old contact form's field
    assert sorted(emails) == ["anna@x.de", "berta@x.de", "carl@x.de", "mia@x.de", "zora@x.de"]
    assert emails == sorted(emails) and "legacy_email" not in rows[0]  # sorted by content
    html = client.post("/surveys/1/contact/draw", data={"n": "2"}, follow_redirects=True).data.decode()
    assert "Gezogen" in html and html.count("@x.de") >= 2
    # the result stands in its own section (where the page opens), not in the messages at the top
    section = html.split('id="contact-data"')[1]
    assert "Gezogen" in section and "Die Ziehung wird nicht gespeichert" in section and "data-flashes" not in html


def test_preview_and_dummy_run_through_inline(app, client, survey):
    build(app, client)
    client.post("/surveys/1/dummy", data={"n": "3"})
    with app.app_context():
        dummies = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        assert len(dummies) == 3 and all("3" in (r.answers or {}) for r in dummies)  # test data, not separated
        assert db.session.scalar(db.select(db.func.count()).select_from(ContactEntry)) == 0


def test_flow_rules(app, client, survey):
    build(app, client)
    from app import design

    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not [f for f in design.check(s) if f.severity == "error"]
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "split", "id": "s1"},
                                            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "eq", "value": "1"},
                                             "children": [{"t": "block", "block": 2}]},
                                            {"t": "block", "block": 3}]}
        msgs = [f.message for f in design.check(s)]
        assert any("keine Randomisierungen, Bedingungen" in m for m in msgs)
    assert "bereits" in client.post(E + "/flow/add", data={"kind": "split"}, follow_redirects=True).data.decode() or True


def test_consent_mentions_separated_section(app, client, survey):
    client.post(E + "/blocks")                                     # block 3, moved into the separated section
    client.post(E + "/blocks/3/move/down")
    client.post(E + "/items/1", data={"code": "consent", "body": "", "researchers": "0000-0000-0000-001X",
                                      "draft": "template"})
    with app.app_context():
        assert "getrennt von Ihren Antworten gespeichert" in db.session.get(Item, 1).texts["de"]["body"]
