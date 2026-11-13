"""Constructs of an organisation (concept konzept-konstrukt-repo.html, step 5; E12 to E16).

Under Organisation > Konstrukte the organisation's admins make constructs of their own from a linked element
(app/linked.py, the workbench): the dialog "Konstrukt anlegen" asks what the toolbox needs (description, source with
DOI, scoring, the licence of the wording from the catalogue's list, the confirmation that the wording may be used and
shared in the organisation). Members propose one of their linked elements to the organisation instead (a submission
with org_id, app/submissions.py); an admin of the organisation takes it over or rejects it.

Such a construct is a row of Construct with visibility "org" and the organisation's org_id: only its members find it in
the toolbox and the editor, with the organisation's name at the entry (constructs.visible). Its key has the prefix
o<org id>- (o12-vertrauen_roboter), so nothing collides with the catalogue; its global id is
local:<host>/o<org id>/<name>, which a catalogue entry names under "aliases" once it is taken into the catalogue.
Constructs of an organisation never go into the operator's pool (constructs.pooled; terms of use section 4 names the
toolbox only), like linked elements.

To offer it to every installation, the dialog and the list name the contact (CONSTRUCTS_CONTACT) and give "Als Datei
für Pull Request": a ZIP with constructs/<name>/construct.yaml (and the wording files where the licence lets it be
passed on) for a pull request to askurious-constructs. construct.yaml is written as JSON, which is valid YAML: the
code needs no YAML library; the maintainers may reformat it. Accepted submissions to the operator get the same file.
"""
import copy
import io
import json
import re
import zipfile

from flask import current_app

from app import construct_catalog as catalog
from app.extensions import db
from app.lang import de_en, english
from app.models import Construct, ConstructSubmission, utcnow

VISIBILITY = "org"
HIDDEN = "org_hidden"  # taken out of the toolbox by an admin of the organisation; surveys that use it keep it
SOURCE = "org"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,29}\Z")
PREFIX_RE = re.compile(r"^o\d+-")
CONTACT = "Ralf Philipsen (philipsen@comm.rwth-aachen.de), Philipp Brauner (brauner@comm.rwth-aachen.de)"
REPOSITORY = "https://github.com/askurious/askurious-constructs"
# the licence keys of the catalogue (askurious-constructs schema/vocab/licences.yaml): key, German, English, the
# wording may be passed on (and so goes into the file for a pull request)
LICENCES = (
    ("CC-BY-4.0", "CC BY 4.0", "CC BY 4.0", True),
    ("CC0-1.0", "CC0 1.0", "CC0 1.0", True),
    ("source-attributed", "Nach einer Veröffentlichung, mit Quellenangabe", "After a publication, with attribution", True),
    ("ZIS-noncommercial", "GESIS/ZIS, nichtkommerziell", "GESIS/ZIS, non-commercial", False),
    ("article-no-licence", "Zeitschriftenartikel, keine Freigabe dokumentiert", "Journal article, no permission documented", False),
    ("publisher-copyright", "Urheberrecht des Verlags", "Publisher's copyright", False),
    ("authors-free-use", "Von den Autoren frei nutzbar, keine Weitergabelizenz", "Free to use per the authors, no licence to pass on", False),
)
FIELDS = (("description", 3000), ("citation", 2000), ("doi", 200), ("scoring", 1000), ("licence", 40), ("name", 200),
          ("name_en", 200), ("description_en", 3000), ("scoring_en", 1000))
ENGLISH = ("name", "description", "scoring")  # optional English of these, for the file for a pull request


def is_org_key(key):
    return isinstance(key, str) and bool(PREFIX_RE.match(key))


def contact():
    return (current_app.config.get("CONSTRUCTS_CONTACT") or "").strip() or CONTACT


def licences():
    """[(key, label)] in the language of the page."""
    return [(key, en if english() else de) for key, de, en, _open in LICENCES]


