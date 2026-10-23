"""The panel "Darstellung" on the page "Konto" (app/display.py): light or dark, colour style and text size of the
researchers' pages, kept with the account; the styles are token blocks in app.css, checked here for contrast."""
import os
import re

from app import display, theme
from app.extensions import db
from app.models import User

OTHER = "0000-0002-1825-0097"
CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "app", "static", "app.css")


def _as_other(client, affiliation=""):
    """The same browser signed in as a second researcher, no admin."""
    client.get(f"/auth/dev-login?orcid={OTHER}&name=Other")
    client.post("/terms/", data={"accept": "1"})
    with client.application.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        user.affiliation = affiliation
        db.session.commit()


def _display(client, orcid=None):
    with client.application.app_context():
        query = db.select(User).filter_by(orcid=orcid) if orcid else db.select(User).order_by(User.id).limit(1)
        return db.session.scalar(query).display


def test_clean_keeps_known_values_only():
    assert display.clean(None) == {"mode": "light", "look": "askurious", "size": 100}  # the brand's standard
    assert display.clean({"mode": "dark", "look": "kontrast", "size": "125"}) == {"mode": "dark", "look": "kontrast", "size": 125}
    assert display.clean({"mode": "pink", "look": "<x>", "size": "99"}) == {"mode": "light", "look": "askurious", "size": 100}


def test_new_accounts_start_light_older_ones_keep_the_browser(app, client):
    # new accounts: "Hell" (Ralf, 2026-10-05)
    page = client.get("/account/appearance").data.decode()
    assert 'id="anzeige"' in page and 'data-mode="light"' in page.split(">", 2)[1]
    assert _display(client)["mode"] == "light"
    # an account from before, with nothing saved: theme-init.js decides as before
    from app.extensions import db
    from app.models import User

    with app.app_context():
        for u in db.session.scalars(db.select(User)):
            u.display = None
        db.session.commit()
    page = client.get("/account/appearance").data.decode()
    assert "data-mode=" not in page.split(">", 2)[1]


def test_panel_saves_and_every_page_carries_it(client):
    r = client.post("/account/display", data={"mode": "dark", "look": "kontrast", "size": "112"}, follow_redirects=True)
    assert "Darstellung gespeichert." in r.data.decode()
    assert _display(client) == {"mode": "dark", "look": "kontrast", "size": 112}
    html = client.get("/dashboard/").data.decode().split("<head>")[0]
    assert 'data-mode="dark"' in html and 'data-look="kontrast"' in html and 'data-size="112"' in html


def test_menu_toggle_keeps_the_mode_with_the_account(client):
    client.post("/account/display", data={"mode": "auto", "look": "kontrast", "size": "125"})
    assert client.post("/account/display/mode", data={"mode": "light"}).status_code == 204
    assert _display(client) == {"mode": "light", "look": "kontrast", "size": 125}
    assert client.post("/account/display/mode", data={"mode": "auto"}).status_code == 400


def test_the_rwth_style_is_gone_and_said_once(client):
    """Ralf 2026-10-19 (konzept-org-designs-2026-10-19.md, E1): the style "RWTH" went with the RWTH design. Whoever had
    it sees "Askurious" and a note on the page "Konto" until they save the panel."""
    _as_other(client)
    page = client.get("/account/appearance").data.decode()
    assert 'value="kontrast"' in page and 'value="rwth"' not in page and "display-dropped" not in page
    r = client.post("/account/display", data={"mode": "auto", "look": "rwth", "size": "100"}, follow_redirects=True)
    assert _display(client, OTHER).get("look") != "rwth"
    with client.application.app_context():
        db.session.scalar(db.select(User).filter_by(orcid=OTHER)).display = {"mode": "auto", "look": "rwth", "size": 100}
        db.session.commit()
    page = client.get("/account/appearance").data.decode()
    assert 'data-look="askurious"' in page.split("<head>")[0]
    assert "Den Stil „RWTH“ gibt es nicht mehr" in page
    client.post("/account/display", data={"mode": "auto", "look": "kontrast", "size": "100"})
    assert "display-dropped" not in client.get("/account/appearance").data.decode()
    assert r.status_code == 200


# --- the styles in app.css -------------------------------------------------------------------------------


