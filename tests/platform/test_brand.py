"""A second name under a second domain (app/brand.py; Ralf 2026-10-09: polltergeist.de as "Polltergeist")."""
import re

from app import brand
from tests.conftest import allow

WORD = re.compile(r"\bAskurious\b")


def _branded(app, participant=""):
    app.config.update(PUBLIC_BASE_URL="https://askurious.de", PARTICIPANT_BASE_URL=participant,
                      BRANDS="polltergeist.de=Polltergeist", SECRET_KEY="x" * 32)
    return app.test_client()


def test_parse_skips_broken_entries():
    assert brand.parse(" Polltergeist.de = Polltergeist , =X, y.de=, z.de=Z") == [("polltergeist.de", "Polltergeist"),
                                                                                   ("z.de", "Z")]
    assert brand.parse("") == []


def test_the_second_domain_shows_its_name_everywhere(app):
    c = _branded(app)
    html = c.get("/", headers={"Host": "polltergeist.de"}).data.decode()
    assert "Polltergeist" in html and not WORD.search(html)
    assert 'data-brand="Polltergeist"' in html
    html = c.get("/imprint", headers={"Host": "polltergeist.de"}).data.decode()
    assert "Polltergeist" in html and not WORD.search(html)
    # the main domain and an unknown name (a forged Host header) keep the main name
    for host in ("askurious.de", "evil.example"):
        html = c.get("/", headers={"Host": host}).data.decode()
        assert "Askurious" in html and "Polltergeist" not in html


def test_signed_in_pages_carry_the_name_too(app):
    c = _branded(app)
    allow(app, "*")
    head = {"Host": "polltergeist.de"}
    c.get("/auth/dev-login?orcid=0000-0002-0000-0001&name=X", headers=head)
    c.post("/terms/", data={"accept": "1"}, headers=head)
    html = c.get("/dashboard/", headers=head, follow_redirects=True).data.decode()
    assert "Polltergeist" in html and not WORD.search(html)


def test_static_files_stay_as_they_are(app):
    c = _branded(app)
    js = c.get("/static/app.js", headers={"Host": "polltergeist.de"})
    assert b"Askurious" in js.data                                                     # the script swaps it itself


def test_links_stay_on_the_domain_of_the_request(app):
    _branded(app, participant="https://ask.askurious.de")
    with app.test_request_context("/", headers={"Host": "polltergeist.de"}):
        assert brand.public_base() == "https://polltergeist.de"
        assert brand.participant_base_url() == "https://ask.polltergeist.de"
        from app.hosts import participant_base, url_for

        assert participant_base() == "https://ask.polltergeist.de"
        assert url_for("public.index", _external=True) == "https://polltergeist.de/"
    with app.test_request_context("/", headers={"Host": "ask.polltergeist.de"}):
        from app.hosts import current_side

        assert brand.name() == "Polltergeist" and current_side() == "participant"
    with app.test_request_context("/", headers={"Host": "evil.example"}):
        assert brand.public_base() == "https://askurious.de" and brand.name() == "Askurious"


def test_texts_outside_pages_and_work_outside_a_request(app):
    _branded(app)
    from app.lang import de_en

    with app.test_request_context("/", headers={"Host": "polltergeist.de"}):
        assert de_en("Mit Askurious erstellt", "Made with Askurious") == "Mit Polltergeist erstellt"
        from app.mail import signature

        assert signature().startswith("\n\n-- \nPolltergeist · https://polltergeist.de")
    with app.app_context():
        assert brand.name() == "Askurious" and de_en("Askurious", "Askurious") == "Askurious"
        with brand.use("polltergeist.de"):
            assert brand.public_base() == "https://polltergeist.de" and brand.apply("Askurious.de") == "Polltergeist.de"
        assert brand.name() == "Askurious"


def test_identifiers_with_the_name_inside_stay(app):
    _branded(app)
    with app.test_request_context("/", headers={"Host": "polltergeist.de"}):
        assert brand.apply("window.AskuriousMap; askurious-survey; Askurious's") == "window.AskuriousMap; askurious-survey; Polltergeist's"


# nginx for further brands and the keys: tests/platform/test_audit9b_q7_betrieb.py (one server block per name, B1)


def test_polltergeist_has_its_own_start_page_and_look(app):
    app.config.update(PUBLIC_BASE_URL="https://askurious.de", BRANDS="polltergeist.de=POLLtergeist", SECRET_KEY="x" * 32)
    c = app.test_client()
    html = c.get("/", headers={"Host": "polltergeist.de"}).data.decode()
    assert 'data-site="polltergeist"' in html and 'class="pg-ghost pg-ghost-hero"' in html and 'class="shots"' in html
    assert html.index("03-leinwand") < html.index("01-fragebogen")      # the big screen of a live poll first
    assert '<span class="wm-strong">POLL</span>tergeist' in html and not WORD.search(html)
    main = c.get("/", headers={"Host": "askurious.de"}).data.decode()
    assert "data-site" not in main and "pg-ghost" not in main and 'class="shots"' in main
    assert main.index("01-fragebogen") < main.index("03-leinwand")


