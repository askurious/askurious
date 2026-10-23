"""Translation of a survey: one page with every participant-facing text in the default language next to its
translation, what is missing, what changed since it was translated, and AI translation of everything open.

Texts stay where they always are (item.texts[lang], block titles, end texts, ...). A "unit" is one place that
holds texts per language (an item, a block title, the end texts, ...); inside a unit every non-empty string of
the default language is one field, addressed by its path ("question", "options.a1b2", "cats.0"). This works for
every item type without code of its own.

Per translated field one TranslationMark keeps the source text it is based on and whether the AI wrote it and
nobody confirmed it yet. Status of a field: missing (no translation) / changed (the source differs from the one
the translation is based on; whitespace and case do not count) / ai (unconfirmed) / ok / validated (a validated
construct with its own version in that language: not translated here). Translations that existed before get a
mark with the current source the first time the page is opened.
"""
import difflib
import json
import re
from dataclasses import dataclass, field

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import Markup, escape

from app.extensions import db, get_or_create
from app.lang import de_en, english
from app.models import TranslationMark, utcnow

bp = Blueprint("translations", __name__, url_prefix="/surveys/<sid:survey_id>/translation")

ROLES = {
    "question": "Fragetext", "body": "Text", "text": "Text", "passage": "Text zum Markieren", "options": "Antwortoption",
    "statements": "Aussage", "labels": "Skalenbeschriftung", "pairs": "Gegensatzpaar", "left": "links", "right": "rechts",
    "agree": "Knopf Zustimmung", "disagree": "Knopf Ablehnung", "agree_note": "Hinweis zur Zustimmung",
    "categories": "Kategorie", "elements": "Element", "attributes": "Merkmal", "levels": "Stufe", "cats": "Kategorie",
    "rate": "Frage zur Bewertung", "low": "unteres Ende", "high": "oberes Ende", "prefix": "vor dem Feld",
    "suffix": "nach dem Feld", "alt": "Alternativtext", "alts": "Alternativtext des Bildes", "validation_message": "Fehlermeldung", "none_label": "Keine-Option",
    "alt_label": "Name der Alternativen", "title": "Titel", "rows": "Zeile", "titles": "Frage der Spalte", "units": "Einheit", "thanks": "Dank am Ende",
    "declined": "Text bei Ablehnung", "closed": "Text bei geschlossener Befragung", "screenout": "Text bei Screen-out",
    "again": "Knopf erneut teilnehmen", "next_person": "Knopf nächste Person", "intro": "Einleitung",
    "hint": "Tempo-Hinweis auf Seiten mit Fragen", "hint_read": "Tempo-Hinweis auf Seiten ohne Frage",
    "groups": "Gruppe", "lists": "Liste", "entries": "Eintrag", "pre_low": "Stapel links", "pre_mid": "Stapel Mitte", "pre_high": "Stapel rechts",
    "objects": "Option", "tie_label": "Knopf gleich", "much": "Stärke stark", "clear": "Stärke deutlich", "bit": "Stärke schwach",
    "same": "Stärke Mitte", "explain_intro": "Erklärung vorab",
}
ROLES_EN = {
    "question": "Question text", "body": "Text", "text": "Text", "passage": "Text to highlight", "options": "Answer option",
    "statements": "Statement", "labels": "Scale label", "pairs": "Pair of opposites", "left": "left", "right": "right",
    "agree": "Consent button", "disagree": "Decline button", "agree_note": "Note on consent",
    "categories": "Category", "elements": "Element", "attributes": "Attribute", "levels": "Level", "cats": "Category",
    "rate": "Rating question", "low": "lower end", "high": "upper end", "prefix": "before the field",
    "suffix": "after the field", "alt": "Alternative text", "alts": "Alternative text of the image", "validation_message": "Error message", "none_label": "None option",
    "alt_label": "Name of the alternatives", "title": "Title", "rows": "Row", "titles": "Question of the column", "units": "Unit", "thanks": "Thanks at the end",
    "declined": "Text on declining", "closed": "Text when the survey is closed", "screenout": "Text on screen-out",
    "again": "Button take part again", "next_person": "Button next person", "intro": "Introduction",
    "hint": "Speed prompt on pages with questions", "hint_read": "Speed prompt on pages without a question",
    "groups": "Group", "lists": "List", "entries": "Entry", "pre_low": "Left pile", "pre_mid": "Middle pile", "pre_high": "Right pile",
    "objects": "Option", "tie_label": "Button same", "much": "Strength strong", "clear": "Strength clear", "bit": "Strength weak",
    "same": "Strength middle", "explain_intro": "Explanation beforehand",
}
STATUS_LABELS = {"missing": "fehlt", "changed": "zu prüfen", "ai": "KI, ungeprüft", "ok": "übersetzt",
                 "validated": "validierte Fassung", "default": "Vorgabe (wird je Sprache gesetzt)"}
STATUS_LABELS_EN = {"missing": "missing", "changed": "to check", "ai": "AI, unchecked", "ok": "translated",
                    "validated": "validated version", "default": "standard wording (set per language)"}


