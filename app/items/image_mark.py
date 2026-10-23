"""Mark an image: participants set spots or draw lines (highlighter) on an image.

The image comes from the image pipeline (upload or AI generation, approval, loop variables),
see items/image.py. Mode "points": each click/tap sets a spot; mode "lines": each stroke is a
free-hand line like a highlighter. The number of marks can be limited (min/max; empty max =
unlimited); "↶" takes back the last mark. Coordinates are stored relative to the image
(0..1 from the top left), so they fit any display size and can be pooled into a heatmap.
Export: number of marks plus the coordinates as JSON.

Hot spot (Ralf, 2026-10-08: a variant, not an element of its own): the researcher draws rectangles over the image
(config "areas" = [{"id", "code", "box": [x, y, w, h] relative to the image}], texts "areas" = {id: label}).
Mode "areas": participants tap the areas that apply (stored as [area codes]; export <code>_<area> = 1/0); mode
"rate": a tap goes round "gefällt" (+1), "gefällt nicht" (-1) and back to neutral (stored as {area code: 1 | -1};
export <code>_<area> = 1/0/-1). <code>_n counts the chosen or rated areas. The areas lie over the image as buttons
(participant.js [data-hot]); without JavaScript a list of check boxes or choices stands below the image instead."""
import json
import math
import re

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import Unfit, r_colour, r_id, r_int, r_one, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import CODE_RE, AnswerError, form_ids, in_lang, load_json, new_id, plain, since_note, unseen_since, whole
from app.rows import NOT_SHOWN
from app.items.image import Image
from app.lang import Both, de_en, english
from app.ui_i18n import tr

MODES = {"points": "Punkte setzen", "lines": "Linien zeichnen (Textmarker)",
         "areas": "Bereiche auswählen (Hot Spot)", "rate": "Bereiche bewerten: gefällt / gefällt nicht (Hot Spot)"}
MODES_EN = {"points": "Set points", "lines": "Draw lines (highlighter)",  # the editor in English (app/lang.py)
            "areas": "Choose areas (hot spot)", "rate": "Rate areas: like / dislike (hot spot)"}
AREA_MODES = ("areas", "rate")
MAX_AREAS = 40
DEFAULT_COLOR = "#ffd400"  # yellow
MAX_MARKS = 200          # also the cap for "unlimited"
MAX_LINE_POINTS = 400    # per line, after thinning in the browser
HEX = re.compile(r"^#[0-9a-fA-F]{6}\Z")


def _xy(p):
    """A coordinate pair clamped to the image, or None."""
    try:
        x, y = float(p[0]), float(p[1])
    except (TypeError, ValueError, IndexError, KeyError, OverflowError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):  # NaN passes min/max unchanged and is no valid JSON
        return None
    return [round(min(max(x, 0.0), 1.0), 4), round(min(max(y, 0.0), 1.0), 4)]


def box_of(value):
    """A rectangle [x, y, w, h] relative to the image from "x,y,w,h" (the editor) or a list (a survey file), cut to
    the image; None if it is no rectangle or smaller than 1 % of the image in either direction."""
    parts = value.split(",") if isinstance(value, str) else value
    if not isinstance(parts, list) or len(parts) != 4 or any(isinstance(v, bool) for v in parts):
        return None
    try:
        x, y, w, h = (float(v) for v in parts)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    w, h = min(max(w, 0.0), 1.0 - x), min(max(h, 0.0), 1.0 - y)
    if w < 0.01 or h < 0.01:
        return None
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def _box_rule(value):
    box = box_of(value)
    if box is None:
        raise Unfit
    return box


AREA_RULES = r_rows(MAX_AREAS, id=r_id, code=r_text(30, 1), box=_box_rule)


