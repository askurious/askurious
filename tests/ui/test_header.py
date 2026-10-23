"""The title bar: work areas on the left with the current one marked, the account in a menu at the name."""
import re

GUEST = "0000-0002-1825-0097"


def _header(client, path):
    return re.search(r"<header.*?</header>", client.get(path).data.decode(), re.S).group(0)


def _part(header, cls):
    """One part of the header (the markup from its class up to the next part)."""
    return re.split(r'class="(?:nav-areas|nav-tools|menu nav-menu|nav-menu-panel|nav-narrow[\w -]*)"', header.split(f'class="{cls}"')[1])[0]


def _labels(markup):
    """The labels of the links and buttons in this markup, in order."""
    return re.findall(r">\s*([^<>]+?)\s*</(?:a|button)>", markup)


def test_header_of_an_admin(app, client):
    app.config["DONATION_URL"] = "https://paypal.example/spende"  # the gift only with a donation link (test_surveys.py)
    head = _header(client, "/constructs/")
    areas = _part(head, "nav-areas")
    # Designs and Gruppen are in the menu at the name (Ralf 2026-10-10); the sample size calculator in the menu
    # "Hilfe und Werkzeuge" (UI/UX audit 2026-10-16, 146; Ralf 2026-10-17)
    assert _labels(areas) == ["Meine Befragungen", "Konstrukte"]
    assert re.search(r'<a href="/constructs/" aria-current="page">Konstrukte</a>', areas)   # the page one is on
    assert areas.count("aria-current") == 1
    # Administration and prices stay in the bar (prices as a point of their own, Ralf 2026-10-12); handbook,
    # glossary and feedback are one menu "Hilfe" with words (UI audit 2026-10-12, E11)
    bar = _part(head, "nav-tools")
    assert ">Administration</a>" in bar and 'class="menu nav-menu help-menu"' in bar
    assert 'class="icon-btn donate-link" href="/pricing"' in head.split('class="nav-tools"')[1].split("</nav>")[0]
    help_menu = head.split('class="menu nav-menu help-menu"')[1].split("</details>")[0]
    assert ">Hilfe und Werkzeuge</span>" in help_menu and "data-feedback-open" in help_menu and 'href="/pricing"' not in help_menu
    # "Hilfe zu dieser Seite" first where the handbook has a section on the page (UI/UX audit 2026-10-16, 17)
    assert _labels(help_menu) == ["Hilfe zu dieser Seite", "Handbuch", "Glossar der Kennzahlen", "Elementtypen", "Stichprobengröße",
                                  "Fehler melden oder Vorschlag machen"]
    assert ">Werkzeuge</p>" in help_menu
    # the menu, from where one works to the session: areas and Administration (narrow only), own collections,
    # settings, prices (narrow only), sign out; the legal notice only in the footer (X67 fixed it there)
    panel = head.split('class="menu nav-menu"')[1].split('class="nav-menu-panel"')[1]
    assert _labels(panel) == ["Meine Befragungen", "Konstrukte", "Administration", "Designs", "Gruppen",
                              "Profil und Anmeldung", "Verbindungen", "Anzeige und Sprache", "KI-Nutzung", "Hell / Dunkel",
                              "Preise und Spenden", "Abmelden"]
    assert "Impressum" not in panel
    assert 'action="/auth/logout"' in panel and "data-theme-toggle" in panel
    assert "Menü für Philipp" in head
    # narrower windows: first Administration and prices go into the one menu, in narrow windows the areas too
    # (UI audit 2026-10-07, W82); the rest is in the menu at every width
    narrow = _part(head, "nav-narrow nav-narrow-areas").split("</div>")[0]
    assert _labels(narrow) == ["Meine Befragungen", "Konstrukte"]
    assert ">Meine Sammlungen</p>" in panel and ">Konto</p>" in panel
    more = [_labels(part.split("</div>")[0]) for part in panel.split('class="nav-narrow nav-narrow-more"')[1:]]
    assert more == [["Administration"], ["Preise und Spenden"]]
    assert head.count("data-theme-toggle") == 1 and head.count("data-feedback-open") == 1


def test_header_marks_designs_and_groups_in_the_menu(client):
    for path, label in (("/designs/", "Designs"), ("/groups/", "Gruppen")):
        head = _header(client, path)
        assert f'aria-current="page"><svg' in head and re.search(rf'aria-current="page"><svg[^>]*>.*?</svg> {label}</a>', head, re.S), path
        assert "aria-current" not in _part(head, "nav-areas")


def test_header_marks_the_area_of_a_survey_and_the_account(client, survey):
    for path in ("/dashboard/", "/surveys/1", "/surveys/1/editor/"):
        assert 'aria-current="page">Meine Befragungen</a>' in _part(_header(client, path), "nav-areas"), path
    head = _header(client, "/account/")
    assert "aria-current" not in _part(head, "nav-areas")
    assert re.search(r'<a href="/account/" aria-current="page">', head)
    assert 'aria-current="page">Administration</a>' in _part(_header(client, "/admin/people"), "nav-tools")


def test_header_without_admin_right_and_without_groups(app):
    app.config["DONATION_URL"] = "https://paypal.example/spende"
    guest = app.test_client()
    guest.get(f"/auth/dev-login?orcid={GUEST}&name=Gast")
    guest.post("/terms/", data={"accept": "1"})
    head = _header(guest, "/dashboard/")
    assert re.findall(r">([^<>]+)</a>", _part(head, "nav-areas")) == ["Meine Befragungen", "Konstrukte"]
    assert "Administration" not in head and "Gruppen" not in head and " Designs</a>" in head
    assert "Preise und Spenden</a>" in _part(head, "nav-narrow nav-narrow-more")


def test_header_signed_out(app):
    head = _header(app.test_client(), "/")
    assert "nav-menu" not in head and "nav-areas" not in head and "signed-in" not in head
    assert "Mit ORCID anmelden" in head and "data-theme-toggle" in head and "donate-link" not in head  # no link set
    app.config["DONATION_URL"] = "https://paypal.example/spende"
    assert 'class="icon-btn donate-link"' in _header(app.test_client(), "/")
