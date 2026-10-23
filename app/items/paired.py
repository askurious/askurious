"""Paired comparison: two options per page, participants choose the better one (concept paarvergleich-konzept.md,
Ralf 2026-10-10: "wie empfohlen").

K options (text, optionally an image each from the image pipeline). Answer forms (config "answer", E3): "choice" (one
of the two), "tie" (also "gleich") and "strength" (5 or 7 steps from "the first much better" over "gleich" to "the
second much better", config "steps"). Design (app/paired_design.py, E4, E5): up to 8 options all pairs, above that
about 2 K pairs per person; versions balanced over the participants like MaxDiff, frozen at the start, left/right
balanced, generated in draft. One page per pair (E6), "Weiter ohne Klick" on by default for "choice" and "tie";
optionally an explanation page first.

Results (app/paired_analysis.py, E7, E8, E10): counts, the adjacent-categories Bradley-Terry model with the position
effect, values summing to 100 with 95 % intervals, the table option x option; with a complete design the consistency
per person (circular triads, Kendall's zeta) and the agreement over persons (Kendall's u).

config: objects [{id, prompt?}], answer, steps, per_person (0 = suggestion), versions, explain, auto_next, images,
        design {sig, versions: [[[first, second], ...], ...], stats}
texts:  question, objects {id: text}, tie_label, much, clear, bit, same, explain_intro, alts {id: text}
answer: {"v": version, "t0": c, "t1": c, ...} with c the category from the point of view of the option shown first
        (choice: 1 / -1, tie: also 0, strength: -m .. m), None for a pair left open
"""
import hashlib
import json
import math
import random
import secrets

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_any, r_flag, r_id, r_int, r_maybe, r_one, r_rows, r_text  # rules for survey files
from app.items.base import AnswerError, ItemType, form_ids, in_lang, new_id, plain, prepared, whole
from app.rows import NOT_SHOWN
from app.lang import Both, de_en
from app.ui_i18n import pick, tr

ANSWERS = ("choice", "tie", "strength")
DEFAULT_TEXTS = {
    "de": {"question": "Was ist Ihnen wichtiger?", "tie_label": "Beide gleich", "much": "viel", "clear": "deutlich",
           "bit": "etwas", "same": "gleich"},
    "en": {"question": "Which is more important to you?", "tie_label": "Both the same", "much": "much",
           "clear": "clearly", "bit": "slightly", "same": "same"},
}
MAX_IMAGE_OPTIONS = 20
IMAGE_PREFIX = "o:"
DUMMY_SPREAD, DUMMY_PERSON = 1.0, 0.5  # sd of the true values and of the person deviations (dummy data)
DUMMY_THETA = {0: -0.3, 1: 0.6, 2: 0.6}  # dummy use of "gleich" and of the weaker steps


def _new_id():
    return new_id("o")


