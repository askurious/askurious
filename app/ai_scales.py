"""AI suggestions for the questionnaire: construct ideas for the study and draft items for a
construct idea. Only on request (a button), never live.

Context sent to the text model: title, project, description, research question and a compact
outline of the questionnaire (blocks, loop variables, constructs used, other questions as code,
type and truncated text). No answers, no participant data.

Nothing here may look validated: no references, no author or scale names; drafted items are
marked ("ai" in the item config: idea, model, time) and carry the label "KI-generiert" in the
editor, the codebook and the pre-launch check."""
import re
from datetime import datetime, timezone

from flask import current_app

from app import ai
from app.items.base import plain
from app.lang import de_en

MIN_CONTEXT_WORDS = 20  # description + research question; below that, ask for more context first

IDEAS_INSTRUCTIONS = """You help social scientists plan questionnaires. From the study information and the current
questionnaire outline, suggest 3 to 5 psychological or social-science CONSTRUCTS that are relevant for the study and
NOT yet covered by the questionnaire. Write in {language}.
Rules: do not name published scales, instruments, authors, years or references; do not claim anything is validated;
use plain construct names (e.g. "Vertrauen in Technik", "Datenschutzbedenken"). Each "why" is one short sentence that
relates the construct to this study.
Answer ONLY with JSON: [{{"name": "...", "why": "..."}}, ...]"""

ITEMS_INSTRUCTIONS = """You help social scientists draft Likert items (agreement scale) for a construct idea.
Write {n} statements for the construct described by the researcher, fitting the study context. Languages: {languages}.
Rules: one idea per statement (no double-barrelled items), no double negation, no absolute words like "always"/"never",
short and concrete, first person where natural. {reverse}
Do not name published scales, authors or references, and do not reuse wording of well-known instruments verbatim.
{avoid}Answer ONLY with JSON: [{{"reverse": false, "text": {{{lang_keys}}}}}, ...]"""

LANGUAGE = {"de": "German (formal address: Sie, Ihr, Ihnen)", "de-du": "German (informal address: du, dich, dein, in lower case)",
            "en": "English", "es": "Spanish", "fr": "French", "it": "Italian", "pl": "Polish",
            "tr": "Turkish", "ru": "Russian", "zh-Hans": "Simplified Chinese", "nl": "Dutch",
            "pt": "European Portuguese", "ar": "Modern Standard Arabic (Western digits)"}


def context(survey, lang=None):
    """Plain-text study information and questionnaire outline for the prompts."""
    from app import constructs, loops

    lang = lang or survey.default_language
    lines = [f"Title: {survey.title}"]
    if survey.project:
        lines.append(f"Project: {survey.project}")
    if survey.description:
        lines.append(f"Description: {survey.description}")
    if getattr(survey, "research_question", ""):
        lines.append(f"Research question / hypotheses: {survey.research_question}")
    outline = []
    for block in survey.blocks:
        items = [i for i in block.items if i.type not in ("page_break", "consent")]
        if not items:
            continue
        head = f"Block {block.title(lang) or block.id}"
        if loops.active(block):
            cols = [c["name"] for c in loops.config(block)["columns"]]
            head += f" (repeated for each row of a table with variables: {', '.join(cols)})"
        outline.append(head)
        for item in items:
            info = constructs.info(item)
            if info:
                outline.append(f"  - construct already used: {info['label']}")
            elif (item.config or {}).get("ai"):
                outline.append(f"  - self-developed scale (AI draft): {item.config['ai'].get('idea', '')[:120]}")
            else:
                text = re.sub(r"\s+", " ", plain(item.text("question", lang)))[:120]
                outline.append(f"  - {item.kind.label} {item.code}: {text}")
    if outline:
        lines.append("Current questionnaire outline:")
        lines += outline
    return "\n".join(lines)


def enough_context(survey):
    words = len(((survey.description or "") + " " + (getattr(survey, "research_question", "") or "")).split())
    questions = sum(1 for b in survey.blocks for i in b.items if i.type not in ("page_break", "consent"))
    return words >= MIN_CONTEXT_WORDS or (words >= 8 and questions >= 3)


