"""Q-Sort: participants lay statements into a grid from "most disagree" (-k) to "most agree" (+k) whose columns
hold a fixed number of cards each, as a rule a symmetric, quasi-normal shape (forced distribution).

The Q method (Stephenson, 1953; Brown, 1980; Watts & Stenner, 2012): the sorts of many persons are correlated
and factor-analysed by person, e.g. with the R package qmethod; the export holds one column per statement with
its value, which is the matrix those tools read (transposed). Optionally a presort comes first, as Watts and
Stenner (2012) describe: every card once into one of three piles (rather disagree, neutral, rather agree), which then
fill the grid from both ends.

Interaction (participant.js, [data-sort] with data-sort-q): the same sorting widget as the card sort; the columns
are its groups, each with a number of places. The page sends {"q": {card: value}, "p": {card: -1|0|1}} as JSON in
the field i<id>. Export: <code>_<card> = -k … +k, with the presort also <code>_<card>_pre = -1, 0 or 1."""
import math

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_int, r_list  # rules for survey files, see base.py
from app.items.base import AnswerError, in_lang, load_json, plain, prepared
from app.items.choice import LAYOUT_RULE, OPTION_RULES, _Choice
from app.lang import Both, de_en
from app.ui_i18n import tr

MIN_RANGE, MAX_RANGE = 2, 6  # -2..+2 up to -6..+6
MAX_CARDS = 80
STEPHENSON = "Stephenson, W. (1953). The study of behavior: Q-technique and its methodology. University of Chicago Press."
BROWN = "Brown, S. R. (1980). Political subjectivity: Applications of Q methodology in political science. Yale University Press."
WATTS = ("Watts, S., & Stenner, P. (2012). Doing Q methodological research: Theory, method and interpretation. Sage. "
         "https://doi.org/10.4135/9781446251911")
TEXTS = ("low", "high", "pre_low", "pre_mid", "pre_high")


def quasi_normal(n, k):
    """Places per column for n cards in the columns -k..+k: symmetric, at least one each, filled where a normal
    curve (SD = 3k / 4: flatter than the bell, as Q studies use) is furthest above what the column has so far."""
    cols = 2 * k + 1
    if n < cols:
        return None
    weights = [math.exp(-(i * i) / (2 * (0.75 * k) ** 2)) for i in range(-k, k + 1)]
    want = [n * w / sum(weights) for w in weights]
    caps, left = [1] * cols, n - cols
    while left > 0:
        best = 0  # 0: the middle, which takes one card; i: the pair of columns -i and +i, which take two
        best_gap = want[k] - caps[k]
        if left >= 2:
            for i in range(1, k + 1):
                gap = want[k + i] - caps[k + i]
                if gap > best_gap:
                    best, best_gap = i, gap
        if best == 0:
            caps[k] += 1
            left -= 1
        else:
            caps[k + best] += 1
            caps[k - best] += 1
            left -= 2
    return caps


