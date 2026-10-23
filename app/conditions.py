"""Conditions as researchers set and read them: without answer codes.

A condition is stored as {"item": question id | "p:<URL parameter>" | "q:<quota id>" | "qc:<quota id>", "op",
"value"} and evaluated by flow.holds. This module is the other side: what a condition can be put on
(`describe`), the fields of a form for it (`from_form`, `view`: answers to tick, numbers and dates as from … to,
no code to type) and its wording (`text`: "gender: männlich", "age: 18 bis 29"). Used by the conditions of the
flow (element "Bedingung") and by the Teilquoten of a quota.
"""
import json
import re

from app import units
from app.lang import de_en, english
from app.request_values import safe_loads


class ByLanguage(dict):
    """MODES: the German table, read in the language of the page (the English one beside it has the same keys
    and order). Code that only takes the keys reads the same either way; the forms show the labels."""

    def __init__(self, de, en):
        super().__init__(de)
        self.en = en

    def __getitem__(self, key):
        return self.en[key] if english() else super().__getitem__(key)

    def get(self, key, default=None):
        return self[key] if key in self else default

    def values(self):
        return [self[k] for k in self]

    def items(self):
        return [(k, self[k]) for k in self]


# what can be asked about a question, by its kind: [(mode, label)]; the first one is the default
MODES = {
    "choice": [("is", "ist"), ("not", "ist nicht"), ("answered", "beantwortet"), ("empty", "nicht beantwortet")],
    "number": [("range", "von … bis"), ("lt", "kleiner als"), ("gt", "größer als"), ("answered", "beantwortet"),
               ("empty", "nicht beantwortet")],
    "date": [("range", "von … bis"), ("lt", "vor"), ("gt", "nach"), ("answered", "beantwortet"), ("empty", "nicht beantwortet")],
    "text": [("eq", "ist gleich"), ("ne", "ist nicht gleich"), ("contains", "enthält"), ("answered", "beantwortet"),
             ("empty", "nicht beantwortet")],
    "param": [("is", "ist"), ("not", "ist nicht"), ("answered", "vorhanden"), ("empty", "nicht vorhanden")],
    "paramtext": [("eq", "ist gleich"), ("ne", "ist nicht gleich"), ("contains", "enthält"), ("answered", "vorhanden"),
                  ("empty", "nicht vorhanden")],
    "quota": [("is", "ist"), ("not", "ist nicht")],
    "geo": [("is", "ist"), ("not", "ist nicht"), ("answered", "ermittelt"), ("empty", "nicht ermittelt")],
    "other": [("answered", "beantwortet"), ("empty", "nicht beantwortet")],
}
MODES_EN = {
    "choice": [("is", "is"), ("not", "is not"), ("answered", "answered"), ("empty", "not answered")],
    "number": [("range", "from … to"), ("lt", "less than"), ("gt", "greater than"), ("answered", "answered"),
               ("empty", "not answered")],
    "date": [("range", "from … to"), ("lt", "before"), ("gt", "after"), ("answered", "answered"), ("empty", "not answered")],
    "text": [("eq", "equals"), ("ne", "does not equal"), ("contains", "contains"), ("answered", "answered"),
             ("empty", "not answered")],
    "param": [("is", "is"), ("not", "is not"), ("answered", "present"), ("empty", "not present")],
    "paramtext": [("eq", "equals"), ("ne", "does not equal"), ("contains", "contains"), ("answered", "present"),
                  ("empty", "not present")],
    "quota": [("is", "is"), ("not", "is not")],
    "geo": [("is", "is"), ("not", "is not"), ("answered", "determined"), ("empty", "not determined")],
    "other": [("answered", "answered"), ("empty", "not answered")],
}
MODES = ByLanguage(MODES, MODES_EN)
DATE_HINT = "z. B. 23.05.2000"


def modes():
    """MODES as the condition editors show them, in the language of the page (a plain dict)."""
    return MODES_EN if english() else dict(MODES)


def date_hint():
    """The placeholder of a date field of a condition (dates are typed the German way in either language)."""
    return de_en(DATE_HINT, "e.g. 23.05.2000")


