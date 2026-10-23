"""The pages of the designer and the shared links (blueprints boards and board_share)."""
import secrets
from datetime import timedelta

from flask import abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import autosave
from app.boards.base import (AFTER_FIELD, MAX_BOARDS, MAX_SHARES, MAX_TEXT, MAX_TILES, MAX_VALUE_SHARES, MIN_CELL,
                             MIN_TILE, TEMPLATES, TILE_MINS, UNLIMITED, _question, _tile, bp, expiry, language,
                             min_tile_of, share_bp, speaking, starts, template_notes, templates, text_kinds, validity,
                             widths)
from app.boards.compute import forget, sample, state
from app.boards.render import _board, _live, _page, _survey, embed_code, end_share, live_shares, share_url
from app.boards.tiles import SPLIT_SHAPES, _known, candidates, from_form, is_text, split_vars, template_tiles, tiles_of
from app.extensions import db
from app.lang import de_en
from app.models import Board, BoardShare, utcnow


@bp.route("/")
@login_required
def index(survey_id):
    survey = _survey(survey_id)
    return render_template("boards/index.html", survey=survey, templates=templates(), notes=template_notes(), max_boards=MAX_BOARDS,
                           min_tile=MIN_TILE, min_cell=MIN_CELL,
                           live={b.id: len(live_shares(b)) for b in survey.boards}, tiles={b.id: len(tiles_of(b)) for b in survey.boards})


@bp.route("/new", methods=["POST"])
@login_required
def new(survey_id):
    survey = _survey(survey_id, need="editor")
    if len(survey.boards) >= MAX_BOARDS:
        flash(de_en("Höchstens {n} Dashboards je Befragung; bitte zuerst eines löschen.", "At most {n} dashboards per survey; please delete one first.",
                    n=MAX_BOARDS), "error")
        return redirect(url_for("boards.index", survey_id=survey.id))
    template = request.form.get("template") if request.form.get("template") in TEMPLATES else "empty"
    name = " ".join(request.form.get("name", "").split())[:200] or (
        templates()[template] if template != "empty" else f"Dashboard {len(survey.boards) + 1}")
    from app import undo

    step = undo.begin(survey)  # "Rückgängig" in a draft (app/undo.py: part "board:<id>")
    board = Board(name=name, tiles=template_tiles(survey, template), settings={},
                  position=max((b.position for b in survey.boards), default=-1) + 1)
    survey.boards.append(board)
    db.session.flush()
    step.done(undo.both("Dashboard „{name}“ angelegt", "Dashboard “{name}” created", name=board.name), page="boards", node=str(board.id))
    db.session.commit()
    return redirect(url_for("boards.designer", survey_id=survey.id, board_id=board.id))


def _designer_context(survey, board):
    tiles = tiles_of(board)
    known = _known(survey)
    groups = candidates(survey)
    return dict(survey=survey, board=board, tiles=tiles, known=known, groups=groups, picked={t["key"] for t in tiles},
                tile_mins=TILE_MINS, board_min_tile=min_tile_of(board), min_tile_default=MIN_TILE, min_cell=MIN_CELL,
                can_edit=may_change(survey, board), locked=survey.can(current_user, "editor") and not may_change(survey, board),
                can_share=survey.can(current_user, "admin"),
                shares=live_shares(board), validity=validity(), unlimited=UNLIMITED, expiry=expiry, split_shapes=SPLIT_SHAPES,
                embed_code=embed_code, end_share=end_share(board),
                providers=__import__("app.quotas", fromlist=["providers"]).providers(survey),
                groupers=[(key, _question(var.item, survey.default_language) if key.startswith("item:") else var.label)
                          for key, var in split_vars(survey).items()], max_tiles=MAX_TILES, widths=widths(), starts=starts(),
                text_kinds=text_kinds(), max_text=MAX_TEXT,
                gone=len([t for t in board.tiles or [] if isinstance(t, dict) and t.get("key") not in known and not is_text(t.get("key"))]))


def may_change(survey, board):
    """Who may change what the dashboard shows: the role "Bearbeiten", and "Admin" as soon as a valid link shows
    it without signing in. What leaves the survey is decided by its admins, also after the link was made."""
    return survey.can(current_user, "admin" if live_shares(board) else "editor")


