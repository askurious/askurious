"""UI/UX audit of 2026-10-09, package E (frame, header, account and administration): X68 to X80 and the parts of S13
outside the footer, one test per finding where a test can say something."""
import re
from datetime import timedelta
from pathlib import Path

from app.extensions import db
from app.models import EditStep, User, utcnow

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
OTHER = "0000-0002-1825-0097"


def _other(app):
    """A second researcher, not an admin of the platform, signed in with the current terms."""
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    c.post("/terms/", data={"accept": "1"})
    return c


def _english(client):
    client.post("/account/language", data={"lang": "en"})


# --- X68: the app's own error pages -------------------------------------------------------------------------------

def test_x68_404_in_the_frame_with_the_way_back_and_in_the_language_of_the_person(app, client, survey):
    r = client.get("/gibt-es-nicht")
    page = r.data.decode()
    assert r.status_code == 404
    assert "Diese Seite gibt es nicht (mehr)" in page and "Not Found" not in page
    assert 'class="site-header' in page and 'class="site-footer' in page        # header and footer with the imprint
    assert 'href="/dashboard/"' in page and "Zu „Meine Befragungen“" in page
    other = _other(app)
    _english(other)
    page = other.get("/surveys/1/editor/").data.decode()                       # a survey of someone else
    assert "This page does not exist (any more)" in page and '<html lang="en"' in page
    assert "an admin of the survey can give you access" in page


def test_x68_404_signed_out_leads_to_the_home_page(app):
    page = app.test_client().get("/gibt-es-nicht").data.decode()
    assert "Zur Startseite" in page and "Zu „Meine Befragungen“" not in page


def test_x68_403_says_who_gives_the_permission(app, client):
    r = _other(app).get("/admin/people")
    page = r.data.decode()
    assert r.status_code == 403
    assert "Dafür fehlt Ihrem Konto die Berechtigung" in page and "Forbidden" not in page
    assert "Admin der Plattform" in page and "data-feedback-open" in page


def test_x68_500_in_the_frame_with_feedback(app):
    from werkzeug.exceptions import InternalServerError

    @app.get("/kaputt-test")
    def _broken():
        raise InternalServerError()

    page = _other(app).get("/kaputt-test").data.decode()
    assert "Hier ist etwas schiefgegangen" in page and "data-feedback-open" in page


def test_x68_participant_pages_keep_their_own_error_page(app):
    page = app.test_client().get("/s/zzzzzz").data.decode()
    assert "site-header" not in page                                            # participant/error.html


def test_x68_every_feedback_button_opens_the_dialog():
    assert 'querySelectorAll("[data-feedback-open]")' in JS


# --- X69: new terms -----------------------------------------------------------------------------------------------

def test_x69_new_terms_say_what_is_new_jump_to_the_confirmation_and_offer_sign_out(app, client):
    from app import terms

    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        user.terms_version = "2026-10-07"
        db.session.commit()
        current = terms.version()
    page = client.get("/terms/").data.decode()
    assert 'href="#bestaetigung"' in page and 'id="bestaetigung"' in page
    assert f"Neu in Version {current}:" in page and 'href="#abschnitt-7"' in page and 'id="abschnitt-7"' in page
    assert "Neu in Version 2026-10-08:" in page and "Neu in Version 2026-10-06:" not in page
    assert 'form="terms-sign-out"' in page and 'id="terms-sign-out"' in page and "/account/delete" in page
    assert 'class="nav-areas"' not in page                                      # no areas that lead back here
    _english(client)
    page = client.get("/terms/").data.decode()
    assert "New in version" in page and 'id="abschnitt-7"' in page and "Sign out" in page
    client.post("/terms/", data={"accept": "1"})
    assert 'class="nav-areas"' in client.get("/terms/").data.decode()


def test_x69_every_version_of_the_terms_says_what_changed():
    from app import terms

    source = (ROOT / "licence.md").read_text(encoding="utf-8")
    current = re.search(r"^Version:\s*(\S+)", source, re.M).group(1)
    assert terms.CHANGES[0][0] == current, "licence.md has a new version: add what changed to terms.CHANGES"


def test_x69_a_new_account_gets_no_list_of_changes(app):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    page = c.get("/terms/").data.decode()
    assert "Willkommen!" in page and "Neu in Version" not in page and 'href="#bestaetigung"' in page


# --- S13: the imprint in the account menu -------------------------------------------------------------------------