def describe(survey, lang=None, everything=False):
    """What a condition can be put on, in the order of the questionnaire: {ref: {"label", "name", "kind",
    "modes", "options": [(code, label)], "multi"}} with ref = the id of a question as text, "p:name" for a URL
    parameter, "q:<id>"/"qc:<id>" for a quota. kind: "choice" (answers to tick), "number" and "date" (from … to),
    "text" (a value to type), "other" (only answered or not). modes: the key of MODES.

    Without `everything` only what gives something to tick or a range: choices, numbers, dates and the URL
    parameters (the conditions of a Teilquote). With it also questions with free answers, every other question
    (answered or not), the Rechenwerte (numbers) and what the quota calculations decided (the conditions of the
    flow and of statements)."""
    from app import flow, panel
    from app.items.base import plain

    out = {}
    in_loop = flow.loop_blocks(survey)
    for block in survey.blocks:
        if block.id in in_loop:
            continue  # answered once per pass: a condition on such a question would never hold (flow.loop_blocks)
        for item in block.items:
            if item.kind.computed:
                # a Rechenwert (app/items/calc.py): no answer of its own, a number worked out from earlier ones
                if everything:
                    question = plain(item.text("question", lang) or "") or item.kind.label
                    out[str(item.id)] = {"label": de_en("{code} · Rechenwert: {q}", "{code} · calculated value: {q}",
                                                        code=item.code, q=question[:50]), "name": item.code,
                                         "kind": "number", "modes": "number", "options": [], "multi": False}
                continue
            if not item.kind.collects_answer or item.type == "consent":
                continue
            if item.type == "geolocation":
                # the federal state the shared position lies in (or, on a refusal, the estimate from the IP address)
                out[f"{flow.STATE_REF}{item.id}"] = {
                    "label": de_en("{code} · Bundesland (aus dem Standort)", "{code} · federal state (from the location)",
                                   code=item.code), "name": state_name(item), "kind": "choice",
                    "modes": "choice", "options": state_options(lang) + [("0", "nicht in Deutschland" if (lang or "de") == "de"
                                                                          else "not in Germany")], "multi": False}
                if everything:
                    question = plain(item.text("question", lang) or "") or item.kind.label
                    out[str(item.id)] = {"label": f"{item.code} · {question[:50]}", "name": item.code, "kind": "other",
                                         "modes": "other", "options": [], "multi": False}
                continue
            if item.type == "iat":  # the D score (items/iat.py), a number; none for a run that cannot be scored
                out[str(item.id)] = {"label": de_en("{code} · D-Wert des IAT", "{code} · D score of the IAT", code=item.code),
                                     "name": de_en("{code} (D-Wert)", "{code} (D score)", code=item.code),
                                     "kind": "number", "modes": "number", "options": [], "multi": False}
                continue
            options = flow.answer_codes(item, lang)
            if options:
                kind = "choice"
            elif (item.type == "text_input" and (item.config or {}).get("validation") in ("integer", "decimal")) \
                    or item.type == "rating" or (item.type == "date_time" and item.kind.asked(item) == "year"):
                kind = "number"
            elif item.type == "date_time":
                kind = "date"
            elif not everything and item.type != "postal_code":
                continue
            else:
                kind = "text" if item.type in ("text_input", "postal_code") else "other"
            question = plain(item.text("question", lang) or "") or item.kind.label
            if everything or item.type != "postal_code":
                # ordered: answers with an order (ordinal, metric); only there may a condition say "ab/bis" (as_codes)
                out[str(item.id)] = {"label": f"{item.code} · {question[:50]}", "name": item.code, "kind": kind, "modes": kind,
                                     "options": [(code, plain(label) or code) for code, label in options],
                                     "multi": item.type == "multiple_choice",
                                     "ordered": kind == "choice" and item.kind.level(item) in ("ordinal", "metric")}
            if item.type == "postal_code":
                # the federal state the postal code lies in: answers to tick, as with the element Bundesland
                out[f"{flow.STATE_REF}{item.id}"] = {
                    "label": de_en("{code} · Bundesland (aus der Postleitzahl)", "{code} · federal state (from the postcode)",
                                   code=item.code), "name": state_name(item), "kind": "choice",
                    "modes": "choice", "options": state_options(lang), "multi": False}
    for p in panel.config(survey)["params"]:
        allowed = p.get("allowed") or []
        out[f"p:{p['name']}"] = {"label": de_en("URL-Parameter · {name}", "URL parameter · {name}", name=p["name"]),
                                 "name": f"URL {p['name']}",
                                 "kind": "choice" if allowed else "text", "modes": "param" if allowed else "paramtext",
                                 "options": [(v, v) for v in allowed], "multi": False}
    if getattr(survey, "geo_ip", False):
        from app import geoip

        # estimated from the IP address with the agreement to take part (app/geoip.py): answers to tick
        out[geoip.COUNTRY_REF] = {"label": de_en("IP-Region · Land (geschätzt aus der IP-Adresse)",
                                                 "IP region · country (estimated from the IP address)"),
                                  "name": de_en("IP-Land", "IP country"), "kind": "choice",
                                  "modes": "geo", "options": geoip.country_options(lang), "multi": False}
        out[geoip.STATE_REF] = {"label": de_en("IP-Region · Bundesland (geschätzt aus der IP-Adresse)",
                                               "IP region · federal state (estimated from the IP address)"),
                                "name": de_en("IP-Bundesland", "IP federal state"),
                                "kind": "choice", "modes": "geo", "options": state_options(lang), "multi": False}
    if everything:
        from app import quality

        if quality.survey_checks(survey):  # the attention checks failed up to this point of the flow
            out[quality.ATTENTION_REF] = {"label": de_en("Aufmerksamkeitsprüfungen · bis hierhin", "Attention checks · up to here"),
                                          "name": de_en("Aufmerksamkeitsprüfungen", "Attention checks"),
                                          "kind": "choice", "modes": "quota", "multi": False,
                                          "options": quality.attention_options()}
        from app import tempo

        if tempo.active(survey):  # the pages sent faster than the tempo hint allows, up to this point of the flow
            out[tempo.REF] = {"label": de_en("Tempo-Hinweis · zu schnell abgeschickte Seiten bis hierhin",
                                             "Speed prompt · pages sent too fast up to here"),
                              "name": de_en("Tempo", "Speed"),
                              "kind": "choice", "modes": "quota", "multi": False, "options": tempo.flow_options()}
        for quota in survey.quotas:
            out[f"q:{quota.id}"] = {"label": de_en("Quote · {name}: Ergebnis", "Quota · {name}: result", name=quota.name),
                                    "name": de_en("Quote „{name}“", "Quota “{name}”", name=quota.name), "kind": "choice",
                                    "modes": "quota", "multi": False,
                                    "options": [("in", de_en("Platz erhalten", "got a place")),
                                                ("full", de_en("voll, kein Platz", "full, no place")),
                                                ("none", de_en("keine Teilquote", "no sub-quota"))]}
            out[f"qc:{quota.id}"] = {"label": de_en("Quote · {name}: Teilquote", "Quota · {name}: sub-quota", name=quota.name),
                                     "name": de_en("Teilquote von „{name}“", "Sub-quota of “{name}”", name=quota.name),
                                     "kind": "choice", "modes": "quota", "multi": False,
                                     "options": [(c.code, c.name) for c in quota.cells]}
    return out


