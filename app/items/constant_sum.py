"""Constant sum: distribute a fixed total (e.g. 100 points) over 2 or more options.

Classic constant-sum questions make participants type numbers until they add up exactly,
and then show an error if they do not. Here every option has a slider (and a number field),
and the sum is always right: moving one option takes the difference from the others, first
from those the participant has not set yet (keeping their proportions), then from the ones
already set. The start is an even split; like the ranking, an answer only counts once the
participant has moved something or confirmed "Verteilung passt so".
Without JavaScript the number fields remain and the server checks the sum.
Export: one column per option with its share.

The total can also come from the answer to an earlier question (config "total_from": a number field or a
slider on an earlier page, e.g. the monthly income): every person then distributes their own amount. Without
that answer, or with 0, the question is left out (applies) or takes a fallback total ("total_missing",
"total_fallback"). The export then has <code>_total per person and, if ticked ("export_pct"), the shares in
percent as <code>_<option>_pct; results and dashboards show the shares in percent, since the amounts of people
with a large total would decide every mean."""
import re

from app import rows, units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en, english
from app.items.choice import CARRY_RULES, LAYOUT_RULE, OPTION_RULES
from app.items.base import Unfit, r_flag, r_int, r_one  # rules for survey files, see base.py
from app.items.base import AnswerError, in_lang, whole
from app.items.choice import _Choice
from app.ui_i18n import tr

FORMATS_EN = {"number": "Number", "currency": "Currency", "percent": "Per cent"}  # units.FORMATS for the English editor
MAX_TOTAL = 10000
MAX_PERSONAL = 10 ** 9  # a total taken from an answer: no 10,000 cap, only what a whole number in a form field holds
SLIDER_STEPS = 200      # a large personal total: the sliders move in round steps, the number fields take every amount
PERSONAL_KEYS = ("total_from", "total_missing", "total_fallback", "export_pct")
SOURCE_RE = re.compile(r"[0-9]{1,12}(:[A-Za-z0-9_.-]{1,40})?")


def _r_source(value):
    """Rule for a survey file: None, or "<item id>" / "<item id>:<row id>" (transfer and duplicate map the id)."""
    if value is None:
        return None
    if not isinstance(value, str) or not SOURCE_RE.fullmatch(value):
        raise Unfit
    return value


def slider_step(total):
    """1 up to SLIDER_STEPS; above, the round number (1, 2 or 5 times a power of ten) that gives at most that
    many steps."""
    if total <= SLIDER_STEPS:
        return 1
    raw = total / SLIDER_STEPS
    power = 10 ** (len(str(int(raw))) - 1)
    return next(m * power for m in (1, 2, 5, 10) if m * power >= raw)


def even_split(total, n):
    """Integers as equal as possible that add up to `total` (the first ones get the remainder)."""
    if n <= 0:
        return []
    base, rest = divmod(total, n)
    return [base + (1 if i < rest else 0) for i in range(n)]


