"""Organisation designs (konzept-org-designs-2026-10-19.md, Ralf 2026-10-19: A1, B1, C1, D1, E1).

Every organisation has at most one light and one dark organisation design (Organisation.design_light/design_dark), a
whole theme with preset "org" (app/theme.py). Its admins make it under Verwaltung > Design: they upload a design file,
take over a built-in template or a design they can use (made in the design editor), or take over the template of the
site admins. It is the organisation's standard: new surveys of the organisation start with it, and every member finds
it at the top of the list in the appearance of a survey. Choosing it copies it into the survey, so changing or
removing the organisation design changes no survey; members change it only in their own copy (a survey, or a design
saved from it).

Site admins may upload a template per organisation (template_light/template_dark, Administration > Organisationen):
only a starting point, so that the admins of the organisation do not begin from nothing. A new template never replaces
what they made; it becomes the organisation design only where there was none yet.
"""
import json

from app import theme
from app.extensions import db
from app.lang import de_en

MODES = ("light", "dark")
MAX_FILE = 64 * 1024  # bytes of a design file


def mode_label(mode):
    return de_en("hell", "light") if mode == "light" else de_en("dunkel", "dark")


def get(org, mode, template=False):
    """The organisation design (or the template) of one side as a complete theme, or None."""
    if org is None or mode not in MODES:
        return None
    data = getattr(org, f"{'template' if template else 'design'}_{mode}")
    found = theme.clean(data) if data else None
    return found if found is not None and found["preset"] == theme.ORG else None


def put(org, mode, data, template=False, name=None):
    """Store one side (None removes it). The theme is cleaned again and loses logo, partners and dark palette. `name`:
    the name it keeps (the editor: own colours make no "Eigenes Design" of it)."""
    field = f"{'template' if template else 'design'}_{mode}"
    if data is None:
        setattr(org, field, None)
        return None
    clean = theme.as_org(data, name or theme.name(theme.clean(data) or theme.standard()), org.id)
    setattr(org, field, clean)
    return clean


def start(org, mode):
    """What the editor shows for a side without an organisation design: the template, else a built-in template of
    that side (Askurious, light or dark)."""
    found = get(org, mode, template=True)
    if found is not None:
        return found
    key = "askurious" if mode == "light" else "askurious-dark"
    t = theme.BUILTIN[key]
    return theme.as_org({"preset": key, "colors": t["colors"]}, theme.name({"preset": key}), org.id)


class Holder:
    """One side of an organisation design as app/theme_edit.py sees it (`.theme`, `.uid`, `.languages`) and as the
    sample page asks of a survey."""

    def __init__(self, org, mode):
        self.org, self.mode = org, mode
        self.theme = get(org, mode) or start(org, mode)
        self.uid = theme.org_folder(org.id)
        self.languages, self.default_language, self.title, self.id = ["de", "en"], "de", org.name, None


def pair(org):
    """The theme new surveys of the organisation start with and members choose first: the light side (it stays light),
    or the one side there is; None without an organisation design."""
    return theme.org_pair(get(org, "light"), get(org, "dark"))


def wrong_side(data, mode):
    """Why a theme cannot be the design of this side, or "": the light one needs a light background, the dark one a
    dark background (else "automatisch" would show the same twice)."""
    dark = theme.is_dark(data)
    if mode == "light" and dark:
        return de_en("Das helle Design braucht einen hellen Hintergrund.", "The light design needs a light background.")
    if mode == "dark" and not dark:
        return de_en("Das dunkle Design braucht einen dunklen Hintergrund.", "The dark design needs a dark background.")
    return ""


def contrast_errors(data):
    """The text contrasts of the design below 4.5 : 1 (they would block the start of every survey with it)."""
    return [c["label"] for c in theme.checks(data) if not c["ok"] and c["severity"] == "error"]


def read_upload(storage, mode):
    """A design file from the form: (theme, "") or (None, why not)."""
    if storage is None or not storage.filename:
        return None, de_en("Bitte eine Designdatei auswählen.", "Please choose a design file.")
    raw = storage.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        return None, de_en("Die Datei ist zu groß für eine Designdatei.", "The file is too large for a design file.")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None, de_en("Die Datei ist keine Designdatei (JSON).", "The file is not a design file (JSON).")
    found = theme.read_design_file(data, fallback_name=(storage.filename or "").rsplit(".", 1)[0])
    if found is None:
        return None, de_en("Die Datei ist keine Designdatei von Askurious.", "The file is not an Askurious design file.")
    return check(found, mode)


