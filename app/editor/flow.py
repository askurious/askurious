"""The flow page: loops, vignettes, flow elements."""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import ai, autosave, design, edit_guard, trash, undo
from app.editor.base import _CANNOT_PLACE, _back, _lang, _malformed, _require_draft, bp, new_id
from app.extensions import db
from app.lang import de_en
from app.surveys import LANGUAGE_NAMES, get_survey


# --- survey flow (randomizers, branches, end nodes) --------------------------------------

flow_bp = Blueprint("flow_page", __name__, url_prefix="/surveys/<sid:survey_id>/flow")
flow_bp.register_error_handler(ValueError, _malformed)
flow_bp.register_error_handler(OverflowError, _malformed)


@flow_bp.route("/loop/<nid>", methods=["GET", "POST"], endpoint="loop_node")
@login_required
@autosave.aware
def flow_loop_node(survey_id, nid):
    from app import flow, loops

    survey = get_survey(survey_id)
    lang = _lang(survey)
    node, _ = flow.find(flow.tree(survey), nid)
    if node is None or node["t"] != "loop":
        abort(404)
    here = url_for("flow_page.loop_node", survey_id=survey.id, nid=nid, lang=lang)
    if node.get("source") == "factors":  # vignettes: the same loop with factors as its source, on their own page
        return redirect(url_for("flow_page.vignettes_node", survey_id=survey.id, nid=nid, lang=lang))
    if request.method == "POST" and request.form.get("action") == "source_factors":
        return _switch_source(survey, nid, lang, "factors")
    if request.method == "POST" and request.form.get("action") == "plan_generate":
        import copy

        from app import loop_plan

        if not _require_draft(survey):
            return redirect(here)
        step = undo.begin(survey)
        root = copy.deepcopy(flow.tree(survey))
        mine, _ = flow.find(root, nid)
        try:
            cfg = loop_plan.generate(survey, mine)
        except ValueError as e:
            db.session.rollback()
            flash(str(e), "error")
            return redirect(here + "#auswahl")
        survey.flow = root
        step.done(undo.both("Schleife „{name}“ Versuchsplan erzeugt", "Loop “{name}”: experimental design generated",
                        name=mine.get("name") or undo.both("Schleife", "Loop")), page="loop", node=nid)
        db.session.commit()
        n_rows, n_decks = sum(len(d) for d in cfg["decks_rows"]), len(cfg["decks_rows"])
        flash(de_en("Versuchsplan erzeugt: {rows} Zeilen in {n} Deck.", "Experimental design generated: {rows} rows in {n} deck.",
                    rows=n_rows, n=n_decks) if n_decks == 1 else
              de_en("Versuchsplan erzeugt: {rows} Zeilen in {n} Decks.", "Experimental design generated: {rows} rows in {n} decks.",
                    rows=n_rows, n=n_decks), "success")
        return redirect(here + "#auswahl")
    if request.method == "POST":
        if request.form.get("action") == "variants":  # stimulus sampling (loops.add_variants)
            if not _require_draft(survey):
                return redirect(here)
            n = request.form.get("variants", "")
            step = undo.begin(survey)
            try:
                groups, added = loops.add_variants(survey, node, request.form.getlist("variant_col"),
                                                   max(2, min(10, int(n))) if n.isascii() and n.isdigit() else 3)
            except ValueError as e:
                flash(str(e), "error")
                return redirect(here + "#varianten")
            step.done(undo.both("Varianten der Schleife „{name}“ angelegt", "Variants of the loop “{name}” created",
                            name=node.get("name") or undo.both("Schleife", "Loop")), page="loop", node=nid)
            db.session.commit()
            flash(de_en("{groups} Bedingungen mit Varianten; {added} Zeilen als Kopie angelegt: bitte die Varianten in der "
                        "Tabelle unterscheiden (Text ändern oder {placeholder} im Bild-Prompt).",
                        "{groups} conditions with variants; {added} rows created as copies: please make the variants differ "
                        "in the table (change the text or {placeholder} in the image prompt).",
                        groups=groups, added=added, placeholder="{{" + loops.VARIANT_COLUMN + "}}"), "success")
            return redirect(here)
        if not survey.is_editable and lang == survey.default_language:
            flash(de_en("Die Befragung läuft oder ist geschlossen: Die Tabelle ist fest.",
                        "The survey is running or closed: the table is fixed."), "warning")  # loops.update_from_form
        else:
            key = f"loop:{nid}"
            conflict = edit_guard.check(survey, key, edit_guard.node_state(node), request.form)
            step = undo.begin(survey)
            kept = loops.update_from_form(survey, node, request.form, lang)
            if kept:
                rows = ", ".join(map(str, kept))
                flash((de_en("Zeile {rows} bleibt in der Tabelle:", "Row {rows} stays in the table:", rows=rows)
                       if len(kept) == 1 else
                       de_en("Zeilen {rows} bleiben in der Tabelle:", "Rows {rows} stay in the table:", rows=rows))
                      + de_en(" echte Teilnehmende haben sie schon gesehen, ihre Antworten hängen daran. Texte lassen sich "
                              "weiter ändern; zum Umbauen der Tabelle die Befragung duplizieren. Bitte die Seite neu laden.",
                              " real participants have already seen it, their answers depend on it. Texts can still be "
                              "changed; to rebuild the table, duplicate the survey. Please reload the page."),
                      "warning")
            if conflict:  # someone saved the table in between: show the inputs again, keep their version
                return edit_guard.show_again(lambda: _loop_page_html(survey, node, lang))
            edit_guard.saved(survey, key)
            saved, _ = flow.find(flow.tree(survey), nid)
            step.done(undo.both("Schleife „{name}“ bearbeitet", "Loop “{name}” edited",
                            name=saved.get("name") or undo.both("Schleife", "Loop")), page="loop", node=nid,
                      merge=autosave.merge(f"loop:{nid}"))
            db.session.commit()
            autosave.base(edit_guard.node_state(flow.find(flow.tree(survey), nid)[0]))
            flash(de_en("Schleife gespeichert.", "Loop saved."), "success")
        return redirect(here)
    return _loop_page_html(survey, node, lang)


