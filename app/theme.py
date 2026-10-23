"""Themes: the look of the pages participants see (colours, font, corners).

A theme belongs to a survey (models.SurveyTheme, one row per survey; no row = the standard look). Researchers
give a few base colours only; every other tone of app/static/participant.css is derived here, in one place,
so the rules can change without touching stored themes. The result is a block of CSS variables for the pages
of that survey. The researcher pages are not themed.

Only validated values reach the page: colours as #rrggbb, everything else from fixed lists. That is what makes
the inline <style> block safe; free CSS is not accepted on purpose.

Built-in themes (BUILTIN) are part of the code. "askurious" is the standard look: it sets nothing, the values in
participant.css are the theme. Built-in themes may fix single derived tokens ("tokens").

An organisation design (preset ORG, concept konzept-org-designs-2026-10-19.md, Ralf 2026-10-19) is stored whole: its
name, the tokens it fixes (light and, for "automatisch", dark), a fixed font and corners, the logo position and colour
suggestions. Its admins upload it as a design file or take it over from a template or design; a survey that chooses it
gets a copy of all of it, so a later change of the organisation design changes no survey. Token values are checked
like colours (ORG_TOKEN_VALUE), so the inline <style> block stays safe.
"""
import functools
import math
import os
import re

HEX = re.compile(r"^#[0-9a-f]{6}\Z")
COLOURS = ("accent", "background", "text", "surface")  # what researchers choose
FONTS = {
    "system": ("Systemschrift", 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'),
    "serif": ("Serifenschrift", '"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, "Times New Roman", serif'),
    "rounded": ("Runde Schrift", 'ui-rounded, "SF Pro Rounded", "Hiragino Maru Gothic ProN", Quicksand, "Arial Rounded MT Bold", system-ui, sans-serif'),
    "grotesk": ("Helvetica / Arial", '"Helvetica Neue", Helvetica, Arial, sans-serif'),
    # the one font that comes with Askurious (app/static/fonts, SIL Open Font License); the others are fonts of the device
    "mono": ("Monospace (Schreibmaschine)", 'ui-monospace, "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace'),
    "atkinson": ("Atkinson Hyperlegible Next (gut lesbar)", '"Atkinson Hyperlegible Next", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'),
}
RADII = {"soft": ("Weich", "10px"), "round": ("Rund", "16px"), "square": ("Eckig", "2px")}
FONT_SCALES = (100, 112, 125)  # percent of the standard text size
LOGO_POSITIONS = {"left": "links", "center": "mittig", "right": "rechts"}
LOGO_SIZES = {"s": ("klein", 32), "m": ("mittel", 48), "l": ("groß", 72)}  # height in px
MAX_PARTNERS = 3  # logos of funders and partners, small, on the first and the last page
LOGO_FILE = re.compile(r"^[A-Za-z0-9_-]{8,40}\.(png|jpg|webp|svg)\Z")

# The standard look, as in participant.css (kept here for the editor: its colour fields start with these).
STANDARD = {"accent": "#2a5db0", "background": "#ffffff", "text": "#1d1d1b", "surface": "#ffffff"}

BUILTIN = {
    "askurious": {"name": "Askurious", "note": "Standard", "colors": STANDARD},
    # the night mode of the researcher pages (app.css), for participants
    "askurious-dark": {"name": "Askurious dunkel", "note": "dunkler Hintergrund",
                      "colors": {"accent": "#7ea6e6", "background": "#141517", "text": "#e7e5e0", "surface": "#1c1e21"},
                      "tokens": {"--muted": "#a4a29c", "--on-accent": "#0e1a2e", "--error": "#f08d86"}},
}
# Two open templates with a look of their own: choosing one also sets font, corners and text size ("look"),
# which stay changeable afterwards (unlike "fixed").
BUILTIN.update({
    # warm paper, brick red, a book face: like a printed questionnaire
    "papier": {"name": "Papier", "note": "warm, Serifenschrift, eckig",
               "colors": {"accent": "#a4442a", "background": "#f4eee2", "text": "#2a241c", "surface": "#fffcf5"},
               "look": {"font": "serif", "radius": "square", "font_scale": 112}},
    # pale sage, deep green, round shapes: calm and friendly
    "salbei": {"name": "Salbei", "note": "ruhig, runde Schrift und Ecken",
               "colors": {"accent": "#1f6f5c", "background": "#e6efe9", "text": "#17302a", "surface": "#ffffff"},
               "look": {"font": "rounded", "radius": "round", "font_scale": 100},
               "tokens": {"--error-soft": "#fbeceb"}},
    # night blue and coral: dark, warm, the font that reads best
    "nachtblau": {"name": "Nachtblau", "note": "dunkel, Koralle, gut lesbare Schrift",
                  "colors": {"accent": "#ff7f61", "background": "#101a33", "text": "#f3efe8", "surface": "#17233f"},
                  "look": {"font": "atkinson", "radius": "soft", "font_scale": 100}},
    # black and yellow, as in Aachen: black buttons with yellow type, a chosen answer in full yellow
    "schwarz-gelb": {"name": "Schwarz-Gelb", "note": "Aachen, Grotesk, eckig",
                     "colors": {"accent": "#000000", "background": "#ffffff", "text": "#000000", "surface": "#ffffff"},
                     "look": {"font": "grotesk", "radius": "square", "font_scale": 100},
                     "tile": {"accent": "#ffe600"},  # the tile in the list shows the yellow, not only black and white
                     "tokens": {"--accent-soft": "#ffe600", "--on-accent": "#ffe600", "--star": "#ffd200",
                                "--star-hover": "#ffe600", "--hover": "#fff8b8"}},
})
# Retro: a monospace face, square corners; light green paper with dark green type, and as its dark partner a
# terminal with light green type on dark green ("automatisch hell oder dunkel" switches between the two)
BUILTIN.update({
    "retro": {"name": "Retro", "note": "hellgrün, Monospace, eckig",
              "colors": {"accent": "#1e7a3c", "background": "#e9f3e1", "text": "#113a1f", "surface": "#f6fbf1"},
              "look": {"font": "mono", "radius": "square", "font_scale": 100},
              "tokens": {"--error-soft": "#f9e4e1"}},  # derived it came out beige on the green paper
    "retro-dark": {"name": "Retro dunkel", "note": "Terminal: hellgrün auf Dunkelgrün, Monospace",
                   "colors": {"accent": "#42e57a", "background": "#0b1d12", "text": "#b9f6c9", "surface": "#11291b"},
                   "look": {"font": "mono", "radius": "square", "font_scale": 100}},
})
ORG = "org"  # the preset of an organisation design (above)
ORG_TOKEN = re.compile(r"^--[a-z][a-z0-9-]{0,39}\Z")
ORG_TOKEN_VALUE = re.compile(r"^(#[0-9a-f]{6}|rgba\(\d{1,3}, ?\d{1,3}, ?\d{1,3}, ?(0|1|0?\.\d{1,3})\))\Z")
ORG_TOKEN_MAX = 80
MAX_SWATCHES = 24
# tokens participant.css reads with a fallback but tokens() does not write: only a design sets them (the smileys in one
# look, a second pixel of edge on the chosen answer; the RWTH design files, tests/data)
DESIGN_TOKENS = frozenset({"--face-bg", "--face-on", "--face-edge", "--face-ink", "--chosen-edge"})
DESIGN_FILE = "askurious-design"  # the "format" of a design file (organisation designs, app/org_designs.py)
# "Automatic": light or dark after the setting of the participant's device. The dark partner of a built-in theme:
PAIRS = {"askurious": "askurious-dark", "retro": "retro-dark"}
SCHEMES = {"fixed": "immer gleich", "auto": "automatisch hell oder dunkel, nach der Einstellung des Geräts"}
# names and notes of the built-in themes and the font labels in English (researcher pages in English, app/lang.py);
# the German ones stay in BUILTIN and FONTS
BUILTIN_EN = {"askurious": ("Askurious", "standard"), "askurious-dark": ("Askurious dark", "dark background"),
              "papier": ("Paper", "warm, serif font, square"), "salbei": ("Sage", "calm, rounded font and corners"),
              "nachtblau": ("Night blue", "dark, coral, highly legible font"),
              "schwarz-gelb": ("Black and yellow", "Aachen, sans serif, square"),
              "retro": ("Retro", "light green, monospace, square"),
              "retro-dark": ("Retro dark", "terminal: light green on dark green, monospace")}
FONTS_EN = {"system": "System font", "serif": "Serif font", "rounded": "Rounded font", "grotesk": "Helvetica / Arial",
            "mono": "Monospace (typewriter)", "atkinson": "Atkinson Hyperlegible Next (highly legible)"}


def _english(lang=None):
    """English for lang "en", German for "de"/"de-du"; without lang the language of the page."""
    if lang is None:
        from app.lang import english

        return english()
    return lang not in ("de", "de-du")


RADII_EN = {"soft": "Soft", "round": "Round", "square": "Square"}
LOGO_POSITIONS_EN = {"left": "left", "center": "centre", "right": "right"}
LOGO_SIZES_EN = {"s": "small", "m": "medium", "l": "large"}
SCHEMES_EN = {"fixed": "always the same", "auto": "light or dark automatically, after the device's setting"}


def radii(lang=None):
    """{key: label} of RADII in the language of the page (or `lang`)."""
    return {key: (RADII_EN[key] if _english(lang) else label) for key, (label, _px) in RADII.items()}


def logo_positions(lang=None):
    """{key: label} of LOGO_POSITIONS in the language of the page (or `lang`)."""
    return {key: (LOGO_POSITIONS_EN[key] if _english(lang) else label) for key, label in LOGO_POSITIONS.items()}


def logo_sizes(lang=None):
    """{key: (label, height in px)} of LOGO_SIZES in the language of the page (or `lang`)."""
    return {key: (LOGO_SIZES_EN[key] if _english(lang) else label, px) for key, (label, px) in LOGO_SIZES.items()}


def schemes(lang=None):
    """{key: label} of SCHEMES in the language of the page (or `lang`)."""
    return {key: (SCHEMES_EN[key] if _english(lang) else label) for key, label in SCHEMES.items()}


def font_name(key, lang=None):
    """The label of a font of FONTS, in the language of the page (or `lang`)."""
    return FONTS_EN.get(key, FONTS[key][0]) if _english(lang) else FONTS[key][0]


def fonts():
    """{key: label} of FONTS in the language of the page."""
    return {key: font_name(key) for key in FONTS}
DEFAULT = "askurious"

# meanings that stay the same under every theme; only their lightness follows the background
RED, AMBER, GREEN, GOLD = "#b3261e", "#6b4f00", "#1f6b2a", "#f2b01e"
VIOLET = "#7a3fd1"  # AI in Askurious: the element KI-Chat (app/items/ai_chat.py)

TEXT_MIN, PART_MIN = 4.5, 3.0  # WCAG 2.2 AA: text; parts of controls and focus marks


# --- colour maths ---------------------------------------------------------------------------


def _rgb(colour):
    return tuple(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _hex(rgb):
    return "#" + "".join(f"{round(min(1, max(0, c)) * 255):02x}" for c in rgb)


def _linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def luminance(colour):
    r, g, b = (_linear(c) for c in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """WCAG contrast ratio of two colours, 1 to 21."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# OKLab and its inverse with the matrices of Ottosson, B. (2020), "A perceptual color space for image processing",
# https://bottosson.github.io/posts/oklab/ (his reference code is in the public domain, also offered under MIT).
def _oklab(colour):
    r, g, b = (_linear(c) for c in _rgb(colour))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (math.copysign(abs(v) ** (1 / 3), v) for v in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _from_oklab(lab):
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return _hex((_gamma(max(0, 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s)),
                 _gamma(max(0, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s)),
                 _gamma(max(0, -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s))))


def lightness(colour):
    """Perceived lightness, 0 (black) to 1 (white)."""
    return _oklab(colour)[0]


def mix(a, b, share):
    """`share` of b in a, mixed in OKLab (even steps look even)."""
    x, y = _oklab(a), _oklab(b)
    return _from_oklab(tuple(p + (q - p) * share for p, q in zip(x, y)))


def tint(a, b, share):
    """`share` of b in a, as paint would mix on screen (in sRGB). Used for the soft areas and lines of a theme:
    on a very dark background a perceptual mix (OKLab) stays almost black, this one gives a visible tone."""
    return _hex(tuple(p + (q - p) * share for p, q in zip(_rgb(a), _rgb(b))))


def with_lightness(colour, L):
    """The same hue at another lightness; the chroma shrinks towards black and white so the result stays a colour
    a screen can show."""
    l0, a, b = _oklab(colour)
    room = min(L, 1 - L) / max(1e-6, min(l0, 1 - l0))
    k = min(1, room)
    return _from_oklab((L, a * k, b * k))


def readable(colour, background, ratio=TEXT_MIN):
    """`colour` moved away from the background's lightness until it reaches the contrast `ratio` on it (hue kept).
    Black or white is the last step."""
    if contrast(colour, background) >= ratio:
        return colour
    dark_bg = lightness(background) < 0.5
    L = lightness(colour)
    for _ in range(60):
        L = min(1, L + 0.02) if dark_bg else max(0, L - 0.02)
        candidate = with_lightness(colour, L)
        if contrast(candidate, background) >= ratio:
            return candidate
    return "#ffffff" if dark_bg else "#000000"


def _rgba(colour, alpha):
    r, g, b = (round(c * 255) for c in _rgb(colour))
    return f"rgba({r}, {g}, {b}, {alpha})"


# --- themes ---------------------------------------------------------------------------------


def _name(value):
    """A choice as it is looked up in the tables above: a text, or None. A file read in (app/transfer.py) may
    hold a list or a dict there, and asking a dict for such a key is an error, not "unknown"."""
    return value if isinstance(value, str) else None


def clean(data):
    """A stored theme from untrusted input (form, older versions, a file read in): unknown keys and invalid
    values fall back to the standard. Returns None for "no theme" (the standard look)."""
    data = data if isinstance(data, dict) else {}
    named = _name(data.get("preset"))
    if named == ORG and not _org_name(data.get("name")):
        named = None  # an organisation design always has its name
    base = BUILTIN[named]["colors"] if named in BUILTIN else STANDARD
    given = data.get("colors") if isinstance(data.get("colors"), dict) else {}
    colors = {}
    for key in COLOURS:
        value = str(given.get(key, "")).strip().lower()
        colors[key] = value if HEX.match(value) else base[key]
    # the name follows the colours: a built-in theme is recognised by them
    if named == ORG or (named in BUILTIN and BUILTIN[named]["colors"] == colors):
        preset = named
    else:
        preset = next((key for key, source in BUILTIN.items() if source["colors"] == colors), "custom")
    org = _org_parts(data) if preset == ORG else {}
    fixed = org.get("fixed") or BUILTIN.get(preset, {}).get("fixed", {})
    font = fixed.get("font") or (data.get("font") if _name(data.get("font")) in FONTS else "system")
    radius = fixed.get("radius") or (data.get("radius") if _name(data.get("radius")) in RADII else "soft")
    try:
        scale = int(data.get("font_scale", 100))
    except (TypeError, ValueError):
        scale = 100
    scale = scale if scale in FONT_SCALES else 100
    theme = {"v": 1, "preset": preset, "colors": colors, "font": font, "font_scale": scale, "radius": radius,
             "scheme": "fixed"}
    theme.update({k: v for k, v in org.items() if k != "tokens_dark"})
    if data.get("scheme") == "auto" and preset != ORG and lightness(colors["background"]) >= 0.5:
        # a light look with a second palette for devices set to dark (a dark look stays as it is); never for an
        # organisation design: its light side chosen stays light, its dark side is chosen on its own (Ralf,
        # 2026-10-23), so surveys that still carry the former pair show light
        given = data.get("colors_dark") if isinstance(data.get("colors_dark"), dict) else {}
        base = BUILTIN[PAIRS[preset]]["colors"] if preset in PAIRS else suggest_dark(colors)
        dark = {}
        for key in COLOURS:
            value = str(given.get(key, "")).strip().lower()
            dark[key] = value if HEX.match(value) else base[key]
        if lightness(dark["background"]) < 0.5:
            theme.update(scheme="auto", colors_dark=dark)
            if org.get("tokens_dark"):
                theme["tokens_dark"] = org["tokens_dark"]
    if data.get("maps") == "dark" and (theme["scheme"] == "auto" or lightness(colors["background"]) < 0.5):
        theme["maps"] = "dark"  # maps of map questions get dark tiles on the dark side (else the usual light map)
    partners = clean_partners(data.get("partners"))
    if partners:
        theme["partners"] = partners
    logo = clean_logo(data.get("logo"))
    if logo and fixed_logo_position(theme):
        logo["position"] = fixed_logo_position(theme)
    if logo:
        theme["logo"] = logo
    if data.get("image_rights"):
        from app import asset_rights

        active = {value for key, value in (theme.get("logo") or {}).items() if key in ("file", "file_dark", "icon")}
        active.update(entry["file"] for entry in theme.get("partners", []))
        source = data["image_rights"] if isinstance(data["image_rights"], dict) else {}
        records = asset_rights.clean_theme_records({name: source[name] for name in active if name in source})
        if records:
            theme["image_rights"] = records
    return None if theme == standard() else theme


def _org_name(value):
    return " ".join(value.split())[:80] if isinstance(value, str) else ""


def _org_tokens(data):
    """The tokens an organisation design fixes: known names (those of participant.css and tokens()), values that are
    colours only. Font and corners come from "fixed", not from here."""
    if not isinstance(data, dict):
        return {}
    known = token_names()
    out = {}
    for key, value in data.items():
        value = value.strip().lower() if isinstance(value, str) else ""
        if (isinstance(key, str) and ORG_TOKEN.match(key) and key in known and key not in ("--radius", "--font")
                and ORG_TOKEN_VALUE.match(value)):
            out[key] = value
        if len(out) >= ORG_TOKEN_MAX:
            break
    return out


def _org_parts(data):
    """What an organisation design carries beyond the usual theme, checked: {"name", "tokens", "tokens_dark", "fixed",
    "logo_position", "swatches", "org"} (only the keys it has)."""
    out = {"name": _org_name(data.get("name"))}
    for key in ("tokens", "tokens_dark"):
        found = _org_tokens(data.get(key))
        if found:
            out[key] = found
    given = data.get("fixed") if isinstance(data.get("fixed"), dict) else {}
    fixed = {}
    if _name(given.get("font")) in FONTS:
        fixed["font"] = given["font"]
    if _name(given.get("radius")) in RADII:
        fixed["radius"] = given["radius"]
    if fixed:
        out["fixed"] = fixed
    if _name(data.get("logo_position")) in LOGO_POSITIONS:
        out["logo_position"] = data["logo_position"]
    given = data.get("swatches") if isinstance(data.get("swatches"), list) else []
    colours = list(dict.fromkeys(c.strip().lower() for c in given if isinstance(c, str) and HEX.match(c.strip().lower())))
    if colours:
        out["swatches"] = colours[:MAX_SWATCHES]
    org_id = data.get("org")
    if isinstance(org_id, int) and not isinstance(org_id, bool) and 0 < org_id < 2 ** 31:
        out["org"] = org_id
    return out


@functools.lru_cache(maxsize=1)
def token_names():
    """Every CSS variable a theme may set: those of participant.css and those tokens() writes."""
    names = set(standard_tokens()) | DESIGN_TOKENS
    names.update(tokens({"preset": "custom", "colors": dict(STANDARD), "font": "system", "radius": "soft"}))
    for t in BUILTIN.values():
        names.update(t.get("tokens", {}))
    return frozenset(names)


def fixed_logo_position(theme):
    """Where an organisation design puts the logo whatever the survey chose (the RWTH design: top right), or None."""
    theme = theme or {}
    return theme.get("logo_position") or BUILTIN.get(theme.get("preset"), {}).get("logo_position")


def as_org(source, name, org_id=None):
    """An organisation design made from a theme (a built-in template, a design, an organisation design): the colours,
    font, corners and text size of its light side, and what a built-in template fixes (tokens, font, corners, logo
    position, colour suggestions) written into it, so that it no longer depends on the template. Without logo,
    partners and dark palette (an organisation keeps its light and its dark design apart)."""
    t = clean(source) or standard()
    built = BUILTIN.get(t["preset"], {})
    data = {"preset": ORG, "name": name, "colors": t["colors"], "font": t["font"], "font_scale": t["font_scale"],
            "radius": t["radius"], "tokens": t.get("tokens") if t["preset"] == ORG else dict(built.get("tokens", {})),
            "fixed": t.get("fixed") or built.get("fixed"), "logo_position": fixed_logo_position(t),
            "swatches": t.get("swatches") or built.get("swatches"), "org": org_id}
    return clean(data)


def org_pair(light, dark=None):
    """The theme a survey gets from an organisation design: the light one if there is one, else the dark one. A light
    one stays light on a device set to dark too (Ralf, 2026-10-23); the dark one is a choice of its own."""
    light, dark = clean(light), clean(dark)
    light = light if light is not None and light["preset"] == ORG else None
    dark = dark if dark is not None and dark["preset"] == ORG else None
    return light or dark


def design_file(theme):
    """An organisation design as a design file (a dict for JSON), the form an upload reads (read_design_file)."""
    t = clean(theme) or standard()
    out = {"format": DESIGN_FILE, "v": 1, "name": name(t), "colors": t["colors"], "font": t["font"],
           "font_scale": t["font_scale"], "radius": t["radius"]}
    for key in ("tokens", "fixed", "logo_position", "swatches"):
        if t.get(key):
            out[key] = t[key]
    return out


def read_design_file(data, fallback_name=""):
    """An organisation design from a design file (a dict read from JSON), or None if it is none. Unknown keys and
    values are dropped as everywhere (clean); a logo is never part of it."""
    if not isinstance(data, dict) or data.get("format") != DESIGN_FILE or not isinstance(data.get("colors"), dict):
        return None
    label = _org_name(data.get("name")) or _org_name(fallback_name)
    if not label:
        return None
    keep = ("colors", "font", "font_scale", "radius", "tokens", "fixed", "logo_position", "swatches")
    return clean({**{k: data[k] for k in keep if k in data}, "preset": ORG, "name": label})


def clean_partners(data):
    """Logos of funders and partners: [{"file", "w", "h", "alt"}], at most MAX_PARTNERS."""
    out = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict) or not LOGO_FILE.match(str(entry.get("file", ""))):
            continue
        try:
            w, h = (max(1, min(20000, int(entry.get(k, 0)))) for k in ("w", "h"))
        except (TypeError, ValueError):
            w = h = 1
        out.append({"file": entry["file"], "w": w, "h": h, "alt": " ".join(str(entry.get("alt") or "").split())[:200]})
    return out[:MAX_PARTNERS]


PARTNER_HEIGHT = 32  # px


def partners_for(survey):
    """For the page: [{"file", "w", "h", "alt"}] at the height they are shown in."""
    theme = clean(getattr(survey, "theme", None)) if survey is not None else None
    return [{"file": p["file"], "alt": p["alt"], "h": PARTNER_HEIGHT, "w": max(1, round(p["w"] * PARTNER_HEIGHT / p["h"]))}
            for p in (theme or {}).get("partners", [])]


def clean_logo(data):
    """The logo part of a theme: file names as media.save_logo gives them, texts and choices checked. None
    without a file."""
    data = data if isinstance(data, dict) else {}
    if not LOGO_FILE.match(str(data.get("file", ""))):
        return None

    def size(key):
        try:
            return max(1, min(20000, int(data.get(key, 0))))
        except (TypeError, ValueError):
            return 1

    alt = data.get("alt") if isinstance(data.get("alt"), dict) else {}
    logo = {
        "file": data["file"], "w": size("w"), "h": size("h"),
        "alt": {str(lang)[:8]: str(text).strip()[:200] for lang, text in alt.items() if str(text).strip()},
        "decorative": bool(data.get("decorative")),
        "position": data.get("position") if _name(data.get("position")) in LOGO_POSITIONS else "left",
        "size": data.get("size") if _name(data.get("size")) in LOGO_SIZES else "m",
    }
    if LOGO_FILE.match(str(data.get("file_dark", ""))):
        logo.update(file_dark=data["file_dark"], w_dark=size("w_dark"), h_dark=size("h_dark"))
    if LOGO_FILE.match(str(data.get("icon", ""))):
        logo["icon"] = data["icon"]  # for the browser tab, made from the logo (media.save_icon)
    return logo


def standard():
    return {"v": 1, "preset": DEFAULT, "colors": dict(STANDARD), "font": "system", "font_scale": 100, "radius": "soft",
            "scheme": "fixed"}


def suggest_dark(colors):
    """A dark palette to a light one: the same hues, the lightness turned round."""
    background = with_lightness(colors["background"], 0.19)
    return {"background": background, "surface": with_lightness(colors["surface"], 0.23),
            "text": with_lightness(colors["text"], 0.92),
            "accent": readable(with_lightness(colors["accent"], max(lightness(colors["accent"]), 0.74)), background)}


def is_auto(theme):
    return bool(theme) and theme.get("scheme") == "auto"


def dark_side(theme):
    """The theme a device set to dark shows (scheme "auto"): the same font, corners and logo, the dark colours. It
    is a built-in theme if the colours are those of the partner (so its fixed tokens apply)."""
    colors = theme["colors_dark"]
    if theme["preset"] == ORG:  # the dark organisation design: its own tokens, the rest as on the light side
        side = {k: v for k, v in theme.items() if k not in ("colors_dark", "tokens", "tokens_dark")}
        if theme.get("tokens_dark"):
            side["tokens"] = theme["tokens_dark"]
        return {**side, "colors": colors, "scheme": "fixed"}
    partner = PAIRS.get(theme["preset"])
    preset = partner if partner and BUILTIN[partner]["colors"] == colors else "custom"
    return {**{k: v for k, v in theme.items() if k != "colors_dark"}, "preset": preset, "colors": colors, "scheme": "fixed"}


def dark_colours(theme):
    """For the editor: the dark palette of the theme, or the one it would start with."""
    if theme.get("colors_dark"):
        return theme["colors_dark"]
    return dict(BUILTIN[PAIRS[theme["preset"]]]["colors"]) if theme["preset"] in PAIRS else suggest_dark(theme["colors"])


def of(survey):
    """The survey's theme as a complete dict (the standard if it has none)."""
    return clean(getattr(survey, "theme", None)) or standard()


def records_scheme(survey):
    """The survey's look follows the device (light or dark), and which one was shown is stored (export column
    "scheme", named in the consent)."""
    return is_auto(of(survey))


def swatches(survey):
    """Colours the colour fields of single elements offer (map markers, image marks): those of the theme's
    colour world, if it has one."""
    return swatches_of(of(survey))


def swatches_of(theme):
    """The colour suggestions of a theme: an organisation design's own, else a built-in template's."""
    return theme.get("swatches") or BUILTIN.get(theme["preset"], {}).get("swatches", [])


def presets_for(user, current=None):
    """The built-in themes, open to everyone (Ralf 2026-10-19: no theme is reserved for an institution any more)."""
    return {key: _local(key, t) for key, t in BUILTIN.items()}


def _local(key, t):
    """A built-in theme with its name and note in the language of the page (a copy; BUILTIN stays German)."""
    if not _english() or key not in BUILTIN_EN:
        return t
    return {**t, "name": BUILTIN_EN[key][0], "note": BUILTIN_EN[key][1]}


def org_folder(org_id):
    """Where the logo files of an organisation lie (uploaded by its admins, Verwaltung > Design; Ralf 2026-10-16): in
    the instance folder of this server, outside the repository (a logo is a protected mark)."""
    return f"_org_{int(org_id)}"


def org_logo(org_id):
    """{"file", "w", "h"[, "file_dark", "w_dark", "h_dark"]} of an organisation, or None."""
    import json

    from flask import current_app

    if org_id is None:
        return None
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], org_folder(org_id), "logo.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if LOGO_FILE.match(str(data.get("file", ""))) else None


def save_org_logo(org_id, data):
    import json

    from flask import current_app

    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], org_folder(org_id))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "logo.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)


