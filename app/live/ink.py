"""Pen notes on presentation slides (concept stiftnotizen-konzept-2026-10-23.md, Ralf 2026-10-23: all as recommended).

The presenter writes with a pen (or, in drawing mode, a finger or the mouse) on a presentation slide in the presenter
view; live_ink.js sends the strokes here while they are drawn, the audience view shows them (unless switched off,
state "ink_off") and fetches them only when their revision changed, faster while someone is drawing. The notes
belong to a run and a slide (E3): a new run starts clean. "Folien mit Notizen (PDF)" draws every presentation slide of
a run with its notes as a PDF (E8). Strokes are vectors with the pen's pressure per point (E2); the laser dot is shown
on the screen for a moment and never exported.

The rows are the presenter's own content, no data of participants (live_ink, app/models.py); they go with the run
(scores._forget_feedback, control._merge_run) and with the survey (lifecycle.delete).
"""
import io
import json
import os
import re
import time
from datetime import timedelta

from flask import abort, current_app, jsonify, request, send_file
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.lang import de_en
from app.live.base import _survey, bp, is_slide, questions, state
from app.models import LiveInk, utcnow

MAX_STROKES = 2000  # strokes on one slide in one run (E10)
MAX_BYTES = 300_000  # the strokes of one slide in one run as JSON (E10)
MAX_POINTS = 4000  # points of one stroke
LASER_SECONDS = 2  # the laser dot fades on the screen after this long (E5)
ACTIVE_SECONDS = 4  # someone drew this recently: the screen asks every half second (E6)
COLOUR = re.compile(r"^#[0-9a-f]{6}$")
STROKE_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")


def _clamp(value, low, high):
    return max(low, min(high, float(value)))


def clean_stroke(raw):
    """A stroke as it is stored, or None: colour, width as a share of the slide's width, highlighter flag, points as
    [x, y, pressure] from 0 to 1 (rounded: four places are a tenth of a pixel on a 2560 px slide)."""
    try:
        sid, colour = str(raw["id"]), str(raw.get("c", "#1a1a1a")).lower()
        points = [[round(_clamp(p[0], 0, 1), 4), round(_clamp(p[1], 0, 1), 4), round(_clamp(p[2] if len(p) > 2 else 0.5, 0, 1), 2)]
                  for p in raw["p"][:MAX_POINTS]]
        width = round(_clamp(raw.get("w", 0.003), 0.0005, 0.05), 5)
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if not STROKE_ID.match(sid) or not COLOUR.match(colour) or not points:
        return None
    return {"id": sid, "c": colour, "w": width, "m": 1 if raw.get("m") else 0, "p": points}


def apply_ops(strokes, ops):
    """The strokes after the presenter's changes: put (a new stroke, or a stroke drawn further, replaced by its id), del
    (strokes by id: the eraser, Undo of a stroke), clear ("Folie leeren"); Undo of an erasure puts the strokes again."""
    strokes = list(strokes or [])
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict):
            continue
        if op.get("clear"):
            strokes = []
        if isinstance(op.get("del"), list):
            gone = {str(i) for i in op["del"]}
            strokes = [s for s in strokes if s["id"] not in gone]
        if isinstance(op.get("put"), dict):
            stroke = clean_stroke(op["put"])
            if stroke is not None:
                at = next((n for n, s in enumerate(strokes) if s["id"] == stroke["id"]), None)
                if at is None:
                    strokes.append(stroke)
                else:
                    strokes[at] = stroke
    return strokes


def _run_id(survey, st, raw, need_edit=False):
    """The run asked for (?durchgang=), the current one without; an earlier run only with the right to edit, as on the
    page "Alle Folien" (AZ-1)."""
    current = st["run"]["id"]
    if raw in (None, "", str(current)):
        return current
    try:
        wanted = int(raw)
    except ValueError:
        abort(404)
    if wanted not in {r["id"] for r in st["runs"]}:
        abort(404)
    if not survey.can(current_user, "editor"):
        abort(403)
    return wanted


def _slide(survey, item_id):
    item = next((i for i in questions(survey) if i.id == item_id), None)
    if item is None or not is_slide(item):
        abort(404)
    return item


def _row(survey, run_id, item_id):
    return db.session.scalar(db.select(LiveInk).filter_by(survey_id=survey.id, run_id=run_id, item_id=item_id))


