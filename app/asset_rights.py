"""Researcher declarations for distributing study images in a public archive.

Embedded metadata supplies suggestions only. A declaration is bound to both the image bytes and the entered
terms, and is set only by the signed-in editor. This records their statement, not a legal verification.
"""
import copy
import hashlib
import json
import os
import re
from types import SimpleNamespace

from app import image_rights
from app.lang import de_en

BASES = ("own", "license", "permission", "ai")
FIELDS = (*image_rights.FIELDS, "basis", "permission")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class RightsError(ValueError):
    pass


def _text(value):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)[:4096].strip() if isinstance(value, str) else ""


def details(value):
    """Bounded declarations only; importers can never manufacture a local confirmation."""
    value = value if isinstance(value, dict) else {}
    result = {field: _text(value.get(field)) for field in FIELDS}
    if result["basis"] not in BASES:
        result["basis"] = ""
    return result


def image_assets(survey, approved_only=True):
    from app.models import Asset

    return [a for b in survey.blocks for item in b.items if item.type != "media" for a in item.assets
            if (not approved_only or a.status == Asset.APPROVED) and os.path.splitext(a.filename)[1].lower() in IMAGE_EXTENSIONS]


class ThemeImage:
    """The same rights form for a local logo/icon, backed by Survey.theme rather than a fictitious Asset row."""
    source, model = "upload", ""

    def __init__(self, survey, filename, label):
        self.survey, self.filename, self.id = survey, filename, "theme:" + filename
        self.item = SimpleNamespace(code=label, block=SimpleNamespace(survey=survey))

    @property
    def rights(self):
        return ((self.survey.theme or {}).get("image_rights") or {}).get(self.filename)

    @rights.setter
    def rights(self, value):
        self.survey.theme = {**(self.survey.theme or {}), "image_rights": {
            **((self.survey.theme or {}).get("image_rights") or {}), self.filename: value}}


def theme_images(survey):
    from app import theme

    look = theme.clean(survey.theme) or {}
    logo = look.get("logo") or {}
    names = [(logo.get(k), label) for k, label in (("file", "Logo"), ("file_dark", "Logo (dark)"), ("icon", "Icon"))]
    names += [(entry["file"], "Partner logo") for entry in look.get("partners", [])]
    seen, result = set(), []
    for filename, label in names:
        if filename and filename not in seen:
            seen.add(filename)
            result.append(ThemeImage(survey, filename, label))
    return result


def clean_theme_records(raw, foreign=False):
    """Keep this bounded extension through theme edits; foreign imports always lose their claimed approval."""
    if not isinstance(raw, dict):
        return {}
    result = {}
    for name, value in list(raw.items())[:24]:
        if not isinstance(name, str) or not re.fullmatch(r"[\w-]{1,180}\.(?:png|jpe?g|webp|svg|gif)", name):
            continue
        value = value if isinstance(value, dict) else {}
        if foreign:
            result[name] = imported(value)
        else:
            record = details(value)
            confirmation = value.get("confirmation")
            if isinstance(confirmation, dict):
                record["confirmation"] = {k: _text(confirmation.get(k)) for k in
                                           ("scope", "by", "at", "sha256", "details_sha256")}
                if isinstance(confirmation.get("user_id"), int) and not isinstance(confirmation["user_id"], bool):
                    record["confirmation"]["user_id"] = confirmation["user_id"]
            if value.get("imported_unconfirmed") is True:
                record["imported_unconfirmed"] = True
            if isinstance(value.get("copied_from"), dict):
                record["copied_from"] = {"survey_uid": _text(value["copied_from"].get("survey_uid"))}
            result[name] = record
    return result


def copy_theme_records(source, target):
    original = {digest: copy.deepcopy(a.rights) for a in theme_images(source)
                if a.rights and (digest := file_hash(source, a))}
    for asset in theme_images(target):
        value = original.get(file_hash(target, asset))
        if value:
            asset.rights = {**value, "copied_from": {"survey_uid": source.uid}}


