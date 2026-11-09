"""Construct toolbox: validated instruments that are inserted as ordinary items.

Constructs live in the database (model `Construct`, classified by `Tag`s in facets) and are
found through a full-text index (SQLite FTS5; LIKE fallback on other databases). The seed
constructs come from the construct catalogue (app/construct_catalog.py, a snapshot of askurious-constructs in
app/data/constructs/manifest.json); they are written to the database at start-up and updated when their
`seed_version` (the major number of their catalogue version) increases.

Inserting a construct copies its item template into a regular Likert or single-choice item
whose config carries {"construct": {"key", "unlocked"}}. While locked, the structure (points,
statements, codes, reverse coding) cannot change; wording can still be corrected. Unlocking is
possible, but the item is then reported as "adapted" in codebook and README.

Seed wordings must be checked against the original sources before real fieldwork
(status "review" until the project owner has confirmed them).
"""
import copy
import functools
import json
import os
import re

from flask import current_app
from flask_login import current_user
from sqlalchemy import text

from app import construct_catalog as catalog
from app import ratswd
from app.extensions import db, get_or_create
from app.lang import de_en, english
from app.models import Construct, ConstructPick, Tag, construct_tag

# the facet "status" is the kind of a construct (validated scale or standard question), shown as "Typ"; whether its
# wording is checked against the source is a state of its own on the card (UI audit 2026-10-07, W88)
FACETS = {"status": "Typ", "bezug": "Bezug", "quelle": "Quelle", "art": "Art", "thema": "Thema", "fach": "Fachgebiet"}
FACETS_EN = {"status": "Type", "bezug": "Refers to", "quelle": "Source", "art": "Kind", "thema": "Topic", "fach": "Discipline"}


def facets():
    """The facets with their names in the language of the page (the keys as in FACETS)."""
    return FACETS_EN if english() else FACETS


# What the licence of each construct allows, from its "licence" in the active catalogue (askurious-constructs,
# schema/vocab/licences.yaml; withdrawn constructs included, old surveys still carry them). The names stay as before
# for all callers; the values follow a newer catalogue release as soon as the installation takes it (construct_sync).


def _licences(m):
    return {c["key"]: c["licence"] for c in m["constructs"]}


# No general distribution permission is documented (journal articles, publishers' copyright: gdms, kut, tam_*, kuk):
# public outputs use neutral IDs and sources instead, and each study needs a documented permission (permitted()).
PERMISSION_REQUIRED = catalog.live_set(lambda m: (k for k, lic in _licences(m).items() if lic.get("permission_required")))
NOT_SHARED = PERMISSION_REQUIRED
# Scales of the GESIS/ZIS collection: another form than the documented one (a translation) needs the consent of the
# authors; the translation page says so (rights review 2026-10-21)
ZIS = catalog.live_set(lambda m: (k for k, lic in _licences(m).items() if lic.get("wording") == "ZIS-noncommercial"))
# Wording the public Konstrukte page shows to visitors who are not signed in (Ralf 2026-10-16, option 1): only where
# the licence lets anyone pass it on (the RatSWD standard questions, ratswd.KEYS, and Askurious' own wordings, both
# CC BY 4.0 in the catalogue). Everything else (GESIS/ZIS, journal articles, APA PsycTests, the answer categories of
# the gender question after Spiel et al., 2019, licence "source-attributed"; constructs submitted by researchers) shows
# its source instead. PERMISSION_REQUIRED also applies to signed-in researchers.
OWN_WORDING = catalog.live_set(lambda m: (k for k, lic in _licences(m).items()
                                          if lic.get("wording") == "CC-BY-4.0" and k not in ratswd.KEYS))

def permission_notice():
    return de_en(
        "Dieser Wortlaut ist nicht allgemein freigegeben. Zum Anzeigen oder neuen Einfügen muss der Betreiber "
        "eine dokumentierte Berechtigung für dieses Instrument, Ihre Person, diese Studie und ihre Sprachen "
        "hinterlegen. Eine Anmeldung oder nichtkommerzielle Nutzung allein genügt nicht. Die Quelle bleibt zugänglich.",
        "This wording is not generally released. To view or newly insert it, the operator must record documented "
        "permission for this instrument, your account, this study and its languages. Signing in or non-commercial "
        "use alone is not sufficient. The source remains available.")


def permitted(key, survey=None, user=None, languages=None):
    """Study-specific private wording permission, never a public distribution licence.

    INSTRUMENT_WORDING_PERMISSIONS is a list (or JSON list) of {key, survey_uid, orcid, languages, evidence}.
    `evidence` names the operator's checked permission document, never sent to a client. Existing study editing
    and participation are deliberately not gated here: this guards catalogue access and creation of new uses.
    """
    if key not in PERMISSION_REQUIRED:
        return True
    user = current_user if user is None else user
    if survey is None or not getattr(user, "is_authenticated", False):
        return False
    from app import access

    if not access.own_role_of_user(user, survey):
        return False
    needed = wording_languages(key, languages if languages is not None else survey.languages)
    if not needed:
        return False
    grants = current_app.config.get("INSTRUMENT_WORDING_PERMISSIONS", [])
    if isinstance(grants, str):
        try:
            grants = json.loads(grants)
        except (TypeError, ValueError):
            return False
    if not isinstance(grants, list):
        return False
    for grant in grants:
        if not isinstance(grant, dict):
            continue
        allowed = grant.get("languages")
        if (grant.get("key") == key and grant.get("survey_uid") == survey.uid and grant.get("orcid") and grant.get("orcid") == user.orcid
                and isinstance(grant.get("evidence"), str) and grant["evidence"].strip()
                and isinstance(allowed, list) and all(isinstance(l, str) for l in allowed)
                and needed <= set(allowed)):
            return True
    return False


def wording_languages(key, languages):
    """The requested languages plus the source actually used for a template's fallback."""
    needed = set(languages)
    source = get(key)
    available = ((source.spec or {}).get("texts") or {}) if source is not None else {}
    if available:
        for lang in list(needed):
            if lang not in available:
                needed.add("de" if lang == "de-du" and "de" in available else
                           "en" if "en" in available else next(iter(available)))
    return needed


def copy_wording_sources(item, target):
    """Persistent original languages of each copied wording, including repeated automatic fallback copies."""
    meta = (item.config or {}).get("construct") or {}
    recorded = meta.get("wording_sources") or {}
    template_languages = list(((meta.get("template") or {}).get("texts") or {}).keys())
    texts = item.texts or {}

    def sources(lang):
        found = recorded.get(lang) if isinstance(recorded, dict) else None
        if isinstance(found, list) and found and all(isinstance(value, str) for value in found):
            return list(found)
        # Older stored items have no language provenance. Conservatively retain the template's source languages.
        return template_languages or [lang]

    out = {lang: sources(lang) for lang in target.languages if texts.get(lang)}
    source = item.block.survey.default_language
    if target.default_language not in out and texts.get(source):
        out[target.default_language] = sources(source)
    return out


