"""Categorising open answers (concept offene-antworten-kodieren-konzept.md, proposal 4 of 2026-10-09; K1 to K7 as
recommended, Ralf 2026-10-10). Stage 1: the category scheme, assigning categories, the results and the export.

Experimental (Ralf, 2026-10-10): the feature "coding" (app/experimental.py); a survey that has categories keeps
them for everyone on it. Where (K1): every open answer on the results page that can be categorised gets the button "Kategorisieren"; it opens
a page of its own for that column, without a new tab or menu row (decision "No extra menu band"). Who (K2): the role
"Bearbeiten" builds the scheme and assigns; "Ansehen" sees only the frequencies on the results page.

What can be categorised: the open columns (ItemType.open_columns) of free text (not numbers or e-mail addresses),
the texts typed next to "Anderes, und zwar", the gap text and the short-text columns of the side-by-side matrix;
outside loops. A column is named by its key: the item's id and the rest of the column after the item's code ("12",
"12_a3_text"), so renaming the variable keeps its categories.

The scheme (survey.codings[key]): categories with a name, a definition and an anchor example, as in a coding guide
after Mayring (2022), optionally grouped under a main category; "Sonstiges" (98) and "nicht verwertbar" (99) are
there from the start and cannot be removed. Several categories per answer are allowed unless the tick "nur eine
Kategorie je Antwort" is set (K3).

The assignments (response.coding[key] = [category ids]) belong to the participation (K7): they go with a deletion
request, a retention period and the survey. Answers are shown once per text (spaces and upper and lower case
aside): one click codes every participation that gave that text. Only participations that are no longer under way
are coded, so a researcher's save never meets a participant's page on the same row (Response.version); new
answers from the field show up as "noch offen".

Export: one 0/1 column per category, <column>_k<id>, or with "nur eine" one column <column>_kat holding the id;
empty = no answer or not categorised yet. The codebook carries the definitions, and the coding guide comes as a
file of its own (kodierleitfaden.md) in the export of the codebook."""
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, experimental
from app.extensions import db
from app.lang import de_en
from app.models import Response, Survey

bp = Blueprint("coding", __name__, url_prefix="/surveys")

TYPES = ("text_input", "single_choice", "multiple_choice", "cloze", "side_by_side", "association")
ASSOC = "_assoc"  # the column that stands for all words of an association task (their slots are its units)
OTHER, UNUSABLE = 98, 99  # the two categories every scheme has, last in every list
FIXED = (OTHER, UNUSABLE)
MAX_CATS = 40  # own categories per scheme (ids 1 to 97)
MAX_NAME, MAX_TEXT, MAX_GROUP = 80, 600, 60
MAYRING = experimental.MAYRING


# --- what can be categorised -----------------------------------------------------------------------------------------

def columns(item):
    """[(column, label)] of one element that can be categorised; [] for the others. An association task is one
    column "<code>_assoc" over all its words (each word is coded on its own, the person gets the categories of all of
    them). In a loop each pass is coded on its own (its answer is a unit), the export has the categories per pass."""
    from app.items.base import plain

    if item.type not in TYPES:
        return []
    if item.type == "text_input" and item.cfg.get("validation") == "email":
        return []
    if item.type == "association":
        return [(f"{item.code}{ASSOC}", "Assoziationen: alle genannten Begriffe")]
    opens = set(item.kind.open_columns(item))
    return [(c, plain(label)) for c, label in item.kind.export_columns(item) if c in opens]


def passes(item):
    """[(pass number, loop row id)] of an element in a loop, [] outside loops."""
    from app import export

    return export._loop_rows(item) or []


def unit_plan(item, column, key):
    """Where the units of a column lie in a participation: [(sub key, answer key, export column)]. One unit outside
    loops (the sub key is the key itself); one per pass in a loop ("<key>#<row id>"); one per word of an association
    task ("<key>#<slot>"). response.coding holds the categories of every unit under its sub key."""
    rows = passes(item)
    if rows:
        return [(f"{key}#{rid}", f"{item.id}#{rid}", column) for _, rid in rows]
    if item.type == "association" and column.endswith(ASSOC):
        return [(f"{key}#{k}", str(item.id), f"{item.code}_{k}") for k in item.kind.slots(item)]
    return [(key, str(item.id), column)]


def units(item, uplan, response):
    """[(sub key, text)] of one participation, the text "" where there is none."""
    answers, values = response.answers or {}, {}
    out = []
    for sub, akey, col in uplan:
        if akey not in values:
            values[akey] = item.kind.export_values(item, answers.get(akey))
        value = values[akey].get(col)
        out.append((sub, "" if value in (None, "") else str(value).strip()))
    return out


def base_of(name):
    """The sub key a key of response.coding belongs to ("12#r3~2" -> "12#r3")."""
    return name.split("~", 1)[0]


def subkeys(coding, key):
    """The sub keys of `key` that a coding dict holds anything for."""
    return {base_of(k) for k in coding or {} if base_of(k) == key or base_of(k).startswith(key + "#")}


def key_of(item, column):
    return f"{item.id}{column[len(item.code):]}"


def targets(survey):
    """[(item, column, label, key)] of every column of the survey that can be categorised."""
    from app import export

    return [(item, col, label, key_of(item, col)) for item in export._items(survey) for col, label in columns(item)]


def find(survey, key):
    """(item, column, label) of `key`, or None."""
    return next(((i, c, label) for i, c, label, k in targets(survey) if k == key), None)


def scheme(survey, key):
    """The scheme of `key` with its categories in their order (own ones first, then 98 and 99), or None."""
    found = (survey.codings or {}).get(key)
    if not isinstance(found, dict):
        return None
    cats = [c for c in found.get("cats") or [] if isinstance(c, dict) and isinstance(c.get("id"), int)]
    cats.sort(key=lambda c: (c["id"] in FIXED, c["id"] if c["id"] in FIXED else 0))
    return {**found, "single": bool(found.get("single")), "cats": cats, "next": int(found.get("next") or 1)}


def plan(survey, loops=False, hide=None):
    """{item id: [(column, label, key, scheme)]} of the columns that have a scheme: the export adds their category
    columns (export.columns, export._rows, export.codebook). Outside loops; loops=True: only those in loops (their
    columns carry the pass, _<n>; the group comparison and the dashboards take the others only).

    Not the columns the person of the request codes blind as the second coder (hidden(): audit 2026-10-10, B-4): their
    export, codebook, coding guide, group comparison and dashboards show these answers without categories (hide: the
    keys to leave out instead, e.g. running() for a dashboard, whose state is one file for every viewer).
    An association task with "nur eine" has one category per word, a person several (B-2): its scheme here counts as
    several per answer (0/1 per category, as the results read it), with "single_word" and the word columns ("words")
    that get one column <word>_kat each in the export."""
    out = {}
    if not survey.codings:
        return out
    blind = hidden(survey) if hide is None else hide
    for item, col, label, key in targets(survey):
        if bool(passes(item)) != loops or key in blind:
            continue
        found = scheme(survey, key)
        if found:
            if found["single"] and item.type == "association" and col.endswith(ASSOC) and not passes(item):
                found = {**found, "single": False, "single_word": True,
                         "words": [f"{item.code}_{k}" for k in item.kind.slots(item)]}
            out.setdefault(item.id, []).append((col, label, key, found))
    return out