def revisions(survey, run_id):
    """{slide id: (revision, drawn within ACTIVE_SECONDS)} of a run, for the screen's state (one query)."""
    since = utcnow() - timedelta(seconds=ACTIVE_SECONDS)
    rows = db.session.execute(db.select(LiveInk.item_id, LiveInk.rev, LiveInk.updated_at)
                              .filter_by(survey_id=survey.id, run_id=run_id))
    return {item_id: (rev, updated >= since) for item_id, rev, updated in rows}


def ink_of(survey, run_id):
    """{slide id: strokes} of a run, for the page "Alle Folien" and the PDF."""
    rows = db.session.execute(db.select(LiveInk.item_id, LiveInk.strokes).filter_by(survey_id=survey.id, run_id=run_id))
    return {item_id: strokes or [] for item_id, strokes in rows}


def _laser(row):
    laser = row.laser if row is not None else None
    return laser[:2] if laser and time.time() - laser[2] < LASER_SECONDS else None


@bp.route("/<code>/ink.json")
@login_required
def ink_json(code):
    """The strokes of one slide in one run; with ?rev= only when they changed since ("same")."""
    survey = _survey(code)
    st = state(survey)
    run_id = _run_id(survey, st, request.args.get("durchgang"))
    item = _slide(survey, request.args.get("item", type=int))
    row = _row(survey, run_id, item.id)
    rev = row.rev if row is not None else 0
    active = row is not None and row.updated_at >= utcnow() - timedelta(seconds=ACTIVE_SECONDS)
    out = {"rev": rev, "active": active, "laser": _laser(row)}
    if request.args.get("rev", type=int) == rev:
        return jsonify({**out, "same": True})
    return jsonify({**out, "strokes": (row.strokes or []) if row is not None else []})


@bp.route("/<code>/ink", methods=["POST"])
@login_required
def ink_save(code):
    """The presenter's changes to the strokes of one slide (form fields item, durchgang, ops as JSON; laser [x, y] or
    null). Answers the new revision; over the limits (E10) nothing is saved and the answer says why."""
    survey = _survey(code, need="editor")
    st = state(survey)
    run_id = _run_id(survey, st, request.form.get("durchgang"))
    item = _slide(survey, request.form.get("item", type=int))
    try:
        ops = json.loads(request.form.get("ops") or "[]")
        laser = json.loads(request.form.get("laser") or "false")
    except ValueError:
        return jsonify(ok=False, error=de_en("Ungültige Striche.", "Invalid strokes.")), 400
    for attempt in (1, 2):  # two requests may create the row at once: the second one finds it
        row = _row(survey, run_id, item.id)
        if row is None:
            row = LiveInk(survey_id=survey.id, run_id=run_id, item_id=item.id, strokes=[], rev=0)
            db.session.add(row)
        if ops:
            strokes = apply_ops(row.strokes, ops)
            if len(strokes) > MAX_STROKES or len(json.dumps(strokes, separators=(",", ":"))) > MAX_BYTES:
                db.session.rollback()
                return jsonify(ok=False, error=de_en("Auf dieser Folie ist kein Platz für weitere Striche. Bitte „Folie leeren“ oder einige Striche radieren.",
                                                     "There is no room for more strokes on this slide. Please use “Clear slide” or erase some strokes.")), 413
            row.strokes = strokes
            row.rev = (row.rev or 0) + 1
        if laser is None:
            row.laser = None
        elif isinstance(laser, list) and len(laser) == 2:
            try:
                row.laser = [round(_clamp(laser[0], 0, 1), 4), round(_clamp(laser[1], 0, 1), 4), round(time.time(), 2)]
            except (TypeError, ValueError):
                pass
        row.updated_at = utcnow()
        try:
            db.session.commit()
            return jsonify(ok=True, rev=row.rev)
        except IntegrityError:
            db.session.rollback()
            if attempt == 2:
                raise
    abort(409)


# the PDF: a page per presentation slide, the width of an A4 page across, its height after the slide's own shape
PAGE_W = 842.0


def _image_path(survey, item):
    asset = item.kind.asset_for(item, {})
    if asset is None:
        return None
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], survey.uid, asset.filename)
    return path if os.path.isfile(path) else None


