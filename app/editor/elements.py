"""Adding and editing one element (item), linked elements, design lock, conjoint design, copying."""
import copy

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import answer_lock, constructs, edit_guard, experimental, heavy, item_condition, item_texts, items, linked, undo
from app.editor.arrange import _draft_consent
from app.editor.base import (PRESETS, _CONSENT_BLOCK_ONLY, _admin_step, _back, _block_beside, _consent_admin,
                             _consent_to_flow_start, _embed, _get_block, _get_item, _is_consent_block, _lang,
                             _renumber, _require_draft, _unique_code, bp)
from app.extensions import db
from app.items.base import CODE_RE, LEVEL_LABELS, TEXT_MAX, too_long_text
from app.lang import de_en
from app.models import Item, utcnow
from app.sav import RESERVED as SPSS_RESERVED
from app.surveys import LANGUAGE_NAMES, get_survey
from app.timeutil import to_local


# --- items --------------------------------------------------------------------------


def _position(block, value):
    try:
        pos = int(value)
    except (TypeError, ValueError):
        return len(block.items)
    return max(0, min(pos, len(block.items)))


@bp.route("/blocks/<int:block_id>/items", methods=["POST"])
@login_required
def add_item(survey_id, block_id):
    """Add an element from the palette (click: end of block; drop: at `position`; drop between two blocks:
    `new_block` = before/after, into a new block on that side of the block)."""
    survey = get_survey(survey_id)
    block = _get_block(survey, block_id)
    lang = _lang(survey)
    if not _require_draft(survey):
        return _back(survey, lang)
    kind_key = request.form.get("type", "")
    preset = PRESETS.get(kind_key)
    if preset is not None and preset.live_only and survey.live is None:
        abort(400)
    if preset is not None:
        kind_key = preset.base
    construct = kind_key[len("construct:"):] if kind_key.startswith("construct:") else None
    # a construct of the toolbox, or a linked element this person may use (app/linked.py)
    source = (linked.get(construct) if constructs.is_linked_key(construct) else constructs.get(construct)) if construct else None
    if construct is not None and (source is None or (constructs.is_linked_key(construct) and not linked.usable(current_user, source))):
        abort(400)
    if construct and not constructs.permitted(construct, survey, current_user):
        flash(constructs.permission_notice(), "error")
        return _back(survey, lang)
    if construct is None and kind_key not in items.REGISTRY:
        abort(400)
    if construct is None and getattr(items.get(kind_key), "experimental", None):
        experimental.require(items.get(kind_key).experimental)  # an element type of an experimental feature
    from app import interview

    if not interview.may_add(survey, block, construct and "construct" or kind_key):  # what a guide's part offers
        abort(400)
    if kind_key == "consent":
        from app.items.consent import ensure_consent_block

        if survey.consent_item is not None:
            flash(de_en("Es gibt bereits eine Einwilligung.", "There already is a consent."), "warning")
            return _back(survey, lang)
        if not _consent_admin(survey):
            return _back(survey, lang)
        step = undo.begin(survey)
        ensure_consent_block(survey, current_user)
        _consent_to_flow_start(survey)
        _admin_step(step.done(undo.both("Einwilligung hinzugefügt", "Consent added"), item=survey.consent_item.id))
        db.session.commit()
        flash(de_en("Einwilligung als eigener Block am Anfang eingefügt; der Block lässt sich verschieben.",
                    "Consent inserted as a block of its own at the start; the block can be moved."), "success")
        return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=survey.consent_item.id, lang=lang))
    step = undo.begin(survey)  # a new block for the element (drop between two blocks) is part of the same step
    if request.form.get("new_block") in ("before", "after"):
        block = _block_beside(survey, block, request.form["new_block"], lang)
    elif _is_consent_block(block):
        flash(_CONSENT_BLOCK_ONLY(), "error")
        return _back(survey, lang)
    if construct:
        kind_key, base, config, texts = constructs.build(construct, survey.languages, source, survey=survey)
        used = {i.code for b in survey.blocks for i in b.items}
        item = Item(type=kind_key, answer_mode="nudge", config=config, texts=texts,
                    code=base if base not in used else _unique_code(survey, base))
        if item.code != base:  # second copy: keep the variable names of the rows unique
            from app import duplicate

            duplicate.number_rows(kind_key, config, item.code)
    else:
        kind = items.get(kind_key)
        item = Item(
            type=kind_key,
            answer_mode="required" if kind_key == "consent" else "optional",
            code=_unique_code(survey, {"page_break": "_pb", "consent": "consent"}.get(kind_key, "Q")),
            config={**kind.default_config(), **(preset.config if preset else {})},
            # nothing for the standard wordings: they come in the language of the page when shown (item_texts, S9)
            texts=item_texts.new(kind, survey.languages, survey.default_language),
        )
    rows = list(block.items)
    rows.insert(_position(block, request.form.get("position")), item)
    block.items = rows
    _renumber(rows)
    db.session.flush()
    step.done(undo.both("Seitenumbruch eingefügt", "Page break inserted") if kind_key == "page_break"
              else undo.both("„{code}“ hinzugefügt ({kind})", "“{code}” added ({kind})", code=item.code, kind=undo.named(item.kind)),
              item=item.id)
    db.session.commit()
    block_name = (block.titles or {}).get(lang) or de_en("Block {n}", "Block {n}", n=block.position + survey.block_start)
    note = ""
    if construct and not constructs.is_linked_key(construct) and constructs.non_commercial(source):
        note = de_en("Die Bedingungen von „{name}“ erlauben nur nichtkommerzielle Forschung und Lehre, auch keine "
                     "Auftragsforschung für Unternehmen; für anderes holen Sie das Einverständnis der Rechteinhaber ein.",
                     "The terms of “{name}” allow only non-commercial research and teaching, no commissioned research "
                     "for companies either; for anything else ask the rights holders for permission.",
                     name=constructs.shown_text(source, "name"))
    if request.form.get("box"):  # the full construct box (konzept-konstrukt-schnellbox, K5): stays open, undo at the card
        from flask import jsonify

        from app.surveys import undo_tops

        return jsonify(done=True, code=item.code, block=block_name, note=note, **undo_tops(survey))
    if note:
        flash(note, "info")
    flash(de_en("„{code}“ in „{block}“ hinzugefügt.", "“{code}” added to “{block}”.", code=item.code, block=block_name), "undo")
    if kind_key == "page_break":
        return _back(survey, lang, f"#item-{item.id}")
    if survey.live is not None:  # a live vote: the new question opens beside the list (app/live/)
        if preset is not None and preset.key == "free_text":
            from app import live

            live.as_wall(survey, item.id)
            db.session.commit()
        return redirect(url_for("live.manage", code=survey.code, item=item.id, neu=1))
    if survey.interview is not None:  # an interview guide: the same (app/interview.py)
        return redirect(url_for("interview.guide", code=survey.code, item=item.id, neu=1))
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))