def copy_languages(item, target):
    needed = set(target.languages)
    needed.update(lang for sources in copy_wording_sources(item, target).values() for lang in sources)
    return wording_languages(toolbox_key(item), needed)


def permitted_copy(item, target, user=None):
    if not wording_withheld(item):
        return True
    return permitted(toolbox_key(item), target, user, copy_languages(item, target))


def require_permission(key, survey=None, user=None, languages=None):
    if not permitted(key, survey, user, languages):
        raise ValueError(permission_notice())


def restricted_keys(survey):
    return {toolbox_key(i) for b in survey.blocks for i in b.items if wording_withheld(i)}


def require_public_material(survey):
    """Block public immutable full material when its protected wording cannot be redacted (e.g. guide ZIPs)."""
    _require_public_keys(restricted_keys(survey))


def restricted_definition_keys(data):
    """Known provenance in an exported/frozen questionnaire; do not consult the current survey/catalogue."""
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
        return set()
    found = set()
    for block in data["blocks"]:
        if not isinstance(block, dict) or not isinstance(block.get("items"), list):
            continue
        for item in block["items"]:
            config = item.get("config") if isinstance(item, dict) else None
            meta = config.get("construct") if isinstance(config, dict) else None
            key = meta.get("key") if isinstance(meta, dict) else None
            if isinstance(key, str) and key in NOT_SHARED:
                found.add(key)
    return found


def require_public_definition(data):
    _require_public_keys(restricted_definition_keys(data))


def _require_public_keys(keys):
    if keys:
        raise ValueError(de_en(
            "Diese Fassung enthält Instrumentwortlaut ohne allgemeine Veröffentlichungsfreigabe. Entfernen Sie "
            "die betroffenen Instrumente aus einer zur Veröffentlichung bestimmten Fassung: {keys}.",
            "This version contains instrument wording without a general publication licence. Remove these "
            "instruments from a version intended for publication: {keys}.", keys=", ".join(sorted(keys))))


def non_commercial(c):
    """Whether the terms of a construct allow only non-commercial research and teaching (GESIS/ZIS, APA PsycTests,
    CC BY-NC): the editor says so when it is inserted (copyright audit
    2026-10-16, U13)."""
    if getattr(c, "key", None) in PERMISSION_REQUIRED:
        return False  # no claim of a general non-commercial licence; permission is checked for the exact study
    notes = (getattr(c, "notes", None) or "").lower()
    return "nichtkommerziell" in notes or "non-commercial" in notes or "by-nc" in notes


# Scales of others (GESIS/ZIS, journal articles, APA PsycTests): the catalogue holds their structure (codes, points,
# response labels, subscales, reversed items) and a hash of their wording; the wording comes from the installation,
# never from the public code (rights review 2026-10-21): INSTRUMENT_WORDINGS, by default
# instance_overlay/instruments.json (instance_overlay/README.md). Without it, or with a wording whose hash differs
# from the documented one, these constructs are not seeded; constructs already in the database stay as they are.
PLACEHOLDER = "[System]"  # TAM: stands for the technology
INSTRUMENTS = catalog.live_map(lambda m: {c["key"]: c["structure"] for c in m["constructs"] if catalog.licensed(c)})
_WORDINGS = {}  # path: (modification time, wordings)
_WARNED = set()  # (path, modification time, key) of a differing wording already in the log


def instrument_wordings():
    """The installation's wordings of the scales of others: {key: {"question": {lang: text}, "statements": [{lang:
    text}, …]}} from instruments.json, and {key: {"_wordings": {lang: wording}}} from the folder
    INSTRUMENT_WORDINGS_DIR (wordings/<key>/<lang>.json, items by their codes), which wins for its constructs. {}
    without either."""
    return {**_file_wordings(), **_folder_wordings()}


_FOLDER = {}  # folder: (read at, wordings)
FOLDER_SECONDS = 30  # how long a process keeps the folder before it reads it again


def _folder_wordings():
    import time

    folder = current_app.config.get("INSTRUMENT_WORDINGS_DIR") or ""
    if not folder or not os.path.isdir(folder):
        return {}
    cached = _FOLDER.get(folder)
    if cached is not None and time.monotonic() - cached[0] < FOLDER_SECONDS:
        return cached[1]
    out = {}
    for key in sorted(os.listdir(folder)):
        sub = os.path.join(folder, key)
        if not os.path.isdir(sub) or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            continue
        found = {}
        for name in sorted(os.listdir(sub)):
            lang, ext = os.path.splitext(name)
            if ext != ".json":
                continue
            try:
                with open(os.path.join(sub, name), encoding="utf-8") as f:
                    wording = json.load(f)
            except (OSError, ValueError):
                current_app.logger.warning("instrument wording %s/%s unreadable; left out", key, name)
                continue
            if isinstance(wording, dict):
                found[lang] = wording
        if found:
            out[key] = {"_wordings": found}
    _FOLDER[folder] = (time.monotonic(), out)
    return out


def _file_wordings():
    path = current_app.config.get("INSTRUMENT_WORDINGS") or ""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _WORDINGS.get(path)
    if cached is None or cached[0] != mtime:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            wordings = data.get("instruments") if isinstance(data, dict) else None
        except (OSError, ValueError):
            wordings = None
        if not isinstance(wordings, dict):
            current_app.logger.warning("instrument wordings %s unreadable; their constructs are left out", path)
            wordings = {}
        cached = _WORDINGS[path] = (mtime, wordings)
    return cached[1]


def instrument_available(key):
    return key not in INSTRUMENTS or _instrument_spec(key) is not None


class _OnceLog:
    """The app's log, warning about one differing wording once per version of the installation's file."""

    def __init__(self, key):
        path = current_app.config.get("INSTRUMENT_WORDINGS") or ""
        self.mark = (path, _WORDINGS.get(path, (None,))[0], key)

    def warning(self, *args):
        if self.mark not in _WARNED:
            _WARNED.add(self.mark)
            current_app.logger.warning(*args)


def _instrument_spec(key):
    """The item template of a scale of others with the installation's wording; None without a fitting wording, or
    when its hash differs from the documented one (INSTRUMENT_WORDING_CHECK, on unless switched off)."""
    wordings = instrument_wordings()
    return catalog.spec(key, wordings, check=current_app.config.get("INSTRUMENT_WORDING_CHECK", True),
                        log=_OnceLog(key))


def _instrument(key):
    return functools.partial(_instrument_spec, key)


# Language versions that are only working translations of a scale of others (kut English, tam_* German; status
# "working" in the catalogue): the translation page (app/translations.py) treats them as translatable, not as
# validated versions.
def _working(m):
    out = {c["key"]: {lang for lang, info in c["languages"].items() if info.get("status") == "working"}
           for c in m["constructs"] if catalog.licensed(c)}
    return {k: v for k, v in out.items() if v}


