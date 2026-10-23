"""Code review 2026-10-15, F5: the separated section's session counts the changes of its row, as Response does. Of
two requests that overlap (its last page sent twice) the one that stores second is refused (StaleDataError, shown
as a page sent twice), so the answers become one contact entry, not two."""
import pytest
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import ContactEntry, SplitSession, Survey


def _split(app):
    split = SplitSession(survey_id=1, language="de", layout={}, answers={"2": "eins"}, position=0)
    db.session.add(split)
    db.session.commit()
    return split


def _other_request_stored(split_id):
    # what the other request did meanwhile, on its own connection: its row has a new version
    db.session.execute(db.update(SplitSession).where(SplitSession.id == split_id)
                       .values(version=SplitSession.version + 1).execution_options(synchronize_session=False))


def test_the_second_of_two_overlapping_requests_is_refused(app, client, survey):
    with app.app_context():
        split = _split(app)
        assert split.version == 1
        _other_request_stored(split.id)
        split.position = 1
        with pytest.raises(StaleDataError):
            db.session.commit()
        db.session.rollback()


def test_a_section_finished_twice_is_stored_once(app, client, survey):
    from app import contact

    with app.test_request_context():
        split = _split(app)
        _other_request_stored(split.id)                   # the other request changed it first
        contact.store(db.session.get(Survey, 1), dict(split.answers), split)
        with pytest.raises(StaleDataError):
            db.session.commit()
        db.session.rollback()
        assert db.session.scalar(db.select(db.func.count()).select_from(ContactEntry)) == 0
