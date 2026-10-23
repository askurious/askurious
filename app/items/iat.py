"""Implicit Association Test, the standard seven-block design.

Four categories: two targets (A, B, e.g. "Roboter" / "Menschen") and two attributes (X, Y, e.g. "gut" /
"schlecht"), each with its own stimuli (words). Stimuli appear one at a time in the middle; participants sort them
left (E) or right (I), or tap the two fields on a touch screen. A wrong answer shows a red ✗ and has to be
corrected; the latency runs to the correct answer ("built-in error penalty", Greenwald, Nosek & Banaji, 2003).

Blocks (trials; Nosek, Greenwald & Banaji, 2005, block 5 with 28 trials against order effects):
  1 targets 20, 2 attributes 20, 3 combined practice 20, 4 combined test 40,
  5 targets swapped 28, 6 combined practice 20, 7 combined test 40.
"Compatible" is A with X. Order 1 pairs A+X in blocks 3/4 (A+X first), order 2 pairs B+X there (A+X in 6/7);
by default drawn per participant from the pid, or fixed to order 1. Combined blocks alternate target and attribute
trials.

D score (improved algorithm with built-in correction): blocks 3, 4, 6, 7; trials over 10,000 ms dropped; a
participant with more than 10 % of the trials under 300 ms gets no score (_valid = 0). D = mean of
(M6 − M3) / SD(3, 6) and (M7 − M4) / SD(4, 7), with the incompatible minus the compatible block: positive D =
faster with A+X (e.g. robots with "good").

  answer = {"o": 1 | 2, "t": [[block, stimulus id, ms, error 0/1], ...]}  (all 188 trials in the order shown)

A trial longer than MAX_RT (tab switched, interrupted) is stored capped at MAX_RT, not refused: the D score drops it
anyway (over 10,000 ms), and a refused answer would make the whole task to be repeated (math audit 2026-10-05, N-2).

The D score goes into later texts as {{<code>_d}} ("0,46"; empty for a run without a score, piping.py) and into the
conditions of the flow as a number (conditions.describe, flow.holds; Philipp, 2026-10-06). A single person's D is
not very reliable (test–retest around .5): the editor says so.

Fast key presses are essential to the task and stay so (WCAG 2.1.1/2.2.1 allow it). Who cannot give them says so
before the start ("Diese Aufgabe kann ich nicht bearbeiten", accessibility concept D5/E5): the answer is then CANNOT,
a missing value of its own, exported as CANNOT_CODE in every column of the item, declared missing and left out of the
D scores and the results like "weiß nicht" (-98)."""
import hashlib
import random
import statistics

from app import units
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en
from app.items.base import AnswerError, ItemType, in_lang, load_json, r_id, r_one, r_rows, r_text, read_cfg
from app.items.base import form_ids, new_id as _nid
from app.ui_i18n import tr

CATS = ("A", "B", "X", "Y")
TARGETS, ATTRIBUTES = ("A", "B"), ("X", "Y")
BLOCKS = (20, 20, 20, 40, 28, 20, 40)
MAX_RT = 60_000  # longer latencies are capped (participant.js and _parse), not refused
SLOW, FAST, FAST_SHARE = 10_000, 300, 0.10
MAX_STIMULI = 30  # per category
CANNOT = "cannot"   # the stored answer of "Diese Aufgabe kann ich nicht bearbeiten" (accessibility concept D5)
CANNOT_CODE = -97   # its code in the export (-98 is "weiß nicht", -77 "nicht gezeigt")

GREENWALD = ("Greenwald, A. G., Nosek, B. A., & Banaji, M. R. (2003). Understanding and using the Implicit Association "
             "Test: I. An improved scoring algorithm. Journal of Personality and Social Psychology, 85(2), 197–216. "
             "https://doi.org/10.1037/0022-3514.85.2.197")
NOSEK = ("Nosek, B. A., Greenwald, A. G., & Banaji, M. R. (2005). Understanding and using the Implicit Association Test: "
         "II. Method variables and construct validity. Personality and Social Psychology Bulletin, 31(2), 166–180. "
         "https://doi.org/10.1177/0146167204271418")
SOURCES = (GREENWALD, NOSEK)


