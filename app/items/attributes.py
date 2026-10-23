"""Attributes and levels for choice experiments (conjoint and its variants).

Shared by the item types that show profiles (one level per attribute): editing attributes,
levels and their texts, prohibited level pairs, labels, the explanation page, and images of
the "visual" attributes (one image per combination of visual levels).

config: attributes [{id, code, visual, price_attr (levels are prices, CBC), levels: [{id, hint, price (optional component of a summed price),
        value (optional number of the level for the exports, e.g. 5 for "5 km")}]}], prohibitions [{a, la, b, lb}],
        explain, hide_attr_labels, scene
texts:  attributes {id: label}, levels {id: label}, level_defs {id: definition}, attr_desc {id: text},
        explain_intro

Templates: items/_attributes/editor.html (attributes and levels), prohibitions.html,
explain_editor.html, explain.html (participant page) and macros.html (profile table).
"""
import json
import re

from app.items.base import CODE_RE, form_id, form_ids, r_flag, r_id, r_maybe, r_num, r_one, r_rows, r_text
from app.lang import Both, de_en


# a number inside a label: a minus ("-" or "−") right before it, digits joined by "." "," and apostrophes or
# (thin) no-break spaces as thousands separators ("1'000"), a plain space only before a group of three digits
NUMBER_IN_TEXT = re.compile(r"[-\u2212]?\d(?:\d|[.,'\u2019\u00a0\u202f](?=\d)| (?=\d{3}(?!\d)))*")


def numbers_in(text):
    """The numbers written in a label, as typed: "ab 3 Jahre 20 €" -> ["3", "20"]."""
    return NUMBER_IN_TEXT.findall(str(text or ""))


# a multiplier written right after the number: "10k €", "2,5 Tsd. €", "1,2 Mio. €" (not "10 kg", "5 km")
MULTIPLIER = re.compile(r"\s?(k|tsd\.?|tausend|mio\.?|millionen|million|mrd\.?|milliarden|milliarde)(?![^\W\d_])",
                        re.IGNORECASE)
FACTORS = {"k": 1e3, "tsd": 1e3, "tausend": 1e3, "mio": 1e6, "million": 1e6, "millionen": 1e6,
           "mrd": 1e9, "milliarde": 1e9, "milliarden": 1e9}


def parse_number(text):
    """The number in a label, read like the numbers of conditions (German style, conditions.read_number):
    "1.299,90 €" -> 1299.9, "$49.99" -> 49.99, "−5 €" -> -5, "1'000 CHF" -> 1000, "0.500" -> 0.5,
    "1,299" -> 1.299, "10k €" -> 10000, "1,5 Mio. €" -> 1500000; None without a number or with several
    ("ab 3 Jahre 20 €": which one is the price? price_problem names it)."""
    from app.conditions import read_number

    found = list(NUMBER_IN_TEXT.finditer(str(text or "")))
    if len(found) != 1:
        return None
    value = read_number(found[0].group())
    unit = MULTIPLIER.match(str(text), found[0].end())
    if value is not None and unit:
        value *= FACTORS[unit.group(1).lower().rstrip(".")]
    return value


def price_problem(text):
    """Why a price level cannot be read for sure, or None."""
    from app import units
    from app.conditions import _COMMA_THOUSANDS

    found = numbers_in(text)
    if not found:
        return de_en("„{text}“ enthält keine Zahl", "“{text}” contains no number", text=text)
    if len(found) > 1:
        return de_en("„{text}“ enthält mehrere Zahlen ({found}); eine Preisstufe braucht genau eine",
                     "“{text}” contains several numbers ({found}); a price level needs exactly one",
                     text=text, found=", ".join(found))
    value = parse_number(text)
    if value is None:
        return de_en("„{text}“: „{n}“ ist keine lesbare Zahl", "“{text}”: “{n}” is not a readable number", text=text, n=found[0])
    if _COMMA_THOUSANDS.fullmatch(found[0]):
        whole = found[0].replace(",", "")
        return de_en("„{text}“ ist nicht eindeutig ({v} oder {whole}?) und wird als {v} gelesen; bitte ohne "
                     "Tausendertrennzeichen schreiben, z. B. {whole}",
                     "“{text}” is ambiguous ({v} or {whole}?) and is read as {v}; please write it without a thousands "
                     "separator, e.g. {whole}", text=text, whole=whole,
                     v=units.shown_plain(value, units.page_lang()))  # 1.299 in English (Mathe-Audit 2026-10-06, Z-7)
    return None