def hidden(survey, user=None):
    """The keys whose categories the person of this request may not see: those they code blind as the second coder
    (coding_check.blind_for). `user`: the person a request acts for without being signed in (a data access token:
    data_access._file sets it, g.coding_user); otherwise the person signed in. A guest (a public dashboard link and
    any other request without a person) sees no category of a column while a blind second coding of it runs, so the
    second coder cannot read the first coding through a link either (audit 2026-10-10, R1). [] outside a request."""
    from flask import g, has_request_context

    if not survey.codings or not has_request_context():
        return []
    user = user if user is not None else g.get("coding_user")
    if user is None and getattr(current_user, "is_authenticated", False):
        user = current_user
    if user is None:
        return running(survey)
    return sorted(k for k, v in survey.codings.items()
                  if isinstance(v, dict) and isinstance(v.get("second"), dict) and coding_check.blind_for(survey, k, user))


def running(survey):
    """The keys with a blind second coding under way (not finished): hidden from guests and on every dashboard."""
    return sorted(k for k, v in (survey.codings or {}).items()
                  if isinstance(v, dict) and coding_check.state(survey, k) and not coding_check.state(survey, k).get("done"))


def new_scheme():
    return {"single": False, "next": 1, "cats": [
        {"id": OTHER, "name": de_en("Sonstiges", "Other"), "definition": "", "example": "", "group": "", "fixed": True},
        {"id": UNUSABLE, "name": de_en("nicht verwertbar", "not usable"),
         "definition": de_en("leer, unverständlich oder ohne Bezug zur Frage", "empty, unintelligible or not about the question"),
         "example": "", "group": "", "fixed": True}]}


# Keys of survey.codings beside the columns: VERSION counts every change of the schemes (audit 2026-10-10, L-9),
# AI_OK holds who confirmed that participants were told about the AI coding help (B-1).
VERSION, AI_OK = "_v", "_ai"


class Conflict(Exception):
    """Someone else saved the schemes since this request (or the page sending it) read them: nothing is saved."""


def conflict_text():
    return de_en("Inzwischen hat jemand anderes die Kategorien geändert. Ihre letzte Änderung wurde nicht gespeichert; "
                 "die Seite zeigt jetzt den neuen Stand.",
                 "Meanwhile someone else changed the categories. Your last change was not saved; the page now shows "
                 "the new state.")


def version(codings):
    """The version counter of survey.codings (0 before the first change that counted)."""
    value = (codings or {}).get(VERSION)
    return value if isinstance(value, int) else 0


def check_base(survey, form):
    """A form carries `base`, the version of the schemes when its page was drawn: raises Conflict if someone saved
    in between. Forms without it (scripts, tests) are saved as before."""
    sent = str(form.get("base") or "")
    if sent and sent != str(version(survey.codings)):
        raise Conflict()


def _store(survey, key, found):
    """Writes one entry of survey.codings, counting the version up. survey.codings is one JSON value: two people at
    work at once (first and second coder) would overwrite each other's change without a word (audit 2026-10-10, L-9),
    so the version the database holds now must be the one this request read; otherwise Conflict. (Within one request
    the database holds what the request wrote before: the query flushes it.) The row is read with FOR UPDATE, so a
    second request at the same moment waits for this one's commit and then sees its version instead of passing the
    check too (audit 2026-10-21, F10)."""
    now = db.session.execute(db.select(Survey.codings).where(Survey.id == survey.id).with_for_update()).scalar()
    if version(now) != version(survey.codings):
        raise Conflict()
    survey.codings = {**(survey.codings or {}), key: found, VERSION: version(survey.codings) + 1}


# --- the answers -------------------------------------------------------------------------------------------------

def norm(text):
    """Texts that differ only in spaces and upper and lower case are one answer."""
    return " ".join(str(text).split()).casefold()


# The columns the coding reads of a participation (performance audit 2026-10-10, P2): not layout, paradata, params,
# quota and the other JSON columns, which made up most of the time of a click. What the seal chain watches
# (seal.WATCHED, "external" among them) and the version counter are loaded too, else its after_flush listener and
# the version check would load them row by row.
COLUMNS = ("id", "survey_id", "answers", "coding", "pid", "source", "status", "is_preview", "completed_at",
           "language", "qversion", "external", "version")


def _responses(survey, real=False):
    """The participations whose answers are coded: every one that is no longer under way, of all sources; real: only
    those of the source "real" (the coding guide, the second coding and the AI help: audit 2026-10-10, B-1, B-5).
    Only the columns the coding reads (COLUMNS)."""
    from sqlalchemy.orm import load_only

    query = db.select(Response).options(load_only(*(getattr(Response, c) for c in COLUMNS))).filter(
        Response.survey_id == survey.id, Response.status != Response.ACTIVE)
    if real:
        query = query.filter(Response.source == "real")
    return db.session.scalars(query.order_by(Response.id)).all()


def _text(item, column, response):
    value = item.kind.export_values(item, (response.answers or {}).get(str(item.id))).get(column)
    return "" if value in (None, "") else str(value).strip()


# What response.coding holds besides the categories of a column (key): "<key>~2" the categories of the second
# coder, "<key>~1" the first coding of the sample as it was when the second coder finished (the reliability compares
# these two, so clarifying a disagreement afterwards does not raise it), "<key>~ai" what the AI suggests, "<key>~ki" 1 =
# the categories were taken over from the AI suggestion, "<key>~r" 1 = to be checked again (its category was split).
SECOND, FROZEN, SUGGESTED, FROM_AI, RECHECK = "~2", "~1", "~ai", "~ki", "~r"
SLOTS = ("", SECOND, FROZEN, SUGGESTED)  # the slots holding category ids


def answers(survey, item, column, key, real=False):
    """The answers of the column, one per text, the most frequent first: [{"norm", "text", "n", "cats", "mixed",
    "sources", "open", "ai", "from_ai", "recheck", "second", "ids"}]. A text is counted once per unit (a word of an
    association task, a pass of a loop). cats: the categories of the first coded unit with this text, None while none
    is coded; mixed: units with this text carry different categories (e.g. after a scheme was changed); ai: the AI's
    suggestion; second: the second coder's categories; ids: [participation id, sub key] of every unit; pids: [pid or
    id, sub key] of every unit (what the sample of the second coding keeps). real: only participations of the source
    "real"."""
    groups = {}
    uplan = unit_plan(item, column, key)
    for r in _responses(survey, real):
        coding = r.coding or {}
        for sub, text in units(item, uplan, r):
            if not text:
                continue
            g = groups.setdefault(norm(text), {"norm": norm(text), "text": text, "n": 0, "cats": None, "mixed": False,
                                               "sources": set(), "open": 0, "ai": None, "from_ai": False,
                                               "recheck": False, "second": None, "ids": [], "pids": []})
            g["n"] += 1
            g["ids"].append([r.id, sub])
            g["pids"].append([r.pid or r.id, sub])
            g["sources"].add(r.source)
            if g["ai"] is None and coding.get(sub + SUGGESTED) is not None:
                g["ai"] = sorted(coding[sub + SUGGESTED])
            if g["second"] is None and coding.get(sub + SECOND) is not None:
                g["second"] = sorted(coding[sub + SECOND])
            g["from_ai"] = g["from_ai"] or bool(coding.get(sub + FROM_AI))
            g["recheck"] = g["recheck"] or bool(coding.get(sub + RECHECK))
            got = coding.get(sub)
            if got is None:
                g["open"] += 1
                continue
            got = sorted(got)
            if g["cats"] is None:
                g["cats"] = got
            elif g["cats"] != got:
                g["mixed"] = True
    out = sorted(groups.values(), key=lambda g: (-g["n"], g["norm"]))
    for g in out:
        g["mixed"] = g["mixed"] or (g["cats"] is not None and g["open"] > 0)
    return out


