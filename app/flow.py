"""Survey flow: a tree over the blocks with randomizers, branches, and end nodes.

Stored as JSON on Survey.flow:
  {"t": "root", "children": [node, ...]}
  block:      {"t": "block", "block": <block id>}
  randomizer: {"t": "rand", "id": "r..", "code": "cond1", "name": "...", "k": 1, "balanced": true,
               "shuffle": false, "children": [...]}
  branch:     {"t": "branch", "id": "b..", "cond": {"item": <id>, "op": "eq", "value": "1"}, "children": [...]}
  else:       {"t": "else", "id": "x..", "of": <branch id>, "children": [...]}
              (shown when the branch's condition does not hold; always directly after its branch)
  end:        {"t": "end", "id": "e..", "text": {lang: "..."}, "kind": "screenout" | "quotafull" | "excluded",
               "link": "https://…", "link_mode": "now" | "button" | "auto"}
              (kind decides the status of the participation and the standard text and link; a link of its
              own goes before the link of the outcome, see panel.py)
              kind "quotafull" also has "quotas": [quota ids] and "rule": "any" | "all": the end applies only
              to people for whom one (or each) of these quotas is full; everybody else passes it (quotas.py).
              "links": {quota id (str): {"link": "https://…", "mode": "" | link mode, "cells": {cell code:
              "https://…"}}} gives a quota, and single cells of it, a completion link of their own: who ends
              here is sent to the link of the full cell, else of the full quota, else of the end (end_link)
              An end saved with several triggers has "cases" instead of all of the above (see `cases`):
              [{"id": "c..", "when": END_TRIGGERS key, "text": {lang: "..."}, "link", "link_mode",
                "quotas": [quota ids], "rule": "any" | "all"      (when "quotafull"; "quotas" also for "nocell")
                "quota": quota id, "cells": [cell codes]}]        (when "cellfull")
              The first trigger that holds for a person ends the participation, with its status, text and
              link; nobody for whom none holds is ended. Both forms are read through `cases`.
  quota:      {"t": "quota", "id": "q..", "quotas": [quota ids]}
              (a quota calculation, see quotas.py: the person gets a place in the cell of each quota, if there
              is room. It ends nobody; in surveys started before it was a "Quotenprüfung" with "full", "text",
              "link", "link_mode", which ended a full quota itself)
  loop:       {"t": "loop", "id": "l..", "name": "...", "code": "loop1", "columns": [...], "rows": [...],
               "mode": "all", "k": 1, "order": "random", "balanced": true, "children": [blocks]}
              (a loop over a table, see loops.py: the blocks inside run once per selected table row)
  ws:         {"t": "ws", "id": "w..", "code": "ws1", ...}   a web service called by the server (app/webservice.py)
  ext:        {"t": "ext", "id": "o..", "code": "ext1", "name": "...", "url": "https://…?pid={{pid}}&return={{return}}",
               "mode": "button" | "auto", "text": {lang: "..."}, "values": [{"name": "score", "kind": "number"}]}
              (an external section, see external.py: the person leaves for a web app and comes back through a
              signed return link, with the declared values)

Every block appears exactly once. Block.position always follows the depth-first order of the
tree, so the editor shows blocks in flow order. Randomizer choices are made once per response
(stored in its layout); branch conditions are evaluated while the participant answers.
"""
import re
from math import comb, factorial

from app.items.base import new_id as _nid
from app.lang import de_en

GROUPS = ("rand", "branch", "loop", "else")
MAX_CONDS = 8  # conditions of one element "Bedingung" (parts)
OPS = {
    "eq": "=",
    "ne": "≠",
    "lt": "<",
    "le": "≤",
    "gt": ">",
    "ge": "≥",
    "between": "von … bis",
    "in": "einer von",
    "nin": "keiner von",
    "contains": "enthält",
    "answered": "beantwortet",
    "empty": "nicht beantwortet",
}
_BOUNDS = re.compile(r"^\s*(.+?)\s*(?:\.\.+|;|\s[-–]\s|\sbis\s|\sto\s)\s*(.+?)\s*$")


def bounds(value):
    """(from, to) of the value of a "von … bis" condition, written "18..29" (also "18;29", "18 bis 29",
    "18 - 29"), or (None, None)."""
    m = _BOUNDS.match(str(value or ""))
    return (m.group(1), m.group(2)) if m else (None, None)


def code_list(value):
    """The codes of an "einer von" condition, written "1, 2, 3"."""
    return [v.strip() for v in re.split(r"[,;]", str(value or "")) if v.strip()]
CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}\Z")
# kinds of ends: the status a participation gets there (models.Response) and what the end is called
END_KINDS = {"screenout": "Screen-out", "quotafull": "Quote voll", "excluded": "Ausschluss"}


def end_kind(node):
    kind = (node or {}).get("kind")
    return kind if kind in END_KINDS else "screenout"


# what can trigger an end: its name on the page and the status the participation gets
END_TRIGGERS = {"quotafull": ("Quote voll", "quotafull"), "cellfull": ("Teilquote voll", "quotafull"),
                "nocell": ("Keine Teilquote passt", "screenout"), "screenout": ("Screen-out", "screenout"),
                "excluded": ("Ausschluss", "excluded")}
# the same names in English, for the researchers' pages (trigger_name); END_TRIGGERS keeps the German ones, which
# also stand in exports (child_label)
END_TRIGGERS_EN = {"quotafull": "Quota full", "cellfull": "Sub-quota full", "nocell": "No sub-quota fits",
                   "screenout": "Screen-out", "excluded": "Exclusion"}


def trigger_name(when):
    """The name of a trigger in the language of the page."""
    when = when if when in END_TRIGGERS else "screenout"
    return de_en(END_TRIGGERS[when][0], END_TRIGGERS_EN[when])
MAX_CASES = 500  # the quota assistant makes one per cell


def cases(node, expand=False):
    """The triggers of an end, in their order: what an end with "cases" holds, or the one trigger an end of the
    older form stands for (its kind, with its quotas and rule). expand (the page): the links such an end has for
    single quotas and cells (end_link) as triggers of their own, as they are saved from then on."""
    if node.get("cases"):
        return node["cases"]
    kind = end_kind(node)
    base = {"text": node.get("text") or {}, "link": node.get("link") or "", "link_mode": node.get("link_mode") or "now"}
    if kind != "quotafull" or "quotas" not in node:
        return [{"id": "c0", "when": kind, **base}]  # a "Quote voll" that names no quota ends everybody (case_holds)
    out = []
    for qid in node["quotas"] if expand else []:
        own = (node.get("links") or {}).get(str(qid)) or {}
        mode = own.get("mode") or base["link_mode"]
        for code, url in (own.get("cells") or {}).items():
            out.append({"when": "cellfull", "quota": qid, "cells": [code], "text": base["text"], "link": url, "link_mode": mode})
        if own.get("link"):
            out.append({"when": "quotafull", "quotas": [qid], "rule": "any", "text": base["text"], "link": own["link"],
                        "link_mode": mode})
    out.append({"when": "quotafull", "quotas": list(node["quotas"]), "rule": node.get("rule") or "any", **base})
    return [{"id": f"c{n}", **case} for n, case in enumerate(out)]


def case_quotas(case):
    """Ids of the quotas a trigger looks at."""
    if case.get("when") == "cellfull":
        return [case["quota"]] if case.get("quota") else []
    return list(case.get("quotas") or []) if case.get("when") in ("quotafull", "nocell") else []


def case_holds(case, view):
    """The trigger holds for a person. view: quotas.view of the participation ({"q:<id>": "in" | "full" | "none",
    "qc:<id>": code of the cell}); what is missing there has not been calculated."""
    when = case.get("when")
    if when in ("screenout", "excluded"):
        return True
    if when == "quotafull":
        if "quotas" not in case:
            return True  # an end "Quote voll" from before the ends named their quotas: it stands in a condition
        full = [view.get(f"q:{q}") == "full" for q in case["quotas"]]
        return bool(full) and (all(full) if case.get("rule") == "all" else any(full))
    if when == "cellfull":
        return view.get(f"q:{case.get('quota')}") == "full" and view.get(f"qc:{case.get('quota')}") in (case.get("cells") or [])
    if when == "nocell":
        return any(view.get(f"q:{q}") == "none" for q in case.get("quotas") or [])
    return False


def case_for(node, view):
    """The trigger of the end that holds for this person: the first one in their order, or None."""
    return next((case for case in cases(node) if case_holds(case, view or {})), None)


