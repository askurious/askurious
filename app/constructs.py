"""Construct toolbox: validated instruments that are inserted as ordinary items.

Constructs live in the database (model `Construct`, classified by `Tag`s in facets) and are
found through a full-text index (SQLite FTS5; LIKE fallback on other databases). The seed
constructs below are written to the database at start-up and updated when their
`seed_version` increases.

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


# Response labels of the GESIS short scales (ZIS questionnaire sheets, German and English version)
GESIS5 = {
    "de": ["trifft gar nicht zu", "trifft wenig zu", "trifft etwas zu", "trifft ziemlich zu", "trifft voll und ganz zu"],
    "en": ["doesn't apply at all", "applies a bit", "applies somewhat", "applies mostly", "applies completely"],
}
GESIS_TERMS = ("GESIS/ZIS: kostenfrei für nichtkommerzielle Forschungszwecke. Für andere Zwecke oder eine andere als die "
               "dokumentierte Form ist das Einverständnis der Autorinnen und Autoren einzuholen. Die ZIS-Dokumentation "
               "ist in allen resultierenden Arbeiten als Quelle anzugeben.")
# No general distribution permission is documented; public outputs use neutral IDs and sources instead.
PERMISSION_REQUIRED = frozenset({"gdms", "kut", "tam_pu", "tam_peou", "tam_bi"})
NOT_SHARED = PERMISSION_REQUIRED
# Scales of the GESIS/ZIS collection: another form than the documented one (a translation) needs the consent of the
# authors (GESIS_TERMS); the translation page says so (rights review 2026-10-21)
ZIS = frozenset({"bfi10", "asku", "ie4", "kusiv3", "i8", "nfck", "tb", "r1", "aub"})
# Wording the public Konstrukte page shows to visitors who are not signed in (Ralf 2026-10-16, option 1): only where
# the licence lets anyone pass it on (the RatSWD standard questions, CC BY 4.0, ratswd.KEYS) and Askurious' own
# wordings. Everything else (GESIS/ZIS, journal articles, APA PsycTests, constructs submitted by researchers) shows
# its source instead. PERMISSION_REQUIRED also applies to signed-in researchers.
OWN_WORDING = {"age", "sorgfalt", "kamb", "fitai", "kuk"}  # kamb, fitai, kuk: CC BY 4.0 (Ralf, 2026-10-16)


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


def _scale(code, points, labels, question, items, labeling="full"):
    """Likert template. items: [{"de": …, "en": …, "reverse": bool, "subscale": …}]; languages as in `question`."""
    stmts = []
    for n, it in enumerate(items):
        st = {"id": f"s{n + 1}", "code": f"{code}_{n + 1}", "reverse": bool(it.get("reverse"))}
        if it.get("subscale"):
            st["subscale"] = it["subscale"]
        stmts.append(st)
    config = {"points": points, "labeling": labeling, "label_display": "once", "randomize": True, "statements": stmts}
    texts = {lang: {"question": question[lang], "labels": list(labels[lang]),
                    "statements": {st["id"]: it[lang] for st, it in zip(stmts, items)}}
             for lang in question}
    return {"type": "likert", "code": code, "config": config, "texts": texts}


KUT_LABELS = {
    "de": ["stimmt gar nicht", "stimmt wenig", "stimmt mittelmäßig", "stimmt ziemlich", "stimmt sehr"],
    "en": ["not at all true", "slightly true", "moderately true", "quite true", "very true"],
}

# (option id, code, German, English); the self-description has a text field (column gender_4_text) and may be
# left out per element (`optional`, the editor's tick; Spiel et al., 2019: "prefer to self-describe"). "keine
# Angabe" counts as missing from the start (UI/UX audit 2026-10-07, W1): a group comparison by gender then takes the
# genders, not the refusal; only newly inserted questions get it, existing surveys stay as they are
GENDER_OPTIONS = [("o1", "1", "weiblich", "female"), ("o2", "2", "männlich", "male"), ("o3", "3", "divers", "diverse"),
                  ("o5", "4", "Ich beschreibe mich selbst:", "Prefer to self-describe:"),
                  ("o4", "99", "keine Angabe", "prefer not to disclose")]
GENDER_SELF = "o5"
GENDER_MISSING = "o4"


def _gender_spec():
    opts = [{"id": oid, "code": code, **({"text": True, "optional": True} if oid == GENDER_SELF else {}),
             **({"missing": True} if oid == GENDER_MISSING else {})}
            for oid, code, _, _ in GENDER_OPTIONS]
    texts = {lang: {"question": "Welchem Geschlecht fühlen Sie sich zugehörig?" if lang in ("de", "de-du") else "What is your gender?",
                    "options": {oid: de if lang in ("de", "de-du") else en for oid, _, de, en in GENDER_OPTIONS}}
             for lang in ("de", "en")}
    return {"type": "single_choice", "code": "gender", "config": {"options": opts, "layout": "vertical"}, "texts": texts}


SELF_REPORT_TEXTS = {
    "de": ("Zum Schluss eine ehrliche Frage: Haben Sie die Fragen so aufmerksam beantwortet, dass wir Ihre Antworten "
           "auswerten sollten? Ihre Antwort hat keinerlei Folgen für Sie.",
           "Ja, meine Antworten können ausgewertet werden.", "Nein, meine Antworten sollten nicht ausgewertet werden."),
    "de-du": ("Zum Schluss eine ehrliche Frage: Hast du die Fragen so aufmerksam beantwortet, dass wir deine Antworten "
              "auswerten sollten? Deine Antwort hat keinerlei Folgen für dich.",
              "Ja, meine Antworten können ausgewertet werden.", "Nein, meine Antworten sollten nicht ausgewertet werden."),
    "en": ("One last honest question: Did you answer the questions attentively enough for us to use your answers? "
           "Your answer has no consequences for you whatsoever.",
           "Yes, my answers can be used.", "No, my answers should not be used."),
}


def _self_report_spec():
    """Self-report on the own participation at the end: 1 = use my answers, 0 = do not (app/quality.py reads it)."""
    opts = [{"id": "o1", "code": "1"}, {"id": "o2", "code": "0"}]
    texts = {lang: {"question": t[0], "options": {"o1": t[1], "o2": t[2]}} for lang, t in SELF_REPORT_TEXTS.items()}
    return {"type": "single_choice", "code": "sorgfalt", "config": {"options": opts, "layout": "vertical"}, "texts": texts}


def _age_spec():
    """Age in whole years (10 to 120), a free text field with number validation and "years" after the box."""
    texts = {"de": {"question": "Wie alt sind Sie?", "prefix": "", "suffix": "Jahre", "validation_message": ""},
             "en": {"question": "How old are you?", "prefix": "", "suffix": "years", "validation_message": ""}}
    return {"type": "text_input", "code": "age", "texts": texts,
            "config": {"multiline": False, "max_length": 3, "validation": "integer", "min": 10, "max": 120, "pattern": ""}}



# Scales of others (GESIS/ZIS, journal articles, APA PsycTests): the code holds their structure (codes, points,
# response labels, subscales, reversed items); the wording comes from the installation, never from the public code
# (rights review 2026-10-21): INSTRUMENT_WORDINGS, by default instance_overlay/instruments.json (instance_overlay/
# README.md). Without it these constructs are not seeded; constructs already in the database stay as they are.
PLACEHOLDER = "[System]"  # TAM: stands for the technology
LIKELY7 = {
    "de": ["äußerst unwahrscheinlich", "ziemlich unwahrscheinlich", "eher unwahrscheinlich", "weder noch",
           "eher wahrscheinlich", "ziemlich wahrscheinlich", "äußerst wahrscheinlich"],
    "en": ["extremely unlikely", "quite unlikely", "slightly unlikely", "neither", "slightly likely", "quite likely",
           "extremely likely"],
}
GDMS_STYLES = ["Rational", "Intuitive", "Dependent", "Avoidant", "Spontaneous"]


def _agreement7():
    from app.items.likert import AGREEMENT

    return {"de": AGREEMENT["de"][7], "en": AGREEMENT["en"][7]}


def _items(*pairs):
    """(subscale or None, reversed) per item, in the order of the source."""
    return [{"subscale": s, "reverse": r} for s, r in pairs]


_TB = "Technikakzeptanz", "Technikkompetenzüberzeugungen", "Technikkontrollüberzeugungen"
_AUB = "Instrumentelles Naturverständnis", "Intrinsisches Naturverständnis", "Natur als Existenzgrundlage"
# key: code, points, response labels (or a function that gives them), labeling, items
INSTRUMENTS = {
    # BFI-10: polarity and subscales as in the ZIS documentation (zis76); only the endpoints are labelled there
    "bfi10": ("BFI", 5, {"de": ["trifft überhaupt nicht zu", "2", "3", "4", "trifft voll und ganz zu"],
                         "en": ["disagree strongly", "2", "3", "4", "agree strongly"]}, "endpoints",
              _items(("E", True), ("A", False), ("C", True), ("N", True), ("O", True),
                     ("E", False), ("A", True), ("C", False), ("N", False), ("O", False))),
    # English: an unofficial working translation
    "kut": ("KUT", 5, KUT_LABELS, "full", _items(*((None, r) for r in (False, True, True, False, True, True, False, False)))),
    "asku": ("ASKU", 5, GESIS5, "full", _items((None, False), (None, False), (None, False))),
    "ie4": ("IE4", 5, GESIS5, "full", _items(("IKÜ", False), ("IKÜ", False), ("EKÜ", False), ("EKÜ", False))),
    "kusiv3": ("KUSIV", 5, GESIS5, "full", _items((None, False), (None, True), (None, False))),
    "i8": ("I8", 5, GESIS5, "full", _items(*((s, False) for s in ("Dringlichkeit", "Dringlichkeit", "Absicht", "Absicht",
                                                                  "Ausdauer", "Ausdauer", "Risikobereitschaft",
                                                                  "Risikobereitschaft")))),
    # German only: the ZIS documentation labels the German scale (1, middle "weder noch", 7)
    "nfck": ("NFC", 7, {"de": ["trifft überhaupt nicht zu", "2", "3", "weder noch", "5", "6", "trifft ganz genau zu"]},
             "endpoints", _items((None, True), (None, False), (None, False), (None, True))),
    # Technikbereitschaft: polarity, subscales and response labels as in the ZIS documentation (zis244, table 1)
    "tb": ("TB", 5, {"de": ["stimmt gar nicht", "stimmt wenig", "stimmt teilweise", "stimmt ziemlich", "stimmt völlig"]},
           "full", _items(*([(_TB[0], False)] * 4 + [(_TB[1], True)] * 4 + [(_TB[2], False)] * 4))),
    # One item: the question is the item, the single row has no text of its own (zis236; English zis286)
    "r1": ("R1", 7, {"de": ["gar nicht risikobereit", "2", "3", "4", "5", "6", "sehr risikobereit"],
                     "en": ["not at all willing to take risks", "2", "3", "4", "5", "6", "very willing to take risks"]},
           "endpoints", _items((None, False))),
    # Allgemeines Umweltbewusstsein (zis208): subscales as in table 3 there; items 3 and 5 load on two factors
    "aub": ("AUB", 5, {"de": ["stimme überhaupt nicht zu", "stimme eher nicht zu", "teils/teils", "stimme weitgehend zu",
                              "stimme voll und ganz zu"]}, "full",
            _items(*((s, False) for s in (_AUB[0], _AUB[0], None, _AUB[0], None, _AUB[0], _AUB[1], _AUB[1], _AUB[2],
                                          _AUB[2], _AUB[2])))),
    # TAM (Davis, 1989; Venkatesh & Davis, 2000): "[System]" stands for the technology; German a working translation
    "tam_pu": ("PU", 7, LIKELY7, "full", _items(*[(None, False)] * 6)),
    "tam_peou": ("PEOU", 7, LIKELY7, "full", _items(*[(None, False)] * 6)),
    "tam_bi": ("BI", 7, _agreement7, "full", _items((None, False), (None, False))),
    # GDMS (Scott & Bruce, 1995): 25 items, styles in turn; English only
    "gdms": ("GDMS", 5, {"en": ["strongly disagree", "2", "3", "4", "strongly agree"]}, "endpoints",
             _items(*((GDMS_STYLES[n % 5], False) for n in range(25)))),
}
_WORDINGS = {}  # path: (modification time, wordings)


def instrument_wordings():
    """{key: {"question": {lang: text}, "statements": [{lang: text}, …]}} of the installation, {} without the file."""
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


def _instrument_spec(key):
    """The item template of a scale of others with the installation's wording; None without a fitting wording."""
    code, points, labels, labeling, items = INSTRUMENTS[key]
    labels = labels() if callable(labels) else labels
    wording = instrument_wordings().get(key)
    question = wording.get("question") if isinstance(wording, dict) else None
    statements = wording.get("statements") if isinstance(wording, dict) else None
    if not isinstance(question, dict) or not isinstance(statements, list) or len(statements) != len(items):
        return None
    langs = [lang for lang in question if lang in labels and isinstance(question[lang], str)]
    if not langs or not all(isinstance(s, dict) and all(isinstance(s.get(lang), str) for lang in langs)
                            for s in statements):
        return None
    return _scale(code, points, {lang: labels[lang] for lang in langs}, {lang: question[lang] for lang in langs},
                  [{**item, **{lang: s[lang] for lang in langs}} for item, s in zip(items, statements)], labeling)