def is_dark(theme):
    return lightness(theme["colors"]["background"]) < 0.5


def name(theme, lang=None):
    """The name of the look: the built-in theme's, or "Eigenes Design"; in the language of the page (or `lang`)."""
    if theme["preset"] in BUILTIN:
        return BUILTIN_EN[theme["preset"]][0] if _english(lang) and theme["preset"] in BUILTIN_EN else BUILTIN[theme["preset"]]["name"]
    if theme["preset"] == ORG and theme.get("name"):
        return theme["name"]
    return "Own design" if _english(lang) else "Eigenes Design"


@functools.lru_cache(maxsize=1)
def standard_tokens():
    """The standard look is written down once, in the token block of participant.css. The Docker image serves it
    minified (tools/build_static.py: no spaces, no last semicolon), so the block is read in both forms (500 on the
    organisation pages after the deploy of 2026-10-19)."""
    path = os.path.join(os.path.dirname(__file__), "static", "participant.css")
    with open(path, encoding="utf-8") as f:
        return tokens_of_css(f.read())


def tokens_of_css(text):
    """{name: value} of the token block "body.participant, .participant-frame { ... }" of a style sheet."""
    block = re.search(r"body\.participant\s*,\s*\.participant-frame\s*\{([^}]*)\}", text).group(1)
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return {k: v.strip() for k, v in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+)", block)}


