"""The researcher's preview of a survey and its comments."""
from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import access, item_condition, undo
from app.editor.base import _block_name, _get_block, _is_consent_block, _lang, _require_draft, bp
from app.extensions import db
from app.lang import de_en
from app.surveys import get_survey


PREVIEW_KEY = "preview_responses"


def _preview_response(survey, restart=False, on_phone=False):
    """The researcher's current preview run (a Response with is_preview=True). on_phone: over the phone link,
    which works without signing in; there the number of new test runs per day is limited (app/limits.py)."""
    from flask import session

    from app.models import Response

    if not on_phone and access.showcase_only(current_user, survey):
        from app import preview_state

        return preview_state.response(survey, _lang(survey), restart=restart)
    runs = dict(session.get(PREVIEW_KEY, {}))
    previous = db.session.get(Response, runs.get(str(survey.id), 0))
    response = None if restart else previous
    if response is None or response.survey_id != survey.id or not response.is_preview:
        if previous is not None and previous.survey_id == survey.id and previous.source == "test" \
                and not survey.keep_test_runs:
            db.session.delete(previous)  # test runs are only kept if the survey says so
        if on_phone:
            from app import limits

            if not limits.allow_phone_run(survey):
                db.session.commit()
                abort(429, "Über diesen Link wurden heute schon sehr viele Testdurchläufe gestartet. Bitte später erneut.")
        response = Response(survey_id=survey.id, is_preview=True, source="test", language=_lang(survey), answers={},
                            qversion=survey.qversion)
        db.session.add(response)
        db.session.commit()
        runs[str(survey.id)] = response.id
        session[PREVIEW_KEY] = runs
    return response


@bp.route("/preview", methods=["GET", "POST"])
@login_required
def preview_survey(survey_id):
    """Test run of the whole questionnaire with all checks active (answers are never counted)."""
    survey = get_survey(survey_id)
    from app import theme

    theme.preview_side(survey)  # ?scheme=light|dark|auto from the preview bar: remembered before any redirect
    # an example survey seen only as one (access.showcase_only): no comments, they would mix the notes of everyone
    may_comment = not access.showcase_only(current_user, survey)
    return _run_preview(survey, _lang(survey), lambda **kw: url_for("editor.preview_survey", survey_id=survey.id, **kw),
                        on_phone=False,
                        comment_url=url_for("editor.preview_comment", survey_id=survey.id) if may_comment else None)


@bp.route("/preview/comment", methods=["POST"])
@login_required
def preview_comment(survey_id):
    """A comment from the editor preview (any member, also "Ansehen")."""
    from app import comment_shots, comments

    survey = get_survey(survey_id)
    if access.showcase_only(current_user, survey):
        abort(403)
    try:
        comment, error = comments.add(survey, request.form, current_user.display_name, "editor",
                                      page=request.form.get("page", type=int), author_id=current_user.id,
                                      files=request.files)
    except comment_shots.Refused as e:
        return {"error": str(e)}, e.status
    return ({"error": error}, 400) if error else comments.as_dict(comment)


@bp.route("/preview/comment/<int:comment_id>/shot")
@login_required
def preview_comment_shot(survey_id, comment_id):
    """The image of a comment, for every member of the survey (preview and comments page); not for who sees an
    example survey only as one: the team's notes stay with the team (access.showcase_only; audit 2026-10-11, M6)."""
    survey = get_survey(survey_id)
    if access.showcase_only(current_user, survey):
        abort(404)
    return _comment_shot(survey, comment_id)


def _comment_shot(survey, comment_id):
    from flask import send_file

    from app import comment_shots
    from app.models import PreviewComment

    comment = db.session.get(PreviewComment, comment_id)
    where = comment_shots.path(survey.uid, comment.shot) if comment is not None and comment.survey_id == survey.id else None
    if where is None:
        abort(404)
    response = send_file(where, mimetype="image/png" if where.endswith(".png") else "image/jpeg")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/preview/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def preview_comment_delete(survey_id, comment_id):
    from app import comments

    survey = get_survey(survey_id)
    return ({"ok": True}) if comments.delete_own(survey, comment_id) else ({"error": "nicht möglich"}, 403)


