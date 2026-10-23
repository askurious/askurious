"""Versions ("Fassungen") of an interview guide (concept interviewleitfaden-konzept.md 6.3, decision I8).

"Fassung festhalten" freezes the guide as it is now: one ZIP with the Word guide, the interview card, the category
system (.qdc), the survey file and a README with the SHA-256 of every file, numbered from 1, never changed
afterwards (the mechanics of app/prereg.py). Next to it the SHA-256 of the ZIP, which a method section, an ethics
application or a pre-registration can cite. The guide stays editable: adapting it after the first interviews is
usual (Helfferich, 2011; Kruse, 2015), and the versions document with which guide which interviews were held.

The list lives in Survey.interview["versions"] ({n, at, by, note, text, sha256, name}); the files under the survey's
snapshot folder, which goes with the survey (lifecycle.delete). Nothing of an interview is in them. The file is
written before the list is committed: the number of a file whose commit failed (or of a double click) is skipped.
The survey row is locked while a version is recorded, so two requests at once cannot lose each other's entry; a file
still without its entry an hour later goes back into the list instead of being removed, as it may already have been
cited or passed on (data audit L-7; audit 2026-10-21, F3)."""
import os
import json
import re
import shutil
from pathlib import Path

from flask import current_app

NOTES = {"vor_probe": "vor dem Probeinterview", "nach_probe": "nach dem Probeinterview",
         "erste": "nach den ersten Interviews", "ethik": "für den Ethikantrag", "endgueltig": "endgültig",
         "andere": "anderer Anlass"}
NOTES_EN = {"vor_probe": "before the trial interview", "nach_probe": "after the trial interview",
            "erste": "after the first interviews", "ethik": "for the ethics application", "endgueltig": "final",
            "andere": "other occasion"}
MAX_TEXT = 200
NAME_RE = re.compile(r"fassung-(\d{1,4})\.zip")
ORPHAN_AGE = 3600  # seconds after which a version file without its entry in the list is swept
MATERIAL_LICENSES = {
    "cc-by-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "cc-by-sa-4.0": ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
    "cc-by-nc-4.0": ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/"),
    "cc-zero": ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
}
LICENSE_MANIFEST = "material-license.json"
# the names in a version (app/packages.py, E6); versions before 2026-10-23: leitfaden.docx, interviewkarte.docx,
# kategoriensystem.qdc, befragung.json, LIESMICH.txt, pruefsummen.sha256
GUIDE, CARD, CATEGORIES = "interview-guide.docx", "interview-card.docx", "category-system.qdc"
MAX_RIGHTS_TEXT = 4000


def material_license(version):
    """The explicit grant frozen into this revision; never infer it from the current survey or an old upload."""
    from app.lang import de_en
    from app.zenodo import ZenodoError

    grant = version.get("material_license")
    if not (isinstance(grant, dict) and grant.get("schema") == 1 and grant.get("scope") == "own-materials"
            and isinstance(grant.get("id"), str) and grant["id"] in MATERIAL_LICENSES and grant.get("confirmed") is True
            and isinstance(grant.get("confirmed_by"), str) and grant["confirmed_by"].strip()
            and isinstance(grant.get("confirmed_at"), str) and grant["confirmed_at"].strip()
            and isinstance(grant.get("third_party"), str) and isinstance(grant.get("attribution"), str)
            and grant.get("public_material_checked") is True):
        raise ZenodoError(de_en(
            "Für diese Fassung ist keine bestätigte Materiallizenz festgelegt. Bitte eine neue Fassung mit "
            "ausdrücklicher Lizenzwahl festhalten. Die alte Fassung kann weiterhin heruntergeladen werden.",
            "No confirmed material licence is set for this revision. Please record a new revision with an explicit "
            "licence selection. The old revision can still be downloaded."))
    return grant