def _crowd(fg, bg, own, soft):
    """The colour of the others in the crowd figure: 3 : 1 on the stage in a light theme; in a dark one darker, until
    one's own figure (lighter than the accent) stands out by 3 : 1 (UI/UX audit 2026-10-16, 71)."""
    if lightness(bg) >= 0.5:
        return readable(soft(fg, 0.46), soft(fg, 0.035), PART_MIN)
    share = 0.30
    crowd = soft(fg, share)
    while contrast(crowd, own) < PART_MIN and share > 0.12:
        share -= 0.02
        crowd = soft(fg, share)
    return crowd


def tokens(theme):
    """Every CSS variable of participant.css for this theme, {name: value}."""
    if theme["preset"] == DEFAULT:  # the standard colours with another font or other corners
        base = standard_tokens()
        return {**base, "--on-nudge": readable(base["--nudge"], base["--nudge-soft"]), "--radius": RADII[theme["radius"]][1], "--font": FONTS[theme["font"]][1]}
    c = theme["colors"]
    bg, fg, accent, surface = c["background"], c["text"], c["accent"], c["surface"]
    dark = lightness(bg) < 0.5
    on_accent = max(("#ffffff", "#111111"), key=lambda t: contrast(t, accent))
    error, nudge, ok = readable(RED, bg), readable(AMBER, bg), readable(GREEN, bg)
    ai = readable(VIOLET, bg, PART_MIN)
    # the tinted areas carry text in the page's text colour, so they stay close to the background
    # (on a dark background a tone needs a larger share to be seen)
    soft = lambda colour, share: tint(bg, colour, min(1.0, share * (1.8 if dark else 1.0)))
    own = with_lightness(accent, max(0.0, lightness(accent) - 0.14)) if not dark else with_lightness(accent, min(1.0, lightness(accent) + 0.12))
    out = {
        "--bg": bg, "--surface": surface, "--surface-2": soft(fg, 0.035), "--surface-3": soft(fg, 0.025),
        "--hover": soft(fg, 0.05),
        "--fg": fg, "--muted": readable(tint(fg, bg, 0.36), bg),
        "--accent": accent, "--accent-soft": soft(accent, 0.10), "--on-accent": on_accent,
        "--on-accent-veil": _rgba(on_accent, 0.7), "--focus-ring": readable(accent, bg, PART_MIN),  # opaque: a faded ring fell below 3:1 (UI audit 2026-10-04, R66)
        "--border": soft(fg, 0.17), "--border-strong": soft(fg, 0.29), "--line": soft(fg, 0.08),
        "--track": soft(fg, 0.12), "--track-edge": readable(soft(fg, 0.36), bg, PART_MIN),
        # the edge of a field one types in or picks from: 3:1 on the page and on the field's own surface (D1)
        "--control-edge": readable(readable(tint(bg, fg, 0.45), bg, PART_MIN), surface, PART_MIN),
        "--error": error, "--error-soft": soft(error, 0.10), "--error-line": soft(error, 0.42),
        "--nudge": nudge, "--nudge-soft": soft(GOLD, 0.14), "--nudge-line": soft(GOLD, 0.55),
        "--ok": ok,
        "--ai": ai, "--ai-soft": soft(VIOLET, 0.12), "--ai-line": soft(VIOLET, 0.40),
        "--on-ai": max(("#ffffff", "#111111"), key=lambda t: contrast(t, ai)),
        "--good-soft": soft(ok, 0.08), "--good-mid": soft(ok, 0.14), "--good-line": soft(ok, 0.45),
        "--bad-soft": soft(error, 0.06), "--bad-mid": soft(error, 0.11), "--bad-line": soft(error, 0.40),
        "--star": GOLD, "--star-hover": tint(GOLD, "#ffffff", 0.3), "--star-off": soft(fg, 0.18),
        "--face-bg-l": "24%" if dark else "94%", "--face-on-l": "38%" if dark else "80%",
        "--face-edge-l": "68%" if dark else "42%", "--face-ink-l": "82%" if dark else "30%",
        "--data": tint(bg, accent, 0.5), "--data-soft": tint(bg, accent, 0.32), "--data-strong": tint(bg, accent, 0.66),
        "--crowd": _crowd(fg, bg, own, soft), "--own": own, "--own-shadow": _rgba(own, 0.28),
        "--partner-bg": "#ffffff" if dark else "rgba(255, 255, 255, 0)",
        "--radius": RADII[theme["radius"]][1], "--font": FONTS[theme["font"]][1],
    }
    out.update(BUILTIN.get(theme["preset"], {}).get("tokens", {}))
    out.update(theme.get("tokens") or {})  # what an organisation design fixes (checked by clean)
    if theme.get("tokens") and "--ai" not in theme["tokens"]:
        # an organisation design with a fixed colour world from before the KI-Chat: AI takes its accent, so that no
        # colour outside its world appears
        out.update({"--ai": out["--accent"], "--ai-soft": out["--accent-soft"], "--ai-line": out["--accent"],
                    "--on-ai": out["--on-accent"]})
    # text on its own tinted area (the note of a missing answer, an error row, the separated section): checked against
    # the background only it fell to 2.7-3.9 : 1 in the dark themes (UI/UX audit 2026-10-16, 58)
    for text, area in (("--nudge", "--nudge-soft"), ("--error", "--error-soft"), ("--ok", "--good-soft")):
        fitted = readable(out[text], out[area])
        if contrast(fitted, out["--bg"]) >= TEXT_MIN:  # never at the cost of the page itself (a mid-tone background)
            out[text] = fitted
    # the colour of data in the feedback figures: 3 : 1 on the page like every graphic (UI/UX audit 2026-10-16, 62)
    for key in ("--data", "--data-strong"):
        if not out[key].startswith("var("):
            out[key] = readable(out[key], out["--bg"], PART_MIN)
    out["--on-nudge"] = readable(out["--nudge"], out["--nudge-soft"])
    return out