@bp.route("/comments")
@login_required
def comments_page(survey_id):
    from app import comments

    survey = get_survey(survey_id)
    if access.showcase_only(current_user, survey):  # the team's notes on an example survey stay with the team
        abort(404)
    show = request.args.get("show", "open")
    rows = comments.all_for(survey)
    shown = [c for c in rows if show == "all" or c.status == "open"]
    return render_template("editor/comments.html", survey=survey, comments=shown, show=show, lang=_lang(survey),
                           n_open=sum(1 for c in rows if c.status == "open"), n_all=len(rows),
                           labels=_comment_status_labels(), can_edit=survey.can(current_user, "editor"),
                           env_label=__import__("app.comment_shots", fromlist=["env_label"]).env_label,
                           any_shots=any(c.shot for c in rows))


@bp.route("/comments/<int:comment_id>", methods=["POST"])
@login_required
def handle_comment(survey_id, comment_id):
    from app import comments
    from app.models import PreviewComment

    survey = get_survey(survey_id)
    comment = db.session.get(PreviewComment, comment_id)
    if comment is None or comment.survey_id != survey.id:
        abort(404)
    if request.form.get("remove_shot"):
        comments.remove_shot(survey, comment)
        flash(de_en("Das Bild ist entfernt; der Kommentar bleibt.", "The image is removed; the comment stays."), "success")
    else:
        comments.handle(comment, request.form.get("status", ""), current_user, request.form.get("note", ""))
    return redirect(url_for("editor.comments_page", survey_id=survey.id, show=request.form.get("show", "open"))
                    + f"#comment-{comment.id}")


def _comment_status_labels():
    """The states of a comment in the language of the page (the German ones are comments.STATUS_LABELS)."""
    from app import comments

    return comments.status_labels()
# a pretest link for test persons without an account (UI audit 2026-10-04, R11): the same signed link, valid for a
# chosen number of days; "alle ungültig machen" raises a counter in survey.panel that every link carries
PRETEST_DAYS = (1, 7, 30)


def _share_serializer():
    from flask import current_app
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="preview-share")


def pretest_generation(survey):
    return int((survey.panel or {}).get("pretest_gen") or 0)


def share_url(survey, days=1):
    """Signed link to this survey's preview, valid `days` days (the phone link of the preview: 1), bound to the
    current researcher and to the survey's current generation of pretest links."""
    from app.hosts import participant_base

    days = days if days in PRETEST_DAYS else 1
    data = {"s": survey.id, "user": current_user.id, "d": days, "g": pretest_generation(survey)}
    if access.showcase_only(current_user, survey):
        data.update(d=1, x=1)  # the phone link of someone who sees an example survey only as one: valid while it is one
    token = _share_serializer().dumps(data)
    return participant_base() + url_for("preview_share.run", token=token)


def qr_svg(url):
    import segno

    # omitsize: viewBox only, so the SVG scales with its container
    return segno.make(url, error="m").svg_inline(scale=4, border=2, dark="#1c1c1a", omitsize=True)


