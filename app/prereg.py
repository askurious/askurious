"""Pre-registration snapshot: the survey definition frozen when its fieldwork starts.

lifecycle.start() calls freeze() when a draft goes into the field, so once per questionnaire version (a pause
and a restart freeze nothing; reopening for editing makes a new version, whose start freezes it again). The
snapshot is one ZIP file, written once and never changed: the survey as a file (app/transfer.py, can be read in
again), the questionnaire as Markdown and as Word with codebook, the codebook and a README with the SHA-256 of
every file. Next to it lies the SHA-256 of the ZIP itself, which a pre-registration (OSF, AsPredicted) can cite,
and the timestamp of the DFN for that SHA-256 (.tsr, RFC 3161: the reply of the service as it came; TSA_URL, Philipp
2026-10-08): it proves that the ZIP existed no later than then. Asked for at the freeze; if the service does not
answer, the hourly housekeeping asks again (stamp_missing). Checkable with seal_verify.py (tab "Export") or
openssl ts -verify.
Since 2026-10-23 (concept "Vereinheitlichung der Befragungsexporte", E10 B) it also holds the survey material as PDF
(app/study_material.py), its SHA-256 goes into the seal chain as an entry "prereg" (seal.prereg_frozen), stamped by the
DFN with the start of the fieldwork, and compare() names what differs between the frozen survey file and the survey as
it runs now (shown on the export page).
Nothing collected and nobody named is in it, so every role may download it. It lies with the other snapshots
under the survey's uid and goes with the survey (lifecycle.delete).

The readable texts (README, research plan) are in the language of the person who starts the fieldwork (German or
English, app.lang; decision E8 of the concept "Mehrsprachige Oberfläche", 2026-10-06); the English README names it
("Language: en"), a README without that line is German. File names and the survey file are the same in both."""
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from flask import current_app
from sqlalchemy import event
from sqlalchemy.orm import Session

NAME_RE = re.compile(r"v(\d+)_(\d{8}-\d{6})\.zip")


def folder(survey):
    from app import snapshots

    return os.path.join(snapshots.folder(survey), "prereg")


def _files(survey, lang="de"):
    """[(name in the ZIP, bytes or Path)] of the survey as it is now; lang: the language of the readable texts. The
    parts and their names are those of every package (app/packages.py): survey file, questionnaire as Markdown and
    Word, codebook, loop tables, study plan."""
    from app import packages

    return packages.survey_file(survey) + packages.documentation(survey, lang)


def freeze(survey, lang=None, pending=False):
    """Write the snapshot of the survey's current version. Returns its name, or None if it could not be
    written: the start of the fieldwork does not fail over it (the error is logged). lang: the language of its
    readable texts ("de" or "en"; default: that of the person starting, German outside a request).

    pending (lifecycle.start): written under a name nobody lists ("….zip.pending") and named, given its SHA-256 and
    stamped only once the session commits the start; a start whose commit fails leaves no pre-registration "frozen
    at the start" of a fieldwork that never began (code review 2026-10-15, D6)."""
    from app import lang as ui_lang
    from app.models import utcnow

    lang = "en" if (lang or ui_lang.current()) == "en" else "de"

    try:
        from app import packages, survey_files
        from app.timeutil import to_local

        # the local time of the app (Europe/Berlin), whatever the clock of the server says (UTC on most)
        now = to_local(utcnow())
        name = f"v{survey.qversion or 1}_{now:%Y%m%d-%H%M%S}.zip"
        files = _files(survey, lang)
        sums = packages.checksum_lines(files)
        readme = _readme_en(survey, now, utcnow(), sums) if lang == "en" else (
            f"# Präregistrierung: {survey.title}\n\n"
            f"Fragebogen-Fassung {survey.qversion or 1}, eingefroren beim Start der Feldphase am "
            f"{now:%d.%m.%Y um %H:%M:%S} (UTC {utcnow():%Y-%m-%d %H:%M:%S}).\n"
            f"Erzeugt von Askurious. Die Datei {packages.SURVEY} lässt sich als neue Befragung wieder einlesen.\n\n"
            f"Dateien und ihre SHA-256-Prüfsummen (auch in {packages.CHECKSUMS}, prüfbar mit sha256sum -c):\n\n"
            "```\n" + sums + "```\n")
        stamp = now.timetuple()[:6]
        os.makedirs(folder(survey), exist_ok=True)
        path = os.path.join(folder(survey), name)
        if os.path.exists(path):
            raise FileExistsError(path)  # written once and never changed
        with survey_files.snapshot_zip([(packages.README, readme.encode("utf-8"))] + files + packages.checksums(files),
                                       stamp) as buf, open(path + PENDING, "xb") as f:
            shutil.copyfileobj(buf, f, 65536)
        from app import seal

        if pending:
            from app.extensions import db

            db.session().info.setdefault(_SESSION_KEY, []).append(path)
            seal.prereg_frozen(survey, survey_files.content_hash(Path(path + PENDING)), name)
            return name
        _publish(path)
        seal.prereg_frozen(survey, survey_files.content_hash(Path(path)), name)
        return name
    except Exception:
        current_app.logger.exception("pre-registration snapshot of survey %s failed", survey.id)
        return None