@bp.route("/<int:board_id>", methods=["GET", "POST"])
@login_required
@autosave.aware
def designer(survey_id, board_id):
    """The designer: what is on the dashboard and how it is shown. The form saves by itself (app/autosave.py; POST,
    role "Bearbeiten"); the page gets the form anew with the answer and reloads the preview next to it."""
    survey = _survey(survey_id, need="editor" if request.method == "POST" else None)
    board = _board(survey, board_id)
    if request.method == "POST" and not may_change(survey, board):
        flash(de_en("Dieses Dashboard hat gültige Links. Ändern kann es nur, wer die Links widerrufen darf (Rolle „Admin“).",
                    "This dashboard has valid links. Only those who may revoke the links can change it (role “Admin”)."), "error")
        return redirect(url_for("boards.designer", survey_id=survey.id, board_id=board.id))
    if request.method == "POST":
        from app import edit_guard, undo

        # two people in the designer: a save based on an outdated page is not applied (code review 2026-10-15, I3)
        key = f"board:{board.id}"
        board = edit_guard.lock(board) or abort(404)  # overlapping saves one after the other (audit 2026-10-23, S-02)
        conflict = edit_guard.check(survey, key, edit_guard.board_state(board), request.form)
        if conflict:
            from_form(board, request.form)
            return edit_guard.show_again(lambda: render_template("boards/designer.html", **_designer_context(survey, board)))
        step = undo.begin(survey)
        from_form(board, request.form)
        edit_guard.saved(survey, key)
        autosave.base_later(lambda: edit_guard.board_state(board))
        what = autosave.what()
        step.done(undo.both("Dashboard „{name}“: {what} geändert", "Dashboard “{name}”: {what} changed", name=board.name, what=what) if what
                  else undo.both("Dashboard „{name}“ geändert", "Dashboard “{name}” changed", name=board.name), page="boards",
                  node=str(board.id), merge=autosave.merge(f"board:{board.id}"))
        db.session.commit()
        _saved(board)
        if autosave.wanted():  # which rows there are and what they offer is decided here: the page takes the form from us
            autosave.redraw(render_template("boards/_form.html", **_designer_context(survey, board)))
        return redirect(url_for("boards.designer", survey_id=survey.id, board_id=board.id))
    return render_template("boards/designer.html", **_designer_context(survey, board))


@bp.route("/<int:board_id>/delete", methods=["POST"])
@login_required
def delete(survey_id, board_id):
    survey = _survey(survey_id, need="editor")
    board = _board(survey, board_id)
    if live_shares(board) and not survey.can(current_user, "admin"):
        flash(de_en("Dieses Dashboard hat gültige Links. Löschen kann es nur, wer die Links widerrufen darf (Rolle „Admin“).",
                    "This dashboard has valid links. Only those who may revoke the links can delete it (role “Admin”)."), "error")
        return redirect(url_for("boards.index", survey_id=survey.id))
    from app import undo

    step = undo.begin(survey)
    forget(board)
    survey.boards.remove(board)
    step.done(undo.both("Dashboard „{name}“ gelöscht", "Dashboard “{name}” deleted", name=board.name), page="boards", node=str(board.id))
    db.session.commit()
    flash(de_en("Dashboard „{name}“ gelöscht; seine Links zeigen nichts mehr.", "Dashboard “{name}” deleted; its links show nothing any more.",
                name=board.name), "success")
    return redirect(url_for("boards.index", survey_id=survey.id))


FRESH = "board_fresh"  # session: dashboards saved in the designer whose next look of the member's view is fresh


def _saved(board):
    """The designer saved the dashboard: the next state the member asks for (the preview reloads) is made
    without waiting for BOARD_CHECK_SECONDS, in whatever server process it lands."""
    session[FRESH] = sorted(set(session.get(FRESH) or []) | {board.id})[-20:]


def _member_state(board):
    """The state for a signed-in member: as a link shows it, but the newest (a file of its own, kept apart from
    the links); with ?beispiel=1 (the designer's preview) tiles that have too few answers show invented numbers,
    marked as examples. Looked at as often as BOARD_CHECK_SECONDS allows, as for a link (H4 of the security audit
    of 2026-10-03: every member's view used to compute afresh); fresh once right after saving in the designer."""
    waiting = session.get(FRESH) or []
    if board.id in waiting:
        session[FRESH] = [b for b in waiting if b != board.id]
    fresh = state(board, fresh=board.id in waiting, lang=language(board.survey), live=True)
    if request.args.get("beispiel") == "1":
        with speaking(fresh.get("lang")):
            fresh = {**sample(fresh), "sampled": True}
    return fresh


@bp.route("/<int:board_id>/view")
@login_required
def view(survey_id, board_id):
    """The dashboard as a link shows it, for the members of the survey (every role)."""
    survey = _survey(survey_id)
    board = _board(survey, board_id)
    live_url = url_for("boards.live", survey_id=survey.id, board_id=board.id, beispiel=request.args.get("beispiel") or None,
                       lang=request.args.get("lang") or None)
    return _page(board, _member_state(board), live_url, hall=request.args.get("hoersaal") == "1",
                 frame=request.args.get("beispiel") == "1",
                 arrange=request.args.get("beispiel") == "1" and survey.can(current_user, "editor"),
                 images=not request.args.get("beispiel") and request.args.get("hoersaal") != "1")


