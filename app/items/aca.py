"""Adaptive conjoint (ACA), experimental feature "aca" (concept aca-cva-konzept.md, A1 to A6; Ralf 2026-10-17: "wie
empfohlen"). The arithmetic is app/aca_design.py, the analysis app/aca_analysis.py.

Pages (A2): for every attribute without a fixed order a page that rates its levels (1..pref_points); one page with the
importance of every attribute ("if two offers were the same otherwise, how important would the difference between
<best level> and <worst level> be", 1..imp_points); `pairs` pairs of partial profiles on a graded scale (A3; the
category from the point of view of the first offer, positive = first preferred, as for the ratings-based conjoint);
optionally calibration concepts with a purchase likelihood of 0 to 100 (A2). No question on unacceptable levels.
The pairs and the calibration concepts are built from the stored answer and its seed (replay, as for ACBC): a changed
rating or importance drops the pairs and the calibration, a changed pair drops the pairs after it and the calibration.

config: attributes, prohibitions, explain, hide_attr_labels (attributes.py; display is always the table with text),
        orders {attr id: "best_first" | "best_last"} (attributes with a fixed order, not rated), pref_points,
        imp_points, n_pairs (0 = aca_design.suggested_pairs; not "pairs": results.py reads that key as
        the pairs of a semantic differential), size_start, size_end, pair_points, cal (0 or 3..8), cal_size,
        auto_next
texts:  question (pairs), pref_question, pref_low, pref_high, imp_question, imp_low, imp_high, much, same,
        cal_question, alt_label (+ attribute texts)
answer: {"seed", "r": {attr id: [rating per level]}, "i": {attr id: rating}, "p": {"k": category}, "c": {"k": 0..100}}
"""
import json
import re
import threading

from app import aca_design
from app.items import register
from app.items.attributes import ATTRIBUTE_RULES, AttributeLevels
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_any, r_flag, r_int, r_one  # rules for survey files
from app.items.base import AnswerError, ItemType, in_lang, plain, read_cfg, whole
from app.lang import Both, de_en
from app.ui_i18n import pick, tr

ORDERS = ("best_first", "best_last")
DEFAULT_TEXTS = {
    "de": {"question": "Welches dieser beiden Angebote würden Sie vorziehen?",
           "pref_question": "Wie sehr sagen Ihnen diese Ausprägungen von „{attr}“ zu?",
           "pref_low": "gar nicht", "pref_high": "sehr",
           "imp_question": "Wenn zwei Angebote sonst gleich wären: Wie wichtig wäre Ihnen dieser Unterschied?",
           "imp_low": "nicht wichtig", "imp_high": "äußerst wichtig", "much": "viel lieber", "same": "gleich",
           "cal_question": "Wie wahrscheinlich würden Sie dieses Angebot wählen (0 bis 100)?", "alt_label": "Angebot"},
    "en": {"question": "Which of these two offers would you prefer?",
           "pref_question": "How much do you like these levels of “{attr}”?",
           "pref_low": "not at all", "pref_high": "very much",
           "imp_question": "If two offers were the same otherwise, how important would this difference be to you?",
           "imp_low": "not important", "imp_high": "extremely important", "much": "much preferred", "same": "same",
           "cal_question": "How likely would you be to choose this offer (0 to 100)?", "alt_label": "Offer"},
}
TEXT_KEYS = ("question", "pref_question", "pref_low", "pref_high", "imp_question", "imp_low", "imp_high", "much",
             "same", "cal_question", "alt_label")
PRICE_WORDS = re.compile(r"preis|price|kosten|cost|€|\$|eur\b", re.IGNORECASE)
DUMMY_NOISE = 0.5


