"""Admin lists: sorting by column, search and pages, done by the database (app/tables.py)."""
import re
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Response, Survey, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember
from app.tables import PER_PAGE, ROW_SIZES, back_args, text_key

ROWS = ROW_SIZES[0]  # people and groups: 10 rows first
from tests.conftest import allow

OTHER = "0000-0002-1825-0097"


def _people(app, n=30):
    """n accounts next to the admin: P00 ... with affiliations and sign-in times in the order of the names."""
    with app.app_context():
        for i in range(n):
            db.session.add(User(orcid=f"0000-0001-0000-{i:04d}", name=f"P{i:02d} Person", number=100 + i,
                                affiliation="Uni Köln" if i % 2 else "RWTH Aachen",
                                last_login_at=datetime(2026, 1, 1) + timedelta(days=i)))
        db.session.commit()


def _section(html, anchor):
    part = html.split(f'id="{anchor}"', 1)[1].split("</section>", 1)[0]
    return part.split('id="design-gruppen"', 1)[0] if anchor == "zugang" else part  # both lists in one panel


def _names(html, anchor="forschende"):
    return re.findall(r'<td class="admin-name">\s*([^<]+?)\s*<', _section(html, anchor))


def _get(client, url):
    r = client.get(url)
    assert r.status_code == 200
    return r.data.decode()


def test_text_key_folds_case_and_accents():
    assert text_key("  Özdemir   Ünal ") == "ozdemir unal" and text_key("Straße") == "strasse" and text_key(None) is None


def test_researchers_come_in_pages(app, client):
    _people(app)
    page = _get(client, "/admin/people")
    assert len(_names(page)) == ROWS and f"1–{ROWS} von 31" in page
    assert 'href="/admin/people?f_page=2#forschende"' in page and 'aria-current="page">1<' in page
    second = _get(client, "/admin/people?f_page=2")
    assert len(_names(second)) == ROWS and f"{ROWS + 1}–{2 * ROWS} von 31" in second
    assert not set(_names(page)) & set(_names(second))
    assert len(_names(_get(client, "/admin/people?f_page=999"))) == 31 % ROWS      # past the end: the last page
    # 10, 20 or 50 rows (Ralf 2026-10-16); the smallest size needs no word in the address
    assert "Zeilen je Seite" in page and 'href="/admin/people?f_n=20#forschende"' in page
    assert len(_names(_get(client, "/admin/people?f_n=50"))) == 31 and len(_names(_get(client, "/admin/people?f_n=7"))) == ROWS
    assert len(_names(_get(client, "/admin/people?f_page=-3&f_page=x"))) == ROWS


def test_researchers_sort_by_every_column(app, client):
    _people(app)
    page = _get(client, "/admin/people")
    assert _names(page)[0] == "Philipp" and _names(page)[1] == "P29 Person"      # default: last sign-in first
    heads = re.findall(r'href="/admin/people\?f_sort=(\w+)&amp;f_dir=(\w+)#forschende"', page)
    # no column "Einrichtung" to sort by any more: the organisation stands there (Ralf 2026-10-16)
    assert [h[0] for h in heads] == ["nr", "name", "org", "groups", "surveys", "running", "own", "completed", "since", "last"]
    assert dict(heads)["last"] == "asc" and dict(heads)["name"] == "asc" and dict(heads)["surveys"] == "desc"
    for key, _ in heads:                                                          # every column answers, both ways
        for direction in ("asc", "desc"):
            assert len(_names(_get(client, f"/admin/people?f_sort={key}&f_dir={direction}"))) == ROWS
    by_name = _get(client, "/admin/people?f_sort=name")
    assert _names(by_name)[:2] == ["P00 Person", "P01 Person"] and 'aria-sort="ascending"' in by_name
    assert 'href="/admin/people?f_sort=name&amp;f_dir=desc#forschende"' in by_name      # a second click turns it round
    assert _names(_get(client, "/admin/people?f_sort=name&f_dir=desc"))[0] == "Philipp"
    assert _names(_get(client, "/admin/people?f_sort=nr&f_dir=desc"))[0] == "P29 Person"
    # what the address asks for is checked: unknown columns and directions fall back, nothing reaches the SQL
    bad = _get(client, "/admin/people?f_sort=orcid;drop table user&f_dir=sideways")
    assert _names(bad)[:2] == ["Philipp", "P29 Person"]


