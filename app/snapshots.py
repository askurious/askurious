"""Snapshots of a survey's data before its questionnaire is changed again.

Export columns follow the current items, so answers to items that are later deleted or
changed would disappear from the export. reopen_for_editing() therefore stores the data
(all sources, incl. unfinished), codebook and questionnaire as they were, per version. Choice items
(conjoint, MaxDiff, ACBC) also get their long format and their design and texts as JSON: the answers
refer to profile and statement positions of that design. Since 2026-10-23 the questionnaire of that version is also
there as a survey file (survey.json, app/packages.py; concept "Vereinheitlichung der Befragungsexporte", E4): it can
be read in again and holds the look with its logos, which before lay beside it as theme.json and image files.

No checksum file, unlike the packages that are handed on: a deletion request or the end of a retention period
rewrites data.csv and the long formats here (app/erasure.py, app/retention.py), and a checksum file would then be
wrong. Names as in every package (packages.long_name); older snapshots keep theirs (long_<code>.csv), which
is_data_file knows as well."""
import json
import os
import re
from datetime import datetime, timedelta

from flask import current_app

FILES = ("data.csv", "codebook.csv", "questionnaire.md", "survey.json")
EXTRA_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]{0,80}_long\.csv"                                          # long formats
                      r"|long_[A-Za-z][A-Za-z0-9_]{0,63}\.csv|design_[A-Za-z][A-Za-z0-9_]{0,63}\.json"   # per choice item
                      r"|theme\.json|(logo(_dark)?|partner_[1-3])\.(png|jpg|webp|svg))")  # the look, in snapshots before E4


def is_data_file(name):
    """Whether a file of a snapshot holds answers: data.csv and the long formats, under their names since 2026-10-23
    (<…>_long.csv) and before (long_<…>.csv). Who may download it, and what a deletion or a retention period rewrites
    (app/surveys/base.py, app/erasure.py, app/retention.py), go by this."""
    return name == "data.csv" or (name.endswith(".csv") and (name.startswith("long_") or name.endswith("_long.csv")))


NAME_RE = re.compile(r"v(\d+)_(\d{8}-\d{6})")


def folder(survey):
    """Under the survey's uid, which is never given twice (the number of a deleted survey can be), and removed
    with the survey (lifecycle.delete)."""
    return os.path.join(current_app.config["UPLOAD_FOLDER"], "snapshots", survey.uid)


def _adopt_old_folder(survey):
    """Snapshots used to lie under the survey's number. Move those of this survey to its own folder. A folder
    left by a deleted survey that had the same number stays where it is: its snapshots are older than this
    survey."""
    old = os.path.join(current_app.config["UPLOAD_FOLDER"], "snapshots", f"survey_{survey.id}")
    if not os.path.isdir(old):
        return
    from app.timeutil import to_local

    # names hold local time (to_local, as save() names them); an hour of leeway
    created = to_local(survey.created_at).replace(tzinfo=None) - timedelta(hours=1)
    for name in os.listdir(old):
        m = NAME_RE.fullmatch(name)
        if m and datetime.strptime(m.group(2), "%Y%m%d-%H%M%S") >= created:
            os.makedirs(folder(survey), exist_ok=True)
            try:
                os.replace(os.path.join(old, name), os.path.join(folder(survey), name))
            except OSError:
                pass


def save(survey):
    from app import export

    from app.models import utcnow
    from app.timeutil import to_local

    # the local time of the app (Europe/Berlin, timeutil.to_local), as the pre-registration names its files, not the server clock, which
    # runs in UTC in a container (audit 2026-10-12, E-5); older names stay as they are
    name = f"v{survey.qversion or 1}_{to_local(utcnow()):%Y%m%d-%H%M%S}"
    path = os.path.join(folder(survey), name)
    os.makedirs(path, exist_ok=True)
    from app import packages, transfer

    contents = {
        "data.csv": export.to_csv(survey, include_incomplete=True, sources=export.SOURCES),
        "codebook.csv": export.codebook_csv(survey),
        "questionnaire.md": export.questionnaire_md(survey),
    }
    try:  # the questionnaire as a survey file, its images and logos inside
        contents[packages.SURVEY] = transfer.dumps(survey)
    except transfer.ExportTooLarge:  # too large with them: the questionnaire without (the images stay with the survey)
        contents[packages.SURVEY] = transfer.dumps(survey, with_files=False)
    contents.update(packages.long_files(survey, include_incomplete=True, sources=export.SOURCES))
    for item in (i for b in survey.blocks for i in b.items if i.kind.long_export):
        contents[f"design_{item.code}.json"] = json.dumps(
            {"type": item.type, "code": item.code, "config": item.config,
             # as shown in each language, with the standard wordings (not stored, app/item_texts.py)
             "texts": {lang: item.t(lang) for lang in survey.languages}},
            ensure_ascii=False, indent=1)
    for fname, text in contents.items():
        with open(os.path.join(path, fname), "w", encoding="utf-8", newline="") as f:  # the texts bring their line ends
            f.write(text)
    return name


def listing(survey):
    """[(name, version, datetime)] newest first."""
    _adopt_old_folder(survey)
    base = folder(survey)
    if not os.path.isdir(base):
        return []
    out = []
    for name in os.listdir(base):
        m = NAME_RE.fullmatch(name)
        if m:
            out.append((name, int(m.group(1)), datetime.strptime(m.group(2), "%Y%m%d-%H%M%S")))
    return sorted(out, key=lambda x: x[2], reverse=True)


def extra_files(survey, name):
    """Long formats and designs of the choice items (and, in snapshots before E4, the look: theme, logo) in one
    snapshot, sorted by name."""
    base = os.path.join(folder(survey), name)
    if not os.path.isdir(base):
        return []
    return sorted(f for f in os.listdir(base) if EXTRA_RE.fullmatch(f))


def path(survey, name, fname):
    if not (fname in FILES or EXTRA_RE.fullmatch(fname)) or name not in {n for n, _, _ in listing(survey)}:
        return None
    full = os.path.join(folder(survey), name, fname)
    return full if os.path.isfile(full) else None
