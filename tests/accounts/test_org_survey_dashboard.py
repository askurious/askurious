"""Verwaltung > Befragungen (app/org_admin.py; Ralf 2026-10-17): the management of an organisation opens with the
dashboard of its own surveys (app/admin_stats.py), counted only over the organisation's surveys; the tab comes first."""
import re
from datetime import timedelta

from app import admin_stats
from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.accounts.test_organisations import OTHER, THIRD, _as, _org_with_admin
from tests.conftest import allow


def _survey(app, title, org_id, n, hidden=False):
    with app.app_context():
        s = Survey(title=title, status=Survey.RUNNING, org_id=org_id, admin_hidden=hidden,
                   opened_at=utcnow() - timedelta(days=40))
        db.session.add(s)
        db.session.flush()
        t = utcnow() - timedelta(days=2)
        for _ in range(n):
            db.session.add(Response(survey_id=s.id, source="real", status=Response.COMPLETED, started_at=t,
                                    completed_at=t + timedelta(minutes=5), last_active_at=t + timedelta(minutes=5)))
        db.session.add(Response(survey_id=s.id, source="test", status=Response.COMPLETED, started_at=t, completed_at=t))
        db.session.commit()
        return s.id


def test_the_management_opens_with_the_organisations_dashboard(app, client, survey):
    org_id, _ = _org_with_admin(app, client)
    _survey(app, "Unsere Studie", org_id, 7)
    _survey(app, "Versteckt hier", org_id, 3, hidden=True)
    _survey(app, "Fremde Studie", None, 11)                       # the main organisation: not counted
    other = _as(app, OTHER, "Kollegin")
    r = other.get(f"/org/{org_id}/")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/org/{org_id}/surveys")
    r = other.get(f"/org/{org_id}/?p_group=4")                    # the former first page with its list's state
    assert r.status_code == 308 and f"/org/{org_id}/people?p_group=4" in r.headers["Location"]
    page = other.get(f"/org/{org_id}/surveys").data.decode()
    tabs = re.findall(r'href="/org/\d+/(\w*)"', page.split('class="admin-tabs"', 1)[1].split("</nav>", 1)[0])
    assert tabs == ["surveys", "constructs", "people", "groups", "types", "design"]
    assert re.search(r'aic-big">10</span><span class="muted">Teilnahmen abgeschlossen', page)   # 7 + 3 hidden, no test run
    assert "Fremde Studie" not in page and "Versteckt hier" not in page and "Unsere Studie" in page
    assert 'href="/org/%d/surveys?period=7"' % org_id in page and "data-admin-dash" in page
    assert f'href="/org/{org_id}/surveys"' in other.get("/dashboard/").data.decode()   # the menu at the name
    with app.app_context():
        assert admin_stats.compute("30", org_id=org_id)["parts"]["completed"] == 10
        assert admin_stats.compute("30")["parts"]["completed"] == 21
    allow(app, THIRD, keep=True)
    assert _as(app, THIRD, "Dritte").get(f"/org/{org_id}/surveys").status_code == 404   # not an admin of it


def test_the_cache_keeps_organisations_apart(app, client, survey):
    org_id, _ = _org_with_admin(app, client)
    _survey(app, "Hier", org_id, 2)
    _survey(app, "Dort", None, 6)
    with app.app_context():
        assert admin_stats.overview(app, "30", org_id=org_id)["parts"]["completed"] == 2
        assert admin_stats.overview(app, "30")["parts"]["completed"] == 8


def test_the_organisations_surveys_sort_search_and_come_in_pages(app, client, survey):
    """Like Administration > Befragungen: sortable, searchable, 10 rows, switchable to 20 or 50 (Ralf, 2026-10-17)."""
    org_id, _ = _org_with_admin(app, client)
    for i in range(12):
        _survey(app, f"Studie {i:02d}" + (" Kochen" if i == 5 else ""), org_id, i)
    other = _as(app, OTHER, "Kollegin")

    def titles(url):
        part = other.get(url).data.decode().split('id="befragungen"', 1)[1]
        return re.findall(r'<td class="admin-name">(Studie \d\d)', part)

    base = f"/org/{org_id}/surveys"
    assert titles(base) == [f"Studie {i:02d}" for i in range(11, 1, -1)]           # most participations first, 10 rows
    page = other.get(base).data.decode()
    assert "Zeilen je Seite" in page and f'href="{base}?b_n=20#befragungen"' in page
    assert len(titles(base + "?b_n=20")) == 12 and titles(base + "?b_page=2") == ["Studie 01", "Studie 00"]
    assert titles(base + "?b_sort=title&b_dir=asc")[0] == "Studie 00"
    for key in ("title", "kind", "status", "members", "period", "completed"):
        assert f"b_sort={key}&amp;" in page and len(titles(f"{base}?b_sort={key}")) >= 9   # the org may hold one more
    assert titles(base + "?b_q=kochen") == ["Studie 05"]


def test_the_platform_counts_organisations_without_the_guests(app, client, survey):
    """Ralf 2026-10-19: "1 von 7" with six organisations. The guests are no organisation, and a survey without org_id
    belongs to the starting one."""
    from app import orgs

    org_id, _ = _org_with_admin(app, client)
    _survey(app, "Hier", org_id, 2)
    _survey(app, "Im Haus", None, 1)
    with app.app_context():
        assert orgs.guest_id() is not None
        assert admin_stats.compute("30")["people"]["orgs"] == (2, len(orgs.all_orgs(guests=False))) == (2, 2)