@bp.route("/demographics", methods=["POST"])
@login_required
def add_demographics(survey_id):
    """The bundle "Demografie kurz (RatSWD)" from the palette: a block of its own at the end of the survey with the
    ticked parts (app/ratswd.py), each question built from its construct, the catalogue's filters as conditions."""
    from app import flow, ratswd
    from app.models import Block
    from app.wizard import DEMOGRAPHICS

    survey = get_survey(survey_id)
    lang = _lang(survey)
    if not _require_draft(survey):
        return _back(survey, lang)
    from app import diary

    if survey.live is not None or survey.interview is not None or diary.is_diary(survey):
        abort(400)  # pages of their own there; the palette does not offer the bundle
    parts = [p for p in request.form.getlist("part") if p in ratswd.PARTS]
    if not parts:
        flash(de_en("Keine Frage angekreuzt: Es wurde nichts eingefügt.", "No question ticked: nothing was inserted."), "error")
        return _back(survey, lang)
    step = undo.begin(survey)
    first = not any((b.settings or {}).get("demographics") for b in survey.blocks)
    block = Block(position=len(survey.blocks), settings={"demographics": True} if first else {},
                  titles={l: DEMOGRAPHICS.get(l, DEMOGRAPHICS["en"]) for l in survey.languages})
    survey.blocks.append(block)
    db.session.flush()
    made = ratswd.add_bundle(survey, parts, block)
    flow.normalize(survey)
    step.done(undo.both("„Demografie kurz (RatSWD)“ eingefügt", "“Short demographics (RatSWD)” inserted"), block=block.id)
    db.session.commit()
    flash(de_en("„Demografie kurz (RatSWD)“ als eigener Block am Ende eingefügt ({n} Fragen); er lässt sich verschieben.",
                "“Short demographics (RatSWD)” inserted as a block of its own at the end ({n} questions); it can be moved.",
                n=len(made)), "undo")
    return _back(survey, lang, f"#block-{block.id}")


