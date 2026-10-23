"""Ids of participations are never given twice, and a paper sheet deletes only its own participation (audit data
storage 2026-10-21, B1).

On SQLite a table without AUTOINCREMENT gives the id of a deleted last row to the next one. The period "answers"
(retention.purge) deleted every participation in one statement and left the number on the paper sheets; a new
participation of another survey got that id, and "Bogen löschen" then deleted the stranger's answers."""
import time

from app.extensions import db
from app.models import IdFloor, PaperSheet, Response, Survey
from tests.exports.test_paper import _all_pages, _pdf, _run, _setup, _sheets, _upload, _yes


def _taken_over(app, client):
    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]))))
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "done" and sheet.response_id
        return s["id"], sheet.response_id


def _stranger(app):
    with app.app_context():
        other = Survey(title="Fremde Befragung", uid="fremd123", created_by_id=1)
        db.session.add(other)
        db.session.flush()
        stranger = Response(survey_id=other.id, source="real", status=Response.COMPLETED, answers={"x": "1"})
        db.session.add(stranger)
        db.session.commit()
        return other.id, stranger.id


def _erase(client, sheet_id):
    with client.session_transaction() as sess:
        sess["signed_in_at"] = time.time()
    client.post(f"/surveys/1/paper/sheets/{sheet_id}/erase", data={"why": "wish"})


def test_purge_leaves_no_number_on_the_sheet_and_no_id_is_given_again(app, client, survey):
    from app import retention

    sheet_id, old_id = _taken_over(app, client)
    with app.app_context():
        retention.purge(db.session.get(Survey, 1))
        db.session.commit()
        sheet = db.session.get(PaperSheet, sheet_id)
        assert sheet.response_id is None and sheet.state == "done"  # a sheet scanned again is still refused
        assert db.session.get(IdFloor, "response").highest >= old_id
    other_id, stranger_id = _stranger(app)
    assert stranger_id > old_id
    _erase(client, sheet_id)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=other_id)) == 1
        assert db.session.get(PaperSheet, sheet_id).state == "erased"


def test_a_sheet_naming_a_strangers_participation_leaves_it_alone(app, client, survey):
    """Sheets purged before the fix still name the old number; the participation it names now is not theirs."""
    sheet_id, _old = _taken_over(app, client)
    other_id, stranger_id = _stranger(app)
    with app.app_context():
        db.session.execute(db.update(PaperSheet).where(PaperSheet.id == sheet_id).values(response_id=stranger_id))
        db.session.commit()
    _erase(client, sheet_id)
    with app.app_context():
        assert db.session.get(Response, stranger_id) is not None
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=other_id)) == 1


def test_bulk_deletes_keep_the_floor_of_participations(app, survey):
    """The deletions in one statement pass the mapper events: they lift the floor themselves (lifecycle.delete,
    retention.purge, deleting dummy data) and ORM deletes through the events."""
    from app import lifecycle

    with app.app_context():
        doomed = Survey(title="Weg damit", uid="weg12345", created_by_id=1)
        db.session.add(doomed)
        db.session.flush()
        rows = [Response(survey_id=doomed.id, source="real", status=Response.COMPLETED, answers={}) for _ in range(3)]
        db.session.add_all(rows)
        db.session.commit()
        highest = max(r.id for r in rows)
        lifecycle.delete(db.session.get(Survey, doomed.id), force=True)
        db.session.commit()
        assert db.session.get(IdFloor, "response").highest == highest
        new = Response(survey_id=1, source="real", status=Response.ACTIVE, answers={})
        db.session.add(new)
        db.session.commit()
        assert new.id == highest + 1
        db.session.delete(new)
        db.session.commit()
        again = Response(survey_id=1, source="real", status=Response.ACTIVE, answers={})
        db.session.add(again)
        db.session.commit()
        assert again.id == highest + 2
