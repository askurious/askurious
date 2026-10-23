"""The app's own error pages 403, 404 and 500 (UI/UX audit 2026-10-09, X68 and S13): in the frame of every page
(base.html: header, footer with the imprint), in the language of the person, with a way back and, for 403, who can
give the access. Before, Flask's bare English page stood there, without header, footer or a link.

The participants' pages keep their own (app/participate.py, participant/error.html): a 404 on the participants' side
(their blueprints, or every address on the participants' address) gets the participants' page "Diese Umfrage gibt
es nicht"; their other errors stay as they were.
"""
from flask import render_template, request
from werkzeug.exceptions import Forbidden, InternalServerError, NotFound

from app import hosts


def _participant_side():
    return hosts.on_participant_side() or hosts.BLUEPRINT_SIDES.get(request.blueprint) == hosts.PARTICIPANT


def _page(kind, status):
    from flask_login import current_user

    survey_id = (request.view_args or {}).get("survey_id")
    return render_template("error.html", kind=kind, survey_id=survey_id,
                           signed_in=current_user.is_authenticated), status


def _not_found(e):
    if _participant_side():
        from app import participate

        return participate._error_page("missing", 404)
    return _page("missing", 404)


def _forbidden(e):
    if _participant_side():
        return e
    return _page("forbidden", 403)


def _server_error(e):
    """An error nobody caught: the page in the frame if it can still be built (the database may be what failed),
    else Flask's own."""
    from app import db

    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001  the session itself may be broken
        pass
    if _participant_side():
        return e
    try:
        return _page("error", 500)
    except Exception:  # noqa: BLE001  no frame without the database (footer, account): the plain page
        return e if isinstance(e, InternalServerError) else InternalServerError()


def register(app):
    app.register_error_handler(NotFound, _not_found)
    app.register_error_handler(Forbidden, _forbidden)
    app.register_error_handler(InternalServerError, _server_error)
