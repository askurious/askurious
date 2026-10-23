"""Choice-based conjoint (CBC).

Attributes with levels, a D-efficient design (app/conjoint_design.py) generated in draft and
frozen at launch, T tasks with J alternatives each (one task per page), optional
"none of these". Display per study: table, image, or both. Only attributes marked "visual"
go into the images, so the number of images is the number of distinct combinations of
visual levels in the design, not the number of profiles.

config: attributes [{id, code, visual, levels: [{id, hint}]}] (texts per language: attributes, levels,
        level_defs = definition per level for the explanation page and the image prompt), tasks, alternatives, versions,
        none ("none of these" as an option), dual (dual response: after the choice "would you really choose it?",
        Ja/Nein; never together with none), display (table | image | both), attr_order (fixed | participant), shuffle_alts (display order of
        the options per participant and task; answers keep the option's number), scene,
        design {sig, versions, d_eff}, holdouts [{id, alts: [[level id, ...], ...]}] (app/holdouts.py)
texts:  question, attributes {id: label}, levels {id: label}, none_label, alt_label, dr_question, dr_yes, dr_no
answer: {"t<task>": option 1..J (0 = none), "h<holdout>": the same for a holdout task, with dual response
        "dt<task>" / "dh<holdout>": 1 = would choose it, 0 = would not}
"""
import hashlib
import json

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en, de_en_n, english
from app import holdouts
from app.items.base import r_any, r_flag, r_id, r_int, r_list, r_one, r_rows, r_text  # rules for survey files, see base.py
from app.items.attributes import ATTRIBUTE_RULES, MAX_ATTRIBUTES, AttributeLevels, display_from_form
from app.items.base import AnswerError, ItemType, in_lang, whole
from app.ui_i18n import pick, tr
from app.segments import clean as clean_segments  # shown latent class solution and segment names
from app.simulator import clean as clean_scenarios  # saved scenarios of the market simulator
from app.units import fixed

DISPLAYS = {"table": "Tabelle mit Text", "image": "Ein Bild je Option", "both": "Ein Bild je Option, Tabelle darunter"}
MIN_TASKS = 6  # fewer tasks per person: the check and the HB block warn (H-11; Festlegung von Askurious)
DUMMY_SPREAD = 1.0  # sd of the true part-worths of dummy data (as items/maxdiff.py)
DUMMY_PERSON = 0.5  # sd of the person deviations
DUMMY_PRICE = 1.5   # utility the dummies lose from the lowest to the highest price
DUMMY_NONE = -0.5   # utility of "none" relative to an average concept


def _value(values, level):
    """The value of a level for the long format: empty where the level has none or was not shown."""
    v = values[level] if level is not None and 0 <= level < len(values) else None
    return "" if v is None else v


def gumbel(rng):
    import math

    return -math.log(-math.log(max(rng.random(), 1e-12)))


def dummy_person(kind, item, rng, respondent, prices=None):
    """Part-worths of one dummy person ({"pw": [[per level] per attribute], "none", "price"}), kept on the generator
    between the pages of this person. prices: per attribute the price component per level (ACBC) or None; a CBC
    attribute marked as price with numbers gets part-worths falling linearly with the price instead of drawn ones.
    "price": utility per currency unit (0 without a price)."""
    import random

    persons = rng.__dict__.setdefault("_conjoint_persons", {})
    key = (item.id, respondent)
    if key in persons:
        return persons[key]
    true = random.Random(f"conjoint:{item.id}")
    counts = kind.level_counts(item)
    base = []
    for n in counts:
        pw = [true.gauss(0, DUMMY_SPREAD) for _ in range(n)]
        base.append([x - sum(pw) / max(n, 1) for x in pw])
    per_unit = 0.0
    pa = kind.price_attribute(item) if prices is None else None
    numbers = kind.level_numbers(item, pa) if pa is not None else None
    if numbers and max(numbers) > min(numbers):
        per_unit = -DUMMY_PRICE / (max(numbers) - min(numbers))
        middle = sum(numbers) / len(numbers)
        base[pa] = [per_unit * (x - middle) for x in numbers]
    elif prices:
        spread = sum(max(p) - min(p) for p in prices if p)
        per_unit = -DUMMY_PRICE / spread if spread > 0 else 0.0
    person = {"pw": [[x + rng.gauss(0, DUMMY_PERSON) for x in pw] for pw in base],
              "none": DUMMY_NONE + rng.gauss(0, DUMMY_PERSON),
              "price": per_unit * max(0.2, 1 + rng.gauss(0, 0.3))}  # every dummy dislikes higher prices
    if numbers and per_unit:
        person["pw"][pa] = [person["price"] * (x - middle) for x in numbers]
    persons[key] = person
    return person
