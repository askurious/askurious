"""Leitfrage: the element of an interview guide (app/interview.py, concept interviewleitfaden-konzept.md 3.3).

Four parts after the guide scheme of Helfferich (2011): the narrative prompt, read out word for word (stored as the
"question", so lists and documents show it like any question text), the content aspects to tick when they come up
(Check), maintaining questions without content ("Und dann?"), and concrete questions with a fixed wording for the
aspects that did not come up by themselves. A hint for the interviewer, the tick "Kernfrage" (asked in every
interview, Gläser & Laudel 2010) and a planned duration complete it.

A Leitfrage can be indented as an "Unterfrage" (`sub`, concept unterfragen-konzept, Ralf 2026-10-18): a follow-up that
belongs to the nearest Leitfrage above it that is not indented (app/interview.py, outline). It may say when to ask it
("when": "falls zutreffend"), has no duration of its own (it counts towards its Leitfrage) and is never a Kernfrage.

Nachfragen and conditions of the concrete questions (concept bedingungen-leitfaden, A-D, Ralf 2026-10-19): a row of
"Konkrete Fragen" can be indented as a "Nachfrage" of the row above it (`probe_sub`, one level, like an Unterfrage),
with its own "Wann stellen?" (`probe_when`). In the interview view a concrete question with Nachfragen has a switch
"trifft zu / trifft nicht zu" (B, stored with the interview as answers[id]["fits"]); "trifft nicht zu" pales its
Nachfragen. A row may be tied to a condition (`probe_if`): to an aspect of the Leitfrage (C, {"aspect", "mode":
"absent" | "present"}: "nur, wenn „Pausen“ nicht von selbst kam") or to an answer of the Kurzfragebogen (D,
{"item": its id, "code": an answer code}) or to an earlier concrete question of the same Leitfrage (E, concept
bedingung-konkrete-frage, Ralf 2026-10-22: {"probe": its row id, "fits": true | false}: "nur, wenn K1 zutraf"). An
Unterfrage may carry D or E (`case`), E naming a concrete question of its Leitfrage. A concrete question that a
condition names gets the switch "trifft zu" too (`switched`); only concrete questions, not Nachfragen, can be named,
counted K1, K2, … as in the export. Nothing is hidden: a row
whose condition does not hold is only paled, the interviewer decides (E2). No answer of the interview decides
anything on the server; the interview view reads the ticks, the switches and the Kurzfragebogen (interview.js).

No answer is collected: Askurious does not store anything from an interview (Ralf, 2026-10-04). The element exists
only in interview guides; the editor offers it nowhere else.

  config  {"aspects": [id, ...], "keep": [id, ...], "probe": [id, ...], "core": bool, "minutes": int, "sub": bool,
           "probe_sub": [id, ...], "probe_if": {id: condition}, "case": condition}
  texts   {lang: {"question": prompt, "aspects": {id: text}, "keep": {id: text}, "probe": {id: text}, "hint": text,
                  "when": text, "probe_when": {id: text}}}
"""
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import ItemType, Unfit, form_ids, new_id, plain, r_dict, r_flag, r_id, r_list, r_maybe, r_one, r_text
from app.lang import Both, de_en

LISTS = (("aspects", "aspect", "Inhaltliche Aspekte (Check)"), ("keep", "keep", "Aufrechterhaltungsfragen"),
         ("probe", "probe", "Konkrete Fragen"))
MAX_ROWS = 30
MAX_TEXT = 1000
MINUTES = (5, 10, 15, 20, 30, 45)
MAX_WHEN = 200
# what the field "Wann stellen?" of an Unterfrage suggests (a datalist: any other wording may be typed)
WHEN_SUGGESTIONS = (("falls zutreffend", "if applicable"), ("bei Bedarf", "if needed"), ("nur wenn …", "only if …"))
HELFFERICH = ("Helfferich, C. (2011). Die Qualität qualitativer Daten: Manual für die Durchführung qualitativer "
              "Interviews (4. Aufl.). VS Verlag für Sozialwissenschaften. https://doi.org/10.1007/978-3-531-92076-4")
