"""AI suggestions in the editor: ideas, scales, more items, help at the fields of one question."""
import copy

from flask import abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from app import ai, ai_access, ai_fields, ai_scales, experimental, linked, undo
from app.editor.base import (_CONSENT_BLOCK_ONLY, _back, _get_block, _get_item, _is_consent_block, _lang, _renumber,
                             _require_draft, _unique_code, bp, not_draft_message)
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


# --- help at the fields of one question (experimental "ai_fields", app/ai_fields.py) -----------------------------------


def _field_item(survey_id, item_id, part):
    """(survey, item) for a route of the help; 404 without the feature, without "Generative KI nutzen", without the
    role "Bearbeiten" and for a part that is not open (the consent, a locked construct, ...)."""
    experimental.require("ai_fields")
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if not ai_access.ai_on() or not survey.can(current_user, "editor") or not ai_fields.allowed(item, part):
        abort(404)
    return survey, item


def _catalog_way(survey, item, part, hit):
    """How "Aus Katalog einfügen" puts the construct `hit` in, on the ways the editor has already: into this scale
    (take_construct: its statements appended, or the still empty scale becomes the construct), else as a new element
    behind this one (add_item). {"url", "fields", "how"} for the form the popup sends."""
    from app import constructs, item_source

    lang = _lang(survey)
    if part == "statements" and item_source.applies(item) and not constructs.info(item):
        has = any(str(t).strip() for t in (item.lang_texts(survey.default_language).get("statements") or {}).values())
        url = url_for("editor.take_construct", survey_id=survey.id, item_id=item.id, lang=lang)
        if not has:
            return {"url": url, "fields": {"take": f"{hit['key']}|replace"},
                    "how": de_en("Dieses Element wird zum Konstrukt.", "This element becomes the construct.")}
        if hit["type"] == item.type:
            return {"url": url, "fields": {"take": f"{hit['key']}|append"},
                    "how": de_en("Die Items werden an diese Aussagen angehängt.",
                                 "The items are appended to these statements.")}
    position = next((n for n, i in enumerate(item.block.items) if i.id == item.id), len(item.block.items) - 1) + 1
    return {"url": url_for("editor.add_item", survey_id=survey.id, block_id=item.block_id, lang=lang),
            "fields": {"type": f"construct:{hit['key']}", "position": str(position)},
            "how": de_en("Als neues Element hinter diesem.", "As a new element behind this one.")}


@bp.route("/items/<int:item_id>/ai-field/<part>", methods=["POST"])
@login_required
def ai_field(survey_id, item_id, part):
    """Suggestions for one part of a question (JSON for the popup of the element page): "question" three wordings,
    "options" and "statements" rows to tick. A keyword that names a construct of the toolbox answers with the
    construct instead, before any AI call ("own": the researcher asked for own suggestions all the same)."""
    survey, item = _field_item(survey_id, item_id, part)
    if not survey.is_editable:
        return jsonify(error=not_draft_message()), 409
    form = request.form
    idea = ai_fields.clean(form.get("idea"), ai_fields.MAX_IDEA)
    seen = form.getlist("seen")[:ai_fields.MAX_SEEN]
    if idea and part != "options" and not form.get("own"):
        hits = ai_fields.catalog_hits(survey, idea, current_user)
        if hits:
            return jsonify(catalog=[{**hit, **_catalog_way(survey, item, part, hit)} for hit in hits])
    try:
        if part == "question":
            return jsonify(variants=ai_fields.suggest_question(item, idea, seen))
        if part == "options":
            return jsonify(options=ai_fields.suggest_options(item, seen))
        _, n, reverse = _ai_form()
        return jsonify(statements=ai_fields.suggest_statements(item, idea, n, reverse, seen))
    except ai.AIError as e:
        return jsonify(error=str(e))


def _field_rows(raw):
    """The rows the popup sends ("rows": a JSON list of {"text", ...}), as dicts; anything else is left out."""
    import json

    try:
        rows = json.loads(raw or "[]") if len(raw or "") <= 20000 else []
    except ValueError:
        rows = []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("text"), str)][:ai_fields.MAX_TAKE]


@bp.route("/items/<int:item_id>/ai-field/<part>/apply", methods=["POST"])
@login_required
def ai_field_apply(survey_id, item_id, part):
    """Take the chosen suggestions over, as one step (undo): the question text is replaced, options and statements
    are appended (nothing there is replaced). The texts are cleaned and cut here again; the popup is not trusted."""
    from app import answer_lock, carry

    survey, item = _field_item(survey_id, item_id, part)
    if not survey.is_editable:
        return jsonify(error=not_draft_message()), 409
    back = url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=_lang(survey))
    shape_before = answer_lock.shape(item)
    config_before, texts_before = copy.deepcopy(item.config), copy.deepcopy(item.texts)
    step = undo.begin(survey)
    if part == "question":
        text = ai_fields.clean(request.form.get("text"), ai_fields.MAX_QUESTION)
        if not text:
            return jsonify(error=de_en("Keine Formulierung gewählt; nichts geändert.", "No wording chosen; nothing changed."))
        ai_fields.set_question(item, text)
        label = undo.both("Fragetext von „{code}“ eingesetzt (KI-Vorschlag)",
                          "Question text of “{code}” set (AI suggestion)", code=item.code)
        message = de_en("Fragetext eingesetzt. Vorhandene Übersetzungen zeigt die Seite „Übersetzung“ als geändert.",
                        "Question text set. The page “Translation” shows existing translations as changed.")
    else:
        rows = _field_rows(request.form.get("rows"))
        n = ai_fields.append_options(item, rows) if part == "options" else ai_fields.append_statements(item, rows)
        if not n:
            item.config, item.texts = config_before, texts_before
            return jsonify(error=de_en("Kein Vorschlag ausgewählt; nichts geändert.",
                                       "No suggestion chosen; nothing changed."))
        changed = answer_lock.refused(survey, item, shape_before)
        if changed:  # real answers in a survey opened for editing again: what they mean stays
            item.config, item.texts = config_before, texts_before
            return jsonify(error=answer_lock.message(item, changed, outcome=de_en("Nichts wurde angehängt.",
                                                                                  "Nothing was appended.")))
        answer_lock.stamp_added(survey, item, config_before)  # an option added from now: -77 for earlier versions
        if part == "options":
            label = undo.both("{n} Antwortoptionen an „{code}“ angehängt (KI-Vorschlag)",
                              "{n} answer options appended to “{code}” (AI suggestion)", n=n, code=item.code)
            message = (de_en("{n} Antwortoption angehängt.", "{n} answer option appended.", n=n) if n == 1 else
                       de_en("{n} Antwortoptionen angehängt.", "{n} answer options appended.", n=n))
        else:
            label = undo.both("{n} KI-Aussagen an „{code}“ angehängt", "{n} AI statements appended to “{code}”", n=n,
                              code=item.code)
            message = (de_en("{n} Aussage angehängt, markiert als „KI“. Bitte prüfen: nicht validiert.",
                             "{n} statement appended, marked “AI”. Please check it: not validated.", n=n) if n == 1 else
                       de_en("{n} Aussagen angehängt, markiert als „KI“. Bitte prüfen: nicht validiert.",
                             "{n} statements appended, marked “AI”. Please check them: not validated.", n=n))
    linked.save_template(item, current_user)  # an open linked element: the other uses in drafts take the change
    carry.sync_survey(survey)  # questions that show the options of this one take them anew (app/carry.py)
    step.done(label, page="item", item=item.id)
    db.session.commit()
    flash(message, "success")
    return jsonify(done=True, url=back)
