"""Base class and helpers shared by all item types."""
import contextlib
import contextvars
import copy
import re
import secrets

from markupsafe import Markup, escape

from app.lang import Both, de_en, english

# {{name}}, or {{name|text}} with a text for an empty value (piping: an earlier question left unanswered)
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|([^{}|]*))?\}\}")
CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}\Z")  # \Z, not $: "$" also lets a newline at the end pass
# "Weiß nicht" of a Likert matrix, a rating or a date (config "dont_know", off by default; UI/UX audit 2026-10-05,
# V41): stored as DONT_KNOW, exported as DONT_KNOW_CODE (a date: 1 in its column <code>_dk), declared missing and
# left out of every statistic like -77 (rows.NOT_SHOWN)
DONT_KNOW = "dk"
DONT_KNOW_CODE = -98


def read_cfg(item):
    """The config of `item` for reading: Item.cfg (the defaults of its kind under the settings a stored row lacks),
    or the plain config of a stand-in without it (a shape not yet put into a question, vignette factors)."""
    c = getattr(item, "cfg", None)
    return c if isinstance(c, dict) else (item.config or {})


def dont_know(item):
    """True if the item offers "weiß nicht"."""
    return bool((item.config or {}).get("dont_know"))


# Numbers typed by participants (number fields, prices): beyond this they are slips or attempts to break the
# statistics (1e308 twice overflows a mean), and a float no longer holds every whole number exactly
MAX_NUMBER = 1e15
PLAIN_NUMBER_RE = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)")  # no "1e5", "1_000", "nan" or "inf", which float() accepts


# What an export prepares per element and not per participation (code review 2026-10-07, R4): export.rows opens a
# plan, and the type functions keep in it what does not depend on the answer (column names, statement codes, the
# confirmations of a consent, the source questions of a comparison). Outside an export each call builds anew.
_EXPORT_PLAN = contextvars.ContextVar("export_plan", default=None)


@contextlib.contextmanager
def export_plan():
    """While open, prepared() builds each thing once per element; export.rows opens one per call."""
    token = _EXPORT_PLAN.set({})
    try:
        yield
    finally:
        _EXPORT_PLAN.reset(token)


def prepared(item, name, build):
    """build(), once per element and name while an export plan is open (export_plan), else every time. The
    result is shared between the participations: callers must not change it."""
    plan = _EXPORT_PLAN.get()
    if plan is None:
        return build()
    key = (id(item), name)
    found = plan.get(key)
    if found is None or found[0] is not item:  # the element itself is held, so its id is not reused
        found = plan[key] = (item, build())
    return found[1]


def new_id(prefix=""):
    """A short random id (prefix and six hex digits): options, statements, rows, flow nodes, loop columns."""
    return prefix + secrets.token_hex(3)


def whole(value):
    """The whole number in a form field, or None: ASCII digits only, at most nine of them. `str.isdigit()`
    also takes "²" and digit strings of any length, where int() fails or gives a number no export can hold."""
    return int(value) if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 9 else None


def load_json(raw, default):
    """JSON a page sent in a hidden field, or `default` if it cannot be read (also when it is nested so deeply
    that the parser gives up). Read like every other JSON from a page (request_values.safe_loads): without control
    characters and lone surrogates, which made the data exports fail (audit 2026-10-13, TN-20), NaN refused."""
    from app.request_values import safe_loads

    try:
        return safe_loads(raw) if raw else default
    except (ValueError, RecursionError):
        return default


# placeholders filled for every participant (runtime.builtin_variables), not only in loops: the participation
# id, the language of the participation and the code of the survey. The variable name of a randomizer
# ({{cond1}}: what was drawn for this person) is known per survey (flow.branch_names).
BUILTIN_VARIABLES = {"pid", "lang", "survey"}


def number_text(n, lang="de"):
    """A number in a text for participants: whole numbers without ".0", a decimal comma except in the languages
    that write numbers like English (units.writes_like_english)."""
    from app.units import writes_like_english

    if float(n).is_integer():
        return str(int(n))
    return str(n) if writes_like_english(lang) else str(n).replace(".", ",")


class Typed(str):
    """A value that participants typed (an earlier answer, a URL parameter): it goes into a text after the
    formatting, so "**" or "<b>" in it stay as they are."""


class Live(Typed):
    """The answer to a question on the same page (piping.live): what is stored of it so far, and what the
    page's script needs to keep the text current while the person answers (participant.js, [data-pipe])."""

    def __new__(cls, value, spec):
        text = super().__new__(cls, value)
        text.spec = spec
        return text


def _live_span(name, value, fallback):
    """The place of a same-page answer in a text: the stored answer or the text for an empty one, in a span
    the page's script fills; participant.css isolates it with its own direction (X44). `fallback` is escaped
    already (it is part of the escaped text)."""
    spec = "".join(f' data-pipe-{key}="{escape(v)}"' for key, v in sorted(value.spec.items()) if v not in (None, ""))
    shown = str(escape(value)) or (fallback or "")
    return f'<span data-pipe="{escape(name)}"{spec} data-fallback="{fallback or ""}">{shown}</span>'


_RTL = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefe]")  # Hebrew, Arabic and their presentation forms


def _fill(text, variables, mark_missing, render=None):
    """Escaped text with its placeholders replaced; `render` (the formatting) runs before Typed values go in."""
    variables = variables or {}
    later = []

    def sub(m):
        name, fallback = m.group(1), m.group(2)
        value = variables.get(name)
        if isinstance(value, Live) and render is not None:
            # only in formatted texts (question, instruction, info text): a plain text can end up in an attribute
            later.append(_live_span(name, value, fallback.strip() if fallback is not None else ""))
            return f"\x01{len(later) - 1}\x01"
        if value not in (None, ""):
            if render is not None and isinstance(value, Typed):
                # an answer written right to left (Arabic in a German text) keeps its own direction and does not
                # turn the sentence around it (X44)
                later.append(f"<bdi>{escape(value)}</bdi>" if _RTL.search(value) else str(escape(value)))
                return f"\x01{len(later) - 1}\x01"
            return str(escape(value))
        if name not in variables and mark_missing:  # e.g. the editor's preview, where no answers exist
            return f'<span class="placeholder">{m.group(0)}</span>'
        if fallback is not None:  # escaped with the text; like a typed value it takes no formatting of its own
            if render is None:
                return fallback.strip()
            later.append(fallback.strip())
            return f"\x01{len(later) - 1}\x01"
        return "" if name in variables else m.group(0)

    html = PLACEHOLDER.sub(sub, str(escape(text or "")).replace("\x01", ""))
    if render is not None:
        html = re.sub("\x01(\\d+)\x01", lambda m: later[int(m.group(1))], render(html))
    return html


def fill(text, variables=None, mark_missing=False):
    """Escape text, replace {{var}} placeholders ({{var|text}}: the text where var is empty), and keep line
    breaks."""
    return Markup(_fill(text, variables, mark_missing).replace("\n", "<br>"))


