"""Piping: the answer to an earlier question in a later text, written {{code}} with the question's variable name.

Works wherever texts take placeholders (question texts, instructions and info texts, Likert statements and scale
labels, the poles of a semantic differential, answer options, affixes of text fields), since the answers simply
join the values of the page (runtime.step), next to URL parameters, {{pid}} and the columns of a loop.
{{code|Ihr Produkt}} gives a text for an empty answer (not answered, skipped by a condition).

What goes in comes from the item type (pipe_text): the label of the chosen option in the participant's language,
"A, B und C" for several, a typed text, a number, a date. Where an answer has several values, each can go in by a
name of its own (pipe_parts: the rows of a matrix or of several sliders, the shares of a constant sum and the four
prices by their columns in the data set, <code>_rank1 for what was ranked first, <code>_gap1 for the first gap of a
cloze text). Answers from earlier pages go in on the server. An answer of the same page goes into the question
and info texts of that page while it is given, by the page's script (live, ItemType.pipe_live): single and
multiple choice, text and number fields, one slider, a rating; until then the text for an empty answer stands
there. Everything else of the same page would need the page to be rebuilt while it is filled in. Inside a loop, {{code}} of a question in the same loop
is the answer of the current pass. Typed values go into a text after its formatting (items.base.Typed), as one
line of at most 200 characters (items.base.typed_text).

The pre-launch check (check) names references to unknown or later questions, to types whose answers are no text (a
matrix, a ranking), names that are also URL parameters or loop columns, order made random by a randomizer, and
references without a text for an empty answer to questions that may stay empty. A question that gets a new
variable name takes the references to it along (renamed)."""
from collections.abc import Mapping

from app.items.base import Typed, rename_placeholder


def sources(survey):
    """{name: item} of what can stand in a text: the questions whose answer is a text of its own (by their
    code), and the single values of the answers that have several (by the names of pipe_parts)."""
    found = {}
    for block in survey.blocks:
        for item in block.items:
            if not (item.kind.collects_answer or item.kind.computed):
                continue
            if item.kind.pipeable(item):
                found[item.code] = item
            for name, _ in item.kind.pipe_parts(item, survey.default_language):
                found[name] = item
    return found


def label(item, name, lang):
    """What the value `name` of a question is, in words: the question, or for one value of several its own name
    with the question behind it ("Preis „günstig“ – Was darf der Apfel kosten?")."""
    from app.items.base import plain

    question = plain(item.text("question", lang))
    part = dict(item.kind.pipe_parts(item, lang)).get(name)
    if part is None or _whole(item, name):
        return question
    return f"{part} – {question}" if question else part


def _whole(item, name):
    return name == item.code and item.kind.pipeable(item)


def values(survey, answers, lang, row=None, skip=()):
    """{name: Typed text} of everything in sources() ("" without an answer), as load_answers() gives the
    answers; `row`: the current pass of a loop; `skip`: ids of the items on the current page (their answers
    are not final yet). A mapping that works a value out when a text asks for it (Values): a step rendered every
    value of the survey, Rechenwerte included, for the few placeholders of one page (audit 2026-10-06, P4)."""
    return Values(survey, answers, lang, row, skip)


class Values(Mapping):
    """The values of values(), each worked out on first use and then kept; sources() too is read only once a
    name is asked for that nothing above it in the step's variables has (runtime.step)."""

    def __init__(self, survey, answers, lang, row=None, skip=()):
        self._survey, self._answers, self._lang, self._row, self._skip = survey, answers, lang, row, skip
        self._found, self._done = None, {}

    @property
    def _sources(self):
        if self._found is None:
            self._found = sources(self._survey)
        return self._found

    def __getitem__(self, name):
        if name not in self._done:
            self._done[name] = self._value(name, self._sources[name])  # KeyError for a name that is none
        return self._done[name]

    def __iter__(self):
        return iter(self._sources)

    def __len__(self):
        return len(self._sources)

    def __contains__(self, name):
        return name in self._sources

    def _value(self, name, item):
        answers, lang, row = self._answers, self._lang, self._row
        if item.kind.computed:  # a Rechenwert: worked out from the answers so far, wherever it stands
            return Typed(item.kind.computed_text(item, answers, lang) or "")
        answer = None
        if item.id not in self._skip:
            key = f"{item.id}#{row}"
            answer = answers.get(key) if row and key in answers else answers.get(item.id)
        text = ""
        if answer not in (None, "", [], {}):
            text = item.kind.pipe_text(item, answer, lang) if _whole(item, name) else item.kind.pipe_part_text(item, answer, name, lang)
        return Typed(text or "")


