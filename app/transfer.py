"""A survey as a file: the questionnaire and its settings written as JSON, and a new survey made from such a file.

The file holds what duplicating copies (app/duplicate.py): project, languages, options, blocks, questions with
their choice designs, the flow, quotas (conditions and targets), the look, entry parameters and redirects, end
texts, translations and their status, the dashboards without their links; images and logos lie in it as base64 (key "files"), so one file is the
whole survey. It holds nothing that was collected (participations, counts, contact data, saved states, HB
results, comments), nobody who is responsible (no co-owners, no groups, and of the consent neither the named
researchers nor the contact address) and no secret: the key that signs completion links stays, and so do the keys
of external sections that check the returned values (the file says only that they were signed). Whoever reads a file in becomes admin of the new survey and the researcher
its consent names.

Reading a file is reading what a stranger wrote. Nothing in it is taken as it stands:
- size, depth and counts are limited (LIMITS below), numbers are finite, texts lose their control characters
  (the formatting of texts marks places with such characters);
- every setting is rebuilt from what this version knows: an unknown key, kind or value falls back to the
  standard, the flow tree is built anew node by node, completion links must pass panel.link_error;
- ids in the file only pair things up inside it (a condition and its question). The new survey gets ids, a
  code, a link and file names of its own; no name from the file is ever used as a path;
- images are decoded, measured and written anew with only attribution metadata (media.strip_metadata), logos pass
  media.save_logo (SVG from an allow-list); a former RWTH template (gone since 2026-10-19) keeps its base colours;
- questions keep their settings only within the bounds the editor allows (items.base.ItemType.import_problems),
  and a construct of the toolbox is compared with the toolbox of this installation, not with the file;
- the finished survey must get through the pre-launch check and the export columns without an exception; if
  anything fails, nothing stays: neither rows nor files.
"""
import base64
import binascii
import copy
import json
import math
import os
import re
import secrets
import shutil

from flask import current_app

from app import item_condition, item_texts
from app.extensions import db
from app.items.base import TEXT_MAX, too_long_text
from app.lang import de_en
from app.media import upload_folder
from app.models import Asset, Block, Item, Quota, QuotaCell, Survey, SurveyMember, TranslationMark, utcnow

# version 5: an element's texts hold only what was written or changed; the standard wordings of its type (buttons of
# the consent, federal states, "Keine davon", ...) come from the reading installation in each language (app/item_texts.py,
# UI/UX audit S9). Files of earlier versions hold copies of them: they are dropped when read (an earlier standard
# wording such as "Fortsetzen" goes as well, unless a part of it was changed: _without_standard, U43 of the UI audit
# 2026-10-12).
# version 6: diary studies (app/diary.py): "sampling" and the parts "entry" and "m<n>" of their blocks.
# version 4: interview guides (app/interview.py): "interview" and the part of a block (settings "part").
# version 3: an end may hold several triggers ("cases", flow.cases).
# version 2: quota calculations end nobody; an end "Quote voll" names its quotas (app/quotas.py). A file of
# version 1 may hold a "Quotenprüfung"; it is brought to the new form when read (quotas.upgrade)
FORMAT, VERSION = "askurious-survey", 6
FORMATS = (FORMAT, "queryous-survey")  # files written before the rename to Askurious are read as well
# copied as they are on export and rebuilt value by value on import (_settings)
SETTINGS = ("project", "description", "research_question", "study_plan", "default_language", "languages", "allow_back", "auto_advance",
            "show_progress", "language_choice", "keep_test_runs", "paradata", "page_timing", "focus_paste", "focus_inputs", "ai_bait", "block_load", "repeat_marker", "erase_code", "retention", "geo_ip", "tempo_hint", "tempo_dose", "tempo_options", "tempo_texts", "speeder_pct", "speeder_rule", "speeder_rsi", "data_pool", "quality_rules", "quota_drop_marked", "weighting", "end_texts",
            "contact", "image_style", "max_responses")
ASSET_FIELDS = ("key", "source", "prompt", "model", "status", "style", "rights")
MEDIA_FIELDS = ("kind", "seconds", "name")  # of a video or audio file (stimulus.info), beside its file name and key
MAX_MEDIA = 200            # video and audio files read from one survey file
# The one list of what the survey file and a duplicate (app/duplicate.py) take over from a survey: SETTINGS above as
# they are, these on their own (each by its own code here and in duplicate.create) ...
HANDLED = ("title", "title_translations", "panel", "paper", "flow", "live", "interview", "sampling")
# ... and these never, with the reason. tests/exports/test_export_fields.py checks that every column of Survey is in
# exactly one of SETTINGS, HANDLED and NOT_TAKEN, so a new column cannot be forgotten in one of the two ways.
NOT_TAKEN = {
    "id": "new", "uid": "new", "code": "new", "status": "a copy starts as a draft", "archived": "state",
    "created_host": "where the new one is made", "org_id": "the institution of whoever makes it",
    "zenodo": "the deposit belongs to the original", "coscine": "the upload belongs to the original",
    "setup_step": "where the wizard continues (a duplicate keeps it: duplicate.SETTINGS)",
    "admin_hidden": "the platform's view of the original", "showcase": "the platform's view of the original",
    "org_showcase": "the platform's view of the original", "seal": "SEAL_DEFAULT for every new survey",
    "waves": "links between surveys, made anew", "codings": "categories of collected open answers",
    "declined_count": "collected", "field_end": "field time", "opened_at": "field time", "closed_at": "field time",
    "close_reason": "field time", "start_at": "field time", "start_plan": "field time", "status_log": "field time",
    "qversion": "a new survey starts at version 1", "created_by_id": "whoever makes it",
    "created_at": "new", "updated_at": "new", "data_rev": "new", "structure_rev": "new", "data_nonce": "new",
    "structure_nonce": "new",
}
# a duplicate also keeps these; the file leaves out where the wizard stood and writes the panel without its key (_panel_out)
DUPLICATE_ONLY = ("panel", "setup_step")
END_TEXT_KEYS = ("thanks", "declined", "closed", "screenout", "quotafull", "excluded")
# of the consent: who is responsible stays with the survey the file was written from
CONSENT_PEOPLE = ("researchers", "contact_email")

MAX_DEPTH = 24             # nesting of the JSON
MAX_STRING = 60_000        # characters of one text (a long consent text has a few thousand)
MAX_NUMBER = 1e15          # as items.base.MAX_NUMBER: beyond it a float no longer holds every whole number
MAX_VALUES = 600_000       # values (numbers, texts, lists, ...) in the settings of one question: a large choice design
MAX_BLOCKS, MAX_ITEMS = 500, 5000
MAX_FLOW_NODES, MAX_FLOW_DEPTH = 3000, 12
MAX_LOOP_COLUMNS, MAX_LOOP_ROWS = 60, 2000
MAX_PARAMS = 40
MAX_MARKS = 200_000
MAX_FILES = 3000           # images and logos read from the file (one named twice counts twice)
# of all images together: each is decoded and written anew. 300 megapixels hold a full image set of a choice design
# (a hundred photos of 3 MP); a gigapixel took 70 s of CPU from a file of a few KB (audit 2026-10-13, DA-3)
MAX_PIXELS = 300_000_000
# commas, brackets and braces in the whole file: what it holds besides its images (their data has none of them).
# A file of nothing but empty lists is small on disk and takes a gigabyte once it is read
MAX_STRUCTURE = 3_000_000
NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,15}\Z")
PART_ID = re.compile(r"^[A-Za-z0-9]{1,16}\Z")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _rule_dropped():
    return de_en("Eine Regel für Abschlusslinks wurde nicht übernommen: Sie nennt etwas, das es in der Datei nicht gibt, "
                 "oder eine Adresse, die hier nicht zulässig ist.",
                 "A rule for completion links was not imported: it names something that does not exist in the file, "
                 "or an address that is not allowed here.")


class Refused(Exception):
    """The file cannot be read in; the message is for the researcher (in the language of their pages)."""


class ExportTooLarge(Refused):
    """The inline export would exceed its bounded memory budget; ZIP can carry the images separately."""


def export_limit():
    return max(1, int(current_app.config.get("EXPORT_JSON_MAX_BYTES", 32 * 1024 * 1024)))


def json_chunks(data):
    """Bound encoded JSON before joining it, also for questionnaire structures without images."""
    left = export_limit()
    for chunk in json.JSONEncoder(ensure_ascii=False, indent=1).iterencode(data):
        left -= len(chunk.encode("utf-8"))
        if left < 0:
            raise ExportTooLarge(de_en("Der JSON-Export ist zu groß. Bitte als ZIP oder ohne Bilder exportieren.",
                                       "The JSON export is too large. Please export as ZIP or without images."))
        yield chunk


# --- writing ------------------------------------------------------------------------------------------


def file_name(survey):
    from app import export

    from app.timeutil import local_today  # the date in Europe/Berlin, not the container's UTC (G-07)
    return f"{export.slug(survey.title)}-befragung-{local_today():%Y%m%d}.json"


