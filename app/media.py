"""Storing and serving images. Files live in instance/uploads/<survey uid>/ under random names,
so participant pages can load them without login and nobody can guess other files."""
import base64
import os
import re
import secrets
import threading
from contextlib import contextmanager

from flask import Blueprint, abort, current_app, send_from_directory
from sqlalchemy import event
from sqlalchemy.orm import Session

from app import image_rights
from app.lang import de_en
from app.storage_room import StorageFull, remove_file, tracked, writing

bp = Blueprint("media", __name__, url_prefix="/media")

SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}


def _sniff(data):
    for sig, ext in SIGNATURES.items():
        if data.startswith(sig):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


# An image of a few hundred kilobytes can hold a hundred million pixels (one colour compresses to nothing), and
# decoding takes four bytes per pixel: 13000 x 13000 px as a logo took 700 MB. The size is read from the file's
# header before anything is decoded. 50 megapixels cover the photos of current cameras; a logo needs far less.
MAX_PIXELS = 50_000_000
LOGO_MAX_PIXELS = 16_000_000

# At most DECODES_AT_ONCE images are decoded at a time per process, as for the images of comments (comment_shots):
# a WebP of 2 KB with 7000 x 7000 px takes 3.4 s and ~190 MB, and parallel uploads, image sets and imports added up
# without a bound (audit 2026-10-13, DA-3). A further one waits up to DECODE_WAIT seconds, then is refused as busy.
DECODES_AT_ONCE = 2
DECODE_WAIT = 30
_decoding = threading.BoundedSemaphore(DECODES_AT_ONCE)
_held = threading.local()  # this thread holds a place already (strip_metadata inside fit_still): no second one


class Busy(ValueError):
    """Too many images are being decoded in this process right now; the message is for the researcher."""