@register
class Aca(AttributeLevels, ItemType):
    key = "aca"
    experimental = "aca"
    label = Both("Adaptive Conjoint (ACA)", "Adaptive conjoint (ACA)")
    short = Both("Stufen bewerten, dann Paare abwägen", "Rate levels, then weigh pairs")
    description = Both("Experimentell: Stufen und Merkmale selbst einschätzen, dann Paare aus Teilprodukten abgestuft vergleichen",
                       "Experimental: rate levels and attributes, then compare pairs of partial offers on a graded scale")
    order = 48
    fact_sheet = FactSheet(
        "choice",
        keywords=["ACA", "Adaptive Conjoint Analysis", "Adaptive conjoint", "Self-explicated", "Selbstauskunft",
                  "Teilnutzen", "Part-worths"],
        can=[("Stufen bewerten (oder feste Reihenfolge), Wichtigkeit je Merkmal, Paare aus Teilprodukten mit 2 bis 5 "
              "Merkmalen, die sich nach jeder Antwort anpassen, optional Kalibrierung mit Kaufwahrscheinlichkeit.",
              "Rate levels (or a fixed order), importance per attribute, pairs of partial offers with 2 to 5 attributes "
              "that adapt after every answer, optionally calibration with a purchase likelihood."),
             ("Teilnutzen und Wichtigkeiten je Person, Selbstauskunft neben Paaren, Marktsimulator.",
              "Part-worths and importances per person, self-explicated next to pairs, market simulator.")],
        good_for=[("Sehr viele Merkmale (bis etwa 30) und kleine Stichproben", "Very many attributes (up to about 30) and "
                                                                           "small samples"),
                  ("Wiederholung älterer Untersuchungen mit diesem Verfahren und Lehre",
                   "Repeating older research with this method and teaching")],
        not_for=[("Preise und Zahlungsbereitschaft (ACA unterschätzt den Preis; dann CBC oder ACBC)",
                  "Prices and willingness to pay (ACA underestimates the price; then CBC or ACBC)"),
                 ("Papierfragebögen (die Paare entstehen während der Befragung)",
                  "Paper questionnaires (the pairs arise during the survey)")],
        designs=["choice"],
        examples=[("Smartphone mit 10 Merkmalen, 20 Paare, 5 Kalibrierungskonzepte",
                   "A smartphone with 10 attributes, 20 pairs, 5 calibration concepts")],
    )
    import_rules = {**ATTRIBUTE_RULES, "orders": r_any, "pref_points": r_one(5, 7, 9), "imp_points": r_one(4, 5, 7),
                    "n_pairs": r_int(0, aca_design.MAX_PAIRS), "size_start": r_int(2, 5), "size_end": r_int(2, 5),
                    "pair_points": r_one(7, 9), "cal": r_int(0, 8), "cal_size": r_int(2, 8)}
    result_kind = "aca"
    levels = ("metric",)
    alone_on_page = True
    allowed_in_loop = allowed_in_separated = False
    supports_auto_next = True
    auto_next_inline = True
    auto_next_hint = Both("sobald eine Stufe der Skala gewählt ist (nur Paare)", "as soon as a point of the scale is chosen (pairs only)")
    editor_tabs = Both([("content", "1 · Inhalt", "Merkmale, Stufen, Reihenfolge"),
                        ("design", "2 · Ablauf", "Paare, Skalen, Kalibrierung"),
                        ("display", "3 · Darstellung", "")],
                       [("content", "1 · Content", "Attributes, levels, order"),
                        ("design", "2 · Flow", "Pairs, scales, calibration"),
                        ("display", "3 · Display", "")])
    panel_template = "items/aca/panel.html"
    design_lock = True
    long_export = "aca"
    long_export_label = Both("Person × Paar bzw. Konzept × Angebot", "Person × pair or concept × offer")
    long_export_note = ("- `aca_*_long.csv`: long format of the adaptive conjoint (participant × pair or calibration concept × "
                        "offer); `phase` pair or cal, levels of attributes not shown are empty; `answer` the category of "
                        "the pair from the point of view of the first offer (positive = first preferred) or the purchase "
                        "likelihood 0 to 100.")

    def import_config(self, config):
        config, unfit, unknown = super().import_config(config)
        orders = config.get("orders")
        if not isinstance(orders, dict) or not all(isinstance(k, str) and v in ORDERS for k, v in orders.items()):
            config["orders"] = {}
            if orders not in (None, {}):
                unknown = unknown + ["orders"]
        return config, unfit, unknown

    # --- defaults and editing ----------------------------------------------------------

    def default_config(self):
        return {"attributes": [], "orders": {}, "pref_points": 7, "imp_points": 4, "n_pairs": 0, "size_start": 2,
                "size_end": 3, "pair_points": 9, "cal": 0, "cal_size": 4, "auto_next": False}

    def default_texts(self, lang):
        return {**pick(DEFAULT_TEXTS, lang), "attributes": {}, "levels": {}}

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config)
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        for key in TEXT_KEYS:
            if key in form:
                lt[key] = form.get(key, "").strip()[:500]
        self.update_attributes(item, form, lang, structural, config, lt)
        if structural:
            ids = {a["id"] for a in config.get("attributes", [])}
            if any(k.startswith("order_") for k in form):
                config["orders"] = {aid: form.get(f"order_{aid}") for aid in ids if form.get(f"order_{aid}") in ORDERS}
            else:
                config["orders"] = {k: v for k, v in (config.get("orders") or {}).items() if k in ids}

            def pick_num(key, allowed, default):
                v = whole(form.get(key, ""))
                return v if v in allowed else config.get(key, default)

            def clamp(key, lo, hi, default):
                v = whole(form.get(key, ""))
                return config.get(key, default) if key not in form else (default if v is None else max(lo, min(hi, v)))

            config.update(pref_points=pick_num("pref_points", (5, 7, 9), 7), imp_points=pick_num("imp_points", (4, 5, 7), 4),
                          pair_points=pick_num("pair_points", (7, 9), 9),
                          n_pairs=clamp("n_pairs", 0, aca_design.MAX_PAIRS, 0), size_start=clamp("size_start", 2, 5, 2),
                          size_end=clamp("size_end", 2, 5, 3), cal_size=clamp("cal_size", 2, 8, 4))
            if "cal" in form:
                v = whole(form.get("cal", "")) or 0
                config["cal"] = 0 if v < 3 else min(8, v)
        texts[lang] = lt
        item.config, item.texts = config, texts

    # --- structure ---------------------------------------------------------------------

    def has_image_set(self, item):
        """ACA shows its levels as text only: no image set. AttributeLevels says True for the conjoint types with
        pictures; for ACA that made the editor and the flow count images through a required_keys it does not have
        (500 on both pages, pre-test 2026-10-20)."""
        return False

    def attrs(self, item):
        """The attributes of the config with their fixed order (aca_order) for app/aca_design.py."""
        orders = read_cfg(item).get("orders") or {}
        return [{**a, "aca_order": orders.get(a["id"], "")} for a in read_cfg(item).get("attributes", [])]

    def rated(self, item):
        return [a for a in self.attrs(item) if not a["aca_order"]]

    def n_pairs(self, item):
        c = read_cfg(item)
        counts = self.level_counts(item)
        if not counts:
            return 0
        return c.get("n_pairs") or aca_design.suggested_pairs(counts)

    def n_cal(self, item):
        c = read_cfg(item)
        n_attrs = len(c.get("attributes", []))
        return min(c.get("cal") or 0, n_attrs + 1) if (c.get("cal") or 0) >= 3 else 0

    def pair_size(self, item, k):
        c = read_cfg(item)
        n = self.n_pairs(item)
        size = c.get("size_start", 2) if k < n / 2 else c.get("size_end", 3)
        return max(1, min(size, len([a for a in c.get("attributes", []) if len(a.get("levels", [])) >= 2])))

    def pair_steps(self, item):
        m = (read_cfg(item).get("pair_points", 9) - 1) // 2
        return list(range(m, -m - 1, -1))

    def page_specs(self, item):
        specs = [{"explain": True}] if read_cfg(item).get("explain") else []
        specs += [{"phase": "pref", "attr": a["id"]} for a in self.rated(item)]
        specs.append({"phase": "imp"})
        specs += [{"phase": "pair", "k": k} for k in range(self.n_pairs(item))]
        specs += [{"phase": "cal", "k": k} for k in range(self.n_cal(item))]
        return specs

    def expand_pages(self, survey, item, rng):
        if not read_cfg(item).get("attributes") or self.validate_attributes(item):
            return None
        seed = rng.randrange(2 ** 31)
        return {"seed": seed}, [{**s, "seed": seed} if s.get("phase") in ("pref", "imp") else s for s in self.page_specs(item)]

    def preview_empty(self, item):
        if read_cfg(item).get("attributes"):
            return None
        return de_en("Die Vorschau erscheint, sobald Merkmale und Stufen eingetragen sind.",
                     "The preview appears as soon as attributes and levels are entered."), "content"

    # --- the interview, replayed from the answer ----------------------------------------

    _memo = threading.local()

    def plan(self, item, answer):
        """{"prior", "pairs": [(left, right)] up to the first pair not reached (including it), "answers" (per pair shown,
        None where it was left open), "answered", "final", "cal"} of one stored answer; cached per answer and
        configuration (the cache holds the last 64)."""
        answer = answer if isinstance(answer, dict) else {}
        key = (item.id, json.dumps(answer, sort_keys=True, default=str), json.dumps(read_cfg(item), sort_keys=True,
                                                                                     default=str))
        cache = getattr(self._memo, "cache", None)
        if cache is None or len(cache) > 64:
            cache = self._memo.cache = {}
        if key in cache:
            return cache[key]
        attrs = self.attrs(item)
        c = read_cfg(item)
        prior = aca_design.prior(attrs, answer, c.get("imp_points", 4))
        seed = answer.get("seed", 0)
        given = answer.get("p") or {}
        # pairs: every pair shown (and the next one); a pair left open counts as shown, so the later pairs still
        # follow, but gives the estimate nothing; a pair not reached yet ends the list
        pairs, shown, used, ys = [], [], [], []
        for k in range(self.n_pairs(item)):
            u = aca_design.update(attrs, prior, used, ys)
            pair = aca_design.choose_pair(attrs, u, pairs, self.pair_size(item, k), seed, k, self._allowed(item))
            pairs.append(pair)
            if str(k) not in given:
                break
            y = given[str(k)]
            shown.append(y if isinstance(y, int) else None)
            if isinstance(y, int):
                used.append(pair)
                ys.append(y)
        final = aca_design.final(attrs, prior, used, ys)
        cal = aca_design.calibration_concepts(attrs, final, self.n_cal(item), c.get("cal_size", 4), seed, self._allowed(item))
        out = {"prior": prior, "pairs": pairs, "answers": shown, "answered": len(ys), "final": final, "cal": cal}
        cache[key] = out
        return out

    def _allowed(self, item):
        bad = self.prohibitions(item)

        def allowed(concept):
            return not any(concept.get(a) == la and concept.get(b) == lb for (a, la), (b, lb) in bad)
        return allowed

    def task_pair(self, item, answer, k):
        p = self.plan(item, answer)["pairs"]
        return p[k] if 0 <= k < len(p) else None

    def cal_concept(self, item, answer, k):
        cal = self.plan(item, answer)["cal"]
        return cal[k] if 0 <= k < len(cal) else None

    def best_worst(self, item, answer, lang):
        """Per attribute (label, best level, worst level) after the ratings (or the fixed order)."""
        attrs = self.attrs(item)
        texts = self.attributes(item, lang)
        out = []
        for a, (attr, t) in enumerate(zip(attrs, texts)):
            d = aca_design.desirability(a, attr, (answer or {}).get("r") or {})
            best, worst = max(range(len(d)), key=lambda i: (d[i], -i)), min(range(len(d)), key=lambda i: (d[i], i))
            out.append({"id": attr["id"], "label": t["label"], "best": t["level_labels"][best],
                        "worst": t["level_labels"][worst], "same": d[best] == d[worst]})
        return out

    def timing_pages(self, item):
        out = [("_x", "Erklärungsseite", ".x")] if read_cfg(item).get("explain") else []
        out += [(f"_r{n + 1}", f"Stufen bewerten {n + 1}", f".r{n}") for n in range(len(self.rated(item)))]
        out.append(("_i", "Wichtigkeit", ".i"))
        out += [(f"_p{k + 1}", f"Paar {k + 1}", f".p{k}") for k in range(self.n_pairs(item))]
        out += [(f"_c{k + 1}", f"Kalibrierung {k + 1}", f".c{k}") for k in range(self.n_cal(item))]
        return out

    def seconds(self, item, read):
        c = read_cfg(item)
        levels = sum(len(a.get("levels", [])) for a in self.rated(item))
        return (read + 2 * levels + 3 * len(c.get("attributes", [])) + 8 * self.n_pairs(item) + 8 * self.n_cal(item)
                + (15 if c.get("explain") else 0))

    # --- checks --------------------------------------------------------------------------

    def validate(self, item):
        problems = self.validate_attributes(item)
        c = read_cfg(item)
        if c.get("attributes") and not problems and len(c["attributes"]) < 2:
            problems.append(de_en("mindestens zwei Merkmale", "at least two attributes"))
        return problems

    def price_like(self, item, lang=None):
        """The attributes that look like a price (label or level numbers with a currency)."""
        out = []
        for a in self.attributes(item, lang):
            if a.get("price_attr") or PRICE_WORDS.search(plain(a["label"])) or \
                    any(PRICE_WORDS.search(plain(x)) for x in a["level_labels"]):
                out.append(plain(a["label"]))
        return out

    def check_warnings(self, item):
        out = []
        price = self.price_like(item)
        if price:
            out.append(de_en("ACA unterschätzt die Wichtigkeit des Preises ({names}), weil die Paare nur Teilprodukte zeigen "
                             "(Pinnell, 1994). Für Preisfragen besser CBC oder ACBC.",
                             "ACA underestimates the importance of the price ({names}) because the pairs only show partial "
                             "offers (Pinnell, 1994). For price questions CBC or ACBC are better.", names=", ".join(price)))
        return out

    design_warnings = check_warnings

    def source_groups(self, item):
        return [(de_en("Adaptive Conjoint (ACA): Ablauf, Wahl der Paare und Schätzung",
                       "Adaptive conjoint (ACA): flow, choice of the pairs and estimation"),
                 [aca_design.JOHNSON, aca_design.GREEN_KRIEGER_AGARWAL]),
                (de_en("ACA und der Preis", "ACA and the price"), [aca_design.PINNELL])]

    def references(self, item):
        return [s for _, group in self.source_groups(item) for s in group]

    # --- answers --------------------------------------------------------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        meta = meta or {}
        if meta.get("explain"):
            return {"explained": True}
        c = read_cfg(item)
        phase = meta.get("phase")
        extra = {"seed": int(meta["seed"])} if "seed" in meta else {}

        def value(name, lo, hi):
            raw = form.get(name, "").strip()
            if raw == "":
                return None
            try:
                v = int(raw)
            except ValueError:
                raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice.")) from None
            if not lo <= v <= hi:
                raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
            return v

        if phase == "pref":
            attr = next((a for a in c.get("attributes", []) if a["id"] == meta.get("attr")), None)
            if attr is None:
                return extra
            row = [value(f"i{item.id}_r{n}", 1, c.get("pref_points", 7)) for n in range(len(attr.get("levels", [])))]
            if item.required and any(v is None for v in row):
                raise AnswerError(tr(lang, "Bitte bewerten Sie jede Ausprägung.", "Please rate every level."))
            return {"r": {attr["id"]: row}, **extra}
        if phase == "imp":
            imp = {a["id"]: value(f"i{item.id}_i{n}", 1, c.get("imp_points", 4)) for n, a in enumerate(c.get("attributes", []))}
            if item.required and any(v is None for v in imp.values()):
                raise AnswerError(tr(lang, "Bitte beantworten Sie jede Zeile.", "Please answer every row."))
            return {"i": imp, **extra}
        if phase == "pair":
            m = (c.get("pair_points", 9) - 1) // 2
            v = value(f"i{item.id}_p{meta.get('k', 0)}", -m, m)
            if v is None and item.required:
                raise AnswerError(tr(lang, "Bitte wählen Sie eine Antwort.", "Please choose an answer."))
            return {"p": {str(meta.get("k", 0)): v}}
        if phase == "cal":
            v = value(f"i{item.id}_c{meta.get('k', 0)}", 0, 100)
            if v is None and item.required:
                raise AnswerError(tr(lang, "Bitte wählen Sie eine Antwort.", "Please choose an answer."))
            return {"c": {str(meta.get("k", 0)): v}}
        return extra

    def display_value(self, item, form, meta=None):
        return {}

    def merge_answer(self, old, new):
        """A changed rating or importance drops the pairs and the calibration; a changed pair the pairs after it and
        the calibration (they were chosen from the earlier answers)."""
        old, new = dict(old or {}), dict(new or {})
        out = {**old}
        if "seed" in new:
            out["seed"] = new["seed"]
        for key in ("r", "i"):
            if key in new:
                merged = {**(old.get(key) or {}), **new[key]}
                if merged != (old.get(key) or {}):
                    out.pop("p", None)
                    out.pop("c", None)
                out[key] = merged
        if "p" in new:
            pairs = dict(old.get("p") or {})
            for k, v in new["p"].items():
                if pairs.get(k) != v:
                    pairs = {j: x for j, x in pairs.items() if int(j) < int(k)}
                    out.pop("c", None)
                pairs[k] = v
            out["p"] = pairs
        if "c" in new:
            out["c"] = {**(out.get("c") or {}), **new["c"]}
        if "explained" in new:
            out["explained"] = True
        return out

    def is_missing(self, item, answer):
        answer = answer if isinstance(answer, dict) else {}
        if answer in ({}, {"explained": True}):
            return answer == {}
        given = list((answer.get("p") or {}).values()) + list((answer.get("i") or {}).values())
        return not given or all(v is None for v in given)

    def dummy_fields(self, item, rng, meta, answers=None):
        """Self-explicated answers, pairs and calibration from a dummy person's part-worths (as for CBC dummies)."""
        from app.items.conjoint import dummy_person

        meta = meta or {}
        if meta.get("explain"):
            return {}
        c = read_cfg(item)
        person = dummy_person(self, item, rng, meta.get("respondent"))
        pw = person["pw"]
        attrs = c.get("attributes", [])
        phase = meta.get("phase")
        if phase == "pref":
            a = next((n for n, x in enumerate(attrs) if x["id"] == meta.get("attr")), None)
            if a is None:
                return {}
            top = c.get("pref_points", 7)
            spread = max(max(pw[a]) - min(pw[a]), 1e-6)
            return {f"i{item.id}_r{n}": str(max(1, min(top, round(1 + (x - min(pw[a])) / spread * (top - 1)
                                                                   + rng.gauss(0, DUMMY_NOISE)))))
                    for n, x in enumerate(pw[a])}
        if phase == "imp":
            top = c.get("imp_points", 4)
            ranges = [max(r) - min(r) for r in pw]
            hi = max(ranges) or 1.0
            return {f"i{item.id}_i{n}": str(max(1, min(top, round(1 + r / hi * (top - 1) + rng.gauss(0, DUMMY_NOISE)))))
                    for n, r in enumerate(ranges)}
        answer = (answers or {}).get(item.id) if isinstance(answers, dict) else None
        if answer is None and isinstance(answers, dict):
            answer = answers.get(str(item.id))
        if phase == "pair":
            pair = self.task_pair(item, answer, meta.get("k", 0))
            if not pair:
                return {}
            m = (c.get("pair_points", 9) - 1) // 2
            d = sum(pw[a][pair[0][a]] - pw[a][pair[1][a]] for a in pair[0])
            return {f"i{item.id}_p{meta.get('k', 0)}": str(max(-m, min(m, round(1.5 * d + rng.gauss(0, DUMMY_NOISE)))))}
        if phase == "cal":
            concept = self.cal_concept(item, answer, meta.get("k", 0))
            if not concept:
                return {}
            import math

            u = sum(pw[a][lv] for a, lv in concept.items()) + rng.gauss(0, DUMMY_NOISE)
            return {f"i{item.id}_c{meta.get('k', 0)}": str(round(100 / (1 + math.exp(-u))))}
        return {}

    # --- export -------------------------------------------------------------------------------

    def export_columns(self, item):
        code = item.code
        attrs = self.attributes(item, item.survey.default_language)
        rated = {a["id"] for a in self.rated(item)}
        cols = []
        for a in attrs:
            if a["id"] in rated:
                cols += [(f"{code}_r_{a['code']}_{k}", f"Bewertung der Stufe: {plain(a['label'])} = {plain(lv)}")
                         for k, lv in enumerate(a["level_labels"], start=1)]
        cols += [(f"{code}_i_{a['code']}", f"Wichtigkeit (Selbstauskunft): {plain(a['label'])}") for a in attrs]
        cols += [(f"{code}_p{k + 1}", f"Paar {k + 1}: Urteil (positiv = erstes Angebot lieber)") for k in range(self.n_pairs(item))]
        cols += [(f"{code}_c{k + 1}", f"Kalibrierung {k + 1}: Wahrscheinlichkeit 0–100") for k in range(self.n_cal(item))]
        for a in attrs:
            cols += [(f"{code}_pw_{a['code']}_{k}", f"Teilnutzen {plain(a['label'])}: {plain(lv)}")
                     for k, lv in enumerate(a["level_labels"], start=1)]
        cols += [(f"{code}_imp_{a['code']}", f"Wichtigkeit (Teilnutzen): {plain(a['label'])}") for a in attrs]
        if self.n_cal(item):
            cols.append((f"{code}_cal_r2", "R² der Kalibrierung"))
        return cols

    def missing_codes(self, item, column):
        return ()

    def export_values(self, item, answer):
        from app import aca_analysis

        answer = answer if isinstance(answer, dict) else {}
        code = item.code
        attrs = read_cfg(item).get("attributes", [])
        rated = {a["id"] for a in self.rated(item)}
        out = {}
        for a in attrs:
            if a["id"] in rated:
                row = (answer.get("r") or {}).get(a["id"]) or []
                for k in range(len(a.get("levels", []))):
                    out[f"{code}_r_{a['code']}_{k + 1}"] = row[k] if k < len(row) else None
            out[f"{code}_i_{a['code']}"] = (answer.get("i") or {}).get(a["id"])
        for k in range(self.n_pairs(item)):
            out[f"{code}_p{k + 1}"] = (answer.get("p") or {}).get(str(k))
        for k in range(self.n_cal(item)):
            out[f"{code}_c{k + 1}"] = (answer.get("c") or {}).get(str(k))
        fitted = aca_analysis.person(item, answer)
        for j, a in enumerate(attrs):
            for k in range(len(a.get("levels", []))):
                out[f"{code}_pw_{a['code']}_{k + 1}"] = round(fitted["pw"][j][k], 4) if fitted else None
            imp = fitted["importance"][j] if fitted else None
            out[f"{code}_imp_{a['code']}"] = round(imp, 4) if imp is not None else None
        if self.n_cal(item):
            out[f"{code}_cal_r2"] = round(fitted["cal_r2"], 4) if fitted and fitted.get("cal_r2") is not None else None
        return out

    def column_level(self, item, column):
        return "metric"

    def codebook_info(self, item, column, lang, ui=None):
        code, c = item.code, read_cfg(item)
        if column.startswith(f"{code}_r_"):
            return "ACA", in_lang(ui, "1 bis {n}", "1 to {n}", n=c.get("pref_points", 7)), [in_lang(
                ui, "Bewertung der Stufe durch die Person (größer = besser)", "the person's rating of the level (higher = better)")]
        if column.startswith(f"{code}_i_"):
            return "ACA", in_lang(ui, "1 bis {n}", "1 to {n}", n=c.get("imp_points", 4)), [in_lang(
                ui, "Wichtigkeit des Unterschieds zwischen bester und schlechtester Stufe (Selbstauskunft)",
                "importance of the difference between the best and the worst level (self-explicated)")]
        if re.fullmatch(rf"{re.escape(code)}_p\d+", column):
            m = (c.get("pair_points", 9) - 1) // 2
            return "ACA", in_lang(ui, "{lo} bis {hi}", "{lo} to {hi}", lo=-m, hi=m), [in_lang(
                ui, "positiv = das zuerst gezeigte Angebot lieber, 0 = gleich; welche Stufen das Paar zeigte: Langformat",
                "positive = the offer shown first preferred, 0 = same; which levels the pair showed: long format")]
        if re.fullmatch(rf"{re.escape(code)}_c\d+", column):
            return "ACA", in_lang(ui, "0 bis 100", "0 to 100"), [in_lang(
                ui, "Wahrscheinlichkeit, das Angebot zu wählen; welches Angebot: Langformat",
                "likelihood of choosing the offer; which offer: long format")]
        if column.startswith(f"{code}_pw_"):
            return "Zahl", "", [in_lang(ui, "Teilnutzen der Person (Selbstauskunft und Paare kombiniert, "
                                            "nullzentriert je Merkmal; mit Kalibrierung in Logit-Einheiten)",
                                        "part-worth of the person (self-explicated and pairs combined, "
                                        "zero-centred per attribute; with calibration in logit units)")]
        if column.startswith(f"{code}_imp_"):
            return "Zahl", in_lang(ui, "von 0 bis 1", "from 0 to 1"), [in_lang(
                ui, "Spannweite der Teilnutzen des Merkmals / Summe aller Spannweiten",
                "range of the attribute's part-worths / sum of all ranges")]
        if column == f"{code}_cal_r2":
            return "Zahl", in_lang(ui, "von 0 bis 1", "from 0 to 1"), [in_lang(
                ui, "wie gut die Teilnutzen die Kaufwahrscheinlichkeiten der Kalibrierung vorhersagen (Logit-Regression)",
                "how well the part-worths predict the purchase likelihoods of the calibration (logit regression)")]
        return None

    def result_column(self, item, column):
        return None, "", None

    def results(self, item, table, lang):
        from app import aca_analysis
        from app.choice_model import responses_for

        return aca_analysis.analyse(item, responses_for(item, table), lang)

    def long_columns(self, item):
        codes = [a["code"] for a in read_cfg(item).get("attributes", [])]
        return ["phase", "task", "side"] + codes + [c + "_label" for c in codes] + ["answer"]

    def long_rows(self, item, response):
        answer = (response.answers or {}).get(str(item.id))
        if not isinstance(answer, dict):
            return
        attrs = self.attributes(item, item.survey.default_language)
        plan = self.plan(item, answer)

        def row(phase, task, side, concept, y):
            return ([phase, task, side] + [concept[a] + 1 if a in concept else "" for a in range(len(attrs))]
                    + [plain(attrs[a]["level_labels"][concept[a]]) if a in concept else "" for a in range(len(attrs))]
                    + ["" if y is None else y])

        for k, y in enumerate(plan["answers"]):
            left, right = plan["pairs"][k]
            yield row("pair", k + 1, 1, left, y)
            yield row("pair", k + 1, 2, right, y)
        for k, concept in enumerate(plan["cal"]):
            y = (answer.get("c") or {}).get(str(k))
            if y is not None:
                yield row("cal", k + 1, 1, concept, y)

    def questionnaire_lines(self, item, lang):
        out = [f"  - Merkmale `{a['code']}` {a['label']}: " + "; ".join(a["level_labels"]) for a in self.attributes(item, lang)]
        out.append(f"  - adaptive conjoint: level ratings, importance, {self.n_pairs(item)} adaptive pairs"
                   + (f", {self.n_cal(item)} calibration concepts" if self.n_cal(item) else ""))
        return out