WORKING_LANGS = catalog.live_map(_working)

# The seed constructs (the RatSWD standard questions among them, app/ratswd.py), from the catalogue
def _seeds(m):
    out = [catalog.seed(c) for c in catalog.entries(m)]
    for seed in out:
        if catalog.licensed(catalog.entry(seed["key"], m)):
            seed["spec"] = _instrument(seed["key"])
    return out


SEEDS = catalog.live_list(_seeds)

# What a construct measures, for meta-analyses (facet "Bezug"): a lasting trait of the person, something about the
# person that can change, or the rating of the object shown. In the catalogue as the tag "bezug:…".
REFERENCE = catalog.live_map(lambda m: {s["key"]: t.split(":", 1)[1] for s in SEEDS for t in s["tags"]
                                         if t.startswith("bezug:")})

# Name, short line, description, scoring and notes of each seed construct for researchers who read the pages in
# English. The item texts and answer scales stay as published (they are survey content, in spec["texts"]); the
# German fields stay the stored ones. A field shows in English only while the stored text is still the seed's German
# one (an operator who changed it in the database has written something of their own).
SEEDS_EN = catalog.live_map(lambda m: {c["key"]: catalog.seed_en(c) for c in catalog.entries(m)})

# Tag names in English, by the stored (German) name; a tag not listed here (one an operator made) keeps its name
TAGS_EN = {
    "Standardfrage": "standard question", "validiert": "validated scale",
    "Demografie": "Demographics", "Datenqualität": "Data quality", "Persönlichkeit": "Personality",
    "Einstellung": "Attitude", "Wahrnehmung": "Perception", "Verhaltensabsicht": "Behavioural intention",
    "Selbstwirksamkeit": "Self-efficacy", "Bewertung": "Evaluation", "Wissen": "Knowledge",
    "Alter": "Age", "Sorgfalt": "Care taken", "Technik": "Technology", "Kontrollüberzeugung": "Locus of control",
    "Vertrauen": "Trust", "Impulsivität": "Impulsivity", "Kognition": "Cognition",
    "Technikbereitschaft": "Technology commitment", "Risiko": "Risk", "Risikobereitschaft": "Risk proneness",
    "Umwelt": "Environment", "Umweltbewusstsein": "Environmental awareness", "Technikakzeptanz": "Technology acceptance",
    "Kochen": "Cooking", "Medien": "Media", "Medienbewertung": "Media evaluation",
    "Künstliche Intelligenz": "Artificial intelligence", "Entscheidungsstil": "Decision-making style",
    "Technikaffinität": "Affinity for technology",
    "Psychologie": "Psychology", "Soziologie": "Sociology", "Wirtschaftsinformatik": "Information systems",
    "Kommunikationswissenschaft": "Communication science",
    "Bildung": "Education", "Erwerbstätigkeit": "Employment", "Beruf": "Occupation", "Einkommen": "Income",
    "Staatsangehörigkeit": "Citizenship", "Haushalt": "Household", "Familienstand": "Marital status",
    "Person: stabil": "Person: stable", "Person: veränderlich": "Person: changeable", "Gegenstand": "Object rated",
}
SHOWN_FIELDS = ("name", "short", "description", "scoring", "notes")
_SEED_BY_KEY = catalog.live_map(lambda m: {seed["key"]: seed for seed in SEEDS})


def shown_text(c, field):
    """Field `field` (name, short, description, scoring, notes) of construct `c` in the language of the page: the
    English of a seed construct while the stored text is still the seed's German one, else the stored text."""
    value = getattr(c, field)
    if english():
        seed, en = _SEED_BY_KEY.get(c.key), SEEDS_EN.get(c.key, {})
        if seed is not None and field in en and value == seed[field]:
            return en[field]
    return value


# Tag names shown in German where the stored name says too little ("validiert" beside "Wortlaut noch nicht
# geprüft" read as a contradiction, W88); the stored name and its address stay
TAGS_DE = {"validiert": "validierte Skala"}


def tag_name(t):
    """The name of tag `t` in the language of the page."""
    return TAGS_EN.get(t.name, t.name) if english() else TAGS_DE.get(t.name, t.name)


class Shown:
    """A construct as the pages show it: name, short, description, scoring and notes in the language of the page
    (shown_text()); everything else is the construct's own."""

    def __init__(self, c):
        self._c = c

    def __getattr__(self, name):
        if name in SHOWN_FIELDS:
            return shown_text(self._c, name)
        return getattr(self._c, name)


def shown(c):
    """`c` for a page: as it is in German, with its texts in English on an English page (Shown)."""
    return Shown(c) if c is not None and english() else c


# ---- tags, seeding, index ----

def _slug(value):
    facet, name = value.split(":", 1)
    return f"{facet}:{re.sub(r'[^a-z0-9äöüß]+', '-', name.lower()).strip('-')}", facet, name


def _tag(value):
    slug, facet, name = _slug(value)
    tag = db.session.scalar(db.select(Tag).filter_by(slug=slug))
    if not tag:
        tag = Tag(slug=slug, facet=facet, name=name)
        db.session.add(tag)
    return tag


def _search_text(c):
    parts = [c.name, c.short, c.description, c.authors, str(c.year or ""), c.citation, c.doi]
    parts += [t.name for t in c.tags]
    for lt in (c.spec or {}).get("texts", {}).values():
        parts.append(lt.get("question", ""))
        for group in ("statements", "options", "pairs"):
            for v in (lt.get(group) or {}).values():
                parts += v.values() if isinstance(v, dict) else [v]
        parts += lt.get("labels", [])
    return " ".join(p for p in parts if p)


# Seeds that left the toolbox: {construct key: item type}. The item type stays available as an ordinary
# element; items built from the construct lose the link (no lock, no pooling) and keep everything else.
RETIRED = {"psm": "psm"}  # Van Westendorp: a method, not a scale; pooling prices across studies means nothing


def _retire():
    from app.models import Item

    changed = False
    for key, item_type in RETIRED.items():
        for item in db.session.scalars(db.select(Item).filter(Item.type == item_type)):
            if ((item.config or {}).get("construct") or {}).get("key") == key:
                item.config = {k: v for k, v in item.config.items() if k != "construct"}
                changed = True
        c = db.session.scalar(db.select(Construct).filter_by(key=key))
        if c:
            c.tags = []
            db.session.execute(db.delete(ConstructPick).where(ConstructPick.construct_id == c.id))
            db.session.delete(c)
            changed = True
    return changed


def ensure_seed():
    """Insert missing seed constructs and update outdated ones; then rebuild the search index."""
    from sqlalchemy.exc import IntegrityError

    try:
        changed = _seed()
    except IntegrityError:
        # Two server processes start at the same moment after a deploy with a new construct: both miss it, both
        # insert it, and the second one fails on the unique key. Its rows are there now, written by the first;
        # look again (as add_missing_columns does for a column the other process added).
        db.session.rollback()
        changed = _seed()
    rebuild_index(force=changed)
    report_wordings()


