"""Drawing a dashboard: palette, tiles, the page around them, share and embed links."""
import threading
from collections import OrderedDict
from datetime import datetime
from markupsafe import Markup

from flask import abort, current_app, jsonify, render_template, request, url_for

from app.boards.base import MAX_TILES, _num, _pct, _point, _tile, cell_min_of, expiry, has_link, item_ref, speaking
from app.boards.tiles import _known
from app.extensions import db
from app.hosts import participant_base
from app.models import Board, Survey, utcnow
from app.ui_i18n import date_text, german, tr


# --- showing --------------------------------------------------------------------------------------------

# Okabe and Ito, in darker variants that keep 3:1 to a white surface (UI/UX audit 2026-10-05, V81)
PALETTE = ("var(--accent)", "#b07800", "#2f7fb3", "#00795a", "#a3508a", "#b84a00", "#005a8c", "#6e6e6e")
RWTH_PALETTE = ("#00549f", "#f6a800", "#0098a1", "#57ab27", "#e30066", "#006165", "#a11035", "#612158")  # RWTH Farbwelt
# the 13 full colours of the RWTH Farbwelt: an organisation design with exactly these colour suggestions (the RWTH
# design file, konzept-org-designs-2026-10-19.md) keeps the order above, chosen for the ring
RWTH_SWATCHES = frozenset(("#00549f", "#000000", "#e30066", "#ffed00", "#006165", "#0098a1", "#57ab27", "#bdcd00",
                           "#f6a800", "#cc071e", "#a11035", "#612158", "#7a6fac"))


def _base(current):
    """The colours before fitting: those of the design's colour world if it has one (colour suggestions of an
    organisation design or a built-in template), else PALETTE."""
    from app import theme

    swatches = theme.swatches_of(current)
    if not swatches:
        return PALETTE
    if set(swatches) == RWTH_SWATCHES:
        return RWTH_PALETTE
    skip = {current["colors"][k] for k in ("accent", "background", "surface", "text")}
    return ("var(--accent)",) + tuple(c for c in swatches if c not in skip)[:7]


def palette(survey):
    """Colours for answers without an order (ring): the accent first, then colours that stay apart for people
    with a red-green weakness; with a design that has a colour world (RWTH) only colours of it."""
    from app import theme

    base = _base(theme.of(survey))
    # on a dark tile the fixed colours fell to 1.9-2.8 : 1; each is lightened until it keeps 3 : 1 to the tile's
    # surface (UI/UX audit 2026-10-16, 60). On a light tile they stay as they are.
    surface = theme.tokens(theme.of(survey)).get("--surface", "#ffffff")
    dark = theme.lightness(surface) < 0.5

    def fit(colour):
        colour = theme.readable(colour, surface, theme.PART_MIN)
        # the percentages on a segment are small text: one of black or white reaches 4.5 : 1 on it
        if max(theme.contrast(ink, colour) for ink in ("#ffffff", "#111111")) < theme.TEXT_MIN:
            colour = theme.readable(colour, "#111111" if dark else "#ffffff", theme.TEXT_MIN)
        return colour

    return tuple(c if c.startswith("var(") else fit(c) for c in base)


def seg_ink(colour):
    """The writing on a segment of a stacked bar: black or white, whichever stands out more from its colour (white on
    RWTH orange had 2.0 : 1); on the accent the theme's own colour for it."""
    from app import theme

    if colour.startswith("var("):
        return "var(--on-accent)"
    return max(("#ffffff", "#111111"), key=lambda ink: theme.contrast(ink, colour))


def _signed(value):
    """A score with its sign and two decimals: +0,35."""
    from app.units import fixed

    text = fixed(value, 2, signed=True).replace("-", "−")
    return text if _point() else text.replace(".", ",")


def donut(rows):
    """Segments of a ring for rows with "share": [(offset, length)] on a circle of circumference 100."""
    out, at = [], 0.0
    gap = 0.8 if sum(1 for row in rows if row.get("share")) > 1 else 0  # a white gap between the parts (V81)
    for row in rows:
        length = (row.get("share") or 0) * 100
        out.append((round(25 - at, 3), round(max(length - gap, 0), 3)))  # the ring starts at the top
        at += length
    return out


