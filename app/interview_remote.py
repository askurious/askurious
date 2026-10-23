"""A second device as the recorder of an interview ("Interviews erheben"; Ralf 2026-10-04).

The guide runs on a tablet, a smartphone beside it records. The smartphone opens the recording page of the same
interview (link or QR code in the interview view): only the recording bar, no guide, so it cannot overwrite the
tablet's notes. It sends its pieces like the interview view (app/interview_audio.py; the part starts at the interview
clock the server knows) and every few seconds its state. The interview view asks for that state: it shows that the
smartphone records, starts its clock with the recording if the clock has not run yet (so the time marks fit the
recording), and "Interview beenden" asks the smartphone to stop and waits until its last piece is there.

A refused report or request to stop (another request stored the interview at the same moment) is tried once more on
the server, and the interview view asks again until the smartphone confirms (data audit L-2). The view also learns
how many pieces the smartphone still holds and when the last piece arrived: it warns when a recording smartphone sent
nothing for a while (I-20) and does not end silently while pieces are missing (I-26).

  Response.interview["remote"] = {"state": "recording" | "paused" | "stopped", "ms": recorded milliseconds,
                                  "pending": pieces not sent yet, "problem": what the device reports ("" or e.g.
                                  "Mikrofon getrennt"), "seen": ISO time of the last report,
                                  "stop": True once the interview view asked}"""
import math
from datetime import datetime

from flask import Blueprint, abort, g, jsonify, render_template, request, url_for

from app import brand
from app.interviewer import interviewer_or_signed_in
from app.models import utcnow

bp = Blueprint("interview_remote", __name__, url_prefix="/guide")

STATES = ("recording", "paused", "stopped")
ALIVE = 20  # seconds without a report after which the device counts as gone


def remote(row):
    return dict((row.interview or {}).get("remote") or {})


def _age(iso, now):
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds() if iso else None
    except ValueError:
        return None


def status(row, now=None):
    """What the interview view needs: {"state", "ms", "alive", "stop", "pending", "problem", "piece"}; "ms" runs on
    while the device records, "piece" is the seconds since the last piece of the recording arrived (None: none yet)."""
    from app import interview_audio

    r, now = remote(row), now or utcnow()
    age = _age(r.get("seen"), now)
    alive = age is not None and age <= ALIVE
    ms = int(r.get("ms") or 0)
    if alive and r.get("state") == "recording":
        ms += int(max(0, age) * 1000)
    pieces = [a for a in (_age(p.get("at"), now) for p in interview_audio.parts(row)) if a is not None]
    return {"state": r.get("state") if r.get("state") in STATES else "", "ms": ms, "alive": alive, "stop": bool(r.get("stop")),
            "pending": int(r.get("pending") or 0), "problem": str(r.get("problem") or ""),
            "piece": int(min(pieces)) if pieces else None}


def report(row, state, ms, pending=0, problem=""):
    """The device's report; returns whether it is asked to stop."""
    r = remote(row)
    if state not in STATES:
        abort(400)
    if state == "recording" and r.get("state") in ("", None, "stopped"):
        r["stop"] = False  # a new recording: an old request to stop is done
    r.update(state=state, ms=max(0, min(int(ms or 0), 86400 * 1000)), seen=utcnow().isoformat(timespec="seconds"),
             pending=max(0, min(int(pending or 0), 100000)), problem=" ".join(str(problem or "").split())[:80])
    row.interview = {**(row.interview or {}), "remote": r}
    return bool(r.get("stop"))


def _row(code, rid, right=None):
    """(survey, interview) for a researcher with "Bearbeiten", or an interviewer's own interview (app/interviewer.py)."""
    from app import interviewer

    return interviewer.row_for(code, rid, right=right)


def _consented(row):
    return bool(((row.interview or {}).get("consent") or {}).get("audio"))


@bp.route("/<code>/interviews/<int:rid>/recorder")
@interviewer_or_signed_in
def page(code, rid):
    """The recording page for the second device."""
    from app import interview_audio

    survey, row = _row(code, rid, right="record")
    g.microphone = True  # this page is the recording device (Permissions-Policy in create_app)
    return render_template("interview/recorder.html", survey=survey, row=row, consented=_consented(row),
                           part=interview_audio.next_part(row))


@bp.route("/<code>/interviews/<int:rid>/recorder/qr.svg")
@interviewer_or_signed_in
def qr(code, rid):
    from flask import Response as FlaskResponse

    from app.links import qr as make

    survey, row = _row(code, rid)
    # the configured address, not the host the request named (security audit 2026-10-06, H3)
    link = brand.public_base() + url_for("interview_remote.page", code=survey.code, rid=row.id)
    return FlaskResponse(make(link),
                         mimetype="image/svg+xml", headers={"Cache-Control": "private, no-store"})


@bp.route("/<code>/interviews/<int:rid>/recorder/state", methods=["GET", "POST"])
@interviewer_or_signed_in
def state(code, rid):
    """POST: the device reports (state, ms) and learns whether to stop. GET: the interview view asks."""
    survey, row = _row(code, rid, right="record" if request.method == "POST" else None)
    if request.method == "POST":
        if not _consented(row):
            abort(403)
        from app import interview_audio

        data = request.get_json(silent=True)
        if not isinstance(data, dict):  # a list or a number as the body: 400 below, not a 500 (code review 2026-10-15, K8)
            data = {}
        number = lambda key: data.get(key) if isinstance(data.get(key), (int, float)) and math.isfinite(data.get(key)) else 0  # noqa: E731
        stop = interview_audio.retried(lambda: report(row, data.get("state"), number("ms"), number("pending"),
                                                      data.get("problem") if isinstance(data.get("problem"), str) else ""))
        return jsonify(ok=True, stop=stop)
    return jsonify(status(row))


@bp.route("/<code>/interviews/<int:rid>/recorder/stop", methods=["POST"])
@interviewer_or_signed_in
def stop(code, rid):
    """The interview view ends: the device stops at its next report."""
    from app import interview_audio

    survey, row = _row(code, rid)

    def ask():
        r = remote(row)
        if r.get("state") in ("recording", "paused"):
            r["stop"] = True
            row.interview = {**(row.interview or {}), "remote": r}

    interview_audio.retried(ask)
    return jsonify(status(row))
