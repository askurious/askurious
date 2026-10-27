"""Likert scales: one or more statements rated on the same scale of 2 to 10 points (usually 5 or 7).

With several statements this is a matrix question; statements can be shown in random
order. Statements can be marked as reverse coded; the export recodes them
(points + 1 - value), so analysis never has to remember which items were reversed.

A statement can be an attention check (instructed response item, "Bitte wählen Sie hier „stimme eher zu“"):
config "check" names the scale point to choose. Such a row is exported as answered, never recoded, and stays
out of scale scores and reliability; app/quality.py counts the checks a person failed.
"""

from app import carry, item_paste, rows, scales
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en, english
from app.items.base import r_flag, r_id, r_int, r_list, r_maybe, r_one, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import CODE_RE, DONT_KNOW, DONT_KNOW_CODE, AnswerError, ItemType, dont_know, whole, read_cfg
from app.items.base import form_ids, new_id as _new_id
from app.items.base import prepared
from app.ui_i18n import pick, tr

POINTS = tuple(range(2, 11))  # 2 to 10 steps

AGREEMENT = {
    "de": {
        4: ["stimme überhaupt nicht zu", "stimme eher nicht zu", "stimme eher zu", "stimme voll und ganz zu"],
        5: ["stimme überhaupt nicht zu", "stimme eher nicht zu", "teils/teils", "stimme eher zu", "stimme voll und ganz zu"],
        6: ["stimme überhaupt nicht zu", "stimme nicht zu", "stimme eher nicht zu", "stimme eher zu", "stimme zu",
            "stimme voll und ganz zu"],
        7: ["stimme überhaupt nicht zu", "stimme nicht zu", "stimme eher nicht zu", "weder noch", "stimme eher zu",
            "stimme zu", "stimme voll und ganz zu"],
    },
    "en": {
        4: ["strongly disagree", "somewhat disagree", "somewhat agree", "strongly agree"],
        5: ["strongly disagree", "disagree", "neither agree nor disagree", "agree", "strongly agree"],
        6: ["strongly disagree", "disagree", "somewhat disagree", "somewhat agree", "agree", "strongly agree"],
        7: ["strongly disagree", "disagree", "somewhat disagree", "neither agree nor disagree", "somewhat agree",
            "agree", "strongly agree"],
    },
}


