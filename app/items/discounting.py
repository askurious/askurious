"""Delay discounting: a smaller amount now or a larger one later.

Two adaptive procedures (config "mode"):
  adjusting  adjusting amount (Du, Green & Myerson, 2002): for each delay the immediate amount starts at half of the
             later amount and moves by a step that halves after every choice (later chosen: more now; now chosen:
             less now). After the choices of one delay the next offer is its indifference point. From the
             indifference points: k of the hyperbola V = A / (1 + kD) (Mazur, 1987; least squares of the indifference
             points, searched over log k) and the area under the curve (Myerson, Green & Warusawitharana, 2001); the
             two criteria of nonsystematic data of Johnson & Bickel (2008) as columns _jb1 and _jb2.
  quick      five questions with an adjusting delay, after Koffarnus & Bickel (2014): half the amount now or all of
             it later; the delay moves along a ladder of roughly log-spaced delays (our own ladder, not the published
             list; asked are 2 hours to 25 years) by 8, 4, 2, 1 steps. The fifth choice decides between the fifth delay
             and its neighbour in the chosen direction: ED50 is their geometric mean (later: the longer neighbour,
             sooner: the shorter), k = 1 / ED50. The outer steps of the ladder (1 hour, 40 years) are never asked, they
             are the neighbours at the ends (math audit 2026-10-05, N-1: before, the fifth choice changed nothing).

The browser runs the same procedure to show the offers; the server replays the stored choices, so the amounts and
scores in the export never come from the browser.

  answer = {"c": "LSL…" (L = later, S = sooner, in the order shown), "rt": [ms, …]}"""
import json
import math

from app import units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import AnswerError, ItemType, in_lang, load_json, number_text, r_id, r_int, r_num, r_one, r_rows, r_text, whole
from app.items.base import form_ids, new_id as _nid
from app.lang import Both, de_en
from app.ui_i18n import pick, tr

MODES = ("adjusting", "quick")
HOUR, DAY, WEEK, MONTH, YEAR = 1 / 24, 1, 7, 30.44, 365.25
# (count, unit) of the ladder of the quick procedure: 33 delays, roughly log-spaced. Asked are the indices 1 to 31
# (start 16 ± 8 ± 4 ± 2 ± 1); 1 hour and 40 years are only the neighbours of the fifth question at the ends
LADDER = ([(n, "h") for n in (1, 2, 3, 4, 6, 9, 12)] + [(n, "d") for n in (1, 2, 3, 4)] + [(n, "w") for n in (1, 2, 3)]
          + [(n, "m") for n in (1, 2, 3, 4, 6, 8)] + [(n, "y") for n in (1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 15, 25, 40)])
UNIT_DAYS = {"h": HOUR, "d": DAY, "w": WEEK, "m": MONTH, "y": YEAR}
QUICK_START, QUICK_STEPS = 16, (8, 4, 2, 1)
MIN_STEPS, MAX_STEPS = 3, 8
MAX_DELAYS = 10
MAX_RT = 10 * 60 * 1000
LOG_K = (-9, 5)  # search range of log10 k (per day): wide enough that real data never end at its edge
# Johnson & Bickel (2008): a point more than 20 % of the later amount above the one before (criterion 1); the last
# point not at least 10 % of the amount below the first (criterion 2)
JB_RISE, JB_DROP = 0.20, 0.10

MAZUR = ("Mazur, J. E. (1987). An adjusting procedure for studying delayed reinforcement. In M. L. Commons, J. E. "
         "Mazur, J. A. Nevin, & H. Rachlin (Eds.), Quantitative analyses of behavior: The effect of delay and of "
         "intervening events on reinforcement value (Vol. 5, pp. 55–73). Erlbaum.")
DU = ("Du, W., Green, L., & Myerson, J. (2002). Cross-cultural comparisons of discounting delayed and probabilistic "
      "rewards. The Psychological Record, 52(4), 479–492. https://doi.org/10.1007/BF03395199")
MYERSON = ("Myerson, J., Green, L., & Warusawitharana, M. (2001). Area under the curve as a measure of discounting. "
           "Journal of the Experimental Analysis of Behavior, 76(2), 235–243. https://doi.org/10.1901/jeab.2001.76-235")