def dump(survey, with_files=True, raw=None, public=False, media=False):
    """The survey as the dict the file holds. with_files=False: without images and logos (the questions that
    show an image come without it). raw: a dict that gets the image paths instead of the
    file as paths, {name: path} (ZIP streams them, app/survey_files.py); "files" is then empty.
    public: for a package others may read (Zenodo, Coscine, an AI agent): the wording of instruments whose terms
    forbid passing it on (constructs.wording_withheld) is left out, their structure and variable names stay, and the
    element says "wording_withheld" (concept "Vereinheitlichung der Befragungsexporte", E2).
    media (only with raw, the ZIP of app/survey_files.py): the video and audio files of the stimuli come along as
    files beside the survey file, each named in its element's "media" (E8); the JSON alone never holds them."""
    from app import constructs, flow, theme

    files = {}
    remaining = export_limit() * 3 // 4

    def take(name):
        """The name if the file is there and goes into the file, else None."""
        nonlocal remaining
        if not with_files or not name:
            return None
        if name not in files:
            path = os.path.join(upload_folder(survey.uid), name)
            if not os.path.isfile(path):
                return None
            if raw is not None:
                files[name] = path
            else:
                # A size check alone races a growing file: never read more than the remaining budget.
                with open(path, "rb") as f:
                    blob = f.read(remaining + 1)
                remaining -= len(blob)
                if remaining < 0:
                    raise ExportTooLarge(de_en("Der JSON-Export ist zu groß. Bitte als ZIP oder ohne Bilder exportieren.",
                                               "The JSON export is too large. Please export as ZIP or without images."))
                files[name] = base64.b64encode(blob).decode("ascii")
        return name

    from app.export import preload

    preload(survey, assets=True)  # the images of all elements in one query, not one per element (P12)
    consent = survey.consent_item
    blocks = []
    for block in survey.blocks:
        rows = []
        for item in block.items:
            config = copy.deepcopy(item.config or {})
            row = {"id": item.id, "type": item.type, "code": item.code, "answer_mode": item.answer_mode,
                   "config": config, "texts": _own_texts(item, survey)}
            if item.type == "consent":
                for key in CONSENT_PEOPLE:
                    config.pop(key, None)
                if item is consent:
                    # a text that is still the standard text follows the new survey (its title, its researcher)
                    row["standard_body"] = [lang for lang in survey.languages
                                            if ((item.texts or {}).get(lang) or {}).get("body")
                                            and item.texts[lang]["body"] == item.kind.template_body(item, lang)]
            # video and audio stimuli (up to 50 MB each; items/stimulus.py) only beside the file in a ZIP (media)
            images = [{"file": a.filename, **{f: getattr(a, f) for f in ASSET_FIELDS}} for a in item.assets
                      if item.type != "media" and take(a.filename)]
            if item.type == "media":
                info = config.pop("files", None) or {}
                clips = [{"file": a.filename, "key": a.key or "", **{k: info[a.key or ""].get(k) for k in MEDIA_FIELDS}}
                         for a in item.assets if media and raw is not None and a.status == Asset.APPROVED
                         and isinstance(info.get(a.key or ""), dict) and take(a.filename)]
                if clips:
                    row["media"] = clips
            if images:
                row["images"] = images
            if public and constructs.wording_withheld(item):
                row["texts"] = _blank(row["texts"])
                template = (config.get("construct") or {}).get("template")
                if isinstance(template, dict) and "texts" in template:
                    template["texts"] = _blank(template["texts"])
                row["wording_withheld"] = True
            rows.append(row)
        blocks.append({"id": block.id, "titles": dict(block.titles or {}), "show_title": bool(block.show_title),
                       "randomize_items": bool(block.randomize_items), "settings": dict(block.settings or {}), "items": rows})

    look = copy.deepcopy(theme.clean(survey.theme))
    if look:
        logo = look.pop("logo", None)
        if logo and take(logo["file"]):
            logo.pop("icon", None)  # made anew from the logo
            if not take(logo.get("file_dark")):
                for key in ("file_dark", "w_dark", "h_dark"):
                    logo.pop(key, None)
            look["logo"] = logo
        partners = [p for p in look.pop("partners", []) if take(p["file"])]
        if partners:
            look["partners"] = partners

    from app import quotas as quota_mod

    tree = copy.deepcopy(flow.tree(survey))
    # a survey in the field may still hold a "Quotenprüfung": the file always has the new form
    quota_mod.upgrade_tree(tree, {q.id: (q.no_cell, q.name) for q in survey.quotas})
    from app import external

    for node in external.nodes_in(tree):
        # the key that checks the returned values is a secret between the survey and its web app, like the key of
        # the completion links (_panel_out): the file says that the values were signed, never the key
        signed = external.signs(node)
        node.pop("sign", None)
        if signed:
            node["sign"] = {"signed": True}
    from app import webservice

    for node in webservice.nodes_in(tree):
        # the value of a web service's header (an API key) stays on this server: the file says there was one
        if node.pop("secret", None):
            node["secret_set"] = True
    quotas = [{"id": q.id, "code": q.code, "name": q.name, "mode": q.mode, "tolerance": q.tolerance,
               "tolerance_pct": bool(q.tolerance_pct), "hold_minutes": q.hold_minutes, "pick": q.pick,
               "cells": [{"code": c.code, "name": c.name, "conds": copy.deepcopy(c.conds or []), "target": c.target}
                         for c in q.cells]} for q in survey.quotas]
    marks = [{"unit": m.unit, "lang": m.lang, "path": m.path, "source": m.source, "ai": bool(m.ai)}
             for m in db.session.scalars(db.select(TranslationMark).filter_by(survey_id=survey.id).order_by(TranslationMark.id))]
    # dashboards (app/boards/): what they show and how, never their links
    boards = [{"name": b.name, "tiles": copy.deepcopy(b.tiles or []), "settings": dict(b.settings or {})} for b in survey.boards]
    if raw is not None:
        raw.update(files)
    return {
        "format": FORMAT, "version": VERSION, "exported_at": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "survey": {"title": survey.title, "title_translations": dict(survey.title_translations or {}),
                   **{f: copy.deepcopy(getattr(survey, f)) for f in SETTINGS}, "panel": _panel_out(survey),
                   # the paper questionnaire: which sheets are checked, how long scans stay (E-6)
                   "paper": __import__("app.paper", fromlist=["copied"]).copied(survey.paper)},
        "theme": look, "blocks": blocks, "flow": tree, "quotas": quotas,
        "translation_marks": marks, "boards": boards,
        "files": {} if raw is not None else files,
        # a live vote: its settings and quiz (app/live/); runs, answers and what the screen shows stay behind
        "live": live_out(survey),
        # an interview guide: its settings (app/interview.py); None for every other survey
        "interview": __import__("app.interview", fromlist=["guide_state"]).guide_state(survey) if survey.interview is not None else None,
        # a diary study: its schedule without persons (app/diary.py); None for every other survey
        "sampling": __import__("app.diary", fromlist=["copied_state"]).copied_state(survey) if survey.sampling is not None else None,
    }


