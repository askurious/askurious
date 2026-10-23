"""Example surveys (Survey.showcase, app/access.py): every signed-in researcher may view them, without commenting,
and take a copy; only site admins who are their admins switch it. And "Für mich ausblenden" (SurveyHide) on the
start page, for example surveys and surveys shared by others alike (demo-befragungen-konzept.md, Ralf 2026-10-11)."""
from app.extensions import db
from app.models import AccessLog, Survey, SurveyHide, SurveyMember, User
from tests.conftest import add, allow

ME = "0000-0000-0000-001X"       # site admin and admin of survey 1 (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"


def _as(app, orcid, name):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    return c


def _showcase(client, on=True, survey_id=1):
    return client.post(f"/surveys/{survey_id}/showcase", data={"state": "on"} if on else {}, follow_redirects=True)


def test_only_site_admins_who_are_its_admins_switch_it(app, client, survey):
    allow(app, OTHER)
    other = _as(app, OTHER, "Kollegin")
    assert "Als Beispielbefragung allen zeigen" in client.get("/surveys/1").data.decode()
    r = _showcase(client)
    assert "ist jetzt eine Beispielbefragung" in r.data.decode()
    with app.app_context():
        assert db.session.get(Survey, 1).showcase is True
        assert db.session.scalar(db.select(AccessLog.action).filter_by(action="Beispielbefragung an/aus")) is not None
    # an admin of a survey who is no site admin cannot switch it, and sees no switch
    other.post("/surveys/new", data={"title": "Eigene", "languages": ["de"], "default_language": "de"})
    assert "Als Beispielbefragung allen zeigen" not in other.get("/surveys/2").data.decode()
    assert other.post("/surveys/2/showcase", data={"state": "on"}).status_code == 403
    with app.app_context():
        assert db.session.get(Survey, 2).showcase is False
    _showcase(client, on=False)
    with app.app_context():
        assert db.session.get(Survey, 1).showcase is False


def test_everyone_views_an_example_survey_but_changes_nothing(app, client, survey):
    add(client, "text_input")                                                  # item 2
    allow(app, OTHER)
    other = _as(app, OTHER, "Kollegin")
    assert other.get("/surveys/1").status_code == 404                          # not yet an example
    _showcase(client)
    home = other.get("/dashboard/").data.decode()
    assert "Beispielbefragungen" in home and "Test" in home and "Als Kopie übernehmen" in home
    assert 'data-dash-examples open' in home                                    # nothing of one's own yet: open
    assert other.get("/surveys/1/editor/").status_code == 200
    assert other.get("/surveys/1/results").status_code == 200
    other.post("/surveys/1/editor/items/2/delete")
    other.post("/surveys/1/meta", data={"title": "Gekapert"})
    assert other.get("/surveys/1/export/data.csv", follow_redirects=True).request.path == "/dashboard/"
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.title == "Test" and [i.id for i in s.blocks[1].items] == [2]
        assert [m.orcid for m in s.members] == [ME]                             # access, not authorship
    # no comments: not in the preview, not posted, not the team's comment page
    preview = other.get("/surveys/1/editor/preview").data.decode()
    assert "/editor/preview/comment" not in preview
    assert other.post("/surveys/1/editor/preview/comment", data={"text": "Hallo", "block_id": 2}).status_code == 403
    assert other.get("/surveys/1/editor/comments").status_code == 404
    assert "/editor/preview/comment" in client.get("/surveys/1/editor/preview").data.decode()   # the team still can
    # archived: gone for everyone else
    with app.app_context():
        db.session.get(Survey, 1).archived = True
        db.session.commit()
    assert other.get("/surveys/1").status_code == 404
    assert "Beispielbefragungen" not in other.get("/dashboard/").data.decode()


def test_the_example_is_not_counted_as_ones_own(app, client, survey):
    allow(app, OTHER)
    other = _as(app, OTHER, "Kollegin")
    _showcase(client)
    other.post("/surveys/new", data={"title": "Eigene", "languages": ["de"], "default_language": "de"})
    home = other.get("/dashboard/").data.decode()
    assert "Eigene" in home and "Beispielbefragungen" in home
    assert 'data-dash-examples open' not in home                                # with one's own: folded
    with app.app_context():
        from app import access

        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert set(db.session.scalars(access.survey_ids(user))) == {2}          # palette, footer: one's own only
    # the team finds its example only in the section, not in its own list as well (Ralf 2026-10-11), without
    # "Alle für mich ausblenden", which takes only examples one is not part of; with nothing else of its own the
    # section is open, and the card keeps what the team may do there
    mine = client.get("/dashboard/").data.decode()
    assert mine.count('id="survey-1"') == 1 and mine.index("data-dash-examples") < mine.index('id="survey-1"')
    assert "badge-example" in mine and "data-dash-examples open" in mine and "Noch keine Befragungen." in mine
    assert "/dashboard/hide-examples" not in mine
    with app.app_context():
        db.session.get(Survey, 1).archived = True                               # no longer an example: the list
        db.session.commit()
    assert "Beispielbefragungen" not in client.get("/dashboard/").data.decode()
    assert 'id="survey-1"' in client.get("/dashboard/?archived=1").data.decode()