def license_text(grant, ui="de"):
    """Human-readable terms attached to this revision, including the limits of the grant."""
    label, url = MATERIAL_LICENSES[grant["id"]]
    if ui == "en":
        return (f"Material licence: {label}\n{url}\n\n"
                "This licence applies only to the contributors' own material in this revision. Third-party "
                "material, including images, instruments and quotations, retains its separate rights and is not "
                "relicensed. The original credits and conditions must be respected.\n"
                f"Attribution: {grant['attribution']}\n"
                f"Authorised to license and publish, confirmed by {grant['confirmed_by']} on {grant['confirmed_at']}.\n"
                "The confirmation records the contributor's declaration; it is not an independent rights verification.\n\n"
                "Image rights and declarations for the exact source files: image-rights.json.\n\n"
                "Other third-party material and its separate conditions:\n" + (grant["third_party"] or "None declared.") + "\n")
    return (f"Materiallizenz: {label}\n{url}\n\n"
            "Diese Lizenz gilt ausschließlich für eigene Materialien der Beteiligten in dieser Fassung. "
            "Fremdbestandteile, einschließlich Bildern, Instrumenten und Zitaten, behalten ihre eigenen Rechte "
            "und werden nicht umlizenziert. Ihre Quellenangaben und Bedingungen sind einzuhalten.\n"
            f"Namensnennung: {grant['attribution']}\n"
            f"Berechtigung zur Lizenzvergabe und Veröffentlichung bestätigt von {grant['confirmed_by']} am {grant['confirmed_at']}.\n"
            "Die Bestätigung dokumentiert die Erklärung der bestätigenden Person; sie ist keine unabhängige Rechteprüfung.\n\n"
            "Bildrechte und Bestätigungen für die genauen Quelldateien: image-rights.json.\n\n"
            "Weitere Fremdbestandteile und deren gesonderte Bedingungen:\n" + (grant["third_party"] or "Keine angegeben.") + "\n")


def verify_material_license(version, archive):
    """Check the frozen file and declaration before any external upload, including updates of old drafts."""
    import hashlib
    import zipfile

    from app import survey_files
    from app.lang import de_en
    from app.zenodo import ZenodoError

    grant = material_license(version)
    try:
        if survey_files.content_hash(Path(archive)) != version.get("sha256"):
            raise ValueError("archive hash differs")
        with zipfile.ZipFile(archive) as packed:
            info = packed.getinfo(LICENSE_MANIFEST)
            if info.file_size > 32 * 1024:
                raise ValueError("oversized licence manifest")
            if json.loads(packed.read(info)) != grant:
                raise ValueError("licence differs from frozen manifest")
            with packed.open("image-rights.json") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != grant.get("image_rights_sha256"):
                    raise ValueError("image declarations differ from frozen manifest")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise ZenodoError(de_en(
            "Die ZIP-Datei und die festgehaltene Materiallizenz stimmen nicht überein. Der Upload wurde abgebrochen; "
            "bitte die Fassung prüfen oder eine neue Fassung festhalten.",
            "The ZIP file and the recorded material licence do not match. Upload was cancelled; please check the "
            "revision or record a new revision.")) from exc
    return grant


def notes():
    """NOTES in the language of the page."""
    from app.lang import english

    return NOTES_EN if english() else NOTES


def folder(survey):
    from app import snapshots

    return os.path.join(snapshots.folder(survey), "fassungen")


def files(survey, material_license=None):
    """[(name in the ZIP, bytes or Path)] of the guide as it is now; the survey file and the names as in every package
    (app/packages.py)."""
    from app import interview_docx, packages, qdc, survey_files

    definition = packages.survey_file(survey)
    receipts = None
    if material_license:
        import base64

        from app import asset_rights

        # survey_file holds either exact inline bytes or Paths for the ZIP's files/ members.
        data = json.loads(next(content for name, content in definition if name == packages.SURVEY))
        images = {name: base64.b64decode(encoded, validate=True) for name, encoded in data.get("files", {}).items()}
        images.update({name.removeprefix(f"{packages.FILES}/"): content for name, content in definition
                       if name.startswith(f"{packages.FILES}/")})
        images = {name: content for name, content in images.items()
                  if Path(name).suffix.lower() in asset_rights.IMAGE_EXTENSIONS | {".svg"}}
        receipts = json.dumps(asset_rights.snapshot_receipts(survey, images), ensure_ascii=False, indent=2).encode("utf-8")
        material_license["image_rights_sha256"] = survey_files.content_hash(receipts)
    content = [(GUIDE, interview_docx.build(survey, "leitfaden", material_license=material_license)),
               (CARD, interview_docx.build(survey, "interviewkarte", material_license=material_license)),
               (CATEGORIES, qdc.build(survey))] + definition
    if material_license:
        from app.items.base import reader

        content += [("LICENSE.txt", license_text(material_license, reader()).encode("utf-8")),
                    ("image-rights.json", receipts),
                    (LICENSE_MANIFEST, json.dumps(material_license, ensure_ascii=False, indent=2).encode("utf-8"))]
    return content