WELLS_WINDSCHITL = ("Wells, G. L., & Windschitl, P. D. (1999). Stimulus sampling and social psychological "
                    "experimentation. Personality and Social Psychology Bulletin, 25(9), 1115–1125. "
                    "https://doi.org/10.1177/01461672992512005")
JUDD_WESTFALL_KENNY = ("Judd, C. M., Westfall, J., & Kenny, D. A. (2012). Treating stimuli as a random factor in social "
                       "psychology: A new and comprehensive solution to a pervasive but largely ignored problem. Journal "
                       "of Personality and Social Psychology, 103(1), 54–69. https://doi.org/10.1037/a0028347")


def _loop_page_html(survey, node, lang):
    from app import loops

    blocks = loops.blocks_in(survey, node)
    from app import doe, loop_plan, vignettes

    loop = loops.config_node(node)
    structural = survey.is_editable and lang == survey.default_language and survey.can(current_user, "editor")
    # one fold of sources at the end of the page (W-10: the table subset is a Fedorov exchange)
    loop_sources = []
    if node.get("mode") == "plan":
        loop_sources.append((de_en("Versuchsplan: D-optimaler Ausschnitt der Tabelle durch Austausch einzelner Zeilen "
                                   "(Fedorov-Austausch), D-Effizienz, Aufteilung in Decks",
                                   "Experimental design: D-optimal subset of the table by exchanging single rows "
                                   "(Fedorov exchange), D-efficiency, split into decks"),
                             [doe.FEDOROV, doe.KUHFELD, doe.ATZMUELLER_STEINER]))
    if structural and loop["rows"] and loop["columns"]:
        loop_sources.append((de_en("Varianten je Stufe (Stimulus Sampling)", "Variants per level (stimulus sampling)"),
                             [WELLS_WINDSCHITL, JUDD_WESTFALL_KENNY]))
    return render_template("editor/loop.html", survey=survey, node=node, blocks=blocks, lang=lang, vignettes=vignettes,
                           loop_sources=loop_sources,
                           languages=LANGUAGE_NAMES, loop=loop,
                           loop_plan=loop_plan, subset=loop_plan.settings(node), in_plan=loop_plan.in_plan(node),
                           plan_current=loop_plan.current(survey, node) if node.get("mode") == "plan" else False,
                           variant_column=loops.VARIANT_COLUMN, structural=structural,
                           readings=loops.readings(survey, node, lang), read_rows=loops.READ_ROWS,
                           findings=[f for f in loops.check(survey)
                                     if f[3] in {b.id for b in blocks} or node.get("name", "") in f[1]])