def _block(css, selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", m.group(1)))


def _palettes():
    css = open(CSS, encoding="utf-8").read()
    light, dark = _block(css, ":root"), _block(css, ':root[data-theme="dark"]')
    dark = {**light, **dark}
    out = {"askurious hell": light, "askurious dunkel": dark}
    for look in display.LOOKS:
        if look == "askurious":
            continue
        out[f"{look} hell"] = {**light, **_block(css, f':root[data-look="{look}"]:not([data-theme="dark"])')}
        out[f"{look} dunkel"] = {**dark, **_block(css, f':root[data-look="{look}"][data-theme="dark"]')}
    return light, out


def test_every_style_has_a_light_and_a_dark_block_with_known_tokens_only():
    light, palettes = _palettes()
    assert len(palettes) == 2 * len(display.LOOKS)
    for name, tokens in palettes.items():
        assert set(tokens) == set(light), name  # nothing the standard does not know


def test_every_style_meets_wcag_aa():
    pairs = [("fg", "bg", 4.5), ("fg", "surface", 4.5), ("muted", "surface", 4.5), ("muted", "bg", 4.5),
             ("accent", "surface", 4.5), ("accent", "bg", 3), ("on-accent", "accent", 4.5),
             # the field has the colour of its panel; 3:1 was hard to see (researchers, Ralf 2026-10-21)
             ("field-border", "surface", 4.5), ("field-border", "bg", 4.5),
             ("danger", "surface", 4.5), ("ai", "surface", 4.5), ("linked", "surface", 4.5), ("interview", "surface", 4.5),
             ("live", "surface", 4.5), ("ok", "surface", 4.5), ("warn", "surface", 4.5)]
    pairs += [(f"chart-{i}", "surface", 3) for i in range(1, 7)]  # all six series (W120)
    _light, palettes = _palettes()
    for name, tokens in palettes.items():
        for a, b, minimum in pairs:
            ratio = theme.contrast(tokens[f"--{a}"].strip(), tokens[f"--{b}"].strip())
            assert ratio >= minimum, f"{name}: {a} auf {b} {ratio:.2f}"


# --- the data colours of the charts (UI/UX audit 2026-10-07, W120, W121) ------------------------------------------

# Machado, Oliveira & Fernandes (2009), severity 1.0, applied to linear RGB
PROTANOPIA = ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998))
DEUTERANOPIA = ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881))
MEANING = ("ok", "caution", "warn", "danger", "ai", "linked", "interview", "live", "accent")


def _linear(hex_colour):
    h = hex_colour.strip().lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return out


def _lab(linear, matrix=None):
    """CIELAB (D65) of a linear RGB colour, as seen with the dichromacy of `matrix` (None: normal vision)."""
    if matrix:
        linear = [min(1.0, max(0.0, sum(m * c for m, c in zip(row, linear)))) for row in matrix]
    r, g, b = linear
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e(a, b):
    return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5


def test_chart_colours_differ_with_red_green_colour_blindness():
    """Every pair of the six data colours stays apart by ΔE 12 and more (CIE 1976) for normal vision, protanopia
    and deuteranopia, in every style (W120: blue and violet were ΔE 1.5 apart with protanopia)."""
    _light, palettes = _palettes()
    for name, tokens in palettes.items():
        colours = [_linear(tokens[f"--chart-{i}"]) for i in range(1, 7)]
        for vision, matrix in (("normal", None), ("Protanopie", PROTANOPIA), ("Deuteranopie", DEUTERANOPIA)):
            labs = [_lab(c, matrix) for c in colours]
            for i in range(6):
                for j in range(i + 1, 6):
                    gap = _delta_e(labs[i], labs[j])
                    assert gap >= 12, f"{name}, {vision}: chart-{i + 1} und chart-{j + 1} nur ΔE {gap:.1f}"


def test_chart_colours_are_no_meaning_colours():
    """No data colour takes the tone of a colour that carries a meaning (W121: chart-3 was the green of "ok",
    chart-2 the orange of "caution"): ΔE 15 and more to each of them."""
    _light, palettes = _palettes()
    for name, tokens in palettes.items():
        meaning = MEANING
        for i in range(1, 7):
            data = _lab(_linear(tokens[f"--chart-{i}"]))
            for m in meaning:
                gap = _delta_e(data, _lab(_linear(tokens[f"--{m}"])))
                assert gap >= 15, f"{name}: chart-{i} liegt bei --{m} (ΔE {gap:.1f})"