@contextmanager
def decoding():
    """A place among the DECODES_AT_ONCE decodings of this process for the block; Busy after DECODE_WAIT seconds."""
    if getattr(_held, "depth", 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    if not _decoding.acquire(timeout=DECODE_WAIT):
        raise Busy(de_en("Gerade werden viele Bilder verarbeitet. Bitte in einem Moment erneut versuchen.",
                         "Many images are being processed right now. Please try again in a moment."))
    _held.depth = 1
    try:
        yield
    finally:
        _held.depth = 0
        _decoding.release()


def open_image(data, formats, max_pixels=MAX_PIXELS):
    """The image, opened but not yet decoded; ValueError (message for the researcher) if it has more pixels
    than allowed or cannot be read."""
    import io

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data), formats=formats)
        width, height = img.size
    except Exception as e:  # truncated files, unsupported variants, Pillow's own limit
        raise ValueError(de_en("Das Bild konnte nicht gelesen werden.", "The image could not be read.")) from e
    if width * height > max_pixels:
        raise ValueError(de_en("Das Bild hat zu viele Bildpunkte ({w} × {h}); höchstens {mp} Megapixel.",
                               "The image has too many pixels ({w} × {h}); at most {mp} megapixels.", w=width, h=height,
                               mp=max_pixels // 1_000_000))
    return img


def upload_folder(uid):
    """The upload folder of a survey (its uid) or of a design (designs.folder_uid); not created here."""
    return os.path.join(current_app.config["UPLOAD_FOLDER"], uid)


def _folder(survey):
    path = upload_folder(survey.uid)
    os.makedirs(path, exist_ok=True)
    return path


MB = 1024 * 1024


def _tree_bytes(path):
    """Bytes of all files below `path` (0 if it does not exist)."""
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def storage_bytes(uid):
    from app import storage_room, storage_usage

    if storage_room.held():
        return storage_usage.used(uid)
    return _physical_storage_bytes(uid)


def _physical_storage_bytes(uid):
    """Bytes a survey takes on the disk: its upload folder (images, video and audio, kept results), its paper scans
    and its interview recordings."""
    base = os.path.dirname(current_app.config["UPLOAD_FOLDER"])
    return sum(_tree_bytes(p) for p in (upload_folder(uid), os.path.join(base, "paper-scans", uid),
                                        os.path.join(base, "interview-audio", uid)))


def _account_uids():
    """The upload folders (uids) of all surveys the signed-in person runs as admin (SurveyMember role "admin"), or
    None without a signed-in person."""
    from flask import has_request_context
    from flask_login import current_user
    from sqlalchemy import inspect

    from app.extensions import db
    from app.models import Survey, SurveyMember

    if not has_request_context() or not getattr(current_user, "is_authenticated", False):
        return None
    # Synchronous scan workers close/reopen ORM sessions while keeping the request. The login object may be
    # detached and expired then; its ORM identity remains available without refreshing that object. Flask's
    # login ID includes a session version/secret and must never be compared with an integer database ID.
    identity = inspect(current_user._get_current_object()).identity
    if identity is None:
        return None
    user_id = identity[0]
    with db.session.no_autoflush:
        return list(db.session.scalars(db.select(Survey.uid).join(SurveyMember, SurveyMember.survey_id == Survey.id)
                                       .filter(SurveyMember.user_id == user_id, SurveyMember.role == "admin")))


def check_room(survey, size):
    """Raise ValueError (the sentence for the researcher) when `size` more bytes for this survey would leave the disk
    with less than STORAGE_MIN_FREE_MB, or pass SURVEY_STORAGE_MAX_MB for the survey or ACCOUNT_STORAGE_MAX_MB for
    all surveys the signed-in person runs as admin (security audit 2026-10-13, DA-5: images, video and audio, paper
    scans and interview recordings had no limit per survey or account, and only voice messages and recordings looked
    at the free space). 0 switches a limit off."""
    import shutil

    cfg = current_app.config
    floor = int(cfg.get("STORAGE_MIN_FREE_MB", 0) or 0) * MB
    if floor:
        try:
            free = shutil.disk_usage(os.path.dirname(cfg["UPLOAD_FOLDER"])).free
        except OSError:
            free = None
        if free is not None and free - size < floor:
            current_app.logger.error("upload refused: only %s bytes free on the disk", free)
            raise StorageFull(de_en("Auf dem Server ist gerade kein Platz für weitere Dateien. Bitte später erneut versuchen "
                                   "oder an einen Admin der Plattform wenden.",
                                   "There is no room for further files on the server right now. Please try again later "
                                   "or contact a platform admin."))
    size = max(0, int(size))
    from app import storage_usage
    uid = getattr(survey, "uid", None)
    most = int(cfg.get("SURVEY_STORAGE_MAX_MB", 0) or 0) * MB
    if most and uid and storage_bytes(uid) + size + storage_usage.pending({uid}) > most:
        raise StorageFull(de_en("Die Dateien dieser Befragung haben ihren Platz ausgeschöpft ({mb} MB). Bitte nicht mehr "
                               "gebrauchte Bilder, Medien, Scans oder Aufnahmen löschen.",
                               "The files of this survey have used up their room ({mb} MB). Please delete images, "
                               "media, scans or recordings no longer needed.", mb=cfg["SURVEY_STORAGE_MAX_MB"]))
    most = int(cfg.get("ACCOUNT_STORAGE_MAX_MB", 0) or 0) * MB
    groups = []
    uids = _account_uids() if most else None
    if uids:
        groups.append(set(uids) | ({uid} if uid else set()))
    # Collaborators and unattended requests must not bypass the account budget of a survey's admins.
    if most and getattr(survey, "id", None):
        from app.extensions import db
        from app.models import Survey, SurveyMember

        admins = db.select(SurveyMember.user_id).where(SurveyMember.survey_id == survey.id,
                                                      SurveyMember.role == "admin", SurveyMember.user_id.is_not(None))
        with db.session.no_autoflush:
            rows = db.session.execute(db.select(SurveyMember.user_id, Survey.uid).select_from(SurveyMember)
                                      .join(Survey, Survey.id == SurveyMember.survey_id)
                                      .where(SurveyMember.user_id.in_(admins), SurveyMember.role == "admin")).all()
        owned = {}
        for owner, folder_uid in rows:
            owned.setdefault(owner, set()).add(folder_uid)
        groups.extend(owned.values())
    counted = {}
    for group in groups:
        for u in group:
            if u not in counted:
                counted[u] = storage_bytes(u)
        if sum(counted[u] for u in group) + size + storage_usage.pending(group) > most:
            raise StorageFull(de_en("Die Dateien Ihrer Befragungen haben zusammen den Platz Ihres Kontos ausgeschöpft "
                                   "({mb} MB). Bitte nicht mehr gebrauchte Dateien oder Befragungen löschen.",
                                   "The files of your surveys together have used up the room of your account ({mb} MB). "
                                   "Please delete files or surveys no longer needed.", mb=cfg["ACCOUNT_STORAGE_MAX_MB"]))


def save_bytes(survey, data, check=True):
    """Store image bytes; return the filename or raise ValueError if not an image or there is no room for it
    (check_room; check=False only where a file is replaced by a smaller one, convert_webp)."""
    ext = _sniff(data)
    if ext is None:
        raise ValueError(de_en("Nur PNG, JPEG, GIF oder WebP.", "Only PNG, JPEG, GIF or WebP."))
    name = f"{secrets.token_urlsafe(12)}.{ext}"
    path = os.path.join(_folder(survey), name)
    with writing(survey, len(data), check=check):
        try:
            with open(path, "xb") as f:
                f.write(data)
        except OSError:
            _remove(path)
            raise
    make_thumbnail(survey, name)
    return name


def save_media(survey, data, ext):
    """Store the bytes of a video or audio file read in from a survey file (app/transfer.py) under a new name with the
    extension its first bytes gave (stimulus.sniff); the room of the survey and the account is checked (check_room).
    Returns the file name."""
    name = f"{secrets.token_urlsafe(12)}.{ext}"
    path = os.path.join(_folder(survey), name)
    with writing(survey, len(data)):
        try:
            with open(path, "xb") as f:
                f.write(data)
        except OSError:
            _remove(path)
            raise
    return name


def copy_file(source, target, uid):
    """Copies use the same admission as uploads; a source growing during the copy cannot bypass the quota."""
    from types import SimpleNamespace

    survey = SimpleNamespace(uid=uid)
    with writing(survey, 0, check=False):
        size = os.path.getsize(source)
        check_room(survey, size)
        total = 0
        try:
            with open(source, "rb") as input_file, open(target, "xb") as output:
                while chunk := input_file.read(64 * 1024):
                    total += len(chunk)
                    if total > size:
                        check_room(survey, len(chunk))
                    output.write(chunk)
        except OSError:
            _remove(target)
            raise
        except ValueError:
            _remove(target)
            raise


# --- video and audio stimuli, uploaded in pieces (items/stimulus.py, editor.item_media_upload) -------------------


PART_SECONDS = 3600  # an upload not continued for an hour is given up (tab closed, network gone): its pieces go


def _parts(survey):
    path = os.path.join(_folder(survey), ".parts")  # "." and no served extension: never sent by serve()
    os.makedirs(path, exist_ok=True)
    return path


def part_slot(item, key=""):
    """The place of the one open upload of an element and its value of the loop (`key`): a new upload there gives
    up the one before (audit 2026-10-09, D1). Safe as a file name whatever the key holds."""
    import hashlib

    return f"{int(item.id)}-{hashlib.sha256(str(key).encode('utf-8')).hexdigest()[:16]}"


def _part_name(slot, upload):
    from app.lang import de_en

    if not (len(upload) == 32 and all(c in "0123456789abcdef" for c in upload)) or not re.fullmatch(r"\d+-[0-9a-f]{16}", slot or ""):
        raise ValueError(de_en("Der Upload ist abgelaufen oder unvollständig. Bitte die Datei noch einmal hochladen.",
                               "The upload has expired or is incomplete. Please upload the file again."))
    return f"{slot}.{upload}"


def _parts_bytes(uid, but=None):
    """Bytes the unfinished uploads of a survey take (`but`: the file name of one left out)."""
    total = 0
    try:
        with os.scandir(os.path.join(upload_folder(uid), ".parts")) as entries:
            for entry in entries:
                if entry.name != but:
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
    except OSError:
        pass
    return total


def media_bytes(survey, but=None, but_part=None):
    """Bytes the video and audio files of a survey take: the files of its stimuli (`but`: one left out) and the
    uploads not yet finished (`but_part`: the one under way left out; audit 2026-10-09, D1)."""
    from app.items.stimulus import EXTENSIONS

    total = 0
    for block in survey.blocks:
        for item in block.items:
            for asset in item.assets:
                if asset.filename != but and asset.filename.rsplit(".", 1)[-1] in EXTENSIONS:
                    try:
                        total += os.path.getsize(os.path.join(upload_folder(survey.uid), asset.filename))
                    except OSError:
                        pass
    return total + _parts_bytes(survey.uid, but_part)


class _PartLock:
    """One piece of a slot at a time: two requests with the same index both passed the check of the order and both
    appended (audit 2026-10-09, D1). A lock file made with O_EXCL; one left by a process that died goes after a
    minute (writing a piece takes milliseconds)."""

    def __init__(self, folder, slot):
        self.path = os.path.join(folder, slot + ".lock")

    def __enter__(self):
        import time

        from app.lang import de_en

        for _ in range(2):
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 60:
                        os.remove(self.path)
                        continue
                except OSError:
                    continue
                break
        raise ValueError(de_en("Teil der Datei in falscher Reihenfolge, bitte neu hochladen.",
                               "Part of the file out of order, please upload again."))

    def __exit__(self, *exc):
        _remove(self.path)


def add_media_part(survey, slot, upload, index, data, replaced=None):
    """Append piece `index` (from 0) of the upload `upload` (32 hex characters from the editor's script) in the slot
    `slot` (part_slot: one element and value); returns the bytes so far. The first piece gives up any other upload
    of the slot. ValueError (message for the researcher) for a piece out of order, a file over MEDIA_MAX_MB or a
    survey over MEDIA_SURVEY_MAX_MB, the unfinished uploads counted (`replaced`: the file this one takes the place
    of does not count)."""
    from app.items.stimulus import CHUNK_BYTES
    from app.lang import de_en

    name = _part_name(slot, upload)
    if index < 0:
        raise ValueError(de_en("Der Upload ist abgelaufen oder unvollständig. Bitte die Datei noch einmal hochladen.",
                               "The upload has expired or is incomplete. Please upload the file again."))
    folder = _parts(survey)
    path = os.path.join(folder, name)
    with _PartLock(folder, slot), writing(survey, 0, check=False):
        if index == 0:
            for other in os.listdir(folder):
                if other.startswith(slot + ".") and other != name and not other.endswith(".lock"):
                    _remove(os.path.join(folder, other))
        have = os.path.getsize(path) if os.path.exists(path) else 0
        if index * CHUNK_BYTES != have or len(data) > CHUNK_BYTES:
            raise ValueError(de_en("Teil der Datei in falscher Reihenfolge, bitte neu hochladen.",
                                   "Part of the file out of order, please upload again."))
        most = int(current_app.config["MEDIA_MAX_MB"]) * 1024 * 1024
        if have + len(data) > most:
            _remove(path)
            raise ValueError(de_en("Die Datei ist größer als {mb} MB. Bitte verkleinern (z. B. HandBrake, 720p).",
                                   "The file is larger than {mb} MB. Please make it smaller (e.g. HandBrake, 720p).",
                                   mb=current_app.config["MEDIA_MAX_MB"]))
        try:
            check_room(survey, len(data))  # every piece, under the shared admission lock
        except ValueError:
            _remove(path)
            raise
        room = int(current_app.config["MEDIA_SURVEY_MAX_MB"]) * 1024 * 1024 - media_bytes(survey, but=replaced, but_part=name)
        if have + len(data) > room:
            _remove(path)
            raise ValueError(de_en("Die Befragung hat keinen Platz mehr für Video und Audio (zusammen {mb} MB).",
                                   "The survey has no room left for video and audio ({mb} MB together).",
                                   mb=current_app.config["MEDIA_SURVEY_MAX_MB"]))
        try:
            with open(path, "ab") as f:
                f.write(data)
        except OSError:
            if os.path.exists(path):
                os.truncate(path, have)  # a partial piece must not break the next retry's index
            raise
    return have + len(data)


@tracked
def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def finish_media(survey, slot, upload):
    """The finished upload as a file of the survey: (file name, mime, bytes). ValueError if it is no video or audio
    the browser can play (its first bytes decide)."""
    from app.items.stimulus import MIMES, sniff
    from app.lang import de_en

    folder = _parts(survey)
    path = os.path.join(folder, _part_name(slot, upload))
    # Moving between .parts and the root during a quota walk could make an existing file disappear from its
    # count. Share admission, and serialize finalization with another piece of the same upload as well.
    with _PartLock(folder, slot), writing(survey, 0, check=False):
        try:
            with open(path, "rb") as f:
                head = f.read(16)
        except FileNotFoundError:
            raise ValueError(de_en("Der Upload ist abgelaufen oder unvollständig. Bitte die Datei noch einmal hochladen.",
                                   "The upload has expired or is incomplete. Please upload the file again.")) from None
        mime = sniff(head)
        if mime is None:
            _remove(path)
            raise ValueError(de_en("Nur MP4 oder WebM (Video) und MP3, M4A, OGG oder WAV (Audio).",
                                   "Only MP4 or WebM (video) and MP3, M4A, OGG or WAV (audio)."))
        name = f"{secrets.token_urlsafe(12)}.{MIMES[mime][1]}"
        size = os.path.getsize(path)
        os.replace(path, os.path.join(_folder(survey), name))
    return name, mime, size


def sweep_parts(now=None):
    """Remove the pieces of uploads not continued for PART_SECONDS, in every survey's folder (from the hourly
    housekeeping, lifecycle._rare_sweeps; before, nothing removed them; audit 2026-10-09, D1). Returns the number
    of files removed; never raises."""
    import time

    now = time.time() if now is None else now
    gone = 0
    try:
        with os.scandir(current_app.config["UPLOAD_FOLDER"]) as folders:
            parts = [os.path.join(f.path, ".parts") for f in folders if f.is_dir(follow_symlinks=False)]
    except OSError:
        return 0
    for folder in parts:
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if now - entry.stat().st_mtime > PART_SECONDS:
                            remove_file(entry.path)
                            gone += 1
                    except OSError:
                        pass
        except OSError:
            continue
    return gone


def optimize(data, max_side=1400, quality=80):
    with decoding():  # audit 2026-10-13, DA-3
        return _optimize(data, max_side, quality)


def participant_upload(data, max_side=1600):
    """Bound still uploads for mobile participants; keep transparency, colour profiles and animated stimuli."""
    import io

    with decoding():
        data = strip_metadata(data)
        with open_image(data, ["PNG", "JPEG", "GIF", "WEBP"]) as img:
            if getattr(img, "is_animated", False):
                return data
            icc = img.info.get("icc_profile")
            rights = image_rights.read(img)
            img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
            img.thumbnail((max_side, max_side))
            out = io.BytesIO()
            img.info.clear()
            img.save(out, format="WEBP", quality=85, method=5, **({"icc_profile": icc} if icc else {}),
                     **image_rights.save_options(rights, "WEBP"))
            return out.getvalue()


def _optimize(data, max_side=1400, quality=80):
    """Re-encode generated images as WebP at quality 80, at their resolution (at most max_side): AI PNGs are ~2 MB,
    participants often use mobile data. WebP 80 is ~37 % smaller than the JPEG 85 used before, with no visible
    difference (measured on 25 CBC images, Philipp 2026-10-08). Transparency becomes white, as before."""
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(data))
    rights = image_rights.read(img)
    if img.mode in ("RGBA", "LA", "P"):
        if img.mode == "P":
            img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        img = background
    else:
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.info.clear()
    img.save(out, format="WEBP", quality=quality, method=5, **image_rights.save_options(rights, "WEBP"))
    return out.getvalue()


def strip_metadata(data, max_pixels=MAX_PIXELS):
    with decoding():  # audit 2026-10-13, DA-3
        return _strip_metadata(data, max_pixels)


def _strip_metadata(data, max_pixels=MAX_PIXELS):
    """Uploaded images without private metadata (camera, GPS position, comments): participants load
    these files. Rebuild only attribution fields; the EXIF rotation is applied first and the colour profile stays. GIFs and animated
    WebP and PNG images are written anew frame by frame (_animated): they carried EXIF, GPS and XMP unchanged
    before (audit 2026-10-13, DA-2). Raises ValueError if the image cannot be read."""
    import io

    from PIL import ImageOps

    ext = _sniff(data)
    if ext is None:
        raise ValueError(de_en("Nur PNG, JPEG, GIF oder WebP.", "Only PNG, JPEG, GIF or WebP."))
    img = open_image(data, ["GIF"] if ext == "gif" else ["PNG", "JPEG", "WEBP"], max_pixels)
    try:
        if ext == "gif" or getattr(img, "is_animated", False):
            return _animated(img, max_pixels)
        fmt, icc, lossless = img.format, img.info.get("icc_profile"), img.info.get("lossless", False)
        rights = image_rights.read(img)
        img = ImageOps.exif_transpose(img)
        if fmt == "JPEG" and img.mode not in ("RGB", "L", "CMYK"):
            img = img.convert("RGB")
        options = {"JPEG": {"quality": 92, "optimize": True, "progressive": True}, "PNG": {"optimize": True},
                   "WEBP": {"quality": 92, "lossless": bool(lossless)}}[fmt]
        out = io.BytesIO()
        # PNG transparency is image content; metadata is supplied only through our allowlist below.
        img.info = {"transparency": img.info["transparency"]} if "transparency" in img.info else {}
        img.save(out, format=fmt, **options, **({"icc_profile": icc} if icc else {}),
                 **image_rights.save_options(rights, fmt))
    except ValueError:
        raise
    except Exception as e:  # truncated files, decompression bombs, unsupported variants
        raise ValueError(de_en("Das Bild konnte nicht gelesen werden.", "The image could not be read.")) from e
    return out.getvalue()


# all frames of a moving image together: each is decoded once and held until the image is written anew (Pillow keeps
# the frames of a GIF together while it writes them), at up to 4 bytes a pixel. 25 megapixels are about 100 MB and a
# second or two, e.g. 100 frames of 500 x 500; 200 megapixels took about 1 GB and 12 s from a file of 0.4 MB, and
# eight uploads at once could take the researchers' container down (audit 2026-10-15, T03-N1)
ANIMATED_MAX_PIXELS = 25_000_000
FRAME_KEYS = ("duration", "transparency", "disposal", "blend", "background")


def _animated(img, max_pixels=MAX_PIXELS):
    """A GIF or a moving WebP or PNG written anew in its format, frame by frame with the times and the loop, without
    private EXIF, XMP, comments and other chunks. The colour profile and allowlisted attribution stay."""
    import io

    from PIL import ImageSequence

    fmt = img.format
    frames = getattr(img, "n_frames", 1)
    if frames * img.width * img.height > min(max_pixels, ANIMATED_MAX_PIXELS):
        raise ValueError(de_en("Die Animation hat zu viele Bilder für ihre Größe.",
                               "The animation has too many frames for its size."))
    icc, loop = img.info.get("icc_profile"), img.info.get("loop")
    rights = image_rights.read(img)
    copies, durations = [], []
    for frame in ImageSequence.Iterator(img):
        frame.load()  # WebP names the time of a frame only once it is decoded
        durations.append(frame.info.get("duration") or 100)
        copy = frame.copy()
        copy.info = {k: v for k, v in frame.info.items() if k in FRAME_KEYS}
        copies.append(copy)
    options = {"save_all": True, "append_images": copies[1:], "duration": durations,
               **({} if loop is None else {"loop": loop})}  # a GIF without a loop plays once, as before
    if fmt == "WEBP":
        options.update(lossless=bool(img.info.get("lossless", False)), quality=92)
    elif fmt == "GIF":
        options.update(comment=b"")
    if icc and fmt != "GIF":
        options["icc_profile"] = icc
    options.update(image_rights.save_options(rights, fmt))
    out = io.BytesIO()
    copies[0].save(out, format=fmt, **options)
    return out.getvalue()


def fit_still(data, max_side=1400):
    with decoding():  # audit 2026-10-13, DA-3
        return _fit_still(data, max_side)


def _fit_still(data, max_side=1400):
    """An uploaded image for a choice task (conjoint): a still image with attribution only, at most max_side px wide
    or high like the generated ones, so participants on mobile data do not load several photos of a few MB per
    task. Same format, transparency stays. Moving images are refused: motion draws the eye to one option and
    distorts the choice. Raises ValueError (message for the researcher)."""
    import io

    data = strip_metadata(data)
    img = open_image(data, ["PNG", "JPEG", "GIF", "WEBP"])
    if getattr(img, "is_animated", False):
        raise ValueError(de_en("Bewegte Bilder (z. B. animierte GIFs) gehen hier nicht; bitte ein Standbild hochladen.",
                               "Moving images (e.g. animated GIFs) do not work here; please upload a still image."))
    if max(img.size) <= max_side:
        return data
    try:
        fmt, icc, lossless = img.format, img.info.get("icc_profile"), img.info.get("lossless", False)
        rights = image_rights.read(img)
        img.thumbnail((max_side, max_side))
        options = {"JPEG": {"quality": 92, "optimize": True, "progressive": True}, "PNG": {"optimize": True},
                   "GIF": {}, "WEBP": {"quality": 92, "lossless": bool(lossless)}}[fmt]
        out = io.BytesIO()
        img.save(out, format=fmt, **options, **({"icc_profile": icc} if icc and fmt != "GIF" else {}),
                 **image_rights.save_options(rights, fmt))
    except Exception as e:  # truncated files, unsupported variants
        raise ValueError(de_en("Das Bild konnte nicht gelesen werden.", "The image could not be read.")) from e
    return out.getvalue()


SERVED ={".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
# video and audio stimuli (items/stimulus.py): the type is set here, not guessed from the system's table
MEDIA_TYPES = {".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
               ".ogg": "audio/ogg", ".wav": "audio/wav", ".weba": "audio/webm"}
LOGO_MAX_BYTES = 2 * 1024 * 1024
LOGO_MAX_SIDE = 1200


def save_logo(survey, data):
    with decoding():  # audit 2026-10-13, DA-3
        return _save_logo(survey, data)


def _save_logo(survey, data):
    """Store a logo for the survey pages: PNG, JPEG or WebP (with attribution only, at most 1200 px wide or high,
    transparency kept) or SVG (written anew from an allow-list, app/svg_safe.py). Returns
    {"file", "kind", "w", "h"} or raises ValueError with a message for the researcher."""
    from app import svg_safe
    from app.lang import de_en

    if svg_safe.looks_like_svg(data):
        clean, width, height = svg_safe.clean(data)
        name = f"{secrets.token_urlsafe(12)}.svg"
        path = os.path.join(_folder(survey), name)
        with writing(survey, len(clean)):
            try:
                with open(path, "xb") as f:
                    f.write(clean)
            except OSError:
                _remove(path)
                raise
        return {"file": name, "kind": "svg", "w": width, "h": height}
    if len(data) > LOGO_MAX_BYTES:
        raise ValueError(de_en("Das Logo ist größer als 2 MB.", "The logo is larger than 2 MB."))
    kind = _sniff(data)
    if kind not in ("png", "jpg", "webp"):
        raise ValueError(de_en("Für das Logo: PNG, JPEG, WebP oder SVG.", "For the logo: PNG, JPEG, WebP or SVG."))
    import io

    data = strip_metadata(data, LOGO_MAX_PIXELS)
    try:
        img = open_image(data, ["PNG", "JPEG", "WEBP"], LOGO_MAX_PIXELS)
        if getattr(img, "is_animated", False):
            raise ValueError(de_en("Animierte Bilder eignen sich nicht als Logo.", "Animated images are not suitable as a logo."))
        if max(img.size) > LOGO_MAX_SIDE:
            fmt, icc = img.format, img.info.get("icc_profile")
            rights = image_rights.read(img)
            img.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE))
            out = io.BytesIO()
            options = {"JPEG": {"quality": 92, "optimize": True}, "PNG": {"optimize": True}, "WEBP": {"quality": 92}}[fmt]
            img.save(out, format=fmt, **options, **({"icc_profile": icc} if icc else {}),
                     **image_rights.save_options(rights, fmt))
            data = out.getvalue()
        width, height = img.size
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(de_en("Das Bild konnte nicht gelesen werden.", "The image could not be read.")) from e
    return {"file": save_bytes(survey, data), "kind": kind, "w": width, "h": height}


