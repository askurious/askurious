"""Ratings-based conjoint (CVA, "Bewertungs-Conjoint"; concept aca-cva-konzept.md, Ralf 2026-10-17: "wie empfohlen").

Attributes and levels as for CBC (app/items/attributes.py). Every person rates a whole plan of one version (C4):
either one profile per page on a rating scale ("single", C2/C3: 5, 7, 9 or 11 points, 11 = 0 to 10, or a slider
0 to 100) or two profiles side by side on a graded scale from "the first much better" over "same" to "the second
much better" ("pairs", 7 or 9 points), or all offers of the plan on one page put in an order ("ranking", stage 4:
the classic card sort; analysed as ratings with the reversed rank, Green & Srinivasan, 1990). The plan is D-optimal for the linear model (app/cva_design.py), generated in
draft, frozen at the start; with several versions they are balanced over real participants like CBC. Display per
option as in CBC: table, table with images per level, or one image per option.

Results (app/cva_analysis.py, C5): an OLS regression per person (part-worths, importances, R²), the means over
persons with t intervals; the market simulator on the part-worths per person (app/simulator.py, basis "persons"), a
dashboard tile (app/boards/tiles.py _cva), the quality criterion qc_cva (app/quality.py) and the paper version
(app/paper_pdf.py _cva_task) (C6, C8).

config: attributes, prohibitions, explain, hide_attr_labels, level_images (attributes.py), form ("single" | "pairs" |
        "ranking"),
        points (single: 5, 7, 9, 11; pairs: 7, 9), slider (single: 0 to 100 instead of points), tasks (0 = suggestion),
        versions, display, scene, show_visual_in_table, stack_mobile, auto_next,
        design {sig, versions: [[profile, ...]] or [[[first, second], ...]], eff},
        holdouts [{id, alts: [[level id per attribute]] or two of them in pairs}] (app/holdouts.py, stage 4)
texts:  question, low, high (ends of the rating scale), much, same (pairs), alt_label, explain_intro, attributes, levels
answer: {"v": version, "t0": value, ..., "h0": value, ...}: single the rating (0..10 with 11 points, else 1..points;
        0..100 with the slider), pairs the category from the point of view of the profile shown first (-m .. m,
        m = (points - 1) / 2); "h<k>" the same for holdout k, which never enters the regression; ranking: "t<k>" the
        rank of offer k (1 = liked most), all on one page, no holdouts
"""
import hashlib
import json
import secrets

from app import holdouts
from app.items import register
from app.items.attributes import ATTRIBUTE_RULES, MAX_ATTRIBUTES, AttributeLevels, display_from_form
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_any, r_flag, r_id, r_int, r_list, r_one, r_rows, r_text  # rules for survey files
from app.items.base import AnswerError, ItemType, in_lang, plain, whole
from app.lang import Both, de_en
from app.ui_i18n import pick, tr

FORMS = ("single", "pairs", "ranking")
RANK_HINT = 20  # more offers than this to put in order: a hint (Festlegung von Askurious: ordering more is tedious)
SINGLE_POINTS = (5, 7, 9, 11)
PAIR_POINTS = (7, 9)
DISPLAYS = {"table": "Tabelle mit Text", "image": "Ein Bild je Option", "both": "Ein Bild je Option, Tabelle darunter"}
DEFAULT_TEXTS = {
    "de": {"question": "Wie wahrscheinlich würden Sie dieses Angebot wählen?",
           "pair_question": "Welches der beiden Angebote sagt Ihnen mehr zu?",
           "rank_question": "Bringen Sie die Angebote in eine Reihenfolge: Oben das, das Ihnen am meisten zusagt.",
           "low": "sehr unwahrscheinlich", "high": "sehr wahrscheinlich", "much": "viel lieber", "same": "gleich",
           "alt_label": "Angebot"},
    "en": {"question": "How likely would you be to choose this offer?",
           "pair_question": "Which of the two offers do you like more?",
           "rank_question": "Put the offers in order: at the top the one you like most.",
           "low": "very unlikely", "high": "very likely", "much": "much preferred", "same": "same",
           "alt_label": "Offer"},
}
DUMMY_NOISE = 0.15  # sd of the noise on the rating scale, as a share of its range (dummy data)


