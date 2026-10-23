"""AI review of the wording of a questionnaire: one question (button in the item editor) or the whole questionnaire
(button in the pre-launch check), by the text model, against the rules of questionnaire design.

What is checked: the texts participants read in the source language of the survey: question texts and
instructions, info texts, statements of a matrix, answer options, scale labels, the poles of a semantic
differential. Not sent: validated constructs while locked (their wording must stay as published), the consent
(legal text), page breaks, and the other languages (the translation page shows changed sources as new).

The answer is a list of suggestions per text part, each with a reason, plus general remarks (for the whole
questionnaire also on order, repetition and changes of scale direction). Nothing changes until the researcher
ticks suggestions and takes them over (editor.text_review_apply), in the draft only, as one step (undo)."""
import copy
import json

from app import ai

RULES = """Check the wording against the rules of questionnaire design (e.g. Porst; Dillman, Smyth & Christian;
Tourangeau, Rips & Rasinski), for the participants of a social science online survey:
- one question asks one thing (no double-barrelled questions: "useful and easy to use");
- neutral, not leading or loaded; no presupposition;
- no double negation; negations in statements only where the scale needs reversed items, and then clear;
- plain words instead of jargon and abbreviations, short sentences, concrete time frames instead of "regularly",
  "recently", "often";
- answer options are exhaustive and do not overlap; a "don't know" or "prefer not to say" where a person may
  really not know or not want to answer; scale labels fit the question and run in one direction;
- the form of address is consistent ({address});
- info texts and instructions are friendly, clear, complete (what comes next, how long) and not longer than needed;
- typos, grammar, punctuation.
Suggest a change only where it clearly improves the text; keep the meaning, the language ({language}) and the
form of address; keep placeholders in double braces ({{{{name}}}}) and the formatting marks (**bold**, *italic*)
exactly. Do not suggest a change that only swaps synonyms.
A text repeated in a loop comes with "as_read": the same text as participants read it in each pass, with the values
put in. Check every one of these versions (grammar, case, article, a value that does not fit the sentence); if one
reads wrongly, suggest a template text (with its placeholders) that reads correctly with all values, or say in a
remark that the values in the loop table need another form."""

ITEM_INSTRUCTIONS = """You review the wording of one element of an online questionnaire.
""" + RULES + """
Answer with ONE JSON object and nothing else:
{{"remarks": ["general remark", ...],
 "parts": [{{"id": "<part id>", "suggestion": "new text", "why": "short reason"}}, ...]}}
Remarks and reasons in German, suggestions in {language}."""

SURVEY_INSTRUCTIONS = """You review the wording of a whole online questionnaire, element by element, and as a whole.
""" + RULES + """
As a whole, also check: order effects (a question that frames the answers to later ones), repetitions, changes of
scale direction or of the number of points between similar questions, a missing introduction or closing text,
and whether the length fits the stated purpose.
Answer with ONE JSON object and nothing else:
{{"remarks": ["remark on the questionnaire as a whole", ...],
 "parts": [{{"item": "<variable name>", "id": "<part id>", "suggestion": "new text", "why": "short reason"}}, ...]}}
Remarks and reasons in German, suggestions in {language}."""

AS_READ_ROWS = 10  # passes of a loop the AI reads a text with (U45)
MAX_SURVEY_CHARS = 60000  # what one request may carry; a longer questionnaire is checked from the start


def _lang(survey):
    return survey.default_language


def reviewable(item):
    """Items whose wording may be checked: not the consent, no page break, no locked validated construct."""
    if item.type in ("consent", "page_break"):
        return False
    meta = (item.config or {}).get("construct")
    from app import constructs

    if meta and constructs.is_linked_key(meta.get("key")):  # a linked element: only while it is open (app/linked.py)
        return not constructs.locked(item)
    if meta and meta.get("key") in constructs.NOT_SHARED:
        # wording that may go only to the participants (APA PsycTests): never to an AI, unlocked or not (copyright
        # audit 2026-10-16, U4)
        return False
    return not (meta and not meta.get("unlocked"))


def parts(item, lang=None):
    """[(part id, role, text)] of the texts participants read, in the source language (or `lang`), in their order."""
    t = item.lang_texts(lang or _lang(item.survey))  # with the standard wordings, which are not stored (S9)
    c = item.config or {}
    out = []
    if (t.get("question") or "").strip():
        role = {"info": "Text"}.get(item.type) or ("Instruktion" if c.get("statements") or c.get("pairs") else "Fragetext")
        out.append(("question", role, t["question"]))
    for n, s in enumerate(c.get("statements") or [], start=1):
        text = (t.get("statements") or {}).get(s["id"], "")
        if text.strip():
            out.append((f"statements/{s['id']}", f"Aussage {n}", text))
    for n, o in enumerate(c.get("options") or [], start=1):
        text = (t.get("options") or {}).get(o["id"], "")
        if text.strip():
            out.append((f"options/{o['id']}", f"Antwortoption {n}", text))
    for n, p in enumerate(c.get("pairs") or [], start=1):
        pair = (t.get("pairs") or {}).get(p["id"], {})
        for side, name in (("left", "links"), ("right", "rechts")):
            if (pair.get(side) or "").strip():
                out.append((f"pairs/{p['id']}/{side}", f"Paar {n} {name}", pair[side]))
    labels = t.get("labels") or []
    for n, label in enumerate(labels, start=1):
        if c.get("labeling") == "endpoints" and 1 < n < len(labels):
            continue  # "nur Endpunkte verbalisiert": nobody reads the steps in between
        if (label or "").strip():
            out.append((f"labels/{n - 1}", f"Skalenstufe {n}", label))
    return out


