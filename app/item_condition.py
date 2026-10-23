"""A condition of a question of its own: "Nur zeigen, wenn …".

A filter question ("Falls ja: Wie oft …?") needed a block of its own and a branch in the flow. Now the question
carries the condition itself, in its config as "when": {"item", "op", "value"}, stored and evaluated exactly as the
condition of a statement (app/rows.py, flow.holds) and set with the same form (app/conditions.py): an earlier
question is picked and its answers are ticked, nobody types a code. Offered are the questions on earlier pages
(outside a loop), the Rechenwerte before the question and the URL parameters (rows.offered): the answers of the
same page are not known yet when the page is drawn. Inside a loop the condition reads the answers of the current
pass, as the condition of a statement does (runtime.row_view).

A question whose condition does not hold is left out of its page: runtime.applies combines it with the type's own
ItemType.applies, wherever the runtime, the quality indicators and the dropping of answers ask. A page on which
nothing is left is skipped like one behind a condition of the flow, and an answer given before the filter
question was changed is dropped (runtime.drop_skipped_answers). The export columns of a question not shown stay
empty, as those of a block behind a condition of the flow; −77 stays for statements, pairs and carried options
(display-logic concept, E2), so the exports of existing surveys do not change.

The condition counts as logic: it is set only in a draft and in the source language (editor.edit_item), like
every other change to what people are asked.
"""
from app.lang import de_en

KEY = "when"  # in Item.config
FIELD = "item_when"  # the fields of the form: item_when_item, item_when_mode, ... (conditions.from_form)


def supported(item):
    """Can this element carry a condition? Every element of a page but the consent and page breaks; a Rechenwert
    is never shown, a feedback element (results so far) shows what it has to the people who get there, and live
    polls and interview guides have no pages of this kind."""
    if item.type in ("consent", "page_break") or item.kind.computed or item.kind.category == "feedback":
        return False
    survey = item.block.survey if item.block is not None else None
    return survey is None or (survey.live is None and survey.interview is None)


def of(item):
    """The stored condition of the question, or None."""
    cond = (item.config or {}).get(KEY)
    if not isinstance(cond, dict) or cond.get("item") is None or item.type in ("consent", "page_break") \
            or item.kind.computed or item.kind.category == "feedback":  # as supported(), without the survey
        return None
    return cond


def holds(item, answers):
    """Is the question shown, by its own condition, to a person with these answers so far (item id -> answer, URL
    parameters as "p:name")? True without a condition."""
    cond = of(item)
    if cond is None or answers is None:
        return True
    from app import flow

    return flow.holds(cond, answers, item.survey)


def text(item, lang=None, ui=None, shown=False):
    """The condition in words ("geraete: Smartphone"), "" without one. lang: the survey language of the answers;
    ui: the words around them for the reader of a file ("de"/"en"); shown: in the language of the page."""
    from app import conditions

    cond = of(item)
    return conditions.text(item.survey, cond, lang, ui=ui, shown=shown) if cond else ""


def editor_view(item, lang=None):
    """What the editor's fold "Nur zeigen, wenn …" shows (items/_item_condition.html)."""
    from app import conditions, rows

    described = rows.offered(item, lang)
    cond = of(item)
    v = conditions.view(cond, described) if cond else None
    return {"view": v, "described": described, "data": conditions.page_data(described), "modes": conditions.MODES,
            "text": text(item, lang, shown=True)}


def from_form(item, form, before, editable, lang=None):
    """Sets the condition from the editor form (fields "item_when_item" and so on). before: the condition as
    stored before the form was read (an element's update_from_form may build its config anew). Not editable (a
    running survey, another language) or a form without the fields: the condition stays as it was."""
    config = {k: v for k, v in (item.config or {}).items() if k != KEY}
    cond = before
    if editable and supported(item) and f"{FIELD}_item" in form:
        from app import conditions, rows

        cond = conditions.from_form(form, FIELD, rows.offered(item, lang))
    if cond:
        config[KEY] = cond
    if config != (item.config or {}):
        item.config = config


