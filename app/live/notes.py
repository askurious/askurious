"""The audience's notes on presentation slides (concept folien-notizen, Ralf 2026-10-29: all as recommended).

With "Präsentationsfolien auch auf den Handys zeigen" and "Notizen und Herunterladen erlauben" (base.notes_on) a
phone that shows a presentation slide shows it with a note field and a strip of the slides so far; on a wide screen
with a pen as well (live_notes.js). The notes stay on the device (IndexedDB in the browser, per vote and slide, E1):
the server stores none of them. For the PDF the device sends them once; the PDF is drawn in memory and the notes go
with the request (no table, no log, E1). The audience pages back, never beyond the presenter's place
(base.notes_slides, E3): the pictures, the presenter's strokes and the PDF are refused beyond it. The presenter's pen
notes are a layer of their own on the phone and in the PDF (E6). Each slide is a page of the PDF: portrait with the
typed note under the slide, landscape filling the page without one (E7). One PDF per device and minute (E10,
limits.allow_notes_pdf).
"""
import io
import json
import os
import unicodedata

from flask import abort, current_app, jsonify, request, send_file

from app import pdf_fonts
from app.extensions import db
from app.live import ink
from app.live.audience import _device, _public
from app.live.base import notes_on, notes_slides, public_bp, state
from app.ui_i18n import german, tr

MAX_STROKES = ink.MAX_STROKES  # strokes of the audience on one slide (E10), as for the presenter
MAX_TEXT = 5000  # characters of a typed note on one slide (E10)
MAX_SLIDE_BYTES = ink.MAX_BYTES  # the strokes of one slide as JSON
MAX_BYTES = 6_000_000  # the notes of all slides as sent (MAX_CONTENT_LENGTH is 10 MB)

# the PDF: A4 portrait for a slide with a typed note, the slide's own shape (landscape for a wide slide) without
A4_W, A4_H = 595.28, 841.89
MARGIN = 42
FONT, SIZE, LEADING = pdf_fonts.REGULAR, 11, 15


def _slides(survey, st):
    if not notes_on(survey):
        abort(404)
    return notes_slides(survey, st)


def _reached(survey, st, item_id):
    """The slide asked for, if the audience has reached it; 403 beyond the presenter's place (E3)."""
    item = next((i for i in _slides(survey, st) if i.id == item_id), None)
    if item is None:
        abort(403)
    return item


@public_bp.route("/<code>/folie/<int:item_id>")
def notes_slide(code, item_id):
    """The picture of a slide the audience has reached (?klein=1: the small picture for the strip, if there is one).
    The phones page through these instead of /media, so a slide not yet shown has no address on the phone."""
    from app import media

    survey = _public(code)
    item = _reached(survey, state(survey), item_id)
    asset = item.kind.asset_for(item, {})
    if asset is None:
        abort(404)
    name = media.thumbnail_name(survey.uid, asset.filename) if request.args.get("klein") else asset.filename
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], survey.uid, name)
    if not os.path.isfile(path):
        abort(404)
    ext = os.path.splitext(name)[1].lower()
    response = send_file(path, mimetype=media.MEDIA_TYPES.get(ext), conditional=True, max_age=media.MEDIA_MAX_AGE)
    response.cache_control.public = False
    response.cache_control.private = True
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@public_bp.route("/<code>/folie/<int:item_id>/striche")
def notes_ink(code, item_id):
    """The presenter's strokes on a slide reached, in the current run (E6); none while the presenter keeps them off the
    screen ("ink_off"). With ?rev= only whether they changed ("same")."""
    survey = _public(code)
    st = state(survey)
    item = _reached(survey, st, item_id)
    if st.get("ink_off"):
        return jsonify(rev=-1, strokes=[])
    row = ink._row(survey, st["run"]["id"], item.id)
    rev = row.rev if row is not None else 0
    if request.args.get("rev", type=int) == rev:
        return jsonify(rev=rev, same=True)
    return jsonify(rev=rev, strokes=(row.strokes or []) if row is not None else [])