def freeze(survey, note, text, person, *, licence="", confirmed=False, third_party=""):
    """Write the next version and add it to the list (the caller commits). Returns the entry. Its README is in the
    language of the person who records it (as the Word documents in it, app/interview_docx.py)."""
    from app import interview, packages, survey_files
    from app.items.base import reader
    from app.models import utcnow
    from app.timeutil import to_local
    from app.lang import de_en

    if licence and licence not in MATERIAL_LICENSES:
        raise ValueError(de_en("Bitte eine der angebotenen Materiallizenzen wählen.", "Please choose one of the offered material licences."))
    if licence and not confirmed:
        raise ValueError(de_en("Bitte die Berechtigung zur Lizenzvergabe und Veröffentlichung ausdrücklich bestätigen.",
                              "Please explicitly confirm your authority to license and publish the material."))
    if not licence and confirmed:
        raise ValueError(de_en("Bitte zuerst eine Materiallizenz wählen oder die Bestätigung abwählen.",
                              "Please first choose a material licence or clear the confirmation."))
    if len(third_party) > MAX_RIGHTS_TEXT:
        raise ValueError(de_en("Die Angaben zu Fremdmaterialien sind zu lang (höchstens 4000 Zeichen).",
                              "The third-party material notice is too long (at most 4000 characters)."))
    if licence:
        from app import constructs

        constructs.require_public_material(survey)

    from app.extensions import db

    # Read the list under a lock of the survey row and as committed: a second "Fassung festhalten" at the same moment
    # waits here, and its entry is not overwritten by a list read before it was committed (audit 2026-10-21, F3).
    if db.inspect(survey).persistent:
        db.session.refresh(survey, ["interview"], with_for_update=True)
    st = interview.state(survey)
    os.makedirs(folder(survey), exist_ok=True)
    st["versions"] = sorted(st["versions"] + _orphans(survey, {v.get("n") or 0 for v in st["versions"]}),
                            key=lambda v: v.get("n") or 0)
    listed = {v.get("n") or 0 for v in st["versions"]}
    n = max(listed | _on_disk(survey, listed), default=0) + 1  # a number a file without its entry holds is skipped
    now = to_local(utcnow())
    grant = ({"schema": 1, "scope": "own-materials", "id": licence, "confirmed": True,
              "confirmed_by": person, "confirmed_at": now.isoformat(timespec="seconds"),
              "attribution": survey.title + " — " + "; ".join(m.display_name for m in survey.members),
              "third_party": third_party.strip(), "public_material_checked": True} if licence else None)
    note = note if note in NOTES else "andere"
    text = " ".join((text or "").split())[:MAX_TEXT]
    content = files(survey, grant)
    sums = packages.checksum_lines(content)
    english = reader() == "en"
    for _ in range(50):
        if english:
            readme = (f"Interview guide: {survey.title}\n\nRevision {n}, recorded on {now:%Y-%m-%d at %H:%M:%S} "
                      f"by {person} ({NOTES_EN[note]}{': ' + text if text else ''}).\n"
                      f"Made by Askurious. The file {packages.SURVEY} can be read in again as a new interview guide,\n"
                      f"{CATEGORIES} in MAXQDA, ATLAS.ti or NVivo (REFI-QDA).\n\n"
                      + ("Material licence and third-party conditions: see LICENSE.txt and material-license.json.\n\n" if grant
                         else "No publication licence has been granted for this revision.\n\n")
                      + f"Files and their SHA-256 checksums (also in {packages.CHECKSUMS}, to check with sha256sum -c):\n\n"
                      + "```\n" + sums + "```\n")
        else:
            readme = (f"Interviewleitfaden: {survey.title}\n\nFassung {n}, festgehalten am {now:%d.%m.%Y um %H:%M:%S} "
                      f"von {person} ({NOTES[note]}{': ' + text if text else ''}).\n"
                      f"Erzeugt von Askurious. Die Datei {packages.SURVEY} lässt sich als neuer Interviewleitfaden wieder einlesen,\n"
                      f"{CATEGORIES} in MAXQDA, ATLAS.ti oder NVivo (REFI-QDA).\n\n"
                      + ("Materiallizenz und Bedingungen für Fremdbestandteile: siehe LICENSE.txt und material-license.json.\n\n" if grant
                         else "Für diese Fassung wurde keine Veröffentlichungslizenz erteilt.\n\n")
                      + f"Dateien und ihre SHA-256-Prüfsummen (auch in {packages.CHECKSUMS}, prüfbar mit sha256sum -c):\n\n"
                      + "```\n" + sums + "```\n")
        stamp = now.timetuple()[:6]
        name = f"fassung-{n}.zip"
        try:
            with survey_files.snapshot_zip([(packages.README, readme.encode("utf-8"))] + content + packages.checksums(content),
                                           stamp) as buf, open(os.path.join(folder(survey), name), "xb") as f:
                shutil.copyfileobj(buf, f, 65536)
            break
        except FileExistsError:  # a request at the same moment (a double click) took this number
            n += 1
    else:
        raise FileExistsError(name)
    entry = {"n": n, "at": now.isoformat(timespec="seconds"), "by": person, "note": note, "text": text,
             "sha256": survey_files.content_hash(Path(folder(survey)) / name), "name": name, "material_license": grant}
    survey.interview = {**st, "versions": st["versions"] + [entry]}
    current_app.logger.info("interview guide %s: version %s frozen", survey.id, n)
    return entry


