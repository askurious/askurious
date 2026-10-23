"""The dashboard of Administration > Befragungen (app/admin_stats.py; Ralf 2026-10-17): the first tab of the
administration, counts of real participations per period, small numbers as "< 5", hidden surveys counted but not
named, the period kept in the list's links."""
import re
from datetime import date, datetime, timedelta

from app import admin_stats
from app.extensions import db
from app.models import Response, Survey, utcnow


def _get(client, url):
    r = client.get(url)
    assert r.status_code == 200, (url, r.status_code)
    return r.data.decode()


def _add(app, survey_id, n, *, started, completed=None, status=Response.COMPLETED, source="real", preview=False,
         active=None):
    with app.app_context():
        for _ in range(n):
            db.session.add(Response(survey_id=survey_id, source=source, is_preview=preview, status=status,
                                    started_at=started, completed_at=completed,
                                    last_active_at=active or completed or started))
        db.session.commit()


def _surveys(app, *titles, hidden=()):
    with app.app_context():
        ids = []
        for title in titles:
            s = Survey(title=title, status=Survey.RUNNING, opened_at=utcnow() - timedelta(days=40),
                       admin_hidden=title in hidden)
            db.session.add(s)
            db.session.flush()
            ids.append(s.id)
        db.session.commit()
        return ids


def test_administration_opens_with_surveys_first(client):
    r = client.get("/admin/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/surveys")
    # an address of the former first page "Personen" goes on to its new place
    r = client.get("/admin/?f_org=3&f_sort=name")
    assert r.status_code == 308 and "/admin/people?" in r.headers["Location"] and "f_org=3" in r.headers["Location"]
    page = _get(client, "/admin/surveys")
    tabs = re.findall(r'<a class="[^"]*"[^>]*href="(/admin/[^"#?]*|/feedback/[^"#?]*|/blog/[^"#?]*)"',
                      page.split('class="admin-tabs"', 1)[1].split("</nav>", 1)[0])
    assert tabs[0] == "/admin/surveys" and tabs.index("/admin/people") == 4    # Inhalte first, then Personen
    assert len(tabs) == 12                                                      # the menu did not grow
    assert 'href="/admin/surveys"' in page.split('class="admin-tabs"', 1)[0]   # the header leads here too


def test_counts_only_real_participations_of_the_period(app, client):
    now = utcnow()
    a, b = _surveys(app, "Sichtbar", "Versteckt", hidden=("Versteckt",))
    day = now - timedelta(days=3)
    _add(app, a, 6, started=day, completed=day + timedelta(minutes=9))
    _add(app, b, 4, started=day, completed=day + timedelta(minutes=9))                      # hidden: counted
    _add(app, a, 3, started=day, source="test")                                              # not counted
    _add(app, a, 3, started=day, source="dummy")
    _add(app, a, 2, started=day, preview=True)
    _add(app, a, 2, started=now - timedelta(days=2), status=Response.ACTIVE)                 # abandoned
    _add(app, a, 1, started=now - timedelta(minutes=20), status=Response.ACTIVE,
         active=now - timedelta(minutes=2))                                                  # still at it
    _add(app, a, 5, started=now - timedelta(days=40), completed=now - timedelta(days=40))    # the period before
    with app.app_context():
        d = admin_stats.compute("30")
    assert d["parts"]["started"] == 13 and d["parts"]["completed"] == 10
    assert d["parts"]["prev_completed"] == 5 and d["parts"]["prev_started"] == 5
    assert d["outcome"]["completed"] == 10 and d["outcome"]["abandoned"] == 2 and d["outcome"]["active"] == 1
    assert d["active_now"] == 1 and d["with_input"] == 2
    assert sum(b["done"] for b in d["buckets"]) == 10 and sum(map(sum, d["heat"])) == 10
    assert d["kinds"]["survey"]["completed"] == 10
    page = _get(client, "/admin/surveys")
    assert "Versteckt" not in page                                              # counted, never named
    assert re.search(r'aic-big">10</span><span class="muted">Teilnahmen abgeschlossen', page)
    assert "davor: 5 (+100 %)" in page
    ausgang = page.split('id="ausgang"', 1)[1].split("</section>", 1)[0]
    assert "&lt; 5" in ausgang and ">10<" in ausgang                           # 2 abandoned, 1 running: "< 5"


def test_small_cells_and_change():
    assert admin_stats.small_cell(0) == 0 and admin_stats.small_cell(4) == "< 5" and admin_stats.small_cell(5) == 5
    assert admin_stats.heat_level(4, 100) == 0 and admin_stats.heat_level(100, 100) == 4
    assert admin_stats.change(5, 0) == ""


def test_periods_and_the_list_keep_the_period(app, client, survey):
    now = utcnow()
    (a,) = _surveys(app, "Liste")
    _add(app, a, 7, started=now - timedelta(days=20), completed=now - timedelta(days=20))
    _add(app, a, 2, started=now - timedelta(hours=2), completed=now - timedelta(hours=1))
    with app.app_context():
        assert admin_stats.compute("7")["parts"]["completed"] == 2
        assert admin_stats.compute("30")["parts"]["completed"] == 9
        assert admin_stats.window("24h")["step"] == "hour" and admin_stats.window("365")["step"] == "week"
        since = admin_stats.window("since", date(2025, 1, 1), now)
        assert since["step"] == "month" and since["prev"] is None
        assert admin_stats.window("since", None)["key"] == "30"                  # no date: the standard
    page = _get(client, "/admin/surveys?period=7")
    row = page.split("<td>Liste ", 1)[1].split("</tr>", 1)[0]
    assert "<strong>2</strong>" in row                                          # column "im Zeitraum"
    assert re.search(r'href="/admin/surveys\?[^"]*period=7[^"]*b_sort=completed', page)  # sorting keeps the period
    assert 'aria-current="page">7 Tage</a>' in page
    page = _get(client, "/admin/surveys?period=since&since=2026-09-01")
    assert 'value="2026-09-01"' in page and "verglichen mit" not in page
    _get(client, "/admin/surveys?period=bogus&since=not-a-date")              # anything else: the standard


def test_empty_platform_says_so(client):
    page = _get(client, "/admin/surveys?period=24h")
    assert "Im gewählten Zeitraum kamen keine Teilnahmen an." in page
    assert "Im gewählten Zeitraum wurde keine Teilnahme begonnen." in page


def test_figures_are_kept_for_a_minute(app, client):
    (a,) = _surveys(app, "Zwischenspeicher")
    with app.app_context():
        first = admin_stats.overview(app, "30")
    _add(app, a, 6, started=utcnow() - timedelta(hours=1), completed=utcnow())
    with app.app_context():
        assert admin_stats.overview(app, "30") is first
        app.extensions["admin_stats"].clear()
        assert admin_stats.overview(app, "30")["parts"]["completed"] == 6


def test_privacy_concept_names_the_statistics(app):
    from app import privacy_concept

    with app.test_request_context("/"):
        text = str(privacy_concept.build())
    assert "Statistik der Admins" in text and "nur über die eigene" in text and "app/admin_stats.py" in text


def test_buckets_follow_local_days():
    now = datetime(2026, 10, 25, 12, 0)   # the day the clocks go back
    w = admin_stats.window("7", now=now)
    cuts = admin_stats._buckets(w)
    assert len(cuts) == 8 and all(b[0] < b[1] for b in cuts)
    assert any(b[1] - b[0] == timedelta(hours=25) for b in cuts)