PENDING = ".pending"
_SESSION_KEY = "prereg_pending"


def _publish(path):
    """The written snapshot (path + PENDING) under its name, with its SHA-256 and the timestamp of the DFN."""
    from app import survey_files

    if os.path.exists(path):
        raise FileExistsError(path)
    os.rename(path + PENDING, path)
    digest = survey_files.content_hash(Path(path))
    with open(path[:-4] + ".sha256", "w", encoding="ascii", newline="\n") as f:
        f.write(f"{digest}  {os.path.basename(path)}\n")
    _stamp(path, None, quick=True)  # hash the file incrementally, never materialise the ZIP


@event.listens_for(Session, "after_commit")
def _published(session):
    for path in session.info.pop(_SESSION_KEY, []):
        try:
            _publish(path)
        except Exception:  # the start is committed all the same; the error is logged
            current_app.logger.exception("pre-registration snapshot %s not published", os.path.basename(path))


@event.listens_for(Session, "after_rollback")
def _dropped(session):
    for path in session.info.pop(_SESSION_KEY, []):
        try:
            os.remove(path + PENDING)
        except FileNotFoundError:
            pass
        except OSError:
            current_app.logger.exception("unused pre-registration snapshot %s not removed", os.path.basename(path))


STAMP_DAYS = 30  # how long the housekeeping keeps asking for a missing timestamp


def _stamp(path, body, quick=False):
    """Ask the DFN for a timestamp of the ZIP at path (its bytes: body) and lay it next to it. True if it came.
    quick: inside the request that starts the fieldwork, with the short timeout seal.QUICK (code audit 2026-10-08,
    S10); stamp_missing in the background asks again with the full one."""
    from app import seal

    if not seal.tsa_url() or os.path.exists(path[:-4] + ".tsr"):
        return False
    try:
        if body is None:
            with open(path, "rb") as source:
                digest = hashlib.file_digest(source, "sha256").digest()
        else:
            digest = hashlib.sha256(body).digest()
        reply, _token, _when = seal.stamp(digest, seal.QUICK if quick else seal.TIMEOUT)
    except Exception as exc:  # the service is down: the housekeeping asks again
        current_app.logger.warning("timestamp of the pre-registration %s put off: %s", os.path.basename(path), exc)
        return False
    with open(path[:-4] + ".tsr", "wb") as f:
        f.write(reply)
    return True


def stamp_path(survey, name):
    full = path(survey, name)
    return full[:-4] + ".tsr" if full and os.path.isfile(full[:-4] + ".tsr") else None


def stamp_time(survey, name):
    """The time (UTC) the timestamp of a snapshot states, or None."""
    from app import seal_verify

    found = stamp_path(survey, name)
    if not found:
        return None
    try:
        with open(found, "rb") as f:
            return seal_verify.info(seal_verify.token_from_reply(f.read()))["time"].replace(tzinfo=None)
    except (OSError, seal_verify.Invalid, ValueError, IndexError):
        return None


def stamp_times(survey):
    """{name: time of its timestamp} of the snapshots that have one."""
    return {name: when for name, *_ in listing(survey) if (when := stamp_time(survey, name))}


def stamp_missing(limit=20):
    """Timestamps for snapshots of the last STAMP_DAYS days that have none (the service did not answer at the
    freeze). Returns how many came."""
    from datetime import timedelta

    from app.extensions import db
    from app.models import Survey, utcnow

    since, n = utcnow() - timedelta(days=STAMP_DAYS), 0
    for survey in db.session.scalars(db.select(Survey).where(Survey.status != Survey.DRAFT, Survey.updated_at >= since)):
        for name, _version, at, _digest in listing(survey):
            if at < since or stamp_path(survey, name):
                continue
            if not _stamp(path(survey, name), None):
                return n  # the service is down: next hour
            n += 1
            if n >= limit:
                return n
    return n


def _readme_en(survey, now, utc, sums):
    """The README of the snapshot in English (the German one is written in freeze())."""
    from app import packages

    return (f"# Pre-registration: {survey.title}\n\n"
            "Language: en\n\n"
            f"Questionnaire version {survey.qversion or 1}, frozen at the start of the fieldwork on "
            f"{now:%Y-%m-%d at %H:%M:%S} (UTC {utc:%Y-%m-%d %H:%M:%S}).\n"
            f"Created by Askurious. The file {packages.SURVEY} can be read in again as a new survey.\n\n"
            f"Files and their SHA-256 checksums (also in {packages.CHECKSUMS}, checkable with sha256sum -c):\n\n"
            "```\n" + sums + "```\n")