ICON_SIDE = 64
ICON_MAX_ASPECT = 2.0  # a wide word mark shrunk into a square is a grey line, not an icon


def save_icon(survey, logo_file):
    """A small square icon for the browser tab, made from a logo that is about square: a PNG of 64 px for raster
    logos, the file itself for SVG logos. Returns the file name, or None if the logo is too wide or too tall."""
    path = os.path.join(_folder(survey), logo_file)
    if logo_file.lower().endswith(".svg"):
        from app import svg_safe

        with open(path, "rb") as f:
            _, width, height = svg_safe.clean(f.read())
        return logo_file if max(width, height) / max(1, min(width, height)) <= ICON_MAX_ASPECT else None
    import io

    from PIL import Image

    with Image.open(path) as img:
        if max(img.size) / max(1, min(img.size)) > ICON_MAX_ASPECT:
            return None
        rights = image_rights.read(img)
        img = img.convert("RGBA")
        img.thumbnail((ICON_SIDE, ICON_SIDE))
        canvas = Image.new("RGBA", (ICON_SIDE, ICON_SIDE), (0, 0, 0, 0))
        canvas.paste(img, ((ICON_SIDE - img.width) // 2, (ICON_SIDE - img.height) // 2), img)
        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=True, **image_rights.save_options(rights, "PNG"))
    return save_bytes(survey, out.getvalue())


def logo_colours(survey, logo_file, limit=5):
    """The main colours of a logo as #rrggbb, most frequent first, without white, black and greys close to them
    (those are in every logo and no accent). For "Farben aus dem Logo" on the theme page."""
    import re

    path = os.path.join(_folder(survey), logo_file)
    counts = {}
    if logo_file.lower().endswith(".svg"):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for value in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", text):
            value = value.lower()
            if len(value) == 4:
                value = "#" + "".join(c * 2 for c in value[1:])
            counts[value] = counts.get(value, 0) + 1
    else:
        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("RGBA")
            img.thumbnail((160, 160))
            solid = Image.new("RGB", img.size, (255, 255, 255))
            solid.paste(img, mask=img.split()[-1])
            visible = [p for p, a in zip(solid.getdata(), img.split()[-1].getdata()) if a > 128]
        if not visible:
            return []
        flat = Image.new("RGB", (len(visible), 1))
        flat.putdata(visible)
        quant = flat.quantize(colors=12)
        palette = quant.getpalette()
        for count, index in quant.getcolors():
            r, g, b = palette[index * 3:index * 3 + 3]
            counts[f"#{r:02x}{g:02x}{b:02x}"] = count
    out = []
    for value, _ in sorted(counts.items(), key=lambda kv: -kv[1]):
        r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
        grey = max(r, g, b) - min(r, g, b) < 24
        if grey and (min(r, g, b) > 200 or max(r, g, b) < 60):
            continue  # paper and ink
        if any(sum(abs(x - y) for x, y in zip((r, g, b), tuple(int(o[i:i + 2], 16) for i in (1, 3, 5)))) < 48 for o in out):
            continue  # a shade of one already taken
        out.append(value)
        if len(out) == limit:
            break
    return out


def save_b64(survey, b64):
    """Generated images: stored optimized as WebP (optimize)."""
    return save_bytes(survey, optimize(base64.b64decode(b64)))


_DOOMED = "media_doomed"
_NEW_FILES = "media_new_files"


def delete_after_rollback(survey, filename):
    """A newly written file has no durable owner until the database commits."""
    from app.extensions import db

    if filename:
        db.session().info.setdefault(_NEW_FILES, set()).add(os.path.join(upload_folder(survey.uid), filename))


def delete_after_commit(survey, filename):
    """Delete the file once the session commits, so that a failed commit leaves the survey with its files
    (a rollback keeps them)."""
    from app.extensions import db

    if filename:
        db.session().info.setdefault(_DOOMED, []).append(os.path.join(upload_folder(survey.uid), filename))


@event.listens_for(Session, "after_commit")
def _remove_doomed(session):
    session.info.pop(_NEW_FILES, None)
    for path in session.info.pop(_DOOMED, []):
        try:
            _delete_image_path(path)
        except FileNotFoundError:
            pass


@event.listens_for(Session, "after_rollback")
def _keep_doomed(session):
    session.info.pop(_DOOMED, None)
    for path in session.info.pop(_NEW_FILES, set()):
        try:
            _delete_image_path(path)
        except FileNotFoundError:
            pass


_SIZES = {}


def size(survey_uid, filename):
    """Width and height of an uploaded image, for the width/height of its <img> (the browser keeps the room free
    while it loads, so the page does not jump; the CBC concepts flickered without, Philipp 2026-10-08). Read once
    per file and version (mtime); (None, None) if the file is not there or not an image."""
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], survey_uid, filename or "")
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None, None
    if key not in _SIZES:
        try:
            from PIL import Image

            with Image.open(path) as img:
                _SIZES[key] = img.size
        except Exception:
            _SIZES[key] = (None, None)
    return _SIZES[key]


