"""From the logo of a survey's theme: the icon of the browser tab (for logos that are about square) and
suggestions for the accent colour."""
import io
import os

from PIL import Image

from tests.conftest import add, fill_consent

from app import theme
from app.extensions import db
from app.models import Design, Survey

SQUARE_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#bdcd00"/>'
              b'<circle cx="32" cy="32" r="20" fill="#00549f"/><path d="M10 10h4" stroke="#fff"/><path d="M1 1h2" stroke="#000000"/></svg>')
WIDE_SVG = SQUARE_SVG.replace(b'viewBox="0 0 64 64"', b'viewBox="0 0 400 64"')


def png(width, height, parts):
    """An image made of vertical stripes: [(colour, share), ...]."""
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    x = 0
    for colour, share in parts:
        w = round(width * share)
        img.paste(Image.new("RGBA", (w, height), colour), (x, 0))
        x += w
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def upload(client, data, name="logo.png", variant="light"):
    return client.post("/surveys/1/design/logo", data={"file": (io.BytesIO(data), name), "variant": variant, "rights": "1"},
                       content_type="multipart/form-data", follow_redirects=True).data.decode()


def logo_of(client):
    with client.application.app_context():
        return (theme.clean(db.session.get(Survey, 1).theme) or {}).get("logo")


def stored(client, name):
    with client.application.app_context():
        return os.path.join(client.application.config["UPLOAD_FOLDER"], db.session.get(Survey, 1).uid, name)


def test_square_logo_becomes_the_tab_icon(client, survey):
    fill_consent(client)
    add(client, "info")
    assert 'rel="icon"' not in client.get("/surveys/1/editor/preview").data.decode()
    upload(client, png(300, 240, [((0, 84, 159, 255), 1.0)]))
    logo = logo_of(client)
    assert logo["icon"].endswith(".png") and logo["icon"] != logo["file"]
    with Image.open(stored(client, logo["icon"])) as icon:
        assert icon.size == (64, 64) and icon.mode == "RGBA"
        assert icon.getpixel((32, 32))[:3] == (0, 84, 159) and icon.getpixel((32, 1))[3] == 0    # centred, the rest transparent
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert '<link rel="icon" type="image/png" href="/media/' in page and logo["icon"] in page
    # a wide word mark: no icon (it would be a line); the old icon file goes with the old logo
    upload(client, png(600, 100, [((0, 84, 159, 255), 1.0)]))
    assert "icon" not in logo_of(client) and not os.path.exists(stored(client, logo["icon"]))
    assert 'rel="icon"' not in client.get("/surveys/1/editor/preview").data.decode()
    # an SVG logo is its own icon
    upload(client, SQUARE_SVG, "logo.svg")
    logo = logo_of(client)
    assert logo["icon"] == logo["file"]
    assert '<link rel="icon" type="image/svg+xml"' in client.get("/surveys/1/editor/preview").data.decode()
    upload(client, WIDE_SVG, "logo.svg")
    assert "icon" not in logo_of(client)
    # removing the logo removes the icon
    upload(client, png(200, 200, [((204, 7, 30, 255), 1.0)]))
    icon = logo_of(client)["icon"]
    client.post("/surveys/1/design/logo/remove", data={"variant": "light"})
    assert logo_of(client) is None and not os.path.exists(stored(client, icon))


def test_templates_with_logo_rules_get_no_icon(client, survey):
    upload(client, png(200, 200, [((0, 84, 159, 255), 1.0)]))
    from tests.helpers import rwth_design

    with client.application.app_context():  # the RWTH design as an organisation design: its logo stands at the right
        s = db.session.get(Survey, 1)
        s.theme = theme.clean({**rwth_design.look(), "logo": theme.of(s)["logo"]})
        db.session.commit()
        assert theme.of(s)["preset"] == "org" and theme.of(s)["logo"]["position"] == "right"
    assert logo_of(client)["icon"]                                              # kept with the logo …
    assert 'rel="icon"' not in client.get("/surveys/1/design/sample").data.decode()   # … but not used: the mark is never cut or shrunk


def test_a_design_carries_the_icon(client, survey):
    upload(client, png(200, 200, [((0, 84, 159, 255), 1.0)]))
    client.post("/surveys/1/design/save", data={"name": "Mit Symbol"})
    with client.application.app_context():
        design = db.session.scalar(db.select(Design))
        kept = design.data["logo"]
        folder = os.path.join(client.application.config["UPLOAD_FOLDER"], f"_design_{design.id}")
        assert kept["icon"] not in (logo_of(client)["icon"], kept["file"]) and os.path.isfile(os.path.join(folder, kept["icon"]))
        did = design.id
    client.post("/surveys/1/design/logo/remove", data={"variant": "light"})
    client.post("/surveys/1/design/apply", data={"design": did})
    logo = logo_of(client)
    assert logo["icon"] and os.path.isfile(stored(client, logo["icon"])) and os.path.isfile(stored(client, logo["file"]))


def test_colours_of_the_logo_are_suggested(client, survey):
    assert client.get("/surveys/1/design/logo/colours").get_json() == {"colours": []}       # no logo
    upload(client, png(400, 100, [((0, 84, 159, 255), 0.5), ((246, 168, 0, 255), 0.3), ((255, 255, 255, 255), 0.1),
                                   ((20, 20, 20, 255), 0.1)]))
    found = [c["colour"] for c in client.get("/surveys/1/design/logo/colours").get_json()["colours"]]
    assert found[:2] == ["#00549f", "#f6a800"] and len(found) == 2               # most frequent first; no white, no black
    assert "Farben aus dem Logo vorschlagen" in client.get("/surveys/1/design").data.decode()
    upload(client, SQUARE_SVG, "logo.svg")
    data = client.get("/surveys/1/design/logo/colours").get_json()["colours"]
    assert {c["colour"] for c in data} == {"#bdcd00", "#00549f"}                 # #fff and #000000 are paper and ink
    assert next(c for c in data if c["colour"] == "#00549f")["on_white"] == "7,6"
