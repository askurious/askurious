"""A theme that follows the device: light, and dark for participants whose device is set to dark. Two looks
within one survey are a condition of the data collection, so every participation carries which one was shown
(export column "scheme"), and the consent and the privacy page say so."""
import csv
import io

from tests.conftest import CONSENT_OK, add, fill_consent

from app import theme
from app.extensions import db
from app.models import Item, Response, Survey

BASE = {**theme.STANDARD, "font": "system", "font_scale": "100", "radius": "soft"}
RWTH = {"accent": "#00549f", "background": "#ffffff", "text": "#000000", "surface": "#ffffff"}


def _theme(client):
    with client.application.app_context():
        return theme.clean(db.session.get(Survey, 1).theme)


def test_auto_keeps_a_light_and_a_dark_palette():
    t = theme.clean({"scheme": "auto"})
    assert t["scheme"] == "auto" and t["colors_dark"] == theme.BUILTIN["askurious-dark"]["colors"]
    assert theme.dark_side(t)["preset"] == "askurious-dark" and theme.tokens(theme.dark_side(t))["--on-accent"] == "#0e1a2e"
    css = theme.css(t)
    # the light side is the standard look and sets nothing; the dark side lies in a media query
    assert css.startswith("@media (prefers-color-scheme: dark) { body.participant, .participant-frame { --bg: #141517;")
    assert "color-scheme: dark" in css and css.count("--bg:") == 1
    assert theme.css(t, "light") == "" and theme.css(t, "dark").startswith("body.participant, .participant-frame { --bg: #141517;")
    # an own light design: both sides
    own = theme.clean({"colors": {**theme.STANDARD, "accent": "#8a1c3b"}, "scheme": "auto"})
    assert own["colors_dark"] == theme.suggest_dark(own["colors"]) and theme.lightness(own["colors_dark"]["background"]) < 0.5
    assert all(c["ok"] for c in theme.checks(theme.dark_side(own))), theme.checks(theme.dark_side(own))
    css = theme.css(own)
    assert css.startswith("body.participant, .participant-frame { --bg: #ffffff;") and "@media (prefers-color-scheme: dark)" in css
    assert css.count("--bg:") == 2
    # the RWTH pair
    from tests.helpers import rwth_design

    rwth = rwth_design.pair()  # an organisation design never follows the device: its light side stays light (Ralf, 2026-10-23)
    assert rwth["scheme"] == "fixed" and not theme.is_dark(rwth) and "prefers-color-scheme" not in theme.css(rwth)


def test_auto_needs_a_light_look_and_a_dark_second_palette():
    dark = theme.clean({"preset": "askurious-dark", "scheme": "auto"})
    assert dark["scheme"] == "fixed" and "colors_dark" not in dark          # a dark look stays as it is
    wrong = theme.clean({"scheme": "auto", "colors_dark": {"accent": "#2a5db0", "background": "#fefefe", "text": "#111111", "surface": "#ffffff"}})
    assert wrong is None                                                     # a light "dark" palette: no second look
    assert theme.clean({"scheme": "sometimes"}) is None
    unreadable = theme.clean({"colors": {**theme.STANDARD, "accent": "#2a5db1"}, "scheme": "auto",
                              "colors_dark": {"accent": "#333333", "background": "#000000", "text": "#444444", "surface": "#000000"}})
    found = theme.problems(unreadable)
    assert found and all("(dunkle Darstellung)" in message for _, message in found)