def test_researchers_sort_by_organisation(app, client):
    """The column "Organisation" sorts by the short name of the home organisation (Ralf, 2026-10-20)."""
    from app.models import Organisation, OrganisationMember

    _people(app)
    with app.app_context():
        org = Organisation(key=None, name="Aaa-Institut")
        db.session.add(org)
        db.session.flush()
        user = db.session.scalar(db.select(User).filter_by(name="P17 Person"))
        db.session.add(OrganisationMember(org_id=org.id, user_id=user.id, role="member", is_home=True))
        db.session.commit()
    assert _names(_get(client, "/admin/people?f_sort=org&f_dir=asc"))[0] == "P17 Person"
    assert "P17 Person" not in _names(_get(client, "/admin/people?f_sort=org&f_dir=desc"))


def test_researchers_sort_by_counts(app, client):
    allow(app, OTHER)
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    other.post("/surveys/new", data={"title": "Zählt", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(title="Zählt"))
        for status in (Response.COMPLETED, Response.COMPLETED, Response.ACTIVE):
            db.session.add(Response(survey_id=survey.id, status=status, completed_at=datetime(2026, 5, 1)))
        db.session.add(Response(survey_id=survey.id, status=Response.COMPLETED, source="dummy", is_preview=True))
        db.session.commit()
    for key in ("surveys", "own", "completed"):
        assert _names(_get(client, f"/admin/people?f_sort={key}"))[0] == "Kollegin"
        assert _names(_get(client, f"/admin/people?f_sort={key}&f_dir=asc"))[0] == "Philipp"
    row = _section(_get(client, "/admin/people?f_sort=completed"), "forschende").split("<tbody>")[1].split("</tr>")[0]
    assert re.findall(r"<td>(\d+|–)</td>", row)[-4:] == ["1", "–", "1", "2"]         # surveys, running, own, completed
    ranking = _section(_get(client, "/admin/surveys"), "befragungen")
    assert "<strong>0</strong></td><td>2</td><td>3</td>" in ranking and "Zählt" in ranking   # im Zeitraum, completed, started


def test_researchers_search(app, client):
    _people(app)
    with app.app_context():
        db.session.add(User(orcid="0000-0001-9999-0001", name="Ayşe Özdemir", affiliation="TU Delft"))
        local = User(orcid="q-0123456789abcdef", name="Ohne Orcid")
        group = WorkGroup(name="Lehrstuhl Ökonomie")
        db.session.add_all([local, group])
        db.session.flush()
        db.session.add(UserLogin(user_id=local.id, email="gast@example.org"))
        db.session.add(WorkGroupMember(group_id=group.id, orcid="0000-0001-0000-0007", role="lead"))
        db.session.commit()
    assert _names(_get(client, "/admin/people?f_q=ozdemir")) == ["Ayşe Özdemir"]        # no case, no accents
    assert _names(_get(client, "/admin/people?f_q=ÖZDEMIR+ayse")) == ["Ayşe Özdemir"]   # every word must be found
    assert _names(_get(client, "/admin/people?f_q=0000-0001-0000-0003")) == ["P03 Person"]
    assert _names(_get(client, "/admin/people?f_q=gast@example")) == ["Ohne Orcid"]
    assert _names(_get(client, "/admin/people?f_q=ökonomie")) == ["P07 Person"]         # by the name of a group
    page = _get(client, "/admin/people?f_q=person&f_sort=name")
    assert len(_names(page)) == ROWS and "30 Treffer" in page and "Alle zeigen" in page
    assert 'name="f_sort" value="name"' in page                                   # the search keeps the order
    nothing = _get(client, "/admin/people?f_q=%25_nichts")                              # % and _ are text, not wildcards
    assert "Keine Konten zu dieser Suche" in nothing and "Konto anlegen" in nothing


def test_surveys_list_sorts_searches_and_keeps_the_other_list(app, client):
    _people(app)
    with app.app_context():
        for i in range(PER_PAGE + 3):
            survey = Survey(title=f"Studie {i:02d} " + ("siebzehn" if i == 17 else "x"))
            db.session.add(survey)
            db.session.flush()
            db.session.add(SurveyMember(survey_id=survey.id, orcid="0000-0001-0000-0002", name="zwischengespeichert"))
            for _ in range(i):
                db.session.add(Response(survey_id=survey.id, status=Response.COMPLETED, completed_at=datetime(2026, 5, 1)))
        db.session.commit()
    page = _get(client, "/admin/people?f_sort=name&f_page=2")
    assert 'id="befragungen"' not in page                                          # a tab of its own (Ralf, 2026-10-07)
    listing = _get(client, "/admin/surveys")
    ranking = _section(listing, "befragungen")
    titles = re.findall(r"<td>(Studie \d\d) ", ranking)
    assert titles[:2] == ["Studie 27", "Studie 26"] and len(titles) == ROWS       # default: most participations first
    assert "P02 Person" in ranking and "zwischengespeichert" not in ranking
    assert 'href="/admin/surveys?b_page=2#befragungen"' in listing
    assert _names(page)[0] == "P10 Person"
    second = _section(_get(client, "/admin/surveys?b_page=2"), "befragungen")
    assert re.findall(r"<td>(\d+)</td>\s*<td>(Studie \d\d) ", second)[0] == (str(ROWS + 1), "Studie 17")
    # 10, 20 or 50 rows as in the people's list (Ralf, 2026-10-17)
    assert "Zeilen je Seite" in listing and 'href="/admin/surveys?b_n=20#befragungen"' in listing
    assert len(re.findall(r"<td>(Studie \d\d) ", _section(_get(client, "/admin/surveys?b_n=50"), "befragungen"))) == PER_PAGE + 3
    for key in ("title", "kind", "status", "members", "completed", "started", "last"):
        assert f"b_sort={key}&amp;" in listing
        assert "Studie" in _section(_get(client, f"/admin/surveys?b_sort={key}&b_dir=asc"), "befragungen")
    assert re.findall(r"<td>(Studie \d\d) ", _section(_get(client, "/admin/surveys?b_sort=title"), "befragungen"))[0] == "Studie 00"
    found = _section(_get(client, "/admin/surveys?b_q=STUDIE+siebzehn"), "befragungen")
    assert re.findall(r"<td>(Studie \d\d) ", found) == ["Studie 17"]
    assert len(re.findall(r"<td>(Studie \d\d) ", _section(_get(client, "/admin/surveys?b_q=p02"), "befragungen"))) == ROWS


def test_a_change_of_one_row_comes_back_to_the_same_page(app, client):
    _people(app)
    assert back_args("f_sort=name&f_page=2&next=//evil.example&f_q=a%20b&zz_sort=x&b_dir=desc&__x=1") == {
        "f_sort": "name", "f_page": "2", "f_q": "a b", "zz_sort": "x", "b_dir": "desc"}
    page = _get(client, "/admin/people?f_sort=name&f_page=2")
    assert 'name="back" value="f_sort=name&amp;f_page=2"' in page
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(name="P12 Person"))
    r = client.post(f"/admin/accounts/{uid}/lock", data={"action": "block", "back": "f_sort=name&f_page=2&next=//evil.example"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/people?f_sort=name&f_page=2#forschende")
    assert ">gesperrt<" in _get(client, "/admin/people?f_sort=name&f_page=2")


def test_theme_access_lists(app, client):
    """No design access to give any more (Ralf 2026-10-19): the template of an organisation's design is on its page."""
    _people(app)
    page = _get(client, "/admin/orgs")
    assert "Organisationen" in page and 'name="rwth"' not in page and "design-gruppen" not in page

def test_admin_groups_list(app, client):
    assert "Noch keine Gruppen" in _get(client, "/admin/groups")
    with app.app_context():
        for i in range(PER_PAGE + 2):
            group = WorkGroup(name=f"Gruppe {i:02d}", created_at=datetime(2026, 1, 1) + timedelta(days=i))
            db.session.add(group)
            db.session.flush()
            for m in range(1 + (i == 5) * 3):
                db.session.add(WorkGroupMember(group_id=group.id, orcid=f"0000-0002-{i:04d}-{m:04d}", role="lead",
                                               name=f"Leitung {i:02d}"))
        db.session.commit()

    def names(url):
        return re.findall(r'name="name" value="([^"]+)"', _get(client, url))

    assert names("/admin/groups")[0] == "Gruppe 00" and len(names("/admin/groups")) == ROWS
    assert names("/admin/groups?g_page=2")[0] == "Gruppe 10" and len(names("/admin/groups?g_page=3")) == 27 - 2 * ROWS
    assert names("/admin/groups?g_sort=members")[0] == "Gruppe 05"
    assert names("/admin/groups?g_sort=created")[0] == "Gruppe 26"
    assert names("/admin/groups?g_q=leitung+13") == ["Gruppe 13"] and names("/admin/groups?g_q=0002-0007") == ["Gruppe 07"]
    assert "Keine Gruppen zu dieser Suche" in _get(client, "/admin/groups?g_q=gibtesnicht")
    with app.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).filter_by(name="Gruppe 26"))
    r = client.post(f"/admin/groups/{gid}", data={"name": "Umbenannt", "back": "g_page=2"})
    assert r.headers["Location"].endswith("/admin/groups?g_page=2#gruppen")
    assert names("/admin/groups?g_page=3")[-1] == "Umbenannt"                         # sorted by name
    # the list was a panel of "Personen" (S10) until 2026-10-10: its old addresses lead to the tab, the list as it was
    r = client.get("/admin/?g_page=2&f_page=3")
    assert r.status_code == 308 and r.headers["Location"].endswith("/admin/groups?g_page=2#gruppen")


