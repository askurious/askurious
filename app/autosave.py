"""Forms that save by themselves (concept "Automatisch speichern und Rückgängig").

The page sends a form after every change, with the field `autosave=1` (app.js, "Forms that save by themselves").
A route marked `@autosave.aware` then works as for "Speichern", and what it would have shown on the next page
comes back as JSON instead:

    {"status": "saved", "at": "16:50", "base": ..., "fill": {...}, "form": ..., "parts": {...}, "notes": [...],
     "undo": ..., "redo": ...}
    {"status": "invalid", "message": ...}      the route refused the form (its error message); nothing was saved
    {"status": "conflict", "who": ..., "when": ..., "base": ...}    someone else saved in between (app/edit_guard.py)

The route itself needs no second way: its messages (flash) are collected here, its redirect is dropped. It only
says what the page cannot know: `autosave.base(...)` gives the fingerprint the next save builds on, and
`merge=autosave.merge(...)` lets what is typed in one visit to a text field be one step for "Rückgängig"."""
from functools import wraps

from flask import g, get_flashed_messages, jsonify, request
from flask_login import current_user

from app.extensions import db
from app.models import Survey, utcnow
from app.timeutil import to_local


def wanted():
    return request.method == "POST" and request.form.get("autosave") == "1"


def base(value):
    """The fingerprint of what the form edits, as it is after this save."""
    g.autosave_base = value


def base_later(make):
    """As base(), for a route that cannot say it itself: `make()` is called once the route is done."""
    g.autosave_base_later = make


def redraw(html):
    """The form as it is after this save, drawn by the server (the page puts it in place of the one it has:
    form[data-autosave-redraw]). For forms whose rows the server decides, like the dashboard designer."""
    g.autosave_form = html


def part(name, html):
    """A part of the page outside the form that the save changes (the week preview of a diary's schedule), drawn anew
    by the server: the page puts it in place of its element [data-autosave-part="<name>"] (pre-test 2026-10-20, 20)."""
    g.autosave_parts = {**g.get("autosave_parts", {}), name: html}


def fill(values):
    """{field name: value} the page puts into fields that are still empty, e.g. the number of a row this save made."""
    g.autosave_fill = {**g.get("autosave_fill", {}), **{name: str(value) for name, value in values.items()}}


def merge(prefix):
    """The key that joins the saves of one visit to a text field into one step (undo.done(merge=...)); None for a
    click, a choice, or a form sent with "Speichern"."""
    key = request.form.get("merge", "") if wanted() else ""
    return f"{prefix}:{key[:60]}" if key else None


def what(fallback=""):
    """The name of the changed field as the page shows it, for the label of the step."""
    text = " ".join(request.form.get("what", "").split())[:50] if wanted() else ""
    return text or fallback


def arrows():
    """What ↶ and ↷ offer after this save, for the survey of the request (empty outside a draft)."""
    from app import undo

    survey = db.session.get(Survey, (request.view_args or {}).get("survey_id") or 0)
    if survey is None or not current_user.is_authenticated:
        return {"undo": "", "redo": ""}
    steps = undo.history(survey, current_user)
    return {"undo": undo.label(steps["undo"][0]) if steps["undo"] else "", "redo": undo.label(steps["redo"][0]) if steps["redo"] else ""}


def aware(view):
    """Let a route that saves a form answer the page's own saves with JSON (see the module text)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not wanted():
            return view(*args, **kwargs)
        view(*args, **kwargs)
        messages = get_flashed_messages(with_categories=True)  # taken out: the page shows them now, not the next page
        conflict = g.get("edit_conflict")
        if conflict:
            return jsonify(status="conflict", who=conflict["who"], when=conflict["when"], base=conflict["base"])
        errors = [text for kind, text in messages if kind == "error"]
        if g.get("form_error"):  # a settings form that was refused (surveys._refused)
            errors.insert(0, g.form_error["message"])
        if errors:
            return jsonify(status="invalid", message=" ".join(str(e) for e in errors))
        if g.get("autosave_base_later"):
            base(g.autosave_base_later())
        return jsonify(status="saved", at=to_local(utcnow()).strftime("%H:%M"), base=g.get("autosave_base"),
                       fill=g.get("autosave_fill", {}), form=g.get("autosave_form"), parts=g.get("autosave_parts", {}),
                       notes=[str(text) for kind, text in messages if kind == "warning"], **arrows())

    return wrapper
