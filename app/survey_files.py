"""Survey files as ZIP, the preview of what an upload holds, and several surveys in one file.

app/transfer.py writes a survey as one JSON file with its images inside as base64. Here the same survey goes into
a ZIP: befragung.json (its "files" empty) and the images as files of their own under dateien/, a quarter smaller
than base64 and so readable where the JSON is above the upload limit. Several surveys go into one ZIP, a folder
each (<title>/befragung.json, <title>/dateien/...). An upload is any of these: a JSON file, a ZIP of one survey,
a ZIP of several, or a ZIP whose JSON holds its images itself (the pre-registration snapshot, app/prereg.py).

Reading a ZIP is reading what a stranger wrote, as in transfer.py: nothing is unpacked onto the disk, no name
in it is a path, and every member is read with a bound, whatever its header says (a few kilobytes can unpack
to gigabytes). Each survey is then checked and created by transfer.loads and transfer.create.
"""
import hashlib
import io
import re
import tempfile
import zipfile
import zlib
from collections import Counter
from datetime import datetime
from pathlib import Path

from flask import current_app

from app import brand, packages, transfer
from app.lang import current, de_en, english
from app.transfer import Refused, _dict, _dicts, _str

JSON_NAME, FOLDER = packages.SURVEY, packages.FILES  # older ZIPs: befragung.json and dateien/ (packages.SURVEY_NAMES)


def content_hash(content):
    if isinstance(content, Path):
        with content.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()
    return hashlib.sha256(content).hexdigest()


def snapshot_zip(files, stamp=None):
    """Snapshot members as bytes or local Paths, bounded and spilled to disk; caller owns the returned handle."""
    out = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)  # noqa: SIM115 - caller owns success; errors close below
    left = max(1, int(current_app.config.get("EXPORT_ZIP_MAX_BYTES", 5 * 1024 ** 3)))
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in files:
                info = zipfile.ZipInfo(name, date_time=stamp) if stamp else zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_STORED if isinstance(content, Path) else zipfile.ZIP_DEFLATED
                with archive.open(info, "w", force_zip64=True) as target:
                    if isinstance(content, Path):
                        with content.open("rb") as source:
                            while chunk := source.read(65536):
                                left -= len(chunk)
                                if left < 0:
                                    raise transfer.ExportTooLarge(de_en("Der ZIP-Export ist zu groß. Bitte ohne Bilder exportieren.",
                                                                       "The ZIP export is too large. Please export without images."))
                                target.write(chunk)
                    else:
                        left -= len(content)
                        if left < 0:
                            raise transfer.ExportTooLarge(de_en("Der ZIP-Export ist zu groß. Bitte ohne Bilder exportieren.",
                                                               "The ZIP export is too large. Please export without images."))
                        target.write(content)
        out.seek(0)
        return out
    except BaseException:
        out.close()
        raise


MAX_SURVEYS = 50            # surveys in one upload, each shown in the preview and created one after the other
MAX_MEMBERS = 10_000        # entries of a ZIP: a survey has at most transfer.MAX_FILES images
FILE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}\Z")  # images as the app names them (media.py)
# a folder or a JSON file of a ZIP: any name of one part, but no "..", no hidden name, no control character (the
# folders of several surveys are named after their titles, export.slug)
PART_NAME = re.compile(r"^[^./\\\x00-\x1f][^/\\\x00-\x1f]{0,199}\Z")
README = """Befragungen aus Askurious: Fragebogen und Einstellungen, ohne Teilnahmen und Daten.

{lines}
Einlesen: unter „Meine Befragungen“ > „+ Fragebogen“ > „Aus einer Datei anlegen“ diese ZIP-Datei als Ganzes auswählen. Eine Vorschau
zeigt, was sie enthält{many}.
"""
README_EN = """Surveys from Askurious: questionnaire and settings, without participations and data.

{lines}
Reading in: under “My surveys” > “+ Questionnaire” > “Create from a file”, choose this ZIP file as a whole. A preview
shows what it holds{many}.
"""


def is_zip(raw):
    return raw[:4] == b"PK\x03\x04"


# --- writing ------------------------------------------------------------------------------------------


def file_name(surveys):
    if len(surveys) == 1:
        return transfer.file_name(surveys[0])[:-len(".json")] + ".zip"
    from app.timeutil import local_today  # the date in Europe/Berlin, not the container's UTC (G-07)
    return f"befragungen-{local_today():%Y%m%d}.zip"


