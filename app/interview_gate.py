"""Filters of an interview guide: "Nur stellen, wenn …" on a Leitfrage or a whole Themenblock (concept
konzept-filter-leitfragen.md, E1-E7 as recommended, Ralf 2026-11-03).

A filter names what decides whether the interviewer asks a Leitfrage (its page in the interview view) or the pages of
a Themenblock (its Leitfragen, hints, stimuli and tasks):
  {"of": Leitfrage id, "probe": row id, "fits": bool}  "trifft zu" of a concrete question of an EARLIER Leitfrage (E4)
  {"item": id, "code": answer code}                    an answer of the Kurzfragebogen, only while it comes before
                                                        the conversation (state "short" = "before")
stored as Item.config["gate"] of a Leitfrage (not of an Unterfrage, which has its "case") and as
Block.settings["gate"] of a Themenblock; when both are set, both must hold (E2).

The interview view (interview.js, the rules in interview-gate.js) reads them. In the order "Der Reihe nach" a page whose filter does not hold is left
out by "Weiter" and "Zurück" (E1), its Leitfragen and Unterfragen are stored as "nicht gestellt" with "by": "filter"
(E5), and "Weiter" waits while a switch on the page is open that a later filter names (E3). A filter on a Leitfrage
that is itself filtered out does not hold (E7). In the order "Frei springen" nothing is left out; a filtered page is
only paled with "entfällt (Filter)". Leitfragen are named by the start of their prompt and the K number of the
concrete question, never by a new number (E6).
"""
from app.items.base import Unfit, plain, r_id
from app.lang import de_en

MAX_CODE = 40
SHORT_Q = 40  # how much of a prompt names its Leitfrage (E6)


def clean(value):
    """A filter as the editor stores it (also the import rule of a survey file), else Unfit."""
    if not isinstance(value, dict):
        raise Unfit
    if set(value) == {"of", "probe", "fits"}:
        if isinstance(value["of"], bool) or not isinstance(value["of"], int) or not isinstance(value["fits"], bool):
            raise Unfit
        return {"of": value["of"], "probe": r_id(value["probe"]), "fits": value["fits"]}
    if set(value) == {"item", "code"}:
        if isinstance(value["item"], bool) or not isinstance(value["item"], int) or not isinstance(value["code"], str) \
                or not 0 < len(value["code"]) <= MAX_CODE:
            raise Unfit
        return {"item": value["item"], "code": value["code"]}
    raise Unfit


def valid(value):
    """The filter if it is one, else None (a stored value of an older or broken file)."""
    try:
        return clean(value)
    except Unfit:
        return None


def remap(gate, twin):
    """A filter for a copy: the Leitfrage or the question of the Kurzfragebogen it names becomes its copy; None when
    that did not come along (the filter falls away)."""
    gate = valid(gate)
    if gate is None:
        return None
    key = "of" if "of" in gate else "item"
    new = twin(gate[key])
    if new is None:
        return None
    return {**gate, key: new}


def remap_block(settings, twin):
    """Block.settings with the filter of a Themenblock remapped (dropped when its question did not come along)."""
    settings = dict(settings or {})
    if "gate" in settings:
        gate = remap(settings["gate"], twin)
        if gate is None:
            settings.pop("gate")
        else:
            settings["gate"] = gate
    return settings


def of_item(item):
    """The filter of a Leitfrage (None for an Unterfrage or without one)."""
    if item.type != "guide_question" or (item.config or {}).get("sub"):
        return None
    return valid((item.config or {}).get("gate"))


def of_block(block):
    from app import interview

    if interview.part(block) != "guide":
        return None
    return valid((block.settings or {}).get("gate"))


# --- what a filter can name ----------------------------------------------------------------------------------


def leitfragen(survey):
    """The Leitfragen (not Unterfragen) of the guide in the order of an interview: [(block, item)]."""
    from app import interview

    return [(block, item) for which, block in interview.ordered_blocks(survey) if which == "guide"
            for item, main in interview.outline(block) if item.type == "guide_question" and main is None]


def lead_name(item, lang=None):
    """A Leitfrage as a filter names it: the start of its prompt (E6)."""
    text = plain(item.text("question", lang) or "") or item.code
    return text if len(text) <= SHORT_Q else text[:SHORT_Q - 1].rstrip() + "…"


def earlier(survey, target):
    """The Leitfragen a filter on `target` (a Leitfrage or a Themenblock) may name: those before it in the order of an
    interview; for a Themenblock those of the blocks before it."""
    from app.models import Block

    out = []
    for block, item in leitfragen(survey):
        if isinstance(target, Block):
            if block.id == target.id:
                break
        elif item.id == target.id:
            break
        out.append(item)
    if isinstance(target, Block):  # a block not shown yet (or a Kurzfragebogen): only blocks before it count
        ids = [b.id for b, _ in leitfragen(survey)]
        if target.id not in ids:
            from app import interview

            order = [b.id for which, b in interview.ordered_blocks(survey) if which == "guide"]
            if target.id in order:
                before = set(order[:order.index(target.id)])
                out = [item for block, item in leitfragen(survey) if block.id in before]
    return out