@bp.route("/items/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit_item(survey_id, item_id):
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if getattr(item.kind, "experimental", None):  # feature off: the item stays, its editor does not open
        experimental.require(item.kind.experimental, survey=survey)
    lang = _lang(survey)
    # Structure (codes, options, points) only changes in draft and in the default language;
    # other languages are pure translation views. Validated constructs keep their structure
    # (only wording fixes) unless they are explicitly unlocked.
    can_edit = survey.can(current_user, "editor")
    meta_editable = survey.is_editable and lang == survey.default_language and can_edit
    # choice designs with real data keep their structure (stored answers refer to positions)
    structural = (meta_editable and not constructs.locked(item)
                  and not item.kind.design_locked(item))
    if request.method == "POST":
        too_long = _question_too_long(request.form)
        if too_long:
            if request.form.get("autosave") == "1":
                return jsonify(status="invalid", message=too_long)
            flash(too_long, "error")
            return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang, **_embed()))
        key = f"item:{item.id}"
        state_before = edit_guard.item_state(item)
        conflict = edit_guard.check(survey, key, state_before, request.form)
        # The page saves by itself in a draft (app.js, "Element editor: saving by itself"): the same form, sent
        # after every change, answered with JSON instead of a new page. What a message would say goes into `notes`.
        # A page opened in draft may still send by itself after the start: it gets JSON too (only the text
        # corrections are kept, as for "Speichern"), and a note to load the page anew.
        auto = request.form.get("autosave") == "1"
        notes = []
        say = (lambda text, kind="success": notes.append(text)) if auto else flash
        if auto and not survey.is_editable and not request.form.get("opened_running"):
            say(de_en("Die Befragung ist inzwischen gestartet: Gespeichert werden nur noch Textkorrekturen. "
                      "Bitte die Seite neu laden.",
                      "The survey has started in the meantime: only text corrections are saved now. Please reload the page."),
                "warning")
        step = undo.begin(survey)
        if meta_editable:
            code = request.form.get("code", "").strip()
            taken = {i.code for b in survey.blocks for i in b.items if i.id != item.id}
            code_problem = None
            if not CODE_RE.match(code):
                code_problem = de_en("Variablenname: Buchstabe am Anfang, dann Buchstaben, Ziffern oder _.",
                                     "Variable name: a letter first, then letters, digits or _.")
            elif code != item.code and code.upper() in SPSS_RESERVED:  # a name kept from before stays (sav.spss_names)
                code_problem = de_en("„{code}“ ist in SPSS ein Schlüsselwort (wie TO, BY, AND, NOT) und kein gültiger "
                                     "Variablenname. Bitte einen anderen Namen wählen.",
                                     "“{code}” is a keyword in SPSS (like TO, BY, AND, NOT) and no valid variable name. "
                                     "Please choose another name.", code=code)
            elif code in taken:
                code_problem = de_en("Der Variablenname „{code}“ ist schon vergeben.",
                                     "The variable name “{code}” is already taken.", code=code)
            elif code != item.code and answer_lock.fixed(survey, item):
                code_problem = de_en("Diese Frage hat schon echte Antworten; ihr Variablenname bleibt, damit sie im Datensatz "
                                     "zusammenpassen. Zum Umbauen die Befragung duplizieren.",
                                     "This question already has real answers; its variable name stays so that they match "
                                     "in the data set. To rebuild it, duplicate the survey.")
            elif code != item.code:
                from app import piping

                parts = [n for n, _ in item.kind.pipe_parts(item, lang)]
                old, item.code = item.code, code
                from app import study_plan

                study_plan.rename_code(survey, old, code)  # a hypothesis keeps its element
                # texts that show this answer (or one of its values) keep showing it
                followed = sorted(set(piping.renamed(survey, old, code))
                                  | set(piping.renamed(survey, parts, [n for n, _ in item.kind.pipe_parts(item, lang)])))
                if followed:
                    say(de_en("Die Texte von {codes} zeigen die Antwort weiter: Der Baustein folgt dem neuen "
                              "Variablennamen „{code}“.",
                              "The texts of {codes} still show the answer: the piece follows the new variable name “{code}”.",
                              codes=", ".join(followed), code=code), "success")
            mode = request.form.get("answer_mode", "optional")
            item.answer_mode = mode if mode in Item.ANSWER_MODES else "optional"
            if item.type == "consent":
                item.answer_mode = "required"
        before = item.kind.answer_structure(item) if item.kind.design_lock and structural else None
        texts_before = dict((item.texts or {}).get(lang) or {})
        # a frozen linked element keeps its wording in the languages it has (app/linked.py)
        kept_texts = copy.deepcopy(item.texts) if linked.text_locked(item, lang) else None
        shape_before, config_before = answer_lock.shape(item), copy.deepcopy(item.config)
        all_texts_before = copy.deepcopy(item.texts)
        item.kind.update_from_form(item, request.form, lang, structural)
        _read_lock(item, request.form, structural)
        if kept_texts is not None:
            item.texts = kept_texts
        from app import carry, units

        for note in units.entry_notes():  # a number typed that cannot be read for sure ("1,000" in German)
            say(note, "warning")

        if structural:  # a question carried from now: its rows come before the check (another source, none)
            carry.sync(item, check=False)
        changed_parts = answer_lock.refused(survey, item, shape_before) if structural else []
        if structural and not changed_parts:  # an option or area added from now: -77 for earlier versions (N-5)
            answer_lock.stamp_added(survey, item, config_before)
        if changed_parts:
            # real answers in a survey opened for editing again: what they mean stays (codes, points, reversal). The
            # form counts as text corrections only, as in a running survey (scale labels resized to other points go too)
            item.config, item.texts = config_before, all_texts_before
            item.kind.update_from_form(item, request.form, lang, False)
            if kept_texts is not None:
                item.texts = kept_texts
            say(answer_lock.message(item, changed_parts), "error")
        elif structural and item.kind.computed and item.config != config_before:
            changed_values = _recomputed(survey, item, config_before)
            if changed_values:
                say((de_en("Der Rechenwert „{code}“ ergibt mit der neuen Formel für {n} echte Teilnahme einen anderen Wert "
                           "als bisher;", "With the new formula the computed value “{code}” gives a different value than "
                           "before for {n} real participation;", code=item.code, n=changed_values)
                     if changed_values == 1 else
                     de_en("Der Rechenwert „{code}“ ergibt mit der neuen Formel für {n} echte Teilnahmen einen anderen Wert "
                           "als bisher;", "With the new formula the computed value “{code}” gives a different value than "
                           "before for {n} real participations;", code=item.code, n=changed_values))
                    + de_en(" Datensatz und Ergebnisse zeigen auch für sie den neuen Wert.",
                            " data set and results show the new value for them too."), "warning")
        carry.sync_survey(survey)  # questions that show the options of this one take them anew (app/carry.py)
        if lang != survey.default_language:  # a translation typed here counts as checked (Übersetzung page)
            from app import translations

            translations.stamp_changes(survey, f"item:{item.id}", lang, texts_before, (item.texts or {}).get(lang) or {})
        if before is not None and item.kind.answer_structure(item) != before and item.kind.forget_trial_answers(item):
            say(_trial_answers_gone(), "warning")
        if meta_editable and item.kind.supports_dropdown:
            item.kind.set_display(item, request.form)  # presentation only: also on locked constructs
        if meta_editable:  # options a construct allows to leave out (self-description of gender): also when locked
            shape_before, kept = answer_lock.shape(item), (item.config, item.texts)
            item.kind.set_optional_options(item, request.form)
            dropped = answer_lock.refused(survey, item, shape_before)  # one taken out with real answers stays
            if dropped:
                item.config, item.texts = kept
                if not changed_parts:
                    say(answer_lock.message(item, dropped), "error")
        if meta_editable and item.kind.supports_auto_next:
            item.config = {**item.config, "auto_next": bool(request.form.get("auto_next"))}
        # "Nur zeigen, wenn …" (app/item_condition.py): logic, so only in a draft and in the source language; also on
        # locked constructs, whose items stay as they are. Kept as it was where the type built its config anew
        item_condition.from_form(item, request.form, (config_before or {}).get(item_condition.KEY), meta_editable, lang)
        level = request.form.get("level")
        if can_edit and lang == survey.default_language and level in item.kind.level_choices(item):
            # an analysis choice, not part of the instrument: stays switchable while running and on
            # locked constructs; the collected data do not change (results page, SPSS export)
            item.config = {**item.config, "level": level}
        if meta_editable and not structural and item.type == "likert":
            # random order stays switchable on locked constructs (the order is exported per response)
            item.config = {**item.config, "randomize": bool(request.form.get("randomize"))}
        item_texts.tidy(item)  # a draft keeps only what was changed, not copies of standard wordings (S9)
        long_text = too_long_text(item.texts, all_texts_before)
        if long_text:  # every text of the element, not only the question (audit 2026-10-10, T1): nothing is saved
            db.session.rollback()
            message = _text_too_long(long_text)
            if auto:
                return jsonify(status="invalid", message=message)
            flash(message, "error")
            return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang, **_embed()))
        if conflict and auto:  # someone saved in between: nothing is saved, the page offers their version or this one
            db.session.rollback()
            return jsonify(status="conflict", who=conflict["who"], when=conflict["when"], base=conflict["base"])
        if conflict:  # someone saved in between: show the inputs again, keep their version
            return edit_guard.show_again(lambda: _item_page_html(survey, item, lang, structural, meta_editable))
        if survey.is_editable and can_edit:  # an open linked element: the other uses in drafts take the change
            spread = linked.save_template(item, current_user)
            if spread:
                say(de_en("Das verknüpfte Element ist noch offen: Eine weitere Verwendung hat die Änderung übernommen.",
                          "The linked element is still open: the change now applies to one more use.") if spread == 1 else
                    de_en("Das verknüpfte Element ist noch offen: {n} weitere Verwendungen haben die Änderung übernommen.",
                          "The linked element is still open: the change now applies to {n} more uses.", n=spread),
                    "success")
        changed = edit_guard.item_state(item) != state_before
        if changed or not auto:
            edit_guard.saved(survey, key)
        # saving by itself: one step per click and per visit to a text field (`merge` joins what was typed in one
        # visit), named after the field (`what`, its label on the page)
        what = " ".join(request.form.get("what", "").split())[:50] if auto else ""
        merge = f"item:{item.id}:{request.form['merge'][:60]}" if auto and request.form.get("merge") else None
        # a button that names its step in full ("Spalte 2 entfernt", app.js data-undo-what) is taken as it is
        if what and request.form.get("what_done"):
            label = undo.both("„{code}“: {what}", "“{code}”: {what}", code=item.code, what=what)
        elif what:
            label = undo.both("„{code}“: {what} geändert", "“{code}”: {what} changed", code=item.code, what=what)
        else:
            label = undo.both("„{code}“ bearbeitet", "“{code}” edited", code=item.code)
        step.done(label, page="item", item=item.id, merge=merge)
        db.session.commit()
        if auto:
            steps = undo.history(survey, current_user)
            return jsonify(
                status="saved", base=edit_guard.item_state(item), at=to_local(utcnow()).strftime("%H:%M"), code=item.code,
                code_problem=(code_problem if meta_editable else None), problems=item.kind.validate(item) + item_condition.problems(item), notes=notes,
                # panels below the form (design, images) were drawn from the state before: the page loads them anew
                stale=bool(changed and (item.kind.panel_template or item.kind.editor_tabs)),
                undo=undo.label(steps["undo"][0]) if steps["undo"] else "", redo=undo.label(steps["redo"][0]) if steps["redo"] else "")
        if item.type == "consent" and request.form.get("draft") in ("template", "ai"):
            _draft_consent(item, lang, request.form["draft"])
            return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang) + "#body")
        if meta_editable and code_problem:
            # one message that says both: what was refused and what happened to the rest
            flash(code_problem + de_en(" Der bisherige Name „{code}“ bleibt; alles andere wurde gespeichert.",
                                       " The previous name “{code}” stays; everything else was saved.", code=item.code),
                  "error")
            return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang, **_embed()))
        flash(de_en("Gespeichert.", "Saved."), "success")
        if request.form.get("after") == "back":
            return _back(survey, lang, f"#item-{item.id}")
        return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang, **_embed()))
    return _item_page_html(survey, item, lang, structural, meta_editable)