def _switch_source(survey, nid, lang, to):
    """The choice at the top of the loop page: rows from a table or from factors and levels (app/vignettes.py,
    to_factors / to_table). Only in the draft; one undo step, the page of the new source next."""
    import copy

    from app import flow, vignettes

    back = "flow_page.vignettes_node" if to == "table" else "flow_page.loop_node"
    if not _require_draft(survey) or not survey.can(current_user, "editor") or lang != survey.default_language:
        return redirect(url_for(back, survey_id=survey.id, nid=nid, lang=lang))
    step = undo.begin(survey)
    root = copy.deepcopy(flow.tree(survey))
    mine, _ = flow.find(root, nid)
    note = _source_note(survey, mine, to)
    if to == "factors":
        vignettes.to_factors(survey, mine, root)
    else:
        vignettes.to_table(survey, mine, root)
    survey.flow = root
    step.done(undo.both("Schleife „{name}“: {source} als Quelle", "Loop “{name}”: {source} as source",
                    name=mine.get("name") or undo.both("Schleife", "Loop"),
                    source=undo.both("Faktoren und Stufen", "factors and levels") if to == "factors" else undo.both("Tabelle", "table")),
              page="loop", node=nid)
    db.session.commit()
    if note:  # no question beforehand: the switch is done, says what it did and offers "Rückgängig" (W28)
        flash(note, "undo")
    if to == "factors":
        return redirect(url_for("flow_page.vignettes_node", survey_id=survey.id, nid=nid, lang=lang))
    return redirect(url_for("flow_page.loop_node", survey_id=survey.id, nid=nid, lang=lang))


def _source_note(survey, node, to):
    """What a switch of the loop's source does to what is there (said after it, with "Rückgängig"); None when there
    was nothing yet (no rows, no factors)."""
    from app import vignettes

    if to == "factors":
        if not node.get("rows"):
            return None
        names, outcome = vignettes.from_table(survey, node)
        if not names:
            return de_en("Faktoren und Stufen sind jetzt die Quelle; die Tabelle ist durch leere Faktoren ersetzt.",
                         "Factors and levels are now the source; the table is replaced by empty factors.")
        rest = {"decks": de_en("; die Decks des Versuchsplans sind geblieben", "; the decks of the experimental design stayed"),
                "full": de_en("; alle Kombinationen sind der Versuchsplan", "; all combinations are the experimental design"),
                "new": de_en("; den Versuchsplan erzeugen Sie jetzt", "; you generate the experimental design now")}[outcome]
        return de_en("Faktoren und Stufen sind jetzt die Quelle: Die Spalten {names} sind Faktoren, ihre Werte Stufen{rest}. "
                     "Andere Spalten sind entfallen.", "Factors and levels are now the source: the columns {names} are "
                     "factors, their values levels{rest}. Other columns were dropped.", names=", ".join(names), rest=rest)
    if not node.get("attributes"):
        return None
    decks = len(((node.get("plan") or {}).get("decks_rows") or [])) > 1
    return de_en("Die Tabelle ist jetzt die Quelle: Die Vignetten stehen als Tabelle, die Sie bearbeiten können{rest}. "
                 "Faktoren, Ausschlüsse und Versuchsplan sind entfallen.", "The table is now the source: the vignettes "
                 "appear as a table that you can edit{rest}. Factors, exclusions and experimental design were dropped.",
                 rest=de_en("; jede Person bekommt weiter eines der Decks", "; each person still gets one of the decks") if decks else "")


