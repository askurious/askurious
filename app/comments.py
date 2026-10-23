"""Comments from test runs (preview in the editor or on a phone): add, list, handle, export.

A comment belongs to an item on a preview page or to the whole page (item_id None) and always to a block.
Whoever wrote a comment may delete it during the same test run (remembered in the session; "Von vorn"
starts a new run and forgets it). Researchers mark comments as resolved or ignored (with an optional note); open comments flag the item and
the block in the editor. The export (comments.csv) lists all comments and how they were handled.

A comment may carry one image of the page (app/comment_shots.py) and always the labels of where it was written
(device, browser, window size, language, colour scheme). With images the export is a ZIP with comments.csv and bilder/.
"""
import csv
import io

from flask import session

from app.extensions import db
from app.ids import db_id
from app.models import Block, Item, PreviewComment, utcnow

MAX_TEXT = 4000
STATUS_LABELS = {PreviewComment.OPEN: "offen", PreviewComment.RESOLVED: "erledigt", PreviewComment.IGNORED: "ignoriert"}
STATUS_LABELS_EN = {PreviewComment.OPEN: "open", PreviewComment.RESOLVED: "done", PreviewComment.IGNORED: "ignored"}


def status_labels():
    """The states of a comment (STATUS_LABELS) in the language of the page."""
    from app.lang import english

    return STATUS_LABELS_EN if english() else STATUS_LABELS


TARGET_MAX = 300  # PreviewComment.target


def target_label(block, item, lang=None):
    """"<block title> · <element code>"; the title shortened so that it fits the column (a title has up to 300
    characters, and PostgreSQL refused the comment with a 500: code review 2026-10-07, D4)."""
    name = (block.title(lang) if block else "") or "Block"
    tail = " · ganze Seite" if item is None else f" · {item.code}"
    return (name[:max(TARGET_MAX - len(tail), 1)] + tail)[:TARGET_MAX]