QUESTION_MAX = TEXT_MAX  # characters of a question text: generous, but keeps every page that shows it quick (O2)


def _question_too_long(form):
    """The message when the question text sent is longer than QUESTION_MAX, else None; nothing is saved then."""
    n = len(form.get("question", ""))
    if n <= QUESTION_MAX:
        return None
    return de_en("Nicht gespeichert: Der Fragetext ist mit {n} Zeichen zu lang (höchstens {max}). Längere Texte bitte "
                 "kürzen oder auf mehrere Elemente aufteilen.",
                 "Not saved: the question text has {n} characters, which is too long (at most {max}). Please shorten "
                 "longer texts or split them over several elements.", n=n, max=QUESTION_MAX)


def _text_too_long(n):
    return de_en("Nicht gespeichert: Ein Text des Elements ist mit {n} Zeichen zu lang (höchstens {max}). Längere Texte "
                 "bitte kürzen oder auf mehrere Elemente aufteilen.",
                 "Not saved: a text of the element has {n} characters, which is too long (at most {max}). Please "
                 "shorten longer texts or split them over several elements.", n=n, max=TEXT_MAX)


def _pipe_sources(survey, item, lang):
    """[(name, what it is)] of the answers to earlier questions that this item's texts can show (app/piping.py)."""
    from app import piping

    found = piping.sources(survey)
    return ([(name, piping.label(found[name], name, lang)[:120]) for name in piping.earlier(survey, item)]
            # the same page: goes into the question text while the person answers (piping.live)
            + [(name, (piping.label(found[name], name, lang) + de_en(" (gleiche Seite)", " (same page)"))[:120])
               for name in piping.same_page(survey, item)
               if name in found])