def css(theme, show=None):
    """The <style> content for the pages of a survey; empty for the standard look (participant.css is the theme).
    With scheme "auto" the dark palette follows in a media query; `show` ("light" or "dark") shows one side
    whatever the device says (the preview of the editor)."""
    if theme is None:
        return ""
    if is_auto(theme) and show == "dark":
        return css(dark_side(theme))
    plain = {k: v for k, v in theme.items() if k not in ("logo", "colors_dark", "tokens_dark", "maps", "partners", "image_rights")}
    plain["scheme"] = "fixed"
    out = ""
    if plain != standard():  # else the standard look, with or without a logo
        out = (f"body.participant, .participant-frame {{ {_rules(theme)}; color-scheme: {'dark' if is_dark(theme) else 'light'}; }}"
               + (f" html {{ font-size: {theme['font_scale']}%; }}" if theme["font_scale"] != 100 else "")
               + (_dark_map_rule(theme) if is_dark(theme) else ""))
    if is_auto(theme) and show != "light":
        out += (f" @media (prefers-color-scheme: dark) {{ body.participant, .participant-frame "
                f"{{ {_rules(dark_side(theme))}; color-scheme: dark; }}{_dark_map_rule(theme)} }}")
    return out.strip()


# The usual map tiles, turned dark in the browser: no other map service is called for it. Inverting alone would turn
# water orange and parks purple, so the hue is turned back. Markers and controls lie on other layers and stay.
DARK_TILES = "filter: invert(1) hue-rotate(180deg) brightness(0.95) contrast(0.9)"


