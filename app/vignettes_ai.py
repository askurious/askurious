"""AI suggestions for vignettes (app/vignettes.py): factors with levels, more levels for one factor,
implausible level pairs, and a vignette text with the placeholders. Only on request (a button), never live;
nothing changes until the researcher ticks suggestions and takes them over, as one step (undo). Factors and levels
taken over carry "ai" (shown as „KI-generiert“ in the editor): the choice of factors needs a theoretical reason.

Context sent to the text model: title, project, description, research question and the questionnaire outline
(ai_scales.context), plus the factors and levels typed so far. No answers, no participant data."""
import copy
import re

from app import ai, ai_scales
from app.items.base import plain
from app.lang import de_en

MAX_FACTORS = 6
MAX_LEVELS = 6
MAX_PAIRS = 15
MAX_LABEL = 120

RULES = """Rules for factors and levels of a factorial survey (vignette experiment):
- factors describe the hypothetical situation, person or object in the vignette, never characteristics of the
  respondents (those are asked as normal questions);
- the levels of one factor exclude each other, are on the same level of abstraction and differ in one respect only;
- levels are short (at most six words) and fit into a sentence of the vignette text;
- do not name published studies, scales, authors or references."""

FACTORS = """You help social scientists design a factorial survey (vignette experiment). From the study information,
suggest 3 to {n} FACTORS that should vary systematically in the vignettes because they matter for the research
question, each with 2 to 4 levels. Write the names and levels in {language}.
{rules}
Do not repeat factors the vignettes already have (listed in the input).
Answer with ONE JSON object and nothing else:
{{"factors": [{{"name": "...", "levels": ["...", "..."], "why": "short reason in German"}}]}}"""

LEVELS = """You help social scientists design a factorial survey (vignette experiment). For the factor named in the
input, suggest 2 to 4 FURTHER levels that are not yet among its levels. Write in {language}.
{rules}
Answer with ONE JSON object and nothing else:
{{"levels": ["...", "..."], "why": "short reason in German"}}"""

PAIRS = """You help social scientists design a factorial survey (vignette experiment). Combinations of levels of
different factors that are logically impossible or very implausible should be excluded from the vignettes (for
example a medical doctor without a university degree). From the factors and levels in the input, list such PAIRS of
two levels of two different factors. Only clear cases; at most {n}; an empty list if there are none. Copy the
factor names and levels exactly as given.
Answer with ONE JSON object and nothing else:
{{"pairs": [{{"factor_a": "...", "level_a": "...", "factor_b": "...", "level_b": "...", "why": "short reason in German"}}]}}"""

TEXT = """You help social scientists write the text of a vignette for a factorial survey. Write one short vignette
(2 to 4 sentences) in {language} ({address}) that describes the situation and contains EVERY placeholder of the input
exactly once, written as {{{{name}}}}. The text must read correctly for every level of every factor (all levels are
listed). Neutral wording, no question, no evaluation.
Answer with ONE JSON object and nothing else:
{{"text": "...", "why": "short note in German, e.g. which level needs care"}}"""


def ai_scales_language(survey):
    from app.surveys import LANGUAGE_NAMES

    lang = survey.default_language
    address = "informal: du" if lang == "de-du" else ("formal: Sie" if lang == "de" else "as usual in the language")
    return LANGUAGE_NAMES.get(lang, lang), address


def _short(text, n=MAX_LABEL):
    return " ".join(plain(str(text or "")).split())[:n]


def _factors_text(survey, node):
    from app import vignettes

    f = vignettes.factors(survey, node)
    lines = []
    for a in f.attributes(f):
        lines.append(f"- {a['label']} (placeholder {{{{{a['code']}}}}}): " + "; ".join(l for l in a["level_labels"] if l))
    return "\n".join(lines)


def _ask(instructions, prompt, purpose):
    return ai.json_answer(ai.generate_text(instructions, prompt, purpose=purpose))


