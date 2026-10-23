"""Estimated completion time along the survey flow.

Per item: reading time for everything participants read (question, statements, options,
labels) at READ_WPM, plus a typical answering time per type; per page a short overhead.
Along the flow tree: a randomizer shows k of n children (range from the k shortest to the k
longest, typical k × mean); loops repeat their block; a branch without ELSE may or may not apply
(minimum without it, typical and maximum with it), and so may a question with "Nur zeigen, wenn …"; a branch with ELSE runs exactly one of the two
(minimum the shorter, maximum the longer, typical their mean); an end node stops its sequence if one of its
triggers always holds (a screen-out or exclusion); an end that ends only some (a full quota) counts like a branch
that may apply: minimum with the end, typical and maximum without it (those who go on, for whom the time is
said). A branch whose path ends the survey splits the rest in two: where it applies, nothing after it is shown;
where not, the rest runs (minimum and maximum over both, typical their mean).

The figures are heuristics for planning (consent text, panel pay), not a promise; the Export
page compares them with the observed median once data exist."""
import re

READ_WPM = 200           # survey reading speed (words per minute)
CONSENT_WPM = 300        # consent texts are skimmed
PAGE_SECONDS = 2         # clicking "Weiter", page load
ANSWER_SECONDS = {       # typical answering time per unit (after reading)
    "single_choice": 2, "multiple_choice": 4, "likert": 2, "semantic": 2.5, "rating": 3,
    "text_short": 6, "text_long": 30, "image": 4, "consent": 5, "conjoint_task": 10, "maxdiff_task": 20,
    "paired_task": 4, "paired_strength": 6,  # one pair: a choice, or a step on the strength scale (Askurious estimate)
    "cva_task": 5,  # one offer (or pair) of the ratings-based conjoint, a point on the scale; the attributes add 0.5 s each
    "sample_compare": 3,
    # a number recalled from memory ("Wie viele Minuten haben Sie heute ...?"): remembering and adding up takes
    # longer than typing a short answer (pre-test 2026-10-23, Ralf: 12 s instead of 6 s)
    "number_recall": 12,
}
PHONE_FACTOR = 1.3  # entries of a diary study are answered on the phone: about a third slower (pre-test 2026-10-23)