def listing(survey):
    """[(name, version, datetime, sha256)] newest first."""
    base = folder(survey)
    if not os.path.isdir(base):
        return []
    out = []
    for name in os.listdir(base):
        m = NAME_RE.fullmatch(name)
        if not m:
            continue
        try:
            with open(os.path.join(base, name[:-4] + ".sha256"), encoding="ascii") as f:
                digest = f.read().split()[0]
        except (OSError, IndexError):
            digest = ""
        out.append((name, int(m.group(1)), datetime.strptime(m.group(2), "%Y%m%d-%H%M%S"), digest))
    return sorted(out, key=lambda x: (x[2], x[1]), reverse=True)


def path(survey, name):
    if not NAME_RE.fullmatch(name or ""):
        return None
    full = os.path.join(folder(survey), name)
    return full if os.path.isfile(full) else None


# --- frozen against running (concept "Vereinheitlichung der Befragungsexporte", E10 B) --------------------------

COMPARE_MAX = 30  # differences named on the page; the count is always whole
# what the start itself sets right after the freeze (lifecycle.start): the data pool of the results and the
# confirmations of the consent; no change of the questionnaire
SET_AT_START = {"survey": ("data_pool",), "config": ("confirm_keys",)}


def frozen_definition(survey, name):
    """The survey file inside a frozen snapshot as a dict, or None (unreadable, or a snapshot without one)."""
    import zipfile

    from app import packages, transfer

    full = path(survey, name)
    if not full:
        return None
    try:
        with zipfile.ZipFile(full) as z:
            member = packages.find(z.namelist(), packages.SURVEY_NAMES)
            if member is None or z.getinfo(member).file_size > transfer.export_limit() * 2:
                return None
            return json.loads(z.read(member))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


def _without(value, keys):
    return {k: v for k, v in value.items() if k not in keys} if isinstance(value, dict) else value


def _items(data):
    return {i.get("code"): i for b in data.get("blocks") or [] for i in b.get("items") or [] if isinstance(i, dict)}


def compare(survey, name):
    """What differs between the pre-registration `name` and the survey as it runs now: (count, [sentence]) in the
    language of the page, or None if the snapshot holds no readable survey file. Compared are the questionnaire
    (elements by variable name: added, removed, wording, settings, required), the order of blocks, the flow, the
    quotas and the settings; not what the file never holds (ids, the time of the export, images)."""
    from app import transfer
    from app.lang import de_en

    frozen = frozen_definition(survey, name)
    if frozen is None:
        return None
    now = json.loads(json.dumps(transfer.dump(survey, with_files=False)))  # the same JSON types as the frozen file
    out = []
    before, after = _items(frozen), _items(now)
    for code in [c for c in after if c not in before]:
        out.append(de_en("Element {c} neu", "element {c} added", c=code))
    for code in [c for c in before if c not in after]:
        out.append(de_en("Element {c} entfernt", "element {c} removed", c=code))
    for code in [c for c in after if c in before]:
        old, new = before[code], after[code]
        if old.get("type") != new.get("type"):
            out.append(de_en("Element {c}: anderer Typ", "element {c}: other type", c=code))
            continue
        if old.get("texts") != new.get("texts"):
            out.append(de_en("Element {c}: Wortlaut geändert", "element {c}: wording changed", c=code))
        if _without(old.get("config"), SET_AT_START["config"]) != _without(new.get("config"), SET_AT_START["config"]):
            out.append(de_en("Element {c}: Einstellungen geändert", "element {c}: settings changed", c=code))
        if old.get("answer_mode") != new.get("answer_mode"):
            out.append(de_en("Element {c}: Antwortpflicht geändert", "element {c}: answer mode changed", c=code))
    order = lambda d: [[i.get("code") for i in b.get("items") or []] for b in d.get("blocks") or []]  # noqa: E731
    if order(frozen) != order(now) and not out:
        out.append(de_en("Reihenfolge der Elemente oder Blöcke geändert", "order of elements or blocks changed"))
    if [b.get("titles") for b in frozen.get("blocks") or []] != [b.get("titles") for b in now.get("blocks") or []]:
        out.append(de_en("Blocktitel geändert", "block titles changed"))
    if frozen.get("flow") != now.get("flow"):
        out.append(de_en("Ablauf geändert (Bedingungen, Randomisierungen, Schleifen, Enden)",
                         "flow changed (conditions, randomisations, loops, ends)"))
    if frozen.get("quotas") != now.get("quotas"):
        out.append(de_en("Quoten geändert", "quotas changed"))
    settings = sorted(k for k in set(frozen.get("survey") or {}) | set(now.get("survey") or {})
                      if k not in SET_AT_START["survey"]
                      and (frozen.get("survey") or {}).get(k) != (now.get("survey") or {}).get(k))
    if settings:
        out.append(de_en("Einstellungen geändert: {k}", "settings changed: {k}", k=", ".join(settings[:8])
                         + (" …" if len(settings) > 8 else "")))
    return len(out), out[:COMPARE_MAX]