KOFFARNUS = ("Koffarnus, M. N., & Bickel, W. K. (2014). A 5-trial adjusting delay discounting task: Accurate discount "
             "rates in less than one minute. Experimental and Clinical Psychopharmacology, 22(3), 222–228. "
             "https://doi.org/10.1037/a0035973")
JOHNSON_BICKEL = ("Johnson, M. W., & Bickel, W. K. (2008). An algorithm for identifying nonsystematic delay-discounting "
                  "data. Experimental and Clinical Psychopharmacology, 16(3), 264–274. "
                  "https://doi.org/10.1037/1064-1297.16.3.264")

WORDS = {  # "in 3 Tagen": count, unit; singular and plural
    "de": {"h": ("in {n} Stunde", "in {n} Stunden"), "d": ("in {n} Tag", "in {n} Tagen"),
           "w": ("in {n} Woche", "in {n} Wochen"), "m": ("in {n} Monat", "in {n} Monaten"),
           "y": ("in {n} Jahr", "in {n} Jahren")},
    "en": {"h": ("in {n} hour", "in {n} hours"), "d": ("in {n} day", "in {n} days"),
           "w": ("in {n} week", "in {n} weeks"), "m": ("in {n} month", "in {n} months"),
           "y": ("in {n} year", "in {n} years")},
}


def delay_text(n, unit, lang):
    one, many = pick(WORDS, lang)[unit]
    return (one if n == 1 else many).format(n=number_text(n, lang))


def replay(mode, amount, delays, steps, choices):
    """The offers and results of a sequence of choices: (trials [(now amount, delay days)], indifference points
    {delay index: amount} or ED50 in days). Choices beyond the procedure are ignored."""
    trials, points = [], {}
    if mode == "quick":
        idx = QUICK_START
        for k in range(1 + len(QUICK_STEPS)):
            if k >= len(choices):
                break
            trials.append((amount / 2, ladder_days(idx)))
            if k < len(QUICK_STEPS):
                idx += QUICK_STEPS[k] if choices[k] == "L" else -QUICK_STEPS[k]
        if len(choices) < 1 + len(QUICK_STEPS):
            return trials, None
        # the fifth choice: ED50 lies between the fifth delay and the next one in the chosen direction (Koffarnus &
        # Bickel, 2014), their geometric mean; the ladder has one more step at either end for this
        other = idx + (1 if choices[len(QUICK_STEPS)] == "L" else -1)
        return trials, math.sqrt(ladder_days(idx) * ladder_days(other))
    c = 0
    for j, days in enumerate(delays):
        now, step = amount / 2, amount / 4
        for _ in range(steps):
            if c >= len(choices):
                return trials, points
            trials.append((now, days))
            now += step if choices[c] == "L" else -step
            step /= 2
            c += 1
        points[j] = now
    return trials, points


def ladder_days(idx):
    n, unit = LADDER[max(0, min(idx, len(LADDER) - 1))]
    return n * UNIT_DAYS[unit]


