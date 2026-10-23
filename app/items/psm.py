"""Van Westendorp Price Sensitivity Meter (PSM): four open price questions about one product.

Participants read a product description and give four prices: too cheap (TC, doubts about
quality), cheap (C, a bargain), expensive (E, still considered) and too expensive (TE, not
considered). The questions come in the order TC → TE (default) or TE → TC.

Input: number fields with currency (or a free unit), comma or point as decimal separator,
thousands separators allowed ("1.299,00", "1,299.00"); never negative, optional min/max,
at most `decimals` decimal places. Consistency: no price below the one before, TC <= C <= E <= TE (equal neighbours
are allowed, as in the analysis, app/psm_analysis.py; audit 2026-10-05).
`consistency` decides what an inconsistent answer does: "required" (default) blocks the page
with an explanation, "warn" shows a soft warning once (like a nudge) and then accepts it,
"off" accepts it silently. The same checks run in the browser (participant.js, [data-psm])
and again on the server.

Newton-Miller-Smith extension (config "nms"): two more questions on the same page, how likely
the participant would buy at their own "cheap" and "expensive" price ({price} in the question is
replaced by the typed price, live in the browser). Five answers, 1 = very unlikely … 5 = very
likely; the analysis turns them into purchase probabilities with `nms_weights` (Festlegung von
Askurious: common practice weights for the five-point purchase intention, 0, 10, 30, 50, 70 %;
Newton, Miller & Smith, 1993, is the source of the method itself).

Export: four numeric columns <code>_tc, <code>_c, <code>_e, <code>_te; with NMS also
<code>_nms_c and <code>_nms_e (1 to 5). Curves, price points and the NMS trial and revenue
curves on the results page: app/psm_analysis.py."""
import re

from markupsafe import Markup, escape

from app import units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en, english
from app.items.base import r_flag, r_int, r_list, r_num, r_one  # rules for survey files, see base.py
from app.items.base import MAX_NUMBER, AnswerError, ItemType, fill, whole, read_cfg
from app.ui_i18n import pick, tr

KEYS = ("tc", "c", "e", "te")  # canonical order: prices must not fall along it
ORDERS = {"asc": "zu billig → zu teuer", "desc": "zu teuer → zu billig"}
CONSISTENCY = {
    "required": "Pflicht: Seite erst mit aufsteigenden Preisen verlassen",
    "warn": "Hinweis: einmal warnen, dann weiter",
    "off": "keine Prüfung",
}
# the same lists for the English editor (properties of PriceSensitivity)
ORDERS_EN = {"asc": "too cheap → too expensive", "desc": "too expensive → too cheap"}
CONSISTENCY_EN = {"required": "Required: the page can only be left with rising prices",
                  "warn": "Note: warn once, then continue", "off": "no check"}
MAX_DECIMALS = 4
# Neither proceedings volume has a DOI; title, congress and pages as they are commonly cited.
# The NMS paper (APA 7: proceedings with their publisher, no place) has no pages in any listing found; audit
# 2026-10-05 (P-5)
CITATION = ("Van Westendorp, P. (1976). NSS-Price Sensitivity Meter (PSM): A new approach to study consumer "
            "perception of price. Proceedings of the 29th ESOMAR Congress, 139–167.")
NMS_CITATION = ("Newton, D., Miller, J., & Smith, P. (1993). A market acceptance extension to traditional price "
                "sensitivity measurement. In Proceedings of the American Marketing Association Advanced Research "
                "Techniques Forum. American Marketing Association.")
