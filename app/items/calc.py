"""Rechenwert: a number worked out from earlier answers, put together by picking, never by typing a formula.

A first value (an earlier number question, a slider, a rating or another Rechenwert), what is done with it (plus,
minus, times, divided by, share in percent) and a second value (one of these, or a fixed number), rounded to a
number of decimals, with a unit: "yearly income = monthly income times 12".

Participants never see the element: it is on no page (applies), takes no answer and stores none. Its value
follows from the answers whenever it is needed (value): as a chip in later texts ({{code}}, app/piping.py; with
the text for an empty value where a part is missing or the divisor is 0), as the total of a constant sum, and as
a column of the data set, so what a person read can be traced. A chain is a Rechenwert as the first value of
the next one.
"""
import math
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app import units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import ItemType, r_int, r_num, r_one, r_text
from app.items.base import MAX_NUMBER, in_lang, whole
from app.lang import Both, de_en, english

OPS = (("add", "plus"), ("sub", "minus"), ("mul", "mal"), ("div", "geteilt durch"), ("pct", "als Anteil in Prozent von"))
OPS_EN = (("add", "plus"), ("sub", "minus"), ("mul", "times"), ("div", "divided by"), ("pct", "as a percentage of"))
SIGNS = {"add": "+", "sub": "−", "mul": "×", "div": "÷", "pct": "in % von"}
MAX_PLACES = 4
MAX_DEPTH = 10  # a chain of Rechenwerte longer than this is a loop


def _r_ref(value):
    from app.items.base import Unfit
    from app.items.constant_sum import SOURCE_RE

    if value is None:
        return None
    if not isinstance(value, str) or not SOURCE_RE.fullmatch(value):
        raise Unfit
    return value


def number_sources(item, lang=None):
    """[(ref, label)] of the numbers of earlier pages (and of Rechenwerte before this element) a value can be
    taken from, as people read them, in the order of the questionnaire. ref: "<item id>", for one of several
    sliders "<item id>:<row id>"."""
    from app import flow, piping
    from app.items.base import plain

    survey = item.block.survey
    pos = piping._positions(survey)
    here = pos.get(item.id)
    in_loop = flow.loop_blocks(survey)
    order = {i.id: n for n, i in enumerate(i for b in survey.blocks for i in b.items)}
    found = []
    for block in survey.blocks:
        if block.id in in_loop:
            continue
        for source in block.items:
            if here is None or source.id not in pos or source.id == item.id:
                continue
            question = plain(source.text("question", lang) or "") or source.kind.label
            if source.type == "calc":
                # another Rechenwert: any that stands before this element (it is on no page itself)
                if (pos[source.id], order[source.id]) < (here, order[item.id]):
                    found.append((order[source.id], str(source.id), f"{source.code} · Rechenwert: {question[:50]}"))
                continue
            # a Rechenwert may stand on the page of the questions it reads (it is worked out when it is needed);
            # anything else (the total of a constant sum) needs the number from an earlier page
            if pos[source.id] > here or (pos[source.id] == here and item.type != "calc"):
                continue
            if source.type == "text_input" and (source.config or {}).get("validation") in ("integer", "decimal"):
                found.append((order[source.id], str(source.id), f"{source.code} · {question[:60]}"))
            elif source.type == "rating":
                found.append((order[source.id], str(source.id), f"{source.code} · {question[:60]}"))
            elif source.type == "slider":
                rows = source.kind.rows(source, lang)
                for r in rows:
                    name = f"{question[:40]}: {plain(r.get('text') or '')[:30]}" if len(rows) > 1 else question[:60]
                    found.append((order[source.id], f"{source.id}:{r['id']}", f"{source.code} · {name}"))
    return [(ref, label) for _, ref, label in sorted(found, key=lambda f: f[0])]