def _instrument(key):
    return functools.partial(_instrument_spec, key)


TAM_CITATION = ("Davis, F. D. (1989). Perceived usefulness, perceived ease of use, and user acceptance of information "
                "technology. MIS Quarterly, 13(3), 319–340.")
# own instruments the authors released under CC BY 4.0 (Ralf, 2026-10-16: KAMB, FITAI, KUK)
CC_BY_TERMS = ("Lizenz: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), frei nutzbar und anpassbar mit "
               "Quellenangabe und Hinweis auf Änderungen. ")
CC_BY_TERMS_EN = ("Licence: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), free to use and adapt with "
                  "attribution and a note of changes. ")
# Scales from journal articles without terms of their own (rights review 2026-10-08): no publisher has released the wording.
ARTICLE_TERMS = ("Für den Wortlaut und seine Übersetzungen ist keine allgemeine Nutzungs- oder "
                 "Veröffentlichungsfreigabe dokumentiert. Die Angabe „nichtkommerzielle Forschung und Lehre“ "
                 "allein ersetzt keinen Rechtebeleg. Neue Nutzung setzt einen vom Betreiber geprüften Nachweis für die konkrete "
                 "Studie, Person und Sprachfassung voraus. Die Quelle ist immer anzugeben.")
TAM_NOTES = (ARTICLE_TERMS + " „[System]“ durch den Namen der Technik ersetzen (zählt nicht als Veränderung). Englischer Wortlaut wie "
             "veröffentlicht; deutsche Fassung ist eine inoffizielle Arbeitsübersetzung. Davis (1989) nutzte 7-stufige "
             "Skalen (likely/unlikely; in Studie 1 agree/disagree), im Original steht „likely“ links; hier 1 = "
             "äußerst unwahrscheinlich, 7 = äußerst wahrscheinlich.")

# Language versions in the toolbox that are only working translations (see the notes of each construct): the
# translation page (app/translations.py) treats them as translatable, not as validated versions.
def _semantic(code, points, question, pairs):
    """Semantic differential template. pairs: [{"de": (left, right), "en": (left, right), "reverse": bool,
    "subscale": …}] in the order and orientation of the source; languages as in `question`."""
    config_pairs, texts = [], {lang: {"question": question[lang], "pairs": {}} for lang in question}
    for n, p in enumerate(pairs):
        pair = {"id": f"p{n + 1}", "code": f"{code}_{n + 1}", "reverse": bool(p.get("reverse"))}
        if p.get("subscale"):
            pair["subscale"] = p["subscale"]
        config_pairs.append(pair)
        for lang in question:
            texts[lang]["pairs"][pair["id"]] = {"left": p[lang][0], "right": p[lang][1]}
    config = {"points": points, "numbers": False, "randomize": True, "pairs": config_pairs}
    return {"type": "semantic", "code": code, "config": config, "texts": texts}


def _kamb_spec():
    # Brauner, Glawe, Vervier & Ziefle (2026): wording, order, and poles as administered (German labels of the
    # published data on OSF); the pairs with the positive pole on the left are reversed, as in their analysis
    A, K = "affektiv", "kognitiv"
    return _semantic("KAMB", 6, {"de": "Bewerten Sie den Artikel mithilfe der folgenden Begriffspaare.",
                                 "en": "Please rate the article using the following pairs of terms."}, [
        {"subscale": K, "reverse": True, "de": ("professionell", "amateurhaft"), "en": ("professional", "amateur")},
        {"subscale": A, "de": ("kaltherzig", "warmherzig"), "en": ("cold-hearted", "warm-hearted")},
        {"subscale": K, "de": ("verwirrend", "verständlich"), "en": ("confusing", "understandable")},
        {"subscale": K, "reverse": True, "de": ("überzeugend", "zweifelhaft"), "en": ("convincing", "doubtful")},
        {"subscale": A, "reverse": True, "de": ("beruhigend", "beängstigend"), "en": ("reassuring", "frightening")},
        {"subscale": A, "de": ("negativ", "positiv"), "en": ("negative", "positive")},
        {"subscale": K, "reverse": True, "de": ("neutral", "parteiisch"), "en": ("neutral", "biased")},
        {"subscale": A, "de": ("langweilig", "interessant"), "en": ("boring", "interesting")},
    ])


def _kuk_spec():
    # Wittland, Brauner & Ziefle (2015); English translated for Askurious and released as validated by the authors' group
    from app.items.likert import AGREEMENT

    return _scale("KUK", 6, {"de": AGREEMENT["de"][6], "en": AGREEMENT["en"][6]}, {
        "de": "Sie als Koch: Nun möchten wir von Ihnen wissen, wie Sie sich persönlich als Koch einschätzen.",
        "en": "You as a cook: We would now like to know how you personally rate yourself as a cook."}, [
        {"reverse": True, "de": "Ich fühle mich beim Kochen hilflos überfordert.", "en": "I feel helplessly overwhelmed when cooking."},
        {"de": "Ich bin bei uns zuhause der Chefkoch.", "en": "I am the head chef in our home."},
        {"de": "Ich probiere gerne neue Gerichte aus, die ich vorher noch nie gekocht habe.",
         "en": "I like to try out new dishes that I have never cooked before."},
        {"de": "Ich koche leidenschaftlich gerne und habe viel Spaß dabei.", "en": "I am passionate about cooking and enjoy it a lot."},
        {"reverse": True, "de": "Ich koche in der Regel altbewährte Rezepte, da ich weiß, dass sie mir gelingen.",
         "en": "I usually cook tried-and-tested recipes because I know they will turn out well."},
        {"de": "Ich werde häufig von anderen wegen meiner Kochkünste gelobt.", "en": "Others often praise me for my cooking skills."},
        {"de": "Ich könnte ohne Schwierigkeiten für ein großes Familientreffen kochen.",
         "en": "I could cook for a large family gathering without difficulty."},
        {"de": "Ich kann Anderen beim Kochen noch etwas beibringen.", "en": "I can still teach others a thing or two about cooking."},
        {"reverse": True, "de": "Ich koche lieber für mich alleine, da ich mich schäme, wenn es mal nicht gelingt.",
         "en": "I prefer to cook just for myself because I am embarrassed when something does not turn out well."},
        {"de": "Ich lerne beim Kochen gerne von Anderen und probiere es dann selber aus.",
         "en": "I like to learn from others when cooking and then try it out myself."},
    ])


