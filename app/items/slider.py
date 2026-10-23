"""Sliders: one or more statements, each on its own slider from `min` to `max`
(e.g. 0 to 100, −100 % to +100 %, 0 to 1,000 €). Unlike the constant sum, the values
are independent. Units: plain number (free unit text), currency or percent (app/units.py).

By default a slider has no starting value: its handle appears on first touch, so there is no
anchor and "not answered" stays distinguishable from any position. Optionally a start value
shows the handle there from the beginning; it still only counts as an answer once the slider is
moved or tapped (a tap on the handle confirms the start value).
Export: one column per statement (a single statement without its own code uses the item code)."""
import math

from app import units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_id, r_num, r_one, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import CODE_RE, AnswerError, ItemType, in_lang, reader, read_cfg
from app.items.base import form_ids, new_id as _new_id
from app.lang import Both, de_en, english
from app.ui_i18n import tr

PRESETS = [  # (label, min, max, step, format)
    ("0 bis 100", 0, 100, 1, "number"),
    ("0 bis 10", 0, 10, 1, "number"),
    ("0 % bis 100 %", 0, 100, 1, "percent"),
    ("−100 % bis +100 %", -100, 100, 1, "percent"),
    ("−50 bis +50", -50, 50, 1, "number"),
    ("0 bis 1.000 €", 0, 1000, 10, "currency"),
]
# the same templates and units named in English, for the editor in English (app/lang.py)
PRESET_LABELS_EN = ("0 to 100", "0 to 10", "0% to 100%", "−100% to +100%", "−50 to +50", "0 to 1,000 €")
FORMATS_EN = {"number": "Number", "currency": "Currency", "percent": "Percent"}


def _num(value, default=None):
    try:
        number = float(str(value).replace(",", ".").replace("−", "-"))
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default  # "nan", "inf": no bound, step or answer


def snap(v, lo, hi, step):
    """v on the grid lo, lo + step, ... (never above hi), a tie rounded up like the browser's range input
    (HTML: "closest to positive infinity"); as an int for whole steps. Half to even would put 2 on 0 and 6 on
    8 with step 4, and a grid that does not end on hi would snap 10 to 12 (from 0 to 10 in steps of 6).
    The grid point is lo + n·step in decimal arithmetic, kept with the decimals of step and of lo: from 0.5 in
    steps of 1, 2.5 stays 2.5 (rounding to the step's 0 decimals made it 3; audit 2026-10-23, M8)."""
    places = max(units.decimals(step), units.decimals(lo))
    n = math.floor((v - lo) / step + 0.5 + 1e-9)
    while n > 0 and lo + n * step > hi + 1e-9:
        n -= 1
    v = units.round_half_up(float(units.exact(lo) + n * units.exact(step)), places)
    return int(v) if float(v).is_integer() and places == 0 else v


