"""Images on comments from test runs (preview in the editor, phone and pretest link) and where a comment was written.

The browser makes the image smaller before sending (comments.js); here it is read again with Pillow and written
anew, so what lies on the disk is a PNG or JPEG with only bounded attribution (no camera or GPS position), at most
MAX_SIDE pixels on its longer side. Files lie outside what Askurious serves without login (not under /media):
instance/comment-shots/<survey uid>/<random>.png|jpg. The editor shows them to members of the survey; a test person
on the phone link sees only the images of their own comments in this test run (app/editor/preview.py).

An image goes with its comment: deleting an own comment, "Bild entfernen" on the comments page, and the survey
(`run` sweeps folders of deleted surveys and files no comment names any more).

env: device, system, browser, window size, language and colour scheme, as the page reports them; only short labels
from known values, never the whole user agent or an address.
"""
import io
import os
import re
import secrets
import shutil
import threading
import zipfile

from flask import current_app
from app.lang import de_en

MAX_BYTES = 8 * 1024 * 1024  # one image as sent; the form as a whole stays below MAX_CONTENT_LENGTH (10 MB)
MAX_SIDE = 2400
PNG_LIMIT = 1_500_000        # a PNG larger than this (a photo rather than a page) is written as JPEG
NAME_RE = re.compile(r"[A-Za-z0-9_-]{16}\.(png|jpg)\Z")
STALE_SECONDS = 3600         # a file without a comment (the comment failed after storing) goes after this
# A small file can unpack to a huge image (a lossless WebP of 2 KB holds 7065 × 7065 pixels, some 800 MB to decode).
# The browser sends at most 2400 px a side (comments.js), so 16 megapixels leave room for any real screenshot; the
# size is read from the header, before anything is decoded. At most DECODES_AT_ONCE images are decoded at a time per
# process; a further one waits up to DECODE_WAIT seconds, then the comment is refused as busy (audit 2026-10-09b, O1).
MAX_PIXELS = 16_000_000
DECODES_AT_ONCE = 2
DECODE_WAIT = 5
_decoding = threading.BoundedSemaphore(DECODES_AT_ONCE)

DEVICES = ("Computer", "Tablet", "Smartphone", "Mobilansicht")
SYSTEMS = ("Windows", "macOS", "Linux", "iOS", "iPadOS", "Android", "ChromeOS")
BROWSERS = ("Chrome", "Edge", "Firefox", "Safari", "Opera", "Samsung Internet")
SCHEMES = ("hell", "dunkel")
_SIZE_RE = re.compile(r"(\d{2,5})x(\d{2,5})\Z")
_LANG_RE = re.compile(r"[a-z]{2}(-[A-Za-z]{2,4})?\Z")
_VERSION_RE = re.compile(r"\d{1,3}\Z")


def root():
    return os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "comment-shots")


def path(survey_uid, name):
    """The file of an image, or None if there is none (also when the name is not one this module writes)."""
    if not name or not NAME_RE.match(name):
        return None
    full = os.path.join(root(), survey_uid, name)
    return full if os.path.isfile(full) else None


class Refused(ValueError):
    """An image not read for now (the sender or the server is busy); the route answers with `status` and the
    message, the comment is not stored."""
    status = 429


class Busy(Refused):
    """More than DECODES_AT_ONCE images are being read in this process."""
    status = 503


def check_sender():
    """Count this image for its sender before anything is read; Refused once the sender sent too many."""
    from app import limits

    if not limits.allow_shot_sender():
        raise Refused(de_en("Von Ihrem Anschluss kamen in letzter Zeit sehr viele Bilder. Bitte später erneut oder "
                            "ohne Bild senden.",
                            "Very many images came from your connection recently. Please try again later or send "
                            "without an image."))


def store(survey, upload, attribution=None):
    """Read an uploaded image, write it anew; returns (file name, width, height). ValueError with a message for the
    person testing if it is no image, too large or the survey has no room left; Refused (a ValueError too) when the
    sender sent too many images or the process is decoding DECODES_AT_ONCE already."""
    from app import image_rights
    from app.media import open_image

    check_sender()
    data = upload.read(MAX_BYTES + 1)
    if not data:
        raise ValueError(de_en("Das Bild ist leer.", "The image is empty."))
    if len(data) > MAX_BYTES:
        raise ValueError(de_en("Das Bild ist zu groß (höchstens 8 MB).", "The image is too large (at most 8 MB)."))
    if used_bytes(survey) >= current_app.config["COMMENT_SHOTS_MAX_MB"] * 1024 * 1024:
        raise ValueError(de_en("Für diese Befragung sind schon sehr viele Bilder gespeichert. Bitte ohne Bild senden.",
                               "Very many images are already stored for this survey. Please send without an image."))
    img = open_image(data, ["PNG", "JPEG", "WEBP", "GIF"], max_pixels=MAX_PIXELS)  # header only, nothing decoded yet
    if not _decoding.acquire(timeout=DECODE_WAIT):
        raise Busy(de_en("Gerade werden viele Bilder verarbeitet. Bitte den Kommentar in einem Moment erneut senden.",
                         "Many images are being processed right now. Please send the comment again in a moment."))
    try:
        embedded = image_rights.read(img)
        supplied = image_rights.normalize(attribution)
        rights = image_rights.normalize({field: embedded.get(field, []) + supplied.get(field, [])
                                        for field in image_rights.FIELDS})
        img = _decoded(img)
    finally:
        _decoding.release()
    out = io.BytesIO()
    img.info.clear()
    img.save(out, "PNG", optimize=True, **image_rights.save_options(rights, "PNG"))
    ext = "png"
    if out.tell() > PNG_LIMIT:
        out = io.BytesIO()
        img.save(out, "JPEG", quality=88, optimize=True, **image_rights.save_options(rights, "JPEG"))
        ext = "jpg"
    folder = os.path.join(root(), survey.uid)
    os.makedirs(folder, exist_ok=True)
    name = f"{secrets.token_urlsafe(12)}.{ext}"
    tmp = os.path.join(folder, f".{name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(out.getvalue())
    os.replace(tmp, os.path.join(folder, name))
    return name, img.width, img.height


def _decoded(img):
    """The image upright, flat on white and at most MAX_SIDE a side (only the first frame of an animation)."""
    from PIL import Image, ImageOps

    try:
        if img.format == "JPEG":  # JPEG can decode at a fraction of its size right away
            img.draft("RGB", (MAX_SIDE, MAX_SIDE))
        img = ImageOps.exif_transpose(img)  # photos of a screen: upright as they were taken
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.getchannel("A"))
            img = flat
        else:
            img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    except Exception as e:  # truncated files and the like fail only when decoded
        raise ValueError(de_en("Das Bild konnte nicht gelesen werden.", "The image could not be read.")) from e
    return img