def sides(order, block):
    """(left categories, right categories) of a block (1..7) for order 1 or 2."""
    first, second = ("A", "B") if order == 1 else ("B", "A")
    if block in (5, 6, 7):
        first, second = second, first
    if block in (1, 5):
        return (first,), (second,)
    if block == 2:
        return ("X",), ("Y",)
    return (first, "X"), (second, "Y")


def compatible(order, block):
    """True for the combined blocks that pair A with X."""
    return sides(order, block)[0] in (("A", "X"),)


def _cycle(rng, pool, n):
    """n stimuli from the pool, each as often as possible (shuffled rounds)."""
    out = []
    while len(out) < n and pool:
        batch = list(pool)
        rng.shuffle(batch)
        out += batch
    return out[:n]


@register
class IAT(ItemType):
    key = "iat"
    CANNOT = CANNOT  # for the participant template
    label = Both("Impliziter Assoziationstest (IAT)", "Implicit Association Test (IAT)")
    short = Both("Assoziationen über Reaktionszeiten", "Associations by reaction times")
    description = Both("Begriffe zweier Konzepte und zweier Bewertungen nach links oder rechts sortieren; "
                       "sieben Blöcke, D-Wert nach Greenwald et al. (2003)",
                       "Sort terms of two concepts and two evaluations to the left or right; "
                       "seven blocks, D score after Greenwald et al. (2003)")
    order = 28
    fact_sheet = FactSheet(
        "tasks",
        keywords=["IAT", "Implicit", "Implizit", "Reaktionszeit", "Reaction time", "Assoziation"],
        can=[("Sieben Blöcke, vier Kategorien mit eigenen Stimuli.",
              "Seven blocks, four categories with stimuli of their own."),
             ("Reihenfolge je Person ausgelost oder fest.", "Order drawn per person or fixed."),
             ("D-Wert nach Greenwald et al. (2003).", "D score after Greenwald et al. (2003)."),
             ("Danach auf Wunsch das Ergebnis für die Person (IAT-Ergebnis).",
              "Then, if you wish, the result for the person (IAT result).")],
        good_for=[("Implizite Einstellungen, die man nicht gern oder nicht bewusst angibt",
                   "Implicit attitudes people do not like to or cannot consciously report")],
        not_for=[("Bildschirme ohne Tastatur oder kleine Handys ohne Erprobung",
                  "Screens without a keyboard or small phones without trying it out"),
                 ("Explizite Einstellungen (dann Skalen)", "Explicit attitudes (then scales)")],
        designs=["cross", "framing"],
        examples=[("Menschen und Roboter mit gut und schlecht", "Humans and robots with good and bad")],
    )
    supports_auto_next = True
    alone_on_page = True
    allowed_in_loop = False
    allowed_in_separated = False
    auto_next_hint = Both("nach dem letzten Block", "after the last block")
    long_export = "iat"
    long_export_label = Both("Person × Durchgang: Block, Begriff, Kategorie, Reaktionszeit, Fehler",
                             "Person × trial: block, term, category, reaction time, error")
    long_export_note = ("- `iat_*_long.csv`: one row per trial (participant × trial): block 1 to 7, trial number in the "
                        "block, stimulus and category code, latency to the correct answer in ms, error (1 = first "
                        "answer wrong), whether the block pairs A with X (compatible).")
    import_rules = {"categories": r_rows(4, 4, id=r_one(*CATS), code=r_text(64, 1)),
                    "stimuli": r_rows(4 * MAX_STIMULI, id=r_id, cat=r_one(*CATS), code=r_text(64, 1)),
                    "order": r_one("person", "fixed")}

    def default_config(self):
        # fixed ids: the editor asks for the settings and the texts separately (editor.add_item)
        stim = [{"id": f"{c.lower()}{n}", "cat": c, "code": f"{c.lower()}{n}"} for c in CATS for n in range(1, 5)]
        return {"categories": [{"id": c, "code": str(n)} for n, c in enumerate(CATS, start=1)], "stimuli": stim,
                "order": "person"}

    def default_texts(self, lang):
        words = {"A": tr(lang, "Roboter|Maschine|Android|Automat", "robot|machine|android|automaton"),
                 "B": tr(lang, "Mensch|Person|Nachbar|Kollegin", "human|person|neighbour|colleague"),
                 "X": tr(lang, "Freude|Liebe|Frieden|wunderbar", "joy|love|peace|wonderful"),
                 "Y": tr(lang, "Qual|schrecklich|furchtbar|böse", "agony|terrible|awful|evil")}
        cfg = self.default_config()
        return {"question": tr(lang, "Sortieren Sie die Begriffe, die in der Mitte erscheinen, so schnell wie möglich "
                                     "nach links oder rechts. Machen Sie einen Fehler, erscheint ein rotes ✗: dann "
                                     "einfach die andere Seite wählen.",
                               "Sort the words that appear in the middle to the left or right as quickly as you can. "
                               "If you make a mistake, a red ✗ appears: just choose the other side."),
                "categories": {"A": tr(lang, "Roboter", "Robots"), "B": tr(lang, "Menschen", "Humans"),
                               "X": tr(lang, "gut", "good"), "Y": tr(lang, "schlecht", "bad")},
                "stimuli": {s["id"]: words[s["cat"]].split("|")[int(s["code"][1:]) - 1] for s in cfg["stimuli"]}}

    # --- editor ---------------------------------------------------------------------------------------------

    def update_from_form(self, item, form, lang, structural):
        config, texts = dict(item.config), dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        lt["categories"] = {c: form.get(f"cat_{c}_label", (lt.get("categories") or {}).get(c, "")).strip()[:60]
                            for c in CATS}
        stexts = dict(lt.get("stimuli", {}))
        ids, cats, codes, labels = (form_ids(form.getlist("st_id")), form.getlist("st_cat"), form.getlist("st_code"),
                                    form.getlist("st_label"))
        if structural:
            stimuli, per = [], {c: 0 for c in CATS}
            for sid, cat, code, label in zip(ids, cats, codes, labels):
                if cat not in CATS or not (code.strip() or label.strip()) or per[cat] >= MAX_STIMULI:
                    continue
                per[cat] += 1
                sid = sid or _nid()
                stimuli.append({"id": sid, "cat": cat, "code": code.strip() or f"{cat.lower()}{per[cat]}"})
                stexts[sid] = label.strip()[:80]
            config["stimuli"] = stimuli
            config["categories"] = [{"id": c, "code": (form.get(f"cat_{c}_code") or str(n)).strip()[:64]}
                                    for n, c in enumerate(CATS, start=1)]
            config["order"] = "fixed" if form.get("order") == "fixed" else "person"
        else:
            known = {s["id"] for s in config.get("stimuli", [])}
            for sid, label in zip(ids, labels):
                if sid in known:
                    stexts[sid] = label.strip()[:80]
        lt["stimuli"] = stexts
        texts[lang] = lt
        item.config, item.texts = config, texts

    def categories(self, item, lang):
        ct = item.t(lang).get("categories", {})
        return {c["id"]: {**c, "label": ct.get(c["id"], "")} for c in read_cfg(item).get("categories", [])}

    def stimuli(self, item, lang, cat=None):
        st = item.t(lang).get("stimuli", {})
        return [{**s, "label": st.get(s["id"], "")} for s in read_cfg(item).get("stimuli", [])
                if cat is None or s["cat"] == cat]

    def words(self, item, lang, variables=None):
        """{stimulus id: [text, category]} for the participant page (shown as plain text by the script; attribute
        words in another colour than target words)."""
        return {s["id"]: [s["label"], s["cat"]] for s in self.stimuli(item, lang)}

    def labels(self, item, lang):
        return {c: v["label"] for c, v in self.categories(item, lang).items()}

    def validate(self, item):
        problems = []
        cats = self.categories(item, None)
        if any(not cats.get(c, {}).get("label") for c in CATS):
            problems.append(de_en("alle vier Kategorien brauchen eine Bezeichnung", "all four categories need a name"))
        if len({c["code"] for c in read_cfg(item).get("categories", [])}) != 4:
            problems.append(de_en("die vier Kategorien brauchen verschiedene Codes", "the four categories need different codes"))
        for c in CATS:
            if len(self.stimuli(item, None, c)) < 2:
                problems.append(de_en("„{name}“: mindestens zwei Begriffe", "“{name}”: at least two terms",
                                      name=cats.get(c, {}).get("label") or c))
        codes = [s["code"] for s in read_cfg(item).get("stimuli", [])]
        if len(set(codes)) != len(codes):
            problems.append(de_en("Codes der Begriffe sind nicht eindeutig", "the codes of the terms are not unique"))
        if any(not s["label"] for s in self.stimuli(item, None)):
            problems.append(de_en("Begriff ohne Text", "term without text"))
        return problems

    def check_warnings(self, item):
        few = [c for c in CATS if 2 <= len(self.stimuli(item, None, c)) < 4]
        return [de_en("IAT: Mit weniger als vier Begriffen je Kategorie wiederholen sich die Begriffe oft",
                      "IAT: with fewer than four terms per category the terms repeat often")] if few else []

    # --- the plan of one participant ---------------------------------------------------------------------------

    def order_of(self, item, seed):
        if read_cfg(item).get("order") == "fixed" or not seed:
            return 1
        return 1 + hashlib.sha256(f"{seed}:{item.id}:order".encode()).digest()[0] % 2

    def plan(self, item, seed, lang=None):
        """The seven blocks for one participant: [{"n", "left", "right", "trials": [[stimulus id, 0 left | 1 right]]}]."""
        rng = random.Random(f"{seed}:{item.id}") if seed else random.Random()
        order = self.order_of(item, seed)
        pool = {c: [s["id"] for s in self.stimuli(item, lang, c)] for c in CATS}
        blocks = []
        for n, count in enumerate(BLOCKS, start=1):
            left, right = sides(order, n)
            cats = left + right
            if len(cats) == 2:
                picks = _cycle(rng, [(c, s) for c in cats for s in pool[c]], count)
            else:  # combined: target and attribute trials alternate, each type balanced over its two categories
                tpool = [(c, s) for c in cats if c in TARGETS for s in pool[c]]
                apool = [(c, s) for c in cats if c in ATTRIBUTES for s in pool[c]]
                ts, ats = _cycle(rng, tpool, count // 2), _cycle(rng, apool, count - count // 2)
                start_with_target = rng.random() < 0.5
                picks = []
                for k in range(count):
                    if not (ts or ats):  # no terms (yet): the editor's preview shows the block without trials (C5)
                        break
                    use_t = (k % 2 == 0) == start_with_target
                    src = ts if use_t and ts else ats if ats else ts
                    picks.append(src.pop(0))
            blocks.append({"n": n, "left": list(left), "right": list(right),
                           "trials": [[s, 0 if c in left else 1] for c, s in picks]})
        return order, blocks

    # --- answers ---------------------------------------------------------------------------------------------

    def _parse(self, item, raw):
        data = load_json(raw or "", None)
        if not isinstance(data, dict) or data.get("o") not in (1, 2) or not isinstance(data.get("t"), list):
            return None
        cat_of = {s["id"]: s["cat"] for s in read_cfg(item).get("stimuli", [])}
        order, counts, trials = data["o"], [0] * 7, []
        for t in data["t"][:sum(BLOCKS)]:
            # block and error flag as whole numbers: true and 1.0 counted as block 1 or as an error (audit 2026-10-13,
            # TN-26)
            if not (isinstance(t, list) and len(t) == 4 and type(t[0]) is int and t[0] in range(1, 8) and t[1] in cat_of
                    and isinstance(t[2], (int, float)) and not isinstance(t[2], bool) and 0 <= t[2]
                    and type(t[3]) is int and t[3] in (0, 1)):
                return None
            left, right = sides(order, t[0])
            if cat_of[t[1]] not in left + right:
                return None
            counts[t[0] - 1] += 1
            trials.append([t[0], t[1], int(min(t[2], MAX_RT)), t[3]])  # a long pause: capped, dropped by scores()
        if counts != list(BLOCKS):
            return None
        return {"o": order, "t": trials}

    def parse_answer(self, item, form, lang="de", meta=None):
        if form.get(f"i{item.id}") == CANNOT:
            return CANNOT  # "kann ich nicht bearbeiten": an answer, so a required IAT lets the person go on
        answer = self._parse(item, form.get(f"i{item.id}"))
        seed = (meta or {}).get("pid") if isinstance(meta, dict) else None
        if answer is not None and seed is not None and answer["o"] != self.order_of(item, seed):
            # an order the page did not draw for this person (forged or a script error) would turn the sign of D
            # (audit 2026-10-12, N-12): unreadable, as an incomplete run
            answer = None
        if item.required and answer is None:
            raise AnswerError(tr(lang, "Bitte führen Sie die Aufgabe bis zum Ende durch.", "Please complete the task."))
        return answer

    def display_value(self, item, form, meta=None):
        if form.get(f"i{item.id}") == CANNOT:
            return CANNOT
        return self._parse(item, form.get(f"i{item.id}"))

    def is_missing(self, item, answer):
        return not answer

    # --- piping and conditions: the D score ---------------------------------------------------------------------

    def pipe_parts(self, item, lang):
        return [(f"{item.code}_d", "D-Wert")]

    def pipe_part_text(self, item, answer, name, lang):
        d = self.scores(answer).get("d") if name == f"{item.code}_d" else None
        return units.number(d, 2, lang) if d is not None else ""

    def scores(self, answer):
        """{"d", "d1", "d2", "valid", "err", "fast", "rt_comp", "rt_incomp"} of a complete answer, else {}."""
        if not isinstance(answer, dict) or not answer.get("t"):
            return {}
        order = answer["o"]
        combined = [t for t in answer["t"] if t[0] in (3, 4, 6, 7)]
        err = sum(t[3] for t in combined) / len(combined)
        kept = [t for t in combined if t[2] <= SLOW]
        # the share under 300 ms of the trials that remain after the long ones are deleted (Greenwald et al., 2003,
        # table 4; audit 2026-10-12, N-11)
        fast = sum(1 for t in kept if t[2] < FAST) / len(kept) if kept else 0.0
        by = {b: [t[2] for t in kept if t[0] == b] for b in (3, 4, 6, 7)}
        comp = [b for b in (3, 4, 6, 7) if compatible(order, b)]
        incomp = [b for b in (3, 4, 6, 7) if not compatible(order, b)]
        out = {"valid": int(fast <= FAST_SHARE), "err": round(100 * err, 1), "fast": round(100 * fast, 1),
               "rt_comp": round(statistics.mean(by[comp[0]] + by[comp[1]])) if by[comp[0]] + by[comp[1]] else None,
               "rt_incomp": round(statistics.mean(by[incomp[0]] + by[incomp[1]])) if by[incomp[0]] + by[incomp[1]] else None,
               "d": None, "d1": None, "d2": None}
        parts = []
        for practice in (True, False):  # (3, 6) and (4, 7)
            c = comp[0] if practice else comp[1]
            i = incomp[0] if practice else incomp[1]
            pooled = by[c] + by[i]
            if len(by[c]) < 2 or len(by[i]) < 2 or statistics.stdev(pooled) == 0:
                parts.append(None)
                continue
            parts.append((statistics.mean(by[i]) - statistics.mean(by[c])) / statistics.stdev(pooled))
        out["d1"], out["d2"] = (round(p, 3) if p is not None else None for p in parts)
        if out["valid"] and None not in parts:
            out["d"] = round((parts[0] + parts[1]) / 2, 3)
        return out

    def dummy_fields(self, item, rng, meta, answers=None):
        import json

        # the order the page drew for this participation, which parse_answer checks (audit 2026-10-12, N-12)
        order, blocks = self.plan(item, (meta or {}).get("pid") or rng.random() + 1)
        effect = rng.gauss(80, 60)  # ms slower in the incompatible blocks
        trials = []
        for b in blocks:
            slow = b["n"] in (3, 4, 6, 7) and not compatible(order, b["n"])
            for sid, _side in b["trials"]:
                trials.append([b["n"], sid, max(320, int(rng.gauss(720 + (effect if slow else 0), 140))),
                               int(rng.random() < 0.06)])
        return {f"i{item.id}": json.dumps({"o": order, "t": trials})}

    # --- export and results ---------------------------------------------------------------------------------

    COLUMNS = (("d", "D-Wert (positiv: schneller, wenn {A} mit {X} gepaart ist)"),
               ("d1", "D-Wert der Übungsblöcke (3 und 6)"), ("d2", "D-Wert der Testblöcke (4 und 7)"),
               ("valid", "Gültig: 1 = höchstens 10 % der Antworten unter 300 ms, 0 = kein D-Wert"),
               ("order", "Reihenfolge: 1 = {A} + {X} zuerst (Blöcke 3/4), 2 = {B} + {X} zuerst"),
               ("err", "Fehler in den kombinierten Blöcken (%)"),
               ("fast", "Antworten unter 300 ms in den kombinierten Blöcken (%)"),
               ("rt_comp", "Mittlere Reaktionszeit {A} + {X} (ms)"), ("rt_incomp", "Mittlere Reaktionszeit {B} + {X} (ms)"))

    def export_columns(self, item):
        names = {c: v["label"] or c for c, v in self.categories(item, None).items()}
        return [(f"{item.code}_{k}", label.format(**names)) for k, label in self.COLUMNS]

    def export_values(self, item, answer):
        if answer == CANNOT:
            return {f"{item.code}_{k}": CANNOT_CODE for k, _ in self.COLUMNS}
        s = self.scores(answer)
        out = {f"{item.code}_{k}": s.get(k) for k, _ in self.COLUMNS if k != "order"}
        out[f"{item.code}_order"] = answer.get("o") if isinstance(answer, dict) else None
        return out

    levels = ("metric",)

    def missing_codes(self, item, column):
        return (CANNOT_CODE,)

    def column_level(self, item, column):
        return "nominal" if column.endswith(("_valid", "_order")) else "metric"

    def result_column(self, item, column):
        if column.endswith(("_valid", "_order")):
            return "categorical", "", None
        if column.endswith(("_rt_comp", "_rt_incomp")):
            return "auto", "ms", None
        if column.endswith(("_err", "_fast")):
            return "auto", "%", None
        return "auto", "", None

    def codebook_info(self, item, column, lang, ui=None):
        cannot = in_lang(ui, "{code} = Diese Aufgabe kann ich nicht bearbeiten (vor dem Start gewählt, z. B. weil schnelle "
                             "Tastendrücke nicht möglich sind); zählt als fehlend, ohne D-Wert",
                         "{code} = I cannot do this task (chosen before the start, e.g. because fast key presses are not "
                         "possible); counts as missing, no D score", code=CANNOT_CODE)
        label = in_lang(lang, "{code} = nicht bearbeitbar", "{code} = cannot do the task", code=CANNOT_CODE)  # a value label
        if column != f"{item.code}_d":
            return "Zahl", label, [cannot]
        return "Zahl", in_lang(ui, "etwa −2 bis 2", "about −2 to 2") + "; " + label, [
            in_lang(ui, "verbesserter Algorithmus mit eingebauter Fehlerkorrektur (Zeit bis zur richtigen Antwort) nach ",
                    "improved algorithm with built-in error penalty (time to the correct answer) after ") + GREENWALD,
            in_lang(ui, "Durchgänge über 10 000 ms entfernt; leer, wenn mehr als 10 % der Antworten unter 300 ms lagen",
                    "trials over 10,000 ms removed; empty if more than 10 % of the answers were under 300 ms"),
            in_lang(ui, "Block 5 mit 28 Durchgängen gegen Reihenfolgeeffekte nach ",
                    "block 5 with 28 trials against order effects after ") + NOSEK, cannot]

    def source_groups(self, item):
        return [(de_en("IAT: D-Wert nach dem verbesserten Algorithmus", "IAT: D score after the improved algorithm"),
                 [GREENWALD]),
                (de_en("IAT: Block 5 mit 28 Durchgängen gegen Reihenfolgeeffekte",
                       "IAT: block 5 with 28 trials against order effects"), [NOSEK])]

    def results(self, item, table):
        """For the line under the figures of the results page: persons with a complete answer and those with a D
        score (valid), and those who said they cannot do the task (cannot, not in n); {} without answers."""
        given = [r for r in table if r.get(f"{item.code}_valid") not in (None, "")]
        cannot = sum(1 for r in given if str(r.get(f"{item.code}_valid")) == str(CANNOT_CODE))
        rows = [r for r in given if str(r.get(f"{item.code}_valid")) != str(CANNOT_CODE)]  # no run, no D score (E5)
        if not given:
            return {}
        return {"n": len(rows), "valid": sum(1 for r in rows if r.get(f"{item.code}_valid") in (1, "1")), "cannot": cannot}

    def method_text(self, item, answers, lang="de"):
        """The sentence of the method section (app/methods.py) on this element; answers: the stored answers of the
        final sample."""
        found = [s for s in (self.scores(a) for a in answers) if s]
        valid = sum(1 for s in found if s.get("d") is not None)
        fixed = read_cfg(item).get("order") == "fixed"
        cannot = sum(1 for a in answers if a == CANNOT)  # left out of n (E5)
        if lang == "en":
            return (f"Implicit Association Test (\"{item.code}\"): seven blocks, {sum(BLOCKS)} trials, block 5 with 28 "
                    f"trials (Nosek et al., 2005), " + ("fixed order (A with X first)" if fixed else
                                                        "order of the pairings counterbalanced per participant")
                    + "; D score by the improved algorithm with built-in error correction (Greenwald et al., 2003): "
                    f"blocks 3, 4, 6, 7, trials over 10,000 ms dropped, no score with more than 10 % of the responses "
                    f"under 300 ms; D for {valid} of n = {len(found)}"
                    + (f"; {cannot} more said before the start that they could not do the task and are left out"
                       if cannot else ""))
        return (f"„{item.code}“ war ein Impliziter Assoziationstest (IAT) in sieben Blöcken mit {sum(BLOCKS)} "
                f"Durchgängen, Block 5 mit 28 Durchgängen (Nosek et al., 2005); "
                + ("die Paarungen kamen in fester Reihenfolge (zuerst A mit X). " if fixed else
                   "die Reihenfolge der Paarungen wurde je Person gegenbalanciert. ")
                + "Ausgewertet wurde der D-Wert nach dem verbesserten Algorithmus mit eingebauter Fehlerkorrektur "
                "(Greenwald et al., 2003): Blöcke 3, 4, 6 und 7, Durchgänge über 10 000 ms entfernt, kein D-Wert bei "
                f"mehr als 10 % Antworten unter 300 ms. Einen D-Wert hatten {valid} von n = {len(found)} Personen."
                + (f" Weitere {cannot} gaben vor dem Start an, die Aufgabe nicht bearbeiten zu können, und blieben "
                   "unberücksichtigt." if cannot else ""))

    def method_sources(self, item):
        """The source groups of method_text."""
        return [("IAT: D-Wert nach dem verbesserten Algorithmus; Block 5 mit 28 Durchgängen", list(SOURCES))]

    def long_columns(self, item):
        return ["block", "trial", "stimulus", "category", "rt", "error", "compatible"]

    def long_rows(self, item, response):
        answer = (response.answers or {}).get(str(item.id))
        if not isinstance(answer, dict):
            return
        code = {s["id"]: s["code"] for s in read_cfg(item).get("stimuli", [])}
        cat = {s["id"]: s["cat"] for s in read_cfg(item).get("stimuli", [])}
        cat_code = {c["id"]: c["code"] for c in read_cfg(item).get("categories", [])}
        n = {}
        for b, sid, rt, err in answer.get("t") or []:
            n[b] = n.get(b, 0) + 1
            comp = int(compatible(answer["o"], b)) if b in (3, 4, 6, 7) else ""
            yield [b, n[b], code.get(sid, ""), cat_code.get(cat.get(sid), ""), rt, err, comp]

    def questionnaire_lines(self, item, lang):
        cats = self.categories(item, lang)
        words = "; ".join(f"{cats[c]['label']}: " + ", ".join(s["label"] for s in self.stimuli(item, lang, c)) for c in CATS)
        return [tr(lang, f"[IAT, sieben Blöcke mit {sum(BLOCKS)} Durchgängen; {words}]",
                   f"[IAT, seven blocks with {sum(BLOCKS)} trials; {words}]")]