def clean(found, cats):
    """The categories `cats` as `assign` takes them: only ids of the scheme, sorted, each once; None when the scheme
    allows only one and more are given."""
    known = {c["id"] for c in found["cats"]}
    cats = sorted({c for c in cats if c in known})
    return None if found["single"] and len(cats) > 1 else cats


def assign(survey, item, column, key, text_norm, cats, slot="", from_ai=False, only_open=False):
    """Gives every unit of a finished participation whose answer is `text_norm` the categories `cats` (ids of the
    scheme; none: back to "noch offen") in `slot` ("" the categories, SECOND the second coder's, SUGGESTED the AI's).
    In the first slot a person's choice clears the marks "from the AI" and "check again"; from_ai: taken over from
    the AI suggestion (marked). only_open: leave units that already have categories alone. Returns the number of
    participations changed, or an error text."""
    found = scheme(survey, key)
    if found is None:
        return de_en("Für diese Antworten gibt es noch kein Kategorienschema.", "There is no category scheme for these answers yet.")
    cats = clean(found, cats)
    if cats is None:
        return de_en("Hier ist nur eine Kategorie je Antwort erlaubt.", "Only one category per answer is allowed here.")
    return assign_many(survey, item, column, key, {text_norm: cats}, slot=slot, from_ai=from_ai, only_open=only_open)


def assign_many(survey, item, column, key, wanted, slot="", from_ai=False, only_open=False):
    """`assign` for many texts at once: wanted = {text_norm: categories} (already clean()). One pass over the
    participations, however many texts: the AI help gives up to 150 texts or all open ones at once, and a pass per
    text read every participation again and held the write lock for minutes (performance audit 2026-10-10, P1, K4).
    Returns the number of participations changed."""
    changed = 0
    if not wanted:
        return changed
    uplan = unit_plan(item, column, key)
    for r in _responses(survey):
        coding = dict(r.coding or {})
        for sub, text in units(item, uplan, r):
            cats = wanted.get(norm(text)) if text else None
            if cats is None:
                continue
            if only_open and coding.get(sub) is not None:
                continue
            if cats:
                coding[sub + slot] = cats
            else:
                coding.pop(sub + slot, None)
            if slot == "":
                coding.pop(sub + RECHECK, None)
                if from_ai and cats:
                    coding[sub + FROM_AI] = 1
                else:
                    coding.pop(sub + FROM_AI, None)
        if coding != (r.coding or {}):
            r.coding = coding or None
            changed += 1
    return changed


def person(item, uplan, response):
    """(has an answer, categories or None) of one participation: the categories of all its units together (an
    association task: of all its words); None while one of its units with a text is not coded yet, so a person
    counts as categorised only once all of it is (audit 2026-10-10, B-11: open words counted as 0 before)."""
    coding = response.coding or {}
    answered, got, still_open = False, set(), False
    for sub, text in units(item, uplan, response):
        if not text:
            continue
        answered = True
        if coding.get(sub) is None:
            still_open = True
        else:
            got |= set(coding[sub])
    return answered, (None if still_open or not answered else got)


def _drop_category(survey, key, cat_id):
    """Takes a category out of every participation, in every unit and slot; a unit left without any is "noch offen"
    again. Returns True if a slot of the second coding (its categories or the frozen first coding) changed."""
    compared = False
    for r in _responses(survey):
        coding = dict(r.coding or {})
        for sub in subkeys(coding, key):
            for slot in SLOTS:
                got = coding.get(sub + slot)
                if got and cat_id in got:
                    rest = [c for c in got if c != cat_id]
                    compared = compared or slot in (SECOND, FROZEN)
                    if rest:
                        coding[sub + slot] = rest
                    else:
                        coding.pop(sub + slot)
        if coding != (r.coding or {}):
            r.coding = coding or None
    return compared


def _rescored(found, compared):
    """The scheme after a category was removed or merged: if that changed the compared coding of a finished second
    coding, its alpha is computed anew, and the page and the methods section say so (audit 2026-10-10, B-13)."""
    second = found.get("second")
    if compared and isinstance(second, dict) and second.get("done"):
        return {**found, "second": {**second, "rescored": True}}
    return found


def _several(survey, key):
    """How many units of `key` hold more than one category: in every unit (a pass of a loop, a word of an
    association) and in the second coder's and the frozen first coding (audit 2026-10-10, B-3)."""
    n = 0
    for r in _responses(survey):
        coding = r.coding or {}
        subs = subkeys(coding, key)
        n += sum(1 for k, v in coding.items() if base_of(k) in subs and k[len(base_of(k)):] in ("", SECOND, FROZEN)
                 and isinstance(v, list) and len(v) > 1)
    return n


def _clip(text, most):
    return " ".join(str(text or "").split())[:most]


def save_scheme(survey, key, form):
    """The form of the scheme: names, definitions, examples and main categories of the categories it lists, and the
    tick "nur eine". Returns an error text or None."""
    found = scheme(survey, key)
    if found is None:
        return de_en("Für diese Antworten gibt es noch kein Kategorienschema.", "There is no category scheme for these answers yet.")
    single = bool(form.get("single"))
    if single and not found["single"]:
        several = _several(survey, key)
        if several:
            return de_en("{n} Antworten haben schon mehrere Kategorien (auch in Schleifen, bei Begriffen einer "
                         "Assoziation oder in der Zweitkodierung). Nehmen Sie dort erst alle bis auf eine weg.",
                         "{n} answers already have several categories (also in loops, for words of an association or "
                         "in the second coding). First take away all but one of them there.", n=several)
    cats = []
    for c in found["cats"]:
        n = c["id"]
        name = _clip(form.get(f"name_{n}", c["name"]), MAX_NAME)
        cats.append({**c, "name": name or c["name"],
                     "definition": _clip(form.get(f"definition_{n}", c.get("definition")), MAX_TEXT),
                     "example": _clip(form.get(f"example_{n}", c.get("example")), MAX_TEXT),
                     "group": _clip(form.get(f"group_{n}", c.get("group")), MAX_GROUP)})
    names = [c["name"].casefold() for c in cats]
    if len(set(names)) < len(names):
        return de_en("Zwei Kategorien haben denselben Namen.", "Two categories have the same name.")
    _store(survey, key, {**found, "single": single, "cats": cats})
    return None