def write(surveys, with_files=True, lang=None):
    """A ZIP of these surveys in a temporary file (at the start, for send_file). One survey: survey.json and
    files/ at the top; several: a folder each, named after the title; README.md and checksums.sha256 at the top
    (app/packages.py). lang: the language of README.md (the downloader's, E8; German outside a request); the file
    names stay."""
    from app import export

    en = (lang or current()) == "en"

    out = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)  # noqa: SIM115 - caller owns success; errors close below
    used, lines, sums = set(), [], []
    left = max(1, int(current_app.config.get("EXPORT_ZIP_MAX_BYTES", 5 * 1024 ** 3)))

    def put(target, chunk, digest):
        nonlocal left
        digest.update(chunk)
        left -= len(chunk)
        if left < 0:
            raise transfer.ExportTooLarge(de_en("Der ZIP-Export ist zu groß. Bitte weniger Befragungen oder ohne Bilder exportieren.",
                                               "The ZIP export is too large. Please export fewer surveys or without images."))
        target.write(chunk)

    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for survey in surveys:
                prefix = ""
                if len(surveys) > 1:
                    base = export.slug(survey.title) or "befragung"
                    name, n = base, 2
                    while name.casefold() in used:
                        name, n = f"{base}-{n}", n + 1
                    used.add(name.casefold())
                    prefix = name + "/"
                images = {}
                data = transfer.dump(survey, with_files, raw=images, media=with_files)  # video and audio too (E8)
                digest = hashlib.sha256()
                with z.open(prefix + JSON_NAME, "w", force_zip64=True) as target:
                    for chunk in transfer.json_chunks(data):
                        put(target, chunk.encode("utf-8"), digest)
                sums.append(f"{digest.hexdigest()}  {prefix}{JSON_NAME}\n")
                for fname, path in images.items():
                    info = zipfile.ZipInfo(f"{prefix}{FOLDER}/{fname}")
                    info.compress_type = zipfile.ZIP_STORED
                    digest = hashlib.sha256()
                    with open(path, "rb") as source, z.open(info, "w", force_zip64=True) as target:
                        # Includes files that grow after discovery; no whole-file read or bytes dictionary.
                        while chunk := source.read(64 * 1024):
                            put(target, chunk, digest)
                    sums.append(f"{digest.hexdigest()}  {prefix}{FOLDER}/{fname}\n")
                lines.append(f"- {prefix}{JSON_NAME}: {survey.title}"
                             + ((f" ({len(images)} image and media files)" if en else f" ({len(images)} Bild- und Mediendateien)") if images else ""))
            many = (" and which are read in" if en else " und welche eingelesen werden") if len(surveys) > 1 else ""
            z.writestr(packages.README, brand.apply(README_EN if en else README).format(lines="\n".join(lines) + "\n",
                                                                                     many=many).encode("utf-8"))
            z.writestr(packages.CHECKSUMS, "".join(sums).encode("utf-8"))
        out.seek(0)
        return out
    except BaseException:
        out.close()
        raise


# --- reading ------------------------------------------------------------------------------------------


class Part:
    """One survey of an upload: the bytes of its file, where its images are (fetch, for transfer.create; None: in
    the file itself) and how many there are and how large. The file is parsed only while it is needed (load): for
    the preview one part after the other, for reading in one survey after the other, never all at once (a ZIP of
    a few kilobytes can hold dozens of files that each take a gigabyte once they are read)."""

    def __init__(self, body, fetch=None, images=(0, 0)):
        self.body, self.fetch = body, fetch
        self.image_count, self.image_bytes = images
        self.inline = not images[0]  # no image files beside it: any images are in the file itself

    def load(self):
        """The dict of the file (transfer.loads): a new one each time, for the caller to let go of."""
        data = transfer.loads(self.body)
        if self.inline:
            files = _dict(data.get("files"))
            self.image_count = sum(1 for v in files.values() if isinstance(v, str))
            self.image_bytes = sum(len(v) * 3 // 4 for v in files.values() if isinstance(v, str))
        return data


def _structure(body):
    return sum(body.count(mark) for mark in (b",", b"[", b"{"))


def _bounded(z, info, limit):
    """The bytes of a member, or None if it holds more than limit (the header is not trusted) or cannot be read."""
    if info.flag_bits & 0x1 or info.file_size > limit:
        return None
    try:
        with z.open(info) as f:
            blob = f.read(limit + 1)
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError, zlib.error, RuntimeError, ValueError):
        return None
    return blob if len(blob) <= limit else None


