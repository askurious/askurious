"""Colours that stand on each other, measured in every built-in theme and in derived dark sides (UI/UX audit
2026-10-16, Paket 3): text on its own tinted area (58), the colour of data in feedback figures (62), the fixed
colours and their writing on dashboards (60)."""
from app import theme
from app.boards import render


def _themes():
    out = []
    for key, entry in theme.BUILTIN.items():
        out.append({**theme.standard(), "preset": key, "colors": dict(entry["colors"])})
    # a theme of one's own and its suggested dark side ("automatisch hell oder dunkel")
    own = {**theme.standard(), "preset": "custom",
           "colors": {"background": "#fdfaf3", "surface": "#ffffff", "text": "#1d1b16", "accent": "#7a3e9d"}}
    out += [own, {**own, "colors": theme.suggest_dark(own["colors"])}]
    return out


def test_text_on_its_own_area_and_data_colours_reach_their_contrast():
    weak = []
    for t in _themes():
        tok = theme.tokens(t)
        for text, area in (("--nudge", "--nudge-soft"), ("--error", "--error-soft"), ("--ok", "--good-soft")):
            if theme.contrast(tok[text], tok[area]) < theme.TEXT_MIN:
                weak.append((t["preset"], text, area, round(theme.contrast(tok[text], tok[area]), 2)))
        for key in ("--data", "--data-strong"):
            if not tok[key].startswith("var(") and theme.contrast(tok[key], tok["--bg"]) < theme.PART_MIN:
                weak.append((t["preset"], key, "--bg", round(theme.contrast(tok[key], tok["--bg"]), 2)))
        assert all(c["ok"] for c in theme.checks(t)), (t["preset"], [c for c in theme.checks(t) if not c["ok"]])
    assert not weak, weak


def test_dashboard_colours_keep_three_to_one_and_their_writing_four_and_a_half():
    class Survey:
        pass

    for t in _themes():
        s = Survey()
        s.theme = t
        surface = theme.tokens(theme.of(s))["--surface"]
        for colour in render.palette(s):
            if colour.startswith("var("):
                continue
            assert theme.contrast(colour, surface) >= theme.PART_MIN, (t["preset"], colour)
            assert theme.contrast(render.seg_ink(colour), colour) >= theme.TEXT_MIN, (t["preset"], colour)