GLAESER_LAUDEL = ("Gläser, J., & Laudel, G. (2010). Experteninterviews und qualitative Inhaltsanalyse als Instrumente "
                  "rekonstruierender Untersuchungen (4. Aufl.). VS Verlag für Sozialwissenschaften. "
                  "https://doi.org/10.1007/978-3-531-91538-8")
HOPF = ("Hopf, C. (1978). Die Pseudo-Exploration: Überlegungen zur Technik qualitativer Interviews in der "
        "Sozialforschung. Zeitschrift für Soziologie, 7(2), 97–115. https://doi.org/10.1515/zfsoz-1978-0201")


MODES = ("absent", "present")  # an aspect that did not come up by itself / one that did (C)
FITS = (("yes", True), ("no", False))  # "trifft zu" / "trifft nicht zu" of an earlier concrete question (E)


def _case(value):
    """A condition on an answer of the Kurzfragebogen (D): {"item": its id, "code": an answer code}."""
    if not isinstance(value, dict) or set(value) != {"item", "code"} or isinstance(value["item"], bool)             or not isinstance(value["item"], int) or not isinstance(value["code"], str) or not 0 < len(value["code"]) <= 40:
        raise Unfit
    return {"item": value["item"], "code": value["code"]}


def _probe_ref(value):
    """A condition on an earlier concrete question (E): {"probe": its row id, "fits": bool}."""
    if set(value) != {"probe", "fits"} or not isinstance(value["fits"], bool):
        raise Unfit
    return {"probe": r_id(value["probe"]), "fits": value["fits"]}


def _sub_case(value):
    """The condition of an Unterfrage: an answer of the Kurzfragebogen (D) or a concrete question of its Leitfrage (E)."""
    if isinstance(value, dict) and "probe" in value:
        return _probe_ref(value)
    return _case(value)


def _probe_cond(value):
    """A condition of a concrete question: an aspect of the Leitfrage (C), an answer of the Kurzfragebogen (D) or an
    earlier concrete question (E)."""
    if isinstance(value, dict) and "probe" in value:
        return _probe_ref(value)
    if isinstance(value, dict) and "aspect" in value:
        if set(value) != {"aspect", "mode"} or value["mode"] not in MODES:
            raise Unfit
        return {"aspect": r_id(value["aspect"]), "mode": value["mode"]}
    return _case(value)


def _probe_conds(value):
    if not isinstance(value, dict) or len(value) > MAX_ROWS:
        raise Unfit
    return {r_id(k): _probe_cond(v) for k, v in value.items()}


def short_choices(survey, lang=None):
    """[(item, [(code, label)])] of the Kurzfragebogen's questions with answers to pick (single and multiple choice,
    Bundesland): what a condition of a concrete question or an Unterfrage can name (D). The Postskript is written
    after the interview and offers nothing."""
    from app import flow, interview

    block = interview.form_block(survey, "short") if survey is not None else None
    if block is None:
        return []
    out = []
    for item in block.items:
        codes = flow.answer_codes(item, lang) if item.kind.collects_answer else []
        if codes:
            out.append((item, codes))
    return out


def _aspect_texts(value):
    """{aspect id: definition} of a survey file's category definitions (import_rules)."""
    if not isinstance(value, dict) or len(value) > MAX_ROWS:
        raise Unfit
    return {r_id(k): r_text(500)(v) for k, v in value.items()}


def _probe_line(row, sub=False):
    """A concrete question for the questionnaire text: "↳" before a Nachfrage, its notes in brackets."""
    notes = [n for n in (row["when"], row["cond_text"]) if n]
    return ("↳ " if sub else "") + plain(row["text"]) + (f" ({'; '.join(plain(n) for n in notes)})" if notes else "")