def exists(survey, filename):
    return bool(filename) and os.path.isfile(os.path.join(upload_folder(survey.uid), filename))


def _delete_image_path(path):
    from app import report_jobs

    if not os.path.isdir(os.path.dirname(path)):
        return
    raster = os.path.splitext(path)[1].lower() in {".png", ".jpg", ".jpeg", ".webp"}
    if raster:
        with report_jobs._claim(path + ".thumbnail.claim"):
            small = os.path.splitext(path)[0] + "-400.webp"
            import glob

            _delete_paths([path, small, *glob.glob(small + ".tmp-*")])
    else:
        _delete_paths([path])


@tracked
def _delete_paths(paths):
    for path in paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def delete(survey, filename):
    _delete_image_path(os.path.join(_folder(survey), filename))


MEDIA_MAX_AGE = 86400  # seconds a browser keeps an image of /media (private, never in a shared cache)


@bp.route("/<survey_uid>/<filename>")
def serve(survey_uid, filename):
    if not survey_uid.replace("-", "").replace("_", "").isalnum():
        abort(404)
    # images only: other things lie next to the survey folders (progress notes, the settings of an institution's
    # logo) and are nobody's to fetch
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SERVED and ext not in MEDIA_TYPES:
        abort(404)
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], survey_uid)
    # media go in ranges (Safari asks for them; seeking loads from the place sought): send_file answers 206
    response = send_from_directory(folder, filename, max_age=MEDIA_MAX_AGE, mimetype=MEDIA_TYPES.get(ext), conditional=True)
    # private: the browser keeps it a day, no shared cache or CDN keeps it at all, so an image withdrawn (its rights,
    # a logo) is gone from the address once it is deleted; until 2026-10-18 public, a year and immutable (audit
    # 2026-10-18, INJ-3). The ETag keeps asking again cheap.
    response.cache_control.public = False
    response.cache_control.private = True
    if current_app.config.get("MEDIA_X_ACCEL") and response.status_code in (200, 206):
        from urllib.parse import quote

        response.close()
        response.response = []
        response.direct_passthrough = False
        response.headers.pop("Content-Length", None)
        response.headers["X-Accel-Redirect"] = "/_protected_media/" + quote(survey_uid, safe="") + "/" + quote(filename, safe="")
    response.headers["X-Content-Type-Options"] = "nosniff"
    if filename.lower().endswith(".svg"):
        # an SVG is a document: opened by its address it must not run or load anything (logos are cleaned on
        # upload and shown with <img>, where nothing runs anyway; this is the last line)
        response.headers["Content-Type"] = "image/svg+xml"
        response.headers["Content-Security-Policy"] = ("default-src 'none'; style-src 'unsafe-inline'; sandbox; "
                                                       "frame-ancestors 'self'")
    return response