def _blank(value):
    """Every text in `value` emptied, its shape kept (languages, statements, labels)."""
    if isinstance(value, str):
        return ""
    if isinstance(value, dict):
        return {k: _blank(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_blank(v) for v in value]
    return value


def _panel_out(survey):
    """The entry settings for the file. The key that signs completion links is a secret between the survey and
    its panel: the file says how links are signed and that there was a key, never the key."""
    from app import panel

    cfg = copy.deepcopy(survey.panel)
    if cfg:
        cfg.pop("value_labels", None)  # whom a link was made for (e.g. students' names) stays with the survey
    if cfg and "sign" in cfg:
        sign = panel.sign_config(survey)
        cfg["sign"] = {"alg": sign["alg"], "enc": sign["enc"], "base": sign["base"], "signed": bool(sign["key"])}
    return cfg


def dumps(survey, with_files=True, public=False):
    """The file's content. One value per line, so that the file can be read and compared; an image is one line."""
    return "".join(json_chunks(dump(survey, with_files, public=public)))


# --- reading: the values of a file ------------------------------------------------------------------------


def _no_constant(name):
    raise ValueError(name)  # NaN, Infinity: json.loads takes them, no export and no statistic does


def loads(raw):
    """The dict a file holds, or Refused: not JSON, not a survey file, or from a newer version."""
    if sum(raw.count(mark) for mark in (b",", b"[", b"{")) > MAX_STRUCTURE:
        raise Refused(de_en("Die Datei enthält mehr Einträge, als eine Befragung haben kann.",
                            "The file holds more entries than a survey can have."))
    try:
        data = json.loads(raw.decode("utf-8-sig"), parse_constant=_no_constant)
    except (ValueError, RecursionError):  # also UnicodeDecodeError
        raise Refused(de_en("Die Datei ist keine lesbare JSON-Datei.", "The file is not a readable JSON file.")) from None
    if not isinstance(data, dict) or data.get("format") not in FORMATS:
        raise Refused(de_en("Die Datei ist keine Befragungsdatei von Askurious (erwartet wird eine Datei aus „Als Datei "
                            "exportieren“).",
                            "The file is not a survey file of Askurious (expected is a file from “Export as a file”)."))
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise Refused(de_en("Die Datei nennt keine gültige Formatversion.", "The file names no valid format version."))
    if version > VERSION:
        raise Refused(de_en("Die Datei hat die Formatversion {version}; diese Installation liest bis Version {most}. "
                            "Sie stammt aus einer neueren Version von Askurious.",
                            "The file has format version {version}; this installation reads up to version {most}. "
                            "It comes from a newer version of Askurious.", version=version, most=VERSION))
    return data


def _plain(value, budget, depth=0):
    """The value with nothing in it but what JSON holds, within the limits; texts without control characters."""
    budget[0] -= 1
    if depth > MAX_DEPTH or budget[0] < 0:
        raise Refused(de_en("Die Datei ist zu tief verschachtelt oder ein Element hat zu viele Einstellungen.",
                            "The file is nested too deeply or an element has too many settings."))
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value != value or abs(value) > MAX_NUMBER:
            raise Refused(de_en("Die Datei enthält eine Zahl außerhalb des erlaubten Bereichs.",
                                "The file holds a number outside the allowed range."))
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise Refused(de_en("Die Datei enthält einen Text mit mehr als {n} Zeichen.",
                                "The file holds a text with more than {n} characters.", n=MAX_STRING))
        return _CONTROL.sub("", value)
    if isinstance(value, list):
        return [_plain(v, budget, depth + 1) for v in value]
    if isinstance(value, dict):
        return {_CONTROL.sub("", str(k))[:200]: _plain(v, budget, depth + 1) for k, v in value.items()}
    raise Refused(de_en("Die Datei enthält einen Wert, der nicht gelesen werden kann.", "The file holds a value that cannot be read."))


def _str(value, limit, line=False):
    """A text of at most `limit` characters ("" for anything else); line: on one line, without outer spaces."""
    if not isinstance(value, str):
        return ""
    value = _CONTROL.sub("", value)
    return " ".join(value.split())[:limit] if line else value[:limit]


def _int(value, default, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) > MAX_NUMBER:
        return default
    return max(low, min(high, int(value)))


def _dict(value):
    return value if isinstance(value, dict) else {}


def _name(value):
    """A choice of the file as it is looked up in the tables of the app (`_name(x) in MODES`): a text, or None.
    A list or a dict there is not "unknown" to a dict or set, but an error (unhashable)."""
    return value if isinstance(value, str) else None


def _dicts(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _twin(mapping, old):
    """The new id for an id of the file, or None."""
    return mapping.get(old) if isinstance(old, int) and not isinstance(old, bool) else None


def _by_language(value, limit):
    from app.surveys import LANGUAGE_NAMES

    return {lang: _str(text, limit) for lang, text in _dict(value).items() if lang in LANGUAGE_NAMES and isinstance(text, str)}


class Budget:
    """What the images of one upload may take together: MAX_FILES images, IMPORT_MAX_BYTES, MAX_PIXELS. One for
    each file read in, or one shared by all surveys of a ZIP (wizard.import_file)."""

    def __init__(self):
        self.count, self.pixels, self.left = 0, MAX_PIXELS, current_app.config["IMPORT_MAX_BYTES"]

    def take(self, size):
        self.count += 1
        self.left -= size
        if self.count > MAX_FILES or self.left < 0:
            raise Refused(de_en("Die Datei verweist auf mehr Bilder, als eingelesen werden können.",
                                "The file refers to more images than can be read in."))


class _Files:
    """The images of the file, decoded one at a time. What is read counts against the size of the upload: a
    small file naming its one image a thousand times would otherwise fill the disk. fetch: where the images lie
    when the file is part of a ZIP (app/survey_files.py), fetch(name, largest size) -> bytes or None. budget: what
    all images of the upload may take (Budget), shared by the surveys of one ZIP."""

    def __init__(self, raw, fetch=None, budget=None):
        self.raw, self.fetch, self.budget = _dict(raw), fetch, budget or Budget()
        self.one = current_app.config["MAX_CONTENT_LENGTH"]  # as an upload of one image

    def get(self, name):
        if self.fetch is not None and not self.raw:
            blob = self.fetch(name, self.one) if isinstance(name, str) else None
            if blob is None:
                return None
            self.budget.take(len(blob))
            return blob
        data = self.raw.get(name) if isinstance(name, str) else None
        if not isinstance(data, str) or len(data) > self.one * 4 // 3 + 4:
            return None
        self.budget.take(len(data) * 3 // 4)
        try:
            return base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return None

    def media(self, name):
        """The bytes of a video or audio file beside the survey file in a ZIP (E8), at most MEDIA_MAX_MB; None if the
        upload does not hold it. Counted in the budget of the upload like an image."""
        if self.fetch is None or not isinstance(name, str):
            return None
        blob = self.fetch(name, int(current_app.config["MEDIA_MAX_MB"]) * 1024 * 1024)
        if blob is not None:
            self.budget.take(len(blob))
        return blob

    def image(self, name):
        """The bytes of an image as stored: only attribution metadata, written anew. None if the file does not
        hold it or it is no image. An image of a few kilobytes can hold millions of pixels, and each is decoded:
        all images of a file together stay under MAX_PIXELS."""
        from app import media

        blob = self.get(name)
        try:
            img = media.open_image(blob, ["PNG", "JPEG", "WEBP", "GIF"]) if blob else None
            # every frame of a moving image is decoded, not only the first (audit 2026-10-15, T03-N1)
            self.budget.pixels -= img.width * img.height * getattr(img, "n_frames", 1) if img else 0
            if self.budget.pixels < 0:
                raise Refused(de_en("Die Bilder der Datei haben zusammen zu viele Bildpunkte.",
                                    "The images of the file together have too many pixels."))
            return media.strip_metadata(blob) if blob else None
        except media.Busy as e:  # the server is busy, the image is fine: refuse the file rather than lose the image
            raise Refused(str(e)) from None
        except ValueError:
            return None


# --- reading: the parts of a survey -----------------------------------------------------------------------


def _settings(raw, title):
    """The columns of Survey from the file's "survey" (without the entry settings, see _panel)."""
    from app import tempo
    from app.image_styles import IMAGE_STYLES
    from app.surveys import LANGUAGE_NAMES

    languages = list(dict.fromkeys(l for l in (raw.get("languages") if isinstance(raw.get("languages"), list) else [])
                                   if isinstance(l, str) and l in LANGUAGE_NAMES)) or ["de"]
    default = raw.get("default_language") if raw.get("default_language") in languages else languages[0]
    own_title = _str(title, 300, line=True)
    out = {"title": own_title or _str(raw.get("title"), 300, line=True), "languages": languages, "default_language": default,
           "project": _str(raw.get("project"), 300, line=True), "description": _str(raw.get("description"), 20000).strip(),
           "research_question": _str(raw.get("research_question"), 2000).strip()}
    from app import study_plan

    out["study_plan"] = study_plan.clean(raw.get("study_plan"))
    if not out["title"]:
        raise Refused(de_en("Die Datei nennt keinen Titel. Bitte einen Titel für die neue Befragung eingeben.",
                            "The file names no title. Please enter a title for the new survey."))
    if not own_title:  # the translations belong to the title of the file
        names = {l: _str(t, 300, line=True) for l, t in _dict(raw.get("title_translations")).items() if l in languages and l != default}
        out["title_translations"] = {l: t for l, t in names.items() if t} or None
    for key, standard in (("allow_back", True), ("auto_advance", False), ("show_progress", True), ("language_choice", True), ("keep_test_runs", False),
                          ("paradata", False), ("page_timing", False), ("focus_paste", False), ("ai_bait", False), ("block_load", False), ("repeat_marker", False), ("erase_code", False), ("geo_ip", False),
                          ("quota_drop_marked", False)):
        out[key] = raw.get(key) if isinstance(raw.get(key), bool) else standard
    out["focus_inputs"] = out["focus_paste"]  # a new draft: its consent names the counts of today
    out["tempo_hint"] = raw.get("tempo_hint") if _name(raw.get("tempo_hint")) in dict(tempo.MODES) else "off"
    out["tempo_dose"] = raw.get("tempo_dose") if _name(raw.get("tempo_dose")) in dict(tempo.DOSES) else "once"
    if out["tempo_hint"] != "off":
        out["page_timing"] = True  # the hint needs the page times (surveys.apply_options)
    saved = tempo.options(type("Saved", (), {"tempo_options": _dict(raw.get("tempo_options"))}))
    out["tempo_options"] = saved if saved != tempo.options(None) else None
    from app import retention

    # a file from before the periods existed names none; only values the lists offer are taken
    periods = retention.of(type("Saved", (), {"retention": _dict(raw.get("retention"))}))
    out["retention"] = periods if any(periods.values()) else None
    out["tempo_texts"] = tempo.clean_texts({l: t for l, t in _dict(raw.get("tempo_texts")).items() if l in languages})
    pct = raw.get("speeder_pct")
    out["speeder_pct"] = pct if type(pct) is int and 5 <= pct <= 100 else 50
    out["speeder_rule"] = raw.get("speeder_rule") if raw.get("speeder_rule") in ("duration", "rsi") else ""
    from app.export import RSI_DEFAULT, RSI_LIMITS

    limit = raw.get("speeder_rsi")
    out["speeder_rsi"] = limit if type(limit) is int and RSI_LIMITS[0] <= limit <= RSI_LIMITS[1] else RSI_DEFAULT
    from app.export import SOURCES

    saved = _dict(raw.get("data_pool"))
    sources = [x for x in SOURCES if x in (saved.get("sources") if isinstance(saved.get("sources"), list) else [])]
    out["data_pool"] = {"sources": sources or ["real"], "all": saved.get("all") is True} if saved else None
    from app import quality

    marking = _dict(raw.get("quality_rules"))
    out["quality_rules"] = quality.rules(type("Saved", (), {"quality_rules": marking})) if marking else None
    from app import weights

    out["weighting"] = weights.clean(raw.get("weighting"))  # names quotas by their code: they come along with it
    planned = raw.get("max_responses")
    out["max_responses"] = planned if type(planned) is int and 1 <= planned <= 10_000_000 else None
    texts = {lang: {k: _str(v, 3000) for k, v in _dict(per).items() if k in END_TEXT_KEYS}
             for lang, per in _dict(raw.get("end_texts")).items() if lang in LANGUAGE_NAMES}
    out["end_texts"] = texts or None
    contact = _dict(raw.get("contact"))
    if contact:
        out["contact"] = {**{k: contact.get(k) is True for k in ("enabled", "email", "comment")},
                          "texts": {lang: {str(k)[:40]: _str(v, 3000) for k, v in _dict(per).items()}
                                    for lang, per in _dict(contact.get("texts")).items() if lang in LANGUAGE_NAMES}}
    style = _dict(raw.get("image_style"))
    if style:
        key = style.get("key")
        out["image_style"] = {"key": key if isinstance(key, str) and (key in IMAGE_STYLES or key == "custom") else None,
                              "custom": _str(style.get("custom"), 1000)}
    return out


def _panel(raw, notes):
    """Entry parameters and redirects (panel.py), without the rules: they name quotas (_rules). Returns
    (panel or None, the placeholders a completion link may use)."""
    from app import panel

    raw = _dict(raw)
    params, seen = [], set()
    for p in _dicts(raw.get("params"))[:MAX_PARAMS]:
        name = p.get("name")
        if not isinstance(name, str) or not panel.NAME_RE.match(name) or name in seen:
            continue
        seen.add(name)
        allowed = [panel.clean_value(a) for a in (p.get("allowed") if isinstance(p.get("allowed"), list) else []) if isinstance(a, str)]
        params.append({"name": name, "mode": "store" if p.get("mode") == "store" else "redirect",
                       "required": p.get("required") is True, "allowed": [a for a in dict.fromkeys(allowed) if a][:50],
                       **({"once": True} if p.get("once") is True and p.get("mode") == "store" else {})})
    quota_placeholder = raw.get("quota_placeholder") is True
    # signed links: how they are signed comes along, the key does not. The links keep their {{sig}}; until the
    # key is entered again the pre-launch check says so, and the signature stays empty
    given = _dict(raw.get("sign"))
    sign = {"alg": given.get("alg") if _name(given.get("alg")) in panel.SIGN_ALGS else "sha256",
            "enc": given.get("enc") if _name(given.get("enc")) in panel.SIGN_ENCODINGS else "hex",
            "base": given.get("base") if _name(given.get("base")) in panel.SIGN_BASES else "amp", "key": ""}
    signed = given.get("signed") is True and panel.SIG not in seen
    if signed:
        notes.append(de_en("Die Abschlusslinks der Datei sind signiert. Der Signaturschlüssel steht nicht in der Datei; bitte unter "
                           "„Datenerhebung → Teilnahmelink“ neu eintragen.",
                           "The completion links of the file are signed. The signature key is not in the file; please "
                           "enter it again under “Data collection → Participation link”."))
    names = panel._names(params, quota_placeholder, signed)
    redirects, modes = {}, {}
    for outcome, label in panel.OUTCOMES.items():
        url = _str(_dict(raw.get("redirects")).get(outcome), 1000).strip()
        if url and panel.link_error(url, names):
            notes.append(de_en("Die Weiterleitung „{label}“ wurde nicht übernommen: Ihre Adresse ist hier nicht zulässig.",
                               "The redirect “{label}” was not imported: its address is not allowed here.", label=label))
            url = ""
        redirects[outcome] = url
        if _dict(raw.get("modes")).get(outcome) in ("button", "auto"):
            modes[outcome] = raw["modes"][outcome]
    if not (params or any(redirects.values()) or raw.get("rules") or quota_placeholder or raw.get("multi") is True or given):
        return None, names
    return {"params": params, "redirects": redirects, "modes": modes, "rules": [], "quota_placeholder": quota_placeholder,
            "multi": raw.get("multi") is True, **({"sign": sign} if given else {})}, names


def _rules(raw, cfg, quotas, names, notes, items=None):
    """The link rules of the entry settings, pointing at the parameters, quotas and questions of the new survey."""
    from app import flow, panel

    declared, out = {p["name"] for p in cfg["params"]}, []
    for rule in _dicts(_dict(raw).get("rules"))[:panel.MAX_RULES]:
        ref, outcome = rule.get("item"), _name(rule.get("outcome"))
        if isinstance(ref, int) or (isinstance(ref, str) and ref.startswith(flow.STATE_REF)):  # an answer
            cond = _cond(rule, items or {}, quotas)
            url = _str(rule.get("url"), 1000).strip()
            if cond["item"] is None or outcome not in panel.OUTCOMES or outcome in panel.EARLY_OUTCOMES                     or panel.link_error(url, names):
                notes.append(_rule_dropped())
                continue
            out.append({"outcome": outcome, **cond, "url": url,
                        "mode": rule.get("mode") if _name(rule.get("mode")) in panel.LINK_MODES else "now"})
            continue
        kind, _, key = ref.partition(":") if isinstance(ref, str) else ("", "", "")
        if kind in ("q", "qc"):
            key = _twin(quotas, int(key)) if key.isdigit() and len(key) < 12 else None
        elif kind != "p" or key not in declared:
            key = None
        url = _str(rule.get("url"), 1000).strip()
        value = _str(rule.get("value"), 200).strip()
        if (not key or outcome not in panel.OUTCOMES or not value or panel.link_error(url, names)
                or (outcome in panel.EARLY_OUTCOMES and kind != "p")):
            notes.append(_rule_dropped())
            continue
        out.append({"outcome": outcome, "item": f"{kind}:{key}", "op": rule.get("op") if _name(rule.get("op")) in flow.OPS else "eq",
                    "value": value, "url": url, "mode": rule.get("mode") if _name(rule.get("mode")) in panel.LINK_MODES else "now"})
    return out


def _free_code(base, used):
    n = 1
    while f"{base}{n}" in used:
        n += 1
    return f"{base}{n}"


def _images(row, item, new, files, notes):
    """The images of a question: written anew under names of this survey, retaining only attribution metadata."""
    from app import asset_rights, media

    reviewed = 0
    for image in _dicts(row.get("images")):
        blob = files.image(image.get("file"))
        name = media.save_bytes(new, blob) if blob else None
        if name is None:  # a question without that image, not one with a broken image
            notes.append(de_en("{code}: Ein Bild wurde nicht übernommen (es fehlt in der Datei oder ist kein lesbares Bild).",
                               "{code}: an image was not imported (it is missing in the file or is not a readable image).",
                               code=item.code))
            continue
        ai = image.get("source") == "ai"
        status = image.get("status") if image.get("status") in (Asset.PENDING, Asset.APPROVED, Asset.REJECTED) else Asset.PENDING
        item.assets.append(Asset(
            filename=name, key=_str(image.get("key"), 500), prompt=_str(image.get("prompt"), 5000),
            model=_str(image.get("model"), 60, line=True), style=_str(image.get("style"), 40, line=True),
            source="ai" if ai else "upload",
            rights=asset_rights.imported(image.get("rights")),
            # the review of a generated image comes along as the file has it: approved stays approved, rejected
            # stays rejected. From 2026-10-02 (code review K40) every generated image waited for approval again,
            # rejected ones too; a live poll's slide then had no approved picture and its screen stayed blank
            # (pre-test 2026-10-20, finding 13). An uploaded image of the same file was taken as approved all
            # along, and "source" is the file's own word, so waiting protected nothing; a note says to look.
            status=status))
        reviewed += ai and status == Asset.APPROVED
    if reviewed:
        notes.append(de_en("{code}: {n} KI-Bild(er) mit ihrer Freigabe aus der Datei übernommen; bitte im Element prüfen.",
                           "{code}: {n} AI image(s) imported with their approval from the file; please check them in the element.",
                           code=item.code, n=reviewed))


def _media(row, item, new, files, notes):
    """The video and audio files of a stimulus from the ZIP (E8): each checked by its first bytes (stimulus.sniff), at
    most MEDIA_MAX_MB, all of the survey together at most MEDIA_SURVEY_MAX_MB, written under a new name; the element's
    files are rebuilt from what was found, never taken from the file."""
    from app import media
    from app.items import stimulus

    found, room = {}, int(current_app.config["MEDIA_SURVEY_MAX_MB"]) * 1024 * 1024
    for clip in _dicts(row.get("media"))[:MAX_MEDIA]:
        key = _str(clip.get("key"), 41, line=True)
        blob = files.media(clip.get("file")) if key not in found else None
        mime = stimulus.sniff(blob[:16]) if blob else None
        if mime is None or len(blob) > room:
            notes.append(de_en("{code}: Eine Video- oder Audiodatei wurde nicht übernommen (sie fehlt, ist zu groß oder "
                               "keine abspielbare Datei); bitte im Element neu hochladen.",
                               "{code}: a video or audio file was not imported (it is missing, too large or not a "
                               "playable file); please upload it again in the element.", code=item.code))
            continue
        room -= len(blob)
        name = media.save_media(new, blob, stimulus.MIMES[mime][1])
        try:
            seconds = float(clip.get("seconds"))
        except (TypeError, ValueError):
            seconds = 0.0
        found[key] = {"kind": clip.get("kind") if clip.get("kind") in stimulus.KINDS else stimulus.MIMES[mime][0],
                      "mime": mime, "bytes": len(blob),
                      "seconds": round(seconds, 2) if math.isfinite(seconds) and 0 < seconds <= stimulus.MAX_SECONDS else 0.0,
                      "name": _str(clip.get("name"), 200, line=True)}
        item.assets.append(Asset(key=key, filename=name, source="upload", status=Asset.APPROVED))
    if found:
        item.config = {**item.config, "files": found}


def _text_fits(value, model, depth=0):
    """A text of a question as the pages expect it: a string, or texts per entry (options, statements, pairs:
    a dict, at most three deep) or in a row (scale labels: a list of strings). model: the type's own text of
    this name, if it has one; then the kind (string, dict, list) must be the same. Numbers, null and anything
    else are no texts: the editor, the results or the Word export would fail on them later."""
    if isinstance(value, str):
        return model is None or isinstance(model, str)
    if isinstance(value, dict):
        return (model is None or isinstance(model, dict)) and depth < 3 and all(
            isinstance(k, str) and _text_fits(v, None, depth + 1) for k, v in value.items())
    if isinstance(value, list):
        return (model is None or isinstance(model, list)) and depth < 3 and all(isinstance(v, str) for v in value)
    return False


def _own_texts(item, survey):
    """The texts of an element as the file holds them: without copies of standard wordings (version 5); a construct
    keeps its validated wording as it is."""
    texts = copy.deepcopy(item.texts or {})
    if (item.config or {}).get("construct"):
        return texts
    return item_texts.strip(item.kind, texts, survey.default_language, earlier=False)


def _without_standard(kind, texts, default):
    """The texts of a question read from a file, without copies of standard wordings. An earlier standard wording
    ("Fortsetzen" and its note on the consent) goes too, so the element shows today's ("Weiter", as on every page):
    the file carried it on from an export, nobody typed it (UI audit 2026-10-12, U43). It stays in a language where
    a part of that wording is changed (an own note naming "Fortsetzen"), so button and note keep saying the same."""
    recent = item_texts.strip(kind, texts, default, earlier=False)
    out = item_texts.strip(kind, texts, default, earlier=True)
    for lang, t in out.items():
        if isinstance(t, dict) and any(k in t for k in item_texts.former(kind, lang)):
            out[lang] = recent.get(lang, t)
    return out


def _texts(kind, raw, code):
    """The texts of a question by language. Refused: a text of a form the type cannot show."""
    from app.surveys import LANGUAGE_NAMES

    model, out = kind.default_texts("de"), {}
    for lang, per in _plain(_dict(raw), [MAX_VALUES]).items():
        if lang not in LANGUAGE_NAMES or not isinstance(per, dict):
            continue
        for name, value in per.items():
            if not _text_fits(value, model.get(name)):
                raise Refused(de_en("Element „{code}“ ({label}) lässt sich nicht einlesen: Der Text „{name}“ hat "
                                    "nicht die Form eines Textes dieses Elementtyps.",
                                    "Element “{code}” ({label}) cannot be read in: the text “{name}” does not have "
                                    "the form of a text of this element type.", code=code, label=kind.label, name=str(name)[:40]))
        out[lang] = per
    too_long = too_long_text(out)
    if too_long:  # the editor's limit for every text of an element (audit 2026-10-10, T1)
        raise Refused(de_en("Element „{code}“ ({label}) lässt sich nicht einlesen: Ein Text hat {n} Zeichen, höchstens "
                            "{max} sind erlaubt.",
                            "Element “{code}” ({label}) cannot be read in: a text has {n} characters, at most {max} are "
                            "allowed.", code=code, label=kind.label, n=too_long, max=TEXT_MAX))
    return out


def _questionnaire(data, new, files, notes, user):
    """Blocks and questions. Returns ({block id of the file: Block}, {item id of the file: Item}, the row of the
    consent in the file or None). Questions of an experimental type `user` does not have (app/experimental.py) are
    left out, as unknown types are (security audit 2026-10-06, S12)."""
    from app import experimental
    from app import items as kinds
    from app import pagetime
    from app.items.base import CODE_RE

    blocks, pairs, used, consent, total = {}, {}, set(), None, 0
    unknown, locked = [], []
    for raw in _dicts(data.get("blocks"))[:MAX_BLOCKS]:
        settings = _dict(raw.get("settings"))
        block = Block(position=len(new.blocks), titles=_by_language(raw.get("titles"), 300), show_title=raw.get("show_title") is True,
                      randomize_items=raw.get("randomize_items") is True,
                      settings={**{k: True for k in ("demographics", "tempo_hint") if settings.get(k) is True},
                                **pagetime.clean(settings),
                                # the part of a block of an interview guide (app/interview.py) or a diary study (app/diary.py)
                                **({"part": settings["part"]} if settings.get("part") in ("guide", "short", "post", "entry", "exit")
                                   or re.fullmatch(r"m\d{1,3}", str(settings.get("part", ""))) else {})})
        for row in _dicts(raw.get("items")):
            total += 1
            if total > MAX_ITEMS:
                raise Refused(de_en("Die Datei enthält mehr als {n} Elemente.", "The file holds more than {n} elements.", n=MAX_ITEMS))
            kind = kinds.REGISTRY.get(row.get("type")) if isinstance(row.get("type"), str) else None
            if kind is None:
                unknown.append(_str(row.get("code"), 64, line=True) or "?")
                continue
            if getattr(kind, "experimental", None) and not experimental.enabled(kind.experimental, user=user):
                locked.append(_str(row.get("code"), 64, line=True) or "?")
                continue
            if kind.key == "consent":
                if consent is not None:
                    notes.append(de_en("Die Datei enthält mehr als eine Einwilligung; übernommen wurde die erste.",
                                       "The file holds more than one consent; the first was imported."))
                    continue
                consent = row
            code = row.get("code").strip() if isinstance(row.get("code"), str) else ""  # stripped as in the editor
            # as the editor names them: a variable name, "_pb1" for a page break (no column in any export)
            if not (CODE_RE.match(code) or (kind.key == "page_break" and re.fullmatch(r"_pb\d{1,6}", code))):
                code = ""
            if not code or code in used:
                was, code = code, _free_code(code or {"page_break": "_pb", "consent": "consent"}.get(kind.key, "Q"), used)
                if was:
                    notes.append(de_en("Der Variablenname „{was}“ kam in der Datei doppelt vor; ein Element heißt jetzt „{code}“.",
                                       "The variable name “{was}” occurred twice in the file; one element is now called “{code}”.",
                                       was=was, code=code))
            used.add(code)
            # only the settings the type names, each as the editor would store it (ItemType.import_rules)
            config, unfit, dropped = kind.import_config(_plain(_dict(row.get("config")), [MAX_VALUES]))
            if unfit or kind.import_rules is None:
                raise Refused(de_en("Element „{code}“ ({label}) lässt sich nicht einlesen: Die Einstellung „{name}“ "
                                    "hat einen Wert, den der Editor nicht zulässt.",
                                    "Element “{code}” ({label}) cannot be read in: the setting “{name}” has a value the "
                                    "editor does not allow.", code=code, label=kind.label, name=(unfit or ["?"])[0]))
            if dropped:
                notes.append(de_en("{code}: Nicht übernommen wurde {names}", "{code}: not imported: {names}", code=code,
                                   names=", ".join(de_en("„{k}“", "“{k}”", k=k[:40]) for k in dropped[:5]))
                             + (de_en(" (den Versuchsplan bitte neu erzeugen).", " (please generate the experimental design again).")
                                if "design" in dropped else de_en(" (hier unbekannte Einstellung).", " (setting unknown here).")))
            texts = _texts(kind, row.get("texts"), code)
            if not config.get("construct"):  # copies of standard wordings (files before version 5) are not kept (S9)
                texts = _without_standard(kind, texts, new.default_language)
            item = Item(position=len(block.items), type=kind.key, code=code, texts=texts, config=config,
                        answer_mode=row.get("answer_mode") if row.get("answer_mode") in Item.ANSWER_MODES else "optional")
            block.items.append(item)
            _images(row, item, new, files, notes)
            if kind.key == "media":
                _media(row, item, new, files, notes)
            if isinstance(row.get("id"), int) and not isinstance(row.get("id"), bool):
                pairs.setdefault(row["id"], item)
        new.blocks.append(block)
        if isinstance(raw.get("id"), int) and not isinstance(raw.get("id"), bool):
            blocks.setdefault(raw["id"], block)
    if unknown:
        notes.append(de_en("{n} Element(e) mit einem Typ, den diese Installation nicht kennt, wurden ausgelassen: {codes}.",
                           "{n} element(s) of a type this installation does not know were left out: {codes}.",
                           n=len(unknown), codes=", ".join(unknown[:10]) + (" …" if len(unknown) > 10 else "")))
    if locked:
        notes.append(de_en("{n} Element(e) eines experimentellen Typs, der für Sie nicht freigeschaltet ist, wurden "
                           "ausgelassen: {codes}.",
                           "{n} element(s) of an experimental type that is not switched on for you were left out: {codes}.",
                           n=len(locked), codes=", ".join(locked[:10]) + (" …" if len(locked) > 10 else "")))
    return blocks, pairs, consent


def _cond(raw, items, quotas):
    """A condition of the file ({"item", "op", "value"}, or several: {"conds": [...], "rule"}) on the questions
    and quotas of the new survey; one whose question is not there keeps no question (the pre-launch check names it)."""
    from app import flow, panel

    raw = _dict(raw)
    if isinstance(raw.get("conds"), list):
        return {"conds": [_cond(c, items, quotas) for c in raw["conds"][:flow.MAX_CONDS] if isinstance(c, dict)],
                "rule": "any" if raw.get("rule") == "any" else "all"}
    ref, item = raw.get("item"), None
    if ref in ("att", "tempo", "geo:country", "geo:state") or (isinstance(ref, str) and ref.startswith("p:") and panel.NAME_RE.match(ref[2:])):
        item = ref  # a URL parameter, or the attention checks failed so far (app/quality.py)
    elif isinstance(ref, str) and ref.startswith(("q:", "qc:")):
        kind, _, qid = ref.partition(":")
        twin = _twin(quotas, int(qid)) if qid.isdigit() and len(qid) < 12 else None
        item = f"{kind}:{twin}" if twin else None
    elif isinstance(ref, str) and ref.startswith(flow.STATE_REF):  # the federal state of a postal code question
        twin = _twin(items, flow.source_id(ref)) if len(ref) < 16 else None
        item = f"{flow.STATE_REF}{twin}" if twin else None
    else:
        item = _twin(items, ref)
    value = raw.get("value")
    value = str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
    return {"item": item, "op": raw.get("op") if isinstance(raw.get("op"), str) and raw["op"] in flow.OPS else "eq",
            "value": _str(value, 200)}


def _quotas(data, new, items):
    """The quotas with their cells: conditions and targets, every counter at zero. Returns {id of the file: Quota}."""
    from app import quotas

    pairs, codes = {}, set()
    for raw in _dicts(data.get("quotas"))[:quotas.MAX_QUOTAS]:
        code = raw.get("code").strip() if isinstance(raw.get("code"), str) else ""
        code = code if quotas.CODE_RE.match(code) else ""
        if not code or code.lower() in codes:
            code = _free_code("quote", codes)
        codes.add(code.lower())
        quota = Quota(code=code, name=_str(raw.get("name"), 200, line=True) or code, position=len(new.quotas),
                      mode=raw.get("mode") if _name(raw.get("mode")) in quotas.MODES else Quota.HARD,
                      tolerance=_int(raw.get("tolerance"), 10, 0, 100_000), tolerance_pct=raw.get("tolerance_pct") is not False,
                      # only a file of version 1 says "end": then flow.normalize puts a screen-out into the flow
                      no_cell="end" if raw.get("no_cell") == "end" else "continue",
                      hold_minutes=_int(raw.get("hold_minutes"), quotas.DEFAULT_HOLD, 1, 7 * 24 * 60),
                      count_at="complete",
                      pick=raw.get("pick") if _name(raw.get("pick")) in quotas.PICK else "first")
        taken = set()
        for cell in _dicts(raw.get("cells"))[:quotas.MAX_CELLS]:
            ccode = cell.get("code").strip() if isinstance(cell.get("code"), str) else ""
            ccode = ccode if quotas.CODE_RE.match(ccode) else ""
            if not ccode or ccode.lower() in taken:
                ccode = _free_code("z", taken)
            taken.add(ccode.lower())
            conds = [_cond(c, items, {}) for c in _dicts(cell.get("conds"))[:quotas.MAX_CONDS]]
            quota.cells.append(QuotaCell(code=ccode, name=_str(cell.get("name"), 200, line=True) or ccode, conds=conds,
                                         target=_int(cell.get("target"), 0, 0, 1_000_000), closed=False, running=0, done=0,
                                         refused=0, over=0, position=len(quota.cells)))
        new.quotas.append(quota)
        if isinstance(raw.get("id"), int) and not isinstance(raw.get("id"), bool):
            pairs.setdefault(raw["id"], quota)
    return pairs


def live_out(survey):
    """The part of a live vote's state that belongs to the vote, not to a session: options, quiz, wall; None for a
    questionnaire."""
    if survey.live is None:
        return None
    from app import live

    st = live.state(survey)
    return {**{k: st[k] for k in live.OPTIONS if k in st}, "quiz": copy.deepcopy(st.get("quiz") or {}), "wall": list(st.get("wall") or []),
            **{k: copy.deepcopy(st[k]) for k in ("flow", "teams", "emojis") if k in st}}  # not the contact address


def _live(raw, items):
    """A live vote's state from the file: options as booleans, quiz settings checked, question ids those of the new
    survey (items: id in the file -> new id), a first run."""
    from app import live

    raw = _dict(raw)
    quiz = {}
    for key, entry in _dict(raw.get("quiz")).items():
        old = int(key) if isinstance(key, str) and key.isdigit() else None
        entry = _dict(entry)
        if old not in items:
            continue
        seconds = entry.get("seconds") if type(entry.get("seconds")) is int and 5 <= entry["seconds"] <= 600 else None
        value = entry.get("value") if isinstance(entry.get("value"), (int, float)) and not isinstance(entry.get("value"), bool) else None
        tol = entry.get("tol") if isinstance(entry.get("tol"), (int, float)) and not isinstance(entry.get("tol"), bool) else 0
        correct = [str(c)[:40] for c in entry.get("correct", []) if isinstance(c, (str, int))][:50] if isinstance(entry.get("correct"), list) else []
        quiz[str(items[old])] = {"seconds": seconds, **({"value": value, "tol": abs(tol)} if value is not None else {"correct": correct})}
    return {"screen": None, "show": True, "closed": [], "runs": [], "run": live._run("Durchgang 1"),
            **{k: raw[k] for k in live.OPTIONS if isinstance(raw.get(k), bool)}, "quiz": quiz,
            "wall": [items[w] for w in raw.get("wall", []) if isinstance(w, int) and w in items] if isinstance(raw.get("wall"), list) else [],
            **({"flow": raw["flow"]} if raw.get("flow") in ("questions", "blocks") else {}),
            **({"teams": live.clean_teams(raw["teams"])} if isinstance(raw.get("teams"), list) else {}),
            **({"emojis": [e for e in live.REACTION_CHOICES if e in raw["emojis"]]}
               if isinstance(raw.get("emojis"), list) else {})}


def _boards(data, new, items, quotas):
    """The dashboards with their tiles on the questions and quotas of the new survey; a tile whose element the
    file does not hold is left out, every setting is checked again when the dashboard is shown (boards.tiles_of)."""
    from app import boards
    from app.models import Board

    for raw in _dicts(data.get("boards"))[:boards.MAX_BOARDS]:
        tiles = []
        for tile in _dicts(raw.get("tiles"))[:boards.MAX_TILES]:
            key = boards.remap(tile.get("key") if isinstance(tile.get("key"), str) else None, items, quotas)
            if key:
                tiles.append({"key": key, "view": tile.get("view") if _name(tile.get("view")) in boards.VIEWS else "",
                              "w": boards.width_of(tile), "x": boards.start_of(tile, boards.width_of(tile)), "pct": tile.get("pct") is not False,
                              "n": tile.get("n") is not False,
                              "by": boards.remap_split(tile.get("by") if isinstance(tile.get("by"), str) else None, items, quotas) or "",
                              "title": _str(tile.get("title"), 200, line=True),
                              **({"kind": tile.get("kind") if _name(tile.get("kind")) in boards.TEXT_KINDS else "text",
                                  "text": _str(tile.get("text"), boards.MAX_TEXT)} if boards.is_text(key) else {})})
        new.boards.append(Board(name=_str(raw.get("name"), 200, line=True) or f"Dashboard {len(new.boards) + 1}", tiles=tiles,
                                settings=({"min_tile": _dict(raw.get("settings")).get("min_tile")}
                                          if _dict(raw.get("settings")).get("min_tile") in boards.TILE_MINS else {}),
                                position=len(new.boards)))


def _vignette_fields(node, notes):
    """The fields of a vignette loop (vignettes.FIELDS) as the editor stores them, or None if its factors cannot be
    read (the loop stays a table then). The plan comes along as it is; _loop_plans keeps it only if it still fits."""
    from app.items.attributes import ATTRIBUTE_RULES, MAX_ATTRIBUTES, MAX_LEVELS
    from app.items.base import CODE_RE, Unfit, r_flag, r_id, r_maybe, r_rows, r_text
    from app.surveys import LANGUAGE_NAMES

    try:
        attrs = r_rows(MAX_ATTRIBUTES, id=r_id, code=r_text(64, 1), between=r_maybe(r_flag),
                       levels=r_rows(MAX_LEVELS, id=r_id, hint=r_maybe(r_text(300))))(node.get("attributes") or [])
        prohibitions = ATTRIBUTE_RULES["prohibitions"](node.get("prohibitions") or [])
        if any(not CODE_RE.match(a["code"]) for a in attrs):
            raise Unfit
    except Unfit:
        notes.append(de_en("Die Faktoren der Vignettenschleife „{name}“ ließen sich nicht lesen: Sie ist als Tabelle "
                           "übernommen.", "The factors of the vignette loop “{name}” could not be read: it was imported "
                           "as a table.", name=_str(node.get("name"), 100, line=True)))
        return None
    for a in attrs:
        if a.pop("between", False):
            a["between"] = True
    aids = {a["id"] for a in attrs}
    lids = {lv["id"] for a in attrs for lv in a["levels"]}
    levels_of = {a["id"]: {lv["id"] for lv in a["levels"]} for a in attrs}
    prohibitions = [p for p in prohibitions if p["a"] != p["b"] and p["la"] in levels_of.get(p["a"], ())
                    and p["lb"] in levels_of.get(p["b"], ())]
    texts = {}
    for lang, entry in _dict(node.get("texts")).items():
        if lang not in LANGUAGE_NAMES:
            continue
        entry = _dict(entry)
        texts[lang] = {"attributes": {k: _str(v, 500) for k, v in _dict(entry.get("attributes")).items() if k in aids},
                       "levels": {k: _str(v, 500) for k, v in _dict(entry.get("levels")).items() if k in lids},
                       "level_defs": {k: _str(v, 500) for k, v in _dict(entry.get("level_defs")).items() if k in lids}}
    raw = _dict(node.get("plan"))
    plan = {"method": "full" if raw.get("method") == "full" else "doptimal",
            "per_person": _int(raw.get("per_person"), 10, 1, 500), "decks": _int(raw.get("decks"), 1, 1, 500),
            "interactions": []}
    for pair in raw.get("interactions") if isinstance(raw.get("interactions"), list) else []:
        if isinstance(pair, list) and len(pair) == 2 and all(isinstance(p, str) and p in aids for p in pair) \
                and pair[0] != pair[1] and sorted(pair) not in plan["interactions"]:
            plan["interactions"].append(sorted(pair))
    ids, profiles, decks = raw.get("ids"), raw.get("profiles"), raw.get("decks_rows")
    counts = [len(a["levels"]) for a in attrs]
    fits = (isinstance(ids, list) and isinstance(profiles, list) and isinstance(decks, list) and 0 < len(ids) <= MAX_LOOP_ROWS
            and len(ids) == len(profiles) and all(isinstance(r, str) and PART_ID.match(r) for r in ids)
            and len(set(ids)) == len(ids)
            and all(isinstance(p, list) and len(p) == len(counts)
                    and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n for v, n in zip(p, counts))
                    for p in profiles)
            and decks and all(isinstance(d, list) and d and len(d) == len(decks[0]) for d in decks)
            and [r for d in decks for r in d] == ids and isinstance(raw.get("sig"), str))
    if fits:
        plan.update(ids=list(ids), profiles=[list(p) for p in profiles], decks_rows=[list(d) for d in decks],
                    sig=raw["sig"][:40])
    return {"source": "factors", "attributes": attrs, "prohibitions": prohibitions, "texts": texts, "plan": plan}


def _subset(node, cids, row_ids):
    """The "Ausschnitt nach Versuchsplan" of a table loop (app/loop_plan.py) on the columns and rows read in; the
    decks come along only if each of their rows is there (_loop_plans keeps them only if they still fit)."""
    raw = _dict(node.get("subset"))
    cols = list(dict.fromkeys(cids[c] for c in raw.get("cols") or [] if isinstance(c, str) and c in cids))
    out = {"cols": cols, "per_person": _int(raw.get("per_person"), 6, 1, 500), "decks": _int(raw.get("decks"), 1, 1, 500)}
    decks = raw.get("decks_rows")
    if isinstance(decks, list) and decks and isinstance(raw.get("sig"), str) \
            and all(isinstance(d, list) and d and all(isinstance(r, str) and r in row_ids for r in d) for d in decks):
        out.update(decks_rows=[list(d) for d in decks], sig=raw["sig"][:40])
    return out


def _loop_plans(new, tree, notes):
    """The plans of vignette loops and of table loops with an "Ausschnitt nach Versuchsplan", once the survey is
    there: kept (their figures computed anew) only if they still fit what was read in, else left out with a note."""
    from app import doe, flow, loop_plan, vignettes

    for node, _, _ in flow.walk(tree):
        if node.get("t") != "loop":
            continue
        name = node.get("name") or node.get("code")
        if vignettes.is_vignettes(node):
            plan = node["plan"]
            ok = vignettes.plan_current(new, node)
            if ok:
                f = vignettes.factors(new, node)
                size = len(plan["decks_rows"][0])
                try:
                    ok = all(f.profile_allowed(f, p) for p in plan["profiles"]) and (
                        plan["method"] == "full" or len(plan["decks_rows"]) == plan["decks"])
                    plan["stats"] = doe.stats(plan["profiles"], vignettes.level_counts(node),
                                              [list(range(t * size, (t + 1) * size)) for t in range(len(plan["decks_rows"]))],
                                              vignettes.pairs(node), vignettes.between(node)) if ok else {}
                except (IndexError, ValueError, ArithmeticError):
                    ok = False
            if not ok:
                for key in ("ids", "profiles", "decks_rows", "sig", "stats"):
                    plan.pop(key, None)
                if any(a.get("levels") for a in node.get("attributes") or []):
                    notes.append(de_en("Der Versuchsplan der Vignettenschleife „{name}“ passte nach dem Einlesen nicht mehr und "
                                       "wurde verworfen. Bitte auf ihrer Seite „Versuchsplan erzeugen“.",
                                       "The experimental design of the vignette loop “{name}” no longer fitted after reading in and was left "
                                       "out. Please choose “Generate experimental design” on its page.", name=name))
            vignettes.materialize(new, node)
        elif node.get("mode") == "plan":
            subset = node.setdefault(loop_plan.KEY, {"cols": [], "per_person": 6, "decks": 1})
            ok = loop_plan.current(new, node)
            if ok:
                cols, levels, candidates = loop_plan.factor_space(new, node)
                profile = dict(candidates)
                decks = subset["decks_rows"]
                try:
                    ok = all(r in profile for d in decks for r in d) and len({len(d) for d in decks}) == 1
                    size = len(decks[0])
                    subset["stats"] = doe.stats([profile[r] for d in decks for r in d], [len(lv) for lv in levels],
                                                [list(range(t * size, (t + 1) * size)) for t in range(len(decks))]) if ok else {}
                except (IndexError, ValueError, ArithmeticError, KeyError):
                    ok = False
            if not ok:
                for key in ("decks_rows", "sig", "stats"):
                    subset.pop(key, None)
                if subset.get("cols"):
                    notes.append(de_en("Der Versuchsplan der Schleife „{name}“ passte nach dem Einlesen nicht mehr und wurde "
                                       "verworfen. Bitte auf ihrer Seite den Versuchsplan neu erzeugen.",
                                       "The experimental design of the loop “{name}” no longer fitted after reading in and was left out. "
                                       "Please generate the experimental design again on its page.", name=name))


def _flow(raw, blocks, items, quotas, names, notes, web_service=True):
    """The flow tree, built anew node by node from what flow.py knows (see its head for the shapes). A kind of
    node this version does not know dissolves into its children, so no block is lost. web_service=False: web
    services are left out (experimental, not switched on for whoever reads the file in; code audit 2026-10-08, W3).
    Returns (tree, {node id of the file: node id here})."""
    from app import census, flow, panel
    from app.items.base import CODE_RE

    ids, renamed, count, numbers = set(), {}, [0], {"rand": 0, "loop": 0, "ext": 0, "ws": 0}
    left_out = [0]

    def nid(node, prefix):
        old = node.get("id")
        new = old if (isinstance(old, str) and NODE_ID.match(old) and old not in ids and old != "root"
                      and not (old[0] == "k" and old[1:].isdigit())) else None  # "k12" is how block 12 is named
        while new is None or new in ids:
            new = flow._nid(prefix)
        ids.add(new)
        if isinstance(old, str):
            renamed.setdefault(old, new)
        return new

    def link(node, what):
        url = _str(node.get("link"), 1000).strip()
        if url and panel.link_error(url, names):
            notes.append(de_en("Der Abschlusslink {what} wurde nicht übernommen: Seine Adresse ist hier nicht zulässig.",
                               "The completion link {what} was not imported: its address is not allowed here.", what=what))
            url = ""
        return {"link": url, "link_mode": node.get("link_mode") if _name(node.get("link_mode")) in panel.LINK_MODES else "now"}

    def code(node, kind, base):
        numbers[kind] += 1
        given = node.get("code").strip() if isinstance(node.get("code"), str) else ""
        return given if CODE_RE.match(given) else f"{base}{numbers[kind]}"

    def children(node, depth):
        out = []
        for child in _dicts(node.get("children")):
            count[0] += 1
            if count[0] > MAX_FLOW_NODES or depth > MAX_FLOW_DEPTH:
                raise Refused(de_en("Der Ablauf in der Datei hat zu viele oder zu tief verschachtelte Elemente.",
                                    "The flow in the file has too many or too deeply nested elements."))
            out += build(child, depth + 1)
        return out

    def loop_table(node):
        columns, cids = [], {}
        for c in _dicts(node.get("columns"))[:MAX_LOOP_COLUMNS]:
            name = _str(c.get("name"), 40, line=True)
            old = c.get("id")
            if not name or not isinstance(old, str) or old in cids:
                continue
            cids[old] = old if PART_ID.match(old) and old not in cids.values() else flow._nid("c")
            columns.append({"id": cids[old], "name": name, "hidden": c.get("hidden") is True})
        rows, rids = [], set()
        for r in _dicts(node.get("rows"))[:MAX_LOOP_ROWS]:
            rid = r.get("id") if isinstance(r.get("id"), str) and PART_ID.match(r["id"]) and r["id"] not in rids else flow._nid("r")
            rids.add(rid)
            rows.append({"id": rid, "values": {cids[old]: _by_language(cell, 500)
                                               for old, cell in _dict(r.get("values")).items() if old in cids}})
        return columns, rows, cids  # cids: old column id -> new one (for level_cols)

    def build(node, depth):
        out = make(node, depth)
        if node.get("by") == census.MARK and len(out) == 1 and out[0]["t"] == node.get("t") in ("branch", "end", "quota"):
            out[0]["by"] = census.MARK  # made by the quota assistant: a second run replaces it (app/census.py)
            facts = census.clean_facts(node.get("census"), items) if out[0]["t"] == "quota" else None
            if facts:
                out[0]["census"] = facts
        return out

    def make(node, depth):
        kind = node.get("t")
        if kind == "block":
            block = _twin(blocks, node.get("block"))
            return [{"t": "block", "block": block}] if block else []
        if kind == "rand":
            rcode = code(node, "rand", "cond")
            return [{"t": "rand", "id": nid(node, "r"), "code": rcode, "name": _str(node.get("name"), 100, line=True) or rcode,
                     "k": _int(node.get("k"), 1, 1, 1000), "balanced": node.get("balanced") is not False,
                     "shuffle": node.get("shuffle") is True, "children": children(node, depth)}]
        if kind == "branch":
            out = {"t": "branch", "id": nid(node, "b"), "cond": _cond(node.get("cond"), items, quotas),
                   "children": children(node, depth)}
            if _str(node.get("name"), 100, line=True):
                out["name"] = _str(node.get("name"), 100, line=True)
            return [out]
        if kind == "else":
            return [{"t": "else", "id": nid(node, "x"), "of": node.get("of") if isinstance(node.get("of"), str) else "",
                     "children": children(node, depth)}]
        def named(node):  # the quotas a calculation or an end "Quote voll" names, as those of the new survey
            wanted = [_twin(quotas, q) for q in (node.get("quotas") if isinstance(node.get("quotas"), list) else [])]
            return list(dict.fromkeys(q for q in wanted if q))

        if kind == "end" and _dicts(node.get("cases")):
            # an end with triggers (flow.cases): each built from what flow.py knows, on the quotas of the new survey
            found, seen = [], set()
            for raw in _dicts(node.get("cases"))[:flow.MAX_CASES]:
                when = _name(raw.get("when"))
                if when not in flow.END_TRIGGERS:
                    continue
                cid = raw.get("id") if isinstance(raw.get("id"), str) and re.fullmatch(r"c[0-9a-f]{1,12}", raw["id"]) else ""
                if not cid or cid in seen:
                    cid = flow._nid("c")
                seen.add(cid)
                case = {"id": cid, "when": when, "text": _by_language(raw.get("text"), 3000),
                        **link(raw, "eines Endes im Ablauf")}
                if when in ("quotafull", "nocell"):
                    case["quotas"] = named(raw)
                    if when == "quotafull":
                        case["rule"] = "all" if raw.get("rule") == "all" else "any"
                elif when == "cellfull":
                    case["quota"] = _twin(quotas, raw.get("quota")) if isinstance(raw.get("quota"), int) else None
                    case["cells"] = [c.strip() for c in (raw.get("cells") if isinstance(raw.get("cells"), list) else [])[:400]
                                     if isinstance(c, str) and CODE_RE.match(c.strip())] if case["quota"] else []
                found.append(case)
            if found:
                return [{"t": "end", "id": nid(node, "e"), "cases": found}]
        if kind == "end":
            out = {"t": "end", "id": nid(node, "e"), "text": _by_language(node.get("text"), 3000), "kind": flow.end_kind({"kind": _name(node.get("kind"))}),
                   **link(node, de_en("eines Endes im Ablauf", "of an end in the flow"))}
            if out["kind"] == "quotafull" and "quotas" in node:
                out.update(quotas=named(node), rule="all" if node.get("rule") == "all" else "any")
                links = {}
                for old, entry in _dict(node.get("links")).items():  # the links of single quotas and cells
                    twin = _twin(quotas, int(old)) if isinstance(old, str) and old.isdigit() and len(old) < 12 else None
                    if not twin or twin not in out["quotas"] or not isinstance(entry, dict):
                        continue
                    own = link({"link": entry.get("link"), "link_mode": entry.get("mode")}, de_en("einer Quote an einem Ende im Ablauf", "of a quota at an end in the flow"))
                    cells = {code: link({"link": url}, de_en("einer Teilquote an einem Ende im Ablauf", "of a sub-quota at an end in the flow"))["link"]
                             for code, url in list(_dict(entry.get("cells")).items())[:400]
                             if isinstance(code, str) and CODE_RE.match(code)}
                    cells = {code: url for code, url in cells.items() if url}
                    mode = entry.get("mode") if _name(entry.get("mode")) in panel.LINK_MODES else ""
                    if own["link"] or mode or cells:
                        links[str(twin)] = {"link": own["link"], "mode": mode, "cells": cells}
                if links:
                    out["links"] = links
            return [out]
        if kind == "quota":
            out = {"t": "quota", "id": nid(node, "q"), "quotas": named(node)}
            if "full" in node:  # a "Quotenprüfung" of a file of version 1: flow.normalize takes it apart (quotas.upgrade)
                out.update(full="continue" if node.get("full") == "continue" else "end",
                           text=_by_language(node.get("text"), 3000), **link(node, de_en("einer Quotenprüfung", "of a quota check")))
            return [out]
        if kind == "loop":
            lcode = code(node, "loop", "loop")
            columns, rows, cids = loop_table(node)
            levels = [cids[c] for c in node.get("level_cols") or [] if isinstance(c, str) and c in cids]
            mode = node.get("mode") if node.get("mode") in ("k", "level", "plan") else "all"
            out = {"t": "loop", "id": nid(node, "l"), "name": _str(node.get("name"), 100, line=True) or lcode, "code": lcode,
                   "columns": columns, "rows": rows, "mode": mode, **({"level_cols": levels} if mode == "level" else {}),
                   "k": _int(node.get("k"), 1, 1, MAX_LOOP_ROWS), "order": "fixed" if node.get("order") == "fixed" else "random",
                   "balanced": node.get("balanced") is not False, "children": children(node, depth)}
            factors = _vignette_fields(node, notes) if node.get("source") == "factors" else None
            if factors:  # a vignette loop: its rows follow from the plan again (_loop_plans)
                out.update(factors, mode="all")
            elif mode == "plan":  # a D-optimal subset of the table (app/loop_plan.py)
                out["subset"] = _subset(node, cids, {r["id"] for r in rows})
            return [out]
        if kind == "split":
            return [{"t": "split", "id": nid(node, "s")}]
        if kind == "ext":  # an external section (external.py)
            from app import external

            ecode = code(node, "ext", "ext")
            url = _str(node.get("url"), 1000).strip()
            if url and (panel.SIG in panel.PLACEHOLDER.findall(url)
                        or panel.link_error(url, (set(names) - {panel.SIG}) | {external.RETURN})):
                notes.append(de_en("Die Adresse eines externen Abschnitts wurde nicht übernommen: Sie ist hier nicht zulässig.",
                                   "The address of an external section was not imported: it is not allowed here."))
                url = ""
            found, seen_names = [], set()
            for v in _dicts(node.get("values"))[:external.MAX_VALUES]:
                name = v.get("name")
                if isinstance(name, str) and external.NAME_RE.match(name) and name.lower() not in seen_names | {"t", external.SIG}:
                    seen_names.add(name.lower())
                    found.append({"name": name, "kind": "text" if v.get("kind") == "text" else "number"})
            if _dict(node.get("sign")).get("signed") is True:
                notes.append(de_en("Der externe Abschnitt „{code}“ hat seine Rückgabewerte signiert. Der Schlüssel steht nicht "
                                   "in der Datei; bitte im Ablauf „Rückgabewerte signieren“ einschalten und den neuen Schlüssel "
                                   "in die Web-App übernehmen.",
                                   "The external section “{code}” signed its return values. The key is not in the file; "
                                   "please switch on “Sign return values” in the flow and copy the new key into the web app.",
                                   code=ecode))
            return [{"t": "ext", "id": nid(node, "o"), "code": ecode, "name": _str(node.get("name"), 100, line=True) or ecode,
                     "url": url, "mode": "auto" if node.get("mode") == "auto" else "button",
                     "text": _by_language(node.get("text"), 3000), "values": found}]
        if kind == "ws":  # a web service (webservice.py); the value of its header is never in a file
            from app import webservice

            if not web_service:
                left_out[0] += 1
                return []

            wcode = code(node, "ws", "ws")
            url = _str(node.get("url"), 1000).strip()
            if url and webservice.url_error(url):
                notes.append(de_en("Die Adresse eines Web Service wurde nicht übernommen: Sie ist hier nicht zulässig.",
                                   "The address of a web service was not imported: it is not allowed here."))
                url = ""
            found, seen_names = [], set()
            for v in _dicts(node.get("values"))[:webservice.MAX_VALUES]:
                name, path = v.get("name"), v.get("path")
                if (isinstance(name, str) and webservice.NAME_RE.match(name) and isinstance(path, str)
                        and webservice.PATH_RE.match(path) and name.lower() not in seen_names | {"ok"}):
                    seen_names.add(name.lower())
                    found.append({"name": name, "path": path[:200], "kind": "number" if v.get("kind") == "number" else "text"})
            header = _str(node.get("header"), 60).strip()
            if not webservice.HEADER_RE.match(header) or header.lower() in webservice.FORBIDDEN_HEADERS:
                header = ""
            if header and node.get("secret_set") is True:
                notes.append(de_en("Der Web Service „{code}“ hatte einen Wert für die Kopfzeile {header}. Er steht nicht in der "
                                   "Datei; bitte im Ablauf neu eintragen.",
                                   "The web service “{code}” had a value for the header {header}. It is not in the file; "
                                   "please enter it again in the flow.", code=wcode, header=header))
            method = "POST" if node.get("method") == "POST" else "GET"
            return [{"t": "ws", "id": nid(node, "w"), "code": wcode, "name": _str(node.get("name"), 100, line=True) or wcode,
                     "method": method, "url": url, "body": _str(node.get("body"), 5000) if method == "POST" else "",
                     "header": header, "values": found}]
        return children(node, depth)

    root = {"t": "root", "children": children(_dict(raw), 0)}
    if left_out[0]:
        notes.append(de_en("{n} Web Service(s) im Ablauf wurden ausgelassen: Das experimentelle Merkmal „Web Service im "
                           "Ablauf“ ist für Sie nicht freigeschaltet.",
                           "{n} web service(s) in the flow were left out: the experimental feature “Web Service in the "
                           "flow” is not switched on for you.", n=left_out[0]))
    seen = set()
    for node, _, _ in flow.walk(root):
        if node["t"] == "else":  # its branch may have got another id
            node["of"] = renamed.get(node["of"], "")
        if node["t"] == "quota":  # every quota is calculated at one place only (flow.update)
            node["quotas"] = [q for q in node["quotas"] if q not in seen]
            seen.update(node["quotas"])
    codes = set()
    for node, _, _ in flow.walk(root):
        if node["t"] in ("ext", "ws"):  # every external section and web service has its own columns in the export
            while node["code"] in codes:
                numbers[node["t"]] += 1
                node["code"] = f"{node['t']}{numbers[node['t']]}"
            codes.add(node["code"])
    return root, renamed


def _theme(raw, new, user, files, notes):
    """The look: colours and choices through theme.clean, the logos written anew through media.save_logo."""
    from app import asset_rights, media, theme

    raw = _plain(_dict(raw), [MAX_VALUES])
    look = theme.clean({k: v for k, v in raw.items() if k not in ("logo", "partners", "image_rights")})
    imported_rights = asset_rights.clean_theme_records(raw.get("image_rights"), foreign=True)
    renamed_rights = {}
    given, partners_given = _dict(raw.get("logo")), _dicts(raw.get("partners"))
    if not (look or given or partners_given):
        return
    look = look or theme.standard()

    def saved(name):
        blob = files.get(name)
        try:
            saved = media.save_logo(new, blob) if blob else None
            if saved and imported_rights.get(name):
                renamed_rights[saved["file"]] = imported_rights[name]
            return saved
        except ValueError:
            return None

    logo, main = None, saved(given.get("file"))
    if main:
        logo = {**given, "file": main["file"], "w": main["w"], "h": main["h"]}
        for key in ("file_dark", "w_dark", "h_dark", "icon"):
            logo.pop(key, None)
        dark = saved(given.get("file_dark"))
        if dark:
            logo.update(file_dark=dark["file"], w_dark=dark["w"], h_dark=dark["h"])
        try:
            logo["icon"] = media.save_icon(new, main["file"])  # for the browser tab, if the logo is about square
            if logo.get("icon") and renamed_rights.get(main["file"]):
                renamed_rights[logo["icon"]] = asset_rights.imported(renamed_rights[main["file"]])
        except (OSError, ValueError):
            pass
    elif given.get("file"):
        notes.append(de_en("Das Logo wurde nicht übernommen (es fehlt in der Datei oder ist kein zulässiges Bild).",
                           "The logo was not imported (it is missing in the file or is not an allowed image)."))
    partners = []
    for p in partners_given[:theme.MAX_PARTNERS]:
        file = saved(p.get("file"))
        if file:
            partners.append({"file": file["file"], "w": file["w"], "h": file["h"], "alt": p.get("alt")})
    new.theme = theme.clean({**look, "logo": logo, "partners": partners, "image_rights": renamed_rights})


def _constructs(new, notes, user):
    """Questions that say they come from the construct toolbox are measured against the toolbox of this
    installation: the file's own copy of the construct ("template") would let a changed scale pass as the
    validated one, and its answers go into the pooled data of the construct. A construct this installation does
    not have leaves an ordinary question. So does a linked element (app/linked.py) the person may not use: a file
    must not attach a question to someone else's element (and freeze it)."""
    from app import constructs, linked
    from app.models import Construct

    for item in (i for b in new.blocks for i in b.items):
        meta = (item.config or {}).get("construct")
        if meta is None:
            continue
        key = meta.get("key") if isinstance(meta, dict) else None
        if isinstance(key, str) and key in constructs.PERMISSION_REQUIRED:
            # _questionnaire has now normalised keys and values (_plain removes control characters).
            # Check before consulting the catalogue or dropping unknown provenance, not only on raw input.
            raise Refused(constructs.permission_notice())
        known = db.session.scalar(db.select(Construct).filter_by(key=key)) if isinstance(key, str) else None
        if known is not None and constructs.is_linked_key(key) and not linked.usable(user, known):
            known = None
        if known is None or known.spec.get("type") != item.type:
            item.config = {k: v for k, v in item.config.items() if k != "construct"}
            notes.append(f"{item.code}: " + (de_en("Das verknüpfte Element der Datei gibt es hier nicht oder Sie dürfen es nicht "
                                                   "verwenden", "The linked element of the file does not exist here or you "
                                                   "may not use it") if constructs.is_linked_key(key) else
                                             de_en("Das Konstrukt der Datei gibt es in dieser Installation nicht",
                                                   "The construct of the file does not exist in this installation"))
                         + de_en("; das Element ist jetzt eine gewöhnliche Frage.", "; the element is now an ordinary question."))
            continue
        item.config = {**item.config, "construct": {"key": key, "version": known.seed_version, "unlocked": meta.get("unlocked") is True,
                                                    "template": copy.deepcopy(known.spec)}}


def _consent(new, row, user):
    """The consent names the person who read the file in; a text that was the untouched standard text in the
    file is the standard text of this survey. A file without a consent gets the standard one, in front (True)."""
    from app.items.consent import ensure_consent_block

    if (new.live is not None or new.interview is not None) and new.consent_item is None:
        # a live vote (app/live/) and an interview guide (app/interview.py) have none on purpose
        return False
    if ensure_consent_block(new, user):
        return True
    consent = new.consent_item
    from app.items.consent import user_refs

    consent.config = {**consent.config, "researchers": user_refs(user), "contact_email": ""}
    standard = row.get("standard_body") if isinstance(row.get("standard_body"), list) else []
    texts = dict(consent.texts or {})
    for lang in new.languages:
        if lang in standard and isinstance(texts.get(lang), dict):
            texts[lang] = {**texts[lang], "body": consent.kind.template_body(consent, lang)}
    consent.texts = texts
    return False


def _marks(data, new, blocks, items, nodes, own_title):
    """What the translations are based on (page "Übersetzung"), for the units that exist in the new survey."""
    from app import flow

    here = {n["id"] for n, _, _ in flow.walk(flow.tree(new)) if n["t"] != "block"}
    # a consent text that was the standard text is written anew (_consent: the title, whoever read the file in);
    # what a translation of it was based on is then out of date and would show it as changed since the translation
    standard = {f"item:{_twin(items, row.get('id'))}" for block in _dicts(data.get("blocks")) for row in _dicts(block.get("items"))
                if row.get("type") == "consent" and isinstance(row.get("standard_body"), list)
                and new.default_language in row["standard_body"] and isinstance(row.get("id"), int)}
    seen = set()
    for m in _dicts(data.get("translation_marks"))[:MAX_MARKS]:
        unit, lang, path = m.get("unit"), m.get("lang"), m.get("path")
        if not (isinstance(unit, str) and isinstance(path, str) and len(path) <= 200 and lang in new.languages):
            continue
        path = _CONTROL.sub("", path)  # before the look for doubles: two paths may differ in such a character only
        kind, _, ref = unit.partition(":")
        if kind in ("item", "block") and ref.isdigit() and len(ref) < 12:
            twin = _twin(items if kind == "item" else blocks, int(ref))
            unit = f"{kind}:{twin}" if twin else None
        elif kind in ("flow", "loop"):
            unit = f"{kind}:{nodes[ref]}" if nodes.get(ref) in here else None
        elif unit not in ("end", "contact") and not (unit == "title" and not own_title):
            unit = None
        if unit is None or (unit, lang, path) in seen or (unit in standard and path == "body"):
            continue
        seen.add((unit, lang, path))
        db.session.add(TranslationMark(survey_id=new.id, unit=unit, lang=lang, path=path,
                                       source=_str(m.get("source"), MAX_STRING), ai=m.get("ai") is True))


def _proof_items(new):
    """Every question must get through what its pages run: the type's own view of its settings together, its
    checks, the columns of its export and the page participants see, in every language. The rules of the types
    keep the settings within bounds; this catches texts and lists of a shape the pages do not expect."""
    from flask import render_template

    for block in new.blocks:
        for item in block.items:
            try:
                problems = item.kind.import_problems(item)
                if not problems:
                    item.kind.validate(item)
                    item.kind.export_columns(item)
                    item.kind.used_placeholders(item)
                    for lang in new.languages:
                        render_template("participant/_item.html", survey=new, item=item, lang=lang, answers={}, errors={},
                                        variables={}, preview=True)
                        item.kind.questionnaire_lines(item, lang)
                    _proof_researcher_pages(item)
            except Exception:
                problems = [de_en("Seine Texte oder Einstellungen haben eine Form, die zu diesem Elementtyp nicht passt.",
                                  "Its texts or settings have a form that does not fit this element type.")]
            if problems:
                raise Refused(de_en("Element „{code}“ ({label}) lässt sich nicht einlesen: {problem}",
                                    "Element “{code}” ({label}) cannot be read in: {problem}",
                                    code=item.code, label=item.kind.label, problem=problems[0]))


def _proof_researcher_pages(item):
    """What the pages of the researchers do with a question: the Word questionnaire in both versions, the
    comparison with its construct, an empty table of results. Without python-docx on the server (vendor.zip
    without it) the Word questionnaire is not tried: that is no fault of the file."""
    from app import constructs

    try:
        from docx import Document
    except ImportError:
        Document = None
    if Document is not None:
        from app import questionnaire_docx

        for version in questionnaire_docx.VERSIONS:
            questionnaire_docx._item(questionnaire_docx.Ctx(Document(), item.survey.default_language, version), item)
    constructs.info(item)


def _proof_survey(new):
    """And the survey as a whole: the pre-launch check with flow, loops and quotas, the columns of the export, the
    estimate of the duration."""
    from app import design, duration, export

    try:
        design.check(new)
        export.columns(new, with_hb=False)
        duration.estimate(new)
    except Exception:
        raise Refused(de_en("Die Befragung der Datei lässt sich nicht aufbauen: Ihre Einstellungen passen nicht zusammen.",
                            "The survey of the file cannot be built: its settings do not fit together.")) from None


def may(user):
    """Reading in at all: "Anlegen" for some type; each survey of the file then needs it for its own (_build)."""
    from app.auth import may_create_surveys

    return may_create_surveys(user)


# --- a file of a survey that is already here: as duplicating, under a title of its own ------------------------


def title_of(data, title=""):
    """The title the new survey would get: the one given, else the file's."""
    return _str(title, 300, line=True) or _str(_dict(data.get("survey")).get("title"), 300, line=True)


def twin_of(user, title):
    """A survey this researcher can open (own or through a group, archived ones too) that has this title, or
    None. Reading in the file of a survey that is already here is duplicating it: the new one needs a title of
    its own, so that the two can be told apart. Everything else is its own anyway (ids, code, link, files)."""
    from app import access

    wanted = " ".join(title.split()).casefold()
    return next((s for s in db.session.scalars(db.select(Survey).filter(Survey.id.in_(access.survey_ids(user))))
                 if " ".join(s.title.split()).casefold() == wanted), None)


def suggested_title(title):
    from app.duplicate import TITLE_SUFFIX

    return title[:300 - len(TITLE_SUFFIX)] + TITLE_SUFFIX


PENDING_SECONDS = 3600
TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,40}\Z")


def _pending():
    # next to the upload folder, not in it: nothing there is ever served
    return os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "import_pending")