FITAI_ITEMS = [  # (target: the statement is true, German, English)
    (True, "Große Sprachmodelle (LLMs) funktionieren in erster Linie, indem sie das statistisch wahrscheinlichste nächste "
           "Wort in einer Sequenz vorhersagen.",
     "Large language models (LLMs) primarily work by predicting the statistically most likely next word in a sequence."),
    (True, "KI-Modelle werden auf umfangreichen Datensätzen trainiert, die häufig urheberrechtlich geschützte Bücher, "
           "Artikel und Websites enthalten.",
     "AI models are trained on extensive datasets that often contain copyrighted books, articles, and websites."),
    (True, "Generative KI kann „halluzinieren“, d. h. sie liefert mit Überzeugung Antworten, die sachlich falsch sind.",
     "Generative AI can “hallucinate”, i.e., it confidently gives answers that are factually wrong."),
    (True, "Das „Kontextfenster“ eines Modells legt die maximale Textmenge fest, die es in einem einzelnen Gespräch "
           "verarbeiten oder „erinnern“ kann.",
     "A model's “context window” sets the maximum amount of text it can process or “remember” in a single conversation."),
    (True, "„Reinforcement Learning from Human Feedback (RLHF)“ ist eine Methode, um das Verhalten von KI an menschlichen "
           "Werten und Sicherheitsrichtlinien auszurichten.",
     "“Reinforcement Learning from Human Feedback (RLHF)” is a method for aligning AI behavior with human values and "
     "safety guidelines."),
    (True, "Neuronale Netze bestehen aus „Gewichten“ und „Biaswerten“, die während des Trainingsprozesses angepasst werden, "
           "um Fehler zu minimieren.",
     "Neural networks consist of “weights” and “biases” that are adjusted during training to minimize errors."),
    (False, "Um Überhitzung zu verhindern, müssen moderne KI-Systeme die Erinnerungen inaktiver Nutzerinnen und Nutzer "
            "löschen, um physischen Speicherplatz freizugeben.",
     "To prevent overheating, modern AI systems must delete the memories of inactive users to free up physical storage."),
    (False, "KI-Modelle besitzen ein digitales „Unterbewusstsein“, das Informationen verarbeitet, auch wenn sie nicht aktiv "
            "genutzt werden.",
     "AI models have a digital “subconscious” that processes information even when they are not actively used."),
    (False, "KI-Modelle lernen neue Sprache durch „Digitale Osmose“, indem sie neben Dateien gespeichert werden, die in "
            "dieser Sprache verfasst sind.",
     "AI models learn a new language through “digital osmosis” by being stored next to files written in that language."),
    (False, "Durch „Semantische Synchronizität“ können KI-Modelle grundlegende menschliche Emotionen wie Frustration "
            "ausdrücken, wenn eine Eingabe unklar ist.",
     "Through “semantic synchronicity”, AI models can express basic human emotions such as frustration when a prompt "
     "is unclear."),
    (False, "Fortgeschrittene LLMs nutzen „Quantenverschränkung“, um ihre Antworten gleichzeitig auf verschiedenen "
            "Nutzergeräten zu synchronisieren, ohne das Internet zu verwenden.",
     "Advanced LLMs use “quantum entanglement” to synchronize their answers across different user devices at the same "
     "time without using the internet."),
    (False, "„Rekursive Hardware-Heilung“ ist ein Prozess, bei dem KI beschädigte GPU-Schaltkreise physisch reparieren kann, "
            "indem sie elektrische Ströme umleitet.",
     "“Recursive hardware healing” is a process by which AI can physically repair damaged GPU circuits by rerouting "
     "electrical currents."),
]


def _fitai_spec():
    # Brauner & Glawe (2026); the instruction without "Sie" or "du", so one German version serves both
    items = [{"de": de, "en": en, "reverse": not target, "subscale": "Target" if target else "Foil"}
             for target, de, en in FITAI_ITEMS]
    return _scale("FI", 5, {"de": ["stimme gar nicht zu", "", "", "", "stimme voll zu"],
                            "en": ["strongly disagree", "", "", "", "strongly agree"]}, {
        "de": "Im Folgenden stehen verschiedene Aussagen über Künstliche Intelligenz. Inwieweit treffen diese Aussagen zu?",
        "en": "Below are various statements about artificial intelligence. To what extent are these statements true?"},
        items, labeling="endpoints")




WORKING_LANGS = {"kut": {"en"}, "tam_pu": {"de"}, "tam_peou": {"de"}, "tam_bi": {"de"}}

