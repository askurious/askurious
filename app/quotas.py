"""Quotas: cells with a target, calculated at a "Quotenberechnung" in the flow.

A quota (models.Quota) is a list of cells; a cell (models.QuotaCell) has conditions that must all hold (the
conditions of the flow: a question or URL parameter, an operator, a value), a target and counters. A cross quota
is simply a quota whose cells have several conditions each.

The calculation is a node in the flow ({"t": "quota", "quotas": [quota ids]}) and nothing but a trigger: when a
participation passes it, the person's cell is found and a place is taken there, for each of its quotas on its
own. Where the node stands decides when that happens. The decision is made once and kept with the participation
(Response.quota); passing the node again changes nothing, and there is no way back across it
(runtime.previous_index). The node never ends a participation: what happens when a quota is full is said by an
end in the flow: an end has triggers (flow.cases), among them "Quote voll" (one or all of some quotas had no
room for the person), "Teilquote voll" (the person's cell is one of some cells, and full) and "keine Teilquote
passt". The first trigger that holds ends the participation (`end_case`); everybody else passes the end. Before
the calculation of a quota, a trigger looks at the state as it is and takes nothing.

Surveys started before this (the node was a "Quotenprüfung" that ended a full quota itself, with a text and a
link of its own: it has the key "full") keep their flow, since changing it would restart the participations
under way; `pass_check` treats such a node as before. A draft is brought to the new form (`upgrade`).

Counting under load: finding out whether there is room and taking the place is one UPDATE statement with the
limit in its WHERE clause (`_take`). The database decides, not Python: two people who arrive at the same moment
never share the last place, on SQLite as on any other database. Nothing here counts participations.

  hard  let in while  done + running < target                                   not above the target (*)
  soft  let in while  done < target  and  done + running < target + tolerance   ends between target and
                                                                                target + tolerance (*)

(*) as long as reservations hold: someone whose reservation ran out (below) and whose place was taken meanwhile
may still finish and is counted above the target (`over`); the state of the quotas shows them as "über Soll".

Only completed participations count: drop-outs and people filtered out later never fill a cell, they only hold
a place for a while. (Quota.count_at "check", counted at once and for good, could be chosen for surveys started
before the calculation became a trigger; they keep it, nothing new gets it.)

A reserved place (running) becomes a completed one (done) when the participation is completed, and is given
back when it ends earlier (declined, a later end in the flow) or when no page has been sent for the quota's
`hold_minutes` (release_expired; not while the person is in an external section and may still come back).
A reservation no page followed yet (the person passed the calculation and stopped) is given back sooner, after
QUOTA_FRESH_HOLD_MINUTES (5 by default): scripts that pass the screening and leave can no longer hold the places
of a cell for an hour; who goes on sends a page and from then on keeps the place for hold_minutes, every page
starting the time anew (audit 2026-10-18, BIZ-06). The stamp "t" of the entry says when the place was taken.
Who comes back after that gets the place again if it is still free; otherwise the participation may be finished
and counts as completed above the target (`over`). Test runs, previews and dummy data never touch a counter;
dummy data count among themselves (dummy_tally).

With the survey setting quota_drop_marked (page "Quoten"), a participation that quality assurance marks as it
completes (export.marked_at_end) fills no place: its places are given back as for a drop-out, also a place
counted at the check, and the cell counts it under `freed`. Off (the default), marked participations fill their
place; the report only leaves them out of what is reached (marked_done). The judgement at the completion is
final for the place, so it only rests on what is settled then (`_marked_now`): the hidden field, failed checks
and the other conspicuous things of the participation itself; the speeder judgement, which compares with the
median of the others, only from SPEEDER_MIN real completions on (before that the median moves too much and the
first completions could never be speeders).

The counters can always be rebuilt from what the participations hold (recount).
"""
import math
import re

from sqlalchemy.orm.util import identity_key

from app.conditions import ByLanguage
from app.extensions import db
from app.lang import de_en, english
from app.models import Quota, QuotaCell, Response

CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}\Z")
MODES = {Quota.HARD: "hart", Quota.SOFT: "weich"}
PICK = {"first": "die erste in der Reihenfolge", "least": "die am wenigsten gefüllte"}
MODES_EN = {Quota.HARD: "hard", Quota.SOFT: "soft"}
PICK_EN = {"first": "the first in the order", "least": "the least filled"}


def modes():
    """MODES as the quota editor shows them, in the language of the page."""
    return MODES_EN if english() else MODES


def picks():
    """PICK as the quota editor shows them, in the language of the page."""
    return PICK_EN if english() else PICK


RULES = {"any": "mindestens eine der Quoten voll ist", "all": "alle Quoten voll sind"}  # of an end "Quote voll"
RULES_EN = {"any": "at least one of the quotas is full", "all": "all quotas are full"}
# read in the language of the page wherever it is shown (rules() for a plain dict); the keys are the same
RULES = ByLanguage(RULES, RULES_EN)


def rules():
    """RULES as the flow page shows them, in the language of the page."""
    return RULES_EN if english() else dict(RULES)


# what the calculation decided for one quota; the number is the value of the export column quota_<code>_res
# ("other" only in surveys started with a "Quotenprüfung", see the head)
RES = {"none": (0, "keine Teilquote"), "in": (1, "Platz"), "full": (2, "voll"), "over": (3, "über Soll abgeschlossen"),
       "other": (4, "kein Platz, weil eine andere Quote derselben Prüfung voll war")}
MAX_QUOTAS, MAX_CELLS, MAX_CONDS = 20, 400, 8
DEFAULT_HOLD = 15  # minutes, for new quotas (60 before the audit 2026-10-18, BIZ-06; stored ones keep theirs)
FRESH_HOLD = 5  # minutes: a reservation no page followed yet (QUOTA_FRESH_HOLD_MINUTES)
COUNTER_NAMES = {"running": "laufend", "done": "abgeschlossen", "refused": "abgewiesen", "over": "über Soll",
                 "freed": "markiert, Platz freigegeben"}
HELD = '%"held": true%'  # a participation with a reserved place, as its stored decisions read
SPEEDER_MIN = 30  # real completions from which a speeder gives its place back (quota_drop_marked, _marked_now)


# --- definitions ----------------------------------------------------------------------------------------


def of(survey):
    return list(survey.quotas)


def get(survey, quota_id):
    return next((q for q in survey.quotas if q.id == quota_id), None)


def nodes(survey):
    """The quota calculations in the flow, in flow order."""
    from app import flow

    return [n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "quota"]


def node_quotas(survey, node):
    """The quotas a node names (a calculation, or an end "Quote voll"), in the survey's order."""
    wanted = set(node.get("quotas") or [])
    return [q for q in survey.quotas if q.id in wanted]


def node_of(survey, quota):
    """The calculation node of a quota, or None while it is not calculated anywhere."""
    return next((n for n in nodes(survey) if quota.id in (n.get("quotas") or [])), None)


def is_legacy(node):
    """A "Quotenprüfung" of a survey started before the calculation became a trigger (see the head)."""
    return node["t"] == "quota" and "full" in node


def end_quotas(node):
    """Ids of the quotas whose being full an end reacts to (its triggers "Quote voll" and "Teilquote voll")."""
    from app import flow

    return [q for case in flow.cases(node) if case.get("when") in ("quotafull", "cellfull") for q in flow.case_quotas(case)]


def full_ends(survey):
    """The ends that react to a full quota, in flow order."""
    from app import flow

    return [n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "end" and end_quotas(n)]


def ends_of(survey, quota):
    return [n for n in full_ends(survey) if quota.id in end_quotas(n)]


def _digits(raw):
    """A whole number a form field or a stored reference may hold: ASCII digits only. str.isdigit alone also
    takes "²" or "٣", which int() refuses or reads as something nobody typed; and int() refuses very long ones."""
    return raw.isascii() and raw.isdigit() and len(raw) < 12


def add(survey):
    """A new, empty quota with a free code (q1, q2, ...)."""
    taken = {q.code for q in survey.quotas}
    n = next(k for k in range(1, MAX_QUOTAS + 2) if f"q{k}" not in taken)
    quota = Quota(code=f"q{n}", name=f"Quote {n}", position=len(survey.quotas), mode=Quota.HARD,
                  tolerance=10, tolerance_pct=True, no_cell="continue", hold_minutes=DEFAULT_HOLD, count_at="complete",
                  pick="first")
    survey.quotas.append(quota)
    return quota


def tolerance_of(quota, cell):
    """How far above the target a soft quota may end, in places (0 for a hard one)."""
    if quota.mode != Quota.SOFT or not quota.tolerance:
        return 0
    if quota.tolerance_pct:
        return max(1, math.ceil((cell.target or 0) * quota.tolerance / 100))
    return quota.tolerance


def describe(survey, lang=None):
    """What a condition of a Teilquote can be put on (conditions.describe): questions that give something to
    tick or a range, and the URL parameters."""
    from app import conditions

    return conditions.describe(survey, lang)


def _range_cond(ref, lo, hi):
    from app import conditions

    return conditions.range_cond(ref, lo, hi)


def _cond_from_form(form, prefix, described):
    """One condition of the quota form (conditions.from_form): the question and what was ticked or entered."""
    from app import conditions

    return conditions.from_form(form, prefix, described)


def cond_view(cond, described):
    """A stored condition as the quota form shows it (conditions.view, without a choice of what is asked)."""
    from app import conditions

    return conditions.view(cond, described, modes=False)


def tick_conds(survey):
    """Ordered comparisons on answers without an order in the Teilquoten ("ab divers" of an older file) as the
    answers they take in (conditions.as_codes; UI audit 2026-10-12, U18). For reading a survey file in."""
    from app import conditions

    described = describe(survey)
    for quota in survey.quotas:
        for cell in quota.cells:
            conds = [conditions.as_codes(c, described.get(str(c.get("item")))) if isinstance(c, dict) else c
                     for c in (cell.conds or [])]
            if conds != (cell.conds or []):
                cell.conds = conds


def update_from_form(survey, quota, form, structural=True):
    """Save the form of one quota. structural=False (fieldwork): only what steers the field (targets, mode,
    tolerance, closed cells); names, codes and conditions stay as they are. Returns an error message or None."""
    def number(key, default=0, top=1_000_000):
        raw = form.get(key, "").strip()
        return min(int(raw), top) if _digits(raw) else default

    mode = form.get("mode") if form.get("mode") in MODES else quota.mode
    tolerance = number("tolerance", quota.tolerance or 0, 100_000)
    pct = form.get("tolerance_unit", "pct" if quota.tolerance_pct else "n") == "pct"
    if structural:
        described = describe(survey)
        code = form.get("code", "").strip() or quota.code
        if not CODE_RE.match(code):
            return de_en("Code der Quote: Buchstabe am Anfang, dann Buchstaben, Ziffern oder _ (höchstens 40 Zeichen).",
                         "Code of the quota: a letter first, then letters, digits or _ (at most 40 characters).")
        if any(q.code.lower() == code.lower() for q in survey.quotas if q is not quota):
            return de_en("Der Code „{code}“ ist schon an eine andere Quote vergeben.",
                         "The code “{code}” is already taken by another quota.", code=code)
        rows, prefixes = [], []
        for n in range(number("cells", 0, MAX_CELLS + 5)):
            p = f"c{n}"
            if form.get(f"{p}_delete"):
                continue
            conds = [c for c in (_cond_from_form(form, f"{p}_k{m}", described) for m in range(MAX_CONDS)) if c]
            name = form.get(f"{p}_name", "").strip()[:200]
            if not name and conds:  # no name given: what the conditions say, e.g. "männlich / 18 bis 29"
                name = " / ".join(cond_text(survey, c, short=True) for c in conds)[:200]
            ccode = form.get(f"{p}_code", "").strip()
            cid = form.get(f"{p}_id", "")
            if not (cid or name or ccode or conds or form.get(f"{p}_target", "").strip()):
                continue  # the empty row for a new cell, left empty
            rows.append((int(cid) if _digits(cid) else None, name, ccode, conds, number(f"{p}_target")))
            prefixes.append(p)
        if len(rows) > MAX_CELLS:
            return de_en("Höchstens {n} Teilquoten je Quote.", "At most {n} sub-quotas per quota.", n=MAX_CELLS)
        taken = set()
        for k, (cid, name, ccode, conds, target) in enumerate(rows):
            # no code given: one made from the name (a code is only needed for the export)
            ccode = ccode or _slug(name or "z", taken | {r[2].lower() for r in rows if r[2]}, add=False)
            if not CODE_RE.match(ccode):
                return de_en("Code der Teilquote „{name}“: Buchstabe am Anfang, dann Buchstaben, Ziffern oder _.",
                             "Code of the sub-quota “{name}”: a letter first, then letters, digits or _.", name=name or ccode)
            if ccode.lower() in taken:
                return de_en("Der Teilquoten-Code „{code}“ kommt in dieser Quote doppelt vor.",
                             "The sub-quota code “{code}” appears twice in this quota.", code=ccode)
            taken.add(ccode.lower())
            rows[k] = (cid, name, ccode, conds, target)
        existing = {c.id: c for c in quota.cells}
        _rename_cell_links(survey, quota, {existing[cid].code: ccode for cid, _, ccode, _, _ in rows if cid in existing})
        kept = []
        for position, (cid, name, ccode, conds, target) in enumerate(rows):
            cell = existing.get(cid) or QuotaCell(running=0, done=0, refused=0, over=0, closed=False)
            cell.name, cell.code, cell.conds, cell.target = name or ccode, ccode, conds, target
            cell.position = position
            kept.append(cell)
        quota.cells = kept  # cells left out are deleted (delete-orphan)
        # for a page that saves by itself: the row of the form each cell came from (a new cell's number goes back
        # into its row, so the next save changes it instead of making another one)
        quota.form_rows = dict(zip(prefixes, kept))
        quota.code, quota.name = code, form.get("name", "").strip()[:200] or code
        quota.pick = form.get("pick") if form.get("pick") in PICK else (quota.pick or "first")
    else:
        for cell in quota.cells:
            raw = form.get(f"target_{cell.id}", "").strip()
            if _digits(raw):
                cell.target = min(int(raw), 1_000_000)
            cell.closed = bool(form.get(f"closed_{cell.id}"))
    quota.mode, quota.tolerance, quota.tolerance_pct = mode, tolerance, pct
    quota.hold_minutes = max(1, number("hold_minutes", quota.hold_minutes or DEFAULT_HOLD, 7 * 24 * 60))
    return None