def add(survey, form, author, via, page=None, author_id=None, files=None):
    """Store a comment from the preview form; author_id: the signed-in researcher who wrote it; files: the request's
    files (the image comes as "shot"). Returns (comment, error); comment_shots.Refused when the image cannot be read
    now (too many from this sender, or the process busy decoding others)."""
    from app import comment_shots

    text = (form.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        return None, "Bitte einen Kommentar eingeben."
    block = db.session.get(Block, form.get("block_id", type=db_id) or 0)
    if block is None or block.survey_id != survey.id:
        return None, "Unbekannter Block."
    item = None
    if form.get("item_id"):
        item = db.session.get(Item, form.get("item_id", type=db_id) or 0)
        if item is None or item.block_id != block.id:
            return None, "Unbekanntes Element."
    upload = files.get("shot") if files else None
    shot, width, height = None, None, None
    if upload is not None and upload.filename:
        if via == "phone":
            from app import limits

            if not limits.allow_phone_shot(survey):
                return None, "Über diesen Link sind heute schon sehr viele Bilder eingegangen. Bitte ohne Bild senden."
        try:
            from app.image_rights import FIELDS

            shot, width, height = comment_shots.store(
                survey, upload, attribution={field: form.get("shot_rights_" + field, "") for field in FIELDS})
        except comment_shots.Refused:  # busy: the route answers 429/503 (audit 2026-10-09b, O1)
            raise
        except ValueError as e:
            return None, str(e)
    comment = PreviewComment(survey_id=survey.id, block_id=block.id, item_id=item.id if item else None,
                             target=target_label(block, item), page=page, qversion=survey.qversion or 1,
                             text=text, author=(author or "").strip()[:120], author_id=author_id, via=via,
                             shot=shot, shot_w=width, shot_h=height, env=comment_shots.env(form))
    db.session.add(comment)
    db.session.commit()
    session[_key(survey)] = mine(survey) + [comment.id]
    return comment, None


def _key(survey):
    return f"pc{survey.id}"


def mine(survey):
    """Ids of the comments written in this browser during the current test run."""
    return list(session.get(_key(survey), []))


def forget_run(survey):
    session.pop(_key(survey), None)


def delete_own(survey, comment_id):
    """Delete a comment written in this browser during the current run. Returns True if deleted."""
    comment = db.session.get(PreviewComment, comment_id)
    if comment is None or comment.survey_id != survey.id or comment_id not in mine(survey):
        return False
    shot = comment.shot
    db.session.delete(comment)
    db.session.commit()
    session[_key(survey)] = [i for i in mine(survey) if i != comment_id]
    if shot:
        from app import comment_shots

        comment_shots.remove(survey.uid, shot)
    return True


def remove_shot(survey, comment):
    """"Bild entfernen" on the comments page: the comment stays, its image goes."""
    from app import comment_shots

    shot = comment.shot
    comment.shot, comment.shot_w, comment.shot_h = None, None, None
    db.session.commit()
    if shot:
        comment_shots.remove(survey.uid, shot)


def as_dict(c, own=True):
    from app.timeutil import to_local

    return {"id": c.id, "own": own, "text": c.text, "author": c.author or "Testperson", "target": c.target,
            "item_id": c.item_id, "status": c.status, "created": to_local(c.created_at).strftime("%d.%m.%Y %H:%M"),
            "shot": bool(c.shot)}


def any_shots(survey):
    return db.session.scalar(db.select(db.func.count(PreviewComment.id)).filter(
        PreviewComment.survey_id == survey.id, PreviewComment.shot.is_not(None))) > 0


def for_page(survey, block, items):
    """Open comments on this page (its items or the whole page), newest first."""
    ids = [i.id for i in items]
    q = db.select(PreviewComment).filter_by(survey_id=survey.id, status=PreviewComment.OPEN, block_id=block.id)
    rows = db.session.scalars(q.order_by(PreviewComment.created_at.desc())).all()
    return [c for c in rows if c.item_id is None or c.item_id in ids]


def open_counts(survey):
    """({item id: n}, {block id: n}) of open comments, for the flags in the editor."""
    by_item, by_block = {}, {}
    for c in db.session.scalars(db.select(PreviewComment).filter_by(survey_id=survey.id, status=PreviewComment.OPEN)):
        if c.item_id:
            by_item[c.item_id] = by_item.get(c.item_id, 0) + 1
        if c.block_id:
            by_block[c.block_id] = by_block.get(c.block_id, 0) + 1
    return by_item, by_block


def all_for(survey):
    return db.session.scalars(db.select(PreviewComment).filter_by(survey_id=survey.id)
                              .order_by(PreviewComment.created_at.desc())).all()


def handle(comment, status, user, note=""):
    if status not in STATUS_LABELS:
        return
    comment.status = status
    comment.note = (note or "").strip()[:MAX_TEXT] or comment.note
    if status == PreviewComment.OPEN:
        comment.handled_by, comment.handled_by_id, comment.handled_at = "", None, None
    else:
        comment.handled_by, comment.handled_at = (getattr(user, "display_name", "") or "")[:120], utcnow()
        comment.handled_by_id = getattr(user, "id", None)
    db.session.commit()


def to_csv(survey):
    from app import comment_shots, image_rights
    from app.export import safe_text
    from app.timeutil import to_local

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["comment_id", "created", "author", "via", "questionnaire_version", "page", "block_item", "text",
                "status", "handled_by", "handled_at", "note", "image", "device", "system", "browser", "window",
                "language", "colour_scheme"] + ["image_" + field for field in image_rights.FIELDS])
    for c in sorted(all_for(survey), key=lambda c: c.created_at):
        e = c.env or {}
        rights = comment_shots.attribution(survey.uid, c.shot)
        w.writerow([safe_text(v) for v in (
            c.id, to_local(c.created_at).strftime("%Y-%m-%d %H:%M"), c.author, c.via, c.qversion, c.page or "",
            c.target, c.text, c.status, c.handled_by,
            to_local(c.handled_at).strftime("%Y-%m-%d %H:%M") if c.handled_at else "", c.note,
            comment_shots.zip_name(c), e.get("device", ""), e.get("os", ""), e.get("browser", ""), e.get("size", ""),
            e.get("lang", ""), e.get("scheme", ""),
            *(" | ".join(rights.get(field, [])) for field in image_rights.FIELDS))])
    return "﻿" + buf.getvalue()
