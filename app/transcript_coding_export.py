"""Interviews kodieren, stage 4: an analysis as REFI-QDA files (app/transcript_coding.py; concept "Interviews
kodieren", all as recommended, Ralf 2026-10-27), for MAXQDA, ATLAS.ti, NVivo and QDA Miner.

- The project (.qdpx): the analysis's category system (main categories with their subcategories nested, candidates on
  top level; definition, delimitation and anchor example as the description), each interview with a transcript as a
  text document with its coded passages (PlainTextSelection with a Coding of its category, positions in characters of
  the transcript as stored), the interviews as cases with the case variables of the guide's own export
  (app/interview_qdpx.py), and each coder as a user, so that a QDA program shows who coded what. Passages that can no
  longer be placed (app/transcript_coding_view.py) are left out; merged categories count for the one they went into.
- The category system (.qdc): the same tree, nothing about interviews or people.
- The category system as an Excel table (.xlsx, app/xlsx.py), the codebook after Mayring: sheet "Kategoriensystem"
  with number (1, 1.2, 1.2.3 as in the coding guide), level, category, definition, anchor example with where it
  stands, coding rule/delimitation, passages and former names, the candidates below without a number; sheet "Stellen"
  with each coded passage that can be placed (as in the .qdpx): number and name of its category, interview, where it
  stands ("B03, Pos. 12, 00:14:32", app/transcript_coding_view.py, spot), quote and coder; last the sheet
  "Überschneidungen", category × category over every interview (app/transcript_coding_overlap.py).

The GUIDs of categories are their own (CodingCategory.guid: from the guide as in app/qdc.py, else new), so a category
system read in again matches; the others are derived from the guide, the analysis and the coding, so the same export
gives the same GUIDs. Only the team of the guide exports (coders see no exports, privacy concept). Unlike the guide's
own project, the transcript is the one coded, without the lines of the tasks, so that the positions fit.

Format: REFI-QDA Project Exchange (urn:QDA-XML:project:1.0) and Codebook Exchange (urn:QDA-XML:codebook:1.0),
https://www.qdasoftware.org."""
import io
import uuid
import zipfile
from xml.etree import ElementTree as ET

from flask import send_file

from app import brand
from app.extensions import db
from app.lang import de_en
from app.transcript_coding import _in, bp, chain, live, numbers, signed_in_or_coder, tree
from app.transcript_coding_view import (anchor_spots, clean_quote, example_spots, has_transcript, paragraphs, placed,
                                        spot, transcript)

PROJECT_NS = "urn:QDA-XML:project:1.0"
CODEBOOK_NS = "urn:QDA-XML:codebook:1.0"
COLOURS = ("#8A4B14", "#2957A4", "#1F6B2A", "#7A3FD1", "#B3261E", "#0B6E86", "#7A5B00")


def _guid(survey, project, *parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"askurious-coding:{survey.uid}:{project.id}:" + ":".join(str(p) for p in parts))).upper()


def _el(parent, ns, tag, attrs=None, text=None):
    e = ET.SubElement(parent, f"{{{ns}}}{tag}", attrs or {})
    if text is not None:
        e.text = text
    return e


def description(cat, where=""):
    """The text of a category for a QDA program; `where`: the place of its anchor example (anchor_spots)."""
    lines = []
    if cat.definition:
        lines.append(cat.definition)
    if cat.delimitation:
        lines.append(de_en("Abgrenzung: ", "Delimitation: ") + cat.delimitation)
    if (cat.anchor or {}).get("quote"):
        lines.append(de_en("Ankerbeispiel: ", "Anchor example: ") + f"„{clean_quote(cat.anchor['quote'])}“ ({where or cat.anchor.get('label', '')})")
    if cat.former_names:
        lines.append(de_en("Früher auch: ", "Formerly also: ") + ", ".join(cat.former_names))
    return "\n\n".join(lines)