def clean_text(raw):
    """A typed note as it goes into the PDF: text, at most MAX_TEXT characters, line breaks kept, other control
    characters left out."""
    if not isinstance(raw, str):
        return ""
    text = "".join(ch for ch in raw[:MAX_TEXT] if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    return text.replace("\t", "    ").strip()


def clean_strokes(raw):
    """The audience's strokes of one slide, each checked as the presenter's are (ink.clean_stroke); at most
    MAX_STROKES and MAX_SLIDE_BYTES, the rest left out."""
    out, size = [], 0
    for stroke in (raw if isinstance(raw, list) else [])[:MAX_STROKES]:
        stroke = ink.clean_stroke(stroke) if isinstance(stroke, dict) else None
        if stroke is None:
            continue
        size += len(json.dumps(stroke, separators=(",", ":")))
        if size > MAX_SLIDE_BYTES:
            break
        out.append(stroke)
    return out


def _lines(c, text, width):
    """The note wrapped to `width`: paragraphs at its line breaks, words that do not fit split by characters (in the
    fonts of app/pdf_fonts.py, every survey language)."""
    return pdf_fonts.lines(text, SIZE, width, FONT)


def audience_pdf(survey, pages):
    """The PDF of the slides with notes: pages is [(item, presenter's strokes, own strokes, typed note)]. A slide with a
    typed note: A4 portrait, the slide at the top, the note under it (going on to further portrait pages when it is
    long); without: the slide fills a page of its own shape (E7)."""
    from reportlab.pdfgen import canvas

    lang = survey.default_language
    out = io.BytesIO()
    c = canvas.Canvas(out)
    pdf_fonts.register()
    c.setTitle(survey.title)
    c.setAuthor("Askurious")
    for n, (item, theirs, mine, text) in enumerate(pages, start=1):
        picture = ink.slide_picture(survey, item)
        if not text:
            page_h = ink.slide_height(picture, ink.PAGE_W)
            c.setPageSize((ink.PAGE_W, page_h))
            ink.draw_slide(c, survey, item, picture, 0, 0, ink.PAGE_W, page_h, [theirs, mine])
            c.showPage()
            continue
        c.setPageSize((A4_W, A4_H))
        label = tr(lang, "Folie {n}", "Slide {n}", n=n)
        width = A4_W - 2 * MARGIN
        slide_w, slide_h = width, ink.slide_height(picture, width)
        if slide_h > A4_H * 0.55:  # a tall slide leaves room for the note
            slide_h = A4_H * 0.55
            slide_w = slide_h * picture.width / picture.height if picture else width
        top = A4_H - MARGIN
        c.setFont(FONT, 9)
        c.setFillGray(0.4)
        pdf_fonts.draw(c, MARGIN, top - 9, label, 9, FONT)
        c.setFillGray(0)
        top -= 18
        ink.draw_slide(c, survey, item, picture, MARGIN + (width - slide_w) / 2, top - slide_h, slide_w, slide_h, [theirs, mine])
        y = top - slide_h - 24
        c.setFont(FONT, SIZE)
        for line in _lines(c, text, width):
            if y < MARGIN:  # the note goes on, on a further portrait page
                c.showPage()
                c.setPageSize((A4_W, A4_H))
                c.setFont(FONT, 9)
                c.setFillGray(0.4)
                pdf_fonts.draw(c, MARGIN, A4_H - MARGIN - 9, label, 9, FONT)
                c.setFillGray(0)
                c.setFont(FONT, SIZE)
                y = A4_H - MARGIN - 9 - 24
            if pdf_fonts.rtl(line):  # a note in Arabic runs from the right margin
                pdf_fonts.draw(c, A4_W - MARGIN, y, line, SIZE, FONT, anchor="end")
            else:
                pdf_fonts.draw(c, MARGIN, y, line, SIZE, FONT)
            y -= LEADING
        c.showPage()
    c.save()
    return out.getvalue()


@public_bp.route("/<code>/notizen.pdf", methods=["POST"])
def notes_pdf(code):
    """"Folien mit Notizen herunterladen": every slide up to the presenter's place as a page of a PDF, with what the
    device chose of its own notes (form field notes: {slide id: {"t": typed note, "s": strokes}}, ticks mine_ink,
    mine_text) and the presenter's strokes (presenter). The notes are only read for this PDF and stored nowhere."""
    from app import export, limits

    survey = _public(code)
    st = state(survey)
    slides = _slides(survey, st)
    if not slides:
        abort(404)
    raw = request.form.get("notes") or "{}"
    if len(raw) > MAX_BYTES:
        return jsonify(ok=False, error=tr(survey.default_language, "Die Notizen sind zu umfangreich für ein PDF. Bitte einige Striche radieren.",
                                          "The notes are too large for a PDF. Please erase some strokes.")), 413
    try:
        notes = json.loads(raw)
    except ValueError:
        notes = None
    if not isinstance(notes, dict):
        abort(400)
    device = _device(survey)
    db.session.commit()  # the device's receipt (_public): the limit counts in a transaction of its own (SQLite: one writer)
    if not limits.allow_notes_pdf(survey, device):
        return jsonify(ok=False, error=tr(survey.default_language, "Bitte warten Sie eine Minute, bevor Sie das PDF erneut herunterladen.",
                                          "Please wait a minute before downloading the PDF again.")), 429
    mine_ink, mine_text, presenter = (request.form.get(k) == "1" for k in ("mine_ink", "mine_text", "presenter"))
    theirs = ink.ink_of(survey, st["run"]["id"]) if presenter and not st.get("ink_off") else {}
    pages = []
    for item in slides:
        entry = notes.get(str(item.id))
        entry = entry if isinstance(entry, dict) else {}
        pages.append((item, theirs.get(item.id, []), clean_strokes(entry.get("s")) if mine_ink else [],
                      clean_text(entry.get("t")) if mine_text else ""))
    data = audience_pdf(survey, pages)
    name = f"{'folien-notizen' if german(survey.default_language) else 'slides-notes'}-{export.slug(survey.title)}.pdf"
    response = send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name=name)
    response.headers["Cache-Control"] = "no-store"
    return response
