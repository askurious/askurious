"""The RWTH design as organisation design files (tests/data/rwth-*.askurious-design.json; the built-in templates
"rwth" and "rwth-dark" went with migration 0091, konzept-org-designs-2026-10-19.md): colours of the RWTH Farbwelt only,
font and corners fixed, the logo at the top right."""
import re

from tests.helpers import rwth_design

from app import theme

# RWTH Farbwelt, 05/2023: the 13 full colours and the tints 75, 50, 25 and 10 %
FARBWELT = set(rwth_design.file()["swatches"]) | {
    "#ffffff",
    "#407fb7", "#8ebae5", "#c7ddf2", "#e8f1fa",                 # blue
    "#646567", "#9c9e9f", "#cfd1d2", "#eceded",                 # black
    "#d85c41", "#e69679", "#f3cdbb", "#faebe3",                 # red
    "#fabe50", "#fdd48f", "#feeac9", "#fff7ea",                 # orange
    "#8dc060", "#b8d698", "#ddebce", "#f2f7ec",                 # green
    "#b65256", "#cd8b87", "#e5c5c0", "#f5e8e5",                 # bordeaux
}
RWTH = rwth_design.COLOURS


def _colours(tokens):
    """Every colour a theme puts on the page: (token, #rrggbb), translucent ones by their base colour."""
    out = []
    for key, value in tokens.items():
        if theme.HEX.match(value):
            out.append((key, value))
        elif value.startswith("rgba("):
            r, g, b = (int(x) for x in re.findall(r"\d+", value)[:3])
            out.append((key, f"#{r:02x}{g:02x}{b:02x}"))
    return out


def test_every_colour_of_both_designs_is_a_colour_of_the_farbwelt():
    """Checked token by token against "RWTH Farbwelt" (05/2023): the 13 colours and their tints 75, 50, 25, 10 %,
    plus white. Nothing is mixed or derived for these designs, the smileys included."""
    assert len(rwth_design.file()["swatches"]) == 13
    for mode in ("light", "dark"):
        t = rwth_design.look(mode)
        tokens = theme.tokens(t)
        assert set(tokens) >= set(theme.standard_tokens()) | {"--face-bg", "--face-on", "--face-edge", "--face-ink"}
        outside = [(name, value) for name, value in _colours(tokens) if value not in FARBWELT]
        assert outside == [], (mode, outside)
        assert all(c["ok"] for c in theme.checks(t)), (mode, [c for c in theme.checks(t) if not c["ok"]])
        assert theme.problems(t) == []
        assert "Helvetica Neue" in tokens["--font"] and "Arial" in tokens["--font"] and tokens["--radius"] == "2px"


def test_the_light_design():
    t = rwth_design.look("light")
    assert t["preset"] == "org" and theme.name(t) == "RWTH Aachen"
    tokens = theme.tokens(t)
    assert tokens["--accent"] == "#00549f" and tokens["--fg"] == "#000000" and tokens["--on-accent"] == "#ffffff"
    assert theme.contrast(tokens["--border"], "#ffffff") >= 3            # frames of controls: black 75 %
    for key in ("--muted", "--error", "--ok", "--accent"):               # as type on white
        assert theme.contrast(tokens[key], "#ffffff") >= 4.5, key


def test_the_dark_design_is_readable_on_black():
    t = rwth_design.look("dark")
    assert theme.name(t) == "RWTH Aachen dunkel" and theme.is_dark(t) and "color-scheme: dark" in theme.css(t)
    tokens = theme.tokens(t)
    assert tokens["--accent"] == "#8ebae5" and tokens["--on-accent"] == "#000000"   # blue 100 % has 2.8 : 1 on black
    for key in ("--muted", "--error", "--nudge", "--ok", "--accent", "--fg"):
        assert theme.contrast(tokens[key], "#000000") >= 4.5, key
    # the Farbwelt has no dark tints: areas stay black, a state shows in its line
    for key in ("--accent-soft", "--error-soft", "--nudge-soft", "--good-mid", "--bad-mid", "--surface-2"):
        assert tokens[key] == "#000000", key
    for key in ("--border", "--line", "--error-line", "--nudge-line", "--good-line", "--bad-line", "--accent"):
        assert theme.contrast(tokens[key], "#000000") >= 3, key


def test_font_corners_and_logo_position_are_fixed_and_the_colours_alone_are_no_rwth_design():
    t = theme.clean({**rwth_design.look(), "font": "serif", "radius": "round",
                     "logo": {"file": "abcdefgh12345678.png", "w": 300, "h": 80, "position": "left"}})
    assert (t["font"], t["radius"], t["logo"]["position"]) == ("grotesk", "square", "right")
    typed = theme.clean({"colors": RWTH, "font": "serif"})
    assert typed["preset"] == "custom" and typed["font"] == "serif"      # typed in: an own design
    assert "rwth" not in theme.BUILTIN and "rwth-dark" not in theme.BUILTIN
    old = theme.clean({"preset": "rwth", "colors": RWTH})                # a file of before 0091: its colours only
    assert old["preset"] == "custom" and old["colors"] == RWTH