@flow_bp.route("/vignettes/<nid>", methods=["GET", "POST"], endpoint="vignettes_node")
@login_required
@autosave.aware
def flow_vignettes_node(survey_id, nid):
    """The editor of a vignette loop (app/vignettes.py): factors, prohibitions, plan, preview."""
    from app import flow, vignettes

    survey = get_survey(survey_id)
    lang = _lang(survey)
    node, _ = flow.find(flow.tree(survey), nid)
    if not vignettes.is_vignettes(node):
        abort(404)
    here = url_for("flow_page.vignettes_node", survey_id=survey.id, nid=nid, lang=lang)
    if request.method == "POST":
        import copy

        if not survey.can(current_user, "editor"):
            abort(403)
        action = request.form.get("action", "")
        if action.startswith("ai_"):
            return _vignettes_ai(survey, node, nid, lang, action, here)
        if action == "source_table":
            return _switch_source(survey, nid, lang, "table")
        if request.form.get("action") == "generate":
            if not _require_draft(survey):
                return redirect(here)
            if vignettes.has_real_answers(survey, node):  # code review 2026-10-15, C3
                flash(de_en("Zu dieser Schleife liegen schon echte Antworten vor. Sie sind je Vignette gespeichert; ein neuer "
                            "Versuchsplan würde sie anderen Stufen zuordnen. Der Versuchsplan bleibt daher, wie er ist.",
                            "Real answers to this loop already exist. They are stored per vignette; a new experimental "
                            "design would assign them to other levels. The experimental design therefore stays as it is."),
                      "error")
                return redirect(here + "#plan")
            from app import heavy
            from app.conjoint_design import Budget

            if not heavy.hold():  # the search takes up to DESIGN_SECONDS of a CPU (code review 2026-10-15, B2)
                return heavy.busy()
            step = undo.begin(survey)
            root = copy.deepcopy(flow.tree(survey))
            mine, _ = flow.find(root, nid)
            budget = Budget(current_app.config.get("DESIGN_SECONDS", 20))
            try:
                plan = vignettes.generate(survey, mine, budget=budget)
            except ValueError as e:
                db.session.rollback()
                flash(str(e), "error")
                return redirect(here + "#plan")
            survey.flow = root
            step.done(undo.both("Versuchsplan der Schleife „{name}“ erzeugt", "Experimental design of the loop “{name}” generated",
                            name=mine.get("name") or undo.both("Vignetten", "Vignettes")), page="loop", node=nid)
            db.session.commit()
            n_vig, n_decks = len(plan["profiles"]), len(plan["decks_rows"])
            flash(de_en("Versuchsplan erzeugt: {v} Vignetten in {n} Deck.", "Experimental design generated: {v} vignettes in {n} deck.",
                        v=n_vig, n=n_decks) if n_decks == 1 else
                  de_en("Versuchsplan erzeugt: {v} Vignetten in {n} Decks.", "Experimental design generated: {v} vignettes in {n} decks.",
                        v=n_vig, n=n_decks), "success")
            if budget.cut:
                flash(de_en("Die Suche nach dem Versuchsplan wurde nach {s} Sekunden beendet; gespeichert ist der beste bis "
                            "dahin gefundene Versuchsplan. Ein erneutes Erzeugen kann einen etwas besseren finden; weniger Vignetten "
                            "oder Decks machen die Suche schneller.",
                            "The search for the experimental design was stopped after {s} seconds; the best one found until "
                            "then is saved. Generating it again may find a slightly better one; fewer vignettes or decks "
                            "make the search faster.", s=f"{current_app.config.get('DESIGN_SECONDS', 20):g}"), "warning")
            return redirect(here + "#plan")
        if not survey.is_editable and lang == survey.default_language:
            flash(de_en("Die Befragung läuft oder ist geschlossen: Faktoren und Versuchsplan sind fest.",
                        "The survey is running or closed: factors and experimental design are fixed."), "warning")
            return redirect(here)
        key = f"loop:{nid}"
        conflict = edit_guard.check(survey, key, edit_guard.node_state(node), request.form)
        step = undo.begin(survey)
        root = copy.deepcopy(flow.tree(survey))
        mine, _ = flow.find(root, nid)
        vignettes.update_from_form(survey, mine, request.form, lang)
        survey.flow = root
        if conflict:
            return edit_guard.show_again(lambda: _vignettes_page_html(survey, flow.find(flow.tree(survey), nid)[0], lang))
        edit_guard.saved(survey, key)
        step.done(undo.both("Schleife „{name}“ bearbeitet", "Loop “{name}” edited",
                        name=mine.get("name") or undo.both("Vignetten", "Vignettes")), page="loop", node=nid,
                  merge=autosave.merge(f"loop:{nid}"))
        db.session.commit()
        autosave.base(edit_guard.node_state(flow.find(flow.tree(survey), nid)[0]))
        flash(de_en("Gespeichert.", "Saved."), "success")
        return redirect(here)
    return _vignettes_page_html(survey, node, lang)