def short_offered(survey):
    """The Kurzfragebogen's questions a filter may name: only while it comes before the conversation (E4)."""
    from app import interview
    from app.items.guide_question import short_choices

    if interview.state(survey)["short"] != "before":
        return []
    return short_choices(survey)


def choices(survey, target, lang=None):
    """What the choice "Nur stellen, wenn …" offers: [(group, [(value, label)])]. Values "g:<Leitfrage>:<row>:yes|no"
    and "k:<question>:<code>"."""
    from app.items.guide_question import GuideQuestion

    out = []
    for item in earlier(survey, target):
        options = []
        for rid, n, _ in item.kind.numbered(item, lang):
            for fit, on in (("yes", True), ("no", False)):
                options.append((f"g:{item.id}:{rid}:{fit}", label(survey, {"of": item.id, "probe": rid, "fits": on}, lang)))
        if options:
            out.append((de_en("Leitfrage „{q}“", "Guide question “{q}”", q=lead_name(item, lang)), options))
    shorts = short_offered(survey)
    if shorts:
        out.append((de_en("Kurzfragebogen (vor dem Gespräch)", "Short questionnaire (before the conversation)"),
                    [(f"k:{short.id}:{code}", f"{GuideQuestion.short_label(short, lang)}: {text}")
                     for short, codes in shorts for code, text in codes]))
    return out


def values(survey, target, lang=None):
    return [value for _, options in choices(survey, target, lang) for value, _ in options]


def value_of(gate):
    """A stored filter as the value of the editor's choice; "" for none."""
    gate = valid(gate)
    if gate is None:
        return ""
    if "of" in gate:
        return f"g:{gate['of']}:{gate['probe']}:{'yes' if gate['fits'] else 'no'}"
    return f"k:{gate['item']}:{gate['code']}"


def read(survey, target, value):
    """The filter of the editor's choice: (gate or None, refused). A value that names no earlier Leitfrage (a later
    one, one of another guide) or no offered answer is refused, never stored."""
    value = (value or "").strip()
    if not value:
        return None, False
    if value not in values(survey, target):
        return None, True
    kind, _, rest = value.partition(":")
    if kind == "g":
        of, rid, fit = rest.split(":")
        return {"of": int(of), "probe": rid, "fits": fit == "yes"}, False
    iid, _, code = rest.partition(":")
    return {"item": int(iid), "code": code}, False


def refused_text():
    return de_en("Der Filter kann nur auf eine konkrete Frage einer früheren Leitfrage verweisen (oder auf den Kurzfragebogen, wenn er vor dem Gespräch steht).",
                 "The filter can only name a specific question of an earlier guide question (or the short questionnaire when it comes before the conversation).")


# --- a filter in words -------------------------------------------------------------------------------------------


def _items(survey):
    return {i.id: i for b in survey.blocks for i in b.items}


def _say(ui, de, en, **values):
    if ui:
        return (en if ui == "en" else de).format(**values)
    return de_en(de, en, **values)


def _probe(survey, gate, lang=None):
    """(Leitfrage, K number) a filter on a concrete question names, or (None, None)."""
    lead = _items(survey).get(gate.get("of"))
    if lead is None or lead.type != "guide_question":
        return None, None
    n = next((n for rid, n, _ in lead.kind.numbered(lead, lang) if rid == gate.get("probe")), None)
    return (lead, n) if n else (None, None)


def label(survey, gate, lang=None, ui=None):
    """"K1 bei „Wie sind Sie …“ trifft zu", "„ja“ bei „Erwerbstätig?“"; "" when it names nothing any more."""
    gate = valid(gate)
    if gate is None:
        return ""
    if "of" in gate:
        lead, n = _probe(survey, gate, lang)
        if lead is None:
            return ""
        if gate["fits"]:
            return _say(ui, "K{n} bei „{q}“ trifft zu", "Q{n} of “{q}” applies", n=n, q=lead_name(lead, lang))
        return _say(ui, "K{n} bei „{q}“ trifft nicht zu", "Q{n} of “{q}” does not apply", n=n, q=lead_name(lead, lang))
    from app.items.guide_question import GuideQuestion, short_choices

    for short, codes in short_choices(survey, lang):
        if short.id == gate["item"]:
            text = dict(codes).get(gate["code"])
            if text:
                return _say(ui, "„{a}“ bei „{q}“", "“{a}” for “{q}”", a=text, q=GuideQuestion.short_label(short, lang))
    return ""


