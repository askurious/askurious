"""Editing a theme: the steps behind the page "Erscheinungsbild" of a survey and the editor of a design.

Both edit the same thing (a theme with its logo files) for a different owner: a survey keeps its theme in
Survey.theme and its files in the survey's upload folder, a design in Design.data and its own folder. The
functions here work on a "holder": anything with `.theme` (the stored theme), `.uid` (the upload folder) and
`.languages`. They change no database row: each returns what the caller stores, and the names of files the
caller deletes once the change is committed.
"""
from flask import render_template

from app import media, theme
from app.lang import de_en


class Refused(Exception):
    """The step is not possible; the message is for the researcher (in the language of the page)."""


def from_form(values, holder):
    """The theme a form or the query of the live preview describes. The logo files and partner logos are not part
    of the form (they are uploaded on their own); the texts and choices for the logo are."""
    stored = theme.clean(holder.theme) or {}
    logo = dict(stored.get("logo") or {})
    if logo:
        if "logo_position" in values:
            logo.update(position=values.get("logo_position"), size=values.get("logo_size"))
        if "logo_fields" in values:  # the form on the page (the live preview sends position and size only)
            logo.update(decorative=bool(values.get("logo_decorative")),
                        alt={lang: values.get(f"logo_alt_{lang}", "") for lang in holder.languages})
    colors = {key: values.get(key, "") for key in theme.COLOURS}
    org = {}
    if values.get("preset") == theme.ORG and stored.get("preset") == theme.ORG:
        # an organisation design stays one while its colours stay (logo, scheme or size changed); other colours make
        # it an own design (app/org_designs.py)
        if {k: str(v).strip().lower() for k, v in colors.items()} == stored["colors"]:
            org = {k: stored[k] for k in ("name", "tokens", "fixed", "logo_position", "swatches", "org") if k in stored}
            if stored.get("tokens_dark") and stored.get("colors_dark") == {
                    key: str(values.get(f"dark_{key}", "")).strip().lower() for key in theme.COLOURS}:
                org["tokens_dark"] = stored["tokens_dark"]
    return theme.clean({
        **org,
        "preset": values.get("preset") if org or values.get("preset") != theme.ORG else None,
        "colors": colors,
        "font": values.get("font"), "font_scale": values.get("font_scale"), "radius": values.get("radius"),
        "scheme": values.get("scheme"), "colors_dark": {key: values.get(f"dark_{key}", "") for key in theme.COLOURS},
        "maps": values.get("maps"), "partners": stored.get("partners"),
        "logo": logo, "image_rights": stored.get("image_rights"),
    })


def ignored_by_template(saved, form):
    """What the form asked for and the template does not allow: ["Schrift", "Ecken"] (a page from before, or
    without JavaScript; the fields are disabled otherwise)."""
    fixed = saved.get("fixed") or theme.BUILTIN.get(saved["preset"], {}).get("fixed", {})
    return [label for field, label in (("font", de_en("Schrift", "Font")), ("radius", de_en("Ecken", "Corners")))
            if field in fixed and form.get(field) not in (None, "", fixed[field])]


def sample_page(holder, args, lang, short=False):
    """A page with typical elements in the theme given by the query (the live preview while choosing colours);
    without a query in the stored theme. "show": one side of a look that follows the device. short: without the
    questions that show an error and a reminder (the small view on a design's tile)."""
    shown = from_form(args, holder) if args.get("accent") else theme.clean(holder.theme)
    show = args.get("show") if args.get("show") in ("light", "dark") and theme.is_auto(shown) else None
    logo = theme.logo_of(theme.dark_side(shown) if show == "dark" else shown, lang)
    if logo and show:
        logo.pop("file_on_dark", None)
    return render_template("participant/theme_sample.html", survey=holder, lang=lang, sample_css=theme.css(shown, show),
                           sample_logo=logo, short=short)


def check(holder, args):
    """Contrast of the theme given by the query, for the list next to the live preview."""
    shown = from_form(args, holder) or theme.standard()
    rows = [{**c, "ratio": theme.num(c["ratio"]), "need": theme.num(c["need"]), "fix": theme.suggest(shown, c["key"]) if not c["ok"] else {}}
            for c in theme.checks(shown)]
    if theme.is_auto(shown):  # the dark side must be readable as well
        side = theme.dark_side(shown)
        rows += [{**c, "label": de_en("Dunkel: {label}", "Dark: {label}", label=c["label"]), "ratio": theme.num(c["ratio"]),
                  "need": theme.num(c["need"]),
                  "fix": {"dark_" + k: v for k, v in theme.suggest(side, c["key"]).items()} if not c["ok"] else {}}
                 for c in theme.checks(side)]
    return {"name": theme.name(shown), "dark": theme.is_dark(shown), "auto": theme.is_auto(shown), "checks": rows}