def _vignettes_ai(survey, node, nid, lang, action, here):
    """The AI buttons of the vignette editor (app/vignettes_ai.py): ask (the page shows the suggestions to tick) or
    take the ticked ones over (one undo step)."""
    import copy

    from app import flow, vignettes_ai

    if not _require_draft(survey) or lang != survey.default_language:
        return redirect(here)
    asks = {"ai_factors": ("factors", lambda: vignettes_ai.suggest_factors(survey, node)),
            "ai_levels": ("levels", lambda: vignettes_ai.suggest_levels(survey, node, request.form.get("aid", ""))),
            "ai_pairs": ("pairs", lambda: vignettes_ai.suggest_pairs(survey, node)),
            "ai_text": ("text", lambda: vignettes_ai.suggest_text(survey, node))}
    if action in asks:
        kind, ask = asks[action]
        try:
            found = ask()
        except ai.AIError as e:
            flash(str(e), "error")
            return redirect(here)
        if kind == "pairs" and not found:
            flash(de_en("Die KI hat keine unplausiblen Kombinationen gefunden.", "The AI found no implausible combinations."),
                  "success")
            return redirect(here + "#ausschluesse")
        return _vignettes_page_html(survey, node, lang, ai_found={"kind": kind, "data": found})
    step = undo.begin(survey)
    root = copy.deepcopy(flow.tree(survey))
    mine, _ = flow.find(root, nid)
    what, anchor = None, ""
    if action == "ai_take_factors":
        chosen = vignettes_ai.from_form_factors(request.form)
        if chosen:
            vignettes_ai.take_factors(survey, mine, chosen)
            what = (de_en("{n} Faktor von der KI übernommen", "{n} factor accepted from the AI", n=len(chosen))
                    if len(chosen) == 1 else
                    de_en("{n} Faktoren von der KI übernommen", "{n} factors accepted from the AI", n=len(chosen)))
    elif action == "ai_take_levels":
        labels = [vignettes_ai._short(l) for l in request.form.getlist("take") if vignettes_ai._short(l)]
        if labels:
            vignettes_ai.take_levels(survey, mine, request.form.get("aid", ""), labels)
            what = (de_en("{n} Stufe von der KI übernommen", "{n} level accepted from the AI", n=len(labels))
                    if len(labels) == 1 else
                    de_en("{n} Stufen von der KI übernommen", "{n} levels accepted from the AI", n=len(labels)))
    elif action == "ai_take_pairs":
        pairs = vignettes_ai.from_form_pairs(request.form)
        if pairs:
            vignettes_ai.take_pairs(survey, mine, pairs)
            what = (de_en("{n} Ausschluss von der KI übernommen", "{n} exclusion accepted from the AI", n=len(pairs))
                    if len(pairs) == 1 else
                    de_en("{n} Ausschlüsse von der KI übernommen", "{n} exclusions accepted from the AI", n=len(pairs)))
            anchor = "#ausschluesse"
    elif action == "ai_take_text":
        text = " ".join(request.form.get("text", "").split())[:2000]
        if text and vignettes_ai.take_text(survey, mine, text) is not None:
            what = de_en("Vignettentext von der KI übernommen", "Vignette text accepted from the AI")
    if what is None:
        db.session.rollback()
        flash(de_en("Nichts angekreuzt: nichts übernommen.", "Nothing ticked: nothing accepted."), "warning")
        return redirect(here)
    survey.flow = root
    step.done(undo.both("Schleife „{name}“: {what}", "Loop “{name}”: {what}",
                    name=mine.get("name") or undo.both("Vignetten", "Vignettes"), what=what), page="loop", node=nid)
    db.session.commit()
    flash(what + ".", "success")
    return redirect(here + anchor)


def _vignettes_page_html(survey, node, lang, ai_found=None):
    from app import doe, loops, vignettes

    blocks = loops.blocks_in(survey, node)
    f = vignettes.factors(survey, node)
    preview = request.args.get("v", "1")
    preview = int(preview) if preview.isdigit() else 1
    sources = [(de_en("Faustregeln für Faktoren, Vignetten je Person und D-Effizienz",
                      "Rules of thumb for factors, vignettes per person and D-efficiency"), [doe.AUSPURG_HINZ, doe.GUTFLEISCH]),
               (de_en("Aufteilung in Decks", "Split into decks"), [doe.ATZMUELLER_STEINER, doe.SAUER]),
               (de_en("D-optimale Auswahl statt Zufallsziehung", "D-optimal selection instead of random draw"),
                [doe.DUELMER, doe.SU_STEINER]),
               (de_en("Verfahren und Effizienzmaß: Koordinatenaustausch, Austausch einzelner Vignetten beim Aufteilen "
                      "(Fedorov-Austausch), D-Effizienz",
                      "Method and efficiency measure: coordinate exchange, exchange of single vignettes when splitting "
                      "(Fedorov exchange), D-efficiency"), [doe.MEYER_NACHTSHEIM, doe.FEDOROV, doe.KUHFELD])]
    return render_template("editor/vignettes.html", survey=survey, node=node, blocks=blocks, lang=lang,
                           languages=LANGUAGE_NAMES, item=f, kind=f, t=(node.get("texts") or {}).get(lang, {}),
                           new_id=new_id, plan=node.get("plan") or {}, current=vignettes.plan_current(survey, node),
                           universe=vignettes.universe_size(node), allowed=vignettes.allowed_count(survey, node),
                           full_options=vignettes.full_options(survey, node), problems=vignettes.problems(survey, node),
                           n_params=doe.n_params(vignettes.level_counts(node), vignettes.pairs(node)) if node.get("attributes") else 0,
                           structural=survey.is_editable and lang == survey.default_language
                           and survey.can(current_user, "editor"),
                           findings=[x for x in loops.check(survey) if x[3] in {b.id for b in blocks}
                                     or node.get("name", "") in x[1]],
                           preview=preview, doe=doe, vignettes=vignettes, sources=sources, ai_found=ai_found)