_BOLD = re.compile(r"\*\*(.+?)\*\*")
# *italic*: an opening star (not after a star or a letter, not before a space or a star) pairs with the first closing
# star on the same line (not after a space or a star, not before a star or a letter). As one regex,
# (?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w]), this was quadratic on " *a *a *a ..." (audit 2026-10-10, T1):
# every opening star searched to the end of the line. _italic pairs the stars in one pass, with the same result.
_ITALIC_OPEN = re.compile(r"(?<![*\w])\*(?=[^\s*])")
_ITALIC_CLOSE = re.compile(r"(?<=[^\s*])\*(?![*\w])")


# _italic_ as in Markdown (pre-test 2026-10-20, 33), but never inside a word or a technical string: the opening
# underscore not after a letter, digit, underscore or a character of addresses, paths and placeholders
# (/ \ . : @ { } = ? # % + ~ ^ ` $ | & -), the closing one not before a letter, digit, underscore or such a
# character (punctuation like . , ; : ! ? ) and quotes may follow). So snake_case, Q1_1, a_b@x.de,
# https://x.de/_a_/, {{x_y}}, {{_x_}} and lines like "____" stay as they are.
_U_BEFORE = r"\w/\\.:@{}=?#%+~^`$|&\-"
_U_AFTER = r"\w/\\@{}=#%+~^`$|"
_UNDER_OPEN = re.compile(r"(?<![" + _U_BEFORE + r"])_(?=[^\s_])")
_UNDER_CLOSE = re.compile(r"(?<=[^\s_])_(?![" + _U_AFTER + r"])")


def _italic(text, before="<em>", after="</em>", mark="*", opener=_ITALIC_OPEN, closer=_ITALIC_CLOSE):
    """`text` with each *italic* part written as before + inner text + after; linear in the length.
    With mark="_" and the underscore patterns the same for _italic_ parts."""
    if mark not in text:
        return text
    closers = [m.start() for m in closer.finditer(text)]
    out, pos, c = [], 0, 0
    for m in opener.finditer(text):
        i = m.start()
        if i < pos:  # inside the part just closed
            continue
        while c < len(closers) and closers[c] < i + 2:
            c += 1
        if c == len(closers):
            break
        j = closers[c]
        if text.find("\n", i + 1, j) != -1:  # no closing star on this line: the next opening star tries again
            continue
        out += [text[pos:i], before, text[i + 1:j], after]
        pos = j + 1
    out.append(text[pos:])
    return "".join(out)


def _underscore(text, before="<em>", after="</em>"):
    """`text` with each _italic_ part written as before + inner text + after (see _UNDER_OPEN)."""
    return _italic(text, before, after, "_", _UNDER_OPEN, _UNDER_CLOSE)


_CODE = re.compile(r"`([^`]+)`")
# H~2~O and x^2^ (the Markdown syntax of Pandoc): no spaces inside, so "ca. ~5 Min." or a lone ^ stay as they are
_SUB = re.compile(r"(?<!~)~([^~\s]+)~(?!~)")
_SUP = re.compile(r"\^([^\^\s]+)\^")
_TAGS = ("b", "strong", "i", "em", "small", "u", "sub", "sup", "code")
_TAG = re.compile(r"&lt;(/?)(" + "|".join(_TAGS) + r")&gt;", re.I)
_BR = re.compile(r"&lt;br\s*/?&gt;", re.I)
FORMAT_HINT = "**fett**, *kursiv*, `Code`, H~2~O tief, x^2^ hoch; auch <u>, <small>, <sub>, <sup>, <br>"