def test_shown_look_is_stored_exported_and_disclosed(app, client, survey):
    fill_consent(client)
    add(client, "info")
    add(client, "page_break")
    add(client, "info")
    client.post("/surveys/1/design", data={**BASE, "scheme": "auto"})
    assert _theme(client)["scheme"] == "auto"
    # the standard text of the consent follows; the privacy page says it as well
    client.post("/surveys/1/editor/items/1", data={"code": "consent", "body": "", "researchers": "0000-0000-0000-001X", "draft": "template"})
    with app.app_context():
        assert "hell oder dunkel" in db.session.get(Item, 1).texts["de"]["body"]
    fill_consent(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        code = db.session.get(Survey, 1).code
    first, second, third = app.test_client(), app.test_client(), app.test_client()
    page = first.get(f"/s/{code}").data.decode()
    assert "data-scheme" in page and "@media (prefers-color-scheme: dark)" in page
    assert "hell oder dunkel" in first.get(f"/s/{code}/privacy?lang=de").data.decode()
    second.get(f"/s/{code}"), third.get(f"/s/{code}")   # a participation begins with the first page shown
    first.post(f"/s/{code}", data={**CONSENT_OK, "_scheme": "dark"})
    first.post(f"/s/{code}", data={"nav": "next", "_scheme": "dark"})
    second.post(f"/s/{code}", data={**CONSENT_OK, "_scheme": "light"})
    second.post(f"/s/{code}", data={"nav": "next", "_scheme": "dark"})         # switched on the way
    second.post(f"/s/{code}", data={"nav": "next", "_scheme": "light"})        # stays "mixed"
    third.post(f"/s/{code}", data={**CONSENT_OK, "_scheme": "<script>"})       # without JavaScript, or nonsense: nothing
    with app.app_context():
        seen = [(r.paradata or {}).get("_scheme") for r in db.session.scalars(db.select(Response).filter_by(is_preview=False).order_by(Response.id))]
    assert seen == ["dark", "mixed", None]
    text = client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert sorted(r["scheme"] for r in rows) == ["", "dark", "mixed"]
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "scheme" in book and "light | dark | mixed" in book
    assert client.get("/surveys/1/export/data.sav?all=1").status_code == 200       # SPSS takes the text column as well
    assert client.get("/surveys/1/export/spss.zip?all=1").status_code == 200
    from app import export

    assert not export.is_public_column("scheme")


def test_a_fixed_look_records_nothing(app, client, survey):
    fill_consent(client)
    add(client, "info")
    client.post("/surveys/1/design", data={**BASE, "accent": "#8a1c3b"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        code = db.session.get(Survey, 1).code
    p = app.test_client()
    assert "data-scheme" not in p.get(f"/s/{code}").data.decode()
    p.post(f"/s/{code}", data={**CONSENT_OK, "_scheme": "dark"})
    with app.app_context():
        assert db.session.scalar(db.select(Response).filter_by(is_preview=False)).paradata == {"_assignment_network_starts": 0}
    assert "scheme" not in client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0].split(",")
    assert "hell oder dunkel" not in p.get(f"/s/{code}/privacy?lang=de").data.decode()


def test_design_check_reminds_an_edited_consent(app, client, survey):
    from app import design

    fill_consent(client)   # an own text ("Text") that does not name the look
    add(client, "info")

    def mine():
        with app.app_context():
            return [f.message for f in design.check(db.session.get(Survey, 1)) if "Darstellung" in f.message]

    assert mine() == []
    client.post("/surveys/1/design", data={**BASE, "scheme": "auto"})
    assert len(mine()) == 1 and "Einwilligung in Deutsch (Sie)" in mine()[0] and "hell oder dunkel" in mine()[0]  # EN has no text yet


def test_design_page_and_preview_of_one_side(client, survey):
    page = client.get("/surveys/1/design").data.decode()
    assert 'name="scheme" value="auto"' in page and 'name="dark_background" value="#141517"' in page
    assert 'data-dark_accent="#7ea6e6"' in page                               # the partner of "Askurious"
    q = "accent=%232a5db0&background=%23ffffff&text=%231d1d1b&surface=%23ffffff&scheme=auto"
    both = client.get(f"/surveys/1/design/sample?{q}").data.decode()
    assert "@media (prefers-color-scheme: dark)" in both
    dark = client.get(f"/surveys/1/design/sample?{q}&show=dark").data.decode()
    assert "@media" not in dark and "--bg: #141517" in dark and "color-scheme: dark" in dark
    light = client.get(f"/surveys/1/design/sample?{q}&show=light").data.decode()
    assert "<style>body.participant" not in light and "@media" not in light
    check = client.get(f"/surveys/1/design/check?{q}").get_json()
    assert check["auto"] is True and len(check["checks"]) == 30 and check["checks"][15]["label"].startswith("Dunkel: ")
    bad = client.get(f"/surveys/1/design/check?{q}&dark_background=%23000000&dark_text=%23333333&dark_accent=%237ea6e6&dark_surface=%23000000").get_json()
    weak = [c for c in bad["checks"] if not c["ok"]]
    assert weak and all(c["label"].startswith("Dunkel: ") for c in weak) and "dark_text" in weak[0]["fix"]


def test_logo_follows_the_device(client, survey):
    from PIL import Image

    def png(colour):
        out = io.BytesIO()
        Image.new("RGBA", (300, 80), colour).save(out, format="PNG")
        return out.getvalue()

    for variant, colour in (("light", (0, 84, 159, 255)), ("dark", (255, 255, 255, 255))):
        client.post("/surveys/1/design/logo", data={"file": (io.BytesIO(png(colour)), "l.png"), "variant": variant, "rights": "1"},
                    content_type="multipart/form-data")
    plain = client.get("/surveys/1/editor/preview").data.decode()
    assert "<picture>" not in plain
    client.post("/surveys/1/design", data={**BASE, "scheme": "auto"})
    logo = _theme(client)["logo"]
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert '<picture><source media="(prefers-color-scheme: dark)" srcset="/media/' in page
    assert logo["file_dark"] in page.split("<source")[1].split(">")[0] and logo["file"] in page.split("<img")[1].split(">")[0]
