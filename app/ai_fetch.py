"""AI calls sent by the page's own script (header "X-AI-Call: 1", app/static/app.js aiSend): no banner at the top.

Ralf, 2026-10-10: a failed AI function says so where it was clicked, and when the API reports its limit (429) a
dialog opens with the message, the choice of another model (own access) and "Erneut versuchen" / "Abbrechen".
The routes stay as they are (flash and redirect, or a page); for such a request the answer is turned into JSON
here, after the route:

- a 429 of the AI call (ai._note_limit): {"status": "limit", "code": "quota"|"rate", "message", "own", "models",
  "current", "platform", "model_url"}; the dialog repeats the same request, with the chosen model saved before;
- an error flashed by the route (also a page drawn with it): {"status": "error", "message"}; the page stays as
  it is, with what was typed, and shows the sentence at the button;
- a redirect: {"status": "redirect", "url"}; the other flashed messages stay for the next page;
- anything else (a page drawn by the route, a JSON answer of its own) goes out unchanged.

Without the script (or for any other request) nothing changes: the form posts as before.
"""
from flask import current_app, g, jsonify, request, session, url_for
from flask.globals import request_ctx

HEADER = "X-AI-Call"


def wanted():
    return request.method == "POST" and request.headers.get(HEADER) == "1"


def _take_errors():
    """The error messages flashed in this request, taken out (the page shows them at the button); the other
    messages stay for the next page, also those a page drawn here had already taken."""
    shown = request_ctx.flashes or []  # taken by a page drawn in this request (get_flashed_messages)
    pending = session.get("_flashes") or []
    errors = [str(text) for kind, text in list(shown) + list(pending) if kind == "error"]
    if not errors:
        return []
    rest = [(kind, text) for kind, text in list(shown) + list(pending) if kind != "error"]
    if rest:
        session["_flashes"] = rest
    else:
        session.pop("_flashes", None)
    request_ctx.flashes = None
    return errors


def _limit(limit):
    from app import ai_access

    own = ai_access.active() if limit.get("own") else None
    return jsonify(status="limit", code=limit["code"], message=str(limit["message"]), own=own is not None,
                   models=list(own.models or []) if own is not None else [],
                   current=own.text_model if own is not None else "",
                   platform=own is not None and bool(current_app.config.get("OPENAI_API_KEY")),
                   model_url=url_for("ai_access.model"))


def after(response):
    if not wanted():
        return response
    limit = g.pop("ai_limit", None)
    errors = _take_errors()
    if limit:
        return _limit(limit)
    if errors:
        from app import ai_access

        # with an own access the line at the button links to it (the key, the model)
        access = url_for("accounts.connections", _anchor="ki-zugang") if ai_access.active() is not None else None
        return jsonify(status="error", message=" ".join(errors), access_url=access)
    if response.is_json:
        return response
    if 300 <= response.status_code < 400 and response.location:
        return jsonify(status="redirect", url=response.location)
    return response


def init_app(app):
    app.after_request(after)