def _rename_cell_links(survey, quota, codes):
    """The ends "Quote voll" name the cells of their links by code: a cell that gets another code keeps its link,
    the link of a cell that is gone goes with it. codes: {old code: new code} of the cells that stay."""
    import copy

    from app import flow

    if not any((n.get("links") or {}).get(str(quota.id), {}).get("cells")
               or any(case.get("quota") == quota.id for case in n.get("cases") or []) for n in full_ends(survey)):
        return
    root = copy.deepcopy(flow.tree(survey))
    for node, _, _ in flow.walk(root):
        entry = (node.get("links") or {}).get(str(quota.id)) if node["t"] == "end" else None
        if entry and entry.get("cells"):
            entry["cells"] = {codes[old]: url for old, url in entry["cells"].items() if old in codes}
        for case in node.get("cases") or [] if node["t"] == "end" else []:  # a trigger "Teilquote voll"
            if case.get("quota") == quota.id:
                case["cells"] = [codes[old] for old in case.get("cells") or [] if old in codes]
    survey.flow = root


def remove(survey, quota):
    """Delete a quota with its cells; calculations and ends in the flow no longer name it."""
    import copy

    from app import flow

    root = copy.deepcopy(flow.tree(survey))
    for node, _, _ in flow.walk(root):
        if node["t"] in ("quota", "end") and quota.id in (node.get("quotas") or []):
            node["quotas"] = [q for q in node["quotas"] if q != quota.id]
            (node.get("links") or {}).pop(str(quota.id), None)
        for case in node.get("cases") or [] if node["t"] == "end" else []:  # the triggers of an end
            if quota.id in (case.get("quotas") or []):
                case["quotas"] = [q for q in case["quotas"] if q != quota.id]
            if case.get("quota") == quota.id:
                case.update(quota=None, cells=[])
    survey.flow = root
    survey.quotas.remove(quota)


def upgrade_tree(root, quotas):
    """Bring a flow with "Quotenprüfung" nodes to the new form, in place: the node keeps its quotas and nothing
    else; what it did besides becomes elements of their own right behind it: for every quota that ended people
    without a cell (quotas: {quota id: its old `no_cell`}) a condition with a screen-out, and, if it ended a
    full quota, an end "Quote voll" with its text and link. An end "Quote voll" in a condition on a quota gets
    that quota. Returns True if something changed."""
    from app import flow

    changed = False

    def rec(node):
        nonlocal changed
        out = []
        for child in node.get("children", []):
            if child["t"] in flow.GROUPS:
                rec(child)
            out.append(child)
            if is_legacy(child):
                changed = True
                full, text = child.pop("full"), child.pop("text", None)
                link, mode = child.pop("link", ""), child.pop("link_mode", "now")
                by = {"by": child["by"]} if child.get("by") else {}
                named = [q for q in child.get("quotas") or []]
                for qid in named:
                    if (quotas.get(qid) or ("", ""))[0] == "end":
                        out.append({"t": "branch", "id": flow._nid("b"), "name": f"{quotas[qid][1]}: keine Teilquote"[:100],
                                    "cond": {"item": f"q:{qid}", "op": "eq", "value": "none"}, **by,
                                    "children": [{"t": "end", "id": flow._nid("e"), "text": {}, "kind": "screenout", **by}]})
                if full == "end" and named:
                    out.append({"t": "end", "id": flow._nid("e"), "kind": "quotafull", "quotas": named, "rule": "any",
                                "text": text or {}, "link": link or "", "link_mode": mode or "now", **by})
            elif child["t"] == "end" and flow.end_kind(child) == "quotafull" and "quotas" not in child:
                ref = str((node.get("cond") or {}).get("item") or "") if node["t"] == "branch" else ""
                if ref.startswith("q:") and _digits(ref[2:]) and int(ref[2:]) in quotas:
                    child.update(quotas=[int(ref[2:])], rule="any")
                    changed = True
        node["children"] = out

    rec(root)
    return changed


def add_to_flow(survey, quota):
    """A new quota with what makes it work: a calculation and behind it an end "Quote voll" with this quota, at
    the end of the regular survey (before the separated section), where every question has been answered.
    Ralf, 2026-10-03: creating a quota does this unless it is unticked."""
    from app import flow

    root = flow.normalize(survey)
    nodes = [{"t": "quota", "id": flow._nid("q"), "quotas": [quota.id]},
             {"t": "end", "id": flow._nid("e"), "cases": [{"id": flow._nid("c"), "when": "quotafull", "quotas": [quota.id],
                                                         "rule": "any", "text": {}, "link": "", "link_mode": "now"}]}]
    split_at = next((n for n, c in enumerate(root["children"]) if c["t"] == "split"), len(root["children"]))
    root["children"][split_at:split_at] = nodes
    survey.flow = root
    flow.normalize(survey)


def upgrade(survey):
    """A draft with a "Quotenprüfung" gets the new form (upgrade_tree); its quotas no longer end anybody
    themselves and count at completion. Surveys in the field stay as they are. True if something changed."""
    import copy

    from app import flow

    if not survey.is_editable or not any(
            is_legacy(n) or (n["t"] == "end" and flow.end_kind(n) == "quotafull" and "quotas" not in n)
            for n, _, _ in flow.walk(flow.tree(survey))):
        return False
    root = copy.deepcopy(flow.tree(survey))
    if not upgrade_tree(root, {q.id: (q.no_cell, q.name) for q in survey.quotas}):
        return False
    for quota in survey.quotas:
        quota.no_cell, quota.count_at = "continue", "complete"
    survey.flow = root
    return True


def upgrade_drafts():
    """Every draft at the start of the server (create_app). Returns how many were changed. Never raises: a draft
    whose flow cannot be read, or a database another server process is writing to at this moment, must not keep
    the server from starting. Each draft is saved on its own; one that fails stays as it is and is tried again
    at the next start."""
    from flask import current_app

    from app.models import Survey

    changed = 0
    try:
        ids = list(db.session.scalars(db.select(Survey.id).filter(Survey.status == Survey.DRAFT, Survey.quotas.any())))
    except Exception:
        db.session.rollback()
        current_app.logger.exception("quotas.upgrade_drafts: the drafts could not be read")
        return 0
    for survey_id in ids:
        try:
            survey = db.session.get(Survey, survey_id)
            if survey is not None and upgrade(survey):
                db.session.commit()
                changed += 1
        except Exception:
            db.session.rollback()
            current_app.logger.exception("quotas.upgrade_drafts: survey %s left as it is", survey_id)
    return changed


# --- texts ----------------------------------------------------------------------------------------------


def cond_text(survey, cond, lang=None, items=None, short=False, shown=False):
    """One condition as people read it (conditions.text): "gender: männlich", "age: 18 bis 29". shown: the words
    around the answers in the language of the page (a page that only shows it); else German (names, files)."""
    from app import conditions

    return conditions.text(survey, cond, lang, items, short, shown)


def cell_text(survey, cell, lang=None, items=None, shown=False):
    """The conditions of a cell; shown: the words around the answers in the language of the page (cond_text)."""
    return ("; ".join(cond_text(survey, c, lang, items, shown=shown) for c in cell.conds or [])
            or (de_en("ohne Bedingung", "no condition") if shown else "ohne Bedingung"))


# --- which cell -----------------------------------------------------------------------------------------


def match(quota, answers):
    """The cell of these answers: the first one, in the quota's order, whose conditions all hold."""
    from app import flow

    for cell in quota.cells:
        if cell.conds and all(flow.holds(c, answers, quota.survey) for c in cell.conds):
            return cell
    return None


def fill(quota, cell, tally=None):
    """How full a cell is, as a share of its target: what counts against the target (completed, and for a quota
    that counts at completion also the reserved places). tally: the dummies' own count instead."""
    if not cell.target:
        return float("inf")
    if tally is not None:
        return tally.get(cell.id, 0) / cell.target
    return (cell.done + (0 if quota.count_at == "check" else cell.running)) / cell.target


def candidates(quota, answers, tally=None):
    """The cells a person may get a place in, in the order they are tried: the first one whose conditions hold;
    with pick "least" all that hold, the least-filled first (equally filled ones in the quota's order). The
    counters are read as loaded; taking the place decides for real, and the next candidate is tried if the
    first has no room any more."""
    from app import flow

    fitting = [cell for cell in quota.cells if cell.conds and all(flow.holds(c, answers, quota.survey) for c in cell.conds)]
    if quota.pick != "least":
        return fitting[:1]
    return sorted(fitting, key=lambda cell: fill(quota, cell, tally))


def is_full(quota, cell):
    """Nobody gets into the cell right now (read from the counters as loaded; `_take` decides for real)."""
    if cell.closed:
        return True
    if quota.count_at == "check":
        return cell.done >= cell.target
    if quota.mode == Quota.SOFT:
        return cell.done >= cell.target or cell.done + cell.running >= cell.target + tolerance_of(quota, cell)
    return cell.done + cell.running >= cell.target


# --- the counters ---------------------------------------------------------------------------------------


def _bump(cell_id, *conditions, **changes):
    """One UPDATE on the counters of a cell; True if it applied (the conditions held at that moment)."""
    values = {name: getattr(QuotaCell, name) + step for name, step in changes.items()}
    result = db.session.execute(
        db.update(QuotaCell).where(QuotaCell.id == cell_id, *conditions).values(**values)
        .execution_options(synchronize_session=False))
    cell = db.session.identity_map.get(identity_key(QuotaCell, cell_id))
    if cell is not None:  # loaded in this request: its counters are read again when needed
        db.session.expire(cell, ["running", "done", "refused", "over", "freed"])
    return result.rowcount == 1


def _take(quota, cell):
    """Reserve a place if there is room: check and reservation are one statement. A quota that counts at the
    check counts the participation at once instead (done), for good."""
    room = [QuotaCell.closed.is_(False)]
    if quota.count_at == "check":
        return _bump(cell.id, *room, QuotaCell.done < QuotaCell.target, done=1)
    if quota.mode == Quota.SOFT:
        room += [QuotaCell.done < QuotaCell.target,
                 QuotaCell.done + QuotaCell.running < QuotaCell.target + tolerance_of(quota, cell)]
    else:
        room.append(QuotaCell.done + QuotaCell.running < QuotaCell.target)
    return _bump(cell.id, *room, running=1)


def counted(response):
    """Real, stored participations move the counters; previews, test runs and dummies never do."""
    return (getattr(response, "source", None) == "real" and not getattr(response, "is_preview", True)
            and getattr(response, "id", None) is not None)


