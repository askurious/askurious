"""Shared parts of the questionnaire editor: the blueprint, template helpers, lookups and the palette."""
import re

from flask import Blueprint, abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user

from app import access, autosave, experimental, items, undo
from app.extensions import db
from app.items.base import FormRefused, fill, new_id as base_new_id
from app.lang import de_en
from app.models import Block, Item


bp = Blueprint("editor", __name__, url_prefix="/surveys/<sid:survey_id>/editor")


def _malformed(e):
    """A form field that should hold a number and does not ("²", "inf", far too many digits) where a form did not
    check it itself (base.whole): a message instead of a server error, and nothing of the request is kept."""
    db.session.rollback()
    if isinstance(e, FormRefused):  # an element's form said why (items.base.FormRefused)
        message = de_en("Nicht gespeichert: {why}", "Not saved: {why}", why=str(e))
    else:
        message = de_en("Nicht gespeichert: Eine Zahl im Formular ist ungültig.", "Not saved: a number in the form is invalid.")
    if autosave.wanted():
        return jsonify(status="invalid", message=message)
    if request.method == "POST" and request.referrer:
        flash(message, "error")
        return redirect(request.referrer)
    return message, 400


bp.register_error_handler(ValueError, _malformed)
bp.register_error_handler(OverflowError, _malformed)  # int(float("inf"))


@bp.app_template_global()
def new_id():
    return base_new_id()


@bp.app_template_global()
def edit_history(survey):
    """The steps the arrows in a survey's head offer the current person (editor/_undo.html)."""
    return undo.history(survey, current_user)


@bp.app_context_processor
def _undo_helpers():
    return {"undo_when": undo.when, "undo_shown": undo.SHOWN, "undo_label": undo.label}


@bp.app_template_global()
def consent_confirmations():
    from app.items.consent import CONFIRMATIONS

    return CONFIRMATIONS


@bp.app_template_global()
def consent_labels():
    from app.items.consent import CONFIRMATION_LABELS

    return {key: de_en(de, en) for key, (de, en) in CONFIRMATION_LABELS.items()}


@bp.app_template_global()
def consent_default(key, lang, config):
    from app.items.consent import default_confirmation

    return default_confirmation(key, lang, config)


@bp.app_template_global()
def condition_text(survey, cond):
    from app import flow

    return flow.condition_text(survey, cond or {})


@bp.app_template_global()
def condition_label(survey, rid, picked):
    from app import flow

    node, _ = flow.find(flow.tree(survey), rid)
    if node is None:
        return rid
    by_id = {flow.node_id(c): c for c in node.get("children", [])}
    return f"{node.get('name')} ({node.get('code')}): " + ", ".join(
        flow.child_label(survey, by_id[c]) for c in picked if c in by_id)