def test_a_survey_can_leave_the_admins_overview(app, client, survey):
    """Setting "In der Übersicht der Plattform-Admins nicht aufführen": the survey is left out of the list of all
    surveys, the totals still count it, and it can be undone at any time (also after the start)."""
    client.post("/surveys/new", data={"title": "Sichtbar", "languages": ["de"], "default_language": "de"})
    assert "<td>Test " in _section(_get(client, "/admin/surveys"), "befragungen")
    assert 'name="admin_hidden"' in _get(client, "/surveys/1")
    with app.app_context():  # the deletion code is off by default since the pre-test 2026-10-16: switched on here
        db.session.get(Survey, 1).erase_code = True
        db.session.commit()
    client.post("/surveys/1/meta", data={"part": "datenschutz", "admin_hidden_shown": "1", "admin_hidden": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.admin_hidden and s.erase_code   # the other fields of the group stay
        s.status = Survey.RUNNING
        db.session.commit()
    page = _get(client, "/admin/surveys")
    ranking = _section(page, "befragungen")
    assert "<td>Test " not in ranking and "<td>Sichtbar " in ranking
    assert "1 Befragung ist auf Wunsch" in ranking
    assert re.search(r'aic-big">2</span><span class="muted">Befragungen', page)
    assert "<td>Test " not in _section(_get(client, "/admin/surveys?b_q=Test"), "befragungen")
    client.post("/surveys/1/meta", data={"part": "datenschutz", "admin_hidden_shown": "1"})   # running: still allowed
    with app.app_context():
        assert not db.session.get(Survey, 1).admin_hidden
    assert "<td>Test " in _section(_get(client, "/admin/surveys"), "befragungen")
    # forms without the field leave it alone
    client.post("/surveys/1/meta", data={"part": "datenschutz", "admin_hidden_shown": "1", "admin_hidden": "1"})
    client.post("/surveys/1/meta", data={"part": "datenschutz", "retention_shown": "1", "retention_personal": "3",
                                         "retention_answers": "60"})
    with app.app_context():
        assert db.session.get(Survey, 1).admin_hidden