def _rich_vars(survey, loop_vars, pipe_sources):
    """What a text of the element can show in place of a placeholder, for the formatting field (richtext.js:
    words on the placeholders, the list behind "Antwort einfügen"): the answers to earlier questions, the columns
    of the loop, and what the participation link brought."""
    from app import flow, panel

    groups = [
        {"title": de_en("Antworten früherer Fragen", "Answers to earlier questions"), "kind": "answer",
         "items": [{"name": code, "text": question or code} for code, question in pipe_sources]},
        {"title": de_en("Werte der Schleife", "Values of the loop"), "kind": "loop",
         "items": [{"name": v, "text": de_en("Schleife: {v}", "Loop: {v}", v=v)} for v in loop_vars]},
        {"title": de_en("Aus dem Teilnahmelink", "From the participation link"), "kind": "param",
         "items": [{"name": p["name"], "text": de_en("Link-Parameter: {p}", "Link parameter: {p}", p=p["name"])}
                   for p in panel.config(survey)["params"]]
         + [{"name": "pid", "text": de_en("Teilnahme-ID", "Participation ID")}]},
        # what every survey knows of the participation: its language, the survey, what a randomizer drew
        {"title": de_en("Befragung und Ablauf", "Survey and flow"), "kind": "param",
         "items": [{"name": "lang", "text": de_en("Sprache der Teilnahme", "Language of the participation")},
                   {"name": "survey", "text": de_en("Code der Befragung", "Code of the survey")}]
         + [{"name": code, "text": de_en("Randomisierung: {name} (gezogener Zweig)", "Randomisation: {name} (branch drawn)",
                                         name=name)} for code, name in flow.branch_names(survey).items()]
         + [{"name": code, "text": de_en("Web Service: {name}", "Web Service: {name}", name=name)}
            for code, name in __import__("app.webservice", fromlist=["names"]).names(survey).items()]},
    ]
    return [g for g in groups if g["items"]]


def _item_page_html(survey, item, lang, structural, meta_editable):
    problems = item.kind.validate(item) + item_condition.problems(item)
    from app import loops

    loop_vars = [c["name"] for c in loops.config(item.block)["columns"]] if loops.active(item.block) else []
    pipe_sources = _pipe_sources(survey, item, lang)
    return render_template(
        "editor/item.html", survey=survey, item=item, kind=item.kind, lang=lang,
        loop_vars=loop_vars, pipe_sources=pipe_sources, rich_vars=_rich_vars(survey, loop_vars, pipe_sources),
        text_reviewable=__import__("app.text_review", fromlist=["reviewable"]).reviewable(item),
        languages=LANGUAGE_NAMES,
        # only for who may use it: copying needs "Bearbeiten" in this survey too
        copy_targets=__import__("app.duplicate", fromlist=["item_targets"]).item_targets(current_user, item)
        if item.type not in ("consent", "page_break") and survey.can(current_user, "editor") else [], problems=problems, structural=structural, meta_editable=meta_editable,
        **_lock_banner(survey, item),
        bases=__import__("app.items.score_band", fromlist=["BASES"]).BASES,
        construct=constructs.info(item), zis_changed=constructs.zis_changed(), level_labels=_level_labels(),
        linked=_linked_box(survey, item, lang),
        # "Nur zeigen, wenn …" (app/item_condition.py)
        item_cond=item_condition.editor_view(item, lang) if item_condition.supported(item) else None,
    )


def _level_labels():
    """The levels of measurement in the language of the page (the German ones are items.base.LEVEL_LABELS)."""
    english = {"nominal": "nominal", "ordinal": "ordinal", "metric": "metric"}
    return {key: de_en(label, english.get(key, label)) for key, label in LEVEL_LABELS.items()}


def _lock_banner(survey, item):
    """The note at the top of the element editor: a choice design with real data, or a question with real answers in
    a survey opened for editing again."""
    if survey.is_editable and item.kind.design_locked(item):
        return {"design_locked": True, "design_lock_message": _design_locked()}
    return {"design_locked": answer_lock.fixed(survey, item), "design_lock_message": answer_lock.note(item)}


def _linked_box(survey, item, lang):
    """What the element editor says about linking: the element this question uses (state, uses), or whether the
    question can become one (app/linked.py)."""
    c = linked.element(item)
    if c is not None:
        version = linked.version_of(item)
        return {**linked.overview(c, current_user), "text_locked": linked.text_locked(item, lang),
                "adapted": linked.adapted(item), "usable": linked.usable(current_user, c),
                # the Fassung this question asks: frozen by its own uses; a newer one may be on offer
                "frozen": linked.frozen(c, current_user, version), "version": version,
                "switch_refused": linked.switch_refused(item) if version < c.seed_version else None,
                "new_version": linked.may_change(current_user, c) and linked.new_version_refused(item) is None}
    if (item.config or {}).get("construct") or not survey.can(current_user, "editor") or item.type in ("consent", "page_break"):
        return None
    reason = linked.linkable(item)
    return {"element": None, "reason": reason, "candidates": [] if reason else linked.candidates(current_user, item)}