def roles():
    """The names of the fields (ROLES) in the language of the page."""
    return ROLES_EN if english() else ROLES


def status_labels():
    """The names of the states (STATUS_LABELS) in the language of the page."""
    return STATUS_LABELS_EN if english() else STATUS_LABELS
CHUNK_CHARS = 9000  # source characters per AI request: one request for a usual survey


# ---- units: the places that hold texts per language -------------------------------------------------------


@dataclass
class Unit:
    key: str
    group: str                       # "general" or "block:<id>"
    label: str
    kind: str = ""                   # e.g. the item type, for the AI and the page
    get: object = None               # lang -> nested texts
    set: object = None               # (lang, nested texts) -> None
    validated: bool = False          # a validated construct has its own version in the target language
    note: str = ""
    item: object = None
    filled: bool = False             # a validated version was put in just now (needs a commit)
    role: object = None              # path -> readable name, for units with ids in their paths
    fields: list = field(default_factory=list)
    admin: bool = False              # texts of a settings section only admins change (see below)


# Units with admin=True: their texts belong to a section of the settings that only the role "Admin" changes
# (surveys.section_role): the end texts (/end-texts), the wording of the tempo hint (project facts) and the separate
# contact section (/panel, personal data). In other languages, too, they are the admins' (S9 of the security audit
# 2026-10-03): "Bearbeiten" sees them on this page without a field, the AI leaves them out for them, "confirm" too. The
# consent is a question of the questionnaire: its text stays with "Bearbeiten", its key facts with the admins
# (decisions.md, "Rollenregeln").


def may_change(survey, unit, user=None):
    """May this person change the texts of `unit` here (role "Bearbeiten", and "Admin" for admin units)?"""
    user = user or current_user
    return survey.can(user, "admin" if unit.admin else "editor")


def _item_unit(survey, item, lang, fill=True):
    from app import constructs

    shown = {}  # a validated version that is shown without being stored (fill is off)

    def get(code):
        return (item.texts or {}).get(code) or shown.get(code) or {}

    def put(code, value):
        item.texts = {**(item.texts or {}), code: value}

    validated, note, template, filled = False, "", {}, False
    meta = (item.config or {}).get("construct")
    if meta and constructs.is_linked_key(meta.get("key")):
        # a linked element (app/linked.py): frozen, its languages are kept as they are; new ones are translated here
        template = (meta.get("template") or {}).get("texts") or {}
        if lang in template and constructs.locked(item):
            validated, note = True, de_en("Fassung des verknüpften Elements; eingefroren, wird hier nicht übersetzt.",
                                          "Version of the linked element; frozen, not translated here.")
    elif meta:
        template = (meta.get("template") or {}).get("texts") or {}
        if lang in constructs.WORKING_LANGS.get(meta.get("key"), ()):
            template = {k: v for k, v in template.items() if k != lang}  # only a working translation
        if lang in template and constructs.locked(item):
            validated, note = True, de_en("Validierte Fassung aus „Konstrukte“; wird hier nicht übersetzt.",
                                          "Validated version from “Constructs”; not translated here.")
            filled = fill and not flatten((item.texts or {}).get(lang))
            if filled:
                put(lang, template[lang])  # the language came later than the construct: its validated version
            elif not flatten((item.texts or {}).get(lang)):
                shown[lang] = template[lang]
        elif lang in template:
            note = de_en("Validierte Fassung, für diese Befragung angepasst: Änderungen prüfen.",
                         "Validated version, adapted for this survey: check the changes.")
        elif meta.get("key") in constructs.NOT_SHARED:
            note = de_en("Für diese Sprache gibt es keine validierte Fassung. Der Wortlaut darf nur an Teilnehmende gehen: "
                         "Die KI übersetzt ihn nicht, und eine eigene Übersetzung braucht die Erlaubnis von Autorinnen, "
                         "Autoren und Verlag.",
                         "There is no validated version for this language. The wording may go only to participants: the "
                         "AI does not translate it, and a translation of your own needs the permission of its authors "
                         "and publisher.")
        elif meta.get("key") in constructs.ZIS:
            note = de_en("Für diese Sprache gibt es keine validierte Fassung. Eine Übersetzung ist eine andere als die "
                         "dokumentierte Form: Nach den ZIS-Bedingungen braucht sie das Einverständnis der Autorinnen und "
                         "Autoren, auch wenn die KI sie entwirft.",
                         "There is no validated version for this language. A translation is a form other than the "
                         "documented one: under the ZIS terms it needs the consent of the authors, also when the AI "
                         "drafts it.")
        else:
            note = de_en("Für diese Sprache gibt es keine validierte Fassung; eine eigene Übersetzung ist nicht validiert.",
                         "There is no validated version for this language; a translation of your own is not validated.")
    from app import undo

    label = f"{item.code} · " + undo.named(item.kind)  # both languages: the step name of a change (save_field)
    return Unit(f"item:{item.id}", f"block:{item.block_id}", label, item.kind.label, get, put, validated, note, item,
                filled=validated and filled)