def read_questions(survey, item, depth=0):
    """Ids of the questions a Rechenwert reads, also through the Rechenwerte it reads (a chain)."""
    from app.conditions import own_item

    found = set()
    if depth > MAX_DEPTH:
        return found
    for key in ("a", "b"):
        head = str((item.config or {}).get(key) or "").partition(":")[0]
        source = own_item(survey, whole(head)) if whole(head) is not None else None
        if source is None:
            continue
        if source.type == "calc":
            found |= read_questions(survey, source, depth + 1)
        else:
            found.add(source.id)
    return found


def _decimal(value):
    """A number as a Decimal read from its shortest decimal text (0.15 -> 0.15, not 0.1499…), None for none.
    A fixed number is typed as a decimal text anyway, and so are the answers."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return Decimal(repr(number)) if math.isfinite(number) else None


def read(survey, ref, answers, depth=0):
    """The number behind `ref` in the answers so far (item id -> answer), or None."""
    from app.conditions import own_item

    head, _, row = str(ref or "").partition(":")
    source = own_item(survey, whole(head)) if whole(head) is not None else None
    if source is None:
        return None
    if source.type == "calc":
        # unrounded: a chain works with the exact value, only the value shown and exported is rounded
        # (2000 ÷ 3 with 0 decimals reads 667, but times 3 is 2000, not 2001)
        return source.kind.value(source, answers, depth + 1, exact=True)
    value = (answers or {}).get(source.id)
    if row:
        value = value.get(row) if isinstance(value, dict) else None
    elif source.type == "slider" and isinstance(value, dict):
        value = next(iter(value.values()), None) if len(value) == 1 else None
    elif source.type == "rating" and isinstance(value, (int, float)) and not isinstance(value, bool):
        value = source.kind.export_values(source, value).get(source.code)  # reversed: the recoded value, as exported
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@register
class Calc(ItemType):
    key = "calc"
    read_keys = ("formula",)
    formula_keys = ("a", "b", "op", "fixed", "places", "unit")

    def read_shape(self, item):
        return {"formula": tuple(item.cfg.get(k) for k in self.formula_keys)}

    def has_real_answers(self, item):
        """A computed value is used through its inputs, although it has no answer row of its own."""
        from app.conditions import own_item

        for source_id in read_questions(item.block.survey, item):
            source = own_item(item.block.survey, source_id)
            if source is not None and source.kind.has_real_answers(source):
                return True
        return super().has_real_answers(item)
    label = Both("Rechenwert", "Computed value")
    short = Both("Rechnet mit früheren Zahlen", "Calculates with earlier numbers")
    description = Both("Rechnet mit früheren Zahlen (z. B. Monatseinkommen mal 12): für spätere Texte, als Summe einer Konstantsumme und als Spalte im Export",
                       "Calculates with earlier numbers (e.g. monthly income times 12): for later texts, as the total of a constant sum and as a column in the export")
    order = 48
    fact_sheet = FactSheet(
        "classic",
        keywords=["Berechnung", "Calculation", "Formel", "Formula", "Summe", "Sum", "Variable"],
        can=[("Rechnet mit früheren Zahlen: plus, minus, mal, geteilt, „in % von“.",
              "Calculates with earlier numbers: plus, minus, times, divided by, “in % of”."),
             ("Für spätere Texte, als Summe einer Konstantsumme und als Spalte im Export.",
              "For later texts, as the total of a constant sum and as a column in the export.")],
        good_for=[("Werte, die sich aus Antworten ergeben, z. B. Jahreseinkommen aus Monatseinkommen",
                   "Values that follow from answers, e.g. annual income from monthly income"),
                  ("Zahlen, die in einer späteren Frage erscheinen sollen",
                   "Numbers that are to appear in a later question")],
        not_for=[("Skalenwerte über mehrere Aussagen (die rechnet die Auswertung)",
                  "Scale scores over several statements (the analysis works these out)")],
        designs=["cross"],
        examples=[("Sie sagten, Sie geben {{monat}} € im Monat aus, das sind {{jahr}} € im Jahr.",
                   "You said you spend €{{month}} a month, that is €{{year}} a year.")],
    )
    collects_answer = False
    has_answer_mode = False
    computed = True  # no answer, but a value of its own: a chip in texts and a column of the data set
    allowed_in_loop = False
    allowed_in_separated = False
    result_kind = "auto"
    levels = ("metric",)
    import_rules = {"a": _r_ref, "op": r_one(*(k for k, _ in OPS)), "b": _r_ref, "fixed": r_num(-MAX_NUMBER, MAX_NUMBER, none=True),
                    "places": r_int(0, MAX_PLACES), "unit": r_text(30)}

    @property
    def ops(self):
        """The operations with their names in the language of the page (the editor's list)."""
        return OPS_EN if english() else OPS

    def default_config(self):
        return {"a": None, "op": "mul", "b": None, "fixed": None, "places": 0, "unit": ""}

    def default_texts(self, lang):
        return {"question": ""}

    def applies(self, item, answers):
        return False  # on no page: nobody sees it

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        if not structural:
            return
        from app.items.constant_sum import SOURCE_RE

        config = dict(item.config)
        for key in ("a", "b"):
            ref = form.get(f"calc_{key}", "")
            config[key] = ref if SOURCE_RE.fullmatch(ref) else None
        config["op"] = form.get("calc_op") if form.get("calc_op") in dict(OPS) else "mul"
        fixed = form.get("calc_fixed", "").strip()
        # read like the numbers of conditions (German), whatever the language of the tab or page: "1.000" is 1000;
        # "1,000" is read as 1 with a note after saving, as the pre-launch check says for conditions (decisions.md,
        # Mathe-Audit 2026-10-06, Z-6)
        number = units.read_entry(fixed, "de", known=item.config.get("fixed")) if fixed and not config["b"] else None
        config["fixed"] = number if number is not None and abs(number) <= MAX_NUMBER else None
        places = form.get("calc_places", "")
        config["places"] = whole(places) if whole(places) is not None and whole(places) <= MAX_PLACES else 0
        config["unit"] = form.get("calc_unit", "").strip()[:30]
        item.config = config

    def sources(self, item, lang=None):
        return number_sources(item, lang)

    def fixed_text(self, item, lang):
        """The fixed number for the field of the editor, German in every tab (it is read back that way)."""
        from app.conditions import _plain

        fixed = item.config.get("fixed")
        return _plain(fixed) if fixed is not None else ""

    def validate(self, item):
        c = item.config
        known = {ref for ref, _ in number_sources(item)}
        problems = []
        if not c.get("a"):
            problems.append(de_en("der erste Wert fehlt", "the first value is missing"))
        elif c["a"] not in known:
            problems.append(de_en("der erste Wert kommt aus einer Frage, die es nicht mehr gibt oder die nicht davor steht",
                                  "the first value comes from a question that no longer exists or does not come before it"))
        if c.get("b") and c["b"] not in known:
            problems.append(de_en("der zweite Wert kommt aus einer Frage, die es nicht mehr gibt oder die nicht davor steht",
                                  "the second value comes from a question that no longer exists or does not come before it"))
        elif not c.get("b") and c.get("fixed") is None:
            problems.append(de_en("der zweite Wert fehlt (eine Frage oder eine feste Zahl)", "the second value is missing (a question or a fixed number)"))
        elif not c.get("b") and c.get("fixed") == 0 and c.get("op") in ("div", "pct"):
            problems.append(de_en("durch 0 lässt sich nicht teilen", "cannot divide by 0"))
        return problems

    def remap_config(self, config, twin):
        out = dict(config)
        for key in ("a", "b"):
            head, sep, row = str(config.get(key) or "").partition(":")
            if head:
                new = twin(whole(head)) if whole(head) is not None else None
                out[key] = f"{new}{sep}{row}" if new is not None else None
        return out

    # --- the value -------------------------------------------------------------------------------------------

    def value(self, item, answers, depth=0, exact=False):
        """The number for a person with these answers (item id -> answer), rounded half up (2,5 -> 3,
        1,005 -> 1,01); None where a part is missing, the divisor is 0 or the result is no number any export
        can hold. Worked out in decimal, not in binary floating point, so half up rounds what the numbers
        give: 0,15 × 3 = 0,45 -> 0,5 (in binary 0,4499… -> 0,4; audit 2026-10-05, R-2). exact: unrounded,
        as a Decimal, for a Rechenwert that reads this one."""
        c = item.config
        if depth > MAX_DEPTH:
            return None
        survey = item.block.survey
        a = _decimal(read(survey, c.get("a"), answers, depth))
        b = _decimal(read(survey, c.get("b"), answers, depth) if c.get("b") else c.get("fixed"))
        if a is None or b is None:
            return None
        op = c.get("op")
        if op in ("div", "pct") and b == 0:
            return None
        with localcontext() as ctx:
            ctx.prec = 34  # far more digits than any answer; a division is cut off there
            result = {"add": lambda: a + b, "sub": lambda: a - b, "mul": lambda: a * b,
                      "div": lambda: a / b, "pct": lambda: a / b * 100}.get(op, lambda: None)()
        if result is None or abs(result) > MAX_NUMBER:
            return None
        if exact:
            return result
        places = int(c.get("places") or 0)
        rounded = result.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return int(rounded) if not places else float(rounded) + 0.0

    def result_column(self, item, column):
        """Results page: the unit of the computed value next to M and Md."""
        return self.result_kind, ((item.config or {}).get("unit") or "").strip(), None

    def shown(self, item, value, lang):
        c = item.config
        return units.fmt(value, "number", unit=c.get("unit") or "", lang=lang, places=c.get("places") or 0) if value is not None else ""

    def formula(self, item, lang=None):
        """The calculation in words: "income × 12", for lists and the codebook."""
        from app.conditions import own_item
        from app.items.base import number_text

        c = item.config

        def name(ref):
            head = str(ref or "").partition(":")[0]
            source = own_item(item.block.survey, whole(head)) if whole(head) is not None else None
            return source.code if source is not None else "?"

        second = name(c["b"]) if c.get("b") else (number_text(c["fixed"], lang or "de") if c.get("fixed") is not None else "?")
        return f"{name(c.get('a'))} {SIGNS.get(c.get('op'), '?')} {second}"

    # piping: the value as participants read it ("28.200 €")
    def pipeable(self, item):
        return True

    def computed_text(self, item, answers, lang):
        return self.shown(item, self.value(item, answers), lang)

    # export: one column with the number
    def export_columns(self, item):
        from app.items.base import plain

        label = plain(item.text("question")) or "Rechenwert"
        return [(item.code, f"{label} (berechnet: {self.formula(item)})")]

    def export_values(self, item, answer):
        return {item.code: None}

    def export_response_values(self, item, answer, response):
        from app.runtime import load_answers

        return {item.code: self.value(item, load_answers(response))}

    def codebook_info(self, item, column, lang, ui=None):
        c = item.config
        unit = f" {c['unit']}" if c.get("unit") else ""
        return "Zahl", "", [in_lang(ui, "berechnet: {formula}, auf {places} Nachkommastellen kaufmännisch gerundet{unit}",
                                    "computed: {formula}, rounded half up to {places} decimal places{unit}",
                                    formula=self.formula(item, lang), places=c.get("places") or 0, unit=unit),
                            in_lang(ui, "leer, wenn ein Wert fehlt oder durch 0 geteilt würde; Teilnehmende sehen das Element nicht",
                                    "empty if a value is missing or it would divide by 0; participants do not see the element")]

    def questionnaire_lines(self, item, lang):
        return [f"  - computed, not shown: `{item.code}` = {self.formula(item, lang)}"]

    def seconds(self, item, read):
        return 0