SEEDS = [
    {"key": "gender", "seed_version": 6, "name": "Geschlecht", "status": "review",
     "short": "Weiblich, männlich, divers, Selbstbeschreibung, keine Angabe",
     "description": "Geschlechtsidentität nach den Empfehlungen von Spiel et al. für inklusivere Geschlechtsabfragen: "
                    "weiblich, männlich, divers, „Ich beschreibe mich selbst:“ mit Textfeld und „keine Angabe“. Die "
                    "Selbstbeschreibung lässt sich je Element abwählen; das gilt nicht als Abweichung.",
     "authors": "Spiel, Haimson & Lottridge", "year": 2019,
     "citation": "Spiel, K., Haimson, O. L., & Lottridge, D. (2019). How to do better with gender on surveys: "
                 "A guide for HCI researchers. Interactions, 26(4), 62–65.",
     "doi": "10.1145/3338283", "scoring": "",
     "notes": "Eigene Formulierung von Askurious nach den Empfehlungen von Spiel, Haimson & Lottridge (2019).",
     "spec": _gender_spec,
     "tags": ["status:Standardfrage", "art:Demografie", "thema:Gender", "fach:HCI"]},
    {"key": "age", "seed_version": 1, "name": "Alter", "status": "review",
     "short": "Alter in Jahren (ganze Zahl, 10–120)",
     "description": "Alter in ganzen Jahren als Freitext mit Zahlenprüfung (10 bis 120), „Jahre“ hinter dem Feld. "
                    "Einheitlich erhoben, damit Studien in der Metaanalyse zusammengeführt werden können.",
     "authors": "", "year": None, "citation": "", "doi": "", "scoring": "Ganze Jahre; keine Rekodierung.",
     "notes": "Eigene Formulierung von Askurious.", "spec": _age_spec, "tags": ["status:Standardfrage", "art:Demografie", "thema:Alter"]},
    {"key": "sorgfalt", "seed_version": 1, "name": "Sorgfalt der Teilnahme (Selbstauskunft)", "status": "review",
     "short": "Eine Frage am Ende: Sollen wir Ihre Antworten auswerten? Ja oder nein",
     "description": "Selbstauskunft zur eigenen Teilnahme, gestellt am Ende der Befragung. Wer „nein“ antwortet, hat "
                    "nach eigener Angabe nicht aufmerksam geantwortet. Personen, die sich als ernsthaft Teilnehmende "
                    "bezeichnen, antworten konsistenter und vorhersagegültiger (Aust et al., 2013). Die Seite "
                    "„Qualitätssicherung“ zählt die Antworten „nein“.",
     "authors": "Meade & Craig; Aust, Diedenhofen, Ullrich & Musch", "year": 2012,
     "citation": "Meade, A. W., & Craig, S. B. (2012). Identifying careless responses in survey data. Psychological "
                 "Methods, 17(3), 437–455. Aust, F., Diedenhofen, B., Ullrich, S., & Musch, J. (2013). Seriousness "
                 "checks are useful to improve data validity in online research. Behavior Research Methods, 45(2), "
                 "527–535. https://doi.org/10.3758/s13428-012-0265-2",
     "doi": "10.1037/a0028085", "scoring": "1 = Antworten auswerten, 0 = nicht auswerten; keine Rekodierung.",
     "notes": "Eigene Formulierung in Anlehnung an die Selbstauskunft bei Meade & Craig (2012) und den Seriousness "
              "Check bei Aust et al. (2013), kein wörtlich übernommenes Item. Wichtig ist der Zusatz, dass die Antwort "
              "keine Folgen hat (Vergütung, Verlosung), sonst antwortet niemand mit „nein“.",
     "spec": _self_report_spec, "tags": ["status:Standardfrage", "art:Datenqualität", "thema:Sorgfalt"]},
    {"key": "bfi10", "seed_version": 4, "name": "Big Five Inventory (BFI-10)", "status": "verified",
     "short": "10 Aussagen, 5 Stufen; je 2 Items für Extraversion, Verträglichkeit, Gewissenhaftigkeit, Neurotizismus, Offenheit",
     "description": "Ultrakurzform des Big Five Inventory für Befragungen mit wenig Zeit. Erfasst die fünf "
                    "Persönlichkeitsdimensionen mit je einem positiv und einem negativ gepolten Item.",
     "authors": "Rammstedt & John", "year": 2007,
     "citation": "Rammstedt, B., & John, O. P. (2007). Measuring personality in one minute or less: A 10-item short "
                 "version of the Big Five Inventory in English and German. Journal of Research in Personality, 41(1), 203–212. "
                 "Dokumentation: Rammstedt, B., Kemper, C. J., Klein, M. C., Beierlein, C., & Kovaleva, A. (2014). "
                 "Big Five Inventory (BFI-10). Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS). "
                 "https://doi.org/10.6102/zis76",
     "doi": "10.1016/j.jrp.2006.02.001",
     "scoring": "Skalenwerte = Mittelwert je Subskala (E: 1, 6; A: 2, 7; C: 3, 8; N: 4, 9; O: 5, 10); "
                "die negativ gepolten Items 1, 3, 4, 5 und 7 sind im Export bereits rekodiert.",
     "notes": GESIS_TERMS + " Deutscher Wortlaut geprüft gegen ZIS; englische Instruktion und Endpunkte nach Rammstedt & John (2007). "
              "Die englischen Items stammen aus dem Big Five Inventory (John, Donahue & Kentle, 1991; © Oliver P. John), "
              "das für nichtkommerzielle Forschung frei genutzt werden darf.",
     "spec": _instrument("bfi10"), "tags": ["status:validiert", "quelle:GESIS ZIS", "art:Persönlichkeit", "fach:Psychologie"]},
    {"key": "kut", "seed_version": 4, "name": "Kontrollüberzeugung im Umgang mit Technik (KUT)", "status": "review",
     "short": "8 Aussagen, 5 Stufen; subjektive Kontrolle über technische Geräte",
     "description": "Erfasst, inwieweit Personen glauben, technische Probleme durch eigenes Handeln lösen zu können "
                    "(internal) oder sich Technik ausgeliefert fühlen.",
     "authors": "Beier", "year": 1999,
     "citation": "Beier, G. (1999). Kontrollüberzeugungen im Umgang mit Technik. Report Psychologie, 24(9), 684–693.",
     "doi": "",
     "scoring": "Skalenwert = Mittelwert der 8 Items; umgepolte Items (2, 3, 5, 6) sind im Export bereits rekodiert.",
     "notes": ARTICLE_TERMS + " Englische Fassung: inoffizielle Arbeitsübersetzung.", "spec": _instrument("kut"),
     "tags": ["status:validiert", "art:Einstellung", "thema:Technik", "thema:Kontrollüberzeugung", "fach:Psychologie"]},
    {"key": "asku", "seed_version": 1, "name": "Allgemeine Selbstwirksamkeit Kurzskala (ASKU)", "status": "verified",
     "short": "3 Aussagen, 5 Stufen; Erwartung, Schwierigkeiten aus eigener Kraft zu bewältigen",
     "description": "Ökonomisches Instrument zur Erfassung individueller Kompetenzerwartungen, mit Schwierigkeiten und "
                    "Hindernissen im täglichen Leben umgehen zu können. McDonalds Omega .81 bis .87.",
     "authors": "Beierlein, Kovaleva, Kemper & Rammstedt", "year": 2014,
     "citation": "Beierlein, C., Kovaleva, A., Kemper, C. J., & Rammstedt, B. (2014). Allgemeine Selbstwirksamkeit "
                 "Kurzskala (ASKU). Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS).",
     "doi": "10.6102/zis35",
     "scoring": "Skalenwert = Mittelwert der 3 Items (1 bis 5).",
     "notes": GESIS_TERMS + " Englische Fassung (eigene Quelle, Bedingungen: CC BY-NC-SA 4.0): GSE-3: Doll, E. S., Nießen, D., Schmidt, I., Rammstedt, B., & Lechner, C. M. (2021). The General Self-Efficacy Short Scale-3 (GSE-3). ZIS. https://doi.org/10.6102/zis294", "spec": _instrument("asku"),
     "tags": ["status:validiert", "quelle:GESIS ZIS", "art:Persönlichkeit", "thema:Selbstwirksamkeit", "fach:Psychologie"]},
    {"key": "ie4", "seed_version": 1, "name": "Internale-Externale-Kontrollüberzeugung-4 (IE-4)", "status": "verified",
     "short": "4 Aussagen, 5 Stufen; je 2 Items für internale und externale Kontrollüberzeugung",
     "description": "Kurzskala zur Kontrollüberzeugung: Wie sehr erleben Personen Ereignisse als Folge des eigenen Handelns "
                    "(internal) oder als Schicksal, Zufall oder von anderen bestimmt (external)?",
     "authors": "Kovaleva, Beierlein, Kemper & Rammstedt", "year": 2014,
     "citation": "Kovaleva, A., Beierlein, C., Kemper, C. J., & Rammstedt, B. (2014). Internale-Externale-"
                 "Kontrollüberzeugung-4 (IE-4). Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS).",
     "doi": "10.6102/zis184",
     "scoring": "Zwei Skalenwerte: Mittelwert der Items 1 und 2 (internal, IKÜ) und der Items 3 und 4 (external, EKÜ); "
                "keine Umpolung.",
     "notes": GESIS_TERMS + " Englische Fassung: Nießen, D., Schmidt, I., Groskurth, K., Rammstedt, B., & Lechner, C. M. (2022). The Internal–External Locus of Control Short Scale–4 (IE-4): A comprehensive validation of the English-language adaptation. PLOS ONE, 17(7), e0271289. https://doi.org/10.1371/journal.pone.0271289; der Artikel steht unter CC BY 4.0, für die Nutzung der Items gelten die ZIS-Bedingungen.", "spec": _instrument("ie4"),
     "tags": ["status:validiert", "quelle:GESIS ZIS", "art:Persönlichkeit", "thema:Kontrollüberzeugung", "fach:Psychologie"]},
    {"key": "kusiv3", "seed_version": 1, "name": "Interpersonales Vertrauen (KUSIV3)", "status": "verified",
     "short": "3 Aussagen, 5 Stufen; Vertrauen in andere Menschen als Persönlichkeitsmerkmal",
     "description": "Kurzskala zur Messung des zwischenmenschlichen Vertrauens mit drei Items (Bearbeitung etwa 30 Sekunden).",
     "authors": "Beierlein, Kemper, Kovaleva & Rammstedt", "year": 2014,
     "citation": "Beierlein, C., Kemper, C. J., Kovaleva, A., & Rammstedt, B. (2014). Interpersonales Vertrauen (KUSIV3). "
                 "Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS).",
     "doi": "10.6102/zis37",
     "scoring": "Skalenwert = Mittelwert der 3 Items; Item 2 ist negativ formuliert und im Export bereits rekodiert "
                "(Nießen et al., 2020).",
     "notes": GESIS_TERMS + " Englische Fassung: Nießen, D., Beierlein, C., Rammstedt, B., & Lechner, C. M. (2020). An English-language adaptation of the Interpersonal Trust Short Scale (KUSIV3). Measurement Instruments for the Social Sciences, 2, 10. https://doi.org/10.1186/s42409-020-00016-1; der Artikel steht unter CC BY 4.0, für die Nutzung der Items gelten die ZIS-Bedingungen.", "spec": _instrument("kusiv3"),
     "tags": ["status:validiert", "quelle:GESIS ZIS", "art:Einstellung", "thema:Vertrauen", "fach:Psychologie"]},
    {"key": "i8", "seed_version": 1, "name": "Impulsives-Verhalten-8 (I-8)", "status": "verified",
     "short": "8 Aussagen, 5 Stufen; Dringlichkeit, Absicht, Ausdauer, Risikobereitschaft",
     "description": "Kurzskala zu vier Facetten impulsiven Verhaltens mit je zwei Items. Hohe Werte bei Dringlichkeit und "
                    "Risikobereitschaft stehen für hohe, bei Absicht und Ausdauer für niedrige Impulsivität.",
     "authors": "Kovaleva, Beierlein, Kemper & Rammstedt", "year": 2014,
     "citation": "Kovaleva, A., Beierlein, C., Kemper, C. J., & Rammstedt, B. (2014). Die Skala Impulsives-Verhalten-8 "
                 "(I-8). Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS).",
     "doi": "10.6102/zis183",
     "scoring": "Vier Skalenwerte als Mittelwert je zwei Items: Dringlichkeit (1, 2), Absicht (3, 4), Ausdauer (5, 6), "
                "Risikobereitschaft (7, 8); keine Umpolung.",
     "notes": GESIS_TERMS + " Englische Fassung: Groskurth, K., Nießen, D., Rammstedt, B., & Lechner, C. M. (2022). The Impulsive Behavior Short Scale–8 (I-8): A comprehensive validation of the English-language adaptation. PLOS ONE, 17(9), e0273801. https://doi.org/10.1371/journal.pone.0273801; der Artikel steht unter CC BY 4.0, für die Nutzung der Items gelten die ZIS-Bedingungen.", "spec": _instrument("i8"),
     "tags": ["status:validiert", "quelle:GESIS ZIS", "art:Persönlichkeit", "thema:Impulsivität", "fach:Psychologie"]},
    {"key": "nfck", "seed_version": 1, "name": "Need for Cognition Kurzskala (NFC-K)", "status": "verified",
     "short": "4 Aussagen, 7 Stufen; Freude am und Engagement beim Nachdenken",
     "description": "Misst Need for Cognition mit vier Items zu den Facetten Engagement und Freude. Geeignet ab dem "
                    "Jugendalter; Cronbachs Alpha .51 bis .54 (bei vier Items niedrig).",
     "authors": "Beißert, Köhler, Rempel & Beierlein", "year": 2015,
     "citation": "Beißert, H., Köhler, M., Rempel, M., & Beierlein, C. (2015). Kurzskala Need for Cognition NFC-K. "
                 "Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS).",
     "doi": "10.6102/zis230",
     "scoring": "Skalenwert = Mittelwert der 4 Items (1 bis 7); Items 1 und 4 sind im Export bereits rekodiert.",
     "notes": GESIS_TERMS + " Nur deutsche Fassung: Für die englischen Items dokumentiert ZIS keine Antwortskala.",
     "spec": _instrument("nfck"),
     "tags": ["status:validiert", "quelle:GESIS ZIS", "art:Persönlichkeit", "thema:Kognition", "fach:Psychologie"]},
    {"key": "tb", "seed_version": 1, "name": "Kurzskala Technikbereitschaft (TB)", "status": "verified",
     "short": "12 Aussagen, 5 Stufen; je 4 Items für Technikakzeptanz, Technikkompetenz- und Technikkontrollüberzeugungen",
     "description": "Erfasst die Bereitschaft zum Umgang mit moderner Technik (technology commitment) über drei Facetten. "
                    "In allen Altersgruppen einsetzbar, entwickelt besonders für das höhere Lebensalter. Cronbachs Alpha "
                    ".84 (Technikakzeptanz), .84 (Technikkompetenz), .74 (Technikkontrolle), Gesamtskala .84.",
     "authors": "Neyer, Felber & Gebhardt", "year": 2012,
     "citation": "Neyer, F. J., Felber, J., & Gebhardt, C. (2012). Entwicklung und Validierung einer Kurzskala zur Erfassung "
                 "von Technikbereitschaft (technology commitment). Diagnostica, 58, 87–99. "
                 "Dokumentation: Neyer, F. J., Felber, J., & Gebhardt, C. (2016). Kurzskala Technikbereitschaft (TB, "
                 "technology commitment). Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS).",
     "doi": "10.6102/zis244",
     "scoring": "Drei Skalenwerte als Mittelwert je vier Items: Technikakzeptanz (1 bis 4), Technikkompetenzüberzeugungen "
                "(5 bis 8), Technikkontrollüberzeugungen (9 bis 12); die negativ gepolten Items 5 bis 8 sind im Export "
                "bereits rekodiert, hohe Werte stehen also überall für hohe Technikbereitschaft. Ein Gesamtwert über alle "
                "12 Items ist laut ZIS ebenfalls zulässig (dort als Summenwert beschrieben, Kennwerte im Bereich 1 bis 5).",
     "notes": GESIS_TERMS + " Nur deutsche Fassung: ZIS dokumentiert keine englischen Items.",
     "spec": _instrument("tb"),
     "tags": ["status:validiert", "quelle:GESIS ZIS", "art:Einstellung", "thema:Technik", "thema:Technikbereitschaft",
              "fach:Psychologie"]},
    {"key": "r1", "seed_version": 1, "name": "Kurzskala Risikobereitschaft (R-1)", "status": "verified",
     "short": "1 Frage, 7 Stufen; allgemeine Neigung, Risiken einzugehen",
     "description": "Erfasst die Risikobereitschaft, also die Neigung, Risiken einzugehen oder zu vermeiden, mit einer "
                    "einzigen Frage (unter 20 Sekunden). Für die deutschsprachige Allgemeinbevölkerung ab 18 Jahren "
                    "entwickelt; Retest-Reliabilität .74 (englische Fassung .76).",
     "authors": "Beierlein, Kovaleva, Kemper & Rammstedt", "year": 2015,
     "citation": "Beierlein, C., Kovaleva, A., Kemper, C. J., & Rammstedt, B. (2015). Kurzskala zur Erfassung der "
                 "Risikobereitschaft (R-1). Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS). "
                 "https://doi.org/10.6102/zis236. Englische Fassung: Nießen, D., Groskurth, K., Rammstedt, B., & Lechner, C. M. (2020). Risk Proneness "
                 "Short Scale (R-1). Zusammenstellung sozialwissenschaftlicher Items und Skalen (ZIS). "
                 "https://doi.org/10.6102/zis286",
     "doi": "10.6102/zis236",
     "scoring": "Skalenwert = Antwort auf die eine Frage (1 = gar nicht risikobereit bis 7 = sehr risikobereit); keine "
                "Umpolung. Bei einem einzelnen Item gibt es keine interne Konsistenz (kein Cronbachs Alpha).",
     "notes": GESIS_TERMS + " Die englische Fassung (zis286) steht unter CC BY-NC-SA 4.0; veränderte Fassungen davon dürfen nur unter derselben Lizenz weitergegeben werden. Nur die Endpunkte der Skala "
              "sind beschriftet.",
     "spec": _instrument("r1"),
     "tags": ["status:validiert", "quelle:GESIS ZIS", "art:Persönlichkeit", "thema:Risiko", "thema:Risikobereitschaft",
              "fach:Psychologie"]},
    {"key": "aub", "seed_version": 1, "name": "Allgemeines Umweltbewusstsein", "status": "verified",
     "short": "11 Aussagen, 5 Stufen; intrinsisches und instrumentelles Naturverständnis, Natur als Existenzgrundlage",
     "description": "Erfasst Umweltbewusstsein als allgemeine Überzeugung oder Weltbild in drei Dimensionen. Entwickelt "
                    "an der Umfrage „Umweltbewusstsein in Deutschland 1996“. Cronbachs Alpha .46 (intrinsisches "
                    "Naturverständnis), .61 (instrumentelles Naturverständnis), .68 (Natur als Existenzgrundlage), also "
                    "teils niedrig.",
     "authors": "Wingerter", "year": 2001,
     "citation": "Wingerter, C. (2001). Allgemeines Umweltbewusstsein. Zusammenstellung sozialwissenschaftlicher Items "
                 "und Skalen (ZIS).",
     "doi": "10.6102/zis208",
     "scoring": "ZIS macht keine Auswertungsangaben. Die Reliabilitäten dort beziehen sich auf drei Dimensionen: "
                "instrumentelles Naturverständnis (Items 1, 2, 4, 6), intrinsisches Naturverständnis (7, 8), Natur als "
                "Existenzgrundlage (9, 10, 11); hier je als Mittelwert. Die Items 3 und 5 laden auf zwei Faktoren "
                "(intrinsisch und Existenzgrundlage) und sind keiner Subskala zugeordnet. Keine Umpolung; ein Gesamtwert "
                "ist nicht dokumentiert.",
     "notes": GESIS_TERMS + " Nur deutsche Fassung. Im Original ein mündliches Interview mit Antwortkarten (daher „sagen "
              "Sie mir“) in zwei Blöcken: Vor den Items 9 bis 11 stand „Jetzt noch ein paar weitere Aussagen. Bitte sagen "
              "Sie mir auch diesmal zu jeder Aussage, in welchem Maße Sie zustimmen.“",
     "spec": _instrument("aub"),
     "tags": ["status:validiert", "quelle:GESIS ZIS", "art:Einstellung", "thema:Umwelt", "thema:Umweltbewusstsein",
              "fach:Soziologie"]},
    {"key": "tam_pu", "seed_version": 1, "name": "TAM: Wahrgenommene Nützlichkeit (Perceived Usefulness)", "status": "review",
     "short": "6 Aussagen, 7 Stufen; Technology Acceptance Model (Davis, 1989)",
     "description": "Grad, in dem eine Person glaubt, dass die Nutzung eines Systems ihre Arbeitsleistung verbessert. "
                    "Kernkonstrukt des Technology Acceptance Model (TAM); Reliabilität im Original .98.",
     "authors": "Davis", "year": 1989, "citation": TAM_CITATION, "doi": "10.2307/249008",
     "scoring": "Skalenwert = Mittelwert der 6 Items (1 bis 7); keine Umpolung.", "notes": TAM_NOTES,
     "spec": _instrument("tam_pu"),
     "tags": ["status:validiert", "art:Wahrnehmung", "thema:Technik", "thema:Technikakzeptanz", "fach:Wirtschaftsinformatik"]},
    {"key": "tam_peou", "seed_version": 1, "name": "TAM: Wahrgenommene Benutzerfreundlichkeit (Perceived Ease of Use)",
     "status": "review",
     "short": "6 Aussagen, 7 Stufen; Technology Acceptance Model (Davis, 1989)",
     "description": "Grad, in dem eine Person glaubt, dass die Nutzung eines Systems mühelos ist. Kernkonstrukt des "
                    "Technology Acceptance Model (TAM); Reliabilität im Original .94.",
     "authors": "Davis", "year": 1989, "citation": TAM_CITATION, "doi": "10.2307/249008",
     "scoring": "Skalenwert = Mittelwert der 6 Items (1 bis 7); keine Umpolung.", "notes": TAM_NOTES,
     "spec": _instrument("tam_peou"),
     "tags": ["status:validiert", "art:Wahrnehmung", "thema:Technik", "thema:Technikakzeptanz", "fach:Wirtschaftsinformatik"]},
    {"key": "tam_bi", "seed_version": 2, "name": "TAM: Nutzungsabsicht (Behavioral Intention)", "status": "review",
     "short": "2 Aussagen, 7 Stufen; Nutzungsabsicht nach TAM2 (Venkatesh & Davis, 2000)",
     "description": "Absicht, ein System zu nutzen; abhängige Variable im erweiterten Technology Acceptance Model (TAM2).",
     "authors": "Venkatesh & Davis", "year": 2000,
     "citation": "Venkatesh, V., & Davis, F. D. (2000). A theoretical extension of the technology acceptance model: "
                 "Four longitudinal field studies. Management Science, 46(2), 186–204.",
     "doi": "10.1287/mnsc.46.2.186.11926",
     "scoring": "Skalenwert = Mittelwert der 2 Items (1 bis 7).",
     "notes": ARTICLE_TERMS + " „[System]“ durch den Namen der Technik ersetzen (zählt nicht als Veränderung). "
              "Englischer Wortlaut wie veröffentlicht; deutsche Fassung ist eine inoffizielle Arbeitsübersetzung.",
     "spec": _instrument("tam_bi"),
     "tags": ["status:validiert", "art:Verhaltensabsicht", "thema:Technik", "thema:Technikakzeptanz", "fach:Wirtschaftsinformatik"]},
    {"key": "kuk", "seed_version": 2, "name": "Kontrollüberzeugung beim Kochen", "status": "verified",
     "short": "10 Aussagen, 6 Stufen; Selbsteinschätzung als Koch",
     "description": "Erfasst, wie sicher und kompetent sich eine Person beim Kochen erlebt: Überforderung oder Souveränität, "
                    "Freude am Ausprobieren, Anerkennung durch andere. Eingesetzt in einer Studie zur Akzeptanz von "
                    "Serious Games zum kognitiven Training im Umfeld von Ambient Assisted Living.",
     "authors": "Wittland, Brauner & Ziefle", "year": 2015,
     "citation": "Wittland, J., Brauner, P., & Ziefle, M. (2015). Serious Games for Cognitive Training in Ambient Assisted "
                 "Living Environments – A Technology Acceptance Perspective. In Human-Computer Interaction – INTERACT 2015 "
                 "(Lecture Notes in Computer Science, S. 453–471). Springer.",
     "doi": "10.1007/978-3-319-22701-6_34",
     "scoring": "Skalenwert = Mittelwert der 10 Items (1 bis 6); die negativ formulierten Items 1, 5 und 9 sind im Export "
                "bereits rekodiert, hohe Werte stehen also für hohe Kontrollüberzeugung beim Kochen.",
     "notes": CC_BY_TERMS + "Englische Fassung: Übersetzung für Askurious, von den Autoren als Fassung der Skala freigegeben.",
     "spec": _kuk_spec,
     "tags": ["status:validiert", "art:Selbstwirksamkeit", "thema:Kochen", "thema:Kontrollüberzeugung", "fach:Psychologie"]},
    {"key": "kamb", "seed_version": 2, "name": "Kognitive und Affektive Medienbewertungsskala", "status": "verified",
     "short": "8 Begriffspaare, 6 Stufen; affektive und kognitive Bewertung eines Medienbeitrags",
     "description": "Erfasst, wie ein Medienbeitrag (z. B. ein Zeitungsartikel) wahrgenommen wird, in zwei Dimensionen: "
                    "affektiv (negativ – positiv, langweilig – interessant, kaltherzig – warmherzig, beruhigend – "
                    "beängstigend) und kognitiv (professionell – amateurhaft, verwirrend – verständlich, überzeugend – "
                    "zweifelhaft, neutral – parteiisch). Entwickelt als Manipulationscheck für Framing-Experimente; "
                    "positiv und negativ geframte Artikel unterschieden sich deutlich, am stärksten affektiv.",
     "authors": "Brauner, Glawe, Vervier & Ziefle", "year": 2026,
     "citation": "Brauner, P., Glawe, F., Vervier, L., & Ziefle, M. (2026). Media Framing Moderates Risk-Benefit "
                 "Perceptions and Value Tradeoffs in Human-Robot Collaboration. arXiv:2602.12785. "
                 "https://arxiv.org/abs/2602.12785",
     "doi": "10.48550/arXiv.2602.12785",
     "scoring": "Zwei Skalenwerte als Mittelwert je vier Paare (1 bis 6): affektiv (Paare 2, 5, 6, 8) und kognitiv "
                "(1, 3, 4, 7). Die Paare mit dem positiven Pol links (1, 4, 5, 7) sind im Export bereits rekodiert; hohe "
                "Werte stehen überall für eine positive Bewertung. Cronbachs Alpha in den öffentlichen, ungefilterten "
                "Daten der Studie (n = 1512, eigene Berechnung): affektiv .71, kognitiv .68, alle acht Paare .78.",
     "notes": CC_BY_TERMS + "Deutscher Wortlaut und Polung wie erhoben (Variablenlabels der veröffentlichten Daten auf OSF). "
              "Englische Paare aus der Publikation, "
              "englische Instruktion übersetzt. „den Artikel“ in der Instruktion an das Medium anpassen (z. B. „das "
              "Video“). Paare in zufälliger Reihenfolge wie im Original.",
     "spec": _kamb_spec,
     "tags": ["status:validiert", "art:Bewertung", "thema:Medien", "thema:Framing", "thema:Medienbewertung",
              "fach:Kommunikationswissenschaft"]},
    {"key": "fitai", "seed_version": 1, "name": "Foil & Target Instrument for AI Literacy (FITAI)", "status": "verified",
     "short": "12 Aussagen, 5 Stufen; KI-Wissen: 6 zutreffende Aussagen (Targets), 6 erfundene (Foils)",
     "description": "Erfasst KI-Wissen (AI Literacy) mit Aussagen über Künstliche Intelligenz, die als zutreffend "
                    "beurteilt werden sollen: sechs sachlich korrekte (Targets, z. B. Halluzinationen, Kontextfenster, "
                    "RLHF) und sechs plausibel klingende, aber erfundene (Foils, z. B. „Digitale Osmose“, „Rekursive "
                    "Hardware-Heilung“). Die Foils trennen echtes Wissen von bloßer Zustimmungstendenz und "
                    "Selbstüberschätzung.",
     "authors": "Brauner & Glawe", "year": 2026,
     "citation": "Brauner, P., & Glawe, F. (2026). Foil & Target Instrument for AI Literacy (FITAI).",
     "doi": "",
     "scoring": "Skalenwert = Mittelwert der 12 Items (1 bis 5); die Foils (Items 7 bis 12) sind im Export bereits "
                "rekodiert, hohe Werte stehen also überall für zutreffendes Wissen. Teilskalen: Target (Items 1 bis 6, "
                "Zustimmung zu Korrektem) und Foil (Items 7 bis 12, Ablehnung von Erfundenem); ihr Unterschied zeigt eine "
                "Zustimmungstendenz.",
     "notes": CC_BY_TERMS + "Aussagen im Wortlaut des Originals; die Instruktion ist ohne Anrede umformuliert (Original: „Im Folgenden "
              "findest du … Bitte beurteile, inwieweit die Aussagen deiner Einschätzung nach zutreffen.“), damit sie für "
              "„Sie“ und „du“ passt. Englische Fassung: "
              "Übersetzung für Askurious, von den Autoren freigegeben. Nur die Endpunkte sind beschriftet. Aussagen in "
              "zufälliger Reihenfolge.",
     "spec": _fitai_spec,
     "tags": ["status:validiert", "art:Wissen", "thema:Künstliche Intelligenz", "thema:AI Literacy", "fach:HCI"]},
    {"key": "gdms", "seed_version": 1, "name": "General Decision-Making Style (GDMS)", "status": "review",
     "short": "25 Aussagen, 5 Stufen; fünf Entscheidungsstile: rational, intuitiv, abhängig, vermeidend, spontan",
     "description": "Erfasst, wie eine Person gewöhnlich wichtige Entscheidungen trifft, in fünf Stilen mit je fünf "
                    "Items: rational (Informationen suchen, Optionen logisch abwägen), intuitiv (Gefühl und Ahnung), "
                    "abhängig (Rat und Unterstützung anderer), vermeidend (Aufschieben) und spontan (schnell, aus dem "
                    "Moment). Die Stile schließen sich nicht aus; Personen nutzen meist mehrere. Interne Konsistenz im "
                    "Original .68 bis .94.",
     "authors": "Scott & Bruce", "year": 1995,
     "citation": "Scott, S. G., & Bruce, R. A. (1995). Decision-making style: The development and assessment of a new "
                 "measure. Educational and Psychological Measurement, 55(5), 818–831.",
     "doi": "10.1177/0013164495055005017",
     "scoring": "Fünf Skalenwerte als Mittelwert je fünf Items (1 bis 5): rational (Items 1, 6, 11, 16, 21), intuitiv "
                "(2, 7, 12, 17, 22), abhängig (3, 8, 13, 18, 23), vermeidend (4, 9, 14, 19, 24), spontan (5, 10, 15, 20, "
                "25). Keine Umpolung; kein Gesamtwert, die Stile werden getrennt ausgewertet.",
     "notes": "© 1995 SAGE Publications. Eintrag in APA PsycTests: https://doi.org/10.1037/t08399-000. Neue Nutzung und Katalogzugriff nur mit dokumentierter, vom Betreiber geprüfter Berechtigung für "
              "Person, Studie und Sprachfassung. Anmeldung allein genügt nicht; keine allgemeine Weitergabe. "
              "Quelle immer angeben. Nur Englisch: Eine validierte deutsche Fassung der vollständigen Skala gibt es nicht (Fischer, "
              "Soyez & Gurtner, 2015, haben eine 13-Item-Fassung für Patientenentscheidungen validiert, die nicht "
              "dieselbe Skala ist); eine eigene Übersetzung ist nicht validiert. Nur die Endpunkte sind beschriftet "
              "(Original: 5 Stufen von „strongly disagree“ bis „strongly agree“). Aussagen in zufälliger Reihenfolge; "
              "im Original wechseln sich die Stile der Reihe nach ab.",
     "spec": _instrument("gdms"),
     "tags": ["status:validiert", "quelle:APA PsycTests", "art:Persönlichkeit", "thema:Entscheidungsstil",
              "fach:Psychologie"]},
]

