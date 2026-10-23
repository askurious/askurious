"""Protection against overwriting each other's changes when several members edit a survey at once.

Every editor form carries `base`, a fingerprint of the part it edits (an item, a loop table, a settings
section, a flow node) as it was when the page was opened. On saving, the route compares it with the
current fingerprint: if someone saved in between, nothing is saved. Large forms (items, loop tables,
settings) are shown again with the person's own inputs, on trial and rolled back, together with who
changed the part and when; saving once more then deliberately replaces that version. Forms without
`base` (setup wizard, scripts, tests) are saved as before.

While an editor is open, the page reports itself every few seconds (EditMark.seen_at), so others who
open the same part see "Gerade ebenfalls geöffnet von ...".
"""
import hashlib
import json
from datetime import timedelta

from flask import g
from flask_login import current_user

from app.extensions import db, get_or_create
from app.models import EditMark, utcnow
from app.timeutil import to_local

PRESENT_SECONDS = 45  # the page reports every 15 s; a closed tab drops out after this
HEARTBEAT_SECONDS = 15
FRESH_SECONDS = 20  # a mark younger than this is not written again (two reports fit into PRESENT_SECONDS)


def fingerprint(*parts):
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def item_state(item):
    return fingerprint(item.code, item.answer_mode, item.config, item.texts)


def node_state(node):
    """A flow node without its children: moving blocks in or out is not a change of the node's form."""
    return fingerprint({k: v for k, v in (node or {}).items() if k != "children"})


def _periods(survey):
    """The retention periods without the note of the housekeeping (retention.DONE): writing it is no change of the
    settings form (code review 2026-10-07, K3)."""
    from app import retention

    return retention.of(survey)


def _panel(survey):
    """The entry settings without the names of the links per value: the table of links saves those itself, and a
    name typed there is no change of the form of parameters (panel.set_value_labels)."""
    cfg = survey.panel
    return {k: v for k, v in cfg.items() if k != "value_labels"} if cfg and "value_labels" in cfg else cfg


# the survey fields each settings form writes (app/surveys/)
SETTINGS = {
    "meta": lambda s: (s.title, s.project, s.description, s.research_question, s.default_language, s.languages,
                       s.allow_back, s.auto_advance, s.show_progress, s.language_choice, s.paradata, s.page_timing, s.focus_paste, s.focus_inputs, s.ai_bait, s.block_load, s.repeat_marker, s.erase_code, s.geo_ip, s.admin_hidden, s.tempo_hint, s.tempo_dose,
                       s.tempo_options, s.tempo_texts, s.keep_test_runs, _periods(s)),
    "image_style": lambda s: s.image_style,
    "theme": lambda s: s.theme,
    "panel": _panel,
    "end_texts": lambda s: s.end_texts,
    "interview": lambda s: s.interview,  # the settings of an interview guide (app/interview.py)
    # the tab "Zeitplan" of a diary study (app/diary.py): the schedule and which block belongs to which part
    "diary": lambda s: (s.sampling, sorted([b.id, (b.settings or {}).get("part")] for b in s.blocks)),
    # the page "Studienplan" (app/study_plan.py): hypotheses, exclusion rules, the research question (code review
    # 2026-10-15, I3); not a section of "Rückgängig" (SETTINGS_FIELDS)
    "plan": lambda s: (s.research_question, s.study_plan),
}


# the same sections by the names of their fields, for "Rückgängig" (app/undo.py puts them back one by one)
SETTINGS_FIELDS = {
    "meta": ("title", "project", "description", "research_question", "default_language", "languages", "allow_back",
             "auto_advance", "show_progress", "language_choice", "paradata", "page_timing", "focus_paste", "focus_inputs", "ai_bait", "block_load", "repeat_marker", "erase_code", "geo_ip", "admin_hidden", "tempo_hint",
             "tempo_dose", "tempo_options", "tempo_texts", "keep_test_runs", "retention"),
    "image_style": ("image_style",), "theme": ("theme",), "panel": ("panel",), "end_texts": ("end_texts",),
    "interview": ("interview",),
    "diary": ("sampling",),  # the parts of the blocks are in the blocks' own steps (undo "block:<id>")
}


def settings_state(survey, section):
    return fingerprint(SETTINGS[section](survey))


def board_state(board):
    """What the dashboard designer's form edits: the tiles, the name and from how many answers a tile shows numbers
    (code review 2026-10-15, I3). Key "board:<id>"."""
    return fingerprint(board.name, board.tiles, (board.settings or {}).get("min_tile"))


def base(key, current):
    """The fingerprint a form carries: after a conflict the one of the saved version, else the current one."""
    conflict = g.get("edit_conflict")
    return conflict["base"] if conflict and conflict["key"] == key else current


