"""The page engine: turns a questionnaire into pages and checks submitted pages.

Used by the researcher preview now and by the participant runtime (M4). Answer modes:
  required  -> error, the page cannot be left
  nudge     -> the first "Next" highlights missing answers, a second "Next" continues
  optional  -> no check
Soft warnings (ItemType.soft_warning, e.g. PSM prices out of order) behave like nudges in every mode.
Input validation (numbers, ranges, regex, consent ticks) always applies to given answers.
"""
from collections import ChainMap
from dataclasses import dataclass, field

from app.items.base import AnswerError
from app.ui_i18n import tr


def pages(survey):
    """[(block, [items])] in presentation order (randomization per response comes in M5)."""
    return [(block, items) for block in survey.blocks for items in block.pages()]


@dataclass
class PageResult:
    answers: dict = field(default_factory=dict)   # item_id -> parsed answer (valid ones)
    display: dict = field(default_factory=dict)   # item_id -> what to show again
    errors: dict = field(default_factory=dict)    # item_id -> message (blocking)
    nudges: dict = field(default_factory=dict)    # item_id -> message (soft)
    declined: bool = False

    @property
    def ok(self):
        return not self.errors and not self.nudges


UNREADABLE = (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError)


def _log_unreadable(item, error):
    from flask import current_app, has_app_context

    if has_app_context():
        # the kind of error only: the message of a ValueError or KeyError can repeat what the person typed
        current_app.logger.warning("Unreadable answer for item %s (%s): %s", item.id, item.type, type(error).__name__)


def _display(kind, item, form, meta):
    """What to show again after an error; nothing if even that cannot be read."""
    try:
        return kind.display_value(item, form, meta=meta)
    except UNREADABLE as e:
        _log_unreadable(item, e)
        return None


def _answer_meta(layout, position, response):
    """What the types read beside the form of a page: its plan in the layout and the participation's pid, the seed
    of what the page drew for the person (the IAT checks its order against it, audit 2026-10-12, N-12)."""
    # "_page": the entry itself, which ACBC compares by identity (items/acbc.py, stored_answer)
    page = layout["pages"][position]
    return {**page, "pid": builtin_variables(response)["pid"], "_page": page}


def evaluate(items, form, lang, nudge_acknowledged=False, meta=None, answers=None):
    """answers: the answers so far, for the types that read them (ItemType.uses_answers)."""
    result = PageResult()
    for item in items:
        kind = item.kind
        if not kind.collects_answer:
            continue
        try:
            answer = kind.parse_answer(item, form, lang, meta=meta, **({"answers": answers} if kind.uses_answers else {}))
        except AnswerError as e:
            result.errors[item.id] = str(e)
            result.display[item.id] = _display(kind, item, form, meta)
            # A required multi-row answer can be incomplete without any of its given values being invalid.
            # Read it once more as optional: persist only what passes the type's normal value validation.
            if item.required and item.type != "consent" and not kind.alone_on_page:
                try:
                    partial = kind.parse_answer(_AsOptional(item), form, lang, meta=meta,
                                                **({"answers": answers} if kind.uses_answers else {}))
                    if partial not in (None, "", [], {}):
                        result.answers[item.id] = partial
                except (AnswerError, *UNREADABLE):
                    pass
            continue
        except UNREADABLE as e:
            # a value no page of ours sends (forged, or mangled on the way): an error on the question, not a
            # server error for the whole page
            _log_unreadable(item, e)
            result.errors[item.id] = tr(lang, "Diese Eingabe konnte nicht gelesen werden. Bitte beantworten Sie die Frage noch einmal.",
                                        "This input could not be read. Please answer the question again.")
            result.display[item.id] = None
            continue
        result.answers[item.id] = answer
        result.display[item.id] = answer
        if item.type == "consent" and kind.declined(answer):
            result.declined = True
        if item.nudge and not nudge_acknowledged and kind.is_missing(item, answer):
            result.nudges[item.id] = (
                tr(lang, "Diese Frage ist noch offen. Sie können sie beantworten oder trotzdem weitergehen.", "This question is still open. You can answer it or continue anyway.")
            )
        if not nudge_acknowledged and item.id not in result.nudges:
            warning = kind.soft_warning(item, answer, lang)
            if warning:
                result.nudges[item.id] = warning
    if result.declined:
        # declining ends the survey; nothing else on the page matters
        result.errors, result.nudges = {}, {}
    return result


class _AsOptional:
    """An item seen as optional. "Back" and the language switch keep what is on the page without
    checking that it is complete: a half-filled required matrix must not be lost on the way."""

    required = False
    nudge = False

    def __init__(self, item):
        self._item = item

    def __getattr__(self, name):
        return getattr(self._item, name)


def answer_key(item_id, row=None):
    """Storage key of an answer: "12" normally, "12#r3ab" for pass `row` of a loop block."""
    return f"{item_id}#{row}" if row else str(item_id)


def load_answers(response):
    """Stored answers: int item id for normal items, "id#row" strings inside loops."""
    return {(int(k) if k.isdigit() else k): v for k, v in (response.answers or {}).items()}


def store_answers(response, answers, row=None):
    from app.extensions import db
    from app.models import Item

    merged = dict(response.answers or {})
    for k, v in answers.items():
        item = db.session.get(Item, k)
        key = answer_key(k, row)
        old = merged.get(key)
        merged[key] = item.kind.merge_answer(old, v) if item is not None and old is not None else v
    response.answers = merged


def conditional(item):
    """True if whether the question is shown depends on the person: a condition of its own ("Nur zeigen, wenn …",
    app/item_condition.py) or a type that decides it (ItemType.applies)."""
    from app import item_condition
    from app.items.base import ItemType

    return item_condition.of(item) is not None or type(item.kind).applies is not ItemType.applies


def applies(item, answers):
    """Is the question shown to a person with these answers so far: its own condition holds (app/item_condition.py)
    and its type shows it (ItemType.applies). Every place that asks goes through here, so both always count."""
    from app import item_condition

    return item_condition.holds(item, answers) and item.kind.applies(item, answers)


def row_view(answers, row=None):
    """The answers as a question in pass `row` of a loop sees them: those of its pass under the plain ids."""
    if not row:
        return answers
    tail = f"#{row}"
    own = {int(k[:-len(tail)]): v for k, v in answers.items()
           if isinstance(k, str) and k.endswith(tail) and k[:-len(tail)].isdigit()}
    return {**answers, **own} if own else answers


def present(items, answers, row=None):
    """The items of a page that this person sees: without the questions that do not apply to their answers so
    far (ItemType.applies, e.g. a constant sum whose total comes from an answer that is missing)."""
    if not any(conditional(i) for i in items):
        return items
    view = row_view(answers, row)
    return [i for i in items if not conditional(i) or applies(i, view)]


def auto_next_ok(page_items, meta=None):
    """No navigation buttons: every answer on the page completes it by itself (item option
    "auto_next"); never on a conjoint explanation page, which asks nothing, nor on the MaxDiff
    anchoring question (tick boxes: no single click completes it)."""
    collecting = [i for i in page_items if i.kind.collects_answer]
    meta = meta or {}
    return bool(collecting and not meta.get("explain") and not meta.get("anchor")
                and all(i.kind.auto_next(i) for i in collecting))