def check(data, mode):
    """(theme, "") if it can be the design of this side, else (None, why not)."""
    why = wrong_side(data, mode)
    if why:
        return None, why
    errors = contrast_errors(data)
    if errors:
        return None, de_en("Zu wenig Kontrast: {what}. Bitte die Farben im Designeditor anpassen.",
                           "Not enough contrast: {what}. Please adjust the colours in the design editor.",
                           what=", ".join(errors))
    return data, ""


def sources(user, org):
    """What an admin of the organisation can take over as a side of the organisation design: the built-in templates and
    the designs they may use (their own, shared with them or with the organisation): [(key, name)]."""
    from app import designs

    out = [(f"builtin:{key}", theme.name({"preset": key})) for key in theme.presets_for(user)]
    for d in designs.available(user):
        out.append((f"design:{d.id}", d.name))
    return out


def from_source(user, org, key):
    """The theme a choice of sources() stands for, with its name; None if the user may not use it."""
    from app import designs
    from app.models import Design

    key = key or ""
    if key.startswith("builtin:") and key[8:] in theme.BUILTIN:
        t = theme.BUILTIN[key[8:]]
        return theme.as_org({**t.get("look", {}), "preset": key[8:], "colors": t["colors"]}, theme.name({"preset": key[8:]}),
                            org.id)
    if key.startswith("design:") and key[7:].isascii() and key[7:].isdigit() and len(key) < 19:
        design = db.session.get(Design, int(key[7:]))
        if designs.usable(user, design):
            return theme.as_org(design.data, design.name, org.id)
    return None


def take_template(org, overwrite=False):
    """The template of the site admins as organisation design, for each side that has one; a side the admins of the
    organisation already made stays unless `overwrite`. Returns the sides taken."""
    taken = []
    for mode in MODES:
        template = get(org, mode, template=True)
        if template is not None and (overwrite or get(org, mode) is None):
            put(org, mode, template)
            taken.append(mode)
    return taken


def template_differs(org):
    """The template has a side that is not the organisation design: the page offers "Vorlage übernehmen"."""
    for mode in MODES:
        template = get(org, mode, template=True)
        if template is not None and _look(template) != _look(get(org, mode)):
            return True
    return False


def _look(data):
    return None if data is None else {k: v for k, v in data.items() if k != "org"}


def for_user(user):
    """The organisation designs this researcher may choose in a survey: those of every organisation they belong to,
    [{"key": "org:<id>", "org": Organisation, "name", "theme", "light", "dark"}], their home organisation first."""
    from app import orgs

    mine = orgs.ids_of(user)
    out = []
    for org in orgs.all_orgs(guests=False):
        if org.id not in mine:
            continue
        both = pair(org)
        if both is None:
            continue
        light, dark = get(org, "light"), get(org, "dark")
        # what the list offers (Ralf, 2026-10-23): the light one, which stays light, and the dark one, which stays dark;
        # no third entry that follows the device
        options = [{"key": f"org:{org.id}", "name": theme.name(light or dark), "theme": both, "colors": both["colors"],
                    "note": de_en("dunkel", "dark") if theme.is_dark(both) else de_en("hell", "light")}]
        if light and dark and not theme.is_dark(both):
            dark_name = theme.name(dark)
            if dark_name == options[0]["name"]:
                dark_name = de_en("{name} dunkel", "{name} dark", name=dark_name)
            options.append({"key": f"org:{org.id}:dark", "name": dark_name, "theme": dark, "colors": dark["colors"],
                            "note": de_en("dunkel", "dark")})
        for o in options:
            o["org"] = org
        out.append({"key": f"org:{org.id}", "org": org, "name": theme.name(light or dark), "theme": both,
                    "light": light, "dark": dark, "options": options})
    home = orgs.home_id(user)
    out.sort(key=lambda entry: (entry["org"].id != home, entry["org"].name.lower()))
    return out


def chosen(user, key):
    """The choice "org:<id>" (the light side, or the only one) or "org:<id>:dark" in a survey ("org:<id>:light", offered
    for a short time on 2026-10-23, means the light side too): {"name", "theme", ...}, if
    the user belongs to that organisation."""
    key = key[:-len(":light")] if key.endswith(":light") else key
    for entry in for_user(user):
        for option in entry["options"]:
            if option["key"] == key:
                return option
    return None
