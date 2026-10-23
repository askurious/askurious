"""Rows of a question that only some people see: the statements of a Likert matrix, the pairs of a semantic
differential.

A row of the config can carry a condition ("when": {"item", "op", "value"}, as a condition of the flow,
flow.holds): the row is shown to the people for whom it holds. It is set like the conditions of the flow, with
the form of app/conditions.py: an earlier question is picked and its answers are ticked, nobody types a code.
Offered are the questions on earlier pages and the URL parameters (offered).

Not shown is not the same as not answered. Which rows a person did not see is written into the answer when the
page is sent (HIDDEN: the ids of these rows), not worked out later: a condition changed afterwards would change
old data. The export writes NOT_SHOWN (-77) into the columns of these rows, declared as a missing value (SPSS,
jamovi, codebook) and left out of every statistic; a row that was shown and left out stays empty. Required
checks, nudges and the share of questions left out count the rows shown. A question none of whose rows is shown
is left out of its page (ItemType.applies).
"""
from app.lang import de_en

NOT_SHOWN = -77
HIDDEN = "_hidden"

# Named where a scale mean is taken over the statements a person saw (results page). Read in full text.
MAZZA = ("Mazza, G. L., Enders, C. K., & Ruehlman, L. S. (2015). Addressing item-level missing data: A comparison of "
         "proration and full information maximum likelihood estimation. Multivariate Behavioral Research, 50(5), "
         "504–519. https://doi.org/10.1080/00273171.2015.1068157")


def r_when(value):
    """Rule for a survey file: a condition as the editor stores it."""
    from app import flow
    from app.items.base import Unfit

    if not isinstance(value, dict) or set(value) - {"item", "op", "value"}:
        raise Unfit
    ref, op, text = value.get("item"), value.get("op"), value.get("value", "")
    if isinstance(ref, bool) or not isinstance(ref, (int, str)) or len(str(ref)) > 60:
        raise Unfit
    if op not in flow.OPS or not isinstance(text, str) or len(text) > 200:
        raise Unfit
    return {"item": ref, "op": op, "value": text}


r_when.optional = True  # a row may leave it out (see items.base.r_dict)


def partial(row):
    """True if only some people see this row: it has a condition, or it is an option carried from an earlier
    question and shown to those who chose it (or did not; app/carry.py)."""
    return bool(row.get("when")) or (row.get("src") or {}).get("which") in ("chosen", "unchosen")


def conditional(rows):
    return any(partial(r) for r in rows)


def offered(item, lang=None):
    """What a row of `item` can depend on, as conditions.describe: the questions on earlier pages (outside a
    loop) and the URL parameters."""
    from app import conditions, flow, piping

    survey = item.block.survey
    pos = piping._positions(survey)
    here = pos.get(item.id)
    out = {}
    for ref, info in conditions.describe(survey, lang, everything=True).items():
        if ref.startswith("p:"):
            out[ref] = info
            continue
        source = int(ref) if ref.isdigit() else (flow.source_id(ref) if ref.startswith(flow.STATE_REF) else None)
        if source is not None and here is not None and source in pos and pos[source] < here:
            out[ref] = info
    return out


def hidden(rows, answers, survey):
    """The ids of the rows this person does not see, given the answers so far (item id -> answer, URL parameters
    as "p:name"); nothing without answers to look at (the editor's views show every row). survey: the survey of
    the question, so that a condition reads a Rechenwert of this survey only (security audit 2026-10-06, H5)."""
    if answers is None:
        return set()
    from app import carry, flow

    return {r["id"] for r in rows if (r.get("when") and not flow.holds(r["when"], answers, survey))
            or (r.get("src") and carry.hidden(r, answers))}


def hidden_in(answer):
    """The ids of the rows that were not shown when this answer was given."""
    found = answer.get(HIDDEN) if isinstance(answer, dict) else None
    return set(found) if isinstance(found, list) else set()


def from_form(item, rows, form, before, lang=None):
    """Sets the conditions of `rows` from the editor form (fields "when_<row id>_item" and so on, as
    conditions.from_form reads them). A row whose fields the form does not have keeps its condition (`before`:
    {row id: row as stored})."""
    from app import conditions

    described = None
    for row in rows:
        prefix = f"when_{row['id']}"
        if f"{prefix}_item" not in form:
            if (before.get(row["id"]) or {}).get("when"):
                row["when"] = before[row["id"]]["when"]
            continue
        if described is None:
            described = offered(item, lang)
        cond = conditions.from_form(form, prefix, described)
        if cond:
            row["when"] = cond
        else:
            row.pop("when", None)


def view(item, row, described):
    """The condition of a row as the form shows it (conditions.view), None without one."""
    from app import conditions

    return conditions.view(row["when"], described) if row.get("when") else None


def text(item, row, lang=None, ui=None):
    """The condition in words ("geraete: Smartphone"), "" without one. lang: the survey language of the answers;
    ui: the words around them in this language ("de"/"en"; None: German), e.g. the reader's of a codebook."""
    from app import conditions

    return conditions.text(item.block.survey, row["when"], lang, ui=ui) if row.get("when") else ""


def complete(cond):
    return cond.get("op") in ("answered", "empty") or str(cond.get("value", "")).strip() != ""


