

def test_favicon_the_serif_q(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200 and r.mimetype == "image/vnd.microsoft.icon"
    assert r.data[:4] == b"\x00\x00\x01\x00"  # an ICO file
    html = client.get("/").data.decode()
    assert 'rel="icon" href="/static/favicon.ico' in html and 'rel="apple-touch-icon"' in html
    assert client.get("/static/img/icon/icon-512.png").status_code == 200


def test_footer_and_imprint_name_both_developers(app, client):
    app.config.update(IMPRINT_NAME="Dr. Erika Muster", IMPRINT_ADDRESS=["Musterstraße 1", "12345 Musterstadt"])
    assert "Dr. Philipp Brauner</a> · Dr. Ralf Philipsen" in client.get("/").data.decode()
    html = client.get("/imprint").data.decode()
    assert '<p class="imprint-address">Dr. Erika Muster<br>Musterstraße 1' in html
    # no ORCID link beside the name (Philipp, 2026-10-10: it showed as "(unauthenticated)")
    assert "Dr. Erika Muster<br>Musterstraße 1<br>12345 Musterstadt" in html and "orcid-link" not in html.split("<main")[1]


def test_imprint_has_no_address_of_its_own():
    """Without IMPRINT_ADDRESS and IMPRINT_NAME in .env the public code names nobody (Ralf, 2026-10-19)."""
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k not in ("IMPRINT_ADDRESS", "IMPRINT_NAME")}
    env["PYTHONIOENCODING"] = "utf-8"
    code = "import config; print(config.Config.IMPRINT_ADDRESS, repr(config.Config.IMPRINT_NAME))"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, encoding="utf-8", check=True).stdout
    assert out.strip() == "[] ''"

def test_landing_page_cites_in_the_text_and_lists_the_references_open(client):
    """Citations in APA style in the text, each linked to its entry; the references with titles in alphabetical order,
    always open without a fold (Philipp, 2026-10-08; for a day closed, Ralf, UI audit W104)."""
    html = client.get("/").data.decode()
    for key, label in (("brauner2024", "Brauner, 2024"), ("brauner2026", "Brauner, 2026"), ("conrad2017", "Conrad et al., 2017"),
                       ("kuehne2018", "Kühne &amp; Kroh, 2018"), ("leiner2019", "Leiner, 2019")):
        assert f'href="#ref-{key}">{label}</a>' in html and f'id="ref-{key}"' in html
    refs = html[html.index('class="landing-references"'):]
    assert "<details" not in refs and "<h2>Literatur</h2>" in refs
    assert "Mapping acceptance" in refs and "From prompts to preferences" in refs
    order = [refs.index(f'id="ref-{k}"') for k in ("brauner2024", "brauner2026", "conrad2017", "kuehne2018", "leiner2019")]
    assert order == sorted(order)


def test_landing_page_links_the_element_types_open_to_everyone(app):
    """"Viele Elementtypen" leads to the list of all element types, also without an account (Philipp, 2026-10-09)."""
    guest = app.test_client()
    home = guest.get("/").data.decode()
    assert 'href="/itemtypes/">Viele Elementtypen</a>' in home
    page = guest.get("/itemtypes/")
    html = page.data.decode()
    assert page.status_code == 200 and "Likert" in html and "In Ihren Befragungen" not in html


def test_visitors_pick_the_language_in_the_header(app):
    """Before signing in, DE/EN right of the gift box; the choice holds for the visit (Philipp, 2026-10-09)."""
    guest = app.test_client()
    home = guest.get("/").data.decode()
    assert 'class="lang-pick"' in home and "Viele Elementtypen" in home
    r = guest.post("/language", data={"lang": "en", "next": "/itemtypes/"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/itemtypes/")
    assert "Many element types" in guest.get("/").data.decode()
    assert guest.post("/language", data={"lang": "en", "next": "https://evil.example/"}).headers["Location"] == "/"
    guest.post("/language", data={"lang": "de", "next": "/"})
    assert "Viele Elementtypen" in guest.get("/").data.decode()