def licence_label(key):
    return next((en if english() else de for k, de, en, _o in LICENCES if k == key), key or "")


def distributable(key):
    return next((o for k, _d, _e, o in LICENCES if k == key), False)


def home_org_id(user):
    """The organisation a member proposes to: their home ("main" by its own id, never None, which is the operator)."""
    from app import orgs

    return orgs.home_id(user) or orgs.default_id()


def of_org(org_id, hidden=False):
    return list(db.session.scalars(db.select(Construct).filter_by(visibility=HIDDEN if hidden else VISIBILITY,
                                                                  org_id=org_id).order_by(Construct.name)))


def get(org_id, key):
    """A construct of organisation `org_id` (shown or hidden), or None."""
    return db.session.scalar(db.select(Construct).filter(Construct.key == key, Construct.org_id == org_id,
                                                         Construct.visibility.in_((VISIBILITY, HIDDEN))))


def uses(key):
    """How many surveys have an element of construct `key`."""
    from app import construct_sync

    return construct_sync.uses(key)


def english_of(c):
    """{field: English text} given for construct `c` (name, description, scoring)."""
    return dict((c.spec or {}).get("en") or {})


def _terms(org, element_key, licence):
    return de_en("Lizenz des Wortlauts: {l}.", "Licence of the wording: {l}.", l=licence_label(licence)) + " " + de_en(
        "Konstrukt der Organisation {o}, aus dem verknüpften Element {k}.",
        "Construct of the organisation {o}, from the linked element {k}.", o=org.name, k=element_key)


def update(c, org, f):
    """Change name, description, source, scoring, licence and the English of a construct of the organisation (its
    structure and wording stay those of the element it was made from). The caller commits."""
    from app import constructs

    c.name = (f.get("name") or c.name)[:200]
    c.description, c.citation, c.doi, c.scoring = f["description"], f["citation"], f.get("doi", ""), f["scoring"]
    element = ((c.notes or "").rsplit(" ", 1)[-1] or "").rstrip(".")
    c.notes = _terms(org, element, f["licence"])
    c.spec = {**(c.spec or {}), "licence": f["licence"],
              "en": {k: f.get(f"{k}_en", "") for k in ENGLISH if f.get(f"{k}_en")}}
    c.search_text = constructs._search_text(c)


def hide(c):
    """Out of the toolbox: the organisation's members no longer find it; surveys that use it keep their copy."""
    c.visibility = HIDDEN


def show(c):
    c.visibility = VISIBILITY


def proposals(org_id):
    """The open proposals of members for organisation `org_id`."""
    return list(db.session.scalars(db.select(ConstructSubmission).filter(
        ConstructSubmission.org_id == org_id, ConstructSubmission.state.in_(ConstructSubmission.OPEN))
        .order_by(ConstructSubmission.created_at)))


def read(form, need_name=True):
    """(fields, errors) of the dialog "Konstrukt anlegen" or of taking over a proposal."""
    from app import submissions

    f = {k: (form.get(k) or "").strip()[:limit] for k, limit in FIELDS}
    errors = []
    if need_name and not f["name"]:
        errors.append(de_en("Name", "Name"))
    for key, de, en in (("description", "Beschreibung", "Description"), ("citation", "Quelle", "Source"),
                        ("scoring", "Auswertung", "Scoring")):
        if not f[key]:
            errors.append(de_en(de, en))
    if f["licence"] not in {k for k, *_ in LICENCES}:
        errors.append(de_en("Lizenz des Wortlauts", "Licence of the wording"))
    if f["doi"]:
        match = submissions.DOI_RE.match(f["doi"])
        if match:
            f["doi"] = match.group(1)
        else:
            errors.append(de_en("DOI (z. B. 10.1037/0022-3514.84.4.822)", "DOI (e.g. 10.1037/0022-3514.84.4.822)"))
    if form.get("rights") != "1":
        errors.append(de_en("Bestätigung der Nutzungsrechte", "confirmation of the rights of use"))
    return f, errors