def remove(survey_uid, name):
    where = path(survey_uid, name)
    if where:
        try:
            os.remove(where)
        except OSError:
            pass


def used_bytes(survey):
    folder = os.path.join(root(), survey.uid)
    if not os.path.isdir(folder):
        return 0
    return sum(e.stat().st_size for e in os.scandir(folder) if e.is_file())


def env(form):
    """The labels the page sent along (fields env_*), only known values; None if none."""
    out = {}
    if form.get("env_device") in DEVICES:
        out["device"] = form["env_device"]
    if form.get("env_os") in SYSTEMS:
        out["os"] = form["env_os"]
    browser, version = form.get("env_browser") or "", form.get("env_version") or ""
    if browser in BROWSERS:
        out["browser"] = f"{browser} {version}" if _VERSION_RE.match(version) else browser
    size = _SIZE_RE.match(form.get("env_size") or "")
    if size and all(100 <= int(v) <= 20000 for v in size.groups()):
        out["size"] = f"{int(size.group(1))}x{int(size.group(2))}"
    lang = form.get("env_lang") or ""
    if _LANG_RE.match(lang):
        out["lang"] = lang
    if form.get("env_scheme") in SCHEMES:
        out["scheme"] = form["env_scheme"]
    return out or None


def env_label(data):
    """One line for the comments page: "iOS · Smartphone · Safari 19 · 390 × 844 · de · dunkel"."""
    data = data or {}
    size = data.get("size", "")
    parts = [data.get("os"), data.get("device"), data.get("browser"), size.replace("x", " × ") if size else "",
             data.get("lang"), data.get("scheme")]
    return " · ".join(p for p in parts if p)


def zip_name(comment):
    return f"bilder/{comment.id}.{comment.shot.rsplit('.', 1)[-1]}" if comment.shot else ""


def attribution(survey_uid, name):
    """Only bounded rights metadata from the stored, redacted image, for the accompanying CSV."""
    from app import image_rights

    where = path(survey_uid, name)
    if not where:
        return {}
    try:
        with open(where, "rb") as source:
            data = source.read(MAX_BYTES + 1)
        return image_rights.from_bytes(data) if len(data) <= MAX_BYTES else {}
    except OSError:
        return {}


def zip_file(survey, csv_text, comments):
    """comments.csv and the folder bilder/ with the image of every comment that has one (<comment id>.png|jpg).
    PNG and JPEG are compressed already: stored, not deflated again. In a temporary file (at the start, for
    send_file) that goes to the disk past 16 MB, not in memory (audit 2026-10-09, D3)."""
    import tempfile

    buf = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("comments.csv", csv_text.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        for c in comments:
            where = path(survey.uid, c.shot)
            if where:
                z.write(where, zip_name(c), compress_type=zipfile.ZIP_STORED)
    buf.seek(0)
    return buf


def run(now=None):
    """Sweep folders of deleted surveys and images no comment names any more. Returns the number of things removed.
    Never raises."""
    import time

    from app.extensions import db
    from app.models import PreviewComment, Survey

    base = root()
    if not os.path.isdir(base):
        return 0
    cutoff = (now.timestamp() if now else time.time()) - STALE_SECONDS
    gone = 0
    try:
        for uid in os.listdir(base):
            survey = db.session.scalar(db.select(Survey).filter_by(uid=uid))
            if survey is None:
                shutil.rmtree(os.path.join(base, uid), ignore_errors=True)
                gone += 1
                continue
            named = set(db.session.scalars(db.select(PreviewComment.shot).filter(
                PreviewComment.survey_id == survey.id, PreviewComment.shot.is_not(None))))
            for entry in os.scandir(os.path.join(base, uid)):
                if entry.name not in named and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
                    gone += 1
    except OSError as e:
        current_app.logger.error("Comment images not swept: %s", e)
    return gone