# the demographic standard questions of the RatSWD (2023), one construct per item (app/ratswd.py)
SEEDS += [{k: v for k, v in seed.items() if k != "reference"} for seed in ratswd.SEEDS]

# What a construct measures, for meta-analyses (facet "Bezug"): a lasting trait of the person (age, personality,
# technology readiness), something about the person that can change (knowledge, mood, the care taken in this
# survey), or the rating of the object shown (a technology, a media item), which differs with every object.
REFERENCE = {
    "gender": "Person: stabil", "age": "Person: stabil", "bfi10": "Person: stabil",
    "kut": "Person: stabil", "asku": "Person: stabil", "ie4": "Person: stabil",
    "kusiv3": "Person: stabil", "i8": "Person: stabil", "nfck": "Person: stabil",
    "tb": "Person: stabil", "r1": "Person: stabil", "kuk": "Person: stabil",
    "aub": "Person: veränderlich", "sorgfalt": "Person: veränderlich", "fitai": "Person: veränderlich",
    "tam_pu": "Gegenstand", "tam_peou": "Gegenstand", "tam_bi": "Gegenstand", "kamb": "Gegenstand",
    "gdms": "Person: stabil",
    **{seed["key"]: seed["reference"] for seed in ratswd.SEEDS},
}
for _seed in SEEDS:
    _seed["tags"] = _seed["tags"] + ["bezug:" + REFERENCE[_seed["key"]]]