def decisions(response):
    return dict(getattr(response, "quota", None) or {})


def _decided(response, quotas):
    """The stored decisions for these quotas, in their order: None where one is missing (or names a cell that
    is gone)."""
    stored = decisions(response)
    out = []
    for quota in quotas:
        entry = stored.get(str(quota.id))
        gone = bool(entry) and entry.get("cell") is not None and entry["cell"] not in {c.id for c in quota.cells}
        out.append(entry if entry and not gone else None)
    return out


def pass_check(survey, response, node, answers, simulate_full=False, tally=None):
    """A participation arrives at a quota calculation: every quota of the node finds the person's cell and
    takes a place there. Returns "in" or "full" (a quota had no room); None if the node names no quota. The
    decision is made once: later calls return what was decided then. tally: the counts dummy data keep among
    themselves. The node of a survey started with a "Quotenprüfung" (is_legacy) decides all its quotas together
    and may return "none": no cell, and that quota ends the participation then (runtime.step ends it)."""
    quotas = node_quotas(survey, node)
    if not quotas:
        return None
    legacy = is_legacy(node)
    entries = _decided(response, quotas)
    if None in entries:
        if legacy:  # all its quotas or none: decided together
            entries = _decide(response, quotas, answers, simulate_full, tally)
        else:
            # only the quotas without a decision: one added to the calculation after the person had passed it
            # (the survey was opened for editing in between). The others keep their decision and their place;
            # calculating them again would take a second place for the same person.
            new = [q for q, e in zip(quotas, entries) if e is None]
            fresh = dict(zip([q.id for q in new], _calculate(response, new, answers, simulate_full, tally)))
            entries = [fresh[q.id] if e is None else e for q, e in zip(quotas, entries)]
        if hasattr(response, "quota"):
            response.quota = {**decisions(response), **{str(q.id): e for q, e in zip(quotas, entries)}}
    if any(e["res"] in ("full", "other") for e in entries):
        return "full"
    if legacy and any(e["res"] == "none" and q.no_cell == "end" for q, e in zip(quotas, entries)):
        return "none"
    return "in"


def _place(response, quota, cells, simulate_full, tally):
    """The cell among `cells` (candidates) the person gets a place in, or None if none has room."""
    for cell in cells:  # one cell, or with "least fill" the fitting ones from the least-filled on
        if counted(response):
            ok = _take(quota, cell)
        elif getattr(response, "source", None) == "real":  # not stored yet (the first page): look, take nothing
            ok = not is_full(quota, cell)
        elif tally is not None:  # dummy data: a count of their own, as strict as a hard quota
            ok = not cell.closed and tally.get(cell.id, 0) < cell.target
            if ok:
                tally[cell.id] = tally.get(cell.id, 0) + 1
        else:  # preview, test run: the counters are neither read nor changed
            ok = not simulate_full
        if ok:
            return cell
    return None


def _calculate(response, quotas, answers, simulate_full, tally=None):
    """Every quota on its own: the person's cell, and a place in it if there is room."""
    real = counted(response)
    entries = []
    for quota in quotas:
        cells = candidates(quota, answers, tally)
        if not cells:
            entries.append({"cell": None, "res": "none"})
            continue
        got = _place(response, quota, cells, simulate_full, tally)
        if got is None:
            if real:
                _bump(cells[0].id, refused=1)
            entries.append({"cell": cells[0].id, "res": "full"})
        elif quota.count_at == "check":  # only quotas of surveys started before: counted now and for good
            entries.append({"cell": got.id, "res": "in", "held": False, "done": True, "at": "check"})
        else:
            entries.append(_held_entry(got.id, real))
    return entries


def _held_entry(cell_id, real):
    """A place in the cell: reserved for a stored real participation, with the moment it was taken (BIZ-06)."""
    entry = {"cell": cell_id, "res": "in", "held": real}
    if real:
        entry["t"] = _now_stamp()
    return entry


def _now_stamp():
    from app.models import utcnow

    return utcnow().isoformat()


def fresh_hold():
    """Minutes a reservation no page followed yet is kept (QUOTA_FRESH_HOLD_MINUTES, BIZ-06)."""
    from flask import current_app, has_app_context

    value = current_app.config.get("QUOTA_FRESH_HOLD_MINUTES", FRESH_HOLD) if has_app_context() else FRESH_HOLD
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return FRESH_HOLD


def _limit_of(entry, minutes, since):
    """The minutes this held entry keeps its place: hold_minutes, or the shorter fresh hold while no page was sent
    after the place was taken (its stamp is not older than the last activity)."""
    stamp = entry.get("t")
    if not stamp or since is None:
        return minutes
    from datetime import datetime

    try:
        taken = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return minutes
    if since.tzinfo is None and taken.tzinfo is not None:
        taken = taken.replace(tzinfo=None)
    return min(minutes, fresh_hold()) if since <= taken else minutes


def _look(response, quota, answers, simulate_full=False, tally=None):
    """What a quota that is not calculated yet would say for this person, taking nothing: {"cell": None, "res":
    "none"} if no cell fits, {"cell": id, "res": "full"} if every cell the person could get is full, else None
    (there is room; the calculation decides)."""
    cells = candidates(quota, answers, tally)
    if not cells:
        return {"cell": None, "res": "none"}
    if getattr(response, "source", None) == "real":
        full = all(is_full(quota, cell) for cell in cells)
    elif tally is not None:
        full = all(cell.closed or tally.get(cell.id, 0) >= cell.target for cell in cells)
    else:
        full = simulate_full
    return {"cell": cells[0].id, "res": "full"} if full else None


def end_case(survey, response, node, answers, simulate_full=False, tally=None):
    """The trigger of an end that ends this participation (flow.cases: the first that holds), or None: the person
    passes the end. A trigger on a quota that is calculated asks what was decided there; before the calculation
    it looks at the state as it is and takes nothing. What it saw then is kept with the participation it ends:
    the end page and the completion link are those of that trigger, and the cell counts the refusal."""
    from app import flow

    stored = decisions(response)
    wanted = {q for case in flow.cases(node) for q in flow.case_quotas(case)}
    seen = {}
    for quota in survey.quotas:
        if quota.id in wanted and str(quota.id) not in stored:
            entry = _look(response, quota, answers, simulate_full, tally)
            if entry:
                seen[str(quota.id)] = entry
    codes = {c.id: c.code for q in survey.quotas for c in q.cells}
    looked = {key: value for qid, entry in seen.items()
              for key, value in ((f"q:{qid}", entry["res"]), (f"qc:{qid}", codes.get(entry["cell"], "")))}
    case = flow.case_for(node, {**view(survey, response), **looked})
    if case is None:
        return None
    keep = {qid: entry for qid, entry in seen.items() if int(qid) in flow.case_quotas(case)}
    for entry in keep.values():
        if entry["res"] == "full" and counted(response):
            _bump(entry["cell"], refused=1)
    if keep and hasattr(response, "quota"):
        response.quota = {**stored, **keep}
    return case


def _decide(response, quotas, answers, simulate_full, tally=None):
    """A "Quotenprüfung" (is_legacy): all its quotas or none."""
    options = [candidates(q, answers, tally) for q in quotas]
    if any(not cells and q.no_cell == "end" for q, cells in zip(quotas, options)):
        # the participation ends here as a screen-out: nobody takes a place
        return [{"cell": cells[0].id if cells else None, "res": "other" if cells else "none"} for cells in options]
    real = counted(response)
    failed = None  # the position of the quota that had no room
    taken = []
    chosen = []  # per quota: the cell the person got, or would have got, or None
    for n, (quota, cells) in enumerate(zip(quotas, options)):
        if failed is not None or not cells:
            chosen.append(cells[0] if cells else None)
            continue
        got = _place(response, quota, cells, simulate_full, tally)
        if got is None:
            failed = n
            chosen.append(cells[0])
            if real:
                _bump(cells[0].id, refused=1)
            continue
        taken.append((quota, got))
        chosen.append(got)
    if failed is not None:
        for quota, cell in taken:  # all or nothing: places already taken at this check go back
            if real:
                _give_back(quota, cell.id)
            elif tally is not None:
                tally[cell.id] -= 1
        return [{"cell": c.id if c else None, "res": "none" if c is None else "full" if n == failed else "other"}
                for n, c in enumerate(chosen)]
    entries = []
    for quota, cell in zip(quotas, chosen):
        if cell is None:
            entries.append({"cell": None, "res": "none"})
        elif quota.count_at == "check":  # counted now and for good: nothing is held, nothing changes at the end
            entries.append({"cell": cell.id, "res": "in", "held": False, "done": True, "at": "check"})
        else:
            entries.append(_held_entry(cell.id, real))
    return entries



def _give_back(quota, cell_id):
    if quota.count_at == "check":
        _bump(cell_id, QuotaCell.done > 0, done=-1)
    else:
        _bump(cell_id, QuotaCell.running > 0, running=-1)


def _by_cell(stored):
    """The stored decisions in the order of their cells: every request that changes several cells locks them in
    the same order (release_expired sorts too), so two of them never wait for each other (code review 2026-10-07,
    T7)."""
    return sorted(stored.items(), key=lambda kv: (kv[1].get("cell") is None, kv[1].get("cell") or 0, kv[0]))


def settle(response, completed, survey=None):
    """The participation has ended: reserved places become completed ones, or are given back. survey: the
    participation's survey, when it completes in the flow (runtime.step); with its setting quota_drop_marked a
    completion that quality assurance marks gives its places back instead (_free)."""
    stored = decisions(response)
    if not stored:
        return
    real, changed = counted(response), False
    from app import quality

    if completed and real and quality.bot_signal(response.paradata) and any(
            e.get("res") == "in" and e.get("cell") is not None for e in stored.values()):
        _free(response, stored)  # a script's completion takes no place, whatever the setting (audit 2026-10-22, D-06)
        return
    if completed and real and getattr(survey, "quota_drop_marked", False) and survey.quotas             and any(e.get("res") == "in" and e.get("cell") is not None for e in stored.values()):
        if _marked_now(survey, response):
            _free(response, stored)
            return
    for key, entry in _by_cell(stored):
        if entry.get("res") != "in" or entry.get("cell") is None or entry.get("done"):
            continue
        entry = dict(entry)
        if entry.get("held"):
            if real:
                if not completed:
                    _bump(entry["cell"], QuotaCell.running > 0, running=-1)
                elif not _bump(entry["cell"], QuotaCell.running > 0, running=-1, done=1):
                    _bump(entry["cell"], done=1)
            entry["held"] = False
            entry.pop("t", None)  # the stamp of the reservation goes with it
            entry["done"] = bool(completed)
        elif completed and real:
            # the reservation had run out and the place was gone: completed above the target
            _bump(entry["cell"], done=1, over=1)
            entry["done"], entry["res"] = True, "over"
        elif completed:
            entry["done"] = True
        else:
            continue
        stored[key], changed = entry, True
    if changed:
        response.quota = stored


def _marked_now(survey, response):
    """Quality assurance marks this completion for good (settle, setting quota_drop_marked): only by what is settled
    as it completes (audit 2026-10-05, T-28). The speeder judgement compares with the median of the real completed
    participations so far and so moves with every later one; it counts only from SPEEDER_MIN of them on (this one
    included). Before that only the conspicuous things of the participation itself count (export.marked_at_end
    without the speeder)."""
    from app import export, quality

    completed = db.session.scalar(db.select(db.func.count(Response.id)).filter(
        Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False),
        Response.status == Response.COMPLETED))
    if completed >= SPEEDER_MIN:
        return export.marked_at_end(survey, response)
    ctx = quality.context(survey, export._items(survey))
    if not quality.can_mark(ctx):
        return False
    return quality.marked({"speeder": 0, "qc_flags": quality.values(survey, response, ctx)["qc_flags"]}, ctx["rules"]["min"])


def _free(response, stored):
    """A completion quality assurance marks (settle): every place it has is given back, a reserved one as for a
    drop-out and one counted at the check (count_at "check") as well; the cell counts it under `freed`. The
    entries keep their cell and say "marked", so recount and the report read the same."""
    for key, entry in _by_cell(stored):
        if entry.get("res") != "in" or entry.get("cell") is None:
            continue
        if entry.get("held"):
            _bump(entry["cell"], QuotaCell.running > 0, running=-1, freed=1)
        elif entry.get("done") and entry.get("at") == "check":
            _bump(entry["cell"], QuotaCell.done > 0, done=-1, freed=1)
        else:  # the reservation had run out: there is nothing to give back
            _bump(entry["cell"], freed=1)
        stored[key] = {**{k: v for k, v in entry.items() if k != "t"}, "held": False, "done": False, "marked": True}
    response.quota = stored