@bp.route("/presence", methods=["POST"])
@login_required
def presence(survey_id):
    """Heartbeat of an open editor (app/edit_guard.py): records this person, returns the others."""
    survey = get_survey(survey_id, need="viewer")
    from app import access

    if access.showcase_only(current_user, survey):
        return {"others": []}
    key = request.form.get("key", "")[:60]
    if not key:
        abort(400)
    if not edit_guard.known(survey, key):  # every new key is a row: only parts this survey has (or had just now)
        return "", 204
    edit_guard.seen(survey, key, here=request.form.get("leave") != "1")
    db.session.commit()
    return {"others": edit_guard.others_here(survey, key)}


@bp.route("/items/<int:item_id>/frame", methods=["GET", "POST"])
@login_required
def item_frame(survey_id, item_id):
    """The element as participants see it (live preview in the element editor). GET: as saved.
    POST: with the editor's current, unsaved form values, applied on trial and rolled back."""
    survey = get_survey(survey_id, need="viewer")
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    if request.method == "POST" and survey.can(current_user, "editor"):
        structural = (survey.is_editable and lang == survey.default_language
                      and not constructs.locked(item)
                      and not item.kind.design_locked(item))
        try:
            code = request.form.get("code", "").strip()
            if structural and CODE_RE.match(code) and (code == item.code or code.upper() not in SPSS_RESERVED):
                item.code = code
            mode = request.form.get("answer_mode")
            if structural and mode in Item.ANSWER_MODES and item.type != "consent":
                item.answer_mode = mode
            if not linked.text_locked(item, lang):
                item.kind.update_from_form(item, request.form, lang, structural)
                _read_lock(item, request.form, structural)
            if survey.is_editable and lang == survey.default_language and item.kind.supports_dropdown:
                item.kind.set_display(item, request.form)
            if survey.is_editable and lang == survey.default_language:
                item.kind.set_optional_options(item, request.form)
            html = render_template("editor/item_frame.html", survey=survey, item=item, kind=item.kind, lang=lang)
        finally:
            db.session.rollback()  # nothing of the trial is kept
        return html
    return render_template("editor/item_frame.html", survey=survey, item=item, kind=item.kind, lang=lang)


@bp.route("/items/<int:item_id>/unlock", methods=["POST"])
@login_required
def unlock_construct(survey_id, item_id):
    """Allow structural changes to a validated construct; it is then reported as adapted."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    if not _require_draft(survey):
        return _back(survey, lang)
    config = copy.deepcopy(item.config)
    if "construct" in config:
        step = undo.begin(survey)
        config["construct"]["unlocked"] = True
        item.config = config
        is_linked = linked.is_linked(item)
        step.done(undo.both("Verknüpftes Element „{code}“ entsperrt", "Linked element “{code}” unlocked", code=item.code)
                  if is_linked else
                  undo.both("Konstrukt „{code}“ entsperrt", "Construct “{code}” unlocked", code=item.code), page="item", item=item.id)
        db.session.commit()
        flash(de_en("In dieser Befragung angepasst: Das Element bleibt verknüpft und wird in Codebuch und gemeinsamer "
                    "Auswertung als angepasst gekennzeichnet.",
                    "Adapted in this survey: the element stays linked and is marked as adapted in the codebook and the "
                    "joint analysis.") if is_linked else
              de_en("Konstrukt entsperrt. Im Codebuch wird es als angepasst gekennzeichnet.",
                    "Construct unlocked. The codebook marks it as adapted."), "success")
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))


@bp.route("/items/<int:item_id>/link", methods=["POST"])
@login_required
def make_linked(survey_id, item_id):
    """Make this question a linked element (app/linked.py): other surveys can insert the same question, and the
    answers of all of them can be analysed together. Also in a running or closed survey: nothing in the question
    changes, it only becomes the first use."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    reason = linked.linkable(item)
    if reason:
        flash(reason, "error")
        return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))
    step = undo.begin(survey)
    c = linked.create(item, current_user, request.form.get("name", ""))
    step.done(undo.both("„{code}“ verknüpfbar gemacht", "“{code}” made linkable", code=item.code), page="item", item=item.id)
    db.session.commit()
    flash(de_en("„{name}“ ist jetzt ein verknüpftes Element. Fügen Sie es in anderen Befragungen über die Liste "
                "„Verknüpfte Elemente“ im Fragebogen ein.",
                "“{name}” is now a linked element. Insert it in other surveys from the list “Linked elements” in the "
                "questionnaire.", name=c.name), "success")
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))


@bp.route("/items/<int:item_id>/new-version", methods=["POST"])
@login_required
def new_linked_version(survey_id, item_id):
    """Fassung n+1 of the linked element from this question (as it is, also adapted): it is open again until a survey
    asks it for real; surveys that ask the older Fassung keep it. Only whoever made the element (app/linked.py)."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    c = linked.element(item)
    if c is None or not linked.may_change(current_user, c):
        abort(403)
    if not _require_draft(survey):
        return _back(survey, lang)
    refused = linked.new_version_refused(item)
    if refused:
        flash(refused, "error")
        return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))
    step = undo.begin(survey)
    linked.new_version(item)
    step.done(undo.both("„{code}“: Fassung {rev} angelegt", "“{code}”: revision {rev} created", code=item.code,
                    rev=c.seed_version), page="item", item=item.id)
    db.session.commit()
    flash(de_en("Fassung {rev} von „{name}“ angelegt. Sie ist offen, bis eine Befragung damit läuft; Befragungen "
                "mit Fassung {old} behalten diese.",
                "Revision {rev} of “{name}” created. It stays open until a survey with it runs; surveys with revision "
                "{old} keep theirs.", rev=c.seed_version, name=c.name, old=c.seed_version - 1), "success")
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))


@bp.route("/items/<int:item_id>/switch-version", methods=["POST"])
@login_required
def switch_linked_version(survey_id, item_id):
    """This question asks the newest Fassung of its linked element."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    refused = linked.switch_refused(item)
    if refused:
        flash(refused, "error")
    else:
        step = undo.begin(survey)
        linked.switch(item)
        step.done(undo.both("„{code}“ auf Fassung {rev} umgestellt", "“{code}” switched to revision {rev}", code=item.code,
                        rev=linked.version_of(item)), page="item", item=item.id)
        db.session.commit()
        flash(de_en("Die Frage stellt jetzt Fassung {rev}.", "The question now asks revision {rev}.",
                    rev=linked.version_of(item)), "success")
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))