def read(raw):
    """[Part] of an upload, in the order of the ZIP's names; Refused if it holds no survey file or too much. All
    survey files of an upload together keep the limits of one: IMPORT_MAX_BYTES unpacked, transfer.MAX_STRUCTURE
    entries. Nothing is parsed here (Part.load)."""
    if not is_zip(raw):
        if _structure(raw) > transfer.MAX_STRUCTURE:
            raise Refused(de_en("Die Datei enthält mehr Einträge, als eine Befragung haben kann.",
                                "The file holds more entries than a survey can have."))
        return [Part(raw)]
    from app import zip_limits

    if zip_limits.directory(raw) is None:  # no end record that fits (audit 2026-10-15, T03-N2)
        raise Refused(de_en("Die ZIP-Datei ist beschädigt und lässt sich nicht öffnen.",
                            "The ZIP file is damaged and cannot be opened."))
    if zip_limits.too_many(raw, MAX_MEMBERS):  # counted from the end record, before zipfile reads them (DA-4)
        raise Refused(de_en("Die ZIP-Datei enthält mehr Einträge, als Befragungen haben können.",
                            "The ZIP file holds more entries than surveys can have."))
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        infos = z.infolist()
    except (zipfile.BadZipFile, ValueError, OSError, EOFError):
        raise Refused(de_en("Die ZIP-Datei ist beschädigt und lässt sich nicht öffnen.",
                            "The ZIP file is damaged and cannot be opened.")) from None
    if len(infos) > MAX_MEMBERS:
        raise Refused(de_en("Die ZIP-Datei enthält mehr Einträge, als Befragungen haben können.",
                            "The ZIP file holds more entries than surveys can have."))
    members = {}
    for info in infos:
        if not info.is_dir():
            members.setdefault(info.filename, info)  # a name twice: the first
    # a survey file at the top or one folder down; other JSON files (not from Askurious) are passed over
    candidates = sorted(name for name in members if name.lower().endswith(".json") and name.count("/") <= 1
                        and "\\" not in name and all(PART_NAME.match(p) for p in name.split("/")))
    budget, entries, parts = current_app.config["IMPORT_MAX_BYTES"], transfer.MAX_STRUCTURE, []
    for name in candidates:
        info = members[name]
        if info.flag_bits & 0x1:
            raise Refused(de_en("Die ZIP-Datei ist verschlüsselt. Bitte ohne Passwort packen.",
                                "The ZIP file is encrypted. Please pack it without a password."))
        body = _bounded(z, info, budget)
        if body is None:
            raise Refused(de_en("Die Dateien in der ZIP-Datei sind zusammen zu groß oder beschädigt. Bitte in kleineren "
                                "Teilen einlesen.",
                                "The files in the ZIP file are too large together or damaged. Please read them in in "
                                "smaller parts."))
        budget -= len(body)
        if not any(f'"{name}"'.encode() in body for name in transfer.FORMATS):
            continue
        entries -= _structure(body)
        if entries < 0:
            raise Refused(de_en("Die Befragungen der ZIP-Datei haben zusammen mehr Einträge, als eingelesen werden können. "
                                "Bitte in kleineren Teilen einlesen.",
                                "The surveys of the ZIP file together have more entries than can be read in. Please "
                                "read them in in smaller parts."))
        if len(parts) == MAX_SURVEYS:
            raise Refused(de_en("Die ZIP-Datei enthält mehr als {n} Befragungen. Bitte in kleineren Teilen einlesen.",
                                "The ZIP file holds more than {n} surveys. Please read them in in smaller parts.", n=MAX_SURVEYS))
        prefix = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
        # files/ since 2026-10-23, dateien/ before (packages.FILE_FOLDERS): the first that holds images
        folder = next((f"{prefix}{f}/" for f in packages.FILE_FOLDERS
                       if any(n.startswith(f"{prefix}{f}/") for n in members)), f"{prefix}{FOLDER}/")
        images = [i for n, i in members.items() if n.startswith(folder) and FILE_NAME.match(n[len(folder):])]

        def fetch(fname, most, folder=folder):
            info = members.get(folder + fname) if FILE_NAME.match(fname) else None
            return _bounded(z, info, most) if info is not None else None

        parts.append(Part(body, fetch, (len(images), sum(i.file_size for i in images))))
    if not parts:
        raise Refused(de_en("Die ZIP-Datei enthält keine Befragungsdatei von Askurious (erwartet wird eine Datei aus „Als "
                            "Datei exportieren“).",
                            "The ZIP file holds no survey file of Askurious (expected is a file from “Export as a "
                            "file”)."))
    return parts