def add_category(survey, key, name):
    found = scheme(survey, key) or new_scheme()
    own = [c for c in found["cats"] if c["id"] not in FIXED]
    name = _clip(name, MAX_NAME)
    if len(own) >= MAX_CATS or found["next"] >= OTHER:
        return de_en("Mehr als {n} Kategorien sind nicht möglich.", "More than {n} categories are not possible.", n=MAX_CATS)
    if not name:
        name = de_en("Kategorie {n}", "Category {n}", n=len(own) + 1)
    if name.casefold() in {c["name"].casefold() for c in found["cats"]}:
        return de_en("Eine Kategorie mit diesem Namen gibt es schon.", "A category with this name already exists.")
    cat = {"id": found["next"], "name": name, "definition": "", "example": "", "group": ""}
    _store(survey, key, {**found, "cats": own + [cat] + [c for c in found["cats"] if c["id"] in FIXED],
                         "next": found["next"] + 1})
    return None


def remove_category(survey, key, cat_id):
    found = scheme(survey, key)
    if found is None or cat_id in FIXED or cat_id not in {c["id"] for c in found["cats"]}:
        return de_en("Diese Kategorie lässt sich nicht entfernen.", "This category cannot be removed.")
    compared = _drop_category(survey, key, cat_id)
    _store(survey, key, _rescored({**found, "cats": [c for c in found["cats"] if c["id"] != cat_id]}, compared))
    return None


def merge_categories(survey, key, source_id, target_id):
    """Puts category `source_id` into `target_id`: every answer with it gets the other (in every slot), the first
    one goes. Returns an error text or None."""
    found = scheme(survey, key)
    ids = {c["id"] for c in (found or {}).get("cats", [])}
    if found is None or source_id not in ids or target_id not in ids or source_id == target_id or source_id in FIXED:
        return de_en("Diese Kategorien lassen sich nicht zusammenlegen.", "These categories cannot be merged.")
    compared = False
    for r in _responses(survey):
        coding = dict(r.coding or {})
        for sub in subkeys(coding, key):
            for slot in SLOTS:
                got = coding.get(sub + slot)
                if got and source_id in got:
                    merged = sorted({target_id if c == source_id else c for c in got})
                    coding[sub + slot] = merged[:1] if found["single"] else merged
                    compared = compared or slot in (SECOND, FROZEN)
        if coding != (r.coding or {}):
            r.coding = coding
    target = next(c for c in found["cats"] if c["id"] == target_id)
    source = next(c for c in found["cats"] if c["id"] == source_id)
    note = de_en("zusammengelegt mit „{name}“", "merged with “{name}”", name=source["name"])
    cats = [{**c, "merged": ((c.get("merged") or "") + "; " + note).strip("; ")} if c is target else c
            for c in found["cats"] if c["id"] != source_id]
    _store(survey, key, _rescored({**found, "cats": cats}, compared))
    return None


def split_category(survey, key, cat_id, name):
    """Splits a new category off `cat_id`: the answers with `cat_id` are marked "neu prüfen", to be looked at again
    and given the old or the new one. Returns an error text or None."""
    found = scheme(survey, key)
    if found is None or cat_id in FIXED or cat_id not in {c["id"] for c in found["cats"]}:
        return de_en("Diese Kategorie lässt sich nicht teilen.", "This category cannot be split.")
    problem = add_category(survey, key, name)
    if problem:
        return problem
    for r in _responses(survey):
        coding = dict(r.coding or {})
        for sub in subkeys(coding, key):
            got = coding.get(sub)
            if got and cat_id in got:
                coding[sub + RECHECK] = 1
        if coding != (r.coding or {}):
            r.coding = coding
    return None


# --- export ------------------------------------------------------------------------------------------------------

def export_columns(entries):
    """[(column, label)] of the category columns of one element (plan()[item.id]); German, as the export's own labels."""
    from app import export

    out = []
    for col, label, _, found in entries:
        if found["single"]:
            out.append((f"{col}_kat", f"{label}: Kategorie"))
        else:
            out += [(f"{col}_k{c['id']}", f"{label}: Kategorie „{export._label(c['name'])}“") for c in found["cats"]]
        out += [(f"{word}_kat", f"{label}: Kategorie von {word}") for word in found.get("words") or []]
    return out


def export_values(item, entries, response, rid=None):
    """{column: value} of the category columns for one participation; rid: the loop row of one pass (the caller adds
    the pass, _<n>). Empty where the person gave no answer or none of it is categorised yet."""
    out = {}
    for col, _, key, found in entries:
        uplan = unit_plan(item, col, key)
        if rid is not None:
            uplan = [u for u in uplan if u[0] == f"{key}#{rid}"]
        _, got = person(item, uplan, response)
        if found["single"]:
            out[f"{col}_kat"] = min(got) if got else None
        else:
            for c in found["cats"]:
                out[f"{col}_k{c['id']}"] = None if got is None else int(c["id"] in got)
        if found.get("words"):  # association with "nur eine": the category of each word (B-2)
            coding = response.coding or {}
            for (sub, _, word), (_, text) in zip(uplan, units(item, uplan, response)):
                one = coding.get(sub) if text else None
                out[f"{word}_kat"] = min(one) if one else None
    return out


def codebook_rows(entries, t):
    """Rows of the codebook (variable, label, type, values, notes) for the category columns; t(de, en) gives the
    reader's language."""
    from app import export

    out = []
    empty = t("leer = keine Antwort oder noch nicht kategorisiert", "empty = no answer or not categorised yet")
    guide = t("Kodierleitfaden: kodierleitfaden.md", "coding guide: kodierleitfaden.md")
    for col, label, _, found in entries:
        what = col
        if col.endswith(ASSOC):  # all words of an association task: <code>_1, <code>_2, ...
            what = t("{base}_1 bis _n (alle Begriffe)", "{base}_1 to _n (all words)", base=col[:-len(ASSOC)])
        if found["single"]:
            values = "; ".join(f"{c['id']} = {export._label(c['name'])}" for c in found["cats"])
            out.append((f"{col}_kat", f"{label}: Kategorie", "Kategorie", values,
                        "; ".join([t("Kategorie der offenen Antwort {col}, eine je Antwort",
                                     "category of the open answer {col}, one per answer", col=what), empty, guide])))
            continue
        for c in found["cats"]:
            notes = [t("Kategorie der offenen Antwort {col}, je Person die Kategorien aller Begriffe (eine je Begriff)",
                       "category of the open answer {col}, per person the categories of all words (one per word)", col=what)
                     if found.get("words") else
                     t("Kategorie der offenen Antwort {col}, mehrere je Antwort möglich",
                       "category of the open answer {col}, several per answer possible", col=what)]
            if c.get("definition"):
                notes.append(t("Definition: {d}", "definition: {d}", d=export._label(c["definition"])))
            out.append((f"{col}_k{c['id']}", f"{label}: Kategorie „{export._label(c['name'])}“", "Kategorie",
                        t("0 = nicht zugeordnet; 1 = zugeordnet", "0 = not assigned; 1 = assigned"),
                        "; ".join(notes + [empty, guide])))
        values = "; ".join(f"{c['id']} = {export._label(c['name'])}" for c in found["cats"])
        for word in found.get("words") or []:
            out.append((f"{word}_kat", f"{label}: Kategorie von {word}", "Kategorie", values,
                        "; ".join([t("Kategorie des Begriffs {col}, eine je Begriff", "category of the word {col}, one per word",
                                     col=word), empty, guide])))
    return out