def auto_advance_ok(survey, page_items):
    """Continue automatically only if every answer on the page is a one-click answer."""
    collecting = [i for i in page_items if i.kind.collects_answer]
    return bool(survey.auto_advance and collecting and all(i.kind.one_click(i) for i in collecting))


# --- per-response layout (randomization decided once, on the server) ---------------


# what a flow node says without changing who sees which page: texts, names and completion links, and the labels
# of the factors and levels of a vignette loop ("texts", app/vignettes.py: its factors, levels, plan and decks count
# by their ids). They may change while the survey runs (translation page, loop page), and a layout drawn before must
# stay valid then
_WORDING = ("text", "name", "link", "link_mode", "links", "texts")
# the wording as the signature knew it until 2026-10-05: the labels of a vignette loop counted as structure
_WORDING_BEFORE = ("text", "name", "link", "link_mode", "links")


def _structure(node, wording=_WORDING):
    """A flow node without its wording: a loop row counts by its id, not by the texts of its cells."""
    out = {k: v for k, v in node.items() if k not in wording and k not in ("children", "rows")}
    if "rows" in node:
        out["rows"] = [r.get("id") for r in node["rows"]]
    if "cases" in node:  # the triggers of an end: their texts and links are wording too
        out["cases"] = [{k: v for k, v in case.items() if k not in wording} for case in node["cases"]]
    if "children" in node:
        out["children"] = [_structure(c, wording) for c in node["children"]]
    return out