@flow_bp.route("/", endpoint="index")
@login_required
def flow_index(survey_id):
    from app import external, flow, loops, panel

    survey = get_survey(survey_id)
    lang = _lang(survey)
    # the upkeep of opening the page is for who may edit; "Ansehen" changes nothing with a GET (audit 2026-10-18,
    # AUTHZ-06): the flow is put in order for the page only, and rolled back after it
    upkeep = survey.can(current_user, "editor")
    if upkeep and survey.is_editable and trash.purge(survey):
        db.session.flush()
    before = survey.flow
    flow.normalize(survey)
    if upkeep and before != survey.flow:
        db.session.commit()
    every = design.check(survey)
    # the flow's own messages (flow.check: flow elements, quota calculation, ends) and those of the completion links
    # (panel.check), whatever the language of the page, besides what is said about a block as a whole; messages on
    # one element of a block are fixed in the questionnaire, where "Alle … Meldungen" leads (UI/UX audit 2026-10-07, W20)
    of_flow = {m for _, m, _ in flow.check(survey)} | {m for _, m in panel.check(survey)}
    findings = [f for f in every if f.message in of_flow or (f.block_id is not None and f.item_id is None)]
    blocks = {b.id: b for b in survey.blocks}
    from app import conditions

    # what a condition can be put on: questions, URL parameters, and what the quota calculations decided
    from app.quotas import RULES as quota_rules

    described = conditions.describe(survey, lang, everything=True)
    from app import diary

    page = render_template(
        "flow/index.html", survey=survey, lang=lang, languages=LANGUAGE_NAMES, root=flow.tree(survey),
        diary=diary, diary_part_label=lambda b: diary.part_label(diary.part_of(b), survey),  # a diary study: its parts
        else_nodes={n.get("of"): n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "else"},
        root_branches={n["id"]: n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "branch"},
        blocks=blocks, editable=survey.is_editable and survey.can(current_user, "editor"), findings=findings,
        all_findings=len(every), all_blockers=len([f for f in every if f.severity == "error" or f.blocks_start]),
        may_link=survey.can(current_user, "admin"),  # completion links of ends: as the links in the settings
        described=described, cond_data=conditions.page_data(described), cond_modes=conditions.MODES, conditions=conditions,
        end_kinds=flow.END_KINDS, end_triggers=flow.END_TRIGGERS, end_cases=flow.cases, end_label=flow.end_label,
        quota_rules=quota_rules, link_modes=panel.LINK_MODES, link_host=panel.link_host, external=external,
        webservice=__import__("app.webservice", fromlist=["nodes"]),
        ws_test=session.pop("ws_test", None) if session.get("ws_test", {}).get("survey") == survey.id else None,
        ws_allowed=__import__("app.experimental", fromlist=["enabled"]).enabled("webservice", user=current_user),
        chips=panel.link_chips(survey), ext_chips=panel.link_chips(survey, [("return", de_en("Rückkehrlink", "Return link"))]),
        quota_cells={q.id: [[c.code, c.name] for c in q.cells] for q in survey.quotas},
        quotas=list(survey.quotas), quota_used={q: n["id"] for n in flow.walk_nodes(survey, "quota") for q in n.get("quotas") or []},
        stats=design.stats(survey), human_count=design.human_count,
        loop_info={b.id: loops.config(b) for b in survey.blocks if loops.node_of(b)},
        loop_shown=lambda node: loops.shown_rows(survey, loops.config_node(node)),
    )
    if not upkeep:
        db.session.rollback()  # the flow put in order for the page only
    return page


def _flow_back(survey, lang, at=None, opened=False):
    """Back to the flow page: at the element that was moved or saved (`at`, scrolled to and marked by app.js;
    a query value, since a dropped element comes back through fetch, which drops #anchors), its settings open."""
    if request.values.get("back") == "editor":  # actions started in the questionnaire editor return there
        return _back(survey, lang, "#split")
    where = {"at": at, **({"open": at} if opened else {})} if at else {}
    return redirect(url_for("flow_page.index", survey_id=survey.id, lang=lang, **where))