NMS_KEYS = ("c", "e")  # purchase probability at the "cheap" and at the "expensive" price
NMS_WEIGHTS = [0, 10, 30, 50, 70]  # percent for answers 1 … 5 (Festlegung von Askurious, common practice weights)
NMS_QUESTIONS = {
    "de": {"c": "Wie wahrscheinlich würden Sie das Produkt zum Preis von {price} kaufen?",
           "e": "Und wie wahrscheinlich würden Sie es zum Preis von {price} kaufen?"},
    "en": {"c": "How likely would you be to buy the product at a price of {price}?",
           "e": "And how likely would you be to buy it at a price of {price}?"},
}
NMS_LABELS = {  # the usual 5-point purchase-intent scale (1 = definitely not … 5 = definitely)
    "de": ["würde ich bestimmt nicht kaufen", "würde ich wahrscheinlich nicht kaufen",
           "würde ich vielleicht kaufen, vielleicht nicht", "würde ich wahrscheinlich kaufen", "würde ich bestimmt kaufen"],
    "en": ["definitely would not buy", "probably would not buy", "might or might not buy", "probably would buy",
           "definitely would buy"],
}
NMS_FALLBACK = {"de": {"c": "Ihrem Preis „günstig“", "e": "Ihrem Preis „teuer“"},
                "en": {"c": "your “cheap” price", "e": "your “expensive” price"}}

NAMES = {
    "de": {"tc": "zu billig", "c": "günstig", "e": "teuer", "te": "zu teuer"},
    "en": {"tc": "too cheap", "c": "cheap", "e": "expensive", "te": "too expensive"},
}

# English: the four questions of Van Westendorp (1976) in their usual short wording (CITATION); German: close
# working translation
QUESTIONS = {
    "de": {
        "tc": "Bei welchem Preis wäre das Produkt so billig, dass Sie an seiner Qualität zweifeln würden?",
        "c": "Bei welchem Preis wäre das Produkt ein Schnäppchen, also ein sehr guter Kauf für das Geld?",
        "e": "Bei welchem Preis würde das Produkt allmählich teuer, sodass ein Kauf zwar nicht ausgeschlossen ist, "
             "Sie aber darüber nachdenken müssten?",
        "te": "Bei welchem Preis wäre das Produkt so teuer, dass Sie einen Kauf nicht in Betracht ziehen würden?",
    },
    "en": {
        "tc": "At what price would you consider the product to be priced so low that you would feel the quality "
              "couldn't be very good?",
        "c": "At what price would you consider the product to be a bargain—a great buy for the money?",
        "e": "At what price would you consider the product starting to get expensive, so that it is not out of the "
             "question, but you would have to give some thought to buying it?",
        "te": "At what price would you consider the product to be so expensive that you would not consider buying it?",
    },
}

MESSAGES = {
    "de": {
        "number": "„{name}“: Bitte geben Sie einen Preis als Zahl ein, z. B. 12,50.",
        "negative": "„{name}“: Der Preis darf nicht negativ sein.",
        "decimals0": "„{name}“: Bitte geben Sie einen Preis ohne Nachkommastellen ein.",
        "decimals": "„{name}“: Bitte höchstens {n} Nachkommastellen.",
        "min": "„{name}“: Bitte geben Sie mindestens {lo} ein.",
        "max": "„{name}“: Bitte geben Sie höchstens {hi} ein.",
        "required": "Bitte geben Sie alle vier Preise an.",
        "nms_required": "Bitte geben Sie auch an, wie wahrscheinlich Sie das Produkt zu diesen Preisen kaufen würden.",
        "order": "Die Preise sollten von „zu billig“ über „günstig“ und „teuer“ bis „zu teuer“ ansteigen. "
                 "Jetzt ist „{hi_name}“ ({hi}) niedriger als „{lo_name}“ ({lo}).",
        "order_fix": " Bitte passen Sie Ihre Angaben an.",
        "order_warn": " Bitte prüfen Sie Ihre Angaben; Sie können sie auch so übernehmen.",
    },
    "en": {
        "number": "“{name}”: Please enter a price as a number, e.g. 12.50.",
        "negative": "“{name}”: The price cannot be negative.",
        "decimals0": "“{name}”: Please enter a price without decimals.",
        "decimals": "“{name}”: Please use at most {n} decimal places.",
        "min": "“{name}”: Please enter at least {lo}.",
        "max": "“{name}”: Please enter at most {hi}.",
        "required": "Please enter all four prices.",
        "nms_required": "Please also say how likely you would be to buy the product at these prices.",
        "order": "The prices should rise from “too cheap” via “cheap” and “expensive” to “too expensive”. "
                 "Now “{hi_name}” ({hi}) is lower than “{lo_name}” ({lo}).",
        "order_fix": " Please adjust your answers.",
        "order_warn": " Please check your answers; you can also keep them as they are.",
    },
}