# ---- the seed constructs in English (researchers' pages in English, app/lang.py) ----
# Name, short line, description, scoring and notes of each seed construct for researchers who read the pages in
# English. The item texts and answer scales stay as published (they are survey content, in spec["texts"]); the
# German fields above stay the stored ones. A field shows in English only while the stored text is still the
# seed's German one (an operator who changed it in the database has written something of their own).

GESIS_TERMS_EN = ("GESIS/ZIS: free of charge for non-commercial research. For other purposes or a form other than the "
                  "documented one, ask the authors for permission. Cite the ZIS documentation as the source in all "
                  "resulting work.")
ARTICLE_TERMS_EN = ("No general use or publication permission is documented for the wording or its translations. "
                    "Stating “non-commercial research and teaching” alone does not replace rights evidence. New use requires "
                    "operator-checked evidence covering the specific study, account and language version. "
                    "Always cite the source.")
TAM_NOTES_EN = (ARTICLE_TERMS_EN + " Replace “[System]” with the name of the technology (does not count as a change). English wording as "
                "published; the German version is an unofficial working translation. Davis (1989) used 7-point scales "
                "(likely/unlikely; in study 1 agree/disagree), with “likely” on the left in the original; here 1 = "
                "extremely unlikely, 7 = extremely likely.")