def _run_preview(survey, lang, url, on_phone, comment_url=None):
    """Shared by the editor preview and the phone link. GET ?page=N jumps freely (bar);
    POST is the participant's Back/Next through runtime.step, as for real participants."""
    from flask import session

    from app import runtime

    if not runtime.pages(survey):
        flash(de_en("Der Fragebogen ist noch leer.", "The questionnaire is still empty."), "error")
        return redirect(url_for("editor.index", survey_id=survey.id))
    if request.args.get("device") in ("desktop", "mobile") and not on_phone:
        session["preview_device"] = request.args["device"]
    device = "desktop" if on_phone else session.get("preview_device", "desktop")
    from app.items import geolocation, voice

    if voice.used(survey):
        voice.allow_microphone()  # the page (and the frame of the phone view) may record
    if geolocation.used(survey) or any(i.type == "map_marker" for b in survey.blocks for i in b.items):
        geolocation.allow()  # the page (and the frame of the phone view) may ask for the position
    if comment_url:
        g.display_capture = True  # the comment box may take a picture of the page (static/comment_shot.js)
    if request.args.get("validate") in ("0", "1"):
        session["preview_validate"] = request.args["validate"] == "1"
    validate = session.get("preview_validate", True)
    # texts that fall back to the default language are marked "noch nicht übersetzt" (only here, S9); a switch in the bar
    if request.args.get("untranslated") in ("0", "1"):
        session["preview_untranslated"] = request.args["untranslated"] == "1"
    mark_untranslated = session.get("preview_untranslated", True)
    framed = request.args.get("frame") == "1"  # the page inside the phone frame
    # URL parameters can be simulated in the preview, e.g. …/preview?group=bio
    from app import panel as panel_mod

    pv_key = f"pvp{survey.id}"
    declared = [p["name"] for p in panel_mod.config(survey)["params"]]
    if any(n in request.args for n in declared):
        session[pv_key] = {n: panel_mod.clean_value(request.args[n]) for n in declared if request.args.get(n)}

    def here():
        # the language switch of the page (participant/_langs.html) sends the page along, as in a participation
        wanted = request.form.get("set_lang") if request.method == "POST" else None
        return url(lang=wanted if wanted in (survey.languages or []) else lang, **({"frame": 1} if framed else {}))

    # quotas can be tried out as full: …/preview?qfull=<node id>&on=1 (a new test run, since a calculation
    # decides once per run). The switch belongs to the quota calculation; an end "Quote voll" that stands
    # before the calculation of one of its quotas looks for itself and gets a switch of its own
    from app import flow
    from app import quotas as quota_mod

    pq_key = f"pvq{survey.id}"
    quota_nodes, calculated = [], set()
    for n, _, _ in flow.walk(flow.tree(survey)):
        if n["t"] == "quota" and n.get("quotas"):
            quota_nodes.append(n)
            calculated.update(n["quotas"])
        elif n["t"] == "end" and set(quota_mod.end_quotas(n)) - calculated:
            quota_nodes.append(n)
    if request.args.get("qfull") in {n["id"] for n in quota_nodes}:
        full = set(session.get(pq_key, [])) - {request.args["qfull"]}
        if request.args.get("on") == "1":
            full.add(request.args["qfull"])
        session[pq_key] = sorted(full)
    quota_sim = set(session.get(pq_key, [])) & {n["id"] for n in quota_nodes}
    # the IP region can be tried out as well: …/preview?geo=DE-5 (a new test run: it is looked up once per run)
    from app import geoip

    pg_key = f"pvg{survey.id}"
    if "geo" in request.args and getattr(survey, "geo_ip", False):
        session[pg_key] = request.args["geo"] if geoip.simulated(request.args["geo"]) is not None else ""
    geo_sim = session.get(pg_key, "") if getattr(survey, "geo_ip", False) else ""
    # a diary study: …/preview?part=m1 starts a test run of one part (entry survey, a short survey, exit survey) as a
    # person gets it; the run keeps it (Response.occasion), a new run without it shows the whole questionnaire
    from app import diary

    part = request.args.get("part", "") if diary.is_diary(survey) and request.args.get("part") in diary.part_keys(survey) else ""
    # the whole study as a test run (pre-test 2026-10-16): one run per part, in the order of a person's study, the
    # short surveys several times; the end page of each run leads on to the next (study_next below)
    study_key = f"pvst{survey.id}"
    study_step = request.args.get("study", type=int)
    if diary.is_diary(survey):
        plan = (session.get(study_key) or {}).get("plan") or []
        if study_step is not None and part and 0 <= study_step < len(plan) and plan[study_step] == part:
            session[study_key] = {"plan": plan, "at": study_step}
        elif part:
            session.pop(study_key, None)  # one part on its own ("Vorschau dieses Teils")
        elif request.method == "GET" and not any(k in request.args for k in ("page", "block", "qfull", "geo"))                 and (request.args.get("restart") == "1" or not _diary_run_going(survey, on_phone)):
            passes = request.args.get("passes", type=int) or diary.STUDY_PASSES
            plan = diary.study_run(survey, min(max(passes, 1), 10))
            if plan:
                session[study_key] = {"plan": plan, "at": 0}
                return redirect(url(lang=lang, part=plan[0], study=0, **({"frame": 1} if request.args.get("frame") == "1" else {})))
    restart = request.args.get("restart") == "1" or "qfull" in request.args or "geo" in request.args or "part" in request.args
    if restart:
        from app import comments

        comments.forget_run(survey)  # a new test run: earlier comments can no longer be deleted here
    response = _preview_response(survey, restart=restart, on_phone=on_phone)
    if part and (response.occasion or {}).get("part") != part and not response.layout:
        response.occasion = {"part": part}  # runtime.build_layout shows the blocks of this part only (diary.only_blocks)
        db.session.commit()
    runtime.ensure_layout(survey, response)
    pages = runtime.page_entries(response.layout)
    count = len(pages)
    if request.args.get("block", type=int) and pages:
        # jump to a block's first page in this run; if randomisation left the block out, draw a new run with it
        wanted = request.args.get("block", type=int)

        def first_page():
            return next((n for n, pos in enumerate(pages) if response.layout["pages"][pos].get("block") == wanted), None)

        n = first_page()
        for _ in range(30):
            if n is not None:
                break
            response = _preview_response(survey, restart=True, on_phone=on_phone)
            runtime.ensure_layout(survey, response)
            pages = runtime.page_entries(response.layout)
            n = first_page()
        if n is None:
            flash(de_en("Dieser Block kommt im Testdurchlauf nicht vor (z. B. leer oder durch den Ablauf ausgeschlossen).",
                        "This block does not occur in the test run (e.g. empty or excluded by the flow)."), "error")
        else:
            response.position, response.status = pages[n], "active"
        db.session.commit()
        return redirect(here())
    page = request.args.get("page", type=int)  # anything that is no number: as if not given
    if page is not None and pages:
        # the bar jumps to any page, ignoring conditions
        response.position = pages[min(max(page - 1, 0), count - 1)]
        response.status = "active"
        db.session.commit()
        if framed or device != "mobile":
            return redirect(here())
    db.session.commit()

    # "jump to block" in the bar: first page of every block (and loop pass) in this test run's order
    blocks = {b.id: b for b in survey.blocks}
    jump, prev = [], None
    for n, pos in enumerate(pages, start=1):
        spec = response.layout["pages"][pos]
        key = (spec.get("block"), spec.get("iter"))
        if key != prev and (spec.get("iter") or 1) == 1:  # loops: only their entry, not every pass
            b = blocks.get(spec.get("block"))
            jump.append({"page": n, "title": (b.title(lang) if b else "") or "Block", "loop": bool(spec.get("iter"))})
        prev = key
    from app import comments as comments_mod

    common = dict(survey=survey, lang=lang, device=device, validate=validate, on_phone=on_phone, jump=jump,
                  preview=True, mark_untranslated=mark_untranslated,
                  quota_nodes=quota_nodes, quota_sim=quota_sim, geo_sim=geo_sim,
                  comment_url=comment_url, my_comments=comments_mod.mine(survey))
    if device == "mobile" and not framed and (on_phone or not access.showcase_only(current_user, survey)):
        position = response.position if response.position in pages else pages[0]
        block, page_items = runtime.page_at(survey, response, position)
        phone_link = share_url(survey)
        from app import comments

        common.update(page_items=page_items, page_comments=comments.for_page(survey, block, page_items))
        return render_template(
            "participant/preview_device.html", frame_src=url(lang=lang, frame=1), page=pages.index(position) + 1,
            page_count=count, block=block, phone_link=phone_link, phone_qr=qr_svg(phone_link), **common,
        )

    g.participation = response  # for elements that store files with the participation (items/voice.py)
    result = runtime.step(survey, response, request.form, lang, request.method, validate=validate,
                          params=session.get(pv_key, {}), quota_full=quota_sim, geo=geoip.simulated(geo_sim))
    db.session.commit()
    if result.external:
        from app import external

        # an external section: the address it would open, filled with the test run's values; never followed here
        node = result.external["node"]
        forward = external.target(
            survey, response, node,
            {**session.get(pv_key, {}), **runtime.builtin_variables(response, survey), **panel_mod.extra_values(survey, response)},
            return_link="(Rückkehrlink-nur-im-Feld)")
        return render_template("participant/external.html", response=response, page_count=count, framed=framed,
                               node=node, forward=forward, text=(node.get("text") or {}).get(lang) or "",
                               values=external.values(node), kinds=external.KINDS, auto_seconds=external.AUTO_SECONDS,
                               page_token=result.external["token"], **common)
    if result.redirect:
        return redirect(here())
    if result.finished:
        from app import panel
        from app.participate import end_node, finished_text

        # the completion link this end would open, filled with the test run's values; never followed here
        forward, mode = panel.target(
            survey, response.status,
            {**session.get(pv_key, {}), **runtime.builtin_variables(response, survey), **panel.extra_values(survey, response)},
            end_node(survey, response),
            {**{f"p:{k}": v for k, v in session.get(pv_key, {}).items()}, **quota_mod.view(survey, response)},
            signed=False)
        # the end page as participants see it (W47): the deletion code (an example) or the date until which the
        # browser can delete, and the result dashboards after a completion
        from flask import current_app

        from app import boards, erasure
        from app.timeutil import local_today
        from app.models import Response
        from app.ui_i18n import date_text

        example_code = erasure.example(survey) if getattr(survey, "erase_code", False) else None
        study_next = None  # the test run of the whole diary study: the next part (pre-test 2026-10-16)
        study = session.get(study_key) or {}
        plan, at = study.get("plan") or [], study.get("at")
        if isinstance(at, int) and 0 <= at < len(plan) and (response.occasion or {}).get("part") == plan[at]:
            study_next = {"k": at + 1, "n": len(plan),
                          "url": url(lang=lang, part=plan[at + 1], study=at + 1, **({"frame": 1} if framed else {}))
                          if at + 1 < len(plan) else None,
                          "label": diary.part_label(plan[at + 1], survey) if at + 1 < len(plan) else ""}
        return render_template("participant/end.html", response=response, page_count=count, framed=framed,
                               text=finished_text(survey, response, lang, bool(example_code)), forward=forward,
                               forward_mode=mode if mode in panel.LINK_MODES else "", status=response.status,
                               erase_code=example_code, study_next=study_next,
                               erase_until=None if example_code else date_text(
                                   local_today() + current_app.config["PERMANENT_SESSION_LIFETIME"], lang),
                               boards=boards.end_links(survey, lang) if response.status == Response.COMPLETED else [],
                               **common)
    from app import comments

    page_comments = comments.for_page(survey, result.context["block"], result.context["page_items"])
    # the page of the participation itself (participant/page.html): the preview adds only its bar and the comments (S9)
    # the questions on this page shown only to some ("Nur zeigen, wenn …", app/item_condition.py): marked for the tester
    item_conds = {i.id: item_condition.text(i, lang, shown=True) for i in result.context["page_items"] if item_condition.of(i)}
    return render_template("participant/page.html", whole_survey=True, framed=framed, page_comments=page_comments,
                           item_conds=item_conds, **common, **result.context)