@register
class Paired(ItemType):
    key = "paired"
    label = Both("Paarvergleich", "Paired comparison")
    short = Both("Die bessere von zwei Optionen", "The better of two options")
    description = Both("Je zwei Optionen nebeneinander, die bessere wählen (auf Wunsch mit „gleich“ oder Stärke)",
                       "Two options side by side, choose the better one (optionally with “same” or a strength)")
    order = 47
    fact_sheet = FactSheet(
        "choice",
        keywords=["Paarvergleich", "Paired comparison", "Pairwise", "Bradley-Terry", "Thurstone", "Präferenz",
                  "Preference", "Bildvergleich"],
        can=[("Zwei Optionen je Seite (Text, auf Wunsch mit Bild); wählen, mit „gleich“ oder auf einer Stärke-Skala "
              "mit 5 oder 7 Stufen.",
              "Two options per page (text, optionally with an image); choose, with “same” or on a strength scale with "
              "5 or 7 steps."),
             ("Bis 8 Optionen alle Paare, darüber ein balanciertes unvollständiges Design in Versionen.",
              "Up to 8 options all pairs, above that a balanced incomplete design in versions."),
             ("Auswertung: Bradley-Terry-Werte (Summe 100) mit Intervallen, Positionseffekt, Tabelle Option × Option, "
              "Konsistenz je Person (zirkuläre Triaden).",
              "Analysis: Bradley-Terry values (sum 100) with intervals, position effect, table option × option, "
              "consistency per person (circular triads).")],
        good_for=[("Prioritäten zwischen 4 und 20 Optionen, wenn das Urteil schwer fällt",
                   "Priorities among 4 to 20 options when the judgement is hard"),
                  ("Bilder, Logos oder Entwürfe vergleichen", "Comparing images, logos or drafts")],
        not_for=[("Sehr viele Optionen (dann MaxDiff)", "Very many options (then MaxDiff)"),
                 ("Merkmale mit Stufen (dann Conjoint)", "Attributes with levels (then Conjoint)"),
                 ("Wenige Optionen, die man leicht ordnet (dann Rangfolge)",
                  "A few options that are easy to order (then ranking)")],
        designs=["choice", "perception"],
        examples=[("Welches von zwei Logos wirkt vertrauenswürdiger?", "Which of two logos looks more trustworthy?"),
                  ("Was ist Ihnen bei einem Arbeitsplatz wichtiger: Gehalt oder Homeoffice?",
                   "What matters more to you in a job: pay or working from home?")],
    )
    import_rules = {"objects": r_rows(20, id=r_id, prompt=r_maybe(r_text(1000))), "answer": r_one(*ANSWERS),
                    "steps": r_one(5, 7), "per_person": r_int(0, 190), "versions": r_int(1, 20), "explain": r_flag,
                    "images": r_flag, "design": r_any}
    result_kind = "paired"  # own summary (results())
    alone_on_page = True  # one page per pair
    allowed_in_loop = allowed_in_separated = False  # design version per response (layout["cj"])
    supports_auto_next = True
    auto_next_hint = Both("sobald eine Antwort gewählt ist", "as soon as an answer is chosen")
    editor_tabs = Both([("content", "1 · Inhalt", "Optionen, Frage, Antwortform"), ("design", "2 · Ablauf", "Paare, Versionen")],
                       [("content", "1 · Content", "Options, question, answer form"), ("design", "2 · Flow", "Pairs, versions")])
    panel_template = "items/paired/panel.html"
    design_lock = True
    long_export = "paired"
    long_export_label = Both("Person × Paar", "Person × pair")
    long_export_note = ("- `paired_*_long.csv`: long format of the paired comparison (participant × pair; first and "
                        "second option as shown, category, win1/win2 as read by the R package BradleyTerry2).")
    levels = ("nominal",)
    image_set_label = Both("Bilder der Optionen", "Images of the options")
    image_set_unit = Both("Optionen", "options")
    image_uploads = True

    def import_config(self, config):
        """A design of the file is kept only if this item could have generated it (every version a list of pairs of
        two different existing options); its balance figures are counted anew."""
        from app import paired_design

        config, unfit, unknown = super().import_config(config)
        design = config.get("design")
        if design is not None and not unfit:
            n = len(config.get("objects") or [])
            versions = design.get("versions") if isinstance(design, dict) else None
            fits = (n >= paired_design.MIN_OPTIONS and isinstance(versions, list)
                    and len(versions) == config.get("versions", 1) and all(
                        isinstance(v, list) and v and all(
                            isinstance(p, list) and len(p) == 2 and all(type(i) is int and 0 <= i < n for i in p)
                            and p[0] != p[1] for p in v) for v in versions))
            if fits and isinstance(design.get("sig"), str) and len(design["sig"]) <= 40:
                config["design"] = {"sig": design["sig"], "versions": versions,
                                    "stats": paired_design.diagnostics(n, versions)}
            else:
                config["design"] = None
                unknown = unknown + ["design"]
        return config, unfit, unknown

    # --- defaults and editing ----------------------------------------------------------------------------------

    def default_config(self):
        return {"objects": [], "answer": "choice", "steps": 5, "per_person": 0, "versions": 1, "explain": False,
                "auto_next": True, "design": None}

    def default_texts(self, lang):
        return {**pick(DEFAULT_TEXTS, lang), "objects": {}}

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config)
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        for key in ("question", "tie_label", "much", "clear", "bit", "same"):
            if key in form:
                lt[key] = form.get(key, "").strip()[:500]
        if "explain_intro" in form:
            lt["explain_intro"] = form.get("explain_intro", "").strip()[:3000]
        otexts = dict(lt.get("objects", {}))
        alts = dict(lt.get("alts", {}))
        known = {o["id"] for o in config.get("objects", [])}
        rows = list(zip(form_ids(form.getlist("md_id")), form.getlist("md_text")))
        if structural:
            objects = []
            old = {o["id"]: o for o in config.get("objects", [])}
            for oid, text in rows:
                text = text.strip()[:500]
                if not text:
                    continue
                oid = oid if oid and oid not in {o["id"] for o in objects} else _new_id()
                objects.append({k: v for k, v in old.get(oid, {"id": oid}).items()})
                objects[-1]["id"] = oid
                otexts[oid] = text
            config["objects"] = objects[:20]
            if form.get("answer") in ANSWERS:
                if form["answer"] == "strength" and config.get("answer") != "strength":
                    config["auto_next"] = False  # a slip on a scale of five or seven would move on at once (E6)
                config["answer"] = form["answer"]
            if whole(form.get("steps", "")) in (5, 7):
                config["steps"] = whole(form["steps"])

            def num(key, lo, hi, default):
                v = whole(form.get(key, ""))
                return max(lo, min(hi, v)) if v is not None else default

            if "pairs" in form:
                config.update(per_person=num("pairs", 0, 190, 0), versions=num("versions", 1, 20, 1))
            if form.get("images_shown"):
                config["images"] = bool(form.get("images"))
                for o in config["objects"]:
                    field = f"option_prompt_{o['id']}"
                    if field in form:
                        prompt = " ".join(form.get(field, "").split())[:1000]
                        if prompt:
                            o["prompt"] = prompt
                        else:
                            o.pop("prompt", None)
        else:
            for oid, text in rows:
                if oid in known and text.strip():
                    otexts[oid] = text.strip()[:500]
        for o in config.get("objects", []):
            field = f"option_alt_{o['id']}"
            if field in form:
                alts[o["id"]] = form.get(field, "").strip()[:300]
        kept = {o["id"] for o in config.get("objects", [])}
        lt["objects"] = {k: t for k, t in otexts.items() if k in kept}
        lt["alts"] = {k: t for k, t in alts.items() if k in kept and t}
        if lang == item.survey.default_language:
            config["explain"] = bool(form.get("explain"))
        texts[lang] = lt
        item.config, item.texts = config, texts

    # --- structure ---------------------------------------------------------------------------------------------

    def n_items(self, item):
        return len(item.config.get("objects", []))

    def labels(self, item, lang=None):
        t = item.t(lang).get("objects", {})
        return [t.get(o["id"], "") for o in item.config.get("objects", [])]

    def n_pairs(self, item):
        """Pairs per person: as configured (at most all of them), or the suggestion when left empty."""
        from app import paired_design

        n = self.n_items(item)
        if n < 2:
            return 0
        wanted = item.config.get("per_person") or paired_design.suggested_pairs(n)
        return min(wanted, paired_design.all_pairs(n))

    def complete(self, item):
        """Every person sees every pair."""
        from app import paired_design

        n = self.n_items(item)
        return n >= 3 and self.n_pairs(item) == paired_design.all_pairs(n)

    def categories(self, item):
        from app.paired_analysis import categories

        return categories(item.config.get("answer", "choice"), item.config.get("steps", 5))

    def scale_steps(self, item):
        """The strength scale as shown from left to right: [(c, caption key)], first option much better first."""
        steps = item.config.get("steps", 5)
        side = ["much", "clear", "bit"] if steps == 7 else ["much", "bit"]
        m = len(side)
        return [(m - n, key) for n, key in enumerate(side)] + [(0, "same")] + \
               [(-(n + 1), key) for n, key in enumerate(reversed(side))]

    def signature(self, item):
        c = item.config
        shape = [self.n_items(item), self.n_pairs(item), c.get("versions")]
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
        from app import paired_design

        c = item.config
        n = self.n_items(item)
        versions = paired_design.generate(n, self.n_pairs(item), c.get("versions", 1), seed=secrets.randbits(32))
        item.config = {**c, "design": {"sig": self.signature(item), "versions": versions,
                                       "stats": paired_design.diagnostics(n, versions)}}

    def design_summary(self, item):
        s = item.config["design"]["stats"]
        return de_en("Versuchsplan erzeugt: je Option {f0}–{f1}-mal je Person, Paare {p0}–{p1}-mal über alle Versionen.",
                     "Experimental design generated: each option {f0}–{f1} times per person, pairs {p0}–{p1} times over all versions.",
                     f0=s["freq_min"], f1=s["freq_max"], p0=s["pair_min"], p1=s["pair_max"])

    def design_notes(self, item):
        n = self.n_items(item)
        notes = []
        if n and not self.complete(item) and 2 * self.n_pairs(item) < 3 * n:
            notes.append(de_en("Jede Option erscheint je Person weniger als dreimal; für Werte je Person (HB) mehr Paare "
                               "einplanen (etwa 2 · Optionen).",
                               "Each option appears fewer than three times per person; for values per person (HB) plan "
                               "more pairs (about 2 · options)."))
        d = item.config.get("design") or {}
        if d and self.design_current(item) and not d.get("stats", {}).get("connected_each", True):
            notes.append(de_en("Nicht alle Optionen sind innerhalb jeder Version über Paare verbunden; mehr Paare wählen.",
                               "Not all options are connected through pairs within each version; choose more pairs."))
        if n > 15 and self.complete(item):
            notes.append(de_en("{p} Paare je Person sind viel; weniger Paare je Person (unvollständiger Versuchsplan) halten die "
                               "Aufgabe kurz.", "{p} pairs per person are a lot; fewer pairs per person (incomplete design) "
                               "keep the task short.", p=self.n_pairs(item)))
        return notes

    def expand_pages(self, survey, item, rng):
        """One page per pair (an explanation first, optional); without a current design the item stays a normal page,
        which shows "Design noch nicht erzeugt"."""
        if not self.design_current(item):
            return None
        state = {"version": self.balanced_version(survey, item, rng)}
        pages = [{"explain": True}] if item.config.get("explain") else []
        pages += [{"task": t, "v": state["version"]} for t in range(len(self.version_pairs(item, state["version"])))]
        return state, pages

    def balanced_version(self, survey, item, rng):
        """The design version used least so far by real participants (random among ties)."""
        from app import balance

        n_versions = len(item.config["design"]["versions"])
        used = balance.counts(survey)["version"].get(str(item.id), {})
        counts = [used.get(v, 0) for v in range(n_versions)]
        least = min(counts)
        return rng.choice([v for v, c in enumerate(counts) if c == least])

    def version_pairs(self, item, version):
        versions = (item.config.get("design") or {}).get("versions") or []
        return versions[version] if 0 <= version < len(versions) else []

    def task_pair(self, item, version, task):
        pairs = self.version_pairs(item, version)
        return pairs[task] if 0 <= task < len(pairs) else None

    def shown_tasks(self, item):
        return self.n_pairs(item)

    def timing_pages(self, item):
        pages = [("_x", "Erklärungsseite", ".x")] if item.config.get("explain") else []
        return pages + [(f"_t{x + 1}", f"Paar {x + 1}", f".t{x}") for x in range(self.n_pairs(item))]

    def seconds(self, item, read):
        from app.duration import ANSWER_SECONDS

        per = ANSWER_SECONDS["paired_strength" if item.config.get("answer") == "strength" else "paired_task"]
        return read + per * self.n_pairs(item) + (15 if item.config.get("explain") else 0)

    def source_groups(self, item):
        from app import paired_analysis as pa
        from app.paired_design import FORD

        out = [(de_en("Paarvergleich: Bradley-Terry-Modell", "Paired comparison: Bradley-Terry model"),
                [pa.BRADLEY_TERRY, pa.THURSTONE, pa.CATTELAN]),
               (de_en("Verbundener Versuchsplan", "Connected design"), [FORD])]
        if item.config.get("answer") != "choice":
            out.append((de_en("Gleichstand und Stärke", "Ties and strength"), [pa.DAVIDSON, pa.AGRESTI, pa.SCHEFFE]))
        if self.complete(item):
            out.append((de_en("Konsistenz und Übereinstimmung", "Consistency and agreement"), [pa.KENDALL]))
        return out

    def references(self, item):
        return [s for _, group in self.source_groups(item) for s in group]

    # --- images of the options (the image pipeline, items/images.html) ----------------------------------------

    def images(self, item):
        return bool((item.config or {}).get("images"))

    def has_image_set(self, item):
        return self.images(item)

    def required_keys(self, item, variable_rows=None):
        return [IMAGE_PREFIX + o["id"] for o in item.config.get("objects", [])] if self.images(item) else []

    def _object_of(self, item, key):
        oid = key[len(IMAGE_PREFIX):] if str(key).startswith(IMAGE_PREFIX) else None
        return next((o for o in item.config.get("objects", []) if o["id"] == oid), None)

    def key_label(self, item, key):
        o = self._object_of(item, key)
        if o is None:
            return "?"
        n = item.config["objects"].index(o) + 1
        label = plain(item.t(item.survey.default_language).get("objects", {}).get(o["id"], ""))
        return f"{n}: {label}" if label else str(n)

    def prompt_for_key(self, item, key):
        from app import image_styles

        o = self._object_of(item, key) or {}
        scene = (o.get("prompt") or "").strip() or plain(
            item.t(item.survey.default_language).get("objects", {}).get(o.get("id"), ""))
        return image_styles.compose(item.survey, scene, text_free=True)

    def asset_for_key(self, item, key):
        return next((a for a in item.assets if a.key == key and a.status == "approved"), None)

    def object_image(self, item, index):
        objects = item.config.get("objects", [])
        if not self.images(item) or not 0 <= index < len(objects):
            return None
        return self.asset_for_key(item, IMAGE_PREFIX + objects[index]["id"])

    def object_alt(self, item, index, lang):
        o = item.config.get("objects", [])[index]
        own = (item.t(lang).get("alts") or {}).get(o["id"], "")
        return own or ((o.get("prompt") or "") if str(lang).startswith("en") else "")

    def image_size(self, item):
        return "1024x1024"

    def image_problems(self, item):
        keys = self.required_keys(item)
        missing = [k for k in keys if self.asset_for_key(item, k) is None]
        return [de_en("{n} von {total} Bildern der Optionen fehlen oder sind nicht freigegeben",
                      "{n} of {total} images of the options are missing or not approved", n=len(missing),
                      total=len(keys))] if missing else []

    # --- checks ------------------------------------------------------------------------------------------------

    def validate(self, item):
        from app import paired_design

        n = self.n_items(item)
        problems = []
        if n < paired_design.MIN_OPTIONS:
            problems.append(de_en("mindestens drei Optionen", "at least three options"))
        elif n > paired_design.MAX_OPTIONS:
            problems.append(de_en("höchstens 20 Optionen (darüber MaxDiff)", "at most 20 options (beyond that MaxDiff)"))
        elif not self.design_current(item):
            problems.append(de_en("Versuchsplan fehlt oder ist veraltet: bitte „Versuchsplan erzeugen“",
                                  "Experimental design missing or out of date: please “Generate experimental design”"))
        return problems + self.image_problems(item)

    # --- answers -----------------------------------------------------------------------------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        meta = meta or {}
        if meta.get("explain"):
            return {"explained": True}
        task = meta.get("task", 0)
        key = f"t{task}"
        extra = {"v": meta["v"]} if "v" in meta else {}
        raw = form.get(f"i{item.id}_{key}", "")
        if raw == "":
            if item.required:
                raise AnswerError(tr(lang, "Bitte wählen Sie eine Antwort.", "Please choose an answer."))
            return {key: None, **extra}
        try:
            c = int(raw)
        except ValueError:
            raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice.")) from None
        if c not in self.categories(item):
            raise AnswerError(tr(lang, "Ungültige Auswahl.", "Invalid choice."))
        return {key: c, **extra}

    def display_value(self, item, form, meta=None):
        meta = meta or {}
        key = f"t{meta.get('task', 0)}"
        raw = form.get(f"i{item.id}_{key}", "")
        try:
            return {key: int(raw)}
        except ValueError:
            return {key: None}

    def merge_answer(self, old, new):
        return {**(old or {}), **(new or {})}

    def is_missing(self, item, answer):
        answer = answer if isinstance(answer, dict) else {}
        if answer == {"explained": True}:
            return False  # the explanation page alone; stored with the tasks it hides nothing (code review 2026-10-15, F4)
        chosen = {k: v for k, v in answer.items() if k.startswith("t")}
        return not chosen or all(v is None for v in chosen.values())

    def dummy_fields(self, item, rng, meta, answers=None):
        """The model of the analysis: true values per option (the same for all dummies of this item), a deviation per
        dummy person, a small advantage of the first place, and the categories drawn by their probabilities."""
        meta = meta or {}
        if meta.get("explain") or not self.design_current(item):
            return {}
        n = self.n_items(item)
        true = random.Random(f"paired:{item.id}")
        base = [true.gauss(0, DUMMY_SPREAD) for _ in range(n)]
        persons = rng.__dict__.setdefault("_paired_persons", {})
        who = (item.id, meta.get("respondent"))
        if who not in persons:
            persons[who] = [u + rng.gauss(0, DUMMY_PERSON) for u in base]
        utility = persons[who]
        task = meta.get("task", 0)
        pair = self.task_pair(item, meta.get("v", 0), task)
        if not pair:
            return {}
        a, b = pair
        d = 2 * (utility[a] - utility[b]) + 0.2  # on the scale of the categories c / 2
        cats = self.categories(item)
        weights = [math.exp(c / 2 * d + (DUMMY_THETA.get(abs(c), 0) if abs(c) < max(cats) else 0)) for c in cats]
        r, acc = rng.random() * sum(weights), 0.0
        for c, w in zip(cats, weights):
            acc += w
            if r <= acc:
                break
        return {f"i{item.id}_t{task}": str(c)}

    # --- export ------------------------------------------------------------------------------------------------

    def pair_columns(self, item):
        """[(i, j, column)] for every pair of options, i < j (0-based)."""
        n, code = self.n_items(item), item.code
        return [(i, j, f"{code}_{i + 1}_{j + 1}") for i in range(n) for j in range(i + 1, n)]

    def missing_codes(self, item, column):
        """-77 (pair not shown) in the column of a pair and of its strength: missing in SPSS, R and jamovi, as for
        the other types (audit 2026-10-12, E-3)."""
        pairs = {col for _, _, col in self.pair_columns(item)}
        return (NOT_SHOWN,) if column in pairs or (column.endswith("_st") and column[:-3] in pairs) else ()

    def export_columns(self, item):
        labels = [plain(x) for x in self.labels(item)]
        strength = item.config.get("answer") == "strength"
        cols = [(f"{item.code}_version", "Version des Versuchsplans")]
        for i, j, col in self.pair_columns(item):
            cols.append((col, f"Paar {i + 1}–{j + 1}: vorgezogene Option (Nummer); {labels[i]} | {labels[j]}"))
            if strength:
                cols.append((f"{col}_st", f"Paar {i + 1}–{j + 1}: Stärke"))
        for i, label in enumerate(labels, start=1):
            cols.append((f"{item.code}_score{i}", f"Score (gewonnen − verloren) / verglichen: {label}"))
        if self.complete(item):
            cols.append((f"{item.code}_triads", "Zirkuläre Triaden"))
            cols.append((f"{item.code}_zeta", "Konsistenzkoeffizient ζ (Kendall)"))
        return cols

    def _export_plan(self, item):
        return {"n": self.n_items(item), "pairs": self.pair_columns(item),
                "versions": item.config["design"]["versions"] if self.design_current(item) else None,
                "strength": item.config.get("answer") == "strength", "complete": self.complete(item)}

    def answered(self, item, answer, versions=None):
        """[(first, second, c)] of one answer, in page order."""
        answer = answer if isinstance(answer, dict) else {}
        versions = versions if versions is not None else ((item.config.get("design") or {}).get("versions") or [])
        v = answer.get("v")
        if v is None or not 0 <= v < len(versions):
            return []
        out = []
        for t, (a, b) in enumerate(versions[v]):
            c = answer.get(f"t{t}")
            if isinstance(c, int) and not isinstance(c, bool):
                out.append((a, b, c))
        return out

    def export_values(self, item, answer):
        from app import paired_analysis

        answer = answer if isinstance(answer, dict) else {}
        plan = prepared(item, "paired_export", lambda: self._export_plan(item))
        n, code, versions = plan["n"], item.code, plan["versions"]
        out = {f"{code}_version": None}
        v = answer.get("v")
        shown = set()
        if versions is not None and v is not None and 0 <= v < len(versions):
            shown = {(min(a, b), max(a, b)) for a, b in versions[v]}
        answered = self.answered(item, answer, versions or [])
        result = {(min(a, b), max(a, b)): (a, b, c) for a, b, c in answered}
        for i, j, col in plan["pairs"]:
            value = strength = None
            if (i, j) in result:
                a, b, c = result[(i, j)]
                value = 0 if c == 0 else (a + 1 if c > 0 else b + 1)
                strength = abs(c)
            elif shown and (i, j) not in shown:
                value = strength = -77
            out[col] = value
            if plan["strength"]:
                out[f"{col}_st"] = strength
        scores = paired_analysis.person_scores(n, answered) if answered else [None] * n
        out.update({f"{code}_score{i + 1}": scores[i] for i in range(n)})
        if plan["complete"]:
            circular, triples, zeta = paired_analysis.triads(n, answered) if answered else (None, 0, None)
            out[f"{code}_triads"] = circular if triples else None
            out[f"{code}_zeta"] = zeta
        return out

    def export_state_values(self, item, state):
        return {f"{item.code}_version": state["version"] + 1 if "version" in (state or {}) else None}

    def codebook_info(self, item, column, lang, ui=None):
        code, labels = item.code, self.labels(item, lang)
        if column == f"{code}_version":
            return "Version des Versuchsplans", in_lang(ui, "1 bis {n}", "1 to {n}", n=item.config.get("versions", 1)), [
                in_lang(ui, "balanciert über echte Teilnahmen; welche Paare in welcher Reihenfolge: Versuchsplan bzw. Langformat",
                        "balanced over real participations; which pairs in which order: experimental design or long format")]
        for i, j, col in self.pair_columns(item):
            if column == col:
                values = f"{i + 1} = {str(plain(labels[i])).replace(';', ',')}; {j + 1} = {str(plain(labels[j])).replace(';', ',')}"
                notes = [in_lang(ui, "Nummer der vorgezogenen Option; 0 = gleich; -77 = Paar nicht gezeigt (Version des Versuchsplans); "
                                     "leer = nicht beantwortet",
                                 "number of the preferred option; 0 = same; -77 = pair not shown (version of the experimental design); empty = "
                                 "not answered")]
                return "Paarvergleich", values + "; 0 = gleich / same; -77", notes
            if column == f"{col}_st":
                m = 3 if item.config.get("steps") == 7 else 2
                return "Stärke", in_lang(ui, "0 = gleich; 1 bis {m} (größer = deutlicher)", "0 = same; 1 to {m} (higher = "
                                         "stronger)", m=m), [in_lang(ui, "-77 = Paar nicht gezeigt", "-77 = pair not shown")]
        if column.startswith(f"{code}_score"):
            return "Zahl", in_lang(ui, "von -1 bis 1", "from -1 to 1"), [in_lang(
                ui, "(gewonnene − verlorene Vergleiche) / Vergleiche der Option, je Person; gleich zählt 0",
                "(comparisons won − comparisons lost) / comparisons of the option, per person; same counts 0")]
        if column == f"{code}_triads":
            return "Zahl", in_lang(ui, "0 und mehr", "0 and more"), [in_lang(
                ui, "zirkuläre Triaden (A vor B, B vor C, C vor A) unter den entschiedenen Dreiergruppen; bei reinem "
                    "Raten ist ein Viertel der Dreiergruppen zirkulär (Kendall & Babington Smith, 1940)",
                "circular triads (A over B, B over C, C over A) among the decided triples; random choices make a quarter "
                "of the triples circular (Kendall & Babington Smith, 1940)")]
        if column == f"{code}_zeta":
            return "Zahl", in_lang(ui, "von 0 bis 1", "from 0 to 1"), [in_lang(
                ui, "1 − Triaden / größtmögliche Zahl; 1 = widerspruchsfrei; leer, wenn nicht jedes Paar entschieden "
                    "wurde (Kendall & Babington Smith, 1940)",
                "1 − triads / largest possible number; 1 = free of contradictions; empty when not every pair was "
                "decided (Kendall & Babington Smith, 1940)")]
        return None

    def column_level(self, item, column):
        return "metric" if column.startswith(f"{item.code}_score") or column.endswith("_zeta") or \
            column.endswith("_triads") else "nominal"

    def result_column(self, item, column):
        return None, "", None  # summarised as a whole: results()

    def results(self, item, table, lang):
        from app.paired_analysis import analyse

        return analyse(item, table, [plain(x) for x in self.labels(item, lang)])

    def long_columns(self, item):
        return ["version", "page", "first", "second", "first_label", "second_label", "category", "preferred",
                "win1", "win2"]

    def long_rows(self, item, response):
        """One row per answered pair: first and second option as shown, the category from the first's point of view,
        the preferred option (0 = same) and win1/win2 (1 for the preferred one, both 0 for "same")."""
        state = ((response.layout or {}).get("cj") or {}).get(str(item.id))
        versions = (item.config.get("design") or {}).get("versions") or []
        if not state or not self.design_current(item) or not 0 <= state.get("version", -1) < len(versions):
            return
        labels = [plain(x) for x in self.labels(item, item.survey.default_language)]
        answer = dict((response.answers or {}).get(str(item.id)) or {})
        answer["v"] = state["version"]
        page = {}
        for t, (a, b) in enumerate(versions[state["version"]]):
            page[(a, b)] = t + 1
        for a, b, c in self.answered(item, answer, versions):
            preferred = 0 if c == 0 else (a + 1 if c > 0 else b + 1)
            yield [state["version"] + 1, page[(a, b)], a + 1, b + 1, labels[a], labels[b], c, preferred,
                   int(c > 0), int(c < 0)]

    def questionnaire_lines(self, item, lang):
        out = [f"  - {n}. {plain(label)}" for n, label in enumerate(self.labels(item, lang), start=1)]
        c = item.config
        form = {"choice": "choose one", "tie": "choose one or “same”",
                "strength": f"strength scale with {c.get('steps', 5)} steps"}.get(c.get("answer"), "")
        out.append(f"  - {self.n_pairs(item)} pairs per person ({form}), {c.get('versions')} "
                   f"{'version' if c.get('versions') == 1 else 'versions'}"
                   + (", explanation page" if c.get("explain") else ""))
        return out