DEFAULT_TEXTS = {
    "de": {"question": "Welche der folgenden Optionen würden Sie wählen?", "none_label": "Keine davon",
           "alt_label": "Option", "dr_question": "Würden Sie sich in Wirklichkeit für die gewählte Option entscheiden?",
           "dr_yes": "Ja", "dr_no": "Nein"},
    "en": {"question": "Which of the following options would you choose?", "none_label": "None of these",
           "alt_label": "Option", "dr_question": "Would you really choose the option you selected?",
           "dr_yes": "Yes", "dr_no": "No"},
}
NONE_MODES = ("", "option", "dual")  # editor: no "none", "none of these" as an option, dual response


@register
class Conjoint(AttributeLevels, ItemType):
    key = "conjoint"
    import_rules = {**ATTRIBUTE_RULES, "tasks": r_int(1, 30), "alternatives": r_int(2, 6), "versions": r_int(1, 20),
                    "none": r_flag, "dual": r_flag, "display": r_one(*DISPLAYS), "attr_order": r_one("fixed", "participant"),
                    "shuffle_alts": r_flag, "scene": r_text(2000), "show_visual_in_table": r_flag,
                    "stack_mobile": r_flag, "design": r_any,  # see import_config
                    "holdouts": r_rows(holdouts.MAX, id=r_id, alts=r_list(6, r_list(MAX_ATTRIBUTES, r_id))), "sim": clean_scenarios,
                    "lc": clean_segments}

    def import_config(self, config):
        """A design of the file is kept only if it is one this item could have generated: versions x tasks x
        alternatives profiles, each naming a level that exists for every attribute. Otherwise the item comes
        without a design, as before "Design erzeugen"."""
        config, unfit, unknown = super().import_config(config)
        design = config.get("design")
        if design is not None and not unfit:
            counts = [len(a["levels"]) for a in config["attributes"]]
            versions = design.get("versions") if isinstance(design, dict) else None
            fits = (isinstance(versions, list) and len(versions) == config["versions"] and all(
                isinstance(v, list) and len(v) == config["tasks"] and all(
                    isinstance(t, list) and len(t) == config["alternatives"] and all(
                        isinstance(p, list) and len(p) == len(counts)
                        and all(type(x) is int and 0 <= x < n for x, n in zip(p, counts)) for p in t) for t in v)
                for v in versions))
            eff = design.get("d_eff") if fits else None
            if fits and isinstance(design.get("sig"), str) and len(design["sig"]) <= 40 and type(eff) in (int, float) and 0 <= eff <= 1e6:
                config["design"] = {"sig": design["sig"], "versions": versions, "d_eff": eff}
            else:
                config["design"] = None
                unknown = unknown + ["design"]
        if not unfit and "holdouts" in config and holdouts.import_fit(config, "alts") is None:
            config["holdouts"] = []  # levels the file does not have
            unknown = unknown + ["holdouts"]
        return config, unfit, unknown
    result_kind = "cbc"  # own summary: choice shares per level and an aggregate logit (app/cbc_analysis.py)
    levels = ("nominal",)  # design version and chosen option per task
    label = "Conjoint (CBC)"
    short = Both("Auswahlaufgaben mit Merkmalen", "Choice tasks with attributes")
    description = Both("Auswahlaufgaben mit Merkmalen und Stufen, als Tabelle, Bild oder beides",
                       "Choice tasks with attributes and levels, as a table, an image or both")
    order = 45
    fact_sheet = FactSheet(
        "choice",
        keywords=["CBC", "Choice-based conjoint", "Discrete choice", "Wahlexperiment", "Choice experiment", "DCE"],
        can=[("Merkmale mit Stufen, Aufgaben und Optionen, Versionen (Blocking), optional „keine davon“.",
              "Attributes with levels, tasks and options, versions (blocking), optionally “none of these”."),
             ("Anzeige als Tabelle, als Produktbild oder als Produktbild mit Tabelle darunter; Bilder von der KI oder hochgeladen.",
              "Shown as a table, as a product image or as a product image with a table below; images from AI or uploaded."),
             ("Merkmalsreihenfolge fest oder je Person; Optionen gemischt; Haltefragen.",
              "Attribute order fixed or per person; options shuffled; holdout tasks."),
             ("Nutzenwerte und Wichtigkeiten (Hierarchical Bayes), Simulator für Marktanteile.",
              "Part-worth utilities and importances (Hierarchical Bayes), simulator for market shares.")],
        good_for=[("Welche Merkmale und Stufen Menschen vorziehen und wie sie abwägen",
                   "Which attributes and levels people prefer and how they trade them off"),
                  ("Zahlungsbereitschaft und Marktanteile", "Willingness to pay and market shares")],
        not_for=[("Einstellungen oder Zusammenhänge (dann Skalen)", "Attitudes or associations (then scales)"),
                 ("Mehr als etwa 6 Merkmale (dann ACBC)", "More than about 6 attributes (then ACBC)")],
        designs=["choice"],
        examples=[("Serviceroboter mit 4 Merkmalen, KI-Bilder für Aussehen und Größe, 8 Aufgaben zu je 2 Optionen",
                   "Service robots with 4 attributes, AI images for appearance and size, 8 tasks of 2 options each")],
    )
    alone_on_page = True  # one page per task
    allowed_in_loop = allowed_in_separated = False  # design version per response (layout["cj"])
    editor_tabs = Both([("content", "1 · Inhalt", "Merkmale, Stufen, Erklärung"),
                        ("design", "2 · Ablauf", "Aufgaben, verbotene Kombinationen, Berechnung"),
                        ("display", "3 · Darstellung & Bilder", "")],
                       [("content", "1 · Content", "Attributes, levels, explanation"),
                        ("design", "2 · Flow", "Tasks, prohibitions, calculation"),
                        ("display", "3 · Display & images", "")])
    panel_template = "items/conjoint/panel.html"
    auto_next_hint = Both("nach der Auswahl einer Möglichkeit", "after choosing an option")
    design_lock = True  # structure locked once real data exist (ItemType.design_locked)
    long_export = "conjoint"
    long_export_label = Both("Person × Aufgabe × Alternative", "Person × task × alternative")
    long_export_note = ("- `conjoint_*_long.csv`: long format for choice models (participant × task × alternative); "
                        "`alt` is the alternative's number in the design, `position` where it was shown (1 = first); "
                        "`<attribute>_value`: the value given to the level in the editor, where there is one; "
                        "with dual response `buy`: the answer to the follow-up question of the task (1 = would choose "
                        "the chosen option, 0 = would not).")

    # --- defaults and editing ----------------------------------------------------------

    def one_click(self, item):
        return True

    def default_config(self):
        return {"attributes": [], "tasks": 8, "alternatives": 2, "versions": 8, "none": False, "dual": False,
                "display": "table", "attr_order": "fixed", "scene": "", "show_visual_in_table": False,
                "stack_mobile": True,  # with images, on a phone held upright: the options one below the other
                "design": None, "auto_next": True,  # no Back/Next by default: choosing an option moves on
                "shuffle_alts": True,  # options in random order per participant and task (answers keep the option)
                "holdouts": []}

    def default_texts(self, lang):
        return {**pick(DEFAULT_TEXTS, lang), "attributes": {}, "levels": {}}

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config)
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        for key in ("question", "none_label", "alt_label", "dr_question", "dr_yes", "dr_no"):
            # "none_label" only where the page shows it (with the option "Keine davon", W34): elsewhere it stays
            if key in form or key in ("question", "alt_label"):
                lt[key] = form.get(key, "").strip()[:500]
        self.update_attributes(item, form, lang, structural, config, lt)
        if structural:
            display, level_images = display_from_form(form, DISPLAYS)

            def num(key, lo, hi, default):
                v = form.get(key, "")
                return max(lo, min(hi, whole(v))) if whole(v) is not None else default

            config.update(
                tasks=num("tasks", 1, 30, 8), alternatives=num("alternatives", 2, 6, 2), versions=num("versions", 1, 20, 8),
                none=self._none_mode(form) == "option", dual=self._none_mode(form) == "dual",
                display=display, level_images=level_images,
                attr_order="participant" if form.get("attr_order") == "participant" else "fixed",
                shuffle_alts=bool(form.get("shuffle_alts")),
                # hidden fields are not submitted: keep the scene when switching to "table"
                scene=form.get("scene", item.config.get("scene", "")).strip()[:2000],
                show_visual_in_table=bool(form.get("show_visual_in_table")),
                # shown with images only: kept when the display is "table"
                stack_mobile=bool(form.get("stack_mobile")) if display in ("image", "both")
                else item.config.get("stack_mobile", True),
            )
            if "ho_count" in form:  # holdout tasks (app/holdouts.py), locked with the design
                config["holdouts"] = holdouts.cbc_from_form(item, config, form)
        texts[lang] = lt
        item.config, item.texts = config, texts

    @staticmethod
    def _none_mode(form):
        """"" | "option" | "dual" from the editor's choice (older forms: the tick "none")."""
        mode = form.get("none_mode")
        if mode in NONE_MODES:
            return mode
        return "option" if form.get("none") else ""

    def dual(self, item):
        """True with dual response: every task a choice among the options, then "would you really choose it?"."""
        return bool((item.config or {}).get("dual")) and not (item.config or {}).get("none")

    def prohibition_count(self, item):
        """How often forbidden pairs occur in the current design (should be 0)."""
        d = item.config.get("design") or {}
        bad = self.prohibitions(item)
        return sum(1 for v in d.get("versions", []) for t in v for prof in t
                   for (a, la), (b, lb) in bad if prof and prof[a] == la and prof[b] == lb)

    # --- design ----------------------------------------------------------------------

    def signature(self, item):
        c = item.config
        shape = [self.level_counts(item), c.get("tasks"), c.get("alternatives"), c.get("versions")]
        if self.prohibitions(item):  # new or removed prohibitions make the design outdated
            shape.append(self.prohibitions(item))
        return hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]

    def preview_empty(self, item):
        if self.design_current(item):
            return None
        if not item.config.get("attributes"):
            return de_en("Die Vorschau erscheint, sobald Merkmale und Stufen eingetragen und der Versuchsplan erzeugt ist.",
                         "The preview appears as soon as attributes and levels are entered and the experimental design is generated."), "content"
        return de_en("Die Vorschau erscheint, sobald der Versuchsplan erzeugt ist.",
                     "The preview appears as soon as the experimental design is generated."), "design"

    def design_current(self, item):
        d = item.config.get("design")
        return bool(d) and d.get("sig") == self.signature(item)

    def make_design(self, item):
        """Generate the design within DESIGN_SECONDS; returns the Budget (budget.cut: the search was cut short)."""
        from flask import current_app

        from app.conjoint_design import Budget, generate

        c = item.config
        budget = Budget(current_app.config.get("DESIGN_SECONDS", 20))
        design, eff = generate(self.level_counts(item), c["tasks"], c["alternatives"], c["versions"],
                               forbidden=self.prohibitions(item), budget=budget)
        item.config = {**c, "design": {"sig": self.signature(item), "versions": design, "d_eff": round(eff, 4)}}
        return budget

    def design_efficiency(self, item):
        """D-efficiency of the stored design, 0 to 1 (conjoint_design.d_efficiency); computed afresh, so a design
        stored before the scale was normalised shows on the same scale."""
        from app.conjoint_design import d_efficiency

        design = item.config.get("design")
        if not design or not self.design_current(item):
            return None
        return d_efficiency(design["versions"], self.level_counts(item), item.config["alternatives"])

    def design_frequency(self, item, lang=None):
        """Per attribute of the stored design {"label", "min", "max"}: how often its rarest and its most frequent level
        occur over all versions (the D-efficiency does not see this, conjoint_design.W_BAL); None without a design."""
        from app.conjoint_design import level_frequency

        design = item.config.get("design")
        if not design or not self.design_current(item):
            return None
        attrs = self.attributes(item, lang)
        return [{"label": a["label"], "min": lo, "max": hi, "ratio": hi / lo if lo else float("inf")}
                for a, (lo, hi) in zip(attrs, level_frequency(design["versions"], self.level_counts(item)))]

    def too_few_tasks(self, item):
        """Text for a design whose part-worths cannot all be estimated (D-efficiency 0), else None."""
        if self.design_efficiency(item) != 0:
            return None
        c = item.config
        k = sum(n - 1 for n in self.level_counts(item))
        versions = de_en_n(c["versions"], ("{n} Version", "{n} Versionen"), ("{n} version", "{n} versions"))
        return de_en("D-Effizienz 0: Mit {tasks} Aufgaben × {versions} und {alts} Alternativen lassen sich "
                     "die {k} Teilnutzen nicht alle trennen. Mehr Aufgaben, Alternativen oder Versionen wählen.",
                     "D-efficiency 0: with {tasks} tasks × {versions} and {alts} alternatives the {k} "
                     "part-worths cannot all be separated. Choose more tasks, alternatives or versions.",
                     tasks=c['tasks'], versions=versions, alts=c['alternatives'], k=k)

    def design_summary(self, item):
        eff = self.design_efficiency(item)
        return de_en("Versuchsplan erzeugt (D-Effizienz {eff}).", "Experimental design generated (D-efficiency {eff}).",
                     eff=fixed(eff, 2) if english() else fixed(eff, 2).replace('.', ',')) + (
            f" {self.too_few_tasks(item)}" if eff == 0 else "")

    def source_groups(self, item):
        from app.conjoint_design import HUBER_ZWERINA, SOURCES

        return [(de_en("Stichprobe und Zahl der Aufgaben (Faustregel; das 1,5-Fache als Empfehlung ist eine Festlegung "
                       "von Askurious)", "Sample and number of tasks (rule of thumb; 1.5 times as the recommendation is a "
                       "setting of Askurious)"), list(SOURCES)),
                (de_en("Versuchsplan: Information des Logit-Modells und D-Effizienz (die Normierung auf 0 bis 1 über eine obere "
                       "Schranke ist eine Herleitung von Askurious)", "Experimental design: information of the logit model and "
                       "D-efficiency (the scaling to 0 to 1 via an upper bound is a derivation of Askurious)"),
                 [HUBER_ZWERINA])]

    def sample_size(self, item):
        from app.conjoint_design import sample_size

        c = item.config
        return sample_size(c.get("tasks", 0), c.get("alternatives", 0), max(self.level_counts(item) or [0]))

    def tasks_needed(self, item, n):
        """The other way round: (alternatives, minimum tasks, recommended tasks) for a sample of n,
        one row per number of alternatives the editor allows."""
        from app.conjoint_design import tasks_needed

        levels = max(self.level_counts(item) or [0])
        return [(j, *tasks_needed(n, j, levels)) for j in range(2, 7)]

    def expand_pages(self, survey, item, rng):
        """One page per task (and the explanation page first); without a current design the item
        stays a normal page, which shows "Design noch nicht erzeugt"."""
        if not self.design_current(item):
            return None
        pages = [{"explain": True}] if item.config.get("explain") else []
        active = dict(holdouts.cbc_active(item))
        if not active:
            pages += [{"task": t} for t in range(item.config["tasks"])]
        else:  # holdout tasks between and after the tasks; "n": the number the page shows ("Aufgabe n von ...")
            order = [(k, x) for k, x in holdouts.page_order(item.config["tasks"], len(holdouts.stored(item)))
                     if k == "t" or x in active]
            pages += [{"task" if k == "t" else "holdout": x, "n": n} for n, (k, x) in enumerate(order, start=1)]
        return self.response_state(survey, item, rng), pages

    def holdout_profiles(self, item, h):
        return holdouts.cbc_profiles(item, h)

    def holdout_count(self, item):
        return len(holdouts.stored(item))

    def holdout_level(self, item, h, j, attr_index):
        return holdouts.cbc_level_choice(item, h, j, attr_index)

    def shown_tasks(self, item):
        """Tasks a person sees, holdouts included (the "of" in "Aufgabe 3 von 10")."""
        return item.config.get("tasks", 0) + len(holdouts.cbc_active(item))

    def response_state(self, survey, item, rng):
        """Design version (balanced over real participants) and attribute order for one response."""
        from app import balance

        n_versions = len(item.config["design"]["versions"])
        used = balance.counts(survey)["version"].get(str(item.id), {})
        counts = [used.get(v, 0) for v in range(n_versions)]
        least = min(counts)
        version = rng.choice([v for v, c in enumerate(counts) if c == least])
        order = [a["id"] for a in item.config.get("attributes", [])]
        if item.config.get("attr_order") == "participant":
            rng.shuffle(order)
        return {"version": version, "order": order}

    def timing_pages(self, item):
        pages = [("_x", "Erklärungsseite", ".x")] if item.config.get("explain") else []
        active = {h for h, _ in holdouts.cbc_active(item)}
        return pages + [(f"_t{x + 1}", f"Aufgabe {x + 1}", f".t{x}") if k == "t" else
                        (f"_ho{x + 1}", f"Holdout-Aufgabe {x + 1}", f".h{x}")
                        for k, x in holdouts.page_order(item.config.get("tasks", 0), len(holdouts.stored(item)))
                        if k == "t" or x in active]

    def seconds(self, item, read):
        from app.duration import ANSWER_SECONDS

        c = item.config
        per_task = (ANSWER_SECONDS["conjoint_task"] + 0.5 * len(c.get("attributes", [])) * max(c.get("alternatives", 2), 1)
                    + (3 if self.dual(item) else 0))  # the follow-up question of dual response
        return read + per_task * (c.get("tasks", 8) + len(holdouts.cbc_active(item)))

    def dummy_fields(self, item, rng, meta, answers=None):
        """Random utility model, as for MaxDiff dummies (audit 2026-10-05, M-14): utilities per level the same for
        all dummies of this item, a deviation per dummy person, Gumbel noise per choice; the highest utility is
        chosen ("none" has a constant of its own). A price marked as numbers enters linearly and negatively."""
        c = item.config
        meta = meta or {}
        choices = list(range(1, c["alternatives"] + 1)) + ([0] if c.get("none") else [])
        key = f"h{meta['holdout']}" if "holdout" in meta else f"t{meta.get('task', 0)}"
        if "holdout" in meta:
            profiles = holdouts.cbc_profiles(item, meta["holdout"])
        else:
            version = ((meta.get("cj_state") or {}).get(item.id) or {}).get("version")
            profiles = (item.config["design"]["versions"][version][meta.get("task", 0)]
                        if self.design_current(item) and self.version_known(item, version) else None)
        dual = self.dual(item)
        if not profiles:  # no design known for this page: a random choice
            out = {f"i{item.id}_{key}": str(rng.choice(choices))}
            return {**out, f"i{item.id}_d{key}": str(rng.choice((0, 1)))} if dual else out
        person = dummy_person(self, item, rng, meta.get("respondent"))
        utility = [sum(person["pw"][a][lv] for a, lv in enumerate(p) if lv is not None) for p in profiles]
        if c.get("none"):
            utility.append(person["none"])
        noisy = [u + gumbel(rng) for u in utility]
        best = max(range(len(noisy)), key=noisy.__getitem__)
        out = {f"i{item.id}_{key}": str(0 if best == c["alternatives"] else best + 1)}
        if dual:
            # the chosen option against "none" with the same noise as in the first choice: the person would buy if the
            # option that won is better than "none" (the model cbc_analysis.choice_sets estimates, audit 2026-10-06, W-1)
            out[f"i{item.id}_d{key}"] = str(int(noisy[best] > person["none"] + gumbel(rng)))
        return out

    def version_known(self, item, version):
        """True if the current design has this version (answers from an older design may not)."""
        return isinstance(version, int) and 0 <= version < len((item.config.get("design") or {}).get("versions") or [])

    def profile(self, item, version, task, alt):
        """Level indices (one per attribute, in config order) for one alternative."""
        return item.config["design"]["versions"][version][task][alt]

    # --- images --------------------------------------------------------------------------

    def required_keys(self, item, variable_rows=None):
        if not self.uses_images(item) or not self.design_current(item) or not self.visual_attributes(item):
            return []
        keys = set()
        for version in item.config["design"]["versions"]:
            for task in version:
                for profile in task:
                    keys.add(self.image_key(item, profile))
        for _, profiles in holdouts.cbc_active(item):
            keys.update(self.image_key(item, profile) for profile in profiles)
        return sorted(keys)

    # --- checks ----------------------------------------------------------------------------

    def validate(self, item):
        problems = self.validate_attributes(item)
        if item.config.get("attributes") and not problems and not self.design_current(item):
            problems.append(de_en("Versuchsplan fehlt oder ist veraltet: bitte „Versuchsplan erzeugen“",
                                  "Experimental design missing or out of date: please “Generate experimental design”"))
        return (problems + self.validate_price_attribute(item) + self.validate_images(item)
                + holdouts.cbc_problems(item))

    def check_warnings(self, item):
        """Hints that do not block the start: holdouts, a design whose part-worths cannot all be estimated
        (D-efficiency 0, audit 2026-10-05 W-5) and few tasks per person (H-11)."""
        return self.holdout_warnings(item) + self.design_warnings(item)

    def holdout_warnings(self, item):
        """Only the hints on the holdout tasks: the editor shows them in the holdout box (audit 2026-10-07 W26)."""
        return list(holdouts.cbc_warnings(item))

    def design_warnings(self, item):
        """The hints on the design itself, shown once in the box "Design" above "Design neu erzeugen" (W26)."""
        out = []
        if self.design_current(item) and self.too_few_tasks(item):
            out.append(self.too_few_tasks(item))
        tasks = int(item.config.get("tasks") or 0)
        if 0 < tasks < MIN_TASKS:
            out.append(de_en("Nur {n} Auswahlaufgaben je Person: Für individuelle Nutzen (HB) sind mindestens {min} "
                             "üblich; mit weniger Aufgaben bleiben die Werte je Person nah am Mittel aller Personen und "
                             "ihre Intervalle decken die wahren Werte seltener ab.",
                             "Only {n} choice tasks per person: for individual utilities (HB) at least {min} are usual; "
                             "with fewer tasks the values per person stay close to the mean of all people and their "
                             "intervals cover the true values less often.", n=tasks, min=MIN_TASKS))
        return out

    # --- answers -------------------------------------------------------------------------

    supports_auto_next = True  # choosing an alternative completes the task
    auto_next_inline = True  # in the fieldset "Darstellung" of the editor (W34)

    def alt_order(self, item, seed, version, task):
        """Display order of the options in one task: with "shuffle_alts" a random order per participant
        (seed) and task, the same on reload; the choice still records the option's own number."""
        n = int(item.config.get("alternatives", 2))
        order = list(range(n))
        if item.config.get("shuffle_alts") and seed is not None:
            import hashlib
            import random

            digest = hashlib.sha256(f"{seed}:{item.id}:{version}:{task}".encode()).hexdigest()
            random.Random(int(digest[:16], 16)).shuffle(order)
        return order

    def parse_answer(self, item, form, lang="de", meta=None):
        if (meta or {}).get("explain"):
            return {"explained": True}  # the explanation page has no answer
        key = f"h{meta['holdout']}" if "holdout" in (meta or {}) else f"t{(meta or {}).get('task', 0)}"
        raw = form.get(f"i{item.id}_{key}", "")
        allowed = {str(n) for n in range(1, item.config["alternatives"] + 1)} | ({"0"} if item.config.get("none") else set())
        dual = self.dual(item)
        if raw == "":
            if item.required:
                raise AnswerError(tr(lang, "Bitte wählen Sie eine Option.", "Please choose an option."))
            return {key: None, f"d{key}": None} if dual else {key: None}
        if raw not in allowed:
            raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
        if not dual:
            return {key: int(raw)}
        again = form.get(f"i{item.id}_d{key}", "")
        if again not in ("0", "1"):
            if again or item.required:
                raise AnswerError(tr(lang, "Bitte beantworten Sie auch die Frage unter den Optionen.",
                                     "Please also answer the question below the options."))
            return {key: int(raw), f"d{key}": None}
        return {key: int(raw), f"d{key}": int(again)}

    def display_value(self, item, form, meta=None):
        key = f"h{meta['holdout']}" if "holdout" in (meta or {}) else f"t{(meta or {}).get('task', 0)}"
        raw = form.get(f"i{item.id}_{key}", "")
        if self.dual(item):
            return {key: whole(raw), f"d{key}": whole(form.get(f"i{item.id}_d{key}", ""))}
        return {key: whole(raw)}

    def merge_answer(self, old, new):
        return {**(old or {}), **(new or {})}

    def is_missing(self, item, answer):
        if answer == {"explained": True}:
            return False  # the explanation page itself asks nothing
        choices = {k: v for k, v in (answer or {}).items() if k.startswith("t")}
        return not choices or all(v is None for v in choices.values())

    # --- export ----------------------------------------------------------------------------

    def random_attr_order(self, item):
        return item.config.get("attr_order") == "participant"

    def export_columns(self, item):
        cols = [(f"{item.code}_version", "Version des Versuchsplans")]
        if self.random_attr_order(item):  # per-person order of the attributes (rows of the table)
            cols.append((f"{item.code}_attr_order", "Gezeigte Reihenfolge der Merkmale (Variablennamen)"))
        dual = self.dual(item)
        for t in range(item.config.get("tasks", 0)):
            cols.append((f"{item.code}_t{t + 1}", f"Aufgabe {t + 1}: gewählte Option (0 = keine)"))
            if dual:
                cols.append((f"{item.code}_t{t + 1}_dr", f"Aufgabe {t + 1}: gewählte Option wirklich gewählt (Nachfrage)"))
        for h in range(len(holdouts.stored(item))):
            cols.append((f"{item.code}_ho{h + 1}", f"Holdout-Aufgabe {h + 1}: gewählte Option (0 = keine)"))
            if dual:
                cols.append((f"{item.code}_ho{h + 1}_dr",
                             f"Holdout-Aufgabe {h + 1}: gewählte Option wirklich gewählt (Nachfrage)"))
        return cols

    def export_values(self, item, answer):
        answer = answer if isinstance(answer, dict) else {}  # answers from older versions of the item: empty
        out = {f"{item.code}_version": None}
        if self.random_attr_order(item):
            out[f"{item.code}_attr_order"] = None
        dual = self.dual(item)
        for t in range(item.config.get("tasks", 0)):
            out[f"{item.code}_t{t + 1}"] = answer.get(f"t{t}")
            if dual:
                out[f"{item.code}_t{t + 1}_dr"] = answer.get(f"dt{t}")
        for h in range(len(holdouts.stored(item))):
            out[f"{item.code}_ho{h + 1}"] = answer.get(f"h{h}")
            if dual:
                out[f"{item.code}_ho{h + 1}_dr"] = answer.get(f"dh{h}")
        return out

    def result_column(self, item, column):
        return None, "", None  # summarised as a whole: results()

    def results(self, item, table, lang):
        from app import cbc_analysis

        return cbc_analysis.results(item, table, lang)

    def export_state_values(self, item, state):
        state = state or {}
        out = {f"{item.code}_version": state["version"] + 1 if "version" in state else None}
        if self.random_attr_order(item):
            codes = {a["id"]: a["code"] for a in item.config.get("attributes", [])}
            order = state.get("order") or []
            out[f"{item.code}_attr_order"] = "|".join(codes.get(aid, "?") for aid in order) or None
        return out

    def codebook_info(self, item, column, lang, ui=None):
        code, c = item.code, item.config
        if column == f"{code}_version":
            return "Version des Versuchsplans", in_lang(ui, "1 bis {n}", "1 to {n}", n=c.get('versions', 1)), [
                in_lang(ui, "balanciert über echte Teilnahmen", "balanced over real participations")]
        if column == f"{code}_attr_order":
            return ("Reihenfolge", " | ".join(a["code"] for a in c.get("attributes", [])),
                    [in_lang(ui, "Merkmale in der Reihenfolge, in der die Person sie sah (von oben nach unten), getrennt "
                                 "durch |; je Person zufällig, in allen Aufgaben gleich",
                             "attributes in the order the person saw them (top to bottom), separated by |; random per "
                             "person, the same in all tasks")])
        if (column.startswith(f"{code}_t") or column.startswith(f"{code}_ho")) and column.endswith("_dr"):
            t, d = item.t(lang), pick(DEFAULT_TEXTS, lang)
            return "Conjoint", f"1 = {t.get('dr_yes') or d['dr_yes']}; 0 = {t.get('dr_no') or d['dr_no']}", [
                in_lang(ui, "Nachfrage nach der Wahl (Dual Response): „{q}“; leer: Aufgabe oder Nachfrage nicht beantwortet",
                        "follow-up question after the choice (dual response): “{q}”; empty: task or follow-up question "
                        "not answered", q=t.get("dr_question") or d["dr_question"])]
        if column.startswith(f"{code}_t") or column.startswith(f"{code}_ho"):
            t, d = item.t(lang), pick(DEFAULT_TEXTS, lang)
            shuffled = bool(c.get("shuffle_alts"))
            # with shuffled options the number is the alternative of the design, not the label on the screen
            name = "Alternative" if shuffled else (t.get("alt_label") or d["alt_label"])
            labels = [(n, f"{name} {n}") for n in range(1, int(c.get("alternatives", 2)) + 1)]
            if c.get("none"):
                labels.insert(0, (0, t.get("none_label") or d["none_label"]))
            notes = [in_lang(ui, "leer: Aufgabe nicht beantwortet", "empty: task not answered")]
            if column.startswith(f"{code}_ho"):
                notes += self.holdout_notes(item, int(column[len(code) + 3:]) - 1, lang, ui)
            if shuffled:
                notes.append(in_lang(ui, "Nummer der Alternative im Versuchsplan; die Optionen standen je Person und Aufgabe in "
                                         "zufälliger Reihenfolge, die gezeigte Position steht im Langformat (Spalte position)",
                                     "number of the alternative in the experimental design; the options appeared in random order per "
                                     "person and task, the position shown is in the long format (column position)"))
            return "Conjoint", "; ".join(f"{n} = {str(label).replace(';', ',')}" for n, label in labels), notes
        return None

    def holdout_notes(self, item, h, lang, ui=None):
        """Codebook notes of a holdout column: what it is and the options it showed (`ui`: the reader's language)."""
        notes = [in_lang(ui, "Holdout-Aufgabe: für alle gleich, geht nicht in Zählwerte, Logit-Modell und HB ein (Prüfung "
                             "der Vorhersage)",
                         "holdout task: the same for everybody, not part of the counts, the logit model and HB (check "
                         "of the prediction)")]
        profiles = holdouts.cbc_profiles(item, h)
        if profiles:
            attrs = self.attributes(item, lang)
            notes += [f"Option {j}: " + ", ".join(f"{a['code']} = {a['level_labels'][p[k]]}" for k, a in enumerate(attrs))
                      for j, p in enumerate(profiles, start=1)]
        return notes

    def long_columns(self, item):
        attrs = item.config.get("attributes", [])
        codes = [a["code"] for a in attrs]
        return (["version", "task", "alt", "position"] + codes + [c + "_label" for c in codes]
                + [a["code"] + "_value" for a in attrs if self.level_values(a)] + ["none", "chosen"]
                + (["buy"] if self.dual(item) else []))

    def long_rows(self, item, response):
        """One row per task and alternative (+ "none"): position on the screen (1-based, as in the
        participant template: alt_order with models.display_seed as seed; "none" stands below the options
        and has no position), level numbers (1-based), labels, chosen."""
        from app.models import display_seed

        state = ((response.layout or {}).get("cj") or {}).get(str(item.id))
        if not state or not self.design_current(item) or not self.version_known(item, state.get("version")):
            return  # no design, or a version the current design does not have: skipped
        attrs = self.attributes(item, item.survey.default_language)
        valued = [(k, self.level_values(a)) for k, a in enumerate(attrs) if self.level_values(a)]
        answer = (response.answers or {}).get(str(item.id)) or {}
        v = state["version"]
        dual = self.dual(item)
        seed = display_seed(response)
        for t in range(item.config["tasks"]):
            choice = answer.get(f"t{t}")
            again = answer.get(f"dt{t}") if dual else None
            buy = [] if not dual else ["" if again is None else again]  # the task's answer to the follow-up question
            position = {a: n for n, a in enumerate(self.alt_order(item, seed, v, t), start=1)}
            for a in range(item.config["alternatives"]):
                profile = self.profile(item, v, t, a)
                if profile is None:  # not offered in this task (imported adaptive designs)
                    continue
                # a level None: attribute not shown (imported partial profiles), level 0 and no label as in .cho
                yield ([v + 1, t + 1, a + 1, position[a]] + [0 if p is None else p + 1 for p in profile]
                       + ["" if p is None else attrs[k]["level_labels"][p] for k, p in enumerate(profile)]
                       + [_value(values, profile[k]) for k, values in valued]
                       + [0, "" if choice is None else int(choice == a + 1)] + buy)
            if item.config.get("none"):
                yield ([v + 1, t + 1, 0, ""] + [""] * (2 * len(attrs) + len(valued))
                       + [1, "" if choice is None else int(choice == 0)])

    def questionnaire_lines(self, item, lang):
        out = [f"  - Merkmale `{a['code']}` {a['label']}: " + "; ".join(a["level_labels"])
               for a in self.attributes(item, lang)]
        c = item.config
        out.append(f"  - {c['tasks']} tasks × {c['alternatives']} alternatives, {c['versions']} {'version' if c['versions'] == 1 else 'versions'}"
                   + (", none option" if c.get("none") else "")
                   + (", dual response (follow-up question after every choice: would you really choose it?)"
                      if self.dual(item) else "") + f", display: {c['display']}")
        if holdouts.stored(item):
            out.append(f"  - {len(holdouts.stored(item))} holdout task(s), the same for everyone, not used for estimation")
        return out