def _node_name(survey, node):
    """A flow element in words, for messages and the list of steps: its kind and, if it has one, its name."""
    from app import flow

    if node["t"] == "block":
        return de_en("Block „{name}“", "Block “{name}”", name=flow.child_label(survey, node))
    names = {"loop": ("Schleife", "Loop"), "rand": ("Randomisierung", "Randomisation"), "branch": ("Bedingung", "Condition"),
             "else": ("Sonst-Zweig", "Else branch"), "end": ("Ende", "End"), "quota": ("Quotenberechnung", "Quota calculation"),
             "split": ("Getrennter Abschnitt", "Separated section"), "ext": ("Externer Abschnitt", "External section"),
             "ws": ("Web Service", "Web Service")}
    kind = de_en(*names.get(node["t"], ("Element", "Element")))
    if node.get("name") and node["t"] in ("loop", "rand", "branch", "ext", "ws"):
        return de_en("{kind} „{name}“", "{kind} “{name}”", kind=kind, name=node["name"])
    return kind


@bp.route("/flow/add", methods=["POST"])
@login_required
def flow_add(survey_id):
    from sqlalchemy.orm.attributes import flag_modified

    from app import flow

    """A new flow element: by a click at the end of the regular survey; dragged into the flow at `target` with
    `mode` = before/after/into, or wrap: onto a block, which goes into the new loop, randomizer or condition."""
    survey = get_survey(survey_id)
    from app import experimental

    if request.form.get("kind") == "ws" and not experimental.enabled("webservice", survey=survey):
        abort(403)  # experimental: only for whom it is switched on (app/experimental.py)
    if survey.sampling is not None and request.form.get("kind") in ("end", "ext", "ws", "quota", "split"):
        abort(400)  # a diary study: they do nothing in its parts (runtime.build_layout, only)
    if _require_draft(survey):
        target, mode = request.form.get("target", ""), request.form.get("mode", "")
        step = undo.begin(survey)
        try:
            if not target:
                node = flow.add_node(survey, request.form.get("kind", ""))
            elif mode == "wrap" and survey.consent_item is not None and target == f"k{survey.consent_item.block_id}":
                node = None  # the consent is for everyone and is never repeated
            else:
                node = flow.add_at(survey, request.form.get("kind", ""), target, mode)
        except ValueError as e:
            if str(e) != request.form.get("kind", ""):  # a refusal in words (one separated section only), not an unknown kind
                flash(str(e), "warning")
                return _flow_back(survey, _lang(survey))
            abort(400)
        if node is None:
            db.session.rollback()
            flash(de_en("An dieser Stelle lässt sich das Element nicht einfügen: In den Block der Einwilligung gehört nur die "
                        "Einwilligung. Bitte einen anderen Block wählen.",
                        "The element cannot be inserted here: only the consent belongs in the consent block. Please choose "
                        "another block."), "warning")
            return _flow_back(survey, _lang(survey))
        flag_modified(survey, "flow")
        inside = next((c for c in node.get("children", []) if mode == "wrap"), None)
        step.done(undo.both("Ablauf: {name} hinzugefügt", "Flow: {name} added", name=_node_name(survey, node))
                  + (undo.both(", {name} hineingelegt", ", {name} put inside", name=_node_name(survey, inside)) if inside else ""),
                  page="flow", node=node["id"])
        db.session.commit()
        if target:
            return _flow_back(survey, _lang(survey), node["id"])
    return _flow_back(survey, _lang(survey))


@bp.route("/flow/<nid>/ws-test", methods=["POST"])
@login_required
def flow_ws_test(survey_id, nid):
    """"Testaufruf" of a web service (app/webservice.py): one call with the example values of the form, the answer
    shown under the settings. Admins only, like its address."""
    from app import flow, webservice

    survey = get_survey(survey_id)
    from app import experimental

    # experimental: for whom it is switched on, also in a survey that has the node from a file or a copy (W3)
    if not survey.can(current_user, "admin") or not experimental.enabled("webservice", user=current_user):
        abort(403)
    node, _ = flow.find(flow.tree(survey), nid)
    if node is None or node.get("t") != webservice.NODE or not webservice.usable(node):
        flash(de_en("Erst eine gültige https-Adresse eintragen.", "Enter a valid https address first."), "warning")
        return _flow_back(survey, _lang(survey), nid)
    given = {name: request.form.get(f"test_{name}", "")[:200] for name in webservice.used(node)}
    variables = {**given, "pid": "test-pid", "lang": _lang(survey), "survey": survey.code or ""}
    result = webservice.test(node, variables)
    # small enough for the session cookie (W8)
    session["ws_test"] = webservice.compact({**result, "nid": nid, "survey": survey.id, "given": given})
    return _flow_back(survey, _lang(survey), nid)


