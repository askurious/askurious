"""Moving, placing and deleting elements, the trash and undo/redo."""
from flask import abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import ai, answer_lock, edit_guard, item_condition, linked, trash, undo
from app.editor.base import (_admin_step, _back, _block_beside, _consent_admin, _consent_to_flow_start,
                             _ensure_content_block, _get_block, _get_item, _hold, _is_consent_block, _lang, _place,
                             _refuse_consent, _renumber, _require_draft, bp)
from app.extensions import db
from app.lang import de_en
from app.models import Item
from app.surveys import LANGUAGE_NAMES, get_survey


def _draft_consent(item, lang, mode):
    import json

    kind = item.kind
    step = undo.begin(item.survey)
    texts = dict(item.texts or {})
    lt = dict(texts.get(lang, {}))
    if mode == "template":
        lt["body"] = kind.template_body(item, lang)
        flash(de_en("Standardtext eingesetzt. Bitte prüfen und anpassen.", "Standard text inserted. Please check and adapt it."),
              "success")
    else:  # AI draft; without AI (no key, error) the standard text is used instead
        language = LANGUAGE_NAMES.get(lang, lang)
        # the AI gets stand-ins for the names, affiliations and the contact address of the researchers; the real
        # values are put into its answer here
        facts, real = kind.masked_facts(item, lang)
        prompt = (
            "Facts (JSON):\n" + json.dumps(facts, ensure_ascii=False, indent=2)
            + "\n\nA plain template version for orientation (improve wording, keep all facts):\n"
            + kind.template_body(item, lang, facts)
        )
        try:
            lt["body"] = kind.unmask(ai.generate_text(ai.CONSENT_INSTRUCTIONS.format(language=language), prompt,
                                                      purpose="consent", item_type="consent"), real)
            flash(de_en("KI-Entwurf eingesetzt. Bitte sorgfältig prüfen: Sie sind für den Text verantwortlich.",
                        "AI draft inserted. Please check it carefully: you are responsible for the text."), "success")
        except ai.AIError as e:
            lt["body"] = kind.template_body(item, lang)
            flash(de_en("KI nicht verfügbar ({error}). Stattdessen wurde der Standardtext eingesetzt; bitte prüfen und anpassen.",
                        "AI not available ({error}). The standard text was inserted instead; please check and adapt it.",
                        error=e), "warning")
    texts[lang] = lt
    item.texts = texts
    step.done(undo.both("Entwurf für den Einwilligungstext eingesetzt", "Draft of the consent text inserted"), page="item",
              item=item.id)
    db.session.commit()


def _moved(item, new_block=False):
    """The step of a moved question or page break; new_block: dropped between two blocks, into a new one."""
    if new_block:
        return (undo.both("Seitenumbruch in einen neuen Block verschoben", "Page break moved into a new block")
                if item.type == "page_break" else
                undo.both("„{code}“ in einen neuen Block verschoben", "“{code}” moved into a new block", code=item.code))
    return (undo.both("Seitenumbruch verschoben", "Page break moved") if item.type == "page_break"
            else undo.both("„{code}“ verschoben", "“{code}” moved", code=item.code))


@bp.route("/items/<int:item_id>/move/<direction>", methods=["POST"])
@login_required
def move_item(survey_id, item_id, direction):
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if item is survey.consent_item:
        _refuse_consent(survey)
    elif _require_draft(survey):
        step = undo.begin(survey)
        blocks = list(survey.blocks)
        siblings = list(item.block.items)
        i = siblings.index(item)
        if direction in ("up", "down"):
            j = i - 1 if direction == "up" else i + 1
            if 0 <= j < len(siblings):
                siblings[i], siblings[j] = siblings[j], siblings[i]
                _renumber(siblings)
        elif direction in ("prev_block", "next_block"):
            b = blocks.index(item.block)
            k = b - 1 if direction == "prev_block" else b + 1
            if 0 <= k < len(blocks) and _is_consent_block(blocks[k]):
                _refuse_consent(survey)
            elif 0 <= k < len(blocks):
                _place(item, blocks[k])
        step.done(_moved(item), item=item.id)
        db.session.commit()
    return _back(survey, _lang(survey), f"#item-{item.id}")