def _how(sch, t):
    """How many categories an answer may have, in the words of the guide and the methods section."""
    if sch.get("single_word"):
        return t("je Begriff eine Kategorie", "one category per word")
    return t("je Antwort eine Kategorie", "one category per answer") if sch["single"] \
        else t("je Antwort mehrere Kategorien möglich", "several categories per answer possible")


def guide_md(survey, lang=None, examples=True):
    """The coding guide of the survey (Markdown): per categorised column its categories with definition and anchor
    example, and how far it is coded; "" for a survey without any scheme. examples: False leaves the anchor examples
    out (a public deposit without open answers: an example may quote what someone typed). Counts only participations
    of the source "real", as the methods section and the data file (audit 2026-10-10, B-5)."""
    from app import units as fmt
    from app.items.base import in_lang, reader

    ui = reader(lang)

    def t(de, en, **values):
        return in_lang(ui, de, en, **values)

    found = {**plan(survey), **{i: e for i, e in plan(survey, loops=True).items()}}
    if not found:
        return ""
    lines = [t("# Kodierleitfaden: {title}", "# Coding guide: {title}", title=survey.title), "",
             t("Kategorien der offenen Antworten, vergeben auf der Seite „Kategorisieren“ von Askurious. Aufbau nach "
               "Mayring (2022): je Kategorie Definition und Ankerbeispiel.",
               "Categories of the open answers, assigned on the page “Kategorisieren” of Askurious. Built after "
               "Mayring (2022): per category a definition and an anchor example."), ""]
    responses = _responses(survey, real=True)
    from app import export

    for item in export._items(survey):
        for col, label, key, sch in found.get(item.id, []):
            uplan = unit_plan(item, col, key)
            states = [person(item, uplan, r) for r in responses]
            texts = [r for r, (answered, _) in zip(responses, states) if answered]
            coded = sum(1 for answered, got in states if answered and got is not None)
            lines += [f"## {col}", "", label, "",
                      t("{n} Antworten, davon {k} kategorisiert; {how}.", "{n} answers, {k} of them categorised; {how}.",
                        n=len(texts), k=coded, how=_how(sch, t)), ""]
            from_ai = sum(1 for r in texts if any((r.coding or {}).get(sub + FROM_AI) for sub, _, _ in uplan))
            if from_ai:
                lines += [t("Davon {k} gesammelt aus KI-Vorschlägen übernommen (nicht einzeln bestätigt).",
                            "{k} of them accepted in bulk from AI suggestions (not confirmed one by one).", k=from_ai), ""]
            check = coding_check.report(survey, item, col, key)
            if check and check["n"]:
                value = "–" if check["alpha"] is None else fmt.fixed(check["alpha"], 2)
                interval = f" [{fmt.fixed(check['ci'][0], 2)}; {fmt.fixed(check['ci'][1], 2)}]" if check["ci"] else ""
                lines += [t("Zweitkodierung: {n} verschiedene Antworttexte blind von einer zweiten Person kodiert; "
                            "Übereinstimmung {p} %, Krippendorffs α = {a}{ci} (95-%-Bootstrap-Intervall).",
                            "Second coding: {n} distinct answer texts coded blind by a second person; agreement {p}%, "
                            "Krippendorff's α = {a}{ci} (95% bootstrap interval).", n=check["n"],
                            p=fmt.percent(check["agree"]), a=value, ci=interval), ""]
                for name, a in check["per_cat"]:
                    lines.append(f"- {name}: α = {'–' if a is None else fmt.fixed(a, 2)}")
                if check["per_cat"]:
                    lines.append("")
            lines += [t("| Code | Kategorie | Oberkategorie | Definition | Ankerbeispiel |",
                        "| Code | Category | Main category | Definition | Anchor example |"),
                      "| --- | --- | --- | --- | --- |"]
            for c in sch["cats"]:
                cells = [str(c["id"]), c["name"], c.get("group") or "", c.get("definition") or "",
                         (c.get("example") or "") if examples else "–"]
                lines.append("| " + " | ".join(x.replace("|", "/") for x in cells) + " |")
            lines.append("")
    lines += [t("## Quellen", "## Sources"), "", MAYRING, "", coding_check.KRIPPENDORFF, ""]
    return "\n".join(lines)


# --- methods section -------------------------------------------------------------------------------------------

def _dec(value, lang, places=2):
    """A number of the methods section, rounded half up as everywhere (decision "einheitlich runden"; B-9)."""
    from app import units

    return units.decimal_text(value, places, lang=lang)