def state_name(item):
    return de_en("{code} (Bundesland)", "{code} (federal state)", code=item.code)


def state_options(lang=None):
    """[(code, name)] of the 16 federal states, with the codes of the element Bundesland."""
    from app.items.federal_state import STATES

    return [(code, de if (lang or "de") == "de" else en) for code, de, en in STATES]


def page_data(described, max_conds=8, max_ranges=12):
    """What the page's script needs to build the fields of a condition when a question is chosen (app.js)."""
    return {"order": list(described), "max_conds": max_conds, "max_ranges": max_ranges, "date_hint": date_hint(),
            "modes": {key: [list(m) for m in shown] for key, shown in modes().items()},
            "items": {ref: {"label": d["label"], "kind": d["kind"], "modes": d["modes"], "options": [list(o) for o in d["options"]]}
                      for ref, d in described.items()}}


# "1,000": a thousand in English, one in German. Read as one (German, as the answers of a number field are read),
# but the pre-launch check asks to write it without a thousands separator.
_COMMA_THOUSANDS = re.compile(r"[+\-−]?[1-9]\d{0,2},\d{3}")


def read_number(value):
    """A number of a condition (the value compared with, or an answer), or None: numbers as they are, texts read
    like the answers of a number field (items/text._num, German style): "1.000" and "1000" are 1000, "1.500,50",
    "1,500.50" and "1500.5" are 1500.5, "1,5" is 1.5."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    from app.items.text import _num

    try:
        return _num(str(value), "de")
    except ValueError:
        return None


def number_problem(value):
    """Why a value typed for a number condition cannot be read for sure, or None."""
    text = str(value).strip()
    if _COMMA_THOUSANDS.fullmatch(text):
        whole = text.replace(",", "")
        return de_en("„{text}“ ist nicht eindeutig ({read} oder {whole}?) und wird als {read} gelesen; bitte ohne "
                     "Tausendertrennzeichen schreiben, z. B. {whole}",
                     "“{text}” is ambiguous ({read} or {whole}?) and is read as {read}; please write it without a "
                     "thousands separator, e.g. {whole}", text=text, whole=whole,
                     # the number read with the decimal mark of the page (1.5 in English; Mathe-Audit 2026-10-06, Z-7)
                     read=units.shown_plain(read_number(text), units.page_lang()))
    if read_number(text) is None:
        return de_en("„{text}“ ist keine Zahl; so trifft die Bedingung nie zu",
                     "“{text}” is not a number; so the condition never holds", text=text)
    return None


def _plain(number):
    """A number as a condition stores it: no thousands separator, decimal comma, no exponent ("1500,5")."""
    from decimal import Decimal

    text = format(Decimal(repr(float(number))), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text.replace(".", ",")


def number_text(value):
    """A value typed for a number condition as it is stored: "1.000" -> "1000", "1,500.50" -> "1500,5"; what
    cannot be read for sure stays as typed (the pre-launch check names it)."""
    return value if not value or number_problem(value) else _plain(read_number(value))


def is_number(item):
    """Whether conditions on this question compare numbers (kind "number" in describe, without the years of a
    date question, which items/date_time.py reads)."""
    from app import flow

    if item.kind.computed or item.type == "iat":  # a Rechenwert; the D score of an IAT
        return True
    if flow.answer_codes(item):
        return False
    return (item.type == "text_input" and (item.config or {}).get("validation") in ("integer", "decimal")) \
        or item.type == "rating"


def number_problems(cond, item):
    """What cannot be read for sure in the values of a condition on a number question: [message]."""
    from app import flow

    if item is None or not is_number(item):
        return []
    op, value = cond.get("op", "eq"), str(cond.get("value", ""))
    if op == "between":
        values = [v for v in flow.bounds(value) if v is not None]
    elif op in ("eq", "ne", "lt", "gt", "le", "ge"):
        values = [value] if value.strip() else []
    else:
        values = []
    return [p for p in (number_problem(v) for v in values) if p]


def range_cond(ref, lo, hi):
    """from … to as a condition: both (between, or = when equal), only from (≥), only to (≤)."""
    if lo and hi:
        return {"item": ref, "op": "eq", "value": lo} if lo == hi else {"item": ref, "op": "between", "value": f"{lo}..{hi}"}
    return {"item": ref, "op": "ge" if lo else "le", "value": lo or hi}


ORDERED_OPS = ("lt", "gt", "le", "ge", "between")


def as_codes(cond, info):
    """An ordered comparison ("ab", "bis", "von … bis") on answers without an order, e.g. "geschlecht: ab
    divers" from an older survey file, as the answers it takes in: "in"/"eq" with their codes, so the form shows
    ticks and a new order of the options cannot change who counts (UI audit 2026-10-12, U18). The codes are
    compared as numbers, as flow.holds does. Unchanged for any other condition, for answers with an order and
    when a code or the value is no number or no answer is taken in."""
    from app import flow

    op = cond.get("op") if isinstance(cond, dict) else None
    if not info or info.get("kind") != "choice" or info.get("ordered") or op not in ORDERED_OPS:
        return cond
    value = str(cond.get("value", ""))
    lo, hi = flow.bounds(value) if op == "between" else (value, value)
    lo, hi = read_number(lo), read_number(hi)
    if lo is None or hi is None:
        return cond
    test = {"lt": lambda n: n < hi, "gt": lambda n: n > lo, "le": lambda n: n <= hi, "ge": lambda n: n >= lo,
            "between": lambda n: lo <= n <= hi}[op]
    numbers = [(code, read_number(code)) for code, _ in info["options"]]
    if any(n is None for _, n in numbers):
        return cond
    codes = [code for code, n in numbers if test(n)]
    if not codes:
        return cond
    if len(codes) == 1 and not info.get("multi"):
        return {**cond, "op": "eq", "value": codes[0]}
    return {**cond, "op": "in", "value": ", ".join(codes)}


def from_form(form, prefix, described):
    """One condition of a form: the question ("item"), what is asked about it ("mode", see MODES; without the
    field the default of the question's kind) and what was ticked or entered ("codes", "from"/"to", "value").
    prefix: the fields are named "<prefix>_item" and so on; "" for a form with one condition. A condition the
    form cannot show (stored with another comparison) comes back as it was (field "raw") unless something new
    was entered. None: no question chosen."""
    def name(field):
        return f"{prefix}_{field}" if prefix else field

    key = form.get(name("item"), "")
    info = described.get(key)
    try:  # safe_loads: a value nested too deeply is a ValueError too (audit 2026-10-11, N9)
        raw = safe_loads(form.get(name("raw"), "") or "null")
    except ValueError:
        raw = None
    raw = _stored(raw) if isinstance(raw, dict) and key and str(raw.get("item")) == key else None
    if info is None:
        # a question that is not offered (any more): the stored condition stays, so the pre-launch check can name it
        return raw
    ref = int(key) if key.isdigit() else key
    empty = raw or {"item": ref, "op": "eq", "value": ""}
    allowed = [mode for mode, _ in MODES[info["modes"]]]
    mode = form.get(name("mode"), "")
    mode = mode if mode in allowed else allowed[0]
    if mode in ("answered", "empty"):
        return {"item": ref, "op": mode, "value": ""}
    if info["kind"] == "choice":
        known = [code for code, _ in info["options"]]
        codes = [c for c in form.getlist(name("codes")) if c in known]
        if not codes:
            return empty
        if len(codes) == 1 and not info["multi"]:
            return {"item": ref, "op": "ne" if mode == "not" else "eq", "value": codes[0]}
        return {"item": ref, "op": "nin" if mode == "not" else "in", "value": ", ".join(codes)}
    if info["kind"] in ("number", "date"):
        typed = number_text if info["kind"] == "number" else str
        if mode in ("lt", "gt"):
            value = typed(form.get(name("value"), "").strip()[:40])
            return {"item": ref, "op": mode, "value": value} if value else empty
        lo, hi = typed(form.get(name("from"), "").strip()[:40]), typed(form.get(name("to"), "").strip()[:40])
        return range_cond(ref, lo, hi) if lo or hi else empty
    value = form.get(name("value"), "").strip()[:200]
    return {"item": ref, "op": mode, "value": value} if value or not raw else raw


def _stored(raw):
    """A condition that came back through the form's field "raw": only what a stored condition can hold."""
    from app import flow

    ref = raw.get("item")
    if isinstance(ref, bool) or not isinstance(ref, (int, str)) or len(str(ref)) > 60:
        return None
    return {"item": ref, "op": raw.get("op") if raw.get("op") in flow.OPS else "eq", "value": str(raw.get("value", ""))[:200]}


def own_item(survey, item_id):
    """The question with this id if it belongs to this survey, else None. A condition names its question by id,
    and ids count on across all surveys: a question of another survey must never be read through one."""
    from app.extensions import db
    from app.models import Item

    item = db.session.get(Item, item_id) if isinstance(item_id, int) and survey is not None else None
    return item if item is not None and item.block.survey_id == survey.id else None


def view(cond, described, modes=True):
    """A stored condition as the form shows it: {"ref", "kind", "mode", "codes", "lo", "hi", "value", "special",
    "raw"}. special: the form has no fields for this condition (another comparison, or a question that is not
    offered); it is shown in words and kept as it is until something else is chosen. modes=False: a form
    without the choice of what is asked (the Teilquoten): everything but the default is special."""
    from app import flow

    ref = str(cond.get("item"))
    info = described.get(ref)
    cond = as_codes(cond, info)  # "ab divers" on answers without an order: ticks (U18)
    op, value = cond.get("op", "eq"), str(cond.get("value", ""))
    out = {"ref": ref, "kind": info["kind"] if info else None, "mode": "", "codes": [], "lo": "", "hi": "", "value": "",
           "special": False, "raw": json.dumps(cond, ensure_ascii=False)}
    allowed = [mode for mode, _ in MODES[info["modes"]]] if info else []
    kind = info["kind"] if info else None
    if op in ("answered", "empty"):
        out["mode"] = op
    elif kind == "choice" and op in ("eq", "in", "ne", "nin"):
        out["mode"] = "is" if op in ("eq", "in") else "not"
        out["codes"] = flow.code_list(value) if op in ("in", "nin") else [value]
    elif kind in ("number", "date") and op == "eq":
        out["mode"], out["lo"], out["hi"] = "range", value, value
    elif kind in ("number", "date") and op == "between" and flow.bounds(value)[0] is not None:
        out["mode"] = "range"
        out["lo"], out["hi"] = flow.bounds(value)
    elif kind in ("number", "date") and op in ("ge", "le"):
        out["mode"] = "range"
        out["lo" if op == "ge" else "hi"] = value
    elif kind in ("number", "date") and op in ("lt", "gt"):
        out["mode"], out["value"] = op, value
    elif kind == "text" and op in ("eq", "ne", "contains"):
        out["mode"], out["value"] = op, value
    if out["mode"] not in allowed or (not modes and out["mode"] != allowed[0]):
        out["special"] = True
    return out


def text(survey, cond, lang=None, items=None, short=False, shown=False, ui=None):
    """One condition as people read it, with the wording of the answers instead of their codes: "gender:
    männlich", "age: 18 bis 29", "age: ab 50", "URL src: a oder b", "Quote „Alter“: voll". short: without the
    name of the question (for the name of a Teilquote). items: {id: Item} if they are at hand. shown: the words
    around the answers in the language of the page (for a page that only shows it); else German, as names of
    Teilquoten and the files keep them. ui: the words around the answers in this language ("de"/"en"), for a
    downloaded file written for a reader (the codebook, export.codebook(lang=))."""
    from app import flow
    from app.items.base import plain

    def say(de, en, **values):
        if ui:
            text = en if ui == "en" else de
            return text.format(**values) if values else text
        return de_en(de, en, **values) if shown else (de.format(**values) if values else de)

    words_en = ui == "en" if ui else (shown and english())  # the language of the labels of Askurious's own values

    if isinstance(cond, dict) and isinstance(cond.get("conds"), list):  # several: "… und …" / "… oder …"
        joined = say(" oder ", " or ") if cond.get("rule") == "any" else say(" und ", " and ")
        return (joined.join(text(survey, c, lang, items, short, shown, ui) for c in flow.parts(cond))
                or say("Bedingung unvollständig", "Incomplete condition"))
    ref, op, value = cond.get("item"), cond.get("op", "eq"), str(cond.get("value", ""))
    date = False
    found = "beantwortet"  # how "answered" reads: "vorhanden" for a URL parameter, "ermittelt" for an estimate
    if isinstance(ref, str) and ref.startswith(("q:", "qc:")):
        kind, _, qid = ref.partition(":")
        quota = next((q for q in survey.quotas if str(q.id) == qid), None) if survey is not None else None
        if quota is None:
            return say("Bedingung auf eine Quote, die es nicht gibt", "Condition on a quota that does not exist")
        if kind == "q":
            name = say("Quote „{name}“", "Quota “{name}”", name=quota.name)
            labels = {"in": say("Platz erhalten", "got a place"), "full": say("voll", "full"),
                      "none": say("keine Teilquote", "no sub-quota")}
        else:
            name, labels = say("Teilquote von „{name}“", "Sub-quota of “{name}”", name=quota.name), {c.code: c.name for c in quota.cells}
    elif ref == "att":
        from app import quality

        name, labels = say("Aufmerksamkeitsprüfungen", "Attention checks"), dict([(k, quality.ATTENTION_OPTIONS_EN[k]) for k, _ in quality.ATTENTION_OPTIONS] if words_en else quality.ATTENTION_OPTIONS)
    elif ref == "tempo":
        from app import tempo

        name, labels = say("Tempo", "Speed"), dict([(k, tempo.FLOW_OPTIONS_EN[k]) for k, _ in tempo.FLOW_OPTIONS] if words_en else tempo.FLOW_OPTIONS)
    elif ref in ("geo:country", "geo:state"):
        from app import geoip

        found = "ermittelt"
        name, labels = ((say("IP-Land", "IP country"), dict(geoip.country_options(lang))) if ref == geoip.COUNTRY_REF
                        else (say("IP-Bundesland", "IP federal state"), dict(state_options(lang))))
    elif isinstance(ref, str) and ref.startswith("p:"):
        found = "vorhanden"
        name, labels = f"URL {ref[2:]}", {}
    elif isinstance(ref, str) and ref.startswith(flow.STATE_REF):
        source = flow.source_id(ref)
        item = (items or {}).get(source) if items is not None else own_item(survey, source)
        if item is None:
            return say("Bedingung unvollständig", "Incomplete condition")
        name, labels = say("{code} (Bundesland)", "{code} (federal state)", code=item.code), dict(state_options(lang))
    else:
        item = (items or {}).get(ref) if items is not None else own_item(survey, ref)
        if item is None:
            return say("Bedingung unvollständig", "Incomplete condition")
        name, labels = item.code, {code: plain(label) for code, label in flow.answer_codes(item, lang)}
        date = item.type == "date_time" and item.kind.asked(item) != "year"
        if item.type == "iat":  # "Q1 (D-Wert): über 0,35"; no D score: "nicht ermittelt" (not done, or not scorable)
            name = say("{code} (D-Wert)", "{code} (D score)", code=item.code)
            found = "ermittelt"

    def shown_value(v):
        return labels.get(v) or v
    either = say(" oder ", " or ")
    if op in ("answered", "empty"):
        words = {"beantwortet": ("beantwortet", "answered", "nicht beantwortet", "not answered"),
                 "vorhanden": ("vorhanden", "present", "nicht vorhanden", "not present"),
                 "ermittelt": ("ermittelt", "determined", "nicht ermittelt", "not determined")}[found]
        said = say(words[0], words[1]) if op == "answered" else say(words[2], words[3])
    elif op == "between":
        lo, hi = flow.bounds(value)
        said = say("{lo} bis {hi}", "{lo} to {hi}", lo=lo, hi=hi) if lo is not None else say("von … bis {v}", "from … to {v}", v=value)
    elif op == "in":
        said = either.join(shown_value(v) for v in flow.code_list(value))
    elif op == "nin":
        said = say("nicht {v}", "not {v}", v=either.join(shown_value(v) for v in flow.code_list(value)))
    elif op == "contains":
        said = say("enthält {v}", "contains {v}", v=shown_value(value))
    else:
        words = {"eq": ("{v}", "{v}"), "ne": ("nicht {v}", "not {v}"), "ge": ("ab {v}", "from {v}"), "le": ("bis {v}", "up to {v}"),
                 "gt": ("nach {v}", "after {v}") if date else ("über {v}", "above {v}"),
                 "lt": ("vor {v}", "before {v}") if date else ("unter {v}", "below {v}")}
        de, en = words.get(op, ("{v}", "{v}"))
        said = say(de, en, v=shown_value(value))
    return said if short else f"{name}: {said}"