def wording_state():
    """{"missing": [keys], "differing": [(key, [langs])], "ok": [keys]} of the licensed constructs of the active
    catalogue against the installation's wordings (their SHA-256 against the documented hash, construct_catalog)."""
    wordings = instrument_wordings()
    out = {"missing": [], "differing": [], "ok": []}
    for c in catalog.entries():
        if not catalog.licensed(c):
            continue
        found = catalog.overlay_wordings(c, wordings)
        if not found:
            out["missing"].append(c["key"])
            continue
        langs = catalog.differing_languages(c, found)
        if langs:
            out["differing"].append((c["key"], langs))
        else:
            out["ok"].append(c["key"])
    return out


_REPORTED = set()


def report_wordings():
    """At the start: one line in the log about the installation's wordings of the scales of others (which are
    missing, which differ from the documented version and so stay out while INSTRUMENT_WORDING_CHECK is on)."""
    state = wording_state()
    mark = (catalog.release()[0], tuple(state["missing"]), tuple(k for k, _ in state["differing"]))
    if mark in _REPORTED:
        return state
    _REPORTED.add(mark)
    if state["differing"]:
        current_app.logger.warning(
            "construct catalogue %s: wording of the installation differs from the documented one for %s%s",
            mark[0], ", ".join(f"{k} ({'/'.join(langs)})" for k, langs in state["differing"]),
            "; left out of the toolbox" if current_app.config.get("INSTRUMENT_WORDING_CHECK", True) else
            " (INSTRUMENT_WORDING_CHECK off)")
    if state["missing"]:
        current_app.logger.info("construct catalogue %s: no wording in the installation for %s", mark[0],
                                ", ".join(state["missing"]))
    return state


def seed_name(key):
    """The name of a construct of the toolbox in the language of the page, as its page shows it: "Sorgfalt der
    Teilnahme (Selbstauskunft)" or "Care taken in participating (self-report)" (UI/UX audit 2026-10-07, W115)."""
    seed = next((s for s in SEEDS if s["key"] == key), None)
    if seed is None:
        return key
    return (SEEDS_EN.get(key) or {}).get("name", seed["name"]) if english() else seed["name"]


def _provenance(c, seed):
    """Mark a seed construct as one of the catalogue, with its version and the release of the snapshot (migration
    0117). True if something changed."""
    want = ("catalog", seed["version"], catalog.release()[0])
    if (c.source, c.version, c.catalog_release) == want:
        return False
    c.source, c.version, c.catalog_release = want
    return True


def _seed():
    changed = _retire()
    for seed in SEEDS:
        c = db.session.scalar(db.select(Construct).filter_by(key=seed["key"]))
        # a construct of the catalogue takes every new version of it (a corrected wording too, a minor number) and an
        # earlier one when the installation goes back (construct_sync.undo); others only a higher seed_version
        newer = c is not None and c.source == "catalog" and c.version not in (None, seed["version"])
        if c and c.seed_version >= seed["seed_version"] and not newer:
            changed = _provenance(c, seed) or changed
            if c.source == "catalog" and c.visibility == "retired":
                c.visibility = "public"  # back in the catalogue (going back to an earlier release)
                changed = True
            if {t.slug for t in c.tags} != {_slug(t)[0] for t in seed["tags"]}:
                c.tags = [_tag(t) for t in seed["tags"]]  # new tags in the code (e.g. the facet "Bezug")
                changed = True
            if c.seed_version == seed["seed_version"] and c.notes != seed["notes"]:
                c.notes = seed["notes"]  # terms of use: not a new revision of the instrument (rights review 2026-10-08)
                changed = True
            continue
        spec = seed["spec"]()
        if spec is None:
            continue  # a scale of others without the installation's wording (INSTRUMENTS)
        if not c:
            c = Construct(key=seed["key"])
            db.session.add(c)
        for field in ("name", "short", "description", "authors", "year", "citation", "doi", "scoring", "notes", "status",
                      "seed_version"):
            setattr(c, field, seed[field])
        c.spec = spec
        c.tags = [_tag(t) for t in seed["tags"]]
        _provenance(c, seed)
        if c.visibility == "retired":
            c.visibility = "public"  # back in the catalogue (going back to an earlier release)
        changed = True
    for gone in catalog.retired():
        # withdrawn from the catalogue: existing items keep it (find() still finds it), the toolbox no longer offers it
        c = db.session.scalar(db.select(Construct).filter_by(key=gone["key"]))
        if c is not None and c.source == "catalog" and c.visibility != "retired":
            c.visibility = "retired"
            changed = True
    if changed:
        db.session.flush()
        for c in db.session.scalars(db.select(Construct)):
            c.search_text = _search_text(c)
        for t in db.session.scalars(db.select(Tag).filter(~Tag.id.in_(db.select(construct_tag.c.tag_id)))):
            db.session.delete(t)  # tags no construct uses any more
        db.session.commit()
    return changed


def _fts_available():
    return db.engine.dialect.name == "sqlite"


def rebuild_index(force=False):
    """(Re)create the FTS5 table. Idempotent; silently falls back to LIKE search without FTS5."""
    if not _fts_available():
        return
    try:
        with db.engine.begin() as con:
            exists = con.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'construct_fts'")).first()
            if exists and not force:
                return
            con.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS construct_fts USING "
                             "fts5(key UNINDEXED, name, body, tokenize = 'unicode61 remove_diacritics 2')"))
            con.execute(text("DELETE FROM construct_fts"))
            for key, name, body in con.execute(text("SELECT key, name, search_text FROM construct")):
                con.execute(text("INSERT INTO construct_fts (key, name, body) VALUES (:k, :n, :b)"),
                            {"k": key, "n": name, "b": body})
    except Exception:  # sqlite without FTS5: search uses LIKE
        pass


def _fts_keys(q):
    words = re.findall(r"\w+", q.lower())
    if not words:
        return None
    match = " ".join(f'"{w}"*' for w in words)
    try:
        with db.engine.connect() as con:
            rows = con.execute(text("SELECT key FROM construct_fts WHERE construct_fts MATCH :m "
                                    "ORDER BY bm25(construct_fts, 0, 5.0, 1.0)"), {"m": match})
            return [r[0] for r in rows]
    except Exception:
        return None


def visible(user=None):
    """The constructs of the toolbox `user` (default: the person signed in) may find: those of the platform, and
    those of their organisations (app/org_constructs.py)."""
    stmt = db.select(Construct)
    orgs_seen = _org_ids(user)
    if orgs_seen:
        return stmt.filter(db.or_(Construct.visibility == "public",
                                  db.and_(Construct.visibility == "org", Construct.org_id.in_(orgs_seen))))
    return stmt.filter(Construct.visibility == "public")


