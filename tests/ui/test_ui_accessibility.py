"""Accessibility and dark mode of the researchers' pages (UI/UX audit of 2026-10-03, package B)."""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "app" / "static" / "app.css"


def test_the_page_language_is_german_whatever_survey_language_is_edited(client, survey):
    for lang in ("en", "de"):
        html = client.get(f"/surveys/1/editor/?lang={lang}").data.decode()
        assert '<html lang="de"' in html[:80]  # the account's look may follow as data attributes


def test_admin_tabs_name_the_current_page(client):
    html = client.get("/admin/people").data.decode()
    assert 'aria-label="Bereiche der Administration"' in html
    assert re.search(r'aria-current="page"[^>]*>Personen<', html)


def test_translation_filters_say_which_is_pressed(client, survey):
    html = client.get("/surveys/1/translation/").data.decode()
    assert 'aria-pressed="true" data-tr-filter="all"' in html and 'aria-pressed="false" data-tr-filter="missing"' in html


def test_link_parameters_have_names(client, survey):
    html = client.get("/surveys/1/field").data.decode()
    for name in ("Name des Parameters", "Umgang mit dem Parameter", "Erlaubte Werte, kommagetrennt"):
        assert f'aria-label="{name}"' in html


def test_new_areas_take_their_colours_from_the_theme():
    """Colours that were fixed to the light theme (flow slots, comments, unsaved marker, translation diff, focus):
    they come from tokens that the dark theme redefines."""
    css = CSS.read_text(encoding="utf-8")
    for fixed in ("#fff3c4", "#6b5200", "#e8d27a", "#8a6a00", "#8a6500", "rgba(41, 87, 164, 0.15)", "var(--hover)"):
        assert fixed not in css, fixed
    dark = css[css.index(':root[data-theme="dark"] {'):]
    dark = dark[:dark.index("}")]
    assert "--warn-border" in dark and "--warn-bar" in dark


def test_focus_is_an_opaque_line_and_icon_buttons_have_names(client):
    """UI audit 2026-10-04, package G: the keyboard focus is never just a faded shadow (R66, R67), a skip link
    leads to the content (R76), and every icon button has a name of its own (R74)."""
    import glob
    import re

    for path in ("app/static/app.css", "app/static/participant.css"):
        css = open(path, encoding="utf8").read()
        rules = re.findall(r"([^{}]*)\{([^}]*outline: none[^}]*)\}", css)
        assert [sel.strip() for sel, _ in rules] in ([], ["main:focus"]), path
        assert "box-shadow: 0 0 0 3px var(--focus-ring)" not in css, path
        assert "@media (forced-colors: active)" in css, path
    for path in glob.glob("app/templates/**/*.html", recursive=True):
        for tag in re.findall(r'<(?:button|a)\b[^>]*class="[^"]*\bicon-btn\b[^"]*"[^>]*>', open(path, encoding="utf8").read()):
            assert "aria-label" in tag, (path, tag)
    html = client.get("/dashboard/").data.decode()
    assert 'class="skip-link" href="#main"' in html and 'id="main"' in html