def codes(project, parent, ns, notes=None):
    """The tree of categories under `parent` (a CodeBook): {category id: guid}. `notes`: {category id: [guid of a
    Note]} for the memos at a category (NoteRef)."""
    holder = _el(parent, ns, "Codes")
    mains, candidates = tree(project)
    spots = anchor_spots(project)
    guids = {}

    def code(where, cat, colour):
        attrs = {"guid": cat.guid.upper(), "name": cat.name, "isCodable": "true", "color": cat.colour or colour}
        e = _el(where, ns, "Code", attrs)
        text = description(cat, spots.get(cat.id, ""))
        if text:
            _el(e, ns, "Description", text=text)
        for note in (notes or {}).get(cat.id, []):  # before the codes below it, as the schema wants
            _el(e, ns, "NoteRef", {"targetGUID": note})
        guids[cat.id] = attrs["guid"]
        return e

    for n, (main, subs) in enumerate(mains):
        colour = main.colour or COLOURS[n % len(COLOURS)]
        at = {main.id: code(holder, main, colour)}
        for s in subs:  # nested as deep as they stand (REFI-QDA allows any depth)
            at[s.id] = code(at.get(s.parent_id, at[main.id]), s, colour)
    for c in candidates:
        code(holder, c, COLOURS[-1])
    return guids