def units(survey, lang, fill=True):
    """Every unit with participant-facing texts: the blocks in the order of the questionnaire, then the texts of
    the survey as a whole (title, end texts, contact section, screen-outs, loop tables). fill: a validated
    construct without texts in this language gets its validated version (off: it is shown, not stored)."""
    from app import flow, loops, undo

    out = []
    for block in survey.blocks:
        if block.show_title:
            out.append(Unit(f"block:{block.id}", f"block:{block.id}", undo.both("Blocktitel", "Block title"), "Überschrift über den Seiten des Blocks",
                            lambda code, b=block: {"title": (b.titles or {}).get(code, "")},
                            lambda code, v, b=block: setattr(b, "titles", {**(b.titles or {}), code: v.get("title", "")})))
        for item in block.items:
            if item.type != "page_break":
                out.append(_item_unit(survey, item, lang, fill))

    def get_title(code):
        return {"title": survey.title if code == survey.default_language else (survey.title_translations or {}).get(code, "")}

    def put_title(code, v):
        if code != survey.default_language:
            survey.title_translations = {**(survey.title_translations or {}), code: " ".join(str(v.get("title") or "").split())}
    out.append(Unit("title", "general", undo.both("Titel der Befragung", "Survey title"), "Titel der Befragung (Browser-Tab, Datenschutzseite)",
                    get_title, put_title))
    end = Unit("end", "general", undo.both("Texte der Endseiten", "End page texts"), "Endtexte der Befragung",
               lambda code: (survey.end_texts or {}).get(code) or {},
               lambda code, v: setattr(survey, "end_texts", {**(survey.end_texts or {}), code: v}), admin=True)
    out.append(end)
    from app import tempo

    if tempo.active(survey) and (survey.tempo_texts or {}).get(survey.default_language):  # an own wording of the hint
        out.append(Unit("tempo", "general", undo.both("Tempo-Hinweis", "Speed prompt"), "Hinweis auf einer zu schnell abgeschickten Seite",
                        lambda code: (survey.tempo_texts or {}).get(code) or {},
                        lambda code, v: setattr(survey, "tempo_texts", {**(survey.tempo_texts or {}), code: v}), admin=True))
    if (survey.contact or {}).get("enabled"):
        def put_contact(code, v):
            c = dict(survey.contact or {})
            c["texts"] = {**(c.get("texts") or {}), code: v}
            survey.contact = c
        out.append(Unit("contact", "general", undo.both("Getrennter Abschnitt (Kontakt)", "Separate section (contact)"), "Kontaktabschnitt",
                        lambda code: ((survey.contact or {}).get("texts") or {}).get(code) or {}, put_contact, admin=True))
    for node, _, _ in flow.walk(flow.tree(survey)):
        if node.get("t") == "end" and node.get("cases"):
            # an end with triggers: the end page of each is a field of its own (flow.cases)
            nid = node["id"]

            def get_cases(code, nid=nid):
                n, _ = flow.find(flow.tree(survey), nid)
                return {case["id"]: (case.get("text") or {}).get(code, "") for case in (n or {}).get("cases") or []}

            def put_cases(code, v, nid=nid):
                import copy

                root = copy.deepcopy(flow.tree(survey))
                n, _ = flow.find(root, nid)
                for case in n.get("cases") or []:
                    if case["id"] in v:
                        case["text"] = {**(case.get("text") or {}), code: v[case["id"]]}
                survey.flow = root
            names = {case["id"]: de_en("Auslöser {k}: {name}", "Trigger {k}: {name}", k=k, name=flow.END_TRIGGERS[case['when']][0])
                     for k, case in enumerate(node["cases"], start=1)}
            out.append(Unit(f"flow:{nid}", "general", de_en("Ende ({label}) „{nid}“", "End ({label}) “{nid}”", label=flow.end_label(node), nid=nid),
                            "Ende im Ablauf", get_cases,
                            put_cases, role=lambda path, names=names: names.get(path, path)))
        elif node.get("t") == "end":
            nid = node["id"]

            def get_end(code, nid=nid):
                n, _ = flow.find(flow.tree(survey), nid)
                return {"screenout": ((n or {}).get("text") or {}).get(code, "")}

            def put_end(code, v, nid=nid):
                import copy

                root = copy.deepcopy(flow.tree(survey))
                n, _ = flow.find(root, nid)
                n["text"] = {**(n.get("text") or {}), code: v.get("screenout", "")}
                survey.flow = root
            out.append(Unit(f"flow:{nid}", "general", de_en("{kind} „{name}“", "{kind} “{name}”", kind=flow.END_KINDS[flow.end_kind(node)],
                                                             name=node.get('name') or nid),
                            "Ende im Ablauf", get_end, put_end))
        elif node.get("t") == "ext" and (node.get("text") or {}).get(survey.default_language):
            # an external section with a text of its own on the page before the redirect (external.py)
            nid = node["id"]

            def get_ext(code, nid=nid):
                n, _ = flow.find(flow.tree(survey), nid)
                return {"text": ((n or {}).get("text") or {}).get(code, "")}

            def put_ext(code, v, nid=nid):
                import copy

                root = copy.deepcopy(flow.tree(survey))
                n, _ = flow.find(root, nid)
                n["text"] = {**(n.get("text") or {}), code: v.get("text", "")}
                survey.flow = root
            out.append(Unit(f"flow:{nid}", "general", de_en("Externer Abschnitt „{name}“", "External section “{name}”",
                                                             name=node.get('name') or node.get('code')),
                            "Seite vor der Weiterleitung", get_ext, put_ext))
        elif node.get("t") == "quota" and "full" in node:  # a "Quotenprüfung" of a survey started before (quotas.is_legacy)
            nid = node["id"]

            def get_full(code, nid=nid):
                n, _ = flow.find(flow.tree(survey), nid)
                return {"quotafull": ((n or {}).get("text") or {}).get(code, "")}

            def put_full(code, v, nid=nid):
                import copy

                root = copy.deepcopy(flow.tree(survey))
                n, _ = flow.find(root, nid)
                n["text"] = {**(n.get("text") or {}), code: v.get("quotafull", "")}
                survey.flow = root
            out.append(Unit(f"flow:{nid}", "general", undo.both("Quotenberechnung: Text bei voller Quote", "Quota calculation: text when the quota is full"), "Ende im Ablauf", get_full, put_full))
        elif node.get("t") == "loop" and node.get("source") == "factors":  # vignettes (app/vignettes.py)
            nid = node["id"]
            from app import vignettes

            def get_vig(code, nid=nid):
                n, _ = flow.find(flow.tree(survey), nid)
                return vignettes.translation_get(survey, n or {}, code)

            def put_vig(code, v, nid=nid):
                import copy

                root = copy.deepcopy(flow.tree(survey))
                n, _ = flow.find(root, nid)
                vignettes.translation_put(survey, n, code, v)
                survey.flow = root
            names = {}
            for a in node.get("attributes") or []:
                names[f"a.{a['id']}"] = de_en("Faktor {code}", "Factor {code}", code=a['code'])
                for i, l in enumerate(a.get("levels", []), start=1):
                    names[f"l.{l['id']}"] = de_en("{code}, Stufe {i}", "{code}, level {i}", code=a['code'], i=i)
                    names[f"d.{l['id']}"] = de_en("{code}, Stufe {i}, Definition", "{code}, level {i}, definition", code=a['code'], i=i)

            def vig_role(path, names=names):
                return names.get(path, path)
            out.append(Unit(f"loop:{nid}", "general", de_en("Faktoren der Schleife „{name}“", "Factors of the loop “{name}”", name=node.get('name') or nid),
                            "Faktoren und Stufen", get_vig, put_vig, role=vig_role))
        elif node.get("t") == "loop":
            nid = node["id"]
            cols = [c for c in node.get("columns", []) if not c.get("hidden")]
            if not cols:
                continue

            def get_loop(code, nid=nid, cols=cols):
                n, _ = flow.find(flow.tree(survey), nid)
                return {r["id"]: {c["id"]: ((r.get("values") or {}).get(c["id"]) or {}).get(code, "") for c in cols}
                        for r in (n or {}).get("rows", [])}

            def put_loop(code, v, nid=nid):
                import copy

                n, _ = flow.find(flow.tree(survey), nid)
                loop = copy.deepcopy(n)
                for r in loop.get("rows", []):
                    for cid, text in (v.get(r["id"]) or {}).items():
                        cell = dict((r.setdefault("values", {})).get(cid) or {})
                        cell[code] = text
                        r["values"][cid] = cell
                loops.save_node(survey, nid, loop)
            rows_n = {r["id"]: n for n, r in enumerate(node.get("rows", []), start=1)}
            col_names = {c["id"]: c["name"] for c in cols}

            def loop_role(path, rows_n=rows_n, col_names=col_names):
                rid, _, cid = path.partition(".")
                return de_en("Zeile {n} · {col}", "Row {n} · {col}", n=rows_n.get(rid, '?'), col=col_names.get(cid, cid))
            out.append(Unit(f"loop:{nid}", "general", de_en("Tabelle der Schleife „{name}“", "Table of the loop “{name}”", name=node.get('name') or nid),
                            "Werte einer Schleifentabelle; Spalten: " + ", ".join(c["name"] for c in cols), get_loop, put_loop,
                            role=loop_role))
    return out