def _readable(value):
    """A stored answer part as text: lists joined, mappings as "key: value" (not the Python form)."""
    if isinstance(value, dict):
        return " · ".join(f"{k}: {_readable(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_readable(v) for v in value)
    return "" if value is None else str(value)


@bp.app_template_global()
def preview_answer(item, answer, lang):
    """One answer of a test run as the summary under the end page shows it: codes of fixed answers with their
    wording, a typed text in quotes ("4 (Ich beschreibe mich selbst: „25“)"), everything else readable instead of
    the stored structure (UI audit 2026-10-12, U28)."""
    from app import flow
    from app.items.base import plain

    if answer is None or answer in ("", [], {}):
        return "—"
    labels = {code: plain(label) for code, label in flow.answer_codes(item, lang)}
    if not labels:
        return _readable(answer)
    texts = {}
    if isinstance(answer, dict) and "v" in answer and set(answer) <= {"v", "t", "h"}:
        texts, answer = {str(k): v for k, v in (answer.get("t") or {}).items()}, answer["v"]
    opening, closing = ("„", "“") if (lang or "de") == "de" else ("“", "”")

    def one(code):
        code = str(code)
        label, text = labels.get(code), texts.get(code)
        typed = f"{opening}{text}{closing}" if text not in (None, "") else ""
        inner = f"{label}: {typed}" if label and typed else label or typed
        return f"{code} ({inner})" if inner else code

    codes = answer if isinstance(answer, (list, tuple)) else [answer]
    return ", ".join(one(c) for c in codes) if codes else "—"


@bp.app_template_global()
def rating_styles():
    """The styles of a rating (items/rating.py STYLES) in the language of the page."""
    from app.items.rating import STYLES
    from app.lang import english

    english_names = {"slider": "Slider", "smileys": "Smileys", "stars": "Stars", "numbers": "Row of numbers"}
    return {key: english_names.get(key, label) for key, label in STYLES.items()} if english() else STYLES


@bp.app_template_global()
def kind_validations():
    """The input checks of a text field (items/text.py VALIDATIONS) in the language of the page."""
    from app.items.text import VALIDATIONS
    from app.lang import english

    english_names = {"none": "none", "integer": "whole number", "decimal": "decimal number", "email": "email address",
                     "plz_de": "postcode (DE)", "regex": "own regular expression"}
    return {key: english_names.get(key, label) for key, label in VALIDATIONS.items()} if english() else VALIDATIONS


@bp.app_template_global()
def fmt_num(n):
    """A number for a field of the element editor in the language of the page, as units.read_entry reads it back
    (2,5 in German, 2.5 in English, no exponent; Mathe-Audit 2026-10-06, Z-4)."""
    from app.units import page_lang, shown_plain

    if n is None or n == "":
        return ""
    try:
        return shown_plain(n, page_lang())
    except (TypeError, ValueError, ArithmeticError):
        return str(n)


@bp.app_template_filter("rich")
def rich_filter(text, variables=None):
    from app.items.base import rich

    return rich(text, variables)


# where a long German compound may break in a narrow column (the palette, 280 px): after these parts, if a word of at
# least four letters follows (UI/UX audit 2026-10-09, X25; W40: browsers often leave German unhyphenated)
_SOFT_AFTER = re.compile(r"(ungs|heits|keits|tions|schafts|umwelt|technik|bewusst)(?=\w{4})", re.IGNORECASE)


@bp.app_template_filter("soft_hyphens")
def soft_hyphens(text):
    """`text` with soft hyphens (U+00AD) inside long compounds: Zuordnungs-aufgabe, Belohnungs-aufschub,
    Umwelt-bewusst-sein. Only words of 13 letters and more; the text reads and copies as before."""
    return re.sub(r"\w{13,}", lambda m: _SOFT_AFTER.sub(lambda p: p.group(1) + "\u00ad", m.group(0)), str(text or ""))


@bp.app_template_filter("inline")
def inline_filter(text, variables=None):
    """Question texts: **bold**, *italic*, `code`, ~sub~, ^sup^ and a few tags, placeholders marked as in fill."""
    from app.items.base import inline_rich

    return inline_rich(text, variables, mark_missing=True)


@bp.app_template_global()
def hl_colors():
    """The colours of a highlight category (items/highlight.py COLORS): {key: (name, css)}, names in the language
    of the page."""
    from app.items.highlight import COLORS
    from app.lang import english

    english_names = {"yellow": "Yellow", "green": "Green", "red": "Red", "blue": "Blue", "grey": "Grey", "violet": "Violet"}
    if not english():
        return COLORS
    return {key: (english_names.get(key, name), css) for key, (name, css) in COLORS.items()}


@bp.app_template_global()
def format_hint():
    """The formatting a text field takes (items/base.py FORMAT_HINT), in the language of the page."""
    from app.items.base import FORMAT_HINT

    return de_en(FORMAT_HINT, "**bold**, *italic*, `code`, H~2~O subscript, x^2^ superscript; also <u>, <small>, <sub>, "
                              "<sup>, <br>")


@bp.app_template_filter("fill")
def fill_filter(text, variables=None):
    return fill(text, variables, mark_missing=True)


@bp.app_template_filter("readable_placeholders")
def readable_placeholders(text):
    """A text with its placeholders shown readably where no value can go in (headings of the results, the jump
    list of the preview): „Wie erleben Sie [verkehrsmittel]?“ instead of the raw {{verkehrsmittel}} (UI/UX audit
    2026-10-05, V12)."""
    from app.items.base import PLACEHOLDER

    return PLACEHOLDER.sub(lambda m: f"[{m.group(1)}]", text or "")


@bp.app_template_filter("column_head")
def column_head(label, passes=False):
    """The label of one column in the heads of the results (distributions, group comparisons): placeholders shown
    readably, and in a loop only the pass, „Durchgang 1: dem Auto“, as the stacked bars show it (UI/UX audit
    2026-10-07, W60)."""
    label = label or ""
    if passes and label.endswith("]") and " [" in label:
        return label.rsplit(" [", 1)[1][:-1]
    return readable_placeholders(label)


def _lang(survey):
    lang = request.values.get("lang", survey.default_language)
    return lang if lang in survey.languages else survey.default_language


def _is_consent_block(block):
    """The block that holds the survey's consent and nothing else."""
    consent = block.survey.consent_item
    return consent is not None and len(block.items) == 1 and block.items[0] is consent


def _consent_to_flow_start(survey):
    """A consent block that was just created (new or from the trash) goes to the start of the flow."""
    import copy

    from app import flow

    db.session.flush()
    root = copy.deepcopy(flow.tree(survey))
    root.setdefault("children", []).insert(0, {"t": "block", "block": survey.consent_item.block_id})
    survey.flow = root
    flow.normalize(survey)


def _refuse_consent(survey):
    flash(de_en("Die Einwilligung steht allein in ihrem Block: Der Block kann verschoben werden, nimmt aber keine "
                "weiteren Elemente auf. Entfernen lässt sie sich über den Papierkorb am Einwilligungsblock.",
                "The consent is alone in its block: the block can be moved but takes no further elements. It can "
                "be removed with the bin icon on the consent block."), "error")
    return False


def _consent_admin(survey):
    """Removing, adding and restoring the consent sets its key facts (minimum age, open data, licence, contact,
    researchers), which only admins choose: they need the role "Admin" (security audit 2026-10-06, S10)."""
    if survey.can(current_user, "admin"):
        return True
    flash(de_en("Dafür fehlen die Rechte: Die Einwilligung entfernen, neu anlegen oder wiederherstellen kann nur die Rolle "
                "„Admin“, weil das ihre Eckdaten setzt.",
                "You lack the rights for this: only the role “Admin” can remove, add or restore the consent, because "
                "that sets its key facts."), "error")
    return False


def _admin_step(step):
    """A step only admins may take back or redo (undo.need)."""
    if step is not None:
        step.need = "admin"


def not_draft_message():
    """Why a change of the structure is refused outside a draft (_require_draft; the agent tools, app/agent_tools.py)."""
    return de_en("Die Befragung läuft oder ist geschlossen: Nur Textkorrekturen sind möglich.",
                 "The survey is running or closed: only text corrections are possible.")


def _require_draft(survey):
    if not survey.is_editable:
        flash(not_draft_message(), "error")
        return False
    return True


def _CANNOT_PLACE():
    """The refusal of a drop the flow does not allow (place_block, flow_node)."""
    return de_en("Diese Verschiebung ist nicht möglich: Die Einwilligung bleibt außerhalb von Randomisierung, Bedingung "
                 "und Schleife, und ein Block kann nicht in sich selbst stehen. Die Einwilligung lässt sich nur davor oder "
                 "dahinter setzen.",
                 "This move is not possible: the consent stays outside randomisation, condition and loop, and a block "
                 "cannot be inside itself. The consent can only be put before or after.")


def _CONSENT_BLOCK_ONLY():
    return de_en("Der Einwilligungsblock enthält nur die Einwilligung. Bitte einen anderen Block wählen.",
                 "The consent block holds only the consent. Please choose another block.")


def _get_block(survey, block_id):
    block = db.session.get(Block, block_id)
    if block is None or block.survey_id != survey.id:
        abort(404)
    return block


def _get_item(survey, item_id):
    item = db.session.get(Item, item_id)
    if item is None or item.block.survey_id != survey.id:
        abort(404)
    return item


def _block_name(block, lang=None):
    return block.title(lang) or "Block"


def _ensure_content_block(survey):
    """A draft whose only blocks hold the consent gets an empty content block: something to drop question
    types into. Returns True if one was added."""
    if not survey.is_editable or not all(_is_consent_block(b) for b in survey.blocks):
        return False
    survey.blocks.append(Block(position=len(survey.blocks),
                               titles={l: survey.next_block_title() for l in survey.languages}))
    db.session.flush()
    return True


def _embed():
    """The item editor inside the page of a live vote (?embed=1): it stays embedded after saving."""
    return {"embed": "1"} if request.args.get("embed") == "1" else {}


def _back(survey, lang, anchor=""):
    if survey.interview is not None:  # an interview guide: its own page (app/interview.py), the element opened again
        item = anchor[len("#item-"):] if anchor.startswith("#item-") else ""
        return redirect(url_for("interview.guide", code=survey.code, item=int(item) if item.isdigit() else None) + anchor)
    if survey.live is not None:  # a live vote: its questions are managed on its own page (app/live/)
        return redirect(url_for("live.manage", code=survey.code) + (anchor if anchor.startswith("#item-") else ""))
    return redirect(url_for("editor.index", survey_id=survey.id, lang=lang) + anchor)


def _renumber(rows):
    for i, r in enumerate(rows):
        r.position = i


def _place(item, target, pos=None):
    """Put `item` into block `target` at index `pos` (end if None), renumbering both blocks.

    Both lists are loaded before anything changes: an autoflush in between would treat
    the item as an orphan (cascade delete-orphan) and delete it.
    """
    source = item.block
    target_rows = [i for i in target.items if i.id != item.id]
    if source is not target:
        src_rows = [i for i in source.items if i.id != item.id]
        source.items = src_rows
        _renumber(src_rows)
    pos = len(target_rows) if pos is None else max(0, min(pos, len(target_rows)))
    target_rows.insert(pos, item)
    target.items = target_rows
    _renumber(target_rows)


def _drop_nid(root, block):
    """The flow node a drop next to `block` refers to: a block alone in its loop stands for the loop
    (it moves with it, and nothing joins it by a drop)."""
    from app import flow

    _, parent = flow.find(root, f"k{block.id}")
    alone = parent is not root and parent["t"] == "loop" and len(parent["children"]) == 1
    return parent["id"] if alone else f"k{block.id}"


def _block_beside(survey, neighbour, side, lang):
    """Drop between two blocks: a new, empty block right before/after `neighbour`. Like a block dropped there
    it joins the neighbour's group (randomizer, condition, loop with several blocks), and says so."""
    from sqlalchemy.orm.attributes import flag_modified

    from app import flow

    block = Block(position=len(survey.blocks), titles={lang: survey.next_block_title()})
    survey.blocks.append(block)
    db.session.flush()
    root = flow.normalize(survey)
    flow.place(survey, f"k{block.id}", _drop_nid(root, neighbour), "before" if side == "before" else "after")
    flag_modified(survey, "flow")
    labels = flow.badges(survey).get(block.id)
    if labels:
        flash(de_en("Der neue Block gehört zu: {groups}. Das lässt sich im Reiter „Ablauf“ ändern.",
                    "The new block belongs to: {groups}. You can change this in the tab “Flow”.",
                    groups=" · ".join(labels)), "warning")
    return block


def _unique_code(survey, base):
    used = {i.code for b in survey.blocks for i in b.items}
    n = 1
    while f"{base}{n}" in used:
        n += 1
    return f"{base}{n}"


def type_counts(user):
    """How often each element type occurs in the surveys this researcher can open: their own and those shared
    with their groups, archived ones included. One grouped query; questions in the trash are not counted."""
    rows = db.session.execute(
        db.select(Item.type, db.func.count()).join(Block, Item.block_id == Block.id)
        .filter(Block.survey_id.in_(access.survey_ids(user))).group_by(Item.type))
    return {kind: n for kind, n in rows}


PALETTE_KEY = "palette_counts"


def palette_counts(user):
    """type_counts as at the first look at the palette in this login session (kept in the session, per person):
    the order by usage changes at most once per session, with the next sign-in."""
    from flask import session

    held = session.get(PALETTE_KEY)
    if isinstance(held, dict) and held.get("u") == user.id and isinstance(held.get("c"), dict):
        return held["c"]
    counts = type_counts(user)
    session[PALETTE_KEY] = {"u": user.id, "c": counts}
    return counts


class _Preset:
    """An entry of the palette that is a set-up type, not a type of its own: "Bildauswahl" is a single choice
    whose options show as images (items/choice.py); a tick in the editor makes it a multiple choice's too.
    label and description: (German, English), read in the language of the page."""

    def __init__(self, key, base, config, label, description, live_only=False, short=None):
        self.key, self.base, self.config, self._label, self._description = key, base, config, label, description
        self._short = short  # the palette's few words (UI audit 2026-10-12, U20)
        self.category = "items"
        self.live_only = live_only  # offered in live votes only (live/manage.html), not in the palette

    @property
    def label(self):
        return de_en(*self._label)

    @property
    def description(self):
        return de_en(*self._description)

    @property
    def short(self):
        return de_en(*self._short) if self._short else ""


PRESETS = {"image_choice": _Preset("image_choice", "single_choice", {"images": True}, ("Bildauswahl", "Image choice"),
                                   ("Antworten als Bilder zum Antippen, hochgeladen oder mit KI erzeugt",
                                    "Answers as images to tap, uploaded or generated with AI"),
                                   short=("Antworten als Bilder", "Answers as images")),
           # live votes: longer texts, on the screen as a list of boxes instead of a word cloud (app/live/, as_wall;
           # Philipp, 2026-10-07)
           "free_text": _Preset("free_text", "text_input", {"multiline": True, "max_length": 1000}, ("Freitext", "Free text"),
                                ("Längere Antworten, auf der Leinwand als Liste", "Longer answers, on the screen as a list"),
                                live_only=True)}


@bp.app_template_global()
def item_preset(item):
    """The palette entry an element was made as, when it is a set-up type (PRESETS): a single choice with images is
    shown as "Bildauswahl" with its icon in the editor's list and on its page, not as "Einfachauswahl" (pre-test
    2026-10-20: a click on "Bildauswahl" seemed to land on a single choice). None for every other element."""
    config = item.config or {}
    return next((p for p in PRESETS.values() if not p.live_only and p.base == item.type
                 and all(config.get(k) == v for k, v in p.config.items())), None)


def palette_types(survey, user):
    """The element types for the palette, the most used first (type_counts); equally often and never used
    ones keep the fixed order. A missing consent stays on top: it is offered only while the survey has none. A type
    that `requires` another (the IAT-Ergebnis an IAT) is offered only when the survey has one; the palette marks it
    "neu" until it is used once (editor/index.html, app.js).
    A preset follows its base type; it is not counted (its items are items of the base type). The counts are taken
    once per login session (palette_counts): one element added does not move the entries under the hand (UI/UX
    audit 2026-10-05, V102)."""
    counts = palette_counts(user)
    kinds = []
    for t in items.all_types():
        if getattr(t, "interview_only", False):  # the Leitfrage: interview guides only (app/interview.py)
            continue
        if getattr(t, "experimental", None) and not experimental.enabled(t.experimental):  # app/experimental.py
            continue
        requires = getattr(t, "requires", None)  # a feedback that belongs to an element (IAT-Ergebnis): only with it
        if requires and not any(i.type == requires for b in survey.blocks for i in b.items):
            continue
        if t.key != "consent" or survey.consent_item is None:
            kinds.append(t)
        kinds += [p for p in PRESETS.values() if p.base == t.key and not p.live_only]
    return sorted(kinds, key=lambda t: (t.key != "consent", -counts.get(t.key, 0)))  # stable: ties as before