def release(response):
    """The participation is removed (declined): its reserved places are free again, and where it was counted at
    the check it no longer counts (nothing of it is left), nor where it was turned away as "full" (audit 2026-10-12,
    T-10: "abgewiesen" stayed one above what recount finds)."""
    if counted(response):
        for _key, entry in _by_cell(decisions(response)):
            if entry.get("cell") is None:
                continue
            if entry.get("at") == "check" and entry.get("done"):
                _bump(entry["cell"], QuotaCell.done > 0, done=-1)
            if entry.get("res") == "full":
                _bump(entry["cell"], QuotaCell.refused > 0, refused=-1)
    settle(response, completed=False)


JUMP_MINUTES = 10  # a cell whose running places came within this time ...
JUMP_MIN = 10      # ... at least this many, and at least half of its open places: a note on the tabs (jumps)


def jumps(survey, now=None):
    """The cells whose running participations rose by leaps (S20 of the audit 2026-10-06): within JUMP_MINUTES at
    least JUMP_MIN real participations took a place in them, and these hold at least half of what the cell still
    needs. A script that answers into one cell sends real people to "Quote voll" until the places are given back
    after the hold time; the researchers see it on "Datenerhebung" and "Quoten" and decide (nothing happens by
    itself). The limit per sender and survey (config SURVEY_SENDER_LIMIT) slows such a script down.
    [{"quota", "cell", "n", "open", "minutes"}]."""
    from datetime import timedelta

    from sqlalchemy import String, cast

    from app.models import utcnow

    if not survey.quotas:
        return []
    since = (now or utcnow()) - timedelta(minutes=JUMP_MINUTES)
    recent = {}
    for stored, in db.session.execute(db.select(Response.quota).filter(
            Response.survey_id == survey.id, Response.source == "real", Response.is_preview.is_(False),
            Response.status == Response.ACTIVE, Response.started_at >= since, cast(Response.quota, String).like(HELD))):
        for entry in (stored or {}).values():
            if entry.get("held") and entry.get("cell"):
                recent[entry["cell"]] = recent.get(entry["cell"], 0) + 1
    out = []
    for quota in of(survey):
        for cell in quota.cells:
            n, needed = recent.get(cell.id, 0), max(cell.target - cell.done, 0)
            if n >= JUMP_MIN and 2 * n >= needed and not cell.closed:
                out.append({"quota": quota.name, "cell": cell.name, "quota_id": quota.id, "cell_id": cell.id,
                            "n": n, "open": needed, "minutes": JUMP_MINUTES})
    return out


def release_expired(now=None, survey=None):
    """Give back the places of participations that sent no page for longer than their quota holds a place.
    Called every minute or so (lifecycle.maybe_close_due_surveys, /cron/close), and for one survey before its
    link turns someone away at the door (participate.page, full_at_entry). Returns how many participations lost
    their reservation. A participation with several quotas loses all its places at the shortest time.

    Survey by survey, each with its own shortest time, and only the participations whose stored decisions hold a
    place (audit 2026-10-05, T-39). Not while the person is in an external section and can still come back (the
    return link is good for external.MAX_SECONDS): the web app there may take longer than the quota holds a place,
    and nothing is sent to Askurious meanwhile (T-29). Each participation is given back in the order a completion
    takes its locks (settle): first the cells, then the participation, which is skipped if another request holds
    it right now; the version it was read with must still be there, so a decision written in between is never
    overwritten (T-25). Each participation is committed on its own (also what the caller did before): its locks
    then never pile up over several participations in an order a completion does not keep, which could make
    PostgreSQL break one off (code review 2026-10-07, T7)."""
    from datetime import timedelta

    from sqlalchemy import String, cast

    from app.external import MAX_SECONDS
    from app.models import utcnow

    now = now or utcnow()
    query = db.select(Quota.survey_id, Quota.id, Quota.hold_minutes)
    if survey is not None:
        query = query.filter(Quota.survey_id == survey.id)
    holds, shortest = {}, {}
    fresh = fresh_hold()
    for survey_id, qid, minutes in db.session.execute(query):
        holds[qid] = max(minutes or DEFAULT_HOLD, 1)
        first = min(holds[qid], fresh)  # a reservation no page followed yet goes sooner (BIZ-06)
        shortest[survey_id] = min(shortest.get(survey_id, first), first)
    idle_since = db.func.coalesce(Response.last_active_at, Response.started_at)
    back_until = now - timedelta(seconds=MAX_SECONDS)  # who left earlier cannot come back any more
    released = 0
    for survey_id, minutes in sorted(shortest.items()):
        waiting = db.session.execute(
            db.select(Response.id, Response.quota, idle_since, Response.version).filter(
                Response.survey_id == survey_id, Response.status == Response.ACTIVE, Response.source == "real",
                Response.is_preview.is_(False), idle_since < now - timedelta(minutes=minutes),
                db.or_(Response.outside_since.is_(None), Response.outside_since < back_until),
                cast(Response.quota, String).like(HELD))).all()
        for response_id, stored, since, version in waiting:
            held = {key: entry for key, entry in (stored or {}).items() if entry.get("held")}
            limit = min((_limit_of(entry, holds.get(int(key), DEFAULT_HOLD), since) for key, entry in held.items()),
                        default=None)
            if limit is None or since is None or since >= now - timedelta(minutes=limit):
                continue
            released += _give_back_held(response_id, stored, version)
            db.session.commit()  # its locks go at once (T7)
    return released


def _give_back_held(response_id, stored, version):
    """Give back the reserved places of one participation as release_expired read it (stored decisions, version).
    Returns 1 if it did, 0 if the participation changed since it was read or is at work right now."""
    held = [entry for entry in (stored or {}).values() if entry.get("held")]
    cells = sorted({entry["cell"] for entry in held if entry.get("cell") is not None})
    # the cells first, as settle takes them; PostgreSQL only (SQLite has one writer and ignores FOR UPDATE)
    db.session.execute(db.select(QuotaCell.id).where(QuotaCell.id.in_(cells)).with_for_update()).all()
    # then the participation, unless a request of the person is at work on it right now (that one sends a page, so
    # it is not idle); only as it was read: another server process may have given the place back a moment ago, or
    # the person came back and a calculation decided anew
    mine = db.session.execute(db.select(Response.id).where(
        Response.id == response_id, Response.version == version, Response.status == Response.ACTIVE)
        .with_for_update(skip_locked=True)).first()
    if mine is None:
        return 0
    fresh = {key: ({**{k: v for k, v in entry.items() if k != "t"}, "held": False} if entry.get("held") else entry)
             for key, entry in stored.items()}
    done = db.session.execute(
        db.update(Response).where(Response.id == response_id, Response.version == version,
                                  Response.status == Response.ACTIVE)
        # the version counter too: a request that read the participation before is then turned away
        # (StaleDataError) instead of writing the reservation back and taking it off a second time
        .values(quota=fresh, version=Response.version + 1).execution_options(synchronize_session=False)).rowcount
    if not done:
        return 0
    for entry in held:
        _bump(entry["cell"], QuotaCell.running > 0, running=-1)
    return 1


def reclaim(survey, response):
    """A participation whose reservation ran out sends a page again: it gets its place back if there is room.
    Without room it goes on all the same and counts as completed above the target at the end (settle)."""
    stored = decisions(response)
    if not stored or not counted(response):
        return
    changed = False
    for quota in survey.quotas:
        entry = stored.get(str(quota.id))
        if not entry or entry.get("res") != "in" or entry.get("held") or entry.get("done"):
            continue
        cell = next((c for c in quota.cells if c.id == entry.get("cell")), None)
        if cell is not None and _take(quota, cell):
            stored[str(quota.id)] = {**entry, "held": True, "t": _now_stamp()}
            changed = True
    if changed:
        response.quota = stored


def _from_responses(survey, only=None, until=None, sources=("real",)):
    """{cell id: {"running", "done", "refused", "over", "freed"}} as the real participations of the survey say, plus
    {status: n} of those participations. only: (URL parameter, value) of a stored parameter; then only the
    participations that came with it. until: only those completed by then (the step of a dashboard link,
    app/boards/ step_cutoff): the completed cells as they were at that moment. sources: the data pool counted
    (the Methodenteil counts the pool of the results page, test runs and dummies too; they keep their decisions
    in Response.quota as real ones do)."""
    counts = {c.id: {"running": 0, "done": 0, "refused": 0, "over": 0, "freed": 0} for q in survey.quotas for c in q.cells}
    by_status = {}
    rows = db.session.execute(db.select(Response.quota, Response.params, Response.status).filter(
        Response.survey_id == survey.id, Response.source.in_(tuple(sources)),
        db.or_(Response.source != "real", Response.is_preview.is_(False)),  # test runs and dummies are previews
        *([Response.status == Response.COMPLETED, Response.completed_at <= until] if until is not None else [])))
    for stored, params, status in rows:
        if only and (params or {}).get(only[0]) != only[1]:
            continue
        by_status[status] = by_status.get(status, 0) + 1
        for entry in (stored or {}).values():
            cell = counts.get(entry.get("cell"))
            if cell is None:
                continue
            cell["running"] += bool(entry.get("held"))
            cell["done"] += bool(entry.get("done"))
            cell["over"] += entry.get("res") == "over"
            cell["refused"] += entry.get("res") == "full"
            cell["freed"] += bool(entry.get("marked"))
    return counts, by_status


def recount(survey):
    """Set the counters to what the participations hold. Returns [(cell, counter, before, after)] of what
    differed. The write lock is taken first, so nobody takes a place between reading and writing: in SQLite by
    the UPDATE (one writer for the whole file), in PostgreSQL by locking the cells, which _take and _bump change
    (the UPDATE there locks only the quota rows; SQLite ignores FOR UPDATE)."""
    db.session.execute(db.update(Quota).where(Quota.survey_id == survey.id).values(position=Quota.position))
    db.session.execute(db.select(QuotaCell.id).join(Quota, QuotaCell.quota_id == Quota.id)
                       .where(Quota.survey_id == survey.id).with_for_update(of=QuotaCell)).all()
    db.session.expire_all()
    counts, _ = _from_responses(survey)
    changed = []
    for quota in survey.quotas:
        for cell in quota.cells:
            for name, value in counts[cell.id].items():
                if getattr(cell, name) != value:
                    changed.append((cell, name, getattr(cell, name), value))
                    setattr(cell, name, value)
    return changed


def exhausted(survey):
    """Nobody can get in any more: a quota that ends everybody has every cell completed or closed. Then the
    survey may close by itself (lifecycle). It ends everybody if an end "Quote voll" at the top level of the
    flow names it, and a condition at the top level ends those whom no cell fits. (A "Quotenprüfung" of a
    survey started before: at the top level, ending a full quota and people without a cell.)"""
    from app import flow

    top = flow.tree(survey).get("children", [])
    by_id = {q.id: q for q in survey.quotas}

    def spent(quota):
        return bool(quota.cells) and all(c.closed or c.done >= c.target for c in quota.cells)

    def no_cell(part, quota):
        return part.get("item") == f"q:{quota.id}" and (part.get("op", "eq"), str(part.get("value"))) == ("eq", "none")

    def nobody_passes(quota):  # a condition "this quota: no cell" with an end in it, or an end with that trigger
        return any(n["t"] == "branch" and _cond_reaches(n.get("cond") or {}, lambda part: no_cell(part, quota))
                   and any(c["t"] == "end" for c in n.get("children", [])) for n in top) \
            or any(n["t"] == "end" and any(case.get("when") == "nocell" and quota.id in flow.case_quotas(case)
                                           for case in flow.cases(n)) for n in top)

    for node in top:
        if is_legacy(node) and node["full"] == "end":
            if any(q.no_cell == "end" and spent(q) for q in node_quotas(survey, node)):
                return True
        elif node["t"] == "end":
            found = flow.cases(node)
            for case in found:
                if case.get("when") != "quotafull" or not case.get("quotas"):
                    continue
                dead = [spent(by_id[q]) and nobody_passes(by_id[q]) for q in case["quotas"] if q in by_id]
                if dead and (all(dead) if case.get("rule") == "all" else any(dead)):
                    return True
            # triggers "Teilquote voll" that between them name every cell of a quota end it like "Quote voll"
            named = {}
            for case in found:
                if case.get("when") == "cellfull" and case.get("quota") in by_id:
                    named.setdefault(case["quota"], set()).update(case.get("cells") or [])
            for qid, codes in named.items():
                quota = by_id[qid]
                if codes >= {c.code for c in quota.cells} and spent(quota) and nobody_passes(quota):
                    return True
    return False