def dark_tile_server():
    """A tile server with dark maps, if the operator configured one (MAP_TILE_URL_DARK); then the map loads its
    tiles instead of darkening the light ones."""
    from flask import current_app, has_app_context

    return (current_app.config.get("MAP_TILE_URL_DARK") or "") if has_app_context() else ""


def _dark_map_rule(theme):
    if theme.get("maps") != "dark" or dark_tile_server():
        return ""
    return f" .p-map-canvas .leaflet-tile-pane {{ {DARK_TILES}; }} .p-map-canvas.leaflet-container {{ background: var(--surface-2); }}"


def maps_mode(survey):
    """For the script of a map question: "never" (light tiles), "always" (dark), or "auto" (dark when the device is
    set to dark). In the preview the side chosen in the preview bar counts."""
    theme = of(survey)
    if theme.get("maps") != "dark":
        return "never"
    side = preview_side(survey)
    if is_auto(theme):
        return {"dark": "always", "light": "never"}.get(side, "auto")
    return "always"


def preview_side(survey):
    """In the preview of the editor a look that follows the device can be shown light or dark whatever this
    device says: chosen in the preview bar (?scheme=light|dark|auto), remembered for the survey."""
    from flask import has_request_context, request, session

    if survey is None or not has_request_context() or not (request.endpoint or "").startswith("editor.preview"):
        return None
    key = f"preview_scheme_{survey.id}"
    if request.args.get("scheme") in ("light", "dark", "auto"):
        session[key] = request.args["scheme"]
    side = session.get(key)
    return side if side in ("light", "dark") and is_auto(of(survey)) else None