def thumbnail_name(uid, filename):
    """A precomputed small raster, otherwise the original; never generate files from a public GET."""
    small = os.path.splitext(filename)[0] + "-400.webp"
    return small if os.path.isfile(os.path.join(current_app.config["UPLOAD_FOLDER"], uid, small)) else filename


def make_thumbnail(survey, filename):
    from app import report_jobs

    if os.path.splitext(filename)[1].lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    try:
        with report_jobs._claim(os.path.join(_folder(survey), filename) + ".thumbnail.claim"):
            return _make_thumbnail(survey, filename)
    except (OSError, ValueError):
        return None


def _make_thumbnail(survey, filename):
    """Optional 400px WebP copy for small tiles. Preserve the original and skip animation/SVG."""
    if os.path.splitext(filename)[1].lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    import io

    path = os.path.join(_folder(survey), filename)
    small = os.path.splitext(filename)[0] + "-400.webp"
    if os.path.isfile(os.path.join(_folder(survey), small)):
        return small
    try:
        with decoding(), open(path, "rb") as source, open_image(source.read(), ["PNG", "JPEG", "WEBP"]) as image:
            if getattr(image, "is_animated", False) or max(image.size) <= 400:
                return None
            profile = image.info.get("icc_profile")
            rights = image_rights.read(image)
            image.thumbnail((400, 400))
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA")
            data = io.BytesIO()
            image.info.clear()
            image.save(data, format="WEBP", quality=85, **({"icc_profile": profile} if profile else {}),
                       **image_rights.save_options(rights, "WEBP"))
        value = data.getvalue()
        target = os.path.join(_folder(survey), small)
        temporary = target + ".tmp-" + secrets.token_hex(8)
        try:
            with writing(survey, 0, check=False):
                if os.path.isfile(target):
                    return small
                check_room(survey, len(value))
                with open(temporary, "wb") as output:
                    output.write(value)
                os.replace(temporary, target)  # never cache a partially written image under its immutable URL
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass
        return small
    except (OSError, ValueError):
        return None  # an optimisation must not reject an otherwise valid upload


def size_static(filename):
    """Dimensions of our static screenshots; source is a fixed local basename, never a network URL."""
    path = os.path.join(current_app.static_folder, filename)
    try:
        key = (path, os.path.getmtime(path))
        if key not in _SIZES:
            from PIL import Image

            with Image.open(path) as image:
                _SIZES[key] = image.size
        return _SIZES[key]
    except (OSError, ValueError):
        return None, None