def dummy_tally(survey):
    """{cell id: places taken} among the dummy data of the survey: their own count, never the real one."""
    tally = {}
    for stored in db.session.scalars(db.select(Response.quota).filter(Response.survey_id == survey.id, Response.source == "dummy")):
        for entry in (stored or {}).values():
            if entry.get("res") == "in" and entry.get("cell") is not None:
                tally[entry["cell"]] = tally.get(entry["cell"], 0) + 1
    return tally


def providers(survey):
    """[(URL parameter, value)] the state can be shown for: the allowed values of the stored parameters; for a
    stored parameter without a list, the values seen so far (at most 30)."""
    from app import panel

    out = []
    for p in panel.config(survey)["params"]:
        if p.get("mode") != "store":
            continue
        values = list(p.get("allowed") or [])
        if not values:
            seen = set()
            for params in db.session.scalars(db.select(Response.params).filter(
                    Response.survey_id == survey.id, Response.source == "real", Response.params.is_not(None)).limit(5000)):
                value = (params or {}).get(p["name"])
                if value:
                    seen.add(value)
            values = sorted(seen)[:30]
        out += [(p["name"], v) for v in values]
    return out


def provider(survey, key):
    """(parameter, value) for "name=value" if the state can be shown for it, else None."""
    name, sep, value = (key or "").partition("=")
    return (name, value) if sep and (name, value) in providers(survey) else None


def view(survey, response):
    """What conditions of the flow may test: {"q:<quota id>": "in" | "full" | "none" | ..., "qc:<quota id>":
    code of the cell}."""
    stored = decisions(response)
    if not stored:
        return {}
    out = {}
    for quota in survey.quotas:
        entry = stored.get(str(quota.id))
        if entry:
            # for a condition: a place or none; "other" (no place because another quota of the check was full)
            # reads as full, "over" (completed above the target) as a place
            out[f"q:{quota.id}"] = {"over": "in", "other": "full"}.get(entry["res"], entry["res"])
            cell = next((c for c in quota.cells if c.id == entry.get("cell")), None)
            out[f"qc:{quota.id}"] = cell.code if cell else ""
    return out


def passed(survey, response):
    """Ids of the check nodes this participation has been through: there is no way back across them."""
    stored = decisions(response)
    return {n["id"] for n in nodes(survey) if any(str(q) in stored for q in n.get("quotas") or [])} if stored else set()


def _cond_reaches(cond, test):
    """Whoever passes `test` meets the condition: a single condition that passes it; one with several parts
    (flow.parts) if one part passes it and the rule is "any", or if every part passes it (rule "all")."""
    from app import flow

    found = flow.parts(cond)
    if not found:
        return False
    if isinstance(cond.get("conds"), list) and cond.get("rule") != "any":
        return all(test(p) for p in found)
    return any(test(p) for p in found)


def full_at_entry(survey, params):
    """The end "Quote voll" that would end someone who opens the link with these URL parameters, or None. Only
    quotas whose cells depend on URL parameters alone can be decided before the first answer; nothing is
    reserved or counted here, the place is taken when the participation passes the calculation. Ends inside a
    condition are left to the flow, unless the condition is on one of the end's own quotas. (Surveys started
    before: the "Quotenprüfung" that ends a full quota.)"""
    from app import flow

    answers = {f"p:{k}": v for k, v in (params or {}).items()}

    def full(quota):
        return _full_by_params(quota, answers) is not None

    root = flow.tree(survey)
    for node, parent, _ in flow.walk(root):
        if is_legacy(node):
            if node["full"] == "end" and any(full(q) for q in node_quotas(survey, node)):
                return node
        elif node["t"] == "end" and end_quotas(node):
            names = {f"q:{q}" for q in end_quotas(node)}
            own = parent["t"] == "branch" and _cond_reaches(parent.get("cond") or {}, lambda part: str(part.get("item")) in names)
            if parent is not root and not (own and flow.find(root, parent["id"])[1] is root):
                continue
            # the first trigger that holds for what the URL parameters say; one that needs no quota is for the flow
            case = flow.case_for(node, door_view(survey, node, params))
            if case is not None and case.get("when") in ("quotafull", "cellfull") and flow.case_quotas(case):
                return node
    return None


def _full_by_params(quota, answers):
    """The cell that is full for someone with these URL parameters, if the quota depends on URL parameters alone
    and every cell the person could get is full; else None."""
    if not quota.cells or not all(
            isinstance(c.get("item"), str) and c["item"].startswith("p:") for cell in quota.cells for c in cell.conds or []):
        return None
    cells = candidates(quota, answers)
    return cells[0] if cells and all(is_full(quota, cell) for cell in cells) else None


def door_view(survey, node, params):
    """What `view` says for someone turned away when opening the link (full_at_entry gave `node`): the quotas
    of the node that are full for these URL parameters, and the person's cell. For the completion link."""
    answers = {f"p:{k}": v for k, v in (params or {}).items()}
    out = {}
    named = set(end_quotas(node)) if node["t"] == "end" else set(node.get("quotas") or [])
    for quota in (q for q in survey.quotas if q.id in named):
        cell = _full_by_params(quota, answers)
        if cell is not None:
            out.update({f"q:{quota.id}": "full", f"qc:{quota.id}": cell.code})
    return out


# --- cross tables ---------------------------------------------------------------------------------------

_SHARE = re.compile(r"\[\s*(\d+(?:[.,]\d+)?)\s*%?\s*\]\s*$")
_COMPARE = re.compile(r"^(<=|>=|≤|≥|<|>|!=|≠)\s*(.+)$")
_SLUG = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _value_cond(text):
    """The operator and value of one category: "1" (=), "18..29" (from … to), "1, 2" (one of), ">= 50"."""
    from app import flow

    text = text.strip()
    m = _COMPARE.match(text)
    if m:
        op = {"<=": "le", "≤": "le", ">=": "ge", "≥": "ge", "<": "lt", ">": "gt", "!=": "ne", "≠": "ne"}[m.group(1)]
        return op, m.group(2).strip()
    if flow.bounds(text)[0] is not None and ".." in text:
        lo, hi = flow.bounds(text)
        return "between", f"{lo}..{hi}"
    if "," in text:
        return "in", ", ".join(flow.code_list(text))
    return "eq", text


def parse_attributes(survey, spec):
    """The text form of the attributes (the page builds them from what is ticked, attributes_from_form; this
    form is for scripts and tests). One attribute per line: "gender: Männer = 1; Frauen = 2" or
    "age: 18 bis 29 = 18..29 [30]; ab 30 = >= 30 [70]". The name before the colon is the variable name of a
    question, or "p:name" for a URL parameter; "[30]" is the share of the category (for distributing a total).
    Returns [(ref, [(label, op, value, share)])]; raises ValueError with what is wrong."""
    from app import panel

    items = {i.code: i for b in survey.blocks for i in b.items if i.kind.collects_answer}
    params = {p["name"] for p in panel.config(survey)["params"]}
    out = []
    for line in (spec or "").splitlines():
        line = line.strip()
        if not line:
            continue
        name, sep, rest = line.partition(":")
        name = name.strip()
        if name in ("p", "url", "URL") and sep:  # "p:src: a = 1; …"
            param, sep, rest = rest.partition(":")
            name = f"p:{param.strip()}"
        if not sep:
            raise ValueError(f"Zeile „{line}“: erwartet „Variable: Name = Wert; Name = Wert“.")
        if name.startswith("p:"):
            if name[2:] not in params:
                raise ValueError(de_en("„{p}“ ist kein deklarierter URL-Parameter.", "“{p}” is no declared URL parameter.",
                                       p=name[2:]))
            ref = name
        elif name in items:
            ref = items[name].id
        else:
            raise ValueError(de_en("„{n}“ ist kein Variablenname einer Frage (und kein URL-Parameter „p:name“).",
                                   "“{n}” is no variable name of a question (and no URL parameter “p:name”).",
                                   n=name))
        categories = []
        for part in rest.split(";"):
            part = part.strip()
            if not part:
                continue
            share = 1.0
            m = _SHARE.search(part)
            if m:
                share, part = float(m.group(1).replace(",", ".")), part[:m.start()].strip()
            label, eq, value = part.partition("=")
            if eq and label.strip() and not label.strip().endswith(("<", ">", "!")) and value.strip():
                label, value = label.strip(), value.strip()
            else:
                label = value = part  # no name: the value is the name
            op, value = _value_cond(value)
            if not value:
                raise ValueError(f"Zeile „{line}“: „{part}“ hat keinen Wert.")
            categories.append((label[:80], op, value[:200], share))
        if len(categories) < 1:
            raise ValueError(de_en("Zeile „{line}“: keine Ausprägung angegeben.", "Line “{line}”: no value given.",
                                   line=line))
        if sum(c[3] for c in categories) <= 0:
            raise ValueError(f"Zeile „{line}“: Die Anteile ergeben zusammen null.")
        out.append((ref, categories))
    if not out:
        raise ValueError(de_en("Bitte mindestens ein Merkmal angeben, z. B. „gender: Männer = 1; Frauen = 2“.",
                               "Please give at least one characteristic, e.g. “gender: men = 1; women = 2”."))
    if len({ref for ref, _ in out}) != len(out):
        raise ValueError(de_en("Jedes Merkmal nur einmal angeben.", "Give each characteristic only once."))
    return out


def _slug(text, taken, add=True):
    """A code made from a name: lower case, umlauts written out, a letter first; a number behind it where the
    code is taken."""
    base = re.sub(r"[^a-z0-9]+", "_", text.translate(_SLUG).lower()).strip("_") or "z"
    if not base[0].isalpha():
        base = "z" + base
    base = base[:36]
    code, n = base, 2
    while code in taken:
        code, n = f"{base}_{n}", n + 1
    if add:
        taken.add(code)
    return code


MAX_ATTRIBUTES, MAX_RANGES = 4, 12


def attributes_from_form(survey, form):
    """The attributes of a cross quota as picked in the form: per attribute a question ("m0_item") and its
    categories: the ticked answers ("m0_codes", each its own category), or ranges from … to ("m0_r0_from",
    "m0_r0_to"); optional shares ("m0_share_<code>", "m0_r0_share"). Same shape as parse_attributes; raises
    ValueError with what is missing."""
    from app import conditions

    described = describe(survey)

    def share(key):
        raw = form.get(key, "").strip().replace(",", ".")
        try:
            value = float(raw) if raw else None
        except ValueError:
            return None
        if value is not None and not (math.isfinite(value) and value >= 0):
            raise ValueError(de_en("„{value}“ ist kein Anteil: Anteile sind Zahlen ab 0.",
                                   "“{value}” is not a share: shares are numbers from 0.", value=form.get(key)))
        return value

    out = []
    for k in range(MAX_ATTRIBUTES):
        key = form.get(f"m{k}_item", "")
        info = described.get(key)
        if info is None:
            continue
        ref = int(key) if key.isdigit() else key
        categories = []
        if info["kind"] == "choice":
            ticked = set(form.getlist(f"m{k}_codes"))
            for code, label in info["options"]:
                if code in ticked:
                    categories.append((label[:80], "in" if info["multi"] else "eq", code, share(f"m{k}_share_{code}")))
        elif info["kind"] in ("number", "date"):
            for j in range(MAX_RANGES):
                lo, hi = form.get(f"m{k}_r{j}_from", "").strip()[:40], form.get(f"m{k}_r{j}_to", "").strip()[:40]
                if info["kind"] == "number":
                    lo, hi = conditions.number_text(lo), conditions.number_text(hi)
                if lo or hi:
                    cond = _range_cond(ref, lo, hi)
                    categories.append((cond_text(survey, cond, short=True), cond["op"], cond["value"], share(f"m{k}_r{j}_share")))
        else:
            raise ValueError(de_en("„{name}“ hat keine Liste erlaubter Werte; bitte in den Einstellungen unter "
                                   "„Datenerhebung → Teilnahmelink“ die erlaubten Werte eintragen.",
                                   "“{name}” has no list of allowed values; please enter the allowed values in the settings "
                                   "under “Data collection → Participation link”.", name=info["name"]))
        if not categories:
            raise ValueError(de_en("Merkmal „{name}“: Bitte mindestens eine Antwort ankreuzen bzw. einen Bereich angeben.",
                                   "Attribute “{name}”: please tick at least one answer or give a range.", name=info["name"]))
        given = [c[3] for c in categories if c[3] is not None]
        if given and len(given) != len(categories):
            raise ValueError(de_en("Merkmal „{name}“: Bitte entweder für jede Ausprägung einen Anteil angeben oder für keine.",
                                   "Attribute “{name}”: please give a share either for every category or for none.",
                                   name=info["name"]))
        if given and sum(given) <= 0:
            raise ValueError(de_en("Merkmal „{name}“: Die Anteile ergeben zusammen null.",
                                   "Attribute “{name}”: the shares add up to zero.", name=info["name"]))
        out.append((ref, [(label, op, value, 1.0 if s is None else s) for label, op, value, s in categories]))
    if not out:
        raise ValueError(de_en("Bitte mindestens ein Merkmal wählen.", "Please choose at least one attribute."))
    if len({ref for ref, _ in out}) != len(out):
        raise ValueError(de_en("Jedes Merkmal nur einmal wählen.", "Choose each attribute only once."))
    return out