def _stand(state):
    from app.timeutil import to_local

    at = to_local(datetime.strptime(state["at"], "%Y-%m-%dT%H:%M:%S"))
    lang = state.get("lang") or "de"
    return at.strftime("%d.%m.%Y, %H:%M Uhr") if german(lang) else f"{date_text(at, lang)} {at.strftime('%H:%M')}"


def render_tiles(board, state):
    from app.items import federal_state

    lang = state.get("lang") or board.survey.default_language
    with speaking(lang):  # num and pct write numbers as the language does
        return render_template("boards/_tiles.html", board=board, survey=board.survey, state=state, palette=palette(board.survey), seg_ink=seg_ink,
                               state_map=federal_state.outline(), lang=lang, donut=donut, num=_num, pct=_pct, signed=_signed,
                               MIN_CELL=cell_min_of(board))


_rendered = OrderedDict()  # (board, version, language, palette, smallest cell) -> the tiles of a link as HTML
_rendered_lock = threading.Lock()
RENDERED_KEEP = 64  # states kept per process; the oldest goes first


def _version(state):
    return state["key"][:16] + ("s" if state.get("sampled") else "")


def _public_tiles(board, state):
    """The tiles of a link's state as HTML, rendered once per state in this process (Ralf, 2026-10-10): in a lecture
    hall every phone asks every few seconds, and each new state used to be rendered anew for every phone. What the
    tiles show rests only on the state (its key covers tiles, language, provider and quotas), the board, its
    smallest cell and the colours of the survey's theme; these make the key. Only for links: the members' view
    shows tiles a link leaves out (_public), on the same key."""
    if state.get("pending"):
        return render_tiles(board, state)
    key = (board.id, _version(state), state.get("lang") or board.survey.default_language,
           tuple(palette(board.survey)), cell_min_of(board))
    with _rendered_lock:
        html = _rendered.get(key)
        if html is not None:
            _rendered.move_to_end(key)
            return html
    html = render_tiles(board, state)
    with _rendered_lock:
        _rendered[key] = html
        while len(_rendered) > RENDERED_KEEP:
            _rendered.popitem(last=False)
    return html


def _live(board, state, public=False):
    """What the open page asks for now and then: the version, and the tiles if the page has another one."""
    version = _version(state)
    if request.args.get("v") == version:
        return jsonify(v=version)
    html = _public_tiles(board, state) if public else render_tiles(board, state)
    return jsonify(v=version, html=html, stand=_stand(state), n=state.get("n"))


def _qr(url):
    """The QR code of an address as SVG without a size of its own (a viewBox only), so it fills its frame."""
    import io

    import segno

    out = io.BytesIO()
    segno.make(url, error="m").save(out, kind="svg", border=2, xmldecl=False, omitsize=True)
    return Markup(out.getvalue().decode("utf-8"))


def page_sources(state, lang):
    """The sources of the procedures the tiles show, for one closed fold under them: [(heading, [citations])].
    Only the price points (Van Westendorp) and the MaxDiff scores rest on a method with a source of its own."""
    from app.items import psm
    from app.maxdiff_analysis import LOUVIERE

    shapes = {t.get("shape") for t in state.get("tiles") or []}
    groups = []
    if "points" in shapes:
        groups.append((tr(lang, "Preispunkte: Price Sensitivity Meter nach Van Westendorp",
                          "Price points: Van Westendorp price sensitivity meter"), [psm.CITATION]))
    if "score" in shapes:
        groups.append((tr(lang, "MaxDiff: Zählwerte (am wichtigsten minus am wenigsten wichtig, je Mal gezeigt)",
                          "MaxDiff: counts (best minus worst, per time shown)"), [LOUVIERE]))
    return groups


def _language_links(survey, current):
    """[(language, its name, address, shown now?)] for the switch on the page; nothing for one language."""
    from app.surveys import LANGUAGE_NAMES

    if len(survey.languages or []) < 2:
        return []
    args = {k: v for k, v in request.args.items() if k in ("hoersaal", "beispiel")}
    return [(code, LANGUAGE_NAMES.get(code, code).split(" (")[0], url_for(request.endpoint, **request.view_args, **args, lang=code), code == current)
            for code in survey.languages]