def stroke_width(stroke, pressure, page_w):
    """The width of a stroke at a point, in the unit of page_w: the highlighter keeps its width, the pen grows with the
    pressure (0.5 when the device tells none: the base width). live_ink.js draws with the same formula."""
    base = stroke["w"] * page_w
    return base if stroke.get("m") else base * (0.4 + 1.2 * pressure)


def draw_strokes(c, strokes, page_w, page_h):
    """The strokes on a reportlab canvas whose origin is at the bottom left: the highlighter as one path, half see-
    through; the pen as segments, each as wide as the pressure at its ends, with round ends so they join smoothly."""
    for s in strokes:
        r, g, b = (int(s["c"][i:i + 2], 16) / 255 for i in (1, 3, 5))
        c.setStrokeColorRGB(r, g, b)
        c.setLineCap(1)
        c.setLineJoin(1)
        pts = [(x * page_w, page_h - y * page_h, p) for x, y, p in s["p"]]
        if s.get("m"):
            c.setStrokeAlpha(0.4)
            c.setLineWidth(stroke_width(s, 0.5, page_w))
            path = c.beginPath()
            path.moveTo(pts[0][0], pts[0][1])
            for x, y, _ in pts[1:] or pts:
                path.lineTo(x, y)
            c.drawPath(path, stroke=1, fill=0)
            c.setStrokeAlpha(1)
            continue
        if len(pts) == 1:  # a dot
            pts = pts * 2
        for (x1, y1, p1), (x2, y2, p2) in zip(pts, pts[1:]):
            c.setLineWidth(stroke_width(s, (p1 + p2) / 2, page_w))
            c.line(x1, y1, x2, y2)


def slide_picture(survey, item):
    """The picture of a presentation slide as an RGB image of Pillow, or None when it is missing or unreadable."""
    from PIL import Image

    path = _image_path(survey, item)
    if not path:
        return None
    try:
        with Image.open(path) as im:
            return im.convert("RGB")
    except (OSError, ValueError):
        return None


def draw_slide(c, survey, item, picture, x, y, w, h, layers):
    """A slide's picture in the box (x, y bottom left, w by h) with the strokes of each layer over it, in order; a
    slide whose picture is missing gets its name instead."""
    from reportlab.lib.utils import ImageReader

    from app.items.base import plain

    if picture:
        c.drawImage(ImageReader(picture), x, y, w, h)
    else:
        c.setFont("Helvetica", 14)
        c.drawCentredString(x + w / 2, y + h / 2, (plain(item.text("alt", survey.default_language)) or
                                                   de_en("Das Bild dieser Folie fehlt.", "The picture of this slide is missing."))[:120])
    c.saveState()
    c.translate(x, y)
    for strokes in layers:
        draw_strokes(c, strokes, w, h)
    c.restoreState()


def slide_height(picture, width):
    """The height of a slide drawn `width` wide: after its picture's shape, 16:9 without one."""
    return width * picture.height / picture.width if picture else width * 9 / 16


def notes_pdf(survey, run_id):
    """The presentation slides of a run with their notes, as PDF bytes; a slide whose picture is missing gets a page
    that says so."""
    from reportlab.pdfgen import canvas

    ink = ink_of(survey, run_id)
    out = io.BytesIO()
    c = canvas.Canvas(out)
    c.setTitle(survey.title)
    c.setAuthor("Askurious")
    pages = 0
    for item in (i for i in questions(survey) if is_slide(i)):
        picture = slide_picture(survey, item)
        page_h = slide_height(picture, PAGE_W)
        c.setPageSize((PAGE_W, page_h))
        draw_slide(c, survey, item, picture, 0, 0, PAGE_W, page_h, [ink.get(item.id, [])])
        c.showPage()
        pages += 1
    if not pages:
        return None
    c.save()
    return out.getvalue()


@bp.route("/<code>/notes.pdf")
@login_required
def ink_pdf(code):
    """"Folien mit Notizen (PDF)": every presentation slide of a run (?durchgang=, the current one without) with its
    pen notes, one page each (E8)."""
    from app import export

    survey = _survey(code)
    st = state(survey)
    run_id = _run_id(survey, st, request.args.get("durchgang"))
    data = notes_pdf(survey, run_id)
    if data is None:
        abort(404)
    run = next((r for r in st["runs"] + [st["run"]] if r["id"] == run_id), st["run"])
    name = f"folien-notizen-{export.slug(survey.title)}-{export.slug(run['name'])}.pdf"
    return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name=name)
