"""MaxDiff (best-worst scaling, case 1).

K statements; each task shows a set of k of them, and participants pick the most and the least important
one. A balanced design (app/maxdiff_design.py) is generated in draft and frozen at launch, with versions
balanced over participants like the conjoint. One page per task, optionally an explanation page first and
a direct anchoring question last ("Which of these are really important to you?", all statements).
Results: counting scores and an aggregate MNL (app/maxdiff_analysis.py), individual utilities by
hierarchical Bayes on request (app/maxdiff_hb.py, also in the wide export); the long format for other models.

config: statements [{id}], set_size, sets, versions, explain, anchor, auto_next,
        design {sig, versions, stats}, holdouts [{id, statements: [statement id, ...]}] (app/holdouts.py)
texts:  question, statements {id: text}, best_label, worst_label, explain_intro, anchor_question, anchor_none
answer: {"v": version, "t0": {"b": index, "w": index}, ..., "h0": {"b": .., "w": ..} (holdout set), ...,
        "anchor": [indices]} (0-based statement indices;
        "anchor": [] = "none of these" ticked, None = the anchoring question was left open)
"""
import hashlib
import json
import secrets

from app import holdouts
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en
from app.items.base import r_any, r_flag, r_id, r_int, r_list, r_rows  # rules for survey files, see base.py
from app.items.base import AnswerError, ItemType, form_ids, in_lang, new_id, prepared, whole
from app.ui_i18n import pick
from app.segments import clean as clean_segments  # shown latent class solution and segment names
from app.simulator import clean as clean_scenarios  # "Auswahl aus Aussagen" of the results page

DEFAULT_TEXTS = {
    "de": {"question": "Welche dieser Aussagen ist Ihnen am wichtigsten, welche am wenigsten wichtig?",
           "best_label": "Am wichtigsten", "worst_label": "Am wenigsten wichtig",
           "anchor_question": "Welche dieser Aussagen sind für Sie wirklich wichtig?", "anchor_none": "Keine davon"},
    "en": {"question": "Which of these statements is most important to you, and which is least important?",
           "best_label": "Most important", "worst_label": "Least important",
           "anchor_question": "Which of these statements are really important to you?",
           "anchor_none": "None of these"},
}
MESSAGES = {
    "de": {"both": "Bitte wählen Sie je eine Aussage als wichtigste und als am wenigsten wichtige.",
           "same": "Dieselbe Aussage kann nicht zugleich am wichtigsten und am wenigsten wichtig sein.",
           "invalid": "Ungültige Auswahl.",
           "anchor": "Bitte wählen Sie mindestens eine Aussage oder „Keine davon“."},
    "en": {"both": "Please choose one statement as most important and one as least important.",
           "same": "The same statement cannot be both most and least important.",
           "invalid": "Invalid choice.",
           "anchor": "Please choose at least one statement or “None of these”."},
}
MAX_STATEMENTS = 60
DUMMY_SPREAD = 1.0       # sd of the true utilities (dummy data)
DUMMY_PERSON = 0.5       # sd of the person deviations
DUMMY_ANCHOR = 0.5       # dummy persons call statements above this utility "really important"


def _new_id():
    return new_id("s")