def generate_cells(survey, quota, spec, target=0, total=0, replace=False):
    """Cross the attributes: one cell per combination, named after its categories. spec: the attributes as
    attributes_from_form gives them, or their text form (parse_attributes). target: the same target for every
    cell; total (wins): a total distributed by the shares of the categories, largest remainders first, so the
    targets add up exactly. Returns the number of cells made; raises ValueError."""
    import itertools

    attributes = parse_attributes(survey, spec) if isinstance(spec, str) else spec
    combos = list(itertools.product(*[cats for _, cats in attributes]))
    kept = [] if replace else list(quota.cells)
    if len(kept) + len(combos) > MAX_CELLS:
        raise ValueError(de_en("{n} Kombinationen sind zu viele (höchstens {most} Teilquoten je Quote).",
                               "{n} combinations are too many (at most {most} sub-quotas per quota).", n=len(combos),
                               most=MAX_CELLS))
    if total:
        sums = [sum(c[3] for c in cats) for _, cats in attributes]
        exact = []
        for combo in combos:
            share = 1.0
            for cat, whole in zip(combo, sums):
                share *= cat[3] / whole
            exact.append(total * share)
        targets = [int(x) for x in exact]
        for k in sorted(range(len(exact)), key=lambda k: exact[k] - targets[k], reverse=True)[:total - sum(targets)]:
            targets[k] += 1
    else:
        targets = [target] * len(combos)
    taken = {c.code.lower() for c in kept}
    cells = list(kept)
    for combo, goal in zip(combos, targets):
        name = " / ".join(cat[0] for cat in combo)
        conds = [{"item": ref, "op": cat[1], "value": cat[2]} for (ref, _), cat in zip(attributes, combo)]
        cells.append(QuotaCell(name=name[:200], code=_slug(name, taken), conds=conds, target=goal, closed=False,
                               running=0, done=0, refused=0, over=0))
    for position, cell in enumerate(cells):
        cell.position = position
    quota.cells = cells
    return len(combos)


def matrix(survey, quota, rows, lang=None, items=None, conds=True):
    """The cells of a quota as a table of two attributes, if they are a complete cross of exactly two: {"rows":
    [label], "cols": [label], "cells": [[row of the report]]}; else None. conds=False (a share link without
    conditions): the heads come from the names of the cells ("Männer / 18 bis 29": the row is "Männer", the
    column "18 bis 29"), never from their conditions; None if the names do not say rows and columns."""
    cells = list(quota.cells)
    if len(cells) < 4 or any(len(c.conds or []) != 2 for c in cells) or len(rows) != len(cells):
        return None
    def key(cond):
        return (str(cond.get("item")), cond.get("op"), str(cond.get("value")))
    if len({(str(c.conds[0].get("item")), str(c.conds[1].get("item"))) for c in cells}) != 1:
        return None
    first, second, grid = [], [], {}
    for cell, row in zip(cells, rows):
        a, b = key(cell.conds[0]), key(cell.conds[1])
        if a not in first:
            first.append(a)
        if b not in second:
            second.append(b)
        if (a, b) in grid:
            return None
        grid[(a, b)] = (cell, row)
    if len(first) < 2 or len(second) < 2 or len(first) * len(second) != len(cells):
        return None
    if conds:
        labels_a = {key(c.conds[0]): cond_text(survey, c.conds[0], lang, items) for c in cells}
        labels_b = {key(c.conds[1]): cond_text(survey, c.conds[1], lang, items) for c in cells}
    else:
        labels_a, labels_b = {}, {}
        for cell in cells:
            parts = [p.strip() for p in (cell.name or "").split(" / ")]
            if len(parts) != 2 or not all(parts):
                return None
            # one name for the whole row and one for the whole column, and no two rows (columns) of the same name
            if labels_a.setdefault(key(cell.conds[0]), parts[0]) != parts[0] \
                    or labels_b.setdefault(key(cell.conds[1]), parts[1]) != parts[1]:
                return None
        if len(set(labels_a.values())) != len(first) or len(set(labels_b.values())) != len(second):
            return None
    return {"rows": [labels_a[a] for a in first], "cols": [labels_b[b] for b in second],
            "cells": [[grid[(a, b)][1] for b in second] for a in first]}


def matrix_text(quota, grid):
    """The matrix as tab-separated text to copy: "completed / target" in every field."""
    lines = ["\t".join([quota.name] + grid["cols"])]
    for label, row in zip(grid["rows"], grid["cells"]):
        lines.append("\t".join([label] + [f"{r['done']} / {r['target']}" for r in row]))
    return "\n".join(lines)


# --- pre-launch check ---------------------------------------------------------------------------------------


def _seen_by_all(survey, root, block_id):
    """The block has a page and stands in no branch and under no randomizer: everybody sees it."""
    from app import flow

    block = next((b for b in survey.blocks if b.id == block_id), None)
    return (block is not None and bool(block.pages())
            and not any(a["t"] in ("branch", "else", "rand") for a in flow.ancestors(root, f"k{block_id}")))