def _page(board, state, live_url, share=None, hall=False, frame=False, arrange=False, images=False, embed=False):
    from app import links

    survey = board.survey
    qr = None
    if hall:
        qr = _qr(links.participant_url(survey))
    # the link of one person (a link per person with a name, "Eigene Links"): their own participation link on top, to
    # copy and as QR code, so the person gets both in one place (UI/UX audit 2026-10-09, X57, S19)
    person_url = person_qr = None
    if share is not None and share.param and not hall:
        from app import panel

        if share.value in panel.value_labels(survey).get(share.param, {}):
            person_url = links.participant_url(survey, [(share.param, share.value)])
            person_qr = _qr(person_url)
    page = render_template("boards/view.html", person_url=person_url, person_qr=person_qr, board=board, survey=survey, state=state, tiles=Markup(render_tiles(board, state)),
                           share=share, expires=expiry(share) if share else None, hall=hall, frame=frame, embed=embed, arrange=arrange, images=images, qr=qr, live_url=live_url, join_url=links.participant_url(survey) if hall else None,
                           stand=_stand(state), version=state["key"][:16] + ("s" if state.get("sampled") else ""),
                           lang=state.get("lang") or survey.default_language, live=survey.status == Survey.RUNNING or frame,
                           language_links=_language_links(survey, state.get("lang")),
                           cited=[] if hall else page_sources(state, state.get("lang") or survey.default_language),
                           closed=survey.status == Survey.CLOSED,
                           every=current_app.config.get("BOARD_POLL_SECONDS", 15) if not hall else current_app.config.get("BOARD_HALL_POLL_SECONDS", 5))
    return page, 200, {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


# --- the designer ---------------------------------------------------------------------------------------


def _survey(survey_id, need=None):
    from app.surveys import get_survey

    return get_survey(survey_id, need=need)


def _board(survey, board_id):
    board = next((b for b in survey.boards if b.id == board_id), None)
    if board is None:
        abort(404)
    return board


def share_url(share):
    return participant_base() + url_for("board_share.show", token=share.token)


def embed_url(share):
    """The address a page of another site puts into an <iframe> (show_embedded)."""
    return participant_base() + url_for("board_share.show_embedded", token=share.token)


def embed_code(share):
    """The HTML to paste into another page: the dashboard in a frame over the whole width."""
    from markupsafe import escape

    return (f'<iframe src="{escape(embed_url(share))}" title="{escape(share.board.name or "Dashboard")}" '
            'style="width:100%;height:800px;border:0" loading="lazy" referrerpolicy="no-referrer"></iframe>')


def end_share(board):
    """The link the end page of the survey shows for this dashboard (settings "end_share"), if it still works."""
    wanted = (board.settings or {}).get("end_share")
    return next((s for s in board.shares if s.id == wanted and not s.revoked and not s.hall and not s.param
                 and expiry(s) > utcnow()), None) if wanted else None


def end_links(survey, lang):
    """[(name of the dashboard, address)] for the end page of a completed participation: the dashboards that
    have a link made for it, in the language the person took part in."""
    out = []
    for board in survey.boards:
        share = end_share(board)
        if share is not None:
            out.append((board.name, url_for("board_share.show", token=share.token,
                                            lang=lang if len(survey.languages or []) > 1 else None)))
    return out


def live_shares(board):
    now = utcnow()
    return [(s, share_url(s)) for s in board.shares if not s.revoked and expiry(s) > now]


def settle_future():
    """At startup: a dashboard that still has the old option "also future ones" (settings "future") keeps the
    questions it showed through it as tiles of its own, and the option is gone: nothing comes onto a dashboard by
    itself any more (Ralf, 2026-10-03). Data, not schema."""
    changed = False
    for board in db.session.scalars(db.select(Board)):
        settings = board.settings or {}
        if not settings.get("future") and "future_upto" not in settings:
            continue
        if settings.get("future") and board.survey is not None:
            known = _known(board.survey)
            upto = settings.get("future_upto") if isinstance(settings.get("future_upto"), int) and has_link(board) else None
            keys = [t.get("key") for t in board.tiles or [] if isinstance(t, dict)]
            extra = [_tile(key, row) for key, row in known.items()
                     if key.startswith("item:") and key not in keys and row.get("shape") != "words"
                     and not (upto is not None and (item_ref(key)[0] or 0) > upto)]
            board.tiles = (list(board.tiles or []) + extra)[:MAX_TILES]
        board.settings = {k: v for k, v in settings.items() if k not in ("future", "future_upto")}
        changed = True
    if changed:
        db.session.commit()