def methods_sentences(survey):
    """One paragraph per categorised column for the methods section (app/methods.py, app/methods_text.py):
    [{"text_de", "text_en"}]. How many answers were categorised into how many categories (after Mayring, 2022), how
    many of them were taken over from an AI suggestion, and the reliability of the second coding (Krippendorff's
    alpha with its interval and the agreement). Only what happened is claimed (audit 2026-10-10, B-6, B-12, B-14):
    AI suggestions taken over in bulk are named as such, disagreements as clarified only as far as the first coding
    was changed, the unit of the sample is a distinct answer text, a pooled alpha says so."""
    from app import export, units
    from app.items.base import plain

    found = {**plan(survey), **plan(survey, loops=True)}
    if not found:
        return []
    responses = [r for r in _responses(survey) if r.source == "real"]
    out = []
    for item in export._items(survey):
        for col, label, key, sch in found.get(item.id, []):
            uplan = unit_plan(item, col, key)
            states = [person(item, uplan, r) for r in responses]
            answered = sum(1 for a, _ in states if a)
            coded = sum(1 for a, got in states if a and got is not None)
            if not coded:
                continue
            k = len([c for c in sch["cats"] if c["id"] not in FIXED])
            question = plain(item.text("question")) or item.code
            ai_n = sum(1 for r in responses if any((r.coding or {}).get(sub + FROM_AI) for sub, _, _ in uplan))
            how_de, how_en = _how(sch, lambda de, en: de), _how(sch, lambda de, en: en)
            de = (f"Die offenen Antworten auf „{question}“ ({col}) wurden in einem Kategoriensystem mit "
                  f"{'einer inhaltlichen Kategorie' if k == 1 else f'{k} inhaltlichen Kategorien'} sowie „Sonstiges“ "
                  f"und „nicht verwertbar“ kodiert (Definition und Ankerbeispiel je Kategorie, nach Mayring, 2022; "
                  f"{how_de}); kategorisiert sind {coded} von {answered} Personen mit Antwort.")
            en = (f"The open answers to “{question}” ({col}) were coded into a category system of {k} substantive "
                  f"{'category' if k == 1 else 'categories'} plus “other” and “not usable” (definition and anchor "
                  f"example per category, after Mayring, 2022; {how_en}); {coded} of {answered} people with an answer "
                  f"are categorised.")
            if ai_n:
                de += (f" Bei {ai_n} {'Person' if ai_n == 1 else 'Personen'} wurde die Zuordnung gesammelt aus Vorschlägen "
                       f"eines Sprachmodells übernommen (nicht einzeln bestätigt).")
                en += (f" For {ai_n} {'person' if ai_n == 1 else 'persons'} the assignment was accepted in bulk from a "
                       f"language model's suggestions (not confirmed one by one).")
            from app import coding_check

            check = coding_check.report(survey, item, col, key)
            if check and check["n"] and check["alpha"] is not None:
                ci_de = f" [{_dec(check['ci'][0], 'de')}; {_dec(check['ci'][1], 'de')}]" if check["ci"] else ""
                ci_en = f" [{_dec(check['ci'][0], 'en')}, {_dec(check['ci'][1], 'en')}]" if check["ci"] else ""
                n, skipped, agree = check["n"], check["skipped"], units.percent(check["agree"])
                pooled = not check["single"]
                cats_n = len(sch["cats"])
                de += (f" Eine zweite Person kodierte eine Zufallsstichprobe von {n} verschiedenen Antworttexten blind"
                       + (f" ({skipped} weitere ohne Erstkodierung wurden nicht verglichen)" if skipped else "")
                       + f"; Krippendorffs α = {_dec(check['alpha'], 'de')}{ci_de} (95-%-Bootstrap-Intervall"
                       + (f"; gepoolt über die Ja/Nein-Entscheidungen je Kategorie, {cats_n} Kategorien" if pooled else "")
                       + f"), Übereinstimmung {agree} %" + (" (identische Kategorien)" if pooled else "")
                       + " (Krippendorff, 2019).")
                en += (f" A second person coded a random sample of {n} distinct answer texts blind"
                       + (f" ({skipped} more without a first coding were not compared)" if skipped else "")
                       + f"; Krippendorff's α = {_dec(check['alpha'], 'en')}{ci_en} (95% bootstrap interval"
                       + (f"; pooled over the yes/no decisions per category, {cats_n} categories" if pooled else "")
                       + f"), agreement {agree}%" + (" (identical categories)" if pooled else "")
                       + " (Krippendorff, 2019).")
                if check.get("rescored"):
                    de += " α wurde nach einer späteren Änderung des Kategoriensystems neu berechnet."
                    en += " α was computed anew after a later change of the category system."
                differ = len(check["differ"])
                clarified = sum(1 for u in check["differ"] if u["clarified"])
                if differ and clarified == differ:
                    de += " Die Abweichungen wurden anschließend geklärt."
                    en += " The disagreements were resolved afterwards."
                elif clarified:
                    de += f" {clarified} von {differ} Abweichungen wurden anschließend geklärt."
                    en += f" {clarified} of {differ} disagreements were resolved afterwards."
            out.append({"text_de": de, "text_en": en})
    return out


# --- results -----------------------------------------------------------------------------------------------------

def results(item, entries, table, weights=None, suffix=""):
    """The category lists of one element for the results page: [{"code", "label", "single", "n", "open", "mentions",
    "rows": [{"value", "label", "group", "count", "n", "share", "of_mentions", "ci", "rest"}]}], the most frequent
    first, "Sonstiges" and "nicht verwertbar" last. n: persons with a categorised answer, open: answers not yet
    categorised. Shares of persons (several categories: they add up to more than 100 %) and of mentions.
    entries: plan()[item.id]. suffix: "_<n>" for one pass of a loop (plan(survey, loops=True))."""
    from app import intervals

    out = []
    for col, label, _, found in entries:
        if found["single"]:
            got = [r.get(f"{col}_kat{suffix}") for r in table]
            coded = [(g, w) for g, w in zip(got, weights or [1.0] * len(got)) if g not in (None, "")]
            per = {c["id"]: [w for g, w in coded if g == c["id"]] for c in found["cats"]}
        else:
            cols = {c["id"]: f"{col}_k{c['id']}{suffix}" for c in found["cats"]}
            coded = [(r, w) for r, w in zip(table, weights or [1.0] * len(table))
                     if any(r.get(x) in (0, 1) for x in cols.values())]
            per = {cid: [w for r, w in coded if r.get(x) == 1] for cid, x in cols.items()}
        n = len(coded)
        whole = sum(w for _, w in coded)
        mentions = sum(len(v) for v in per.values())
        sources = ([f"{item.code}_{k}" for k in item.kind.slots(item)] if col.endswith(ASSOC) and item.type == "association"
                   else [col])
        answered = sum(1 for r in table if any(r.get(f"{c}{suffix}") not in (None, "") for c in sources))
        rows = []
        for c in found["cats"]:
            count = len(per[c["id"]])
            share = sum(per[c["id"]]) / whole if whole else 0
            rows.append({"value": str(c["id"]), "label": c["name"], "group": c.get("group") or "", "count": count,
                         "n": n, "share": share, "of_mentions": count / mentions if mentions else 0,
                         "ci": intervals.wilson(count, n) if weights is None else None, "rest": c["id"] in FIXED})
        # the most frequent first, weighted by the weighted sum the page shows (audit 2026-10-10, B-10); "Sonstiges"
        # and "nicht verwertbar" last and in this order, whatever their counts
        size = {str(cid): sum(v) for cid, v in per.items()}
        rows.sort(key=lambda r: (r["rest"], 0 if r["rest"] else -size[r["value"]], int(r["value"]) if r["rest"] else 0))
        out.append({"code": col, "label": label, "key": key_of(item, col), "single": found["single"], "n": n,
                    "pass": suffix[1:] or None,
                    "open": answered - n, "mentions": mentions, "rows": rows})
    return out


# --- deletion ----------------------------------------------------------------------------------------------------

def forget(survey, responses):
    """Before participations are deleted (erasure.erase: deletion code, request, platform; retention.purge): an anchor
    example that literally occurs in one of their answers is emptied, and the AI's proposals go, whose examples may
    quote answers (audit 2026-10-10, L-2). Their categories go with them anyway (response.coding, K7). The scheme
    lives in survey.codings, so without this an answer "irretrievably deleted" stayed quoted in the coding guide."""
    responses = list(responses)
    if not survey.codings or not responses:
        return
    codings, changed = dict(survey.codings), False
    for item, col, _, key in targets(survey):
        found = codings.get(key)
        if not isinstance(found, dict):
            continue
        uplan = unit_plan(item, col, key)
        gone = [norm(text) for r in responses for _, text in units(item, uplan, r) if text]
        cats = []
        for c in found.get("cats") or []:
            example = norm(c.get("example") or "") if isinstance(c, dict) else ""
            if example and any(example in text for text in gone):
                c = {**c, "example": ""}
            cats.append(c)
        new = {k: v for k, v in found.items() if k != "proposals"}
        new["cats"] = cats
        if new != found:
            codings[key], changed = new, True
    if changed:  # past the version check: a deletion is no edit of the scheme and must never be refused
        survey.codings = {**codings, VERSION: version(codings) + 1}