def case_status(case):
    """The status a participation gets that this trigger ends (models.Response)."""
    return END_TRIGGERS.get((case or {}).get("when"), END_TRIGGERS["screenout"])[1]


def node_links(node):
    """Every completion link a node holds: its own and those of its triggers; for an end "Quote voll" of the
    older form, those of its quotas and cells."""
    own = [entry for entry in ((node or {}).get("links") or {}).values() if isinstance(entry, dict)]
    urls = [(node or {}).get("link")] + [e.get("link") for e in own] + [u for e in own for u in (e.get("cells") or {}).values()]
    urls += [case.get("link") for case in (node or {}).get("cases") or []]
    return [u for u in urls if u]


def end_link(node, view):
    """(link, mode) an end sends this participation to, or (None, None) if it has none of its own: the link of
    the trigger that holds for the person (view: quotas.view). An end "Quote voll" of the older form looks at its
    quotas in their order: the first that is full for the person and has a link decides: the link of the person's
    cell, else the link of the quota. Else the link of the end."""
    if (node or {}).get("cases"):
        case = case_for(node, view) or {}
        return (case["link"], case.get("link_mode")) if (case.get("link") or "").strip() else (None, None)
    links = (node or {}).get("links") or {}
    for qid in (node or {}).get("quotas") or []:
        own = links.get(str(qid)) or {}
        if (view or {}).get(f"q:{qid}") != "full":
            continue
        url = (own.get("cells") or {}).get((view or {}).get(f"qc:{qid}") or "") or own.get("link")
        if (url or "").strip():
            return url, own.get("mode") or node.get("link_mode")
    if ((node or {}).get("link") or "").strip():
        return node["link"], node.get("link_mode")
    return None, None


def cases_from_form(survey, form, languages, links=True, stored=()):
    """The triggers as the page sends them: "case" names the cards in their order (their numbers k); per card
    "case<k>_when", "_id", "_remove", "_text_<lang>", "_link", "_link_mode", and by trigger "_quotas" and "_rule",
    "_quota" and "_cells", or "_nquotas". Returns (cases, error). links=False (see update): a trigger keeps the
    link it has among `stored` (the triggers so far, by their ids), a new one has none."""
    from app import panel

    names = panel.link_names(survey)
    kept = {case["id"]: case for case in stored}
    if not links and [k for k in form if re.fullmatch(r"case[0-9A-Za-z_]{1,8}_link(_mode)?", k)]:
        return None, de_en("Abschlusslinks legt fest, wer in dieser Befragung die Rolle „Admin“ hat. Es wurde nichts gespeichert.",
                           "Completion links are set by whoever has the role “Admin” in this survey. Nothing was saved.")
    quotas = {q.id: q for q in survey.quotas}
    out, ids = [], set()
    for k in dict.fromkeys(form.getlist("case")):
        p = f"case{k}"
        when = form.get(f"{p}_when", "")
        if when not in END_TRIGGERS or form.get(f"{p}_remove"):
            continue
        cid = form.get(f"{p}_id", "")
        before = kept.get(cid) or {}
        if not (re.fullmatch(r"c[0-9a-f]{1,12}", cid) and cid not in ids):
            cid = _nid("c")
        ids.add(cid)
        link = form.get(f"{p}_link", "").strip()[:1000] if links else before.get("link") or ""
        mode = form.get(f"{p}_link_mode") if links else before.get("link_mode")
        error = panel.link_error(link, names) if links else None
        if error:
            return None, de_en("Auslöser {n} ({name}), Abschlusslink: {error}", "Trigger {n} ({name}), completion link: {error}",
                               n=len(out) + 1, name=trigger_name(when), error=error)
        case = {"id": cid, "when": when, "text": {l: form.get(f"{p}_text_{l}", "").strip()[:3000] for l in languages},
                "link": link, "link_mode": mode if mode in panel.LINK_MODES else "now"}
        if when in ("quotafull", "nocell"):
            ticked = set(form.getlist(f"{p}_quotas" if when == "quotafull" else f"{p}_nquotas"))
            case["quotas"] = [q.id for q in survey.quotas if str(q.id) in ticked]
            if when == "quotafull":
                case["rule"] = "all" if form.get(f"{p}_rule") == "all" else "any"
        elif when == "cellfull":
            raw = form.get(f"{p}_quota", "")
            quota = quotas.get(int(raw)) if raw.isascii() and raw.isdigit() and len(raw) < 12 else None
            ticked = set(form.getlist(f"{p}_cells"))
            case["quota"] = quota.id if quota else None
            case["cells"] = [c.code for c in quota.cells if c.code in ticked] if quota else []
        out.append(case)
        if len(out) >= MAX_CASES:
            break
    if not out:
        return None, de_en("Ein Ende braucht mindestens einen Auslöser.", "An end needs at least one trigger.")
    return out, None


# --- tree basics ------------------------------------------------------------------


def tree(survey):
    return survey.flow or {"t": "root", "children": []}


def node_id(node):
    return f"k{node['block']}" if node["t"] == "block" else node["id"]


def walk(node, parent=None, depth=0):
    """Yield (node, parent, depth) depth-first, root excluded."""
    for child in node.get("children", []):
        yield child, node, depth
        if child["t"] in GROUPS:
            yield from walk(child, child, depth + 1)


def walk_nodes(survey, kind):
    """The nodes of one kind in the survey's flow, in flow order."""
    return [n for n, _, _ in walk(tree(survey)) if n["t"] == kind]


def find(root, nid):
    for node, parent, _ in walk(root):
        if node_id(node) == nid:
            return node, parent
    return None, None


def ancestors(root, nid):
    """Group nodes containing the node, outermost first."""
    path = []

    def rec(node, trail):
        for child in node.get("children", []):
            if node_id(child) == nid:
                path.extend(trail)
                return True
            if child["t"] in GROUPS and rec(child, trail + [child]):
                return True
        return False

    rec(root, [])
    return path


def block_order(root):
    return [n["block"] for n, _, _ in walk(root) if n["t"] == "block"]


def loop_blocks(survey):
    """The ids of the blocks that run inside a loop. Their answers are stored once per pass ("item#row"), so a
    condition or a Teilquote cannot be put on their questions: it would never hold."""
    return {c["block"] for n, _, _ in walk(tree(survey)) if n["t"] == "loop" for c in n.get("children", []) if c["t"] == "block"}


def normalize(survey):
    """Make the tree consistent with the blocks and sync Block.position to flow order.
    Returns the (possibly new) tree; call after any change to blocks or flow."""
    import copy

    from app import quotas

    quotas.upgrade(survey)  # a draft from before the quota calculation became a trigger (quotas.py)
    root = copy.deepcopy(tree(survey))
    ids = [b.id for b in survey.blocks]
    seen = set()

    def clean(node):
        kept = []
        for child in node.get("children", []):
            if child["t"] == "block":
                if child["block"] in ids and child["block"] not in seen:
                    seen.add(child["block"])
                    kept.append(child)
            else:
                if child["t"] in GROUPS:
                    clean(child)
                kept.append(child)
        node["children"] = kept

    clean(root)
    _place_elses(root)
    _one_split(root)
    # blocks not yet in the tree (e.g. just added) go to the end of the regular survey, i.e. before
    # the separated section, in their current order
    split_at = next((n for n, c in enumerate(root["children"]) if c["t"] == "split"), len(root["children"]))
    new = [{"t": "block", "block": b.id} for b in survey.blocks if b.id not in seen]
    root["children"][split_at:split_at] = new
    order = {bid: n for n, bid in enumerate(block_order(root))}
    for b in survey.blocks:
        b.position = order.get(b.id, len(order))
    survey.flow = root
    survey.blocks.sort(key=lambda b: b.position)
    return root


def _place_elses(root):
    """Every "else" sits directly after its branch; without its branch it dissolves (its children
    take its place), so moving or deleting a branch never loses blocks."""
    elses = []

    def extract(node):
        kept = []
        for child in node.get("children", []):
            if child["t"] == "else":
                elses.append((child, node, len(kept)))
            else:
                kept.append(child)
            if child["t"] in GROUPS:
                extract(child)
        node["children"] = kept

    extract(root)
    placed, shift = set(), {}
    for node, parent, index in elses:  # outer groups first, so nested branches are found again
        branch, bparent = find(root, node.get("of", ""))
        if branch is not None and branch["t"] == "branch" and branch["id"] not in placed:
            placed.add(branch["id"])
            sibs = bparent["children"]
            sibs.insert(sibs.index(branch) + 1, node)
            if bparent is parent:
                shift[id(parent)] = shift.get(id(parent), 0) + 1
        else:  # its branch is gone: the children take its place
            at = index + shift.get(id(parent), 0)
            parent["children"][at:at] = node.get("children", [])
            shift[id(parent)] = shift.get(id(parent), 0) + len(node.get("children", []))