@register
class QSort(_Choice):
    key = "q_sort"
    code_answer = False  # not a code to compare with: a condition only asks "answered or not" (X17)
    label = Both("Q-Sort", "Q sort")
    short = Both("Aussagen in ein Raster legen", "Lay statements into a grid")
    description = Both("Aussagen in ein Raster von „lehne stark ab“ bis „stimme stark zu“ legen, mit fester Zahl von Plätzen je Spalte (Q-Methode)",
                       "Lay statements into a grid from “most disagree” to “most agree” with a fixed number of places per column (Q method)")
    order = 28
    fact_sheet = FactSheet(
        "tasks",
        keywords=["Q-Methode", "Q method", "Q-Set", "Q set", "Forced distribution", "Zwangsverteilung"],
        can=[("Aussagen in ein Raster von „lehne stark ab“ bis „stimme stark zu“ legen, mit fester Zahl von Plätzen je Spalte.",
              "Lay statements into a grid from “most disagree” to “most agree” with a fixed number of places per column."),
             ("Raster annähernd normalverteilt aus der Zahl der Aussagen oder selbst festgelegt; auf Wunsch mit Vorsortierung in drei Stapel.",
              "Grid roughly normal from the number of statements or set by you; optionally with a presort into three piles."),
             ("Export eine Spalte je Aussage (−k … +k), so wie Programme der Q-Methode sie lesen.",
              "Export one column per statement (−k … +k), as Q method software reads it.")],
        good_for=[("Subjektive Sichtweisen und ihre Muster (Q-Methode): Personen werden nach ihren Sortierungen gruppiert",
                   "Subjective viewpoints and their patterns (Q method): people are grouped by their sorts"),
                  ("Kleine, gezielt gewählte Gruppen, z. B. Fachleute oder Betroffene",
                   "Small, purposively chosen groups, e.g. experts or those affected")],
        not_for=[("Wie verbreitet eine Meinung in einer Bevölkerung ist (dann Likert-Skala)",
                  "How widespread an opinion is in a population (then Likert scale)"),
                 ("Begriffe ohne Rangordnung in Gruppen ordnen (dann Kartensortierung)",
                  "Grouping terms without an order (then card sorting)")],
        designs=["perception", "delphi"],
        examples=[("40 Aussagen zur Energiewende von −4 bis +4 sortieren",
                   "Sort 40 statements on the energy transition from −4 to +4")],
    )
    uses_answers = False
    codes_in_columns = True
    levels = ("ordinal", "metric")
    import_rules = {"options": OPTION_RULES, "layout": LAYOUT_RULE, "range": r_int(MIN_RANGE, MAX_RANGE),
                    "capacities": r_list(2 * MAX_RANGE + 1, r_int(1, MAX_CARDS)), "presort": r_flag, "randomize": r_flag}
    scale_keys = ("range",)
    read_keys = ("presort", "capacities")  # the _pre columns and the grid in the codebook (audit 2026-10-10, N-3)
    closed_lists = ("options",)  # one more statement changes the forced distribution (audit 2026-10-12, M-4)

    def read_shape(self, item):
        """As ItemType.read_shape, with the grid as it is used (the quasi-normal one for an empty "capacities")."""
        return {**super().read_shape(item), "capacities": self.capacities(item)}

    def freeze_answer_shape(self, item):
        """The grid worked out from the number of statements is written down as own grid once real answers exist
        (audit 2026-10-12, M-4): it no longer follows the statements."""
        caps = self.capacities(item)
        if caps and not (item.config or {}).get("capacities"):
            item.config = {**item.config, "capacities": caps}

    def source_groups(self, item):
        return [(de_en("Q-Methode", "Q method"), [STEPHENSON, BROWN, WATTS])]

    def references(self, item):
        return [STEPHENSON, WATTS]

    def default_config(self):
        return {**super().default_config(), "range": 3, "capacities": [], "presort": True, "randomize": True}

    def default_texts(self, lang):
        return {**super().default_texts(lang),
                "low": tr(lang, "stimme überhaupt nicht zu", "most disagree"),
                "high": tr(lang, "stimme voll und ganz zu", "most agree"),
                "pre_low": tr(lang, "eher nicht", "rather disagree"),
                "pre_mid": tr(lang, "neutral", "neutral"),
                "pre_high": tr(lang, "eher ja", "rather agree")}

    # --- editor -----------------------------------------------------------------------------------

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        texts = dict(item.texts)
        texts[lang] = {**texts.get(lang, {}), **{k: form.get(k, "").strip()[:60] for k in TEXTS}}
        item.texts = texts

    def _update_extra_config(self, config, form):
        k = form.get("range", "")
        config["range"] = max(MIN_RANGE, min(MAX_RANGE, int(k))) if k.isascii() and k.isdigit() else config.get("range", 3)
        caps = []
        if form.get("shape") == "own":
            # a column without a field (just switched to "own", or a new range) starts from the quasi-normal shape
            start = quasi_normal(len(config.get("options", [])), config["range"]) or [1] * (2 * config["range"] + 1)
            for n, i in enumerate(range(-config["range"], config["range"] + 1)):
                v = form.get(f"cap_{i}", "")
                caps.append(max(1, min(MAX_CARDS, int(v))) if v.isascii() and v.isdigit() else start[n])
        config["capacities"] = caps  # empty: quasi-normal for the number of statements (capacities())
        config["presort"] = bool(form.get("presort"))
        config["randomize"] = bool(form.get("randomize"))
        config.pop("layout", None)

    def values(self, item):
        k = int((item.config or {}).get("range") or 3)
        return list(range(-k, k + 1))

    def capacities(self, item):
        """Places per column, -k first; the own ones, or quasi-normal for the number of statements (None if there
        are fewer statements than columns)."""
        c = item.config or {}
        own = c.get("capacities") or []
        if own and len(own) == len(self.values(item)):
            return list(own)
        return quasi_normal(len(c.get("options", [])), int(c.get("range") or 3))

    def validate(self, item):
        problems = [p.replace("Antwortoptionen", "Aussagen").replace("Antwortoption", "Aussage").replace("Antwortcodes", "Codes der Aussagen")
                    .replace("answer options", "statements").replace("answer option", "statement").replace("answer codes", "statement codes")
                    for p in super().validate(item)]
        n, caps = len(item.config.get("options", [])), self.capacities(item)
        if n > MAX_CARDS:
            problems.append(de_en("höchstens {max} Aussagen", "at most {max} statements", max=MAX_CARDS))
        if caps is None:
            problems.append(de_en("weniger Aussagen ({n}) als Spalten ({cols}): mehr Aussagen oder eine kleinere Spannweite",
                                  "fewer statements ({n}) than columns ({cols}): more statements or a smaller range",
                                  n=n, cols=len(self.values(item))))
        elif sum(caps) != n:
            problems.append(de_en("Die Plätze im Raster ({places}) passen nicht zur Zahl der Aussagen ({n})",
                                  "the places in the grid ({places}) do not match the number of statements ({n})", places=sum(caps), n=n))
        if not (item.text("low") and item.text("high")):
            problems.append(de_en("Enden des Rasters ohne Beschriftung", "ends of the grid without a label"))
        return problems

    def check_warnings(self, item):
        caps = self.capacities(item)
        if caps and caps != caps[::-1]:
            return [de_en("Das Raster ist nicht symmetrisch; üblich ist eine symmetrische, annähernd normale Verteilung.",
                          "The grid is not symmetric; a symmetric, roughly normal shape is usual.")]
        return []

    def ordered_options(self, item, lang, start_order=None):
        opts = self.options(item, lang)
        if start_order:
            rank = {oid: n for n, oid in enumerate(start_order)}
            return sorted(opts, key=lambda o: rank.get(o["id"], len(rank)))
        return opts

    def order_codes(self, item):
        if not item.config.get("randomize", True):
            return None
        return {o["id"]: f"{item.code}_{o['code']}" for o in item.config.get("options", [])}

    # --- participant side ---------------------------------------------------------------------------

    def _read(self, item, form):
        """({card: value}, {card: pile}) as the page sent them, cleaned: unknown cards and values are dropped, and so
        is a card beyond the places of its column (the page never sends that). Without a grid (fewer statements than
        columns, which the editor marks as a problem) the columns take any number, as the page then lets them
        (data-cap 0); before, every card was dropped and a required Q sort could never be sent (pre-test 2026-10-20)."""
        data = load_json(form.get(f"i{item.id}"), {})
        data = data if isinstance(data, dict) else {}
        cards = [o["code"] for o in item.config.get("options", [])]
        grid = self.capacities(item)
        caps = dict(zip(self.values(item), grid)) if grid else {v: len(cards) for v in self.values(item)}
        given = data.get("q") if isinstance(data.get("q"), dict) else {}
        used, out = {}, {}
        for c in cards:
            v = given.get(c)
            if isinstance(v, bool) or not isinstance(v, int) or v not in caps or used.get(v, 0) >= caps[v]:
                continue
            used[v] = used.get(v, 0) + 1
            out[c] = v
        piles = data.get("p") if isinstance(data.get("p"), dict) else {}
        pre = {c: piles[c] for c in cards if item.config.get("presort") and piles.get(c) in (-1, 0, 1) and not isinstance(piles.get(c), bool)}
        return out, pre

    def _needed(self, item):
        """How many statements a complete sort lays: all of them, or as many as the grid has places when an own grid
        has fewer places than statements (the page cannot lay more; the editor marks the grid as a problem)."""
        n, caps = len(item.config.get("options", [])), self.capacities(item)
        return min(n, sum(caps)) if caps else n

    def parse_answer(self, item, form, lang="de", meta=None):
        placed, pre = self._read(item, form)
        if item.required and len(placed) < self._needed(item):
            raise AnswerError(tr(lang, "Bitte legen Sie alle Aussagen in das Raster.", "Please lay every statement into the grid."))
        if not placed and not pre:
            return None
        return {"q": placed, **({"p": pre} if pre else {})}

    def display_value(self, item, form, meta=None):
        placed, pre = self._read(item, form)
        return {"q": placed, "p": pre} if placed or pre else None

    def is_missing(self, item, answer):
        placed = (answer or {}).get("q") if isinstance(answer, dict) else None
        return not placed or len(placed) < self._needed(item)

    # --- export ------------------------------------------------------------------------------------

    def export_columns(self, item):
        labels = item.text("options") or {}
        k = len(self.values(item)) // 2
        cols = []
        for o in item.config.get("options", []):
            name = plain(labels.get(o["id"], o["code"]))
            cols.append((f"{item.code}_{o['code']}", f"„{name}“ (−{k} = {plain(item.text('low'))}, +{k} = {plain(item.text('high'))})"))
            if item.config.get("presort"):
                cols.append((f"{item.code}_{o['code']}_pre", f"„{name}“: Vorsortierung"))
        return cols

    def fixed_summary(self, item, answer, lang):
        """The sorting in words for an answer fixed by feedback (UI/UX audit 2026-10-17, W-A03): each statement with
        its column, from the one that agrees most to the one that agrees least."""
        placed = (answer or {}).get("q") or {} if isinstance(answer, dict) else {}
        labels = item.t(lang).get("options") or {}
        rows = [(placed[o["code"]], plain(labels.get(o["id"]) or o["code"])) for o in item.config.get("options", [])
                if isinstance(placed.get(o["code"]), int)]
        return [(name, f"{value:+d}" if value else "0") for value, name in sorted(rows, key=lambda r: -r[0])]

    def export_values(self, item, answer):
        placed = (answer or {}).get("q") or {}
        pre = (answer or {}).get("p") or {}
        out = {}
        for o in item.config.get("options", []):
            out[f"{item.code}_{o['code']}"] = placed.get(o["code"])
            if item.config.get("presort"):
                out[f"{item.code}_{o['code']}_pre"] = pre.get(o["code"])
        return out

    def _is_pre(self, item, column):
        """Whether an export column is the presort of a statement: looked up in the columns the export makes, not
        read from the ending (a statement coded "x_pre" is a statement)."""
        def build():
            kinds = {}
            for o in item.config.get("options", []):
                kinds.setdefault(f"{item.code}_{o['code']}", False)
                if item.config.get("presort"):
                    kinds.setdefault(f"{item.code}_{o['code']}_pre", True)
            return kinds

        return prepared(item, "q_sort_pre", build).get(column, False)

    def column_level(self, item, column):
        return "ordinal" if self._is_pre(item, column) else self.level(item)

    def result_column(self, item, column):
        """Results page: one table of all statements (results()), not a column each."""
        return None, "", None

    def codebook_info(self, item, column, lang, ui=None):
        if self._is_pre(item, column):
            return ("Kategorie", f"-1 = {plain(item.text('pre_low', lang))}; 0 = {plain(item.text('pre_mid', lang))}; "
                                 f"1 = {plain(item.text('pre_high', lang))}",
                    [in_lang(ui, "Stapel der Vorsortierung; leer = nicht vorsortiert", "pile of the presort; empty = not presorted")])
        k = len(self.values(item)) // 2
        caps = self.capacities(item) or []
        shape = ", ".join(f"{v:+d}: {c}" for v, c in zip(self.values(item), caps))
        # the two ends as pairs separated by ";" (the value labels of .sps/.sav/.rds/.omv read them so; audit
        # 2026-10-10, N-4), the steps in between without a label as on a scale with labelled ends
        return ("Skala", f"-{k} = {plain(item.text('low', lang))}; {k} = {plain(item.text('high', lang))}",
                [in_lang(ui, "Werte -{k} bis +{k}", "values -{k} to +{k}", k=k),
                 in_lang(ui, "Plätze je Spalte: {shape}", "places per column: {shape}", shape=shape),
                 in_lang(ui, "leer = nicht gelegt", "empty = not laid")])

    def scale_values(self, item, column):
        return None if self._is_pre(item, column) else self.values(item)

    # --- results -----------------------------------------------------------------------------------

    def results(self, item, table, lang, passes=()):
        """Results page: per statement the mean value, its SD and the share at each end of the grid, most agreed
        first; persons = complete sorts. The factor analysis by person (the point of a Q study) is left to the
        tools of the Q method, which read the export."""
        import statistics

        suffixes = [f"_{n}" for n in passes] or [""]
        cards = self.options(item, lang)
        k = len(self.values(item)) // 2
        complete = 0
        values = {o["code"]: [] for o in cards}
        for row in table:
            for s in suffixes:
                found = {o["code"]: row.get(f"{item.code}_{o['code']}{s}") for o in cards}
                found = {c: v for c, v in found.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                if found and len(found) == len(cards):
                    complete += 1
                for c, v in found.items():
                    values[c].append(v)
        rows = []
        for o in cards:
            vs = values[o["code"]]
            rows.append({"label": plain(o["label"]) or o["code"], "code": o["code"], "n": len(vs),
                         "mean": statistics.fmean(vs) if vs else None, "sd": statistics.stdev(vs) if len(vs) > 1 else None,
                         "agree": sum(1 for v in vs if v >= k - 1) / len(vs) if vs else None,
                         "disagree": sum(1 for v in vs if v <= -(k - 1)) / len(vs) if vs else None})
        rows.sort(key=lambda r: (r["mean"] is None, -(r["mean"] or 0)))
        return {"persons": complete, "rows": rows, "k": k, "low": plain(item.text("low", lang)), "high": plain(item.text("high", lang))}

    # --- dummy data, duration ------------------------------------------------------------------------

    def dummy_fields(self, item, rng, meta, answers=None):
        import json

        caps = self.capacities(item)
        codes = [o["code"] for o in item.config.get("options", [])]
        if not caps or sum(caps) != len(codes):
            return {}
        rng.shuffle(codes)
        slots = [v for v, c in zip(self.values(item), caps) for _ in range(c)]
        placed = dict(zip(codes, slots))
        pre = {c: (v > 0) - (v < 0) for c, v in placed.items()} if item.config.get("presort") else {}
        return {f"i{item.id}": json.dumps({"q": placed, "p": pre})}

    def seconds(self, item, read):
        n = len(item.config.get("options", []))
        return read + 6 * n + (3 * n if item.config.get("presort") else 0)
