"""The page of one live session: QR code, PDF slides, the AI assistant and presenting."""
from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en
from app.live.base import (QUIZ_TYPES, TYPES, _flatten, _people, _qr, _survey, addable, blocks, bp, by_blocks,
                           is_slide, join_url, questions, state, type_label, types)
from app.live.scores import _counts


def _qr_png(survey):
    """The QR code as a PNG for a slide of one's own (Präsentieren -> "QR-Code für eigene Folien"): the code, under
    it the short address and the code to type, on white; 1200 px wide, sharp when shrunk to a corner of a slide."""
    import io

    import segno
    from PIL import Image, ImageDraw, ImageFont

    from app.hosts import participant_base

    code = segno.make(join_url(survey), error="m")
    out = io.BytesIO()
    code.save(out, kind="png", scale=max(1, 1000 // code.symbol_size(border=2)[0]), border=2)
    qr = Image.open(io.BytesIO(out.getvalue())).convert("RGB")
    width = 1200
    qr = qr.resize((width, width), Image.NEAREST)
    page = (participant_base() + url_for("live_public.enter_code")).split("://")[1].rstrip("/")
    lines = [page, de_en("Code: {code}", "Code: {code}", code=survey.code)]
    try:
        font = ImageFont.load_default(size=72)
    except TypeError:  # an older Pillow without scalable default font
        font = ImageFont.load_default()
    canvas = Image.new("RGB", (width, width + 220), "white")
    canvas.paste(qr, (0, 0))
    draw = ImageDraw.Draw(canvas)
    for n, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        draw.text(((width - w) / 2, width + 10 + n * 100), line, fill="black", font=font)
    png = io.BytesIO()
    canvas.save(png, format="PNG", optimize=True)
    return png.getvalue()


@bp.route("/<code>/qr.<any(png, svg):kind>")
@login_required
def qr_download(code, kind):
    """The QR code to put on a slide of one's own, small in a corner (concept konzept-live-feedback-praesentation-
    2026-10-12.md, variant A): PNG with the short address and the code under it, or SVG with the code alone."""
    import io

    import segno
    from flask import Response as FlaskResponse

    survey = _survey(code)
    if kind == "png":
        data, mimetype = _qr_png(survey), "image/png"
    else:
        out = io.BytesIO()
        segno.make(join_url(survey), error="m").save(out, kind="svg", border=2, scale=10, light="#ffffff")
        data, mimetype = out.getvalue(), "image/svg+xml"
    return FlaskResponse(data, mimetype=mimetype, headers={
        "Content-Disposition": f'attachment; filename="qr-{survey.code}.{kind}"', "Cache-Control": "private, no-store"})


@bp.route("/<code>")
@login_required
def manage(code):
    survey = _survey(code)
    if survey.can(current_user, "editor") and _flatten(survey):
        db.session.commit()
    st = state(survey)
    counts, people = _counts(survey, st["run"]["id"])
    runs = [{**r, "n": _people(survey, r["id"])} for r in reversed(st["runs"])]
    chosen = request.args.get("item", type=int)
    items = {i.id: i for b in survey.blocks for i in b.items}
    selected = items.get(chosen) if chosen in items and items[chosen].type in TYPES else None
    return render_template("live/manage.html", survey=survey, blocks=blocks(survey), st=st, types=types(), addable=addable(survey),
                           type_label=type_label,
                           in_blocks=by_blocks(survey),
                           join=join_url(survey), counts=counts, people=people, runs=runs, selected=selected,
                           editable=survey.can(current_user, "editor"), quiz_types=QUIZ_TYPES,
                           quiz=((st.get("quiz") or {}).get(str(selected.id)) if selected else None) or {})


MAX_PDF_PAGES = 150  # pages of one PDF taken over at a time (slides_pdf.js says so before it starts)


@bp.route("/<code>/pdf")
@login_required
def pdf(code):
    """Slides from a PDF (for everyone since 2026-10-17; concept konzept-live-feedback-praesentation-2026-10-12.md, variant C):
    the browser draws each page with pdf.js and sends it as a picture (slides_pdf.js), pdf_page makes it a slide."""
    survey = _survey(code, need="editor")
    slides = [i for i in questions(survey) if is_slide(i)]
    return render_template("live/pdf.html", survey=survey, slides=len(slides), max_pages=MAX_PDF_PAGES)


@bp.route("/<code>/pdf/page", methods=["POST"])
@login_required
def pdf_page(code):
    """One page of the PDF as a picture (PNG or WebP, drawn in the browser): a presentation slide at the end of the
    last block, its alt text "Folie <n> aus <file>". JSON."""
    from app import media, undo
    from app.editor import _renumber, _unique_code
    from app.models import Asset, Item

    survey = _survey(code, need="editor")
    f = request.files.get("file")
    if f is None:
        return jsonify(ok=False, error=de_en("Keine Seite erhalten. Bitte die PDF-Datei noch einmal auswählen.",
                                             "No page received. Please choose the PDF file again.")), 400
    if sum(1 for i in questions(survey) if is_slide(i)) >= 3 * MAX_PDF_PAGES:
        return jsonify(ok=False, error=de_en("Zu viele Präsentationsfolien in dieser Abstimmung.",
                                             "Too many presentation slides in this poll.")), 400
    try:
        data = f.read()
        if media._sniff(data) not in ("png", "webp"):
            raise ValueError(de_en("Nur PNG oder WebP.", "Only PNG or WebP."))
        name = media.save_bytes(survey, media.fit_still(data, 2560))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    number = request.form.get("n", type=int) or 0
    source = " ".join(request.form.get("name", "").split())[:120]
    lang = survey.default_language
    alt = (de_en("Folie {n} aus {file}", "Slide {n} from {file}", n=number, file=source) if source
           else de_en("Folie {n}", "Slide {n}", n=number))
    step = undo.begin(survey)
    block = blocks(survey)[-1]
    kind = Item(type="image").kind
    item = Item(type="image", answer_mode="optional", code=_unique_code(survey, "F"),
                config={**kind.default_config(), "slide": True}, texts={lang: {"question": "", "alt": alt}})
    item.assets.append(Asset(key="", filename=name, source="upload", status=Asset.APPROVED))
    rows = list(block.items) + [item]
    block.items = rows
    _renumber(rows)
    db.session.flush()
    step.done(undo.both("Präsentationsfolie {n} aus PDF übernommen", "Presentation slide {n} added from a PDF", n=number),
              item=item.id)
    db.session.commit()
    return jsonify(ok=True, item=item.id)


@bp.route("/<code>/pdf/clear", methods=["POST"])
@login_required
def pdf_clear(code):
    """The presentation slides taken out before a new version of the PDF comes in ("Bisherige Präsentationsfolien
    ersetzen"): into the trash like a deleted question, one step for "Rückgängig". JSON."""
    from app import trash, undo
    from app.editor import _renumber

    survey = _survey(code, need="editor")
    step = undo.begin(survey)
    gone = 0
    for block in survey.blocks:
        for item in [i for i in block.items if is_slide(i)]:
            trash.put(item)
            block.items.remove(item)
            gone += 1
        _renumber(block.items)
    if gone:
        step.done(undo.both("{n} Präsentationsfolien entfernt", "{n} presentation slides removed", n=gone))
    db.session.commit()
    return jsonify(ok=True, removed=gone)


@bp.route("/<code>/ai", methods=["GET", "POST"])
@login_required
def assistant(code):
    """AI assistant (app/live_ai.py): describe the event, get five questions, take over the ticked ones."""
    from app import ai, live_ai, undo

    survey = _survey(code, need="editor")
    target = [b for b in blocks(survey)]
    description = request.form.get("description", "").strip()[:live_ai.MAX_DESCRIPTION]
    page = lambda suggestions=None: render_template("live/assistant.html", survey=survey, description=description,
                                                    suggestions=suggestions, blocks=target, types=types())
    if request.method == "GET":
        return page()
    if request.form.get("action") == "apply":
        chosen = live_ai.from_form(request.form)
        block = next((b for b in target if b.id == request.form.get("block", type=int)), target[-1])
        if not chosen:
            flash(de_en("Keine Frage ausgewählt; nichts übernommen.", "No question selected; nothing added."), "warning")
            return page(live_ai.from_form_all(request.form))
        step = undo.begin(survey)
        new = live_ai.apply(survey, block, chosen)
        db.session.flush()
        if len(new) == 1:
            step.done(undo.both("1 Frage vom KI-Assistenten übernommen", "1 question added from the AI assistant"), item=new[0].id)
        else:
            step.done(undo.both("{n} Fragen vom KI-Assistenten übernommen", "{n} questions added from the AI assistant",
                                n=len(new)), item=new[0].id)
        db.session.commit()
        if len(new) == 1:
            flash(de_en("1 Frage in „{block}“ übernommen.", "1 question added to “{block}”.", block=block.title()), "success")
        else:
            flash(de_en("{n} Fragen in „{block}“ übernommen.", "{n} questions added to “{block}”.", n=len(new),
                        block=block.title()), "success")
        return redirect(url_for("live.manage", code=survey.code, item=new[0].id))
    if not description:
        flash(de_en("Bitte beschreiben Sie kurz Ihre Veranstaltung und was Sie wissen möchten.",
                    "Please describe your event briefly and what you would like to know."), "error")
        return page()
    try:
        return page(live_ai.suggest(survey, description))
    except ai.AIError as e:
        flash(str(e), "error")
        return page()


@bp.route("/<code>/present")
@login_required
def present(code):
    """Präsentieren (tab): how people join (QR code, address, code), the screen, the runs."""
    from app import limits

    survey = _survey(code)
    st = state(survey)
    runs = [{**r, "n": _people(survey, r["id"])} for r in reversed(st["runs"])]
    return render_template("live/present.html", survey=survey, st=st, join=join_url(survey), qr=_qr(survey),
                           people=_counts(survey, st["run"]["id"])[1], runs=runs, questions=questions(survey),
                           editable=survey.can(current_user, "editor"), cap_notes=limits.cap_notes(survey),
                           has_slides=any(is_slide(i) for i in questions(survey)))