def _one_split(root):
    """The separated section always exists (possibly empty): exactly one split, at the top level;
    a missing one goes to the end, extra ones and nested ones dissolve into their place."""
    first = None
    for node, parent, _ in list(walk(root)):
        if node["t"] != "split":
            continue
        if first is None and parent is root:
            first = node
        else:
            parent["children"].remove(node)
    if first is None:
        root["children"].append({"t": "split", "id": _nid("s")})


def has_separated(survey):
    """True if blocks follow the split: only then is anything stored separately."""
    return bool(blocks_after_split(survey))


def add_else(survey, branch_id):
    """Add the "Sonst" group after a branch (once)."""
    root = normalize(survey)
    branch, parent = find(root, branch_id)
    if branch is None or branch["t"] != "branch":
        return False
    if any(n["t"] == "else" and n.get("of") == branch_id for n, _, _ in walk(root)):
        return False
    sibs = parent["children"]
    sibs.insert(sibs.index(branch) + 1, {"t": "else", "id": _nid("x"), "of": branch_id, "children": []})
    survey.flow = root
    normalize(survey)
    return True


def cond_holds(conds, ref, answers, survey=None):
    """A page condition: a branch id, or "!<branch id>" for its "Sonst" group. survey: see holds."""
    if ref.startswith("!"):
        return not holds(conds.get(ref[1:], {}), answers, survey)
    return holds(conds.get(ref, {}), answers, survey)


# --- editing operations ---------------------------------------------------------------


def add_node(survey, kind):
    root = normalize(survey)
    n_rand = sum(1 for n, _, _ in walk(root) if n["t"] == "rand")
    if kind == "rand":
        node = {"t": "rand", "id": _nid("r"), "code": f"cond{n_rand + 1}", "name": de_en("Randomisierung {n}", "Randomisation {n}", n=n_rand + 1),
                "k": 1, "balanced": True, "shuffle": False, "children": []}
    elif kind == "branch":
        node = {"t": "branch", "id": _nid("b"), "cond": {"item": None, "op": "eq", "value": ""}, "children": []}
    elif kind == "end":
        node = {"t": "end", "id": _nid("e"), "text": {}, "kind": "screenout"}
    elif kind == "quota":
        from app import quotas

        # a quota that is not calculated anywhere yet is what this node is for
        free = [q.id for q in survey.quotas if quotas.node_of(survey, q) is None]
        node = {"t": "quota", "id": _nid("q"), "quotas": free}
    elif kind == "loop":
        n_loop = sum(1 for n, _, _ in walk(root) if n["t"] == "loop")
        node = {"t": "loop", "id": _nid("l"), "name": de_en("Schleife {n}", "Loop {n}", n=n_loop + 1), "code": f"loop{n_loop + 1}",
                "columns": [], "rows": [], "mode": "all", "k": 1, "order": "random", "balanced": True, "children": []}
    elif kind == "ext":
        from app import external

        node = external.new(survey, root)
    elif kind == "ws":
        from app import webservice

        node = webservice.new(survey, root)
    elif kind == "split":
        if any(n["t"] == "split" for n, _, _ in walk(root)):
            raise ValueError(de_en("Es gibt bereits einen getrennten Abschnitt.", "There is already a separated section."))
        node = {"t": "split", "id": _nid("s")}
    else:
        raise ValueError(kind)
    # new elements go to the end of the regular survey, before the separated section
    split_at = next((n for n, c in enumerate(root["children"]) if c["t"] == "split"), len(root["children"]))
    root["children"].insert(split_at, node)
    survey.flow = root
    return node


HOLDS_BLOCKS = ("loop", "rand", "branch", "vignettes")  # dropped onto a block, these take it in


def add_at(survey, kind, target_nid, mode):
    """A new element dropped into the flow: before/after `target_nid`, into the group `target_nid`, or onto
    the block `target_nid` (mode "wrap", for HOLDS_BLOCKS): the element takes the block's place and the block
    goes into it (a condition's "dann"). Returns the node; None if that is not possible (nothing is added)."""
    node = add_node(survey, kind)
    nid = node["id"]
    if mode == "wrap":
        target, _ = find(tree(survey), target_nid)
        done = (kind in HOLDS_BLOCKS and target is not None and target["t"] == "block"
                and place(survey, nid, target_nid, "before") and place(survey, target_nid, nid, "into"))
    else:
        done = mode in ("before", "after", "into") and place(survey, nid, target_nid, mode)
    if not done:
        remove(survey, nid)
        return None
    return find(tree(survey), nid)[0]


def _consent_block(survey):
    """The id of the block that holds the survey's consent, or None. Ask before the tree is taken in hand: the
    question reads the blocks, and what is changed in the tree after that would not be stored."""
    consent = survey.consent_item
    return consent.block_id if consent is not None else None


GROUP_NAMES = {"rand": "einer Randomisierung", "branch": "einer Bedingung", "else": "einem „Sonst“", "loop": "einer Schleife"}
GROUP_NAMES_EN = {"rand": "a randomisation", "branch": "a condition", "else": "an “Else”", "loop": "a loop"}


def group_name(kind):
    """A group in words ("in einer Randomisierung"), in the language of the page."""
    return de_en(GROUP_NAMES.get(kind, "einer Gruppe"), GROUP_NAMES_EN.get(kind, "a group"))


def move(survey, nid, direction):
    """up/down among siblings; in = into the previous sibling group; out = after the parent."""
    consent = _consent_block(survey)
    root = normalize(survey)
    node, parent = find(root, nid)
    if node is None or node["t"] == "split":  # the separated section is a fixed divider; blocks move across it
        return False
    sibs = parent["children"]
    i = sibs.index(node)
    if direction in ("up", "down"):
        j = i - 1 if direction == "up" else i + 1
        if not 0 <= j < len(sibs):
            return False
        sibs[i], sibs[j] = sibs[j], sibs[i]
    elif direction == "in":
        if i == 0 or sibs[i - 1]["t"] not in GROUPS or node.get("block", 0) == consent:
            return False  # the consent is for everyone: never into a group
        sibs.pop(i)
        sibs[i - 1]["children"].append(node)
    elif direction == "out":
        if parent is root:
            return False
        _, grand = find(root, node_id(parent))
        sibs.pop(i)
        grand["children"].insert(grand["children"].index(parent) + 1, node)
    else:
        return False
    survey.flow = root
    normalize(survey)
    return True


def place(survey, nid, target_nid, mode):
    """Drag and drop: put node `nid` before/after `target_nid`, or into the group `target_nid`.
    `newelse:<branch id>` is the empty "Sonst" slot of a branch: the else group is created first."""
    if target_nid.startswith("newelse:") and mode == "into":
        bid = target_nid.split(":", 1)[1]
        add_else(survey, bid)
        target_nid = next((n["id"] for n, _, _ in walk(tree(survey)) if n["t"] == "else" and n.get("of") == bid), "")
        if not target_nid:
            return False
    consent = _consent_block(survey)
    root = normalize(survey)
    node, parent = find(root, nid)
    if node is None or nid == target_nid or node["t"] == "split":
        return False
    if node["t"] in GROUPS and target_nid in {node_id(n) for n, _, _ in walk(node)}:
        return False  # a group cannot go into itself
    if node.get("block", 0) == consent:
        # the consent is for everyone: next to a group, never into one (a randomizer or a condition would let
        # people through without it)
        inside = target_nid != "root" if mode == "into" else find(root, target_nid)[1] not in (None, root)
        if inside:
            return False
    into_root = target_nid == "root" and mode == "into"
    target, tparent = (root, None) if into_root else find(root, target_nid)
    # check the target before the node leaves its place: the tree is the stored one (normalize), so a refused
    # drop (target deleted meanwhile by someone else, or not a group) must leave it as it was
    if target is None or (mode == "into" and not into_root and target["t"] not in GROUPS):
        return False
    parent["children"].remove(node)
    if into_root:
        root["children"].append(node)
    else:
        if mode == "into":
            target["children"].append(node)
        else:
            i = tparent["children"].index(target)
            tparent["children"].insert(i + (1 if mode == "after" else 0), node)
    survey.flow = root
    normalize(survey)
    return True


