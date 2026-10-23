"""UI/UX audit of 2026-10-07, package F (frame, header, account and administration): W76 to W104, one test per
finding where a test can say something."""
import re
from datetime import datetime
from pathlib import Path

from app.extensions import db
from app.models import User, WorkGroup

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
OTHER = "0000-0002-1825-0097"


def _colleague(client):
    """An account the site admin manages (not one of ADMIN_ORCIDS): its id."""
    with client.application.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        if user is None:
            user = User(orcid=OTHER, name="Kollegin")
            db.session.add(user)
            db.session.commit()
        return user.id


def _english(client):
    client.post("/account/language", data={"lang": "en"})


def _rule(selector):
    return CSS[CSS.index(selector + " {"):].split("}", 1)[0]


def test_w76_the_search_of_the_cards_looks_at_title_project_code_people_and_state_only(client, survey):
    page = client.get("/dashboard/").data.decode()
    search = re.search(r'data-search="([^"]*)"', page).group(1)
    assert "test" in search and "entwurf" in search and "fragebogen" in search
    assert "einstellungen" not in search and "export" not in search and "duplizieren" not in search
    assert 'role="status" data-dash-count' in page
    assert "c.dataset.search" in JS and "c.textContent.toLowerCase()" not in JS


def test_w77_the_red_button_of_ending_a_share_speaks_english(app):
    with app.test_request_context():
        from flask import g

        g.ui_lang = "en"
        html = app.jinja_env.from_string('{% from "_share.html" import end_share %}{{ end_share("/x", "End it") }}').render()
    assert 'data-confirm-ok="End sharing"' in html and "Freigabe beenden" not in html


def test_w79_the_head_of_a_survey_wraps_on_a_phone():
    block = CSS[CSS.index(".survey-head .crumb { margin: 0; }"):][:900]
    assert "@media (max-width: 600px)" in block and "flex-wrap: wrap" in block and ".autosave-page" in block
    assert ".row-fields select { max-width: 100%; }" in CSS                      # W6: the long option of "Rekrutierung"


def test_w80_w81_one_control_for_choosing_a_view(client):
    chosen = _rule(".segmented input:checked + span, .segmented a[aria-current], .segmented a.is-active")
    assert "font-weight: 650" in chosen and "border-color: var(--border-strong)" in chosen and "var(--accent)" in chosen
    assert ".aic-periods a.is-active" not in CSS
    page = client.get("/admin/ai").data.decode()
    assert '<form class="adash-bar aic-periods"' in page and '<nav class="segmented"' in page and 'aria-current="page"' in page
    assert '<nav class="segmented"' in client.get("/feedback/").data.decode()


def test_w82_areas_stay_until_820_px_administration_and_prices_go_first():
    assert "@media (max-width: 1000px) {\n  .site-header .nav-wide" in CSS
    assert "@media (max-width: 820px) {\n  .site-header.signed-in" in CSS
    assert ':root[data-size="125"] .site-header .nav-areas { display: none; }' in CSS  # with larger text earlier


def test_w83_the_tabs_of_the_administration_are_one_row_that_scrolls():
    tabs = _rule(".admin-tabs")
    assert "overflow-x: auto" in tabs and "flex-wrap" not in tabs
    assert '".survey-tabs, .admin-tabs"' in JS


def test_w84_the_one_time_link_comes_after_a_redirect_with_a_copy_button(client):
    r = client.post("/admin/accounts/new", data={"name": "Gast", "email": "gast@example.org"})
    assert r.status_code == 302 and "/admin/accounts/" in r.headers["Location"]
    page = client.get(r.headers["Location"]).data.decode()
    assert "/auth/password/" in page and 'data-copy="#one-time-link"' in page
    assert "/auth/password/" not in client.get(r.headers["Location"]).data.decode()   # shown once
    # a refused form comes back with what was entered and the reason inside it
    again = client.post("/admin/accounts/new", data={"name": "Zweiter Gast", "email": "gast@example.org", "affiliation": "Uni"})
    html = again.data.decode()
    assert again.status_code == 422 and 'value="Zweiter Gast"' in html
    assert "Mit dieser E-Mail-Adresse gibt es schon ein Konto" in html


def test_w85_the_red_button_waits_for_the_name(app, client):
    page = client.get(f"/admin/accounts/{_colleague(client)}/delete").data.decode()
    assert 'data-match-name="Kollegin"' in page and "data-match-button" in page and 'aria-describedby="name-match-hint"' in page
    assert "[data-match-name]" in JS


def test_w86_deleting_a_group_says_what_ends_with_singular_and_plural(client):
    client.post("/groups/new", data={"name": "Lehrstuhl"})
    with client.application.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).order_by(WorkGroup.id.desc()))
    page = client.get(f"/groups/{gid}").data.decode()
    assert "mit 1 Mitglied löschen?" in page and "1 Mitgliedern" not in page
    assert "auf 0 Befragungen, 0 Designs und 0 verknüpfte Elemente für 1 Mitglied" in page


def test_w87_kind_and_state_in_two_columns_a_guide_is_no_draft(client, survey):
    client.post("/guide/new", data={"title": "Gespräch", "language": "de"})
    page = client.get("/admin/surveys").data.decode()
    assert "<th>Art</th>" in page
    row = page[page.index("Gespräch"):].split("</tr>", 1)[0]
    assert "Interviewleitfaden" in row and "Entwurf" not in row and "–" in row