def _number(value, known=None):
    """A surcharge typed in the editor, read in the language of the page ("1.299,99" in German; units.read_entry,
    Mathe-Audit 2026-10-06, Z-4: it was 0 without a word); 0 for no number."""
    from app import units

    return units.read_entry(value, default=0.0, known=known)


def level_value(text, known=None):
    """The value typed for a level, read in the language of the page with a note on "1,000" in German
    (units.read_entry); None if empty or not a number."""
    from app import units

    value = units.read_entry(text, known=known)
    if value is None or abs(value) > 1e9:
        return None
    return int(value) if value.is_integer() else value


# Settings of a survey file (see items/base.py). The editor has no upper bound for attributes and levels; a file
# gets one far above any design that can be estimated.
MAX_ATTRIBUTES, MAX_LEVELS = 30, 50
ATTRIBUTE_RULES = {
    "attributes": r_rows(MAX_ATTRIBUTES, id=r_id, code=r_text(64, 1), visual=r_maybe(r_flag), price_attr=r_maybe(r_flag),
                         ordered=r_maybe(r_flag),
                         levels=r_rows(MAX_LEVELS, id=r_id, hint=r_maybe(r_text(300)), price=r_maybe(r_num(-1e9, 1e9)),
                                        value=r_maybe(r_num(-1e9, 1e9)))),
    "prohibitions": r_rows(5000, unique=None, a=r_id, la=r_id, b=r_id, lb=r_id),
    "explain": r_flag, "hide_attr_labels": r_flag, "level_images": r_one("", "add", "replace"),
}


LEVEL_IMAGE = "l:"  # asset key of a level's image: "l:<level id>"
LEVEL_MODES = ("replace", "add")  # display "Tabelle mit Bildern je Stufe": the image instead of the text, or above it


def display_from_form(form, displays):
    """(display, level_images) from the editor's three choices: "table" (text), "levels" (a table whose levels show
    images: config display "table" plus level_images "replace"/"add") and "image" (one image per option, with the
    tick rest_table the attributes not in the image as a table below it: "both"). "both" itself is accepted too
    (files, older forms)."""
    d = form.get("display")
    if d == "levels":
        return "table", form.get("level_mode") if form.get("level_mode") in LEVEL_MODES else "replace"
    if d == "image" and form.get("rest_table"):
        d = "both"
    return (d if d in displays else "table"), ""


