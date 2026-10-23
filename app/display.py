"""The look of the researchers' pages for one person (concept darstellung-forschende-konzept.md, 2026-10-05).

A panel "Darstellung" on the page "Konto" stores User.display: light or dark (or after the device), a colour style
and a text size. Only values from the lists below; base.html writes them onto <html> as data attributes, and the
token blocks in app.css do the rest, so nothing is computed and every style is checked by the tests
(tests/items/test_display.py). The participants' look (app/theme.py) is a different thing and never colours these pages.

The colours that carry a meaning (AI, linked elements, interviews, live votes, OK, errors, warnings) are the same in
every style; the style "Kontrast" only makes them darker (lighter in the dark).
"""
from app import brand, theme
from app.lang import de_en

MODES = {"auto": "Automatisch", "light": "Hell", "dark": "Dunkel"}
MODES_EN = {"auto": "Automatic", "light": "Light", "dark": "Dark"}
# key: (name, note); the style "RWTH" went with the RWTH design (Ralf 2026-10-19, konzept-org-designs-2026-10-19.md, E1):
# whoever had it gets the standard and once a note on the page "Konto" (dropped_look)
LOOKS = {
    "askurious": ("Askurious", "wie es kommt"),
    "kontrast": ("Kontrast", "kräftige Linien, dunklerer Text"),
}
LOOKS_EN = {"askurious": ("Askurious", "as it comes"), "kontrast": ("Contrast", "strong lines, darker text")}
DROPPED = {"rwth": ("RWTH", "RWTH")}  # former styles: the name for the note
SIZES = theme.FONT_SCALES  # 100, 112, 125: the same steps as for participants


def modes():
    """MODES in the language of the page."""
    return {key: de_en(name, MODES_EN[key]) for key, name in MODES.items()}


def clean(data):
    """Only known values; what is missing or unknown is the standard (light or dark after the brand)."""
    data = data if isinstance(data, dict) else {}
    mode = data.get("mode") if data.get("mode") in MODES else brand.default_mode()
    look = data.get("look") if data.get("look") in LOOKS else "askurious"
    try:
        size = int(data.get("size", 100))
    except (TypeError, ValueError):
        size = 100
    return {"mode": mode, "look": look, "size": size if size in SIZES else 100}


def allowed(user, look):
    """Every style is for everyone."""
    return look in LOOKS


def looks_for(user):
    """The styles this person can choose: (key, name, note)."""
    return [(key, de_en(name, LOOKS_EN[key][0]), de_en(note, LOOKS_EN[key][1])) for key, (name, note) in LOOKS.items()]


def of(user):
    """The person's settings; a former style (DROPPED) is the standard."""
    return clean(getattr(user, "display", None))


def dropped_look(user):
    """The name of a former style the person had chosen and now sees as the standard, until they save the panel."""
    saved = getattr(user, "display", None)
    look = saved.get("look") if isinstance(saved, dict) else None
    return de_en(*DROPPED[look]) if look in DROPPED else ""


def kept(user, data):
    """`data` with the person's language (app/lang.py), which lives in the same column and is set on its own."""
    saved = getattr(user, "display", None)
    lang = saved.get("lang") if isinstance(saved, dict) else None
    return dict(data, lang=lang) if lang else data


def attributes(user):
    """The data attributes for <html> (base.html): always the brand's standard (data-default-mode); the person's
    look only when signed in with something saved (a chosen language alone is no look), so that for the others the
    choice kept in this browser, else the brand's standard, holds (theme-init.js)."""
    attrs = {"data-default-mode": brand.default_mode()}
    saved = getattr(user, "display", None)
    if not getattr(user, "is_authenticated", False) or not saved or not set(saved) - {"lang"}:
        return attrs
    data = of(user)
    attrs.update({"data-mode": data["mode"], "data-look": data["look"]})
    if data["size"] != 100:
        attrs["data-size"] = str(data["size"])
    return attrs