@register
class ConstantSum(_Choice):
    key = "constant_sum"
    # the total and the unit the stored amounts are read in (audit 2026-10-12, N-6)
    read_keys = ("total", "total_from", "format", "currency")
    code_answer = False  # not a code to compare with: a condition only asks "answered or not" (X17)
    import_rules = {"options": OPTION_RULES, "layout": LAYOUT_RULE, "total": r_int(1, MAX_TOTAL), "randomize": r_flag,
                    "format": r_one(*units.FORMATS), "currency": r_one(*units.CURRENCIES),
                    "total_from": _r_source, "total_missing": r_one("skip", "fallback"),
                    "total_fallback": r_int(1, MAX_PERSONAL), "export_pct": r_flag, **CARRY_RULES}

    def missing_codes(self, item, column):
        return (rows.NOT_SHOWN,) if column in self.partial_columns(item) else ()
    uses_answers = True  # parse_answer needs the answers so far: the total of the person
    label = Both("Konstantsumme", "Constant sum")
    codes_in_columns = True
    ratio_scale = True
    short = Both("Punkte auf Optionen verteilen", "Share points across options")
    description = Both("Einen festen Betrag (z. B. 100 Punkte) auf mehrere Optionen verteilen; die Summe stimmt immer",
                       "Distribute a fixed amount (e.g. 100 points) across several options; the total is always right")
    order = 26
    fact_sheet = FactSheet(
        "classic",
        keywords=["Punkte verteilen", "Allocate points", "100 Punkte", "100 points", "Budget", "Chip allocation"],
        can=[("Einen festen Betrag auf mehrere Optionen verteilen.",
              "Distribute a fixed amount across several options."),
             ("Als Zahl, Währung (€, $, £, CHF) oder Prozent.", "As a number, currency (€, $, £, CHF) or percent."),
             ("Die Summe ist fest oder kommt aus einer früheren Antwort.",
              "The total is fixed or comes from an earlier answer.")],
        good_for=[("Anteile und Budgets: Zeit, Geld oder Wichtigkeit, die zusammen 100 ergeben",
                   "Shares and budgets: time, money or importance that add up to 100")],
        not_for=[("Werte, die unabhängig voneinander sind (dann Schieberegler)",
                  "Values that are independent of each other (then slider)")],
        designs=["cross", "perception"],
        examples=[("Verteilen Sie 100 Punkte auf diese Eigenschaften eines Autos.",
                   "Distribute 100 points across these features of a car.")],
    )
    levels = ("metric",)

    @property
    def formats(self):
        """The units of the editor's select, labelled in the language of the page."""
        return FORMATS_EN if english() else units.FORMATS
    currencies = units.CURRENCIES

    def default_config(self):
        return {**super().default_config(), "total": 100, "randomize": False, "format": "number", "currency": "EUR"}

    def default_texts(self, lang):
        return {**super().default_texts(lang), "unit": tr(lang, "Punkte", "points")}

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        texts = dict(item.texts)
        texts[lang] = {**texts.get(lang, {}), "unit": form.get("unit", "").strip()[:30]}
        item.texts = texts

    def _update_extra_config(self, config, form):
        total = form.get("total", "").strip()
        config["total"] = min(whole(total), MAX_TOTAL) if (whole(total) or 0) > 0 else 100
        config["randomize"] = bool(form.get("randomize"))
        config["format"] = form.get("format") if form.get("format") in units.FORMATS else "number"
        config["currency"] = form.get("currency") if form.get("currency") in units.CURRENCIES else "EUR"
        config.pop("layout", None)
        # the total of each person from an earlier answer: the form names the question, nobody types a code
        source = form.get("total_from", "") if form.get("total_mode") == "from" else ""
        for key in PERSONAL_KEYS:
            config.pop(key, None)
        if SOURCE_RE.fullmatch(source):
            config["total_from"] = source
            fallback = whole(form.get("total_fallback", "").strip())
            if form.get("total_missing") == "fallback" and fallback:
                config["total_missing"], config["total_fallback"] = "fallback", fallback
            if form.get("export_pct"):
                config["export_pct"] = True

    # --- the total of a person ---------------------------------------------------------------------------

    def personal(self, item):
        return bool((item.config or {}).get("total_from"))

    def _source(self, item):
        """(question, row id or None) the total comes from; (None, None) if it is gone or asks no number."""
        from app.conditions import own_item

        ref, _, row = str(item.config.get("total_from") or "").partition(":")
        source = own_item(item.block.survey, whole(ref)) if whole(ref) is not None else None
        return (source, row or None) if source is not None and self._is_source(source, row or None) else (None, None)

    @staticmethod
    def _is_source(source, row=None):
        if source.type == "calc":
            return row is None
        if source.type == "text_input":
            return (source.config or {}).get("validation") in ("integer", "decimal") and row is None
        return source.type == "slider" and any(r["id"] == row for r in source.config.get("rows", []))

    def total_sources(self, item, lang=None):
        """[(ref, label)] of what the total can come from: the number fields and sliders on earlier pages, as
        people read them (the question, for one of several sliders its row), in the order of the questionnaire."""
        from app import piping
        from app.items.base import plain

        survey = item.block.survey
        pos = piping._positions(survey)
        here, loop = pos.get(item.id), piping._loop_id(survey, item.block)
        found = []
        for block in survey.blocks:
            if piping._loop_id(survey, block) not in (None, loop):
                continue
            for source in block.items:
                if here is None or source.id not in pos or pos[source.id] >= here:
                    if not (source.type == "calc" and here is not None and source.id in pos and pos[source.id] == here
                            and source.position < item.position and source.block_id == item.block_id):
                        continue  # a Rechenwert may stand right before the constant sum, on its page
                question = plain(source.text("question", lang) or "") or source.kind.label
                if source.type == "calc":
                    found.append((pos[source.id], str(source.id), f"{source.code} · Rechenwert: {question[:50]}"))
                elif source.type == "text_input" and self._is_source(source):
                    found.append((pos[source.id], str(source.id), f"{source.code} · {question[:60]}"))
                elif source.type == "slider":
                    rows = source.kind.rows(source, lang)
                    for r in rows:
                        name = f"{question[:40]}: {plain(r.get('text') or '')[:30]}" if len(rows) > 1 else question[:60]
                        found.append((pos[source.id], f"{source.id}:{r['id']}", f"{source.code} · {name}"))
        return [(ref, label) for _, ref, label in sorted(found, key=lambda f: f[0])]

    def total_for(self, item, answers):
        """The total this person distributes, given the answers so far (item id -> answer): the fixed one, or
        the earlier answer rounded to a whole number; without it (or with 0, or below) the fallback total, or
        None: the question is left out."""
        c = item.config
        if not self.personal(item):
            return c.get("total", 100)
        source, row = self._source(item)
        value = (answers or {}).get(source.id) if source is not None else None
        if source is not None and source.type == "calc":
            value = source.kind.value(source, answers or {})
        if row is not None:
            value = value.get(row) if isinstance(value, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.5 <= value <= MAX_PERSONAL:
            return int(value + 0.5)
        return c.get("total_fallback") if c.get("total_missing") == "fallback" else None

    def applies(self, item, answers):
        return super().applies(item, answers) and self.total_for(item, answers) is not None

    def shown_total(self, item, answers):
        """The total on the page; where none can be known (the editor's trial view) the fallback or 100."""
        return self.total_for(item, answers) or item.config.get("total_fallback") or 100

    def own_answer(self, answer, total, options=None):
        """The stored answer if it still adds up to the person's total and names the options they see (an
        earlier answer may have changed after going back), else None: the question starts again with an even
        split."""
        if isinstance(answer, dict) and answer and sum(v or 0 for v in answer.values()) == total \
                and (options is None or set(answer) == {o["code"] for o in options}):
            return answer
        return None

    def slider_step(self, total):
        return slider_step(total)

    def remap_config(self, config, twin):
        config = super().remap_config(config, twin)  # options carried from an earlier question
        ref, sep, row = str(config.get("total_from") or "").partition(":")
        if not ref:
            return config
        new = twin(whole(ref)) if whole(ref) is not None else None
        if new is None:  # the question the total came from did not come along: a fixed total again
            return {k: v for k, v in config.items() if k not in PERSONAL_KEYS}
        return {**config, "total_from": f"{new}{sep}{row}"}

    @staticmethod
    def _reads_own_page(item, calc):
        """The Rechenwert reads (also through a chain) a question on the page of the constant sum."""
        from app import piping
        from app.items.calc import read_questions

        survey = item.block.survey
        pos = piping._positions(survey)
        here = pos.get(item.id)
        return here is not None and any(pos.get(q) == here for q in read_questions(survey, calc))

    def validate(self, item):
        problems = [p.replace("Antwortoption", "Element").replace("Antwortcodes", "Codes")
                    for p in super().validate(item)]
        if self.personal(item):
            if self._source(item)[0] is None:
                problems.append(de_en("die Frage, aus der die Gesamtsumme kommt, gibt es nicht mehr oder sie fragt keine Zahl ab",
                                      "the question the total comes from no longer exists or does not ask for a number"))
            elif item.config["total_from"] not in {ref for ref, _ in self.total_sources(item)}:
                problems.append(de_en("die Frage, aus der die Gesamtsumme kommt, muss auf einer früheren Seite stehen",
                                      "the question the total comes from must be on an earlier page"))
            elif self._source(item)[0].type == "calc" and self._reads_own_page(item, self._source(item)[0]):
                # when the page is shown, its answers are not given yet: the total is unknown, and the question
                # would be left out for everybody (or take the fallback total)
                problems.append(de_en("der Rechenwert, aus dem die Gesamtsumme kommt, liest eine Frage auf derselben Seite; "
                                      "die Summe steht erst nach dem Absenden fest. Bitte die Frage, mit der er rechnet, "
                                      "auf eine frühere Seite setzen",
                                      "the computed value the total comes from reads a question on the same page; the "
                                      "total is only known after submitting. Please put the question it calculates with "
                                      "on an earlier page"))
        return problems

    def _values(self, item, form, answers=None):
        out = {}
        for o in self.visible(item, answers):  # carried options: the ones this person sees
            v = (form.get(f"i{item.id}_{o['code']}") or "").strip()
            out[o["code"]] = whole(v)
        return out

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        total = self.total_for(item, answers)
        if total is None:
            return None  # not shown to this person (the page engine leaves the question out before it gets here)
        values = self._values(item, form, answers)
        touched = bool(form.get(f"i{item.id}_touched"))
        if not touched or any(v is None for v in values.values()):
            if item.required:
                raise AnswerError(tr(lang, "Bitte verteilen Sie die Punkte (oder bestätigen Sie die Verteilung).", "Please distribute the points (or confirm the distribution)."))
            return None
        if sum(values.values()) != total:
            raise AnswerError(tr(lang, "Die Summe muss {total} ergeben (jetzt {now}).", "The values must add up to {total} (now {now}).",
                                 total=total, now=sum(values.values())))
        return values

    def soft_warning(self, item, answer, lang):
        """The given start values, neither changed nor confirmed: asked once instead of losing them as missing (an
        optional question; UI/UX audit 2026-10-05, V37)."""
        if answer is None and not item.required:
            return tr(lang, "Die Verteilung ist noch nicht bestätigt. Wenn sie so passt, wählen Sie „Verteilung passt so“. "
                            "Mit „Weiter“ geht es ohne Antwort weiter.",
                      "The distribution has not been confirmed yet. If it is right, choose “This distribution is fine”. "
                      "“Next” continues without an answer.")
        return None

    def display_value(self, item, form, meta=None):
        values = self._values(item, form)
        return values if form.get(f"i{item.id}_touched") and all(v is not None for v in values.values()) else None

    def start_values(self, item, answer=None, total=None, answers=None):
        """{code: value} to show: the given answer, else an even split (answers: over the options this person sees)."""
        opts = self.visible(item, answers)
        if isinstance(answer, dict) and answer and set(answer) == {o["code"] for o in opts}:
            return {o["code"]: answer.get(o["code"]) or 0 for o in opts}
        total = item.config.get("total", 100) if total is None else total
        return dict(zip([o["code"] for o in opts], even_split(total, len(opts))))

    def unit_label(self, item, lang):
        """The unit next to each number field: free text, currency symbol or %."""
        c = item.config
        if c.get("format") in ("currency", "percent"):
            return "".join(units.affixes(c["format"], c.get("currency"), "", lang)).strip()
        return item.text("unit", lang)

    def amount(self, item, value, lang):
        c = item.config
        return units.fmt(value, c.get("format", "number"), c.get("currency"), item.text("unit", lang), lang)

    def ordered_options(self, item, lang, start_order=None, answers=None):
        opts = self.options(item, lang, answers)
        if start_order:
            rank = {oid: n for n, oid in enumerate(start_order)}
            return sorted(opts, key=lambda o: rank.get(o["id"], len(rank)))
        return opts

    # the even split shown at first is a complete answer: whether it was kept, its own column (UI/UX audit 2026-10-16, 105)
    kept_start_label = "Startverteilung unverändert bestätigt (1 = ja, 0 = geändert)"

    def kept_start(self, item, answer, order):
        if not isinstance(answer, dict) or not answer:
            return None
        # the options the person saw, in the order of start_values: the remainder of the split went to the first ones
        codes = [o["code"] for o in item.config.get("options", [])
                 if isinstance(answer.get(o["code"]), (int, float)) and answer.get(o["code"]) != rows.NOT_SHOWN]
        if not codes:
            return None
        total = sum(answer[c] for c in codes)
        if total != int(total):
            return 0
        return int([answer[c] for c in codes] == even_split(int(total), len(codes)))

    def order_codes(self, item):
        if not item.config.get("randomize"):
            return None
        return {o["id"]: f"{item.code}_{o['code']}" for o in item.config.get("options", [])}

    # piping: each share by its column, as participants read it ("30 %")
    def pipeable(self, item):
        return False

    def pipe_parts(self, item, lang):
        from app.items.base import plain

        labels = item.text("options", lang) or {}
        parts = [(f"{item.code}_{o['code']}", plain(labels.get(o["id"], "")) or o["code"]) for o in item.config.get("options", [])]
        return parts + ([(self.total_column(item), "Gesamtsumme")] if self.personal(item) else [])

    def total_column(self, item):
        return f"{item.code}_total"

    def answer_total(self, answer):
        """The total a person distributed: their amounts always add up to it."""
        values = [v for v in answer.values() if isinstance(v, (int, float))] if isinstance(answer, dict) else []
        return sum(values) if values else None

    def pipe_part_text(self, item, answer, name, lang):
        if self.personal(item) and name == self.total_column(item):
            total = self.answer_total(answer)
            return self.amount(item, total, lang) if total else ""
        for o in item.config.get("options", []):
            if f"{item.code}_{o['code']}" == name:
                v = (answer or {}).get(o["code"]) if isinstance(answer, dict) else None
                return self.amount(item, v, lang) if isinstance(v, (int, float)) else ""
        return ""

    def export_columns(self, item):
        labels = item.text("options") or {}
        opts = item.config.get("options", [])
        if not self.personal(item):
            total = self.amount(item, item.config.get("total", 100), "de")
            return [(f"{item.code}_{o['code']}", f"Anteil „{labels.get(o['id'], o['code'])}“ (Summe: {total})") for o in opts]
        cols = [(f"{item.code}_{o['code']}", f"Betrag „{labels.get(o['id'], o['code'])}“ (von der persönlichen Summe)") for o in opts]
        cols.append((self.total_column(item), "Persönliche Gesamtsumme, die verteilt wurde"))
        if item.config.get("export_pct"):
            cols += [(f"{item.code}_{o['code']}_pct", f"Anteil „{labels.get(o['id'], o['code'])}“ in Prozent der persönlichen Summe")
                     for o in opts]
        return cols

    def pct_columns(self, item):
        if not (self.personal(item) and item.config.get("export_pct")):
            return set()
        return {f"{item.code}_{o['code']}_pct" for o in item.config.get("options", [])}

    def codebook_info(self, item, column, lang, ui=None):
        if not self.personal(item):
            total = item.config.get("total", 100)
            return "Zahl", in_lang(ui, "0 bis {total}", "0 to {total}", total=f"{total:g}"), [in_lang(
                ui, "alle Anteile zusammen ergeben {amount}", "all shares together make {amount}",
                amount=self.amount(item, total, lang))] + self._carry_note(item, column, ui)
        source, _ = self._source(item)
        if source is not None:
            origin = in_lang(ui, "der Antwort auf „{code}“", "the answer to “{code}”", code=source.code)
        else:
            origin = in_lang(ui, "einer früheren Antwort", "an earlier answer")
        if item.config.get("total_missing") == "fallback":
            missing = in_lang(ui, "ohne diese Antwort (oder bei 0) gilt {amount}", "without this answer (or at 0) {amount} applies",
                              amount=self.amount(item, item.config['total_fallback'], lang))
        else:
            missing = in_lang(ui, "ohne diese Antwort (oder bei 0) wurde die Frage nicht gezeigt: leer",
                              "without this answer (or at 0) the question was not shown: empty")
        if column == self.total_column(item):
            return "Zahl", in_lang(ui, "ab 1", "from 1"), [in_lang(ui, "aus {origin}, auf ganze Einheiten gerundet",
                                                                   "from {origin}, rounded to whole units", origin=origin), missing]
        if column in self.pct_columns(item):
            return "Zahl", in_lang(ui, "0 bis 100", "0 to 100"), [in_lang(
                ui, "Betrag durch persönliche Gesamtsumme mal 100, eine Nachkommastelle; jede Spalte einzeln gerundet, "
                "die Summe der Prozentspalten kann um 0,1 von 100 abweichen",
                "amount divided by the personal total times 100, one decimal place; each column rounded on its own, "
                "the sum of the percentage columns can differ from 100 by 0.1")]
        return "Zahl", in_lang(ui, "0 bis zur persönlichen Gesamtsumme", "0 to the personal total"), [in_lang(
            ui, "alle Beträge zusammen ergeben {col}", "all amounts together make {col}", col=self.total_column(item)),
            missing] + self._carry_note(item, column, ui)

    def result_column(self, item, column):
        if column in self.pct_columns(item):
            return None, "", None  # the results show the amounts as shares in percent already
        return super().result_column(item, column)

    def share_of(self, item, column):
        """Results and dashboards: the column of the person's total if `column` is shown as a share in percent
        of it (the amounts of a constant sum with a personal total), else None."""
        if self.personal(item) and column != self.total_column(item) and column not in self.pct_columns(item):
            return self.total_column(item)
        return None

    def export_values(self, item, answer):
        opts = item.config.get("options", [])
        # an answer holds every option the person saw: a carried option that is not in it was not shown
        out = {f"{item.code}_{o['code']}": (answer or {}).get(o["code"], rows.NOT_SHOWN if answer and rows.partial(o) else None)
               for o in opts}
        if self.personal(item):
            total = self.answer_total(answer)
            out[self.total_column(item)] = total
            if item.config.get("export_pct"):
                for o in opts:
                    v = (answer or {}).get(o["code"])
                    out[f"{item.code}_{o['code']}_pct"] = units.round_half_up(100 * v / total, 1) if total and isinstance(v, (int, float)) else None
        return out
