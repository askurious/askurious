"""Dark maps for a dark look (chosen per survey), and the switch in the preview bar that shows one side of a look
that follows the device."""
import json
import re

from tests.conftest import add, fill_consent

from app import theme
from app.extensions import db
from app.models import Survey

BASE = {**theme.STANDARD, "font": "system", "font_scale": "100", "radius": "soft"}
DARK = theme.BUILTIN["askurious-dark"]["colors"]


def _theme(client):
    with client.application.app_context():
        return theme.clean(db.session.get(Survey, 1).theme)


def _map_settings(html):
    return json.loads(re.search(r"data-map='([^']+)'|data-map=\"([^\"]+)\"", html).group(0).split("=", 1)[1][1:-1]
                      .replace("&#34;", '"').replace("&quot;", '"'))


def test_dark_maps_belong_to_a_dark_look():
    assert theme.clean({"maps": "dark"}) is None                                  # light look: maps stay as they are
    dark = theme.clean({"preset": "askurious-dark", "colors": DARK, "maps": "dark"})
    assert dark["maps"] == "dark"
    css = theme.css(dark)
    assert ".p-map-canvas .leaflet-tile-pane { filter: invert(1) hue-rotate(180deg)" in css
    assert "leaflet-tile-pane" not in theme.css(theme.clean({"preset": "askurious-dark", "colors": DARK}))   # the default: light tiles
    # a look that follows the device: dark tiles on the dark side only
    auto = theme.clean({"scheme": "auto", "maps": "dark"})
    css = theme.css(auto)
    assert css.startswith("@media (prefers-color-scheme: dark)") and css.count("leaflet-tile-pane") == 1
    assert "leaflet-tile-pane" not in theme.css(auto, "light") and "leaflet-tile-pane" in theme.css(auto, "dark")


def test_map_question_with_a_dark_tile_server(app, client, survey):
    add(client, "map_marker")
    client.post("/surveys/1/design", data={**BASE, **DARK, "maps": "dark"})
    assert _theme(client)["maps"] == "dark"
    frame = client.get("/surveys/1/editor/items/2/frame?lang=de").data.decode()
    assert "leaflet-tile-pane { filter:" in frame                                # the usual tiles, darkened in the browser
    settings = _map_settings(frame)
    assert "tiles_dark" not in settings and settings["tiles"].startswith("https://tile.openstreetmap.org/")
    # the operator has a server for dark maps: its tiles are loaded, nothing is filtered
    app.config["MAP_TILE_URL_DARK"] = "https://tiles.example.org/dark/{z}/{x}/{y}.png"
    frame = client.get("/surveys/1/editor/items/2/frame?lang=de").data.decode()
    settings = _map_settings(frame)
    assert settings["tiles_dark"] == "https://tiles.example.org/dark/{z}/{x}/{y}.png" and settings["dark"] == "always"
    assert "leaflet-tile-pane" not in frame
    client.post("/surveys/1/design", data={**BASE, "scheme": "auto", "maps": "dark"})
    assert _map_settings(client.get("/surveys/1/editor/items/2/frame?lang=de").data.decode())["dark"] == "auto"
    client.post("/surveys/1/design", data={**BASE, "scheme": "auto"})             # light maps: the dark server is not named
    assert "tiles_dark" not in _map_settings(client.get("/surveys/1/editor/items/2/frame?lang=de").data.decode())
    script = open("app/static/map_marker.js", encoding="utf-8").read()
    assert 'cfg.dark === "auto" && window.matchMedia' in script and "cfg.tiles_dark" in script


def test_preview_bar_shows_one_side(client, survey):
    fill_consent(client)
    add(client, "info")
    plain = client.get("/surveys/1/editor/preview").data.decode()
    assert "scheme=dark" not in plain                                             # a fixed look: no switch
    client.post("/surveys/1/design", data={**BASE, "scheme": "auto"})
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert 'href="?lang=de&scheme=dark"' in page and "@media (prefers-color-scheme: dark)" in page
    assert re.search(r'scheme=auto" aria-current="true"', page)
    dark = client.get("/surveys/1/editor/preview?lang=de&scheme=dark", follow_redirects=True).data.decode()
    assert "@media" not in dark and "--bg: #141517" in dark and re.search(r'scheme=dark" aria-current="true"', dark)
    again = client.get("/surveys/1/editor/preview").data.decode()                # remembered
    assert "--bg: #141517" in again and "@media" not in again
    light = client.get("/surveys/1/editor/preview?lang=de&scheme=light", follow_redirects=True).data.decode()
    assert "<style>body.participant" not in light and "@media" not in light
    back = client.get("/surveys/1/editor/preview?lang=de&scheme=auto", follow_redirects=True).data.decode()
    assert "@media (prefers-color-scheme: dark)" in back
    # participants are never affected by what a researcher chose in the preview
    client.post("/surveys/1/editor/preview?lang=de&scheme=dark")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with client.application.app_context():
        code = db.session.get(Survey, 1).code
    assert "@media (prefers-color-scheme: dark)" in client.get(f"/s/{code}").data.decode()