def update(survey, nid, form, languages, links=True):
    """Save the settings of a flow element from its form; returns an error message or None. links=False: the
    completion links of an end stay as stored. Where participants are sent is decided by the admins of the
    survey, as with the links in the settings (panel.py); the role "Bearbeiten" changes everything else."""
    root = normalize(survey)
    node, _ = find(root, nid)
    if node is None:
        return de_en("Element nicht gefunden.", "Element not found.")
    if node["t"] == "rand":
        code = form.get("code", "").strip()
        if not CODE_RE.match(code):
            return de_en("Variablenname: Buchstabe am Anfang, dann Buchstaben, Ziffern oder _.",
                         "Variable name: a letter first, then letters, digits or _.")
        k = form.get("k", "1")
        node.update(code=code, name=form.get("name", "").strip()[:100] or code,
                    k=max(1, int(k)) if k.isascii() and k.isdigit() and len(k) < 12 else 1,  # "²".isdigit() is true
                    balanced=bool(form.get("balanced")), shuffle=bool(form.get("shuffle")))
    elif node["t"] == "branch" and "op" not in form:
        # the page: a question is picked, its answers ticked or a range entered (conditions.from_form)
        from app import conditions

        described = conditions.describe(survey, everything=True)
        rows = sorted({int(m.group(1)) for k in form for m in [re.fullmatch(r"c(\d{1,2})_item", k)] if m})
        if rows:  # the page: one or several conditions (fields "c<k>_…"), all or one of them must hold
            found = [c for c in (conditions.from_form(form, f"c{k}", described) for k in rows[:MAX_CONDS]) if c]
            if len(found) > 1:
                node["cond"] = {"conds": found, "rule": "any" if form.get("rule") == "any" else "all"}
            else:
                node["cond"] = found[0] if found else {"item": None, "op": "eq", "value": ""}
        else:  # one condition with fields without a prefix (older pages, scripts)
            cond = conditions.from_form(form, "", described)
            node["cond"] = cond or {"item": None, "op": "eq", "value": ""}
        if "name" in form:
            node["name"] = form.get("name", "").strip()[:100]
    elif node["t"] == "branch":
        # the stored form of a condition (question, comparison, value), as scripts and older pages send it
        item = form.get("item", "")
        op = form.get("op", "eq")
        # a question (its id), a URL parameter ("p:name"), what a quota calculation decided ("q:<id>", "qc:<id>"), or
        # the attention checks failed so far ("att", app/quality.py)
        own = {i.id for b in survey.blocks for i in b.items}
        ref = (int(item) if int(item) in own else None) if item.isdigit() and item.isascii()             else (item if item.startswith(("p:", "q:", "qc:", STATE_REF)) or item in ("att", "tempo", "geo:country", "geo:state") else None)
        node["cond"] = {"item": ref, "op": op if op in OPS else "eq", "value": form.get("value", "").strip()[:200]}
        if "name" in form:
            node["name"] = form.get("name", "").strip()[:100]
    elif node["t"] == "end" and form.getlist("case"):
        # the page: a list of triggers. The fields of the older form (one kind, its quotas and links) go
        found, error = cases_from_form(survey, form, languages, links, cases(node, expand=True))
        if error:
            return error
        for key in ("kind", "text", "link", "link_mode", "quotas", "rule", "links"):
            node.pop(key, None)
        node["cases"] = found
    elif node["t"] == "end":
        # the older form of an end, as scripts send it: one kind, with its quotas and their links
        from app import panel

        names = panel.link_names(survey)
        if not links:
            sent = [k for k in form if k in ("link", "link_mode") or k.startswith(("qlink_", "qmode_", "clink_"))]
            if sent:
                return de_en("Abschlusslinks legt fest, wer in dieser Befragung die Rolle „Admin“ hat. Es wurde nichts gespeichert.",
                             "Completion links are set by whoever has the role “Admin” in this survey. Nothing was saved.")
        link = form.get("link", "").strip()[:1000] if links else node.get("link") or ""
        error = panel.link_error(link, names) if links else None
        if error:
            return de_en("Abschlusslink: {error}", "Completion link: {error}", error=error)
        kind = form.get("kind") if form.get("kind") in END_KINDS else "screenout"
        wanted, own_links = [], {}
        if kind == "quotafull":
            # the quotas it looks at, and for each a completion link of its own, also per cell (end_link)
            ticked = set(form.getlist("quotas"))
            for quota in survey.quotas:
                if str(quota.id) not in ticked:
                    continue
                wanted.append(quota.id)
                if not links:  # the stored links of the quotas that stay ticked
                    if str(quota.id) in (node.get("links") or {}):
                        own_links[str(quota.id)] = node["links"][str(quota.id)]
                    continue
                url = form.get(f"qlink_{quota.id}", "").strip()[:1000]
                mode = form.get(f"qmode_{quota.id}") if form.get(f"qmode_{quota.id}") in panel.LINK_MODES else ""
                cells = {c.code: form.get(f"clink_{quota.id}_{c.code}", "").strip()[:1000] for c in quota.cells}
                cells = {code: u for code, u in cells.items() if u}
                for where, u in [(f"„{quota.name}“", url)] + [(de_en("„{name}“, Teilquote {code}", "“{name}”, sub-quota {code}",
                                                                           name=quota.name, code=code), u) for code, u in cells.items()]:
                    error = panel.link_error(u, names)
                    if error:
                        return de_en("Abschlusslink zu {where}: {error}", "Completion link for {where}: {error}", where=where, error=error)
                if url or mode or cells:
                    own_links[str(quota.id)] = {"link": url, "mode": mode, "cells": cells}
        node["text"] = {l: form.get(f"text_{l}", "").strip()[:3000] for l in languages}
        node["kind"] = kind
        node["link"] = link
        if links:
            node["link_mode"] = form.get("link_mode") if form.get("link_mode") in panel.LINK_MODES else "now"
        node.pop("links", None)
        node.pop("cases", None)
        if kind == "quotafull":
            # ... and whether one full quota is enough or all must be full
            node["quotas"] = wanted
            node["rule"] = "all" if form.get("rule") == "all" else "any"
            if own_links:
                node["links"] = own_links
        else:
            node.pop("rule", None)
            node.pop("quotas", None)
    elif node["t"] == "quota":
        own = {q.id for q in survey.quotas}
        elsewhere = {q for n, _, _ in walk(root) if n["t"] == "quota" and n is not node for q in n.get("quotas") or []}
        wanted = [int(q) for q in form.getlist("quotas") if q.isascii() and q.isdigit() and len(q) < 12 and int(q) in own]
        if any(q in elsewhere for q in wanted):
            return de_en("Eine der gewählten Quoten wird schon an einer anderen Stelle berechnet; jede Quote nur an einer.",
                         "One of the chosen quotas is already calculated elsewhere; each quota only in one place.")
        node["quotas"] = wanted
    elif node["t"] == "ext":
        from app import external

        survey.flow = root  # the other external sections are looked up in the tree at hand
        error = external.update(survey, node, form, languages, links)
        if error:
            return error
    elif node["t"] == "ws":
        from app import webservice

        survey.flow = root
        error = webservice.update(survey, node, form, links)
        if error:
            return error
    survey.flow = root
    return None


def remove(survey, nid):
    """Delete a group (its children move up to its place) or an end node. Blocks are deleted in the editor."""
    root = normalize(survey)
    node, parent = find(root, nid)
    if node is None or node["t"] in ("block", "split"):  # the separated section cannot be removed
        return False
    i = parent["children"].index(node)
    parent["children"][i:i + 1] = node.get("children", [])
    survey.flow = root
    normalize(survey)
    return True


def move_block_among_siblings(survey, block_id, direction):
    """The editor's ↑/↓ on a block: move within its group in the flow. A block that is the only one in
    its loop moves together with the loop (otherwise it could never leave it, e.g. across the split)."""
    root = normalize(survey)
    node, parent = find(root, f"k{block_id}")
    if node is not None and parent is not root and parent["t"] == "loop" and len(parent.get("children", [])) == 1:
        return move(survey, parent["id"], direction)
    return move(survey, f"k{block_id}", direction)


# --- labels and badges -------------------------------------------------------------------


