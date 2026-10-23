"""Seal chain checked against the data stored now (siegelkette-konzept.md, Ralf 2026-10-10 "wie empfohlen"): a case
removed in the database past the app showed "Siegel gut" on /verify/ (E2), and on the tab "Export" only after "Jetzt
prüfen" (E1). Now the data are checked every hour and for /verify/, with two verdicts, chain and data (E10), the
researchers and operators are told once, and /verify/ shows the chain in numbers (E3)."""
from datetime import datetime, timedelta

from app import notify, seal
from app.extensions import db
from app.models import Response, SealStatus, Survey
from tests.privacy.test_erasure import CODE, take
from tests.privacy.test_platform_erase import mails
from tests.exports.test_seal import sealed_survey, tsa  # noqa: F401


def cases(app, client, answers):
    uid = sealed_survey(app, client)
    pages = [take(app.test_client(), uid, a) for a in answers]
    return uid, pages


def remove_past_the_app(app, nth):
    """Delete the nth participation straight in the database, as someone with access to it would."""
    with app.app_context():
        ids = db.session.scalars(db.select(Response.id).filter_by(survey_id=1).order_by(Response.id)).all()
        db.session.execute(Response.__table__.delete().where(Response.__table__.c.id == ids[nth]))
        db.session.commit()


def due_again(app):
    with app.app_context():
        db.session.execute(db.update(SealStatus).values(checked_at=datetime(2026, 1, 1)))
        db.session.commit()


