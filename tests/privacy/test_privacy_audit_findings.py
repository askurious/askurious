"""Privacy: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit_2026_10_06_a import EditStep, Survey, _editor, db  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_b import _fresh_limits, _running, _start  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_c import Enrolment, Response, _enrol, _new, date, os  # noqa: F401
from tests.helpers.audit_audit_rest import CONSENT_OK, E, Item, add, fill_consent  # noqa: F401
from tests.helpers.audit_cr8_a import app, client, survey  # noqa: F401
from tests.helpers.audit_cr8_b import ContactEntry, Statements, _taken, retention, timedelta, utcnow  # noqa: F401
from tests.helpers.audit_cr8_c import _other, needs_pg  # noqa: F401


# --- from tests/audits/test_audit13_rest.py: Audit Daten und Berechnungen 2026-10-12, package I (exports, choice models, elements, rest): E-3, E-4, E-5, E-6, ---


def test_n11_iat_fast_share_after_slow_trials_are_deleted():
    from app.items.iat import BLOCKS, IAT

    t = []
    for b, n in enumerate(BLOCKS, start=1):
        for k in range(n):
            t.append([b, f"s{k}", 700, 0])
    comb = [x for x in t if x[0] in (3, 4, 6, 7)]  # 120 combined trials
    for x in comb[:12]:
        x[2] = 250   # 12 fast: 10 % of all, but 12/115 of those kept
    for x in comb[12:17]:
        x[2] = 15000  # 5 deleted
    s = IAT.__new__(IAT).scores({"o": 1, "t": t})
    assert s["valid"] == 0 and s["fast"] == round(100 * 12 / 115, 1)


# --- from tests/audits/test_audit_2026_10_06_a.py: Security audit 2026-10-06, package A "Rollen und Objekte": S1, S2, S9, S10, S12, S22, H5 and the side finding on ---


def test_s10_consent_remove_add_and_restore_need_admin(app, client, survey):
    editor = _editor(app)
    with app.app_context():
        consent = db.session.get(Survey, 1).consent_item.id
    editor.post(f"/surveys/1/editor/items/{consent}/delete")
    with app.app_context():
        assert db.session.get(Survey, 1).consent_item is not None
    assert f"/items/{consent}/delete" not in editor.get("/surveys/1/editor/").data.decode()
    client.post(f"/surveys/1/editor/items/{consent}/delete")   # the admin removes it
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.consent_item is None
        entry = next(e.id for e in s.trash if e.data.get("type") == "consent")
        block = next(b.id for b in s.blocks)
    editor.post(f"/surveys/1/editor/blocks/{block}/items", data={"type": "consent"})
    editor.post(f"/surveys/1/editor/trash/{entry}/restore")
    with app.app_context():
        assert db.session.get(Survey, 1).consent_item is None
    client.post(f"/surveys/1/editor/trash/{entry}/restore")
    with app.app_context():
        assert db.session.get(Survey, 1).consent_item is not None
        step = db.session.scalars(db.select(EditStep).order_by(EditStep.id.desc())).first()
        assert step.label == "Einwilligung wiederhergestellt" and step.need == "admin"


# --- from tests/audits/test_audit_2026_10_06_b.py: Package B of the security and performance audit of 2026-10-06: limits for the public pages. ---


def test_deleting_here_is_limited_per_sender(app, client, survey):
    code = _running(app, client)
    app.config["ERASE_LIMIT"] = 1
    env = {"REMOTE_ADDR": "203.0.113.30"}
    p, _ = _start(app, code, env["REMOTE_ADDR"])
    p2, _ = _start(app, code, env["REMOTE_ADDR"])
    assert "gelöscht" in p.post(f"/s/{code}/delete", data={"sure": "1", "here": "1"}, environ_base=env).data.decode()
    r = p2.post(f"/s/{code}/delete", data={"sure": "1", "here": "1"}, environ_base=env)
    assert r.status_code == 429 and "Zu viele Versuche" in r.data.decode()


# --- from tests/audits/test_audit_2026_10_12_c.py: Audit data and calculations 2026-10-12, package C (diary study): E-1, E-2, D-1 to D-7, D-10, D-12 to D-15 and ---


def test_erasure_takes_the_missed_rows_out_of_saved_states(app, client):
    from app import erasure, snapshots

    code = _new(app, client)
    _enrol(app, code)
    with app.test_request_context():
        person = db.session.scalar(db.select(Enrolment))
        person.first_day, person.last_day, person.status = date(2026, 1, 5), date(2026, 1, 6), Enrolment.ACTIVE
        db.session.commit()
        survey = db.session.get(Survey, 1)
        name = snapshots.save(survey)
        path = os.path.join(snapshots.folder(survey), name, "data.csv")
        pid = person.pid
        with open(path, encoding="utf-8-sig") as f:
            assert f.read().count(",verpasst,") == 6
        erasure.erase(survey, list(db.session.scalars(db.select(Response))), "request")
        with open(path, encoding="utf-8-sig") as f:
            assert pid not in f.read()


# --- from tests/audits/test_audit_rest.py: Remaining fixes from the audit of 2026-10-01 (S5, S9, S12 to S15, S17, S18, D-2, D-6, D-7, D-9, D-12 and small ones). ---


def test_consent_stays_fixed_after_reopening_with_real_participations(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "q", "question": "Frage"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    with app.app_context():
        before = dict(db.session.get(Item, 1).config)
    client.post("/surveys/1/consent-facts", data={"min_age": "18", "researchers": "0000-0000-0000-001X"})
    client.post(E + "/items/1", data={"code": "consent", "question": "Einwilligung", "body": "Neu", "duration": "7",
                                      "agree": "Fortsetzen", "disagree": "Nein"})          # ticks removed
    with app.app_context():
        item = db.session.get(Item, 1)
        assert item.config["confirm"] == before["confirm"] and item.config["min_age"] == before["min_age"]
        assert item.t("de")["body"] == "Neu"


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_e2_undo_keeps_shortened_retention(app, client, survey):
    from app import retention
    from tests.privacy.test_retention import build, periods

    build(app, client, personal=6, answers=120, take=1)
    client.post("/surveys/1/meta", data={**periods(3, 12), "part": "datenschutz"})
    with app.app_context():
        assert retention.of(db.session.get(Survey, 1)) == {"personal": 3, "answers": 12}
    page = client.post("/surveys/1/editor/undo", follow_redirects=True).data.decode()
    assert "Aufbewahrungsfristen dürfen nur" in page
    with app.app_context():
        assert retention.of(db.session.get(Survey, 1)) == {"personal": 3, "answers": 12}


# --- from tests/audits/test_cr8_b.py: Code review 2026-10-07, package B: SQLite write locks and housekeeping (K2/D1, K3/D2, K4, E4, K8, T8/D6, K9). ---


def test_anonymised_once_until_reopened(app, client, survey):
    _taken(app, client)
    client.post("/surveys/1/close")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.closed_at = utcnow() - timedelta(days=220)
        db.session.add(ContactEntry(survey_id=1, answers={"9": "anna@example.org"}))
        db.session.commit()
        stamp = db.session.get(Survey, 1).updated_at
        assert retention.run() == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Response).filter(
            Response.survey_id == 1, Response.params.is_not(None))) in (0, 3)  # emptied to JSON null
        assert retention._with_identifiers(db.session.get(Survey, 1)) == []
        s = db.session.get(Survey, 1)
        assert s.retention[retention.DONE] == s.closed_at.isoformat()
        assert retention.of(s) == {"personal": 6, "answers": 120}
        assert s.updated_at == stamp  # the note moves nothing up on the start page
        db.session.commit()
        with Statements() as sql:
            assert retention.run() == 0
        assert not [q for q in sql.seen if q.startswith(("DELETE", "UPDATE"))]
        assert not [q for q in sql.seen if "FROM response" in q]  # nothing read of the participations either
    # opened again and closed later: a new end of the fieldwork, the period counts anew and is carried out again
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.closed_at = utcnow() - timedelta(days=400)
        db.session.add(ContactEntry(survey_id=1, answers={"9": "ben@example.org"}))
        db.session.commit()
        assert retention.run() == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(ContactEntry)) == 0


# --- from tests/audits/test_cr8_c.py: Code review 2026-10-07, package C: counters and changes at the same moment (D3, D4, T2, K11, T7, R7, F8, F13, G7, ---


def test_erasure_counts_add_up_in_the_database(app, client, survey):
    from app import erasure
    from app.models import Erasure

    with app.app_context():
        erasure.count(1, "2026-10-07", "request", 2, 0)
        db.session.commit()
        db.session.scalars(db.select(Erasure)).all()  # this request has read the row ...
        _other(app, "UPDATE erasure SET responses = responses + 4")  # ... when another deletion counts
        erasure.count(1, "2026-10-07", "request", 1, 3)
        db.session.commit()
        db.session.expire_all()
        assert [(r.responses, r.contacts) for r in db.session.scalars(db.select(Erasure))] == [(7, 3)]
        assert erasure.summary(db.session.get(Survey, 1))["responses"] == 7


@needs_pg
def test_postgres_a_waiting_entry_can_be_deleted_on_request(app, client, survey):
    from app import contact, erasure
    from tests.quotas.test_split import build, participate

    uid = build(app, client)
    participate(app, uid, "1", "zora@x.de")
    participate(app, uid, "1", "anna@x.de")
    with app.app_context():
        s = db.session.get(Survey, 1)
        responses, entries = erasure.find(s, "zora@x.de")
        assert responses == [] and len(entries) == 1
        erasure.erase(s, [], "request", entries)
        assert contact.bundle() == 1
        assert [e.answers["3"] for e in contact.entries(db.session.get(Survey, 1))] == ["anna@x.de"]