def test_take_a_copy_alone_and_only_with_the_right_to_create(app, client, survey):
    allow(app, OTHER, THIRD)
    _showcase(client)
    other = _as(app, OTHER, "Kollegin")
    page = other.get("/surveys/1/duplicate?from=dashboard").data.decode()
    assert "Als Kopie übernehmen" in page and "with_people" not in page and 'name="link"' not in page
    other.post("/surveys/1/duplicate", data={"title": "Meine Kopie", "with_people": "1", "link": "1"})
    with app.app_context():
        copy = db.session.scalar(db.select(Survey).filter_by(title="Meine Kopie"))
        assert [(m.orcid, m.role) for m in copy.members] == [(OTHER, "admin")]  # alone, the example's people stay
        assert copy.showcase is False and not copy.group_links
    third = _as(app, THIRD, "Gast")
    with app.app_context():                                                     # may not create surveys
        from app.models import UserLogin

        uid = db.session.scalar(db.select(User.id).filter_by(orcid=THIRD))
        db.session.merge(UserLogin(user_id=uid, kinds=None))
        db.session.commit()
    assert "/surveys/1/duplicate" not in third.get("/dashboard/").data.decode()
    third.post("/surveys/1/duplicate", data={"title": "Noch eine"})
    with app.app_context():
        assert db.session.scalar(db.select(Survey).filter_by(title="Noch eine")) is None


def test_hide_for_me_and_show_again(app, client, survey):
    allow(app, OTHER)
    _showcase(client)
    other = _as(app, OTHER, "Kollegin")
    r = other.post("/dashboard/hide/1", follow_redirects=True).data.decode()
    assert "ist für Sie ausgeblendet" in r and "Beispielbefragungen" not in r and "Ausgeblendet (1)" in r
    hidden = other.get("/dashboard/?hidden=1").data.decode()
    assert "Test" in hidden and "Wieder einblenden" in hidden
    assert other.get("/surveys/1").status_code == 200                           # the right stays
    other.post("/dashboard/hide/1", data={"hidden": "0", "view": "hidden"})
    assert "Beispielbefragungen" in other.get("/dashboard/").data.decode()
    other.post("/dashboard/hide-examples")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SurveyHide.id))) == 1
    # admins of a survey archive it instead; they cannot hide it
    r = client.post("/dashboard/hide/1", follow_redirects=True).data.decode()
    assert "archivieren Sie die Befragung" in r
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=ME))
        assert db.session.scalar(db.select(SurveyHide).filter_by(user_id=user.id)) is None


def test_hide_a_survey_shared_with_me(app, client, survey):
    allow(app, OTHER)
    client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "viewer"})
    other = _as(app, OTHER, "Kollegin")
    assert "Für mich ausblenden" in other.get("/dashboard/").data.decode()
    other.post("/dashboard/hide/1")
    home = other.get("/dashboard/").data.decode()
    assert "surveys/1/editor" not in home and "Ausgeblendet (1)" in home
    with app.app_context():
        assert db.session.scalar(db.select(SurveyMember.role).filter_by(survey_id=1, orcid=OTHER)) == "viewer"


def test_group_members_stay_hidden_from_example_viewers(app, client, survey):
    allow(app, OTHER, THIRD)
    client.post("/groups/new", data={"name": "Lehrstuhl"})
    client.post("/groups/1/members", data={"orcid": THIRD, "name": "Geheim Mitglied"})
    client.post("/surveys/1/access-groups", data={"group": 1, "role": "viewer"})
    _showcase(client)
    assert "Geheim Mitglied" in client.get("/surveys/1").data.decode()
    other = _as(app, OTHER, "Kollegin")
    assert "Geheim Mitglied" not in other.get("/surveys/1").data.decode()


def test_an_interview_guide_becomes_an_example(app, client):
    """The switch on the settings of an interview guide was refused with 404: the guide lets only listed survey
    actions through (app/interview.py, OPEN_SURVEY_PAGES; Ralf 2026-10-12)."""
    from tests.interview.test_interview import make

    code, sid = make(app, client)
    r = client.post(f"/surveys/{code}/showcase", data={"state": "on", "next": f"/guide/{code}/settings#showcase"},
                    follow_redirects=True)
    assert r.status_code == 200 and "ist jetzt eine Beispielbefragung" in r.data.decode()
    with app.app_context():
        assert db.session.get(Survey, sid).showcase is True
    allow(app, OTHER)
    other = _as(app, OTHER, "Kollegin")
    assert "Pflegende Angehörige" in other.get("/dashboard/").data.decode()
    assert other.get(f"/guide/{code}").status_code == 200
    other.post(f"/surveys/{code}/showcase", data={})                         # a viewer only: refused
    with app.app_context():
        assert db.session.get(Survey, sid).showcase is True


def test_view_only_editor_offers_a_copy_only_with_the_create_right(app, client, survey):
    """UI/UX audit 2026-10-17, W-S01: who may only look at the questionnaire gets "Als eigene Kopie übernehmen" in the
    editor where duplicating is allowed (an example survey, with the right to create surveys); a plain viewer of a
    shared survey, who may not duplicate it, and someone without the create right get no link that leads nowhere."""
    from app.models import UserLogin

    LINK = "Als eigene Kopie übernehmen"
    assert LINK not in client.get("/surveys/1/editor/").data.decode()               # an admin edits, no link
    allow(app, OTHER)
    other = _as(app, OTHER, "Kollegin")
    _showcase(client)
    page = other.get("/surveys/1/editor/").data.decode()
    assert LINK in page and 'href="/surveys/1/duplicate"' in page
    assert other.get("/surveys/1/duplicate").status_code == 200                      # the link leads somewhere
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        login = db.session.get(UserLogin, user.id)
        login.kinds = None
        db.session.commit()
    assert LINK not in other.get("/surveys/1/editor/").data.decode()                # no right to create surveys
    _showcase(client, on=False)
    allow(app, THIRD)
    third = _as(app, THIRD, "Dritte")
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=THIRD, role="viewer"))
        db.session.commit()
    page = third.get("/surveys/1/editor/").data.decode()
    assert "Ihre Rolle ist „Ansehen“" in page and LINK not in page                  # a viewer may not duplicate
