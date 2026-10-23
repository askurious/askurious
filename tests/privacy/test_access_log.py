"""Security audit 2026-10-04: the access log (N2), a fresh sign-in for actions that hand out or remove everything
(N3), and linked elements that say nothing about surveys without access (N5)."""
import time
from datetime import timedelta

from app import audit, linked
from app.extensions import db
from app.models import AccessLog, Survey, WorkGroup, WorkGroupMember, utcnow
from tests.conftest import fill_consent
from tests.editor.test_linked import OTHER, _answered, _linked_question

ME = "0000-0000-0000-001X"


def _log(app):
    with app.app_context():
        return [(r.action, r.survey_code, r.detail) for r in db.session.scalars(db.select(AccessLog).order_by(AccessLog.id))]


def _stale(client):
    """This browser signed in long ago (more than accounts.FRESH_SECONDS)."""
    with client.session_transaction() as sess:
        sess["signed_in_at"] = time.time() - 3600


def _other(app, role=None, client=None):
    if role:
        client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": role})
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    return other


def test_downloads_and_access_changes_are_logged(app, client, survey):
    with app.app_context():
        code = db.session.get(Survey, 1).code
    assert client.get("/surveys/1/export/data.csv?source=real&source=test").status_code == 200
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "viewer"})
    viewer = _other(app)
    assert viewer.get("/surveys/1/export/data.sav", follow_redirects=True).request.path == "/dashboard/"  # refused
    assert client.get("/surveys/1/export/codebook.csv").status_code == 200
    rows = _log(app)
    assert rows[0] == ("Datenexport CSV", code, "kind=data, source=real+test")
    assert rows[1] == ("Person beteiligt", code, "orcid=0000-0002-1825-0097, role=viewer")
    assert rows[2][0] == "Datenexport CSV" and "kind=codebook" in rows[2][2]
    assert len(rows) == 3                                                       # the refused download left no entry
    page = client.get("/admin/log").data.decode()
    assert "Protokoll" in page and "Person beteiligt" in page and "Nr. 1 Philipp" in page
    assert "Person beteiligt" not in client.get("/admin/log?p_q=xxxxxx").data.decode()
    assert "Person beteiligt" in client.get(f"/admin/log?p_q={code}").data.decode()       # search (Ralf, 2026-10-05)
    page = client.get("/admin/log?p_sort=action&p_dir=asc").data.decode()                     # sort by a column
    assert page.index("Datenexport CSV") < page.index("Person beteiligt") and 'aria-sort="ascending"' in page
    assert viewer.get("/admin/log").status_code == 403                    # site admins only


def test_old_entries_go(app, client, survey):
    client.get("/surveys/1/export/data.csv")
    with app.app_context():
        db.session.add(AccessLog(at=utcnow() - timedelta(days=audit.KEEP_DAYS + 1), action="alt", who="x"))
        db.session.commit()
        assert audit.sweep(utcnow()) == 1
        assert [r.action for r in db.session.scalars(db.select(AccessLog))] == ["Datenexport CSV"]


def test_entries_no_longer_needed_go(app, client, survey):
    """Only what data protection needs stays (Ralf, 2026-10-06): rows of actions that are no longer written down go
    with the next housekeeping, however recent."""
    client.get("/surveys/1/export/data.csv")
    with app.app_context():
        for action in ("Anmeldung (Passwort)", "Experimentelle Funktion geändert", "Konto geändert"):
            db.session.add(AccessLog(at=utcnow(), action=action, who="x"))
        db.session.commit()
        assert audit.sweep(utcnow()) == 3
        assert [r.action for r in db.session.scalars(db.select(AccessLog))] == ["Datenexport CSV"]


def test_contacts_and_erasure_need_a_fresh_sign_in(app, client, survey):
    _stale(client)
    r = client.get("/surveys/1/contact/export.csv")
    assert r.status_code == 302 and "/auth/login?fresh=1" in r.headers["Location"]
    assert "contact-data" in r.headers["Location"]                              # back to the section afterwards
    r = client.post("/surveys/1/contact/draw", data={"n": "1"})
    assert "/auth/login?fresh=1" in r.headers["Location"]
    assert _log(app) == []                                                      # nothing went out
    with client.session_transaction() as sess:
        sess["signed_in_at"] = time.time()
    r = client.get("/surveys/1/contact/export.csv")
    assert "/auth/login" not in (r.headers.get("Location") or "")


def test_admin_actions_need_a_fresh_sign_in(app, client, survey):
    other = _other(app, "viewer", client)
    with app.app_context():
        from app.models import User

        uid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
    _stale(client)
    for url, data in ((f"/admin/accounts/{uid}/lock", {"action": "block"}),
                      (f"/admin/accounts/{uid}/delete", {"name": "Kollegin"}),
                      ("/admin/accounts/new", {"name": "Neu", "email": "neu@example.org"}),
                      ("/admin/database", {"action": "copy", "host": "db.example.org", "name": "q", "user": "u"})):
        r = client.post(url, data=data)
        assert r.status_code == 302 and "/auth/login?fresh=1" in r.headers["Location"], url
    assert other.get("/dashboard/").status_code == 200                          # not blocked, not deleted


def test_linked_element_says_nothing_about_surveys_without_access(app, client, survey):
    key = _linked_question(app, client)
    fill_consent(client)
    for n in range(7):
        _answered(app, 1, 2, "1")
    with app.app_context():
        g = WorkGroup(name="Lehrstuhl")
        g.members.append(WorkGroupMember(orcid=ME, name="Ich", role="lead"))
        g.members.append(WorkGroupMember(orcid=OTHER, name="Andere", role="member"))
        db.session.add(g)
        db.session.commit()
        gid = g.id
    client.post(f"/constructs/linked/{key}", data={"action": "share", "group": gid})
    other = _other(app)
    page = other.get(f"/constructs/linked/{key}").data.decode()
    assert "Weitere Verwendungen liegen außerhalb Ihres Zugriffs" in page
    assert "Dazu 1 Befragung" not in page
    assert "7 echte" not in page and "läuft" not in page.split("Verwendungen")[1].split("</table>")[0]
    assert "7 echte Teilnahmen" in client.get(f"/constructs/linked/{key}").data.decode()   # the maker sees the total
    with app.app_context():
        from app.models import User

        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        info = linked.overview(linked.get(key), user)
        assert info["real"] == 0 and info["real_partial"]