def suggest_factors(survey, node):
    language, _ = ai_scales_language(survey)
    have = _factors_text(survey, node)
    data = _ask(FACTORS.format(n=MAX_FACTORS, language=language, rules=RULES),
                ai_scales.context(survey) + ("\n\nFactors the vignettes already have:\n" + have if have else ""),
                "vignette_factors")
    out = []
    for raw in ai.as_list(data.get("factors")):
        if not isinstance(raw, dict):
            continue
        name = _short(raw.get("name"))
        levels = [_short(l) for l in ai.as_list(raw.get("levels")) if _short(l)][:MAX_LEVELS]
        if name and len(levels) >= 2:
            out.append({"name": name, "levels": levels, "why": _short(raw.get("why"), 300)})
    if not out:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.",
                               "The AI answer could not be read. Please try again."))
    return out[:MAX_FACTORS]


def suggest_levels(survey, node, aid):
    from app import vignettes

    f = vignettes.factors(survey, node)
    attr = next((a for a in f.attributes(f) if a["id"] == aid), None)
    if attr is None:
        raise ai.AIError(de_en("Diesen Faktor gibt es nicht mehr.", "This factor no longer exists."))
    language, _ = ai_scales_language(survey)
    data = _ask(LEVELS.format(language=language, rules=RULES),
                ai_scales.context(survey) + f"\n\nAll factors:\n{_factors_text(survey, node)}\n\nFactor to extend: "
                f"{attr['label']} with the levels: " + "; ".join(attr["level_labels"]), "vignette_levels")
    have = {l.casefold() for l in attr["level_labels"]}
    levels = [_short(l) for l in ai.as_list(data.get("levels")) if _short(l) and _short(l).casefold() not in have]
    if not levels:
        raise ai.AIError(de_en("Die KI hat keine neuen Stufen vorgeschlagen. Bitte erneut versuchen.",
                               "The AI suggested no new levels. Please try again."))
    return {"aid": aid, "name": attr["label"], "levels": levels[:MAX_LEVELS], "why": _short(data.get("why"), 300)}


def suggest_pairs(survey, node):
    from app import vignettes

    f = vignettes.factors(survey, node)
    attrs = f.attributes(f)
    data = _ask(PAIRS.format(n=MAX_PAIRS), ai_scales.context(survey) + "\n\nFactors and levels:\n" +
                _factors_text(survey, node), "vignette_prohibitions")

    def find(factor, level):
        for a in attrs:
            if a["label"].casefold() == _short(factor).casefold() or a["code"].casefold() == _short(factor).casefold():
                for lv, label in zip(a["levels"], a["level_labels"]):
                    if label.casefold() == _short(level).casefold():
                        return a, lv, label
        return None

    out, seen = [], {(p["a"], p["la"], p["b"], p["lb"]) for p in node.get("prohibitions") or []}
    for raw in ai.as_list(data.get("pairs")):
        if not isinstance(raw, dict):
            continue
        left, right = find(raw.get("factor_a"), raw.get("level_a")), find(raw.get("factor_b"), raw.get("level_b"))
        if not left or not right or left[0]["id"] == right[0]["id"]:
            continue
        key = (left[0]["id"], left[1]["id"], right[0]["id"], right[1]["id"])
        if key in seen or (key[2], key[3], key[0], key[1]) in seen:
            continue
        seen.add(key)
        out.append({"a": key[0], "la": key[1], "b": key[2], "lb": key[3], "label": f"{left[2]} und {right[2]}",
                    "why": _short(raw.get("why"), 300)})
    return out[:MAX_PAIRS]


