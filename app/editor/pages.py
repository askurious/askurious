"""The questionnaire page of the editor: pages (blocks) added, changed, moved and deleted."""
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import access, answer_lock, constructs, design, duplicate, item_condition, linked, orgs, shortlist, trash, undo
from app.editor.base import (_CANNOT_PLACE, _back, _block_name, _drop_nid, _ensure_content_block, _get_block,
                             _get_item, _is_consent_block, _lang, _refuse_consent, _require_draft, bp, palette_types)
from app.extensions import db
from app.lang import de_en
from app.models import Block
from app.surveys import LANGUAGE_NAMES, get_survey


def _recommended(survey):
    """{construct key: who recommends it} for the "Schnellbox" of `survey`'s organisation (app/shortlist.py)."""
    found, label = shortlist.for_org(orgs.of(survey))
    return {key: label for key in found}


@bp.route("/")
@login_required
def index(survey_id):
    survey = get_survey(survey_id)
    if survey.live is not None:  # a live vote is managed on its own page (app/live/)
        return redirect(url_for("live.manage", code=survey.code))
    from app import flow, loops

    # the upkeep of opening the page is for who may edit; "Ansehen" changes nothing with a GET (audit 2026-10-18,
    # AUTHZ-06): the flow is put in order for the page only, and the request's work is rolled back after it
    upkeep = survey.can(current_user, "editor")
    if upkeep:
        # the consent is created with a new survey; it may be removed (e.g. chained questionnaires) and re-added
        for b in survey.blocks:  # the consent is never repeated: take it out of a loop placed there by mistake
            node = loops.node_of(b) if _is_consent_block(b) and survey.is_editable else None
            if node:
                flow.place(survey, f"k{b.id}", node["id"], "after")
        _ensure_content_block(survey)
        if survey.is_editable and trash.purge(survey):
            db.session.flush()
    before = survey.flow
    flow.normalize(survey)
    if upkeep and (before != survey.flow or db.session.dirty):
        db.session.commit()
    findings = design.check(survey)
    by_item = {}
    for f in findings:
        if f.item_id and f.severity == "error":  # the "unvollständig" badge: errors only, not hints
            by_item.setdefault(f.item_id, []).append(f)
    started = sum(n for (_, preview, _), n in survey.tally().items() if not preview)
    from app import comments

    # an example survey seen only as one: the team's comments stay with the team (access.showcase_only)
    team_notes = not access.showcase_only(current_user, survey)
    comment_items, comment_blocks = comments.open_counts(survey) if team_notes else ({}, {})
    from app import diary

    # a diary study: the blocks in sections, one per part (entry survey, short surveys, exit survey)
    canvas, part_heads, part_tail = diary.editor_parts(survey) if diary.is_diary(survey) else (survey.blocks, {}, [])
    page = render_template(
        "editor/index.html",
        canvas_blocks=canvas, part_heads=part_heads, part_tail=part_tail, diary=diary,
        take_over=diary.take_over_sources(survey, current_user) if diary.is_diary(survey) and survey.is_editable else [],
        comment_items=comment_items, comment_blocks=comment_blocks, team_notes=team_notes,
        # questions with "Nur zeigen, wenn …" (app/item_condition.py): {item id: the condition in words}
        cond_items={i.id: item_condition.text(i, survey.default_language, shown=True) for b in survey.blocks for i in b.items
                    if item_condition.of(i)},
        survey=survey,
        # "Als eigene Kopie übernehmen" for someone who may only look: only where duplicating is allowed, i.e. with the
        # right to create surveys (app/duplicate.py; UI/UX audit 2026-10-17, W-S01)
        may_copy=not survey.can(current_user, "editor") and duplicate.may(current_user, survey),
        lang=survey.default_language,  # other languages: page "Übersetzung"
        languages=LANGUAGE_NAMES,
        item_types=palette_types(survey, current_user),
        # the "Schnellbox": stars, then the list of the survey's organisation or the platform (app/shortlist.py, K12)
        construct_list=constructs.sidebar(current_user, orgs.of(survey)), construct_starred=constructs.starred(current_user),
        construct_by=_recommended(survey),
        box_blocks=[b for b in survey.blocks if not _is_consent_block(b)],  # "Einfügen in" of the full box
        construct_total=len(constructs.search()), demographic_parts=constructs.ratswd.bundle_parts(),
        linked_list=linked.palette(current_user),
        findings=findings,
        by_item=by_item,
        stats=design.stats(survey),
        flow_tree=flow.tree(survey),
        flow_rows=list(flow.walk(flow.tree(survey))),
        flow_badges=flow.badges(survey),
        # "Schneller blättern, auch bei schwachem Netz" (app/blockload.py): per block whether it is loaded at once, else why not
        blockload_info=__import__("app.blockload", fromlist=["check"]).check(survey) if getattr(survey, "block_load", False) else None,
        loop_info={b.id: loops.config(b) for b in survey.blocks if loops.node_of(b)},
        flow_ops=flow.OPS,
        answer_items=[i for b in survey.blocks for i in b.items if i.kind.collects_answer],
        # questions with real answers in a survey opened for editing: deleting them asks first (FUNC-05)
        answered=answer_lock.answered(survey), answer_lock=answer_lock,
        url_params=[p["name"] for p in __import__("app.panel", fromlist=["config"]).config(survey)["params"]],
        human_count=design.human_count,
        split=next((n for n in flow.tree(survey)["children"] if n["t"] == "split"), None),
        split_after=flow.blocks_after_split(survey),
        participants={"completed": survey.completed_count(), "started": started},
        trash=trash,
        agents_now=__import__("app.edit_guard", fromlist=["agents_here"]).agents_here(survey),  # AI agents (app/agent_tools.py)
    )
    if not upkeep:
        db.session.rollback()  # the flow put in order for the page only
    return page