def forget_all(survey):
    """forget() for every participation of the survey, read without loading them (retention.purge deletes them in
    one statement)."""
    from types import SimpleNamespace

    if not survey.codings:
        return
    rows = db.session.execute(db.select(Response.answers).where(Response.survey_id == survey.id)).scalars()
    forget(survey, [SimpleNamespace(answers=a) for a in rows])


# --- the AI help: participants informed (B-1) ----------------------------------------------------------------------

def ai_informed(survey, to=None):
    """True once the owner or an admin of the survey confirmed that the participants were told their answer texts may
    go to the AI service `to` (a recipient key of ai_access.recipient_key; None: to any; audit 2026-10-10, K3)."""
    from app import ai_access

    return ai_access.confirmed((survey.codings or {}).get(AI_OK), to)


def ai_pending(survey):
    """The recipients of the team not confirmed yet, each with its paragraph, for the owner and the admins of the
    survey (K3, decision 6a); [] for everyone else."""
    from app import ai_access

    if not ai_access.may_confirm(survey):
        return []
    stored = (survey.codings or {}).get(AI_OK)
    # only own accesses: the platform's key no longer carries answer texts (Ralf, 2026-10-21)
    return [{**r, "notice": {lang: ai_notice(lang, to=r["name"]) for lang in ("de", "en")}}
            for r in ai_access.team_recipients(survey) if r["own"] and not ai_access.confirmed(stored, r["key"])]


def ai_recipients(survey):
    """For the participants' texts: (the names of the own AI accesses confirmed for this survey, whether the
    platform's access was confirmed before 2026-10-21). A survey whose texts went to OpenAI keeps saying so."""
    from app import ai_access

    found = ai_access.confirmations((survey.codings or {}).get(AI_OK))
    names = [str(v.get("name") or "").strip() for k, v in found.items() if k != ai_access.PLATFORM]
    return [n for n in names if n], ai_access.PLATFORM in found


def ai_notice(lang, own=False, to=None):
    """The paragraph researchers put into the information and consent or the privacy notice of their survey when
    they use the AI help: what goes where, for what, and how long it is kept (German or English). `own`: over the
    researcher's own key (app/ai_access.py): their provider is named, and what it keeps is left to its terms, since
    the OpenAI facts do not hold for it. `to`: the name of another own access (ai_access.recipient of a team member)."""
    if own or to:
        from app import ai_access

        to = to or ai_access.recipient()
        return {
            "de": "Kategorisierung offener Antworten mit KI-Unterstützung: Ihre Antworten auf offene Fragen (Freitexte) "
                  f"können für die Auswertung an den KI-Dienst {to} übermittelt werden, der Vorschläge für Kategorien "
                  "und deren Zuordnung macht; die Entscheidung treffen die Forschenden. Übermittelt werden nur die "
                  "Antworttexte, ohne weitere Angaben zu Ihrer Teilnahme. Ob der Anbieter sie speichert oder zum "
                  "Training verwendet und wie lange er sie aufbewahrt, regeln seine Nutzungsbedingungen. Bitte "
                  "schreiben Sie in offene Antworten keine Namen oder andere Angaben, die Sie oder andere erkennbar "
                  "machen.",
            "en": "Categorising open answers with the help of AI: your answers to open questions (free text) may be "
                  f"sent to the AI service {to} for the analysis, which suggests categories and how to assign them; "
                  "the researchers decide. Only the answer texts are sent, without any other details of your "
                  "participation. Whether the provider stores them or uses them for training, and how long it keeps "
                  "them, is set by its terms of use. Please do not write names or other details that make you or "
                  "others identifiable in open answers.",
        }[lang]
    return {
        "de": "Kategorisierung offener Antworten mit KI-Unterstützung: Ihre Antworten auf offene Fragen (Freitexte) "
              "können für die Auswertung an den KI-Dienst von OpenAI übermittelt werden, der Vorschläge für Kategorien "
              "und deren Zuordnung macht; die Entscheidung treffen die Forschenden. Übermittelt werden nur die "
              "Antworttexte, ohne weitere Angaben zu Ihrer Teilnahme. OpenAI speichert sie nicht für spätere Abrufe, "
              "verwendet sie nicht zum Training seiner Modelle und bewahrt sie bis zu 30 Tage zur Missbrauchskontrolle "
              "auf. Bitte schreiben Sie in offene Antworten keine Namen oder andere Angaben, die Sie oder andere "
              "erkennbar machen.",
        "en": "Categorising open answers with the help of AI: your answers to open questions (free text) may be sent "
              "to the AI service of OpenAI for the analysis, which suggests categories and how to assign them; the "
              "researchers decide. Only the answer texts are sent, without any other details of your participation. "
              "OpenAI does not store them for later retrieval, does not use them to train its models and keeps them for "
              "up to 30 days to monitor abuse. Please do not write names or other details that make you or others "
              "identifiable in open answers.",
    }[lang]


# --- routes ------------------------------------------------------------------------------------------------------

@bp.errorhandler(Conflict)
def _conflict(_):
    """Someone else saved the schemes in between (L-9): nothing of this request is kept, the page says so."""
    db.session.rollback()
    if request.accept_mimetypes.best == "application/json":
        return jsonify(status="conflict", message=conflict_text()), 409
    flash(conflict_text(), "error")
    args = request.view_args or {}
    return redirect(url_for("coding.page", survey_id=args.get("survey_id"), key=args.get("key")))


def _not_blind(survey, key):
    """403 for the second coder while they code `key` blind: the scheme and the first coding are not theirs to change
    (removing, merging or splitting a category moves the first coder's categories; audit 2026-10-10, R2)."""
    if coding_check.blind_for(survey, key, current_user):
        abort(403)


def _target(survey, key):
    from flask import abort

    found = find(survey, key)
    if found is None:
        abort(404)
    return found


@bp.route("/<sid:survey_id>/categorise/<key>")
@login_required
def page(survey_id, key):
    """The page "Kategorisieren" of one open answer (role "Bearbeiten")."""
    from app import ai_access, surveys

    survey = surveys.get_survey(survey_id, need="editor")
    experimental.require("coding", survey)
    item, column, label = _target(survey, key)
    if coding_check.blind_for(survey, key, current_user):  # the second coder does not see the first coding
        return redirect(url_for("coding.second_page", survey_id=survey.id, key=key))
    found = scheme(survey, key)
    rows = answers(survey, item, column, key)
    names = {c["id"]: c["name"] for c in (found or {}).get("cats", [])}
    second = coding_check.state(survey, key)
    return render_template("surveys/coding.html", survey=survey, item=item, column=column, label=label, key=key,
                           scheme=found, rows=rows, names=names, ai_distribution=coding_ai.assignment_distribution(found, rows), done=sum(1 for r in rows if r["cats"] is not None and not r["mixed"]),
                           max_cats=MAX_CATS, mayring=MAYRING, fixed=FIXED, second=second,
                           check=coding_check.report(survey, item, column, key),
                           coders=coding_check.candidates(survey, current_user),
                           sample_n=coding_check.sample_size(sum(1 for r in rows if "real" in r["sources"])), check_sources=(coding_check.KRIPPENDORFF, coding_check.HAYES),
                           coding_ai_on=coding_ai.available(), ai_needs_own=coding_ai.needs_own(), ai_own=coding_ai.own(), proposals=(found or {}).get("proposals") or [],
                           base=version(survey.codings), ai_informed=ai_informed(survey, ai_access.recipient_key()),
                           ai_notice={lang: ai_notice(lang, own=coding_ai.own() is not None) for lang in ("de", "en")},
                           ai_to=ai_access.recipient(), ai_may_confirm=ai_access.may_confirm(survey),
                           ai_pending=ai_pending(survey) if coding_ai.available() else [])