# Further answer scales. Each lists seven anchors from the negative to the positive pole;
# shorter scales take a subset (see SLOTS). "agree" keeps the hand-tuned AGREEMENT wording.
ANCHORS = {
    "applies": ("Zutreffen", "applies to me", {
        "de": ["trifft überhaupt nicht zu", "trifft nicht zu", "trifft eher nicht zu", "teils/teils",
               "trifft eher zu", "trifft zu", "trifft voll und ganz zu"],
        "en": ["not true at all", "not true", "somewhat untrue", "partly true", "somewhat true", "true",
               "completely true"]}),
    "likely": ("Wahrscheinlichkeit", "likelihood", {
        "de": ["sehr unwahrscheinlich", "unwahrscheinlich", "eher unwahrscheinlich", "weder noch",
               "eher wahrscheinlich", "wahrscheinlich", "sehr wahrscheinlich"],
        "en": ["very unlikely", "unlikely", "somewhat unlikely", "neither likely nor unlikely", "somewhat likely",
               "likely", "very likely"]}),
    "valence": ("Bewertung (negativ – positiv)", "evaluation", {
        "de": ["sehr negativ", "negativ", "eher negativ", "neutral", "eher positiv", "positiv", "sehr positiv"],
        "en": ["very negative", "negative", "somewhat negative", "neutral", "somewhat positive", "positive",
               "very positive"]}),
    "quality": ("Qualität (schlecht – gut)", "quality", {
        "de": ["sehr schlecht", "schlecht", "eher schlecht", "mittelmäßig", "eher gut", "gut", "sehr gut"],
        "en": ["very poor", "poor", "somewhat poor", "fair", "somewhat good", "good", "very good"]}),
    "satisfied": ("Zufriedenheit", "satisfaction", {
        "de": ["sehr unzufrieden", "unzufrieden", "eher unzufrieden", "weder noch", "eher zufrieden", "zufrieden",
               "sehr zufrieden"],
        "en": ["very dissatisfied", "dissatisfied", "somewhat dissatisfied", "neither satisfied nor dissatisfied",
               "somewhat satisfied", "satisfied", "very satisfied"]}),
    "important": ("Wichtigkeit", "importance", {
        "de": ["überhaupt nicht wichtig", "nicht wichtig", "eher unwichtig", "teils/teils", "eher wichtig",
               "wichtig", "sehr wichtig"],
        "en": ["not at all important", "not important", "somewhat unimportant", "moderately important",
               "somewhat important", "important", "very important"]}),
    "frequency": ("Häufigkeit (nie – immer)", "frequency", {
        "de": ["nie", "sehr selten", "selten", "manchmal", "oft", "sehr oft", "immer"],
        "en": ["never", "very rarely", "rarely", "sometimes", "often", "very often", "always"]}),
}
# 8 to 10 steps: per answer scale eleven graded labels, from the negative pole to the positive one: extreme,
# strong, plain, rather, slight | middle | slight, rather, plain, strong, extreme. Extreme, plain and rather are
# the wording of the seven-step scale; "strong" is used with 10 steps only, the middle with 9 steps only
# (FINE_SLOTS). Full verbal labels beyond 7 steps are hard to tell apart; these are suggestions to edit.
FINE = {
    "agree": {
        "de": ["stimme überhaupt nicht zu", "stimme weitgehend nicht zu", "stimme nicht zu", "stimme eher nicht zu",
               "stimme wenig zu", "weder noch", "stimme etwas zu", "stimme eher zu", "stimme zu", "stimme weitgehend zu",
               "stimme voll und ganz zu"],
        "en": ["strongly disagree", "largely disagree", "disagree", "somewhat disagree", "slightly disagree",
               "neither agree nor disagree", "slightly agree", "somewhat agree", "agree", "largely agree",
               "strongly agree"]},
    "applies": {
        "de": ["trifft überhaupt nicht zu", "trifft weitgehend nicht zu", "trifft nicht zu", "trifft eher nicht zu",
               "trifft wenig zu", "teils/teils", "trifft etwas zu", "trifft eher zu", "trifft zu", "trifft weitgehend zu",
               "trifft voll und ganz zu"],
        "en": ["not true at all", "largely untrue", "not true", "somewhat untrue", "slightly untrue", "partly true",
               "slightly true", "somewhat true", "true", "largely true", "completely true"]},
    "likely": {
        "de": ["sehr unwahrscheinlich", "ziemlich unwahrscheinlich", "unwahrscheinlich", "eher unwahrscheinlich",
               "etwas unwahrscheinlich", "weder noch", "etwas wahrscheinlich", "eher wahrscheinlich", "wahrscheinlich",
               "ziemlich wahrscheinlich", "sehr wahrscheinlich"],
        "en": ["very unlikely", "quite unlikely", "unlikely", "somewhat unlikely", "slightly unlikely",
               "neither likely nor unlikely", "slightly likely", "somewhat likely", "likely", "quite likely", "very likely"]},
    "valence": {
        "de": ["sehr negativ", "ziemlich negativ", "negativ", "eher negativ", "etwas negativ", "neutral", "etwas positiv",
               "eher positiv", "positiv", "ziemlich positiv", "sehr positiv"],
        "en": ["very negative", "quite negative", "negative", "somewhat negative", "slightly negative", "neutral",
               "slightly positive", "somewhat positive", "positive", "quite positive", "very positive"]},
    "quality": {
        "de": ["sehr schlecht", "ziemlich schlecht", "schlecht", "eher schlecht", "weniger gut", "mittelmäßig",
               "einigermaßen gut", "eher gut", "gut", "ziemlich gut", "sehr gut"],
        "en": ["very poor", "quite poor", "poor", "somewhat poor", "not so good", "fair", "fairly good", "somewhat good",
               "good", "quite good", "very good"]},
    "satisfied": {
        "de": ["sehr unzufrieden", "ziemlich unzufrieden", "unzufrieden", "eher unzufrieden", "etwas unzufrieden",
               "weder noch", "etwas zufrieden", "eher zufrieden", "zufrieden", "ziemlich zufrieden", "sehr zufrieden"],
        "en": ["very dissatisfied", "quite dissatisfied", "dissatisfied", "somewhat dissatisfied", "slightly dissatisfied",
               "neither satisfied nor dissatisfied", "slightly satisfied", "somewhat satisfied", "satisfied",
               "quite satisfied", "very satisfied"]},
    "important": {
        "de": ["überhaupt nicht wichtig", "ziemlich unwichtig", "nicht wichtig", "eher unwichtig", "weniger wichtig",
               "teils/teils", "etwas wichtig", "eher wichtig", "wichtig", "ziemlich wichtig", "sehr wichtig"],
        "en": ["not at all important", "quite unimportant", "not important", "somewhat unimportant",
               "slightly unimportant", "moderately important", "slightly important", "somewhat important", "important",
               "quite important", "very important"]},
    "frequency": {
        "de": ["nie", "fast nie", "sehr selten", "selten", "eher selten", "manchmal", "eher oft", "oft", "sehr oft",
               "fast immer", "immer"],
        "en": ["never", "almost never", "very rarely", "rarely", "fairly rarely", "sometimes", "fairly often", "often",
               "very often", "almost always", "always"]},
}
FINE_SLOTS = {8: (0, 2, 3, 4, 6, 7, 8, 10), 9: (0, 2, 3, 4, 5, 6, 7, 8, 10), 10: (0, 1, 2, 3, 4, 6, 7, 8, 9, 10)}
SLOTS = {2: (0, 6), 3: (0, 3, 6), 4: (0, 2, 4, 6), 5: (0, 2, 3, 4, 6), 6: (0, 1, 2, 4, 5, 6), 7: (0, 1, 2, 3, 4, 5, 6)}
ANCHOR_CHOICES = [("agree", "Zustimmung")] + [(k, v[0]) for k, v in ANCHORS.items()] + [("custom", "Eigene Beschriftung")]
# the same names for the English editor (Likert.anchor_names)
ANCHOR_NAMES_EN = {"agree": "Agreement", "applies": "Applies to me", "likely": "Likelihood",
                   "valence": "Evaluation (negative – positive)", "quality": "Quality (poor – good)",
                   "satisfied": "Satisfaction", "important": "Importance", "frequency": "Frequency (never – always)",
                   "custom": "Own labels"}