def test_segment_keys_have_a_shape_each():
    """Where colour alone marked a segment, its key also has a shape of its own (W120)."""
    css = open(CSS, encoding="utf-8").read()
    for k in range(2, 7):
        assert re.search(r"\.lc-key\.lc-c%d \{[^}]*(clip-path|border-radius: 1px)" % k, css), k


# --- palettes by the type of scale (UI/UX audit 2026-10-07, S11; Harrower & Brewer, 2003) ------------------------

SEQ = [f"seq-{i}" for i in range(1, 6)]
DIV = [f"div-{i}" for i in range(1, 8)]
JUDGING = ("ok", "caution", "warn", "danger")
BOARD_CSS = os.path.join(os.path.dirname(CSS), "board.css")


def _L(tokens, name):
    return _lab(_linear(tokens[f"--{name}"]))[0]


def _even(values, name, what):
    steps = [b - a for a, b in zip(values, values[1:])]
    assert all(s > 0 for s in steps) or all(s < 0 for s in steps), f"{name}: {what} nicht monoton {values}"
    mean = sum(abs(s) for s in steps) / len(steps)
    assert mean >= 10, f"{name}: {what} Stufen zu klein ({mean:.1f})"
    assert all(abs(abs(s) - mean) <= 1.5 for s in steps), f"{name}: {what} ungleiche Stufen {[round(s, 1) for s in steps]}"


def test_scale_palettes_have_even_steps_and_a_neutral_middle():
    """Sequential: five steps of the same lightness difference (ΔL*), darker towards "more" in the light styles and
    lighter in the dark ones. Diverging: both sides of the same lightness, even steps outwards from a grey middle."""
    _light, palettes = _palettes()
    for name, tokens in palettes.items():
        seq = [_L(tokens, s) for s in SEQ]
        _even(seq, name, "sequenziell")
        assert (seq[-1] < seq[0]) == name.endswith("hell"), name
        div = [_L(tokens, d) for d in DIV]
        for i in range(3):
            assert abs(div[i] - div[6 - i]) <= 1.5, f"{name}: div-{i + 1} und div-{7 - i} ungleich hell"
        _even(div[3:], name, "divergierend")
        _l, a, b = _lab(_linear(tokens["--div-4"]))
        assert (a * a + b * b) ** 0.5 <= 5, f"{name}: die Mitte ist nicht neutral"


def test_scale_palettes_hold_their_contrasts():
    """The ends 3:1 against the surface (WCAG 1.4.11), the text on every level 4.5:1, the fill of bars 3:1 against the
    surface and against the error bars in --fg."""
    _light, palettes = _palettes()
    for name, tokens in palettes.items():
        def c(a, b):
            return theme.contrast(tokens[f"--{a}"].strip(), tokens[f"--{b}"].strip())
        for end in ("seq-5", "div-1", "div-7", "data-fill", "data-neg"):
            assert c(end, "surface") >= 3, f"{name}: {end} auf surface {c(end, 'surface'):.2f}"
        for fill in ("data-fill", "data-neg"):
            assert c(fill, "fg") >= 3, f"{name}: {fill} gegen fg {c(fill, 'fg'):.2f}"
        for d in DIV:
            assert c("div-ink", d) >= 4.5, f"{name}: div-ink auf {d} {c('div-ink', d):.2f}"


def test_scale_palettes_differ_with_red_green_colour_blindness():
    """Neighbouring steps ΔE 10 and more, the two sides of the diverging scale and the two directions of a bar ΔE 12
    and more, for normal vision, protanopia and deuteranopia (Machado et al., 2009)."""
    _light, palettes = _palettes()
    for name, tokens in palettes.items():
        for vision, matrix in (("normal", None), ("Protanopie", PROTANOPIA), ("Deuteranopie", DEUTERANOPIA)):
            def lab(t):
                return _lab(_linear(tokens[f"--{t}"]), matrix)
            for scale in (SEQ, DIV):
                for a, b in zip(scale, scale[1:]):
                    assert _delta_e(lab(a), lab(b)) >= 10, f"{name}, {vision}: {a} und {b} ΔE {_delta_e(lab(a), lab(b)):.1f}"
            for i in range(1, 4):
                gap = _delta_e(lab(f"div-{i}"), lab(f"div-{8 - i}"))
                assert gap >= 12, f"{name}, {vision}: div-{i} und div-{8 - i} nur ΔE {gap:.1f}"
            assert _delta_e(lab("data-fill"), lab("data-neg")) >= 12, f"{name}, {vision}: data-fill und data-neg"


