"""Themes of the participant pages (app/theme.py): a few colours from the researcher, everything else derived;
only validated values reach the page; the standard look sets nothing; locked outside the draft."""
import itertools
import re

from tests.conftest import add, fill_consent

from app import theme
from app.extensions import db
from app.models import Survey, SurveyTheme

RWTH = {"accent": "#00549f", "background": "#ffffff", "text": "#000000", "surface": "#ffffff"}


def _post(client, **values):
    data = {**theme.STANDARD, "font": "system", "font_scale": "100", "radius": "soft", **values}
    return client.post("/surveys/1/design", data=data)


def test_standard_tokens_are_the_ones_in_the_stylesheet():
    tokens = theme.standard_tokens()
    assert tokens["--accent"] == theme.STANDARD["accent"] and tokens["--bg"] == theme.STANDARD["background"]
    assert tokens["--fg"] == theme.STANDARD["text"] and tokens["--surface"] == theme.STANDARD["surface"]
    # a derived theme sets every token of the stylesheet, so no value of the standard look shines through
    derived = theme.tokens(theme.clean({"colors": RWTH}))
    assert set(derived) == set(tokens)


def test_contrast_and_readable():
    assert round(theme.contrast("#000000", "#ffffff"), 1) == 21.0
    assert round(theme.contrast("#00549f", "#ffffff"), 1) == 7.6     # RWTH blue on white
    assert round(theme.contrast("#2a5db0", "#ffffff"), 1) == 6.4     # the standard accent
    grey = theme.readable("#9c9e9f", "#ffffff")
    assert theme.contrast(grey, "#ffffff") >= 4.5 and theme.HEX.match(grey)
    assert theme.readable("#000000", "#ffffff") == "#000000"         # already readable: unchanged
    assert theme.contrast(theme.readable("#00549f", "#000000"), "#000000") >= 4.5


def test_derived_tones_are_readable_on_any_background():
    """Whatever colours are chosen: hint, error, nudge and ok texts and the text on buttons are derived so that they
    pass. What the researcher chose directly (text, accent) is reported, not changed."""
    steps = ("#000000", "#141517", "#3a3a3a", "#808080", "#c8c8c8", "#f4f0e6", "#ffffff", "#00549f", "#ffed00", "#57ab27")
    for bg, accent in itertools.product(steps, steps):
        text = "#ffffff" if theme.lightness(bg) < 0.6 else "#000000"
        t = theme.tokens(theme.clean({"colors": {"accent": accent, "background": bg, "text": text, "surface": bg}})
                         or theme.standard())
        if theme.contrast(text, bg) >= 4.5:
            for key in ("--muted", "--error", "--nudge", "--ok"):
                assert theme.contrast(t[key], bg) >= 4.5, (bg, accent, key, t[key])
        assert theme.contrast(t["--on-accent"], t["--accent"]) >= 4.5, (accent, t["--on-accent"])
        assert all(theme.HEX.match(v) or v.startswith(("rgba(", '"', "ui-", "system-ui")) or v.endswith(("px", "%"))
                   for v in t.values()), t


def test_only_validated_values_are_kept():
    bad = theme.clean({"colors": {"accent": "red; } body { display: none", "background": "#FFF", "text": "#12345g",
                                   "surface": "url(https://example.org/x)"},
                       "font": "Comic Sans", "radius": "9999px", "font_scale": "300", "tokens": {"--bg": "#000000"},
                       "preset": "</style><script>alert(1)</script>"})
    assert bad is None                                                # nothing valid in it: the standard look
    mixed = theme.clean({"colors": {**RWTH, "accent": "#00549F", "text": "javascript:alert(1)"}, "font": "grotesk"})
    assert mixed["colors"] == {**RWTH, "text": theme.STANDARD["text"]} and mixed["font"] == "grotesk"
    assert mixed["preset"] == "custom" and set(mixed) == {"v", "preset", "colors", "font", "font_scale", "radius", "scheme"}
    css = theme.css(mixed)
    assert "<" not in css and "url(" not in css and "javascript" not in css and "@import" not in css
    assert re.fullmatch(r"[-#\w\s:;,.%(){}\"]+", css)


def test_builtin_themes():
    assert theme.clean({"preset": "askurious"}) is None and theme.css(None) == ""
    dark = theme.clean({"preset": "askurious-dark"})
    assert dark["preset"] == "askurious-dark" and theme.is_dark(dark) and "color-scheme: dark" in theme.css(dark)
    assert theme.tokens(dark)["--on-accent"] == "#0e1a2e"            # fixed by the built-in theme
    assert all(c["ok"] for c in theme.checks(dark)) and theme.problems(dark) == []
    # the standard colours with another font: the written tokens, not derived ones
    serif = theme.clean({"font": "serif"})
    assert serif["preset"] == "askurious" and theme.tokens(serif)["--border"] == theme.standard_tokens()["--border"]
    assert "Palatino" in theme.css(serif)


