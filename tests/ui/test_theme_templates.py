"""The open templates with a look of their own (Papier, Salbei, Nachtblau, Schwarz-Gelb, Retro)."""
import pytest

from app import theme
from app.extensions import db
from app.models import Survey

LOOKS = [key for key, t in theme.BUILTIN.items() if t.get("look")]


def test_all_are_readable():
    assert set(LOOKS) == {"papier", "salbei", "nachtblau", "schwarz-gelb", "retro", "retro-dark"}
    for key in LOOKS:
        shown = theme.clean({"preset": key, "colors": theme.BUILTIN[key]["colors"], **theme.BUILTIN[key]["look"]})
        assert shown["preset"] == key and all(c["ok"] for c in theme.checks(shown)), key


@pytest.mark.parametrize("key", LOOKS)
def test_choosing_one_sets_font_corners_and_size(app, client, survey, key):
    client.post("/surveys/1/design/choose", data={"preset": key})
    with app.app_context():
        look = theme.of(db.session.get(Survey, 1))
    assert look["preset"] == key and look["colors"] == theme.BUILTIN[key]["colors"]
    assert {k: look[k] for k in ("font", "radius", "font_scale")} == theme.BUILTIN[key]["look"]
    page = client.get("/surveys/1/design").data.decode()
    assert f'data-preset="{key}"' in page and "data-look-font=" in page


def test_every_font_can_be_described_in_both_languages():
    """The questionnaire as Markdown (README of a deposit) failed for the retro looks: no English name for "mono"."""
    from app import theme

    for font in theme.FONTS:
        look = dict(theme.standard(), font=font)
        assert theme.describe(look, "en") and theme.describe(look, "de")


def test_settings_apply_a_template_on_the_click_with_undo(app, client, survey):
    """V86: no "Vorlage übernehmen" any more; the choice saves by itself, says so and can be taken back."""
    page = client.get("/surveys/1").data.decode()
    form = page.split('class="theme-choice"')[1].split("</form>")[0]
    assert "data-autosave-form" in form and "Vorlage übernehmen" not in form and 'name="base"' in form
    with app.app_context():
        before = theme.of(db.session.get(Survey, 1))["preset"]
    key = next(k for k in LOOKS if k != before)
    d = client.post("/surveys/1/design/choose", data={"preset": key, "autosave": "1"},
                    headers={"X-Requested-With": "fetch"}).get_json()
    assert d["status"] == "saved" and any("Die bisherigen Farben wurden ersetzt" in n for n in d["notes"])
    assert d["undo"].startswith("Erscheinungsbild")
    with app.app_context():
        assert theme.of(db.session.get(Survey, 1))["preset"] == key
    client.post("/surveys/1/editor/undo")
    with app.app_context():
        assert theme.of(db.session.get(Survey, 1))["preset"] == before