@bp.route("/items/<int:item_id>/place", methods=["POST"])
@login_required
def place_item(survey_id, item_id):
    """Drag and drop: put an item into block `block_id` at `position`; dropped between two blocks
    (`new_block` = before/after): into a new block on that side of `block_id`."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if _require_draft(survey):
        target = _get_block(survey, request.form.get("block_id", type=int))
        side = request.form.get("new_block")
        side = side if side in ("before", "after") else None
        from app import interview

        if item is survey.consent_item or (side is None and _is_consent_block(target)):
            _refuse_consent(survey)
        elif survey.interview is not None and (side or not interview.may_add(survey, target, item.type)):
            # an interview guide: a Leitfrage stays among the Themenblöcke, a question of a form in its part
            flash(de_en("„{kind}“ gehört nicht in „{block}“.", "“{kind}” does not belong in “{block}”.", kind=item.kind.label,
                        block=target.title() or de_en("diesen Block", "this block")), "error")
        else:
            step = undo.begin(survey)
            if side:
                target = _block_beside(survey, target, side, _lang(survey))
            _place(item, target, request.form.get("position", type=int))
            step.done(_moved(item, new_block=bool(side)), item=item.id)
            db.session.commit()
    return _back(survey, _lang(survey), f"#item-{item.id}")


@bp.route("/items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_item(survey_id, item_id):
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if item is survey.consent_item and _require_draft(survey) and _consent_admin(survey):
        step = undo.begin(survey)
        block = item.block
        trash.put(item)  # text and key facts stay restorable, like every other question
        survey.blocks.remove(block)  # the consent always sits alone in its block (delete-orphan removes both)
        from app import flow

        _ensure_content_block(survey)
        flow.normalize(survey)
        _admin_step(step.done(undo.both("Einwilligung entfernt", "Consent removed"), item=item_id))
        db.session.commit()
        flash(de_en("Die Einwilligung liegt im Papierkorb (unten) und lässt sich {days} Tage lang mit Text und "
                    "Eckdaten wiederherstellen. Eine neue, leere Einwilligung gibt es über „Elemente → Einwilligung“.",
                    "The consent is in the bin (below) and can be restored with its text and key facts for {days} days. "
                    "A new, empty consent is under “Elements → Consent”.", days=trash.TRASH_DAYS), "undo")
    elif item is not survey.consent_item and _require_draft(survey):
        answered = answer_lock.fixed(survey, item)
        if answered and request.form.get(answer_lock.CONFIRM_FIELD) != "1":
            # a question with real answers in a survey opened for editing: only after the dialog that says what
            # follows (audit 2026-10-18, FUNC-05); the button of the editor asks it
            flash(de_en("„{code}“ wurde nicht gelöscht. ", "“{code}” was not deleted. ", code=item.code)
                  + str(answer_lock.DELETE_ASK), "warning")
            return _back(survey, _lang(survey))
        dependent = item_condition.referring(survey, item.id)
        step = undo.begin(survey)
        block = item.block
        kept = trash.put(item, answered=answered)
        block.items.remove(item)
        _renumber(block.items)
        step.done(undo.both("„{code}“ gelöscht", "“{code}” deleted", code=item.code) if kept
                  else undo.both("Seitenumbruch entfernt", "Page break removed"), item=item_id, block=block.id)
        db.session.commit()
        # live votes and interview guides have no trash on their page: "Rückgängig" in the message brings it back (R24)
        if not kept:
            flash(de_en("Seitenumbruch entfernt.", "Page break removed."), "undo")
        elif answered:
            flash(de_en("„{code}“ liegt im Papierkorb (unten) und bleibt dort, bis Sie es endgültig löschen; seine "
                        "Antworten fehlen bis zum Wiederherstellen in Datensatz, Ergebnissen und Exporten.",
                        "“{code}” is in the bin (below) and stays there until you delete it for good; its answers are "
                        "missing from the data set, the results and the exports until it is restored.",
                        code=item.code), "undo")
        elif survey.live is None and survey.interview is None:
            flash(de_en("„{code}“ liegt im Papierkorb (unten) und lässt sich {days} Tage lang wiederherstellen.",
                        "“{code}” is in the bin (below) and can be restored for {days} days.", code=item.code,
                        days=trash.TRASH_DAYS), "undo")
        else:
            flash(de_en("„{code}“ ist gelöscht; „Rückgängig“ bringt es zurück.", "“{code}” is deleted; “Undo” brings it back.",
                        code=item.code), "undo")
        if dependent:  # "Nur zeigen, wenn …" of other questions read this one (app/item_condition.py)
            flash(de_en("Die Bedingung „Nur zeigen, wenn …“ von {codes} bezieht sich auf „{code}“ und ist so nicht mehr "
                        "erfüllbar; die Startprüfung nennt sie, bis sie neu gewählt oder entfernt ist.",
                        "The condition “Show only if …” of {codes} refers to “{code}” and can no longer be met; the "
                        "pre-launch check names it until it is chosen again or removed.",
                        codes=", ".join(i.code for i in dependent), code=item.code), "warning")
    return _back(survey, _lang(survey))


@bp.route("/trash/<int:entry_id>/<action>", methods=["POST"])
@login_required
def trash_entry(survey_id, entry_id, action):
    """Put a deleted question back, or delete it for good."""
    survey = get_survey(survey_id)
    entry = next((e for e in survey.trash if e.id == entry_id), None)
    if entry is None or action not in ("restore", "destroy"):
        abort(404)
    if not _require_draft(survey):
        return _back(survey, _lang(survey))
    _hold(survey)  # read anew under the survey's row: names and blocks taken meanwhile are seen
    entry = next((e for e in survey.trash if e.id == entry_id), None) or abort(404)
    if action == "destroy":
        trash.destroy(survey, entry)
        db.session.commit()
        return _back(survey, _lang(survey), "#trash")
    if entry.data.get("type") == "consent":
        if survey.consent_item is not None:
            flash(de_en("Es gibt bereits eine Einwilligung. Zum Wiederherstellen der alten bitte zuerst die jetzige entfernen.",
                        "There already is a consent. To restore the old one, please remove the current one first."), "warning")
            return _back(survey, _lang(survey), "#trash")
        if not _consent_admin(survey):
            return _back(survey, _lang(survey), "#trash")
        step = undo.begin(survey)
        item = trash.restore_consent(survey, entry, current_user)
        _consent_to_flow_start(survey)
        _admin_step(step.done(undo.both("Einwilligung wiederhergestellt", "Consent restored"), item=item.id, restored=True))
        db.session.commit()
        flash(de_en("Einwilligung mit Text und Eckdaten wiederhergestellt, als eigener Block am Anfang.",
                    "Consent restored with its text and key facts, as a block of its own at the start."), "success")
        return _back(survey, _lang(survey), f"#item-{item.id}")
    content = [b for b in survey.blocks if not _is_consent_block(b)]
    if not content:
        abort(400)
    used = edit_guard.taken_codes(survey)  # read under the survey's row
    before = len(survey.blocks)
    step = undo.begin(survey)
    item = trash.restore(survey, entry, used, content[0])
    if linked.is_linked(item):  # an open linked element changed meanwhile: the question takes its state (E8)
        linked.save_template(item, current_user)
    if len(survey.blocks) > before:  # the old block is gone: it came back as a new one, at the end of the flow
        from app import flow

        flow.normalize(survey)
    step.done(undo.both("„{code}“ wiederhergestellt", "“{code}” restored", code=item.code), item=item.id, restored=True)
    db.session.commit()
    flash(de_en("„{code}“ wiederhergestellt.", "“{code}” restored.", code=item.code) + (
        de_en(" Der frühere Block ist gelöscht; die Frage steht in einem neuen Block „{block}“ am Ende. Weitere Fragen aus "
              "diesem Block kommen beim Wiederherstellen ebenfalls dorthin.",
              " The former block is deleted; the question is now in a new block “{block}” at the end. Further questions "
              "from that block go there too when restored.", block=item.block.title(_lang(survey)) or "Block")
        if len(survey.blocks) > before else ""), "success")
    return _back(survey, _lang(survey), f"#item-{item.id}")


# --- undo and redo (app/undo.py) ------------------------------------------------------------


def _to_step(survey, step, lang):
    """The page where the change of `step` shows, at the question, block or flow element if it is still there."""
    from app import flow

    data = step.data
    item = db.session.get(Item, data.get("item") or 0)
    if item is not None and (item.block is None or item.block.survey_id != survey.id):
        item = None
    block = next((b for b in survey.blocks if b.id == data.get("block")), None)
    if data.get("page") in ("flow", "loop"):
        node, _ = flow.find(flow.tree(survey), data.get("node") or "")
        if node is not None and data["page"] == "loop" and node["t"] == "loop":
            return redirect(url_for("flow_page.loop_node", survey_id=survey.id, nid=node["id"], lang=lang))
        return redirect(url_for("flow_page.index", survey_id=survey.id, lang=lang, **({"at": node["id"]} if node else {})))
    if data.get("page") == "settings":
        if data.get("node") == "theme":
            return redirect(url_for("surveys.design", survey_id=survey.id))
        if data.get("node") == "diary":  # times and short surveys of a diary live on "Zeitplan" (UI/UX audit 2026-10-29, WQ-02)
            return redirect(url_for("diary.schedule", survey_id=survey.id))
        if data.get("node") == "panel":  # parameters and completion links live on "Datenerhebung" (Ralf, 2026-10-06)
            return redirect(url_for("surveys.field", survey_id=survey.id) + "#panel")
        if data.get("node") == "value-links":  # own links per person and their maximum (X59)
            return redirect(url_for("surveys.field", survey_id=survey.id) + "#value-links")
        anchor = {"image_style": "#image-style", "panel": "#panel", "end_texts": "#end-texts",
                  "consent-facts": "#consent-facts"}.get(data.get("node"), "")
        return redirect(url_for("surveys.detail", survey_id=survey.id) + anchor)
    if data.get("page") == "quotas":
        there = any(str(q.id) == data.get("node") for q in survey.quotas)
        return redirect(url_for("flow_page.quotas", survey_id=survey.id, lang=lang) + (f"#quota-{data['node']}" if there else ""))
    if data.get("page") == "quality":
        if data.get("node") == "pool":  # the data pool: the box "Grundlage der Auswertung" of this page
            return redirect(url_for("surveys.quality", survey_id=survey.id) + "#pool")
        return redirect(url_for("surveys.quality", survey_id=survey.id))
    if data.get("page") == "boards":
        there = any(str(b.id) == data.get("node") for b in survey.boards)
        return redirect(url_for("boards.designer", survey_id=survey.id, board_id=int(data["node"])) if there
                        else url_for("boards.index", survey_id=survey.id))
    if data.get("page") == "translation":
        return redirect(url_for("translations.index", survey_id=survey.id, lang=data.get("node")))
    if data.get("page") == "item" and item is not None:
        return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))
    if data.get("page") == "interviewers":  # the rights of the interviewer links (app/interviewer.py, rights)
        return redirect(url_for("interview_data.index", code=survey.code) + "#interviewende")
    if data.get("page") == "coding":  # who of them codes in an analysis (app/transcript_coding.py, interviewers)
        return redirect(url_for("transcript_coding.project", code=survey.code, pid=int(data.get("node") or 0),
                                view="settings") + "#mitglieder")
    if data.get("page") == "results":  # the path diagram moved on the results page (surveys.results_sem_layout)
        return redirect(url_for("surveys.results", survey_id=survey.id) + "#sem")
    return _back(survey, lang, f"#item-{item.id}" if item is not None else f"#block-{block.id}" if block is not None else "")


@bp.route("/undo", methods=["POST"], endpoint="undo", defaults={"again": False})
@bp.route("/redo", methods=["POST"], endpoint="redo", defaults={"again": True})
@login_required
def undo_step(survey_id, again):
    """↶ / ↷: take back or redo the person's last step, or all steps up to `to`. Lands where the change shows."""
    survey = get_survey(survey_id)
    lang = _lang(survey)
    # in the field the history holds only what may change there (undo.FIELD_PARTS), so it may be taken back too
    only = request.form.get("only", type=int)
    if only is not None:  # a script takes back its own step without loading the page (static/sem_drag.js)
        from flask import jsonify

        from app.surveys import undo_tops

        tops = undo_tops(survey)
        if tops["redo_id" if again else "undo_id"] != only:
            return jsonify(stale=True, **tops)
    done, problem = (undo.redo if again else undo.undo)(survey, current_user, request.form.get("to", type=int))
    if only is not None:
        from flask import jsonify

        db.session.commit()
        return jsonify(done=bool(done), error=str(problem or ""), label=undo.label(done[-1]) if done else "",
                       **undo_tops(survey))
    if done:  # an open linked element: the element and the other drafts follow (app/linked.py)
        linked.save_survey(survey, current_user, touch=False)
        db.session.commit()
    if done:
        what = de_en("Wiederholt", "Redone") if again else de_en("Zurückgenommen", "Undone")
        flash(de_en("{what}: {step}.", "{what}: {step}.", what=what, step=undo.label(done[0])) if len(done) == 1 else
              de_en("{what}: {n} Schritte, zuletzt „{step}“.", "{what}: {n} steps, last “{step}”.", what=what, n=len(done),
                    step=undo.label(done[-1])), "success")
    for old, new in (pair for step in done for pair in step.renamed):
        flash(de_en("Der Variablenname „{old}“ ist inzwischen an eine andere Frage vergeben: Die Frage heißt jetzt „{new}“.",
                    "The variable name “{old}” went to another question meanwhile: the question is now called “{new}”.",
                    old=old, new=new), "warning")
    if problem:
        flash(problem, "error")
    elif not done:
        flash(de_en("Es gibt nichts mehr zu wiederholen.", "There is nothing more to redo.") if again
              else de_en("Es gibt nichts mehr zurückzunehmen.", "There is nothing more to undo."), "warning")
    if done:
        return _to_step(survey, done[-1], lang)
    return redirect(request.referrer or url_for("editor.index", survey_id=survey.id))