def anchor_labels(lang, points, anchors="agree"):
    """Standard labels of an answer scale ("custom" falls back to agreement), every step verbalized; with 8 to 10
    steps from the graded labels of FINE."""
    if points in FINE_SLOTS:
        fine = pick(FINE.get(anchors) or FINE["agree"], lang)
        return [fine[i] for i in FINE_SLOTS[points]]
    if anchors in ANCHORS:
        seven = pick(ANCHORS[anchors][2], lang)
    else:
        agree = pick(AGREEMENT, lang)
        if points in agree:
            return list(agree[points])
        seven = agree[7]
    if points in SLOTS:
        return [seven[i] for i in SLOTS[points]]
    return [seven[0]] + [""] * (points - 2) + [seven[-1]]


NUMBERINGS = ("1", "0", "center")  # 1..n, 0..n-1, -k..+k (user test 2026-10-27, point 9)


def numbering(kind, points):
    """The numbers shown beside the points: ["1", "2", …], ["0", …], ["−2", "−1", "0", "+1", "+2"], or empty strings."""
    if kind == "1":
        return [str(i) for i in range(1, points + 1)]
    if kind == "0":
        return [str(i) for i in range(points)]
    if kind == "center":  # an even number of points has no middle: −2 −1 +1 +2
        k = points // 2
        values = list(range(-k, k + 1)) if points % 2 else [v for v in range(-k, k + 1) if v]
        return ["0" if v == 0 else ("+" if v > 0 else "−") + str(abs(v)) for v in values]
    return [""] * points


def shown_labels(labels, labeling):
    """The labels as participants see them: with "endpoints" the steps in between stay empty (editor, saving,
    exports and codebooks use the same rule, so no wording of a step in between turns up anywhere)."""
    labels = list(labels or [])
    if labeling != "endpoints":
        return labels
    return [l if i in (0, len(labels) - 1) else "" for i, l in enumerate(labels)]


def resize_labels(labels, points):
    """Own labels for a new number of steps: both ends stay, steps in between are kept from the
    front (fewer steps) or added empty before the last one (more steps)."""
    labels = list(labels or [])
    if len(labels) < 2:
        return (labels + [""] * points)[:points]
    first, last, middle = labels[0], labels[-1], labels[1:-1]
    middle = (middle + [""] * points)[: max(points - 2, 0)]
    return ([first] + middle + [last])[:points] if points >= 2 else [first]