def test_scale_palettes_judge_nothing():
    """No step of a scale and no fill takes the tone of a colour that judges (OK, caution, warning, error): ΔE 15 and
    more. The grey middle is left out; part-worths below zero keep their sign next to --data-neg."""
    _light, palettes = _palettes()
    for name, tokens in palettes.items():
        for t in SEQ + [d for d in DIV if d != "div-4"] + ["data-fill", "data-neg"]:
            for m in JUDGING:
                gap = _delta_e(_lab(_linear(tokens[f"--{t}"])), _lab(_linear(tokens[f"--{m}"])))
                assert gap >= 15, f"{name}: {t} liegt bei --{m} (ΔE {gap:.1f})"


def test_dashboards_take_the_same_scale_palettes():
    """board.css carries the light and the dark values of the standard style (light-dark()) and the light ones for
    browsers without light-dark()."""
    css = open(BOARD_CSS, encoding="utf-8").read()
    _light, palettes = _palettes()
    light, dark = palettes["askurious hell"], palettes["askurious dunkel"]
    first = re.search(r"body\.board \{\s*(--seq-1[^}]*)\}", css).group(1)
    plain = dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", first))
    pairs = {k: (a, b) for k, a, b in re.findall(r"(--[a-z0-9-]+):\s*light-dark\((#[0-9a-f]{6}),\s*(#[0-9a-f]{6})\)", css)}
    names = [f"--{t}" for t in SEQ + DIV + ["div-ink", "data-fill", "data-neg"]]
    assert set(plain) == set(pairs) == set(names)
    for k in names:
        assert plain[k] == pairs[k][0] == light[k].strip(), k
        assert pairs[k][1] == dark[k].strip(), k


def test_figures_take_the_tokens_not_mixes_of_their_own():
    """Bars, maps, heat maps and stacked levels use the tokens; the accent, the caution colour and hex values of their
    own no longer stand in for data colours (S11)."""
    css = open(CSS, encoding="utf-8").read()
    for selector, token in ((".res-bar span {", "--data-fill"), (".res-hist-bar {", "--data-fill"), (".speed-bar {", "--data-fill"),
                            (".pw-pos {", "--data-fill"), (".pw-neg {", "--data-neg"), (".res-bar-zero span.is-neg {", "--data-neg"),
                            (".res-terms-bar {", "--data-fill"), (".res-stack > span {", "--div-ink")):
        rule = css[css.index(selector):css.index("}", css.index(selector))]
        assert f"var({token})" in rule and "color-mix" not in rule, selector
    assert not re.search(r"\.psm-[a-z_]+ \{ stroke: #", css)
    assert "chart-neutral" not in css
    board = open(BOARD_CSS, encoding="utf-8").read()
    assert "--b-low" not in board and "--b-mid" not in board
    root = os.path.join(os.path.dirname(os.path.dirname(CSS)), "templates")
    for path in ("surveys/_state_results.html", "boards/_tiles.html"):
        text = open(os.path.join(root, path), encoding="utf-8").read()
        assert "color-mix" not in text, path


def test_scale_helpers_give_tokens():
    from app import results
    from app.items import postal_code
    assert [results.level_colour(i, 3)[0] for i in range(3)] == ["var(--div-1)", "var(--div-4)", "var(--div-7)"]
    assert [results.level_colour(i, 4)[0] for i in range(4)] == ["var(--div-1)", "var(--div-3)", "var(--div-5)", "var(--div-7)"]
    assert results.level_colour(1, 5)[0] == "color-mix(in oklab, var(--div-2) 50%, var(--div-3))"
    assert results.level_colour(3, 5)[0] == "color-mix(in oklab, var(--div-5) 50%, var(--div-6))"   # mirrored
    assert results.level_colour(0, 1) == ("var(--div-7)", True)
    assert [results.sequential_colour(v, 2, 10) for v in (2, 4, 6, 8, 10)] == [f"var(--seq-{i})" for i in range(1, 6)]
    assert results.sequential_colour(3, 3, 3) == "var(--seq-5)"
    assert [c["color"] for c in postal_code.classes([1, 2, 3])] == ["var(--seq-3)", "var(--seq-4)", "var(--seq-5)"]