def live(page_items, answers, lang, row=None):
    """{name: Live text} of the questions on the page whose answer the page's script can show in a text of the
    same page (ItemType.pipe_live), with what is stored of them so far (a page somebody came back to)."""
    from app.items.base import Live

    out = {}
    for item in page_items:
        spec = item.kind.pipe_live(item, lang) if item.kind.collects_answer and item.kind.pipeable(item) else None
        if not spec:
            continue
        key = f"{item.id}#{row}"
        answer = answers.get(key) if row and key in answers else answers.get(item.id)
        text = item.kind.pipe_text(item, answer, lang) if answer not in (None, "", [], {}) else ""
        out[item.code] = Live(text or "", spec)
    return out


def live_only(item):
    """The placeholders that stand only in the question or info text of `item` (in every language): there a
    same-page answer can go in live; statements, options and labels are plain texts."""
    from app.items.base import _walk_strings, placeholders

    inside, elsewhere = set(), set()
    for per in (item.texts or {}).values():
        for key, value in (per or {}).items():
            found = set().union(*[placeholders(v) for v in _walk_strings(value)] or [set()])
            (inside if key == "question" else elsewhere).update(found)
    return inside - elsewhere


def same_page(survey, item):
    """Names of the answers of the same page that a text of `item` can show live, in the order of the page."""
    pos = _positions(survey)
    here = pos.get(item.id)
    if here is None:
        return []
    lang = survey.default_language
    return [other.code for other in sorted(item.block.items, key=lambda i: i.position)
            if other.id != item.id and pos.get(other.id) == here and other.kind.collects_answer
            and other.kind.pipeable(other) and other.kind.pipe_live(other, lang)]


def names(survey):
    """Every name a text may use for an answer: the codes of all questions (the pre-launch check says which of
    them cannot go into a text) and the names of single values."""
    return {i.code for b in survey.blocks for i in b.items} | set(sources(survey))


def _positions(survey):
    """{item id: (block number in the flow, page number in the block)}."""
    from app import flow
    from app.runtime import split_alone

    order = {b: n for n, b in enumerate(flow.block_order(flow.tree(survey)))}
    out = {}
    for block in survey.blocks:
        for page_no, items in enumerate(split_alone(block.pages())):
            for item in items:
                out[item.id] = (order.get(block.id, -1), page_no)
    return out


def _loop_id(survey, block):
    from app import loops

    node = loops.node_of(block)
    return node["id"] if node else None


def earlier(survey, item):
    """Names of what `item` can show of the questions before it (on earlier pages), in flow order."""
    pos = _positions(survey)
    here = pos.get(item.id)
    if here is None:
        return []
    loop = _loop_id(survey, item.block)
    order = {i.id: n for n, i in enumerate(i for b in survey.blocks for i in b.items)}
    found = [(pos[s.id], n, code) for n, (code, s) in enumerate(sources(survey).items())
             if s.id in pos and _loop_id(survey, s.block) in (None, loop)
             and (pos[s.id] < here or (s.kind.computed and s.id != item.id and (pos[s.id], order[s.id]) < (here, order[item.id])))]
    return [code for _, _, code in sorted(found)]


def _shuffled_apart(survey, a, b):
    """True if a randomizer with a random order holds the two blocks in different branches."""
    from app import flow

    root = flow.tree(survey)
    outer_a = flow.ancestors(root, f"k{a.id}")
    outer_b = {flow.node_id(n) for n in flow.ancestors(root, f"k{b.id}")}
    return any(n["t"] == "rand" and n.get("shuffle") and flow.node_id(n) in outer_b for n in outer_a)


def _may_stay_empty(survey, source, block):
    """Why the answer to `source` can be missing where a question of `block` shows it ("" if it cannot)."""
    from app import flow

    if source.answer_mode != "required":
        return "ist keine Pflichtfrage"
    root = flow.tree(survey)
    shared = {flow.node_id(n) for n in flow.ancestors(root, f"k{block.id}")}
    if any(n["t"] in ("branch", "else") and flow.node_id(n) not in shared for n in flow.ancestors(root, f"k{source.block_id}")):
        return "steht hinter einer Bedingung"
    return ""


def renamed(survey, old, new):
    """The question with the variable name `old` is now called `new`: every {{old}} in the texts of the
    questionnaire follows, in all languages. Returns the codes of the questions whose texts changed. Nothing
    changes where the name also belongs to a URL parameter, or to a column of the loop a question stands in:
    there the placeholder never showed the answer."""
    from app import loops, panel
    from app.items.base import BUILTIN_VARIABLES

    if isinstance(old, (list, tuple)):  # the names of single values, before and after: each follows
        return sorted({code for a, b in zip(old, new) for code in renamed(survey, a, b)})
    if old == new or old in BUILTIN_VARIABLES | {p["name"] for p in panel.config(survey)["params"]}:
        return []
    changed = []
    for block in survey.blocks:
        if loops.active(block) and old in {c["name"] for c in loops.config(block)["columns"]}:
            continue
        for item in block.items:
            if old in item.kind.used_placeholders(item):
                item.texts = rename_placeholder(item.texts or {}, old, new)
                changed.append(item.code)
    return changed