@bp.route("/<int:board_id>/state")
@login_required
def live(survey_id, board_id):
    survey = _survey(survey_id)
    board = _board(survey, board_id)
    return _live(board, _member_state(board))


# --- links ----------------------------------------------------------------------------------------------


@bp.route("/<int:board_id>/link", methods=["POST"])
@login_required
def share_add(survey_id, board_id):
    survey = _survey(survey_id, need="admin")
    board = _board(survey, board_id)
    back = redirect(url_for("boards.designer", survey_id=survey.id, board_id=board.id) + "#links")
    if len([s for s, _ in live_shares(board) if not s.param]) >= MAX_SHARES:
        flash(de_en("Höchstens {n} Links zugleich; bitte zuerst einen widerrufen.", "At most {n} links at a time; please revoke one first.",
                    n=MAX_SHARES), "error")
        return back
    from app import quotas

    only = quotas.provider(survey, request.form.get("qp", ""))
    if request.form.get("qp") and only is None:
        flash(de_en("Diesen Anbieter gibt es nicht (mehr) unter den gespeicherten URL-Parametern.",
                    "This provider is not (or no longer) among the stored URL parameters."), "error")
        return back
    days, expires = _validity(survey, request.form.get("valid", "field"))
    share = BoardShare(token=secrets.token_urlsafe(24), label=request.form.get("label", "").strip()[:100],
                       hall=request.form.get("hall") == "1", expires_at=expires, revoked=False, opens=0,
                       valid="field" if days == "field" else None,
                       param=only[0] if only else "", value=only[1] if only else "")
    board.shares.append(share)
    at_end = request.form.get("end") == "1"
    if at_end and (share.hall or share.param):
        flash(de_en("Auf der Endseite steht ein gewöhnlicher Link: weder Hörsaal-Modus noch der Link eines Anbieters. "
                    "Der Link wurde angelegt, die Endseite zeigt ihn nicht.",
                    "The end page carries an ordinary link: neither lecture hall mode nor the link of a provider. "
                    "The link was created; the end page does not show it."), "warning")
        at_end = False
    if at_end:  # one link per dashboard: a new one takes the place of the one before
        db.session.flush()
        share.label = share.label or de_en("Endseite der Befragung", "End page of the survey")
        board.settings = {**(board.settings or {}), "end_share": share.id}
    db.session.commit()
    flash(de_en("Link angelegt. Er zeigt nur zusammengefasste Zahlen und lässt sich jederzeit widerrufen.",
                "Link created. It shows only summarised figures and can be revoked at any time.")
          + (de_en(" Wer die Befragung abschließt, findet ihn auf der Endseite.", " Anyone who completes the survey finds it on the end page.")
             if at_end else ""), "success")
    return back


def _validity(survey, days):
    """(days, expires_at) of a new link: "never", "7", "30", "90", or "field" (until after the field end, else 30
    days)."""
    if days == "never":
        return days, UNLIMITED
    if days in ("7", "30", "90"):
        return days, utcnow() + timedelta(days=int(days))
    if survey.field_end and survey.field_end > utcnow():
        return "field", survey.field_end + timedelta(days=AFTER_FIELD)
    return "field", utcnow() + timedelta(days=30)


def value_share_urls(survey):
    """{(URL parameter, value): address} of a working dashboard link for each value that has one (the first
    dashboard's, the newest link), for the table of links per value on "Datenerhebung"."""
    now = utcnow()
    out = {}
    for board in survey.boards:
        for share in sorted(board.shares, key=lambda s: s.id, reverse=True):
            if share.param and not share.revoked and expiry(share) > now:
                out.setdefault((share.param, share.value), share_url(share))
    return out


def add_value_shares(survey, board, param):
    """A link of `board` for each allowed value of the stored parameter `param` (its config dict) that has none
    there yet, valid until after the field end. Returns (added, error); the caller commits."""
    from app import panel

    name = param["name"]
    live = [s for s, _ in live_shares(board) if s.param]
    have = {s.value for s in live if s.param == name}
    missing = [v for v in param["allowed"] if v not in have]
    if len(live) + len(missing) > MAX_VALUE_SHARES:
        return 0, de_en("Höchstens {n} Links je Wert an einem Dashboard.", "At most {n} links per value on one dashboard.",
                        n=MAX_VALUE_SHARES)
    days, expires = _validity(survey, "field")
    labels = panel.value_labels(survey).get(name, {})
    for value in missing:
        board.shares.append(BoardShare(token=secrets.token_urlsafe(24), label=(labels.get(value) or value)[:100],
                                       hall=False, expires_at=expires, revoked=False, opens=0, valid=days,
                                       param=name, value=value))
    return len(missing), None