def construct_ideas(survey):
    """[{"name", "why"}] (3-5)."""
    lang = survey.default_language
    text = ai.generate_text(IDEAS_INSTRUCTIONS.format(language=LANGUAGE.get(lang, lang)), context(survey, lang),
                            purpose="construct_ideas")
    ideas = []
    for d in ai.json_list(text)[:5]:
        if isinstance(d, dict) and str(d.get("name", "")).strip():
            ideas.append({"name": str(d["name"]).strip()[:80], "why": str(d.get("why", "")).strip()[:240]})
    if not ideas:
        raise ai.AIError(de_en("Die KI hat keine Anregungen geliefert.", "The AI returned no suggestions."))
    return ideas


def draft_items(survey, idea, n=6, reverse=True, avoid=()):
    """[{"reverse": bool, "text": {lang: str}}] in all survey languages."""
    langs = list(survey.languages)
    instructions = ITEMS_INSTRUCTIONS.format(
        n=n, languages=", ".join(f"{LANGUAGE.get(l, l)} ({l})" for l in langs),
        reverse=(f"Exactly {n - n // 2} statements keyed in the construct's direction (\"reverse\": false) and exactly "
                 f"{n // 2} reverse-keyed statements, worded in the opposite direction (\"reverse\": true)."
                 if reverse else "All statements keyed in the construct's direction (\"reverse\": false)."),
        avoid=("Do not repeat or paraphrase these existing statements: " + " | ".join(avoid) + "\n") if avoid else "",
        lang_keys=", ".join(f'"{l}": "..."' for l in langs))
    prompt = context(survey) + f"\n\nConstruct idea (from the researcher): {idea}"
    items = []
    for d in ai.json_list(ai.generate_text(instructions, prompt, purpose="scale_items", item_type="likert")):
        if not isinstance(d, dict):
            continue
        texts = d.get("text") if isinstance(d.get("text"), dict) else {langs[0]: d.get("text")}
        texts = {l: str(texts.get(l) or "").strip()[:300] for l in langs}
        if texts.get(langs[0]):
            items.append({"reverse": bool(d.get("reverse")), "text": texts})
    if not items:
        raise ai.AIError(de_en("Die KI hat keine Items geliefert.", "The AI returned no items."))
    # exactly half reverse-keyed (odd n: one less), whatever the model delivered; alternate the two kinds
    want_rev = n // 2 if reverse else 0
    regular = [i for i in items if not i["reverse"]][:n - want_rev]
    reversed_ = [i for i in items if i["reverse"]][:want_rev]
    out = []
    for k in range(max(len(regular), len(reversed_))):
        out += regular[k:k + 1] + reversed_[k:k + 1]
    return out


# The question line in the languages written here; any other survey language stays empty, so the page "Übersetzung"
# and the start check show it as missing (an English line there would count as translated).
QUESTION = {"de": "Inwieweit stimmen Sie den folgenden Aussagen zu?",
            "de-du": "Inwieweit stimmst du den folgenden Aussagen zu?",
            "en": "To what extent do you agree with the following statements?"}


def likert_item(survey, idea, drafted, code):
    """A Likert matrix (not yet in a block) with the drafted statements (draft_items), marked "KI-generiert"."""
    from app import items
    from app.items.base import new_id
    from app.models import Item

    kind = items.get("likert")
    stmts, texts = [], {l: {**kind.default_texts(l), "statements": {}} for l in survey.languages}
    for d in drafted:
        sid = new_id()
        stmts.append({"id": sid, "code": "", "reverse": d["reverse"]})
        for l in survey.languages:
            texts[l]["statements"][sid] = d["text"].get(l, "")
    for l in survey.languages:
        texts[l]["question"] = QUESTION.get(l, "")
    return Item(type="likert", answer_mode="nudge", code=code, texts=texts,
                config={**kind.default_config(), "statements": stmts, "randomize": True, "label_display": "once",
                        "ai": stamp(idea)})


def stamp(idea):
    """The marker stored in the item config."""
    return {"idea": idea[:500], "model": current_app.config.get("OPENAI_TEXT_MODEL", ""),
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