def _rules(theme):
    return "; ".join(f"{k}: {v}" for k, v in tokens(theme).items())


def logo_of(theme, lang):
    """What the page needs to show the logo: {"file", "w", "h", "alt", "position", "size"} or None. On a dark
    background the dark variant, if there is one."""
    logo = (theme or {}).get("logo")
    if not logo:
        return None
    dark = is_dark(theme) and logo.get("file_dark")
    height = LOGO_SIZES[logo["size"]][1]
    w, h = (logo["w_dark"], logo["h_dark"]) if dark else (logo["w"], logo["h"])
    out = {"file": logo["file_dark"] if dark else logo["file"], "h": height, "w": max(1, round(w * height / h)),
           "alt": "" if logo["decorative"] else logo["alt"].get(lang, ""), "position": logo["position"], "size": logo["size"]}
    if is_auto(theme) and logo.get("file_dark"):  # the browser picks the variant for a device set to dark
        out["file_on_dark"] = logo["file_dark"]
    return out


def icon_for(survey):
    """The icon of the browser tab: {"file", "type"} or None. A design that fixes how its logo is used (RWTH: never
    cut, never smaller than its minimum size) gets none."""
    theme = clean(getattr(survey, "theme", None)) if survey is not None else None
    icon = ((theme or {}).get("logo") or {}).get("icon")
    if not icon or fixed_logo_position(theme):
        return None
    return {"file": icon, "type": "image/svg+xml" if icon.endswith(".svg") else "image/png"}