@bp.route("/blocks/<int:block_id>/preview")
@login_required
def preview_block(survey_id, block_id):
    """Start the test run at this block's first page."""
    survey = get_survey(survey_id)
    block = _get_block(survey, block_id)
    # the page number depends on this test run's order (flow, loops, randomisation): the preview looks it up
    from app import diary

    if diary.is_diary(survey):  # a diary study: the preview of the block's part (one part per test run)
        return redirect(url_for("editor.preview_survey", survey_id=survey.id, lang=_lang(survey), block=block.id,
                                part=diary.part_of(block)))
    return redirect(url_for("editor.preview_survey", survey_id=survey.id, lang=_lang(survey), block=block.id))


# --- Loops (stimulus table of a block) -------------------------------------------------------


@bp.route("/blocks/<int:block_id>/loop", methods=["GET", "POST"])
@login_required
def loop_table(survey_id, block_id):
    """"🔁 Schleife" on a block: open its loop on the Ablauf page, or (POST) put the block into a new loop."""
    from app import flow, loops

    survey = get_survey(survey_id)
    block = _get_block(survey, block_id)
    lang = _lang(survey)
    if _is_consent_block(block):
        flash(de_en("Die Einwilligung kann nicht wiederholt werden. Richten Sie die Schleife am Block mit Ihren Fragen ein.",
                    "The consent cannot be repeated. Set up the loop on the block with your questions."), "warning")
        return redirect(url_for("editor.index", survey_id=survey.id, lang=lang))
    node = loops.node_of(block)
    if node is None:
        if request.method != "POST" or not _require_draft(survey):
            return redirect(url_for("flow_page.index", survey_id=survey.id, lang=lang))
        step = undo.begin(survey)
        nid = loops.wrap_block(survey, block)
        flow.normalize(survey)
        step.done(undo.both("Schleife um „{name}“ angelegt", "Loop around “{name}” created", name=_block_name(block)),
                  page="flow", node=nid)
        db.session.commit()
        return redirect(url_for("flow_page.loop_node", survey_id=survey.id, nid=nid, lang=lang))
    return redirect(url_for("flow_page.loop_node", survey_id=survey.id, nid=node["id"], lang=lang))


def _diary_run_going(survey, on_phone):
    """Whether this browser has a test run of one part of the diary study (under way or at its end page) that a
    plain preview link shows on (the bar, a reload, the end page after the last answer) instead of starting the whole
    study anew; "Neuer Testdurchlauf" (restart=1) starts it anew."""
    from flask import session

    from app.models import Response

    rid = (session.get(PREVIEW_KEY) or {}).get(str(survey.id))
    row = db.session.get(Response, rid) if isinstance(rid, int) else None
    return bool(row is not None and row.survey_id == survey.id and (row.occasion or {}).get("part"))