@register
class ImageMark(Image):
    key = "image_mark"
    read_keys = ("mode",)  # points would be read as areas (audit 2026-10-10, N-3)
    since_lists = ("areas",)  # an area added later: -77 for the participations before (audit 2026-10-12, N-5)
    import_rules = {**Image.import_rules, "mode": r_one(*MODES), "min": r_int(1, MAX_MARKS, none=True),
                    "max": r_int(1, MAX_MARKS, none=True), "color": r_colour, "areas": AREA_RULES}
    label = Both("Bild markieren", "Image marking")
    short = Both("Stellen auf einem Bild markieren", "Mark spots on an image")
    description = Both("Auf einem Bild Stellen antippen, Linien zeichnen oder Bereiche antippen (Heatmap, Textmarker, Hot Spot)",
                       "Tap spots, draw lines or tap areas on an image (heatmap, highlighter, hot spot)")
    order = 62
    fact_sheet = FactSheet(
        "spatial",
        keywords=["Hot Spot", "Hotspot", "Hot-Spot", "Heatmap", "Klickbereiche", "Click areas", "Textmarker",
                  "Highlighter", "Bereiche", "Areas"],
        can=[("Punkte setzen oder Linien zeichnen (Textmarker).", "Set points or draw lines (highlighter)."),
             ("Auswertung als Heatmap.", "Analysed as a heatmap."),
             ("Variante Hot Spot: vorher festgelegte Bereiche antippen oder als „gefällt“ / „gefällt nicht“ bewerten, "
              "eine Spalte je Bereich.",
              "Variant hot spot: tap areas set beforehand or rate them as “like” / “dislike”, one column per area.")],
        good_for=[("Was an einem Bild auffällt, gefällt oder stört: Werbung, Webseite, Verpackung",
                   "What stands out, pleases or bothers in an image: an advert, a website, a package")],
        not_for=[("Orte auf einer echten Landkarte (dann Karte mit Markern)",
                  "Places on a real map (then map with markers)")],
        designs=["cross", "ab"],
        examples=[("Markieren Sie, was Ihnen an dieser Startseite zuerst auffällt.",
                   "Mark what you notice first on this home page.")],
    )
    collects_answer = True
    @property
    def modes(self):
        """The kinds of marking, named in the language of the page (the editor's list)."""
        return MODES_EN if english() else MODES

    def default_config(self):
        return {**super().default_config(), "mode": "points", "min": None, "max": None, "color": DEFAULT_COLOR}

    def default_texts(self, lang):
        return {"question": "", "alt": ""}

    # --- hot spot: areas set beforehand ------------------------------------------------------------------

    def read_shape(self, item):
        """The mode and the rectangle of every area: an area moved would let earlier answers stand for another part
        of the image (audit 2026-10-12, N-5)."""
        return {**super().read_shape(item),
                **{f"box|{a['id']}": a.get("box") for a in (item.config or {}).get("areas") or [] if "id" in a}}

    def on_areas(self, item):
        """True for the hot spot modes ("areas", "rate")."""
        return (item.config or {}).get("mode") in AREA_MODES

    def areas(self, item, lang=None):
        """[{"id", "code", "box", "label"}] in the order of the editor."""
        labels = item.t(lang).get("areas") or {}
        return [{**a, "label": labels.get(a["id"], "")} for a in (item.config or {}).get("areas", [])]

    def _areas_from_form(self, item, form, lang, structural):
        """The rows of the editor's list of areas: area_id, area_code, area_label, area_box ("x,y,w,h"). A row
        without a rectangle is dropped; an empty code gets the next free number."""
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        labels = dict(lt.get("areas") or {})
        rows = list(zip(form_ids(form.getlist("area_id")), form.getlist("area_code"), form.getlist("area_label"), form.getlist("area_box")))
        if structural:
            areas = []
            since = {a["id"]: a["since"] for a in (item.config or {}).get("areas", []) if a.get("since")}
            for aid, code, label, box in rows:
                box = box_of(box)
                if box is None or len(areas) >= MAX_AREAS:
                    continue
                areas.append({"id": aid if aid and len(aid) <= 40 else new_id(), "code": code.strip()[:30], "box": box,
                              **({"since": since[aid]} if aid in since else {})})
                labels[areas[-1]["id"]] = " ".join(label.split())[:200]
            used = {a["code"] for a in areas if a["code"]}
            n = 1
            for a in areas:
                if not a["code"]:
                    while str(n) in used:
                        n += 1
                    a["code"] = str(n)
                    used.add(a["code"])
            item.config = {**item.config, "areas": areas}
        else:
            known = {a["id"] for a in (item.config or {}).get("areas", [])}
            for aid, _, label, _ in rows:
                if aid in known:
                    labels[aid] = " ".join(label.split())[:200]
        ids = {a["id"] for a in (item.config or {}).get("areas", [])}
        lt["areas"] = {k: v for k, v in labels.items() if k in ids}
        texts[lang] = lt
        item.texts = texts

    def area_column(self, item, area):
        return f"{item.code}_{area['code']}"

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "question": form.get("question", "").strip(),
                       "alt": form.get("alt", "").strip()}
        item.texts = texts
        config = dict(item.config)
        if structural:
            config["mode"] = form.get("mode") if form.get("mode") in MODES else "points"
            for k in ("min", "max"):
                v = form.get(k, "").strip()
                config[k] = min(whole(v), MAX_MARKS) if (whole(v) or 0) > 0 else None
        if lang == item.survey.default_language:  # presentation only: may change at any time
            color = form.get("color", "")
            config["color"] = color if HEX.match(color) else config.get("color", DEFAULT_COLOR)
        item.config = config
        if "area_id" in form:
            self._areas_from_form(item, form, lang, structural)

    def limits(self, item):
        c = item.config
        lo, hi = c.get("min") or 0, c.get("max") or MAX_MARKS
        return lo, max(hi, lo or 1)

    def validate(self, item):
        problems = super().validate(item)
        lo, hi = item.config.get("min"), item.config.get("max")
        if lo and hi and lo > hi:
            problems.append(de_en("Mindestanzahl ist größer als Höchstanzahl", "minimum number is greater than maximum number"))
        if self.on_areas(item):
            areas = self.areas(item)
            if not areas:
                problems.append(de_en("noch keine Bereiche: Rechtecke auf dem Bild aufziehen", "no areas yet: draw rectangles on the image"))
            if any(not a["label"] for a in areas):
                problems.append(de_en("Bereich ohne Beschriftung", "area without a label"))
            codes = [a["code"] for a in areas]
            if len(set(codes)) != len(codes):
                problems.append(de_en("Codes der Bereiche sind nicht eindeutig", "area codes are not unique"))
            bad = [c for c in codes if c.startswith("_") or c == "n" or not CODE_RE.match(f"{item.code}_{c}")]
            if bad and not self.has_real_answers(item):
                problems.append(de_en("Codes der Bereiche werden Teil der Variablennamen und dürfen nur Buchstaben, Ziffern und "
                                      "Unterstriche enthalten, nicht „n“ (nicht: {bad})",
                                      "area codes become part of the variable names and may only contain letters, digits and "
                                      "underscores, not “n” (not: {bad})", bad=", ".join(bad[:3])))
            if lo and lo > len(areas):
                problems.append(de_en("Mindestens {lo} verlangt, es gibt aber nur {n} Bereiche",
                                      "at least {lo} required, but there are only {n} areas", lo=lo, n=len(areas)))
        return problems

    def _parse(self, item, raw):
        data = load_json(raw or "[]", [])
        if not isinstance(data, list):
            return []
        marks = []
        for m in data[:MAX_MARKS]:
            if item.config.get("mode") == "lines":
                line = [p for p in (_xy(q) for q in (m if isinstance(m, list) else [])[:MAX_LINE_POINTS]) if p]
                if line:
                    marks.append(line)
            else:
                p = _xy(m)
                if p:
                    marks.append(p)
        if item.config.get("mode") == "lines" and sum(map(len, marks)) > 5000:
            # Keep every stroke and its endpoints, uniformly downsampling its interior. Total <= 5000.
            base = sum(min(2, len(line)) for line in marks)
            interior = sum(max(0, len(line) - 2) for line in marks)
            budget = 5000 - base
            bounded = []
            for line in marks:
                n = min(2, len(line)) + (budget * max(0, len(line) - 2) // interior)
                bounded.append([line[round(j * (len(line) - 1) / (n - 1))] for j in range(n)]
                               if n > 1 else line[:1])
            marks = bounded
        return marks

    def _read_areas(self, item, form):
        """The areas a page sent: [codes] (mode "areas", field i<id> once per chosen area) or {code: 1 | -1}
        (mode "rate", field i<id>_<area code> = "1", "-1" or "0")."""
        codes = [a["code"] for a in (item.config or {}).get("areas", [])]
        if item.config.get("mode") == "rate":
            out = {}
            for code in codes:
                value = form.get(f"i{item.id}_{code}", "")
                if value in ("1", "-1"):
                    out[code] = int(value)
            return out
        sent = set(form.getlist(f"i{item.id}"))
        return [code for code in codes if code in sent]

    def _parse_areas(self, item, form, lang):
        chosen = self._read_areas(item, form)
        lo, hi = self.limits(item)
        rate = item.config.get("mode") == "rate"
        if not chosen:
            if item.required:
                raise AnswerError(tr(lang, "Bitte bewerten Sie mindestens einen Bereich.", "Please rate at least one area.") if rate
                                  else tr(lang, "Bitte wählen Sie mindestens einen Bereich.", "Please choose at least one area."))
            # the page was sent without a choice: an answer "none of them" (0 for every area), so that the shares
            # of the results page stand over everyone asked, as in the plain multiple choice (audit 2026-10-10, N-8)
            return {} if rate else []
        if len(chosen) < lo:
            raise AnswerError(tr(lang, "Bitte bewerten Sie mindestens {n} Bereiche.", "Please rate at least {n} areas.", n=lo) if rate
                              else tr(lang, "Bitte wählen Sie mindestens {n} Bereiche.", "Please choose at least {n} areas.", n=lo))
        if item.config.get("max") and len(chosen) > hi:
            raise AnswerError(tr(lang, "Bitte bewerten Sie höchstens {n} Bereiche.", "Please rate at most {n} areas.", n=hi) if rate
                              else tr(lang, "Bitte wählen Sie höchstens {n} Bereiche.", "Please choose at most {n} areas.", n=hi))
        return chosen

    def parse_answer(self, item, form, lang="de", meta=None):
        if self.on_areas(item):
            return self._parse_areas(item, form, lang)
        marks = self._parse(item, form.get(f"i{item.id}"))
        if not marks and form.get(f"i{item.id}_none"):
            # "Ich kann hier nichts markieren": an answer, so a required question does not stop someone without a
            # pointing device (WCAG 2.1.1; UI audit 2026-10-04, R54); exported as 0 marks and "[]"
            return []
        lo, hi = self.limits(item)
        lines = item.config.get("mode") == "lines"

        def at_least(n):
            if lines:
                return (tr(lang, "Bitte markieren Sie mindestens {n} Linien.", "Please mark at least {n} lines.", n=n) if n > 1
                        else tr(lang, "Bitte markieren Sie mindestens eine Linie.", "Please mark at least one line."))
            return (tr(lang, "Bitte markieren Sie mindestens {n} Stellen.", "Please mark at least {n} spots.", n=n) if n > 1
                    else tr(lang, "Bitte markieren Sie mindestens eine Stelle.", "Please mark at least one spot."))
        if not marks:
            if item.required:
                raise AnswerError(at_least(max(lo, 1)))
            return None
        if len(marks) < lo:
            raise AnswerError(at_least(lo))
        return marks[:hi]

    def display_value(self, item, form, meta=None):
        if self.on_areas(item):
            return self._read_areas(item, form) or None
        return self._parse(item, form.get(f"i{item.id}")) or None

    def pooled(self, item):
        """The marks so far for the overview in the editor: the data pool of the results page (completed real
        participations, without what quality assurance marks; no preview, test or dummy data)."""
        from app import balance

        marks, n = [], 0
        if self.on_areas(item):  # the results page shows the areas with their shares
            return {"n": 0, "marks": []}
        for _, a in balance.answers(item.block.survey, str(item.id)):
            if a:
                n += 1
                marks.extend(a)
        return {"n": n, "marks": marks[:20000]}

    def export_columns(self, item):
        if self.on_areas(item):
            rate = item.config.get("mode") == "rate"
            question = plain(item.text("question") or "")
            return ([(f"{item.code}_n", "Anzahl bewerteter Bereiche" if rate else "Anzahl gewählter Bereiche")]
                    + [(self.area_column(item, a), f"{question}: {plain(a['label'])}".lstrip(": ")) for a in self.areas(item)])
        what = "Linien" if item.config.get("mode") == "lines" else "Punkte"
        return [(f"{item.code}_n", f"Anzahl {what} (0 = „kann hier nichts markieren“)"),
                (item.code, f"{what} als JSON, relativ zum Bild (0–1, von links oben)")]

    def fixed_summary(self, item, answer, lang):
        """The marks in words for an answer fixed by feedback (UI/UX audit 2026-10-17, W-A03): the chosen or rated
        areas by their labels, else the number of points or lines."""
        if self.on_areas(item):
            if item.config.get("mode") == "rate":
                rated = answer if isinstance(answer, dict) else {}
                words = {1: tr(lang, "gefällt", "like"), -1: tr(lang, "gefällt nicht", "dislike")}
                return [(plain(a["label"]) or a["code"], words.get(rated.get(a["code"]), tr(lang, "neutral", "neutral")))
                        for a in self.areas(item, lang)]
            chosen = [plain(a["label"]) or a["code"] for a in self.areas(item, lang)
                      if isinstance(answer, list) and a["code"] in answer]
            return [("", ", ".join(chosen) if chosen else tr(lang, "Kein Bereich gewählt", "No area chosen"))]
        marks = answer if isinstance(answer, list) else []
        if not marks:
            return [("", tr(lang, "Ich kann hier nichts markieren", "I cannot mark anything here"))] if answer == [] else []
        if item.config.get("mode") == "lines":
            return [("", tr(lang, "Gezogene Linien: {n}", "Lines drawn: {n}", n=len(marks)))]
        return [("", tr(lang, "Markierte Stellen: {n}", "Spots marked: {n}", n=len(marks)))]

    def export_values(self, item, answer):
        if self.on_areas(item):
            areas = (item.config or {}).get("areas", [])
            if answer is None or not isinstance(answer, (list, dict)):
                return {f"{item.code}_n": None, **{self.area_column(item, a): None for a in areas}}
            if isinstance(answer, dict):  # rate: not rated = 0
                return {f"{item.code}_n": len(answer), **{self.area_column(item, a): answer.get(a["code"], 0) for a in areas}}
            return {f"{item.code}_n": len(answer), **{self.area_column(item, a): int(a["code"] in answer) for a in areas}}
        marks = answer or []
        return {f"{item.code}_n": len(marks) if answer is not None else None,
                item.code: json.dumps(marks, separators=(",", ":")) if answer is not None else None}

    # --- hot spot: codebook, results, dummy data --------------------------------------------------------

    def export_response_values(self, item, answer, response):
        """Hot spot: -77 (not shown) for the areas added after the version this participation answered, not 0
        (audit 2026-10-12, N-5)."""
        if not self.on_areas(item) or not isinstance(answer, (list, dict)):
            return {}
        return {f"{item.code}_{code}": NOT_SHOWN for code in unseen_since(item, "areas", response)}

    def missing_codes(self, item, column):
        area = self._area_of(item, column) if self.on_areas(item) else None
        return (NOT_SHOWN,) if area is not None and isinstance(area.get("since"), int) else ()

    def _area_of(self, item, column):
        return next((a for a in (item.config or {}).get("areas", []) if self.area_column(item, a) == column), None)

    def column_level(self, item, column):
        if self.on_areas(item) and self._area_of(item, column):
            return "ordinal" if item.config.get("mode") == "rate" else "nominal"
        return super().column_level(item, column)

    def result_column(self, item, column):
        """Results page: the areas in the picture and table of results(), not one by one."""
        if self.on_areas(item):
            return None, "", None
        return super().result_column(item, column)

    def codebook_info(self, item, column, lang, ui=None):
        if not self.on_areas(item):
            return None
        rate = item.config.get("mode") == "rate"
        if column == f"{item.code}_n":
            return "Anzahl", "", [in_lang(ui, "Zahl der bewerteten Bereiche", "number of rated areas") if rate
                                  else in_lang(ui, "Zahl der gewählten Bereiche", "number of chosen areas")]
        area = self._area_of(item, column)
        if area is None:
            return None
        x, y, w, h = area["box"]
        where = in_lang(ui, "Bereich {x:.0f}–{x2:.0f} % von links, {y:.0f}–{y2:.0f} % von oben",
                        "area {x:.0f}–{x2:.0f} % from the left, {y:.0f}–{y2:.0f} % from the top",
                        x=x * 100, x2=(x + w) * 100, y=y * 100, y2=(y + h) * 100)
        later = [since_note(area, ui)] if since_note(area, ui) else []
        if rate:
            return "Bewertung", in_lang(ui, "1 = gefällt; 0 = neutral; -1 = gefällt nicht",
                                         "1 = liked; 0 = neutral; -1 = disliked"), [
                where, in_lang(ui, "0 heißt auch: nicht angetippt", "0 also means: not tapped")] + later
        return "Auswahl", in_lang(ui, "1 = gewählt; 0 = nicht gewählt", "1 = chosen; 0 = not chosen"), [where] + later

    def results(self, item, table, lang, passes=()):
        """Results page: per area the share of persons who chose it (mode "areas") or who liked and disliked it
        (mode "rate"), over the persons who sent the page (also without a choice: 0 for every area); in a loop every
        pass counts as one answer."""
        suffixes = [f"_{n}" for n in passes] or [""]
        answers = []
        for row in table:
            for sfx in suffixes:
                if row.get(f"{item.code}_n{sfx}") not in (None, ""):
                    answers.append({a["code"]: row.get(f"{self.area_column(item, a)}{sfx}") for a in item.config.get("areas", [])})
        n = len(answers)

        def share(code, value):
            seen = [a for a in answers if str(a.get(code)) != str(NOT_SHOWN)]  # an area added later (N-5)
            return sum(1 for a in seen if str(a.get(code)) == value) / len(seen) if seen else None

        rate = item.config.get("mode") == "rate"
        rows = []
        for a in self.areas(item, lang):
            x, y, w, h = a["box"]
            row = {"label": plain(a["label"]) or a["code"], "code": a["code"],
                   "x": x * 100, "y": y * 100, "w": w * 100, "h": h * 100}
            if rate:
                row.update(like=share(a["code"], "1"), dislike=share(a["code"], "-1"))
                row["net"] = row["like"] - row["dislike"] if row["like"] is not None else None
            else:
                row["share"] = share(a["code"], "1")
            rows.append(row)
        return {"persons": n, "rate": rate, "rows": rows}

    def dummy_fields(self, item, rng, meta, answers=None):
        """Hot spot modes: a random valid answer; the other modes keep the generic dummy answer (app/dummy.py)."""
        if not self.on_areas(item):
            return None
        codes = [a["code"] for a in (item.config or {}).get("areas", [])]
        if not codes:
            return {}
        lo, hi = self.limits(item)
        k = rng.randint(min(max(lo, 1), len(codes)), min(max(hi, 1), len(codes)))
        picked = rng.sample(codes, k)
        if item.config.get("mode") == "rate":
            return {f"i{item.id}_{code}": rng.choice(("1", "-1")) for code in picked}
        return {f"i{item.id}": picked}  # a list: one field per chosen area (dummy._Form)