def suggest_text(survey, node):
    codes = [a["code"] for a in node.get("attributes") or []]
    if not codes:
        raise ai.AIError(de_en("Erst Faktoren anlegen.", "Add factors first."))
    language, address = ai_scales_language(survey)
    data = _ask(TEXT.format(language=language, address=address),
                ai_scales.context(survey) + "\n\nPlaceholders and the levels they stand for:\n" + _factors_text(survey, node),
                "vignette_text")
    text = " ".join(str(data.get("text") or "").split())[:2000]
    missing = [c for c in codes if "{{" + c + "}}" not in text]
    if not text:
        raise ai.AIError(de_en("Die KI-Antwort war nicht lesbar. Bitte erneut versuchen.",
                               "The AI answer could not be read. Please try again."))
    return {"text": text, "why": _short(data.get("why"), 300), "missing": missing}


# --- taking suggestions over -------------------------------------------------------------------


def take_factors(survey, node, chosen):
    """Append the chosen factors [{name, levels}] (marked "ai")."""
    from app import vignettes
    from app.items.base import new_id

    lang = survey.default_language
    texts = copy.deepcopy(node.get("texts") or {})
    t = texts.setdefault(lang, {})
    attrs = copy.deepcopy(node.get("attributes") or [])
    for f in chosen:
        aid = new_id()
        levels = [{"id": new_id(), "hint": ""} for _ in f["levels"]]
        attrs.append({"id": aid, "code": "", "levels": levels, "ai": True})
        t.setdefault("attributes", {})[aid] = f["name"]
        for lv, label in zip(levels, f["levels"]):
            t.setdefault("levels", {})[lv["id"]] = label
    vignettes._codes(attrs, t.get("attributes", {}))
    node["attributes"], node["texts"] = attrs, texts
    vignettes.materialize(survey, node)


def take_levels(survey, node, aid, labels):
    from app import vignettes
    from app.items.base import new_id

    lang = survey.default_language
    texts = copy.deepcopy(node.get("texts") or {})
    t = texts.setdefault(lang, {})
    attrs = copy.deepcopy(node.get("attributes") or [])
    attr = next((a for a in attrs if a["id"] == aid), None)
    if attr is None:
        return
    for label in labels:
        lid = new_id()
        attr["levels"].append({"id": lid, "hint": "", "ai": True})
        t.setdefault("levels", {})[lid] = label
    node["attributes"], node["texts"] = attrs, texts
    vignettes.materialize(survey, node)


def take_pairs(survey, node, pairs):
    from app import vignettes

    have = list(node.get("prohibitions") or [])
    have += [{"a": p["a"], "la": p["la"], "b": p["b"], "lb": p["lb"]} for p in pairs]
    f = vignettes.factors(survey, node)
    node["prohibitions"] = f._parse_prohibitions([f"{p['a']}:{p['la']}|{p['b']}:{p['lb']}" for p in have],
                                                 node.get("attributes") or [])
    vignettes.materialize(survey, node)


def take_text(survey, node, text):
    """A new text element at the start of the loop's first block; returns it (None without a block)."""
    from app import loops
    from app.editor import _renumber, _unique_code
    from app.models import Item
    from app import items

    blocks = loops.blocks_in(survey, node)
    if not blocks:
        return None
    block = blocks[0]
    kind = items.get("info")
    item = Item(type="info", code=_unique_code(survey, "V"), config={**kind.default_config(), "ai": {"idea": "Vignettentext"}},
                texts={survey.default_language: {**kind.default_texts(survey.default_language), "question": text}})
    rows = [item] + list(block.items)
    block.items = rows
    _renumber(rows)
    return item


def from_form_factors(form):
    out = []
    for n in form.getlist("take"):
        name = _short(form.get(f"f{n}_name"))
        levels = [_short(l) for l in form.getlist(f"f{n}_level") if _short(l)][:MAX_LEVELS]
        if name and len(levels) >= 2:
            out.append({"name": name, "levels": levels})
    return out


def from_form_pairs(form):
    out = []
    for v in form.getlist("take"):
        m = re.fullmatch(r"([\w-]+):([\w-]+)\|([\w-]+):([\w-]+)", v or "")
        if m:
            out.append({"a": m.group(1), "la": m.group(2), "b": m.group(3), "lb": m.group(4)})
    return out