def problems(item):
    """What blocks the start (pre-launch check, the editor's "Noch offen"): a condition on a question that is not on an
    earlier page or no longer exists, or one without an answer ticked."""
    cond = of(item)
    if cond is None:
        return []
    from app import flow, rows

    if str(cond.get("item")) in rows.offered(item):
        if rows.complete(cond):
            return []
        return [de_en("„Nur zeigen, wenn …“ ist unvollständig: bitte eine Antwort ankreuzen oder einen Wert angeben",
                      "“Show only if …” is incomplete: please tick an answer or give a value")]
    ref = cond.get("item")
    if isinstance(ref, str) and ref.startswith("p:"):
        return [de_en("„Nur zeigen, wenn …“ bezieht sich auf den URL-Parameter „{name}“, der nicht deklariert ist",
                      "“Show only if …” refers to the URL parameter “{name}”, which is not declared", name=ref[2:])]
    source = int(ref) if isinstance(ref, str) and ref.isdigit() else flow.source_id(ref)
    others = {i.id: i for b in item.survey.blocks for i in b.items}
    if source is None or source not in others:
        return [de_en("„Nur zeigen, wenn …“ bezieht sich auf eine Frage, die es nicht mehr gibt: bitte neu wählen",
                      "“Show only if …” refers to a question that no longer exists: please choose again")]
    if source == item.id:
        return [de_en("„Nur zeigen, wenn …“ bezieht sich auf die Frage selbst", "“Show only if …” refers to the question itself")]
    if source in {i.id for b in item.survey.blocks if b.id in flow.loop_blocks(item.survey) for i in b.items}:
        return [de_en("„Nur zeigen, wenn …“ bezieht sich auf {code} in einer Schleife: Ihre Antworten gibt es je "
                      "Durchgang, die Bedingung träfe nie zu",
                      "“Show only if …” refers to {code} in a loop: its answers exist per pass, so the condition would "
                      "never hold", code=others[source].code)]
    return [de_en("„Nur zeigen, wenn …“ bezieht sich auf {code}, die nicht auf einer früheren Seite steht: Antworten "
                  "derselben oder einer späteren Seite sind beim Zeigen der Seite noch nicht bekannt. Bitte einen "
                  "Seitenumbruch davor setzen oder eine frühere Frage wählen",
                  "“Show only if …” refers to {code}, which is not on an earlier page: answers on the same or a later "
                  "page are not known yet when the page is shown. Please put a page break in between or choose an "
                  "earlier question", code=others[source].code)]


def check(survey):
    """[(severity, message, item id, block id)] for the pre-launch check: values of a condition on a number question
    that cannot be read for sure (what blocks the start is in problems)."""
    from app.conditions import number_problems

    items = {i.id: i for b in survey.blocks for i in b.items}
    out = []
    for block in survey.blocks:
        for item in block.items:
            cond = of(item)
            if cond is None:
                continue
            source = items.get(cond.get("item")) if isinstance(cond.get("item"), int) else None
            for problem in number_problems(cond, source):
                out.append(("warning", de_en("{code}, „Nur zeigen, wenn {cond}“: {problem}.",
                                             "{code}, “Show only if {cond}”: {problem}.", code=item.code,
                                             cond=text(item, shown=True), problem=problem), item.id, block.id))
    return out


def referring(survey, item_id):
    """The questions of `survey` whose condition reads the question `item_id` (deleting it leaves them without)."""
    from app import flow

    out = []
    for block in survey.blocks:
        for item in block.items:
            cond = of(item)
            if cond is None or item.id == item_id:
                continue
            ref = cond.get("item")
            source = int(ref) if isinstance(ref, str) and ref.isdigit() else flow.source_id(ref)
            if source == item_id:
                out.append(item)
    return out


def remap(config, twin):
    """(config, dropped) for a copy of the question: the condition names the question of the copy (twin(old id) ->
    new id or None); a condition on a question that did not come along is dropped, dropped is then True."""
    if not isinstance((config or {}).get(KEY), dict):
        return config, False
    from app import rows

    new = rows.remap([config], twin)[0]
    return new, KEY not in new


def any_in(survey):
    """True if a question of the survey is shown only to some (paths_differ, the duration estimate)."""
    return any(of(i) for b in survey.blocks for i in b.items)
