"""Audit 2026-10-13, AZ-10/11/13: examples and former editors cannot control other teams."""
from app import duplicate, linked
from app.extensions import db
from app.models import Item, Survey, SurveyMember, User
from tests.conftest import allow
from tests.editor.test_linked import _linked_question

OTHER = "0000-0002-1825-0097"


def test_viewer_copy_does_not_keep_private_link(app, client, survey):
    key = _linked_question(app, client)
    allow(app, "*")
    guest = app.test_client()
    guest.get(f"/auth/dev-login?orcid={OTHER}&name=Guest")
    with app.app_context():
        source = db.session.get(Survey, 1)
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        source.members.append(SurveyMember(orcid=OTHER, user_id=user.id, role="viewer", position=1))
        db.session.commit()
        with app.test_request_context():
            copy, _ = duplicate.create(source, user, "Copy")
        questions = [i for b in copy.blocks for i in b.items if i.type == "single_choice"]
        assert questions and not linked.is_linked(questions[0])
        assert len(linked.uses(linked.get(key))) == 1


def test_removed_creator_loses_global_edit_and_dissolve(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        c = linked.get(key)
        user = db.session.get(User, c.created_by_id)
        source = db.session.get(Survey, 1)
        member = next(m for m in source.members if m.orcid == user.orcid)
        member.role = "viewer"
        db.session.commit()
        with app.test_request_context():
            assert not linked.may_change(user, c)
            assert not linked.dissolvable(user, c)


def test_example_presence_is_empty_and_does_not_record_viewer(app, client, survey):
    allow(app, "*")
    with app.app_context():
        source = db.session.get(Survey, 1)
        source.showcase = True
        db.session.commit()
    guest = app.test_client()
    guest.get(f"/auth/dev-login?orcid={OTHER}&name=Guest")
    guest.post("/terms/", data={"accept": "1"})
    response = guest.post("/surveys/1/editor/presence", data={"key": "item:1"})
    assert response.status_code == 200 and response.json == {"others": []}


def test_revision_status_does_not_name_an_inaccessible_survey(app, client, survey):
    from app.models import ConstructShare
    key = _linked_question(app, client)
    allow(app, "*")
    guest = app.test_client()
    guest.get(f"/auth/dev-login?orcid={OTHER}&name=Guest")
    with app.app_context():
        source = db.session.get(Survey, 1)
        source.title, source.status = "Private survey title", Survey.RUNNING
        c = linked.get(key)
        c.person_shares.append(ConstructShare(orcid=OTHER))
        db.session.commit()
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        with app.test_request_context():
            info = linked.overview(c, user)
        assert "Private survey title" not in str(info["versions"])
        assert "Private survey title" not in (info["frozen"] or "")