def _org_ids(user=None):
    """The organisations whose constructs `user` sees (none for a guest or outside a request)."""
    from flask import has_request_context

    if user is None:
        if not has_request_context():
            return []
        user = current_user
    if not getattr(user, "is_authenticated", False):
        return []
    from app import orgs

    return sorted(orgs.ids_of(user))


# The toolbox is open to visitors (constructs_page.index): every chosen facet costs one more search with every other
# facet as a filter, so made-up facets gave a single request quadratic cost (audit 2026-10-22, D-03). Only tags that
# exist count, at most MAX_TAGS of them, and the query is cut at MAX_QUERY characters.
MAX_TAGS = 40
MAX_QUERY = 200


def clean_tags(slugs):
    """The tag slugs of `slugs` that exist, each once, in the order given: at most MAX_TAGS are looked at."""
    wanted = list(dict.fromkeys(s for s in slugs if isinstance(s, str) and s and len(s) <= 80))[:MAX_TAGS]
    if not wanted:
        return []
    known = set(db.session.scalars(db.select(Tag.slug).where(Tag.slug.in_(wanted))))
    return [s for s in wanted if s in known]


def search(q="", tags=(), limit=None):
    """Constructs matching the query (all words, prefix match) and tags (AND across facets,
    OR within a facet). Ordered by relevance with a query, else by name. Unknown tags are left out (clean_tags)."""
    stmt = visible()
    tags = clean_tags(tags)
    by_facet = {}
    for slug in tags:
        by_facet.setdefault(slug.split(":", 1)[0], []).append(slug)
    for slugs in by_facet.values():
        stmt = stmt.filter(Construct.tags.any(Tag.slug.in_(slugs)))
    base = stmt
    q = (q or "").strip()[:MAX_QUERY]
    order = None
    if q:
        keys = _fts_keys(q) if _fts_available() else None
        if keys is not None:
            stmt = stmt.filter(Construct.key.in_(keys))
            order = {k: n for n, k in enumerate(keys)}
        elif db.engine.dialect.name == "postgresql":
            # as FTS5 does: every word as the start of a word, without case and accents; names first
            from app.tables import key, text_key

            words = [text_key(w) for w in re.findall(r"\w+", q)]
            for w in words:
                stmt = stmt.filter(key(Construct.search_text).op("~")(r"\m" + re.escape(w)))
            in_name = db.and_(*[key(Construct.name).op("~")(r"\m" + re.escape(w)) for w in words])
            stmt = stmt.order_by(db.case((in_name, 0), else_=1))
        else:
            for w in re.findall(r"\w+", q):
                stmt = stmt.filter(Construct.search_text.ilike(f"%{w}%"))
    rows = list(db.session.scalars(stmt.order_by(Construct.name)))
    if order is not None:
        rows.sort(key=lambda c: order.get(c.key, len(order)))
    if q and english():  # the English texts of the seeds (not in the index, which stays as it is): after the hits
        more = [key for key in _english_hits(q) if key not in {c.key for c in rows}]
        if more:
            rows += list(db.session.scalars(base.filter(Construct.key.in_(more)).order_by(Construct.name)))
    return rows[:limit] if limit else rows


def _english_hits(q):
    """Keys of the seed constructs whose English name, short line, description or tags have every word of `q` as the
    start of a word."""
    words = [w.lower() for w in re.findall(r"\w+", q)]
    out = []
    for seed in SEEDS:
        en = SEEDS_EN.get(seed["key"], {})
        tags = [TAGS_EN.get(t.split(":", 1)[1], "") for t in seed["tags"]]
        found = set(re.findall(r"\w+", " ".join([en.get("name", ""), en.get("short", ""), en.get("description", "")] + tags).lower()))
        if words and all(any(f.startswith(w) for f in found) for w in words):
            out.append(seed["key"])
    return out


def facet_counts(rows, q="", tags=()):
    """{facet: [(tag, count)]}, most frequent first. A tag counts the hits its chip would give: the query and the
    chosen tags of the other facets, not those of its own facet (OR within a facet), else every chip of a facet that
    has a choice besides an empty result would read 0. `rows` are the hits for `q` and `tags`."""
    def tally(found, facet=None):
        counts = {}
        for c in found:
            for t in c.tags:
                if facet is None or t.facet == facet:
                    counts[t] = counts.get(t, 0) + 1
        return counts

    tags = clean_tags(tags)  # at most one more search per facet that exists (D-03)
    chosen = {slug.split(":", 1)[0] for slug in tags}
    counts = {t: n for t, n in tally(rows).items() if t.facet not in chosen}
    for facet in chosen:  # the hits without this facet's own choice
        counts.update(tally(search(q, [s for s in tags if s.split(":", 1)[0] != facet]), facet))
    out = {f: [] for f in FACETS}
    for t, n in sorted(counts.items(), key=lambda x: (-x[1], x[0].name)):
        out.setdefault(t.facet, []).append((t, n))
    return out


def all_tags():
    out = {f: [] for f in FACETS}
    for t in db.session.scalars(db.select(Tag).order_by(Tag.name)):
        out.setdefault(t.facet, []).append(t)
    return out


def get(key):
    return db.session.scalar(visible().filter(Construct.key == key))


def wording_public(key):
    """May the wording of construct `key` be shown to someone who is not signed in (OWN_WORDING)?"""
    return key in OWN_WORDING or key in ratswd.KEYS


# ---- favourites and the list in the questionnaire editor ----

SIDEBAR_CONSTRUCTS = 6  # listed in the editor without a search; the full list is on the Konstrukte page
SIDEBAR_STARS = 5       # from this many favourites on, the editor lists the favourites only


def _pick(user, c, create=False):
    if create:  # two requests at the same moment make one row (audit 2026-10-10, C4)
        return get_or_create(ConstructPick, user_id=user.id, construct_id=c.id)
    return db.session.scalar(db.select(ConstructPick).filter_by(user_id=user.id, construct_id=c.id))


def starred(user):
    """Keys of the constructs this researcher starred."""
    return set(db.session.scalars(db.select(Construct.key).join(ConstructPick, ConstructPick.construct_id == Construct.id)
                                  .filter(ConstructPick.user_id == user.id, ConstructPick.starred)))


def toggle_star(user, c):
    row = _pick(user, c, create=True)
    row.starred = not row.starred
    return row.starred


def counts(user, rows):
    """How often each construct occurs in the surveys this researcher can open: their own and those shared
    with their groups, archived ones included (as editor.type_counts). Questions in the trash are not counted."""
    from app import access
    from app.models import Block, Item

    configs = db.session.scalars(
        db.select(Item.config).join(Block, Item.block_id == Block.id)
        .filter(Block.survey_id.in_(access.survey_ids(user)), Item.type.in_({c.spec["type"] for c in rows})))
    out = {}
    for config in configs:
        key = ((config or {}).get("construct") or {}).get("key")
        if key:
            out[key] = out.get(key, 0) + 1
    return out


