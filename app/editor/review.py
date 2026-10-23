"""Text review of an element or the whole survey."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ai, linked, undo
from app.editor.base import _back, _get_item, _lang, _require_draft, bp
from app.extensions import db
from app.items.base import whole
from app.lang import de_en
from app.models import Item
from app.surveys import get_survey


def _text_review_page(survey, result, item=None):
    return render_template("items/text_review.html", survey=survey, item=item, lang=_lang(survey), result=result)


@bp.route("/items/<int:item_id>/text-review", methods=["POST"])
@login_required
def text_review_item(survey_id, item_id):
    """AI review of the wording of one question (app/text_review.py): suggestions to tick."""
    from app import text_review

    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    back = redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=_lang(survey)))
    if not survey.can(current_user, "editor") or not text_review.reviewable(item):
        abort(404)
    if not _require_draft(survey):
        return back
    try:
        return _text_review_page(survey, text_review.review_item(item), item)
    except ai.AIError as e:
        flash(str(e), "error")
        return back


@bp.route("/text-review", methods=["POST"])
@login_required
def text_review_survey(survey_id):
    """AI review of the wording of the whole questionnaire, in one request."""
    from app import text_review

    survey = get_survey(survey_id)
    if not survey.can(current_user, "editor"):
        abort(403)
    if not _require_draft(survey):
        return _back(survey, _lang(survey))
    try:
        return _text_review_page(survey, text_review.review_survey(survey))
    except ai.AIError as e:
        flash(str(e), "error")
        return _back(survey, _lang(survey))


@bp.route("/text-review/apply", methods=["POST"])
@login_required
def text_review_apply(survey_id):
    """Take over the ticked suggestions (one or more questions), as one step (undo)."""
    from app import text_review

    survey = get_survey(survey_id)
    if not survey.can(current_user, "editor"):
        abort(403)
    one = request.form.get("item", type=int)
    back = (redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=one, lang=_lang(survey))) if one
            else _back(survey, _lang(survey)))
    if not _require_draft(survey):
        return back
    changes = {}
    for ref in request.form.getlist("take"):
        item_id, _, pid = ref.partition("|")
        item = db.session.get(Item, whole(item_id)) if whole(item_id) is not None else None
        if item is None or item.block.survey_id != survey.id or not text_review.reviewable(item):
            continue
        if pid in {p for p, _, _ in text_review.parts(item)}:
            changes.setdefault(item, {})[pid] = request.form.get(f"text_{ref}", "")
    if not changes:
        flash(de_en("Kein Vorschlag ausgewählt; nichts geändert.", "No suggestion chosen; nothing changed."), "warning")
        return back
    step = undo.begin(survey)
    for item, parts in changes.items():
        text_review.apply(item, parts)
        linked.save_template(item, current_user)  # an open linked element: the other uses in drafts take the new wording
    n = sum(len(p) for p in changes.values())
    step.done(undo.both("KI-Vorschläge für {n} Texte übernommen", "AI suggestions for {n} texts accepted", n=n),
              item=next(iter(changes)).id if len(changes) == 1 else None)
    db.session.commit()
    flash((de_en("{n} Text übernommen.", "{n} text accepted.", n=n) if n == 1 else
           de_en("{n} Texte übernommen.", "{n} texts accepted.", n=n))
          + de_en(" Vorhandene Übersetzungen dieser Texte zeigt die Seite „Übersetzung“ als geändert.",
                  " The page “Translation” shows existing translations of these texts as changed."), "success")
    return back
