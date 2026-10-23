"""The category system of an interview guide as a REFI-QDA codebook (.qdc), for MAXQDA, ATLAS.ti, NVivo and QDA Miner
(concept interviewleitfaden-konzept.md 6.1, decision I9).

Themenblöcke become main categories, their Leitfragen categories below them, the content aspects of a Leitfrage
subcategories, and a task of the guide a code "Aufgabe: …" under its Leitfrage, to mark the talk about it (app/
interview_tasks.py, E6): the deductive start of a content-structuring qualitative content analysis (Kuckartz & Rädiker, 2022).
The description of a Leitfrage holds its narrative prompt and, where there is one, the definition and the
delimitation proposed by the AI assistant and kept by the researchers (item.config["category"]).

GUIDs are derived from the survey's uid and the ids of blocks, elements and aspects, so the same guide gives the
same GUIDs every time: a category system read in again matches the one coded with before.

Format: REFI-QDA Codebook Exchange, namespace urn:QDA-XML:codebook:1.0 (https://www.qdasoftware.org)."""
import uuid
from xml.etree import ElementTree as ET

from app.items.base import plain

NS = "urn:QDA-XML:codebook:1.0"
KUCKARTZ = ("Kuckartz, U., & Rädiker, S. (2022). Qualitative Inhaltsanalyse: Methoden, Praxis, Computerunterstützung "
            "(5. Aufl.). Beltz Juventa.")
REFI = "REFI-QDA (2018). REFI-QDA Codebook Exchange Standard (Version 1.0). Rotterdam Exchange Format Initiative. https://www.qdasoftware.org"
COLOURS = ("#8A4B14", "#2957A4", "#1F6B2A", "#7A3FD1", "#B3261E", "#0B6E86", "#7A5B00")
NAME_MAX = 120


def _guid(survey, *parts):
    # "queryous" stays after the rename to Askurious: a new export keeps the ids of an earlier one
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "queryous:" + survey.uid + ":" + ":".join(str(p) for p in parts))).upper()


def _name(text, fallback):
    text = " ".join(plain(text or "").split())
    if len(text) > NAME_MAX:
        text = text[:NAME_MAX - 1].rstrip() + "…"
    return text or fallback


def _code(parent, survey, key, name, colour, description="", ns=NS):
    code = ET.SubElement(parent, f"{{{ns}}}Code", {"guid": _guid(survey, *key), "name": name, "isCodable": "true", "color": colour})
    if description:
        ET.SubElement(code, f"{{{ns}}}Description").text = description
    return code


def leitfrage_description(item, lang):
    lines = [plain(item.text("question", lang))]
    when = plain(item.text("when", lang)) if (item.config or {}).get("sub") else ""
    if when:
        lines[0] = f"({when}) {lines[0]}"
    category = (item.config or {}).get("category") or {}
    if category.get("definition"):
        lines.append("Definition: " + category["definition"])
    if category.get("delimit"):
        lines.append("Abgrenzung: " + category["delimit"])
    return "\n\n".join(line for line in lines if line)


def codes(survey, codebook, ns=NS):
    """The tree of categories under the element `codebook` (CodeBook of a codebook file or of a project file)."""
    from app import interview

    lang = survey.default_language
    codes = ET.SubElement(codebook, f"{{{ns}}}Codes")
    for n, block in enumerate(interview.guide_blocks(survey)):
        colour = COLOURS[n % len(COLOURS)]
        main = _code(codes, survey, ("block", block.id), _name(block.title(lang), f"Themenblock {n + 1}"), colour, ns=ns)
        made = {}  # Leitfrage id: its code element; an Unterfrage is a code under it (concept unterfragen, E5)
        owned = interview.task_owner(block)
        for item, leitfrage in interview.outline(block):
            if item.type in interview.TASK_ITEM_TYPES:
                from app import interview_tasks

                _code(made.get(owned[item.id].id, main) if item.id in owned else main, survey, ("task", item.id),
                      _name("Aufgabe: " + interview_tasks.name(item, lang), item.code), colour,
                      interview_tasks.kind_name(item, "de") + ": " + plain(item.text("question", lang)), ns=ns)
                continue
            if item.type != "guide_question":
                continue
            parent = made.get(leitfrage.id, main) if leitfrage is not None else main
            sub = made[item.id] = _code(parent, survey, ("item", item.id), _name(item.text("question", lang), item.code),
                                        colour, leitfrage_description(item, lang), ns=ns)
            definitions = ((item.config or {}).get("category") or {}).get("aspects") or {}
            for rid, text in item.kind.rows(item, "aspects", lang):
                _code(sub, survey, ("aspect", item.id, rid), _name(text, rid), colour, definitions.get(rid, ""), ns=ns)
    return codes


def build(survey):
    """The codebook as UTF-8 bytes."""
    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS}}}CodeBook", {"origin": "Askurious"})
    codes(survey, root)
    return ('<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")).encode("utf-8")