def check(survey):
    """[(severity, message)] for the pre-launch check."""
    from app import conditions, flow

    problems = []
    quotas = of(survey)
    check_nodes = nodes(survey)
    if not quotas and not check_nodes:
        return problems
    root = flow.tree(survey)
    order = flow.block_order(root)
    items = {i.id: i for b in survey.blocks for i in b.items}
    item_block = {i.id: i.block_id for i in items.values()}
    declared = __import__("app.panel", fromlist=["config"]).config(survey)["params"]
    params = {p["name"] for p in declared}
    survey_params = {p["name"]: list(p.get("allowed") or []) for p in declared}
    walk = [n for n, _, _ in flow.walk(root)]
    def late(quota, before):
        """[(cell, condition)] on questions that do not come before a place in the flow."""
        return [(cell, cond) for cell in quota.cells for cond in cell.conds or []
                if flow.source_id(cond.get("item")) in item_block and item_block[flow.source_id(cond.get("item"))] not in before]

    in_loop = flow.loop_blocks(survey)
    for quota in quotas:
        for cell in quota.cells:
            for cond in cell.conds or []:
                if item_block.get(flow.source_id(cond.get("item"))) in in_loop:
                    problems.append(("error", de_en("Quote „{quota}“, Teilquote „{cell}“: Die Bedingung „{cond}“ bezieht sich "
                                                    "auf eine Frage in einer Schleife. Ihre Antworten gibt es je Durchgang; so "
                                                    "bekäme niemand einen Platz in dieser Teilquote.",
                                                    "Quota “{quota}”, sub-quota “{cell}”: the condition “{cond}” refers to a "
                                                    "question in a loop. Its answers exist per round; so nobody would get a "
                                                    "place in this sub-quota.", quota=quota.name, cell=cell.name,
                                                    cond=cond_text(survey, cond, items=items))))
    for node in check_nodes:
        here = walk.index(node)
        before = [n["block"] for n in walk[:here] if n["t"] == "block"]
        checked = node_quotas(survey, node)
        if not checked:
            problems.append(("error", de_en("Quotenberechnung ohne Quote: bitte in ihren Einstellungen eine Quote wählen.",
                                            "Quota calculation without a quota: please choose a quota in its settings.")))
        if not before:
            problems.append(("error", de_en("Die Quotenberechnung steht vor der ersten Seite. Bitte hinter die Einwilligung setzen; "
                                            "Quoten, die nur von URL-Parametern abhängen, weisen volle Teilquoten trotzdem schon "
                                            "beim Öffnen des Links ab.",
                                            "The quota calculation comes before the first page. Please put it after the consent; "
                                            "quotas that depend only on URL parameters still turn people away from full "
                                            "sub-quotas as soon as the link is opened.")))
        elif not any(_seen_by_all(survey, root, b) for b in before):
            # the link opened shows the first page without storing anything: the place is taken with the first page
            # sent (participate.page), so the person may answer that page and only then learn the quota is full
            problems.append(("warning", de_en("Vor der Quotenberechnung steht keine Seite, die alle sehen: Die Blöcke davor "
                                              "hängen an Bedingungen oder Randomisierungen. Wer keinen davon sieht, bekommt "
                                              "den Quotenplatz erst mit der ersten abgeschickten Seite; ist die Teilquote voll, "
                                              "endet die Teilnahme erst dann. Am besten steht die Einwilligung davor.",
                                              "No page that everyone sees comes before the quota calculation: the blocks "
                                              "before it depend on conditions or randomisation. Anyone who sees none of them gets "
                                              "the quota place only with the first page sent; if the sub-quota is full, the "
                                              "participation only ends then. Best put the consent before it.")))
        if any(a["t"] == "loop" for a in flow.ancestors(root, node["id"])):
            problems.append(("error", de_en("Eine Quotenberechnung kann nicht in einer Schleife stehen.",
                                            "A quota calculation cannot be inside a loop.")))
        consent = survey.consent_item
        if consent is not None and before and consent.block_id in order and consent.block_id not in before:
            problems.append(("warning", de_en("Die Quotenberechnung steht vor der Einwilligung.",
                                              "The quota calculation comes before the consent.")))
        if is_legacy(node):  # a survey started with a "Quotenprüfung": its link is still used
            from app import panel

            error = panel.link_error(node.get("link"), panel.link_names(survey))
            if error:
                problems.append(("error", de_en("Quotenberechnung, Abschlusslink: {error}",
                                                "Quota calculation, completion link: {error}", error=error)))
        for quota in checked:
            for cell, cond in late(quota, before):
                problems.append(("error", de_en("Quote „{quota}“, Teilquote „{cell}“: Die Bedingung „{cond}“ bezieht sich "
                                                "auf eine Frage, die erst nach der Quotenberechnung kommt.",
                                                "Quota “{quota}”, sub-quota “{cell}”: the condition “{cond}” refers to a "
                                                "question that only comes after the quota calculation.", quota=quota.name,
                                                cell=cell.name, cond=cond_text(survey, cond, items=items))))
    used = [q for n in check_nodes for q in n.get("quotas") or []]
    # the ends: every trigger on quotas names them, and knows the person's cell where it stands
    by_id = {q.id: q for q in quotas}
    for node in walk:
        if node["t"] != "end":
            continue
        here = walk.index(node)
        before = [n["block"] for n in walk[:here] if n["t"] == "block"]
        calculated = {q for n in walk[:here] if n["t"] == "quota" for q in n.get("quotas") or []}
        for case in flow.cases(node):
            when = case.get("when")
            if when not in ("quotafull", "cellfull", "nocell"):
                continue
            label = flow.END_TRIGGERS[when][0]
            named = [by_id[q] for q in flow.case_quotas(case) if q in by_id]
            if not named:
                problems.append(("error", de_en("Ende ({label}) ohne Quote: bitte in seinen Einstellungen wählen, bei welchen "
                                                "Quoten es greift.",
                                                "End ({label}) without a quota: please choose in its settings for which "
                                                "quotas it applies.", label=label)))
                continue
            if when == "cellfull":
                codes = {c.code for c in named[0].cells}
                if not [c for c in case.get("cells") or [] if c in codes]:
                    problems.append(("error", de_en("Ende ({label}) ohne Teilquote: bitte in seinen Einstellungen mindestens "
                                                    "eine Teilquote von „{quota}“ ankreuzen.",
                                                    "End ({label}) without a sub-quota: please tick at least one sub-quota "
                                                    "of “{quota}” in its settings.", label=label, quota=named[0].name)))
            for quota in named:
                if quota.id in calculated:
                    continue
                # it looks at the state before the calculation: it must know the person's cell by then
                for cell, cond in late(quota, before):
                    problems.append(("error", de_en("Ende ({label}) vor der Berechnung von Quote „{quota}“: Die Bedingung "
                                                    "„{cond}“ der Teilquote „{cell}“ bezieht sich auf eine Frage, die erst "
                                                    "danach kommt.",
                                                    "End ({label}) before the calculation of quota “{quota}”: the condition "
                                                    "“{cond}” of the sub-quota “{cell}” refers to a question that only comes "
                                                    "after it.", label=label, quota=quota.name,
                                                    cond=cond_text(survey, cond, items=items), cell=cell.name)))
    ended = {q for n in walk if n["t"] == "end" for q in end_quotas(n)} \
        | {q for n in check_nodes if is_legacy(n) and n["full"] == "end" for q in n.get("quotas") or []}
    tested = {str(c.get("item")).partition(":")[2] for n in walk if n["t"] == "branch" for c in flow.parts(n.get("cond"))
              if str(c.get("item")).startswith(("q:", "qc:"))}
    for quota in quotas:
        label = de_en("Quote „{name}“", "Quota “{name}”", name=quota.name)
        if used.count(quota.id) > 1:
            problems.append(("error", de_en("{label} wird an mehreren Stellen im Ablauf berechnet; jede Quote nur an einer.",
                                            "{label} is calculated at several places in the flow; each quota only at one.",
                                            label=label)))
        if quota.id not in used:
            problems.append(("warning", de_en("{label} wird nirgends berechnet: Im Ablauf fehlt eine Quotenberechnung dafür.",
                                              "{label} is not calculated anywhere: the flow lacks a quota calculation for it.",
                                              label=label)))
        elif quota.id not in ended and str(quota.id) not in tested:
            problems.append(("warning", de_en("{label} beendet niemanden: Ist sie voll, läuft die Teilnahme weiter. Im Ablauf "
                                              "fehlt ein Ende mit dem Auslöser „Quote voll“ für diese Quote.",
                                              "{label} ends nobody: when it is full, the participation carries on. The flow "
                                              "lacks an end with the trigger “quota full” for this quota.", label=label)))
        if not quota.cells:
            problems.append(("error", de_en("{label} hat keine Teilquote.", "{label} has no sub-quota.", label=label)))
        seen = {}
        for cell in quota.cells:
            where = de_en("{label}, Teilquote „{cell}“", "{label}, sub-quota “{cell}”", label=label, cell=cell.name)
            if not cell.conds:
                problems.append(("error", de_en("{where}: ohne Bedingung.", "{where}: no condition.", where=where)))
            if not cell.target:
                problems.append(("error", de_en("{where}: ohne Soll.", "{where}: no target.", where=where)))
            for cond in cell.conds or []:
                ref, op, value = cond.get("item"), cond.get("op", "eq"), str(cond.get("value", ""))
                if isinstance(ref, str) and ref.startswith("p:"):
                    if ref[2:] not in params:
                        problems.append(("error", de_en("{where}: Bedingung auf URL-Parameter „{name}“, der nicht deklariert ist.",
                                                        "{where}: condition on the URL parameter “{name}”, which is not declared.",
                                                        where=where, name=ref[2:])))
                    continue
                if ref in ("geo:country", "geo:state"):
                    if not getattr(survey, "geo_ip", False):
                        problems.append(("error", de_en("{where}: Bedingung auf Land oder Bundesland aus der IP-Adresse, aber die "
                                                        "Einstellung dazu ist aus (Einstellungen, Einwilligung und Datenschutz).",
                                                        "{where}: condition on the country or federal state from the IP address, "
                                                        "but that setting is off (settings, consent and privacy).", where=where)))
                    continue
                item = items.get(flow.source_id(ref))
                if item is None or (isinstance(ref, str) and item.type not in ("postal_code", "geolocation")):
                    problems.append(("error", de_en("{where}: Die Frage einer Bedingung gibt es nicht mehr.",
                                                    "{where}: the question of a condition no longer exists.", where=where)))
                    continue
                if isinstance(ref, str):  # the state of a postal code or of a location ("0": outside Germany)
                    from app.items.federal_state import STATE_CODES

                    codes = list(STATE_CODES) + (["0"] if item.type == "geolocation" else [])
                else:
                    codes = [c for c, _ in flow.answer_codes(item)]
                wanted = flow.code_list(value) if op == "in" else [value] if op in ("eq", "ne", "contains") else []
                missing = [v for v in wanted if codes and v not in codes]
                if missing:
                    problems.append(("error", de_en("{where}: „{missing}“ ist kein Antwortcode von {code} (vorhanden: {codes}).",
                                                    "{where}: “{missing}” is not an answer code of {code} (available: {codes}).",
                                                    where=where, missing=", ".join(missing), code=item.code,
                                                    codes=", ".join(codes))))
                # as the check of the conditions in the flow says it (flow.check)
                if op == "nin" and codes and [v for v in flow.code_list(value) if v not in codes]:
                    problems.append(("warning", de_en("{where}: Die Bedingung „{cond}“ nennt einen Wert, der kein Antwortcode "
                                                      "von {code} ist (vorhanden: {codes}).",
                                                      "{where}: the condition “{cond}” names a value that is not an answer code "
                                                      "of {code} (available: {codes}).", where=where,
                                                      cond=cond_text(survey, cond, items=items), code=item.code,
                                                      codes=", ".join(codes))))
                if item.type == "date_time" and not isinstance(ref, str):
                    given = flow.bounds(value) if op == "between" else (value,) if op in ("eq", "ne", "lt", "gt", "le", "ge") else ()
                    unread = [v for v in given if v and not item.kind.condition_ok(item, v)]
                    if unread:
                        example = {"year": "2000", "month": "05.2000", "time": "14:30"}.get(item.kind.asked(item), "23.05.2000")
                        problems.append(("warning", de_en("{where}: „{value}“ lässt sich nicht mit den Antworten von {code} "
                                                          "vergleichen (erwartet z. B. {example}); so trifft die Bedingung nie zu.",
                                                          "{where}: “{value}” cannot be compared with the answers of {code} "
                                                          "(expected e.g. {example}); so the condition never holds.",
                                                          where=where, value=unread[0], code=item.code, example=example)))
                if op == "between" and flow.bounds(value)[0] is None:
                    problems.append(("error", de_en("{where}: „von … bis“ braucht zwei Werte.",
                                                    "{where}: “from … to” needs two values.", where=where)))
                for problem in conditions.number_problems(cond, item):
                    problems.append(("warning", de_en("{where}: Bedingung „{cond}“: {problem}.", "{where}: condition “{cond}”: {problem}.",
                                                      where=where, cond=cond_text(survey, cond, items=items), problem=problem)))
                if op not in ("answered", "empty") and not value:
                    problems.append(("error", de_en("{where}: Eine Bedingung hat keinen Wert.", "{where}: a condition has no value.",
                                                    where=where)))
            key = repr(sorted((str(c.get("item")), c.get("op"), str(c.get("value"))) for c in cell.conds or []))
            if cell.conds and key in seen:
                problems.append(("warning", de_en("{where}: dieselben Bedingungen wie Teilquote „{other}“; ",
                                                  "{where}: the same conditions as sub-quota “{other}”; ", where=where, other=seen[key])
                                 + (de_en("beide füllen sich abwechselnd.", "both fill in turn.") if quota.pick == "least"
                                    else de_en("es zählt immer die erste passende Teilquote.",
                                               "the first sub-quota that fits always counts."))))
            seen.setdefault(key, cell.name)
        problems += [("warning", message) for message in coverage(survey, quota, items, params=survey_params)]
    return problems


MAX_POINTS = 20000  # combinations of answers the coverage check tries at most for one quota
SHOW_POINTS = 4     # examples a message of the coverage check names


def _number_text(value):
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}" if english() else f"{value:g}".replace(".", ",")


def _dimension(ref, conds, items, params):
    """[(value as the answers hold it, text, inside)] to try for one question or URL parameter of a quota's
    conditions, or None if its answers cannot be listed. inside: within the range the conditions name (only
    there does a value no cell fits count as a gap; below the lowest and above the highest bound people are
    usually meant to be screened out)."""
    from app import flow
    from app.conditions import read_number
    from app.items.choice import SingleChoice

    if isinstance(ref, str) and ref.startswith("p:"):
        allowed = params.get(ref[2:]) or []
        return [(v, f"{ref[2:]} = {v}", True) for v in allowed] or None
    item = items.get(ref) if isinstance(ref, int) else None
    if item is None:
        return None
    if isinstance(item.kind, SingleChoice):
        codes = flow.answer_codes(item)
        return [(code, f"{item.code} „{label}“" if label else f"{item.code} = {code}", True) for code, label in codes] or None
    kind = (item.config or {}).get("validation") if item.type == "text_input" else None
    if kind not in ("integer", "decimal"):
        return None
    bounds = []
    for cond in conds:
        op, value = cond.get("op", "eq"), cond.get("value")
        given = flow.bounds(value) if op == "between" else (value,) if op in ("eq", "ne", "lt", "gt", "le", "ge") else ()
        bounds += [n for n in (read_number(v) for v in given) if n is not None and math.isfinite(n)]
    if not bounds:
        return None
    lo, hi = min(bounds), max(bounds)
    points = set()
    for n in bounds:
        points |= {math.floor(n) - 1, math.floor(n), math.ceil(n), math.ceil(n) + 1}
        if kind == "decimal":
            points.add(n)
    if kind == "decimal":
        edges = sorted(set(bounds))
        points |= {(a + b) / 2 for a, b in zip(edges, edges[1:])}
    return [(int(n) if float(n).is_integer() else n, f"{item.code} = {_number_text(n)}", lo <= n <= hi)
            for n in sorted(points)]


def coverage(survey, quota, items=None, params=None):
    """Messages about cells that overlap or leave answers out (audit 2026-10-05, T-36): every combination of the
    answers the conditions ask about is tried as `match` would (a choice: its answer codes; a number question: the
    values at and around the bounds; a URL parameter: its allowed values). Overlapping cells: who fits both gets the
    first (or with pick "least" the less filled one), the other fills more slowly than it seems. Answers no cell
    fits: those people count in no cell ("keine Teilquote"). Only for quotas of at least two cells whose
    conditions are all on such questions or parameters; cells with the very same conditions have a message of
    their own (check)."""
    import itertools

    from app import flow, panel

    cells = [c for c in quota.cells if c.conds]
    if len(cells) < 2:
        return []
    if items is None:
        items = {i.id: i for b in survey.blocks for i in b.items}
    if params is None:
        params = {p["name"]: list(p.get("allowed") or []) for p in panel.config(survey)["params"]}
    refs = []
    for cell in cells:
        for cond in cell.conds:
            if cond.get("item") not in refs:
                refs.append(cond.get("item"))
    dims = []
    for ref in refs:
        dim = _dimension(ref, [c for cell in cells for c in cell.conds if c.get("item") == ref], items, params)
        if dim is None:
            return []
        dims.append(dim)
    if math.prod(len(d) for d in dims) > MAX_POINTS:
        return []
    same = {}
    for cell in cells:
        same.setdefault(repr(sorted((str(c.get("item")), c.get("op"), str(c.get("value"))) for c in cell.conds)), []).append(cell.id)
    twins = {cid for ids in same.values() if len(ids) > 1 for cid in ids}
    overlaps, gaps = {}, []
    for point in itertools.product(*dims):
        answers = dict(zip(refs, (value for value, _, _ in point)))
        fitting = [cell for cell in cells if all(flow.holds(c, answers, survey) for c in cell.conds)]
        text = ", ".join(t for _, t, _ in point)
        if len(fitting) >= 2:
            pair = (fitting[0], fitting[1])
            if not (pair[0].id in twins and pair[1].id in twins):
                overlaps.setdefault((pair[0].id, pair[1].id), (pair, []))[1].append(text)
        elif not fitting and all(inside for _, _, inside in point):
            gaps.append(point)
    label = de_en("Quote „{name}“", "Quota “{name}”", name=quota.name)
    out = []
    for (first, second), examples in overlaps.values():
        then = (de_en("wer in beide passt, kommt in die weniger gefüllte.", "anyone who fits both goes to the less filled one.")
                if quota.pick == "least"
                else de_en("es zählt immer die erste passende, „{name}“.", "the first one that fits always counts, “{name}”.",
                           name=first.name))
        shown = "; ".join(examples[:SHOW_POINTS]) + (" …" if len(examples) > SHOW_POINTS else "")
        out.append(de_en("{label}: Die Teilquoten „{a}“ und „{b}“ überschneiden sich, z. B. bei {shown}; {then}",
                         "{label}: the sub-quotas “{a}” and “{b}” overlap, e.g. at {shown}; {then}",
                         label=label, a=first.name, b=second.name, shown=shown, then=then))
    if gaps:
        out.append(_gap_message(label, refs, gaps, items))
    return out