@register
class MaxDiff(ItemType):
    key = "maxdiff"
    import_rules = {"statements": r_rows(MAX_STATEMENTS, id=r_id), "set_size": r_int(3, 6), "sets": r_int(0, 60),
                    "versions": r_int(1, 20), "explain": r_flag, "anchor": r_flag, "design": r_any,  # see import_config
                    "holdouts": r_rows(holdouts.MAX, id=r_id, statements=r_list(6, r_id)), "sim": clean_scenarios,
                    "lc": clean_segments}

    def import_config(self, config):
        """A design of the file is kept only if it is one this item could have generated: versions x sets, each
        set naming set_size different statements that exist. Its balance figures are counted anew. Otherwise
        the item comes without a design, as before "Design erzeugen"."""
        from app.maxdiff_design import diagnostics, suggested_sets

        config, unfit, unknown = super().import_config(config)
        design = config.get("design")
        if design is not None and not unfit:
            n, k = len(config["statements"]), config["set_size"]
            sets = config["sets"] or suggested_sets(n, k)
            versions = design.get("versions") if isinstance(design, dict) else None
            fits = (isinstance(versions, list) and len(versions) == config["versions"] and all(
                isinstance(v, list) and len(v) == sets and all(
                    isinstance(s, list) and len(s) == k and len(set(map(str, s))) == k
                    and all(type(i) is int and 0 <= i < n for i in s) for s in v)
                for v in versions))
            if fits and isinstance(design.get("sig"), str) and len(design["sig"]) <= 40:
                config["design"] = {"sig": design["sig"], "versions": versions, "stats": diagnostics(n, versions)}
            else:
                config["design"] = None
                unknown = unknown + ["design"]
        if not unfit and "holdouts" in config and holdouts.import_fit(config, "statements") is None:
            config["holdouts"] = []  # statements the file does not have
            unknown = unknown + ["holdouts"]
        return config, unfit, unknown
    label = "MaxDiff (Best-Worst)"
    short = Both("Wichtigste und unwichtigste wählen", "Choose most and least important")
    description = Both("Aus Sets von Aussagen jeweils die wichtigste und die unwichtigste wählen",
                       "Choose the most and the least important statement from each set of statements")
    order = 46
    fact_sheet = FactSheet(
        "choice",
        keywords=["MaxDiff", "Best-Worst", "Best-worst scaling", "BWS", "Wichtigkeit", "Importance"],
        can=[("Setgröße 3 bis 6, Zahl der Sets und Versionen.", "Set size 3 to 6, number of sets and versions."),
             ("Optional Erklärung vorab, Ankerfrage und Haltefragen.",
              "Optionally an explanation first, an anchor question and holdout tasks.")],
        good_for=[("Welche von vielen Aussagen oder Eigenschaften am wichtigsten sind",
                   "Which of many statements or features matter most")],
        not_for=[("Wie Merkmale mit Stufen zusammenwirken (dann Conjoint)",
                  "How attributes with levels work together (then Conjoint)"),
                 ("Wenige Optionen (dann Rangfolge)", "Few options (then ranking)")],
        designs=["choice", "perception"],
        examples=[("Welche von 15 Eigenschaften einer App ist Ihnen am wichtigsten, welche am wenigsten?",
                   "Which of 15 features of an app matters most to you, which least?")],
    )
    result_kind = "maxdiff"  # own summary: scores per statement (results())
    alone_on_page = True  # one page per set
    allowed_in_loop = allowed_in_separated = False  # design version per response (layout["cj"])
    supports_auto_next = True
    auto_next_hint = Both("sobald die wichtigste und die unwichtigste Aussage gewählt sind",
                          "as soon as the most and the least important statement are chosen")
    editor_tabs = Both([("content", "1 · Inhalt", "Aussagen, Frage, Beschriftungen"),
                        ("design", "2 · Ablauf", "Sets, Versionen, Berechnung"),
                        ("anchor", "3 · Anker", "optional")],
                       [("content", "1 · Content", "Statements, question, labels"),
                        ("design", "2 · Flow", "Sets, versions, calculation"),
                        ("anchor", "3 · Anchor", "optional")])
    panel_template = "items/maxdiff/panel.html"
    design_lock = True  # structure locked once real data exist (ItemType.design_locked)
    long_export = "maxdiff"
    long_export_label = Both("Person × Set × Aussage", "Person × set × statement")
    long_export_note = "- `maxdiff_*_long.csv`: long format for best-worst models (participant × set × shown statement)."

    # --- defaults and editing ----------------------------------------------------------

    def default_config(self):
        return {"statements": [], "set_size": 4, "sets": 0, "versions": 1, "explain": False, "anchor": False,
                "design": None, "auto_next": False, "holdouts": []}

    def default_texts(self, lang):
        return {**pick(DEFAULT_TEXTS, lang), "statements": {}}

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config)
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        for key in ("question", "best_label", "worst_label", "anchor_question", "anchor_none"):
            lt[key] = form.get(key, lt.get(key, "")).strip()[:500]
        lt["explain_intro"] = form.get("explain_intro", lt.get("explain_intro", "")).strip()[:3000]
        stexts = dict(lt.get("statements", {}))
        known = {s["id"] for s in config.get("statements", [])}
        rows = list(zip(form_ids(form.getlist("md_id")), form.getlist("md_text")))
        if structural:
            statements = []
            for sid, text in rows:
                text = text.strip()[:500]
                if not text:
                    continue
                sid = sid if sid and sid not in {s["id"] for s in statements} else _new_id()
                statements.append({"id": sid})
                stexts[sid] = text
            config["statements"] = statements[:MAX_STATEMENTS]

            def num(key, lo, hi, default):
                v = form.get(key, "")
                return max(lo, min(hi, whole(v))) if whole(v) is not None else default

            config.update(set_size=num("set_size", 3, 6, 4), sets=num("sets", 0, 60, 0),
                          versions=num("versions", 1, 20, 1))
            if "ho_count" in form:  # holdout sets (app/holdouts.py), locked with the design
                config["holdouts"] = holdouts.maxdiff_from_form(item, config, form)
        else:
            for sid, text in rows:
                if sid in known and text.strip():
                    stexts[sid] = text.strip()[:500]
        kept = {s["id"] for s in config.get("statements", [])}
        lt["statements"] = {sid: t for sid, t in stexts.items() if sid in kept}
        if lang == item.survey.default_language:
            config["explain"] = bool(form.get("explain"))
            if structural:
                config["anchor"] = bool(form.get("anchor"))  # adds a page: part of the structure
        texts[lang] = lt
        item.config, item.texts = config, texts

    # --- structure ------------------------------------------------------------------------

    def n_items(self, item):
        return len(item.config.get("statements", []))

    def labels(self, item, lang=None):
        t = item.t(lang).get("statements", {})
        return [t.get(s["id"], "") for s in item.config.get("statements", [])]

    def n_sets(self, item):
        """Sets per person: as configured, or the suggestion ceil(3 K / k) when left empty."""
        from app.maxdiff_design import suggested_sets

        c = item.config
        return c.get("sets") or suggested_sets(self.n_items(item), c.get("set_size", 4))

    def source_groups(self, item):
        from app.maxdiff_analysis import LATTERY
        from app.maxdiff_design import SETS_SOURCES

        return [(de_en("Zahl der Sets: Faustregel der Wahlexperimente, übertragen (Festlegung von Askurious)",
                       "Number of sets: rule of thumb of choice experiments, carried over (Askurious default)"),
                 list(SETS_SOURCES)),
                (de_en("Ankerfrage (direkte Verankerung)", "Anchor question (direct anchoring)"), [LATTERY])]

    def sets_needed(self, item, n):
        """The sample size rule the other way round: (set size, minimum sets, better, sets for values per
        person) for a sample of n, one row per set size the statements allow."""
        from app.maxdiff_design import sets_needed, suggested_sets

        K = self.n_items(item)
        return [(k, *sets_needed(n, K, k), suggested_sets(K, k)) for k in self.allowed_set_sizes(item)]

    def allowed_set_sizes(self, item):
        from app.maxdiff_design import allowed_set_sizes

        return allowed_set_sizes(self.n_items(item))

    # --- design ----------------------------------------------------------------------

    def signature(self, item):
        c = item.config
        shape = [self.n_items(item), c.get("set_size"), self.n_sets(item), c.get("versions")]
        return hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]

    def preview_empty(self, item):
        if self.design_current(item):
            return None
        return de_en("Die Vorschau erscheint, sobald der Versuchsplan erzeugt ist.",
                     "The preview appears as soon as the experimental design is generated."), "design"

    def design_current(self, item):
        d = item.config.get("design")
        return bool(d) and d.get("sig") == self.signature(item)

    def make_design(self, item):
        from app.maxdiff_design import diagnostics, generate

        c = item.config
        n, k, t = self.n_items(item), c.get("set_size", 4), self.n_sets(item)
        versions = generate(n, k, t, c.get("versions", 1), seed=secrets.randbits(32))
        item.config = {**c, "design": {"sig": self.signature(item), "versions": versions,
                                       "stats": diagnostics(n, versions)}}

    def design_summary(self, item):
        s = item.config["design"]["stats"]["version"]
        return de_en("Versuchsplan erzeugt: je Aussage {f0}–{f1}-mal, Paare {p0}–{p1}-mal gemeinsam.",
                     "Experimental design generated: each statement {f0}–{f1} times, pairs together {p0}–{p1} times.",
                     f0=s['freq_min'], f1=s['freq_max'], p0=s['pair_min'], p1=s['pair_max'])

    def design_notes(self, item):
        """Warnings about the design shape for the editor panel."""
        c, n = item.config, self.n_items(item)
        notes = []
        if n and self.n_sets(item) * c.get("set_size", 4) < 3 * n:
            notes.append(de_en("Jede Aussage erscheint je Person weniger als dreimal; für stabile Werte je Person "
                               "mehr Sets einplanen (Faustregel: 3 · Aussagen / Set-Größe).",
                               "Each statement appears fewer than three times per person; for stable values per "
                               "person plan more sets (rule of thumb: 3 · statements / set size)."))
        d = c.get("design") or {}
        if d and self.design_current(item) and not d.get("stats", {}).get("connected_each", True):
            notes.append(de_en("Nicht alle Aussagen sind innerhalb jeder Version über gemeinsame Sets verbunden; "
                               "mehr Sets oder größere Sets wählen.",
                               "Not all statements are connected through shared sets within each version; "
                               "choose more sets or larger sets."))
        return notes

    def expand_pages(self, survey, item, rng):
        """One page per set (explanation first, anchoring question last, both optional); without a current
        design the item stays a normal page, which shows "Design noch nicht erzeugt"."""
        if not self.design_current(item):
            return None
        state = {"version": self.balanced_version(survey, item, rng)}
        pages = [{"explain": True}] if item.config.get("explain") else []
        active = dict(holdouts.maxdiff_active(item))
        if not active:
            pages += [{"task": t, "v": state["version"]} for t in range(self.n_sets(item))]
        else:  # holdout sets between and after the sets; "n": the number the page shows ("Set n von ...")
            order = [(k, x) for k, x in holdouts.page_order(self.n_sets(item), len(holdouts.stored(item)))
                     if k == "t" or x in active]
            pages += [{"task" if k == "t" else "holdout": x, "v": state["version"], "n": n}
                      for n, (k, x) in enumerate(order, start=1)]
        if item.config.get("anchor"):
            # a fixed list favours its first entries: every person sees the statements in an own order
            state["anchor_order"] = rng.sample(range(self.n_items(item)), self.n_items(item))
            pages.append({"anchor": True, "order": state["anchor_order"]})
        return state, pages

    def holdout_set(self, item, h):
        return holdouts.maxdiff_set(item, h) or []

    def holdout_count(self, item):
        return len(holdouts.stored(item))

    def holdout_statement(self, item, h, p):
        return holdouts.maxdiff_choice(item, h, p)

    def shown_tasks(self, item):
        """Sets a person sees, holdouts included (the "of" in "Set 3 von 10")."""
        return self.n_sets(item) + len(holdouts.maxdiff_active(item))

    def balanced_version(self, survey, item, rng):
        """The design version used least so far by real participants (random among ties)."""
        from app import balance

        n_versions = len(item.config["design"]["versions"])
        used = balance.counts(survey)["version"].get(str(item.id), {})
        counts = [used.get(v, 0) for v in range(n_versions)]
        least = min(counts)
        return rng.choice([v for v, c in enumerate(counts) if c == least])

    def task_set(self, item, version, task):
        """Statement indices of one set, in display order."""
        return item.config["design"]["versions"][version][task]

    def timing_pages(self, item):
        pages = [("_x", "Erklärungsseite", ".x")] if item.config.get("explain") else []
        active = {h for h, _ in holdouts.maxdiff_active(item)}
        pages += [(f"_t{x + 1}", f"Set {x + 1}", f".t{x}") if k == "t" else
                  (f"_ho{x + 1}", f"Holdout-Set {x + 1}", f".h{x}")
                  for k, x in holdouts.page_order(self.n_sets(item), len(holdouts.stored(item)))
                  if k == "t" or x in active]
        return pages + ([("_a", "Ankerfrage", ".a")] if item.config.get("anchor") else [])

    def seconds(self, item, read):
        from app.duration import ANSWER_SECONDS

        extra = (15 if item.config.get("explain") else 0) + (5 + 1.5 * self.n_items(item) if item.config.get("anchor") else 0)
        return read + ANSWER_SECONDS["maxdiff_task"] * self.shown_tasks(item) + extra

    # --- checks ----------------------------------------------------------------------------

    def validate(self, item):
        n = self.n_items(item)
        problems = []
        if n < 4:
            problems.append(de_en("mindestens vier Aussagen", "at least four statements"))
        elif item.config.get("set_size") not in self.allowed_set_sizes(item):
            sizes = self.allowed_set_sizes(item)
            problems.append(de_en("Aussagen je Set: {lo} bis {hi}", "statements per set: {lo} to {hi}", lo=sizes[0], hi=sizes[-1]))
        elif not self.design_current(item):
            problems.append(de_en("Versuchsplan fehlt oder ist veraltet: bitte „Versuchsplan erzeugen“",
                                  "Experimental design missing or out of date: please “Generate experimental design”"))
        return problems + holdouts.maxdiff_problems(item)

    # --- answers -------------------------------------------------------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        meta = meta or {}
        m = pick(MESSAGES, lang)
        if meta.get("explain"):
            return {"explained": True}  # the explanation page has no answer
        n = self.n_items(item)
        if meta.get("anchor"):
            raw = form.getlist(f"i{item.id}_a")
            chosen = sorted({whole(x) for x in raw if whole(x) is not None and whole(x) < n})
            if not chosen and "none" not in raw:  # left open: no information, unlike "none of these"
                if item.required:
                    raise AnswerError(m["anchor"])
                return {"anchor": None}
            return {"anchor": chosen}  # "none of these" is an answer: nothing is really important
        task = meta.get("task", 0)
        key = f"h{meta['holdout']}" if "holdout" in meta else f"t{task}"
        raw_b, raw_w = form.get(f"i{item.id}_{key}_b", ""), form.get(f"i{item.id}_{key}_w", "")
        extra = {"v": meta["v"]} if "v" in meta else {}
        if raw_b == "" and raw_w == "":
            if item.required:
                raise AnswerError(m["both"])
            return {key: None, **extra}
        if raw_b == "" or raw_w == "":
            raise AnswerError(m["both"])
        b, w = whole(raw_b), whole(raw_w)
        if b is None or w is None:
            raise AnswerError(m["invalid"])
        if "holdout" in meta:
            allowed = set(holdouts.maxdiff_set(item, meta["holdout"]) or range(n))
        else:
            allowed = (set(self.task_set(item, meta["v"], task)) if "v" in meta and self.design_current(item)
                       else set(range(n)))
        if b not in allowed or w not in allowed:
            raise AnswerError(m["invalid"])
        if b == w:
            raise AnswerError(m["same"])
        return {key: {"b": b, "w": w}, **extra}

    def display_value(self, item, form, meta=None):
        meta = meta or {}
        if meta.get("anchor"):
            raw = form.getlist(f"i{item.id}_a")
            return {"anchor": [whole(x) for x in raw if whole(x) is not None] if raw else None}
        key = f"h{meta['holdout']}" if "holdout" in meta else f"t{meta.get('task', 0)}"
        raw_b, raw_w = form.get(f"i{item.id}_{key}_b", ""), form.get(f"i{item.id}_{key}_w", "")
        return {key: {"b": whole(raw_b), "w": whole(raw_w)}}

    def merge_answer(self, old, new):
        return {**(old or {}), **(new or {})}

    def is_missing(self, item, answer):
        answer = answer if isinstance(answer, dict) else {}
        if answer == {"explained": True}:
            # the explanation page asks nothing; stored with the sets, the mark must not hide a set left open
            return False  # (code review 2026-10-15, F4)
        if "anchor" in answer:
            return answer["anchor"] is None  # "none of these" ([]) is an answer, an open question is not
        choices = {k: v for k, v in answer.items() if k.startswith("t")}
        return not choices or all(v is None for v in choices.values())

    def dummy_fields(self, item, rng, meta, answers=None):
        """Random utility model: true utilities per statement (the same for all dummies of this item),
        a deviation per dummy person, and Gumbel noise per choice; best = highest, worst = lowest."""
        import math
        import random

        meta = meta or {}
        if meta.get("explain") or not self.design_current(item):
            return {}
        n = self.n_items(item)
        true = random.Random(f"maxdiff:{item.id}")
        base = [true.gauss(0, DUMMY_SPREAD) for _ in range(n)]
        # one deviation per simulated person: kept on the generator between the pages of this person
        persons = rng.__dict__.setdefault("_maxdiff_persons", {})
        key = (item.id, meta.get("respondent"))
        if key not in persons:
            persons[key] = [u + rng.gauss(0, DUMMY_PERSON) for u in base]
        utility = persons[key]
        if meta.get("anchor"):
            return {f"i{item.id}_a": [str(i) for i in range(n) if utility[i] > DUMMY_ANCHOR] or ["none"]}
        task = meta.get("task", 0)
        key = f"h{meta['holdout']}" if "holdout" in meta else f"t{task}"
        # a holdout set from the same utilities as the sets
        s = (holdouts.maxdiff_set(item, meta["holdout"]) if "holdout" in meta
             else self.task_set(item, meta.get("v", 0), task))
        if not s:
            return {}

        def gumbel():
            return -math.log(-math.log(max(rng.random(), 1e-12)))

        best = max(s, key=lambda i: utility[i] + gumbel())
        worst = min((i for i in s if i != best), key=lambda i: utility[i] - gumbel())
        return {f"i{item.id}_{key}_b": str(best), f"i{item.id}_{key}_w": str(worst)}

    # --- export ----------------------------------------------------------------------------

    def export_columns(self, item):
        cols = [(f"{item.code}_version", "Version des Versuchsplans")]
        for t in range(self.n_sets(item)):
            cols.append((f"{item.code}_t{t + 1}_best", f"Set {t + 1}: wichtigste Aussage (Nummer)"))
            cols.append((f"{item.code}_t{t + 1}_worst", f"Set {t + 1}: am wenigsten wichtige Aussage (Nummer)"))
        for h in range(len(holdouts.stored(item))):
            cols.append((f"{item.code}_ho{h + 1}_best", f"Holdout-Set {h + 1}: wichtigste Aussage (Nummer)"))
            cols.append((f"{item.code}_ho{h + 1}_worst", f"Holdout-Set {h + 1}: am wenigsten wichtige Aussage (Nummer)"))
        labels = self.labels(item)
        for i, label in enumerate(labels, start=1):
            cols.append((f"{item.code}_score{i}", f"Score (am wichtigsten − am wenigsten wichtig) / gezeigt: {label}"))
        if item.config.get("anchor"):
            for i, label in enumerate(labels, start=1):
                cols.append((f"{item.code}_anchor{i}", f"Anker „wirklich wichtig“: {label}"))
            cols.append((f"{item.code}_anchor_order", "Ankerfrage: gezeigte Reihenfolge der Aussagen (Nummern)"))
        return cols

    def _export_plan(self, item):
        """What export_values needs of the element, the same for every participation (code review 2026-10-07, R4)."""
        code = item.code
        return {"n": self.n_items(item),
                "tasks": [(f"t{t}", t, f"{code}_t{t + 1}_best", f"{code}_t{t + 1}_worst") for t in range(self.n_sets(item))],
                "holdouts": [(f"h{h}", f"{code}_ho{h + 1}_best", f"{code}_ho{h + 1}_worst")
                             for h in range(len(holdouts.stored(item)))],
                "versions": item.config["design"]["versions"] if self.design_current(item) else None,
                "anchor": bool(item.config.get("anchor"))}

    def export_values(self, item, answer):
        answer = answer if isinstance(answer, dict) else {}
        plan = prepared(item, "maxdiff_export", lambda: self._export_plan(item))
        n, code = plan["n"], item.code
        out = {f"{code}_version": None}
        tasks = {}
        for key, t, best, worst in plan["tasks"]:
            choice = answer.get(key) or {}
            b, w = choice.get("b"), choice.get("w")
            out[best] = None if b is None else b + 1
            out[worst] = None if w is None else w + 1
            if b is not None and w is not None:
                tasks[t] = (b, w)
        for key, best, worst in plan["holdouts"]:
            choice = answer.get(key) or {}
            b, w = choice.get("b"), choice.get("w")
            out[best] = None if b is None else b + 1
            out[worst] = None if w is None else w + 1
        scores = [None] * n
        v = answer.get("v")
        versions = plan["versions"]
        if tasks and v is not None and versions is not None and v < len(versions):
            from app.maxdiff_analysis import person_scores

            scores = person_scores(n, versions[v], tasks)
        out.update({f"{code}_score{i + 1}": scores[i] for i in range(n)})
        if plan["anchor"]:
            chosen = answer.get("anchor")
            out.update({f"{code}_anchor{i + 1}": None if chosen is None else int(i in chosen) for i in range(n)})
            out[f"{code}_anchor_order"] = None
        return out

    def export_state_values(self, item, state):
        out = {f"{item.code}_version": state["version"] + 1 if "version" in (state or {}) else None}
        if item.config.get("anchor"):  # None: answered before the order was randomised (list order)
            order = (state or {}).get("anchor_order")
            out[f"{item.code}_anchor_order"] = "|".join(str(i + 1) for i in order) if order else None
        return out

    def codebook_info(self, item, column, lang, ui=None):
        code, labels = item.code, self.labels(item, lang)
        values = "; ".join(f"{n} = {str(label).replace(';', ',')}" for n, label in enumerate(labels, start=1))
        if column == f"{code}_version":
            return "Version des Versuchsplans", in_lang(ui, "1 bis {n}", "1 to {n}", n=item.config.get('versions', 1)), [
                in_lang(ui, "balanciert über echte Teilnahmen", "balanced over real participations")]
        if column.startswith(f"{code}_ho") and (column.endswith("_best") or column.endswith("_worst")):
            h = int(column[len(code) + 3:].split("_")[0]) - 1
            shown = holdouts.maxdiff_set(item, h) or []
            return "MaxDiff", values, [
                in_lang(ui, "Nummer der Aussage; Holdout-Set: für alle gleich, geht nicht in Zählwerte, Logit-Modell und "
                            "HB ein (Prüfung der Vorhersage)",
                        "number of the statement; holdout set: the same for everybody, not part of the counts, the logit "
                        "model and HB (check of the prediction)"),
                in_lang(ui, "Aussagen im Set: ", "statements in the set: ") + ", ".join(str(i + 1) for i in shown)]
        if column.endswith("_best") or column.endswith("_worst"):
            return "MaxDiff", values, [in_lang(ui, "Nummer der Aussage; welche Aussagen im Set standen: Versuchsplan bzw. Langformat",
                                               "number of the statement; which statements were in the set: experimental design or long format")]
        if column.startswith(f"{code}_score"):
            return "Zahl", in_lang(ui, "von -1 bis 1", "from -1 to 1"), [in_lang(
                ui, "(Anzahl am wichtigsten − Anzahl am wenigsten wichtig) / Anzahl gezeigt, je Person",
                "(times most important − times least important) / times shown, per person")]
        if column == f"{code}_anchor_order":
            return "Reihenfolge", in_lang(ui, "Nummern der Aussagen, getrennt durch |", "numbers of the statements, separated by |"), [
                in_lang(ui, "zufällig je Person; leer bei Teilnahmen vor der Umstellung (dort in Listenreihenfolge)",
                        "random per person; empty for participations before the change (list order there)")]
        if column.startswith(f"{code}_anchor"):
            return "Anker", in_lang(ui, "0 = nicht gewählt; 1 = gewählt", "0 = not chosen; 1 = chosen"), [in_lang(
                ui, "direkte Ankerfrage nach den Sets; „Keine davon“: alle 0; leer: Ankerfrage offen gelassen oder nicht erreicht",
                "direct anchor question after the sets; “None of these”: all 0; empty: anchor question left open or not reached")]
        return None

    levels = ("nominal",)

    def column_level(self, item, column):
        # version, chosen statements and anchor are codes; the best-minus-worst scores are metric
        return "metric" if column.startswith(f"{item.code}_score") else "nominal"

    def result_column(self, item, column):
        return None, "", None  # summarised as a whole: results()

    def results(self, item, table, lang):
        from app.maxdiff_analysis import analyse

        return analyse(item, table, self.labels(item, lang))

    def long_columns(self, item):
        return ["version", "set", "position", "statement", "statement_label", "best", "worst"]

    def long_rows(self, item, response):
        """One row per set and shown statement: position in the set (1-based), statement number, best, worst."""
        state = ((response.layout or {}).get("cj") or {}).get(str(item.id))
        versions = (item.config.get("design") or {}).get("versions") or []
        if not state or not self.design_current(item) or not 0 <= state.get("version", -1) < len(versions):
            return  # no design, or a version the current design does not have: skipped
        labels = self.labels(item, item.survey.default_language)
        answer = (response.answers or {}).get(str(item.id)) or {}
        v = state["version"]
        for t in range(self.n_sets(item)):
            choice = answer.get(f"t{t}") or {}
            b, w = choice.get("b"), choice.get("w")
            for pos, i in enumerate(self.task_set(item, v, t), start=1):
                yield [v + 1, t + 1, pos, i + 1, labels[i],
                       "" if b is None else int(b == i), "" if w is None else int(w == i)]

    def questionnaire_lines(self, item, lang):
        out = [f"  - {n}. {label}" for n, label in enumerate(self.labels(item, lang), start=1)]
        c = item.config
        out.append(f"  - {self.n_sets(item)} sets × {c.get('set_size')} statements, {c.get('versions')} {'version' if c.get('versions') == 1 else 'versions'}"
                   + (", explanation page" if c.get("explain") else "")
                   + (", anchoring question" if c.get("anchor") else ""))
        if holdouts.stored(item):
            out.append(f"  - {len(holdouts.stored(item))} holdout set(s), the same for everyone, not used for estimation")
        return out