def _on_disk(survey, listed):
    """The numbers of the version files on the disk, also those the list does not know yet (their request may still be
    at work, or its commit failed)."""
    return {int(m.group(1)) for m in map(NAME_RE.fullmatch, os.listdir(folder(survey))) if m}


README_BY = re.compile(r"(?:festgehalten am .+? von|recorded on .+? by) (.+?) \((.*)\)\.\s*$", re.M)


def _orphans(survey, listed):
    """Entries for the version files older than ORPHAN_AGE that the list does not know: a file written by a request
    whose entry was lost (two requests at once before the lock, or a failed commit). Before, such a file was
    removed, although it may already have been cited or passed on (audit 2026-10-21, F3); now it goes back into
    the list with what its README and licence manifest say."""
    import time
    import zipfile
    from datetime import datetime, timezone

    from app import packages, survey_files
    from app.lang import de_en
    from app.timeutil import to_local

    found = []
    for fname in sorted(os.listdir(folder(survey))):
        m = NAME_RE.fullmatch(fname)
        full = os.path.join(folder(survey), fname)
        if not m or int(m.group(1)) in listed or os.path.getmtime(full) >= time.time() - ORPHAN_AGE:
            continue
        by, grant = "", None
        try:
            with zipfile.ZipFile(full) as z:
                names = set(z.namelist())
                readme_name = packages.find(names, packages.README_NAMES)
                readme = z.read(readme_name).decode("utf-8") if readme_name else ""
                if LICENSE_MANIFEST in names:
                    grant = json.loads(z.read(LICENSE_MANIFEST).decode("utf-8"))
        except (OSError, ValueError, zipfile.BadZipFile, KeyError):
            continue  # not a readable version: left as it is, its number stays taken
        hit = README_BY.search(readme)
        if hit:
            by = hit.group(1)
        found.append({"n": int(m.group(1)), "name": fname, "by": by, "note": "andere",
                      "text": de_en("aus der Datei wiederhergestellt", "restored from the file"),
                      "at": to_local(datetime.fromtimestamp(os.path.getmtime(full), timezone.utc)).isoformat(
                          timespec="seconds"),
                      "sha256": survey_files.content_hash(Path(full)), "material_license": grant})
        current_app.logger.warning("interview guide %s: version file %s taken back into the list", survey.id, fname)
    return found


def listing(survey):
    """The versions newest first, those whose file is there."""
    from app import interview

    return [v for v in reversed(interview.state(survey)["versions"]) if path(survey, v.get("name"))]


def path(survey, name):
    if not NAME_RE.fullmatch(name or ""):
        return None
    full = os.path.join(folder(survey), name)
    return full if os.path.isfile(full) else None