def _luminance(hex_colour):
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_polltergeist_colours_are_readable():
    """Text 4.5:1 on its grounds, light and dark (as tests/items/test_display.py asks of the other styles)."""
    import os

    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(here, "app", "static", "app.css"), encoding="utf-8") as f:
        css = f.read()
    blocks = re.findall(r':root\[data-site="polltergeist"\][^{]*\{([^}]*)\}', css)
    themed = [b for b in blocks if "--fg" in b]
    assert len(themed) == 2                                                          # light and dark
    for block in themed:
        t = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))
        for fg, bg in (("fg", "bg"), ("fg", "surface"), ("muted", "bg"), ("muted", "surface"), ("accent", "surface"),
                       ("accent", "bg"), ("on-accent", "accent"), ("pg-strong", "bg")):
            assert _ratio(t[fg], t[bg]) >= 4.5, (fg, bg, round(_ratio(t[fg], t[bg]), 2))


def test_each_brand_has_its_own_icon(app):
    import os

    app.config.update(PUBLIC_BASE_URL="https://askurious.de", BRANDS="polltergeist.de=POLLtergeist", SECRET_KEY="x" * 32)
    c = app.test_client()
    static = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "app", "static")
    for host, name in (("polltergeist.de", "polltergeist-favicon.ico"), ("askurious.de", "favicon.ico")):
        with open(os.path.join(static, name), "rb") as f:
            assert c.get("/favicon.ico", headers={"Host": host}).data == f.read()
    html = c.get("/", headers={"Host": "polltergeist.de"}).data.decode()
    assert "img/icon/polltergeist-icon-512.webp" in html and "polltergeist-favicon.ico" in html
    assert "polltergeist-" not in c.get("/", headers={"Host": "askurious.de"}).data.decode()


def test_status_link_stays_on_the_brand(app):
    """The footer's "Status" leads to the brand's own status name (status.polltergeist.de, nginx and certbot know it)."""
    c = _branded(app)
    app.config["STATUS_URL"] = "https://status.askurious.de/"
    assert 'href="https://status.polltergeist.de/"' in c.get("/", headers={"Host": "polltergeist.de"}).data.decode()
    assert 'href="https://status.askurious.de/"' in c.get("/", headers={"Host": "askurious.de"}).data.decode()


def test_status_page_carries_the_brand(monkeypatch, tmp_path):
    from app import status_node

    monkeypatch.setenv("BRANDS", "polltergeist.de=POLLtergeist")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://askurious.de")
    monkeypatch.setenv("STATUS_CHECKS", "")
    monkeypatch.setenv("STATUS_DB", str(tmp_path / "status.db"))
    c = status_node.create().test_client()
    html = c.get("/", headers={"Host": "status.polltergeist.de"}).data.decode()
    assert 'data-site="polltergeist"' in html and 'href="https://polltergeist.de/"' in html
    assert 'data-default-mode="dark"' in html
    assert not WORD.search(html) and "askurious.de" not in html
    html = c.get("/", headers={"Host": "status.askurious.de"}).data.decode()
    assert "Askurious" in html and 'href="https://askurious.de/"' in html and "data-site" not in html


def test_polltergeist_is_dark_until_a_person_chooses(app, monkeypatch):
    # Ralf, 2026-10-10: POLLtergeist dark, Askurious light, as long as nobody has chosen
    import app as app_module
    from app.extensions import db
    from app.models import User

    app.config.update(PUBLIC_BASE_URL="https://askurious.de", BRANDS="polltergeist.de=POLLtergeist", SECRET_KEY="x" * 32)
    monkeypatch.setattr(app_module, "is_local_request", lambda: True)  # dev-login under the brand's name
    allow(app, "*")
    c, other = app.test_client(), app.test_client()
    head = {"Host": "polltergeist.de"}
    assert 'data-default-mode="dark"' in c.get("/", headers=head).data.decode().split(">", 2)[1]
    assert 'data-default-mode="light"' in c.get("/", headers={"Host": "askurious.de"}).data.decode().split(">", 2)[1]
    # a new account made under POLLtergeist starts dark, one made under Askurious light
    c.get("/auth/dev-login?orcid=0000-0002-0000-0007&name=X", headers=head)
    other.get("/auth/dev-login?orcid=0000-0002-0000-0008&name=Y", headers={"Host": "askurious.de"})
    with app.app_context():
        modes = {u.orcid: (u.display or {}).get("mode") for u in db.session.scalars(db.select(User))}
    assert modes["0000-0002-0000-0007"] == "dark" and modes["0000-0002-0000-0008"] == "light"
    # the person's own choice wins over the brand's standard
    c.post("/terms/", data={"accept": "1"}, headers=head)
    assert c.post("/account/display/mode", data={"mode": "light"}, headers=head).status_code == 204
    html = c.get("/account/", headers=head).data.decode().split(">", 2)[1]
    assert 'data-mode="light"' in html and 'data-default-mode="dark"' in html


def test_theme_init_falls_back_to_the_brand(app):
    js = app.test_client().get("/static/theme-init.js").data.decode()
    assert js.index('localStorage.getItem("theme")') < js.index('getAttribute("data-default-mode")') \
        < js.index("prefers-color-scheme")