def branch_names(survey):
    """{variable name of a randomizer: its name}: what texts can show of the flow as {{cond1}}."""
    return {n["code"]: n.get("name") or n["code"] for n, _, _ in walk(tree(survey)) if n["t"] == "rand" and n.get("code")}


def branch_values(survey, response):
    """{variable name of a randomizer: what was drawn for this participation}, as in its column of the data
    set, the labels joined by ", "; "" where the randomizer was not reached or nothing is drawn yet."""
    drawn = reached_conditions(getattr(response, "layout", None))
    out = {}
    for node, _, _ in walk(tree(survey)):
        if node["t"] != "rand" or not node.get("code"):
            continue
        children = {node_id(c): c for c in node.get("children", [])}
        out[node["code"]] = ", ".join(child_label(survey, children[c]) for c in drawn.get(node["id"], []) if c in children)
    return out


def child_label(survey, node):
    if node["t"] == "block":
        block = next((b for b in survey.blocks if b.id == node["block"]), None)
        return (block.title() if block else "") or f"Block {node['block']}"
    if node["t"] in ("rand", "loop"):
        return node.get("name") or node.get("code")
    if node["t"] == "branch":
        return node.get("name") or "Bedingung"
    if node["t"] == "else":
        return "Sonst"
    if node["t"] == "split":
        return "Getrennter Abschnitt"
    if node["t"] == "quota":
        return "Quotenberechnung"
    if node["t"] == "ext":
        return node.get("name") or node.get("code") or "Externer Abschnitt"
    if node["t"] == "ws":
        return node.get("name") or node.get("code") or "Web Service"
    # a screen-out stays "Ende": the label is a value in the export (a randomizer's chosen elements), always German
    when = _end_when(node)
    if when == "screenout":
        return "Ende"
    return f"Ende ({END_TRIGGERS[when][0] if when else 'mehrere Auslöser'})"


def _end_when(node):
    """The trigger an end is called after (an END_TRIGGERS key), or None for several."""
    found = cases(node)
    if len(found) != 1:
        return None
    return found[0].get("when") if found[0].get("when") in END_TRIGGERS else "screenout"


def end_label(node):
    """What an end is called after, in the language of the page: its trigger, or "mehrere Auslöser"."""
    when = _end_when(node)
    return trigger_name(when) if when else de_en("mehrere Auslöser", "several triggers")


def answer_codes(item, lang=None):
    """[(code, label)] of a question with fixed answer options whose answer is such a code (single and multiple
    choice, Bundesland), else []: what a condition on that question can be compared with. A Q-Sort, a card
    sorting, a ranking, a constant sum or a 2D positioning has options too, but its answer is no code: a
    condition on it could never hold (X17)."""
    options = getattr(item.kind, "options", None)
    if not callable(options) or not getattr(item.kind, "code_answer", False) or not (item.config or {}).get("options"):
        return []
    from app.items.base import answer_label

    # labels for the researchers' pages: without the colon of "Anderes, und zwar:" (U27)
    return [(str(o["code"]), answer_label(o.get("label"))) for o in options(item, lang or item.block.survey.default_language)]


def condition_text(survey, cond, ui=None):
    """A condition as the flow shows it, with the wording of the answers (conditions.text). ui: the words around
    the answers in this language ("de"/"en"); None: German, as before."""
    from app import conditions

    return conditions.text(survey, cond, ui=ui)


def badges(survey, ui=None):
    """{block_id: [label, ...]} describing the flow context of each block, outermost first. ui: the language of
    the words ("de"/"en"; None: the page's, German outside a request), e.g. the reader's of the Word codebook."""
    from app.items.base import reader

    en = reader(ui) == "en"
    root = tree(survey)
    out = {}
    for node, _, _ in walk(root):
        if node["t"] != "block":
            continue
        labels = []
        for g in ancestors(root, node_id(node)):
            if g["t"] == "rand":
                n = len(g.get("children", []))
                labels.append(f"🎲 {g.get('name')}: {g.get('k', 1)} of {n}" if en else f"🎲 {g.get('name')}: {g.get('k', 1)} von {n}")
            elif g["t"] == "loop":
                from app import loops

                n_rows = len(g.get("rows", []))
                shown = loops.shown_rows(survey, loops.config_node(g))
                name = g.get("name") or g.get("code")
                labels.append(f"🔁 {name}: {shown} of {n_rows} rows" if en else f"🔁 {name}: {shown} von {n_rows} Zeilen")
            elif g["t"] == "else":
                branch, _ = find(root, g.get("of", ""))
                said = condition_text(survey, (branch or {}).get("cond", {}), "en" if en else None)
                labels.append(f"↳ otherwise (not {said})" if en else f"↳ sonst (nicht {said})")
            else:
                said = condition_text(survey, g.get("cond", {}), "en" if en else None)
                labels.append(f"↳ if {said}" if en else f"↳ wenn {said}")
        out[node["block"]] = labels
    return out


def blocks_after_split(survey):
    """Block ids after the separation point, in flow order ([] without a split)."""
    seen, after = False, []
    for n, _, _ in walk(tree(survey)):
        if n["t"] == "split":
            seen = True
        elif seen and n["t"] == "block":
            after.append(n["block"])
    return after


# --- runtime ----------------------------------------------------------------------------------


def _assignment_counts(survey, rid):
    """How often each child of randomizer `rid` has been chosen by real participants (app/balance.py)."""
    from app import balance

    return balance.counts(survey)["rand"].get(rid, {})


def choose(survey, node, rng):
    """Pick k children (ids) of a randomizer: balanced = least-used combination first."""
    children = [node_id(c) for c in node.get("children", [])]
    k = min(max(node.get("k", 1), 1), len(children)) if children else 0
    if not k:
        return []
    if node.get("balanced", True):
        from app import balance

        picked = balance.least_used(children, _assignment_counts(survey, node["id"]), k, rng)
    else:
        picked = rng.sample(children, k)
    if node.get("shuffle"):
        rng.shuffle(picked)
    else:
        picked.sort(key=children.index)
    return picked


def plan(survey, rng, within=None, keep=None):
    """Flatten the tree for one response: [("block", block_id, conds, loop) | ("end", node_id, conds, None)],
    where loop is None or {"loop": node id, "row": row id, "iter": n, "iters": total}; plus the
    randomizer decisions {rid: [child ids in presentation order]} and loop rows {lid: [row ids]}.
    within: a dict that gets {"cond": {randomizer id: the branch conditions it lies behind}} for the randomizers
    behind a branch (runtime.build_layout: they count once reached).
    keep: the layout drawn before for this participation (a new layout after the questionnaire was changed during
    fieldwork, runtime.ensure_layout): its conditions and loop rows stay where their nodes still exist, only what
    is new is drawn (audit 2026-10-10, T-2)."""
    from app import loops

    kept_conditions = (keep or {}).get("conditions") or {}
    kept_rows = (keep or {}).get("loops") or {}

    root = tree(survey)
    decisions, chosen_rows = {}, {}
    out = []
    separated = has_separated(survey)
    behind = {} if within is None else within.setdefault("cond", {})

    def rec(node, conds, ctx=None):
        for child in node.get("children", []):
            emit(child, conds, ctx)

    def emit(child, conds, ctx=None):
        if child["t"] == "block":
            out.append(("block", child["block"], conds, ctx))
        elif child["t"] == "end":
            out.append(("end", child["id"], conds, None))
        elif child["t"] == "quota":
            out.append(("quota", child["id"], conds, None))
        elif child["t"] == "ext":
            if ctx is None:  # not once per pass of a loop (the pre-launch check says so)
                out.append(("ext", child["id"], conds, None))
        elif child["t"] == "ws":
            if ctx is None:  # called once per participation, not per pass of a loop (the pre-launch check says so)
                out.append(("ws", child["id"], conds, None))
        elif child["t"] == "split":
            if separated:  # an empty separated section changes nothing for participants
                out.append(("split", child["id"], conds, None))
        elif child["t"] == "branch":
            rec(child, conds + [child["id"]], ctx)
        elif child["t"] == "else":
            rec(child, conds + ["!" + child.get("of", "")], ctx)
        elif child["t"] == "rand":
            picked = _kept_choice(child, kept_conditions.get(child["id"])) if ctx is None else None
            if picked is None:
                picked = choose(survey, child, rng)
            decisions[child["id"]] = picked
            if conds:
                behind[child["id"]] = list(conds)
            by_id = {node_id(c): c for c in child.get("children", [])}
            for cid in picked:
                emit(by_id[cid], conds, ctx)
        elif child["t"] == "loop":
            loop = loops.config_node(child)
            if not (loop["columns"] and loop["rows"]) or ctx is not None:
                rec(child, conds, ctx)  # an empty (or nested) loop behaves like a plain group
                return
            rows = _kept_rows(child, loop, kept_rows.get(child["id"]), rng)
            if rows is None:
                rows = loops.choose_rows(survey, child, rng)
            chosen_rows[child["id"]] = rows
            for n, row in enumerate(rows, start=1):
                rec(child, conds, {"loop": child["id"], "row": row, "iter": n, "iters": len(rows)})

    rec(root, [])
    return out, decisions, chosen_rows