# --- blocks -------------------------------------------------------------------------


@bp.route("/blocks", methods=["POST"])
@login_required
def add_block(survey_id):
    survey = get_survey(survey_id)
    lang = _lang(survey)
    if _require_draft(survey):
        from app import flow

        step = undo.begin(survey)
        block = Block(position=len(survey.blocks), titles={lang: survey.next_block_title()})
        from app import diary

        part = request.form.get("part", "")
        if diary.is_diary(survey) and part in diary.part_keys(survey):  # "+ Block" in the section of a part
            block.settings = {"part": part}
        survey.blocks.append(block)
        db.session.flush()
        flow.normalize(survey)
        step.done(undo.both("Block „{name}“ angelegt", "Block “{name}” created", name=_block_name(block, lang)), block=block.id)
        db.session.commit()
        return _back(survey, lang, f"#rename-{block.id}")  # the editor selects the name for typing
    return _back(survey, lang)


@bp.route("/blocks/<int:block_id>", methods=["POST"])
@login_required
def update_block(survey_id, block_id):
    survey = get_survey(survey_id)
    block = _get_block(survey, block_id)
    lang = _lang(survey)
    step, old_name = undo.begin(survey), (block.titles or {}).get(lang, "")
    # The page saves the name and each tick at once and says which one in "changed": only what is named is set.
    # The form always carries all of them as the page showed them, and a page opened a while ago would put the
    # others back to what they were then. Without "changed" (no JavaScript: one button for the whole form) all are set.
    only = request.form.getlist("changed")

    def sent(field):
        return not only or field in only

    if sent("title"):
        block.titles = {**(block.titles or {}), lang: request.form.get("title", "").strip()[:300]}
    if survey.is_editable:
        if sent("show_title"):
            block.show_title = bool(request.form.get("show_title"))
        if sent("randomize_items"):
            block.randomize_items = bool(request.form.get("randomize_items"))
        from app import tempo

        if tempo.mode(survey) == "blocks" and sent("tempo_hint"):  # the tick is only on the page then
            block.settings = {**(block.settings or {}), "tempo_hint": bool(request.form.get("tempo_hint"))}
        from app import pagetime

        # how long each page of the block stays at least, and after how long it goes on by itself
        times = pagetime.from_form(request.form, only)
        if times:
            merged = {k: v for k, v in {**(block.settings or {}), **times}.items() if v is not None}
            if pagetime.conflict(merged):
                flash(de_en("Die Höchstzeit muss länger sein als die Mindestzeit. Die Eingabe wurde nicht übernommen.",
                            "The maximum time must be longer than the minimum time. The input was not applied."), "warning")
            else:
                block.settings = merged
        if sent(pagetime.ESSENTIAL):  # "Die Zeit gehört zum Versuch": no "Mehr Zeit" on its pages (D4)
            kept = {k: v for k, v in (block.settings or {}).items() if k != pagetime.ESSENTIAL}
            block.settings = {**kept, **({pagetime.ESSENTIAL: True} if request.form.get(pagetime.ESSENTIAL) else {})}
    step.done(undo.both("Block in „{name}“ umbenannt", "Block renamed to “{name}”", name=_block_name(block, lang))
              if block.titles.get(lang, "") != old_name
              else undo.both("Einstellung am Block „{name}“ geändert", "Setting of block “{name}” changed",
                         name=_block_name(block, lang)), block=block.id)
    db.session.commit()
    return _back(survey, lang, f"#block-{block.id}")