def keep(user, raw):
    """Put an uploaded file aside while the page shows what it holds and asks for titles (a file field cannot be
    filled in again).
    One file per person, gone after an hour; returns the token that names it for this person."""
    folder = _pending()
    os.makedirs(folder, exist_ok=True)
    sweep_pending(user_id=user.id)
    token = secrets.token_urlsafe(18)
    with open(os.path.join(folder, f"{user.id}-{token}.upload"), "wb") as f:
        f.write(raw)
    return token


def sweep_pending(user_id=None):
    """Remove the files put aside that are older than an hour, and all of `user_id` (a new upload, a deleted
    account). Also from the housekeeping (lifecycle.close_due_surveys), so nothing waits for the next upload."""
    import time

    folder = _pending()
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if (user_id is not None and name.startswith(f"{user_id}-")) or time.time() - os.path.getmtime(path) > PENDING_SECONDS:
                os.remove(path)
        except OSError:
            pass


def kept(user, token):
    """The file put aside under this token for this person, or None."""
    if not isinstance(token, str) or not TOKEN.match(token):
        return None
    try:
        with open(os.path.join(_pending(), f"{user.id}-{token}.upload"), "rb") as f:
            return f.read()
    except OSError:
        return None


def forget(user, token):
    if isinstance(token, str) and TOKEN.match(token):
        try:
            os.remove(os.path.join(_pending(), f"{user.id}-{token}.upload"))
        except OSError:
            pass


