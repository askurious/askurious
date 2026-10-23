"""The font that comes with Askurious (Atkinson Hyperlegible Next): served from this server, with its licence beside
the files, offered as a font of a survey's theme."""
import os
import re

from app import theme

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "app", "static")
FOLDER = os.path.join(STATIC, "fonts", "atkinson-hyperlegible-next")


def test_font_files_lie_next_to_their_licence():
    with open(os.path.join(STATIC, "participant.css"), encoding="utf-8") as f:
        css = f.read()
    urls = re.findall(r'@font-face \{[^}]*src: url\("([^"]+)"\) format\("woff2"\)', css)
    assert len(urls) == 2 and all(u.startswith("fonts/atkinson-hyperlegible-next/") for u in urls)
    assert not re.search(r"url\(\s*[\"']?(https?:)?//", css)                 # nothing is loaded from another server
    for url in urls:
        with open(os.path.join(STATIC, url), "rb") as f:
            assert f.read(4) == b"wOF2", url                                  # a WOFF2 file
    # the licence goes with the font: the full text and the copyright notice, in the same folder
    with open(os.path.join(FOLDER, "OFL.txt"), encoding="utf-8") as f:
        licence = f.read()
    assert "SIL OPEN FONT LICENSE Version 1.1" in licence and "PERMISSION & CONDITIONS" in licence
    assert licence.startswith("Copyright 2020-2024 The Atkinson Hyperlegible Next Project Authors")
    assert "Atkinson Hyperlegible Next Project Authors" in css and "OFL.txt" in css   # the stylesheet points to it
    with open(os.path.join(STATIC, "fonts", "README.md"), encoding="utf-8") as f:
        assert "OFL.txt" in f.read()
    # every font folder has a licence file (for fonts added later)
    for name in os.listdir(os.path.join(STATIC, "fonts")):
        folder = os.path.join(STATIC, "fonts", name)
        if os.path.isdir(folder):
            assert any(n.upper().startswith(("OFL", "LICENSE", "LICENCE")) for n in os.listdir(folder)), name


def test_font_and_licence_are_served(client):
    font = client.get("/static/fonts/atkinson-hyperlegible-next/AtkinsonHyperlegibleNext-wght.woff2")
    assert font.status_code == 200 and font.data[:4] == b"wOF2"
    licence = client.get("/static/fonts/atkinson-hyperlegible-next/OFL.txt")
    assert licence.status_code == 200 and b"SIL OPEN FONT LICENSE" in licence.data


def test_font_is_offered_for_a_theme(client, survey):
    assert 'value="atkinson"' in client.get("/surveys/1/design").data.decode()
    client.post("/surveys/1/design", data={**theme.STANDARD, "font": "atkinson", "font_scale": "100", "radius": "soft"})
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert '--font: "Atkinson Hyperlegible Next", system-ui' in page
    with client.application.app_context():
        from app.extensions import db
        from app.models import Survey

        look = theme.of(db.session.get(Survey, 1))
    assert look["font"] == "atkinson" and look["preset"] == "askurious"       # the standard colours, another font
    assert "font: Atkinson Hyperlegible Next" in theme.describe(look)
    # the templates with a fixed font keep theirs
    from tests.helpers import rwth_design

    rwth = theme.clean({**rwth_design.look(), "font": "atkinson"})
    assert rwth["font"] == "grotesk"