@bp.route("/items/<int:item_id>/level-of-measurement", methods=["POST"])
@login_required
def set_level(survey_id, item_id):
    """The one-click fix of the pre-launch check (UI/UX audit 2026-10-05, V17): an element's measurement level, e.g.
    "ordinal" for ja / eher ja / eher nein / nein; one step for "Rückgängig"."""
    from sqlalchemy.orm.attributes import flag_modified

    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    level = request.form.get("level", "")
    if _require_draft(survey) and level in item.kind.level_choices(item):
        step = undo.begin(survey)
        item.config = {**(item.config or {}), "level": level}
        flag_modified(item, "config")
        step.done(undo.both("Messniveau von {code} auf „{level}“ gesetzt", "Level of measurement of {code} set to “{level}”",
                        code=item.code, level=level), block=item.block_id)
        db.session.commit()
        flash(de_en("Messniveau von {code}: {level}.", "Level of measurement of {code}: {level}.", code=item.code,
                    level=level), "success")
    return _back(survey, _lang(survey), "#panel-check")


@bp.route("/blocks/<int:block_id>/move/<direction>", methods=["POST"])
@login_required
def move_block(survey_id, block_id, direction):
    survey = get_survey(survey_id)
    block = _get_block(survey, block_id)
    if _require_draft(survey):
        from app import flow

        step = undo.begin(survey)
        from app import diary

        # blocks move within their flow group (randomizer/branch); structure is edited in the flow tree
        if diary.is_diary(survey):  # a diary study: past the next block of the same part (diary.move_in_part)
            if not diary.move_in_part(survey, block, direction):
                flash(de_en("Der Block steht schon am Rand seines Teils.", "The block is already at the edge of its part."), "warning")
        elif not flow.move_block_among_siblings(survey, block.id, direction):
            flash(de_en("Der Block steht bereits am Rand seiner Randomisierung, Bedingung oder Schleife. Im Reiter „Ablauf“ kann er dort "
                        "herausgenommen oder in eine andere verschoben werden.",
                        "The block is already at the edge of its randomisation, condition or loop. In the tab “Flow” it "
                        "can be taken out there or moved into another one."), "warning")
        step.done(undo.both("Block „{name}“ verschoben", "Block “{name}” moved", name=_block_name(block)), block=block.id)
        db.session.commit()
    return _back(survey, _lang(survey), f"#block-{block.id}")