_CLEAN = re.compile(r"[\s  '’€$£]|CHF|EUR|USD|GBP", re.I)


def parse_price(value, decimals=2, lang=None):
    """'12,50', '12.50', '1.299,00', '1,299.00', '1 299', '12 €' -> float; raises ValueError.

    A single separator followed by exactly three digits is a thousands separator when fewer than
    three decimals are allowed ("1.000" = 1000 in German), otherwise a decimal separator. Not after
    a leading zero: "0,999" is 0.999 (then too many decimal places), never 999. With the language of the
    participant (`lang`) only its own thousands separator counts so: "1,500" is 1500 in English and 1,5 in
    German (units.writes_like_english). Both separators: the thousands in groups of three ("1,5.3" is no price).
    A dash as autocorrection writes it ("–5") is a minus like "−"."""
    v = _CLEAN.sub("", str(value or "")).replace("−", "-").replace("–", "-")
    if not v:
        raise ValueError("empty")
    if "," in v and "." in v:
        dec = "," if v.rfind(",") > v.rfind(".") else "."
        group = "." if dec == "," else ","
        if not re.fullmatch(r"-?\d{1,3}(" + re.escape(group) + r"\d{3})+" + re.escape(dec) + r"\d*", v):
            raise ValueError(value)  # "1,5.3", "1.2,5": thousands not in groups of three
        v = v.replace(group, "").replace(dec, ".")
    elif v.count(",") > 1 or v.count(".") > 1:
        if not re.fullmatch(r"-?\d{1,3}([.,]\d{3})+", v):
            raise ValueError(value)  # "1.2.3" is no number
        v = v.replace(",", "").replace(".", "")  # 1.000.000
    else:
        sep = "," if "," in v else "." if "." in v else ""
        if sep:
            head, tail = v.split(sep)
            own = lang is None or sep == ("," if units.writes_like_english(lang) else ".")  # the language's thousands
            if own and len(tail) == 3 and decimals < 3 and head.lstrip("-") and not head.lstrip("-").startswith("0"):
                v = head + tail
            else:
                v = f"{head}.{tail}"
    if not re.fullmatch(r"-?(\d+\.?\d*|\.\d+)", v) or float(v) > MAX_NUMBER:
        raise ValueError(value)
    return float(v)


def _places(number):
    text = f"{number:.10f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