def why(survey, gate, lang=None):
    """Why the pages fall away when the filter does not hold: "weil K1 bei „…“ nicht zutrifft"."""
    gate = valid(gate)
    if gate is None:
        return ""
    if "of" in gate:
        lead, n = _probe(survey, gate, lang)
        if lead is None:
            return ""
        if gate["fits"]:
            return de_en("weil K{n} bei „{q}“ nicht zutrifft", "because Q{n} of “{q}” does not apply", n=n, q=lead_name(lead, lang))
        return de_en("weil K{n} bei „{q}“ zutrifft", "because Q{n} of “{q}” applies", n=n, q=lead_name(lead, lang))
    text = label(survey, gate, lang)
    return de_en("weil nicht {c} angegeben ist", "because {c} is not given", c=text) if text else ""


def attr(survey, gate, lang=None):
    """A filter for the interview view (data-iv-gates, interview-gate.js), None for none or one that names nothing."""
    gate = valid(gate)
    if gate is None or not label(survey, gate, lang):
        return None
    out = {**gate, "label": label(survey, gate, lang), "why": why(survey, gate, lang)}
    if "of" in gate:
        lead, n = _probe(survey, gate, lang)
        out["n"] = n
        out["gone"] = de_en("weil „{q}“ selbst entfällt", "because “{q}” itself falls away", q=lead_name(lead, lang))
        out["wait"] = de_en("Bitte zuerst angeben, ob K{n} zutrifft. Davon hängt ab, welche Fragen folgen.",
                            "Please first say whether Q{n} applies. The questions that follow depend on it.", n=n)
    return out


def named(survey):
    """{Leitfrage id: {row ids}} of the concrete questions any filter of the guide names: they show the switch
    "trifft zu" in the interview view (GuideQuestion.switched)."""
    out = {}
    gates = [of_item(i) for b in survey.blocks for i in b.items] + [of_block(b) for b in survey.blocks]
    for gate in gates:
        if gate and "of" in gate:
            out.setdefault(gate["of"], set()).add(gate["probe"])
    return out


def otherwise(survey, target, lang=None, ui=None):
    """Where the conversation goes on when the filter does not hold: the next Leitfrage (or part) after the Leitfrage
    or the Themenblock `target`, for the line "Nur wenn …, sonst weiter mit „…“" of the documents; "" at the end."""
    from app import interview
    from app.models import Block

    seen, after = False, None
    for which, block in interview.ordered_blocks(survey):
        if which != "guide":
            if seen:
                after = _say(ui, "dem {p}", "the {p}", p=(interview.PARTS_EN if ui == "en" else interview.PARTS)[which]
                             if ui else interview.parts()[which])
                break
            continue
        if isinstance(target, Block) and block.id == target.id:
            seen = True
            continue
        for item, main in interview.outline(block):
            if item.type != "guide_question" or main is not None:
                continue
            if seen:
                return _say(ui, "„{q}“", "“{q}”", q=lead_name(item, lang))
            if not isinstance(target, Block) and item.id == target.id:
                seen = True
    return after or ""


def line(survey, target, lang=None, ui=None):
    """The filter of a Leitfrage or a Themenblock as one line of a document: "Nur wenn K1 bei „…“ trifft zu, sonst
    weiter mit „…“"; "" without a filter."""
    from app.models import Block

    gate = of_block(target) if isinstance(target, Block) else of_item(target)
    text = label(survey, gate, lang, ui) if gate else ""
    if not text:
        return ""
    then = otherwise(survey, target, lang, ui)
    if then:
        return _say(ui, "Nur wenn {c}, sonst weiter mit {t}", "Only if {c}, otherwise go on with {t}", c=text, t=then)
    return _say(ui, "Nur wenn {c}", "Only if {c}", c=text)


def warnings(survey, target):
    """A filter that names nothing any more or no longer an earlier Leitfrage (a warning, like the conditions of the
    concrete questions)."""
    from app.models import Block

    gate = of_block(target) if isinstance(target, Block) else of_item(target)
    if gate is None:
        return []
    if not label(survey, gate):
        return [de_en("Filter „Nur stellen, wenn …“: Die Frage des Filters gibt es nicht mehr",
                      "Filter “Ask only if …”: the question of the filter no longer exists")]
    if "of" in gate and gate["of"] not in [i.id for i in earlier(survey, target)]:
        return [de_en("Filter „Nur stellen, wenn …“: Die Leitfrage des Filters steht nicht mehr davor",
                      "Filter “Ask only if …”: the guide question of the filter is no longer before it")]
    if "item" in gate and not short_offered(survey):
        return [de_en("Filter „Nur stellen, wenn …“: Der Kurzfragebogen steht nicht mehr vor dem Gespräch",
                      "Filter “Ask only if …”: the short questionnaire no longer comes before the conversation")]
    return []


def pointing_at(survey, item, rid):
    """The Leitfragen and Themenblöcke whose filter names the concrete question `rid` of `item` (to warn before it is
    deleted): [name]."""
    out = []
    for b in survey.blocks:
        gate = of_block(b)
        if gate and gate.get("of") == item.id and gate.get("probe") == rid:
            out.append(b.title())
        for i in b.items:
            gate = of_item(i)
            if gate and gate.get("of") == item.id and gate.get("probe") == rid:
                out.append(lead_name(i))
    return out