def test_s13_imprint_in_the_footer_not_twice_in_the_account_menu(client):
    """S13 put the legal notice into the account menu too, while the footer cut it; since X67 the footer never cuts
    it, so the menu holds it no more (Ralf 2026-10-10)."""
    page = client.get("/dashboard/").data.decode()
    menu = page[page.index('class="nav-menu-panel"'):page.index("</details>", page.index('class="nav-menu-panel"'))]
    assert "/imprint" not in menu
    assert 'href="/imprint"' in page[page.index("<footer"):]


# --- X70: the list of the last changes shows the day ----------------------------------------------------------------

def test_x70_older_changes_show_their_day(app, client, survey):
    client.post("/surveys/1/editor/blocks")                                     # a step to take back
    with app.app_context():
        step = db.session.scalar(db.select(EditStep).order_by(EditStep.id.desc()))
        assert step is not None
        step.created_at = utcnow() - timedelta(days=3)
        db.session.commit()
    page = client.get("/surveys/1/editor/").data.decode()
    times = re.findall(r'<span class="undo-time">([^<]*)</span>', page)
    assert times and all(re.search(r"\d{2}\.\d{2}\.\d{4}", t) for t in times)


# --- X72: one word and one way to the admins ------------------------------------------------------------------------

def test_x72_no_operator_and_a_way_to_the_admins(app, client):
    c = _other(app)
    page = c.post("/surveys/new", data={"title": "Eigene"}, follow_redirects=True).data.decode()
    assert "Betreiber" not in page and "an die Admins der Plattform" in page
    assert page.count("data-feedback-open") >= 2                                # the header and the empty list


# --- X73 to X77: words ----------------------------------------------------------------------------------------------

def test_x73_groups_point_to_the_tab_of_the_design(client):
    page = client.get("/admin/groups").data.decode() if client.get("/admin/groups").status_code == 200 else \
        client.get("/admin/people").data.decode()
    assert "weiter unten unter „Design der Einrichtung“" not in page
    # Ralf 2026-10-16: the design of an institution goes only to organisations, so no pointer for groups any more
    assert "/admin/designs#design-gruppen" not in page


def test_x74_no_template_for_the_institution_design():
    for name in ("accounts/delete.html", "designs/index.html"):
        text = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
        assert "Freigaben für Vorlagen" not in text and "Vorlage einer Einrichtung" not in text


def test_x75_export_of_several_names_the_real_button_and_the_kind(client, survey):
    page = client.get("/dashboard/export").data.decode()
    assert "„+ Fragebogen“ › „Aus einer Datei anlegen“" in page and "„Neue Befragung“" not in page


def test_x76_the_features_themselves_may_change_and_coscine_without_pointing_at_this_page(client):
    from tests.conftest import experiments_on

    experiments_on(client, "agent")  # Coscine left the group on 2026-10-20
    page = re.sub(r"\s+", " ", client.get("/account/").data.decode())
    assert "Die Funktionen selbst können sich noch ändern." in page
    assert "Ein persönliches Token unter „Konto › Verbindungen“ verbindet" in page
    assert "auf ihrer Seite „Konto“" not in page


def test_x77_projects_in_the_dative(app):
    from app import experimental

    with app.test_request_context("/"):
        assert experimental.uses("ainstein") == (0, "Projekten")


# --- X78 to X80: layout ---------------------------------------------------------------------------------------------

def test_x78_search_and_sort_wrap_on_the_phone():
    rule = CSS[CSS.index("@media (max-width: 600px) { .dash-find"):]
    rule = rule[:rule.index("} }") + 3]
    assert "flex-wrap: wrap" in rule and ".dash-find select { flex: 1 1 140px; min-width: 0" in rule


def test_x79_orcid_buttons_carry_the_id_icon(app):
    app.config['ORCID_CLIENT_ID'] = 'configured-client'
    page = app.test_client().get("/").data.decode()
    buttons = re.findall(r'<a class="button" href="/auth/login[^"]*">(.*?)</a>', page, re.S)
    assert buttons and all("orcid-id.svg" in b for b in buttons)
    page = app.test_client().get("/auth/sign-in").data.decode()
    assert 'action="/auth/login"' in page
    assert re.search(r'<button class="button"><img class="orcid-icon"', page)
    assert 'name="remember"' in page


def test_x80_description_field_and_construct_search_42_px(client):
    assert "input.description-field { max-width: 60ch; }" in CSS
    assert ".construct-search input[type=search] { flex: 1; height: var(--field-h); }" in CSS
    assert 'class="description-field"' in client.get("/groups/").data.decode()