# --- the preview --------------------------------------------------------------------------------------

FLOW_NAMES = (("rand", "Randomisierung", "Randomisierungen"), ("loop", "Schleife", "Schleifen"),
              ("branch", "Bedingung", "Bedingungen"), ("split", "getrennter Abschnitt", "getrennte Abschnitte"),
              ("ext", "externer Abschnitt", "externe Abschnitte"), ("ws", "Web Service", "Web Services"),
              ("quota", "Quotenberechnung", "Quotenberechnungen"),
              ("end", "Ende", "Enden"))
FLOW_NAMES_EN = (("rand", "randomisation", "randomisations"), ("loop", "loop", "loops"),
                 ("branch", "condition", "conditions"), ("split", "separate section", "separate sections"),
                 ("ext", "external section", "external sections"), ("ws", "Web Service", "Web Services"),
                 ("quota", "quota calculation", "quota calculations"),
                 ("end", "end", "ends"))


def flow_names():
    """FLOW_NAMES in the language of the page."""
    return FLOW_NAMES_EN if english() else FLOW_NAMES


def _count(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _flow_kinds(tree):
    """{kind: number} of the flow's nodes, walked within the limits transfer._flow keeps."""
    kinds, stack, seen = Counter(), [(tree, 0)], 0
    while stack:
        node, depth = stack.pop()
        for child in _dicts(_dict(node).get("children")):
            seen += 1
            if seen > transfer.MAX_FLOW_NODES or depth > transfer.MAX_FLOW_DEPTH:
                return kinds
            if isinstance(child.get("t"), str):
                kinds[child["t"]] += 1
            stack.append((child, depth + 1))
    return kinds


def summary(part, user):
    """What the preview shows of one survey: read from the file as it is, for the eye only. What is read in is
    decided by transfer.create, which checks every value."""
    from app import items
    from app.surveys import LANGUAGE_NAMES

    data = part.load()  # let go of when the summary is made
    entry = _dict(data.get("survey"))
    title = transfer.title_of(data)
    rows = [i for b in _dicts(data.get("blocks"))[:transfer.MAX_BLOCKS] for i in _dicts(b.get("items"))][:transfer.MAX_ITEMS]
    types = Counter(r.get("type") for r in rows if isinstance(r.get("type"), str))
    known = sorted(((items.REGISTRY[t].label or t, n) for t, n in types.items() if t in items.REGISTRY), key=lambda x: (-x[1], x[0]))
    unknown = sum(n for t, n in types.items() if t not in items.REGISTRY) + sum(1 for r in rows if not isinstance(r.get("type"), str))
    kinds = _flow_kinds(data.get("flow"))
    look = _dict(data.get("theme"))
    quotas = [_str(q.get("name"), 100, line=True) or _str(q.get("code"), 40, line=True) for q in _dicts(data.get("quotas"))]
    languages = [LANGUAGE_NAMES.get(l, l) for l in (entry.get("languages") if isinstance(entry.get("languages"), list) else []) if isinstance(l, str) and l in LANGUAGE_NAMES]
    stamp = _str(data.get("exported_at"), 30, line=True)
    try:
        exported = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        exported = None
    twin = transfer.twin_of(user, title) if title else None
    return {
        "title": title, "project": _str(entry.get("project"), 200, line=True), "live": isinstance(data.get("live"), dict),
        "interview": isinstance(data.get("interview"), dict),
        "version": data.get("version"), "exported": exported, "languages": languages,
        "blocks": len(_dicts(data.get("blocks"))), "item_count": len(rows), "types": known, "unknown": unknown,
        "flow": [_count(kinds[k], one, many) for k, one, many in flow_names() if kinds[k]],
        "quotas": [q for q in quotas if q], "boards": len(_dicts(data.get("boards"))),
        "images": part.image_count, "image_mb": part.image_bytes / (1024 * 1024),
        "logo": bool(_dict(look.get("logo"))), "look": bool(look),
        "params": len(_dicts(_dict(entry.get("panel")).get("params"))),
        "twin": twin, "suggested": transfer.suggested_title(title) if twin else title,
    }
