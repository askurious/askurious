"""The colours of the survey pages are tokens (app/static/participant.css): a theme changes the tokens, so a
colour written into a rule would stay as it is under every theme. The tools of the preview (preview bar, device
frame, comments) are no part of the survey and keep fixed colours."""
import os
import re

CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "app", "static", "participant.css")
TOOLS = (".p-preview", ".p-device", ".device", ".phone-card", ".p-jump", ".p-comments", ".placeholder", ".p-validate",
         "html.in-device")
COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\((?!var\(--h\) \d+% var\(--face-)[^;]*")
# shadows and outlines in translucent black work on every background; smileys take their hue from the scale
# point and their lightness from tokens (the only hsl() allowed)
ALLOWED = re.compile(r"rgba\(0, ?0, ?0, ?0?\.\d+\)")


def _rules():
    """(selector, declarations) of every rule; rules inside @media count like the others."""
    with open(CSS, encoding="utf-8") as f:
        css = re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)
    css = re.sub(r"@(media|keyframes)[^{]*\{", "", css)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def test_survey_pages_use_tokens_only():
    rules = _rules()
    assert len(rules) > 400
    found = []
    for selector, body in rules:
        selector = selector.strip()
        if selector.startswith("body.participant, .participant-frame") and "--bg:" in body:
            continue  # the token block itself
        if all(part.strip().startswith(TOOLS) for part in selector.split(",")):
            continue
        # a face takes its colours from the theme, else from the hue of its scale point (lightness from tokens)
        body = re.sub(r"var\(--face-[a-z]+, hsl\(var\(--h\) \d+% var\(--face-[a-z]+-l\)\)\)", "", body)
        for colour in COLOUR.findall(body):
            if not ALLOWED.fullmatch(colour):
                found.append(f"{selector}: {colour}")
    assert not found, "colours outside the tokens:\n" + "\n".join(found)


def test_every_token_in_use_is_defined():
    with open(CSS, encoding="utf-8") as f:
        css = f.read()
    block = css[css.index("body.participant, .participant-frame {"):]
    defined = set(re.findall(r"(--[a-z0-9-]+):", block[:block.index("}")]))
    # set per element by the templates or by participant.js
    local = {"--points", "--n", "--k", "--pct", "--lo", "--hi", "--h", "--alts", "--mark", "--av", "--text",
             "--hl1", "--hl2", "--hl3", "--sbs-cols", "--q-cols", "--cap"}  # --sbs-cols: column widths of a side-by-side matrix; --q-cols, --cap: columns and places of a Q sort grid; marker colours of a text highlighting, chosen by the researchers
    # set only by themes with a fixed colour world (app/theme.py); without them the hue of the scale point is used
    local |= {"--face-bg", "--face-on", "--face-edge", "--face-ink"}
    # the second pixel of edge of a chosen answer, only where areas cannot differ (RWTH dunkel; UI/UX audit 2026-10-16, 73)
    local |= {"--chosen-edge"}
    used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    assert used - defined - local == set()


def test_text_size_follows_the_browser_and_the_footer_is_not_fixed():
    """UI audit T34 and T32."""
    with open(CSS, encoding="utf-8") as f:
        css = f.read()
    assert "font: 1.125rem/1.55 var(--font)" in css and "font: 18px" not in css
    footer = re.search(r"\n\.p-footer \{([^}]*)\}", css).group(1)
    assert "position: fixed" not in footer
    # the page fills the window so that the footer sits at its bottom, but not in the element preview of the
    # editor: that frame takes the height of its content and would grow without end
    assert "body.participant:not(.is-item-preview) { display: flex; flex-direction: column; min-height: 100vh;" in css


def test_no_text_of_a_survey_page_is_smaller_than_0_8_rem():
    """Scale labels, hints and the like were as small as 0.7rem (11 px). The preview tools are not meant."""
    small = []
    for selector, body in _rules():
        selector = selector.strip()
        if all(part.strip().startswith(TOOLS) for part in selector.split(",")):
            continue
        for size in re.findall(r"font-size: ([0-9.]+)rem", body):
            if float(size) < 0.8:
                small.append(f"{selector}: {size}rem")
    assert not small, small


def test_text_size_of_a_theme_scales_every_text():
    """The sizes are in rem, so the theme sets the size of the root: labels and hints grow with the main text."""
    from app import theme

    css = theme.css(theme.clean({"font_scale": 125}))
    assert css.endswith("html { font-size: 125%; }") and "body.participant { font-size" not in css
    assert "html {" not in theme.css(theme.clean({"font": "serif"}))