class AttributeLevels:
    """Mixin for ItemType subclasses with config["attributes"]."""

    # --- editing -----------------------------------------------------------------------

    def _price_kept(self, item, attr, lang, old, new):
        """True if the wording `new` of a level of the attribute marked "Preis (Zahlen)" is not taken: once the
        structure is fixed (the survey runs or has real answers) the price read from the wording of the default language
        stays, or every earlier choice would be analysed with another price (audit 2026-10-12, N-9). Another wording
        with the same number ("20 €" -> "20,00 €") is taken."""
        survey = getattr(item, "survey", None)
        default = getattr(survey, "default_language", lang)
        if not attr.get("price_attr") or lang != default or not old or parse_number(old) is None:
            return False
        if parse_number(new) == parse_number(old):
            return False
        from app import units

        units.note(de_en("Preisstufe „{old}“: Die Zahl bleibt, solange die Befragung läuft oder echte Antworten hat; "
                         "frühere Wahlen werden mit diesem Preis ausgewertet. Ein anderer Wortlaut mit derselben Zahl ist "
                         "möglich; „{new}“ wurde nicht übernommen.",
                         "Price level “{old}”: the number stays while the survey runs or has real answers; earlier "
                         "choices are analysed with this price. Another wording with the same number is possible; "
                         "“{new}” was not taken.", old=old, new=new))
        return True

    def update_attributes(self, item, form, lang, structural, config, lt):
        """Read attributes, levels, their texts, prohibitions and the explanation page from the
        editor form into config and lt (the texts of `lang`), both changed in place. Structure
        (codes, levels, "visual", prohibitions) only if `structural`, texts always."""
        attr_labels, level_labels = dict(lt.get("attributes", {})), dict(lt.get("levels", {}))
        level_defs = dict(lt.get("level_defs", {}))  # per level, per language: explanation and image context
        known = {a["id"]: a for a in config.get("attributes", [])}
        attributes, revalued = [], {}
        for raw, code, label in zip(form.getlist("attr_id"), form.getlist("attr_code"), form.getlist("attr_label")):
            # the form's id names the fields of its levels; what is stored is an id as the file import takes it: it
            # goes into the editor's HTML templates and the field names (audit 2026-10-13, XS-1)
            aid = form_id(raw)
            code, label = code.strip(), label.strip()
            ids, defs = form_ids(form.getlist(f"lvl_{raw}_id")), form.getlist(f"lvl_{raw}_def")
            if defs:  # texts, not structure: may change any time, also in translation views
                for lid, d in zip(ids, defs + [""] * len(ids)):
                    level_defs[lid] = d.strip()[:500]
            if aid in known and not structural:
                attr_labels[aid] = label or attr_labels.get(aid, "")
                if f"lvl_{aid}_value" in form:
                    revalued[aid] = self._with_values(known[aid], form)
                for lid, llabel in zip(ids, form.getlist(f"lvl_{aid}_label")):
                    if llabel.strip():
                        if not self._price_kept(item, known[aid], lang, level_labels.get(lid), llabel.strip()):
                            level_labels[lid] = llabel.strip()
                continue
            if not structural or not (code or label):
                continue
            levels = []
            before = {l["id"]: l for l in (known.get(aid) or {}).get("levels", [])}  # stored so far: no new note
            prices = form.getlist(f"lvl_{raw}_price")  # only with a summed price (e.g. ACBC)
            values = form.getlist(f"lvl_{raw}_value")  # only CBC and ACBC
            for n, (lid, llabel, hint) in enumerate(zip(ids, form.getlist(f"lvl_{raw}_label"),
                                                        form.getlist(f"lvl_{raw}_hint"))):
                if llabel.strip() or hint.strip():
                    level = {"id": lid or form_id(lid), "hint": hint.strip()[:300]}
                    if n < len(prices) and prices[n].strip():
                        level["price"] = _number(prices[n], (before.get(lid) or {}).get("price"))
                    value = level_value(values[n] if n < len(values) else "", (before.get(lid) or {}).get("value"))
                    if value is not None:
                        level["value"] = value
                    levels.append(level)
                    level_labels[level["id"]] = llabel.strip()
            attributes.append({"id": aid, "code": code or f"A{len(attributes) + 1}",
                               "visual": raw in form.getlist("attr_visual"), "levels": levels})
            if raw in form.getlist("attr_price"):  # price with numbers as levels (CBC analysis)
                attributes[-1]["price_attr"] = True
            attr_labels[aid] = label
        if revalued:  # new dicts, so that the stored config counts as changed
            config["attributes"] = [revalued.get(a["id"], a) for a in config.get("attributes", [])]
        if structural:
            config["attributes"] = attributes
            # forbidden level pairs across two attributes, "aid:lid|aid:lid" (grid in the editor)
            config["prohibitions"] = self._parse_prohibitions(form.getlist("prohib"), attributes)
        lt["attributes"], lt["levels"] = attr_labels, level_labels
        kept = {l["id"] for a in (config.get("attributes") or []) for l in a.get("levels", [])}
        lt["level_defs"] = {lid: d for lid, d in level_defs.items() if d and lid in kept}
        # presentation only (no effect on the design): may change at any time, in the default language view
        if lang == item.survey.default_language:
            config["hide_attr_labels"] = bool(form.get("hide_attr_labels"))
            config["explain"] = bool(form.get("explain"))
            # "Stufen sind geordnet": the results keep the order of the levels instead of sorting them by value;
            # presentation only, so it may change at any time (UI audit 2026-10-12, U23)
            if form.get("attr_ordered_shown"):
                ordered = set(form.getlist("attr_ordered"))
                config["attributes"] = [{**a, "ordered": a["id"] in ordered} for a in config.get("attributes", [])]
        lt["explain_intro"] = form.get("explain_intro", lt.get("explain_intro", "")).strip()[:3000]
        descs = dict(lt.get("attr_desc", {}))
        for aid in [a["id"] for a in config.get("attributes", [])]:
            if f"attr_desc_{aid}" in form:
                descs[aid] = form.get(f"attr_desc_{aid}", "").strip()[:500]
        lt["attr_desc"] = descs

    @staticmethod
    def _with_values(attr, form):
        """A copy of the attribute with the values typed for its levels (exports only, no effect on the design):
        they may change once answers exist."""
        typed = dict(zip(form.getlist(f"lvl_{attr['id']}_id"), form.getlist(f"lvl_{attr['id']}_value")))
        levels = []
        for stored in attr.get("levels", []):
            level = {k: v for k, v in stored.items() if k != "value" or stored["id"] not in typed}
            value = level_value(typed.get(level["id"]), stored.get("value"))
            if value is not None:
                level["value"] = value
            levels.append(level)
        return {**attr, "levels": levels}

    def level_values(self, attr):
        """The values of an attribute's levels (None where a level has none), or None if no level has one."""
        values = [l.get("value") for l in attr.get("levels", [])]
        return values if any(v is not None for v in values) else None

    @staticmethod
    def _parse_prohibitions(values, attributes):
        levels = {a["id"]: {l["id"] for l in a.get("levels", [])} for a in attributes}
        out, seen = [], set()
        for v in values:
            try:
                left, right = v.split("|")
                a, la = left.split(":")
                b, lb = right.split(":")
            except ValueError:
                continue
            if a != b and la in levels.get(a, ()) and lb in levels.get(b, ()) and (a, la, b, lb) not in seen:
                seen.add((a, la, b, lb))
                out.append({"a": a, "la": la, "b": b, "lb": lb})
        return out

    # --- structure -------------------------------------------------------------------------

    def level_counts(self, item):
        return [len(a.get("levels", [])) for a in item.config.get("attributes", [])]

    def prohibitions(self, item):
        """Forbidden level pairs as ((attr index, level index), (attr index, level index)), only valid ones."""
        attrs = item.config.get("attributes", [])
        pos = {a["id"]: (n, {l["id"]: m for m, l in enumerate(a.get("levels", []))}) for n, a in enumerate(attrs)}
        out = []
        for p in item.config.get("prohibitions", []):
            if p.get("a") in pos and p.get("b") in pos and p["a"] != p["b"]:
                (ia, la), (ib, lb) = pos[p["a"]], pos[p["b"]]
                if p.get("la") in la and p.get("lb") in lb:
                    out.append(((ia, la[p["la"]]), (ib, lb[p["lb"]])))
        return sorted(set(out))

    def profile_allowed(self, item, profile):
        return not any(profile[a] == la and profile[b] == lb for (a, la), (b, lb) in self.prohibitions(item))

    # --- labels ------------------------------------------------------------------------

    def attributes(self, item, lang=None, order=None):
        t = item.t(lang)
        rows = []
        for a in item.config.get("attributes", []):
            rows.append({**a, "label": t.get("attributes", {}).get(a["id"], a["code"]),
                         "description": t.get("attr_desc", {}).get(a["id"], ""),
                         "level_labels": [t.get("levels", {}).get(l["id"], "") for l in a.get("levels", [])],
                         "level_defs": [t.get("level_defs", {}).get(l["id"], "") for l in a.get("levels", [])],
                         "level_images": [self.level_image(item, l["id"]) for l in a.get("levels", [])]})
        if order:
            rank = {aid: n for n, aid in enumerate(order)}
            rows.sort(key=lambda r: rank.get(r["id"], len(rank)))
        return rows

    def levels_ordered(self, item, attr):
        """Whether the results keep the order of an attribute's levels: as ticked in the editor, else when its levels
        read as a scale (choice_model.levels_look_ordered: numbers, one open end allowed)."""
        if attr.get("ordered") is not None:
            return bool(attr["ordered"])
        from app.choice_model import levels_look_ordered

        labels = item.t(item.survey.default_language).get("levels") or {}
        return levels_look_ordered([labels.get(l["id"], "") for l in attr.get("levels", [])])

    def price_attribute(self, item):
        """Index of the attribute marked "price (numbers)", or None."""
        return next((n for n, a in enumerate(item.config.get("attributes", [])) if a.get("price_attr")), None)

    def level_numbers(self, item, attr):
        """The levels of an attribute as numbers (default language labels), None if one is not a number."""
        labels = (item.t(item.survey.default_language).get("levels") or {})
        values = [parse_number(labels.get(l["id"], "")) for l in item.config["attributes"][attr].get("levels", [])]
        return None if any(v is None for v in values) else values

    def validate_price_attribute(self, item):
        marked = [a for a in item.config.get("attributes", []) if a.get("price_attr")]
        if len(marked) > 1:
            return [de_en("Höchstens ein Merkmal als Preis (Zahlenwerte) markieren",
                          "Mark at most one attribute as price (numeric values)")]
        if marked:
            labels = item.t(item.survey.default_language).get("levels") or {}
            found = [price_problem(labels.get(lv["id"], "")) for lv in marked[0].get("levels", [])]
            found = [p for p in found if p]
            if found:
                return [de_en("Merkmal {code}: als Preis markiert, aber nicht jede Stufe ist eindeutig eine Zahl: {found}",
                              "Attribute {code}: marked as price, but not every level is clearly a number: {found}",
                              code=marked[0]['code'], found="; ".join(found))]
        return []

    # --- images --------------------------------------------------------------------------

    def uses_images(self, item):
        return item.config.get("display") in ("image", "both")

    def visual_attributes(self, item):
        return [a for a in item.config.get("attributes", []) if a.get("visual")]

    def image_key(self, item, profile):
        """Asset key: the levels of the visual attributes only."""
        attrs = item.config.get("attributes", [])
        return json.dumps({a["code"]: a["levels"][profile[n]]["id"] for n, a in enumerate(attrs) if a.get("visual")},
                          sort_keys=True)

    def image_prompt(self, item, key):
        """The base prompt (scene) plus the image hints of the chosen levels: English and for the AI only. Names,
        levels and definitions are what participants read, in the language of the survey; none of them goes into
        the prompt (a level without a hint adds nothing, the pre-launch check names it). The style is added by
        image_styles."""
        from app import image_styles

        chosen = json.loads(key)
        attrs = {a["code"]: a for a in self.visual_attributes(item)}
        hints = []
        for code, lid in chosen.items():
            level = next((l for l in attrs.get(code, {}).get("levels", []) if l["id"] == lid), {})
            hint = (level.get("hint") or "").strip()
            if hint:
                hints.append(hint if hint.endswith((".", "!", "?")) else hint + ".")
        scene = (item.config.get("scene") or "").strip()
        scene = " ".join([scene + ("" if scene.endswith((".", "!", "?")) or not scene else ".")] + hints).strip()
        return image_styles.compose(item.survey, scene, text_free=True)

    # images per level: a small picture above the level in the table (a brand's logo, a seal), uploaded on the
    # images page; asset key LEVEL_IMAGE + level id, next to the keys of the combination images

    def level_image(self, item, level_id):
        """The file name of the level's image, or None."""
        # vignette factors (vignettes.Factors) use these helpers too but have no images
        return next((a.filename for a in getattr(item, "assets", ()) if a.key == LEVEL_IMAGE + level_id
                     and a.status == "approved"), None)

    def level_images_on(self, item):
        """"replace" or "add" with the display "Tabelle mit Bildern je Stufe", else ""."""
        return item.config.get("level_images", "") if item.config.get("display") == "table" else ""

    def level_image_gaps(self, item):
        """[(attribute code, levels with image, levels)] of attributes where some levels have an image and
        others not, with the display "Tabelle mit Bildern je Stufe": the levels without one would stand out."""
        if not self.level_images_on(item):
            return []
        gaps = []
        for a in item.config.get("attributes", []):
            levels = a.get("levels", [])
            n = sum(1 for l in levels if self.level_image(item, l["id"]))
            if 0 < n < len(levels):
                gaps.append((a["code"], n, len(levels)))
        return gaps

    def missing_hints(self, item):
        """[(attribute code, level label)] of the levels shown in images that have no image hint (the visual
        attributes, or all with the images per level)."""
        t = item.t(item.survey.default_language)
        attrs = self.prompt_attributes(item)
        return [(a["code"], t.get("levels", {}).get(l["id"], "") or l["id"])
                for a in attrs for l in a.get("levels", []) if not (l.get("hint") or "").strip()]

    def prompt_attributes(self, item):
        """The attributes whose image hints go into prompts: all with images per level, else the visual ones."""
        return item.config.get("attributes", []) if self.level_images_on(item) else self.visual_attributes(item)

    def image_page_keys(self, item):
        """The images of the images page: one per level with "Tabelle mit Bildern je Stufe", else one per combination
        of the visual levels."""
        if self.level_images_on(item):
            return [LEVEL_IMAGE + l["id"] for a in item.config.get("attributes", []) for l in a.get("levels", [])]
        return self.required_keys(item)

    def _level_of(self, item, key):
        """(attribute, level number) of a level image key, or (None, None)."""
        lid = key[len(LEVEL_IMAGE):]
        for a in item.config.get("attributes", []):
            for n, l in enumerate(a.get("levels", [])):
                if l["id"] == lid:
                    return a, n
        return None, None

    def key_label(self, item, key):
        t = item.t(item.survey.default_language)
        if key.startswith(LEVEL_IMAGE):
            a, n = self._level_of(item, key)
            if a is None:
                return "?"
            return f"{t.get('attributes', {}).get(a['id'], a['code'])}: {t.get('levels', {}).get(a['levels'][n]['id'], '?')}"
        return " · ".join(t.get("levels", {}).get(lid, "?") for lid in json.loads(key).values())

    def prompt_for_key(self, item, key):
        if key.startswith(LEVEL_IMAGE):  # a small picture for one level: its English image hint only, no scene
            from app import image_styles

            a, n = self._level_of(item, key)
            hint = ((a["levels"][n].get("hint") or "") if a else "").strip()
            scene = f"A single small picture, like an icon or a seal, that stands for: {hint}" if hint else ""
            return image_styles.compose(item.survey, scene, text_free=True)
        return self.image_prompt(item, key)

    def asset_for_key(self, item, key):
        return next((a for a in item.assets if a.key == key and a.status == "approved"), None)

    def has_image_set(self, item):
        return True

    def image_size(self, item):
        return "1024x1024"

    image_set_label = Both("Conjoint-Bilder", "Conjoint images")
    image_uploads = True  # the image page offers an upload per combination, next to the AI
    def image_upload_side(self, item, key):
        """Uploads are made as small as the generated images (media.fit_still); a level's picture is small."""
        return 600 if key.startswith(LEVEL_IMAGE) else 1400

    def upload_name(self, item, key):
        """The file name (without extension) that assigns an upload to this combination when several are
        uploaded at once: variable name and level number of each visual attribute, e.g. "look-2_colour-1"."""
        if key.startswith(LEVEL_IMAGE):
            a, n = self._level_of(item, key)
            return f"{a['code']}-{n + 1}".lower() if a else ""
        chosen = json.loads(key)
        parts = []
        for a in self.visual_attributes(item):
            ids = [l["id"] for l in a.get("levels", [])]
            if chosen.get(a["code"]) in ids:
                parts.append(f"{a['code']}-{ids.index(chosen[a['code']]) + 1}")
        return "_".join(parts).lower()

    # --- checks ----------------------------------------------------------------------------

    def validate_attributes(self, item):
        """Problems with attributes and levels (at least one attribute, two labelled levels each)."""
        attrs = item.config.get("attributes", [])
        problems = []
        if not attrs:
            problems.append(de_en("mindestens ein Merkmal", "at least one attribute"))
        labels = item.text("levels") or {}
        for a in attrs:
            if len(a.get("levels", [])) < 2:
                problems.append(de_en("Merkmal {code}: mindestens zwei Stufen", "Attribute {code}: at least two levels", code=a["code"]))
            elif any(not labels.get(l["id"]) for l in a["levels"]):
                problems.append(de_en("Merkmal {code}: Stufe ohne Beschriftung", "Attribute {code}: level without a label", code=a["code"]))
            if not CODE_RE.match(a["code"]):
                problems.append(de_en("Merkmal {code}: ungültiger Variablenname", "Attribute {code}: invalid variable name", code=a["code"]))
        if not self.has_real_answers(item):  # then the codes are locked with the design
            # the codes are columns of the long format, next to its own columns, and part of the HB columns
            fixed = ["resp_id", "source"] + [c.lower() for c in self.long_columns(item)]
            codes = [a["code"].lower() for a in attrs]
            taken = sorted({a["code"] for a in attrs if fixed.count(a["code"].lower()) > 1})
            if len(set(codes)) != len(codes):
                problems.append(de_en("Variablennamen der Merkmale sind nicht eindeutig",
                                      "The variable names of the attributes are not unique"))
            elif taken:
                problems.append(de_en("Variablenname {names}: So heißt schon eine Spalte des Langformats (z. B. task, "
                                      "alt, chosen, price oder <Code>_label); bitte anders benennen",
                                      "Variable name {names}: a column of the long format already has this name (e.g. "
                                      "task, alt, chosen, price or <Code>_label); please rename",
                                      names=", ".join(taken)))
        return problems

    def validate_images(self, item):
        """Problems with the images (display with images: visual attributes, at most 500, all approved)."""
        problems = []
        if self.uses_images(item):
            if not self.visual_attributes(item):
                problems.append(de_en("Darstellung mit Bild, aber kein Merkmal ist „im Bild“ markiert",
                                      "Display with an image, but no attribute is marked “in the image”"))
            else:
                n = len(self.required_keys(item))
                if n > 500:
                    problems.append(de_en("{n} Bilder nötig: zu viele (höchstens 500); weniger Merkmale ins Bild nehmen",
                                          "{n} images needed: too many (at most 500); put fewer attributes into the image", n=n))
                missing = [k for k in self.required_keys(item) if not self.asset_for_key(item, k)]
                if missing and n <= 500:
                    problems.append(de_en("{k} von {n} Bildern fehlen oder sind nicht freigegeben",
                                          "{k} of {n} images are missing or not approved", k=len(missing), n=n))
        if self.level_images_on(item):
            if not any(self.level_image(item, l["id"]) for a in item.config.get("attributes", []) for l in a.get("levels", [])):
                problems.append(de_en("Anzeige „Tabelle mit Bildern je Stufe“, aber noch keine Stufe hat ein Bild",
                                      "Display “Table with images per level”, but no level has an image yet"))
            elif self.level_images_on(item) == "replace":  # a level without image would show its text among pictures
                problems += [de_en("Merkmal {code}: {k} von {total} Stufen ohne Bild (das Bild ersetzt den Text)",
                                   "Attribute {code}: {k} of {total} levels without an image (the image replaces the text)",
                                   code=code, k=total - n, total=total)
                             for code, n, total in self.level_image_gaps(item)]
        return problems
