"""The standard of the scales of a survey (user test 2026-10-27, point 10: "einen Standard für die Umfrage definieren,
… nicht nochmal kontrollieren, ob wirklich alles 6 Stufen hat").

Survey.scale_default holds {"points", "anchors", "labeling", "label_display", "numbers"} for its Likert scales. A new
Likert scale starts from it (editor.add_item); "Auf alle Skalen anwenden" brings the scales of a draft to it; the
pre-launch check warns about a scale that differs, unless the scale is ticked as a deviation on purpose (config
"own_scale": a validated construct with another number of points, for example). Constructs from the toolbox keep
their validated points when the standard is applied to all; they only show up in the warning."""
from app.lang import de_en

KEYS = ("points", "anchors", "labeling", "label_display", "numbers")


def of(survey):
    """The standard of `survey`, or None."""
    raw = survey.scale_default if isinstance(survey.scale_default, dict) else None
    return {k: raw[k] for k in KEYS if k in raw} if raw and raw.get("points") else None


def from_form(form):
    """The standard from the settings form, None for "kein Standard"."""
    from app.items.likert import ANCHOR_CHOICES, NUMBERINGS, POINTS

    try:
        points = int(form.get("sd_points") or 0)
    except ValueError:
        points = 0
    if points not in POINTS:
        return None
    anchors = form.get("sd_anchors") if form.get("sd_anchors") in dict(ANCHOR_CHOICES) else "agree"
    return {"points": points, "anchors": anchors,
            "labeling": "endpoints" if form.get("sd_labeling") == "endpoints" else "full",
            "label_display": "once" if form.get("sd_label_display") == "once" else "each",
            "numbers": form.get("sd_numbers") if form.get("sd_numbers") in NUMBERINGS else ""}


def clean(raw):
    """A standard read from a survey file, as the settings form would store it; None for anything else."""
    if not isinstance(raw, dict):
        return None
    return from_form({"sd_points": str(raw.get("points") or ""), "sd_anchors": raw.get("anchors"),
                      "sd_labeling": raw.get("labeling"), "sd_label_display": raw.get("label_display"),
                      "sd_numbers": raw.get("numbers") or ""})


def _apply(item, std, langs):
    """Points, answer scale, labels and numbers of the standard on one Likert scale (labels in every language)."""
    config = dict(item.config or {})
    texts = {k: dict(v or {}) for k, v in (item.texts or {}).items()}
    for key in KEYS:
        config[key] = std.get(key, config.get(key))
    for lang in set(langs) | set(texts):
        texts.setdefault(lang, {})["labels"] = item.kind.default_labels(lang, std["points"], std["anchors"])
    if config.get("emoji"):
        config["emoji"] = (list(config["emoji"]) + [""] * std["points"])[:std["points"]]
    item.config, item.texts = config, texts


def apply_new(survey, item):
    """A new Likert scale starts from the standard."""
    std = of(survey)
    if std and item.type == "likert":
        _apply(item, std, survey.languages or [])


def differs(survey, item):
    """What of the standard a Likert scale does not follow: ["5 statt 6 Stufen", …]; [] if it follows or is ticked."""
    std = of(survey)
    if not std or item.type != "likert" or (item.config or {}).get("own_scale"):
        return []
    cfg = item.config or {}
    out = []
    if cfg.get("points") != std["points"]:
        out.append(de_en("{a} statt {b} Stufen", "{a} instead of {b} points", a=cfg.get("points"), b=std["points"]))
    if (cfg.get("anchors") or "agree") != std["anchors"]:
        out.append(de_en("andere Antwortskala", "another answer scale"))
    if (cfg.get("labeling") or "full") != std["labeling"]:
        out.append(de_en("andere Beschriftung", "other labels"))
    if (cfg.get("numbers") or "") != std.get("numbers", ""):
        out.append(de_en("andere Zahlen", "other numbers"))
    return out


def apply_all(survey):
    """The standard on every Likert scale of a draft that is no construct and not ticked as its own; returns the count."""
    from app import constructs

    std = of(survey)
    if not std or not survey.is_editable:
        return 0
    n = 0
    for block in survey.blocks:
        for item in block.items:
            if item.type == "likert" and not constructs.info(item) and not (item.config or {}).get("own_scale") and differs(survey, item):
                _apply(item, std, survey.languages or [])
                n += 1
    return n


def anchor_names():
    """[(key, name)] of the answer scales of a Likert matrix, for the settings form."""
    from app import items

    return items.get("likert").anchor_names()