def _kept_choice(node, before):
    """The children a randomizer drew before for this participation, if they are all still its children and as
    many as it draws now; else None (drawn anew)."""
    if not isinstance(before, list) or not before:
        return None
    children = [node_id(c) for c in node.get("children", [])]
    k = min(max(node.get("k", 1), 1), len(children)) if children else 0
    if len(before) != k or len(set(before)) != k or any(c not in children for c in before):
        return None
    return list(before)


def _kept_rows(node, loop, before, rng):
    """The rows (or the deck) a loop drew before for this participation, if they all still exist; else None
    (drawn anew). A loop that shows every row of its table keeps the order of the rows it had and adds the rows
    that are new."""
    if not isinstance(before, list) or not before:
        return None
    ids = [r["id"] for r in loop["rows"]]
    if any(r not in ids for r in before):
        return None
    every = (node.get("source") != "factors" and node.get("mode") != "plan" and loop["mode"] != "level"
             and not (loop["mode"] == "k" and loop["k"] < len(ids)))
    if not every:
        if loop["mode"] == "k" and node.get("source") != "factors" and node.get("mode") != "plan"                 and len(before) != min(loop["k"], len(ids)):
            return None  # as many rows as the loop draws now
        return list(before)
    new = [r for r in ids if r not in before]
    if loop["order"] == "random":
        rng.shuffle(new)
    return list(before) + new


def reached_conditions(layout):
    """{randomizer id: child ids drawn} of a layout, without the randomizers behind a branch condition that this
    participation has not reached yet (none of their pages or ends was passed, runtime.reach): drawn for everybody
    at the start, they say nothing about the people outside the branch (data set, balance, {{cond}})."""
    layout = layout or {}
    drawn = layout.get("conditions") or {}
    behind = set(layout.get("rand_in") or ())
    if not behind:
        return drawn
    reached = set(layout.get("reached") or ())
    return {rid: picked for rid, picked in drawn.items() if rid not in behind or rid in reached}


def conditions_by_id(survey):
    return {n["id"]: n.get("cond", {}) for n, _, _ in walk(tree(survey)) if n["t"] == "branch"}


def uses_ref(survey, ref, conds=None):
    """A condition of the flow reads `ref` ("att", "tempo", ...), also as one part of a condition with several
    (parts). conds: conditions_by_id(survey), if already at hand."""
    found = conditions_by_id(survey) if conds is None else conds
    return any(p.get("item") == ref for c in found.values() for p in parts(c))


STATE_REF = "st:"  # "st:<id of a postal code question>": the federal state the answered postal code lies in


def source_id(ref):
    """The id of the question a condition reads: the question itself, or the postal code question behind
    "st:<id>"; None for URL parameters and quotas."""
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str) and ref.startswith(STATE_REF) and ref[len(STATE_REF):].isascii() and ref[len(STATE_REF):].isdigit():
        return int(ref[len(STATE_REF):])
    return None


def computed_value(item_id, answers, survey):
    """The number of a Rechenwert (app/items/calc.py) for these answers: it is stored nowhere and follows from
    them whenever a condition asks for it. None for every other question (it has no answer yet). survey: only a
    Rechenwert of this survey is read (conditions.own_item; a condition sent with the id of another survey's
    question reads nothing there, security audit 2026-10-06, H5); without a survey nothing is read."""
    from flask import has_app_context

    if survey is None or not has_app_context():  # (a bare check of answers needs no database)
        return None
    from app.conditions import own_item

    item = own_item(survey, item_id)
    return item.kind.value(item, answers) if item is not None and item.kind.computed else None


def parts(cond):
    """The single conditions of a condition: several ({"conds": [...], "rule": "all" | "any"}, an element
    "Bedingung" with more than one) or just itself."""
    if isinstance(cond, dict) and isinstance(cond.get("conds"), list):
        return [c for c in cond["conds"] if isinstance(c, dict)]
    return [cond] if isinstance(cond, dict) else []


def holds(cond, answers, survey=None):
    """Evaluate a branch condition against the answers given so far (item id -> answer). Several conditions
    (parts): all of them must hold, or with "rule": "any" one of them. survey: the survey the condition belongs
    to, so that a Rechenwert is read only from its own questions (computed_value); every caller in the app passes
    it, without it (bare checks of answers) no Rechenwert is read."""
    if isinstance(cond, dict) and isinstance(cond.get("conds"), list):
        found = [holds(c, answers, survey) for c in parts(cond)]
        return bool(found) and (any(found) if cond.get("rule") == "any" else all(found))
    item = cond.get("item")
    if item is None:
        return False
    from app.items.choice import selected

    if isinstance(item, str) and item.startswith(STATE_REF):
        from app.items.postal_code import state

        given = answers.get(source_id(item))
        if isinstance(given, dict):  # a location (items/geolocation.py): the state it lies in, "0" outside Germany
            answer = given.get("state") or None
        else:
            answer = state(given)  # None for no answer and for a code that does not exist
    elif isinstance(item, int) and item not in answers:
        answer = computed_value(item, answers, survey)  # a Rechenwert, or None: not answered (yet)
    else:
        answer = selected(answers.get(item))  # the chosen code(s), without a text typed next to an option
        if isinstance(answer, dict) and "_hidden" in answer:
            answer = {k: v for k, v in answer.items() if k != "_hidden"}  # a matrix: without the note of rows not shown
        if isinstance(answer, dict) and set(answer) == {"o", "t"}:  # an IAT (items/iat.py): its D score, a number
            from app.items import get

            answer = get("iat").scores(answer).get("d")
    op, value = cond.get("op", "eq"), str(cond.get("value", ""))
    empty = answer in (None, "", [], {})
    if op == "answered":
        return not empty
    if op == "empty":
        return empty
    if empty:
        return False
    if op == "contains":
        return value in [str(a) for a in answer] if isinstance(answer, list) else value in str(answer)
    if op in ("in", "nin"):  # one of several codes (none of them); a multiple choice: one of them is among the chosen ones
        wanted = code_list(value)
        found = any(str(a) in wanted for a in answer) if isinstance(answer, list) else str(answer) in wanted
        return found if op == "in" else not found
    if op == "between":
        lo, hi = bounds(value)
        return lo is not None and holds({**cond, "op": "ge", "value": lo}, answers, survey) \
            and holds({**cond, "op": "le", "value": hi}, answers, survey)
    if isinstance(answer, list):
        answer = ",".join(str(a) for a in answer)
    from app.conditions import read_number
    from app.items.date_time import compare

    if op in ("le", "ge"):
        strict = "lt" if op == "le" else "gt"
        dated = compare(answer, strict, value)
        if dated is not None:
            return dated or bool(compare(answer, "eq", value))
        # the value compared with and the answer are read alike: "1.000" is 1000 on both sides (R-2)
        a, v = read_number(answer), read_number(value)
        if a is None or v is None:
            return False
        return a <= v if op == "le" else a >= v
    dated = compare(answer, op, value)  # a date or a time of day ("1987-05-23", "14:30"), see items/date_time.py
    if dated is not None:
        return dated
    if op in ("lt", "gt"):
        a, v = read_number(answer), read_number(value)
        if a is None or v is None:
            return False
        return a < v if op == "lt" else a > v
    equal = str(answer) == value
    if isinstance(answer, (int, float)) and not isinstance(answer, bool):
        # the answer of a number question is stored as a number: 2.5 equals "2,5", 2.0 equals "2" and 18 equals
        # "018" and 1000 equals "1.000" ("von 2,5 bis 2,5" is stored as = 2,5). Answer codes are texts and stay
        # compared as texts.
        number = read_number(value)
        if number is not None:
            equal = answer == number
    return equal if op == "eq" else not equal