def conflict_for(key):
    conflict = g.get("edit_conflict")
    return conflict if conflict and conflict["key"] == key else None


OWN_SECONDS = 30  # how long after a person's own save a beacon sent while it was under way may build on it


def _after_own(survey, key):
    """The last save of this part is this person's own, a few seconds ago, and no AI agent saved after it: a
    beacon sent when the page was left while that save was still under way (`after_own`, app.js) builds on it,
    though it still carries the fingerprint from before (code review 2026-10-15, I1)."""
    last = db.session.scalar(
        db.select(EditMark).filter(EditMark.survey_id == survey.id, EditMark.key == key, EditMark.saved_at.is_not(None))
        .order_by(EditMark.saved_at.desc()).limit(1))
    if last is None or last.user_id != current_user.id:
        return False
    if (utcnow() - last.saved_at).total_seconds() > OWN_SECONDS:
        return False
    return _last_agent(survey, last.saved_at) is None


def lock(obj):
    """Hold the row of `obj` (an item, a board, the survey) until commit or rollback and read it anew: called at the
    start of a save, before the fingerprint is taken and `check` compares it. Two saves on the same base then run one
    after the other, and the later one sees the earlier one's change as a conflict instead of overwriting it; so does
    the beacon of a page being left while its own save is still under way (audit 2026-10-23, S-02). PostgreSQL:
    SELECT ... FOR NO KEY UPDATE (rows that refer to it by key may still be added). SQLite has no row locks: a write
    that changes nothing takes the database's one write lock first (as diary_access.lock_person)."""
    model = type(obj)
    if db.engine.dialect.name == "sqlite":
        db.session.execute(db.delete(EditMark).where(db.false()))
    return db.session.scalar(db.select(model).where(model.id == obj.id).with_for_update(key_share=True)
                             .execution_options(populate_existing=True))


def hold(obj):
    """Hold the row of `obj` until commit or rollback like `lock`, without reading it anew: for a part of a request
    that must not lose what it has changed on the object but not yet stored (a flush inside `no_autoflush`)."""
    model = type(obj)
    if obj.id is None:
        return
    if db.engine.dialect.name == "sqlite":
        db.session.execute(db.delete(EditMark).where(db.false()))
        return
    db.session.execute(db.select(model.id).where(model.id == obj.id).with_for_update(key_share=True))


def taken_codes(survey, but=()):
    """The variable names the questions of `survey` have, read from the database with the survey's row held until
    commit: two requests that pick or change a name at the same time (two members renaming two questions, adding
    two at once) run one after the other, and the later one sees the name the earlier one took, even where its own
    survey object was loaded before that. Questions of this request not yet stored and names changed
    here count too. `but`: ids of questions left out (the one being renamed)."""
    from sqlalchemy import inspect

    from app.models import Block, Item

    but = set(but or ())
    stored = set()
    if survey.id is not None:
        # no flush here: callers build questions and blocks step by step (import, setup wizard); what is not stored
        # yet is read from the objects below
        with db.session.no_autoflush:
            hold(survey)
            query = (db.select(Item.code, Item.id).join(Block, Item.block_id == Block.id)
                     .where(Block.survey_id == survey.id))
            stored = {code for code, iid in db.session.execute(query) if iid not in but}
    here = set()
    for block in survey.blocks:
        for item in block.items:
            state = inspect(item)
            if item.id in but and item.id is not None:
                continue
            if item.id is None or not state.persistent or state.modified:
                here.add(item.code)
    return stored | here


def check(survey, key, current, form):
    """Before applying a form: None if it may be saved, else the conflict (also kept in g for the page)."""
    sent = form.get("base")
    if not sent or sent == current:
        return None
    if form.get("after_own") == "1" and _after_own(survey, key):
        return None
    last = db.session.scalar(
        db.select(EditMark).filter(EditMark.survey_id == survey.id, EditMark.key == key,
                                   EditMark.user_id != current_user.id, EditMark.saved_at.is_not(None))
        .order_by(EditMark.saved_at.desc()).limit(1))
    who = (last.user.display_name if last.user.name else "Eine andere Person") if last else None  # app/names.py
    when = to_local(last.saved_at).strftime("%H:%M") if last else None
    agent = _last_agent(survey, last.saved_at if last else None)
    if agent is not None:  # an AI agent saved after the others (with this person's token or another's)
        who, when = agent
    who = who or "Jemand anderes"
    g.edit_conflict = {"key": key, "base": current, "who": who, "when": when}
    return g.edit_conflict


def show_again(render):
    """Render the page with the form's values applied on trial, then discard them."""
    try:
        return render()
    finally:
        db.session.rollback()