def logo_for(survey, lang):
    if survey is None:
        return None
    theme, side = clean(getattr(survey, "theme", None)), preview_side(survey)
    logo = logo_of(dark_side(theme) if side == "dark" else theme, lang)
    if logo and side:
        logo.pop("file_on_dark", None)  # one side is shown, whatever the device says
    return logo


def survey_css(survey):
    """For the templates: the survey's theme as CSS ("" without a theme or without a survey)."""
    return css(clean(getattr(survey, "theme", None)), preview_side(survey)) if survey is not None else ""


def describe(theme, lang="en", shown=None, brief=False):
    """The look in one sentence, for the README of a deposit, the questionnaire document and the method section.
    `shown`: {"light": n, "dark": n, "mixed": n} of the participations, for a look that follows the device.
    brief: for the method section, without colour values and font (UI/UX audit 2026-10-05, V31): "Farbschema
    „Askurious“; heller Hintergrund"; the values stay in the README and the pre-registration."""
    theme = theme or standard()
    de = lang in ("de", "de-du")
    c = theme["colors"]
    if theme["preset"] in BUILTIN:
        what = (f"Farbschema „{name(theme, 'de')}“" if de else 'colour scheme "' + name(theme, "en") + '"')
    elif theme["preset"] == ORG:
        what = (f"Design „{name(theme)}“" if de else 'design "' + name(theme) + '"')
    else:
        what = "eigene Farben" if de else "own colours"
    if brief:
        if is_auto(theme):
            look = ("hell oder dunkel nach der Einstellung des Geräts" if de else "light or dark after the setting of the device")
            if shown and sum(shown.values()):
                look += (f" (gezeigt: hell {shown.get('light', 0)}, dunkel {shown.get('dark', 0)}, gewechselt {shown.get('mixed', 0)})" if de else
                         f" (shown: light {shown.get('light', 0)}, dark {shown.get('dark', 0)}, changed on the way {shown.get('mixed', 0)})")
        else:
            look = ("dunkler Hintergrund" if is_dark(theme) else "heller Hintergrund") if de else                    ("dark background" if is_dark(theme) else "light background")
        return "; ".join([what, look] + ([("mit Logo" if de else "with a logo")] if theme.get("logo") else []))
    what += (f" (Akzent {c['accent']}, Hintergrund {c['background']}, Text {c['text']})" if de
             else f" (accent {c['accent']}, background {c['background']}, text {c['text']})")
    parts = [what, (f"Schrift: {FONTS[theme['font']][0]}" if de else
                    "font: " + {"system": "system font", "serif": "serif", "rounded": "rounded", "grotesk": "Helvetica or Arial",
                               "mono": "monospace", "atkinson": "Atkinson Hyperlegible Next"}[theme["font"]])]
    if theme["font_scale"] != 100:
        parts.append(f"Schriftgröße {theme['font_scale']} %" if de else f"text size {theme['font_scale']} %")
    if is_auto(theme):
        d = theme["colors_dark"]
        parts.append((f"hell oder dunkel nach der Einstellung des Geräts (dunkel: Hintergrund {d['background']}, Akzent {d['accent']})" if de else
                      f"light or dark after the setting of the participant's device (dark: background {d['background']}, accent {d['accent']})"))
        if shown and sum(shown.values()):
            parts.append((f"gezeigt: hell {shown.get('light', 0)}, dunkel {shown.get('dark', 0)}, gewechselt {shown.get('mixed', 0)}" if de else
                          f"shown: light {shown.get('light', 0)}, dark {shown.get('dark', 0)}, changed on the way {shown.get('mixed', 0)}"))
    else:
        parts.append(("dunkler Hintergrund" if is_dark(theme) else "heller Hintergrund") if de else
                     ("dark background" if is_dark(theme) else "light background"))
    parts.append(("mit Logo" if theme.get("logo") else "ohne Logo") if de else ("with a logo" if theme.get("logo") else "no logo"))
    if theme.get("partners"):
        n = len(theme["partners"])
        parts.append((f"{n} Partnerlogo{'s' if n > 1 else ''} auf erster und letzter Seite" if de else
                      f"{n} partner logo{'s' if n > 1 else ''} on the first and the last page"))
    return "; ".join(parts)