def copied_theme(source_data, source_uid, target_data, target_uid):
    source = SimpleNamespace(theme=source_data, uid=source_uid)
    target = SimpleNamespace(theme=target_data, uid=target_uid)
    copy_theme_records(source, target)
    from app import theme

    return theme.clean(target.theme)


def path(survey, asset):
    from app.media import upload_folder

    return os.path.join(upload_folder(survey.uid), asset.filename)


def file_hash(survey, asset):
    digest = hashlib.sha256()
    try:
        with open(path(survey, asset), "rb") as stream:
            while chunk := stream.read(128 * 1024):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _details_hash(value):
    return hashlib.sha256(json.dumps(details(value), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def problems(value):
    value = details(value)
    missing = []
    if value["basis"] not in BASES:
        missing.append(de_en("Rechtsgrundlage", "rights basis"))
    if not value["creator"]:
        missing.append(de_en("Urheber oder verantwortliche Person", "creator or responsible person"))
    if value["basis"] in {"license", "permission", "ai"} and not value["source"]:
        missing.append(de_en("Quelle", "source"))
    if value["basis"] in {"license", "ai"} and not (value["license"] or value["license_url"]):
        missing.append(de_en("Lizenz bzw. Nutzungsbedingungen", "licence or terms of use"))
    if value["basis"] == "permission" and not value["permission"]:
        missing.append(de_en("Umfang und Nachweis der individuellen Erlaubnis", "scope and reference of individual permission"))
    return missing


def declared(survey, asset, digest=None):
    value = asset.rights or {}
    confirmation = value.get("confirmation") or {}
    digest = file_hash(survey, asset) if digest is None else digest
    return bool(digest and not problems(value) and confirmation.get("scope") == "public-archive"
                and confirmation.get("by") and confirmation.get("at")
                and confirmation.get("sha256") == digest and confirmation.get("details_sha256") == _details_hash(value))


def record(survey, asset, form, person):
    """Save draft details or a fresh declaration. Omitted checkbox revokes the previous declaration."""
    from app.models import utcnow

    value = details(form)
    if form.get("public_confirm") == "1":
        missing = problems(value)
        if missing:
            raise RightsError(de_en("Für die öffentliche Archivierung fehlen: {fields}.",
                                    "Public archiving requires: {fields}.", fields=", ".join(missing)))
        digest = file_hash(survey, asset)
        if not digest:
            raise RightsError(de_en("Die Bilddatei fehlt; ihre Rechte können nicht bestätigt werden.",
                                    "The image file is missing; its rights cannot be confirmed."))
        value["confirmation"] = {"scope": "public-archive", "by": person.display_name, "user_id": person.id,
                                 "at": utcnow().isoformat(timespec="seconds"), "sha256": digest,
                                 "details_sha256": _details_hash(value)}
    asset.rights = value


def suggested(survey, asset):
    """Unconfirmed editable suggestions; metadata itself never authorizes public distribution."""
    result = details(asset.rights)
    try:
        with open(path(survey, asset), "rb") as stream:
            blob = stream.read()
            embedded = image_rights.extract_xmp(blob) if asset.filename.lower().endswith(".svg") else image_rights.from_bytes(blob)
    except OSError:
        embedded = {}
    for field in image_rights.FIELDS:
        if not result[field]:
            result[field] = "\n".join(embedded.get(field, []))[:4096]
    if asset.source == "ai" and not result["source"]:
        result["source"] = asset.model or ""
    return result


def copied(asset):
    """Server-side copies keep the original declaration and record its origin; bytes must still match."""
    value = copy.deepcopy(asset.rights or {})
    if value:
        value["copied_from"] = {"survey_uid": asset.item.block.survey.uid, "asset_id": asset.id}
    return value or None


def imported(value):
    """A foreign file may state terms, but its claimed approval is never a local declaration."""
    result = details(value)
    return {**result, "imported_unconfirmed": True} if any(result.values()) else None


def require_public(survey, assets=None):
    assets = image_assets(survey) if assets is None else assets
    blocked = [a for a in assets if not declared(survey, a)]
    if blocked:
        labels = ", ".join(dict.fromkeys(a.item.code for a in blocked))
        raise RightsError(de_en("Öffentliche Archivierung gesperrt: Für {n} Bild(er) ({items}) fehlen dokumentierte Rechte "
                                "und die ausdrückliche Bestätigung der öffentlichen Weitergabe. Bitte unter „Bildrechte“ prüfen.",
                                "Public archiving blocked: {n} image(s) ({items}) need documented rights and an explicit "
                                "declaration allowing public distribution. Please review “Image rights”.", n=len(blocked), items=labels))


def require_public_files(survey, image_blobs):
    """Frozen archives: only an explicitly declared image with exactly the same bytes covers a file."""
    snapshot_receipts(survey, dict(image_blobs))


def _confirmed_records(survey):
    allowed = {}
    for asset in image_assets(survey, approved_only=False) + theme_images(survey):
        digest = file_hash(survey, asset)
        if digest and declared(survey, asset, digest):
            allowed[digest] = {**details(asset.rights), "confirmation": copy.deepcopy(asset.rights["confirmation"])}
    return allowed


def _missing_files(names):
    raise RightsError(de_en("Öffentliche Archivierung gesperrt: Für Bilder dieser Fassung fehlen bestätigte Rechte: {names}.",
                            "Public archiving blocked: images in this revision lack declared rights: {names}.",
                            names=", ".join(names[:8])))


def snapshot_receipts(survey, files):
    """Frozen receipt for exact image bytes (name -> bytes or Path); no current-state reinterpretation later."""
    allowed = _confirmed_records(survey)
    receipts, missing = {}, []
    for name, source in files.items():
        if isinstance(source, bytes):
            digest = hashlib.sha256(source).hexdigest()
        else:
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest not in allowed:
            missing.append(name)
        else:
            receipts[name] = {"sha256": digest, "rights": copy.deepcopy(allowed[digest])}
    if missing:
        _missing_files(missing)
    return receipts


def require_public_snapshot(survey, content):
    """A pre-registration can contain older image bytes; inspect exactly those files, not just today's assets."""
    import base64
    import io
    import zipfile

    from app import constructs, packages, transfer

    try:
        allowed = _confirmed_records(survey)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            # survey.json since 2026-10-23, befragung.json before (packages.SURVEY_NAMES)
            info = archive.getinfo(packages.find(archive.namelist(), packages.SURVEY_NAMES) or packages.SURVEY)
            if info.file_size > max(32 * 1024 * 1024, transfer.export_limit()):
                raise ValueError("definition too large")
            data = json.loads(archive.read(info))
            try:
                constructs.require_public_definition(data)
            except ValueError as exc:
                raise RightsError(str(exc)) from exc
            inline = data.get("files") or {}
            if not isinstance(inline, dict) or len(inline) > transfer.MAX_FILES:
                raise ValueError("invalid files")
            for name, encoded in inline.items():
                if not isinstance(encoded, str) or len(encoded) > 70 * 1024 * 1024:
                    raise ValueError("image too large")
                if hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest() not in allowed:
                    _missing_files([name])
            for info in archive.infolist():
                if info.filename.startswith(tuple(f"{f}/" for f in packages.FILE_FOLDERS)) and not info.is_dir():
                    if info.file_size > 50 * 1024 * 1024:
                        raise ValueError("image too large")
                    with archive.open(info) as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() not in allowed:
                            _missing_files([info.filename])
    except RightsError:
        raise
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        raise RightsError(de_en("Die Bildrechte dieser eingefrorenen Datei konnten nicht geprüft werden.",
                                "Image rights in this frozen file could not be checked.")) from exc


def archive_values(asset, embedded):
    """Declared terms take precedence in the manifest; unconfirmed metadata remains clearly distinguishable."""
    stated = details(asset.rights)
    confirmation = (asset.rights or {}).get("confirmation") or {}
    return [stated[field] or "\n".join(embedded.get(field, [])) for field in image_rights.FIELDS] + [
        stated["basis"], stated["permission"], confirmation.get("by", ""), confirmation.get("at", ""),
        confirmation.get("sha256", "")]