@bp.route("/flow/<nid>/<action>", methods=["POST"])
@login_required
@autosave.aware
def flow_node(survey_id, nid, action):
    from sqlalchemy.orm.attributes import flag_modified

    from app import flow

    survey = get_survey(survey_id)
    lang = _lang(survey)
    if not _require_draft(survey):
        return _flow_back(survey, lang)
    step = undo.begin(survey)
    node, _ = flow.find(flow.tree(survey), nid)
    name = _node_name(survey, node) if node else "Element"  # the same word in English
    did = undo.both("Ablauf: {name} verschoben", "Flow: {name} moved", name=name)
    if action in ("up", "down", "in", "out"):
        if not flow.move(survey, nid, action):
            flash(de_en("Diese Verschiebung ist hier nicht möglich: Am Anfang oder Ende einer Gruppe geht es nicht weiter; "
                        "zum Herausnehmen den Pfeil „herausnehmen“ verwenden.",
                        "This move is not possible here: at the start or end of a group it goes no further; to take it "
                        "out, use the arrow “take out”."), "warning")
            db.session.rollback()  # nothing of the refused move is stored
            return _flow_back(survey, lang, nid)
    elif action == "place":
        if not flow.place(survey, nid, request.form.get("target", ""), request.form.get("mode", "after")):
            flash(_CANNOT_PLACE(), "warning")
            db.session.rollback()
            return _flow_back(survey, lang, nid)
    elif action == "update":
        did = undo.both("Ablauf: {name} geändert", "Flow: {name} changed", name=name)
        node, _ = flow.find(flow.tree(survey), nid)
        if edit_guard.check(survey, f"flow:{nid}", edit_guard.node_state(node), request.form):
            c = g.edit_conflict
            flash((de_en("Nicht gespeichert: {who} hat diese Einstellungen um {when} Uhr geändert, nachdem Sie die Seite "
                         "geöffnet haben.", "Not saved: {who} changed these settings at {when} after you opened the page.",
                         who=c["who"], when=c["when"]) if c["when"] else
                   de_en("Nicht gespeichert: {who} hat diese Einstellungen geändert, nachdem Sie die Seite geöffnet haben.",
                         "Not saved: {who} changed these settings after you opened the page.", who=c["who"]))
                  + de_en(" Unten steht jetzt der gespeicherte Stand; bitte prüfen und bei Bedarf erneut ändern.",
                          " Below is the saved state now; please check it and change it again if needed."), "error")
            return _flow_back(survey, lang, nid, opened=True)
        error = flow.update(survey, nid, request.form, survey.languages, links=survey.can(current_user, "admin"))
        if not error:
            edit_guard.saved(survey, f"flow:{nid}")
        flash(error or de_en("Gespeichert.", "Saved."), "error" if error else "success")
    elif action == "delete":
        did = undo.both("Ablauf: {name} entfernt", "Flow: {name} removed", name=name)
        if not flow.remove(survey, nid):
            abort(400)
        # no question before removing: "Rückgängig" brings the element back with its settings
        flash(de_en("{name} entfernt. Enthaltene Blöcke bleiben erhalten.", "{name} removed. Blocks inside it are kept.",
                    name=name), "undo")
    elif action == "else":
        did = undo.both("Ablauf: {name} um „Sonst“ ergänzt", "Flow: “Else” added to {name}", name=name)
        if not flow.add_else(survey, nid):
            flash(de_en("Diese Bedingung hat schon ein „Sonst“.", "This condition already has an “Else”."), "warning")
    else:
        abort(400)
    flag_modified(survey, "flow")
    step.done(did, page="flow", node=nid,
              merge=autosave.merge(f"flow:{nid}") if action == "update" else None)
    db.session.commit()
    after, _ = flow.find(flow.tree(survey), nid)
    if after is not None:
        autosave.base(edit_guard.node_state(after))
        if action == "update" and after.get("t") == "ext" and survey.can(current_user, "admin"):
            from app import external

            # the key drawn when signing was turned on goes into its (still empty) field on the page
            if external.sign_key(after):
                autosave.fill({"sign_key_shown": external.sign_key(after)})
    return _flow_back(survey, lang, None if action == "delete" else nid, opened=action == "update")