def _inline(html):
    """Bold, italic, `code`, ~sub~, ^sup^ and a few plain HTML tags (no attributes) in already escaped text;
    tags that are opened but not closed are closed at the end, stray closing tags are dropped. Inside `code`
    nothing else is formatted."""
    codes = []
    html = html.replace("\x00", "")  # the marker of the kept `code` parts must not come from the text itself

    def keep(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    html = _CODE.sub(keep, html)
    html = _BOLD.sub(r"<strong>\1</strong>", html)
    html = _italic(html)
    html = _underscore(html)
    html = _SUB.sub(r"<sub>\1</sub>", html)
    html = _SUP.sub(r"<sup>\1</sup>", html)
    html = _BR.sub("<br>", html)
    stack, out, pos = [], [], 0
    for m in _TAG.finditer(html):
        out.append(html[pos:m.start()])
        closing, tag = m.group(1) == "/", m.group(2).lower()
        if not closing:
            stack.append(tag)
            out.append(f"<{tag}>")
        elif tag in stack:
            while stack:
                t = stack.pop()
                out.append(f"</{t}>")
                if t == tag:
                    break
        pos = m.end()
    out.append(html[pos:])
    out += [f"</{t}>" for t in reversed(stack)]
    return re.sub("\x00(\\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", "".join(out))


_PLAIN_TAG = re.compile(r"</?(?:" + "|".join(_TAGS) + r")>|<br\s*/?>", re.I)


def plain(text):
    """The text without its formatting marks, for places that show plain text: export and codebook labels,
    results, axis labels of the live comparison. `code` keeps its content, H~2~O becomes H2O."""
    if not text:
        return text or ""
    # one pass each, linear in the length (audit 2026-10-09b, O2); nested marks like "<su<sub>b>Holm</su</sub>b>"
    # leave a tag behind after one pass: then every angle bracket goes, so the result never holds markup (X1)
    t = _PLAIN_TAG.sub("", str(text))
    t = _CODE.sub(r"\1", t)
    t = _BOLD.sub(r"\1", t)
    t = _italic(t, "", "")
    t = _underscore(t, "", "")
    t = _SUB.sub(r"\1", t)
    t = _SUP.sub(r"\1", t)
    if _PLAIN_TAG.search(t):
        t = t.replace("<", "").replace(">", "")
    return t


def answer_label(text):
    """An answer option as a label outside the participant page (results, quotas, conditions): without the closing
    colon that leads into its text field ("Anderes, und zwar:"), which belongs to the participant page only
    (UI audit 2026-10-12, U27)."""
    return str(text or "").rstrip().rstrip(":：").rstrip()


def inline_rich(text, variables=None, mark_missing=False):
    """Formatting within one line or a few (question texts, instructions): like rich, without paragraphs,
    headings and lists; line breaks stay."""
    return Markup(_fill(text, variables, mark_missing, lambda html: _inline(html.replace("\n", "<br>"))))


def rich(text, variables=None):
    """Safe mini-markup for longer texts: blank line = paragraph, "- " = list item,
    **bold**, *italic*, `code`, H~2~O, x^2^, <b> <i> <em> <strong> <small> <u> <sub> <sup> <code> <br>
    (without attributes), {{variables}}.
    Everything else is escaped."""
    html_blocks = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        rendered = [_fill(l.strip(), variables, False, _inline) for l in lines]
        head = _BOLD.fullmatch(lines[0].strip())
        if head and "**" not in head.group(1):
            # a line that is one bold part starts a section: the heading is built from the inner text, not cut out
            # of the rendered line ("**a** und **b**" is two bold parts, "**<b>**" keeps no open <b>)
            html_blocks.append(f"<h3>{_fill(head.group(1), variables, False, _inline)}</h3>")
            lines, rendered = lines[1:], rendered[1:]
            if not lines:
                continue
        if all(l.lstrip().startswith("- ") for l in lines):
            items = "".join(f"<li>{r[2:] if r.startswith('- ') else r}</li>" for r in rendered)
            html_blocks.append(f"<ul>{items}</ul>")
        else:
            html_blocks.append("<p>" + "<br>".join(rendered) + "</p>")
    return Markup("".join(html_blocks))


def placeholders(text):
    return {m.group(1) for m in PLACEHOLDER.finditer(text or "")}


def bare_placeholders(text):
    """The names that stand at least once without a text for an empty value ({{name}}, not {{name|text}})."""
    return {m.group(1) for m in PLACEHOLDER.finditer(text or "") if not (m.group(2) or "").strip()}


def rename_placeholder(value, old, new):
    """`value` (a text, or texts in dicts and lists) with {{old}} and {{old|text}} written as {{new}}, {{new|text}}."""
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: m.group(0)[:m.start(1) - m.start()] + new + m.group(0)[m.end(1) - m.start():]
                               if m.group(1) == old else m.group(0), value)
    if isinstance(value, dict):
        return {k: rename_placeholder(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [rename_placeholder(v, old, new) for v in value]
    return value


TEXT_MAX = 20_000  # characters of one text of an element (question, instruction, info text, an option, ...): editor and file


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def too_long_text(texts, before=None):
    """The length of the longest text in `texts` (an element's texts: by language, then by name, dicts and lists
    inside) above TEXT_MAX, or None. A text that `before` already held as it is does not count: an element saved
    before the limit can still be edited (audit 2026-10-10, T1)."""
    kept = {s for s in _strings(before) if len(s) > TEXT_MAX}
    longest = max((len(s) for s in _strings(texts) if len(s) > TEXT_MAX and s not in kept), default=0)
    return longest or None


PIPE_LIMIT = 200  # characters of a typed answer that go into a later text


def typed_text(value, limit=PIPE_LIMIT):
    """What somebody typed, as it goes into a later text: one line, no control characters, at most `limit`
    characters (a longer text ends in "…")."""
    text = " ".join("".join(c if c.isprintable() else " " for c in str(value or "")).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class AnswerError(ValueError):
    pass


class FormRefused(ValueError):
    """What an element's editor form holds cannot be taken as it is (a table that cannot be read, or one that does
    not fit a fixed structure): nothing of the request is saved and the page shows the message (editor._malformed)."""


# --- settings read from a survey file (app/transfer.py) ------------------------------------------------------
# A file comes from a stranger, and its settings never went through update_from_form. Every type names its
# settings and what the editor would store for each (ItemType.import_rules); a rule takes the value of the file
# and returns it as stored, or raises Unfit. What a type does not name is not taken.


class Unfit(ValueError):
    """A setting of a survey file is not what the editor would store."""


ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}\Z")  # ids of options, statements, levels: part of field names in forms
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}\Z")


def form_id(value, prefix=""):
    """An id from an editor form as the file import takes it (ID_RE), else a new one: ids end up in field names,
    data attributes, CSS selectors of the scripts and HTML templates of the editor (audit 2026-10-13, XS-1/XS-2)."""
    return value if isinstance(value, str) and ID_RE.match(value) else new_id(prefix)


def form_ids(values, prefix=""):
    """form_id for the ids of the rows of an editor form (getlist): an empty one stays empty (a new row, the type
    gives it its id), the same unusable one gets the same new id in every place of one form."""
    replaced = {}
    out = []
    for value in values:
        if value and not ID_RE.match(value):
            value = replaced.setdefault(value, new_id(prefix))
        out.append(value)
    return out


def r_int(low, high, none=False):
    def rule(value):
        if value is None and none:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise Unfit
        return value
    return rule


def r_num(low, high, none=False):
    def rule(value):
        if value is None and none:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:  # NaN fails too
            raise Unfit
        return value
    return rule


def r_one(*values):
    def rule(value):
        if not any(type(value) is type(v) and value == v for v in values):
            raise Unfit
        return value
    return rule


def r_flag(value):
    if value is not None and not isinstance(value, bool):
        raise Unfit
    return bool(value)


def r_text(limit, low=0):
    def rule(value):
        if not isinstance(value, str) or not low <= len(value) <= limit:
            raise Unfit
        return value
    return rule


def r_id(value):
    if not isinstance(value, str) or not ID_RE.match(value):
        raise Unfit
    return value


def r_colour(value):
    if not isinstance(value, str) or not HEX_RE.match(value):
        raise Unfit
    return value


def r_maybe(rule):
    """A key of a row or of a dict that may be left out."""
    def optional(value):
        return rule(value)
    optional.optional = True
    return optional


def r_dict(**fields):
    """A dict with these keys and nothing else; keys wrapped in r_maybe may be missing."""
    def rule(value):
        if not isinstance(value, dict):
            raise Unfit
        out = {}
        for key, check in fields.items():
            if key in value:
                out[key] = check(value[key])
            elif not getattr(check, "optional", False):
                raise Unfit
        return out
    return rule


def r_rows(limit, low=0, unique="id", **fields):
    """A list of dicts (options, statements, levels, ...), each as r_dict; `unique`: a key that names the row."""
    row = r_dict(**fields)

    def rule(value):
        if not isinstance(value, list) or not low <= len(value) <= limit:
            raise Unfit
        out = [row(v) for v in value]
        if unique and len({r.get(unique) for r in out}) != len(out):
            raise Unfit
        return out
    return rule


def r_list(limit, rule, low=0):
    def check(value):
        if not isinstance(value, list) or not low <= len(value) <= limit:
            raise Unfit
        return [rule(v) for v in value]
    return check


def r_any(value):
    return value  # checked elsewhere (transfer._constructs, the sources of a feedback element)


def r_when(value):
    """"Nur zeigen, wenn …" of a question (app/item_condition.py): one condition as a statement's (rows.r_when)."""
    from app import rows

    return rows.r_when(value)


# settings every type may carry
IMPORT_COMMON = {
    "level": r_one("nominal", "ordinal", "metric"), "auto_next": r_flag, "construct": r_any,
    "ai": r_dict(idea=r_maybe(r_text(500)), model=r_maybe(r_text(100)), at=r_maybe(r_text(40))),
    "when": r_when,
    # where the items of a scale come from (app/item_source.py; user test 2026-10-27)
    "source": r_dict(text=r_maybe(r_text(600)), doi=r_maybe(r_text(200)), own=r_maybe(r_flag)),
}


LEVEL_LABELS = {"nominal": "nominal", "ordinal": "ordinal", "metric": "metrisch"}  # German: export, codebook
LEVEL_LABELS_EN = {"nominal": "nominal", "ordinal": "ordinal", "metric": "metric"}


def level_labels():
    """The measurement levels named in the language of the page (the editor's switch, the results page)."""
    return LEVEL_LABELS_EN if english() else LEVEL_LABELS


def reader(ui=None):
    """The language of the person a downloaded file is written for (concept "Mehrsprachige Oberfläche", E8): `ui`
    ("de" or "en") if given, else the page's (app.lang.current(); German outside a request, e.g. in a job)."""
    if ui:
        return "en" if ui == "en" else "de"
    from app.lang import current

    return current()


def in_lang(ui, de, en, **values):
    """A describing text of a downloaded file (codebook notes, types) in the reader's language `ui` (reader()):
    `de` or `en`, values as str.format fields as in de_en. Column names, codes and value labels never go through
    here: they stay as they are in every language."""
    text = en if reader(ui) == "en" else de
    return text.format(**values) if values else text

# One real participation that holds an answer of the item (":key", or ":key#<row>" from a loop pass) or its
# choice state (layout "cj"); see ItemType.has_real_answers
REAL_ANSWER_SQL = (
    "SELECT 1 FROM response r WHERE r.survey_id = :survey AND r.is_preview = 0 AND r.source = 'real' "
    "AND (json_type(r.layout, :state) IS NOT NULL OR EXISTS ("
    "SELECT 1 FROM json_each(r.answers) a WHERE a.key = :key OR substr(a.key, 1, :n) = :loop_key)) LIMIT 1")
# The same in PostgreSQL (the columns are of type json there and are read without a cast to jsonb; only an
# object has keys)
REAL_ANSWER_SQL_PG = (
    "SELECT 1 FROM response r WHERE r.survey_id = :survey AND r.is_preview = false AND r.source = 'real' "
    "AND ((r.layout -> 'cj' -> CAST(:key AS text)) IS NOT NULL OR EXISTS ("
    "SELECT 1 FROM json_object_keys(CASE WHEN json_typeof(r.answers) = 'object' THEN r.answers END) a(k) "
    "WHERE a.k = :key OR left(a.k, :n) = :loop_key)) LIMIT 1")


class FactSheet:
    """What an element type is for, as the type says it itself (concept "Elementtypen beschreiben sich selbst",
    Ralf 2026-10-08): the overview page "Elementtypen", AInstein and docs/elementtypen.md read it through
    app/element_types.py. Every text is a pair (German, English).

    group: the heading it stands under (element_types.GROUPS); can: what it can do, its variants and options;
    good_for / not_for: what it fits and what it does not (and what to take instead); designs: keys of the study
    designs it serves (ainstein_designs.DESIGN_NAMES); examples: uses written by us, never taken from surveys.
    What the class attributes already say (measurement level, loops, drop-down …) is not repeated here:
    element_types.facts() reads it from them, so it cannot go stale.
    keywords: words under which the search of the palette and of the page "Elementtypen" finds the type besides its
    name and description, German and English mixed (variants, other names: "Hot Spot", "Suchfeld", "NPS"; UI/UX audit
    2026-10-09, S14, X23, X11)."""

    def __init__(self, group, can, good_for, not_for=(), designs=(), examples=(), keywords=()):
        self.group, self.can, self.good_for, self.not_for = group, tuple(can), tuple(good_for), tuple(not_for)
        self.designs, self.examples, self.keywords = tuple(designs), tuple(examples), tuple(keywords)


class ItemType:
    key = ""
    label = ""
    description = ""
    short = ""  # a few words for the palette of the editor, one line without a cut word (at most about 40 characters;
    # UI audit 2026-10-12, U20); empty: the description
    order = 100
    collects_answer = True  # False for info text and images
    computed = False  # True: no answer, but a value worked out from the answers (items/calc.py: computed_text)
    ends_page = False  # True if the item brings its own submit buttons (consent)
    has_answer_mode = True  # False: the Optional/Hinweis/Pflicht switch makes no sense (e.g. sample comparison)
    category = "items"  # palette group: items | feedback
    # Types whose answer completes the page by itself (a click on an option, the end of a task) may
    # run without "Next"/"Back": the page then continues automatically (config "auto_next").
    supports_auto_next = False
    auto_next_inline = False  # the type's editor shows the tick itself, in its fieldset "Darstellung" (items/_auto_next.html)
    # Types whose answer options may show as a drop-down list instead of radio buttons (config "display")
    supports_dropdown = False
    # ... or as a search field that suggests matching options while typing (config "display" = "search"; long lists)
    supports_search = False
    supports_map = False  # the federal state: its options may also show as a map to click on (config "display")
    has_image = False  # True: uses the image pipeline (upload, AI generation, approval), see items/image.py
    # What an AI draft of this type is (config "ai", ai_scales.stamp): True for a scale whose items the AI worded
    # (items/likert.py): the editor says "nicht validiert", offers more items, the pre-launch check asks for the
    # reliability. False: the AI drafted texts of another element (Conjoint, IAT … from AInstein): check them.
    ai_scale = False
    # Results page (app/results.py): "auto" = per export column, numbers get M/Md/SD/Gini, anything else
    # counts answers; "categorical" = frequencies; "scale" = like auto plus Cronbach's alpha over the
    # item's columns; None = not summarised (e.g. feedback, conjoint: needs its own model)
    result_kind = "auto"
    # Measurement level of the answer columns ("nominal" | "ordinal" | "metric"): decides which statistics
    # the results page shows and the SPSS measurement level. The first entry is the default; with more
    # than one the editor offers a switch (config "level"). None: follows from the data (see
    # export._spss_dictionary), e.g. feedback elements; types with mixed columns override column_level.
    levels = None

    def level_choices(self, item):
        """The levels the editor offers for this item (a switch only with more than one)."""
        return self.levels or ()

    def default_level(self, item):
        return self.levels[0] if self.levels else None

    def level(self, item):
        chosen = (item.config or {}).get("level")
        return chosen if chosen in self.level_choices(item) else self.default_level(item)

    def column_level(self, item, column):
        """Measurement level of one export column (None: unknown, the export decides from the data)."""
        return self.level(item)

    def missing_codes(self, item, column):
        """Codes of one export column that count as missing ("weiß nicht", "keine Angabe"): they stay
        in the export, are left out of statistics and are declared as missing values for SPSS."""
        return ()

    def date_kind(self, item, column):
        """"date" | "time" | "datetime" | "month" if the column holds such ISO texts ("1987-05-23", "14:30",
        "1987-05-23T14:30", "1987-05"): the SPSS exports then write a date variable. None otherwise."""
        return None

    # Page engine (app/runtime.py): True = implicit page breaks before and after the item
    alone_on_page = False
    # False: the item keeps per-response state or needs the main session (conjoint, consent)
    allowed_in_loop = True
    allowed_in_separated = True
    # Editor: tabs over the item form as [(key, title, hint)] (elements carry data-cj-tab="key"),
    # and a panel below the form (e.g. the conjoint design)
    editor_tabs = None
    panel_template = None
    auto_next_hint = Both("sobald die Antwort vollständig ist", "as soon as the answer is complete")
    # Long-format export (one row per response and shown alternative): file prefix, what a row is (export page)
    # and a line for the README of the data package; see long_columns and long_rows
    long_export = None
    long_export_label = ""
    long_export_note = ""
    long_private = False  # the long format holds what a public data package leaves out without the tick (exact positions)
    image_set_label = Both("Dynamische Bilder", "Dynamic images")  # title of the image set page (items/images.html)
    # Choice designs (conjoint, MaxDiff, ACBC): stored answers refer to positions of profiles, levels
    # and statements, so the structure is locked once real participants have data (design_locked)
    design_lock = False
    # what the type can do and what it is for (FactSheet above); every registered type has one (tests/items/test_element_types.py)
    fact_sheet = None

    @property
    def search_words(self):
        """The keywords of the fact sheet in one line, for the searches of the palette and of /itemtypes (S14)."""
        return " ".join(self.fact_sheet.keywords) if self.fact_sheet else ""
    # Numeric answers with a true zero (amounts, counts, prices): only for them the results page shows the Gini
    # coefficient, which depends on where zero lies (reaction times and distances count by their unit)
    ratio_scale = False

    def is_ratio(self, item):
        """ratio_scale for this item: typed numbers and sliders ask the researcher (tick "Beträge oder Anzahlen")."""
        return self.ratio_scale

    def preview_empty(self, item):
        """The one line the element editor's preview shows instead of an empty or placeholder element:
        (text for the researcher, editor tab with the missing step or None), or None when there is something to show
        (UI/UX audit 2026-10-07, W39)."""
        return None

    def design_locked(self, item):
        """True if real participants (not test runs or dummies) have state or answers for this item."""
        return self.design_lock and self.has_real_answers(item)

    # config keys that say how a stored answer is read: the number of scale points, the range, the reversal. Like the
    # codes they stay as they are once real answers exist (answer_shape, editor.edit_item)
    scale_keys = ()
    # further config keys that say how export, codebook and results read a stored answer ("weiß nicht", the mode of
    # Bild markieren, ...; audit 2026-10-10, N-3): fixed as well once real answers exist (read_shape, READ_WORDS)
    read_keys = ()
    # lists of elements (config keys) to which nothing may be added once real answers exist: in a Q sort and a ranking
    # a new statement or option changes the task itself (the forced distribution, the number of ranks), not only what
    # can be chosen (audit 2026-10-12, M-4, M-7; elsewhere adding stays allowed, decisions.md T-32)
    closed_lists = ()
    # lists of elements whose rows added in a survey opened for editing again carry "since": the questionnaire version
    # from which they are shown; earlier participations get -77 (not shown) in their columns, not 0 (audit 2026-10-12,
    # N-5; app/answer_lock.py stamp_added)
    since_lists = ()

    def answer_shape(self, item):
        """What stored answers mean, as a comparable value: the variable name, the code and reversal of every element
        with an id and a code (options, statements, rows, pairs), the scale_keys and the read_keys. A survey opened
        for editing again keeps it for questions with real answers: a new code, a removed option, 7 instead of 5
        points, a reversal switched or "weiß nicht" taken away would read the answers given before differently (data
        audit 2026-10-05, T-32; audit 2026-10-10, N-3)."""
        c = item.config or {}
        elements = {key: {e["id"]: (e.get("code"), bool(e.get("reverse"))) for e in value}
                    for key, value in c.items()
                    if isinstance(value, list) and value
                    and all(isinstance(e, dict) and "id" in e and "code" in e for e in value)}
        read = read_cfg(item)  # with the defaults of the kind: a setting a stored row lacks is no change
        return {"code": item.code, "elements": elements, "scale": {k: read.get(k) for k in self.scale_keys},
                "read": self.read_shape(item),
                "closed": {k: sorted(e["id"] for e in c.get(k) or [] if isinstance(e, dict) and "id" in e)
                           for k in self.closed_lists}}

    def read_shape(self, item):
        """The read_keys as {key: value}; a key may carry "|<id>" for a part (a column of the side-by-side matrix)."""
        c = read_cfg(item)
        return {k: c.get(k) for k in self.read_keys}

    def freeze_answer_shape(self, item):
        """Called when a survey with real answers of `item` is opened for editing again (app/lifecycle.py): a
        setting that is only worked out while reading (the grid of a Q sort) is written down as it is. Nothing here."""
        return None

    # config keys that only change how a choice task looks, not what a stored answer refers to
    PRESENTATION_KEYS = ("auto_next", "level", "explain", "hide_attr_labels", "show_visual_in_table")

    def answer_structure(self, item):
        """What stored answers of a choice design refer to (attributes, levels, statements, the design), as a
        comparable string: when it changes, earlier answers would be read against another design."""
        import json

        config = {k: v for k, v in (item.config or {}).items() if k not in self.PRESENTATION_KEYS}
        if isinstance(config.get("attributes"), list):  # "Stufen sind geordnet" only orders the results (U23)
            config["attributes"] = [{k: v for k, v in a.items() if k != "ordered"} if isinstance(a, dict) else a
                                    for a in config["attributes"]]
        return json.dumps(config, sort_keys=True, default=str)

    def forget_trial_answers(self, item):
        """Remove the answers and the drawn design version of test runs and dummy data for this item (real
        answers lock the design instead). Returns the number of participations that held some."""
        from app.extensions import db
        from app.models import Response

        key, n = str(item.id), 0
        for r in db.session.scalars(db.select(Response).filter(
                Response.survey_id == item.block.survey_id, Response.source != "real")):
            answers = {k: v for k, v in (r.answers or {}).items() if k.split("#")[0] != key}
            cj = {k: v for k, v in ((r.layout or {}).get("cj") or {}).items() if k != key}
            if len(answers) != len(r.answers or {}):
                r.answers, n = answers, n + 1
            if r.layout and len(cj) != len(r.layout.get("cj") or {}):
                r.layout = {**r.layout, "cj": cj}
        return n

    def scale_values(self, item, column):
        """All values of a short whole-number scale, so the results page also lists levels nobody chose; None
        where the labels already name every level or the scale is not such a list."""
        return None

    def has_real_answers(self, item):
        """True if a real participation holds an answer or a choice state of this item (also from a loop pass).
        Names that later became invalid are then left alone: renaming would cut the answers off."""
        if item.id is None:
            return False
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError, OperationalError

        from app.extensions import db
        from app.models import Response

        key, survey_id = str(item.id), item.block.survey_id
        dialect = db.engine.dialect.name
        if dialect in ("sqlite", "postgresql"):
            # The database looks into the stored JSON itself and stops at the first participation that has the
            # item. The editor asks this for every choice question of the questionnaire; reading all answers
            # of the survey each time took 2 s per page with 1000 participations.
            loop_key = key + "#"  # a pass of a loop: "<item id>#<row id>"
            params = {"survey": survey_id, "key": key, "n": len(loop_key), "loop_key": loop_key}
            if dialect == "postgresql":
                try:
                    with db.session.begin_nested():
                        return db.session.execute(text(REAL_ANSWER_SQL_PG), params).first() is not None
                except DBAPIError:
                    # a stored "\u0000" (from before control characters were removed from form values) cannot
                    # be turned into text by PostgreSQL: read the answers here instead of failing the page
                    pass
            else:
                try:
                    return db.session.execute(text(REAL_ANSWER_SQL), {**params, "state": f'$.cj."{key}"'}).first() is not None
                except OperationalError:
                    pass  # an SQLite built without the JSON functions: read the answers here
        rows = db.session.execute(db.select(Response.answers, Response.layout).filter_by(
            survey_id=survey_id, is_preview=False, source="real"))
        return any(any(k.split("#")[0] == key for k in (answers or {})) or key in ((layout or {}).get("cj") or {})
                   for answers, layout in rows)

    def result_column(self, item, column):
        """How one export column is summarised: (kind, unit, value labels or None). Override for
        items whose columns differ, e.g. answers and reaction times of the assignment task."""
        return self.result_kind, "", None

    def auto_next(self, item):
        return self.supports_auto_next and bool((item.config or {}).get("auto_next"))

    def dropdown(self, item):
        """True if the answer options show as a drop-down list (single choice and the types built on it)."""
        return self.supports_dropdown and (item.config or {}).get("display") == "dropdown"

    def search(self, item):
        """True if the answer options show as a search field with suggestions (single choice, long lists)."""
        return self.supports_search and (item.config or {}).get("display") == "search"

    def optional_options(self, item, lang):
        """[(option id, label, shown)]: answer options of the construct an item was built from that each element
        may leave out (`"optional": True` in the construct's options, e.g. the self-description of gender). Taking
        one away or back is no change of the construct (constructs.modifications), so it stays switchable on a
        locked construct."""
        template = (((item.config or {}).get("construct")) or {}).get("template") or {}
        options = [o for o in (template.get("config") or {}).get("options", []) if o.get("optional")]
        if not options:
            return []
        have = {o["id"] for o in (item.config or {}).get("options", [])}
        texts = template.get("texts") or {}
        labels = (texts.get(lang) or (texts.get("de") if lang == "de-du" else None) or texts.get("en")
                  or next(iter(texts.values()), {})).get("options", {})
        mine = item.t(lang).get("options", {})
        return [(o["id"], mine.get(o["id"]) or labels.get(o["id"], o["code"]), o["id"] in have) for o in options]

    def set_optional_options(self, item, form):
        """The editor's ticks of optional_options (form field `optional_option`, sent with `optional_options_shown`):
        a ticked option is put back at its place of the construct (with its wording where the element has none),
        an unticked one is taken out."""
        template = (((item.config or {}).get("construct")) or {}).get("template") or {}
        canon = (template.get("config") or {}).get("options", [])
        optional = {o["id"] for o in canon if o.get("optional")}
        if not optional or not form.get("optional_options_shown"):
            return
        wanted = set(form.getlist("optional_option")) & optional
        options = [o for o in (item.config or {}).get("options", []) if o["id"] not in optional or o["id"] in wanted]
        have = {o["id"] for o in options}
        for n, o in enumerate(canon):
            if o["id"] in wanted and o["id"] not in have:
                before = {c["id"] for c in canon[:n]}  # after the last option that comes before it in the construct
                at = max((i + 1 for i, x in enumerate(options) if x["id"] in before), default=0)
                options.insert(at, {k: v for k, v in o.items()})
                have.add(o["id"])
        texts = copy.deepcopy(item.texts or {})
        spec_texts = template.get("texts") or {}
        for lang, lt in texts.items():
            source = spec_texts.get(lang) or (spec_texts.get("de") if lang == "de-du" else None) or spec_texts.get("en") or {}
            labels = lt.setdefault("options", {})
            for oid in wanted:
                if not labels.get(oid) and (source.get("options") or {}).get(oid):
                    labels[oid] = source["options"][oid]
        item.config = {**item.config, "options": options}
        item.texts = texts

    def set_display(self, item, form):
        """The editor's choice "Dropdown-Liste" under "Darstellung" (before: a tick). Only the presentation: codes, answers and export
        stay the same, so it is not part of the structure (it stays switchable on a locked construct). Radio
        buttons are the default and leave no key in the config."""
        config = {k: v for k, v in (item.config or {}).items() if k != "display"}
        shown = form.get("presentation") or form.get("display")  # "Darstellung" (V85) or the former tick
        if shown == "dropdown" or (shown == "search" and self.supports_search):
            config["display"] = shown
        item.config = config

    def one_click(self, item):
        """True if one click (per radio group) completes the answer, so the page may continue
        automatically. Items without an answer are neutral (see runtime.auto_advance_ok)."""
        return False

    # --- defaults ---------------------------------------------------------------

    def default_config(self):
        return {}

    def default_texts(self, lang):
        return {"question": ""}

    def former_default_texts(self, lang):
        """Standard wordings of earlier versions in `lang` (German or English) that a stored text may still hold;
        such a text counts as unchanged when another language falls back to it (Item.t)."""
        return {}

    # --- editor -----------------------------------------------------------------

    @property
    def editor_template(self):
        return f"items/{self.key}/editor.html"

    @property
    def participant_template(self):
        return f"items/{self.key}/participant.html"

    def update_from_form(self, item, form, lang, structural):
        """Apply the editor form. `structural` is False once the survey has started:
        then only texts may change, never codes, options, or scale points."""
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "question": form.get("question", "").strip()}
        item.texts = texts

    def validate(self, item):
        """Return a list of problems (strings) that block starting the survey."""
        return []

    def check_warnings(self, item):
        """Hints for the check of the draft (app/design.py) that do not block the start."""
        return []

    # Settings of this type in a survey file (app/transfer.py): {config key: rule}, see the r_... rules above.
    # None: the type has not said what it takes, and is not read from a file at all. A new setting belongs here
    # with the bounds update_from_form gives it, or it is left out when a file is read in.
    import_rules = None

    def import_config(self, config):
        """The settings of a file as this type stores them: (config, names of settings the editor would not
        store, names of settings this type does not know). Settings the file leaves out get their standard."""
        rules = {**IMPORT_COMMON, **(self.import_rules or {})}
        config = {**{k: v for k, v in self.default_config().items() if k in rules}, **config}
        out, unfit = {}, []
        for key, value in config.items():
            if key in rules:
                try:
                    out[key] = rules[key](value)
                except Unfit:
                    unfit.append(key)
        return out, unfit, [key for key in config if key not in rules]

    def import_problems(self, item):
        """What only shows with all settings together (a range, a design that fits the attributes): reasons
        why the item of a survey file cannot be taken. [] = fine."""
        return []

    def used_placeholders(self, item):
        found = set()
        for lang_texts in (item.texts or {}).values():
            for v in _walk_strings(lang_texts):
                found |= placeholders(v)
        return found

    def bare_placeholders(self, item):
        """The placeholders that stand somewhere without a text for an empty value."""
        found = set()
        for lang_texts in (item.texts or {}).values():
            for v in _walk_strings(lang_texts):
                found |= bare_placeholders(v)
        return found

    # --- participant side --------------------------------------------------------

    def parse_answer(self, item, form, lang="de", meta=None):
        """Return the stored answer (JSON-serialisable) or raise AnswerError."""
        return None

    def display_value(self, item, form, meta=None):
        """The raw input to show again when the page fails validation."""
        return form.get(f"i{item.id}")

    def soft_warning(self, item, answer, lang):
        """A message for a valid but doubtful answer (e.g. prices out of order), shown once like a
        nudge; a second "Next" keeps the answer. None if there is nothing to warn about."""
        return None

    def merge_answer(self, old, new):
        """How a new answer for this item combines with a stored one (conjoint: per task)."""
        return new

    def is_missing(self, item, answer):
        """True if the participant skipped (part of) the item. Drives nudges and required checks."""
        return self.collects_answer and answer in (None, "", [], {})

    # --- piping: the answer as text in later questions ({{code}}, see app/piping.py) ---------------

    def pipeable(self, item):
        """True if the answer can stand in a text as a few words (a choice, a number, a typed text)."""
        return False

    def pipe_text(self, item, answer, lang):
        """The stored answer as plain text in the participant's language ("" for none)."""
        return ""

    def pipe_live(self, item, lang):
        """None, or what the page's script needs to show the answer in a text of the same page while it is given
        (participant.js, [data-pipe]): {"kind": "choice" | "multi" | "text" | "value", ...}. Only where the
        script can form the text without the server."""
        return None

    def pipe_parts(self, item, lang):
        """[(name, label)] of the single values of an answer that can each stand in a text where the answer as
        a whole cannot (the rows of a matrix, the four prices): the name is the value's column in the data set
        where it has one, the label says which value it is (the statement, "Preis günstig")."""
        return []

    def pipe_part_text(self, item, answer, name, lang):
        """The value `name` (of pipe_parts) of the stored answer as plain text ("" for none)."""
        return ""

    def fixed_summary(self, item, answer, lang):
        """[(label, text)]: the stored answer in words in the participant's language, for an answer fixed by feedback
        that a page shows again without letting it change (participant/_item.html, app/feedback_lock.py). The label
        names a part of the answer ("" for the answer as a whole). Default: the texts of piping; a type whose answer
        cannot be piped (a map, a marked image, a Q-sort) says it in its own words, so screen readers read more than
        disabled controls (UI/UX audit 2026-10-17, W-A03). [] if there is nothing to say."""
        parts = self.pipe_parts(item, lang)
        if parts:
            return [(label, self.pipe_part_text(item, answer, key, lang)) for key, label in parts]
        if self.pipeable(item):
            return [("", self.pipe_text(item, answer, lang))]
        return []

    # --- export -----------------------------------------------------------------

    def references(self, item):
        """Sources to cite when this item is used (README of the export), e.g. the method's paper."""
        return []

    def source_groups(self, item):
        """The sources of the rules the editor page of this item states: [(what they belong to, [full citations])],
        shown in one closed fold at the end of the page (_sources.html, grouped)."""
        return []

    def export_columns(self, item):
        """[(column, label)]. Default: one column named after the item code."""
        return [(item.code, item.text("question"))] if self.collects_answer else []

    def open_columns(self, item):
        """The export columns of one element (without a loop suffix) that hold what a person typed in freely, an exact
        position, a day or a postal code: they can name or locate someone, so a public data package (Zenodo, Coscine)
        has them only after an explicit confirmation (export.open_columns). [] for an element without any. A type that
        stores free text names its columns here; tests/exports/test_open_columns.py lists every type that has none."""
        return []

    def place_columns(self, item):
        """The export columns of a list of places (municipalities with their AGS in a search field or a drill-down):
        they locate a person about as closely as a postal code, so a public data package has them only with the tick
        for open answers, like the postal code (export.open_item_columns; audit 2026-10-10, question on N). [] by
        default; unlike open_columns they are codes, not texts to categorise."""
        return []

    def export_values(self, item, answer):
        """{column: value}, with reverse coding already applied."""
        return {item.code: answer} if self.collects_answer else {}

    def codebook_info(self, item, column, lang, ui=None):
        """(type, values, [notes]) for one export column in the codebook, or None for the generic entry.
        Types with their own columns (e.g. the map) describe them here instead of in export.codebook. `lang` is the
        survey language of the value labels, `ui` the reader's language of the notes (reader(); in_lang). The type
        stays German: it is a key (export.TYPES_EN names it in English when the codebook is written)."""
        return None

    def order_codes(self, item):
        """{element id: export column} if the item shows its elements in a random order per response
        (runtime.build_layout, layout["stmt"]); the export then writes <code>_order. None otherwise."""
        return None

    # the export label of <code>_unchanged for a type whose starting state is itself a complete answer (ranking: the
    # start order, constant sum: the even split), else None (UI/UX audit 2026-10-16, 105)
    kept_start_label = None

    def kept_start(self, item, answer, order):
        """1 if the answer is the starting state the page showed, confirmed without a change; 0 if it was changed;
        None without an answer. `order`: the element ids in the order shown (layout["stmt"]) or empty."""
        return None

    def export_response_values(self, item, answer, response):
        """{column: value} that need the response itself, e.g. presentation drawn from its id."""
        return {}

    def export_state_values(self, item, state):
        """{column: value} from the item's per-response state (layout["cj"], see expand_pages)."""
        return {}

    def long_columns(self, item):
        """Columns of the long format after resp_id and source (types with long_export)."""
        return []

    def long_rows(self, item, response):
        """Rows of the long format for one response (without resp_id and source)."""
        return []

    def questionnaire_lines(self, item, lang):
        """Extra Markdown lines below the item in questionnaire.md."""
        return []

    # --- pages, duration, dummy data, images ---------------------------------------

    def expand_pages(self, survey, item, rng):
        """None: the item sits on a normal page. Otherwise (state, [page specs]): the item gets
        pages of its own, one per spec (merged into {"block", "items": [id], "cond"}); state is
        stored per response in layout["cj"][item id]. A spec with "dyn": True is shown only if
        page_applies() says so, e.g. adaptive tasks whose number depends on earlier answers;
        the page count then is an upper bound."""
        return None

    def page_applies(self, item, spec, answers):
        """For pages with "dyn": show this page, given the answers so far (item id -> answer)?"""
        return True

    # True: parse_answer takes the answers so far as `answers` (item id -> answer; inside a loop the answers
    # of the current pass), e.g. a total that comes from an earlier answer
    uses_answers = False

    def applies(self, item, answers):
        """Is this question shown to a person with these answers so far (item id -> answer)? False leaves it
        out of its page (runtime.present); a page on which nothing is left is skipped, like one behind a
        condition of the flow. Its export columns stay empty."""
        return True

    def remap_config(self, config, twin):
        """The settings for a copy of the questionnaire (duplicate, survey file): what names another question
        by its id gets the id of the copy, twin(old id) -> new id or None if that question did not come along."""
        return config

    def timing_pages(self, item):
        """Pages of an item with pages of its own, for the timing columns:
        [(column suffix, label suffix, page key suffix)], or None for a normal page."""
        return None

    def seconds(self, item, read):
        """Estimated seconds (app/duration.py), or None for the generic estimate by type."""
        return None

    def dummy_fields(self, item, rng, meta, answers=None):
        """Form fields with a random valid answer for dummy data, or None for the generic ones;
        answers: what the page shows (item id -> answer), e.g. for adaptive tasks."""
        return None

    def has_image_set(self, item):
        """True if the item needs one pre-generated image per combination (items/images.html)."""
        return self.has_image and self.is_dynamic(item)

    def image_size(self, item):
        return item.config.get("size", "1536x1024")


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def shape_changes(before, after):
    """What changed between two answer_shape values, as words for a message ("die Codes", ...); elements added
    are no change (no answer refers to them)."""
    out = []
    if before["code"] != after["code"]:
        out.append(de_en("der Variablenname", "the variable name"))
    codes = reversal = False
    for key, old in before["elements"].items():
        new = after["elements"].get(key, {})
        for eid, (code, reverse) in old.items():
            if eid not in new or new[eid][0] != code:
                codes = True
            elif new[eid][1] != reverse:
                reversal = True
    if codes:
        out.append(de_en("die vorhandenen Optionen, Aussagen oder Zeilen mit ihren Codes",
                         "the existing options, statements or rows with their codes"))
    if reversal or before["scale"].get("reverse") != after["scale"].get("reverse"):
        out.append(de_en("die Umpolung", "the reversal"))
    if {k: v for k, v in before["scale"].items() if k != "reverse"} != {k: v for k, v in after["scale"].items() if k != "reverse"}:
        out.append(de_en("die Stufenzahl bzw. der Wertebereich", "the number of points or the range of values"))
    read_before, read_after = before.get("read") or {}, after.get("read") or {}
    words = []
    for key, old in read_before.items():  # a part added later (a new column) has no answers yet
        name = key.split("|")[0]
        new = read_after.get(key)
        if name in ONLY_FEWER_REFUSED and (new or 0) >= (old or 0):
            continue  # offering "weiß nicht" or more own groups from now on leaves earlier answers as they are
        if name in ONLY_APPENDED and isinstance(old, list) and isinstance(new, list) and new[:len(old)] == old:
            continue  # rows added at the end: a stored place still names the same row
        if new != old and READ_WORDS[name] not in words:
            words.append(READ_WORDS[name])
    out += [de_en(*w) for w in words]
    closed_before, closed_after = before.get("closed") or {}, after.get("closed") or {}
    if any(set(closed_after.get(k) or []) - set(ids) for k, ids in closed_before.items()):
        out.append(de_en("die Zahl der Aussagen oder Optionen (auch kein Ergänzen: In Q-Sort und Rangfolge ändert "
                         "es die Aufgabe selbst)",
                         "the number of statements or options (no adding either: in a Q sort and a ranking it changes "
                         "the task itself)"))
    return out