SEEDS_EN = {
    "gender": {
        "name": "Gender",
        "short": "Female, male, diverse, self-description, prefer not to disclose",
        "description": "Gender identity following the recommendations of Spiel et al. for more inclusive gender "
                       "questions: female, male, diverse, “Prefer to self-describe:” with a text field, and “prefer not "
                       "to disclose”. The self-description can be left out per element; that does not count as a "
                       "deviation.",
        "notes": "Own wording of Askurious following the recommendations of Spiel, Haimson & Lottridge (2019)."},
    "age": {
        "name": "Age",
        "short": "Age in years (whole number, 10–120)",
        "description": "Age in whole years as free text with a number check (10 to 120), “years” after the field. Asked "
                       "the same way everywhere so that surveys can be combined in a meta-analysis.",
        "scoring": "Whole years; no recoding.",
        "notes": "Own wording of Askurious."},
    "sorgfalt": {
        "name": "Care taken in participating (self-report)",
        "short": "One question at the end: should we use your answers? Yes or no",
        "description": "Self-report on the participant's own participation, asked at the end of the survey. Anyone who "
                       "answers “no” says they did not answer attentively. People who describe themselves as serious "
                       "participants answer more consistently and with higher predictive validity (Aust et al., 2013). "
                       "The page “Quality assurance” counts the “no” answers.",
        "scoring": "1 = use the answers, 0 = do not use them; no recoding.",
        "notes": "Own wording based on the self-report in Meade & Craig (2012) and the seriousness check in Aust et al. "
                 "(2013), not an item taken word for word. The addition that the answer has no consequences (payment, "
                 "prize draw) matters; otherwise nobody answers “no”."},
    "bfi10": {
        "name": "Big Five Inventory (BFI-10)",
        "short": "10 statements, 5 points; 2 items each for extraversion, agreeableness, conscientiousness, neuroticism, "
                 "openness",
        "description": "Ultra-short form of the Big Five Inventory for surveys with little time. Measures the five "
                       "personality dimensions with one positively and one negatively keyed item each.",
        "scoring": "Scale scores = mean per subscale (E: 1, 6; A: 2, 7; C: 3, 8; N: 4, 9; O: 5, 10); the negatively "
                   "keyed items 1, 3, 4, 5 and 7 are already recoded in the export.",
        "notes": GESIS_TERMS_EN + " German wording checked against ZIS; English instruction and endpoints after "
                                  "Rammstedt & John (2007). The English items come from the Big Five Inventory (John, "
                                  "Donahue & Kentle, 1991; © Oliver P. John), free to use for non-commercial research."},
    "kut": {
        "name": "Locus of control in dealing with technology (KUT)",
        "short": "8 statements, 5 points; subjective control over technical devices",
        "description": "Measures to what extent people believe they can solve technical problems through their own "
                       "actions (internal) or feel at the mercy of technology.",
        "scoring": "Scale score = mean of the 8 items; the reverse-coded items (2, 3, 5, 6) are already recoded in the "
                   "export.",
        "notes": ARTICLE_TERMS_EN + " English version: unofficial working translation."},
    "asku": {
        "name": "General Self-Efficacy Short Scale (ASKU)",
        "short": "3 statements, 5 points; expectation of coping with difficulties on one's own",
        "description": "Economical instrument measuring the individual expectation of being able to deal with "
                       "difficulties and obstacles in daily life. McDonald's omega .81 to .87.",
        "scoring": "Scale score = mean of the 3 items (1 to 5).",
        "notes": GESIS_TERMS_EN + " English version (a source of its own, terms: CC BY-NC-SA 4.0, adaptations only under the same licence): GSE-3: Doll, E. S., Nießen, D., Schmidt, I., Rammstedt, B., & Lechner, C. M. (2021). The General Self-Efficacy Short Scale-3 (GSE-3). ZIS. https://doi.org/10.6102/zis294"},
    "ie4": {
        "name": "Internal-External Locus of Control 4 (IE-4)",
        "short": "4 statements, 5 points; 2 items each for internal and external locus of control",
        "description": "Short scale on locus of control: how much do people experience events as a result of their own "
                       "actions (internal) or as fate, chance or determined by others (external)?",
        "scoring": "Two scale scores: mean of items 1 and 2 (internal, IKÜ) and of items 3 and 4 (external, EKÜ); no "
                   "reverse coding.",
        "notes": GESIS_TERMS_EN + " English version: Nießen, D., Schmidt, I., Groskurth, K., Rammstedt, B., & Lechner, C. M. (2022). The Internal–External Locus of Control Short Scale–4 (IE-4): A comprehensive validation of the English-language adaptation. PLOS ONE, 17(7), e0271289. https://doi.org/10.1371/journal.pone.0271289; the article is licensed under CC BY 4.0, the ZIS terms apply to using the items."},
    "kusiv3": {
        "name": "Interpersonal Trust Short Scale (KUSIV3)",
        "short": "3 statements, 5 points; trust in other people as a personality trait",
        "description": "Short scale measuring interpersonal trust with three items (about 30 seconds to answer).",
        "scoring": "Scale score = mean of the 3 items; item 2 is worded negatively and already recoded in the export "
                   "(Nießen et al., 2020).",
        "notes": GESIS_TERMS_EN + " English version: Nießen, D., Beierlein, C., Rammstedt, B., & Lechner, C. M. (2020). An English-language adaptation of the Interpersonal Trust Short Scale (KUSIV3). Measurement Instruments for the Social Sciences, 2, 10. https://doi.org/10.1186/s42409-020-00016-1; the article is licensed under CC BY 4.0, the ZIS terms apply to using the items."},
    "i8": {
        "name": "Impulsive Behavior Short Scale–8 (I-8)",
        "short": "8 statements, 5 points; urgency, premeditation, perseverance, sensation seeking",
        "description": "Short scale on four facets of impulsive behaviour with two items each. High values on urgency "
                       "and sensation seeking stand for high impulsivity, on premeditation and perseverance for low "
                       "impulsivity.",
        "scoring": "Four scale scores as the mean of two items each: urgency (Dringlichkeit; 1, 2), premeditation "
                   "(Absicht; 3, 4), perseverance (Ausdauer; 5, 6), sensation seeking (Risikobereitschaft; 7, 8); no "
                   "reverse coding.",
        "notes": GESIS_TERMS_EN + " English version: Groskurth, K., Nießen, D., Rammstedt, B., & Lechner, C. M. (2022). The Impulsive Behavior Short Scale–8 (I-8): A comprehensive validation of the English-language adaptation. PLOS ONE, 17(9), e0273801. https://doi.org/10.1371/journal.pone.0273801; the article is licensed under CC BY 4.0, the ZIS terms apply to using the items."},
    "nfck": {
        "name": "Need for Cognition Short Scale (NFC-K)",
        "short": "4 statements, 7 points; enjoyment of and engagement in thinking",
        "description": "Measures need for cognition with four items on the facets engagement and enjoyment. Suitable "
                       "from adolescence on; Cronbach's alpha .51 to .54 (low with four items).",
        "scoring": "Scale score = mean of the 4 items (1 to 7); items 1 and 4 are already recoded in the export.",
        "notes": GESIS_TERMS_EN + " German version only: ZIS documents no answer scale for the English items."},
    "tb": {
        "name": "Technology Commitment Short Scale (TB)",
        "short": "12 statements, 5 points; 4 items each for technology acceptance, technology competence beliefs and "
                 "technology control beliefs",
        "description": "Measures the readiness to deal with modern technology (technology commitment) through three "
                       "facets. Usable in all age groups, developed especially for older adults. Cronbach's alpha .84 "
                       "(technology acceptance), .84 (technology competence), .74 (technology control), whole scale .84.",
        "scoring": "Three scale scores as the mean of four items each: technology acceptance (Technikakzeptanz, 1 to 4), "
                   "technology competence beliefs (Technikkompetenzüberzeugungen, 5 to 8), technology control beliefs "
                   "(Technikkontrollüberzeugungen, 9 to 12); the negatively keyed items 5 to 8 are already recoded in "
                   "the export, so high values stand for high technology commitment throughout. According to ZIS, a "
                   "total score over all 12 items is also permitted (described there as a sum score, values in the "
                   "range 1 to 5).",
        "notes": GESIS_TERMS_EN + " German version only: ZIS documents no English items."},
    "r1": {
        "name": "Risk Proneness Short Scale (R-1)",
        "short": "1 question, 7 points; general tendency to take risks",
        "description": "Measures risk proneness, the tendency to take or avoid risks, with a single question (under 20 "
                       "seconds). Developed for the German-speaking general population aged 18 and over; retest "
                       "reliability .74 (English version .76).",
        "scoring": "Scale score = the answer to the one question (1 = not at all willing to take risks to 7 = very "
                   "willing to take risks); no reverse coding. A single item has no internal consistency (no "
                   "Cronbach's alpha).",
        "notes": GESIS_TERMS_EN + " The English version (zis286) is licensed under CC BY-NC-SA 4.0; adapted versions may be passed on only under the same licence. Only the endpoints "
                                  "of the scale are labelled."},
    "aub": {
        "name": "General Environmental Awareness",
        "short": "11 statements, 5 points; intrinsic and instrumental understanding of nature, nature as the basis of "
                 "existence",
        "description": "Measures environmental awareness as a general belief or world view in three dimensions. "
                       "Developed with the survey “Umweltbewusstsein in Deutschland 1996” (environmental awareness in "
                       "Germany 1996). Cronbach's alpha .46 (intrinsic understanding of nature), .61 (instrumental "
                       "understanding of nature), .68 (nature as the basis of existence), so partly low.",
        "scoring": "ZIS gives no scoring instructions. The reliabilities there refer to three dimensions: instrumental "
                   "understanding of nature (items 1, 2, 4, 6), intrinsic understanding of nature (7, 8), nature as the "
                   "basis of existence (9, 10, 11); here each as a mean. Items 3 and 5 load on two factors (intrinsic "
                   "and basis of existence) and belong to no subscale. No reverse coding; a total score is not "
                   "documented.",
        "notes": GESIS_TERMS_EN + " German version only. Originally an oral interview with answer cards (hence “sagen "
                                  "Sie mir”, “tell me”) in two blocks: before items 9 to 11 the interviewer said “Jetzt "
                                  "noch ein paar weitere Aussagen. Bitte sagen Sie mir auch diesmal zu jeder Aussage, in "
                                  "welchem Maße Sie zustimmen.” (now a few more statements; again, please tell me for "
                                  "each statement how far you agree)."},
    "tam_pu": {
        "name": "TAM: Perceived Usefulness",
        "short": "6 statements, 7 points; Technology Acceptance Model (Davis, 1989)",
        "description": "Degree to which a person believes that using a system improves their job performance. Core "
                       "construct of the Technology Acceptance Model (TAM); reliability in the original .98.",
        "scoring": "Scale score = mean of the 6 items (1 to 7); no reverse coding.",
        "notes": TAM_NOTES_EN},
    "tam_peou": {
        "name": "TAM: Perceived Ease of Use",
        "short": "6 statements, 7 points; Technology Acceptance Model (Davis, 1989)",
        "description": "Degree to which a person believes that using a system is free of effort. Core construct of the "
                       "Technology Acceptance Model (TAM); reliability in the original .94.",
        "scoring": "Scale score = mean of the 6 items (1 to 7); no reverse coding.",
        "notes": TAM_NOTES_EN},
    "tam_bi": {
        "name": "TAM: Behavioral Intention",
        "short": "2 statements, 7 points; intention to use following TAM2 (Venkatesh & Davis, 2000)",
        "description": "Intention to use a system; dependent variable in the extended Technology Acceptance Model (TAM2).",
        "scoring": "Scale score = mean of the 2 items (1 to 7).",
        "notes": ARTICLE_TERMS_EN + " Replace “[System]” with the name of the technology (does not count as a change). "
                 "English wording as published; the German version is an unofficial working translation."},
    "kuk": {
        "name": "Locus of control in cooking",
        "short": "10 statements, 6 points; self-assessment as a cook",
        "description": "Measures how confident and competent a person feels when cooking: overwhelmed or in command, "
                       "enjoying trying things out, recognition from others. Used in a study on the acceptance of "
                       "serious games for cognitive training in ambient assisted living.",
        "scoring": "Scale score = mean of the 10 items (1 to 6); the negatively worded items 1, 5 and 9 are already "
                   "recoded in the export, so high values stand for a strong sense of control in cooking.",
        "notes": CC_BY_TERMS_EN + "English version: translation for Askurious, approved by the authors as a version of the scale."},
    "kamb": {
        "name": "Cognitive and Affective Media Evaluation Scale",
        "short": "8 word pairs, 6 points; affective and cognitive evaluation of a media item",
        "description": "Measures how a media item (e.g. a newspaper article) is perceived, in two dimensions: affective "
                       "(negative – positive, boring – interesting, cold-hearted – warm-hearted, reassuring – "
                       "frightening) and cognitive (professional – amateurish, confusing – understandable, convincing – "
                       "doubtful, neutral – biased). Developed as a manipulation check for framing experiments; "
                       "positively and negatively framed articles differed clearly, most strongly on the affective "
                       "dimension.",
        "scoring": "Two scale scores as the mean of four pairs each (1 to 6): affective (pairs 2, 5, 6, 8) and cognitive "
                   "(1, 3, 4, 7). The pairs with the positive pole on the left (1, 4, 5, 7) are already recoded in the "
                   "export; high values stand for a positive evaluation throughout. Cronbach's alpha in the study's "
                   "public, unfiltered data (n = 1512, own calculation): affective .71, cognitive .68, all eight pairs "
                   ".78.",
        "notes": CC_BY_TERMS_EN + "German wording and polarity as collected (variable labels of the published data on OSF). English pairs "
                 "from the publication, English instruction translated. Adapt “the article” in the instruction to the "
                 "medium (e.g. “the video”). Pairs in random order as in the original."},
    "fitai": {
        "name": "Foil & Target Instrument for AI Literacy (FITAI)",
        "short": "12 statements, 5 points; AI knowledge: 6 true statements (targets), 6 invented ones (foils)",
        "description": "Measures AI knowledge (AI literacy) with statements about artificial intelligence to be judged as "
                       "true or not: six factually correct ones (targets, e.g. hallucinations, context window, RLHF) and "
                       "six plausible-sounding but invented ones (foils, e.g. “digital osmosis”, “recursive hardware "
                       "healing”). The foils separate real knowledge from mere acquiescence and overconfidence.",
        "scoring": "Scale score = mean of the 12 items (1 to 5); the foils (items 7 to 12) are already recoded in the "
                   "export, so high values stand for correct knowledge throughout. Subscales: target (items 1 to 6, "
                   "agreeing with what is correct) and foil (items 7 to 12, rejecting what is invented); their "
                   "difference shows acquiescence.",
        "notes": CC_BY_TERMS_EN + "Statements in the original wording; the instruction is rephrased without a form of address (original: "
                 "“Im Folgenden findest du … Bitte beurteile, inwieweit die Aussagen deiner Einschätzung nach "
                 "zutreffen.”) so that it fits both “Sie” and “du”. English version: translation for Askurious, approved "
                 "by the authors. Only the endpoints are labelled. Statements in random order."},
    "gdms": {
        "name": "General Decision-Making Style (GDMS)",
        "short": "25 statements, 5 points; five decision-making styles: rational, intuitive, dependent, avoidant, "
                 "spontaneous",
        "description": "Measures how a person usually makes important decisions, in five styles with five items each: "
                       "rational (searching for information, weighing options logically), intuitive (feelings and "
                       "hunches), dependent (advice and support of others), avoidant (putting decisions off) and "
                       "spontaneous (quick, on the spur of the moment). The styles do not exclude each other; most "
                       "people use several. Internal consistency in the original .68 to .94.",
        "scoring": "Five scale scores as the mean of five items each (1 to 5): rational (items 1, 6, 11, 16, 21), "
                   "intuitive (2, 7, 12, 17, 22), dependent (3, 8, 13, 18, 23), avoidant (4, 9, 14, 19, 24), spontaneous "
                   "(5, 10, 15, 20, 25). No reversed items; no total score, the styles are analysed separately.",
        "notes": "© 1995 SAGE Publications. Record in APA PsycTests: https://doi.org/10.1037/t08399-000. New use and catalogue access require documented, operator-checked permission covering the "
                 "account, study and language version. Signing in alone is insufficient; no general distribution. "
                 "Always cite the source. English only: there is no validated German version of the full scale (Fischer, Soyez & "
                 "Gurtner, 2015, validated a 13-item version for patient decisions, which is not the same scale); a "
                 "translation of your own is not validated. Only the endpoints are labelled (original: 5 points from "
                 "“strongly disagree” to “strongly agree”). Statements in random order; in the original the styles "
                 "take turns."},
}