# the role of a part (German, as the AI reads it) on the researchers' pages (the loop page, U45)
ROLE_WORDS = {"Text": "Text", "Instruktion": "Instruction", "Fragetext": "Question text", "Aussage": "Statement",
              "Antwortoption": "Answer option", "Paar": "Pair", "links": "left", "rechts": "right",
              "Skalenstufe": "Scale point"}


def role_label(role):
    """A part's role ("Aussage 2") in the language of the page."""
    from app.lang import english

    return " ".join(ROLE_WORDS.get(w, w) for w in role.split()) if english() else role


def _facts(item):
    """What the AI reads of one element. A text with a column of its loop also comes as the participants of each pass
    read it ("as_read"), so the review checks the filled-in versions, e.g. the case of a value in a sentence (UI
    audit 2026-10-12, U45)."""
    from app import loops

    out = []
    for pid, role, text in parts(item):
        part = {"id": pid, "role": role, "text": text}
        read = loops.read_with_rows(item.block, text, _lang(item.survey), limit=AS_READ_ROWS)
        if read:
            part["as_read"] = [t for _n, t in read]
        out.append(part)
    return {"variable": item.code, "type": item.kind.label, "required": item.answer_mode == "required", "parts": out}


def _fill(template, survey):
    from app.surveys import LANGUAGE_NAMES

    lang = _lang(survey)
    address = "informal: du" if lang == "de-du" else ("formal: Sie" if lang == "de" else "as in the texts")
    return template.format(language=LANGUAGE_NAMES.get(lang, lang), address=address)


def _suggestions(data, items):
    """The usable suggestions: a known part of a reviewable item, a text that differs from the current one."""
    out = []
    by_code = {i.code: i for i in items}
    for s in ai.as_list(data.get("parts")):
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            continue
        item = (by_code.get(s.get("item")) if isinstance(s.get("item"), str) else None) if len(items) > 1 else items[0]
        if item is None:
            continue
        known = {pid: (role, text) for pid, role, text in parts(item)}
        new = str(s.get("suggestion") or "").strip()
        if s.get("id") not in known or not new or new == known[s["id"]][1].strip():
            continue
        role, old = known[s["id"]]
        out.append({"item": item, "id": s["id"], "role": role, "old": old, "suggestion": new[:4000],
                    "why": str(s.get("why") or "")[:500]})
    return out


def review_item(item):
    """{"remarks": [...], "suggestions": [{"item", "id", "role", "old", "suggestion", "why"}]}"""
    data = ai.json_answer(ai.generate_text(_fill(ITEM_INSTRUCTIONS, item.survey),
                                    json.dumps(_facts(item), ensure_ascii=False, indent=1),
                                    purpose="text_review", item_type=item.type))
    return {"remarks": [str(r)[:500] for r in ai.as_list(data.get("remarks")) if str(r).strip()][:10],
            "suggestions": _suggestions(data, [item])}


def review_survey(survey):
    """The whole questionnaire in one request (blocks in flow order, reviewable items only)."""
    from app import flow

    by_id = {b.id: b for b in survey.blocks}
    blocks, items, size, cut = [], [], 0, False
    for bid in flow.block_order(flow.tree(survey)):
        block = by_id.get(bid)
        if block is None:
            continue
        rows = []
        for item in block.items:
            if not reviewable(item) or not parts(item):
                continue
            facts = _facts(item)
            size += len(json.dumps(facts, ensure_ascii=False))
            if size > MAX_SURVEY_CHARS:
                cut = True
                break
            rows.append(facts)
            items.append(item)
        if rows:
            entry = {"block": block.title(_lang(survey)) or "Block", "elements": rows}
            from app import loops

            read = loops.read_with_rows(block, entry["block"], _lang(survey), limit=AS_READ_ROWS) if block.show_title else []
            if read:  # the page title of a loop with its values (U45: "Bewertung: dem Fahrrad")
                entry["block_as_read"] = [t for _n, t in read]
            blocks.append(entry)
        if cut:
            break
    if not items:
        return {"remarks": [], "suggestions": [], "cut": False, "empty": True}
    facts = {"title": survey.title, "description": survey.description or "", "blocks": blocks}
    data = ai.json_answer(ai.generate_text(_fill(SURVEY_INSTRUCTIONS, survey), json.dumps(facts, ensure_ascii=False, indent=1),
                                    purpose="survey_review"))
    return {"remarks": [str(r)[:800] for r in ai.as_list(data.get("remarks")) if str(r).strip()][:15],
            "suggestions": _suggestions(data, items), "cut": cut, "empty": False}


def apply(item, changes):
    """Write {part id: new text} into the item's texts in the source language."""
    lang = _lang(item.survey)
    texts = copy.deepcopy(item.texts or {})
    t = texts.setdefault(lang, {})
    for pid, text in changes.items():
        text = text.strip()
        head, _, rest = pid.partition("/")
        if pid == "question":
            t["question"] = text
        elif head in ("statements", "options") and rest:
            t.setdefault(head, {})[rest] = text
        elif head == "pairs" and rest.count("/") == 1:
            p, side = rest.split("/")
            t.setdefault("pairs", {}).setdefault(p, {})[side] = text
        elif head == "labels" and rest.isdigit():
            labels = list(t.get("labels") or item.lang_texts(lang).get("labels") or [])
            if int(rest) < len(labels):
                labels[int(rest)] = text
                t["labels"] = labels
    item.texts = texts
