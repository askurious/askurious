"""Rating of a single statement with different visualisations:
slider (continuous), smileys (5/7 faces, sad -> happy), stars (5/7/10), number row (e.g. 0-10).

The slider has no starting value: the handle appears on first touch, so there is no
anchor at the start position and "not answered" stays distinguishable from "middle".
"""
import math

from app import units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_int, r_num, r_one  # rules for survey files, see base.py
from app.items.base import DONT_KNOW, DONT_KNOW_CODE, AnswerError, ItemType, dont_know, read_cfg
from app.lang import Both, de_en
from app.ui_i18n import tr

STYLES = {"slider": "Schieberegler", "smileys": "Smileys", "stars": "Sterne", "numbers": "Zahlenreihe"}
SMILEY_POINTS = (5, 7)
STAR_POINTS = (5, 7, 10)
NUMBERS_POINTS = 21  # a row of numbers: at most this many buttons, in the editor and from a survey file (M-12)


def _num(value, default=None):
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default  # "nan", "inf": no bound, step or answer


@register
class Rating(ItemType):
    key = "rating"
    read_keys = ("dont_know",)  # switched off it would make -98 a value (audit 2026-10-10, N-3)
    import_rules = {"style": r_one(*STYLES), "points": r_int(2, 10), "min": r_num(-1e9, 1e9), "max": r_num(-1e9, 1e9),
                    "step": r_num(1e-6, 1e9), "show_value": r_flag, "reverse": r_flag,
                    "dont_know": r_flag}

    def import_problems(self, item):
        c = read_cfg(item)
        if c["style"] == "numbers" and self._numbers_problem(c):  # one button per whole number, as the editor checks
            return [self._numbers_problem(c)]
        if c["style"] in ("smileys", "stars") and c["points"] not in (SMILEY_POINTS if c["style"] == "smileys" else STAR_POINTS):
            return [de_en("Diese Zahl von Stufen gibt es für Smileys oder Sterne nicht.", "This number of points does not exist for smileys or stars.")]
        return []
    label = Both("Bewertung", "Rating")
    short = Both("Regler, Smileys, Sterne oder Zahlen", "Slider, smileys, stars or numbers")
    description = Both("Schieberegler, Smileys, Sterne oder Zahlenreihe", "Slider, smileys, stars or a row of numbers")
    order = 35
    fact_sheet = FactSheet(
        "scales",
        keywords=["NPS", "Net Promoter", "Net Promoter Score", "Weiterempfehlung", "Recommendation", "0 bis 10",
                  "0 to 10", "Sterne", "Stars", "Smileys", "Schieberegler", "Slider", "Zahlenreihe"],
        can=[("Schieberegler, Smileys (5 oder 7), Sterne (5, 7 oder 10) oder Zahlenreihe (zum Beispiel 0 bis 10).",
              "Slider, smileys (5 or 7), stars (5, 7 or 10) or a row of numbers (for example 0 to 10).")],
        good_for=[("Eine einzelne Gesamtbewertung, z. B. Zufriedenheit oder Weiterempfehlung",
                   "A single overall evaluation, e.g. satisfaction or recommendation"),
                  ("Projektionen im Delphi: Wahrscheinlichkeit, Wirkung, Wünschbarkeit",
                   "Projections in a Delphi: likelihood, impact, desirability")],
        not_for=[("Mehrere Aussagen auf derselben Skala (dann Likert-Skala)",
                  "Several statements on the same scale (then Likert scale)")],
        designs=["cross", "ab", "within", "vignettes", "delphi"],
        examples=[("Wie wahrscheinlich ist es, dass Sie uns weiterempfehlen? (0 bis 10)",
                   "How likely is it that you would recommend us? (0 to 10)")],
    )
    levels = ("ordinal", "metric")
    max_numbers = NUMBERS_POINTS  # the page draws a longer row as a slider (participant.html)

    def one_click(self, item):
        # a slider has no clear "done" moment; the tick "weiß nicht" sends nothing by itself
        return read_cfg(item).get("style") != "slider" and not dont_know(item)

    def default_level(self, item):
        return "metric" if read_cfg(item).get("style") == "slider" else "ordinal"

    def default_config(self):
        return {"style": "smileys", "points": 5, "min": 0, "max": 100, "step": 1, "show_value": True, "reverse": False,
                "dont_know": False}  # a tick "weiß nicht", set apart; counts as missing (V41)

    def default_texts(self, lang):
        return {"question": "", "left": "", "right": ""}

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        texts[lang] = {
            **texts.get(lang, {}),
            "question": form.get("question", "").strip(),
            "left": form.get("left", "").strip()[:100],
            "right": form.get("right", "").strip()[:100],
        }
        item.texts = texts
        if not structural:
            return
        style = form.get("style") if form.get("style") in STYLES else "smileys"
        config = {**item.config, "style": style, "show_value": bool(form.get("show_value")),
                  "reverse": bool(form.get("reverse")), "dont_know": bool(form.get("dont_know"))}
        if style == "smileys":
            p = int(_num(form.get("points"), 5))
            config["points"] = p if p in SMILEY_POINTS else 5
        elif style == "stars":
            p = int(_num(form.get("points"), 5))
            config["points"] = p if p in STAR_POINTS else 5
        else:
            # in the language of the page with a note on "1,000" in German (units.read_entry; Mathe-Audit 2026-10-06, Z-4)
            old = item.config or {}
            lo = units.read_entry(form.get("min"), default=0, known=old.get("min"))
            hi = units.read_entry(form.get("max"), default=100 if style == "slider" else 10, known=old.get("max"))
            step = units.read_entry(form.get("step"), default=1, known=old.get("step")) if style == "slider" else 1
            # a row of numbers that took over the range 0 to 100 of a slider or of the start (smileys): back to the
            # standard 0 to 10; 101 buttons were drawn as a chain of digits (Ralf, 2026-10-21, interview sjur42)
            if (style == "numbers" and old.get("style") != "numbers" and lo is not None and hi is not None
                    and hi - lo > NUMBERS_POINTS - 1):
                lo, hi = 0, 10
            config.update(min=lo, max=hi, step=step if step and step > 0 else 1)
        item.config = config

    # --- scale ---------------------------------------------------------------------

    def answer_shape(self, item):
        """The values an answer can take and the reversal stay once real answers exist (ItemType.answer_shape); the
        style may change as long as the values stay (smileys and stars with the same number of points)."""
        return {**super().answer_shape(item),
                "scale": {"bounds": list(self.bounds(item)), "reverse": bool(read_cfg(item).get("reverse"))}}

    def bounds(self, item):
        c = read_cfg(item)
        if c["style"] in ("smileys", "stars"):
            return 1, c.get("points", 5)
        return c.get("min", 0), c.get("max", 100)

    def is_integer(self, item):
        c = read_cfg(item)
        return c["style"] != "slider" or float(c.get("step", 1)).is_integer() and float(c.get("min", 0)).is_integer()

    def on_scale(self, item, value):
        """Is the value one the page offers? Whole steps for stars, smileys and the row of numbers, the grid
        of the slider otherwise (2.5 of 5 stars cannot be clicked)."""
        c = read_cfg(item)
        if c["style"] != "slider":
            return float(value).is_integer()
        try:
            step = float(c.get("step") or 1)
            steps = (float(value) - float(c.get("min", 0))) / step
        except (TypeError, ValueError, ZeroDivisionError):
            return True
        return abs(steps - round(steps)) < 1e-6

    def values(self, item):
        lo, hi = self.bounds(item)
        return list(range(int(lo), int(hi) + 1))

    def scale_values(self, item, column):
        return self.values(item) if read_cfg(item)["style"] != "slider" and self.is_integer(item) else None

    @staticmethod
    def _numbers_problem(c):
        """What is wrong with the range of a row of numbers, "" if nothing: whole bounds, "from" below "to", at most
        NUMBERS_POINTS buttons; the same rule for the editor and a survey file (audit 2026-10-12, M-12: the file
        allowed 101 points, the editor 21, and 0.5 to 5.5 was cut to 0 to 5)."""
        lo, hi = c.get("min"), c.get("max")
        if lo is None or hi is None or lo >= hi:
            return de_en("„von“ muss kleiner als „bis“ sein", "“from” must be smaller than “to”")
        if not (float(lo).is_integer() and float(hi).is_integer()):
            return de_en("Eine Zahlenreihe braucht ganze Zahlen für „von“ und „bis“",
                         "A row of numbers needs whole numbers for “from” and “to”")
        if hi - lo > NUMBERS_POINTS - 1:
            return de_en("Zahlenreihe höchstens {n} Stufen (sonst Schieberegler verwenden)",
                         "row of numbers at most {n} points (use a slider otherwise)", n=NUMBERS_POINTS)
        return ""

    def smiley(self, index, points):
        """Geometry for face `index` (0-based): mouth curvature -1 (sad) .. 1 (happy), hue red -> green."""
        t = index / (points - 1)
        curve = -1 + 2 * t
        return {"ctrl_y": round(25 + 7 * curve, 2), "hue": round(4 + 116 * t)}

    def validate(self, item):
        c = read_cfg(item)
        problems = []
        if c["style"] == "numbers" and self._numbers_problem(c):
            problems.append(self._numbers_problem(c))
        elif c["style"] == "slider":
            lo, hi = c.get("min"), c.get("max")
            if lo is None or hi is None or lo >= hi:
                problems.append(de_en("„von“ muss kleiner als „bis“ sein", "“from” must be smaller than “to”"))
        lo, hi = self.bounds(item)
        if dont_know(item) and lo is not None and hi is not None and lo <= DONT_KNOW_CODE <= hi:
            problems.append(de_en("„weiß nicht“ steht im Export als {code}, das liegt in der Skala; bitte den Bereich ändern",
                                  "“don't know” is {code} in the export, which lies within the scale; please change the range",
                                  code=DONT_KNOW_CODE))
        return problems

    def missing_codes(self, item, column):
        return (DONT_KNOW_CODE,) if dont_know(item) else ()

    # --- answers ---------------------------------------------------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        if dont_know(item) and form.get(f"i{item.id}_dk"):  # the tick wins over a value left on the page
            return DONT_KNOW
        raw = form.get(f"i{item.id}", "").strip()
        if raw == "":
            if item.required:
                raise AnswerError(tr(lang, "Bitte geben Sie eine Bewertung ab.", "Please give a rating."))
            return None
        value = _num(raw)
        lo, hi = self.bounds(item)
        if value is None or not (lo <= value <= hi) or not self.on_scale(item, value):
            raise AnswerError(tr(lang, "Ungültige Bewertung.", "Invalid rating."))
        return int(value) if self.is_integer(item) and float(value).is_integer() else value

    def display_value(self, item, form, meta=None):
        if form.get(f"i{item.id}_dk"):
            return DONT_KNOW
        v = _num(form.get(f"i{item.id}", ""))
        return None if v is None else (int(v) if float(v).is_integer() else v)

    def pipeable(self, item):
        return True

    def pipe_text(self, item, answer, lang):
        from app.items.base import number_text

        return number_text(answer, lang) if isinstance(answer, (int, float)) else ""

    def pipe_live(self, item, lang):
        return {"kind": "value", "field": f"i{item.id}", "comma": "" if units.writes_like_english(lang) else "1"}

    def export_columns(self, item):
        return [(item.code, item.text("question") + (" (rekodiert)" if read_cfg(item).get("reverse") else ""))]

    def export_values(self, item, answer):
        if answer == DONT_KNOW:
            return {item.code: DONT_KNOW_CODE}
        if answer is not None and read_cfg(item).get("reverse"):
            lo, hi = self.bounds(item)
            answer = lo + hi - answer
            if isinstance(answer, float):
                # 0.2 + 0.7 - 0.3 is 0.5999999999999999 in binary: back onto the decimals of the scale
                from app.units import decimals

                places = max(decimals(read_cfg(item).get("step") or 1), decimals(lo), decimals(hi))
                answer = round(answer, places)
                answer = int(answer) if not places else answer
        return {item.code: answer}