def fit_k(delays, values):
    """k of V = 1 / (1 + kD) for indifference points as shares of the later amount: least squares of the indifference
    points, searched over log10 k (a grid in steps of 0.01, then a golden-section search around its best point)."""
    def err(lk):
        k = 10 ** lk
        return sum((v - 1 / (1 + k * d)) ** 2 for d, v in zip(delays, values))

    low, high = LOG_K
    best = min((low + i / 100 for i in range((high - low) * 100 + 1)), key=err)
    lo, hi = max(low, best - 0.01), min(high, best + 0.01)
    g = (math.sqrt(5) - 1) / 2
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa, fb = err(a), err(b)
    while hi - lo > 1e-7:
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = err(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = err(b)
    return 10 ** ((lo + hi) / 2)


def nonsystematic(values):
    """The two criteria of Johnson & Bickel (2008) for indifference points as shares of the later amount, in the
    order of the delays: (1 if a point lies more than 20 % of the amount above the one before, 1 if the last point
    does not lie at least 10 % of the amount below the first). 1 = nonsystematic by that criterion."""
    eps = 1e-9  # the shares are binary fractions: no rounding noise may decide the limit
    jb1 = int(any(b - a > JB_RISE + eps for a, b in zip(values, values[1:])))
    jb2 = int(values[0] - values[-1] < JB_DROP - eps)
    return jb1, jb2


def small(k):
    """k for the export: six decimals, more for a very small k (at least four significant digits, never 0)."""
    return units.round_half_up(k, max(6, 3 - math.floor(math.log10(k))))


def auc(delays, values):
    """Area under the empirical discounting curve (delays and values normalised, starting at (0, 1))."""
    top = max(delays)
    pts = [(0.0, 1.0)] + sorted((d / top, v) for d, v in zip(delays, values))
    return sum((x2 - x1) * (y1 + y2) / 2 for (x1, y1), (x2, y2) in zip(pts, pts[1:]))


@register
class Discounting(ItemType):
    key = "discounting"
    # the stored choices are replayed with these settings: fixed once real answers exist (audit 2026-10-12, N-1)
    read_keys = ("mode", "amount", "steps")
    label = Both("Belohnungsaufschub (Delay Discounting)", "Delay discounting")
    short = Both("Sofort weniger oder später mehr", "Less now or more later")
    # pre-test 2026-10-20, 23: in whole sentences, the old run-on was hard to follow
    description = Both("Die Person wählt mehrmals zwischen einem kleineren Betrag sofort und einem größeren Betrag "
                       "später. Die Beträge passen sich an ihre Antworten an. Ergebnis: Diskontierungsrate k und "
                       "Fläche unter der Kurve (AUC).",
                       "The person chooses several times between a smaller amount now and a larger amount later. "
                       "The amounts adapt to their answers. Result: discount rate k and area under the curve (AUC).")
    order = 29
    fact_sheet = FactSheet(
        "tasks",
        keywords=["Delay discounting", "Belohnungsaufschub", "Zeitpräferenz", "Time preference", "Geduld",
                  "Patience"],
        can=[("Adaptiv oder schnell (5 Durchgänge).", "Adaptive or quick (5 trials)."),
             ("Ergibt k und die Fläche unter der Kurve.", "Gives k and the area under the curve.")],
        good_for=[("Geduld und Zeitpräferenz, z. B. bei Gesundheit, Geld oder Nachhaltigkeit",
                   "Patience and time preference, e.g. for health, money or sustainability")],
        not_for=[("Präferenzen zwischen Produkten (dann Conjoint)", "Preferences between products (then Conjoint)")],
        designs=["cross", "framing"],
        examples=[("Lieber 50 € heute oder 100 € in einem Jahr?", "Rather €50 today or €100 in a year?")],
    )
    supports_auto_next = True
    alone_on_page = True
    allowed_in_loop = False
    allowed_in_separated = False
    auto_next_hint = Both("nach der letzten Wahl", "after the last choice")
    import_rules = {"mode": r_one(*MODES), "amount": r_num(1, 1_000_000_000), "steps": r_int(MIN_STEPS, MAX_STEPS),
                    "delays": r_rows(MAX_DELAYS, id=r_id, code=r_text(64, 1), days=r_num(HOUR, 100 * YEAR))}

    DEFAULT_DELAYS = (("1w", 7, "in 1 Woche", "in 1 week"), ("1m", 30, "in 1 Monat", "in 1 month"),
                      ("6m", 182, "in 6 Monaten", "in 6 months"), ("1y", 365, "in 1 Jahr", "in 1 year"),
                      ("5y", 1826, "in 5 Jahren", "in 5 years"))

    def default_config(self):
        return {"mode": "adjusting", "amount": 100, "steps": 6,
                "delays": [{"id": code, "code": code, "days": days} for code, days, _de, _en in self.DEFAULT_DELAYS]}

    def default_texts(self, lang):
        return {"question": tr(lang, "Was wäre Ihnen lieber? Es gibt keine richtigen oder falschen Antworten.",
                               "Which would you prefer? There are no right or wrong answers."),
                "now": tr(lang, "heute", "today"), "unit": tr(lang, "{n} €", "€{n}"),
                "delays": {code: tr(lang, de, en) for code, _days, de, en in self.DEFAULT_DELAYS}}

    def mode(self, item):
        return item.config.get("mode") if item.config.get("mode") in MODES else "adjusting"

    def amount(self, item):
        return float(item.config.get("amount") or 100)

    def steps(self, item):
        return int(item.config.get("steps") or 6)

    def delays(self, item, lang=None):
        dt = item.t(lang).get("delays", {})
        return [{**d, "label": dt.get(d["id"], "")} for d in item.config.get("delays", [])]

    def read_shape(self, item):
        """The read_keys and the delays in their order with their days: a delay added, taken out or moved would
        make the stored choices of one delay those of another (audit 2026-10-12, N-1)."""
        return {**super().read_shape(item),
                "delays": [[d.get("id"), d.get("days")] for d in (item.config or {}).get("delays") or []]}

    def total(self, item):
        return 1 + len(QUICK_STEPS) if self.mode(item) == "quick" else self.steps(item) * len(item.config.get("delays", []))

    # --- editor ---------------------------------------------------------------------------------------------

    def update_from_form(self, item, form, lang, structural):
        config, texts = dict(item.config), dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        lt["now"] = form.get("now", "").strip()[:60]
        unit = form.get("unit", "").strip()[:30]
        lt["unit"] = unit if "{n}" in unit else (unit + " {n}" if unit else "{n}") if unit else lt.get("unit", "{n} €")
        dtexts = dict(lt.get("delays", {}))
        ids, codes, days, labels = form_ids(form.getlist("de_id")), form.getlist("de_code"), form.getlist("de_days"), form.getlist("de_label")
        if structural:
            config["mode"] = form.get("mode") if form.get("mode") in MODES else "adjusting"
            # in the language of the page with a note on "1,000" in German (units.read_entry; Mathe-Audit 2026-10-06,
            # Z-4: "1.000" was read as 1)
            a = units.read_entry(form.get("amount", ""), known=config.get("amount"))
            config["amount"] = a if a is not None and 1 <= a <= 1_000_000_000 else 100
            n = whole(form.get("steps", "").strip())
            config["steps"] = min(max(n, MIN_STEPS), MAX_STEPS) if n else 6
            rows = []
            known = [r.get("days") for r in config.get("delays") or []]
            for did, code, d, label in zip(ids, codes, days, labels):
                value = units.read_entry(d, known=known)
                if not code.strip() and not label.strip() and value is None:
                    continue
                if value is None or not (HOUR <= value <= 100 * YEAR) or len(rows) >= MAX_DELAYS:
                    continue
                did = did or _nid()
                rows.append({"id": did, "code": code.strip() or f"d{len(rows) + 1}", "days": value})
                dtexts[did] = label.strip()[:60]
            if ids:  # the table is hidden (and not sent) while the quick procedure is chosen: keep its delays
                config["delays"] = sorted(rows, key=lambda r: r["days"])
        else:
            known = {d["id"] for d in config.get("delays", [])}
            for did, label in zip(ids, labels):
                if did in known:
                    dtexts[did] = label.strip()[:60]
        lt["delays"] = dtexts
        texts[lang] = lt
        item.config, item.texts = config, texts

    def validate(self, item):
        problems = []
        if self.mode(item) == "adjusting":
            ds = item.config.get("delays", [])
            if len(ds) < 2:
                problems.append(de_en("mindestens zwei Verzögerungen", "at least two delays"))
            if len({d["days"] for d in ds}) != len(ds):
                problems.append(de_en("Verzögerungen kommen doppelt vor", "delays occur twice"))
            if len({d["code"] for d in ds}) != len(ds):
                problems.append(de_en("Codes der Verzögerungen sind nicht eindeutig", "codes of the delays are not unique"))
            if any(not d["label"] for d in self.delays(item)):
                problems.append(de_en("Verzögerung ohne Text (z. B. „in 1 Monat“)", "delay without text (e.g. “in 1 month”)"))
        return problems

    # --- participants ----------------------------------------------------------------------------------------

    def money(self, item, value, lang):
        """An amount as participants read it: whole amounts without decimals, others with two ("51,56 €")."""
        shown = units.half_up(value, 2)  # half up (kaufmännisch): 51,555 -> 51,56, not 51,55 (Mathe-Audit 2026-10-06, Z-9)
        text = str(int(shown)) if shown == int(shown) else f"{shown:.2f}"
        # the decimal comma in every language that writes one; zh-Hans and ar a point like English (Z-8)
        return (item.text("unit", lang) or "{n}").replace("{n}", text if units.writes_like_english(lang) else text.replace(".", ","))

    def task(self, item, lang):
        """What the browser needs to run the procedure (participant.js "Delay discounting"); the template writes it with
        |tojson, which escapes "<" so researcher texts cannot end the script block (audit 2026-10-06 S3)."""
        out = {"mode": self.mode(item), "amount": self.amount(item), "steps": self.steps(item),
               "unit": item.text("unit", lang) or "{n}", "now": item.text("now", lang),
               "comma": not units.writes_like_english(lang), "total": self.total(item)}
        if out["mode"] == "quick":
            out["ladder"] = [delay_text(n, u, lang) for n, u in LADDER]
            out["start"], out["moves"] = QUICK_START, list(QUICK_STEPS)
        else:
            out["delays"] = [d["label"] for d in self.delays(item, lang)]
        return out

    def _parse(self, item, raw):
        data = load_json(raw or "", None)
        if not isinstance(data, dict) or not isinstance(data.get("c"), str):
            return None
        c = data["c"]
        if len(c) != self.total(item) or set(c) - {"L", "S"}:
            return None
        rts = data.get("rt") if isinstance(data.get("rt"), list) else []
        rts = [int(r) if isinstance(r, (int, float)) and 0 <= r <= MAX_RT else None for r in rts[:len(c)]]
        return {"c": c, "rt": rts + [None] * (len(c) - len(rts))}

    def parse_answer(self, item, form, lang="de", meta=None):
        answer = self._parse(item, form.get(f"i{item.id}"))
        if item.required and answer is None:
            raise AnswerError(tr(lang, "Bitte treffen Sie alle Entscheidungen.", "Please make all the choices."))
        return answer

    def display_value(self, item, form, meta=None):
        return self._parse(item, form.get(f"i{item.id}"))

    def is_missing(self, item, answer):
        return not answer

    def dummy_fields(self, item, rng, meta, answers=None):
        patience = rng.random()
        c = "".join("L" if rng.random() < patience else "S" for _ in range(self.total(item)))
        return {f"i{item.id}": json.dumps({"c": c, "rt": [int(rng.gauss(2500, 700)) for _ in c]})}

    # --- scores, export, results ----------------------------------------------------------------------------

    def scores(self, item, answer):
        if not isinstance(answer, dict) or not answer.get("c") or len(answer["c"]) != self.total(item):
            return {}  # choices of another procedure: not cut to fit (audit 2026-10-12, N-1)
        amount, ds = self.amount(item), item.config.get("delays", [])
        _trials, result = replay(self.mode(item), amount, [d["days"] for d in ds], self.steps(item), answer["c"])
        if self.mode(item) == "quick":
            if result is None:
                return {}
            k = 1 / result
            return {"ed50": units.round_half_up(result, 4), "k": small(k), "lnk": units.round_half_up(math.log(k), 3)}
        if len(result) != len(ds):
            return {}
        days = [d["days"] for d in ds]
        shares = [result[j] / amount for j in range(len(ds))]
        k = fit_k(days, shares)
        jb1, jb2 = nonsystematic([v for _d, v in sorted(zip(days, shares), key=lambda p: p[0])])
        out = {"k": small(k), "lnk": units.round_half_up(math.log(k), 3),
               "auc": units.round_half_up(auc(days, shares), 4), "jb1": jb1, "jb2": jb2}
        out.update({f"ip_{d['code']}": units.round_half_up(result[j], 2) for j, d in enumerate(ds)})
        return out

    def export_columns(self, item):
        unit = (item.text("unit") or "{n}").replace("{n}", "").strip()
        if self.mode(item) == "quick":
            cols = [("ed50", "ED50: Verzögerung in Tagen, bei der der volle Betrag halb so viel wert ist"),
                    ("k", "k = 1 / ED50 (pro Tag)"), ("lnk", "ln(k)")]
        else:
            cols = [("k", "k der Hyperbel V = A / (1 + kD) (pro Tag)"), ("lnk", "ln(k)"),
                    ("auc", "Fläche unter der Kurve (0 bis 1; kleiner = stärkeres Abwerten)"),
                    ("jb1", "Nicht systematisch, Kriterium 1: ein Indifferenzpunkt mehr als 20 % des Betrags über dem "
                            "vorherigen (1 = ja)"),
                    ("jb2", "Nicht systematisch, Kriterium 2: letzter Indifferenzpunkt nicht mindestens 10 % des Betrags "
                            "unter dem ersten (1 = ja)")]
            cols += [(f"ip_{d['code']}", f"Indifferenzpunkt {d['label'] or d['code']} ({unit})".replace(" ()", ""))
                     for d in self.delays(item)]
        cols.append(("choices", "Wahlen in der gezeigten Reihenfolge: L = später, S = sofort"))
        return [(f"{item.code}_{k}", label) for k, label in cols]

    def export_values(self, item, answer):
        s = self.scores(item, answer)
        out = {col: None for col, _ in self.export_columns(item)}
        for k, v in s.items():
            out[f"{item.code}_{k}"] = v
        out[f"{item.code}_choices"] = answer.get("c") if isinstance(answer, dict) else None
        return out

    levels = ("metric",)

    def column_level(self, item, column):
        return "nominal" if column.endswith(("_choices", "_jb1", "_jb2")) else "metric"

    def result_column(self, item, column):
        if column.endswith(("_choices", "_jb1", "_jb2")):  # the criteria: one line under the figures (results())
            return None, "", None
        if column.endswith("_ed50"):
            return "auto", "Tage", None
        return "auto", "", None

    def codebook_info(self, item, column, lang, ui=None):
        quick = self.mode(item) == "quick"
        after = in_lang(ui, "nach ", "after ")
        if column == f"{item.code}_k":
            return "Zahl", "> 0", [in_lang(ui, "größer = stärkeres Abwerten späterer Belohnungen; für Analysen meist ln(k)",
                                           "larger = stronger discounting of later rewards; for analyses mostly ln(k)"),
                                   in_lang(ui, "vom Server aus den Wahlen berechnet, nicht vom Browser",
                                           "computed by the server from the choices, not by the browser"),
                                   "k = 1 / ED50 " + after + KOFFARNUS if quick else
                                   in_lang(ui, "Hyperbel nach ", "hyperbola after ") + MAZUR
                                   + in_lang(ui, "; kleinste Quadrate der Indifferenzpunkte; Verfahren nach ",
                                             "; least squares of the indifference points; procedure after ") + DU]
        if column == f"{item.code}_ed50":
            return "Zahl", "> 0", [in_lang(ui, "fünf Fragen nach ", "five questions after ") + KOFFARNUS,
                                   in_lang(ui, "nach der fünften Wahl das geometrische Mittel aus der fünften Verzögerung "
                                               "und der nächsten in Richtung der Wahl (eigene Leiter)",
                                           "after the fifth choice the geometric mean of the fifth delay and the next one "
                                           "in the direction of the choice (own ladder)")]
        if column == f"{item.code}_auc":
            return "Zahl", in_lang(ui, "0 bis 1", "0 to 1"), [in_lang(ui, "Fläche unter der Kurve nach ",
                                                                      "area under the curve after ") + MYERSON]
        if column in (f"{item.code}_jb1", f"{item.code}_jb2"):
            return "Kennzeichen", "0 = unauffällig; 1 = nicht systematisch", [
                in_lang(ui, "Kriterien nach ", "criteria after ") + JOHNSON_BICKEL,
                in_lang(ui, "k und Fläche unter der Kurve sind bei 1 mit Vorsicht zu lesen",
                        "at 1, read k and the area under the curve with caution")]
        return None

    def source_groups(self, item):
        return [(de_en("Belohnungsaufschub: Anpassungsverfahren, Hyperbel und Fläche unter der Kurve",
                       "Delay discounting: adjusting procedure, hyperbola and area under the curve"), [DU, MAZUR, MYERSON]),
                (de_en("Belohnungsaufschub: Kurzform mit fünf Fragen", "Delay discounting: short form with five questions"), [KOFFARNUS]),
                (de_en("Belohnungsaufschub: nicht systematische Daten", "Delay discounting: non-systematic data"), [JOHNSON_BICKEL])]

    def results(self, item, table):
        """For the line under the figures of the results page: the mode, and in the adjusting procedure the number
        of persons with a k and how many of them each criterion of Johnson & Bickel (2008) marks; {} without answers."""
        code = item.code
        rows = [r for r in table if r.get(f"{code}_k") not in (None, "")]
        if not rows:
            return {}
        if self.mode(item) == "quick":
            return {"mode": "quick", "n": len(rows)}
        flag = lambda r, c: r.get(f"{code}_{c}") in (1, "1")
        return {"mode": "adjusting", "n": len(rows), "jb1": sum(flag(r, "jb1") for r in rows),
                "jb2": sum(flag(r, "jb2") for r in rows), "any": sum(flag(r, "jb1") or flag(r, "jb2") for r in rows)}

    def method_text(self, item, answers, lang="de"):
        """The sentence of the method section (app/methods.py) on this element; answers: the stored answers of the
        final sample."""
        found = [s for s in (self.scores(item, a) for a in answers) if s]
        a = self.money(item, self.amount(item), lang)
        if self.mode(item) == "quick":
            if lang == "en":
                return (f"Delay discounting (\"{item.code}\"): five-trial adjusting delay task (Koffarnus & Bickel, 2014), "
                        f"half of {a} now or {a} later, delays from 2 hours to 25 years on a separate ladder; ED50 from the "
                        f"fifth choice as the geometric mean of neighbouring delays, k = 1/ED50; n = {len(found)}")
            return (f"Den Belohnungsaufschub maß „{item.code}“ mit der Kurzform aus fünf Fragen (Koffarnus & Bickel, "
                    f"2014): die Hälfte von {a} sofort oder {a} später, die Verzögerung passte sich auf einer eigenen "
                    f"Leiter von 2 Stunden bis 25 Jahren an. ED50 ergab sich nach der fünften Wahl als geometrisches "
                    f"Mittel benachbarter Verzögerungen, k = 1/ED50 (n = {len(found)}).")
        n_d, steps = len(item.config.get("delays", [])), self.steps(item)
        jb1, jb2 = sum(s["jb1"] for s in found), sum(s["jb2"] for s in found)
        if lang == "en":
            return (f"Delay discounting (\"{item.code}\"): adjusting-amount procedure (Du et al., 2002), {n_d} delays with "
                    f"{steps} choices each between an amount now and {a} later; k of the hyperbola V = A/(1 + kD) "
                    f"(Mazur, 1987) by least squares of the indifference points and the area under the curve (Myerson "
                    f"et al., 2001); nonsystematic data (Johnson & Bickel, 2008): criterion 1 met by {jb1}, criterion 2 "
                    f"by {jb2} of n = {len(found)}")
        return (f"Den Belohnungsaufschub maß „{item.code}“ mit einem Anpassungsverfahren (Du et al., 2002): für {n_d} "
                f"Verzögerungen je {steps} Wahlen zwischen einem Betrag sofort und {a} später; der sofortige Betrag "
                f"begann bei der Hälfte und änderte sich in halbierten Schritten. Aus den Indifferenzpunkten wurden k der "
                f"Hyperbel V = A/(1 + kD) (Mazur, 1987) nach kleinsten Quadraten und die Fläche unter der Kurve (Myerson "
                f"et al., 2001) berechnet. Nicht systematische Daten nach Johnson und Bickel (2008): Kriterium 1 traf "
                f"auf {jb1}, Kriterium 2 auf {jb2} von n = {len(found)} Personen zu.")

    def method_sources(self, item):
        """The source groups of method_text."""
        if self.mode(item) == "quick":
            return [("Belohnungsaufschub: Kurzform mit fünf Fragen", [KOFFARNUS])]
        return [("Belohnungsaufschub: Anpassungsverfahren, Hyperbel und Fläche unter der Kurve", [DU, MAZUR, MYERSON]),
                ("Belohnungsaufschub: nicht systematische Daten", [JOHNSON_BICKEL])]

    def questionnaire_lines(self, item, lang):
        a = self.money(item, self.amount(item), lang)
        if self.mode(item) == "quick":
            return [tr(lang, f"[Belohnungsaufschub, fünf Fragen: die Hälfte von {a} heute oder {a} später, die "
                             "Verzögerung passt sich an]",
                       f"[Delay discounting, five questions: half of {a} today or {a} later, adjusting delay]")]
        labels = ", ".join(d["label"] for d in self.delays(item, lang))
        return [tr(lang, f"[Belohnungsaufschub: weniger heute oder {a} {labels}; je Verzögerung "
                         f"{self.steps(item)} Wahlen, der Betrag heute passt sich an]",
                   f"[Delay discounting: less today or {a} {labels}; {self.steps(item)} choices per delay, "
                   "adjusting immediate amount]")]