@bp.route("/blocks/<int:block_id>/place", methods=["POST"])
@login_required
def place_block(survey_id, block_id):
    """Drag and drop: put a block before/after the block `target` (form: target, mode)."""
    survey = get_survey(survey_id)
    block = _get_block(survey, block_id)
    if _require_draft(survey):
        from sqlalchemy.orm.attributes import flag_modified

        from app import flow

        step = undo.begin(survey)
        target = _get_block(survey, request.form.get("target", type=int))
        mode = "before" if request.form.get("mode") == "before" else "after"
        root = flow.normalize(survey)
        groups = lambda: [g["id"] for g in flow.ancestors(flow.tree(survey), f"k{block.id}")]
        before = groups()
        target_nid = _drop_nid(root, target)
        if _is_consent_block(block):  # the consent is for everyone: next to a group, never into it
            target_nid = ([g["id"] for g in flow.ancestors(root, f"k{target.id}")] or [target_nid])[0]
        if not flow.place(survey, _drop_nid(root, block), target_nid, mode):
            flash(_CANNOT_PLACE(), "warning")
            db.session.rollback()  # nothing of the refused drop is stored
            return _back(survey, _lang(survey), f"#block-{block.id}")
        from app import diary

        if diary.is_diary(survey) and diary.set_part(survey, block, diary.part_of(target)):
            flash(de_en("Der Block gehört jetzt zu: {part}.", "The block now belongs to: {part}.",
                        part=diary.part_label(diary.part_of(target), survey)), "success")
        if groups() != before:  # the editor shows groups only as badges: say what the drop changed
            labels = flow.badges(survey).get(block.id)
            if labels:  # e.g. dropped between the blocks of a randomizer: who sees the block has changed
                flash(de_en("Der Block gehört jetzt zu: {groups}. Das lässt sich im Reiter „Ablauf“ ändern.",
                            "The block now belongs to: {groups}. You can change this in the tab “Flow”.",
                            groups=" · ".join(labels)), "warning")
            else:
                flash(de_en("Der Block steht jetzt in keiner Gruppe mehr (Randomisierung, Bedingung oder Schleife).",
                            "The block is no longer in any group (randomisation, condition or loop)."), "success")
        flag_modified(survey, "flow")
        step.done(undo.both("Block „{name}“ verschoben", "Block “{name}” moved", name=_block_name(block)), block=block.id)
        db.session.commit()
    return _back(survey, _lang(survey), f"#block-{block.id}")


@bp.route("/blocks/<int:block_id>/delete", methods=["POST"])
@login_required
def delete_block(survey_id, block_id):
    survey = get_survey(survey_id)
    block = _get_block(survey, block_id)
    if _is_consent_block(block):
        _refuse_consent(survey)
    elif _require_draft(survey):
        answered = answer_lock.answered(survey)
        if any(i.id in answered for i in block.items) and request.form.get(answer_lock.CONFIRM_FIELD) != "1":
            # questions with real answers: only after the dialog that says what follows (audit 2026-10-18, FUNC-05)
            flash(de_en("„{name}“ wurde nicht gelöscht. ", "“{name}” was not deleted. ", name=_block_name(block, _lang(survey)))
                  + str(answer_lock.DELETE_BLOCK_ASK), "warning")
            return _back(survey, _lang(survey))
        step, block_id = undo.begin(survey), block.id
        # the questions stay restorable for a week, those with real answers until deleted for good
        kept = [e for e in (trash.put(item, answered=item.id in answered) for item in block.items) if e]
        name = _block_name(block, _lang(survey))
        survey.blocks.remove(block)
        from app import flow

        _ensure_content_block(survey)  # part of this step: taking it back removes the stand-in again
        flow.normalize(survey)
        step.done(undo.both("Block „{name}“ gelöscht", "Block “{name}” deleted", name=name), block=block_id)
        db.session.commit()
        # no question before deleting: "Rückgängig" brings the block back whole, at its place
        in_trash = ""
        if kept:
            in_trash = " " + (de_en("{n} Frage liegt zusätzlich {days} Tage im Papierkorb (unten).",
                                    "{n} question also stays in the bin (below) for {days} days.",
                                    n=len(kept), days=trash.TRASH_DAYS) if len(kept) == 1 else
                              de_en("{n} Fragen liegen zusätzlich {days} Tage im Papierkorb (unten).",
                                    "{n} questions also stay in the bin (below) for {days} days.",
                                    n=len(kept), days=trash.TRASH_DAYS))
        flash(de_en("„{name}“ gelöscht.", "“{name}” deleted.", name=name) + in_trash, "undo")
    return _back(survey, _lang(survey))
