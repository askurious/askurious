"""Duplicating a question or a block within its own survey: the copy stands right under the original, one step of
undo (concept konzept-duplizieren-2026-10-21.md, all as recommended, Ralf 2026-10-21). Copying into another survey
is on the page of the element ("In anderer Befragung verwenden", elements.copy_item_to)."""
import re

from flask import abort, flash
from flask_login import current_user, login_required

from app import duplicate, experimental, undo
from app.editor.base import (_back, _block_beside, _block_name, _get_block, _get_item, _is_consent_block, _lang,
                             _refuse_consent, _renumber, _require_draft, bp)
from app.extensions import db
from app.lang import de_en
from app.surveys import get_survey

NOT_COPIED = ("consent", "page_break")  # the consent exists once; a page break is quicker added than copied


def _run(survey, item):
    """What duplicating `item` copies: the question; in a Themenblock of an interview guide a Leitfrage with its
    Unterfragen and tasks, as they move together (interview.units)."""
    if survey.interview is None:
        return [item]
    from app import interview

    return next((u for u in interview.units(item.block) if u[0] is item), [item])


def _title(survey, title, lang):
    """The name of the copied block: the original's with a number, one not taken yet ("Szenario A (2)")."""
    taken = {(b.titles or {}).get(lang) for b in survey.blocks}
    n = 2
    while f"{title} ({n})" in taken:
        n += 1
    return f"{title} ({n})"


def _loop_hint(survey):
    """Where a block can be repeated per table row instead (app/loops.py): said once after duplicating a block."""
    if survey.interview is not None or survey.live is not None:
        return ""
    return " " + de_en("Sollen dieselben Fragen je Szenario oder Produkt wiederholt werden, geht das auch ohne Kopien: "
                       "„Schleife um diesen Block“ in den Einstellungen des Blocks.",
                       "If the same questions are to be repeated per scenario or product, a loop does it without "
                       "copies: “Loop around this block” in the settings of the block.")


@bp.route("/items/<int:item_id>/duplicate", methods=["POST"])
@login_required
def duplicate_item(survey_id, item_id):
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    if item is survey.consent_item or item.type in NOT_COPIED:
        abort(400)
    if getattr(item.kind, "experimental", None):  # only for whoever has the feature (as copy_item_to)
        experimental.require(item.kind.experimental)
    if not _require_draft(survey):
        return _back(survey, lang)
    run = _run(survey, item)
    step = undo.begin(survey)
    try:
        pairs, note = duplicate.twins(run, current_user)
    except ValueError as e:  # a construct whose terms allow no copy
        flash(str(e), "error")
        return _back(survey, lang, f"#item-{item.id}")
    block = item.block
    rows = list(block.items)
    at = rows.index(run[-1]) + 1
    rows[at:at] = [new for _, new in pairs]
    block.items = rows
    _renumber(rows)
    db.session.flush()
    duplicate.rewire(pairs)
    new = pairs[0][1]
    step.done(undo.both("„{code}“ dupliziert als „{new}“", "“{code}” duplicated as “{new}”", code=item.code, new=new.code),
              item=new.id)
    db.session.commit()
    if len(pairs) > 1:  # a Leitfrage with its Unterfragen and tasks
        flash(de_en("„{code}“ mit {n} zugehörigen Elementen dupliziert; die Kopie steht direkt darunter.",
                    "“{code}” duplicated with {n} elements belonging to it; the copy stands right below.",
                    code=item.code, n=len(pairs) - 1), "undo")
    else:
        flash(de_en("„{code}“ dupliziert als „{new}“; die Kopie steht direkt darunter.",
                    "“{code}” duplicated as “{new}”; the copy stands right below.", code=item.code, new=new.code), "undo")
    if note:
        flash(note, "warning")
    return _back(survey, lang, f"#item-{new.id}")


@bp.route("/blocks/<int:block_id>/duplicate", methods=["POST"])
@login_required
def duplicate_block(survey_id, block_id):
    import copy

    from app import loops

    survey = get_survey(survey_id)
    source = _get_block(survey, block_id)
    lang = _lang(survey)
    if _is_consent_block(source):
        _refuse_consent(survey)
        return _back(survey, lang)
    if survey.interview is not None:
        from app import interview

        if interview.part(source) != "guide":  # the Kurzfragebogen and the Postskript exist once
            abort(400)
    if not _require_draft(survey):
        return _back(survey, lang)
    # questions of an experimental type the person does not have stay behind (as when a survey is duplicated)
    locked = [i.code for i in source.items if duplicate._locked(i, current_user)]
    items = [i for i in source.items if not duplicate._locked(i, current_user) and i is not survey.consent_item]
    step = undo.begin(survey)
    note = ""
    pairs = []
    if items:
        try:
            pairs, note = duplicate.twins(items, current_user)
        except ValueError as e:
            flash(str(e), "error")
            return _back(survey, lang, f"#block-{source.id}")
    name = _block_name(source, lang)
    block = _block_beside(survey, source, "after", lang)  # in the flow right after it, in the same group
    # a name of its own gets a number; a standard name ("Block 2") the next standard name
    block.titles = {l: _title(survey, t, l) for l, t in (source.titles or {}).items()
                    if t and not re.fullmatch(r"Block \d+", t)} or block.titles
    block.show_title, block.randomize_items = source.show_title, source.randomize_items
    block.settings = {k: v for k, v in copy.deepcopy(source.settings or {}).items() if k != "demographics"}
    rows = [new for _, new in pairs]
    block.items = rows
    _renumber(rows)
    db.session.flush()
    duplicate.rewire(pairs)
    step.done(undo.both("Block „{name}“ dupliziert", "Block “{name}” duplicated", name=name), block=block.id)
    db.session.commit()
    where = ""
    if loops.node_of(source) and not loops.node_of(block):  # alone in its loop: the copy stands after the loop
        where = " " + de_en("Die Kopie steht hinter der Schleife, nicht in ihr.", "The copy stands after the loop, not in it.")
    flash(de_en("Block „{name}“ dupliziert ({n} Elemente); die Kopie steht direkt darunter.",
                "Block “{name}” duplicated ({n} elements); the copy stands right below.", name=name, n=len(pairs))
          + where + _loop_hint(survey), "undo")
    if locked:
        flash(de_en("Nicht kopiert, weil die experimentelle Funktion für Ihr Konto nicht eingeschaltet ist: {codes}.",
                    "Not copied, as the experimental feature is not switched on for your account: {codes}.",
                    codes=", ".join(locked)), "warning")
    if note:
        flash(note, "warning")
    return _back(survey, lang, f"#block-{block.id}")