def unseen_since(item, key, response):
    """The codes of the rows of config[key] that were added after this participation's questionnaire version
    ("since", app/answer_lock.py stamp_added): it never saw them (audit 2026-10-12, N-5)."""
    version = getattr(response, "qversion", None) or 1
    return {e.get("code") for e in (item.config or {}).get(key) or []
            if isinstance(e, dict) and isinstance(e.get("since"), int) and version < e["since"]}


def since_note(row, ui=None):
    """Codebook: what -77 means in the column of a row added later (row["since"]), or None."""
    from app.rows import NOT_SHOWN

    if not isinstance(row.get("since"), int):
        return None
    return in_lang(ui, "{n} = nicht gezeigt: ab Fragebogen-Fassung {v} ergänzt, Teilnahmen mit qversion unter {v} "
                       "sahen es nicht; zählt als fehlend",
                   "{n} = not shown: added from questionnaire version {v}, participations with qversion below {v} did "
                   "not see it; counts as missing", n=NOT_SHOWN, v=row["since"])


def default_texts(item):
    """The texts of the default language of `item` (also of a stand-in with only `texts` and maybe `block`)."""
    texts = getattr(item, "texts", None) or {}
    lang = None
    with contextlib.suppress(AttributeError):
        lang = item.block.survey.default_language
    return texts.get(lang) or texts.get("de") or next(iter(texts.values()), None) or {}