def check(survey):
    """[(severity, message, item id, block id)] for the {{code}} references to answers."""
    from app import loops, panel
    from app.items.base import BUILTIN_VARIABLES

    pipeable = sources(survey)
    by_code = {**{i.code: i for b in survey.blocks for i in b.items}, **pipeable}
    params = BUILTIN_VARIABLES | {p["name"] for p in panel.config(survey)["params"]}
    pos = _positions(survey)
    problems = []
    for block in survey.blocks:
        columns = {c["name"] for c in loops.config(block)["columns"]} if loops.active(block) else set()
        for item in block.items:
            for code in sorted(item.kind.used_placeholders(item) & set(by_code)):
                source, ref = by_code[code], "{{" + code + "}}"
                if code in params or code in columns:
                    what = "Spalte der Schleife" if code in columns else "URL-Parameter"
                    problems.append(("error", f"{item.code}: {ref} ist Variablenname einer Frage und zugleich {what}; "
                                     "bitte eins von beiden umbenennen.", item.id, block.id))
                elif source.kind.computed:
                    # a Rechenwert has no page of its own: it only has to stand before the text that shows it
                    order = {i.id: n for n, i in enumerate(i for b in survey.blocks for i in b.items)}
                    if source.id == item.id or (pos.get(source.id, (0, 0)), order[source.id]) > (pos.get(item.id, (0, 0)), order[item.id]):
                        problems.append(("error", f"{item.code}: {ref} ist ein Rechenwert, der erst danach steht; er muss "
                                         "vor dem Text stehen, der ihn zeigt.", item.id, block.id))
                    elif code in item.kind.bare_placeholders(item):
                        problems.append(("warning", f"{item.code}: {ref} hat keinen Ersatztext; fehlt ein Wert der Rechnung, "
                                         "bleibt dort eine Lücke im Text. Ein Klick auf den Baustein im Text legt den "
                                         "Ersatztext fest.", item.id, block.id))
                elif not source.kind.collects_answer:
                    problems.append(("error", f"{item.code}: {ref}: {source.kind.label} hat keine Antwort, die sich "
                                     "einsetzen ließe.", item.id, block.id))
                elif code not in pipeable:
                    parts = bool(source.kind.pipe_parts(source, survey.default_language))
                    problems.append(("error", f"{item.code}: {ref}: Antworten auf {source.kind.label} lassen sich "
                                     "nicht in einen Text einsetzen" + (", nur ihre einzelnen Werte (im Text über „Antwort "
                                     "einfügen“ wählen)." if parts else "."), item.id, block.id))
                elif (source.id != item.id and pos.get(source.id) == pos.get(item.id) and code == source.code
                      and source.kind.pipe_live(source, survey.default_language) and code in live_only(item)):
                    # the same page: the page's script puts the answer into the question text while it is given
                    if code in item.kind.bare_placeholders(item):
                        problems.append(("warning", f"{item.code}: {ref} steht auf derselben Seite und hat keinen Ersatztext: "
                                         "Bis zur Antwort steht dort eine Lücke im Text. Ein Klick auf den Baustein im Text "
                                         "legt den Ersatztext fest.", item.id, block.id))
                elif source.id == item.id or pos.get(source.id, (0, 0)) >= pos.get(item.id, (0, 0)):
                    if source.id != item.id and pos.get(source.id) == pos.get(item.id):
                        problems.append(("error", f"{item.code}: {ref} steht auf derselben Seite. Dort lassen sich nur Antworten "
                                         "auf Einfach- und Mehrfachauswahl, Text- und Zahlenfelder, einen Schieberegler oder "
                                         "eine Bewertung einsetzen, und nur in den Frage- oder Infotext.", item.id, block.id))
                    else:
                        where = "in derselben Frage" if source.id == item.id else "erst danach"
                        problems.append(("error", f"{item.code}: {ref} steht {where}; eingesetzt werden nur Antworten "
                                         "von früheren Seiten oder derselben Seite.", item.id, block.id))
                elif _loop_id(survey, source.block) not in (None, _loop_id(survey, block)):
                    problems.append(("warning", f"{item.code}: {ref} wird in einer Schleife beantwortet, einmal je "
                                     "Durchlauf; außerhalb der Schleife bleibt es leer.", item.id, block.id))
                elif source.block_id != block.id and _shuffled_apart(survey, source.block, block):
                    problems.append(("warning", f"{item.code}: {ref} steht mit dieser Frage in einer Randomisierung "
                                     "mit zufälliger Reihenfolge und kommt dann manchmal erst danach.", item.id, block.id))
                elif code in item.kind.bare_placeholders(item) and _may_stay_empty(survey, source, block):
                    problems.append(("warning", f"{item.code}: {ref} hat keinen Ersatztext, und „{code}“ "
                                     f"{_may_stay_empty(survey, source, block)}; ohne Antwort bleibt dort eine Lücke im "
                                     "Text. Ein Klick auf den Baustein im Text legt den Ersatztext fest.", item.id, block.id))
    return problems