@register
class Likert(ItemType):
    key = "likert"
    ai_scale = True  # AI-worded items form a scale (ItemType.ai_scale): not validated, more items on request
    scale_keys = ("points",)  # fixed once real answers exist (ItemType.answer_shape)
    read_keys = ("dont_know",)  # switched off it would make -98 a value (audit 2026-10-10, N-3)
    import_rules = {"points": r_int(POINTS[0], POINTS[-1]), "anchors": r_one(*(k for k, _ in ANCHOR_CHOICES)),
                    "labeling": r_one("full", "endpoints"), "label_display": r_one("each", "once"), "randomize": r_flag,
                    "statements": r_rows(300, id=r_id, code=r_text(64), reverse=r_maybe(r_flag), subscale=r_maybe(r_text(100)),
                                         check=r_maybe(r_int(1, POINTS[-1])), when=rows.r_when, src=carry.r_src),
                    "carry": carry.r_carry, "dont_know": r_flag,
                    "numbers": r_one("", "1", "0", "center"), "emoji": r_list(10, r_text(8)), "own_scale": r_flag}
    uses_answers = True  # which statements a person sees can depend on earlier answers (config "when", app/rows.py)
    result_kind = 'scale'
    levels = ("ordinal", "metric")
    label = Both("Likert-Skala / Matrix", "Likert scale / matrix")
    short = Both("Aussagen auf einer Skala", "Statements on one scale")
    description = Both("Eine oder mehrere Aussagen auf derselben Skala mit 2 bis 10 Stufen (üblich 5 oder 7)",
                       "One or more statements on the same scale with 2 to 10 points (usually 5 or 7)")
    order = 30
    fact_sheet = FactSheet(
        "scales",
        keywords=["Matrix", "Grid", "Skala", "Scale", "Zustimmung", "Agreement", "Itembatterie", "Item battery"],
        can=[("2 bis 10 Stufen.", "2 to 10 points."),
             ("Beschriftung: Zustimmung, Zutreffen, Wahrscheinlichkeit, Bewertung, Qualität, Zufriedenheit, Wichtigkeit, Häufigkeit oder eigene.",
              "Labels: agreement, applies, likelihood, evaluation, quality, satisfaction, importance, frequency or your own."),
             ("Alle Stufen beschriftet oder nur die Enden; Beschriftung je Aussage oder einmal oben.",
              "All points labelled or only the ends; labels per statement or once at the top."),
             ("Aussagen in Zufallsreihenfolge, umgepolte Aussagen.",
              "Statements in random order, reversed statements."),
             ("Mehrdimensional: jede Aussage einer Subskala zuordnen, zum Beispiel die fünf Dimensionen der Big Five.",
              "Multidimensional: assign each statement to a subscale, for example the five dimensions of the Big Five.")],
        good_for=[("Einstellungen, Bewertungen und Konstrukte mit mehreren Aussagen",
                   "Attitudes, evaluations and constructs with several statements"),
                  ("Validierte Skalen aus „Konstrukte“", "Validated scales from “Constructs”")],
        not_for=[("Eine einzelne Einschätzung mit Sternen oder Smileys (dann Bewertung)",
                  "A single judgement with stars or smileys (then rating)"),
                 ("Mehrere Fragen je Zeile mit verschiedenen Antwortarten (dann Side-by-Side-Matrix)",
                  "Several questions per row with different kinds of answers (then side-by-side matrix)")],
        designs=["cross", "perception", "framing", "within", "vignettes", "panel"],
        examples=[("Vertrauen in KI mit fünf Aussagen auf einer 7-stufigen Zustimmungsskala",
                   "Trust in AI with five statements on a 7-point agreement scale")],
    )
    anchor_choices = ANCHOR_CHOICES

    def preview_empty(self, item):
        if read_cfg(item).get("statements"):
            return None
        return de_en("Die Vorschau erscheint, sobald die erste Aussage eingetragen ist.",
                     "The preview appears as soon as the first statement is entered."), None

    def anchor_names(self):
        """The answer scales as (key, name) in the language of the page (the select of the editor)."""
        return [(k, ANCHOR_NAMES_EN.get(k, name)) for k, name in ANCHOR_CHOICES] if english() else ANCHOR_CHOICES

    def one_click(self, item):
        return True  # continues once every statement has a value

    def default_level(self, item):
        # validated constructs are scored as means (quasi-interval), everything else counts as ordinal
        from app import constructs

        return "metric" if constructs.toolbox_key(item) else "ordinal"

    def default_config(self):
        return {
            "points": 5,
            "anchors": "agree",  # answer scale preset, see ANCHOR_CHOICES
            "labeling": "full",  # full | endpoints
            "label_display": "each",  # each (repeat per statement on phones) | once (sticky header)
            "randomize": False,
            "statements": [{"id": _new_id(), "code": "", "reverse": False}],
            "dont_know": False,  # a column "weiß nicht", set apart; counts as missing (V41)
        }

    def default_texts(self, lang):
        return {"question": "", "labels": list(pick(AGREEMENT, lang)[5]), "statements": {}}

    def default_labels(self, lang, points, anchors="agree"):
        return anchor_labels(lang, points, anchors)

    def shown_labels(self, labels, labeling):
        return shown_labels(labels, labeling)

    def anchor_presets(self, lang):
        """Preset labels for the editor: {anchors: {points: [labels]}}."""
        return {k: {p: anchor_labels(lang, p, k) for p in POINTS} for k, _ in ANCHOR_CHOICES if k != "custom"}

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config)
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        stexts = dict(lt.get("statements", {}))
        ids, codes = form_ids(form.getlist("stmt_id")), form.getlist("stmt_code")
        values = form.getlist("stmt_text")
        reverse = set(form.getlist("stmt_reverse"))

        old_labeling = config.get("labeling", "full")
        if structural:
            points = whole(str(form.get("points", config["points"])))
            if points not in POINTS:
                points = 5
            anchors = form.get("anchors", config.get("anchors", "agree"))
            if anchors not in dict(ANCHOR_CHOICES):
                anchors = "agree"
            old_anchors = config.get("anchors", "agree")
            if points != config.get("points") or (anchors != old_anchors and anchors != "custom"):
                # new preset: all languages get its standard labels. New number of steps with own
                # labels ("custom"): they are kept and only resized (ends stay), nothing is overwritten.
                # The edited language takes the labels from the form (the editor fills them in).
                for code_lang in list(texts) + ([lang] if lang not in texts else []):
                    current = (texts.get(code_lang) or {}).get("labels") if code_lang != lang else lt.get("labels")
                    if anchors == "custom" and current:
                        new_labels = resize_labels(current, points)
                    else:
                        new_labels = self.default_labels(code_lang, points, anchors if anchors != "custom" else "agree")
                    if code_lang == lang:
                        lt["labels"] = new_labels
                    else:
                        texts[code_lang] = {**texts[code_lang], "labels": new_labels}
            config["anchors"] = anchors
            config["points"] = points
            config["labeling"] = "endpoints" if form.get("labeling") == "endpoints" else "full"
            config["label_display"] = "once" if form.get("label_display") == "once" else "each"
            # numbers beside the words and an emoji per point: shown only, the codes stay 1..n (user test 2026-10-27, 9)
            config["numbers"] = form.get("numbers") if form.get("numbers") in NUMBERINGS else ""
            if form.get("own_scale_shown"):  # apart from the survey's standard on purpose (app/scale_default.py)
                config["own_scale"] = bool(form.get("own_scale"))
            emoji = [" ".join((e or "").split())[:8] for e in form.getlist("emoji")][:points]
            if any(emoji):
                config["emoji"] = emoji + [""] * (points - len(emoji))
            else:
                config.pop("emoji", None)
            config["randomize"] = bool(form.get("randomize"))
            config["dont_know"] = bool(form.get("dont_know"))
            statements = []
            before = {s["id"]: s for s in config.get("statements", [])}
            # the box "Aufmerksamkeitsprüfung einbauen" unticked in the editor: no checks, and the rows that were
            # checks go (their instruction is no statement of the scale); other forms keep the checks as they are
            no_checks = bool(form.get("checks_shown")) and not form.get("checks")
            for sid, code, text in zip(ids, codes, values):
                check = whole(form.get(f"stmt_check_{sid}", ""))
                if no_checks:
                    if (before.get(sid) or {}).get("check"):
                        continue
                    check = None
                if not (code.strip() or text.strip() or check):  # a check alone is a row: it gets the standard text
                    continue
                sid = sid or _new_id()
                if check is not None and 1 <= check <= points:
                    # an instruction has no direction: never recoded
                    statements.append({"id": sid, "code": code.strip(), "reverse": False, "check": check})
                else:
                    statements.append({"id": sid, "code": code.strip(), "reverse": sid in reverse})
                    subscale = scales.subscale_from_form(form, "stmt", sid, before.get(sid))
                    if subscale:  # an instrument with several dimensions (constructs, or ticked in the editor)
                        statements[-1]["subscale"] = subscale
                stexts[sid] = text.strip()
            # a list pasted into "Aussagen einfügen" goes to the end (app/item_paste.py; user test 2026-10-27)
            pasted = item_paste.parse(form.get("stmt_paste")) if (form.get("stmt_paste") or "").strip() else []
            used = {s["code"] for s in statements if s.get("code")}
            for code, line, reverse in pasted:
                if code in used or not CODE_RE.match(code or ""):
                    code = ""
                new_sid = _new_id()
                statements.append({"id": new_sid, "code": code, "reverse": reverse})
                stexts[new_sid] = line
                used.add(code)
            if pasted:
                from flask import flash

                flash(de_en("{n} Aussage übernommen.", "{n} statement added.", n=len(pasted)) if len(pasted) == 1 else
                      de_en("{n} Aussagen übernommen.", "{n} statements added.", n=len(pasted)), "success")
            # shown only to some: the condition of each statement (an attention check is for everyone)
            rows.from_form(item, [s for s in statements if "check" not in s], form, before, lang)
            config["statements"] = statements
            item.config = config
            carry.from_form(item, form)  # statements from the options of an earlier question (app/carry.py)
            config = dict(item.config)
        else:
            known = {s["id"] for s in config.get("statements", [])}
            for sid, text in zip(ids, values):
                if sid in known:
                    stexts[sid] = text.strip()

        # Scale labels from the form apply whenever they match the number of points
        labels = form.getlist("label")
        if len(labels) == config["points"]:
            lt["labels"] = [l.strip() for l in labels]
        lt["statements"] = stexts
        texts[lang] = lt
        if structural:
            self._fit_labeling(item, config, texts, old_labeling)
        item.config, item.texts = config, texts
        carry.sync(item)
        if structural:  # a check without a text gets the standard instruction, in every language of the item
            for s in config["statements"]:
                for code_lang, per in texts.items():
                    if s.get("check") and not (per.get("statements") or {}).get(s["id"]):
                        per.setdefault("statements", {})[s["id"]] = self.check_text(item, code_lang, s["check"])

    def _fit_labeling(self, item, config, texts, old_labeling):
        """Stored labels follow the kind of labelling, in every language: "endpoints" keeps the ends only (a
        step in between keeps no wording that nobody sees). Back to "full", empty steps in between take the
        wording of the answer scale, if one is chosen (not for own wording or a construct without one)."""
        anchors = config.get("anchors")
        for code_lang, per in texts.items():
            labels = (per or {}).get("labels")
            if not labels:
                continue
            if config["labeling"] == "endpoints":
                per["labels"] = shown_labels(labels, "endpoints")
            elif old_labeling == "endpoints" and anchors in dict(ANCHOR_CHOICES) and anchors != "custom":
                preset = self.default_labels(code_lang, config["points"], anchors)
                per["labels"] = [l or (preset[i] if 0 < i < len(labels) - 1 and i < len(preset) else l)
                                 for i, l in enumerate(labels)]

    def check_text(self, item, lang, point):
        """The standard instruction of an attention check: names the answer to choose, or its position where
        the scale point shows no label."""
        label = self.labels(item, lang)[point - 1]
        if label:
            return tr(lang, "Bitte wählen Sie hier „{label}“.", "Please select “{label}” here.", label=label)
        return tr(lang, "Bitte wählen Sie hier die Stufe {point} von links.", "Please select point {point} from the left here.",
                  point=point)

    @property
    def check_sources(self):
        """The sources the editor names next to the attention check, in full (there is no list of references)."""
        from app import quality

        return [quality.GUMMER, quality.SILBER, quality.MONTGOMERY]

    def checks(self, item):
        """{statement id: scale point to choose} of the attention checks."""
        points = read_cfg(item).get("points", 0)
        return {s["id"]: s["check"] for s in read_cfg(item).get("statements", [])
                if isinstance(s.get("check"), int) and 1 <= s["check"] <= points}

    def check_codes(self, item):
        """Export columns of the attention checks: they are no part of the scale."""
        checks = self.checks(item)
        return {self.statement_code(item, i, s) for i, s in enumerate(read_cfg(item).get("statements", [])) if s["id"] in checks}

    def statement_code(self, item, index, stmt):
        return stmt.get("code") or f"{item.code}_{index + 1}"

    def order_codes(self, item):
        if not read_cfg(item).get("randomize"):
            return None
        return {s["id"]: self.statement_code(item, i, s) for i, s in enumerate(read_cfg(item).get("statements", []))}

    def statements(self, item, lang, order=None, answers=None):
        """answers: the answers so far of the person who sees the page; the statements not shown to them are
        left out (app/rows.py). Without them every statement (editor, export, results)."""
        stexts = item.t(lang).get("statements", {})
        gone = rows.hidden(read_cfg(item).get("statements", []), answers, item.survey)
        found = [
            {**s, "text": carry.label(s, answers, stexts.get(s["id"], "")), "export_code": self.statement_code(item, i, s)}
            for i, s in enumerate(read_cfg(item).get("statements", [])) if s["id"] not in gone
        ]
        if order:
            rank = {sid: n for n, sid in enumerate(order)}
            found.sort(key=lambda r: rank.get(r["id"], len(rank)))
        return found

    def carry_view(self, item, lang):
        return carry.editor_view(item, lang)

    def row_conditions(self, item, lang):
        """The editor's part "Aussagen nur manchen zeigen" (not for attention checks: they are for everyone)."""
        from app.items.base import plain

        texts = item.text("statements", lang) or {}
        return rows.editor_view(item, [(s, plain(texts.get(s["id"], "")), self.statement_code(item, i, s))
                                       for i, s in enumerate(read_cfg(item).get("statements", [])) if "check" not in s and not s.get("src")], lang)

    def applies(self, item, answers):
        stmts = read_cfg(item).get("statements", [])
        return (not stmts or len(rows.hidden(stmts, answers, item.survey)) < len(stmts)) and carry.applies(item, answers)

    def missing_codes(self, item, column):
        """-77 (not shown) for the column of a statement that only some people see; -98 ("weiß nicht") where the
        matrix offers it."""
        dk = (DONT_KNOW_CODE,) if dont_know(item) else ()
        for i, s in enumerate(read_cfg(item).get("statements", [])):
            if rows.partial(s) and self.statement_code(item, i, s) == column:
                return (rows.NOT_SHOWN,) + dk
        return dk

    def remap_config(self, config, twin):
        config = carry.remap(self.key, config, twin)
        if not any(s.get("when") for s in config.get("statements", [])):
            return config
        return {**config, "statements": rows.remap(config["statements"], twin)}

    def result_column(self, item, column):
        """Results page: every scale point, labelled or not, so points nobody chose still show; reversed
        statements carry the labels in reverse order, like the recoded export."""
        lang = item.survey.default_language
        labels = self.labels(item, lang)
        stmt = next((s for s in self.statements(item, lang) if s["export_code"] == column), None)
        if stmt and stmt.get("reverse"):
            labels = labels[::-1]
        found = {str(n): label for n, label in enumerate(labels, start=1)}
        if dont_know(item):
            found[str(DONT_KNOW_CODE)] = tr(lang, "weiß nicht", "don't know")
        return self.result_kind, "", found

    # piping: each statement by its column; the text is the label of the chosen point (its number where the
    # point has no label, e.g. a scale with labelled ends only)
    def pipe_parts(self, item, lang):
        from app.items.base import plain

        texts = item.text("statements", lang) or {}
        return [(self.statement_code(item, i, s), plain(texts.get(s["id"], "")) or self.statement_code(item, i, s))
                for i, s in enumerate(read_cfg(item).get("statements", []))]

    def pipe_part_text(self, item, answer, name, lang):
        from app.items.base import plain

        for i, s in enumerate(read_cfg(item).get("statements", [])):
            if self.statement_code(item, i, s) == name:
                v = (answer or {}).get(s["id"]) if isinstance(answer, dict) else None
                if v == DONT_KNOW:
                    return tr(lang, "weiß nicht", "don't know")
                if not isinstance(v, int) or not 1 <= v <= read_cfg(item)["points"]:
                    return ""
                return plain(self.labels(item, lang)[v - 1]) or str(v)
        return ""

    def point_marks(self, item):
        """[(number, emoji)] per point as participants see them beside the labels; the codes stay 1..n."""
        cfg = read_cfg(item)
        points = cfg["points"]
        numbers = numbering(cfg.get("numbers") or "", points)
        emoji = list(cfg.get("emoji") or [])
        emoji = (emoji + [""] * points)[:points]
        return list(zip(numbers, emoji))

    def labels(self, item, lang):
        labels = item.t(lang).get("labels") or self.default_labels(
            lang or "de", read_cfg(item)["points"], read_cfg(item).get("anchors", "agree"))
        labels = (labels + [""] * read_cfg(item)["points"])[: read_cfg(item)["points"]]
        return shown_labels(labels, read_cfg(item).get("labeling"))

    def check_warnings(self, item):
        return super().check_warnings(item) + scales.subscale_warnings(item)

    def validate(self, item):
        problems = []
        stmts = read_cfg(item).get("statements", [])
        if not stmts:
            problems.append(de_en("mindestens eine Aussage", "at least one statement"))
        stexts = item.text("statements") or {}
        if any(not stexts.get(s["id"]) for s in stmts) and len(stmts) > 1:
            problems.append(de_en("Aussage ohne Text", "statement without text"))
        codes = [self.statement_code(item, i, s) for i, s in enumerate(stmts)]
        if len(set(codes)) != len(codes):
            problems.append(de_en("Variablennamen der Aussagen sind nicht eindeutig", "the variable names of the statements are not unique"))
        bad = [c for c in codes if not CODE_RE.match(c)]
        if bad:
            problems.append(de_en(
                "Variablenname „{bad}“ einer Aussage muss mit einem Buchstaben beginnen "
                "(z. B. {code}_{bad}; leer lassen ergibt {code}_1, {code}_2, …)",
                "Variable name “{bad}” of a statement must start with a letter "
                "(e.g. {code}_{bad}; leaving it empty gives {code}_1, {code}_2, …)", bad=bad[0], code=item.code))
        return (problems + rows.problems(item, stmts, lambda i, s: self.statement_code(item, i, s), "Aussage")
                + carry.problems(item))

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        points = read_cfg(item)["points"]
        answer, missing = {}, 0
        gone = rows.hidden(read_cfg(item).get("statements", []), answers, item.survey)
        if gone:
            answer[rows.HIDDEN] = sorted(gone)  # not shown is not the same as left out (export: -77)
        for s in read_cfg(item).get("statements", []):
            if s["id"] in gone:
                continue
            v = form.get(f"i{item.id}_{s['id']}", "")
            if whole(v) is not None and 1 <= whole(v) <= points:
                answer[s["id"]] = whole(v)
            elif v == DONT_KNOW and dont_know(item):  # an answer, but no value of the scale
                answer[s["id"]] = DONT_KNOW
            else:
                missing += 1
        if item.required and missing:
            # how many are still open, beside the marked rows (W56)
            raise AnswerError(tr(lang, "Bitte beantworten Sie alle Aussagen (noch offen: {n}).",
                                 "Please answer all statements ({n} still open).", n=missing))
        return answer

    def display_value(self, item, form, meta=None):
        out = {}
        for s in read_cfg(item).get("statements", []):
            v = form.get(f"i{item.id}_{s['id']}", "")
            if whole(v) is not None:
                out[s["id"]] = whole(v)
            elif v == DONT_KNOW:
                out[s["id"]] = DONT_KNOW
        return out

    def is_missing(self, item, answer):
        gone = rows.hidden_in(answer)
        return any((answer or {}).get(s["id"]) is None for s in read_cfg(item).get("statements", []) if s["id"] not in gone)

    def export_columns(self, item):
        cols = []
        for i, s in enumerate(read_cfg(item).get("statements", [])):
            label = (item.text("statements") or {}).get(s["id"], "")
            cols.append((self.statement_code(item, i, s), label + (" (rekodiert)" if s.get("reverse") else "")
                         + (f" [Aufmerksamkeitsprüfung, richtig: {s['check']}]" if s["id"] in self.checks(item) else "")
                         + (f" [nur gezeigt, wenn {rows.text(item, s)}; {rows.NOT_SHOWN} = nicht gezeigt]" if s.get("when") else "")
                         + (" [KI-generiert, nicht validiert]" if read_cfg(item).get("ai") and self.ai_scale else "")))
        return cols

    def export_values(self, item, answer):
        # the column per statement once per export, not per participation (code review 2026-10-07, R4)
        points, stmts = prepared(item, "likert_export", lambda: (read_cfg(item)["points"], [
            (s["id"], self.statement_code(item, i, s), bool(s.get("reverse")))
            for i, s in enumerate(read_cfg(item).get("statements", []))]))
        out = {}
        gone = rows.hidden_in(answer)
        got = answer or {}
        for sid, code, reverse in stmts:
            v = got.get(sid)
            if v == DONT_KNOW:
                v = DONT_KNOW_CODE
            elif v is not None and reverse:
                v = points + 1 - v
            out[code] = rows.NOT_SHOWN if sid in gone else v
        return out