def test_pages_carry_the_theme_and_the_standard_look_carries_nothing(client, survey):
    fill_consent(client)
    add(client, "single_choice")
    plain = client.get("/surveys/1/editor/preview").data.decode()
    assert "<style>body.participant" not in plain
    assert _post(client, **RWTH, font="grotesk", radius="square").status_code == 302
    with client.application.app_context():
        row = db.session.get(SurveyTheme, 1)
        assert row.data["colors"] == RWTH and row.data["preset"] == "custom"
    page = client.get("/surveys/1/editor/preview").data.decode()
    assert "<style>body.participant, .participant-frame { --bg: #ffffff;" in page
    assert "--accent: #00549f" in page and "--radius: 2px" in page and "Helvetica Neue" in page
    frame = client.get("/surveys/1/editor/items/2/frame?lang=de").data.decode()
    assert "--accent: #00549f" in frame                              # the element preview of the editor as well
    # the researcher pages are not themed
    assert "--accent: #00549f" not in client.get("/surveys/1/editor/").data.decode()
    settings = client.get("/surveys/1").data.decode()
    assert "Erscheinungsbild" in settings and "Eigenes Design" in settings and "background: #00549f" in settings
    # back to the standard: the row is gone
    assert client.post("/surveys/1/design", data={"reset": "1"}).status_code == 302
    with client.application.app_context():
        assert db.session.get(SurveyTheme, 1) is None
    assert "<style>body.participant" not in client.get("/surveys/1/editor/preview").data.decode()


def test_design_page_sample_and_contrast(client, survey):
    page = client.get("/surveys/1/design").data.decode()
    assert "data-design-form" in page and 'data-preset="askurious-dark"' in page and 'value="#2a5db0"' in page
    sample = client.get("/surveys/1/design/sample?accent=%2300549f&background=%23000000&text=%23ffffff&surface=%23000000").data.decode()
    assert "--bg: #000000" in sample and "color-scheme: dark" in sample and "Beispielseite" in sample
    assert "<style>body.participant" not in client.get("/surveys/1/design/sample").data.decode()  # saved: the standard
    weak = client.get("/surveys/1/design/check?accent=%23ffed00&background=%23ffffff&text=%239c9e9f&surface=%23ffffff").get_json()
    by_key = {c["key"]: c for c in weak["checks"]}
    assert not by_key["text"]["ok"] and by_key["text"]["severity"] == "error" and by_key["text"]["ratio"] == "2,7"
    assert theme.contrast(by_key["text"]["fix"]["text"], "#ffffff") >= 4.5
    assert not by_key["accent-part"]["ok"] and by_key["accent-part"]["severity"] == "warning"
    assert by_key["button"]["ok"] and by_key["error"]["ok"]           # chosen automatically
    assert weak["name"] == "Eigenes Design" and weak["dark"] is False


def test_unreadable_text_blocks_the_start(client, survey):
    from app import design

    fill_consent(client)
    add(client, "single_choice")
    _post(client, text="#9c9e9f")
    with client.application.app_context():
        findings = design.check(db.session.get(Survey, 1))
        mine = [f for f in findings if f.message.startswith("Erscheinungsbild")]
        assert mine and all(f.severity == "error" for f in mine) and "2,7 : 1" in mine[0].message
        assert design.blocking(findings)
    _post(client, **RWTH)
    with client.application.app_context():
        assert not [f for f in design.check(db.session.get(Survey, 1)) if f.message.startswith("Erscheinungsbild")]


def test_theme_is_locked_outside_the_draft_and_needs_the_editor_role(client, survey):
    _post(client, **RWTH)
    with client.application.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        db.session.commit()
    r = _post(client, accent="#cc071e")
    assert r.status_code == 302
    with client.application.app_context():
        assert db.session.get(SurveyTheme, 1).data["colors"]["accent"] == "#00549f"
    page = client.get("/surveys/1/design").data.decode()
    assert "nur im Entwurf änderbar" in page and "<fieldset class=\"bare\" disabled>" in page
    with client.application.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.DRAFT
        s.members[0].role = "viewer"
        db.session.commit()
    _post(client, accent="#cc071e")
    with client.application.app_context():
        assert db.session.get(SurveyTheme, 1).data["colors"]["accent"] == "#00549f"
        s = db.session.get(Survey, 1)
        s.members[0].role = "editor"
        db.session.commit()
    _post(client, accent="#cc071e")
    with client.application.app_context():
        assert db.session.get(SurveyTheme, 1).data["colors"]["accent"] == "#cc071e"


def test_deleting_a_survey_removes_its_theme(client, survey):
    from app import lifecycle

    _post(client, **RWTH)
    with client.application.app_context():
        lifecycle.delete(db.session.get(Survey, 1))
        db.session.commit()
        assert db.session.get(SurveyTheme, 1) is None
