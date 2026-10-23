"""Adaptive choice-based conjoint (ACBC).

Attributes and levels as in the CBC (app/items/attributes.py), but no fixed design: every
participant first builds a preferred profile (BYO), then screens concepts near it as
"possible" or "not possible", then chooses among the possible ones in a tournament of up to
three concepts per task (app/acbc_design.py). Optional summed price: base price plus a
component per level, varied at random per concept.

Rule questions between the screening pages (from screen `rules_after` on): levels that appeared
only in rejected concepts are offered as "unacceptable?" (several at once, or "none of these";
at most max_nogo questions), a level present in every possible concept as "must-have?" (one
per question, at most max_must), and with a price once a price limit. Concepts of later
screens that break a confirmed rule are replaced by compliant ones near the BYO profile, or
rejected automatically if there is none; possible concepts that break a rule leave the
tournament. Without any possible concept the BYO profile counts as the winner.

The number of rule questions and tournament tasks depends on the answers, so the item lays out
the largest possible number of pages and skips those not needed (ItemType.expand_pages /
page_applies). plan() replays the path of one participant from the stored answer.

config: attributes, prohibitions, explain, hide_attr_labels, display, scene, show_visual_in_table
        (see attributes.py; levels may carry "price"), screens, per_screen, min_changes,
        max_changes, tour_max, price {on, base, variation (percent), step, currency},
        rules, rules_after, max_nogo, max_must, price_cap
texts:  question (tournament), byo_question, screen_question, possible_label, not_possible_label,
        alt_label, price_label, base_label, nogo_question, nogo_none, must_question, must_yes,
        must_no, price_question, price_none (+ attribute texts, see attributes.py)
answer: {"seed", "k" (concepts per screen), "byo": {attr id: level id}, "byo_filled": [attr ids filled at random
        because the participant left them open], "byo_price", "concepts": [{"p": [level index per
        attribute], "price"}], "s": {concept: 1 | 0}, "t": {round: concept},
        "rules": {"p<s>": {"cap": price | None}, "n<s>": ["aid:lid", ...], "m<s>": {"level": "aid:lid",
        "yes": bool}}, "shown": {screen: {concept: {"p", "price"} | {"auto": 1}}}, "g": {round: [concept, ...]}}
        (s = screen after which the question came; indices as strings; a rule question left open is stored as None:
        no information, unlike "none of these", "no limit" or "no"). "shown" holds per screen page the concepts that
        were shown in place of the stored ones because of a confirmed rule (a replacement, or "auto": rejected and
        not shown; an empty screen entry: all shown as stored), "g" per tournament round the concepts shown together.
        Both are written when the page is saved and win over the replay (plan(), tournament()), so a later change of
        the replay code cannot move judgements already given to other concepts; answers without them (before
        2026-10-22) are still replayed (audit data storage and calculations 2026-10-21, D1).
"""
import itertools
import math
import random
import threading

from app import acbc_design, units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en
from app.items.base import r_dict, r_flag, r_int, r_num, r_one, r_text  # rules for survey files, see base.py
from app.items.attributes import ATTRIBUTE_RULES, AttributeLevels, display_from_form
from app.items.base import AnswerError, ItemType, in_lang, whole, read_cfg
from app.segments import clean as clean_segments  # shown latent class solution and segment names
from app.simulator import clean as clean_scenarios  # saved scenarios of the market simulator
from app.ui_i18n import pick, tr

DISPLAYS = {"table": "Tabelle mit Text", "image": "Ein Bild je Option", "both": "Ein Bild je Option, Tabelle darunter"}
DEFAULT_TEXTS = {
    "de": {"question": "Welche dieser Varianten gefällt Ihnen am besten?",
           "byo_question": "Stellen Sie Ihre bevorzugte Variante zusammen.",
           "screen_question": "Kämen diese Varianten für Sie infrage?",
           "possible_label": "Käme infrage", "not_possible_label": "Kommt nicht infrage",
           "alt_label": "Variante", "price_label": "Preis", "base_label": "Grundpreis",
           "nogo_question": "Wäre eine dieser Eigenschaften für Sie ein Ausschlussgrund? Varianten damit kämen "
                            "für Sie dann überhaupt nicht infrage.",
           "nogo_none": "Keine davon",
           "must_question": "Ist diese Eigenschaft für Sie unverzichtbar? Varianten ohne sie kämen für Sie dann "
                            "überhaupt nicht infrage.",
           "must_yes": "Ja, unverzichtbar", "must_no": "Nein",
           "price_question": "Welchen Preis würden Sie höchstens zahlen?", "price_none": "Keine feste Grenze"},
    "en": {"question": "Which of these options do you like best?",
           "byo_question": "Put together the option you would prefer.",
           "screen_question": "Would these options be a possibility for you?",
           "possible_label": "A possibility", "not_possible_label": "Not a possibility",
           "alt_label": "Option", "price_label": "Price", "base_label": "Base price",
           "nogo_question": "Would any of these features rule an option out for you completely?",
           "nogo_none": "None of these",
           "must_question": "Is this feature a must-have for you? Options without it would be out of the question.",
           "must_yes": "Yes, a must-have", "must_no": "No",
           "price_question": "What is the most you would pay?", "price_none": "No fixed limit"},
}
TEXT_KEYS = ("question", "byo_question", "screen_question", "possible_label", "not_possible_label",
             "alt_label", "price_label", "base_label", "nogo_question", "nogo_none", "must_question",
             "must_yes", "must_no", "price_question", "price_none")
RULE_PHASES = {"price": "p", "nogo": "n", "must": "m"}
MIN_POSSIBLE_FOR_MUST = 2  # a must-have question needs at least this many possible concepts
MAX_NOGO_OPTIONS = 10


def _number(value, default=0.0):
    """A number typed or sent; `default` for anything else, also for nan and inf (audit 2026-10-13, TN-25)."""
    try:
        number = float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


class Plan:
    """The adaptive path of one participant: concepts after replacements (auto = rejected
    automatically, r = replacement), the rule questions shown so far and the rules in force."""

    def __init__(self):
        self.concepts = []
        self.questions = {}  # key ("n1") -> {"phase", "after", "options" | "level"}
        self.nogo, self.must, self.cap = set(), {}, None  # (attr, level); {attr: level}; price


