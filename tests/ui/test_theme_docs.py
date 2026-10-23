"""The look of a survey is written down where the study is documented: README of a deposit, questionnaire
document, method section, and the snapshot taken before a questionnaire is edited again."""
import io
import json
import os

from PIL import Image

from tests.conftest import CONSENT_OK, add, fill_consent

from app import export, methods, snapshots, theme
from app.extensions import db
from app.models import Survey

BASE = {**theme.STANDARD, "font": "system", "font_scale": "100", "radius": "soft"}
PAPIER = theme.BUILTIN["papier"]["colors"]


def test_describe():
    assert theme.describe(None) == ('colour scheme "Askurious" (accent #2a5db0, background #ffffff, text #1d1d1b); font: system font; '
                                    'light background; no logo')
    own = theme.clean({"colors": {**theme.STANDARD, "accent": "#8a1c3b"}, "font": "serif", "font_scale": 112, "scheme": "auto",
                       "logo": {"file": "abcdefgh12345678.png", "w": 3, "h": 1}})
    text = theme.describe(own, "en", {"light": 7, "dark": 2})
    assert text.startswith("own colours (accent #8a1c3b,") and "text size 112 %" in text and "with a logo" in text
    assert "light or dark after the setting of the participant's device" in text and "shown: light 7, dark 2, changed on the way 0" in text
    from tests.helpers import rwth_design

    org = theme.describe(rwth_design.look(), "de")  # an organisation design by its name
    assert org.startswith("Design „RWTH Aachen“ (Akzent #00549f") and "Schrift: Helvetica / Arial" in org and "ohne Logo" in org


def test_readme_questionnaire_and_method_section_name_the_look(app, client, survey):
    fill_consent(client)
    add(client, "info")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert '- Look of the survey pages: colour scheme "Askurious"' in export.readme_md(s)
        assert 'Look of the pages (questionnaire version 1): colour scheme "Askurious"' in export.questionnaire_md(s)
        f = methods.facts(s)
        assert f["look_plain"] and "Look of the survey pages" not in methods.facts_text(f)   # the standard: not worth a sentence
    client.post("/surveys/1/design", data={**BASE, "preset": "papier", **PAPIER, "scheme": "auto"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        code = db.session.get(Survey, 1).code
    for scheme in ("dark", "light", "light"):
        p = app.test_client()
        p.get(f"/s/{code}")
        p.post(f"/s/{code}", data={**CONSENT_OK, "_scheme": scheme})
        p.post(f"/s/{code}", data={"nav": "next", "_scheme": scheme})
    with app.app_context():
        s = db.session.get(Survey, 1)
        readme = export.readme_md(s)
        assert '- Look of the survey pages: colour scheme "Paper" (accent #a4442a, background #f4eee2, text #2a241c)' in readme
        assert "light or dark after the setting of the participant's device" in readme and "shown: light 2, dark 1" in readme
        f = methods.facts(s)
        assert not f["look_plain"] and f["look"]["de"].startswith("Farbschema „Papier“") and "gezeigt: hell 2, dunkel 1" in f["look"]["de"]
        assert "Look of the survey pages: colour scheme \"Paper\"" in methods.facts_text(f)
    page = client.get("/surveys/1/methods").data.decode()
    assert "<dt>Darstellung</dt><dd>Farbschema „Papier“" in page


def test_snapshot_keeps_theme_and_logo(app, client, survey):
    """Since 2026-10-23 (E4) the look and its logo are in the snapshot's survey file, not beside it."""
    fill_consent(client)
    add(client, "info")
    out = io.BytesIO()
    Image.new("RGBA", (300, 80), (0, 84, 159, 255)).save(out, format="PNG")
    client.post("/surveys/1/design", data={**BASE, "accent": "#8a1c3b"})
    client.post("/surveys/1/design/logo", data={"file": (io.BytesIO(out.getvalue()), "l.png"), "variant": "light", "rights": "1"},
                content_type="multipart/form-data")
    with app.app_context():
        s = db.session.get(Survey, 1)
        name = snapshots.save(s)
        assert snapshots.extra_files(s, name) == []
        with open(snapshots.path(s, name, "survey.json"), encoding="utf-8") as f:
            kept = json.load(f)
        assert kept["theme"]["colors"]["accent"] == "#8a1c3b"
        assert kept["theme"]["logo"]["file"] in kept["files"] and kept["files"][kept["theme"]["logo"]["file"]]
        assert snapshots.path(s, name, "logo.exe") is None and snapshots.path(s, name, "../survey.json") is None
    assert client.get(f"/surveys/1/export/snapshot/{name}/survey.json").status_code == 200
    assert ">Befragungsdatei</a>" in client.get("/surveys/1/data").data.decode()


def test_snapshot_of_the_standard_look_has_no_theme_file(app, client, survey):
    fill_consent(client)
    add(client, "info")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert snapshots.extra_files(s, snapshots.save(s)) == []