def _mark(survey_id, key):
    # two saves at the same moment (autosave of two fields, two tabs): the second keeps its save (audit 2026-10-10, C4)
    return get_or_create(EditMark, survey_id=survey_id, key=key, user_id=current_user.id)


def saved(survey, key):
    """Note who saved (part of the save's transaction)."""
    _mark(survey.id, key).saved_at = utcnow()


def known(survey, key):
    """The key names a part of this survey that an editor page can have open: "item:<id>", "loop:<node id>",
    "flow:<node id>", "board:<id>", "settings" or "settings:<section>". The heartbeat takes its key from the page, and anyone
    who may see the survey can send it: without this check every made-up key would become a row."""
    from app import flow
    from app.models import Block, Board, Item

    kind, _, ident = key.partition(":")
    if kind == "settings":
        return not ident or ident in SETTINGS
    if kind == "item":
        return ident.isascii() and ident.isdigit() and db.session.scalar(
            db.select(Item.id).join(Block, Item.block_id == Block.id)
            .filter(Item.id == int(ident), Block.survey_id == survey.id)) is not None
    if kind in ("loop", "flow"):
        return flow.find(flow.tree(survey), ident)[0] is not None
    if kind == "board":
        return ident.isascii() and ident.isdigit() and db.session.scalar(
            db.select(Board.id).filter(Board.id == int(ident), Board.survey_id == survey.id)) is not None
    return False


def seen(survey, key, here=True):
    """Note that this person has the part open (or has left it). A mark that is still fresh is left alone, so
    an open editor writes every second report only: each write takes the database's one write lock."""
    mark = _mark(survey.id, key)
    now = utcnow()
    if here and mark.seen_at is not None and (now - mark.seen_at).total_seconds() < FRESH_SECONDS:
        return
    mark.seen_at = now if here else None


def others_here(survey, key):
    """Who else has this part open, and the AI agents that changed the survey just now (agents_here): an agent with
    the person's own token is named to them as well."""
    since = utcnow() - timedelta(seconds=PRESENT_SECONDS)
    marks = db.session.scalars(
        db.select(EditMark).filter(EditMark.survey_id == survey.id, EditMark.key == key,
                                   EditMark.user_id != current_user.id, EditMark.seen_at >= since))
    from app import experimental

    agents = agents_here(survey) if experimental.enabled("agent", survey=survey) else []
    return sorted({m.user.display_name if m.user.name else "Eine andere Person" for m in marks}) + agents


AGENT_KEY = "agent:"  # EditMark.key of an AI agent's token ("agent:<token id>"), per survey (app/agent_tools.py)


def agent_seen(survey_id, user_id, token_id):
    """An AI agent changed this survey just now with the token of `user_id`: one mark per token and survey, kept
    apart from the person's own marks by its key."""
    now = utcnow()
    mark = get_or_create(EditMark, survey_id=survey_id, key=f"{AGENT_KEY}{token_id}", user_id=user_id)
    mark.seen_at = mark.saved_at = now


AGENT_PRESENT_SECONDS = 120  # an agent works in bursts of calls: it counts as "at work" this long after its last change


def _agent_label(name):
    from app.lang import de_en

    return de_en("KI-Agent (Token „{name}“)", "AI agent (token “{name}”)", name=name)


def agents_here(survey, seconds=AGENT_PRESENT_SECONDS):
    """The AI agents that changed this survey within `seconds`, as "KI-Agent (Token „…“)", for the presence line
    and the banner of the editor."""
    from app import agent_access

    since = utcnow() - timedelta(seconds=seconds)
    keys = db.session.scalars(db.select(EditMark.key).filter(
        EditMark.survey_id == survey.id, EditMark.key.startswith(AGENT_KEY), EditMark.seen_at >= since))
    ids = {int(k[len(AGENT_KEY):]) for k in keys if k[len(AGENT_KEY):].isdigit()}
    return sorted(_agent_label(name) for name in agent_access.names(ids).values())


def _last_agent(survey, after):
    """(label, "HH:MM") of the AI agent that saved in this survey last, if after `after` (None: any) and recent."""
    from app import agent_access

    since = utcnow() - timedelta(seconds=AGENT_PRESENT_SECONDS)
    mark = db.session.scalar(db.select(EditMark).filter(
        EditMark.survey_id == survey.id, EditMark.key.startswith(AGENT_KEY), EditMark.saved_at >= since)
        .order_by(EditMark.saved_at.desc()).limit(1))
    if mark is None or (after is not None and mark.saved_at <= after):
        return None
    ident = mark.key[len(AGENT_KEY):]
    name = agent_access.names({int(ident)}).get(int(ident)) if ident.isdigit() else None
    return (_agent_label(name), to_local(mark.saved_at).strftime("%H:%M")) if name else None