# --- pre-launch check and figures ----------------------------------------------------------------


def check(survey):
    """[(severity, message, block_id or None)]"""
    from app.conditions import number_problems
    from app.extensions import db
    from app.models import Item

    problems = []
    root = tree(survey)
    order = block_order(root)
    consent = survey.consent_item
    around = ancestors(root, f"k{consent.block_id}") if consent is not None else []
    if around:
        problems.append(("error", de_en("Die Einwilligung steht in {group}: So sehen sie nicht alle, und Teilnahmen kämen ohne "
                                        "Einwilligung zustande. Bitte den Block im Reiter „Ablauf“ vor die Gruppe ziehen.",
                                        "The consent is in {group}: not everyone sees it, and participations would come "
                                        "about without consent. Please drag the block in front of the group on the tab “Flow”.",
                                        group=group_name(around[-1]["t"])), consent.block_id))
    item_block = {}
    for b in survey.blocks:
        for i in b.items:
            item_block[i.id] = b.id
    in_loop = loop_blocks(survey)
    walked = [n for n, _, _ in walk(root)]
    for node, parent, _ in walk(root):
        if node["t"] == "rand":
            n = len(node.get("children", []))
            if n < 2:
                problems.append(("error", de_en("Randomisierung „{name}“ braucht mindestens zwei Elemente.",
                                                "Randomisation “{name}” needs at least two elements.", name=node.get("name")), None))
            elif node.get("k", 1) > n:
                problems.append(("error", de_en("Randomisierung „{name}“ zeigt {k} von nur {n} Elementen.",
                                                "Randomisation “{name}” shows {k} of only {n} elements.",
                                                name=node.get("name"), k=node["k"], n=n), None))
        elif node["t"] == "else":
            if not node.get("children"):
                problems.append(("warning", de_en("„Sonst“ ist leer: dann einfach entfernen.", "“Else” is empty: just remove it."), None))
        elif node["t"] == "branch":
            for cond in parts(node.get("cond", {})):  # each condition of the element on its own
                ref = cond.get("item")
                first_inside = next((n["block"] for n, _, _ in walk(node) if n["t"] == "block"), None)
                if isinstance(ref, str) and ref.startswith(("q:", "qc:")):
                    problems += _check_quota_cond(survey, root, node, cond, first_inside)
                    continue
                if ref == "att":
                    from app import quality

                    if not quality.survey_checks(survey):
                        problems.append(("error", de_en("Bedingung auf Aufmerksamkeitsprüfungen, aber der Fragebogen hat keine "
                                                        "(Auswahl „Prüfung“ an einer Aussage der Likert-Matrix).",
                                                        "Condition on attention checks, but the questionnaire has none "
                                                        "(choice “check” on a statement of the Likert matrix)."), first_inside))
                    continue
                if ref == "tempo":
                    from app import tempo

                    if not tempo.active(survey):
                        problems.append(("error", de_en("Bedingung auf zu schnell abgeschickte Seiten, aber der Tempo-Hinweis ist "
                                                        "aus (Qualitätssicherung, „Vor dem Start: Datenqualität steigern“).",
                                                        "Condition on pages sent too fast, but the speed prompt is off "
                                                        "(quality assurance, “Before the start: improve data quality”)."), first_inside))
                    continue
                if ref in ("geo:country", "geo:state"):
                    if not getattr(survey, "geo_ip", False):
                        problems.append(("error", de_en("Bedingung auf Land oder Bundesland aus der IP-Adresse, aber die Einstellung "
                                                        "„Land und Bundesland aus der IP-Adresse schätzen“ ist aus (Einstellungen, "
                                                        "Einwilligung und Datenschutz).",
                                                        "Condition on country or federal state from the IP address, but the setting "
                                                        "“Estimate country and federal state from the IP address” is off (settings, "
                                                        "consent and data protection)."),
                                         first_inside))
                    continue
                if isinstance(ref, str) and ref.startswith("p:"):
                    from app import panel

                    if ref[2:] not in [p["name"] for p in panel.config(survey)["params"]]:
                        problems.append(("error", de_en("Bedingung auf URL-Parameter „{name}“, der nicht deklariert ist.",
                                                        "Condition on URL parameter “{name}”, which is not declared.", name=ref[2:]),
                                         first_inside))
                    continue
                item = db.session.get(Item, source_id(ref)) if source_id(ref) in item_block else None  # a question of this survey
                if item is None:
                    problems.append(("error", de_en("Bedingung ohne Frage: bitte eine Frage auswählen.",
                                                    "Condition without a question: please choose a question."), first_inside))
                elif isinstance(ref, str):  # the state of a postal code: the codes of the 16 states
                    from app.items.federal_state import STATE_CODES

                    wanted = code_list(cond.get("value")) if cond.get("op", "eq") in ("in", "nin") else [str(cond.get("value", ""))]
                    if item.type not in ("postal_code", "geolocation"):
                        problems.append(("error", de_en("Bedingung „{c}“: Das Bundesland gibt es nur zu einer Frage nach der "
                                                        "Postleitzahl oder einem Standort.",
                                                        "Condition “{c}”: the federal state exists only for a question about the "
                                                        "postal code or a location.", c=condition_text(survey, cond)), first_inside))
                    elif cond.get("op", "eq") not in ("answered", "empty") and [
                            v for v in wanted if v not in STATE_CODES and not (v == "0" and item.type == "geolocation")]:
                        problems.append(("warning", de_en("Bedingung „{c}“ nennt kein Bundesland; so trifft sie nie zu.",
                                                          "Condition “{c}” names no federal state, so it never holds.",
                                                          c=condition_text(survey, cond)), first_inside))
                elif cond.get("op", "eq") == "between" and bounds(cond.get("value"))[0] is None:
                    problems.append(("warning", de_en("Bedingung „{c}“: „von … bis“ braucht zwei Werte, geschrieben als 18..29; "
                                                      "so trifft sie nie zu.",
                                                      "Condition “{c}”: “from … to” needs two values, written as 18..29, so it "
                                                      "never holds.", c=condition_text(survey, cond)), first_inside))
                elif cond.get("op", "eq") in ("in", "nin") and answer_codes(item) \
                        and [v for v in code_list(cond.get("value")) if v not in [c for c, _ in answer_codes(item)]]:
                    codes = ", ".join(c for c, _ in answer_codes(item))
                    problems.append(("warning", de_en("Bedingung „{c}“ nennt einen Wert, der kein Antwortcode von {code} ist "
                                                      "(vorhanden: {codes}).",
                                                      "Condition “{c}” names a value that is not an answer code of {code} "
                                                      "(there are: {codes}).", c=condition_text(survey, cond), code=item.code,
                                                      codes=codes), first_inside))
                elif cond.get("op", "eq") in ("eq", "ne", "contains") and answer_codes(item) \
                        and str(cond.get("value", "")) not in [c for c, _ in answer_codes(item)]:
                    codes = ", ".join(c for c, _ in answer_codes(item))
                    problems.append(("warning", de_en("Bedingung „{c}“: „{value}“ ist kein Antwortcode von {code} (vorhanden: "
                                                      "{codes}); so trifft sie nie zu.",
                                                      "Condition “{c}”: “{value}” is not an answer code of {code} (there are: "
                                                      "{codes}), so it never holds.", c=condition_text(survey, cond),
                                                      value=cond.get("value", ""), code=item.code, codes=codes), first_inside))
                elif number_problems(cond, item):
                    problems.append(("warning", de_en("Bedingung „{c}“: {problem}.", "Condition “{c}”: {problem}.",
                                                      c=condition_text(survey, cond), problem=number_problems(cond, item)[0]),
                                     first_inside))
                elif item.type == "date_time" and cond.get("op", "eq") in ("eq", "ne", "lt", "gt", "le", "ge") \
                        and not item.kind.condition_ok(item, cond.get("value", "")):
                    example = {"year": "2000", "month": "05.2000", "time": "14:30"}.get(item.kind.asked(item), "23.05.2000")
                    problems.append(("warning", de_en("Bedingung „{c}“: „{value}“ lässt sich nicht mit den Antworten von {code} "
                                                      "vergleichen (erwartet z. B. {example}); so trifft sie nie zu.",
                                                      "Condition “{c}”: “{value}” cannot be compared with the answers of {code} "
                                                      "(expected e.g. {example}), so it never holds.",
                                                      c=condition_text(survey, cond), value=cond.get("value", ""), code=item.code,
                                                      example=example), first_inside))
                elif not node.get("children"):
                    problems.append(("warning", de_en("Bedingung „{c}“ ist leer.", "Condition “{c}” is empty.",
                                                      c=condition_text(survey, cond)), None))
                if item is not None and item_block[item.id] in in_loop:
                    problems.append(("error", de_en("Bedingung „{c}“ bezieht sich auf eine Frage in einer Schleife: Ihre "
                                                    "Antworten gibt es je Durchgang, die Bedingung träfe nie zu.",
                                                    "Condition “{c}” refers to a question in a loop: its answers exist per "
                                                    "pass, so the condition would never hold.", c=condition_text(survey, cond)),
                                     first_inside))
                elif item is not None and item_block[item.id] in order:
                    # also a condition that holds only an end (a screen-out): it must stand behind its question
                    at = next(n for n, other in enumerate(walked) if other is node)
                    if item_block[item.id] not in [n["block"] for n in walked[:at] if n["t"] == "block"]:
                        problems.append(("error", de_en("Bedingung „{c}“ bezieht sich auf eine Frage, die erst danach kommt.",
                                                        "Condition “{c}” refers to a question that only comes later.",
                                                        c=condition_text(survey, cond)), first_inside))
        elif node["t"] == "loop" and any(a["t"] == "loop" for a in ancestors(root, node["id"])):
            problems.append(("error", de_en("Schleife „{name}“ steht in einer anderen Schleife; das ist nicht möglich.",
                                            "Loop “{name}” is inside another loop; that is not possible.", name=node.get("name")),
                             None))
        elif node["t"] == "end":
            from app import panel

            for url in node_links(node):
                error = panel.link_error(url, panel.link_names(survey))
                if error:
                    problems.append(("error", de_en("Ende ({label}), Abschlusslink: {error}", "End ({label}), completion link: {error}",
                                                    label=end_label(node), error=error), None))
            # a trigger on quotas is a condition in itself: it ends only those for whom it holds. One that
            # always holds ends everybody who gets there, and no trigger behind it is ever asked
            found = cases(node)
            always = next((n for n, case in enumerate(found) if case_holds(case, {})), None)
            if always is not None and parent is root:
                problems.append(("warning", de_en("Ein Ende außerhalb einer Bedingung beendet die Befragung für alle.",
                                                  "An end outside a condition ends the survey for everyone."), None))
            if always is not None and always < len(found) - 1:
                problems.append(("warning", de_en("Ende: Der Auslöser „{name}“ greift immer; die Auslöser dahinter kommen nie zum "
                                                  "Zug. Bitte ans Ende der Liste setzen.",
                                                  "End: the trigger “{name}” always applies; the triggers after it never get "
                                                  "their turn. Please move it to the end of the list.",
                                                  name=trigger_name(found[always]["when"])), None))
    problems += _check_split(survey, root, order)
    from app import quotas

    problems += [(severity, message, None) for severity, message in quotas.check(survey)]
    from app import external

    problems += external.check(survey)
    from app import webservice

    problems += webservice.problems(survey)
    return problems