def checks(theme):
    """Contrast of the pairs that matter: [{"key", "label", "ratio", "need", "ok", "severity"}]. Text below 4.5:1 is
    an error (blocks the start), parts of controls below 3:1 a warning. The thin frame of an answer option is not
    in the list: the choice itself shows in the radio button or check box, as in the standard look."""
    from app.lang import de_en

    t = tokens(theme)
    bg, surface = t["--bg"], t["--surface"]
    pairs = [
        ("text", de_en("Text auf dem Hintergrund", "Text on the background"), t["--fg"], bg, TEXT_MIN),
        ("text-surface", de_en("Text auf Flächen (Antwortoptionen)", "Text on surfaces (answer options)"), t["--fg"], surface, TEXT_MIN),
        ("text-selected", de_en("Text auf einer gewählten Option", "Text on a chosen option"), t["--fg"], t["--accent-soft"], TEXT_MIN),
        ("notice", de_en("Text in Hinweisflächen", "Text in notice panels"), t.get("--on-nudge", t["--fg"]), t["--nudge-soft"], TEXT_MIN),
        ("muted", de_en("Hinweistext auf dem Hintergrund", "Hint text on the background"), t["--muted"], bg, TEXT_MIN),
        ("button", de_en("Schrift auf dem Knopf", "Text on the button"), t["--on-accent"], t["--accent"], TEXT_MIN),
        ("accent-text", de_en("Akzentfarbe als Schrift (Links, Werte)", "Accent colour as text (links, values)"), t["--accent"], bg, TEXT_MIN),
        ("error", de_en("Fehlertext auf dem Hintergrund", "Error text on the background"), t["--error"], bg, TEXT_MIN),
        ("error-area", de_en("Fehlertext auf seiner Fläche", "Error text on its area"), t["--error"], t["--error-soft"], TEXT_MIN),
        ("nudge-area", de_en("Hinweistext auf seiner Fläche", "Note text on its area"), t["--nudge"], t["--nudge-soft"], TEXT_MIN),
        ("ok-area", de_en("Erfolgstext auf seiner Fläche", "Success text on its area"), t["--ok"], t["--good-soft"], TEXT_MIN),
        ("accent-part", de_en("Akzentfarbe an Bedienelementen (Auswahl, Regler, Fortschritt)",
                              "Accent colour on controls (choice, slider, progress)"), t["--accent"], bg, PART_MIN),
        ("focus", de_en("Markierung der Tastatur (Fokus)", "Keyboard marker (focus)"), t["--focus-ring"], bg, PART_MIN),
        ("track-edge", de_en("Rand eines unberührten Schiebereglers", "Edge of an untouched slider"), t["--track-edge"], bg, PART_MIN),
        ("control-edge", de_en("Rand von Eingabefeldern", "Edge of input fields"), t["--control-edge"], bg, PART_MIN),
    ]
    out = []
    for key, label, a, b, need in pairs:
        ratio = contrast(a, b)
        out.append({"key": key, "label": label, "ratio": ratio, "need": need, "ok": ratio >= need,
                    "severity": "error" if need == TEXT_MIN else "warning", "fg": a, "bg": b})
    return out


def problems(theme):
    """What the pre-launch check reports: (severity, message). Built-in themes and organisation designs (checked when
    they were uploaded or taken over, app/org_designs.py) are accepted as they are."""
    if theme is None:
        return []
    from app.lang import de_en

    found = []
    sides = [("", theme)] + ([(de_en(" (dunkle Darstellung)", " (dark colour scheme)"), dark_side(theme))] if is_auto(theme) else [])
    for where, side in sides:
        if side["preset"] not in BUILTIN and side["preset"] != ORG:
            found += [(c["severity"], de_en("Erscheinungsbild{where}: {label} hat einen Kontrast von {ratio} : 1, "
                                            "nötig sind mindestens {need} : 1.",
                                            "Appearance{where}: {label} has a contrast of {ratio} : 1, at least {need} : 1 is needed.",
                                            where=where, label=c["label"], ratio=num(c["ratio"]), need=num(c["need"])))
                      for c in checks(side) if not c["ok"]]
    return found


def logo_problems(theme, languages):
    """A logo needs a text for those who cannot see it, in every language of the survey, or the mark "decorative"."""
    logo = (theme or {}).get("logo")
    if not logo or logo["decorative"]:
        return []
    missing = [lang.upper() for lang in languages if not logo["alt"].get(lang)]
    if not missing:
        return []
    from app.lang import de_en

    return [("warning", de_en("Erscheinungsbild: Das Logo hat keinen Alternativtext ({langs}). Screenreader lesen "
                              "dann nichts oder den Dateinamen vor. Text eintragen oder das Logo als dekorativ kennzeichnen.",
                              "Appearance: the logo has no alternative text ({langs}). Screen readers then read nothing or the "
                              "file name. Enter a text or mark the logo as decorative.", langs=", ".join(missing)))]


def suggest(theme, key):
    """A colour close to the chosen one that passes: {"colour key": new value} for the check `key`, or {}."""
    c = theme["colors"]
    if key in ("text", "text-surface", "text-selected", "muted"):
        target = c["surface"] if key == "text-surface" else c["background"]
        return {"text": readable(c["text"], target, 7.0 if key in ("muted", "text-selected") else TEXT_MIN)}
    if key in ("accent-text", "accent-part"):
        return {"accent": readable(c["accent"], c["background"], TEXT_MIN if key == "accent-text" else PART_MIN)}
    return {}


def _de(number):
    from app.units import fixed

    return fixed(number, 1).replace(".", ",")


def num(number):
    """A contrast ratio with one decimal, in the language of the page (4,5 or 4.5)."""
    from app.lang import english
    from app.units import fixed

    return fixed(number, 1) if english() else _de(number)
