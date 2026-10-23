from app.extensions import db
from app.models import Survey, SurveyMember
from tests.conftest import add, allow

OTHER = "0000-0002-1825-0097"


def _join(app, role):
    """A second researcher (logged in afterwards) with the given role in survey 1."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role=role, position=1))
        db.session.commit()


def _login_other(app, client):
    client.get("/auth/logout")
    client.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    client.post("/terms/", data={"accept": "1"})


def test_shared_survey_appears_in_list_with_role(app, client, survey):
    _join(app, "viewer")
    _login_other(app, client)
    html = client.get("/dashboard/", follow_redirects=True).data.decode()
    assert "Test" in html and "Ansehen" in html


def test_viewer_reads_but_cannot_change(app, client, survey):
    add(client, "text_input")                                       # item 2, as the admin
    _join(app, "viewer")
    _login_other(app, client)
    assert client.get("/surveys/1/editor/").status_code == 200
    assert "Ihre Rolle ist „Ansehen“" in client.get("/surveys/1/editor/").data.decode()
    client.post("/surveys/1/editor/items/2/delete")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "text_input"})
    client.post("/surveys/1/meta", data={"title": "Gekapert"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.title == "Test" and [i.id for i in s.blocks[1].items] == [2]
    assert client.get("/surveys/1/editor/preview", follow_redirects=True).status_code == 200


def test_editor_changes_questionnaire_not_settings(app, client, survey):
    _join(app, "editor")
    _login_other(app, client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "text_input"})
    client.post("/surveys/1/meta", data={"title": "Gekapert"})
    client.post("/surveys/1/archive", data={"archived": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert len(s.blocks[1].items) == 1 and s.title == "Test" and s.status == "draft" and not s.archived
    html = client.get("/surveys/1", follow_redirects=True).data.decode()
    assert "Einstellungen und Beteiligte ändern nur Admins; den Fragebogen bearbeiten und die Befragung starten" in html


def test_admin_roles_and_last_admin_protected(app, client, survey):
    _join(app, "viewer")
    with app.app_context():
        s = db.session.get(Survey, 1)
        me, other = s.members[0], s.members[1]
        assert me.role == "admin"
        me_id, other_id = me.id, other.id
    client.post(f"/surveys/1/members/{me_id}/role", data={"role": "editor"})   # last admin: refused
    client.post(f"/surveys/1/members/{other_id}/role", data={"role": "admin"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [m.role for m in s.members] == ["admin", "admin"]
    client.post(f"/surveys/1/members/{me_id}/role", data={"role": "viewer"})   # now allowed
    with app.app_context():
        assert [m.role for m in db.session.get(Survey, 1).members] == ["viewer", "admin"]


def test_everyone_may_leave(app, client, survey):
    _join(app, "viewer")
    _login_other(app, client)
    with app.app_context():
        mid = db.session.get(Survey, 1).members[1].id
    client.post(f"/surveys/1/members/{mid}/remove")
    with app.app_context():
        assert len(db.session.get(Survey, 1).members) == 1


def test_viewer_cannot_download_data(app, client, survey):
    _join(app, "viewer")
    _login_other(app, client)
    r = client.get("/surveys/1/export/data.csv")
    assert r.status_code == 302                                    # refused with a message
    assert client.get("/surveys/1/export/codebook.csv").status_code == 200
    html = client.get("/surveys/1/data", follow_redirects=True).data.decode()
    assert 'export/data.csv"' not in html and "erlaubt kein Herunterladen der Daten" in html
    # quality control: viewers see the threshold, saving it needs "Bearbeiten"
    html = client.get("/surveys/1/quality").data.decode()
    assert "Qualitätssicherung" in html and ">Speichern</button>" not in html and "disabled" in html
    assert client.post("/surveys/1/quality", data={"speeder": "70"}).status_code == 302
    with app.app_context():
        assert db.session.get(Survey, 1).speeder_pct == 50


def test_editor_can_download_data(app, client, survey):
    _join(app, "editor")
    _login_other(app, client)
    assert client.get("/surveys/1/export/data.csv").status_code == 200


def test_start_page_has_no_studies_of_others(app, client, survey):
    """Studies of other researchers are only in the admin area (ranking on "Forschende"), not on the start page."""
    with app.app_context():
        s = Survey(title="Fremd läuft", status=Survey.RUNNING, languages=["de"], default_language="de")
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="admin"))
        db.session.add(s)
        db.session.commit()
    html = client.get("/dashboard/", follow_redirects=True).data.decode()
    assert 'class="dash-others"' not in html and "Fremd läuft" not in html
    assert "Fremd läuft" in client.get("/admin/surveys").data.decode()


def test_viewer_cannot_see_a_single_map_position(app, client, survey):
    from app.models import Response
    from tests.items.test_map_marker import setup

    setup(client, result_view="markers")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.responses.append(Response(answers={"2": [{"lat": 50.5, "lon": 6.25, "q": {"a1": "Bahnhof", "b2": 5}}]},
                                    status=Response.COMPLETED))
        db.session.commit()
    html = client.get("/surveys/1/results").data.decode()
    assert "Bahnhof" in html and "Klick auf einen Marker" in html             # admin: popup with the answers
    _join(app, "viewer")
    _login_other(app, client)
    html = client.get("/surveys/1/results").data.decode()
    assert '"lat": 50.5' not in html
    assert "Bahnhof" not in html and "Klick auf einen Marker" not in html


def test_invited_only_people_cannot_start_own_surveys(app, client, survey):
    _join(app, "editor")
    _login_other(app, client)
    html = client.get("/dashboard/", follow_redirects=True).data.decode()
    assert "Neue Befragung" not in html
    r = client.post("/surveys/new", data={"title": "Eigene"}, follow_redirects=True)
    assert "an die Admins der Plattform" in r.data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 1
    allow(app, OTHER)                                                          # the operator lists the iD
    assert "</svg> Fragebogen</a>" in client.get("/dashboard/", follow_redirects=True).data.decode()
    client.post("/surveys/new", data={"title": "Eigene"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 2


def test_people_search_is_for_survey_admins(app, client, survey):
    _join(app, "editor")
    _login_other(app, client)                                                  # registers "Kollegin"
    client.get("/auth/logout")
    client.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    from app.surveys import settings as surveys                                 # the page that searches

    calls = []
    original = surveys._search_people
    surveys._search_people = lambda q, s: calls.append(q) or []
    try:
        client.get("/surveys/1?q=Philipp")                                     # the admin searches
        _login_other(app, client)
        assert client.get("/surveys/1?q=Philipp").status_code == 200           # the editor gets the page, no search
    finally:
        surveys._search_people = original
    assert calls == ["Philipp"]


def test_last_admin_check_reads_the_members_again(app, client, survey):
    """Code review 2026-10-15, H6: two admins demoting each other at once both saw two admins in the members they
    had loaded, and none was left. The check now reads the member rows again, locked (PostgreSQL)."""
    from app.surveys import people

    _join(app, "admin")
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        assert len(s.admins()) == 2                                  # loaded: two admins
        with db.engine.begin() as con:                               # meanwhile the other request demoted one
            con.execute(db.update(SurveyMember).where(SurveyMember.survey_id == 1, SurveyMember.orcid == OTHER)
                        .values(role="viewer"))
        assert people._admins_locked(s) == 1
        assert len(s.admins()) == 1                                  # the loaded rows follow
