import re

from app.extensions import db
from app.models import Survey, SurveyPin


def _ids(html):
    return [int(n) for n in re.findall(r'<li class="card[^"]*" id="survey-(\d+)"', html)]


def test_search_and_sort_on_my_surveys(app, client):
    """Search and order of "Meine Befragungen" run on the server over all of one's surveys, the list comes in pages
    (audit 2026-10-22, G-05; before: in the browser over every card of the page, Ralf 2026-10-05)."""
    for title in ("Alpha", "Beta", "Gamma"):
        client.post("/surveys/new", data={"title": title})
    html = client.get("/dashboard/").data.decode()
    assert 'class="dash-find"' not in html  # three cards: one page, no search shown
    html = client.get("/dashboard/?d_sort=title").data.decode()
    assert 'name="d_q"' in html and 'value="title" selected>Titel A–Z' in html
    assert _ids(html) == [1, 2, 3]  # Alpha, Beta, Gamma
    assert _ids(client.get("/dashboard/").data.decode()) == [3, 2, 1]  # last changed first
    found = client.get("/dashboard/?d_q=gam").data.decode()
    assert _ids(found) == [3] and "1 Befragung gefunden" in found and 'role="status" data-dash-count' in found
    none = client.get("/dashboard/?d_q=zzz").data.decode()
    assert _ids(none) == [] and "Keine Befragung passt zur Suche." in none and 'name="d_q" value="zzz"' in none


def test_my_surveys_come_in_pages_of_10_20_50_with_pinned_first(app, client):
    for k in range(23):
        client.post("/surveys/new", data={"title": f"Studie {k:02d}"})
    with app.app_context():
        db.session.add(SurveyPin(user_id=1, survey_id=2))
        db.session.commit()
    first = client.get("/dashboard/").data.decode()
    assert len(_ids(first)) == 10 and _ids(first)[0] == 2  # the pinned one on top, then the last changed
    assert "1–10 von 23" in first and "Zeilen je Seite:" in first and 'href="/dashboard/?d_page=2"' in first
    third = client.get("/dashboard/?d_page=3").data.decode()
    assert len(_ids(third)) == 3
    assert len(_ids(client.get("/dashboard/?d_n=20").data.decode())) == 20
    assert len(_ids(client.get("/dashboard/?d_n=50").data.decode())) == 23
    assert len(_ids(client.get("/dashboard/?d_n=7").data.decode())) == 10  # only the offered sizes


def test_pages_keep_the_filters_of_the_list(app, client):
    for k in range(12):
        client.post("/surveys/new", data={"title": f"Studie {k:02d}"})
    html = client.get("/dashboard/?art=fragebogen&d_sort=title").data.decode()
    assert re.search(r'href="/dashboard/\?(?=[^"]*art=fragebogen)(?=[^"]*d_sort=title)(?=[^"]*d_page=2)[^"]*"', html)
    assert '<input type="hidden" name="art" value="fragebogen">' in html


def test_search_looks_at_title_project_code_people_and_state_but_not_buttons(app, client):
    """UI audit W76: kind and state as the card shows them count, the labels of the buttons do not."""
    client.post("/surveys/new", data={"title": "Test"})
    with app.app_context():
        code = db.session.get(Survey, 1).code
    for q in ("test", "entwurf", "fragebogen", code, "philipp"):
        assert _ids(client.get(f"/dashboard/?d_q={q}").data.decode()) == [1], q
    for q in ("einstellungen", "export", "duplizieren"):
        assert _ids(client.get(f"/dashboard/?d_q={q}").data.decode()) == [], q


def test_a_diary_survey_is_found_by_its_kind(app, client):
    """The card says "Tagebuchstudie", so the search finds it by that word and not as "Fragebogen" (UI/UX audit
    2026-10-29, WQ-05)."""
    client.post("/diary/new", data={"title": "Alltag", "language": "de", "description": "x"})
    client.post("/surveys/new", data={"title": "Umfrage"})
    with app.app_context():
        diary = db.session.scalar(db.select(Survey.id).filter_by(title="Alltag"))
        other = db.session.scalar(db.select(Survey.id).filter_by(title="Umfrage"))
    assert _ids(client.get("/dashboard/?d_q=tagebuchstudie").data.decode()) == [diary]
    assert _ids(client.get("/dashboard/?d_q=fragebogen").data.decode()) == [other]
