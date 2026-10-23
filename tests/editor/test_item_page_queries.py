"""The element page of the editor asks the database a fixed number of times, however many drafts the person has
(performance audit 2026-10-18, PERF-04): the list "In andere Befragung kopieren" (duplicate.item_targets) checked
the role on every draft one by one, three queries each (members, shares with groups, the person's groups), and the
page is drawn again after every autosave."""
from sqlalchemy import event

from app import duplicate
from app.extensions import db
from app.models import Item, Survey, SurveyGroup, SurveyMember, User, WorkGroup, WorkGroupMember
from tests.conftest import add

ME = "0000-0000-0000-001X"


def _drafts(app, n, start):
    """n more drafts of the person, every third shared with a group of theirs, every fifth as a viewer only."""
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        group = db.session.scalar(db.select(WorkGroup).filter_by(name="Lehrstuhl"))
        if group is None:
            group = WorkGroup(name="Lehrstuhl")
            db.session.add(group)
            db.session.flush()
            db.session.add(WorkGroupMember(group_id=group.id, user_id=me.id, name="Philipp"))
        for k in range(start, start + n):
            s = Survey(title=f"Entwurf {k}")
            db.session.add(s)
            db.session.flush()
            if k % 3 == 0:
                db.session.add(SurveyGroup(survey_id=s.id, group_id=group.id, role="editor"))
            else:
                db.session.add(SurveyMember(survey_id=s.id, user_id=me.id, name="Philipp",
                                            role="viewer" if k % 5 == 0 else "editor"))
        db.session.commit()


def _queries(app, client, path):
    seen = []

    def count(*_):
        seen.append(1)

    with app.app_context():
        engine = db.engine
    client.get(path)  # warm: what a first request of the process reads once
    event.listen(engine, "before_cursor_execute", count)
    try:
        assert client.get(path).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", count)
    return len(seen)


def test_the_element_page_asks_as_often_with_5_as_with_30_drafts(app, client, survey):
    add(client, "slider")
    path = "/surveys/1/editor/items/2"
    _drafts(app, 5, 0)
    few = _queries(app, client, path)
    _drafts(app, 25, 5)
    many = _queries(app, client, path)
    assert many == few and many < 60, (few, many)


def test_the_targets_are_the_drafts_the_person_may_edit(app, client, survey):
    add(client, "slider")
    _drafts(app, 10, 0)
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        titles = {s.title for s in duplicate.item_targets(me, db.session.get(Item, 2))}
    # k % 3 == 0 through the group as editor; the others own entries, k % 5 == 0 (5, 10) as viewer: no target
    assert titles == {f"Entwurf {k}" for k in range(10) if k % 3 == 0 or k % 5 != 0}
