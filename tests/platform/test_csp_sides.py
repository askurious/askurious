"""A stricter CSP on the participants' side (bucket list 2026-10-18; participant address E8, decisions.md): their pages
load images only from this site and the tile servers; since the audit 2026-10-18 (INJ-1) the researchers' pages as
well, and their forms go only to this site, ORCID and the participants' address."""
from app import _tile_source
from app.extensions import db
from app.models import Survey
from tests.conftest import CONSENT_OK


def _directive(response, name):
    policy = response.headers["Content-Security-Policy"].split("; ")
    return next(d for d in policy if d.split(" ")[0] == name)


def test_participant_pages_load_images_only_from_here_and_the_tiles(app, client, survey):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    page = p.get(f"/s/{uid}")
    img = _directive(page, "img-src").split(" ")
    assert img[:4] == ["img-src", "'self'", "data:", "blob:"] and "https:" not in img
    assert "https://tile.openstreetmap.org" in img  # the map questions' tiles
    assert _directive(p.post(f"/s/{uid}", data=CONSENT_OK, follow_redirects=True), "img-src") == " ".join(img)
    # the rest as everywhere: scripts from here, forms onward over https, framed only by this site
    assert _directive(page, "script-src") == "script-src 'self'"
    assert _directive(page, "form-action") == "form-action 'self' https:"
    assert _directive(page, "frame-ancestors") == "frame-ancestors 'self'"


def test_researcher_pages_load_images_and_send_forms_only_where_needed(app, client, survey):
    """Audit 2026-10-18, INJ-1: "img-src … https:" and "form-action 'self' https:" let markup that got into a
    researcher's page send what it shows to any site (an image address, a form without a script)."""
    app.config.update(STATUS_URL="https://status.example.org", PARTICIPANT_BASE_URL="https://ask.example.org")
    for page in (client.get("/surveys/1/editor/"), client.get("/dashboard/"), app.test_client().get("/")):
        img = _directive(page, "img-src").split(" ")
        assert img[:4] == ["img-src", "'self'", "data:", "blob:"] and "https:" not in img
        assert "https://tile.openstreetmap.org" in img and "https://status.example.org" in img  # tiles, status dot
        forms = _directive(page, "form-action").split(" ")
        assert forms[:2] == ["form-action", "'self'"] and "https:" not in forms
        assert "https://orcid.org" in forms and "https://ask.example.org" in forms  # sign-in, a test run
        assert _directive(page, "style-src") == "style-src 'self' 'unsafe-inline'"  # inline styles: see app/__init__.py


def test_tile_sources():
    assert _tile_source("https://tile.openstreetmap.org/{z}/{x}/{y}.png") == "https://tile.openstreetmap.org"
    assert _tile_source("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png") == "https://*.basemaps.cartocdn.com"
    assert _tile_source("") == "" and _tile_source("/tiles/{z}/{x}/{y}.png") == ""