def slug(text):
    """A name for the key from `text`: lower-case letters, digits and _ (umlauts spelled out)."""
    text = (text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    text = text if text[:1].isalpha() else "k_" + text
    return text[:30].rstrip("_") or "konstrukt"


def _free_key(org_id, name):
    base = f"o{org_id}-{slug(name)}"
    key, n = base, 1
    while db.session.scalar(db.select(Construct.id).filter_by(key=key)):
        n += 1
        key = f"{base[:57]}_{n}"
    return key


def create(org, user, element, f):
    """A construct of organisation `org` from linked element `element` (a copy of its template), with the fields
    of the dialog. The caller commits."""
    from app import constructs

    if element is None or not (element.spec or {}).get("config"):
        raise ValueError(de_en("Bitte ein verknüpftes Element als Grundlage wählen.",
                               "Please choose a linked element as the basis."))
    c = Construct(key=_free_key(org.id, f.get("name") or element.name), name=(f.get("name") or element.name)[:200],
                  short="", description=f["description"], authors="", year=None, citation=f["citation"],
                  doi=f.get("doi", ""), scoring=f["scoring"], notes=_terms(org, element.key, f["licence"]),
                  spec={**copy.deepcopy({k: v for k, v in (element.spec or {}).items() if k != "origin_survey_id"}),
                        "licence": f["licence"], "en": {k: f.get(f"{k}_en", "") for k in ENGLISH if f.get(f"{k}_en")}},
                  status="review", visibility=VISIBILITY, org_id=org.id, source=SOURCE, version="1.0",
                  created_by_id=user.id, seed_version=1)
    db.session.add(c)
    db.session.flush()
    c.search_text = constructs._search_text(c)
    return c


def propose(element, user, org_id, fields):
    """A member proposes linked element `element` to organisation `org_id` (a submission with org_id). The caller
    commits."""
    from app import linked

    if not linked.may_change(user, element):
        raise PermissionError
    open_one = db.session.scalar(db.select(ConstructSubmission.id).filter(
        ConstructSubmission.construct_id == element.id, ConstructSubmission.org_id == org_id,
        ConstructSubmission.state.in_(ConstructSubmission.OPEN)))
    if open_one:
        raise ValueError(de_en("Das Element ist der Organisation schon vorgeschlagen.",
                               "The element has already been proposed to the organisation."))
    s = ConstructSubmission(construct_id=element.id, user_id=user.id, org_id=org_id, fields=fields)
    db.session.add(s)
    return s


def take_over(s, org, admin, form):
    """An admin of the organisation takes a member's proposal over: a construct of the organisation."""
    if s.org_id != org.id or s.state not in ConstructSubmission.OPEN:
        raise ValueError(de_en("Dieser Vorschlag ist schon entschieden.", "This proposal has already been decided."))
    f = {**(s.fields or {}), "name": (form.get("name") or "").strip()[:200] or s.construct.name}
    c = create(org, admin, s.construct, f)
    s.state, s.toolbox_key, s.seen, s.decided_at = ConstructSubmission.ACCEPTED, c.key, False, utcnow()
    return c


def reject(s, org, note):
    if s.org_id != org.id or s.state not in ConstructSubmission.OPEN:
        raise ValueError(de_en("Dieser Vorschlag ist schon entschieden.", "This proposal has already been decided."))
    s.state, s.note, s.seen, s.decided_at = ConstructSubmission.REJECTED, (note or "").strip()[:2000], False, utcnow()


# ---- the file for a pull request to askurious-constructs ----

def _name(c):
    return PREFIX_RE.sub("", c.key).replace("-", "_")


def construct_yaml(c, host=""):
    """construct.yaml of the catalogue for construct `c` (dict), and {lang: wording} for wording/<lang>.yaml (only
    where the licence lets it be passed on)."""
    spec = c.spec or {}
    key = _name(c)
    lic = spec.get("licence") or ""
    en = english_of(c)
    open_ = distributable(lic)
    wordings = {lang: catalog.wording_from_texts(spec, lang) for lang in (spec.get("texts") or {})}
    languages = {}
    for lang, w in wordings.items():
        languages[lang] = ({"status": "source", "wording": "repo"} if open_ else
                           {"status": "source", "wording": "overlay", "sha256": catalog.wording_hash(w)})
    structure = {"type": spec.get("type"), "code": spec.get("code"), "config": copy.deepcopy(spec.get("config") or {})}
    structure["config"].pop("construct", None)
    if not open_ and spec.get("type") == "likert":
        structure["labels"] = {lang: list(t.get("labels") or []) for lang, t in (spec.get("texts") or {}).items()}
        for w in wordings.values():
            w.pop("labels", None)
        languages = {lang: {"status": "source", "wording": "overlay", "sha256": catalog.wording_hash(w)}
                     for lang, w in wordings.items()}
    data = {
        "format": 1, "id": f"ask:{key}", "key": key,
        "aliases": [f"local:{host or 'askurious'}/o{c.org_id}/{key}"] if c.org_id else [],
        "version": "1.0", "status": "review", "kind": "scale", "requires": "2026.11",
        # English: as given, else the German to translate (the catalogue asks for both; its check names them)
        "name": {"de": c.name, "en": en.get("name") or c.name}, "short": {"de": c.short or "", "en": ""},
        "description": {"de": c.description or "", "en": en.get("description") or c.description or ""},
        "scoring": {"de": c.scoring or "", "en": en.get("scoring") or c.scoring or ""},
        "notes": {"de": c.notes or "", "en": ""}, "authors": [], "year": c.year,
        "sources": [{"role": "original", "citation": c.citation, **({"doi": c.doi} if c.doi else {})}] if c.citation else [],
        "licence": {"wording": lic, "distributable": open_, "permission_required": False, "commercial": None},
        "tags": {}, "family": None, "structure": structure, "languages": languages,
        "versions": [{"version": "1.0", "date": utcnow().date().isoformat(),
                      "note": "Beitrag aus einer Askurious-Installation / contributed from an Askurious installation"}],
    }
    return data, (wordings if open_ else {})


def pr_zip(c, host=""):
    """(file name, bytes) of a ZIP for a pull request to askurious-constructs."""
    data, wordings = construct_yaml(c, host)
    key = data["key"]
    head = ("# askurious-constructs: construct.yaml (als JSON geschrieben, gültiges YAML). Bitte die englischen Felder\n"
            "# ergänzen und mit python tools/validate.py prüfen. / Written as JSON, which is valid YAML. Please fill in\n"
            "# the English fields and check with python tools/validate.py.\n")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"constructs/{key}/construct.yaml", head + json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        for lang, w in wordings.items():
            z.writestr(f"constructs/{key}/wording/{lang}.yaml",
                       json.dumps({"for": f"ask:{key}@1.0", **w}, ensure_ascii=False, indent=2) + "\n")
        z.writestr("README.txt", de_en(
            "Ein Konstrukt für askurious-constructs ({repo}). Ordner constructs/{key}/ in einen Fork kopieren, die "
            "englischen Felder ergänzen, python tools/validate.py ausführen und einen Pull Request mit DCO-Sign-off "
            "öffnen (CONTRIBUTING.md). Ohne Git: {contact}. Lizenzierter Wortlaut ist nicht enthalten, nur sein Hash.\n",
            "A construct for askurious-constructs ({repo}). Copy the folder constructs/{key}/ into a fork, fill in the "
            "English fields, run python tools/validate.py and open a pull request with a DCO sign-off "
            "(CONTRIBUTING.md). Without Git: {contact}. Licensed wording is not included, only its hash.\n",
            repo=REPOSITORY, key=key, contact=contact()))
    return f"{key}-askurious-constructs.zip", out.getvalue()