def sidebar(user, org_id=None):
    """The constructs the editor lists without a search, its "Schnellbox" (app/shortlist.py): the researcher's
    favourites by name, then the list recommended in organisation `org_id` (its own, else the platform's) in its
    order, each once. Only while no list is kept anywhere and there are fewer than SIDEBAR_STARS favourites, the list
    is filled up to SIDEBAR_CONSTRUCTS with the constructs most used in their own surveys and those of their groups
    (counts) as before; equally often and never used ones by name. Each as the page shows it (shown())."""
    from app import shortlist

    rows = search()
    stars = starred(user)
    out = [c for c in rows if c.key in stars]
    recommended, _label = shortlist.for_org(org_id)
    if recommended:
        by_key = {c.key: c for c in rows}
        return [shown(c) for c in out + [by_key[k] for k in recommended if k in by_key and k not in stars]]
    if len(out) >= SIDEBAR_STARS:
        return [shown(c) for c in out]
    used = counts(user, rows)
    # the RatSWD standard questions, one construct per item, fill the list only once used (else the editor would list
    # "Deutsche Staatsangehörigkeit", "Erwerbssituation" … by name before every scale; app/ratswd.py)
    rest = sorted((c for c in rows if c.key not in stars and (c.key not in ratswd.NOT_POOLED or used.get(c.key))),
                  key=lambda c: -used.get(c.key, 0))  # stable: ties by name
    return [shown(c) for c in out + rest[:SIDEBAR_CONSTRUCTS - len(out)]]


def palette_rows(rows, user, org_id=None):
    """Constructs as the editor palette needs them (JSON). "by": who recommends it in the survey's organisation
    (app/shortlist.py), shown after its name; empty for the others."""
    from flask import url_for

    from app import shortlist

    stars = starred(user)
    recommended, label = shortlist.for_org(org_id)
    return [{"key": c.key, "name": shown_text(c, "name"), "short": shown_text(c, "short"), "source": c.author_year, "starred": c.key in stars,
             "by": label if c.key in recommended else "",
             "url": url_for("constructs.detail", key=c.key), "star_url": url_for("constructs.star", key=c.key)}
            for c in rows]


# ---- items ----

def is_linked_key(key):
    """Keys of linked elements (app/linked.py) start with "v-"; they share the table with the toolbox."""
    return isinstance(key, str) and key.startswith("v-")


def toolbox_key(item):
    """The key of the toolbox construct `item` was built from, or None (also for a linked element)."""
    key = (((item.config or {}).get("construct")) or {}).get("key")
    return key if isinstance(key, str) and not is_linked_key(key) else None


def wording_withheld(item):
    """Whether public outputs must replace this item's wording with identifiers.

    The provenance on the item is decisive, including unlocked or translated uses and uses whose catalogue
    record was removed. Access to the questionnaire for participants and survey members is unchanged.
    """
    return toolbox_key(item) in NOT_SHARED


def prefetch(survey):
    """Load the constructs of all elements of a survey with their tags in two queries and keep them for this request,
    so info() and find() need no query per element: the exports and the codebook ask for every element, some twice
    (performance audit 2026-10-09b, P12). Outside a request it does nothing."""
    from flask import g, has_request_context
    from sqlalchemy.orm import selectinload

    if not has_request_context():
        return
    keys = {k for b in survey.blocks for i in b.items
            if isinstance(k := ((i.config or {}).get("construct") or {}).get("key"), str)}
    kept = g.setdefault("_constructs_by_key", {})
    keys -= kept.keys()
    if keys:
        found = {c.key: c for c in db.session.scalars(db.select(Construct).where(Construct.key.in_(keys))
                                                      .options(selectinload(Construct.tags)))}
        kept.update({k: found.get(k) for k in keys})


def find(key):
    """The construct with this key or None, from prefetch() when it ran in this request."""
    from flask import g, has_request_context

    kept = g.get("_constructs_by_key") if has_request_context() else None
    if kept is not None and key in kept:
        return kept[key]
    return db.session.scalar(db.select(Construct).filter_by(key=key))


def info(item):
    """The construct or linked element an item was created from (metadata + lock state), or None."""
    meta = (item.config or {}).get("construct")
    if not meta:
        return None
    c = find(meta.get("key"))
    if not c:
        return None
    return {"key": c.key, "label": c.name, "citation": c.citation + (f" https://doi.org/{c.doi}" if c.doi else ""),
            "scoring": c.scoring, "notes": c.notes, "unlocked": meta.get("unlocked", False),
            "modifications": modifications(item, c, de=True), "linked": is_linked_key(c.key), "version": meta.get("version"),
            "zis": is_zis(c)}


def zis_changed():
    """The note for an unlocked or changed ZIS scale (rights review 2026-10-08)."""
    return de_en("ZIS-Skala (GESIS): Für eine andere als die dokumentierte Form verlangen die Nutzungsbedingungen das "
                 "Einverständnis der Autorinnen und Autoren. Holen Sie es ein, bevor die Befragung startet, oder kehren "
                 "Sie mit „Rückgängig“ zur dokumentierten Form zurück.",
                 "ZIS scale (GESIS): for a form other than the documented one, the terms of use require the authors' "
                 "permission. Obtain it before the survey starts, or go back to the documented form with “Undo”.")


def is_zis(c):
    """A scale from GESIS' ZIS: its terms allow only the documented form without the authors' permission."""
    return any(t.slug == "quelle:gesis-zis" for t in c.tags)


def badge(item):
    """For the questionnaire overview: {"label", "title", "modified", "linked"} or None."""
    meta = (item.config or {}).get("construct")
    if not meta:
        return None
    c = db.session.scalar(db.select(Construct).filter_by(key=meta.get("key")))
    if not c:
        return None
    mods = modifications(item, c, de=True)
    if is_linked_key(c.key):
        version = meta.get("version") or c.seed_version
        title = (de_en("Verknüpftes Element „{name}“, Fassung {v}", "Linked element “{name}”, revision {v}", name=c.name, v=version)
                 + (de_en(". Angepasst", ". Adapted") + (f": {'; '.join(mods)}" if mods else "")
                    if meta.get("unlocked") or mods else ""))
        return {"label": de_en("Verknüpft · Fassung {v}", "Linked · revision {v}", v=version), "title": title,
                "modified": bool(mods) or bool(meta.get("unlocked")), "linked": True}
    status = next((tag_name(t) for t in c.tags if t.facet == "status"), de_en("Konstrukt", "Construct"))
    title = f"{shown_text(c, 'name')}: {c.author_year}" + (de_en(". Verändert: ", ". Modified: ") + "; ".join(mods) if mods else "")
    return {"label": status, "title": title, "modified": bool(mods), "linked": False}