def _digest(shape):
    import hashlib
    import json

    return hashlib.sha1(json.dumps(shape, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _pages_shape(survey):
    return [[[i.id for i in items] for b in survey.blocks for items in b.pages()],
            [(b.id, b.randomize_items) for b in survey.blocks]]


def _signature(survey):
    """Changes whenever the structure of the flow or of the pages changes (only possible in draft). A corrected
    text does not change it: the participations under way keep their layout, their page and what was drawn."""
    from app import flow

    from app.data_revision import stamp as revision
    from app.extensions import db

    if not hasattr(_signature, "kept"):
        _signature.kept = {}
    from app.models import Survey, Block, Item

    changing_structure = any(isinstance(o, (Survey, Block, Item))
                             for o in (*db.session.dirty, *db.session.new, *db.session.deleted))
    if survey.status == survey.DRAFT or survey.id is None or changing_structure:
        return _digest([_structure(flow.tree(survey))] + _pages_shape(survey))
    key = (survey.id, survey.uid, survey.qversion, revision(survey, structure=True))
    if key not in _signature.kept:
        # Bulk/Core SQL can update the database without refreshing an already loaded relationship. Cache only
        # the fresh structure, once per changed stand; ordinary warm participant requests need none of this.
        from sqlalchemy.orm import selectinload
        from sqlalchemy.orm.attributes import set_committed_value

        db.session.refresh(survey, ["flow"])
        blocks = db.session.scalars(db.select(Block).where(Block.survey_id == survey.id).order_by(Block.position)
                                    .options(selectinload(Block.items)).execution_options(populate_existing=True)).all()
        set_committed_value(survey, "blocks", blocks)
        if len(_signature.kept) >= 256:
            _signature.kept.clear()
        _signature.kept[key] = _digest([_structure(flow.tree(survey))] + _pages_shape(survey))
    return _signature.kept[key]


def _signature_vignette_texts(survey):
    """The signature as it was built until 2026-10-05: with the labels of vignette loops. Layouts drawn then carry
    it; they are still valid and get today's signature the next time they are read (ensure_layout)."""
    from app import flow

    return _digest([_structure(flow.tree(survey), _WORDING_BEFORE)] + _pages_shape(survey))


def _signature_with_texts(survey):
    """The signature as it was built before: over the whole flow, texts included. Layouts drawn then carry it;
    they are still valid and get today's signature the next time they are read (ensure_layout)."""
    from app import flow

    return _digest([flow.tree(survey)] + _pages_shape(survey))


def build_layout(survey, rng, separate=True, keep=None, only=None):
    """Presentation plan for one response.

    pages: [{"block": id, "items": [...], "cond": [branch ids]} | {"end": node id, "cond": [...]}]
    stmt:  {item id: [statement ids]}   (randomized Likert statements)
    conditions: {randomizer id: [chosen child ids in presentation order]}
    keep: the layout this participation had before the questionnaire was changed (ensure_layout): what it drew
    stays where it still exists (conditions, loop rows, orders), only what is new is drawn.
    only: the ids of the blocks this participation shows (an entry of a diary study, diary.only_blocks): the flow
    keeps its branches and randomisers, every other block and every marker (end, quota, web service, external
    section, separated section) is left out.
    """
    from app import flow

    flow.normalize(survey)
    blocks = {b.id: b for b in survey.blocks}
    within = {}
    entries, decisions, chosen_rows = flow.plan(survey, rng, within, keep=keep)
    if only is not None:
        entries = [e for e in entries if e[0] == "block" and e[1] in only]

    layout = {"pages": [], "stmt": {}, "conditions": decisions, "cj": {}, "loops": chosen_rows,
              "sig": _signature(survey)}
    if within["cond"]:
        # randomizers behind a branch condition: drawn now for everybody, but they count (data set, balance) only
        # for those who reach them (layout "reached", see reach); their entries name them under "rand"
        layout["rand_in"] = sorted(within["cond"])
    pages = layout["pages"]
    for kind, ref, conds, ctx in entries:
        if kind == "split":
            # Real participants: the main part ends here; the rest runs as a separate session
            # (see participate.py). Previews and dummy runs simply continue.
            if separate:
                pages.append({"split": ref, "cond": conds})
                layout["after_split"] = []
                pages = layout["after_split"]
            continue
        if kind in ("end", "quota", "ext", "ws"):  # markers: an end, a quota calculation (app/quotas.py), a web
            # service called by the server (app/webservice.py), or an
            # external section (app/external.py), where the person leaves and comes back
            pages.append({kind: ref, "cond": conds})
            continue
        block = blocks[ref]
        if ctx:
            # one pass of a loop: this block's pages for the current table row
            for items in block.pages():
                ids = [i.id for i in items]
                if block.randomize_items:
                    rng.shuffle(ids)
                pages.append({"block": block.id, "items": ids, "cond": conds, "row": ctx["row"],
                              "iter": ctx["iter"], "iters": ctx["iters"], "loop": ctx["loop"]})
                for item in items:  # a new order in every pass
                    order = _random_elements(item)
                    if order:
                        rng.shuffle(order)
                        layout["stmt"][answer_key(item.id, ctx["row"])] = order
            continue
        for items in split_alone(block.pages()):
            expanded = items[0].kind.expand_pages(survey, items[0], rng) if len(items) == 1 else None
            if expanded is not None:
                # an item with pages of its own, e.g. a conjoint: one page per task
                state, specs = expanded
                layout["cj"][str(items[0].id)] = state
                for spec in specs:
                    pages.append({"block": block.id, "items": [items[0].id], "cond": conds, **spec})
                continue
            ids = [i.id for i in items]
            if block.randomize_items:
                rng.shuffle(ids)
            pages.append({"block": block.id, "items": ids, "cond": conds})
            for item in items:
                order = _random_elements(item)
                if order:
                    rng.shuffle(order)
                    layout["stmt"][str(item.id)] = order
    if within["cond"]:
        _name_randomizers(survey, layout, set(within["cond"]))
    if keep:
        _keep_orders(layout, keep)
    return layout


def _keep_orders(layout, old):
    """The orders this participation saw before (ensure_layout, audit 2026-10-10, T-2): the statements, rows and
    options of an element, and the questions of a page shown in random order, where they are still the same."""
    stmt = layout["stmt"]
    for key, order in (old.get("stmt") or {}).items():
        if key in stmt and isinstance(order, list) and sorted(map(str, order)) == sorted(map(str, stmt[key])):
            stmt[key] = list(order)

    def where(spec):
        return (spec.get("block"), spec.get("row"), spec.get("task"), spec.get("holdout"), frozenset(spec.get("items") or ()))

    before = {where(p): p["items"] for p in (old.get("pages") or []) + (old.get("after_split") or [])
              if isinstance(p, dict) and p.get("items")}
    for spec in layout["pages"] + layout.get("after_split", []):
        if spec.get("items") and where(spec) in before:
            spec["items"] = list(before[where(spec)])


def _name_randomizers(survey, layout, behind):
    """Each entry of the layout gets the randomizers behind a branch condition it lies in ("rand"): passing it means
    reaching them (reach). A block, an end, a quota calculation and an external section have one place in the flow."""
    from app import flow

    places = {}

    def rec(node, rands):
        for child in node.get("children", []):
            if child["t"] == "block":
                places[("block", child["block"])] = rands
            elif child["t"] in ("end", "quota", "ext", "ws"):
                places[(child["t"], child["id"])] = rands
            if child["t"] in flow.GROUPS:
                rec(child, rands + [child["id"]] if child["t"] == "rand" and child["id"] in behind else rands)

    rec(flow.tree(survey), [])
    for spec in layout["pages"] + layout.get("after_split", []):
        kind = next((k for k in ("block", "end", "quota", "ext", "ws") if k in spec), None)
        rands = places.get((kind, spec[kind])) if kind else None
        if rands:
            spec["rand"] = rands


def reach(response, spec):
    """The participation passes this entry of its layout (a page shown, an end or a quota calculation passed): the
    randomizers behind a branch condition it lies in count from now on (flow.reached_conditions)."""
    rands = spec.get("rand")
    layout = response.layout or {}
    new = [r for r in rands or () if r not in (layout.get("reached") or [])]
    if not new:
        return
    response.layout = {**layout, "reached": [*(layout.get("reached") or []), *new]}
    if getattr(response, "id", None) and getattr(response, "source", None) == "real" and not response.is_preview:
        from app import balance

        balance.note_reached(response.survey_id, layout.get("conditions") or {}, new)


def _random_elements(item):
    """Ids of the elements an item shows in a random order per response (statements, rows, options,
    pairs), or None."""
    c = item.config
    key = {"likert": "statements", "slider": "rows", "constant_sum": "options", "semantic": "pairs"}.get(item.type)
    if key and c.get("randomize"):
        return [e["id"] for e in c.get(key, [])]
    if item.type == "assign" and c.get("randomize", True):
        return [e["id"] for e in c.get("elements", [])]
    if item.type in ("ranking", "position2d", "card_sort", "q_sort") and c.get("randomize", True):
        return [o["id"] for o in c.get("options", [])]
    return None


def split_alone(pages):
    """Implicit page breaks around items that need a page of their own (ItemType.alone_on_page;
    conjoint: one page per task; assignment task: timed, navigation hidden until done). Questions
    before and after them on the same page get pages of their own."""
    out = []
    for items in pages:
        current = []
        for item in items:
            if item.kind.alone_on_page:
                if current:
                    out.append(current)
                out.append([item])
                current = []
            else:
                current.append(item)
        if current:
            out.append(current)
    return out


def layout_matches(survey, layout, signature=None):
    """signature: _signature(survey) if the caller has it already."""
    if not layout:
        return False
    sig = layout.get("sig")
    # the older signatures only for layouts drawn before them (each walks the whole flow)
    return sig == (signature or _signature(survey)) or sig in (_signature_vignette_texts(survey),
                                                               _signature_with_texts(survey))


def _split_layout(survey, rng):
    """The separated section anew, after the questionnaire was changed: its own pages only, never the main part."""
    full = build_layout(survey, rng, separate=True)
    return {"pages": full.get("after_split", []), "stmt": {}, "conditions": {}, "cj": {}, "loops": {}, "sig": full["sig"]}


def ensure_layout(survey, response, rng=None):
    import random

    from app.models import new_pid

    separated = not hasattr(response, "pid")  # the separated section (SplitSession)
    if not separated and not response.pid:
        response.pid = new_pid(survey.code)

    signature = _signature(survey)  # once per step, not twice (audit 2026-10-06, P11)
    if not layout_matches(survey, response.layout, signature):
        old = response.layout
        if old is not None and not separated:
            response.qversion = survey.qversion  # restarts on the changed questionnaire
        if separated:
            # its answers are stored apart from the main part: it never becomes the whole questionnaire
            layout = _split_layout(survey, rng or random.SystemRandom())
        else:
            # a real participation keeps what it drew before where it still exists: its condition, the rows (or
            # deck) of its loops and its orders; its answers would otherwise mix two conditions (audit 2026-10-10, T-2)
            keep = old if old and getattr(response, "source", None) == "real" else None
            from app import diary

            layout = build_layout(survey, rng or random.SystemRandom(), separate=not response.is_preview, keep=keep,
                                  only=diary.only_blocks(survey, response))
        if old and getattr(response, "source", None) == "real":
            # the choice tasks already answered were shown in the design version drawn before: keep it (the
            # design is locked once a real participation has it), or the answers would point at other profiles
            kept = {k: v for k, v in (old.get("cj") or {}).items() if k in (layout.get("cj") or {})}
            if kept:
                layout = {**layout, "cj": {**layout["cj"], **kept}}
        if not separated:
            # random displays drawn from the pid from now on; a layout drawn before keeps the id (models.display_seed)
            seed = "pid" if old is None else old.get("seed")
            if seed:
                layout = {**layout, "seed": seed}
        response.layout = layout
        response.position = 0
        if old is not None and getattr(response, "id", None) and not response.is_preview:
            from app import balance

            balance.forget(survey.id)  # a stored participation got a new layout: counted afresh
            if not separated:
                # it starts again from the first page: counted in its paradata (export column "relayout"), logged, and
                # said on the page it sees next (participate.page reads `relaid`)
                from flask import current_app

                seen = response.paradata or {}
                response.paradata = {**seen, "_relayout": int(seen.get("_relayout") or 0) + 1}
                response.relaid = True
                current_app.logger.info("Participation %s of survey %s starts again on questionnaire version %s "
                                        "(layout rebuilt after a change)", response.id, survey.id, response.qversion)
    elif response.layout.get("sig") != signature:
        response.layout = {**response.layout, "sig": signature}  # drawn under the signature with texts
    return response.layout


def is_marker(spec):
    return "end" in spec or "split" in spec or "quota" in spec or "ext" in spec or "ws" in spec


def page_entries(layout):
    """Indexes of real pages (not end or split markers)."""
    return [n for n, p in enumerate(layout["pages"]) if not is_marker(p)]


def applicable(survey, layout, index, answers, ahead=False, conds=None):
    """The entry applies: its flow conditions hold and, for a page marked "dyn" (see
    ItemType.expand_pages), its item wants to show it given the answers so far.
    ahead: for a page still to come, where the answers of the page in between may yet decide whether its
    questions apply (ItemType.applies): it counts as coming, so the button there says "Next", not "Submit".
    conds: flow.conditions_by_id(survey) (page_conditions), when the caller asks for several entries; otherwise read
    here, and only for an entry with conditions: it walks the whole flow (code review 2026-10-15, F7)."""
    from app import flow

    from app import tempo

    spec = layout["pages"][index]
    from app import quality

    # a condition on the pages sent too fast, or on the attention checks failed: those before this page
    answers = quality.at(tempo.at(answers, index), index)
    if spec.get("cond"):
        conds = flow.conditions_by_id(survey) if conds is None else conds
        if not all(flow.cond_holds(conds, c, answers, survey) for c in spec["cond"]):
            return False
    if spec.get("dyn"):
        from app.extensions import db
        from app.models import Item

        item = db.session.get(Item, spec["items"][0])
        # a question with pages of its own (adaptive tasks) and a condition of its own: none of them without it
        from app import item_condition

        return item is None or (item_condition.holds(item, row_view(answers, spec.get("row")))
                                and item.kind.page_applies(item, spec, answers))
    from app.extensions import db
    from app.models import Item

    if ahead:
        return True
    items = [i for i in (db.session.get(Item, i) for i in spec.get("items", [])) if i is not None]
    # a page on which no question applies to this person (ItemType.applies) is skipped like one behind a condition
    return not items or bool(present(items, answers, spec.get("row")))


def page_conditions(survey, layout):
    """The flow's conditions for applicable over several entries of `layout`: read once, and not at all when no
    entry has one (code review 2026-10-15, F7)."""
    from app import flow

    return flow.conditions_by_id(survey) if any(p.get("cond") for p in layout.get("pages") or []) else {}


def next_index(survey, layout, index, answers):
    """Next applicable entry after `index` (may be an end marker), or None at the end."""
    conds = page_conditions(survey, layout)
    for n in range(index + 1, len(layout["pages"])):
        if applicable(survey, layout, n, answers, conds=conds):
            return n
    return None


def previous_index(survey, layout, index, answers, barrier=(), ext_barrier=()):
    """The page "Back" leads to, or None. `barrier`: quota calculations this participation has passed; the answers
    before them are fixed (a way back would let someone change the answer that decided their cell). `ext_barrier`:
    external sections this browser came back from without having left from them (the return link opened in another
    browser, participate.come_back): the pages before them are not this browser's to see."""
    conds = page_conditions(survey, layout)
    for n in range(index - 1, -1, -1):
        spec = layout["pages"][n]
        if spec.get("quota") in barrier and "quota" in spec and applicable(survey, layout, n, answers, conds=conds):
            return None
        if "ext" in spec and spec["ext"] in ext_barrier:
            return None
        if not is_marker(spec) and applicable(survey, layout, n, answers, conds=conds):
            return n
    return None


def page_at(survey, response, position):
    """(block, [items]) for a page of this response's layout."""
    from app.extensions import db
    from app.models import Block, Item

    spec = response.layout["pages"][position]
    items = [db.session.get(Item, i) for i in spec["items"]]
    return db.session.get(Block, spec["block"]), [i for i in items if i is not None]


def drop_skipped_answers(survey, response, extra=None, keep_ahead=False):
    """On every step forward and at the end: remove answers to pages the answers so far skip, e.g. a
    follow-up question answered before the participant went back and changed the filter question. At the
    end only pages up to the final position count (a screen-out ends earlier); on the way (keep_ahead) the
    answers on later pages stay as long as their pages still apply: the participant may have gone back.
    Answers outside the layout stay. `extra`: values the
    conditions may test besides the answers (URL parameters)."""
    from app import flow, quality, tempo

    layout = response.layout or {}
    pages = layout.get("pages") or []
    last = min(response.position or 0, len(pages) - 1)
    conds = page_conditions(survey, layout)
    keyed = [(n, [answer_key(i, spec.get("row")) for i in spec.get("items", [])])
             for n, spec in enumerate(pages) if not is_marker(spec)]
    in_layout = {k for _, keys in keyed for k in keys}
    from app.extensions import db
    from app.models import Item

    # questions that are shown or not by the answers so far (ItemType.applies): {answer key: (item, row)}
    own = {}
    for spec in pages:
        for i in ([] if is_marker(spec) else spec.get("items", [])):
            item = db.session.get(Item, i)
            if item is not None and conditional(item):
                own[answer_key(i, spec.get("row"))] = (item, spec.get("row"))
    stored = dict(response.answers or {})
    while True:  # a removed answer can switch off further pages
        answers = {**{(int(k) if k.isdigit() else k): v for k, v in stored.items()}, **(extra or {})}
        kept = set()
        for n, keys in keyed:
            if (n <= last or keep_ahead) and all(flow.cond_holds(conds, c, quality.at(tempo.at(answers, n), n), survey) for c in pages[n].get("cond", [])):
                kept.update(k for k in keys if k not in own or applies(own[k][0], row_view(answers, own[k][1])))
        drop = [k for k in stored if k in in_layout and k not in kept]
        if not drop:
            break
        for k in drop:
            del stored[k]
    if stored != (response.answers or {}):
        response.answers = stored


def builtin_variables(response, survey=None, lang=None):
    """Placeholders every survey can use in texts and redirects (see items.base.BUILTIN_VARIABLES): {{pid}},
    {{lang}} (the language of the participation; `lang`: the one the page is shown in) and, with the survey,
    {{survey}} (its code) and what each randomizer drew for this person under its variable name ({{cond1}})."""
    out = {"pid": getattr(response, "pid", None) or "", "lang": lang or getattr(response, "language", None) or ""}
    if survey is not None:
        from app import flow

        out["survey"] = survey.code or ""
        out.update({k: v for k, v in flow.branch_values(survey, response).items() if k not in out})
        from app import webservice

        # what the web services of the flow gave this participation ({{ws1_temp}}, app/webservice.py)
        out.update({k: v for k, v in webservice.variables_of(survey, response).items() if k not in out})
    return out


def statement_orders(response, row=None):
    """{item id: element ids in the order shown}; inside a loop pass (`row`) the orders drawn for that pass."""
    out = {}
    for k, v in ((response.layout or {}).get("stmt") or {}).items():
        item_id, _, of_row = k.partition("#")
        if not of_row or of_row == row:
            out[int(item_id)] = v
    return out


# --- one step of the questionnaire --------------------------------------------------------


def _person_mark(pid):
    """Eight characters that tie a page to its participation without naming it: a keyed digest of the participant ID
    (the server's secret key), so a page of a participation that this browser let go of ("Neue Teilnahme an diesem
    Gerät") never fits the next one at the same position (audit 2026-10-23, DS-01)."""
    import hashlib
    import hmac

    from flask import current_app, has_app_context

    secret = str(current_app.secret_key or "") if has_app_context() else ""
    return hmac.new(secret.encode(), str(pid or "").encode(), hashlib.sha256).hexdigest()[:8]


def _page_name(response, position, revision):
    serial = (getattr(response, "layout", None) or {}).get("page_revisions", {}).get(str(position), 0)
    suffix = f".{serial}" if revision and serial else ""
    if hasattr(response, "pid"):
        return f"r{position}{suffix}"
    return f"x{str(getattr(response, 'id', None) or '')[:8]}-{position}{suffix}"


def page_token(response, position, *, revision=True):
    """What a rendered page carries back in its form (field "pos"): the page it was rendered for.
    The separated section (SplitSession, no pid) counts its own pages, hence the prefix, and its token carries the
    start of the session's random id: a page of a session that has ended (its answers stored) never fits a new one,
    when the browser sends it again with the cookie from before (code review 2026-10-07, T2). A participation's token
    carries a keyed digest of its participant ID for the same reason: a tab still open from the person before on this
    device never writes into the next participation (audit 2026-10-23, DS-01)."""
    name = _page_name(response, position, revision)
    return f"{name}-{_person_mark(response.pid)}" if hasattr(response, "pid") else name


def keep_key(response, position):
    """Under which name participant.js keeps the inputs of a page in the tab while they are not sent (a dropped
    connection on "Weiter"): the participation, as a digest that says nothing about it, and the page. The script
    drops whatever the tab holds under another name, so nothing passes to another page or participation."""
    import hashlib

    from flask import current_app, has_app_context

    secret = str(current_app.secret_key or "") if has_app_context() else ""
    # the participant ID, also before the participation is stored (the consent page); the separated section its own id
    who = getattr(response, "pid", None) or f"{type(response).__name__}:{getattr(response, 'id', None) or ''}"
    return hashlib.sha256(f"{secret}|{who}".encode()).hexdigest()[:16] + "-" + _page_name(response, position, False)


@dataclass
class Step:
    """What to do after handling a request: redirect, or render a page with this context."""

    redirect: bool = False
    finished: bool = False
    context: dict = field(default_factory=dict)
    # at an external section (app/external.py): {"node": its flow node, "go": the person leaves now (the page of
    # the node was sent), "token": the page token for its form}
    external: dict | None = None
    # the page sent was not the current one (sent twice, an older tab): it was dropped, the redirect leads to the
    # current page, which says so (participate.page)
    stale: bool = False


def step(survey, response, form, lang, method, **options):
    """See _step. A recording sent with the page (items/voice.py) is written only when the page goes on; whatever
    else happened to the page (an error, a nudge, "Back", the language switch) drops it again."""
    from app.items import voice

    before = getattr(response, "position", None) if method == "POST" else None
    result = _step(survey, response, form, lang, method, **options)
    voice.discard(response)
    if before is not None and response.position != before and isinstance(response.layout, dict):
        # the page was left: a form of it rendered before (another tab, the browser's history) no longer fits when
        # the person comes back to it, so it cannot overwrite the newer answers (audit 2026-10-17, D-01). Staying on
        # the page (an error, a nudge, "Mehr Zeit") keeps its token.
        revisions = dict(response.layout.get("page_revisions") or {})
        revisions[str(before)] = revisions.get(str(before), 0) + 1
        response.layout = {**response.layout, "page_revisions": revisions}
        if response.position > before:
            # pages left forward: an ID only begun with does not lock its owner out (panel._only_begun, audit
            # 2026-10-18, BIZ-05); an error, "Back" or the same page sent again count nothing
            response.layout = {**response.layout, "sent": int(response.layout.get("sent") or 0) + 1}
    return result


def render_page(survey, response, position, lang, params=None, ext_barrier=()):
    """The context of the page at `position` as a GET would show it, without showing it: no clock started, no
    randomiser reached, nothing noted as seen (quiet). For the further pages of a block loaded at once
    (app/blockload.py). None if the entry is no page or does not apply to this person."""
    layout = response.layout or {}
    pages = layout.get("pages") or []
    if not 0 <= position < len(pages) or is_marker(pages[position]):
        return None
    from app import geoip, quality, tempo
    from app import quotas as quota_mod

    view = {**{f"p:{k}": v for k, v in (params or {}).items()}, **quota_mod.view(survey, response),
            **quality.flow_view(survey, response), **tempo.flow_view(survey, response), **geoip.flow_view(survey, response)}
    if not applicable(survey, layout, position, {**load_answers(response), **view}):
        return None
    kept = response.position
    response.position = position
    try:
        result = _step(survey, response, {}, lang, "GET", params=params, ext_barrier=ext_barrier, quiet=True)
    finally:
        response.position = kept
    return result.context or None


def _step(survey, response, form, lang, method, validate=True, params=None, quota_full=(), quota_tally=None, geo=None,
          ext_barrier=(), on_post=None, form_lang=None, catch_up=False, paper=False, quiet=False, hold=False):
    """Handle GET (show the current page) or POST (Back/Next) for a response. Shared by
    the researcher preview and the participant runtime, so both behave identically.
    validate=False (preview only) records valid answers but lets every page pass.
    params: URL parameters of this participant (for {{piping}} and branches; see panel.py).
    quota_full (preview only): ids of quota calculations (and ends "Quote voll") to try out as full. quota_tally (dummy data only): the
    count the dummies keep among themselves (quotas.dummy_tally). geo (preview only): (country, state) to try out
    instead of looking the address up (app/geoip.py). ext_barrier: external sections "Back" does not lead before
    (previous_index). on_post: called once for a POST of the current page, before its answers are taken (the
    participant runtime notes paradata there; a page sent twice or from an older tab notes nothing). form_lang: the
    language the page that was sent was shown in, for reading its numbers when the language switch sends it along
    (default: lang). catch_up: only walk from the current position over the markers (quota calculations, ends) to
    the next page, as a GET does, without showing it (participate.page, the first page stored). paper: a paper sheet
    taken over (app/paper.py): no page times, no speed check, no address looked up, external sections passed.
    quiet: only the context of the current page, nothing noted (render_page). hold: a page sent while the survey is
    open for editing again (participate._keep_halted): its valid answers are kept as "Back" keeps them, and the
    participation stays on the page."""
    from app import external, flow, lifecycle
    from app import quotas as quota_mod
    from app.models import Response, display_seed, utcnow

    if response.status != Response.ACTIVE and response.layout:
        # ended: nothing of it changes any more, also not after the questionnaire was changed (a new layout
        # would give it conditions and orders the person never saw)
        return Step(finished=True)
    layout = dict(ensure_layout(survey, response))
    if response.status != Response.ACTIVE:
        return Step(finished=True)
    if not layout["pages"] and not hasattr(response, "pid"):
        response.status = Response.COMPLETED  # a separated section that no longer has pages
        return Step(finished=True)
    params = params or {}
    # branches may test URL parameters and what the quota calculations decided (quotas.view)
    from app import geoip, quality, tempo

    # ... and how many attention checks the answers so far failed (quality.flow_view), and which pages were
    # sent too fast (tempo.flow_view)
    param_view = {**{f"p:{k}": v for k, v in params.items()}, **quota_mod.view(survey, response),
                  **quality.flow_view(survey, response), **tempo.flow_view(survey, response),
                  **geoip.flow_view(survey, response)}  # ... and country and state estimated from the IP address
    answers = {**load_answers(response), **param_view}

    def finish(status):
        response.layout = {**response.layout, "answer_drafts": {}}
        response.status = status
        drop_skipped_answers(survey, response, param_view)
        external.forget_params(response)  # the URL parameters held while outside (none kept after the end)
        if status == Response.COMPLETED:
            response.completed_at = utcnow()  # before settle: quality assurance may judge the completion (speeder)
            if getattr(response, "enrolment_id", None):  # a diary entry: the server's own time of arrival (M1)
                response.received_at = response.completed_at
            # a reservation that ran out while the person was away, also when the completion comes with a GET (back
            # from an external section as the last element); after a POST it is taken back already
            quota_mod.reclaim(survey, response)
        # reserved places: completed, or free again (also when quality assurance marks the completion and the
        # survey says marked ones fill no place, quotas.settle)
        quota_mod.settle(response, status == Response.COMPLETED, survey)
        if not response.is_preview and getattr(response, "source", "real") == "real":
            from app import survey_invites

            survey_invites.ended(survey, commit=False)  # an entry of an invitation list: "teilgenommen", no reminders
        if status == Response.COMPLETED:
            if not response.is_preview:
                lifecycle.close_if_sample_complete(survey)
        return Step(redirect=True)

    def web_service(index):
        # a web service: this server calls it once, the person goes on at once (app/webservice.py). Not for a
        # participation that is not stored yet (just opening the link: crawlers, link previews), and not before the
        # consent if it passes on answers or URL parameters: it is called when the person goes forward from a later
        # page (code audit 2026-10-08, W5)
        from app import webservice

        node, _ = flow.find(flow.tree(survey), layout["pages"][index]["ws"])
        if node is None or webservice.done(response, node["id"]):
            return
        if getattr(response, "id", None) is None and getattr(response, "source", "") != "dummy":
            return
        consent = survey.consent_item if webservice.private(node) else None
        if consent is not None and consent.id not in answers:
            return
        from app import piping
        from app.items.base import Typed

        lang = getattr(response, "language", None) or survey.default_language
        known = {**{k: Typed(v) for k, v in builtin_variables(response, survey, lang).items()},
                 **{k: Typed(v) for k, v in params.items()}, "pid": builtin_variables(response)["pid"]}
        webservice.run(survey, response, node, ChainMap(known, piping.values(survey, answers, lang)))

    def go_forward(from_index):
        from app.items import voice

        voice.commit()  # the recordings of the page that is left forward (see step)
        for m in range(min(from_index, len(layout["pages"]) - 1) + 1):
            # web services passed before they could be called (see web_service)
            if "ws" in layout["pages"][m] and applicable(survey, layout, m, answers):
                web_service(m)
        n = next_index(survey, layout, from_index, answers)
        while n is not None and any(k in layout["pages"][n] for k in ("quota", "end", "ext", "ws")):
            spec = layout["pages"][n]
            reach(response, spec)
            if "ws" in spec:
                web_service(n)
                n = next_index(survey, layout, n, answers)
                continue
            if "ext" in spec:
                # an external section: the person stops here until the return link brings them back
                node, _ = flow.find(flow.tree(survey), spec["ext"])
                if node is not None and external.usable(node) and not external.passed(response, node["id"]):
                    response.position = n
                    return Step(redirect=True)
                n = next_index(survey, layout, n, answers)
                continue
            node, _ = flow.find(flow.tree(survey), spec.get("quota") or spec["end"])
            simulate = node is not None and node["id"] in quota_full
            if "end" in spec:
                # an end ends those for whom one of its triggers holds; everybody else passes it. The trigger
                # gives the status: screenout, quotafull, excluded
                case = quota_mod.end_case(survey, response, node, answers, simulate, quota_tally) if node else {}
                if paper and case is not None and flow.case_status(case) == Response.QUOTAFULL:
                    case = None  # a paper sheet is filled in already: a full quota cannot stop it (E13)
                if case is not None:
                    response.position = n
                    return finish(flow.case_status(case))
                n = next_index(survey, layout, n, answers)
                continue
            # a quota calculation: the cell is found and a place taken, once. It ends nobody (only the
            # "Quotenprüfung" of a survey started before does: a full quota, or nobody's cell)
            outcome = quota_mod.pass_check(survey, response, node, answers, simulate, quota_tally) if node else None
            if node and quota_mod.is_legacy(node) and ((outcome == "full" and node["full"] == "end" and not paper)
                                                       or outcome == "none"):
                response.position = n
                return finish(Response.QUOTAFULL if outcome == "full" else Response.SCREENOUT)
            decided = quota_mod.view(survey, response)
            param_view.update(decided)
            answers.update(decided)
            n = next_index(survey, layout, n, answers)
        if n is None:
            return finish(Response.COMPLETED)
        if "split" in layout["pages"][n]:
            response.position = n
            return finish(Response.COMPLETED)  # the separated section follows (participate.py)
        response.position = n
        return Step(redirect=True)

    if not layout.get("pages"):  # nothing to show (a diary module without blocks): it ends at once (audit 2026-10-11, M9)
        return finish(Response.COMPLETED)
    position = min(response.position or 0, len(layout["pages"]) - 1)
    if paper and "ext" in layout["pages"][position]:
        return go_forward(position)  # a web app cannot be visited from paper
    if "ext" in layout["pages"][position] and applicable(survey, layout, position, answers):
        node, _ = flow.find(flow.tree(survey), layout["pages"][position]["ext"])
        if node is not None and external.usable(node) and not external.passed(response, node["id"]):
            sent = method == "POST" and form.get("pos") in (None, page_token(response, position))
            if sent and on_post is not None:
                on_post()
            if sent and getattr(response, "is_preview", False) and form.get("ext_back"):
                # a test run or a simulated person: back at once, with the values given on the page
                external.simulate(response, node, external.form_values(form), form.get("ext_seconds") or 0, utcnow())
                return go_forward(position)
            if sent and not getattr(response, "is_preview", False):
                response.last_active_at = utcnow()
                external.leave(response, node, response.last_active_at, params)  # holds the URL parameters
            return Step(external={"node": node, "go": sent and not getattr(response, "is_preview", False),
                                  "token": page_token(response, position)})
    # the current entry may not apply (e.g. the start, or answers changed after going back)
    if is_marker(layout["pages"][position]) or not applicable(survey, layout, position, answers):
        result = go_forward(position - 1 if position == 0 else position)
        if response.status != Response.ACTIVE or response.position != position or catch_up:
            return result
    if catch_up or (quiet and method != "GET"):
        return Step()
    if not quiet:
        reach(response, layout["pages"][position])  # this page is shown (or sent): its randomizers are reached
    block, all_items = page_at(survey, response, position)
    pages = page_entries(layout)
    errors, nudges, nudge_shown, tempo_note, hold_note = {}, {}, False, None, None
    spec = layout["pages"][position]
    from app import pagetime

    # a minimum or maximum time for the pages of this block (app/pagetime.py), and how long this one is on screen
    times = pagetime.page_times(response, block, all_items, position) if not paper else None
    on_screen = pagetime.waited(response, position, utcnow()) if times and not quiet else None
    row = spec.get("row")
    page_items = present(all_items, answers, row)  # without the questions that do not apply to this person
    from app import feedback_lock

    # answers fixed by feedback already seen (app/feedback_lock.py): what the page sends for them is dropped
    fixed = feedback_lock.frozen(response, page_items, row) if not paper else set()
    from app import loops

    from app import piping
    from app.items.base import Typed

    # answers to earlier pages ({{code}}, app/piping.py); a URL parameter or loop column of the same name wins
    # (the pre-launch check reports such names)
    # ... and of this page: kept current by the page's script while the person answers (piping.live)
    # the answers are worked out only for the placeholders a text of the page asks for (piping.Values): below
    # everything else, which wins over them as before
    variables = ChainMap({**piping.live(page_items, answers, lang, row),
                          # the language, the survey and what the randomizers drew; a URL parameter of the same name
                          # wins, {{pid}} is always the participation id
                          **{k: Typed(v) for k, v in builtin_variables(response, survey, lang).items()},
                          **{k: Typed(v) for k, v in params.items()}, "pid": builtin_variables(response)["pid"],
                          **(loops.variables(block, row, lang) if row else {})},
                         piping.values(survey, answers, lang, row, skip={i.id for i in all_items}))

    def page_answers():
        """All answers; inside a loop, the block's items show the answers of the current pass
        (also those on earlier pages of the pass, e.g. for a live comparison)."""
        view = dict(row_view(answers, row))
        if row:
            for item in block.items:
                view[item.id] = answers.get(answer_key(item.id, row))
        drafts = layout.get("answer_drafts", {})
        for item in page_items:
            key = answer_key(item.id, row)
            if key in drafts:
                view[item.id] = drafts[key]
        return view

    # no page times for the separated section: nothing about it is kept but its answers (it has no paradata)
    timing = getattr(survey, "page_timing", False) and hasattr(response, "pid") and not paper
    from app import paradata
    if method == "POST" and form.get("pos") not in (None, page_token(response, position)):
        # a page sent twice (double click, a pending automatic continue) or an older page from the browser
        # history or another tab: its answers belong to another page, so show the current one instead
        return Step(redirect=True, stale=True)
    if method == "POST" and on_post is not None:
        on_post()
    if method == "POST":
        response.last_active_at = utcnow()
        quota_mod.reclaim(survey, response)  # a reservation that ran out while the person was away
        # "Mehr Zeit" taken on this page (pagetime.extend): its cumulative extension count is retained, also for time_up
        if times and not hold and form.get("time_more") and pagetime.extend(
                response, position, times, paradata.page_key(all_items, spec) if timing else str(position),
                form.get("time_more")):
            times = pagetime.page_times(response, block, all_items, position)
        if form.get("nav") == "time_more" and not hold:
            return Step(redirect=True)  # persist only the timer; never validate or discard in-progress answers
        leaving = form.get("nav") == "back" and survey.allow_back and not hold
        # "Back" from a page shown while it was still allowed (switched off in the meantime): kept like "Back", but
        # the same page again, never taken as "Next" (audit data storage 2026-10-21, A3)
        staying = hold or bool(form.get("set_lang")) or (form.get("nav") == "back" and not survey.allow_back)
        drafts = dict(layout.get("answer_drafts", {}))
        for item in page_items:
            drafts.pop(answer_key(item.id, row), None)
        layout["answer_drafts"] = drafts
        response.layout = {**response.layout, "answer_drafts": drafts}
        if timing:
            page_key = paradata.page_key(all_items, spec)
            # "Back" and a change of language leave the page without its first "Next": it is checked later
            first_seconds = paradata.page_left(response, position, page_key, response.last_active_at,
                                               forward=not (leaving or staying))
            if page_key:  # for the speed index: which questions, statements and options this page showed
                paradata.note_version(response, page_key, paradata.shown_version(all_items, page_items,
                                                                                 row_view(answers, row)))
        if leaving or staying:
            # keep what is valid on this page without checking that it is complete; not the consent (never
            # decided by "Back") nor tasks with pages of their own (choice tasks, the timed assignment),
            # which restart or merge per page
            keep = [_AsOptional(i) for i in page_items if i.type != "consent" and not i.kind.alone_on_page]
            # numbers as the page that was sent writes them: "1.000" typed in German is 1000, also when the switch
            # leads to English
            back = evaluate(keep, form, form_lang or lang, nudge_acknowledged=True,
                            meta=_answer_meta(layout, position, response), answers=row_view(answers, row))
            # Incomplete multiple selections are drafts, never analysis answers. Only
            # the minimum is relaxed; maximum, exclusivity and free-text checks remain.
            for item in keep:
                if item.type == "multiple_choice" and item.id in back.errors and item.id not in fixed:
                    try:
                        drafts[answer_key(item.id, row)] = item.kind.parse_answer(
                            item, form, form_lang or lang, meta={"draft": True}, answers=row_view(answers, row))
                    except (AnswerError, *UNREADABLE):
                        pass
            layout["answer_drafts"] = drafts
            response.layout = {**response.layout, "answer_drafts": drafts}
            feedback_lock.drop(back, fixed)
            store_answers(response, back.answers, row)
            if back.errors:
                # an answer changed into something invalid is not kept, and neither is the answer from before (it
                # is not what the person last gave): the question is open again
                stored = dict(response.answers or {})
                for item_id in back.errors:
                    stored.pop(answer_key(item_id, row), None)
                response.answers = stored
            if leaving and not paper:  # not on a change of language: nothing was sent on, so nothing gets fixed (T-7)
                feedback_lock.note_sent(response, page_items, row, load_answers(response))
            param_view.update(quality.flow_view(survey, response))
            if not leaving:
                return Step(redirect=True)  # the same page again, in the other language (participate.page)
            answers = {**load_answers(response), **param_view}
            prev = previous_index(survey, layout, position, answers, quota_mod.passed(survey, response), ext_barrier)
            if prev is not None:
                response.position = prev
                pagetime.left(response)
            return Step(redirect=True)
        if pagetime.time_up(times, on_screen, form):
            # the timer of the page ran out: on with what is answered, nothing is asked for
            # with the answers so far, like "Next" and "Back": a personal total, carried options and conditions on
            # rows read them (ItemType.uses_answers)
            given = evaluate([_AsOptional(i) for i in page_items], form, form_lang or lang, nudge_acknowledged=True,
                             meta=_answer_meta(layout, position, response), answers=row_view(answers, row))
            feedback_lock.drop(given, fixed)
            store_answers(response, given.answers, row)
            if given.errors:
                # an answer changed into something invalid: not kept, nor the one from before (as with "Back",
                # audit 2026-10-10, T-4)
                stored = dict(response.answers or {})
                for item_id in given.errors:
                    stored.pop(answer_key(item_id, row), None)
                response.answers = stored
            if not paper:
                feedback_lock.note_sent(response, page_items, row, load_answers(response))
            pagetime.note_time_up(response, paradata.page_key(all_items, spec) if timing else str(position))
            pagetime.left(response)
            param_view.update(quality.flow_view(survey, response))
            drop_skipped_answers(survey, response, param_view, keep_ahead=True)
            answers = {**load_answers(response), **param_view}
            return go_forward(position)
        # numbers as the page that was sent writes them, also when another tab switched the language (T-2)
        result = evaluate(page_items, form, form_lang or lang, nudge_acknowledged=bool(form.get("nudge_ack")),
                          meta=_answer_meta(layout, position, response), answers=row_view(answers, row))
        feedback_lock.drop(result, fixed)
        if not validate:
            result.errors, result.nudges = {}, {}
            for item in page_items:
                # consent without the ticks still counts as "continue" when checks are off
                if item.type == "consent" and form.get(f"i{item.id}") == "1":
                    result.answers[item.id] = "1"
        store_answers(response, result.answers, row)
        if result.errors and not paper:
            # an answer changed into something invalid: not kept, nor the one from before (it is not what the person
            # last gave), as with "Back", the language switch and the timer (audit 2026-10-12, T-3). Not the consent
            # nor tasks with pages of their own, whose earlier parts stay
            dropped = ({i.id for i in page_items if i.type != "consent" and not i.kind.alone_on_page}
                       & (set(result.errors) - set(result.answers)))
            if dropped:
                stored = dict(response.answers or {})
                for item_id in dropped:
                    stored.pop(answer_key(item_id, row), None)
                response.answers = stored
        if not result.declined and not paper:
            feedback_lock.note_sent(response, page_items, row, load_answers(response))
        param_view.update(quality.flow_view(survey, response))
        if not result.declined and not paper:
            geoip.note(survey, response, geo)  # once, with the page that agrees to take part
            param_view.update(geoip.flow_view(survey, response))
        answers = {**load_answers(response), **param_view}
        if result.declined:
            # Declining also discards anything answered before the consent (e.g. a screening)
            response.answers = {str(i.id): result.answers[i.id] for i in page_items
                                if i.type == "consent" and i.id in result.answers}
            response.layout = {**response.layout, "answer_drafts": {}}
            response.status = Response.DECLINED
            response.completed_at = None
            return Step(redirect=True)
        if validate and pagetime.too_early(times, on_screen):
            # "Next" before the minimum time of the page was over (the page holds its button until then)
            hold_note = pagetime.message(lang, pagetime.remaining(times, on_screen)["min"])
        elif timing:
            # the first "Next" on this page came faster than it can be read: the page once more, with a note
            fast = tempo.check(survey, response, block, page_items, page_key, first_seconds, lang, variables, spec, position,
                               answers=row_view(answers, row))
            if fast:
                tempo_note = tempo.message(lang, survey, fast)
            param_view.update(tempo.flow_view(survey, response))
            answers = {**load_answers(response), **param_view}
        if result.ok and not tempo_note and not hold_note:
            pagetime.left(response)
            # answers to pages the answers so far skip (given before going back and changing a filter question)
            # must not steer the way: they could end the participation at a branch the person never took
            drop_skipped_answers(survey, response, param_view, keep_ahead=True)
            answers = {**load_answers(response), **param_view}
            return go_forward(position)
        shown = page_answers()
        kinds = {i.id: i.kind for i in page_items}
        for k, v in result.display.items():
            # the failed input over what is stored (conjoint-like items keep their earlier parts)
            shown[k] = kinds[k].merge_answer(shown[k], v) if k in kinds and shown.get(k) is not None else v
        errors, nudges, nudge_shown = result.errors, result.nudges, bool(result.nudges)
        from app.items.base import Live

        # the page comes again: its texts show what was stored of its own answers just now
        variables.update({k: v for k, v in piping.live(page_items, answers, lang, row).items() if isinstance(variables.get(k), Live)})
    else:
        shown = page_answers()

    asset_vars = loops.variables(block, row, survey.default_language) if row else {}
    if not paper and not quiet:
        feedback_lock.note_shown(response, page_items, row, answers)  # the feedback on this page is seen now
    conds = page_conditions(survey, layout)
    remaining = [n for n in pages if n > position and applicable(survey, layout, n, answers, ahead=True, conds=conds)]
    if layout.get("after_split"):
        remaining = remaining or ["split"]  # the separated section still follows
    if timing and not quiet:
        paradata.page_shown(response, position, utcnow())
    hold = None
    if times:
        if not quiet:
            pagetime.shown(response, position, utcnow())
        hold = {**pagetime.remaining(times, None if quiet else pagetime.waited(response, position, utcnow())),
                **pagetime.offer(times)}
        if not validate:
            hold["min"] = 0  # a test run without checks is not held either
    prev = layout["pages"][position - 1] if position > 0 else None
    block_start = (prev is None or is_marker(prev) or prev.get("block") != spec.get("block")
                   or prev.get("iter") != spec.get("iter"))  # first page of this block (or loop pass)
    can_back = previous_index(survey, layout, position, answers, quota_mod.passed(survey, response),
                              ext_barrier) is not None
    return Step(context=dict(
        block=block, page_items=page_items, page=pages.index(position) + 1, page_count=len(pages), can_back=can_back,
        block_start=block_start, page_token=page_token(response, position), keep_key=keep_key(response, position),
        is_last=not remaining, answers=shown, errors=errors,
        # a page that holds "Next" does not go on by itself with the last answer
        auto_advance=auto_advance_ok(survey, page_items) and not tempo_note and not hold_note and not (hold and hold["min"]),
        auto_next=auto_next_ok(page_items, spec) and not errors and not tempo_note and not hold,  # the countdown stands in the navigation
        nudges=nudges, nudge_shown=nudge_shown, tempo_note=tempo_note or hold_note, hold=hold,
        variables=variables, asset_vars=asset_vars,
        stmt_orders=statement_orders(response, row), page_meta=spec,
        cj_state={int(k): v for k, v in (layout.get("cj") or {}).items()},
        crowd_seed=display_seed(response),  # per participant: crowd sample, sides, option order (models.display_seed)
        # answers fixed by feedback: read-only on the page; and those a feedback element will fix (a note says so)
        locked_items=feedback_lock.frozen(response, page_items, row) if not paper else set(),
        lock_hint=feedback_lock.hinted(response, page_items, row, survey) if not paper else set(),
    ))