# the English of what the callers name a row (texts in the language of the page, UI/UX audit 2026-10-09, X85)
WHAT_EN = {"Aussage": "statement", "Zeile": "row", "Gegensatzpaar": "pair"}


def problems(item, rows, name, what):
    """What blocks the start: a condition on a question that is not (or no longer) before this one, or one
    without an answer ticked. name(index, row): how the row is called; what: "Aussage", "Gegensatzpaar"."""
    if not any(r.get("when") for r in rows):
        return []
    allowed = offered(item)
    out = []
    for i, row in enumerate(rows):
        cond = row.get("when")
        if not cond:
            continue
        if str(cond.get("item")) not in allowed:
            out.append(de_en("Bedingung der {what} „{name}“: Die Frage steht nicht auf einer früheren Seite oder es gibt "
                             "sie nicht mehr", "Condition of the {what_en} “{name}”: the question is not on an earlier "
                             "page or no longer exists", what=what, what_en=WHAT_EN.get(what, what), name=name(i, row)))
        elif not complete(cond):
            out.append(de_en("Bedingung der {what} „{name}“ ist unvollständig: bitte eine Antwort ankreuzen oder einen "
                             "Wert angeben", "Condition of the {what_en} “{name}” is incomplete: please tick an answer "
                             "or give a value", what=what, what_en=WHAT_EN.get(what, what), name=name(i, row)))
    return out


def remap(rows, twin):
    """The rows for a copy of the questionnaire: conditions name the questions of the copy; a condition on a
    question that did not come along is dropped (the row is then shown to everyone)."""
    from app import flow

    out = []
    for row in rows:
        cond = row.get("when")
        if not cond:
            out.append(row)
            continue
        ref = cond.get("item")
        if isinstance(ref, str) and ref.startswith("p:"):
            out.append(row)
            continue
        source = flow.source_id(ref) if not (isinstance(ref, str) and ref.isdigit()) else int(ref)
        new = twin(source) if source is not None else None
        if new is None:
            out.append({k: v for k, v in row.items() if k != "when"})
        else:
            out.append({**row, "when": {**cond, "item": new if isinstance(ref, int) or str(ref).isdigit() else f"{flow.STATE_REF}{new}"}})
    return out


def page_data(item, lang=None):
    """What the editor's script needs to build the fields of a condition (conditions.page_data)."""
    from app import conditions

    return conditions.page_data(offered(item, lang))


def editor_view(item, labelled, lang=None):
    """What the editor's part "nur manchen zeigen" shows (items/_row_conditions.html). labelled: [(row, label,
    column)] of the rows that can carry a condition."""
    from app import conditions

    described = offered(item, lang)
    out = []
    for row, label, code in labelled:
        v = view(item, row, described)
        out.append({"id": row["id"], "label": label, "code": code, "view": v, "info": described.get(v["ref"]) if v else None,
                    "text": text(item, row, lang)})
    return {"rows": out, "described": described, "data": conditions.page_data(described), "modes": conditions.MODES,
            "any": sum(1 for r in out if r["view"]),
            # for "the same question for all": the questions with answers to tick
            "multi": [(ref, d["label"]) for ref, d in described.items() if d["kind"] == "choice"]}


ROW_KEYS = {"likert": "statements", "semantic": "pairs", "side_by_side": "rows"}


def rows_of(item):
    return (item.config or {}).get(ROW_KEYS.get(item.type, ""), []) or []


def check(survey):
    """[(severity, message, item id, block id)] for the pre-launch check: what a researcher should know about rows
    that only some people see (what blocks the start is in the validate() of the types, see problems)."""
    from app import constructs
    from app.conditions import number_problems

    out = []
    items = {i.id: i for b in survey.blocks for i in b.items}
    for block in survey.blocks:
        for item in block.items:
            found = rows_of(item)
            if not conditional(found):
                continue
            for r in found:
                cond = r.get("when") or {}
                source = items.get(cond.get("item")) if isinstance(cond.get("item"), int) else None
                for problem in number_problems(cond, source):
                    out.append(("warning", f"{item.code}, Bedingung „{text(item, r)}“: {problem}.", item.id, block.id))
            if constructs.toolbox_key(item) and any(r.get("when") for r in found):
                out.append(("warning", f"{item.code}: Aussagen werden nicht allen gezeigt; die Skala ist so nicht validiert, "
                            "Vergleiche mit Normwerten sind nicht möglich.", item.id, block.id))
            if item.kind.level(item) != "metric":
                continue
            groups = {}
            for r in found:
                if "check" not in r and not r.get("src"):  # carried statements are no scale
                    groups.setdefault(r.get("subscale") or "", []).append(r)
            for name, group in groups.items():
                if len(group) >= 2 and sum(1 for r in group if not r.get("when")) < 2:
                    scale = f"Subskala „{name}“" if name else "Skala"
                    out.append(("warning", f"{item.code}: {scale} hat weniger als zwei Aussagen, die alle sehen. Cronbachs α gibt es "
                                "nur über die Personen, die alle Aussagen gesehen haben; der Skalenmittelwert geht je Person "
                                "über die gezeigten Aussagen (nur bei mindestens zwei gezeigten).", item.id, block.id))
    return out