# ---- nested texts ----------------------------------------------------------------------------------------


def flatten(obj, prefix=""):
    """[(path, text)] of every non-empty string."""
    out = []
    if isinstance(obj, str):
        if obj.strip():
            out.append((prefix, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for n, v in enumerate(obj):
            out += flatten(v, f"{prefix}.{n}" if prefix else str(n))
    return out


def get_path(obj, path):
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isascii() and part.isdigit() and len(part) < 12 and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj if isinstance(obj, str) else None


def set_path(target, source, path, value):
    """Put `value` at `path` into `target`, building dicts and lists like those of `source` on the way."""
    import copy

    target = copy.deepcopy(target) if target else ({} if not isinstance(source, list) else [])
    parts = path.split(".")
    node, src = target, source
    for n, part in enumerate(parts):
        last = n == len(parts) - 1
        key = int(part) if isinstance(src, list) else part
        nxt = src[key] if isinstance(src, list) else (src or {}).get(part)
        if isinstance(node, list):
            while len(node) <= key:
                node.append("" if last or isinstance(nxt, str) else ({} if isinstance(nxt, dict) else []))
        if last:
            node[key] = value
        else:
            child = node[key] if isinstance(node, list) else node.get(key)
            if not isinstance(child, (dict, list)):
                child = [] if isinstance(nxt, list) else {}
                node[key] = child
            node, src = child, nxt
    return target


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def role(path, unit=None):
    """A readable name for a field: "Antwortoption", "Skalenbeschriftung 3", "Zeile 2 · robot"."""
    if unit is not None and unit.role:
        return unit.role(path)
    parts, out, names = path.split("."), [], roles()
    for n, p in enumerate(parts):
        if p in names:
            out.append(names[p])
        elif p.isdigit() and out:
            out[-1] += f" {int(p) + 1}"
    return " · ".join(out) or path


def diff_html(old, new):
    """Word diff of the source then and now: removed words struck, new ones marked."""
    a, b = re.findall(r"\S+|\s+", old or ""), re.findall(r"\S+|\s+", new or "")
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(str(escape("".join(a[i1:i2]))))
            continue
        if i2 > i1:
            out.append(f"<del>{escape(''.join(a[i1:i2]))}</del>")
        if j2 > j1:
            out.append(f"<ins>{escape(''.join(b[j1:j2]))}</ins>")
    return Markup("".join(out))


# ---- status ------------------------------------------------------------------------------------------------


def _marks(survey, lang):
    rows = db.session.scalars(db.select(TranslationMark).filter_by(survey_id=survey.id, lang=lang))
    return {(m.unit, m.path): m for m in rows}


def _mark(survey, marks, unit, lang, path, source, ai):
    m = marks.get((unit, path))
    if m is None:  # two requests at the same moment make one row (audit 2026-10-10, C4)
        m = get_or_create(TranslationMark, known_missing=True, survey_id=survey.id, unit=unit, lang=lang, path=path)
        marks[(unit, path)] = m
    m.source, m.ai, m.updated_at = source, ai, utcnow()
    return m


RICH_ITEM_TEXTS = {"body": "block", "product": "block", "explain_intro": "block"}


def rich_mode(unit, path):
    """How participants see a text formatted, for the formatting field of the page (app/static/richtext.js):
    "inline" (question texts, inline_rich), "block" (paragraphs and lists, rich), "gaps" (the text of a cloze
    item: block with gaps) or "" (shown as typed: options, labels, titles)."""
    if unit.item is not None:
        if path == "question":
            return "block" if unit.item.type == "info" else "inline"
        if path == "text" and unit.item.type == "cloze":
            return "gaps"
        return RICH_ITEM_TEXTS.get(path, "")
    return "block" if unit.key == "end" or unit.key.startswith("flow:") else ""


def _on_open(survey):
    """(write, fill) for a page that is only being opened: looking writes nothing. Marks are stamped only for
    someone who may edit, and the texts of a question are completed only while the survey is a draft (a survey
    in the field or closed changes through what someone does, not through what someone looks at). Outside a
    GET (saving, confirming, the check at the start) everything is written as before."""
    from flask import has_request_context

    from app.models import Survey

    if not has_request_context() or request.method not in ("GET", "HEAD"):
        return True, True
    write = current_user.is_authenticated and survey.can(current_user, "editor")
    return write, write and survey.status == Survey.DRAFT


def overview(survey, lang, write=True, fill=True):
    """The units with their fields and status for one target language; marks translations made before this
    page existed as based on the current source (once). write, fill: see _on_open; without write the status
    is the same, but nothing is stored."""
    default = survey.default_language
    marks = _marks(survey, lang)
    out, counts = [], {k: 0 for k in STATUS_LABELS}
    stamped = False
    for u in units(survey, lang, fill and write):
        stamped = stamped or u.filled
        source, target = u.get(default), u.get(lang)
        standard = shown = None
        if u.item is not None and not u.validated:
            # the standard wordings of the element (buttons of the consent, federal states, ...): not stored, or a
            # copy still unchanged; each language takes its own, so there is nothing to translate (S9)
            from app import item_texts

            source = u.item.t(default)
            standard = (item_texts.defaults(u.item.kind, default), item_texts.former(u.item.kind, default))
            shown = u.item.t(lang)
        for path, text in flatten(source):
            mine = get_path(target, path) or ""
            m = marks.get((u.key, path))
            if standard and not mine.strip() and text in (get_path(standard[0], path), get_path(standard[1], path)):
                status, mine = "default", get_path(shown, path) or text
            elif u.validated:
                status = "validated"
            elif not mine.strip():
                status = "missing"
            elif m is None:
                status = "ok"
                if write:
                    m, stamped = _mark(survey, marks, u.key, lang, path, text, False), True
            elif _norm(m.source) != _norm(text):
                status = "changed"
            else:
                status = "ai" if m.ai else "ok"
            counts[status] += 1
            u.fields.append({"path": path, "role": role(path, u), "source": text, "target": mine, "status": status,
                             "rich": rich_mode(u, path), "ai": bool(m is not None and m.ai),
                             "diff": diff_html(m.source, text) if status == "changed" else None})
        if u.fields:
            out.append(u)
    if stamped:
        db.session.commit()
    return out, counts


def stamp_changes(survey, unit_key, lang, before, after):
    """After an edit in a translation view elsewhere (item editor in another language): the changed fields count
    as translated by a person from the current source."""
    if lang == survey.default_language:
        return
    u = next((x for x in units(survey, lang) if x.key == unit_key), None)
    if u is None:
        return
    source = u.get(survey.default_language)
    old, new = dict(flatten(before)), dict(flatten(after))
    marks = _marks(survey, lang)
    for path, text in new.items():
        if old.get(path) != text and get_path(source, path):
            _mark(survey, marks, unit_key, lang, path, get_path(source, path), False)


# ---- AI ----------------------------------------------------------------------------------------------------

INSTRUCTIONS = """You translate the texts of an online questionnaire for social science research from {source} into
{target}. The texts are what participants read: questions, answer options, scale labels, buttons, info texts.

Translate faithfully and idiomatically, as a professional survey translator would (TRAPD standards): same
meaning, same scope and tone, no explanations added or left out. Keep the form of address of the source (formal
or informal) and use it consistently. Scale labels and answer options must stay parallel to each other. Use the
same term for the same concept throughout; follow the glossary of confirmed translations.

Keep exactly as they are: {{{{placeholders}}}} in double braces (in {{{{name|text}}}} translate only the text after
the bar: it stands in for an unanswered question), the markup **bold**, *italic*, `code`, ~sub~,
^sup^, <u>, <small>, <sub>, <sup>, <br>, line breaks, list dashes "- " at the start of lines, and gaps written as
square brackets "[ ]" (same number of gaps, same spaces inside). Names of products, places and people stay.

Answer with ONE JSON object and nothing else: {{"<id>": "<translation>", ...}} with every id of the input."""


def _context(survey):
    lines = [f"Survey title: {survey.title}"]
    if survey.description:
        lines.append(f"Description: {survey.description}")
    if getattr(survey, "research_question", ""):
        lines.append(f"Research question / hypotheses: {survey.research_question}")
    return "\n".join(lines)


def pending(survey, lang, admin=True):
    """[(unit, field)] the AI should translate: what is missing, and its own translations that nobody confirmed
    and whose source has changed. A translation a person wrote or confirmed stays when its source changes (a
    typo fixed there must not replace it); it keeps the status "changed" until someone looks at it.
    admin: False for someone who may not change admin units: their fields are left out."""
    rows, _ = overview(survey, lang)
    return [(u, f) for u in rows for f in u.fields if _for_ai(f) and (admin or not u.admin) and _may_go_to_ai(u)]


def _may_go_to_ai(u):
    """False for an element of a construct whose wording may go only to the participants (constructs.NOT_SHARED,
    APA PsycTests): neither translated by the AI nor sent along as glossary (copyright audit 2026-10-16, U4)."""
    from app import constructs

    meta = ((u.item.config or {}).get("construct") or {}) if u.item is not None else {}
    return meta.get("key") not in constructs.NOT_SHARED


def _for_ai(f):
    return f["status"] == "missing" or (f["status"] == "changed" and f["ai"])


def translate_next(survey, lang, admin=True):
    """Translate the next chunk of open fields with one AI request. Returns (translated, still open).
    admin: see pending."""
    from app import ai, ai_privacy
    from app.ai_scales import LANGUAGE as LANGUAGE_NAMES
    from app.items.base import placeholders

    todo = pending(survey, lang, admin)
    if not todo:
        return 0, 0
    chunk, size = [], 0
    for u, f in todo:
        if chunk and size + len(f["source"]) > CHUNK_CHARS:
            break
        chunk.append((u, f))
        size += len(f["source"])
    masker = ai_privacy.TranslationMask(survey)
    entries, by_id, masked_sources = [], {}, {}
    for n, (u, f) in enumerate(chunk, start=1):
        key = f"t{n}"
        by_id[key] = (u, f)
        entry = {"id": key, "where": f"{u.kind} › {f['role']}", "text": masker.mask(f["source"])}
        masked_sources[key] = entry["text"]
        question = get_path(u.get(survey.default_language), "question")
        if question and f["path"] != "question":
            entry["question"] = masker.mask(question)[:300]
        entries.append(entry)
    rows, _ = overview(survey, lang)
    glossary = [(f["source"], f["target"]) for u in rows if _may_go_to_ai(u) for f in u.fields
                if f["status"] in ("ok", "validated") and len(f["source"]) <= 60][:80]
    prompt = (masker.mask(_context(survey)) + "\n\nGlossary of confirmed translations (source => target):\n"
              + "\n".join(f"{masker.mask(a)} => {masker.mask(b)}" for a, b in glossary) + "\n\nTexts (JSON):\n" + json.dumps(entries, ensure_ascii=False))
    names = LANGUAGE_NAMES
    answer = ai.generate_text("Preserve every __ASKURI_ placeholder exactly, including repetitions. " + INSTRUCTIONS.format(source=names.get(survey.default_language, survey.default_language),
                                                  target=names.get(lang, lang)), prompt, purpose="translation")
    # read like every other AI answer (app/ai.py): a raw line break inside a text (consent, end texts) is kept, not
    # a reason to throw the whole section away
    data = ai.json_answer(answer)
    marks, done, units_by_key = _marks(survey, lang), 0, {}
    for key, (u, f) in by_id.items():
        text = data.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        text = masker.restore(text, masked_sources[key])
        if text is None:
            continue
        if f["source"].count("[") != text.count("["):  # cloze gaps must survive, else it stays open
            continue
        if placeholders(f["source"]) != placeholders(text):
            continue  # a {{placeholder}} lost, renamed or invented: participants would read a gap or the braces
        units_by_key.setdefault(u.key, [u, u.get(lang)])
        units_by_key[u.key][1] = set_path(units_by_key[u.key][1], u.get(survey.default_language), f["path"], text.strip())
        _mark(survey, marks, u.key, lang, f["path"], f["source"], True)
        done += 1
    for u, value in units_by_key.values():
        u.set(lang, value)
    db.session.commit()
    if not done:
        raise ai.AIError(de_en("Die KI hat keinen der Texte übersetzt. Bitte erneut versuchen.", "The AI did not translate any of the texts. Please try again."))
    return done, len(pending(survey, lang, admin))


# ---- views ---------------------------------------------------------------------------------------------------


def _target(survey):
    others = [lang for lang in survey.languages if lang != survey.default_language]
    lang = request.values.get("lang")
    return lang if lang in others else (others[0] if others else None), others


@bp.route("/")
@login_required
def index(survey_id):
    from app.surveys import LANGUAGE_NAMES
    from app.surveys import get_survey

    survey = get_survey(survey_id)
    lang, others = _target(survey)
    rows, counts = overview(survey, lang, *_on_open(survey)) if lang else ([], {})
    is_admin = survey.can(current_user, "admin")
    groups = []
    for u in rows:
        if not groups or groups[-1]["key"] != u.group:
            block = None
            if u.group.startswith("block:"):
                block = next((b for b in survey.blocks if f"block:{b.id}" == u.group), None)
            groups.append({"key": u.group, "block": block, "units": []})
        groups[-1]["units"].append(u)
    return render_template("surveys/translation.html", survey=survey, target=lang, others=others, groups=groups,
                           counts=counts, total=sum(counts.values()), names=LANGUAGE_NAMES, status_labels=status_labels(),
                           open_n=sum(1 for u in rows for f in u.fields if _for_ai(f) and (is_admin or not u.admin)),
                           check_n=sum(1 for u in rows for f in u.fields if f["status"] in ("ai", "changed") and (is_admin or not u.admin)),
                           can_edit=survey.can(current_user, "editor"), is_admin=is_admin, active_tab="translation")


def _find(survey, lang, unit_key):
    u = next((x for x in units(survey, lang) if x.key == unit_key), None)
    if u is None:
        abort(404)
    return u


@bp.route("/fieldwork", methods=["POST"])
@login_required
def save_field(survey_id):
    """One translation typed on the page (saved when the field is left)."""
    from app.surveys import get_survey

    survey = get_survey(survey_id, "editor")
    lang, _ = _target(survey)
    if not lang:
        abort(400)
    u = _find(survey, lang, request.form.get("unit", ""))
    if not may_change(survey, u):
        return jsonify(error=de_en("Dafür fehlen die Rechte: „{label}“ ändert nur die Rolle „Admin“, wie in den Einstellungen.",
                                    "You lack the rights for this: only the role “Admin” changes “{label}”, as in the settings.", label=u.label)), 403
    path = request.form.get("path", "")
    # an element's texts with its standard wordings, which are not stored (S9)
    model = u.item.t(survey.default_language) if u.item is not None else u.get(survey.default_language)
    source = get_path(model, path)
    if u.validated or source is None:
        abort(400)
    text = request.form.get("text", "").strip()
    from app import undo
    from app.surveys import LANGUAGE_NAMES

    step = undo.begin(survey)
    u.set(lang, set_path(u.get(lang), model, path, text))
    from app import carry

    carry.sync_survey(survey)  # carried options follow the wording of the question they come from
    from app import linked

    linked.save_survey(survey, current_user)  # an open linked element: the other uses in drafts take the translation
    # only what belongs to the questionnaire (questions, block names, flow texts) is a step; title and end texts are settings
    step.done(undo.both("Übersetzung ({name}) geändert: {label}", "Translation ({name}) changed: {label}",
                        name=LANGUAGE_NAMES.get(lang, lang), label=u.label), page="translation", node=lang)
    marks = _marks(survey, lang)
    if text:
        _mark(survey, marks, u.key, lang, path, source, False)
    elif (u.key, path) in marks:
        db.session.delete(marks[(u.key, path)])
    db.session.commit()
    _, counts = overview(survey, lang)
    return jsonify(status="ok" if text else "missing", label=status_labels()["ok" if text else "missing"], counts=counts)


@bp.route("/confirm", methods=["POST"])
@login_required
def confirm(survey_id):
    """Confirm what is translated (a block, the general texts or everything): AI translations count as checked,
    changed sources as still matching. Missing translations stay missing."""
    from app.surveys import get_survey

    survey = get_survey(survey_id, "editor")
    lang, _ = _target(survey)
    scope = request.form.get("scope", "all")
    rows, _ = overview(survey, lang) if lang else ([], {})
    marks, n = _marks(survey, lang), 0
    for u in rows:
        if scope != "all" and u.group != scope and u.key != scope:
            continue
        if not may_change(survey, u):  # the admins' texts stay as they are for "Bearbeiten" (Unit.admin)
            continue
        for f in u.fields:
            if f["status"] in ("ai", "changed"):
                _mark(survey, marks, u.key, lang, f["path"], f["source"], False)
                n += 1
    db.session.commit()
    if n == 1:
        flash(de_en("1 Übersetzung bestätigt.", "1 translation confirmed."), "success")
    elif n:
        flash(de_en("{n} Übersetzungen bestätigt.", "{n} translations confirmed.", n=n), "success")
    else:
        flash(de_en("Nichts zu bestätigen.", "Nothing to confirm."), "info")
    anchor = f"#{scope.replace(':', '-')}" if scope != "all" else ""
    return redirect(url_for("translations.index", survey_id=survey.id, lang=lang) + anchor)


@bp.route("/ai", methods=["POST"])
@login_required
def ai_translate(survey_id):
    """Translate the next chunk of open texts; the page calls this until nothing is open."""
    from app import ai
    from app.surveys import get_survey

    survey = get_survey(survey_id, "editor")
    lang, _ = _target(survey)
    if not lang:
        abort(400)
    from app import undo
    from app.surveys import LANGUAGE_NAMES

    step = undo.begin(survey)
    try:
        done, left = translate_next(survey, lang, survey.can(current_user, "admin"))
    except ai.AIError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 502
    from app import carry

    carry.sync_survey(survey)
    from app import linked

    linked.save_survey(survey, current_user)
    # the page calls this chunk by chunk: one run is one step
    step.done(undo.both("KI-Übersetzung ({name})", "AI translation ({name})", name=LANGUAGE_NAMES.get(lang, lang)), page="translation", node=lang, merge=f"ai-translation:{lang}")
    db.session.commit()
    return jsonify(done=done, left=left)


def findings(survey):
    """For the check before the start (app/design.py): per other language what is missing (blocks the start: the
    participants would see the default language there), changed or translated by the AI and not confirmed."""
    from app.surveys import LANGUAGE_NAMES

    out = []
    for lang in survey.languages:
        if lang == survey.default_language:
            continue
        _, counts = overview(survey, lang, *_on_open(survey))
        name = LANGUAGE_NAMES.get(lang, lang)
        if counts["missing"]:
            n = counts["missing"]
            out.append(("error", de_en("Übersetzung {name} unvollständig: {n} Text fehlen; Teilnehmende sähen dort die "
                                       "Ausgangssprache. Reiter „Übersetzung“.",
                                       "Translation {name} incomplete: {n} text missing; participants would see the source "
                                       "language there. Tab “Translation”.", name=name, n=n) if n == 1 else
                                 de_en("Übersetzung {name} unvollständig: {n} Texte fehlen; Teilnehmende sähen dort die "
                                       "Ausgangssprache. Reiter „Übersetzung“.",
                                       "Translation {name} incomplete: {n} texts missing; participants would see the source "
                                       "language there. Tab “Translation”.", name=name, n=n)))
        if counts["changed"]:
            n = counts["changed"]
            out.append(("warning", de_en("Übersetzung {name}: {n} Text seit der Übersetzung geändert, bitte prüfen "
                                         "(Reiter „Übersetzung“).",
                                         "Translation {name}: {n} text changed since the translation, please check "
                                         "(tab “Translation”).", name=name, n=n) if n == 1 else
                                   de_en("Übersetzung {name}: {n} Texte seit der Übersetzung geändert, bitte prüfen "
                                         "(Reiter „Übersetzung“).",
                                         "Translation {name}: {n} texts changed since the translation, please check "
                                         "(tab “Translation”).", name=name, n=n)))
        if counts["ai"]:
            n = counts["ai"]
            out.append(("warning", de_en("Übersetzung {name}: {n} KI-Übersetzung noch nicht bestätigt (Reiter „Übersetzung“).",
                                         "Translation {name}: {n} AI translation not yet confirmed (tab “Translation”).",
                                         name=name, n=n) if n == 1 else
                                   de_en("Übersetzung {name}: {n} KI-Übersetzungen noch nicht bestätigt (Reiter „Übersetzung“).",
                                         "Translation {name}: {n} AI translations not yet confirmed (tab “Translation”).",
                                         name=name, n=n)))
    return out