def _check_quota_cond(survey, root, node, cond, first_inside):
    """A condition on what a quota calculation decided: the quota exists, is calculated before, and the value means
    something."""
    kind, _, qid = cond["item"].partition(":")
    quota = next((q for q in survey.quotas if str(q.id) == qid), None)
    if quota is None:
        return [("error", de_en("Bedingung auf eine Quote, die es nicht mehr gibt: bitte neu wählen.",
                                "Condition on a quota that no longer exists: please choose again."), first_inside)]
    text = condition_text(survey, cond)
    order = [n for n, _, _ in walk(root)]
    checks = [n for n in order if n["t"] == "quota" and quota.id in (n.get("quotas") or [])]
    problems = []
    if not checks:
        problems.append(("error", de_en("Bedingung „{c}“: Diese Quote wird im Ablauf nirgends berechnet.",
                                        "Condition “{c}”: this quota is not calculated anywhere in the flow.", c=text), first_inside))
    elif order.index(checks[0]) > order.index(node):
        problems.append(("error", de_en("Bedingung „{c}“: Die Quote wird erst danach berechnet; so trifft die Bedingung nie zu.",
                                        "Condition “{c}”: the quota is only calculated later, so the condition never holds.", c=text),
                         first_inside))
    op, value = cond.get("op", "eq"), str(cond.get("value", ""))
    known = ["in", "full", "none"] if kind == "q" else [c.code for c in quota.cells]
    wanted = code_list(value) if op in ("in", "nin") else [value] if op in ("eq", "ne") else []
    if [v for v in wanted if v not in known] or (op in ("eq", "ne", "in", "nin") and not wanted):
        problems.append(("warning", de_en("Bedingung „{c}“: „{value}“ kommt nicht vor (möglich: {known}); so trifft sie nie zu.",
                                          "Condition “{c}”: “{value}” does not occur (possible: {known}), so it never holds.",
                                          c=text, value=value, known=", ".join(known)), first_inside))
    if not node.get("children"):
        problems.append(("warning", de_en("Bedingung „{c}“ ist leer.", "Condition “{c}” is empty.", c=text), None))
    return problems


def _check_split(survey, root, order):
    """The separated section must not reveal anything about the main part."""
    from app import loops

    splits = [(n, p) for n, p, _ in walk(root) if n["t"] == "split"]
    if not splits:
        return []
    problems = []
    if len(splits) > 1:
        problems.append(("error", de_en("Es darf nur einen getrennten Abschnitt geben.", "There may be only one separated section."), None))
    node, parent = splits[0]
    if parent is not root:
        problems.append(("error", de_en("Der getrennte Abschnitt muss auf oberster Ebene stehen, nicht in einer Gruppe.",
                                        "The separated section must be at the top level, not in a group."), None))
    after = blocks_after_split(survey)
    if not after:
        return problems  # empty: nothing is stored separately
    consent = survey.consent_item
    if consent is not None and consent.block_id in after:
        problems.append(("error", de_en("Die Einwilligung muss vor dem getrennten Abschnitt stehen.",
                                        "The consent must come before the separated section."), consent.block_id))
    seen = False
    for n, p, _ in walk(root):
        if n["t"] == "split":
            seen = True
            continue
        if seen and n["t"] in ("rand", "branch", "else", "end", "loop", "quota"):
            problems.append(("error", de_en("Nach dem getrennten Abschnitt sind keine Randomisierungen, Bedingungen, "
                                            "Quotenberechnungen oder Enden erlaubt: Sonst verrät der Ablauf frühere Antworten.",
                                            "No randomisations, conditions, quota calculations or ends are allowed after the "
                                            "separated section: otherwise the flow gives away earlier answers."), None))
            break
        if seen and n["t"] == "ext":
            problems.append(("error", de_en("Nach dem getrennten Abschnitt ist kein externer Abschnitt möglich: Dort gibt es "
                                            "keine Teilnahme-ID, zu der jemand zurückkehren könnte.",
                                            "No external section is possible after the separated section: there is no "
                                            "participation ID there that anyone could return to."), None))
            break
    blocks = {b.id: b for b in survey.blocks}
    for bid in after:
        b = blocks.get(bid)
        if b is None:
            continue
        if loops.active(b):
            problems.append(("error", de_en("Block „{title}“: Eine Schleife ist im getrennten Abschnitt nicht möglich.",
                                            "Block “{title}”: a loop is not possible in the separated section.", title=b.title()), bid))
        for i in b.items:
            if not i.kind.allowed_in_separated:
                problems.append(("error", de_en("Block „{title}“: {kind} gehört nicht in den getrennten Abschnitt.",
                                                "Block “{title}”: {kind} does not belong in the separated section.",
                                                title=b.title(), kind=i.kind.label), bid))
        if (b.settings or {}).get("randomize_items") or b.randomize_items:
            pass  # order randomization is harmless (not stored)
    return problems


def figures(survey):
    """Number of between-subject conditions (randomizer combinations) and branch points."""
    conditions, branches = 1, 0
    for node, _, _ in walk(tree(survey)):
        if node["t"] == "rand":
            n, k = len(node.get("children", [])), node.get("k", 1)
            if n and k <= n:
                conditions *= comb(n, k) * (factorial(k) if node.get("shuffle") else 1)
        elif node["t"] == "branch":
            branches += 1
    return {"conditions": conditions, "branches": branches}