@register
class PriceSensitivity(ItemType):
    key = "psm"
    # the currency names every stored price; NMS switched off takes its columns out (audit 2026-10-12, M-3)
    read_keys = ("currency", "nms")
    import_rules = {"currency": r_one("", *units.CURRENCIES), "order": r_one(*ORDERS), "consistency": r_one(*CONSISTENCY),
                    "decimals": r_int(0, MAX_DECIMALS), "nms": r_flag, "nms_weights": r_list(5, r_num(0, 100), low=5),
                    "min": r_num(0, MAX_NUMBER, none=True), "max": r_num(0, MAX_NUMBER, none=True)}
    ratio_scale = True
    label = Both("Preisbereitschaft (Van Westendorp)", "Price sensitivity (Van Westendorp)")
    short = Both("Vier Preisfragen zu einem Produkt", "Four price questions on a product")
    description = Both("Vier offene Preisfragen zu einem Produkt: zu billig, günstig, teuer, zu teuer",
                       "Four open price questions about a product: too cheap, cheap, expensive, too expensive")
    order = 38
    fact_sheet = FactSheet(
        "scales",
        keywords=["Van Westendorp", "Price sensitivity meter", "PSM", "Preis", "Price", "Zahlungsbereitschaft",
                  "Willingness to pay"],
        can=[("Vier Preisfragen, aufsteigend oder absteigend.", "Four price questions, ascending or descending."),
             ("Währung frei wählbar.", "Any currency."),
             ("Optional mit Kaufwahrscheinlichkeit nach Newton et al. (1993).",
              "Optionally with purchase likelihood after Newton et al. (1993).")],
        good_for=[("Akzeptable Preisspanne für ein Produkt oder einen Dienst",
                   "The acceptable price range for a product or service")],
        not_for=[("Wie Preis und andere Merkmale gegeneinander abgewogen werden (dann Conjoint)",
                  "How price and other attributes are traded off (then Conjoint)")],
        designs=["cross", "choice"],
        examples=[("Ab welchem Preis wäre Ihnen ein Abo für Lernvideos zu teuer?",
                   "At what price would a subscription to learning videos be too expensive?")],
    )
    currencies = units.CURRENCIES

    # the lists of the editor, labelled in the language of the page
    @property
    def orders(self):
        return ORDERS_EN if english() else ORDERS

    @property
    def consistency_modes(self):
        return CONSISTENCY_EN if english() else CONSISTENCY

    keys = KEYS
    result_kind = "auto"

    def default_config(self):
        return {"currency": "EUR", "order": "asc", "min": None, "max": None, "decimals": 2, "consistency": "required",
                "nms": False, "nms_weights": list(NMS_WEIGHTS)}

    def default_texts(self, lang):
        q = pick(QUESTIONS, lang)
        nq = pick(NMS_QUESTIONS, lang)
        return {"question": "", "product": "", "unit": "", **{f"q_{k}": q[k] for k in KEYS},
                **{f"nms_{k}": nq[k] for k in NMS_KEYS}, "nms_labels": list(pick(NMS_LABELS, lang))}

    def references(self, item):
        return [CITATION] + ([NMS_CITATION] if self.nms(item) else [])

    def source_groups(self, item):
        return [(de_en("Price Sensitivity Meter: die vier Preisfragen", "Price Sensitivity Meter: the four price questions"),
                 [CITATION]),
                (de_en("Zusatzfragen zur Kaufwahrscheinlichkeit (Umrechnung der Antworten: Festlegung von Askurious)",
                       "Follow-up questions on the purchase probability (conversion of the answers: Askurious default)"),
                 [NMS_CITATION])]

    def wording_changes(self, item):
        """Where the four questions differ from the standard wording: ["DE: TC, TE", ...]. Only languages
        with a standard wording (German, English) are compared; whitespace does not count."""
        out = []
        for lang, standard in QUESTIONS.items():
            if lang not in (item.texts or {}):
                continue
            lt = item.lang_texts(lang)  # a standard wording is not stored (S9)
            changed = [k.upper() for k in KEYS if f"q_{k}" in lt
                       and " ".join((lt.get(f"q_{k}") or "").split()) != " ".join(standard[k].split())]
            if changed:
                out.append(f"{lang.upper()}: {', '.join(changed)}")
        return out

    def nms(self, item):
        return bool(read_cfg(item).get("nms"))

    def nms_weights(self, item):
        w = read_cfg(item).get("nms_weights") or NMS_WEIGHTS
        return list(w) if len(w) == 5 else list(NMS_WEIGHTS)

    def nms_labels(self, item, lang):
        labels = item.text("nms_labels", lang) or []
        default = pick(NMS_LABELS, lang)
        return [(labels[n] if n < len(labels) and labels[n] else default[n]) for n in range(5)]

    def nms_question(self, item, key, lang, shown=None, variables=None):
        """The NMS question; {price} becomes a span the browser fills with the typed price."""
        text = item.text(f"nms_{key}", lang) or pick(NMS_QUESTIONS, lang)[key]
        value = shown.get(key) if isinstance(shown, dict) else None
        if isinstance(value, (int, float)):
            price = self.amount(item, value, lang)
        elif isinstance(value, str) and value:
            price = f"{value} {self.unit_label(item, lang)}".strip()
        else:
            price = ""
        fallback = pick(NMS_FALLBACK, lang)[key]
        span = (f'<strong data-psm-price="{key}" data-fallback="{escape(fallback)}">'
                f"{escape(price or fallback)}</strong>")
        return Markup(str(fill(text, variables)).replace("{price}", span))

    # --- editor -------------------------------------------------------------------

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt.update(question=form.get("question", "").strip(), product=form.get("product", "").strip()[:5000],
                  unit=form.get("unit", "").strip()[:30])
        for k in KEYS:
            if f"q_{k}" in form:  # a form without the question fields keeps them
                lt[f"q_{k}"] = form.get(f"q_{k}", "").strip()[:500]
        for k in NMS_KEYS:
            if f"nms_{k}" in form:
                lt[f"nms_{k}"] = form.get(f"nms_{k}", "").strip()[:500]
        if "nms_label" in form:
            lt["nms_labels"] = [v.strip()[:80] for v in form.getlist("nms_label")][:5][::-1]  # the editor lists 5 … 1
        texts[lang] = lt
        item.texts = texts
        if not structural:
            return
        c = dict(item.config or {})
        currency = form.get("currency", "EUR")
        c["currency"] = currency if currency in units.CURRENCIES else ""  # "" = own unit text
        c["order"] = form.get("order") if form.get("order") in ORDERS else "asc"
        c["consistency"] = form.get("consistency") if form.get("consistency") in CONSISTENCY else "required"
        d = form.get("decimals", "").strip()
        c["decimals"] = min(whole(d), MAX_DECIMALS) if whole(d) is not None else 2
        c["nms"] = bool(form.get("nms"))
        # the researcher's numbers in the language of the page, with a note on "1,000" in German (units.read_entry;
        # Mathe-Audit 2026-10-06, Z-4: the bound "1.000" was read as 1 €)
        if "nms_weight" in form:
            old = list(c.get("nms_weights") or [])
            w = [units.read_entry(v, known=old) for v in form.getlist("nms_weight")][::-1]
            c["nms_weights"] = [min(max(x, 0), 100) for x in w] if len(w) == 5 and None not in w else list(NMS_WEIGHTS)
        for k in ("min", "max"):
            c[k] = units.read_entry(form.get(k, ""), known=c.get(k))
        item.config = c

    def validate(self, item):
        c = read_cfg(item)
        problems = []
        if c.get("min") is not None and c["min"] < 0:
            problems.append(de_en("Mindestpreis ist negativ", "minimum price is negative"))
        if c.get("min") is not None and c.get("max") is not None and c["min"] >= c["max"]:
            problems.append(de_en("Mindestpreis ist nicht kleiner als der Höchstpreis", "minimum price is not smaller than the maximum price"))
        if not (item.text("product") or "").strip():
            problems.append(de_en("Produktbeschreibung fehlt", "product description missing"))
        names = NAMES["en" if english() else "de"]
        missing = [names[k] for k in KEYS if not (item.text(f"q_{k}") or "").strip()]
        if missing:
            problems.append(de_en("Fragetext fehlt: {names}", "question text missing: {names}", names=", ".join(missing)))
        if self.nms(item):
            w = self.nms_weights(item)
            if any(b < a for a, b in zip(w, w[1:])):
                problems.append(de_en("NMS-Gewichte steigen nicht von „sehr unwahrscheinlich“ zu „sehr wahrscheinlich“",
                                      "NMS weights do not rise from “very unlikely” to “very likely”"))
            for k in NMS_KEYS:
                if "{price}" not in (item.text(f"nms_{k}") or ""):
                    problems.append(de_en("NMS-Frage „{name}“ ohne Platzhalter {{price}}", "NMS question “{name}” without the placeholder {{price}}",
                                          name=names[k]))
        return problems

    # --- participant side -----------------------------------------------------------

    def sequence(self, item):
        """The four keys in the order they are asked."""
        return KEYS[::-1] if read_cfg(item).get("order") == "desc" else KEYS

    def name(self, key, lang):
        return pick(NAMES, lang)[key]

    def unit_label(self, item, lang):
        cur = read_cfg(item).get("currency")
        if cur in units.CURRENCIES:
            return units.CURRENCIES[cur]
        return item.text("unit", lang) or ""

    def amount(self, item, value, lang, full=False):
        """A price as participants read it: German 1.299,50 €, English €1,299.50. `full`: a price with decimals
        shows all the decimal places the element allows (0,80 €, not 0,8 €)."""
        if value is None:
            return ""
        cur = read_cfg(item).get("currency")
        places = 0 if float(value).is_integer() else min(max(_places(float(value)), 0), MAX_DECIMALS)
        if full and places:
            places = min(max(places, read_cfg(item).get("decimals", 2)), MAX_DECIMALS)
        if cur in units.CURRENCIES:
            return units.fmt(value, "currency", cur, "", lang, places)
        return units.fmt(value, "number", "EUR", item.text("unit", lang) or "", lang, places)

    def field_value(self, item, shown, key, lang):
        """What to put into a field: a stored number in the participant's notation, or the raw
        input after a failed check."""
        v = (shown or {}).get(key) if isinstance(shown, dict) else None
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        text = f"{v:.{MAX_DECIMALS}f}".rstrip("0").rstrip(".") if not float(v).is_integer() else str(int(v))
        return text if units.writes_like_english(lang) else text.replace(".", ",")

    def input_mode(self, item):
        return "decimal" if read_cfg(item).get("decimals", 2) else "numeric"

    def range_hint(self, item, lang):
        c = read_cfg(item)
        lo, hi = c.get("min"), c.get("max")
        if lo is not None and hi is not None:
            return tr(lang, "Preise von {lo} bis {hi}", "Prices from {lo} to {hi}", lo=self.amount(item, lo, lang),
                      hi=self.amount(item, hi, lang))
        if lo is not None:
            return tr(lang, "Preise ab {p}", "Prices from {p}", p=self.amount(item, lo, lang))
        if hi is not None:
            return tr(lang, "Preise bis {p}", "Prices up to {p}", p=self.amount(item, hi, lang))
        return ""

    def client_messages(self, item, lang):
        """Texts and limits for the check in the browser (participant.js)."""
        m = pick(MESSAGES, lang)
        c = read_cfg(item)
        return {"m": m, "names": pick(NAMES, lang), "min": c.get("min"), "max": c.get("max"),
                "decimals": c.get("decimals", 2), "consistency": c.get("consistency", "required"),
                "lo": self.amount(item, c.get("min"), lang), "hi": self.amount(item, c.get("max"), lang),
                "unit": self.unit_label(item, lang), "keys": list(KEYS),
                "point": units.writes_like_english(lang)}  # which separator counts for thousands (parse_price)

    def _check_value(self, item, key, raw, lang):
        m = pick(MESSAGES, lang)
        c = read_cfg(item)
        name = self.name(key, lang)
        places = c.get("decimals", 2)
        try:
            value = parse_price(raw, places, lang)
        except ValueError:
            raise AnswerError(m["number"].format(name=name)) from None
        if value < 0:
            raise AnswerError(m["negative"].format(name=name))
        if _places(value) > places:
            raise AnswerError((m["decimals0"] if places == 0 else m["decimals"]).format(name=name, n=places))
        if c.get("min") is not None and value < c["min"]:
            raise AnswerError(m["min"].format(name=name, lo=self.amount(item, c["min"], lang)))
        if c.get("max") is not None and value > c["max"]:
            raise AnswerError(m["max"].format(name=name, hi=self.amount(item, c["max"], lang)))
        return int(value) if places == 0 else value

    def order_problem(self, item, answer, lang):
        """The first violation of TC <= C <= E <= TE among the given prices, as a sentence (or None)."""
        m = pick(MESSAGES, lang)
        given = [(k, answer[k]) for k in KEYS if isinstance(answer, dict) and answer.get(k) is not None]
        for (k1, v1), (k2, v2) in zip(given, given[1:]):
            if v2 < v1:
                return m["order"].format(lo_name=self.name(k1, lang), lo=self.amount(item, v1, lang),
                                         hi_name=self.name(k2, lang), hi=self.amount(item, v2, lang))
        return None

    def parse_answer(self, item, form, lang="de", meta=None):
        m = pick(MESSAGES, lang)
        answer, problems = {}, []
        for k in self.sequence(item):
            raw = (form.get(f"i{item.id}_{k}") or "").strip()
            if not raw:
                answer[k] = None
                continue
            try:
                answer[k] = self._check_value(item, k, raw, lang)
            except AnswerError as e:
                problems.append(str(e))
        if problems:
            raise AnswerError(" ".join(problems))
        if item.required and any(v is None for v in answer.values()):
            raise AnswerError(m["required"])
        if read_cfg(item).get("consistency", "required") == "required":
            problem = self.order_problem(item, answer, lang)
            if problem:
                raise AnswerError(problem + m["order_fix"])
        out = {k: answer[k] for k in KEYS}
        if self.nms(item):
            for k in NMS_KEYS:
                v = (form.get(f"i{item.id}_nms_{k}") or "").strip()
                # the likelihood refers to the participant's own price: without that price it says nothing
                out[f"nms_{k}"] = int(v) if v in ("1", "2", "3", "4", "5") and out[k] is not None else None
            if item.required and any(out[f"nms_{k}"] is None for k in NMS_KEYS):
                raise AnswerError(m["nms_required"])
        return None if all(v is None for v in out.values()) else out  # nothing given at all: no answer

    def soft_warning(self, item, answer, lang):
        if read_cfg(item).get("consistency") != "warn":
            return None
        problem = self.order_problem(item, answer, lang)
        return problem + pick(MESSAGES, lang)["order_warn"] if problem else None

    def display_value(self, item, form, meta=None):
        shown = {k: (form.get(f"i{item.id}_{k}") or "").strip() for k in KEYS}
        for k in NMS_KEYS:
            v = form.get(f"i{item.id}_nms_{k}")
            shown[f"nms_{k}"] = int(v) if v in ("1", "2", "3", "4", "5") else None
        return shown

    def is_missing(self, item, answer):
        keys = KEYS + tuple(f"nms_{k}" for k in NMS_KEYS) if self.nms(item) else KEYS
        return not isinstance(answer, dict) or any(answer.get(k) is None for k in keys)

    # --- piping: each of the four prices by its column, as participants read it ("0,80 €") ---

    def pipe_parts(self, item, lang):
        return [(f"{item.code}_{k}", f"Preis „{self.name(k, lang)}“") for k in KEYS]

    def pipe_part_text(self, item, answer, name, lang):
        for k in KEYS:
            if f"{item.code}_{k}" == name:
                v = answer.get(k) if isinstance(answer, dict) else None
                return self.amount(item, v, lang, full=True) if isinstance(v, (int, float)) else ""
        return ""

    # --- export -------------------------------------------------------------------

    levels = ("metric",)

    def column_level(self, item, column):
        return "ordinal" if "_nms_" in column else "metric"  # purchase likelihood 1–5, prices

    def result_column(self, item, column):
        lang = item.survey.default_language  # an English survey: its own wordings (audit 2026-10-12, M-10)
        if "_nms_" in column:
            return "categorical", "", {str(n): lab for n, lab in enumerate(self.nms_labels(item, lang), start=1)}
        return "auto", self.unit_label(item, lang), None

    def export_columns(self, item):
        unit = self.unit_label(item, "de")
        suffix = f" [{unit}]" if unit else ""
        cols = [(f"{item.code}_{k}", f"Van Westendorp: Preis {NAMES['de'][k]} ({k.upper()}){suffix}") for k in KEYS]
        if self.nms(item):
            cols += [(f"{item.code}_nms_{k}", f"Newton-Miller-Smith: Kaufwahrscheinlichkeit beim Preis {NAMES['de'][k]} (1–5)")
                     for k in NMS_KEYS]
        return cols

    def export_values(self, item, answer):
        answer = answer if isinstance(answer, dict) else {}
        out = {f"{item.code}_{k}": answer.get(k) for k in KEYS}
        if self.nms(item):
            out.update({f"{item.code}_nms_{k}": answer.get(f"nms_{k}") for k in NMS_KEYS})
        return out