def cite(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        seal.anchor(s.uid)
        return seal.summary(s)["cite"]


def test_a_case_removed_in_the_database_breaks_the_seal_on_verify(app, client, survey, tsa):  # noqa: F811
    cases(app, client, ("a", "b", "c", "d"))
    code = cite(app)
    page = app.test_client().get(f"/verify/{code}").data.decode()
    assert "Siegel intakt" in page and "alle 4 vorhandenen Fälle passen" in page
    remove_past_the_app(app, 1)
    due_again(app)                                         # the last check is older than VERIFY_FRESH
    page = app.test_client().get(f"/verify/{code}").data.decode()
    assert "Siegel gebrochen" in page and "Siegel intakt" not in page
    assert "Fall 3 fehlt ohne Löschvermerk" in page and "1 Fall fehlt ohne Löschvermerk" in page
    assert "bis Eintrag 5 lückenlos" in page               # the chain itself still holds


def test_the_hourly_check_shows_on_the_export_tab_and_tells_once(app, client, survey, monkeypatch):
    sent = mails(app, monkeypatch)
    slack = []
    app.config["SLACK_WEBHOOK_URL"] = "https://hooks.slack.test/x"
    monkeypatch.setattr(notify, "post", lambda url, payload: slack.append(payload))
    cases(app, client, ("a", "b", "c"))
    page = client.get("/surveys/1/data").data.decode()     # the tab does not check inside the request (P5)
    assert "automatisch geprüft am" not in page and "läuft im Hintergrund" in page
    with app.app_context():
        assert seal.check_due() == 1
    page = client.get("/surveys/1/data").data.decode()
    assert "automatisch geprüft am" in page and "Kette: intakt" in page and "alle 3 vorhandenen Fälle passen" in page
    remove_past_the_app(app, 2)
    due_again(app)
    with app.app_context():
        assert seal.check_due() == 1
        row = db.session.get(SealStatus, db.session.get(Survey, 1).uid)
        assert (row.chain_ok, row.data_ok, row.missing, row.present) == (True, False, 1, 2)
    assert len(sent) == 1 and "Siegelkette Ihrer Befragung" in sent[0]["Subject"]
    assert "Fall 4 fehlt ohne Löschvermerk" in sent[0].get_content()
    assert len(slack) == 1 and "Siegelkette gebrochen" in slack[0]["text"]
    due_again(app)
    with app.app_context():
        seal.check_due()                                   # the same break: not told again
    assert len(sent) == 1 and len(slack) == 1
    page = client.get("/surveys/1/data").data.decode()
    assert "Siegel gebrochen" in page and "1 Fall fehlt ohne Löschvermerk" in page
    assert "Fall 4 fehlt ohne Löschvermerk" in page
    block = client.post("/surveys/1/export/seal-chain/check", headers={"X-Requested-With": "fetch"}).data.decode()
    assert "Kette: intakt" in block and "Daten: passen nicht zur Kette" in block


def test_a_check_not_yet_due_is_left_alone(app, client, survey):
    cases(app, client, ("a",))
    with app.app_context():
        seal.check_quick(db.session.get(Survey, 1))
        db.session.commit()
        assert seal.check_due() == 0
        two_hours = datetime.utcnow() - timedelta(hours=2)
        db.session.execute(db.update(SealStatus).values(checked_at=two_hours, looked_at=two_hours))
        db.session.commit()
        # nothing changed since: looked at, not read again (audit 2026-10-10, S6/P5)
        assert seal.check_due() == 0
        row = db.session.get(SealStatus, db.session.get(Survey, 1).uid)
        assert row.checked_at == two_hours and row.looked_at > two_hours
        assert seal.check_due() == 0                       # and not due again within the hour
        db.session.execute(db.update(SealStatus).values(checked_at=datetime.utcnow() - timedelta(days=2)))
        db.session.commit()
        assert seal.check_due() == 1                       # unchanged, but the last full check is older than a day
    with app.app_context():
        db.session.add(Response(survey_id=1, status="completed", answers={"2": "b"}, completed_at=datetime(2026, 10, 1)))
        hour_ago = datetime.utcnow() - timedelta(hours=2)
        db.session.execute(db.update(SealStatus).values(looked_at=hour_ago))
        db.session.commit()
        assert seal.check_due() == 1                       # a new case changes the fingerprint


def test_verify_shows_the_chain_in_numbers(app, client, survey, tsa):  # noqa: F811
    uid, pages = cases(app, client, ("a", "b", "c"))
    app.test_client().post(f"/s/{uid}/delete", data={"sure": "1", "code": CODE.search(pages[0]).group(1)})
    code = cite(app)
    page = app.test_client().get(f"/verify/{code}").data.decode()
    assert "Fälle in Zahlen" in page and "beendet und versiegelt" in page
    assert "durch die teilnehmende Person" in page and "bleiben" in page
    assert "Siegel intakt" in page and "alle 2 vorhandenen Fälle passen" in page


def test_counts_name_every_way_and_the_reasons():
    rows = [{"seq": 1, "kind": "fieldstarted", "at": "2026-10-01T08:00:00.000000Z"},
            *({"seq": n, "kind": "sealed", "status": s} for n, s in ((2, "completed"), (3, "completed"),
                                                                     (4, "screenout"), (5, "completed"))),
            {"seq": 6, "kind": "deleted", "ref": 2, "reason": "erasure_self"},
            {"seq": 7, "kind": "deleted", "ref": 3, "reason": "platform: Verstoß"},
            {"seq": 8, "kind": "amended", "ref": 5, "status": "completed", "reason": "changed"}]
    c = seal.counts(rows)
    assert c["cases"] == 4 and c["by_status"] == {"completed": 3, "screenout": 1}
    assert c["deleted"] == 2 and c["by_way"] == {"self": 1, "platform": 1} and c["remaining"] == 2
    assert c["amended"] == 1 and c["reasons"] == [(3, "platform", "Verstoß")]
    assert c["field"] == [("fieldstarted", "2026-10-01T08:00:00.000000Z")]
    purged = seal.counts(rows + [{"seq": 9, "kind": "purged", "reason": "retention"}])
    assert purged["remaining"] == 0 and purged["by_way"]["retention"] == 2


def test_the_checkers_sentences_are_german_on_a_german_page(app):
    with app.test_request_context():
        assert seal.problem_text("timestamp of entry 5: not a hash of this chain").startswith(
            "Zeitstempel zu Eintrag 5: Gehört nicht zu dieser Kette")
        assert seal.problem_text("entry 3: does not follow the entry before") == "Eintrag 3: Folgt nicht auf den Eintrag davor"
        assert seal.problem_text(["missing", 7]) == "Fall 7 fehlt ohne Löschvermerk"


def test_the_entries_are_cases_and_events():
    """A chain holds more entries than cases (Philipp, 2026-10-12): 5 cases, 1 deleted by the person and the end of the
    fieldwork are 7 entries, as the chain of the example linked on the landing page (/verify/42-7-...)."""
    from app import seal

    rows = [{"seq": n, "kind": "sealed", "status": "completed"} for n in range(1, 6)]
    rows += [{"seq": 6, "kind": "deleted", "ref": 3, "status": "completed", "reason": "erasure_self"},
             {"seq": 7, "kind": "fieldclosed", "reason": "manual", "at": "2026-10-12T08:47:26Z"}]
    c = seal.counts(rows)
    assert c["cases"] == 5 and c["deleted"] == 1 and c["remaining"] == 4
    assert c["entries"] == {"total": 7, "cases": 5, "amended": 0, "deleted": 1, "events": 1}