def create(data, user, title="", fetch=None, budget=None):
    """A new draft from the dict of a file (loads), committed. title: another title than the file's. fetch: the
    images of a file in a ZIP (_Files); budget: what the images may take, shared by the surveys of one upload
    (Budget; default: one of this file's own). Returns
    (survey, notes for the researcher). Raises Refused if the file cannot be read in; then nothing stays,
    neither rows nor files. Needs a request (the questions are shown once, as on their pages).

    What takes time (decoding the images, showing every question once) happens before anything is written to
    the database: SQLite has one writer, and while a write is open every participant who sends a page waits.
    Only then the survey is added, gets its ids, and what names things by id (flow, conditions, link rules,
    marks of the translations) is put on them.

    The file is checked value by value, and a value of a shape nobody thought of must still end as a refusal,
    not as a server error: the errors such a value causes (a list where a text is looked up, a row the
    database does not take twice) are logged and refused, after the same clearing up. What is not the file's
    fault (the disk, a busy database) stays an error."""
    from sqlalchemy.exc import IntegrityError

    from app import constructs, flow, panel

    if constructs.restricted_definition_keys(data):
        # Import creates a new study identity, so a grant for the exported study cannot authorize this use.
        raise Refused(constructs.permission_notice())

    notes = []
    entry = _dict(data.get("survey"))
    uid = secrets.token_urlsafe(9)
    try:
        new = Survey(uid=uid, status=Survey.DRAFT, created_by_id=user.id, **_settings(entry, title))
        from app import paper

        new.paper = paper.copied(entry.get("paper"))  # audit 2026-10-12, E-6
        if isinstance(data.get("live"), dict):
            new.live = {}  # a live vote from the start: no consent is added (_consent); its state follows below
        elif isinstance(data.get("interview"), dict):  # an interview guide: the same, its settings checked one by one
            from app import interview

            new.interview = interview.guide_state(Survey(interview=data["interview"]))
        elif isinstance(data.get("sampling"), dict):  # a diary study: only for whoever has the feature (experimental)
            from app import diary, experimental

            if experimental.enabled("diary", user=user):
                new.sampling = diary.clean(data["sampling"])
            else:
                notes.append(de_en("Die Datei enthält eine Tagebuchstudie; die experimentelle Funktion „Tagebuchstudie“ ist "
                                   "für Sie nicht freigeschaltet. Sie wurde als Fragebogen eingelesen, ohne Zeitplan.",
                                   "The file holds a diary survey; the experimental feature “Diary survey” is not switched on "
                                   "for you. It was read in as a questionnaire, without its schedule."))
        from app import kind_access

        if not kind_access.may_create(user, new.kind):  # "Anlegen" for the type (app/kind_access.py)
            raise Refused(de_en("Die Datei enthält eine Befragung der Art „{kind}“; eigene Befragungen dieser Art legt Ihr "
                                "Konto nicht an.", "The file holds a survey of the type “{kind}”; your account does not "
                                "create own surveys of this type.", kind=kind_access.label(new.kind)))
        files = _Files(data.get("files"), fetch, budget)
        new.members.append(SurveyMember(user=getattr(user, "_get_current_object", lambda: user)(), name=user.name,
                                        invited_by_id=user.id,
                                        affiliation=__import__("app.orgs", fromlist=["x"]).affiliation_of(user), position=0, role="admin"))
        from app import account_types

        account_types.add_supervisor(new, user)  # a supervisor becomes admin of what is read in (N10)
        new.panel, names = _panel(entry.get("panel"), notes)
        blocks, pairs, consent_row = _questionnaire(data, new, files, notes, user)
        if new.sampling is not None:  # blocks of a module the schedule does not have belong to the entry part
            __import__("app.diary", fromlist=["fix_parts"]).fix_parts(new)
        _constructs(new, notes, user)
        new_consent = _consent(new, consent_row or {}, user)
        _theme(data.get("theme"), new, user, files, notes)
        _proof_items(new)

        db.session.add(new)
        db.session.flush()  # the new ids
        block_ids, item_ids = {old: b.id for old, b in blocks.items()}, {old: i.id for old, i in pairs.items()}
        for item in (i for b in new.blocks for i in b.items):
            # feedback elements name the questions they show by id (items/sample_compare.py and its relatives)
            if "sources" in item.config:
                kept = [n for n, s in enumerate(item.config["sources"]) if _twin(item_ids, s)]
                subs = item.config.get("subscales") or []  # the subscale chosen per source stays with its source
                item.config = {**item.config, "sources": [item_ids[item.config["sources"][n]] for n in kept],
                               **({"subscales": [subs[n] if n < len(subs) else None for n in kept]} if subs else {})}
            config = item.kind.remap_config(item.config, lambda old: _twin(item_ids, old))
            # "Nur zeigen, wenn …" (app/item_condition.py): on the question of the new survey, or dropped with a note
            config, gone = item_condition.remap(config, lambda old: _twin(item_ids, old))
            if gone:
                notes.append(de_en("{code}: ohne „Nur zeigen, wenn …“, die Frage der Bedingung ist nicht in der Datei.",
                                   "{code}: without “Show only if …”, the question of the condition is not in the file.",
                                   code=item.code))
            if config != item.config:
                item.config = config
        quotas = _quotas(data, new, item_ids)
        db.session.flush()
        quota_ids = {old: q.id for old, q in quotas.items()}
        _boards(data, new, item_ids, quota_ids)
        if isinstance(data.get("live"), dict):
            new.live = _live(data["live"], item_ids)
        from app import experimental

        tree, nodes = _flow(data.get("flow"), block_ids, item_ids, quota_ids, names, notes,
                            web_service=experimental.enabled("webservice", user=user))
        _loop_plans(new, tree, notes)
        if new_consent:  # its block comes first, as in a new survey (flow.normalize would put it last)
            tree["children"].insert(0, {"t": "block", "block": new.blocks[0].id})
        new.flow = tree
        if new.panel:
            new.panel = {**new.panel, "rules": _rules(entry.get("panel"), new.panel, quota_ids, names, notes, item_ids)}
        flow.normalize(new)
        from app import quotas as quota_mod

        quota_mod.tick_conds(new)  # "ab divers" on answers without an order: ticks (UI audit 2026-10-12, U18)
        _marks(data, new, block_ids, item_ids, nodes, bool(_str(title, 300, line=True)))
        db.session.flush()
        _proof_survey(new)
        hosts = panel.link_hosts(new)
        if hosts:
            notes.append(de_en("Die Datei enthält Abschlusslinks, die Teilnehmende am Ende weiterleiten, zu: {hosts}. Bitte "
                               "unter „Datenerhebung → Teilnahmelink“ und im Ablauf prüfen.",
                               "The file holds completion links that redirect participants at the end to: {hosts}. Please "
                               "check under “Data collection → Participation link” and in the flow.", hosts=", ".join(hosts)))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        shutil.rmtree(upload_folder(uid), ignore_errors=True)
        if isinstance(e, Refused) or not isinstance(e, (TypeError, ValueError, LookupError, AttributeError, ArithmeticError,
                                                        RecursionError, IntegrityError)):
            raise
        current_app.logger.exception("survey file could not be read in")
        raise Refused(de_en("Die Datei lässt sich nicht einlesen: Sie enthält an einer Stelle einen Wert in einer Form, die "
                            "dort nicht vorgesehen ist.",
                            "The file cannot be read in: at one place it holds a value in a form not intended there.")) from e
    return new, notes