SEEDS_EN.update(ratswd.EN)

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
    "Psychologie": "Psychology", "Soziologie": "Sociology", "Wirtschaftsinformatik": "Information systems",
    "Kommunikationswissenschaft": "Communication science",
    "Bildung": "Education", "Erwerbstätigkeit": "Employment", "Beruf": "Occupation", "Einkommen": "Income",
    "Staatsangehörigkeit": "Citizenship", "Haushalt": "Household", "Familienstand": "Marital status",
    "Person: stabil": "Person: stable", "Person: veränderlich": "Person: changeable", "Gegenstand": "Object rated",
}
SHOWN_FIELDS = ("name", "short", "description", "scoring", "notes")
_SEED_BY_KEY = {seed["key"]: seed for seed in SEEDS}


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


def seed_name(key):
    """The name of a construct of the toolbox in the language of the page, as its page shows it: "Sorgfalt der
    Teilnahme (Selbstauskunft)" or "Care taken in participating (self-report)" (UI/UX audit 2026-10-07, W115)."""
    seed = next((s for s in SEEDS if s["key"] == key), None)
    if seed is None:
        return key
    return (SEEDS_EN.get(key) or {}).get("name", seed["name"]) if english() else seed["name"]


def _seed():
    changed = _retire()
    for seed in SEEDS:
        c = db.session.scalar(db.select(Construct).filter_by(key=seed["key"]))
        if c and c.seed_version >= seed["seed_version"]:
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
    return db.select(Construct).filter(Construct.visibility == "public")


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
    return key not in ratswd.NOT_POOLED


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