def locked(item):
    """True if the structure of `item` is locked: a toolbox construct not unlocked, or a linked element that is
    frozen (app/linked.py) and not adapted in this survey."""
    meta = (item.config or {}).get("construct")
    if not meta or meta.get("unlocked"):
        return False
    if is_linked_key(meta.get("key")):
        from app import linked

        c = linked.get(meta["key"])
        return c is not None and linked.frozen(c, version=meta.get("version") or 1) is not None
    return True


def build(key, langs, c=None, *, survey=None, user=None):
    """(type, code base, config, texts) for a new item of construct `key` (or of the construct or linked element
    `c`). Languages without a version of the construct get the English (else first available) wording to translate."""
    require_permission(key, survey, user, langs)
    c = c or get(key)
    if not c:
        raise KeyError(key)
    require_permission(c.key, survey, user, langs)
    spec = copy.deepcopy(c.spec)
    if c.key in PERMISSION_REQUIRED:
        # A permission for EN must not quietly include the complete DE wording in the stored template snapshot.
        source_languages = {lang if lang in spec["texts"] else
                            "de" if lang == "de-du" and "de" in spec["texts"] else
                            "en" if "en" in spec["texts"] else next(iter(spec["texts"])) for lang in langs}
        spec["texts"] = {lang: words for lang, words in spec["texts"].items() if lang in source_languages}
    # the template snapshot lets exports show exactly what was modified, even after the
    # construct itself gets a new version
    config = {**spec["config"], "construct": {"key": c.key, "version": c.seed_version, "unlocked": False,
                                              "template": copy.deepcopy(spec)}}
    if c.key in PERMISSION_REQUIRED:
        config["construct"]["wording_sources"] = {
            lang: [lang if lang in spec["texts"] else
                   "de" if lang == "de-du" and "de" in spec["texts"] else
                   "en" if "en" in spec["texts"] else next(iter(spec["texts"]))] for lang in langs}
    if is_linked_key(c.key):  # from when on the consent names the shared analysis (app/linked_pool.py)
        from app.models import utcnow

        config["construct"]["since"] = utcnow().isoformat()
    available = spec["texts"]
    fallback = available.get("en") or next(iter(available.values()))
    # German with "du": the validated German wording to start from (a version with "du" is not validated itself)
    # a language the element has, also without texts of its own ({}: only standard wordings, app/item_texts.py), stays so
    texts = {lang: copy.deepcopy(available[lang] if isinstance(available.get(lang), dict) else available.get(lang) or (available.get("de") if lang == "de-du" else None) or fallback)
             for lang in langs}
    return spec["type"], spec["code"], config, texts


# ---- pooled data across surveys (meta-analyses, licence.md section 4) ----

def pooled(key):
    """Do the answers to construct `key` go into the pooled analysis? Not those of the RatSWD standard questions
    (app/ratswd.py): the pool is meant for scales, and demographics would make the consent's paragraph needlessly
    delicate (concept Demografie-Standards 2026-10-15, decision 7a). Age and gender stay in it as before."""
    from app import org_constructs

    # constructs of an organisation stay out like linked elements (terms of use section 4 names the toolbox only)
    return key not in ratswd.NOT_POOLED and not org_constructs.is_org_key(key)


def _pseudonym(prefix, value, length):
    import hashlib
    import hmac

    from flask import current_app

    # its own lasting key (config POOL_KEY), so a new SECRET_KEY after an incident keeps the pseudonyms and the key
    # that signs sessions and deletion codes does not undo them; without it SECRET_KEY, as before (E-8, 2026-10-05)
    key = (current_app.config.get("POOL_KEY") or current_app.config["SECRET_KEY"]).encode()
    return prefix + hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:length]


def _study_ref(survey_id):
    """Stable pseudonym of a survey in pooled data (no titles, no ids)."""
    return _pseudonym("S", f"survey:{survey_id}", 8)


def _participant_ref(response_id):
    """Stable pseudonym of one participation: the same in the pooled files of all constructs, so
    constructs (e.g. BFI-10 and gender) can be joined on study_id + participant_id."""
    return _pseudonym("P", f"response:{response_id}", 12)


def _norm(value):
    return " ".join(str(value or "").split())


def _same_text(mine, template):
    """Equal after whitespace normalisation; a placeholder like "[System]" in the template matches any name."""
    mine, template = _norm(mine), _norm(template)
    if PLACEHOLDER not in template:
        return mine == template
    pattern = ".+?".join(re.escape(part) for part in template.split(PLACEHOLDER))
    return re.fullmatch(pattern, mine) is not None


def modifications(item, construct, de=False):
    """Human-readable differences between an item and the construct it was built from
    (empty if unchanged). Wording is compared in every language the construct provides.
    In English for the exports (codebook, constructs.md, pooled data); `de`: for the pages, in the language of the
    page (German unless the person reads them in English, app/lang.py)."""
    def t(en, german):
        return german if de and not english() else en

    if is_linked_key(construct.key):
        from app import linked

        template = linked.template_of(item, construct)
    else:
        template = ((item.config or {}).get("construct") or {}).get("template") or construct.spec
    spec_cfg, spec_texts = template["config"], template["texts"]
    cfg, out = item.config or {}, []
    if item.type == "likert":
        if cfg.get("points") != spec_cfg.get("points"):
            out.append(t(f"scale {spec_cfg.get('points')} → {cfg.get('points')} points",
                         f"Skala {spec_cfg.get('points')} → {cfg.get('points')} Stufen"))
        canon = {st["id"]: st for st in spec_cfg.get("statements", [])}
        mine = {st["id"]: st for st in cfg.get("statements", [])}
        removed = [st["code"] for sid, st in canon.items() if sid not in mine]
        added = [st.get("code") or st["id"] for sid, st in mine.items() if sid not in canon]
        if removed:
            out.append(t("removed ", "entfernt: ") + ", ".join(removed))
        if added:
            out.append(t("added ", "hinzugefügt: ") + ", ".join(added))
        flipped = [canon[sid]["code"] for sid, st in mine.items() if sid in canon and bool(st.get("reverse")) != bool(canon[sid].get("reverse"))]
        if flipped:
            out.append(t("reverse coding changed ", "Umpolung geändert: ") + ", ".join(flipped))
        partly = [st.get("code") or sid for sid, st in mine.items() if st.get("when")]
        if partly:  # shown to some people only (a condition on an earlier answer, app/rows.py)
            out.append(t("shown conditionally ", "nur unter einer Bedingung gezeigt: ") + ", ".join(partly))
        order_mine = [sid for sid in mine if sid in canon]
        order_canon = [sid for sid in canon if sid in mine]
        if order_mine != order_canon:
            out.append(t("order changed", "Reihenfolge geändert"))
    elif item.type == "single_choice":
        mine = {o["id"]: o for o in cfg.get("options", [])}
        # an option the construct allows to leave out (self-description of gender) is no change when it is left out
        canon = {o["id"]: o for o in spec_cfg.get("options", []) if o["id"] in mine or not o.get("optional")}
        if set(canon) != set(mine) or any(mine[i].get("code") != canon[i].get("code") for i in mine if i in canon):
            out.append(t("answer options changed", "Antwortoptionen geändert"))
    elif is_linked_key(((item.config or {}).get("construct") or {}).get("key")):
        # a linked element of another type (app/linked.py): any setting of the element itself counts
        from app import linked

        def own(config):
            return {k: v for k, v in (config or {}).items() if k not in linked.LOCAL_KEYS}

        if own(cfg) != own(spec_cfg):
            out.append(t("settings changed", "Einstellungen geändert"))
    from app.items.likert import shown_labels

    def labels_of(texts, config):  # as shown: with ends only, a step in between left empty on saving is no change
        return [_norm(x) for x in shown_labels(texts.get("labels", []), config.get("labeling"))]

    for lang, lt_spec in spec_texts.items():
        lt = (item.texts or {}).get(lang)
        if not lt:
            continue
        changed = []
        if not _same_text(lt.get("question"), lt_spec.get("question")):
            changed.append(t("instruction", "Fragetext"))
        for group, label in (("statements", t("item", "Aussage")), ("options", "Option")):
            for key, text_spec in (lt_spec.get(group) or {}).items():
                if key in (lt.get(group) or {}) and not _same_text(lt[group][key], text_spec):
                    code = next((x["code"] for x in spec_cfg.get(group, []) if x["id"] == key), key)
                    changed.append(f"{label} {code}")
        if "labels" in lt_spec and labels_of(lt, cfg) != labels_of(lt_spec, spec_cfg):
            changed.append(t("scale labels", "Skalenbeschriftung"))
        if changed:
            out.append(t(f"wording ({lang}): ", f"Wortlaut ({lang.upper()}): ") + ", ".join(changed))
    return out