@register
class ACBC(AttributeLevels, ItemType):
    def preview_empty(self, item):
        if read_cfg(item).get("attributes"):
            return None
        return de_en("Die Vorschau erscheint, sobald Merkmale und Stufen eingetragen sind.",
                     "The preview appears as soon as attributes and levels are entered."), "content"

    key = "acbc"
    import_rules = {**ATTRIBUTE_RULES, "screens": r_int(1, 12), "per_screen": r_int(2, 5), "min_changes": r_int(1, 10),
                    "max_changes": r_int(1, 10), "tour_max": r_int(2, 30), "rules": r_flag, "rules_after": r_int(1, 11),
                    "max_nogo": r_int(0, 6), "max_must": r_int(0, 6), "price_cap": r_flag, "display": r_one(*DISPLAYS),
                    "scene": r_text(2000), "show_visual_in_table": r_flag,
                    "price": r_dict(on=r_flag, base=r_num(-1e9, 1e9), variation=r_num(0, 50), step=r_num(0.01, 1e9),
                                    currency=r_text(5)),
                    "lc": clean_segments, "sim": clean_scenarios}

    def source_groups(self, item):
        from app.acbc_design import JOHNSON_ORME_2007

        return [(de_en("Ablauf des adaptiven Conjoint (Konfiguration, Auswahl, Turnier)",
                       "Flow of the adaptive conjoint (configuration, screening, tournament)"), [JOHNSON_ORME_2007])]

    def import_problems(self, item):
        return [] if read_cfg(item)["min_changes"] <= read_cfg(item)["max_changes"] else [de_en("Mindestens mehr Änderungen als höchstens.", "More changes at least than at most.")]
    result_kind = "acbc"  # own summary: counts per level and an aggregate logit (results())
    label = "Adaptive Choice-Based Conjoint (ACBC)"
    short = Both("Adaptive Auswahl in drei Schritten", "Adaptive choice in three steps")
    description = Both("Wunschvariante zusammenstellen, ähnliche Varianten prüfen, Favoriten im Turnier wählen",
                       "Build your own concept, screen similar concepts, choose the favourite in a tournament")
    order = 46
    fact_sheet = FactSheet(
        "choice",
        keywords=["ACBC", "Adaptive conjoint", "Build your own", "Konfigurator", "Configurator"],
        can=[("Wunschvariante zusammenstellen, ähnliche Varianten prüfen, Favoriten im Turnier wählen.",
              "Build your own concept, screen similar concepts, choose the favourite in a tournament."),
             ("Darstellung als Tabelle, als Bild oder als beides.", "Shown as a table, as an image or as both."),
             ("Regeln für „geht nicht“ und „muss“; Preis optional.",
              "Rules for “unacceptable” and “must have”; price optional.")],
        good_for=[("Wahlexperimente mit vielen Merkmalen oder kleinen Stichproben",
                   "Choice experiments with many attributes or small samples")],
        not_for=[("Kurze Befragungen: ACBC dauert länger als ein Conjoint",
                  "Short surveys: ACBC takes longer than a Conjoint")],
        designs=["choice"],
        examples=[("E-Bike mit 7 Merkmalen und Preis", "E-bike with 7 attributes and a price")],
    )
    alone_on_page = True
    allowed_in_loop = allowed_in_separated = False
    editor_tabs = Both([("content", "1 · Inhalt", "Merkmale, Stufen, Erklärung"),
                        ("design", "2 · Ablauf", "Screening, Rückfragen, Turnier, Preis, verbotene Kombinationen"),
                        ("display", "3 · Darstellung & Bilder", "")],
                       [("content", "1 · Content", "Attributes, levels, explanation"),
                        ("design", "2 · Flow", "Screening, follow-up questions, tournament, price, prohibitions"),
                        ("display", "3 · Display & images", "")])
    panel_template = "items/acbc/panel.html"
    design_lock = True  # structure locked once real data exist (ItemType.design_locked)
    long_export = "acbc"
    long_export_label = Both("Person × gezeigte Variante und Rückfrage", "Person × concept shown and follow-up question")
    long_export_note = ("- `acbc_*_long.csv`: long format of the adaptive conjoint (participant × shown concept or "
                        "rule question; phase byo, screen, price_cap, nogo, must or tour; `alt`: position shown on the page, "
                        "empty for a concept left out by the rules and not shown; `<attribute>_value`: the value given to the level "
                        "in the editor, where there is one).")

    # --- defaults and editing ----------------------------------------------------------

    def default_config(self):
        # screens, concepts per screen, changes and the size of the rules are Festlegungen von Askurious: enough concepts
        # near the BYO profile for the tournament, and a short interview
        return {"attributes": [], "screens": 6, "per_screen": 4, "min_changes": 2, "max_changes": 3,
                "tour_max": 12, "display": "table", "scene": "", "show_visual_in_table": False,
                "rules": True, "rules_after": 2, "max_nogo": 3, "max_must": 2, "price_cap": True,
                "price": {"on": False, "base": 0, "variation": 20, "step": 1, "currency": "€"}}

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
            display, level_images = display_from_form(form, DISPLAYS)

            def num(key, lo, hi, default):
                v = form.get(key, "")
                return max(lo, min(hi, whole(v))) if whole(v) is not None else default

            n_attr = max(len(config.get("attributes", [])), 1)
            old_price = config.get("price") or {}
            min_changes = num("min_changes", 1, 10, 2)
            config.update(
                screens=num("screens", 1, 12, 6), per_screen=num("per_screen", 2, 5, 4),
                min_changes=min_changes, max_changes=max(min_changes, num("max_changes", 1, 10, 3)),
                tour_max=num("tour_max", 2, 30, 12),
                rules=bool(form.get("rules")), rules_after=num("rules_after", 1, 11, 2),
                max_nogo=num("max_nogo", 0, 6, 3), max_must=num("max_must", 0, 6, 2),
                price_cap=bool(form.get("price_cap")),
                display=display, level_images=level_images,
                scene=form.get("scene", read_cfg(item).get("scene", "")).strip()[:2000],
                show_visual_in_table=bool(form.get("show_visual_in_table")),
                # in the language of the page with a note on "1,000" in German (units.read_entry; Mathe-Audit
                # 2026-10-06, Z-4: a base price "1.299,99" was 0)
                price={"on": bool(form.get("price_on")),
                       "base": units.read_entry(form.get("price_base"), default=0.0, known=old_price.get("base")),
                       "variation": max(0.0, min(50.0, units.read_entry(form.get("price_variation"), default=20.0,
                                                                        known=old_price.get("variation")))),
                       "step": max(0.01, units.read_entry(form.get("price_step"), default=1.0,
                                                          known=old_price.get("step")) or 1),
                       "currency": (form.get("price_currency") or "€").strip()[:5]},
            )
            config["min_changes"] = min(config["min_changes"], n_attr)
            config["max_changes"] = max(config["min_changes"], min(config["max_changes"], n_attr))
        texts[lang] = lt
        item.config, item.texts = config, texts

    # --- structure -------------------------------------------------------------------------

    def n_concepts(self, item):
        return read_cfg(item).get("screens", 6) * read_cfg(item).get("per_screen", 4)

    def max_tour_tasks(self, item):
        return acbc_design.tournament_tasks(min(read_cfg(item).get("tour_max", 12), self.n_concepts(item)))

    def price_on(self, item):
        return bool((read_cfg(item).get("price") or {}).get("on"))

    def rules_on(self, item):
        return bool(read_cfg(item).get("rules", True))

    def rule_gaps(self, item):
        """Screens (0-based) after which rule questions may come: from rules_after on, not after the last."""
        if not self.rules_on(item):
            return []
        screens = read_cfg(item).get("screens", 6)
        return [s for s in range(screens - 1) if s >= read_cfg(item).get("rules_after", 2) - 1]

    def rules_asked(self, item):
        """True if rule questions can come at all (export: no columns that could never be filled)."""
        return bool(self.rule_gaps(item))

    def rule_phases(self, item):
        c = read_cfg(item)
        return ((["price"] if self.price_on(item) and c.get("price_cap", True) else [])
                + (["nogo"] if c.get("max_nogo", 3) else []) + (["must"] if c.get("max_must", 2) else []))

    def price_components(self, item):
        return [[_number(l.get("price"), 0) for l in a.get("levels", [])] for a in read_cfg(item).get("attributes", [])]

    def format_price(self, item, value, lang):
        p = read_cfg(item).get("price") or {}
        if value is None:
            return ""
        decimals = 0 if float(p.get("step") or 1) >= 1 and float(value).is_integer() else 2
        # half up (kaufmännisch), with the separators of the language: a decimal comma and the currency after the
        # number in every language that writes numbers so, not only in German (fr, es, pl, nl, pt read "€1,299" as
        # 1,30 €; Mathe-Audit 2026-10-06, Z-5)
        comma = not units.writes_like_english(lang)
        text = units.number(value, decimals, "de" if comma else "en")
        currency = p.get("currency") or ""
        return f"{text} {currency}".strip() if comma or len(currency) > 1 else f"{currency}{text}"

    def format_component(self, item, value, lang):
        if not value:
            return ""
        return ("+ " if value > 0 else "− ") + self.format_price(item, abs(value), lang)

    def level_ref(self, item, attr, level):
        a = read_cfg(item)["attributes"][attr]
        return f"{a['id']}:{a['levels'][level]['id']}"

    def parse_ref(self, item, ref):
        """(attr index, level index) for "aid:lid", or None."""
        try:
            aid, lid = str(ref).split(":")
        except ValueError:
            return None
        for n, a in enumerate(read_cfg(item).get("attributes", [])):
            if a["id"] == aid:
                ids = [l["id"] for l in a.get("levels", [])]
                return (n, ids.index(lid)) if lid in ids else None
        return None

    def level_text(self, item, ref, lang, variables=None):
        """"Attribute: level" for a (attr, level) pair."""
        attrs = self.attributes(item, lang)
        a, lv = ref
        return f"{attrs[a]['label']}: {attrs[a]['level_labels'][lv]}"

    # --- pages -----------------------------------------------------------------------------

    def page_specs(self, item):
        """All pages the item may need, in order; rule and tournament pages are "dyn"."""
        c = read_cfg(item)
        specs = [{"explain": True}] if c.get("explain") else []
        specs.append({"task": 0, "phase": "byo"})
        task = 1
        gaps = set(self.rule_gaps(item))
        for s in range(c.get("screens", 6)):
            specs.append({"task": task, "phase": "screen", "screen": s})
            task += 1
            if s in gaps:
                for phase in self.rule_phases(item):
                    specs.append({"task": task, "phase": phase, "after": s, "dyn": True})
                    task += 1
        for r in range(self.max_tour_tasks(item)):
            specs.append({"task": task, "phase": "tour", "round": r, "dyn": True})
            task += 1
        return specs

    def expand_pages(self, survey, item, rng):
        if not read_cfg(item).get("attributes"):
            return None
        seed = rng.randrange(2 ** 31)
        specs = [{**spec, "seed": seed} if spec.get("phase") == "byo" else spec for spec in self.page_specs(item)]
        return {"seed": seed}, specs

    _seen = threading.local()  # the page entry and stored answer of the last page_applies() call

    def page_applies(self, item, spec, answers):
        phase = spec.get("phase")
        answer = answers.get(item.id)
        self._seen.page = (spec, answer)  # for parse_answer, see stored_answer()
        if phase in RULE_PHASES:
            return f"{RULE_PHASES[phase]}{spec['after']}" in self.plan(item, answer).questions
        if phase == "tour":
            rounds, _, _ = self.tournament(item, answer)
            return any(r == spec["round"] for r, _, _ in rounds)
        return True

    def stored_answer(self, meta):
        """The participant's stored answer while a rule or tournament page is parsed, or None if unknown.
        parse_answer gets only the page entry; the runtime asks page_applies() for this very entry (the
        same object) just before it parses the page, so what was seen there is the answer the page was
        built from. Unknown (direct calls, other pages): nothing can be checked against the offer."""
        spec, answer = getattr(self._seen, "page", (None, None))
        page = meta.get("_page", meta) if isinstance(meta, dict) else meta  # the runtime adds the pid to a copy
        return answer if page is not None and spec is page and isinstance(answer, dict) else None

    def timing_pages(self, item):
        labels = {"price": "Preisgrenze", "nogo": "Ausschlussgründe", "must": "Unverzichtbar"}
        out = []
        for spec in self.page_specs(item):
            phase = spec.get("phase")
            if spec.get("explain"):
                out.append(("_x", "Erklärungsseite", ".x"))
            elif phase == "byo":
                out.append(("_byo", "Wunschvariante", ".t0"))
            elif phase == "screen":
                out.append((f"_s{spec['screen'] + 1}", f"Screening {spec['screen'] + 1}", f".t{spec['task']}"))
            elif phase in RULE_PHASES:
                out.append((f"_{RULE_PHASES[phase]}{spec['after'] + 1}",
                             f"{labels[phase]} nach Screening {spec['after'] + 1}", f".t{spec['task']}"))
            else:
                out.append((f"_r{spec['round'] + 1}", f"Turnier {spec['round'] + 1}", f".t{spec['task']}"))
        return out

    def seconds(self, item, read):
        c = read_cfg(item)
        n_attr = len(c.get("attributes", []))
        byo = 4 * n_attr
        screening = self.n_concepts(item) * (2 + 0.3 * n_attr)
        rules = 8 * min(len(self.rule_gaps(item)), c.get("max_nogo", 3) + c.get("max_must", 2))
        # typically about half of the concepts are "possible"
        tour = acbc_design.tournament_tasks(min(c.get("tour_max", 12), self.n_concepts(item) // 2)) * (8 + 0.5 * n_attr * 3)
        return read + byo + screening + rules + tour

    def screen_concepts(self, item, screen):
        k = read_cfg(item).get("per_screen", 4)
        return list(range(screen * k, (screen + 1) * k))

    # --- concepts --------------------------------------------------------------------------

    def make_concepts(self, item, byo_profile, seed):
        """The screening concepts for a BYO profile, with prices if the summed price is on."""
        c = read_cfg(item)
        profiles = acbc_design.screening_concepts(
            self.level_counts(item), byo_profile, self.n_concepts(item), c.get("min_changes", 2),
            c.get("max_changes", 3), forbidden=self.prohibitions(item), seed=seed)
        concepts = [{"p": p} for p in profiles]
        if self.price_on(item):
            p = c.get("price") or {}
            rng = random.Random(seed + 2)
            comps = self.price_components(item)
            for concept in concepts:
                concept["price"] = acbc_design.summed_price(comps, concept["p"], _number(p.get("base")),
                                                            _number(p.get("variation")) / 100, _number(p.get("step"), 1), rng)
        return concepts

    def byo_price(self, item, profile):
        p = read_cfg(item).get("price") or {}
        return acbc_design.summed_price(self.price_components(item), profile, _number(p.get("base")),
                                        step=_number(p.get("step"), 1))

    def byo_profile(self, item, answer):
        byo = (answer or {}).get("byo") or {}
        out = []
        for a in read_cfg(item).get("attributes", []):
            ids = [l["id"] for l in a.get("levels", [])]
            if byo.get(a["id"]) not in ids:
                return None
            out.append(ids.index(byo[a["id"]]))
        return out

    def violates(self, concept, plan):
        p = concept["p"]
        return (any((a, lv) in plan.nogo for a, lv in enumerate(p))
                or any(p[a] != lv for a, lv in plan.must.items())
                or (plan.cap is not None and concept.get("price") is not None and concept["price"] > plan.cap))

    def replacement(self, item, answer, index, plan, taken):
        """A concept for position `index` that keeps the rules, near the BYO profile; None if none."""
        c = read_cfg(item)
        levels = self.level_counts(item)
        byo = self.byo_profile(item, answer) or [0] * len(levels)
        allowed = [[lv for lv in range(n) if (a, lv) not in plan.nogo] for a, n in enumerate(levels)]
        base = [plan.must.get(a, byo[a] if byo[a] in allowed[a] else (allowed[a] or [byo[a]])[0])
                for a in range(len(levels))]
        changeable = [a for a in range(len(levels)) if a not in plan.must and len(allowed[a]) > 1]
        lo = min(c.get("min_changes", 2), len(changeable))
        hi = max(lo, min(c.get("max_changes", 3), len(changeable)))
        ok = acbc_design.allowed_fn(self.prohibitions(item))
        p = c.get("price") or {}
        seed = (answer or {}).get("seed") or 0
        rng = random.Random(seed * 1000003 + index * 7919 + 17)
        for attempt in range(200):
            cand = list(base)
            for a in rng.sample(changeable, rng.randint(lo, hi)) if changeable else []:
                options = [lv for lv in allowed[a] if lv != base[a]]
                if options:
                    cand[a] = rng.choice(options)
            if cand == byo or tuple(cand) in taken or not ok(cand):
                continue
            concept = {"p": cand, "r": True}
            if self.price_on(item):
                concept["price"] = acbc_design.summed_price(
                    self.price_components(item), cand, _number(p.get("base")), _number(p.get("variation")) / 100,
                    _number(p.get("step"), 1), random.Random(seed * 31 + index * 101 + attempt))
            if not self.violates(concept, plan):
                return concept
        return None

    def plan(self, item, answer):
        """Replay the adaptive path of one participant (see Plan), screen by screen: the concepts
        of a screen under the rules confirmed before it, then the rule questions after it."""
        answer = answer or {}
        plan = Plan()
        plan.concepts = [dict(x) for x in answer.get("concepts") or []]
        if not plan.concepts:
            return plan
        c = read_cfg(item)
        s_answers = answer.get("s") or {}
        stored = answer.get("rules") or {}
        gaps = set(self.rule_gaps(item))
        phases = self.rule_phases(item)
        byo = self.byo_profile(item, answer) or []
        asked = {"nogo": 0, "must": 0, "price": 0}
        offered, must_asked = set(), set()
        recorded = answer.get("shown") if isinstance(answer.get("shown"), dict) else {}
        for s in range(c.get("screens", 6)):
            indices = [i for i in self.screen_concepts(item, s) if i < len(plan.concepts)]
            shown = self._recorded_screen(item, recorded.get(str(s)), indices, plan.concepts)
            if shown is not None:  # what the page showed, written when it was saved (D1)
                for i in indices:
                    plan.concepts[i] = shown.get(i, plan.concepts[i])
            else:  # answers from before: replay
                taken = {tuple(x["p"]) for x in plan.concepts}
                for i in indices:
                    if self.violates(plan.concepts[i], plan):
                        new = self.replacement(item, answer, i, plan, taken)
                        plan.concepts[i] = new or {**plan.concepts[i], "auto": True}
                        taken.add(tuple(plan.concepts[i]["p"]))
            if s not in gaps:
                continue
            upto = [i for i in range(min((s + 1) * c.get("per_screen", 4), len(plan.concepts)))]
            if any(str(i) not in s_answers for i in upto):
                break  # this screen is not answered yet: nothing after it is known
            shown = [plan.concepts[i] for i in upto if not plan.concepts[i].get("auto")]
            possible = [plan.concepts[i] for i in upto if not plan.concepts[i].get("auto") and s_answers.get(str(i)) == 1]
            pending = False
            for phase in phases:
                key = f"{RULE_PHASES[phase]}{s}"
                if phase == "price":
                    options = self.cap_options(item, shown) if not asked["price"] else []
                    if not options:
                        continue
                    asked["price"] += 1
                    plan.questions[key] = {"phase": phase, "after": s, "options": options}
                    if key in stored:
                        plan.cap = (stored[key] or {}).get("cap")
                    else:
                        pending = True
                elif phase == "nogo":
                    if asked["nogo"] >= c.get("max_nogo", 3):
                        continue
                    seen = {(a, lv) for x in shown for a, lv in enumerate(x["p"])}
                    liked = {(a, lv) for x in possible for a, lv in enumerate(x["p"])}
                    options = sorted(seen - liked - set(enumerate(byo)) - plan.nogo - offered
                                     - {(a, lv) for a, lv in seen if a in plan.must})[:MAX_NOGO_OPTIONS]
                    if not options:
                        continue
                    asked["nogo"] += 1
                    offered |= set(options)
                    plan.questions[key] = {"phase": phase, "after": s, "options": options}
                    if key in stored:
                        plan.nogo |= {r for r in (self.parse_ref(item, x) for x in stored[key] or []) if r}
                    else:
                        pending = True
                elif phase == "must":
                    if asked["must"] >= c.get("max_must", 2) or len(possible) < MIN_POSSIBLE_FOR_MUST:
                        continue
                    level = None
                    for a in range(len(c.get("attributes", []))):
                        values = {x["p"][a] for x in possible}
                        if a in plan.must or len(values) != 1:
                            continue
                        lv = values.pop()
                        if (a, lv) in must_asked or (a, lv) in plan.nogo or all(x["p"][a] == lv for x in shown):
                            continue  # asked before, or the attribute never varied
                        level = (a, lv)
                        break
                    if level is None:
                        continue
                    asked["must"] += 1
                    must_asked.add(level)
                    plan.questions[key] = {"phase": phase, "after": s, "level": level}
                    if key in stored:
                        if (stored[key] or {}).get("yes"):
                            # the level the page asked for, as stored with the answer; the replayed one otherwise
                            confirmed = self.parse_ref(item, (stored[key] or {}).get("level")) or level
                            plan.must[confirmed[0]] = confirmed[1]
                    else:
                        pending = True
            if pending:
                break  # later screens depend on the open question
        return plan

    @staticmethod
    def shown_entry(concept):
        """How a concept shown in place of the stored one is written into the answer ("shown")."""
        if concept.get("auto"):
            return {"auto": 1}
        return {"p": list(concept["p"]), **({"price": concept["price"]} if concept.get("price") is not None else {})}

    def _recorded_screen(self, item, entry, indices, concepts):
        """{concept index: concept} of one screen as written when its page was saved, or None if there is no
        usable entry (answers from before, or a damaged one): then the screen is replayed."""
        if not isinstance(entry, dict):
            return None
        levels = self.level_counts(item)
        out = {}
        for key, value in entry.items():
            i = whole(key)
            if i is None or i not in indices or not isinstance(value, dict):
                return None
            if value.get("auto"):
                out[i] = {**concepts[i], "auto": True}
                continue
            p = value.get("p")
            if (not isinstance(p, list) or len(p) != len(levels)
                    or not all(isinstance(lv, int) and 0 <= lv < n for lv, n in zip(p, levels))):
                return None
            out[i] = {"p": list(p), "r": True, **({"price": value["price"]} if value.get("price") is not None else {})}
        return out

    def _recorded_groups(self, answer):
        """{round: [concept, ...]} of the tournament groups written when the pages were saved (D1)."""
        n = len((answer or {}).get("concepts") or [])
        out = {}
        for key, group in ((answer or {}).get("g") or {}).items():
            r = whole(key)
            if (r is None or not isinstance(group, list) or not 2 <= len(group) <= 3 or len(set(group)) != len(group)
                    or not all(isinstance(x, int) and 0 <= x < n for x in group)):
                continue
            out[r] = list(group)
        return out

    def cap_options(self, item, shown):
        """Price limits to choose from: evenly between the lowest and the highest price shown."""
        prices = [x["price"] for x in shown if x.get("price") is not None]
        if len(set(prices)) < 2:
            return []
        lo, hi = min(prices), max(prices)
        step = _number((read_cfg(item).get("price") or {}).get("step"), 1) or 1
        return sorted({round(round((lo + (hi - lo) * q) / step) * step, 2) for q in (0.2, 0.4, 0.6, 0.8)})

    def possible(self, item, answer, plan=None):
        """Concept indices marked "possible" that keep all confirmed rules (the tournament field)."""
        plan = plan or self.plan(item, answer)
        s = (answer or {}).get("s") or {}
        return [i for i, x in enumerate(plan.concepts)
                if s.get(str(i)) == 1 and not x.get("auto") and not self.violates(x, plan)]

    def tournament(self, item, answer, plan=None):
        """(rounds, queue, winner) for the stored answer, see acbc_design.tournament."""
        answer = answer or {}
        pool = acbc_design.tournament_pool(self.possible(item, answer, plan), read_cfg(item).get("tour_max", 12),
                                           seed=(answer.get("seed") or 0) + 1)
        choices = {int(r): c for r, c in (answer.get("t") or {}).items()}
        return acbc_design.tournament(pool, choices, self._recorded_groups(answer))

    def tour_total(self, item, answer):
        """Number of tournament tasks for this participant (known once the screening is done)."""
        pool = acbc_design.tournament_pool(self.possible(item, answer), read_cfg(item).get("tour_max", 12))
        return acbc_design.tournament_tasks(len(pool))

    def tour_group(self, item, answer, round_no):
        for r, group, _ in self.tournament(item, answer)[0]:
            if r == round_no:
                return group
        return []

    def screened(self, item, answer, plan=None):
        """True once every concept that was not ruled out automatically has a judgement (left open counts as none)."""
        plan = plan or self.plan(item, answer)
        s = (answer or {}).get("s") or {}
        return bool(s) and all(s.get(str(i)) is not None for i, x in enumerate(plan.concepts) if not x.get("auto"))

    def winner(self, item, answer):
        """(concept dict or None, source): the tournament winner ("tour"), the only possible concept
        ("single"), or the BYO profile if no concept was possible ("byo"); None while undecided."""
        answer = answer or {}
        if not answer.get("s"):
            return None, None
        plan = self.plan(item, answer)
        rounds, queue, win = self.tournament(item, answer, plan)
        if win is not None and rounds:
            return plan.concepts[win], "tour"
        if not self.screened(item, answer, plan):
            return None, None  # "the only one" or "none possible" needs a judgement on every concept
        if win is not None:
            return plan.concepts[win], "single"
        if not queue and self.byo_profile(item, answer):
            return {"p": self.byo_profile(item, answer), "price": answer.get("byo_price")}, "byo"
        return None, None

    # --- images ----------------------------------------------------------------------------

    def required_keys(self, item, variable_rows=None):
        """Every allowed combination of the visual levels (the concepts are only known per person)."""
        if not self.uses_images(item) or not self.visual_attributes(item):
            return []
        attrs = read_cfg(item).get("attributes", [])
        visual = [n for n, a in enumerate(attrs) if a.get("visual")]
        ok = acbc_design.allowed_fn([p for p in self.prohibitions(item)
                                     if p[0][0] in visual and p[1][0] in visual])
        keys = []
        for combo in itertools.product(*[range(len(attrs[n]["levels"])) for n in visual]):
            profile = [0] * len(attrs)
            for n, lv in zip(visual, combo):
                profile[n] = lv
            if ok(profile):
                keys.append(self.image_key(item, profile))
                if len(keys) > 500:
                    break
        return sorted(keys)

    # --- checks ----------------------------------------------------------------------------

    def validate(self, item):
        problems = self.validate_attributes(item)
        if read_cfg(item).get("attributes") and not problems:
            from app.conjoint_design import n_allowed_profiles

            available = n_allowed_profiles(self.level_counts(item), self.prohibitions(item)) - 1
            if available < self.n_concepts(item):
                problems.append(de_en("Zu wenige verschiedene Varianten: {n} Varianten im Screening, aber nur {k} möglich; "
                                      "weniger Screening-Seiten oder mehr Stufen",
                                      "Too few different concepts: {n} concepts in the screening, but only {k} possible; "
                                      "fewer screening pages or more levels", n=self.n_concepts(item), k=max(available, 0)))
        c = read_cfg(item)
        if self.rules_on(item) and self.rule_phases(item) and not self.rule_gaps(item):
            problems.append(de_en("Rückfragen erscheinen nie: sie kommen zwischen den Screening-Seiten, „ab Screening-Seite“ "
                                  "({after}) muss daher kleiner sein als die Zahl der Screening-Seiten ({screens}); Wert "
                                  "senken oder Rückfragen abschalten",
                                  "Follow-up questions never appear: they come between the screening pages, so “from "
                                  "screening page” ({after}) must be smaller than the number of screening pages ({screens}); "
                                  "lower the value or switch off follow-up questions",
                                  after=c.get('rules_after', 2), screens=c.get('screens', 6)))
        return problems + self.validate_images(item)

    # --- answers -------------------------------------------------------------------------

    uses_answers = True  # the stored answer, to write down which concepts a screening page showed (D1)

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        meta = meta or {}
        if meta.get("explain"):
            return {"explained": True}
        phase = meta.get("phase")
        if phase == "byo":
            attrs = read_cfg(item).get("attributes", [])
            byo, profile, missing = {}, [], False
            for a in attrs:
                lid = form.get(f"i{item.id}_byo_{a['id']}", "")
                ids = [l["id"] for l in a.get("levels", [])]
                if lid and lid not in ids:
                    raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
                if not lid:
                    missing = True
                byo[a["id"]] = lid or None
                profile.append(ids.index(lid) if lid else None)
            filled = [a["id"] for a, p in zip(attrs, profile) if p is None]
            if missing:
                if item.required:
                    raise AnswerError(tr(lang, "Bitte wählen Sie für jedes Merkmal eine Ausprägung.", "Please choose one level for every feature."))
                # optional (and nudge after the hint): open features are filled at random, so the screening
                # can still follow; "byo_filled" records them (is_missing: the nudge; export: _byo_filled)
                rng = random.Random(meta.get("seed", 0))
                profile = [p if p is not None else rng.randrange(len(a["levels"])) for p, a in zip(profile, attrs)]
                byo = {a["id"]: a["levels"][p]["id"] for a, p in zip(attrs, profile)}
            seed = int(meta.get("seed", 0))
            answer = {"seed": seed, "k": read_cfg(item).get("per_screen", 4), "byo": byo, "byo_filled": filled,
                      "concepts": self.make_concepts(item, profile, seed)}
            if self.price_on(item):
                answer["byo_price"] = self.byo_price(item, profile)
            return answer
        if phase == "screen":
            s, missing = {}, False
            for c in self.screen_concepts(item, meta.get("screen", 0)):
                raw = form.get(f"i{item.id}_s{c}", "")
                if raw not in ("", "0", "1", "a"):  # "a": rejected automatically (breaks a rule), not shown
                    raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
                s[str(c)] = None if raw == "" else 1 if raw == "1" else 0
                missing |= raw == ""
            if missing and item.required:
                raise AnswerError(tr(lang, "Bitte beurteilen Sie jede Variante.", "Please rate every option."))
            out = {"s": s, "_screen": meta.get("screen", 0)}
            # the answer the page was built from: write down what it showed (D1)
            stored = (answers or {}).get(item.id) if isinstance(answers, dict) else None
            if isinstance(stored, dict) and stored.get("concepts"):
                concepts = self.plan(item, stored).concepts
                out["shown"] = {str(meta.get("screen", 0)): {
                    str(i): self.shown_entry(concepts[i]) for i in self.screen_concepts(item, meta.get("screen", 0))
                    if i < len(concepts) and (concepts[i].get("r") or concepts[i].get("auto"))}}
            return out
        if phase in RULE_PHASES:
            key = f"{RULE_PHASES[phase]}{meta.get('after', 0)}"
            # what this page offered (manipulated or outdated forms may send something else); None: unknown
            stored = self.stored_answer(meta)
            q = self.plan(item, stored).questions.get(key) if stored is not None else None
            if phase == "price":
                raw = form.get(f"i{item.id}_pc", "")
                if raw == "":
                    if item.required:
                        raise AnswerError(tr(lang, "Bitte wählen Sie eine Antwort.", "Please choose an answer."))
                    return {"rules": {key: None}, "_after": meta.get("after", 0)}  # left open
                value = None if raw in ("", "none") else _number(raw, None)
                if raw not in ("", "none") and value is None:
                    raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
                # a price limit only out of what this page offered; without a known page none (TN-25)
                if value is not None and (not q or not any(abs(value - o) < 1e-6 for o in q["options"])):
                    raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
                return {"rules": {key: {"cap": value}}, "_after": meta.get("after", 0)}
            if phase == "nogo":
                values = [v for v in form.getlist(f"i{item.id}_ng") if v]
                if not values:
                    if item.required:
                        raise AnswerError(tr(lang, "Bitte wählen Sie mindestens eine Antwort oder „Keine davon“.", "Please choose at least one answer or “None of these”."))
                    return {"rules": {key: None}, "_after": meta.get("after", 0)}  # left open
                # each level once: the same one sent a million times would be stored a million times, and
                # every later page of this participation reads the list again
                refs = list(dict.fromkeys(v for v in values if v != "none"))[:MAX_NOGO_OPTIONS]
                if any(self.parse_ref(item, v) is None for v in refs):
                    raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
                if q and any(self.parse_ref(item, v) not in [tuple(o) for o in q["options"]] for v in refs):
                    raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
                return {"rules": {key: refs}, "_after": meta.get("after", 0)}
            raw, level = form.get(f"i{item.id}_mh", ""), form.get(f"i{item.id}_ml", "")
            if self.parse_ref(item, level) is None or (q and self.parse_ref(item, level) != tuple(q["level"])):
                raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
            if raw == "":
                if item.required:
                    raise AnswerError(tr(lang, "Bitte wählen Sie eine Antwort.", "Please choose an answer."))
                return {"rules": {key: None}, "_after": meta.get("after", 0)}  # left open
            return {"rules": {key: {"level": level, "yes": raw == "1"}}, "_after": meta.get("after", 0)}
        if phase == "tour":
            raw = form.get(f"i{item.id}_r{meta.get('round', 0)}", "")
            stored = self.stored_answer(meta)
            group = self.tour_group(item, stored, meta.get("round", 0)) if stored is not None else []
            shown = {"g": {str(meta.get("round", 0)): group}} if group else {}  # the concepts shown together (D1)
            if raw == "":
                if item.required:
                    raise AnswerError(tr(lang, "Bitte wählen Sie eine Variante.", "Please choose an option."))
                return {"t": {str(meta.get("round", 0)): None}, **shown}
            # the radio carries the concept number (1-based, like the columns); stored 0-based
            picked = whole(raw)
            if picked is None or not 1 <= picked <= self.n_concepts(item):
                raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
            if stored is not None and picked - 1 not in group:  # only a concept of this task may win it
                raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
            return {"t": {str(meta.get("round", 0)): picked - 1}, **shown}
        return None

    def display_value(self, item, form, meta=None):
        meta = meta or {}
        phase = meta.get("phase")
        if phase == "byo":
            return {"byo": {a["id"]: form.get(f"i{item.id}_byo_{a['id']}") or None
                            for a in read_cfg(item).get("attributes", [])}}
        if phase == "screen":
            return {"s": {str(c): int(form.get(f"i{item.id}_s{c}")) if form.get(f"i{item.id}_s{c}") in ("0", "1") else None
                          for c in self.screen_concepts(item, meta.get("screen", 0))}}
        if phase == "tour":
            raw = form.get(f"i{item.id}_r{meta.get('round', 0)}", "")
            return {"t": {str(meta.get("round", 0)): whole(raw) - 1 if whole(raw) else None}}
        return None  # rule pages: the stored answer is shown again

    def merge_answer(self, old, new):
        """Answers of later steps depend on earlier ones: a new BYO profile starts over (new
        concepts); a changed screening answer or rule drops the rules after it and the
        tournament; a changed rule also drops the screening after it, and so does a changed
        screening answer if rules after it were answered (without them the later screens may
        show other concepts, and the judgements are stored per position); a changed tournament
        choice drops the later rounds."""
        old, new = dict(old or {}), dict(new or {})
        screen, after = new.pop("_screen", None), new.pop("_after", None)
        old.pop("_screen", None), old.pop("_after", None)
        if "byo" in new:
            if "concepts" not in new:  # display only (failed check): keep the rest for the page
                return {**old, "byo": new["byo"]}
            if old.get("byo") == new["byo"] and old.get("concepts") == new.get("concepts"):
                return {**old, **new}
            return new
        out = {**old}

        def drop_rules_from(first):  # rules asked after screen `first` or later; True if there were any
            rules = {k: v for k, v in (out.get("rules") or {}).items() if int(k[1:]) < first}
            dropped = len(rules) < len(out.get("rules") or {})
            if rules or "rules" in out:
                out["rules"] = rules
            return dropped

        def drop_screens_after(last):  # screening after screen `last` may have shown other concepts
            first = (last + 1) * self._per_screen_hint(out)
            out["s"] = {k: v for k, v in (out.get("s") or {}).items() if int(k) < first}
            if "shown" in out:
                out["shown"] = {k: v for k, v in (out.get("shown") or {}).items()
                                if whole(k) is not None and whole(k) <= last}

        def drop_tournament():
            out.pop("t", None)
            out.pop("g", None)

        if "s" in new:
            if any((old.get("s") or {}).get(k) != v for k, v in new["s"].items()):
                drop_tournament()
                if screen is not None and drop_rules_from(screen) and out.get("s"):
                    drop_screens_after(screen)
            out["s"] = {**(out.get("s") or {}), **new["s"]}
        if "rules" in new:
            (key, value), = new["rules"].items()
            if (old.get("rules") or {}).get(key, object()) != value:
                drop_tournament()
                drop_rules_from(int(key[1:]) + 1)
                if after is not None and out.get("s"):
                    drop_screens_after(after)
            out["rules"] = {**(out.get("rules") or {}), key: value}
        if "shown" in new:
            out["shown"] = {**(out.get("shown") or {}), **new["shown"]}
        if "t" in new:
            t, g = dict(old.get("t") or {}), dict(old.get("g") or {})
            for r, v in new["t"].items():
                if t.get(r) != v:
                    t = {k: x for k, x in t.items() if int(k) < int(r)}
                    g = {k: x for k, x in g.items() if whole(k) is not None and whole(k) < int(r)}
                t[r] = v
            out["t"] = t
            g.update(new.get("g") or {})
            if g:
                out["g"] = g
            else:
                out.pop("g", None)
        for k, v in new.items():
            if k not in ("s", "t", "rules", "shown", "g"):
                out[k] = v
        return out

    @staticmethod
    def _per_screen_hint(answer):
        """Concepts per screen, stored with the concepts ("k"), since merge_answer has no item."""
        return answer.get("k") or 1

    def is_missing(self, item, answer):
        answer = answer or {}
        if answer == {"explained": True}:
            return False  # the explanation page alone; stored with the steps it hides nothing (code review 2026-10-15, F4)
        if "byo" in answer:  # open levels, also those filled at random
            return any(v is None for v in answer["byo"].values()) or bool(answer.get("byo_filled"))
        if "s" in answer:
            return any(v is None for v in answer["s"].values())
        if "t" in answer:
            return any(v is None for v in answer["t"].values())
        if "rules" in answer:
            # "none of these", "no limit" and "no" are answers; a question left open is stored as None
            return any(v is None for v in answer["rules"].values())
        return True

    def dummy_fields(self, item, rng, meta, answers=None):
        """Wish profile, screening and tournament from a random utility model (as the CBC dummies, items/conjoint.py;
        audit 2026-10-05, M-14): the level with the highest utility (price components included), "possible" with
        a logistic probability around the person's mean utility of the concepts, the best concept of a round.
        The rule questions stay random."""
        import math

        from app.items.conjoint import dummy_person, gumbel

        meta = meta or {}
        phase = meta.get("phase")
        answer = (answers or {}).get(item.id)
        prices = self.price_components(item) if self.price_on(item) else None
        person = dummy_person(self, item, rng, meta.get("respondent"), prices or [])

        def utility(concept):
            u = sum(person["pw"][a][lv] for a, lv in enumerate(concept.get("p") or []) if lv is not None)
            return u + person["price"] * float(concept.get("price") or 0)

        if phase == "byo":
            out = {}
            for a, attr in enumerate(read_cfg(item).get("attributes", [])):
                levels = range(len(attr["levels"]))
                best = max(levels, key=lambda lv: person["pw"][a][lv] + person["price"] * (prices[a][lv] if prices else 0)
                           + gumbel(rng))
                out[f"i{item.id}_byo_{attr['id']}"] = attr["levels"][best]["id"]
            return out
        if phase == "screen":
            plan = self.plan(item, answer)
            shown = [utility(x) for x in plan.concepts if not x.get("auto")]
            middle = sum(shown) / len(shown) if shown else 0.0
            out = {}
            for c in self.screen_concepts(item, meta.get("screen", 0)):
                if c < len(plan.concepts) and plan.concepts[c].get("auto"):
                    out[f"i{item.id}_s{c}"] = "a"
                else:
                    u = utility(plan.concepts[c]) - middle + 0.5 if c < len(plan.concepts) else 0.0
                    out[f"i{item.id}_s{c}"] = "1" if rng.random() < 1 / (1 + math.exp(-u)) else "0"
            return out
        if phase in RULE_PHASES:
            q = self.plan(item, answer).questions.get(f"{RULE_PHASES[phase]}{meta.get('after', 0)}")
            if not q:
                return {}
            if phase == "price":
                return {f"i{item.id}_pc": str(rng.choice(q["options"] + ["none", "none"]))}
            if phase == "nogo":
                picked = [self.level_ref(item, *o) for o in q["options"] if rng.random() < 0.2]
                return {f"i{item.id}_ng": picked or ["none"]}
            return {f"i{item.id}_mh": "1" if rng.random() < 0.3 else "0", f"i{item.id}_ml": self.level_ref(item, *q["level"])}
        if phase == "tour":
            group = self.tour_group(item, answer, meta.get("round", 0))
            if not group:
                return {}
            plan = self.plan(item, answer)
            best = max(group, key=lambda c: utility(plan.concepts[c]) + gumbel(rng))
            return {f"i{item.id}_r{meta.get('round', 0)}": str(best + 1)}
        return {}

    # --- export ----------------------------------------------------------------------------

    levels = ("nominal",)
    METRIC_COLUMNS = ("_byo_price", "_byo_filled", "_price_cap", "_n_possible", "_n_auto", "_n_tour", "_win_price")

    def column_level(self, item, column):
        # levels, no-go and must-have answers and the winner's source are codes; prices and counts are metric
        return "metric" if column.endswith(self.METRIC_COLUMNS) else "nominal"

    def export_columns(self, item):
        attrs = read_cfg(item).get("attributes", [])
        price = self.price_on(item)
        cols = [(f"{item.code}_byo_{a['code']}", f"Wunschvariante: {a['code']} (Stufe)") for a in attrs]
        if price:
            cols.append((f"{item.code}_byo_price", "Wunschvariante: Preis"))
        cols.append((f"{item.code}_byo_filled", "Wunschvariante: Zahl der offen gelassenen, zufällig gefüllten Merkmale"))
        if self.rules_asked(item):
            if "price" in self.rule_phases(item):
                cols += [(f"{item.code}_price_cap", f"Preisgrenze (leer: keine Grenze oder nicht gefragt, siehe {item.code}_price_cap_set)"),
                         (f"{item.code}_price_cap_set", "Preisgrenze: 1 = genannt, 0 = keine feste Grenze (leer: nicht gefragt)")]
            if read_cfg(item).get("max_nogo", 3):
                cols += [(f"{item.code}_nogo_{a['code']}_{n + 1}",
                          f"Ausschlussgrund {a['code']} Stufe {n + 1} (1 = ja, 0 = angeboten, nicht gewählt)")
                         for a in attrs for n in range(len(a.get("levels", [])))]
            if read_cfg(item).get("max_must", 2):
                cols += [(f"{item.code}_must_{a['code']}", f"Unverzichtbar: {a['code']} (Stufe; 0 = gefragt, verneint)")
                         for a in attrs]
        cols += [(f"{item.code}_n_possible", "Screening: Zahl der Varianten „kommt infrage“ (nach den Regeln)"),
                 (f"{item.code}_n_auto", "Screening: Zahl der Varianten, die wegen der Regeln entfielen"),
                 (f"{item.code}_n_tour", "Turnier: Zahl der Aufgaben"),
                 (f"{item.code}_win_source", "Gewinner aus: tour, single (nur eine infrage), byo (keine infrage)")]
        cols += [(f"{item.code}_win_{a['code']}", f"Gewinner: {a['code']} (Stufe)") for a in attrs]
        if price:
            cols.append((f"{item.code}_win_price", "Gewinner: Preis"))
        return cols

    def export_values(self, item, answer):
        answer = answer if isinstance(answer, dict) else {}
        attrs = read_cfg(item).get("attributes", [])
        code = item.code
        out = {}
        byo = answer.get("byo") or {}
        for a in attrs:
            ids = [l["id"] for l in a.get("levels", [])]
            out[f"{code}_byo_{a['code']}"] = ids.index(byo[a["id"]]) + 1 if byo.get(a["id"]) in ids else None
        if self.price_on(item):
            out[f"{code}_byo_price"] = answer.get("byo_price")
        out[f"{code}_byo_filled"] = len(answer.get("byo_filled") or []) if byo else None
        plan = self.plan(item, answer)
        if self.rules_asked(item):
            stored = answer.get("rules") or {}
            # questions answered (not left open, not pending): only these give 0 = offered / asked, not chosen
            answered = {k: q for k, q in plan.questions.items() if stored.get(k) is not None}
            if "price" in self.rule_phases(item):
                caps = [stored[k].get("cap") for k, q in answered.items() if q["phase"] == "price"]
                out[f"{code}_price_cap"] = caps[0] if caps else None
                # "no fixed limit" (asked, cap None) is not the same as never asked: both leave the cap empty
                out[f"{code}_price_cap_set"] = (0 if caps[0] is None else 1) if caps else None
            if read_cfg(item).get("max_nogo", 3):
                offered = {o for q in answered.values() if q["phase"] == "nogo" for o in q["options"]}
                for n_a, a in enumerate(attrs):
                    for n in range(len(a.get("levels", []))):
                        ref = (n_a, n)
                        out[f"{code}_nogo_{a['code']}_{n + 1}"] = 1 if ref in plan.nogo else 0 if ref in offered else None
            if read_cfg(item).get("max_must", 2):
                asked = {q["level"][0] for q in answered.values() if q["phase"] == "must"}
                for n_a, a in enumerate(attrs):
                    out[f"{code}_must_{a['code']}"] = plan.must[n_a] + 1 if n_a in plan.must else 0 if n_a in asked else None
        screened = self.screened(item, answer, plan)  # counts only of a finished screening
        rounds, _, _ = self.tournament(item, answer, plan)
        out[f"{code}_n_possible"] = len(self.possible(item, answer, plan)) if screened else None
        out[f"{code}_n_auto"] = sum(1 for x in plan.concepts if x.get("auto")) if screened else None
        out[f"{code}_n_tour"] = sum(1 for _, _, chosen in rounds if chosen is not None) if screened else None
        win, source = self.winner(item, answer)
        out[f"{code}_win_source"] = source
        for n, a in enumerate(attrs):
            out[f"{code}_win_{a['code']}"] = win["p"][n] + 1 if win else None
        if self.price_on(item):
            out[f"{code}_win_price"] = win.get("price") if win else None
        return out

    def long_columns(self, item):
        attrs = read_cfg(item).get("attributes", [])
        codes = [a["code"] for a in attrs]
        return (["phase", "task", "alt", "concept"] + codes + [c + "_label" for c in codes]
                + [a["code"] + "_value" for a in attrs if self.level_values(a)]
                + (["price"] if self.price_on(item) else []) + ["chosen", "auto"])

    def long_rows(self, item, response):
        """byo: the BYO profile (chosen = 1); screen: every concept, chosen = 1 if "possible", auto =
        1 if it broke a rule and was not shown; price_cap / nogo / must: one row per offered level
        (only its attribute filled), chosen = 1 if confirmed; tour: every concept of a decided
        task, chosen = 1 for the one picked."""
        answer = (response.answers or {}).get(str(item.id))
        if not isinstance(answer, dict) or not answer.get("concepts"):
            return
        attrs = self.attributes(item, item.survey.default_language)
        valued = [(k, self.level_values(a)) for k, a in enumerate(attrs) if self.level_values(a)]
        price = self.price_on(item)
        plan = self.plan(item, answer)
        concepts = plan.concepts

        def value(values, level):
            v = values[level] if level is not None and 0 <= level < len(values) else None
            return "" if v is None else v
        stored = answer.get("rules") or {}

        def row(phase, task, alt, concept, profile, price_value, chosen, auto=""):
            return ([phase, task, alt, concept] + [p + 1 for p in profile]
                    + [attrs[k]["level_labels"][p] for k, p in enumerate(profile)]
                    + [value(values, profile[k]) for k, values in valued]
                    + ([price_value] if price else []) + [chosen, auto])

        def level_row(phase, task, alt, ref, chosen):
            a, lv = ref
            return ([phase, task, alt, ""] + ["" if k != a else lv + 1 for k in range(len(attrs))]
                    + ["" if k != a else attrs[a]["level_labels"][lv] for k in range(len(attrs))]
                    + ["" if k != a else value(values, lv) for k, values in valued]
                    + ([""] if price else []) + [chosen, ""])

        byo = self.byo_profile(item, answer)
        if byo:
            yield row("byo", 0, 1, 0, byo, answer.get("byo_price"), 1)
        s = answer.get("s") or {}
        k = read_cfg(item).get("per_screen", 4)
        questions = sorted(plan.questions.items(), key=lambda kv: (kv[1]["after"], "pnm".index(kv[0][0])))
        # alt: the position the concept was shown at on its page; concepts left out by the rules were not shown
        # (no position), so the ones after them move up, as on the page
        shown_at, seen = {}, {}
        for c, concept in enumerate(concepts):
            if not concept.get("auto"):
                seen[c // k] = seen.get(c // k, 0) + 1
                shown_at[c] = seen[c // k]
        for c, concept in enumerate(concepts):
            if s.get(str(c)) is None:
                continue
            yield row("screen", c // k + 1, shown_at.get(c, ""), c + 1, concept["p"], concept.get("price"), s[str(c)],
                      int(bool(concept.get("auto"))))
        for key, q in questions:
            if stored.get(key) is None:
                continue  # not answered yet, or left open
            task = q["after"] + 1
            if q["phase"] == "price":
                cap = (stored[key] or {}).get("cap")
                yield ["price_cap", task, 1, ""] + [""] * (2 * len(attrs) + len(valued)) + ([cap] if price else []) + [1, ""]
            elif q["phase"] == "nogo":
                chosen = {self.parse_ref(item, x) for x in stored[key] or []}
                for alt, ref in enumerate(q["options"], start=1):
                    yield level_row("nogo", task, alt, ref, int(ref in chosen))
            else:
                yield level_row("must", task, 1, q["level"], int(bool((stored[key] or {}).get("yes"))))
        for r, group, chosen in self.tournament(item, answer, plan)[0]:
            if chosen is None:
                continue
            for alt, c in enumerate(group, start=1):
                yield row("tour", r + 1, alt, c + 1, concepts[c]["p"], concepts[c].get("price"), int(c == chosen))

    def codebook_info(self, item, column, lang, ui=None):
        code = item.code
        if column == f"{code}_byo_filled":
            n = len(read_cfg(item).get("attributes", []))
            return "Zahl", in_lang(ui, "0 bis {n}", "0 to {n}", n=n), [in_lang(
                ui, "nur ohne Pflichtantwort möglich: offen gelassene Merkmale der Wunschvariante "
                    "werden zufällig gefüllt, damit das Screening folgen kann; leer ohne Wunschvariante",
                "possible only without a required answer: attributes of the build-your-own concept left open are "
                "filled at random so that the screening can follow; empty without a build-your-own concept")]
        if column == f"{code}_price_cap_set":
            return "ACBC", "1 = Grenze genannt; 0 = keine feste Grenze", [in_lang(
                ui, "die genannte Grenze steht in {col}; leer: nicht gefragt (keine Preisrückfrage) oder offen gelassen",
                "the limit given is in {col}; empty: not asked (no price question) or left open", col=f"{code}_price_cap")]
        if column == f"{code}_win_source":
            return "ACBC", "tour | single | byo", [in_lang(
                ui, "tour: Gewinner des Turniers; single: Nur eine Variante kam infrage; "
                    "byo: Keine kam infrage, die Wunschvariante zählt",
                "tour: winner of the tournament; single: only one concept was acceptable; "
                "byo: none was acceptable, the build-your-own concept counts")]

        def levels(a, first=()):  # value labels of the levels (with their values); ";" separates the entries
            values = self.level_values(a) or []
            named = [(n, label + (f" ({in_lang(ui, 'Wert', 'value')} {values[n - 1]})"
                                  if n - 1 < len(values) and values[n - 1] is not None else ""))
                     for n, label in enumerate(a["level_labels"], start=1)]
            return "; ".join(f"{n} = {str(label).replace(';', ',')}" for n, label in [*first, *named])

        for a in self.attributes(item, lang):
            if column == f"{code}_byo_{a['code']}":
                return "ACBC", levels(a), [in_lang(ui, "Stufe des Merkmals „{a}“ in der Wunschvariante",
                                                   "level of the attribute “{a}” in the build-your-own concept", a=a["label"])]
            if column == f"{code}_win_{a['code']}":
                return "ACBC", levels(a), [in_lang(ui, "Stufe des Merkmals „{a}“ beim Gewinner (siehe {col})",
                                                   "level of the attribute “{a}” of the winner (see {col})",
                                                   a=a["label"], col=f"{code}_win_source")]
            if column == f"{code}_must_{a['code']}":
                return "ACBC", levels(a, [(0, "gefragt, verneint")]), [in_lang(
                    ui, "als unverzichtbar bestätigte Stufe des Merkmals „{a}“; leer: nicht gefragt oder offen gelassen",
                    "level of the attribute “{a}” confirmed as a must-have; empty: not asked or left open", a=a["label"])]
            for n, label in enumerate(a["level_labels"], start=1):
                if column == f"{code}_nogo_{a['code']}_{n}":
                    return "ACBC", "1 = unakzeptabel; 0 = angeboten, nicht gewählt", [in_lang(
                        ui, "Stufe „{level}“ des Merkmals „{a}“; leer: nicht angeboten oder offen gelassen",
                        "level “{level}” of the attribute “{a}”; empty: not offered or left open", level=label, a=a["label"])]
        return None

    def result_column(self, item, column):
        return None, "", None  # summarised as a whole: results()

    def results(self, item, table, lang):
        """Counts per level (app/acbc_analysis.py) and the aggregate logit (app/acbc_logit.py)."""
        from app import acbc_analysis, acbc_logit

        answers = acbc_analysis.answers_for(item, table)
        return {"counts": acbc_analysis.analyse(item, answers, lang), "model": acbc_logit.estimate(item, answers, lang)}

    def questionnaire_lines(self, item, lang):
        c = read_cfg(item)
        out = []
        for a in self.attributes(item, lang):
            levels = a["level_labels"]
            if self.price_on(item):
                levels = [f"{label} ({self.format_component(item, _number(l.get('price')), lang) or '± 0'})"
                          for label, l in zip(levels, a["levels"])]
            out.append(f"  - Merkmale `{a['code']}` {a['label']}: " + "; ".join(levels))
        out.append(f"  - Adaptive: build your own, {c.get('screens')} screening pages × {c.get('per_screen')} concepts "
                   f"({c.get('min_changes')}–{c.get('max_changes')} attributes changed from the BYO profile), "
                   f"tournament of up to {c.get('tour_max')} concepts in tasks of three")
        if self.rules_on(item):
            out.append(f"  - Rule questions from screen {c.get('rules_after')} on: up to {c.get('max_nogo')} "
                       f"unacceptable and {c.get('max_must')} must-have questions"
                       + (", one price limit" if "price" in self.rule_phases(item) else ""))
        if self.price_on(item):
            p = c.get("price") or {}
            out.append(f"  - Summed price: base {self.format_price(item, _number(p.get('base')), lang)} plus the level "
                       f"components, varied by ±{p.get('variation')}%, rounded to {p.get('step')}")
        return out