def value_board(survey, name):
    """The dashboard made by "Dashboard für jede Person anlegen" for the links of parameter `name`, or None (Ralf,
    2026-10-09: the second run of "Links anlegen" uses the dashboard of the first). Marked in its settings."""
    return next((b for b in survey.boards if (b.settings or {}).get("value_links") == name), None)


def ensure_value_board(survey, param):
    """With the tick "Dashboard für jede Person anlegen": the dashboard of value_board, made if there is none (a
    single tile, the number of participations, so each person sees only how many took part through their link),
    and a link of it for each value without one. Returns (added, error); the caller commits."""
    board = value_board(survey, param["name"])
    if board is None:
        if len(survey.boards) >= MAX_BOARDS:
            return 0, de_en("Höchstens {n} Dashboards je Befragung; bitte zuerst eines löschen.",
                            "At most {n} dashboards per survey; please delete one first.", n=MAX_BOARDS)
        known = _known(survey)
        board = Board(name=de_en("Eigene Teilnahmen je Link", "Own participations per link"),
                      tiles=[_tile("field:count", known["field:count"], 12)] if "field:count" in known else [],
                      settings={"value_links": param["name"]},
                      position=max((b.position for b in survey.boards), default=-1) + 1)
        survey.boards.append(board)
        db.session.flush()
    return add_value_shares(survey, board, param)


@bp.route("/<int:board_id>/link/<int:share_id>/withdraw", methods=["POST"])
@login_required
def share_revoke(survey_id, board_id, share_id):
    survey = _survey(survey_id, need="admin")
    board = _board(survey, board_id)
    share = next((s for s in board.shares if s.id == share_id), None)
    if share is None:
        abort(404)
    share.revoked = True
    db.session.commit()
    flash(de_en("Link widerrufen: Er zeigt ab sofort nichts mehr.", "Link revoked: from now on it shows nothing."), "success")
    return redirect(url_for("boards.designer", survey_id=survey.id, board_id=board.id) + "#links")


def _shared(token):
    """(share, board) of a link that works, else 404: revoked, run out and unknown links look the same, and
    so does the link of an archived survey."""
    from app import limits

    if not limits.allow_board():
        abort(429)
    from app.shared_tokens import find

    share = find(BoardShare, token)
    if share is None or share.revoked:
        abort(404)
    board = db.session.get(Board, share.board_id)
    if board is None or board.survey is None or board.survey.archived or expiry(share) <= utcnow():
        abort(404)
    return share, board


def _only(share):
    """The provider a link is for: (URL parameter, value), or None for everyone."""
    return (share.param, share.value) if share.param else None


def _public(board, state):
    """The state for a dashboard link: without tiles of the quota of "Höchstzahl je Link" (panel.set_cap), whose
    cells are named after the codes of the links per person; whoever holds the link could build those links from
    them. The members see them in the app (audit 2026-10-09b, V2). Filtered here, not in _build: a lecture hall link
    shares its state file with the members' view."""
    from app.panel import CAP_PREFIX
    from app.boards.public_wording import without_wording

    caps = {f"quota:{q.id}" for q in board.survey.quotas if (q.code or "").startswith(CAP_PREFIX)}
    return without_wording(board, {**state, "tiles": [t for t in state.get("tiles") or [] if t.get("key") not in caps]})


@share_bp.route("/<token>/embed")
def show_embedded(token):
    """The same page for a frame on another site: this address alone may be framed by others (create_app sends
    the headers by g.embeddable). It shows nothing a click could change, so framing it gives nothing away."""
    from flask import g

    g.embeddable = True
    return show(token, embed=True)  # a lecture hall link is never offered for frames: show() answers 404


@share_bp.route("/<token>")
def show(token, embed=False):
    """The dashboard for whoever holds the link: read-only, summarised numbers only, never indexed."""
    share, board = _shared(token)
    if embed and share.hall:  # the lecture hall mode is for a room, never for a frame on another site (H11)
        abort(404)
    try:  # counted, not who: one statement, and a busy database does not keep the page from showing
        db.session.execute(db.update(BoardShare).where(BoardShare.id == share.id)
                           .values(opens=BoardShare.opens + 1, opened_at=utcnow()))
        db.session.commit()
    except Exception:
        db.session.rollback()
    lang = language(board.survey)
    return _page(board, _public(board, state(board, lang=lang, only=_only(share), steps=not share.hall, live=share.hall)),
                 url_for("board_share.show_live", token=token, lang=request.args.get("lang") or None), share=share, hall=share.hall,
                 embed=embed)


@share_bp.route("/<token>/state")
def show_live(token):
    share, board = _shared(token)
    response = _live(board, _public(board, state(board, lang=language(board.survey), only=_only(share), steps=not share.hall,
                                                live=share.hall)), public=True)
    response.headers.update({"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    return response