def _current(holder):
    return theme.clean(holder.theme) or theme.standard()


def upload_logo(holder, form, files):
    """A logo, or its variant for dark backgrounds (variant=dark). Returns (theme, message, files to delete)."""
    current = _current(holder)
    logo = dict(current.get("logo") or {})
    dark = form.get("variant") == "dark"
    f = files.get("file")
    if f is None or not f.filename:
        raise Refused(de_en("Bitte eine Datei auswählen.", "Please choose a file."))
    if not form.get("rights"):
        raise Refused(de_en("Bitte bestätigen Sie, dass Sie das Logo verwenden dürfen.", "Please confirm that you may use the logo."))
    if dark and not logo:
        raise Refused(de_en("Bitte zuerst das Logo für helle Hintergründe hochladen.", "Please upload the logo for light backgrounds first."))
    try:
        saved = media.save_logo(holder, f.read(media.LOGO_MAX_BYTES + 1))
    except ValueError as e:
        raise Refused(str(e)) from e
    old = [logo.get("file_dark" if dark else "file")]
    if dark:
        logo.update(file_dark=saved["file"], w_dark=saved["w"], h_dark=saved["h"])
    else:
        logo.update(file=saved["file"], w=saved["w"], h=saved["h"])
        if logo.get("icon") != logo.get("file"):
            old.append(logo.get("icon"))
        logo["icon"] = media.save_icon(holder, saved["file"])  # for the browser tab, if the logo is about square
    keep = (logo.get("file"), logo.get("file_dark"), logo.get("icon"))
    message = (de_en("Logo hochgeladen. Bitte noch den Alternativtext eintragen.", "Logo uploaded. Please also enter the alternative text.")
               if not dark and not logo.get("alt") and not logo.get("decorative") else de_en("Logo hochgeladen.", "Logo uploaded."))
    return theme.clean({**current, "logo": logo}), message, [n for n in old if n and n not in keep]


def remove_logo(holder, form):
    current = _current(holder)
    logo = dict(current.get("logo") or {})
    if form.get("variant") == "dark":
        gone = [logo.pop("file_dark", None)]
        logo.pop("w_dark", None), logo.pop("h_dark", None)
    else:  # without the logo there is no dark variant of it and no icon
        gone, logo = [logo.get("file"), logo.get("file_dark"), logo.get("icon")], None
    return theme.clean({**current, "logo": logo}), de_en("Logo entfernt.", "Logo removed."), [n for n in gone if n]


def partner_logo(holder, form, files):
    """Add the logo of a funder or partner (upload), or remove one (remove=<position>)."""
    current = _current(holder)
    partners = list(current.get("partners") or [])
    gone = []
    if form.get("remove") is not None:
        index = form.get("remove", type=int)
        if index is None or not 0 <= index < len(partners):
            raise Refused("")
        gone = [partners.pop(index)["file"]]
        message = de_en("Partnerlogo entfernt.", "Partner logo removed.")
    else:
        f = files.get("file")
        alt = " ".join((form.get("alt") or "").split())
        if len(partners) >= theme.MAX_PARTNERS:
            raise Refused(de_en("Mehr als {n} Partnerlogos sind nicht vorgesehen.", "More than {n} partner logos are not possible.",
                                n=theme.MAX_PARTNERS))
        if f is None or not f.filename:
            raise Refused(de_en("Bitte eine Datei auswählen.", "Please choose a file."))
        if not alt:
            raise Refused(de_en("Bitte den Namen des Partners angeben (Screenreader lesen ihn statt des Logos vor).",
                                "Please give the partner's name (screen readers read it instead of the logo)."))
        if not form.get("rights"):
            raise Refused(de_en("Bitte bestätigen Sie, dass Sie das Logo verwenden dürfen.", "Please confirm that you may use the logo."))
        try:
            saved = media.save_logo(holder, f.read(media.LOGO_MAX_BYTES + 1))
        except ValueError as e:
            raise Refused(str(e)) from e
        partners.append({"file": saved["file"], "w": saved["w"], "h": saved["h"], "alt": alt})
        message = de_en("Partnerlogo hinzugefügt. Es steht auf der ersten und der letzten Seite der Befragung.",
                        "Partner logo added. It appears on the first and the last page of the survey.")
    return theme.clean({**current, "partners": partners}), message, gone


def logo_colours(holder):
    """The main colours of the logo, as suggestions for the accent colour."""
    logo = _current(holder).get("logo")
    try:
        colours = media.logo_colours(holder, logo["file"]) if logo else []
    except (OSError, ValueError):
        colours = []
    return {"colours": [{"colour": c, "on_white": theme.num(theme.contrast(c, "#ffffff"))} for c in colours]}