def _words(value):
    if isinstance(value, dict):
        return sum(_words(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_words(v) for v in value)
    return len(re.findall(r"\w+", str(value or "")))


def item_seconds(item, lang):
    t, c = item.type, item.config or {}
    texts = item.t(lang) if item.block is not None else ((item.texts or {}).get(lang) or next(iter((item.texts or {}).values()), {}))
    read = _words({k: v for k, v in texts.items() if k not in ("validation_message", "alt")}) * 60 / READ_WPM
    if t == "consent":
        return _words(texts) * 60 / CONSENT_WPM + ANSWER_SECONDS["consent"]
    if t == "likert":
        return read + ANSWER_SECONDS["likert"] * len(c.get("statements", []))
    if t == "semantic":
        return read + ANSWER_SECONDS["semantic"] * len(c.get("pairs", []))
    if t == "postal_code":
        return read + ANSWER_SECONDS["text_short"]
    if t == "text_input":
        if c.get("validation") in ("integer", "decimal"):
            return read + ANSWER_SECONDS["number_recall"]
        long = c.get("multiline") and c.get("validation", "none") == "none"
        return read + ANSWER_SECONDS["text_long" if long else "text_short"]
    own = item.kind.seconds(item, read)
    if own is not None:
        return own
    if t == "image_mark":
        return read + 6 + 3 * min(c.get("max") or 3, 10)
    if t == "map_marker":  # finding the place on the map takes a while, then the follow-up questions
        per_marker = 10 + 5 * len(c.get("questions", []))
        return read + 10 + per_marker * max(c.get("min") or 1, min(c.get("max") or 3, 3))
    if t == "slider":
        return read + 3 * max(len(c.get("rows", [])), 1)
    if t == "psm":
        return read + 4 * 6 + (2 * ANSWER_SECONDS["single_choice"] if c.get("nms") else 0)  # four prices to type
    if t == "constant_sum":
        return read + 4 * max(len(c.get("options", [])), 1)
    if t == "position2d":  # choose, place, maybe correct
        return read + 5 * max(len(c.get("options", [])), 1)
    if t == "association":  # thinking of words takes longer than rating them
        return read + 10 * c.get("slots", 3)
    if t == "sample_compare":
        return read + ANSWER_SECONDS["sample_compare"] * max(len(c.get("sources", [])), 1)
    if t in ("info", "page_break"):
        return read
    return read + ANSWER_SECONDS.get(t, 3)


def block_seconds(block, lang, shown_to_all=False):
    """Seconds of the block; shown_to_all: without the questions with "Nur zeigen, wenn …" (app/item_condition.py),
    and without a page of which nothing else is left (the shortest way through it)."""
    from app import item_condition, loops

    pages = block.pages()
    if shown_to_all:
        pages = [kept for kept in ([i for i in page if not item_condition.of(i)] for page in pages) if kept]
    once = sum(item_seconds(i, lang) for page in pages for i in page) + PAGE_SECONDS * len(pages)
    if loops.active(block):
        loop = loops.config(block)
        repeats = loops.shown_rows(block.survey, loop)
        return once * max(repeats, 1)
    return once


def _sequence(nodes, blocks, lang, per_block, start=0):
    """(low, typical, high, ends) of the nodes from `start` on; ends: every way through them reaches an end node,
    so nothing after this sequence is shown."""
    if start >= len(nodes):
        return 0.0, 0.0, 0.0, False
    node = nodes[start]
    if node["t"] == "end":
        from app import flow

        if any(flow.case_holds(case, {}) for case in flow.cases(node)):
            return 0.0, 0.0, 0.0, True  # nothing after an end node in this sequence is shown
        # an end for some only ("Quote voll"): most pass it
        rest = _sequence(nodes, blocks, lang, per_block, start + 1)
        return 0.0, rest[1], rest[2], rest[3]
    nxt = nodes[start + 1] if start + 1 < len(nodes) else None
    if node["t"] == "branch" and nxt and nxt["t"] == "else" and nxt.get("of") == node.get("id"):
        # IF with ELSE: exactly one of the two runs, then the rest unless that one ended the survey
        rest = _sequence(nodes, blocks, lang, per_block, start + 2)
        ways = [_then(_sequence(child.get("children", []), blocks, lang, per_block), rest) for child in (node, nxt)]
        return (min(w[0] for w in ways), sum(w[1] for w in ways) / 2, max(w[2] for w in ways), all(w[3] for w in ways))
    rest = _sequence(nodes, blocks, lang, per_block, start + 1)
    if node["t"] in ("branch", "else"):
        inner = _sequence(node.get("children", []), blocks, lang, per_block)
        if inner[3]:
            # the branch ends the survey where it applies: either its own path, or the rest without it
            return (min(inner[0], rest[0]), (inner[1] + rest[1]) / 2, max(inner[2], rest[2]), rest[3])
        return rest[0], inner[1] + rest[1], inner[2] + rest[2], rest[3]  # may or may not apply
    return _then(_node(node, blocks, lang, per_block) + (False,), rest)


def _then(first, rest):
    """A way through `first`, then `rest` unless `first` ended the survey."""
    if first[3]:
        return first
    return first[0] + rest[0], first[1] + rest[1], first[2] + rest[2], rest[3]


def _node(node, blocks, lang, per_block):
    t = node["t"]
    if t == "block":
        block = blocks.get(node["block"])
        s = block_seconds(block, lang) if block else 0.0
        if block:
            per_block[block.id] = s
        # questions shown only to some count like a branch that may or may not apply: minimum without them,
        # typical and maximum with them
        from app import item_condition

        some = block is not None and any(item_condition.of(i) for i in getattr(block, "items", ()))
        return (block_seconds(block, lang, shown_to_all=True) if some else s), s, s
    if t == "rand":
        parts = [_node(ch, blocks, lang, per_block) for ch in node.get("children", [])]
        if not parts:
            return 0.0, 0.0, 0.0
        k = min(max(int(node.get("k", 1)), 1), len(parts))
        lows = sorted(p[0] for p in parts)
        highs = sorted((p[2] for p in parts), reverse=True)
        return sum(lows[:k]), k * sum(p[1] for p in parts) / len(parts), sum(highs[:k])
    if t in ("branch", "else"):  # may or may not apply
        _, typ, high, _ = _sequence(node.get("children", []), blocks, lang, per_block)
        return 0.0, typ, high
    if t == "loop":  # block_seconds already multiplies by the number of passes
        return _sequence(node.get("children", []), blocks, lang, per_block)[:3]
    return 0.0, 0.0, 0.0  # split marker and unknown nodes take no time themselves


def on_phone(part):
    """Whether a part of a diary study is an entry answered on the phone: a module, not the entry or exit survey."""
    from app import diary

    return part not in (None, diary.ENTRY, diary.EXIT)


def estimate(survey, lang=None, part=None):
    """{"low", "typical", "high"} in seconds, "per_block" {block id: seconds}, "has_branches".

    A diary study (app/diary.py) is asked in parts: `part` ("entry", a module key or "exit") estimates one of them,
    and without it the entry survey is meant, the part a person answers in one go after joining (what the consent
    names as the duration; Ralf, 2026-10-11)."""
    from app import flow

    lang = lang or survey.default_language
    blocks = {b.id: b for b in survey.blocks}
    per_block = {}
    import copy

    root = copy.deepcopy(flow.tree(survey))
    in_tree = {n["block"] for n, _, _ in flow.walk(root) if n["t"] == "block"}
    root.setdefault("children", []).extend(  # like flow.normalize: missing blocks go to the end
        {"t": "block", "block": b.id} for b in survey.blocks if b.id not in in_tree)
    if getattr(survey, "sampling", None) is not None:
        from app import diary

        part = part or diary.ENTRY
        keep = {b.id for b in survey.blocks if diary.part_of(b) == part}

        def prune(node):
            node["children"] = [c for c in node.get("children", []) if c["t"] != "block" or c["block"] in keep]
            for c in node["children"]:
                if c["t"] != "block":
                    prune(c)

        prune(root)
    low, typ, high, _ = _sequence(root["children"], blocks, lang, per_block)
    if getattr(survey, "sampling", None) is not None:
        # the blocks of the other parts ("Moment", "Abschluss") are left out of the sum but still have their own
        # seconds: the flow page shows them beside every block, and they read "ca. 0 s" (pre-test 2026-10-20, 19)
        for b in survey.blocks:
            per_block.setdefault(b.id, block_seconds(b, lang))
            if on_phone(diary.part_of(b)):
                per_block[b.id] *= PHONE_FACTOR
        if on_phone(part):
            low, typ, high = low * PHONE_FACTOR, typ * PHONE_FACTOR, high * PHONE_FACTOR
    nodes = [n for n, _, _ in flow.walk(root)]
    return {"low": low, "typical": typ, "high": high, "per_block": per_block,
            "has_branches": any(n["t"] == "branch" for n in nodes),
            "has_random": any(n["t"] == "rand" for n in nodes)}


def minutes(seconds):
    """Rounded minutes for display (at least 1)."""
    from app import units

    return max(1, units.round_half_up(seconds / 60))  # half up: 2:30 reads 3 Min.


def _decimal_minutes(seconds):
    """Minutes with one decimal in the page's language: "1,2" (German) or "1.2" (English)."""
    from app import lang, units

    return units.decimal_text(seconds / 60, 1, lang="en" if lang.english() else "de")


def label(est):
    """E.g. "ca. 7 Min." or "ca. 7 Min. (5–9)"; short surveys (under 3 minutes) with one decimal, e.g.
    "ca. 1,6 Min. (0,6–1,6)", so a range within the same minute stays visible; under a minute in seconds to 5,
    "ca. 45 s (35–55)". One way of writing a duration, no "1:35 Min." beside "5 Min." (UI audit 2026-10-12, U14)."""
    from app.lang import de_en

    if est["high"] < 60:
        lo, typ, hi = (_five(est[k]) for k in ("low", "typical", "high"))
        return de_en("ca. {typ} s", "approx. {typ} s", typ=typ) + (f" ({lo}–{hi})" if lo != hi else "")
    if est["high"] < 180:
        lo, typ, hi = (_decimal_minutes(est[k]) for k in ("low", "typical", "high"))
    else:
        lo, typ, hi = minutes(est["low"]), minutes(est["typical"]), minutes(est["high"])
    return de_en("ca. {typ} Min.", "approx. {typ} min", typ=typ) + (f" ({lo}–{hi})" if lo != hi else "")


def _five(seconds):
    from app import units

    return units.round_half_up(seconds / 5) * 5  # an estimate, not a stopwatch


def label_html(est):
    """Like label(), with the range in brackets set smaller (for display; label() for plain text)."""
    from markupsafe import Markup, escape

    text = label(est)
    if " (" not in text:
        return escape(text)
    main, rng = text.split(" (", 1)
    return Markup(f'{escape(main)} <small class="dur-range">({escape(rng)}</small>')


def estimate_short(seconds):
    """An estimated duration of a block (flow page): "55 s" under a minute, above it minutes with one decimal,
    "1,2 Min.", the same way of writing as label() (UI audit 2026-10-12, U14: "ca. 1:09 Min." beside "ca. 5 Min.").
    Measured times (medians, cut-offs) stay "3:18 Min." (short)."""
    from app import units

    from app.lang import de_en

    s = units.round_half_up(seconds)
    return f"{s} s" if s < 60 else de_en("{m} Min.", "{m} min", m=_decimal_minutes(s))


def short(seconds):
    """Compact per-block figure, e.g. "40 s" or "2:10 Min."."""
    from app import units

    from app.lang import de_en

    s = units.round_half_up(seconds)
    return f"{s} s" if s < 60 else de_en("{m}:{s:02d} Min.", "{m}:{s:02d} min", m=s // 60, s=s % 60)