def build_qdc(project):
    ET.register_namespace("", CODEBOOK_NS)
    root = ET.Element(f"{{{CODEBOOK_NS}}}CodeBook", {"origin": brand.name()})
    codes(project, root, CODEBOOK_NS)
    return ('<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")).encode("utf-8")


def _memos(project):
    """The memos of the analysis that go into an export (the team's: all that are not deleted), oldest first."""
    from app.models import CodingMemo

    return list(db.session.scalars(db.select(CodingMemo).where(CodingMemo.project_id == project.id,
                                                               CodingMemo.deleted_at.is_(None))
                                   .order_by(CodingMemo.created_at, CodingMemo.id)))


def build_qdpx(project, survey):
    from app import interview_qdpx
    from app.models import CodingMemo, utcnow
    from app.transcript_coding import resolver

    ET.register_namespace("", PROJECT_NS)
    ns = PROJECT_NS
    now = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = _rows(survey)
    coded = {r.id: placed(r, project)[0] for r in rows}
    owner = _guid(survey, project, "user", "askurious")
    root = ET.Element(f"{{{ns}}}Project", {"name": f"{survey.title} – {project.name}"[:200], "origin": brand.name(),
                                           "creatingUserGUID": owner, "creationDateTime": now})
    users = _el(root, ns, "Users")
    _el(users, ns, "User", {"guid": owner, "name": brand.name()})
    people = {}
    for items in coded.values():
        for c in items:
            people.setdefault(c.get("by") or "", c.get("by_name") or de_en("unbekannt", "unknown"))
    # memos as REFI-QDA notes: at a code, a text source or a selection (NoteRef); only those whose place is exported
    placed_ids = {c["id"] for items in coded.values() for c in items}
    resolve = resolver(project)
    memo_at = {"category": {}, "interview": {}, "passage": {}}
    memos = []
    for m in _memos(project):
        key = (resolve(m.category_id) if m.kind == CodingMemo.CATEGORY else m.response_id if m.kind == CodingMemo.INTERVIEW
               else m.coding_id)
        if key is None or (m.kind == CodingMemo.INTERVIEW and m.response_id not in coded) or (
                m.kind == CodingMemo.PASSAGE and m.coding_id not in placed_ids):
            continue
        note = _guid(survey, project, "memo", m.id)
        memo_at[m.kind].setdefault(key, []).append(note)
        memos.append((note, m))
        people.setdefault(m.author_key or "", m.author_name or de_en("unbekannt", "unknown"))
    # the thematic case summaries of this analysis as notes at the transcript ("Fallzusammenfassung: <main category>")
    from app.transcript_coding_cases import meta_of

    summary_at, summaries = {}, []
    for r in rows:
        texts = (r.interview or {}).get("summaries") or {}
        for main in [m for m, _subs in tree(project)[0]]:
            key = f"c{project.id}:{main.id}"
            if r.id in coded and texts.get(key):
                meta = meta_of(r, key) or {}
                note = _guid(survey, project, "summary", f"{r.id}:{main.id}")
                summary_at.setdefault(r.id, []).append(note)
                summaries.append((note, main.name, texts[key], meta.get("by") or "", meta.get("at") or ""))
                if meta.get("by"):
                    people.setdefault(meta["by"], meta.get("by_name") or de_en("unbekannt", "unknown"))
    user_of = {}
    for key, name in sorted(people.items(), key=lambda kv: kv[1].casefold()):
        user_of[key] = _guid(survey, project, "user", key or "none")
        _el(users, ns, "User", {"guid": user_of[key], "name": name[:200]})
    codebook = _el(root, ns, "CodeBook")
    guids = codes(project, codebook, ns, memo_at["category"])
    variables = interview_qdpx.variables(survey)
    if variables:
        holder = _el(root, ns, "Variables")
        for key, name, _value in variables:
            _el(holder, ns, "Variable", {"guid": _guid(survey, project, "var", key), "name": name, "typeOfVariable": "Text"})
    files = {}
    if rows:
        cases = _el(root, ns, "Cases")
        for r in rows:
            case = _el(cases, ns, "Case", {"guid": _guid(survey, project, "case", r.id), "name": (r.interview or {}).get("label", str(r.id))})
            for key, _name, value in variables:
                vv = _el(case, ns, "VariableValue")
                _el(vv, ns, "VariableRef", {"targetGUID": _guid(survey, project, "var", key)})
                _el(vv, ns, "TextValue", text=value(r) or "")
            _el(case, ns, "SourceRef", {"targetGUID": _guid(survey, project, "source", r.id)})
        sources = _el(root, ns, "Sources")
        for r in rows:
            guid = _guid(survey, project, "source", r.id)
            text = transcript(r).replace("\r\n", "\n")
            src = _el(sources, ns, "TextSource", {"guid": guid, "name": (r.interview or {}).get("label", str(r.id)),
                                                  "plainTextPath": f"internal://{guid}.txt", "creatingUser": owner,
                                                  "creationDateTime": now})
            for c in coded[r.id]:
                if c["cat"] not in guids:
                    continue
                user = user_of.get(c.get("by") or "", owner)
                at = (c.get("at") or now[:19]).replace("Z", "")[:19] + "Z"
                sel = _el(src, ns, "PlainTextSelection", {"guid": _guid(survey, project, "sel", c["id"]),
                                                          "name": clean_quote(c.get("quote"))[:60],
                                                          "startPosition": str(c["start"]), "endPosition": str(c["end"]),
                                                          "creatingUser": user, "creationDateTime": at})
                if c.get("origin") == "ai":  # an accepted AI suggestion stays recognisable
                    _el(sel, ns, "Description", text=de_en("KI-Vorschlag, übernommen von {who}", "AI suggestion, accepted by {who}",
                                                           who=c.get("accepted_by") or c.get("by_name") or ""))
                coding = _el(sel, ns, "Coding", {"guid": _guid(survey, project, "coding", c["id"]), "creatingUser": user,
                                                 "creationDateTime": at})
                _el(coding, ns, "CodeRef", {"targetGUID": guids[c["cat"]]})
                for note in memo_at["passage"].get(c["id"], []):
                    _el(sel, ns, "NoteRef", {"targetGUID": note})
            for note in memo_at["interview"].get(r.id, []) + summary_at.get(r.id, []):  # after the selections
                _el(src, ns, "NoteRef", {"targetGUID": note})
            files[f"Sources/{guid}.txt"] = text.encode("utf-8")
    if memos or summaries:
        holder = _el(root, ns, "Notes")
        for note, m in memos:
            n = _el(holder, ns, "Note", {"guid": note, "name": ("Memo: " + " ".join(m.text.split()))[:60],
                                         "creatingUser": user_of.get(m.author_key or "", owner),
                                         "creationDateTime": m.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")})
            _el(n, ns, "PlainTextContent", text=m.text)
        for note, name, text, by, at in summaries:
            n = _el(holder, ns, "Note", {"guid": note, "name": (de_en("Fallzusammenfassung: ", "Case summary: ") + name)[:60],
                                         "creatingUser": user_of.get(by, owner),
                                         "creationDateTime": (at[:19] + "Z") if at else now})
            _el(n, ns, "PlainTextContent", text=text)
    _el(root, ns, "Description", text=de_en("Auswertung „{name}“ des Interviewleitfadens „{title}“, exportiert aus {brand} am {day}.",
                                            "Analysis “{name}” of the interview guide “{title}”, exported from {brand} on {day}.",
                                            name=project.name, title=survey.title, brand=brand.name(), day=now[:10])
        + (("\n" + de_en("Forschungsfrage: ", "Research question: ") + project.question) if project.question else ""))
    xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.qde", xml.encode("utf-8"))
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _rows(survey):
    from app import interview_data

    return [r for r in interview_data.interviews(survey, source="real") if has_transcript(r)]


def build_xlsx(project, survey):
    """The category system and its coded passages as an Excel file (see the top of this module)."""
    from app import xlsx

    mains, candidates = tree(project)
    number = numbers(mains)
    spots = anchor_spots(project)
    passages = []  # (category id, interview label, where, quote, coder)
    pairs = []  # (interview, placed coding) for the further examples
    for r in _rows(survey):
        text, label = transcript(r), (r.interview or {}).get("label", str(r.id))
        paras = paragraphs(text)
        for c in placed(r, project)[0]:
            pairs.append((r, c))
            who = c.get("by_name") or ""
            if c.get("origin") == "ai":
                who = de_en("{who} (KI-Vorschlag übernommen)", "{who} (AI suggestion accepted)", who=c.get("accepted_by") or who)
            passages.append((c["cat"], label, spot(label, text, c["start"], paras), clean_quote(c.get("quote")), who))
    by_id = {c.id: c for c in live(project)}
    own, total = {}, {}
    for cid, *_rest in passages:
        own[cid] = own.get(cid, 0) + 1
        cat = by_id.get(cid)
        for k in ([cat] + chain(cat, by_id)) if cat is not None else []:
            total[k.id] = total.get(k.id, 0) + 1

    more = example_spots(project, pairs)

    def line(cat, num, level):
        anchor = (cat.anchor or {}).get("quote") or ""
        examples = "\n".join(f"„{q}“ ({w})" for q, w in more.get(cat.id, []))
        return [num, level, cat.name, cat.definition or "", clean_quote(anchor), spots.get(cat.id, "") if anchor else "",
                examples, cat.delimitation or "", own.get(cat.id, 0), total.get(cat.id, 0), ", ".join(cat.former_names or [])]

    system = []
    for main, subs in mains:
        system.append(line(main, number[main.id], 1))
        system.extend(line(s, number[s.id], s.level) for s in subs)
    system.extend(line(c, "", de_en("Kandidat", "candidate")) for c in candidates)  # no number: not in the system yet
    head = [de_en("Nummer", "Number"), de_en("Ebene", "Level"), de_en("Kategorie", "Category"), de_en("Definition", "Definition"),
            de_en("Ankerbeispiel", "Anchor example"), de_en("Fundstelle des Ankers", "Location of the anchor"),
            de_en("Weitere Beispiele", "Further examples"),
            de_en("Kodierregel / Abgrenzung", "Coding rule / delimitation"), de_en("Stellen", "Passages"),
            de_en("Stellen mit Unterkategorien", "Passages with subcategories"), de_en("Früher auch", "Formerly also")]
    ids = [c.id for main, subs in mains for c in [main] + subs] + [c.id for c in candidates]
    order = {cid: n for n, cid in enumerate(ids)}
    passages.sort(key=lambda p: (order.get(p[0], len(order)),))  # by category, within it in the order of the interviews
    names = {c.id: c.name for c in by_id.values()}
    rows = [[number.get(cid, ""), names.get(cid, ""), label, where, quote, who]
            for cid, label, where, quote, who in passages]
    head2 = [de_en("Nummer", "Number"), de_en("Kategorie", "Category"), de_en("Interview", "Interview"),
             de_en("Fundstelle", "Location"), de_en("Zitat", "Quote"), de_en("Kodiert von", "Coded by")]
    sheets = [(de_en("Kategoriensystem", "Category system"), head, system, [9, 7, 28, 50, 50, 24, 60, 40, 9, 12, 20]),
              (de_en("Stellen", "Passages"), head2, rows, [9, 28, 14, 24, 80, 20])]
    cases = _case_rows(project, survey, mains, number)
    if cases:  # the thematic case summaries of this analysis (app/transcript_coding_cases.py)
        sheets.append((de_en("Fallzusammenfassungen", "Case summaries"),
                       [de_en("Interview", "Interview"), de_en("Nummer", "Number"), de_en("Hauptkategorie", "Main category"),
                        de_en("Zusammenfassung", "Summary"), de_en("von", "by"), de_en("am", "on")], cases, [14, 9, 28, 80, 20, 17]))
    memos = _memos(project)
    if memos:  # memos at passages, categories and interviews (app/transcript_coding_memos.py)
        from app import transcript_coding_memos
        from app.lang import english
        from app.timeutil import fmt

        about = transcript_coding_memos.targets(project, memos)
        head3 = [de_en("Art", "Kind"), de_en("Nummer", "Number"), de_en("Kategorie", "Category"), de_en("Interview", "Interview"),
                 de_en("Fundstelle", "Location"), de_en("Memo", "Memo"), de_en("von", "by"), de_en("am", "on")]
        sheets.append((de_en("Memos", "Memos"), head3,
                       [[transcript_coding_memos.kind_name(m.kind), about[m.id]["number"], about[m.id]["category"],
                         about[m.id]["interview"], about[m.id]["where"], m.text, m.author_name, fmt(m.updated_at, lang="en" if english() else "de")]
                        for m in memos], [11, 9, 28, 14, 24, 80, 20, 17]))
    from app import transcript_coding_overlap

    sheets.append(transcript_coding_overlap.sheet(project, pairs))  # overlaps of categories, every interview; last
    return xlsx.workbook(sheets)


def _case_rows(project, survey, mains, number):
    """[[interview, number, main category, text, by, when]] of the case summaries of this analysis."""
    from app.interview_summaries import _when
    from app.transcript_coding_cases import meta_of

    out = []
    for r in _rows(survey):
        texts = (r.interview or {}).get("summaries") or {}
        for main, _subs in mains:
            key = f"c{project.id}:{main.id}"
            if texts.get(key):
                meta = meta_of(r, key) or {}
                out.append([(r.interview or {}).get("label", str(r.id)), number.get(main.id, ""), main.name, texts[key],
                            meta.get("by_name") or de_en("Forschende", "researchers"), _when(meta.get("at"))])
    return out


@bp.route("/<code>/interviews/coding/<int:pid>/categories.xlsx")
@signed_in_or_coder
def categories_xlsx(code, pid):
    survey, project, who = _in(code, pid, team_only=True)
    return send_file(io.BytesIO(build_xlsx(project, survey)), as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     download_name=f"kategoriensystem-{survey.code}-{project.id}.xlsx")


@bp.route("/<code>/interviews/coding/<int:pid>/analysis.qdpx")
@signed_in_or_coder
def project_file(code, pid):
    survey, project, who = _in(code, pid, team_only=True)
    return send_file(io.BytesIO(build_qdpx(project, survey)), as_attachment=True, mimetype="application/zip",
                     download_name=f"kodieren-{survey.code}-{project.id}.qdpx")


@bp.route("/<code>/interviews/coding/<int:pid>/categories.qdc")
@signed_in_or_coder
def codebook_file(code, pid):
    survey, project, who = _in(code, pid, team_only=True)
    return send_file(io.BytesIO(build_qdc(project)), as_attachment=True, mimetype="application/xml",
                     download_name=f"kategoriensystem-{survey.code}-{project.id}.qdc")