class Gap(str):
    """The message on answers no cell fits: a str that carries the page it leads to, so the pre-launch check links it to
    the quota page by this mark, not by its words (UI/UX audit 2026-10-07, W105)."""
    link = "flow_page.quotas"


def _gap_message(label, refs, gaps, items):
    """The sentence on answers no cell fits (UI/UX audit 2026-10-05, V15): which question, which answers, and what
    to do; one question by the start of its text and its answers, several by examples of the combinations."""
    from app import flow
    from app.items.base import plain

    more = " …" if len(gaps) > SHOW_POINTS else ""
    item = items.get(refs[0]) if len(refs) == 1 and isinstance(refs[0], int) else None
    labels = dict(flow.answer_codes(item)) if item is not None and item.type != "text_input" else {}
    if item is not None and labels:
        quote = de_en("„{}“", "“{}”")
        answers = [quote.format(labels.get(point[0][0]) or point[0][0]) for point in gaps[:SHOW_POINTS]]
        joined = (", ".join(answers[:-1]) + de_en(" oder ", " or ") + answers[-1]) if len(answers) > 1 else answers[0]
        text = " ".join(plain(item.text("question", item.survey.default_language) or "").split())
        asked = (quote.format(text[:40].rsplit(" ", 1)[0].rstrip() + " …") if len(text) > 40
                 else quote.format(text) if text else item.code)
        return Gap(de_en("{label}: Wer bei {asked} {joined}{more} antwortet, passt in keine Teilquote. Teilquote "
                         "ergänzen oder die Antwort im Element als „fehlend“ markieren.",
                         "{label}: anyone who answers {asked} with {joined}{more} fits no sub-quota. Add a sub-quota or "
                         "mark the answer as “missing” in the element.",
                         label=label, asked=asked, joined=joined, more=more))
    examples = " / ".join(", ".join(t for _, t, _ in point) for point in gaps[:SHOW_POINTS])
    return Gap(de_en("{label}: Wer so antwortet ({examples}{more}), passt in keine Teilquote und zählt dort als „keine "
                     "Teilquote“. Teilquote ergänzen, wenn diese Personen zählen sollen.",
                     "{label}: anyone who answers like this ({examples}{more}) fits no sub-quota and counts there as “no "
                     "sub-quota”. Add a sub-quota if these people should count.",
                     label=label, examples=examples, more=more))


# --- the state of the quotas (fieldwork page, to copy and as CSV) ---------------------------------------


COLUMNS = ("Quote", "Teilquote", "Code", "Bedingungen", "Soll", "abgeschlossen", "laufend", "offen", "abgewiesen",
           "über Soll", "Zustand")
# the heads of COLUMNS and the states of a cell as the researchers' pages show them in English (the German words
# above stay the keys)
COLUMNS_EN = {"Quote": "Quota", "Teilquote": "Sub-quota", "Code": "Code", "Bedingungen": "Conditions", "Soll": "Target",
              "abgeschlossen": "completed", "laufend": "running", "offen": "open", "abgewiesen": "refused",
              "über Soll": "over target", "Zustand": "State"}
STATES_EN = {"offen": "open", "voll": "full", "geschlossen": "closed"}


def state_name(state):
    """A state of state_of ("offen", "voll", "geschlossen") in the language of the page."""
    return STATES_EN.get(state, state) if english() else state


def state_of(quota, cell):
    if cell.closed:
        return "geschlossen"
    return "voll" if is_full(quota, cell) else "offen"


def marked_done(table, sources=("real",)):
    """{cell id: completed places} of the participations in an export table (export.rows, before marked ones are
    left out) that quality assurance marks: real participations only, as the counters (sources: those of the data
    pool, see report)."""
    out = {}
    for row in table:
        if row.get("qc_marked") != 1:
            continue
        response = table.numbered.get(row.get("resp_id"))
        if response is None or not (counted(response) if tuple(sources) == ("real",) else
                                    response.source in sources and (response.source != "real" or not response.is_preview)):
            continue
        for entry in (response.quota or {}).values():
            if entry.get("done") and entry.get("cell") is not None:
                out[entry["cell"]] = out.get(entry["cell"], 0) + 1
    return out


def report(survey, lang=None, only=None, conds=True, counts=None, until=None, marked=None, sources=("real",),
           shown=False):
    """[{"quota", "mode", "node", "ends", "cells": [row], "sum": row}] with row = {name, code, conds, target, done,
    running, open, refused, over, marked, freed, state, share}: the state of every cell right now. marked: {cell
    id: completed places of participations quality assurance marks} (marked_done); they are left out of done, open
    and share, as marked participations are left out of the results (the counters, the state and the steering in
    the field stay as they are). freed: completions marked at completion that gave their place back (setting
    quota_drop_marked); they were never in done, and "marked" counts them too. only: (URL parameter,
    value): completed, running, refused and over count the participations of that provider only; target, open
    and state stay those of the cell (a target is not split between providers). conds=False: for readers who
    must not see the conditions (the rows hold none, the matrix is headed by the names of the cells). counts:
    what `_from_responses` gave for `only`, if the caller has it at hand. until: completed as of that moment (the
    step of a dashboard link); target, open and state stay those of the cell. sources: the data pool counted
    instead of the counters of the real participations (the Methodenteil: the pool of the results page, UI/UX
    audit 2026-10-05); then done and open are those of the pool. shown: the conditions with the words of the page's
    language (a page that only shows them, the results page; UI/UX audit 2026-10-07, W110)."""
    items = {i.id: i for b in survey.blocks for i in b.items}
    pool = tuple(sources) != ("real",)
    own = ((counts or _from_responses(survey, only, until, sources))[0]
           if only or until is not None or pool else None)
    out = []
    for quota in of(survey):
        rows = []
        for cell in quota.cells:
            n = own[cell.id] if own else {"done": cell.done, "running": cell.running, "refused": cell.refused, "over": cell.over,
                                          "freed": cell.freed or 0}
            left_out = min((marked or {}).get(cell.id, 0), n["done"])
            n = {**n, "done": n["done"] - left_out}
            done = (n["done"] if pool else cell.done - min(left_out, cell.done))
            rows.append({"id": cell.id, "name": cell.name, "code": cell.code,
                         "conds": cell_text(survey, cell, lang, items, shown) if conds else "",
                         "target": cell.target, "done": n["done"], "running": n["running"],
                         "open": max(cell.target - done, 0), "refused": n["refused"], "over": n["over"],
                         "marked": left_out + n["freed"], "freed": n["freed"],
                         "state": state_of(quota, cell), "closed": cell.closed,
                         "share": min(n["done"] / cell.target, 1) if cell.target else 0,
                         "share_running": min(n["running"] / cell.target, 1) if cell.target else 0})
        total = {k: sum(r[k] for r in rows) for k in ("target", "done", "running", "open", "refused", "over", "marked", "freed")}
        grid = matrix(survey, quota, rows, lang, items, conds)
        node = node_of(survey, quota)
        ends = bool(ends_of(survey, quota)) or bool(node and is_legacy(node) and node["full"] == "end")
        out.append({"quota": quota, "mode": modes().get(quota.mode, quota.mode), "node": node, "ends": ends,
                    "cells": rows, "sum": total, "matrix": grid, "matrix_text": matrix_text(quota, grid) if grid else ""})
    return out


def report_table(survey, lang=None, conds=True, only=None, entries=None):
    """The report as rows of text: header, one row per cell, a sum per quota. entries: the `report`, if the
    caller has it at hand."""
    cols = [c for c in COLUMNS if conds or c != "Bedingungen"]
    table = [[COLUMNS_EN[c] for c in cols] if english() else list(cols)]
    for entry in report(survey, lang, only, conds) if entries is None else entries:
        quota = entry["quota"]
        for r in entry["cells"]:
            row = {"Quote": quota.name, "Teilquote": r["name"], "Code": r["code"], "Bedingungen": r["conds"], "Soll": r["target"],
                   "abgeschlossen": r["done"], "laufend": r["running"], "offen": r["open"], "abgewiesen": r["refused"],
                   "über Soll": r["over"], "Zustand": state_name(r["state"])}
            table.append([row[c] for c in cols])
        s = entry["sum"]
        row = {"Quote": quota.name, "Teilquote": de_en("Summe", "Total"), "Code": "", "Bedingungen": "", "Soll": s["target"],
               "abgeschlossen": s["done"], "laufend": s["running"], "offen": s["open"], "abgewiesen": s["refused"],
               "über Soll": s["over"], "Zustand": ""}
        table.append([row[c] for c in cols])
    return table


def totals(survey, only=None, counts=None):
    """The whole survey in the words of the report: started, completed, screen-out, quota full, not finished;
    with `only`, of that provider (counts: what `_from_responses` gave for it, if the caller has it at hand)."""
    wanted = (Response.COMPLETED, Response.SCREENOUT, Response.QUOTAFULL, Response.EXCLUDED, Response.ACTIVE)
    if only:
        by_status = (counts or _from_responses(survey, only))[1]
        real = {status: by_status.get(status, 0) for status in wanted}
    else:
        tally = survey.tally()
        real = {status: sum(n for (source, preview, s), n in tally.items() if source == "real" and not preview and s == status)
                for status in wanted}
    return [(de_en("begonnen", "started"), sum(real.values())), (de_en("abgeschlossen", "completed"), real[Response.COMPLETED]),
            ("Screen-out", real[Response.SCREENOUT]), (de_en("Quote voll", "quota full"), real[Response.QUOTAFULL]),
            *([(de_en("Ausschluss", "excluded"), real[Response.EXCLUDED])] if real[Response.EXCLUDED] else []),
            (de_en("nicht abgeschlossen", "not completed"), real[Response.ACTIVE])]


def report_text(survey, lang=None, separator="\t", conds=True, only=None, entries=None, sums=None):
    """The report as text for the clipboard (tabs: pastes as a table into mail and spreadsheets) or as CSV.
    entries, sums: the `report` and the `totals`, if the caller has them at hand; else the participations of a
    provider (`only`) are counted once for both."""
    import csv
    import io

    from app.models import utcnow

    if only and (entries is None or sums is None):
        counts = _from_responses(survey, only)
        entries = report(survey, lang, only, conds, counts) if entries is None else entries
        sums = totals(survey, only, counts) if sums is None else sums
    from app.export import safe_text

    class _Safe:  # formula protection like the other exports: quota and Teilquote names come from the team
        def __init__(self, writer):
            self.writer = writer

        def writerow(self, row):
            self.writer.writerow([safe_text(v) for v in row])

        def writerows(self, rows):
            for row in rows:
                self.writerow(row)

    out = io.StringIO()
    writer = _Safe(csv.writer(out, delimiter=separator, lineterminator="\n"))
    writer.writerow([de_en("Quotenstand: {title} ({code})", "Quota state: {title} ({code})", title=survey.title, code=survey.code)])
    from app.timeutil import fmt

    writer.writerow([de_en("Stand: {when} Uhr", "As of: {when}", when=fmt(utcnow(), lang="en" if english() else "de"))])
    if only:
        writer.writerow([de_en("Nur Teilnahmen mit {name} = {value}: abgeschlossen, laufend, abgewiesen und die Gesamtzahlen; "
                               "Soll, offen und Zustand gelten für die ganze Teilquote.",
                               "Only participations with {name} = {value}: completed, running, refused and the totals; "
                               "target, open and state apply to the whole sub-quota.", name=only[0], value=only[1])])
    writer.writerow([])
    writer.writerows(report_table(survey, lang, conds, only, entries))
    writer.writerow([])
    for label, n in totals(survey, only) if sums is None else sums:
        writer.writerow([label, n])
    return out.getvalue()