@register
class Cva(AttributeLevels, ItemType):
    key = "cva"
    label = Both("Bewertungs-Conjoint (CVA)", "Ratings-based conjoint (CVA)")
    short = Both("Angebote mit Merkmalen bewerten", "Rating offers with attributes")
    description = Both("Angebote aus Merkmalen und Stufen einzeln auf einer Skala bewerten oder paarweise abgestuft vergleichen",
                       "Rate offers made of attributes and levels one by one on a scale or compare them in pairs on a graded scale")
    order = 46
    fact_sheet = FactSheet(
        "choice",
        keywords=["CVA", "Conjoint Value Analysis", "Ratings-based conjoint", "Rating-based conjoint", "Traditionelle Conjoint",
                  "Full profile", "Vollprofil", "Teilnutzen", "Part-worths"],
        can=[("Merkmale mit Stufen; jedes Angebot einzeln auf einer Skala bewerten (5 bis 11 Stufen oder 0 bis 100), zwei "
              "Angebote auf einer Skala mit 7 oder 9 Stufen vergleichen oder alle Angebote in eine Reihenfolge bringen.",
              "Attributes with levels; rate each offer on a scale (5 to 11 points or 0 to 100), compare two offers on a "
              "scale with 7 or 9 points or put all offers in order."),
             ("Versuchsplan für das lineare Modell, den jede Person ganz sieht; Anzeige als Tabelle, mit Bildern je Stufe oder "
              "einem Bild je Angebot; bis zu drei feste Holdout-Aufgaben prüfen die Vorhersage.",
              "Experimental design for the linear model that every person sees in full; shown as a table, with images per "
              "level or one image per offer; up to three fixed holdout tasks check the prediction."),
             ("Teilnutzen, Wichtigkeiten und R² je Person (Regression), Mittel über alle mit Intervallen, Segmente "
              "(Latent-Class-Regression), Marktsimulator, Dashboard-Kachel, Papierfassung.",
              "Part-worths, importances and R² per person (regression), means over everyone with intervals, segments "
              "(latent class regression), market simulator, dashboard tile, paper version.")],
        good_for=[("Wenige Merkmale (bis etwa 6) und kleine Stichproben, wenn Werte je Person ohne HB gebraucht werden",
                   "Few attributes (up to about 6) and small samples when values per person are needed without HB"),
                  ("Lehre und Wiederholung klassischer Conjoint-Untersuchungen",
                   "Teaching and repeating classic conjoint research")],
        not_for=[("Marktanteile und Preise (dann CBC: Bewerten ist keine Wahl)",
                  "Market shares and prices (then CBC: rating is not choosing)"),
                 ("Viele Merkmale (dann ACBC)", "Many attributes (then ACBC)")],
        designs=["choice"],
        examples=[("Wohnung mit 4 Merkmalen (Miete, Lage, Größe, Balkon), 14 Angebote je Person auf 0 bis 10",
                   "A flat with 4 attributes (rent, location, size, balcony), 14 offers per person on 0 to 10")],
    )
    import_rules = {**ATTRIBUTE_RULES, "form": r_one(*FORMS), "points": r_one(*SINGLE_POINTS), "slider": r_flag,
                    "tasks": r_int(0, 40), "versions": r_int(1, 4), "display": r_one(*DISPLAYS), "scene": r_text(2000),
                    "show_visual_in_table": r_flag, "stack_mobile": r_flag, "design": r_any,
                    "holdouts": r_rows(holdouts.MAX, id=r_id, alts=r_list(2, r_list(MAX_ATTRIBUTES, r_id)))}
    result_kind = "cva"  # own summary (results())
    levels = ("metric",)
    alone_on_page = True  # one page per task
    allowed_in_loop = allowed_in_separated = False  # design version per response (layout["cj"])
    supports_auto_next = True
    auto_next_inline = True
    auto_next_hint = Both("sobald eine Stufe der Skala gewählt ist", "as soon as a point of the scale is chosen")
    editor_tabs = Both([("content", "1 · Inhalt", "Merkmale, Stufen, Antwortform"),
                        ("design", "2 · Ablauf", "Aufgaben, verbotene Kombinationen, Versuchsplan"),
                        ("display", "3 · Darstellung & Bilder", "")],
                       [("content", "1 · Content", "Attributes, levels, answer form"),
                        ("design", "2 · Flow", "Tasks, prohibitions, experimental design"),
                        ("display", "3 · Display & images", "")])
    panel_template = "items/cva/panel.html"
    design_lock = True
    long_export = "cva"
    long_export_label = Both("Person × Aufgabe × Angebot", "Person × task × offer")
    long_export_note = ("- `cva_*_long.csv`: long format of the ratings-based conjoint (participant × task × offer); "
                        "`side` 1/2 for the two offers of a pair; `answer` the rating, for pairs the category from the "
                        "point of view of the first offer (positive = first preferred).")

    def import_config(self, config):
        """A design of the file is kept only if this item could have generated it."""
        config, unfit, unknown = super().import_config(config)
        design = config.get("design")
        if design is not None and not unfit:
            counts = [len(a["levels"]) for a in config["attributes"]]

            def profile_ok(p):
                return isinstance(p, list) and len(p) == len(counts) and all(
                    type(x) is int and 0 <= x < n for x, n in zip(p, counts))

            pairs = config.get("form") == "pairs"
            versions = design.get("versions") if isinstance(design, dict) else None
            fits = isinstance(versions, list) and len(versions) == config.get("versions", 1) and all(
                isinstance(v, list) and v and all(
                    (isinstance(t, list) and len(t) == 2 and all(profile_ok(p) for p in t)) if pairs else profile_ok(t)
                    for t in v) for v in versions)
            if fits and isinstance(design.get("sig"), str) and len(design["sig"]) <= 40:
                config["design"] = {"sig": design["sig"], "versions": versions, "eff": design.get("eff")
                                    if type(design.get("eff")) in (int, float) else None}
            else:
                config["design"] = None
                unknown = unknown + ["design"]
        if not unfit and config.get("holdouts") and holdouts.import_fit(config, "alts") is None:
            config["holdouts"] = []  # levels the file does not have
            unknown = unknown + ["holdouts"]
        return config, unfit, unknown

    # --- defaults and editing ----------------------------------------------------------

    def default_config(self):
        return {"attributes": [], "form": "single", "points": 11, "slider": False, "tasks": 0, "versions": 1,
                "display": "table", "scene": "", "show_visual_in_table": False, "stack_mobile": True,
                "auto_next": False,  # a slip on a scale of eleven would move on at once (as for the paired comparison)
                "design": None, "holdouts": []}

    def default_texts(self, lang):
        d = pick(DEFAULT_TEXTS, lang)
        return {k: v for k, v in d.items() if k not in ("pair_question", "rank_question")} | {"attributes": {}, "levels": {}}

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config)
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        for key in ("question", "low", "high", "much", "same", "alt_label"):
            if key in form:
                lt[key] = form.get(key, "").strip()[:500]
        self.update_attributes(item, form, lang, structural, config, lt)
        if structural:
            if form.get("form") in FORMS and form["form"] != config.get("form"):
                config["form"] = form["form"]
                d = pick(DEFAULT_TEXTS, lang)
                if lt.get("question", "") in ("", d["question"], d["pair_question"], d["rank_question"]):
                    # the standard wording follows the form
                    lt["question"] = {"pairs": d["pair_question"], "ranking": d["rank_question"]}.get(form["form"],
                                                                                                    d["question"])
                config["points"] = 9 if form["form"] == "pairs" else 11
            points = whole(form.get("points", ""))
            allowed = PAIR_POINTS if config.get("form") == "pairs" else SINGLE_POINTS
            if points in allowed:
                config["points"] = points
            if "points" in form or "slider" in form:
                config["slider"] = config.get("form") == "single" and bool(form.get("slider"))
            if "tasks" in form:
                v = whole(form.get("tasks", ""))
                config["tasks"] = 0 if v is None else max(0, min(40, v))
            if "versions" in form:
                v = whole(form.get("versions", ""))
                config["versions"] = 1 if v is None else max(1, min(4, v))
            if "ho_count" in form:  # holdout tasks (app/holdouts.py), locked with the design
                config["holdouts"] = holdouts.cbc_from_form(item, config, form)
            if "display" in form:
                display, level_images = display_from_form(form, DISPLAYS)
                config.update(display=display, level_images=level_images,
                              scene=form.get("scene", item.config.get("scene", "")).strip()[:2000],
                              show_visual_in_table=bool(form.get("show_visual_in_table")),
                              stack_mobile=bool(form.get("stack_mobile")) if display in ("image", "both")
                              else item.config.get("stack_mobile", True))
        texts[lang] = lt
        item.config, item.texts = config, texts

    # --- structure ---------------------------------------------------------------------

    def form(self, item):
        f = (item.config or {}).get("form")
        return f if f in FORMS else "single"

    def design_form(self, item):
        """The form of the experimental design: pairs, or single profiles (also for the ranking)."""
        return "pairs" if self.form(item) == "pairs" else "single"

    def n_params(self, item):
        from app import cva_design

        return cva_design.n_params(self.level_counts(item))

    def available(self, item):
        """How many different allowed profiles there are (a single-profile plan cannot repeat one)."""
        from app.conjoint_design import n_allowed_profiles

        counts = self.level_counts(item)
        if not counts or any(n < 2 for n in counts):
            return 0
        total = 1
        for n in counts:
            total *= n
        return n_allowed_profiles(counts, self.prohibitions(item)) if total <= 200000 else total

    def n_tasks(self, item):
        """Tasks per person: as set, or the suggestion (twice the parameters, cva_design.suggested)."""
        from app import cva_design

        counts = self.level_counts(item)
        if not counts:
            return 0
        available = self.available(item) if self.design_form(item) == "single" else None
        return item.config.get("tasks") or cva_design.suggested(counts, available)

    def scale_points(self, item):
        """The values of the scale from left to right (single: the ratings, pairs: first much better ... second; ranking:
        the reversed ranks the regression uses, 1 = put last ... n = put first)."""
        c = item.config
        if self.form(item) == "ranking":
            return list(range(1, max(self.n_tasks(item), 2) + 1))
        if self.form(item) == "pairs":
            m = (c.get("points", 9) - 1) // 2
            return list(range(m, -m - 1, -1))
        if c.get("slider"):
            return [0, 100]
        points = c.get("points", 11)
        return list(range(0, 11)) if points == 11 else list(range(1, points + 1))

    def scale_range(self, item):
        values = self.scale_points(item)
        return min(values), max(values)

    def signature(self, item):
        c = item.config
        # the design form: changing between single ratings and the ranking keeps the design
        shape = [self.level_counts(item), self.design_form(item), self.n_tasks(item), c.get("versions", 1)]
        if self.prohibitions(item):
            shape.append(self.prohibitions(item))
        return hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]

    def preview_empty(self, item):
        if self.design_current(item):
            return None
        if not item.config.get("attributes"):
            return de_en("Die Vorschau erscheint, sobald Merkmale und Stufen eingetragen und der Versuchsplan erzeugt ist.",
                         "The preview appears as soon as attributes and levels are entered and the experimental design "
                         "is generated."), "content"
        return de_en("Die Vorschau erscheint, sobald der Versuchsplan erzeugt ist.",
                     "The preview appears as soon as the experimental design is generated."), "design"

    def design_current(self, item):
        d = item.config.get("design")
        return bool(d) and d.get("sig") == self.signature(item)

    def make_design(self, item):
        from flask import current_app

        from app import cva_design
        from app.conjoint_design import Budget

        c = item.config
        counts = self.level_counts(item)
        n = self.n_tasks(item)
        if n < cva_design.minimum(counts):
            raise ValueError(de_en("Mindestens {n} Aufgaben nötig (Teilnutzen + Konstante + 1).",
                                   "At least {n} tasks are needed (part-worths + constant + 1).", n=cva_design.minimum(counts)))
        budget = Budget(current_app.config.get("DESIGN_SECONDS", 20))
        versions = cva_design.generate(counts, n, self.design_form(item), c.get("versions", 1), forbidden=self.prohibitions(item),
                                       seed=secrets.randbits(32), budget=budget)
        eff = cva_design.efficiency(versions, counts, self.design_form(item))
        item.config = {**c, "design": {"sig": self.signature(item), "versions": versions,
                                       "eff": round(eff, 1) if eff is not None else None}}
        return budget

    def design_efficiency(self, item):
        d = item.config.get("design") or {}
        return d.get("eff") if self.design_current(item) else None

    def design_frequency(self, item, lang=None):
        from app import cva_design

        if not self.design_current(item):
            return None
        attrs = self.attributes(item, lang)
        freq = cva_design.level_frequency(item.config["design"]["versions"], self.level_counts(item), self.design_form(item))
        return [{"label": a["label"], "min": lo, "max": hi, "ratio": hi / lo if lo else float("inf")}
                for a, (lo, hi) in zip(attrs, freq)]

    def design_summary(self, item):
        eff = self.design_efficiency(item)
        return de_en("Versuchsplan erzeugt: {n} Aufgaben je Person, D-Effizienz {eff}.",
                     "Experimental design generated: {n} tasks per person, D-efficiency {eff}.",
                     n=self.n_tasks(item), eff="–" if eff is None else round(eff))

    def design_warnings(self, item):
        from app import cva_design

        counts = self.level_counts(item)
        out = []
        if not counts:
            return out
        p, n = cva_design.n_params(counts), self.n_tasks(item)
        if n < cva_design.WARN_FACTOR * p:
            out.append(de_en("{n} Aufgaben für {p} Parameter: Empfohlen ist mindestens das 1,5-Fache ({min}), besser das "
                             "Dreifachen; mit weniger Aufgaben sind die Teilnutzen je Person unsicher.",
                             "{n} tasks for {p} parameters: at least 1.5 times ({min}) is advised, better three times; "
                             "with fewer tasks the part-worths per person are uncertain.",
                             n=n, p=p, min=-(-3 * p // 2)))
        if len(counts) > cva_design.MAX_ATTRIBUTES_HINT and self.design_form(item) == "single":
            out.append(de_en("{k} Merkmale in einem ganzen Angebot sind schwer zu überblicken; als Grenze "
                             "gelten etwa 6. Bei mehr Merkmalen eher ACBC.",
                             "{k} attributes in a whole offer are hard to take in; about 6 count as "
                             "the limit. With "
                             "more attributes rather ACBC.", k=len(counts)))
        if self.form(item) == "ranking" and n > RANK_HINT:
            out.append(de_en("{n} Angebote in eine Reihenfolge zu bringen ist mühsam, auf dem Handy erst recht; über {k} besser "
                             "einzeln bewerten lassen.",
                             "Putting {n} offers in order is tedious, on a phone even more so; above {k} better have them "
                             "rated one by one.", n=n, k=RANK_HINT))
        elif n > 30:
            out.append(de_en("{n} Aufgaben je Person sind viel; über 30 ermüdet.", "{n} tasks per person are a lot; over 30 is "
                             "tiring.", n=n))
        return out

    def check_warnings(self, item):
        return self.holdout_warnings(item) + self.design_warnings(item)

    def source_groups(self, item):
        from app import cva_design

        return [(de_en("Bewertungs-Conjoint (CVA): Regression je Person",
                       "Ratings-based conjoint (CVA): regression per person"),
                 [cva_design.GREEN_SRINIVASAN]),
                (de_en("Versuchsplan für das lineare Modell (D-optimal, Koordinatentausch)",
                       "Experimental design for the linear model (D-optimal, coordinate exchange)"),
                 [_doe().KUHFELD, _doe().MEYER_NACHTSHEIM])]

    def references(self, item):
        return [s for _, group in self.source_groups(item) for s in group]

    def expand_pages(self, survey, item, rng):
        """One page per task (the explanation page first, optional); without a current design the item stays a normal
        page, which shows "Versuchsplan noch nicht erzeugt"."""
        if not self.design_current(item):
            return None
        state = {"version": self.balanced_version(survey, item, rng)}
        pages = [{"explain": True}] if item.config.get("explain") else []
        if self.form(item) == "ranking":  # all offers on one page
            return state, pages + [{"rank": True, "v": state["version"]}]
        active = dict(holdouts.cbc_active(item))
        order = [(k, x) for k, x in holdouts.page_order(len(self.version_tasks(item, state["version"])),
                                                         len(holdouts.stored(item))) if k == "t" or x in active]
        # holdouts after task 3 and at the end; "n": the number the page shows ("Aufgabe n von ...")
        pages += [{"task": x, "v": state["version"], "n": n} if k == "t" else {"holdout": x, "n": n}
                  for n, (k, x) in enumerate(order, start=1)]
        return state, pages

    def balanced_version(self, survey, item, rng):
        from app import balance

        n_versions = len(item.config["design"]["versions"])
        used = balance.counts(survey)["version"].get(str(item.id), {})
        counts = [used.get(v, 0) for v in range(n_versions)]
        least = min(counts)
        return rng.choice([v for v, c in enumerate(counts) if c == least])

    def version_tasks(self, item, version):
        versions = (item.config.get("design") or {}).get("versions") or []
        return versions[version] if isinstance(version, int) and 0 <= version < len(versions) else []

    def task_profiles(self, item, version, task):
        """The profiles of one task: [profile] or [first, second]."""
        tasks = self.version_tasks(item, version)
        if not 0 <= task < len(tasks):
            return []
        return list(tasks[task]) if self.design_form(item) == "pairs" else [tasks[task]]

    def page_profiles(self, item, meta, version):
        """(profiles, answer key) of a task or holdout page."""
        if "holdout" in meta:
            return holdouts.cbc_profiles(item, meta["holdout"]) or [], f"h{meta['holdout']}"
        task = meta.get("task", 0)
        return self.task_profiles(item, version, task), f"t{task}"

    def holdout_profiles(self, item, h):
        return holdouts.cbc_profiles(item, h)

    def holdout_count(self, item):
        return len(holdouts.stored(item))

    def holdout_alternatives(self, item):
        return holdouts.alternatives(item)

    def holdout_level(self, item, h, j, attr_index):
        return holdouts.cbc_level_choice(item, h, j, attr_index)

    def holdout_warnings(self, item):
        return list(holdouts.cbc_warnings(item))

    def shown_tasks(self, item):
        """Tasks a person sees, holdouts included (the "of" in "Aufgabe 3 von 10")."""
        return self.n_tasks(item) + len(holdouts.cbc_active(item))

    def ranked_offers(self, item, version, answer=None):
        """[(task index, profile)] of the ranking page: in the order of the answer if it ranks them all, else in the order
        of the plan."""
        offers = list(enumerate(self.version_tasks(item, version)))
        answer = answer if isinstance(answer, dict) else {}
        ranks = [answer.get(f"t{t}") for t, _ in offers]
        if all(isinstance(r, int) for r in ranks) and sorted(ranks) == list(range(1, len(offers) + 1)):
            offers.sort(key=lambda o: ranks[o[0]])
        return offers

    def timing_pages(self, item):
        pages = [("_x", "Erklärungsseite", ".x")] if item.config.get("explain") else []
        if self.form(item) == "ranking":
            return pages + [("_rk", "Rangfolge", "")]
        active = {h for h, _ in holdouts.cbc_active(item)}
        return pages + [(f"_t{x + 1}", f"Aufgabe {x + 1}", f".t{x}") if k == "t" else
                        (f"_ho{x + 1}", f"Holdout-Aufgabe {x + 1}", f".h{x}")
                        for k, x in holdouts.page_order(self.n_tasks(item), len(holdouts.stored(item)))
                        if k == "t" or x in active]

    def seconds(self, item, read):
        from app.duration import ANSWER_SECONDS

        attrs = len(item.config.get("attributes", []))
        sides = 2 if self.design_form(item) == "pairs" else 1
        per_task = ANSWER_SECONDS["cva_task"] + 0.5 * attrs * sides
        return read + per_task * self.shown_tasks(item) + (15 if item.config.get("explain") else 0)

    # --- images ------------------------------------------------------------------------

    def required_keys(self, item, variable_rows=None):
        if not self.uses_images(item) or not self.design_current(item) or not self.visual_attributes(item):
            return []
        keys = set()
        pairs = self.design_form(item) == "pairs"
        for plan in item.config["design"]["versions"]:
            for task in plan:
                for profile in (task if pairs else [task]):
                    keys.add(self.image_key(item, profile))
        for _, profiles in holdouts.cbc_active(item):
            keys.update(self.image_key(item, profile) for profile in profiles)
        return sorted(keys)

    # --- checks ------------------------------------------------------------------------

    def validate(self, item):
        problems = self.validate_attributes(item)
        if item.config.get("attributes") and not problems and not self.design_current(item):
            problems.append(de_en("Versuchsplan fehlt oder ist veraltet: bitte „Versuchsplan erzeugen“",
                                  "Experimental design missing or out of date: please “Generate experimental design”"))
        return (problems + self.validate_price_attribute(item) + self.validate_images(item)
                + holdouts.cbc_problems(item))

    # --- answers -----------------------------------------------------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        meta = meta or {}
        if meta.get("explain"):
            return {"explained": True}
        if meta.get("rank"):
            return self.parse_ranking(item, form, lang, meta)
        key = f"h{meta['holdout']}" if "holdout" in meta else f"t{meta.get('task', 0)}"
        extra = {"v": meta["v"]} if "v" in meta else {}
        raw = form.get(f"i{item.id}_{key}", "").strip()
        if raw == "":
            if item.required:
                raise AnswerError(tr(lang, "Bitte wählen Sie eine Antwort.", "Please choose an answer."))
            return {key: None, **extra}
        try:
            value = int(raw)
        except ValueError:
            raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice.")) from None
        lo, hi = self.scale_range(item)
        if not lo <= value <= hi:
            raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
        return {key: value, **extra}

    def parse_ranking(self, item, form, lang, meta):
        """The order of the ranking page as in the ranking element (hidden fields i<id> = task index in the order shown,
        i<id>_touched once moved or confirmed; the paper version writes the ranks, app/paper.py): {"v", "t<k>": rank}."""
        n = len(self.version_tasks(item, meta.get("v")))
        codes = [str(t) for t in range(n)]
        order = list(dict.fromkeys(c for c in form.getlist(f"i{item.id}") if c in codes))
        extra = {"v": meta["v"]} if "v" in meta else {}
        if not form.get(f"i{item.id}_touched") or sorted(order) != sorted(codes):
            if item.required:
                raise AnswerError(tr(lang, "Bitte bringen Sie die Angebote in eine Reihenfolge (oder bestätigen Sie sie).",
                                     "Please put the offers in order (or confirm the order)."))
            return {**{f"t{t}": None for t in range(n)}, **extra}
        return {**{f"t{int(c)}": rank for rank, c in enumerate(order, start=1)}, **extra}

    def display_value(self, item, form, meta=None):
        meta = meta or {}
        if meta.get("rank"):
            codes = [c for c in form.getlist(f"i{item.id}") if c.isdigit()]
            return {f"t{int(c)}": rank for rank, c in enumerate(dict.fromkeys(codes), start=1)} \
                if form.get(f"i{item.id}_touched") else {}
        key = f"h{meta['holdout']}" if "holdout" in meta else f"t{meta.get('task', 0)}"
        return {key: whole(form.get(f"i{item.id}_{key}", ""))}

    def merge_answer(self, old, new):
        return {**(old or {}), **(new or {})}

    def is_missing(self, item, answer):
        answer = answer if isinstance(answer, dict) else {}
        if answer == {"explained": True}:
            return False
        given = {k: v for k, v in answer.items() if k.startswith("t")}
        return not given or all(v is None for v in given.values())

    def dummy_fields(self, item, rng, meta, answers=None):
        """A linear utility model as for CBC dummies (part-worths the same for all dummies of this item, a deviation per
        dummy person), mapped onto the scale with noise; pairs answer the difference of the two offers."""
        from app.items.conjoint import dummy_person

        meta = meta or {}
        if meta.get("explain") or not self.design_current(item):
            return {}
        if meta.get("rank"):  # the order of the utilities with noise
            person = dummy_person(self, item, rng, meta.get("respondent"))
            spread = sum(max(pw) - min(pw) for pw in person["pw"]) or 1.0
            offers = self.version_tasks(item, meta.get("v", 0))
            u = [sum(person["pw"][a][lv] for a, lv in enumerate(p)) + rng.gauss(0, DUMMY_NOISE * spread) for p in offers]
            order = sorted(range(len(offers)), key=lambda t: -u[t])
            return {f"i{item.id}": [str(t) for t in order], f"i{item.id}_touched": "1"}
        profiles, key = self.page_profiles(item, meta, meta.get("v", 0))
        if not profiles:
            return {}
        person = dummy_person(self, item, rng, meta.get("respondent"))
        utility = [sum(person["pw"][a][lv] for a, lv in enumerate(p)) for p in profiles]
        lo, hi = self.scale_range(item)
        spread = sum(max(pw) - min(pw) for pw in person["pw"]) or 1.0
        if self.form(item) == "pairs":
            x = (utility[0] - utility[1]) / spread * 2 * hi + rng.gauss(0, DUMMY_NOISE * (hi - lo))
        else:
            x = (lo + hi) / 2 + utility[0] / spread * (hi - lo) + rng.gauss(0, DUMMY_NOISE * (hi - lo))
        return {f"i{item.id}_{key}": str(max(lo, min(hi, round(x))))}

    # --- export --------------------------------------------------------------------------

    def export_columns(self, item):
        code = item.code
        pairs = self.form(item) == "pairs"
        cols = [(f"{code}_version", "Version des Versuchsplans")]
        what = {"pairs": "Paarurteil (positiv = erstes Angebot lieber)", "ranking": "Rang (1 = am liebsten)"}.get(
            self.form(item), "Bewertung")
        cols += [(f"{code}_t{t + 1}", f"{'Angebot' if self.form(item) == 'ranking' else 'Aufgabe'} {t + 1}: {what}")
                 for t in range(self.n_tasks(item))]
        cols += [(f"{code}_ho{h + 1}", f"Holdout-Aufgabe {h + 1}: " + ("Paarurteil (positiv = erstes Angebot lieber)" if pairs
                                                                       else "Bewertung"))
                 for h in range(len(holdouts.stored(item)))]
        for a in self.attributes(item, item.survey.default_language):
            for k, label in enumerate(a["level_labels"], start=1):
                cols.append((f"{code}_pw_{a['code']}_{k}", f"Teilnutzen {plain(a['label'])}: {plain(label)}"))
        for a in item.config.get("attributes", []):
            cols.append((f"{code}_imp_{a['code']}", f"Wichtigkeit {a['code']}"))
        cols.append((f"{code}_r2", "R² der Regression je Person"))
        return cols

    def missing_codes(self, item, column):
        return ()

    def export_values(self, item, answer):
        from app import cva_analysis

        answer = answer if isinstance(answer, dict) else {}
        code = item.code
        out = {f"{code}_version": None}
        for t in range(self.n_tasks(item)):
            out[f"{code}_t{t + 1}"] = answer.get(f"t{t}")
        for h in range(len(holdouts.stored(item))):
            out[f"{code}_ho{h + 1}"] = answer.get(f"h{h}")
        fitted = cva_analysis.person_values(item, answer) if self.design_current(item) else None
        attrs = item.config.get("attributes", [])
        for j, a in enumerate(attrs):
            for k in range(len(a.get("levels", []))):
                out[f"{code}_pw_{a['code']}_{k + 1}"] = round(fitted["pw"][j][k], 4) if fitted else None
            imp = fitted["importance"][j] if fitted else None
            out[f"{code}_imp_{a['code']}"] = round(imp, 4) if imp is not None else None
        out[f"{code}_r2"] = round(fitted["r2"], 4) if fitted and fitted["r2"] is not None else None
        return out

    def export_state_values(self, item, state):
        return {f"{item.code}_version": state["version"] + 1 if "version" in (state or {}) else None}

    def column_level(self, item, column):
        return "nominal" if column == f"{item.code}_version" else "metric"

    def codebook_info(self, item, column, lang, ui=None):
        code = item.code
        pairs = self.form(item) == "pairs"
        if column == f"{code}_version":
            return "Version des Versuchsplans", in_lang(ui, "1 bis {n}", "1 to {n}", n=item.config.get("versions", 1)), [
                in_lang(ui, "balanciert über echte Teilnahmen; welche Angebote: Versuchsplan bzw. Langformat",
                        "balanced over real participations; which offers: experimental design or long format")]
        if column.startswith(f"{code}_ho"):
            lo, hi = self.scale_range(item)
            h = whole(column[len(code) + 3:])
            profiles = holdouts.cbc_profiles(item, h - 1) if h else None
            attrs = self.attributes(item, lang)
            shown = [", ".join(f"{plain(a['label'])}: {plain(a['level_labels'][p[k]])}" for k, a in enumerate(attrs))
                     for p in profiles or []]
            return "CVA", in_lang(ui, "{lo} bis {hi}", "{lo} to {hi}", lo=lo, hi=hi), [in_lang(
                ui, "Holdout-Aufgabe: für alle gleich, geht nicht in die Regression ein (prüft die Vorhersage); leer = "
                    "nicht beantwortet", "holdout task: the same for everybody, not part of the regression (checks the "
                    "prediction); empty = not answered")] + [
                in_lang(ui, "Angebot {n}: {text}", "Offer {n}: {text}", n=n, text=text) for n, text in enumerate(shown, 1)]
        if column.startswith(f"{code}_t") and self.form(item) == "ranking":
            return "CVA", in_lang(ui, "1 bis {n}", "1 to {n}", n=self.n_tasks(item)), [in_lang(
                ui, "Rang des Angebots in der Reihenfolge der Person (1 = am liebsten; welches Angebot: Versuchsplan bzw. "
                    "Langformat); in der Regression umgekehrt (n + 1 − Rang); leer = nicht beantwortet",
                "rank of the offer in the person's order (1 = liked most; which offer: experimental design or long "
                "format); reversed in the regression (n + 1 − rank); empty = not answered")]
        if column.startswith(f"{code}_t"):
            lo, hi = self.scale_range(item)
            if pairs:
                return "CVA", in_lang(ui, "{lo} bis {hi}", "{lo} to {hi}", lo=lo, hi=hi), [in_lang(
                    ui, "positiv = das zuerst gezeigte Angebot lieber, 0 = gleich, negativ = das zweite lieber; leer = nicht "
                        "beantwortet", "positive = the offer shown first preferred, 0 = same, negative = the second "
                        "preferred; empty = not answered")]
            return "CVA", in_lang(ui, "{lo} bis {hi}", "{lo} to {hi}", lo=lo, hi=hi), [in_lang(
                ui, "Bewertung des Angebots der Aufgabe (Langformat); leer = nicht beantwortet",
                "rating of the offer of the task (long format); empty = not answered")]
        if column.startswith(f"{code}_pw_"):
            return "Zahl", "", [in_lang(ui, "Teilnutzen aus der Regression je Person (Effektkodierung, Summe je Merkmal 0), "
                                        "in Punkten der Antwortskala; leer, wenn die Antworten nicht für alle Teilnutzen "
                                        "reichen", "part-worth from the regression per person (effects coding, sum per "
                                        "attribute 0), in points of the answer scale; empty if the answers are not enough "
                                        "for all part-worths")]
        if column.startswith(f"{code}_imp_"):
            return "Zahl", in_lang(ui, "von 0 bis 1", "from 0 to 1"), [in_lang(
                ui, "Spannweite der Teilnutzen des Merkmals / Summe aller Spannweiten, je Person",
                "range of the attribute's part-worths / sum of all ranges, per person")]
        if column == f"{code}_r2":
            return "Zahl", in_lang(ui, "von 0 bis 1", "from 0 to 1"), [in_lang(
                ui, "Anteil der Streuung der Antworten, den die Teilnutzen der Person erklären; leer bei überall gleicher "
                    "Antwort", "share of the variance of the answers that the person's part-worths explain; empty with the "
                    "same answer everywhere")]
        return None

    def result_column(self, item, column):
        return None, "", None  # summarised as a whole: results()

    def results(self, item, table, lang):
        from app import cva_analysis
        from app.choice_model import responses_for

        return cva_analysis.analyse(item, responses_for(item, table), lang)

    def long_columns(self, item):
        attrs = item.config.get("attributes", [])
        codes = [a["code"] for a in attrs]
        return (["version", "task", "side"] + codes + [c + "_label" for c in codes]
                + [a["code"] + "_value" for a in attrs if self.level_values(a)] + ["answer"])

    def long_rows(self, item, response):
        """One row per task and offer shown: level numbers (1-based), labels, values, the answer of the task."""
        from app.items.conjoint import _value

        state = ((response.layout or {}).get("cj") or {}).get(str(item.id))
        if not state or not self.design_current(item) or not self.version_tasks(item, state.get("version")):
            return
        attrs = self.attributes(item, item.survey.default_language)
        valued = [(k, self.level_values(a)) for k, a in enumerate(attrs) if self.level_values(a)]
        answer = (response.answers or {}).get(str(item.id)) or {}
        v = state["version"]
        for t in range(len(self.version_tasks(item, v))):
            y = answer.get(f"t{t}")
            for side, profile in enumerate(self.task_profiles(item, v, t), start=1):
                yield ([v + 1, t + 1, side] + [p + 1 for p in profile]
                       + [plain(attrs[k]["level_labels"][p]) for k, p in enumerate(profile)]
                       + [_value(values, profile[k]) for k, values in valued] + ["" if y is None else y])

    def questionnaire_lines(self, item, lang):
        out = [f"  - Merkmale `{a['code']}` {a['label']}: " + "; ".join(a["level_labels"])
               for a in self.attributes(item, lang)]
        c = item.config
        lo, hi = self.scale_range(item)
        form = {"pairs": f"pairs of offers on a graded scale with {c.get('points', 9)} points",
                "ranking": "all offers on one page put in order"}.get(self.form(item), f"one offer per page rated from {lo} "
                                                                                       f"to {hi}")
        out.append(f"  - {self.n_tasks(item)} tasks ({form}), {c.get('versions', 1)} "
                   f"{'version' if c.get('versions', 1) == 1 else 'versions'}")
        if holdouts.stored(item):
            out.append(f"  - {len(holdouts.stored(item))} holdout task(s), the same for everyone, not used for estimation")
        return out


def _doe():
    from app import doe

    return doe