@register
class Slider(ItemType):
    key = "slider"
    scale_keys = ("min", "max")  # fixed once real answers exist (ItemType.answer_shape)
    read_keys = ("format", "currency")  # the unit of the stored values (audit 2026-10-12, M-3)
    import_rules = {"min": r_num(-1e9, 1e9), "max": r_num(-1e9, 1e9), "step": r_num(1e-6, 1e9), "format": r_one(*units.FORMATS),
                    "currency": r_one(*units.CURRENCIES), "show_value": r_flag, "randomize": r_flag,
                    "start": r_num(-1e9, 1e9, none=True), "rows": r_rows(100, id=r_id, code=r_text(64)), "ratio": r_flag}
    label = Both("Schieberegler", "Slider")
    short = Both("Aussagen auf Reglern", "Statements on sliders")
    description = Both("Eine oder mehrere Aussagen auf Reglern von … bis … (Zahl, Währung, Prozent)",
                       "One or more statements on sliders from … to … (number, currency, percent)")
    order = 36
    fact_sheet = FactSheet(
        "scales",
        keywords=["Schieberegler", "Slider", "Regler", "Visual analogue scale", "Visuelle Analogskala", "VAS"],
        can=[("Eine oder mehrere Aussagen auf Reglern von … bis ….", "One or more statements on sliders from … to …."),
             ("Als Zahl, Währung oder Prozent, mit oder ohne sichtbaren Wert, auf Wunsch in Zufallsreihenfolge.",
              "As a number, currency or percent, with or without a visible value, in random order if you wish."),
             ("Ohne Startwert: kein Anker, „nicht beantwortet“ bleibt erkennbar.",
              "Without a start value: no anchor, “not answered” stays recognisable.")],
        good_for=[("Schätzungen auf einer fein abgestuften Skala: Wahrscheinlichkeit in Prozent, Beträge, Anteile",
                   "Estimates on a finely graded scale: likelihood in percent, amounts, shares"),
                  ("Delphi-Projektionen mit Wahrscheinlichkeit", "Delphi projections with a likelihood")],
        not_for=[("Werte, die zusammen eine Summe ergeben müssen (dann Konstantsumme)",
                  "Values that must add up to a total (then constant sum)")],
        designs=["cross", "perception", "delphi", "vignettes"],
        examples=[("Wie wahrscheinlich ist es, dass 2035 Roboter in der Pflege helfen? (0 bis 100 %)",
                   "How likely is it that robots will help in care by 2035? (0 to 100%)")],
    )
    levels = ("metric",)
    # the editor's lists, labelled in the language of the page
    @property
    def presets(self):
        return [(en, *rest) for en, (_, *rest) in zip(PRESET_LABELS_EN, PRESETS)] if english() else PRESETS

    @property
    def formats(self):
        return FORMATS_EN if english() else units.FORMATS

    currencies = units.CURRENCIES

    def default_config(self):
        return {"min": 0, "max": 100, "step": 1, "format": "number", "currency": "EUR", "show_value": True,
                "randomize": False, "start": None, "rows": [{"id": _new_id(), "code": ""}]}

    def default_texts(self, lang):
        return {"question": "", "unit": "", "left": "", "right": "", "rows": {}}

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config)
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt.update(question=form.get("question", "").strip(), unit=form.get("unit", "").strip()[:30],
                  left=form.get("left", "").strip()[:100], right=form.get("right", "").strip()[:100])
        rtexts = dict(lt.get("rows", {}))
        ids, codes, values = form_ids(form.getlist("stmt_id")), form.getlist("stmt_code"), form.getlist("stmt_text")
        if form.get("ratio_shown"):  # only how the results are summarised: changeable at any time
            config["ratio"] = bool(form.get("ratio"))
        if structural:
            # in the language of the page with a note on "1,000" in German (units.read_entry; Mathe-Audit 2026-10-06,
            # Z-4: "0 bis 1.000" was read as 0 to 1)
            lo, hi, step = (units.read_entry(form.get(k), default=d, known=config.get(k))
                            for k, d in (("min", 0), ("max", 100), ("step", 1)))
            config.update(min=lo, max=hi, step=step if step and step > 0 else 1,
                          format=form.get("format") if form.get("format") in units.FORMATS else "number",
                          currency=form.get("currency") if form.get("currency") in units.CURRENCIES else "EUR",
                          show_value=bool(form.get("show_value")), randomize=bool(form.get("randomize")))
            start = units.read_entry(form.get("start"), known=config.get("start"))
            if start is not None and config["min"] <= start <= config["max"]:  # snap to the grid of the new settings
                start = snap(start, config["min"], config["max"], config["step"])
            config["start"] = start  # outside the range it is kept, so the pre-launch check names it
            rows = []
            for rid, code, text in zip(ids, codes, values):
                if not (code.strip() or text.strip()):
                    continue
                rid = rid or _new_id()
                rows.append({"id": rid, "code": code.strip()})
                rtexts[rid] = text.strip()
            config["rows"] = rows
        else:
            known = {r["id"] for r in config.get("rows", [])}
            for rid, text in zip(ids, values):
                if rid in known:
                    rtexts[rid] = text.strip()
        lt["rows"] = rtexts
        texts[lang] = lt
        item.config, item.texts = config, texts

    # --- scale and display -------------------------------------------------------------

    def is_ratio(self, item):
        """Amounts or counts (Gini on the results page): the editor's tick; without it, money is an amount."""
        c = read_cfg(item)
        return bool(c.get("ratio", c.get("format") == "currency"))

    def unit_label(self, item, lang="de"):
        """The unit of the values on the results page: € (or the currency), %, or the free unit text."""
        c = read_cfg(item)
        if c.get("format") in ("currency", "percent"):
            return "".join(units.affixes(c["format"], c.get("currency"), "", lang)).strip()
        return (item.text("unit", lang) or "").strip()

    def result_column(self, item, column):
        return self.result_kind, self.unit_label(item), None

    def places(self, item):
        c = read_cfg(item)  # a grid from 0.5 in steps of 1 has the decimals of its start (audit 2026-10-23, M8)
        return max(units.decimals(c.get("step", 1)), units.decimals(c.get("min", 0)))

    def signed(self, item):
        return read_cfg(item).get("min", 0) < 0

    def affixes(self, item, lang):
        c = read_cfg(item)
        return units.affixes(c.get("format"), c.get("currency"), item.text("unit", lang), lang)

    def show(self, item, value, lang):
        c = read_cfg(item)
        return units.fmt(value, c.get("format"), c.get("currency"), item.text("unit", lang), lang,
                         self.places(item), self.signed(item))

    def order_codes(self, item):
        if not read_cfg(item).get("randomize"):
            return None
        return {r["id"]: self.row_code(item, i, r) for i, r in enumerate(read_cfg(item).get("rows", []))}

    def row_code(self, item, index, row):
        if row.get("code"):
            return row["code"]
        return item.code if len(read_cfg(item).get("rows", [])) == 1 else f"{item.code}_{index + 1}"

    def rows(self, item, lang, order=None):
        rtexts = item.t(lang).get("rows", {})
        out = [{**r, "text": rtexts.get(r["id"], ""), "export_code": self.row_code(item, i, r)}
               for i, r in enumerate(read_cfg(item).get("rows", []))]
        if order:
            rank = {rid: n for n, rid in enumerate(order)}
            out.sort(key=lambda r: rank.get(r["id"], len(rank)))
        return out

    def validate(self, item):
        c = read_cfg(item)
        problems = []
        if c.get("min") is None or c.get("max") is None or c["min"] >= c["max"]:
            problems.append(de_en("„von“ muss kleiner als „bis“ sein", "“from” must be smaller than “to”"))
        elif (c["max"] - c["min"]) / c.get("step", 1) > 10000:
            problems.append(de_en("zu viele Stufen (Schrittweite vergrößern)", "too many steps (increase the step)"))
        if c.get("start") is not None and c.get("min") is not None and c.get("max") is not None \
                and not (c["min"] <= c["start"] <= c["max"]):
            problems.append(de_en("Startwert liegt außerhalb von „von“ bis „bis“", "starting value lies outside “from” to “to”"))
        rows = c.get("rows", [])
        if not rows:
            problems.append(de_en("mindestens eine Aussage", "at least one statement"))
        codes = [self.row_code(item, i, r) for i, r in enumerate(rows)]
        if len(set(codes)) != len(codes):
            problems.append(de_en("Variablennamen der Aussagen sind nicht eindeutig", "variable names of the statements are not unique"))
        bad = [x for x in codes if not CODE_RE.match(x)]
        if bad:
            problems.append(de_en("Variablenname „{name}“ muss mit einem Buchstaben beginnen", "variable name “{name}” must start with a letter",
                                  name=bad[0]))
        rtexts = item.text("rows") or {}
        if len(rows) > 1 and any(not rtexts.get(r["id"]) for r in rows):
            problems.append(de_en("Aussage ohne Text", "statement without text"))
        return problems

    # --- answers -------------------------------------------------------------------------

    def _value(self, item, raw):
        c = read_cfg(item)
        v = _num(raw)
        if v is None or not (c["min"] <= v <= c["max"]):
            return None
        step = c.get("step", 1)
        return snap(v, c["min"], c["max"], step)

    def parse_answer(self, item, form, lang="de", meta=None):
        answer, missing = {}, False
        for r in read_cfg(item).get("rows", []):
            raw = (form.get(f"i{item.id}_{r['id']}") or "").strip()
            v = self._value(item, raw) if raw else None
            if raw and v is None:
                raise AnswerError(tr(lang, "Ungültiger Wert.", "Invalid value."))
            if v is None:
                missing = True
            else:
                answer[r["id"]] = v
        if item.required and missing:
            n = len(read_cfg(item).get("rows", []))
            raise AnswerError(tr(lang, "Bitte stellen Sie alle Regler ein.", "Please set all sliders.") if n > 1
                              else tr(lang, "Bitte stellen Sie den Regler ein.", "Please set the slider."))
        return answer or None

    def display_value(self, item, form, meta=None):
        out = {}
        for r in read_cfg(item).get("rows", []):
            raw = (form.get(f"i{item.id}_{r['id']}") or "").strip()
            if raw and self._value(item, raw) is not None:
                out[r["id"]] = self._value(item, raw)
        return out

    def is_missing(self, item, answer):
        return any((answer or {}).get(r["id"]) is None for r in read_cfg(item).get("rows", []))

    def pipeable(self, item):
        return len(read_cfg(item).get("rows", [])) == 1  # several sliders: which one?

    def pipe_text(self, item, answer, lang):
        return self._pipe_value(item, next(iter((answer or {}).values()), None), lang)

    def _pipe_value(self, item, value, lang):
        if value is None:
            return ""
        c = read_cfg(item)
        return units.fmt(value, c.get("format") or "number", c.get("currency") or "EUR", item.text("unit", lang),
                         lang, self.places(item))

    def pipe_live(self, item, lang):
        rows = read_cfg(item).get("rows", [])
        if len(rows) != 1:
            return None
        c = read_cfg(item)
        pre, suf = units.affixes(c.get("format") or "number", c.get("currency") or "EUR", item.text("unit", lang), lang)
        return {"kind": "value", "field": f"i{item.id}_{rows[0]['id']}", "comma": "" if units.writes_like_english(lang) else "1",
                "pre": pre, "suf": suf, "places": str(self.places(item))}

    def pipe_parts(self, item, lang):  # several sliders: each by its column
        from app.items.base import plain

        rows = read_cfg(item).get("rows", [])
        texts = item.text("rows", lang) or {}
        return [(self.row_code(item, i, r), plain(texts.get(r["id"], "")) or self.row_code(item, i, r))
                for i, r in enumerate(rows)] if len(rows) > 1 else []

    def pipe_part_text(self, item, answer, name, lang):
        for i, r in enumerate(read_cfg(item).get("rows", [])):
            if self.row_code(item, i, r) == name:
                return self._pipe_value(item, (answer or {}).get(r["id"]) if isinstance(answer, dict) else None, lang)
        return ""

    def export_columns(self, item):
        rtexts = item.text("rows") or {}
        c = read_cfg(item)
        rng = f"{units.number(c['min'], self.places(item))} bis {units.number(c['max'], self.places(item))}"
        unit = {"percent": " %", "currency": " " + c.get("currency", "EUR")}.get(c.get("format"), "")
        return [(self.row_code(item, i, r), f"{rtexts.get(r['id'], '') or item.text('question')} ({rng}{unit})".strip())
                for i, r in enumerate(c.get("rows", []))]

    def codebook_info(self, item, column, lang, ui=None):
        c = read_cfg(item)
        unit = {"percent": " %", "currency": " " + c.get("currency", "EUR")}.get(c.get("format"), "")
        places = self.places(item)
        nl = reader(ui)  # numbers in the reader's language: 0,5 or 0.5

        def num(x):
            return units.number(x, places, nl)
        notes = [in_lang(ui, "Schrittweite {step}", "step {step}", step=num(c.get('step', 1)))]
        if c.get("start") is not None:
            notes.append(in_lang(ui, "Startwert {start}{unit} (unverändert und nicht angetippt = leer)",
                                 "starting value {start}{unit} (unchanged and not touched = empty)", start=num(c['start']), unit=unit))
        return ("Zahl", in_lang(ui, "{lo} bis {hi}{unit}", "{lo} to {hi}{unit}", lo=num(c['min']), hi=num(c['max']), unit=unit), notes)

    def export_values(self, item, answer):
        return {self.row_code(item, i, r): (answer or {}).get(r["id"])
                for i, r in enumerate(read_cfg(item).get("rows", []))}