def test_w88_the_wording_state_is_words_not_the_yellow_of_paused(client):
    page = client.get("/constructs/").data.decode()
    assert "zu prüfen</span>" not in page and "validierte Skala" in page and ">Typ<" in page


def test_w89_w95_texts(client):
    from app import experimental

    text = dict((k, t) for k, _l, t in experimental.FEATURES)["agent"]  # Coscine left the group on 2026-10-20
    assert "auf der Seite „Konto › Verbindungen“" in text and "unten" not in text
    page = client.application.test_client().get("/auth/sign-in").data.decode()
    assert "Für ein neues Konto mit Passwort wenden Sie sich bitte an einen Admin der Plattform" in page and "Betreiber" not in page


def test_w90_switching_off_for_everyone_names_who_loses_it(client):
    client.post("/admin/experimental", data={"feature": "coding", "target": "all", "action": "on"})
    page = client.get("/admin/experimental").data.decode()
    assert "Für alle ausschalten" in page and "Nur für Ausgewählte" not in page
    assert re.search(r"\d+ Konten? sieht? die Funktion danach nicht mehr|1 Konto sieht die Funktion danach nicht mehr", page)
    account = client.get(f"/admin/accounts/{_colleague(client)}").data.decode()
    assert "Auch persönlich einschalten" in account and "bleibt, wenn „für alle“ endet" in account


def test_w91_no_heads_over_nothing_and_short_price_fields(client):
    page = client.get("/admin/ai").data.decode()
    assert "<th>Zweck</th>" not in page and "Keine Aufrufe im Zeitraum." in page and "Element-Typ" not in page
    assert ".list-table:where(.is-num) input:not(.narrow) { width: 100%; }" in CSS


def test_w92_file_sizes_with_the_decimal_mark_of_the_page(app):
    with app.test_request_context():
        render = app.jinja_env.from_string("{{ 3900000|filesize }} · {{ 512|filesize }}").render
        assert render() == "3,9 MB · 512 Bytes"


def test_w93_w99_admin_lists_and_header_focus():
    assert ".sort-arrow.is-off" in CSS and ".list-table:where(:not(.is-edit)) th { text-align: left" in CSS
    assert ".site-header a:focus-visible, .site-header summary:focus-visible { outline: var(--focus-outline)" in CSS
    assert "min-width: 24px; min-height: 24px" in _rule(".pin-btn")


def test_w94_the_tab_is_institutions_and_access_is_given_or_removed(client):
    page = client.get("/admin/orgs").data.decode()
    # the institution design had its own tab, "RWTH-Design" (Ralf, 2026-10-07), then a section of "Organisationen";
    # since 2026-10-19 it is the template of an organisation's design on the page of the organisation
    assert "Vorlagen</a>" not in page and "RWTH-Design</a>" not in page and 'name="rwth"' not in page
    assert "Design der Einrichtung" in client.get("/admin/orgs/1").data.decode()
    assert "Designs der Plattform" in client.get("/designs/").data.decode()


def test_w96_w102_adding_a_person_has_a_visible_label(client, survey):
    page = client.get("/surveys/1").data.decode()
    assert '<label for="member-q">Person hinzufügen</label>' in page
    assert ".person-add .button { height: 42px; }" in CSS and "input.name-field { max-width: 40ch; }" in CSS


def test_w97_the_footer_says_whose_surveys_and_today_is_local(client):
    from app.timeutil import start_of_local_day_utc

    # the counts left the footer (Ralf, 2026-10-17); the local day stays for other pages
    assert "laufende Befragung" not in client.get("/dashboard/").data.decode()
    assert start_of_local_day_utc(datetime(2026, 10, 7, 22, 30)) == datetime(2026, 10, 7, 22, 0)  # after midnight in Berlin
    assert start_of_local_day_utc(datetime(2026, 10, 7, 10, 0)) == datetime(2026, 10, 6, 22, 0)


def test_w98_the_buttons_of_a_card_keep_their_order(client, survey):
    page = client.get("/dashboard/").data.decode()
    card = page[page.index('id="survey-1"'):].split("</li>", 1)[0]
    actions = card[card.index('class="card-actions"'):]
    # Einstellungen in the menu "⋯" after the three ways (UI/UX audit 2026-10-16, 6)
    assert actions.index("Fragebogen") < actions.index("Ergebnisse") < actions.index("Export") < actions.index("Einstellungen")
    assert actions.index('class="button button-small"') < actions.index("button-secondary")   # the main action first, filled


def test_w100_one_autosave_line_on_konto(client):
    page = client.get("/account/").data.decode()
    assert page.count("data-autosave-page") == 1


def test_w101_pages_below_a_tab_keep_the_tabs(client):
    uid = _colleague(client)
    for url in (f"/admin/accounts/{uid}", f"/admin/accounts/{uid}/delete", "/feedback/"):
        page = client.get(url).data.decode()
        # the name of the tab is part of the heading (UI/UX audit 2026-10-16, 21)
        assert "<h1>Administration – " in page and 'class="admin-tabs"' in page
    assert '<section class="panel feedback-panel">' in client.get("/feedback/").data.decode()


def test_english_halves_of_the_new_texts(client, survey):
    _english(client)
    page = client.get("/admin/people").data.decode()
    assert ">People</a>" in page and ">Organisations</a>" in page and ">Surveys</a>" in page and ">accounts<" in page
    assert "<th>Kind</th>" in client.get("/admin/surveys").data.decode()
    assert "Institution design" in client.get("/admin/orgs/1").data.decode()
    assert "running surveys" not in client.get("/dashboard/").data.decode()  # no counts in the footer (2026-10-17)
