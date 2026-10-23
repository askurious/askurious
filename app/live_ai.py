"""AI assistant of a live vote: the presenter describes the event and what they want to learn; the text model
suggests five feedback questions in the types a live vote offers (app/live/ TYPES). Nothing is created until the
presenter ticks suggestions (and may edit them) and takes them over, as one step (undo), at the end of a block."""

from app import ai
from app.lang import current, de_en

COUNT = 5
MAX_DESCRIPTION = 3000
MAX_OPTIONS = 8

INSTRUCTIONS = """You help a presenter (lecturer, workshop host, speaker) with a live vote during their event: the
audience answers on their phones, the results appear on the screen as bars or a word cloud.
From the presenter's description of the event and of what they want to learn, write exactly {count} short feedback
questions that fit a live vote, in {language} ({address}). Mix the types where it helps:
- "single_choice": one answer from 2 to 6 short options;
- "multiple_choice": several answers from 3 to 8 short options;
- "rating": a scale; "style" is "smileys" (5 points, the default) or "stars" (5 points, only for a grade or a liking);
- "text_input": one word or a few words, shown as a word cloud.
Rules: one question asks one thing; neutral, not leading; short enough to read on a phone and on a screen; options
do not overlap and are exhaustive where needed; no personal data.
Answer with ONE JSON object and nothing else:
{{"questions": [{{"type": "single_choice", "question": "...", "options": ["...", "..."], "why": "short reason"}},
               {{"type": "rating", "question": "...", "style": "smileys", "why": "..."}},
               {{"type": "text_input", "question": "...", "why": "..."}}]}}
The reasons in {reasons}.
If the live vote already has questions (listed in the input), do not repeat them or ask the same thing in other
words: suggest questions that add to them."""


def _prompt_texts(survey):
    from app.surveys import LANGUAGE_NAMES

    lang = survey.default_language
    address = "informal: du" if lang == "de-du" else ("formal: Sie" if lang == "de" else "as usual in the language")
    return LANGUAGE_NAMES.get(lang, lang), address


def _clean(raw):
    """A suggestion as the page and apply() use it, or None if it does not fit a live vote."""
    if not isinstance(raw, dict):
        return None
    kind, question = raw.get("type"), " ".join(str(raw.get("question") or "").split())[:500]
    if kind not in ("single_choice", "multiple_choice", "rating", "text_input") or not question:
        return None
    out = {"type": kind, "question": question, "why": " ".join(str(raw.get("why") or "").split())[:300]}
    if kind in ("single_choice", "multiple_choice"):
        options = [" ".join(str(o).split())[:200] for o in ai.as_list(raw.get("options")) if str(o).strip()][:MAX_OPTIONS]
        if len(options) < 2:
            return None
        out["options"] = options
    if kind == "rating":
        out["style"] = raw.get("style") if raw.get("style") in ("stars", "smileys") else "smileys"
    return out


MAX_EXISTING = 60  # questions of the live vote sent along, so that the suggestions do not repeat them


def existing(survey):
    """The questions the live vote already has, as lines "- <question> (<kind>)", for the prompt."""
    from app import live
    from app.items.base import plain

    lines = []
    for item in live.questions(survey)[:MAX_EXISTING]:
        text = " ".join(plain(item.text("question") or "").split())[:300]
        if text:
            lines.append(f"- {text} ({live.TYPES.get(item.type, item.type)})")
    return "\n".join(lines)


def suggest(survey, description):
    """Five suggestions [{type, question, options?, style?, why}] for the description, adding to the questions the
    live vote already has (raises ai.AIError)."""
    language, address = _prompt_texts(survey)
    have = existing(survey)
    # the questions in the vote's language (survey content); the reasons, read only by the presenter, in the language
    # of the presenter's page (E12)
    reasons = "English" if current() == "en" else "German"
    text = ai.generate_text(INSTRUCTIONS.format(count=COUNT, language=language, address=address, reasons=reasons),
                            "Title of the live vote: " + survey.title + "\n\nDescription by the presenter:\n" +
                            description.strip()[:MAX_DESCRIPTION] +
                            ("\n\nQuestions the live vote already has (do not repeat them):\n" + have if have else ""),
                            purpose="live_questions")
    data = ai.json_answer(text)  # lenient like every AI answer: raw line breaks inside a text do not discard it
    found = [q for q in (_clean(r) for r in ai.as_list(data.get("questions"))) if q]
    if not found:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.",
                               "The AI's answer could not be read. Please try again."))
    return found[:COUNT]


def from_form(form, ticked=True):
    """The ticked suggestions as edited on the page (fields q<n>_type, q<n>_question, q<n>_options, q<n>_style)."""
    out = []
    for n in (form.getlist("take") if ticked else [str(n) for n in range(COUNT)]):
        raw = {"type": form.get(f"q{n}_type"), "question": form.get(f"q{n}_question", ""),
               "options": form.get(f"q{n}_options", "").splitlines(), "style": form.get(f"q{n}_style")}
        q = _clean(raw)
        if q:
            out.append(q)
    return out


def from_form_all(form):
    """All suggestions still on the page (when nothing was ticked: shown again)."""
    return from_form(form, ticked=False)


def apply(survey, block, suggestions):
    """Add the suggestions at the end of the block, as items of their types; returns the new items."""
    from app import items
    from app.editor import _renumber, _unique_code
    from app.items.choice import _new_id
    from app.models import Item

    lang = survey.default_language
    rows, new = list(block.items), []
    for q in suggestions:
        kind = items.get(q["type"])
        config, texts = kind.default_config(), {**kind.default_texts(lang), "question": q["question"]}
        if "options" in q:
            config["options"] = [{"id": _new_id(), "code": str(n)} for n in range(1, len(q["options"]) + 1)]
            texts["options"] = {o["id"]: label for o, label in zip(config["options"], q["options"])}
        if q["type"] == "rating":
            config.update(style=q["style"], points=5)
        item = Item(type=q["type"], answer_mode="optional", code=_unique_code(survey, "Q"), config=config,
                    texts={lang: texts})
        rows.append(item)
        new.append(item)
        block.items = rows  # each code is unique against the ones added before
    _renumber(rows)
    return new