PLACE_CODE = re.compile(r"\d{8}|\d{12}")  # an official municipality key (AGS, 8 digits) or regional key (ARS, 12)


def is_place_list(config, codes):
    """A list of places: ticked in the element ("places"), or most of at least three codes are municipality keys."""
    codes = [str(c) for c in codes]
    if (config or {}).get("places"):
        return True
    return len(codes) >= 3 and sum(1 for c in codes if PLACE_CODE.fullmatch(c)) * 2 > len(codes)


def read_places(item, form):
    """The tick "Ortsliste" of the editor; only where the form has the field (a survey file or an older form keeps it)."""
    if "places_box" in form:
        item.config = {**(item.config or {}), "places": bool(form.get("places"))}


# the read_keys (ItemType.read_shape) in words for the message of app/answer_lock.py
READ_WORDS = {
    "formula": ("die Rechenregel und ihre Quellen", "the calculation and its inputs"),
    "dont_know": ("die Option „weiß nicht“", "the “don’t know” option"),
    "when": ("die Bedingungen der Spalten", "the conditions of the columns"),
    "mode": ("der Modus", "the mode"),
    "max_own": ("die Zahl eigener Gruppen (nur mehr erlaubt)", "the number of own groups (only more allowed)"),
    "rank": ("die Rangfolge in den Gruppen", "the ranking within the groups"),
    "presort": ("die Vorsortierung", "the presort"),
    "capacities": ("das Raster", "the grid"),
    # audit 2026-10-12, N-1 to N-10, N-13, M-2, M-3
    "amount": ("der Betrag", "the amount"),
    "steps": ("die Zahl der Wahlen je Verzögerung", "the number of choices per delay"),
    "delays": ("die Verzögerungen mit ihren Tagen", "the delays with their days"),
    "words": ("die Wörter des Textes", "the words of the text"),
    "cats": ("die Zahl der Kategorien", "the number of categories"),
    "gaps": ("die Zahl der Lücken", "the number of gaps"),
    "slots": ("die Zahl der Felder (nur mehr erlaubt)", "the number of fields (only more allowed)"),
    "box": ("die Rechtecke der Bereiche", "the rectangles of the areas"),
    "total": ("die Gesamtsumme", "the total"),
    "total_from": ("die Herkunft der Gesamtsumme", "where the total comes from"),
    "format": ("das Format (Zahl, Währung, Prozent)", "the format (number, currency, per cent)"),
    "currency": ("die Währung", "the currency"),
    "parts": ("die erfragten Teile (Jahr, Monat, Datum, Uhrzeit)", "the parts asked (year, month, date, time)"),
    "sides": ("der Seitenwechsel der Kategorien", "the switching of the categories’ sides"),
    "store": ("die Speicherstufe des Standorts (nur gröber erlaubt)", "the storage level of the location (only coarser allowed)"),
    "kind": ("die Art der Zusatzfragen", "the kind of the follow-up questions"),
    "points": ("die Stufenzahl der Zusatzfragen", "the number of points of the follow-up questions"),
    "options": ("die Optionen der Zusatzfragen (nur am Ende ergänzen)", "the options of the follow-up questions (only add at the end)"),
    "markers": ("die Höchstzahl der Marker (nur mehr erlaubt)", "the maximum number of markers (only more allowed)"),
    "nms": ("die Kauffragen (NMS; nur Einschalten erlaubt)", "the purchase questions (NMS; only switching on allowed)"),
    "validation": ("die Prüfung der Eingabe", "the check of the entry"),
    "allow_skip": ("„kann ich nicht einschätzen“ (nur Einschalten erlaubt)", "“can’t say” (only switching on allowed)"),
    "text_fallback": ("„Tippen möglich“ (nur Einschalten erlaubt)", "“typing possible” (only switching on allowed)"),
}
# keys whose value may grow: only a smaller value (or switching off) rereads earlier answers ("store": the index of
# the storage level, coarser is larger)
ONLY_FEWER_REFUSED = ("dont_know", "max_own", "slots", "store", "markers", "nms", "allow_skip", "text_fallback")
# keys whose value is a list of rows read by their place: rows added at the end leave earlier answers as they are
ONLY_APPENDED = ("options",)
