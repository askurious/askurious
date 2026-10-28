"""Interviews kodieren, stage 4: an analysis as REFI-QDA files (app/transcript_coding.py; concept "Interviews
kodieren", all as recommended, Ralf 2026-10-27), for MAXQDA, ATLAS.ti, NVivo and QDA Miner.

- The project (.qdpx): the analysis's category system (main categories with their subcategories nested, candidates on
  top level; definition, delimitation and anchor example as the description), each interview with a transcript as a
  text document with its coded passages (PlainTextSelection with a Coding of its category, positions in characters of
  the transcript as stored), the interviews as cases with the case variables of the guide's own export
  (app/interview_qdpx.py), and each coder as a user, so that a QDA program shows who coded what. Passages that can no
  longer be placed (app/transcript_coding_view.py) are left out; merged categories count for the one they went into.
- The category system (.qdc): the same tree, nothing about interviews or people.

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
from app.lang import de_en
from app.transcript_coding import _in, bp, signed_in_or_coder, tree
from app.transcript_coding_view import has_transcript, placed, transcript

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


def description(cat):
    lines = []
    if cat.definition:
        lines.append(cat.definition)
    if cat.delimitation:
        lines.append(de_en("Abgrenzung: ", "Delimitation: ") + cat.delimitation)
    if (cat.anchor or {}).get("quote"):
        lines.append(de_en("Ankerbeispiel: ", "Anchor example: ") + f"„{cat.anchor['quote']}“ ({cat.anchor.get('label', '')})")
    if cat.former_names:
        lines.append(de_en("Früher auch: ", "Formerly also: ") + ", ".join(cat.former_names))
    return "\n\n".join(lines)


def codes(project, parent, ns):
    """The tree of categories under `parent` (a CodeBook): {category id: guid}."""
    holder = _el(parent, ns, "Codes")
    mains, candidates = tree(project)
    guids = {}

    def code(where, cat, colour):
        attrs = {"guid": cat.guid.upper(), "name": cat.name, "isCodable": "true", "color": cat.colour or colour}
        e = _el(where, ns, "Code", attrs)
        text = description(cat)
        if text:
            _el(e, ns, "Description", text=text)
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


def build_qdpx(project, survey):
    from app import interview_data, interview_qdpx
    from app.models import utcnow

    ET.register_namespace("", PROJECT_NS)
    ns = PROJECT_NS
    now = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [r for r in interview_data.interviews(survey, source="real") if has_transcript(r)]
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
    user_of = {}
    for key, name in sorted(people.items(), key=lambda kv: kv[1].casefold()):
        user_of[key] = _guid(survey, project, "user", key or "none")
        _el(users, ns, "User", {"guid": user_of[key], "name": name[:200]})
    codebook = _el(root, ns, "CodeBook")
    guids = codes(project, codebook, ns)
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
                                                          "name": " ".join(c.get("quote", "").split())[:60],
                                                          "startPosition": str(c["start"]), "endPosition": str(c["end"]),
                                                          "creatingUser": user, "creationDateTime": at})
                if c.get("origin") == "ai":  # an accepted AI suggestion stays recognisable
                    _el(sel, ns, "Description", text=de_en("KI-Vorschlag, übernommen von {who}", "AI suggestion, accepted by {who}",
                                                           who=c.get("accepted_by") or c.get("by_name") or ""))
                coding = _el(sel, ns, "Coding", {"guid": _guid(survey, project, "coding", c["id"]), "creatingUser": user,
                                                 "creationDateTime": at})
                _el(coding, ns, "CodeRef", {"targetGUID": guids[c["cat"]]})
            files[f"Sources/{guid}.txt"] = text.encode("utf-8")
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