def _canonical_values(item, construct, answer):
    """({canonical variable: value}, {added variable: value}) for one item's answer; reverse
    coding as set in the item is applied."""
    spec_cfg = construct.spec["config"]
    if item.type == "likert":
        canon = {s["id"]: s["code"] for s in spec_cfg.get("statements", [])}
        points, out, extra = item.config["points"], {}, {}
        for n, st in enumerate(item.config.get("statements", [])):
            v = (answer or {}).get(st["id"])
            v = points + 1 - v if v is not None and st.get("reverse") else v
            if st["id"] in canon:
                out[canon[st["id"]]] = v
            else:  # statement added after unlocking
                extra[st.get("code") or f"{item.code}_{n + 1}"] = v
        return out, extra
    if item.type == "single_choice":
        value = next(iter(item.kind.export_values(item, answer).values()), None)
        return {construct.spec["code"]: value}, {}
    if item.type == "text_input":  # e.g. age: the value as entered (validated as a number)
        return {construct.spec["code"]: answer if answer not in ("", None) else None}, {}
    return {}, {}


def usage(key):
    """Items built from construct `key`, as (item, survey). Block and survey are read with the items, not one by one
    (code review 2026-10-07, I14)."""
    from sqlalchemy.orm import selectinload

    from app.models import Block, Item

    c = get(key)
    if not c:
        return []
    items = db.session.scalars(db.select(Item).filter(Item.type == c.spec["type"])
                               .options(selectinload(Item.block).selectinload(Block.survey)))
    return [(i, i.block.survey) for i in items if ((i.config or {}).get("construct") or {}).get("key") == key]


def pool(key):
    """Pooled rows for construct `key`: one row per completed real response and item (`item` tells
    two copies in one survey apart), from closed surveys only. study_id and participant_id are
    pseudonyms that match across the pooled files of all constructs. Demographic constructs of
    the same response are added as moderators (age and gender; not the RatSWD standard questions, pooled()). A
    construct that is not pooled has no rows. Left out, as on the results page of each survey: what its quality
    control marks, and everyone who said in the self-report that their answers should not be used (licence.md
    section 4)."""
    from app.models import Response, Survey

    c = get(key)
    uses = [(i, s) for i, s in usage(key) if s.status == Survey.CLOSED] if pooled(key) else []
    variables = [st["code"] for st in c.spec["config"].get("statements", [])] or [c.spec["code"]]
    demo = [d for d in search(tags=["art:demografie"]) if d.key != key and pooled(d.key)]
    header = (["study_id", "participant_id", "item", "language", "month", "construct_version", "adapted", "modifications", "points"] + variables
              + ["added_items"] + [d.spec["code"] for d in demo])
    rows = []
    import json

    left_out, fetched = {}, {}
    for item, survey in uses:
        if survey.id not in left_out:
            left_out[survey.id] = _left_out_of_pool(survey)
            # the four fields the rows need, of the real completed participations only (not every participation
            # with all its fields)
            fetched[survey.id] = db.session.execute(db.select(
                Response.id, Response.answers, Response.language, Response.completed_at).filter(
                Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False),
                Response.status == Response.COMPLETED).order_by(Response.id)).all()
        meta = item.config["construct"]
        mods = modifications(item, c)
        demo_items = {d.key: [i for b in survey.blocks for i in b.items
                              if ((i.config or {}).get("construct") or {}).get("key") == d.key] for d in demo}
        for r in fetched[survey.id]:
            if r.id in left_out[survey.id]:
                continue
            values, extra = _canonical_values(item, c, r.answers.get(str(item.id)))
            row = [_study_ref(survey.id), _participant_ref(r.id), item.code, r.language, r.completed_at.strftime("%Y-%m") if r.completed_at else "",
                   meta.get("version", ""), int(bool(mods) or bool(meta.get("unlocked"))), "; ".join(mods),
                   item.config.get("points", "")]
            row += [values.get(v) for v in variables]
            row.append(json.dumps(extra, ensure_ascii=False) if extra else "")
            for d in demo:
                found = [_canonical_values(i, d, r.answers.get(str(i.id)))[0] for i in demo_items[d.key]]
                row.append(next((f.get(d.spec["code"]) for f in found if f.get(d.spec["code"]) is not None), None))
            rows.append(row)
    return header, rows


def _left_out_of_pool(survey):
    """Ids of the real participations of `survey` that stay out of the pooled data: marked by its quality assurance
    (speeders and the survey's marking rule, as qc_marked) and those whose self-report says "do not use my
    answers", whether or not the survey's marking rule counts the self-report."""
    from app import export, quality
    from app.models import Response

    ctx = quality.context(survey, export._items(survey))
    out = set(export.marked_ids(survey))
    if ctx["self"]:
        out |= {r.id for r in survey.responses
                if r.source == "real" and r.status == Response.COMPLETED and quality.self_report_no(r, ctx)}
    return out


def usage_stats(key, uses=None):
    """uses: usage(key), if read already."""
    from app.models import Survey

    uses = usage(key) if uses is None else uses
    surveys = {s.id: s for _, s in uses}
    return {"surveys": len(surveys), "closed": sum(1 for s in surveys.values() if s.status == Survey.CLOSED)}