@bp.route("/<sid:survey_id>/categorise/<key>/scheme", methods=["POST"])
@login_required
@autosave.aware
def update_scheme(survey_id, key):
    """The form of the categories: names, definitions, examples, main categories, the tick "nur eine"."""
    from app import surveys

    from flask import g

    survey = surveys.get_survey(survey_id, need="editor")
    experimental.require("coding", survey)
    item, column, _ = _target(survey, key)
    _not_blind(survey, key)
    try:
        check_base(survey, request.form)
        problem = save_scheme(survey, key, request.form)
    except Conflict:  # the autosave of the page shows "Nicht gespeichert: Jemand anderes hat geändert" (app/autosave.py)
        db.session.rollback()
        g.edit_conflict = {"key": f"coding:{key}", "base": str(version(survey.codings)), "who": de_en("Jemand anderes", "Someone else"),
                           "when": None}
        flash(conflict_text(), "error")
        return redirect(url_for("coding.page", survey_id=survey.id, key=key))
    if problem:
        flash(problem, "error")
    else:
        db.session.commit()
        autosave.base(str(version(survey.codings)))
        flash(de_en("Kategorien gespeichert.", "Categories saved."), "success")
    return redirect(url_for("coding.page", survey_id=survey.id, key=key))


@bp.route("/<sid:survey_id>/categorise/<key>/add", methods=["POST"])
@login_required
def add(survey_id, key):
    """"Kategorie hinzufügen"; the first one also lays out the scheme with "Sonstiges" and "nicht verwertbar"."""
    from app import surveys

    survey = surveys.get_survey(survey_id, need="editor")
    experimental.require("coding", survey)
    item, column, _ = _target(survey, key)
    _not_blind(survey, key)
    check_base(survey, request.form)
    problem = add_category(survey, key, request.form.get("name", ""))
    if problem:
        flash(problem, "error")
    else:
        db.session.commit()
    return redirect(url_for("coding.page", survey_id=survey.id, key=key) + "#kategorien")


@bp.route("/<sid:survey_id>/categorise/<key>/remove/<int:cat_id>", methods=["POST"])
@login_required
def remove(survey_id, key, cat_id):
    from app import surveys

    survey = surveys.get_survey(survey_id, need="editor")
    experimental.require("coding", survey)
    item, column, _ = _target(survey, key)
    _not_blind(survey, key)
    check_base(survey, request.form)
    problem = remove_category(survey, key, cat_id)
    if problem:
        flash(problem, "error")
    else:
        coding_check.remember(survey, item, column, key)  # alpha may be rescored: its interval anew, once (P4)
        db.session.commit()
        flash(de_en("Kategorie entfernt.", "Category removed."), "success")
    return redirect(url_for("coding.page", survey_id=survey.id, key=key) + "#kategorien")


@bp.route("/<sid:survey_id>/categorise/<key>/merge", methods=["POST"])
@login_required
def merge(survey_id, key):
    """"Zusammenlegen": one category into another."""
    from app import surveys

    survey = surveys.get_survey(survey_id, need="editor")
    experimental.require("coding", survey)
    item, column, _ = _target(survey, key)
    _not_blind(survey, key)
    check_base(survey, request.form)
    try:
        source, target = int(request.form.get("source", "")), int(request.form.get("target", ""))
    except ValueError:
        source = target = None
    problem = merge_categories(survey, key, source, target)
    if problem:
        flash(problem, "error")
    else:
        coding_check.remember(survey, item, column, key)  # alpha may be rescored: its interval anew, once (P4)
        db.session.commit()
        flash(de_en("Kategorien zusammengelegt.", "Categories merged."), "success")
    return redirect(url_for("coding.page", survey_id=survey.id, key=key) + "#kategorien")


@bp.route("/<sid:survey_id>/categorise/<key>/split/<int:cat_id>", methods=["POST"])
@login_required
def split(survey_id, key, cat_id):
    """"Teilen": a new category beside one, its answers marked to be checked again."""
    from app import surveys

    survey = surveys.get_survey(survey_id, need="editor")
    experimental.require("coding", survey)
    item, column, _ = _target(survey, key)
    _not_blind(survey, key)
    check_base(survey, request.form)
    problem = split_category(survey, key, cat_id, request.form.get("name", ""))
    if problem:
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("Neue Kategorie angelegt. Die Antworten der bisherigen sind als „neu prüfen“ markiert.",
                    "New category created. The answers of the old one are marked “check again”."), "success")
    return redirect(url_for("coding.page", survey_id=survey.id, key=key) + "#antworten")


@bp.route("/<sid:survey_id>/categorise/<key>/assign", methods=["POST"])
@login_required
def assign_route(survey_id, key):
    """One answer (all participations with that text) gets its categories; answered with JSON for the page's
    script, with a redirect without it."""
    from app import surveys

    survey = surveys.get_survey(survey_id, need="editor")
    experimental.require("coding", survey)
    item, column, _ = _target(survey, key)
    cats = []
    for value in request.form.getlist("cat"):
        try:
            cats.append(int(value))
        except ValueError:
            continue
    if request.form.get("slot") == "second":  # the second coder, on their own page
        second = coding_check.state(survey, key)
        if not second or second.get("by") != current_user.id or second.get("done"):
            abort(403)
        result = assign(survey, item, column, key, request.form.get("norm", ""), cats, slot=SECOND)
    elif coding_check.blind_for(survey, key, current_user):
        abort(403)
    else:
        result = assign(survey, item, column, key, request.form.get("norm", ""), cats)
    wants_json = request.accept_mimetypes.best == "application/json"
    if isinstance(result, str):
        if wants_json:
            return jsonify(status="invalid", message=result), 400
        flash(result, "error")
    else:
        db.session.commit()
        if wants_json:
            return jsonify(status="saved", changed=result)
    return redirect(url_for("coding.page", survey_id=survey.id, key=key))


@bp.route("/<sid:survey_id>/export/kodierleitfaden.md")
@login_required
def guide(survey_id):
    """"Kodierleitfaden (Markdown)" of the Export page."""
    from flask import Response as FlaskResponse

    from app import export, surveys

    survey = surveys.get_survey(survey_id, need="editor")
    experimental.require("coding", survey)
    return FlaskResponse(guide_md(survey), mimetype="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "kodierleitfaden")[:-4]}.md"',
        "Cache-Control": "no-store"})


from app import coding_ai, coding_check  # noqa: E402  the second coder and the AI suggestions, with their routes on bp