@bp.route("/items/<int:item_id>/connect", methods=["POST"])
@login_required
def join_linked(survey_id, item_id):
    """Join this existing question to a linked element (e.g. a copy made before linking existed): nothing in the
    question changes; where it differs from the element it counts as adapted."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    c = linked.get(request.form.get("key", ""))
    if c is None or c not in linked.candidates(current_user, item) or linked.linkable(item):
        flash(linked.linkable(item) or de_en("Bitte ein verknüpftes Element gleicher Art wählen.",
                                             "Please choose a linked element of the same kind."), "error")
        return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))
    step = undo.begin(survey)
    mods = linked.join(item, c)
    step.done(undo.both("„{code}“ mit „{name}“ verbunden", "“{code}” connected with “{name}”", code=item.code, name=c.name),
              page="item", item=item.id)
    db.session.commit()
    flash(de_en("„{code}“ gehört jetzt zu „{name}“", "“{code}” now belongs to “{name}”", code=item.code, name=c.name)
          + (de_en(", als angepasst: {mods}.", ", as adapted: {mods}.", mods="; ".join(mods)) if mods else ".")
          + de_en(" Teilnahmen von vorher kommen in die gemeinsame Auswertung nur mit dem Haken „ohne Hinweis in der "
                  "Einwilligung“.", " Earlier participations go into the joint analysis only with the tick “without a "
                  "note in the consent”."), "success" if not mods else "warning")
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))


@bp.route("/items/<int:item_id>/unlink", methods=["POST"])
@login_required
def unlink_item(survey_id, item_id):
    """This question leaves its linked element and becomes an ordinary copy; its answers stay in this survey."""
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    c = linked.element(item)
    if c is not None or linked.is_linked(item):  # also when its element is gone: the link then names nothing
        step = undo.begin(survey)
        linked.unlink(item)
        step.done(undo.both("Verknüpfung von „{code}“ gelöst", "“{code}” unlinked", code=item.code), page="item", item=item.id)
        db.session.commit()
        if c is not None:
            flash(de_en("Verknüpfung gelöst: „{code}“ ist jetzt eine gewöhnliche Frage und zählt nicht mehr zu „{name}“.",
                        "Unlinked: “{code}” is now an ordinary question and no longer counts towards “{name}”.",
                        code=item.code, name=c.name), "success")
        else:
            flash(de_en("Verknüpfung gelöst: „{code}“ ist jetzt eine gewöhnliche Frage.",
                        "Unlinked: “{code}” is now an ordinary question.", code=item.code), "success")
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang))


# --- questions with real answers in a survey opened for editing again (the rule: app/answer_lock.py) -------------

def _read_lock(item, form, structural):
    """The tick "Antworten danach nicht mehr ändern" of a feedback element (app/feedback_lock.py), as its other
    settings: only while the structure may change."""
    if structural and getattr(item.kind, "category", None) == "feedback":
        item.config = {**item.config, "lock_answers": bool(form.get("lock_answers"))}


def _recomputed(survey, item, config_before):
    """How many real participations get another value from a Rechenwert whose formula changed (its value is stored
    nowhere: data set and results show the new one for everybody)."""
    from types import SimpleNamespace

    if (survey.qversion or 1) <= 1:
        return 0
    from app.models import Response
    from app.runtime import load_answers

    old = SimpleNamespace(config=config_before, block=item.block)
    n = 0
    for response in db.session.scalars(db.select(Response).filter_by(survey_id=survey.id, source="real",
                                                                        is_preview=False)):
        answers = load_answers(response)
        if item.kind.value(old, answers) != item.kind.value(item, answers):
            n += 1
    return n


# --- conjoint ------------------------------------------------------------------------

DESIGN_LOCKED = ("Zu diesem Element liegen bereits echte Antworten vor: Versuchsplan, Merkmale, Stufen, Aussagen und "
                 "Versionen bleiben unverändert, weil die gespeicherten Antworten auf ihre Positionen verweisen. "
                 "Texte lassen sich weiter korrigieren. Für einen neuen Versuchsplan bitte ein neues Element anlegen.")
DESIGN_LOCKED_EN = ("This element already has real answers: experimental design, attributes, levels, statements and versions stay "
                    "unchanged because the stored answers refer to their positions. Texts can still be corrected. For a "
                    "new experimental design, please create a new element.")


def _design_locked():
    return de_en(DESIGN_LOCKED, DESIGN_LOCKED_EN)


# test runs and dummy data are not protected by the design lock: their answers go with the design they belonged to
TRIAL_ANSWERS_GONE = ("Die Antworten aus Testdurchläufen und Dummy-Daten zu dieser Frage wurden entfernt: Sie bezogen sich auf "
                      "das bisherige Design und wären mit dem neuen falsch gelesen worden.")
TRIAL_ANSWERS_GONE_EN = ("The answers from test runs and dummy data to this question were removed: they referred to the "
                         "previous design and would have been read wrongly with the new one.")


def _trial_answers_gone():
    return de_en(TRIAL_ANSWERS_GONE, TRIAL_ANSWERS_GONE_EN)


def _search_cut():
    return de_en("Die Suche nach dem Versuchsplan wurde nach {s} Sekunden beendet; gespeichert ist der beste bis dahin gefundene "
                 "Versuchsplan. Ein erneutes Erzeugen kann einen etwas besseren finden; weniger Versionen, Aufgaben oder Stufen "
                 "machen die Suche schneller.",
                 "The search for the experimental design was stopped after {s} seconds; the best one found until then is saved. Generating "
                 "it again may find a slightly better one; fewer versions, tasks or levels make the search faster.",
                 s=f"{current_app.config.get('DESIGN_SECONDS', 20):g}")


@bp.route("/items/<int:item_id>/conjoint/design", methods=["POST"])
@login_required
@heavy.limited  # the design search takes up to DESIGN_SECONDS of a CPU (code review 2026-10-15, B1)
def conjoint_design(survey_id, item_id):
    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    lang = _lang(survey)
    if not hasattr(item.kind, "make_design"):  # conjoint, MaxDiff
        abort(404)
    if item.kind.design_locked(item):
        flash(_design_locked(), "error")
    elif _require_draft(survey):
        step = undo.begin(survey)
        try:
            budget = item.kind.make_design(item)
        except ValueError as e:
            flash(str(e), "error")
        else:
            gone = item.kind.forget_trial_answers(item)
            step.done(undo.both("Versuchsplan von „{code}“ neu erzeugt", "Experimental design of “{code}” generated anew", code=item.code),
                      page="item", item=item.id)
            db.session.commit()
            flash(item.kind.design_summary(item), "success")
            if getattr(budget, "cut", False):
                flash(_search_cut(), "warning")
            if gone:
                flash(_trial_answers_gone(), "warning")
    return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=lang) + "#design")


@bp.route("/items/<int:item_id>/copy-to", methods=["POST"])
@login_required
def copy_item_to(survey_id, item_id):
    """The question into another survey (a draft the person may edit): at its end, in a block of its own. One panel
    "In anderer Befragung verwenden" (Ralf, 2026-10-08): mode "copy" (the default) a question of its own; mode "link"
    first makes the question a linked element (app/linked.py) and then inserts it, linked; linked without a survey
    it only makes the question linkable."""
    from app import duplicate
    from app.models import Survey

    survey = get_survey(survey_id)
    item = _get_item(survey, item_id)
    if item.type in ("consent", "page_break"):  # never copied (the page offers it for neither)
        abort(400)
    if getattr(item.kind, "experimental", None):  # only for whoever has the feature (security audit 2026-10-06, S12)
        experimental.require(item.kind.experimental)
    code = request.form.get("target", "")
    target = db.session.scalar(db.select(Survey).filter_by(code=code)) if code else None
    if request.form.get("mode") == "link" and linked.element(item) is None:
        if not survey.can(current_user, "editor"):
            abort(403)
        if code and (target is None or target not in duplicate.item_targets(current_user, item)):
            abort(403)
        reason = linked.linkable(item)
        if reason:
            flash(reason, "error")
            return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=_lang(survey)))
        step = undo.begin(survey)
        c = linked.create(item, current_user, request.form.get("name", ""))
        step.done(undo.both("„{code}“ verknüpfbar gemacht", "“{code}” made linkable", code=item.code), page="item", item=item.id)
        db.session.commit()
        if target is None:
            flash(de_en("„{name}“ ist jetzt ein verknüpftes Element. Fügen Sie es in anderen Befragungen über die Liste "
                        "„Verknüpfte Elemente“ im Fragebogen ein.",
                        "“{name}” is now a linked element. Insert it in other surveys from the list “Linked elements” in "
                        "the questionnaire.", name=c.name), "success")
            return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=_lang(survey)))
    if target is None or target not in duplicate.item_targets(current_user, item):
        abort(403)
    if not constructs.permitted_copy(item, target, current_user):
        flash(constructs.permission_notice(), "error")
        return redirect(url_for("editor.edit_item", survey_id=survey.id, item_id=item.id, lang=_lang(survey)))
    step = undo.begin(target)
    new = duplicate.copy_item(item, target)
    step.done(undo.both("{code} aus „{title}“ kopiert", "{code} copied from “{title}”", code=new.code, title=survey.title),
              item=new.id)
    db.session.commit()
    if linked.element(new) is not None:
        flash(de_en("{code} ist als {new} am Ende von „{title}“ eingefügt, verknüpft mit „{name}“.",
                    "{code} was inserted as {new} at the end of “{title}”, linked with “{name}”.",
                    code=item.code, new=new.code, title=target.title, name=linked.element(new).name), "success")
    else:
        flash(de_en("{code} ist als {new} am Ende von „{title}“ angelegt.", "{code} was created as {new} at the end of “{title}”.",
                    code=item.code, new=new.code, title=target.title), "success")
    if new.media_note:
        flash(new.media_note, "warning")
    if new.cond_note:
        flash(new.cond_note, "warning")
    if new.untranslated:
        flash(de_en("Die Frage hat keine Texte auf {target}: Hier stehen ihre Texte auf {source}. Bitte übersetzen Sie sie.",
                    "The question has no texts in {target}: its texts in {source} are shown here. Please translate them.",
                    target=LANGUAGE_NAMES.get(target.default_language, target.default_language),
                    source=LANGUAGE_NAMES.get(new.untranslated, new.untranslated)), "warning")
    return redirect(url_for("editor.edit_item", survey_id=target.id, item_id=new.id, lang=target.default_language))