@register
class GuideQuestion(ItemType):
    key = "guide_question"
    label = Both("Leitfrage", "Guide question")
    description = Both("Erzählaufforderung mit Aspekten, Aufrechterhaltungsfragen und konkreten Fragen (Interviewleitfaden)",
                       "Narrative prompt with aspects, maintenance questions and specific questions (interview guide)")
    order = 3
    fact_sheet = FactSheet(
        "structure",
        keywords=["Interview", "Leitfaden", "Guide", "Erzählaufforderung", "Narrative prompt"],
        can=[("Erzählaufforderung mit Aspekten, Aufrechterhaltungsfragen und konkreten Fragen.",
              "Narrative prompt with aspects, maintenance questions and specific questions."),
             ("Als Unterfrage eingerückt unter die Leitfrage darüber, mit „Wann stellen?“ (z. B. falls zutreffend).",
              "Indented as a sub-question under the guide question above, with “When to ask?” (e.g. if applicable).")],
        good_for=[("Leitfadeninterviews: Erfahrungen, Gründe und Bedeutungen verstehen",
                   "Guided interviews: understanding experiences, reasons and meanings")],
        not_for=[("Fragebögen (sie gibt es nur in Interviewleitfäden)",
                  "Questionnaires (it exists only in interview guides)")],
        designs=["interview"],
        examples=[("Erzählen Sie mal, wie Sie zu Ihrem Beruf gekommen sind.",
                   "Tell me how you came to your profession.")],
    )
    collects_answer = False
    has_answer_mode = False
    interview_only = True  # offered in interview guides only (app/interview.py), see editor.add_item
    when_suggestions = WHEN_SUGGESTIONS  # the field "Wann stellen?" of an Unterfrage (editor.html)
    panel_template = "items/guide_question/panel.html"  # "Leitfrage mit KI ergänzen" (app/interview_ai.py)
    result_kind = None
    import_rules = {"aspects": r_list(MAX_ROWS, r_id), "keep": r_list(MAX_ROWS, r_id), "probe": r_list(MAX_ROWS, r_id),
                    "core": r_flag, "minutes": r_one(*MINUTES), "sub": r_flag,
                    "probe_sub": r_list(MAX_ROWS, r_id), "probe_if": _probe_conds, "case": _sub_case,
                    # the definitions for the category system (.qdc) taken over from the AI assistant (interview_ai, A5)
                    "category": r_dict(definition=r_maybe(r_text(1000)), delimit=r_maybe(r_text(1000)),
                                       aspects=r_maybe(_aspect_texts))}

    def default_config(self):
        return {"aspects": [], "keep": [], "probe": [], "core": False, "minutes": 10}

    def default_texts(self, lang):
        return {"question": "", "aspects": {}, "keep": {}, "probe": {}, "hint": "", "when": ""}

    def rows(self, item, part, lang=None):
        """[(id, text)] of one list ("aspects", "keep", "probe") in its order; rows without a text are left out."""
        texts = item.text(part, lang) or {}
        return [(rid, texts.get(rid, "")) for rid in (item.config or {}).get(part, []) if texts.get(rid, "").strip()]

    def is_sub(self, item):
        """Indented as an Unterfrage (whether it has a Leitfrage above to belong to: interview.outline)."""
        return bool((item.config or {}).get("sub"))

    def minutes(self, item):
        """The planned minutes; an Unterfrage has none of its own (they count towards its Leitfrage)."""
        if self.is_sub(item):
            return 0
        value = (item.config or {}).get("minutes")
        return value if value in MINUTES else 10

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        mine = dict(texts.get(lang) or {})
        mine["question"] = form.get("question", "").strip()[:4000]
        mine["hint"] = form.get("hint", "").strip()[:MAX_TEXT]
        if self.is_sub(item):
            mine["when"] = " ".join(form.get("when", "").split())[:MAX_WHEN]
        config = dict(item.config or {})
        if structural and self.is_sub(item) and "case" in form:  # "nur stellen, wenn …" of an Unterfrage (D)
            case = self.read_cond(item, form.get("case", ""), aspects=False)
            if case:
                config["case"] = case
            else:
                config.pop("case", None)
        for part, field, _ in LISTS:
            ids = form_ids(form.getlist(f"{field}_id"))  # audit 2026-10-13, XS-2
            labels = form.getlist(f"{field}_label")
            kept, order = dict(mine.get(part) or {}), []
            for rid, label in zip(ids, labels):
                label = " ".join(label.split())[:MAX_TEXT]
                rid = rid if rid and len(rid) <= 20 and rid.replace("_", "").isalnum() else new_id()
                if structural:
                    if not label:
                        continue
                    order.append(rid)
                elif rid not in (config.get(part) or []):
                    continue  # another language, or a running survey: only the wording of existing rows
                kept[rid] = label
            if structural:
                config[part] = order[:MAX_ROWS]
                kept = {rid: kept[rid] for rid in config[part]}
            mine[part] = kept
            if part == "probe":
                self._probe_extras(item, form, ids, config, mine, structural)
        if structural and not self.is_sub(item):  # an Unterfrage keeps neither (indenting cleared core)
            config["core"] = bool(form.get("core"))
            minutes = form.get("minutes", type=int)
            config["minutes"] = minutes if minutes in MINUTES else 10
        if structural:
            item.config = config
        texts[lang] = mine
        item.texts = texts

    def _probe_extras(self, item, form, ids, config, mine, structural):
        """The Nachfragen, their "Wann stellen?" and the conditions of the concrete questions, read from the rows of
        the form in the same order as their ids (probe_sub "1"/"", probe_when, probe_if). A form without these
        fields (an older page) leaves them as they were, for the rows that are still there."""
        rows = (config.get("probe") or []) if structural else ((item.config or {}).get("probe") or [])
        whens = dict(mine.get("probe_when") or {})
        if "probe_when" in form:
            for rid, when in zip(ids, form.getlist("probe_when")):
                whens[rid] = " ".join(when.split())[:MAX_WHEN]
        mine["probe_when"] = {rid: text for rid, text in whens.items() if rid in rows and text}
        if not structural:
            return
        subs, conds = list(config.get("probe_sub") or []), dict(config.get("probe_if") or {})
        if "probe_sub" in form:
            subs = [rid for rid, flag in zip(ids, form.getlist("probe_sub")) if flag == "1"]
        if "probe_if" in form:
            conds = {}
            for rid, value in zip(ids, form.getlist("probe_if")):
                cond = self.read_cond(item, value)
                if cond:
                    conds[rid] = cond
        # a Nachfrage needs a concrete question above it (the first row never is one)
        config["probe_sub"] = [rid for n, rid in enumerate(rows) if n and rid in subs]
        config["probe_if"] = {rid: cond for rid, cond in conds.items() if rid in rows
                              and (not isinstance(cond, dict) or "aspect" not in cond or cond["aspect"] in (config.get("aspects") or []))
                              and (not isinstance(cond, dict) or cond.get("probe") != rid)}
        if not config["probe_sub"]:
            config.pop("probe_sub")
        if not config["probe_if"]:
            config.pop("probe_if")

    def read_cond(self, item, value, aspects=True):
        """A condition from the value of the editor's choice "nur stellen, wenn …" ("a:<aspect id>:absent",
        "k:<item id>:<code>"), or None for "immer" or a value that names nothing of this guide. The aspect is
        checked against the rows of the same form afterwards (_probe_extras)."""
        kind, _, rest = (value or "").partition(":")
        if kind == "a" and aspects:
            aid, _, mode = rest.rpartition(":")
            if aid and mode in MODES:
                return {"aspect": aid, "mode": mode}
        if kind == "k":
            iid, _, code = rest.partition(":")
            for short, codes in short_choices(item.survey):
                if str(short.id) == iid and code in [c for c, _ in codes]:
                    return {"item": short.id, "code": code}
        if kind == "p":  # an earlier concrete question (E); for an Unterfrage one of its Leitfrage
            rid, _, fit = rest.rpartition(":")
            if rid and len(rid) <= 20 and rid.replace("_", "").isalnum() and fit in dict(FITS):
                if aspects or rid in [r for r, _, _ in self.numbered(self.lead(item))]:
                    return {"probe": rid, "fits": dict(FITS)[fit]}
        return None

    @staticmethod
    def cond_value(cond):
        """The value of a stored condition in the editor's choice (read_cond backwards); "" for none."""
        if not isinstance(cond, dict):
            return ""
        if "aspect" in cond:
            return f"a:{cond['aspect']}:{cond.get('mode')}"
        if "probe" in cond:
            return f"p:{cond['probe']}:{'yes' if cond.get('fits') else 'no'}"
        return f"k:{cond.get('item')}:{cond.get('code')}"

    @staticmethod
    def earlier_group():
        """The title of the group "Frühere konkrete Frage" in the editor's choice (E)."""
        return de_en("Frühere konkrete Frage", "Earlier specific question")

    def numbered(self, item, lang=None):
        """[(row id, n, text)] of the concrete questions without the Nachfragen, n counting from 1 (K1, K2, … as in
        the export columns <code>_k<n>_zu); [] for no item."""
        if item is None:
            return []
        subs = set((item.config or {}).get("probe_sub") or [])
        rows = [(rid, text) for n, (rid, text) in enumerate(self.rows(item, "probe", lang)) if not (n and rid in subs)]
        return [(rid, n, text) for n, (rid, text) in enumerate(rows, start=1)]

    def lead(self, item):
        """The Leitfrage an Unterfrage belongs to (app/interview.py, outline), else None."""
        from app import interview

        if not self.is_sub(item) or item.block is None:
            return None
        return next((main for i, main in interview.outline(item.block) if i.id == item.id), None)

    def fit_choices(self, owner, lang=None, before=None):
        """[(value, label)] of the concrete questions of `owner` a condition can name (E), those above the row
        `before` (all for None or a new row)."""
        out = []
        for rid, n, text in self.numbered(owner, lang):
            if rid == before:
                break
            short = plain(text) if len(plain(text)) <= 50 else plain(text)[:49].rstrip() + "…"
            out += [(f"p:{rid}:yes", de_en("K{n} „{q}“ trifft zu", "Q{n} “{q}” applies", n=n, q=short)),
                    (f"p:{rid}:no", de_en("K{n} „{q}“ trifft nicht zu", "Q{n} “{q}” does not apply", n=n, q=short))]
        return out

    def cond_choices(self, item, lang=None, aspects=True, before=None):
        """What the editor's choice "nur stellen, wenn …" offers: [(group, [(value, label)])]: the aspects of this
        Leitfrage (C), the answers of the Kurzfragebogen's choice questions (D) and the concrete questions above
        the row `before` (E; aspects False: an Unterfrage, the concrete questions of its Leitfrage)."""
        out = []
        earlier = self.fit_choices(item, lang, before) if aspects else self.fit_choices(self.lead(item), lang)
        if earlier:
            out.append((self.earlier_group(), earlier))
        if aspects:
            rows = self.rows(item, "aspects", lang)
            if rows:
                out.append((de_en("Aspekt dieser Leitfrage", "Aspect of this guide question"),
                            [(f"a:{rid}:{mode}", label) for rid, text in rows
                             for mode, label in (("absent", de_en("„{a}“ kam nicht von selbst", "“{a}” did not come up by itself", a=plain(text))),
                                                 ("present", de_en("„{a}“ wurde angesprochen", "“{a}” came up", a=plain(text))))]))
        shorts = short_choices(item.survey, lang)
        if shorts:
            out.append((de_en("Kurzfragebogen (Fallmerkmal)", "Short questionnaire (case feature)"),
                        [(f"k:{short.id}:{code}", f"{self.short_label(short, lang)}: {label}")
                         for short, codes in shorts for code, label in codes]))
        return out

    def cond_values(self, item, lang=None, aspects=True, before=None):
        """The values cond_choices offers (to tell a stored condition that names nothing any more)."""
        return [value for _, options in self.cond_choices(item, lang, aspects, before) for value, _ in options]

    @staticmethod
    def short_label(short, lang=None):
        """A question of the Kurzfragebogen as a condition names it: its text, shortened."""
        text = plain(short.text("question", lang) or "") or short.code
        return text if len(text) <= 50 else text[:49].rstrip() + "…"

    def cond_text(self, item, cond, lang=None, ui=None, owner=None):
        """A condition in words ("falls „Pausen“ nicht von selbst kam", "falls „ja“ bei „Erwerbstätig?“", "falls K1
        zutraf"), "" for none or one that names nothing any more. ui: the words around it in this language
        ("de"/"en"), for a document; None: the language of the page. owner: the element whose concrete questions a
        condition E names (default: item itself)."""
        def say(de, en, **values):
            if ui:
                return (en if ui == "en" else de).format(**values)
            return de_en(de, en, **values)

        if not isinstance(cond, dict):
            return ""
        if "aspect" in cond:
            text = dict(self.rows(item, "aspects", lang)).get(cond["aspect"])
            if not text:
                return ""
            if cond.get("mode") == "present":
                return say("falls „{a}“ angesprochen wurde", "if “{a}” came up", a=plain(text))
            return say("falls „{a}“ nicht von selbst kam", "if “{a}” did not come up by itself", a=plain(text))
        if "probe" in cond:
            found = next(((n, text) for rid, n, text in self.numbered(owner or item, lang) if rid == cond["probe"]), None)
            if found is None:
                return ""
            short = plain(found[1]) if len(plain(found[1])) <= 50 else plain(found[1])[:49].rstrip() + "…"
            if cond.get("fits"):
                return say("falls K{n} „{q}“ zutraf", "if Q{n} “{q}” applied", n=found[0], q=short)
            return say("falls K{n} „{q}“ nicht zutraf", "if Q{n} “{q}” did not apply", n=found[0], q=short)
        for short, codes in short_choices(item.survey, lang):
            if short.id == cond.get("item"):
                label = dict(codes).get(cond.get("code"))
                if label:
                    return say("falls „{a}“ bei „{q}“", "if “{a}” for “{q}”", q=self.short_label(short, lang), a=label)
        return ""

    def cond_label(self, item, cond, lang=None, owner=None):
        """A condition as the line over a row of the interview view says it, in the words of the switch: "nur wenn K2
        trifft zu" (of the Leitfrage, when `owner` is another element), else "nur falls …"; "" for none."""
        if isinstance(cond, dict) and "probe" in cond:
            n = next((n for rid, n, _ in self.numbered(owner or item, lang) if rid == cond["probe"]), None)
            if n is None:
                return ""
            if owner is not None and owner is not item:
                if cond.get("fits"):
                    return de_en("nur wenn K{n} der Leitfrage trifft zu", "only if Q{n} of the guide question applies", n=n)
                return de_en("nur wenn K{n} der Leitfrage trifft nicht zu", "only if Q{n} of the guide question does not apply", n=n)
            if cond.get("fits"):
                return de_en("nur wenn K{n} trifft zu", "only if Q{n} applies", n=n)
            return de_en("nur wenn K{n} trifft nicht zu", "only if Q{n} does not apply", n=n)
        text = self.cond_text(item, cond, lang, owner=owner)
        return de_en("nur {c}", "only {c}", c=text) if text else ""

    def probe_tree(self, item, lang=None, ui=None):
        """The concrete questions as they are shown: [{"id", "text", "subs": [same without "subs"]}] with "when"
        (the note "Wann stellen?" of a Nachfrage), "cond" (the stored condition), "cond_text" (it in words) and
        "cond_attr" (it for the interview view, interview.js). A concrete question has "n" (K1, K2, …) and "fit": it
        shows the switch "trifft zu" (`switched`). A Nachfrage belongs to the concrete question it follows; one first
        in the list stands as a question of its own."""
        config = item.config or {}
        subs, conds = set(config.get("probe_sub") or []), config.get("probe_if") or {}
        whens = item.text("probe_when", lang) or {}
        switched = self.switched(item)
        out = []
        for rid, text in self.rows(item, "probe", lang):
            cond = conds.get(rid)
            row = {"id": rid, "text": text, "when": (whens.get(rid) or "") if rid in subs else "",
                   "cond": cond, "cond_text": self.cond_text(item, cond, lang, ui), "cond_attr": self.cond_attr(item, cond),
                   "cond_label": "" if ui else self.cond_label(item, cond, lang)}
            if rid in subs and out:
                out[-1]["subs"].append(row)
            else:
                row["when"] = ""
                out.append({**row, "subs": [], "n": len(out) + 1})
        for row in out:
            row["fit"] = bool(row["subs"]) or row["id"] in switched
        return out

    def switched(self, item):
        """The row ids of the concrete questions of this Leitfrage that a condition names (E): of its own concrete
        questions or of its Unterfragen. With those that have Nachfragen they show "trifft zu" (B)."""
        from app import interview

        named = [c for c in ((item.config or {}).get("probe_if") or {}).values()]
        if not self.is_sub(item) and item.block is not None:
            named += [(sub.config or {}).get("case") for sub in interview.subs_of(item.block).get(item.id, [])]
        return {c["probe"] for c in named if isinstance(c, dict) and "probe" in c}

    def cond_attr(self, item, cond, owner=None):
        """A condition as the interview view reads it (data-iv-if): E with the element ("of") and the number ("n")
        of the concrete question it names, so the switch "<of>:<probe>" decides; None for none."""
        if not isinstance(cond, dict):
            return None
        if "probe" not in cond:
            return cond
        owner = owner or item
        n = next((n for rid, n, _ in self.numbered(owner) if rid == cond["probe"]), None)
        return {**cond, "of": owner.id, "n": n} if n else None

    def case_questions(self, items, lang=None):
        """[(question of the Kurzfragebogen, [(code, label)])] that the conditions of these Leitfragen and Unterfragen
        ask about (D), in the order of the Kurzfragebogen: the case features the interview view offers on their page."""
        wanted = set()
        for item in items:
            config = item.config or {}
            conds = list((config.get("probe_if") or {}).values()) + ([config.get("case")] if self.is_sub(item) else [])
            wanted |= {c.get("item") for c in conds if isinstance(c, dict) and "item" in c}
        if not wanted:
            return []
        return [(short, codes) for short, codes in short_choices(items[0].survey, lang) if short.id in wanted]

    def case_text(self, item, lang=None, ui=None):
        """The condition of an Unterfrage in words (D, or E on a concrete question of its Leitfrage), "" without one."""
        if not self.is_sub(item):
            return ""
        case = (item.config or {}).get("case")
        if isinstance(case, dict) and "probe" in case:
            lead = self.lead(item)
            return self.cond_text(item, case, lang, ui, owner=lead) if lead is not None else ""
        return self.cond_text(item, case, lang, ui)

    def case_label(self, item, lang=None):
        """The condition of an Unterfrage as the line over it in the interview view says it (cond_label)."""
        if not self.is_sub(item):
            return ""
        case = (item.config or {}).get("case")
        if isinstance(case, dict) and "probe" in case:
            lead = self.lead(item)
            return self.cond_label(item, case, lang, owner=lead) if lead is not None else ""
        return self.cond_label(item, case, lang)

    def case_attr(self, item):
        """The condition of an Unterfrage for the interview view (data-iv-if), None without one."""
        case = (item.config or {}).get("case") if self.is_sub(item) else None
        if isinstance(case, dict) and "probe" in case:
            lead = self.lead(item)
            return self.cond_attr(item, case, lead) if lead is not None else None
        return case or None

    def remap_config(self, config, twin):
        """A copy of the guide: conditions on the Kurzfragebogen name its copy; one whose question did not come
        along is dropped (the note "Wann stellen?" stays)."""
        config = dict(config)
        if "probe_if" in config:
            conds = {}
            for rid, cond in (config.get("probe_if") or {}).items():
                if isinstance(cond, dict) and "item" in cond:
                    new = twin(cond["item"])
                    if new is None:
                        continue
                    cond = {**cond, "item": new}
                conds[rid] = cond
            config["probe_if"] = conds
        case = config.get("case")
        if isinstance(case, dict) and "item" in case:  # E names a row of the Leitfrage, which comes along with its id
            new = twin(case.get("item"))
            if new is None:
                config.pop("case")
            else:
                config["case"] = {**case, "item": new}
        return config

    def check_warnings(self, item):
        out = [] if item.text("question").strip() else [de_en("Leitfrage ohne Erzählaufforderung", "guide question without a narrative prompt")]
        # E: a condition on a concrete question that is gone or no longer above (a warning, the guide is no logic, E9)
        order = [rid for rid, _, _ in self.numbered(item)]
        rows = [rid for rid, _ in self.rows(item, "probe")]
        for rid, cond in ((item.config or {}).get("probe_if") or {}).items():
            if isinstance(cond, dict) and "probe" in cond and rid in rows and (
                    cond["probe"] not in order or rows.index(cond["probe"]) >= rows.index(rid)):
                text = plain(dict(self.rows(item, "probe")).get(rid, ""))[:60]
                out.append(de_en("Konkrete Frage „{q}“: Die Frage ihrer Bedingung steht nicht mehr davor",
                                 "Specific question “{q}”: the question of its condition is no longer above it", q=text))
        case = (item.config or {}).get("case") if self.is_sub(item) else None
        if isinstance(case, dict) and "probe" in case and case["probe"] not in [r for r, _, _ in self.numbered(self.lead(item))]:
            out.append(de_en("Unterfrage: Die konkrete Frage ihrer Bedingung gibt es bei der Leitfrage nicht mehr",
                             "Sub-question: the specific question of its condition no longer exists in the guide question"))
        return out

    def questionnaire_lines(self, item, lang):
        out = []
        if self.is_sub(item):
            when = item.text("when", lang)
            out.append("*Unterfrage* zur Leitfrage darüber" + (f" ({plain(when)})" if when else ""))
        if self.case_text(item, lang, "de"):
            out.append(f"*Nur stellen,* {self.case_text(item, lang, 'de')}")
        for part, _, title in LISTS:
            rows = self.rows(item, part, lang)
            if part == "probe" and rows:
                out.append(f"*{title}:* " + "; ".join(_probe_line(r) + "".join("; " + _probe_line(s, True) for s in r["subs"])
                                                       for r in self.probe_tree(item, lang, "de")))
            elif rows:
                out.append(f"*{title}:* " + "; ".join(plain(text) for _, text in rows))
        hint = item.text("hint", lang)
        if hint:
            out.append(f"*Hinweis für Interviewende:* {plain(hint)}")
        if not self.is_sub(item):
            out.append(f"*Geplante Dauer:* ca. {self.minutes(item)} Min." + (" · Kernfrage" if (item.config or {}).get("core") else ""))
        return out

    def references(self, item):
        return [HELFFERICH]

    def source_groups(self, item):
        return [(de_en("Leitfadenschema mit vier Spalten", "Guide scheme with four columns"), [HELFFERICH]),
                (de_en("Kernfragen und Reihenfolge im Gespräch", "Core questions and order in the conversation"), [GLAESER_LAUDEL, HOPF])]
