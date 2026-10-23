"""AI suggestions in the editor: ideas, scales, more items."""
import copy

from flask import flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import ai, ai_scales, linked, undo
from app.editor.base import (_CONSENT_BLOCK_ONLY, _back, _get_block, _get_item, _is_consent_block, _lang, _renumber,
                             _require_draft, _unique_code, bp)
from app.editor.elements import _position
from app.extensions import db
from app.items.base import new_id as base_new_id
from app.lang import de_en
from app.surveys import get_survey


# --- AI suggestions (on request only) ---------------------------------------------------


@bp.route("/ai/ideas", methods=["POST"])
@login_required
def ai_ideas(survey_id):
    """Construct ideas for the study (JSON for the pre-launch check sidebar)."""
    from flask import jsonify

    survey = get_survey(survey_id)
    if not ai_scales.enough_context(survey):
        return jsonify(error=de_en("Für sinnvolle Anregungen fehlt noch Kontext: bitte Beschreibung und Forschungsfrage in "
                                   "den Einstellungen ergänzen.",
                                   "Useful suggestions still lack context: please add a description and research question "
                                   "in the settings."), settings=url_for("surveys.detail", survey_id=survey.id))
    try:
        return jsonify(ideas=ai_scales.construct_ideas(survey))
    except ai.AIError as e:
        return jsonify(error=str(e))


def _ai_form():
    idea = request.form.get("idea", "").strip()[:500]
    n = request.form.get("n", "3")
    n = min(max(int(n), 2), 15) if n.isascii() and n.isdigit() and len(n) < 12 else 3
    return idea, n, bool(request.form.get("reverse"))


@bp.route("/blocks/<int:block_id>/ai-scale", methods=["POST"])
@login_required
def ai_scale(survey_id, block_id):
    """A Likert matrix with AI-drafted items for a construct idea, marked "KI-generiert"."""
    survey = get_survey(survey_id)
    block = _get_block(survey, block_id)
    lang = _lang(survey)
    if not _require_draft(survey):
        return _back(survey, lang)
    if _is_consent_block(block):
        flash(_CONSENT_BLOCK_ONLY(), "error")
        return _back(survey, lang)
    idea, n, reverse = _ai_form()
    if not idea:
        flash(de_en("Bitte beschreiben Sie kurz, was die Skala messen soll.", "Please describe briefly what the scale should measure."),
              "error")
        return _back(survey, lang)
    try:
        drafted = ai_scales.draft_items(survey, idea, n, reverse)
    except ai.AIError as e:
        flash(de_en("KI nicht verfügbar: {error}", "AI not available: {error}", error=e), "error")
        return _back(survey, lang)
    item = ai_scales.likert_item(survey, idea, drafted, _unique_code(survey, "KI"))
    stmts = item.config["statements"]
    step = undo.begin(survey)
    rows = list(block.items)
    rows.insert(_position(block, request.form.get("position")), item)
    block.items = rows
    _renumber(rows)
    db.session.flush()
    step.done(undo.both("KI-Skala „{code}“ eingefügt", "AI scale “{code}” inserted", code=item.code), item=item.id)
    db.session.commit()
    flash(de_en("KI-Entwurf mit {n} Items angelegt. Bitte jedes Item prüfen, anpassen oder löschen: "
                "nicht validiert, Reliabilität im Pretest prüfen.",
                "AI draft with {n} items created. Please check, adapt or delete each item: not validated, check the "
                "reliability in the pretest.", n=len(stmts)), "success")
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))


@bp.route("/items/<int:item_id>/ai-more", methods=["POST"])
@login_required
def ai_more(survey_id, item_id):
    """More AI-drafted statements for an AI scale (appended, marked like the others)."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    if not _require_draft(survey) or not item.kind.ai_scale or not (item.config or {}).get("ai"):
        return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))
    _, n, reverse = _ai_form()
    idea = item.config["ai"].get("idea", "")
    existing = [s["text"] for s in item.kind.statements(item, survey.default_language) if s["text"]]
    try:
        drafted = ai_scales.draft_items(survey, idea, n, reverse, avoid=existing)
    except ai.AIError as e:
        flash(de_en("KI nicht verfügbar: {error}", "AI not available: {error}", error=e), "error")
        return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))
    step = undo.begin(survey)
    config, texts = copy.deepcopy(item.config), copy.deepcopy(item.texts or {})
    for d in drafted:
        sid = base_new_id()
        config["statements"].append({"id": sid, "code": "", "reverse": d["reverse"]})
        for l in survey.languages:
            texts.setdefault(l, {}).setdefault("statements", {})[sid] = d["text"].get(l, "")
    item.config, item.texts = config, texts
    linked.save_template(item, current_user)  # an open linked element: the other uses in drafts take the change
    step.done(undo.both("KI-Items an „{code}“ angehängt", "AI items appended to “{code}”", code=item.code), page="item",
              item=item.id)
    db.session.commit()
    flash(de_en("{n} weitere KI-Items angehängt. Bitte prüfen.", "{n} more AI items appended. Please check them.",
                n=len(drafted)), "success")
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang) + "#body")
