"""Undo and redo for a draft questionnaire: the last changes of each person, kept on the server.

A change is what a click does (adding, deleting, moving, a tick) or one saved form, never a typed character.
The editor's routes bracket their change with `begin()` and `done()`; `done` compares the questionnaire
before and after and stores the parts that differ as one EditStep, each with its state before and after:

    item:<id>    a question as it is (type, variable name, answer mode, config, texts)
    order:<id>   the questions of a block, in order
    block:<id>   a block's names and ticks
    flow         the flow tree (it also holds the order of the blocks)
    quota:<id>   a quota with its Teilquoten (what is counted and how, never the counts themselves)
    settings:<section>   a section of the settings page (project, image style, theme, links, end texts), and
                 "settings:quality": the data pool, speeder threshold and marking of the page "Qualitätssicherung";
                 "settings:weighting": the weighting of the results (page "Quoten")
    board:<id>   a dashboard: its name, tiles and settings (not its links)
    plan:layout  where the variables stand in the drawing of the model (study plan; moved on the results page)
    linked:<key> a linked element the questions use (app/linked.py): its Fassung, template and name. A step that
                 made one ("verknüpfbar gemacht") takes it away again when it is taken back, and redoing makes it anew.

Undo puts the states before back, redo the states after. Both first check that every part is still as the
step left it: if someone else (or a page that keeps no steps yet, e.g. translation, images, quotas) changed
one of them since, nothing is put back and the step leaves the history, so nobody's work is overwritten.

A step can only be taken back or redone by someone who could make it now: a step that holds a section of the
settings only admins change (project facts with retention, deletion code, IP region and paradata; participation
links; end texts) or the key facts of the consent needs the role "Admin" (`need`), also from the person who made
it while still an admin. The arrows do not offer it to anyone else (history). What only admins change by now is
checked again when the step is taken back or redone (need_now): a dashboard with a valid link, the data pool while
such a link exists, the quota setting of the quality assurance in the field.

Kept per person and survey: the last KEEP steps, DAYS days (like the trash). Starting the survey clears the
history; afterwards it keeps only what can still change in the field (FIELD_PARTS: settings, quality assurance,
weighting, dashboards, quota steering, text corrections), since quality assurance and dashboards are used mostly
then (UI audit 2026-10-04, R16). Deleted questions are also in the trash (app/trash.py): undo takes
them out of it again, with their images, redo puts them back into it.

The name of a step is shown in the language of the person who looks at it (decision E9 of the concept
"Mehrsprachige Oberfläche", 2026-10-06): a caller names it with both(de, en, **values); the German name stays in
EditStep.label as before, the English one goes into data["label_en"], and label(step) reads the one of the page.
A step named with a plain text (older steps, callers not converted yet) shows that text in both languages.
"""
import copy
import inspect
import json
from datetime import timedelta
from types import SimpleNamespace

from flask_login import current_user
from sqlalchemy.orm import defer
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.lang import de_en, english
from app.models import Asset, Block, EditStep, Item, Quota, QuotaCell, utcnow
from app.timeutil import fmt, to_local

KEEP = 20
# what a running or closed survey may still change, and so take back: the structure is fixed from the start on
FIELD_PARTS = ("settings:", "board:", "quota:", "item:", "plan:layout", "access:", "tcassign:")
ACCESS_FIELDS = ("may_record", "may_transcribe", "may_code")  # the rights of an interviewer link (app/interviewer.py)
DAYS = 7
MERGE_MINUTES = 10  # a change that continues the step before it (done(merge=...)) joins it for this long
SHOWN = 5  # the list shows this many at first, the rest unfolds


class Label(str):
    """The name of a step in German and English (both()): reads as the text in the language of the page, and
    done() stores both."""

    def __new__(cls, de, en):
        self = super().__new__(cls, en if english() else de)
        self.de, self.en = de, en
        return self

    def __add__(self, other):  # both("Ablauf: …") + both(", … hineingelegt") stays in both languages
        if isinstance(other, Label):
            return Label(self.de + other.de, self.en + other.en)
        return Label(self.de + other, self.en + other) if isinstance(other, str) else NotImplemented

    def __radd__(self, other):
        return Label(other + self.de, other + self.en) if isinstance(other, str) else NotImplemented


def named(obj, attr="label"):
    """A text attribute of a class in both languages, as a Label where the class writes it with app.lang.Both
    (the label of an element type: kind=undo.named(item.kind)); else the attribute as it reads."""
    from app.lang import Both

    static = inspect.getattr_static(obj, attr, None)
    return Label(static.de, static.en) if isinstance(static, Both) else getattr(obj, attr)


def both(de, en, **values):
    """The name of a step in both languages, written like de_en(): both("„{code}“ gelöscht", "“{code}” deleted",
    code=item.code). A value that is itself a Label goes into each language as its own text."""
    if values:
        de = de.format(**{k: v.de if isinstance(v, Label) else v for k, v in values.items()})
        en = en.format(**{k: v.en if isinstance(v, Label) else v for k, v in values.items()})
    return Label(de, en)


def label(step):
    """The name of `step` in the language of the page: the English one where the step has it (history() reads it
    without the rest of the step's data), else the stored text (German, or the language of a caller that passed
    a plain text)."""
    if english():
        en = step.__dict__.get("label_en")
        if en is None and "data" in step.__dict__:
            en = (step.data or {}).get("label_en")
        if en:
            return en
    return step.label


def _names(name):
    """(text for EditStep.label, English text or None) of what a caller passed to done()."""
    if isinstance(name, Label):
        return name.de[:200], name.en[:200]
    return str(name)[:200], None


class Refused(Exception):
    """The step cannot be taken back or redone; str() is the message for the person."""


def _plain(value):
    """Whole numbers as integers: a form gives 25.0 where the stored config says 25. The database sees no change
    in that (the values are equal) and keeps 25, so the two must not count as different here either."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _dump(value):
    """Canonical JSON, as the value reads once it has been through the database (keys are strings there:
    a config with number keys would otherwise sort differently before and after saving)."""
    return json.dumps(_plain(json.loads(json.dumps(value, default=str))), sort_keys=True, ensure_ascii=False)


def _state(survey):
    """{part: canonical JSON} of the questionnaire as it is now."""
    parts = {"flow": survey.flow}
    for b in survey.blocks:
        parts[f"block:{b.id}"] = {"titles": b.titles or {}, "show_title": bool(b.show_title),
                                 "randomize_items": bool(b.randomize_items), "settings": b.settings or {}}
        rows = sorted(b.items, key=lambda i: (i.position, i.id or 0))  # the list itself may still be in the old order
        parts[f"order:{b.id}"] = [i.id for i in rows]
        for i in rows:
            parts[f"item:{i.id}"] = {"type": i.type, "code": i.code, "answer_mode": i.answer_mode,
                                    "config": i.config or {}, "texts": i.texts or {}}
    for q in survey.quotas:
        parts[f"quota:{q.id}"] = {
            **{f: getattr(q, f) for f in QUOTA_FIELDS},
            "cells": [{"id": c.id, **{f: getattr(c, f) for f in CELL_FIELDS}} for c in sorted(q.cells, key=lambda c: (c.position, c.id or 0))]}
    from app import edit_guard

    for section, fields in edit_guard.SETTINGS_FIELDS.items():
        parts[f"settings:{section}"] = {f: getattr(survey, f) for f in fields}
    parts["settings:quality"] = {f: getattr(survey, f) for f in QUALITY_FIELDS}
    parts["settings:weighting"] = {"weighting": survey.weighting}  # page "Quoten", section "Gewichtung" (app/weights.py)
    # the arrangement of the drawing of the model (study plan, results page): only where the variables stand
    parts["plan:layout"] = ((survey.study_plan or {}).get("layout") if isinstance(survey.study_plan, dict) else None) or {}
    for b in survey.boards:
        parts[f"board:{b.id}"] = {f: getattr(b, f) for f in BOARD_FIELDS}
    for c in _elements(survey):
        parts[f"linked:{c.key}"] = {f: getattr(c, f) for f in LINKED_FIELDS}
    if survey.interview is not None:  # an interview guide: rights of its interviewer links, who codes in an analysis
        parts.update(_interviewer_parts(survey))
    return {k: _dump(v) for k, v in parts.items()}


def _interviewer_parts(survey):
    """access:<id> the rights of an interviewer link; tcassign:<analysis id> the links assigned to code there."""
    from app.models import CodingMember, CodingProject, InterviewerAccess

    parts = {}
    for a in db.session.scalars(db.select(InterviewerAccess).filter_by(survey_id=survey.id)):
        parts[f"access:{a.id}"] = {f: getattr(a, f) for f in ACCESS_FIELDS}
    projects = db.session.scalars(db.select(CodingProject).filter_by(survey_id=survey.id)).all()
    if projects:
        rows = db.session.execute(db.select(CodingMember.project_id, CodingMember.access_id).where(
            CodingMember.project_id.in_([p.id for p in projects]), CodingMember.kind == CodingMember.INTERVIEWER)).all()
        for p in projects:
            parts[f"tcassign:{p.id}"] = {"mode": (p.settings or {}).get("interviewers"),
                                         "ids": sorted(aid for pid, aid in rows if pid == p.id and aid)}
    return parts


def _apply_interviewers(survey, target):
    """Put the rights of interviewer links and the assignments to analyses back (parts access: and tcassign:)."""
    from app.models import CodingMember, CodingProject, InterviewerAccess

    for key, state in target.items():
        if key.startswith("access:") and state is not None:
            access = db.session.get(InterviewerAccess, int(key.partition(":")[2]))
            if access is not None and access.survey_id == survey.id:
                for f in ACCESS_FIELDS:
                    setattr(access, f, state[f])
        if key.startswith("tcassign:") and state is not None:
            pid = int(key.partition(":")[2])
            project = db.session.get(CodingProject, pid)
            if project is None or project.survey_id != survey.id:
                continue
            settings = {k: v for k, v in (project.settings or {}).items() if k != "interviewers"}
            project.settings = {**settings, **({"interviewers": state["mode"]} if state["mode"] else {})}
            rows = db.session.scalars(db.select(CodingMember).filter_by(project_id=pid, kind=CodingMember.INTERVIEWER)).all()
            for row in rows:
                if row.access_id not in state["ids"]:
                    db.session.delete(row)
            have = {row.access_id for row in rows}
            for aid in state["ids"]:
                if aid not in have:
                    db.session.add(CodingMember(project_id=pid, kind=CodingMember.INTERVIEWER, access_id=aid,
                                                added_by_id=current_user.id, added_by_name=(current_user.name or "")[:200]))


def _elements(survey):
    """The linked elements the questions of the survey use (app/linked.py)."""
    from app import linked
    from app.models import Construct

    keys = {linked.key_of(i) for b in survey.blocks for i in b.items} - {None}
    if not keys:
        return []
    return list(db.session.scalars(db.select(Construct).filter(Construct.key.in_(keys), Construct.status == linked.STATUS)))


# what a quota is, without what it has counted (QuotaCell.running, done, refused, over)
QUOTA_FIELDS = ("code", "name", "mode", "tolerance", "tolerance_pct", "no_cell", "hold_minutes", "count_at", "pick", "position")
CELL_FIELDS = ("code", "name", "conds", "target", "closed", "position")
# the page "Qualitätssicherung" (surveys.quality), and on the page "Quoten" whether marked participations fill a place
QUALITY_FIELDS = ("data_pool", "speeder_pct", "speeder_rule", "speeder_rsi", "quality_rules", "quota_drop_marked")
BOARD_FIELDS = ("name", "tiles", "settings", "position")  # a dashboard without its links (app/boards/)
LINKED_FIELDS = ("seed_version", "spec", "name", "visibility", "created_by_id")  # a linked element (Construct)


def _normalized(survey):
    """The state with the flow made consistent first, so that the editor's own tidying on opening a page
    (flow.normalize) is never a difference."""
    from app import flow

    db.session.flush()
    flow.normalize(survey)
    return _state(survey)


class _Recorder:
    def __init__(self, survey, layout=False):
        self.survey = survey
        self.layout = layout  # the arrangement of the model's drawing is a step only where it is moved (begin)
        self.started = utcnow()
        self.before = _normalized(survey) if current_user.is_authenticated else None

    def done(self, label, page="editor", item=None, block=None, node=None, restored=False, merge=None):
        """Store what changed since begin() as one step (nothing changed: no step). The caller commits.
        label: the step's name, both(de, en) to show it in each person's language (or a plain text).
        page/item/block/node say where the change shows, for landing there after undo. restored: the questions
        that appeared came out of the trash, they were not new. merge: a key for changes that belong together
        (one visit to a text field, one run of the AI translation): a change with the key of the person's last
        step continues that step instead of making a new one."""
        if self.before is None:
            return None
        after = _normalized(self.survey)
        parts = {k: [json.loads(self.before[k]) if k in self.before else None,
                     json.loads(after[k]) if k in after else None]
                 for k in sorted(set(self.before) | set(after)) if self.before.get(k) != after.get(k)}
        if not self.layout:
            parts.pop("plan:layout", None)
        if not self.survey.is_editable:  # in the field: only what may change there (the rest cannot have changed)
            parts = {k: v for k, v in parts.items() if k.startswith(FIELD_PARTS)}
        if not parts:
            return None
        fresh = [] if restored else [int(k[5:]) for k, (old, _) in parts.items() if k.startswith("item:") and old is None]
        # linked elements this step made (not those a question joined or came back to): taking it back removes them
        made = [c.key for c in _elements(self.survey) if f"linked:{c.key}" in parts and parts[f"linked:{c.key}"][0] is None
                and c.created_at is not None and c.created_at >= self.started]
        agent = _agent()
        if agent is not None:  # made by an AI agent with the person's token (app/agent_tools.py): the history says so
            label = both("KI-Agent (Token „{name}“): ", "AI agent (token “{name}”): ", name=agent["name"]) + label
            merge = None
        if merge and self._continue(merge, label, parts, fresh):
            return None
        de, en = _names(label)
        step = EditStep(survey_id=self.survey.id, user_id=current_user.id, label=de,
                        data={"parts": parts, "fresh": fresh, "page": page, "item": item, "block": block, "node": node,
                              "merge": merge, **({"made": made} if made else {}), **({"label_en": en} if en else {}),
                              **({"agent": agent["id"]} if agent is not None else {})})
        step.need = _need_of(step.data)
        db.session.add(step)
        db.session.flush()
        _prune(self.survey, current_user.id, step)
        return step

    def _continue(self, merge, label, parts, fresh):
        """Join `parts` to the person's last step if it has the same merge key, is recent, nothing was taken back
        since and every part it shares with this change is still as that step left it. True if joined."""
        last = db.session.scalar(db.select(EditStep).filter_by(survey_id=self.survey.id, user_id=current_user.id)
                                 .order_by(EditStep.id.desc()).limit(1))
        if (last is None or last.undone or last.data.get("merge") != merge
                or last.created_at < utcnow() - timedelta(minutes=MERGE_MINUTES)):
            return False
        old = dict(last.data.get("parts", {}))
        if any(k in old and _dump(old[k][1]) != _dump(states[0]) for k, states in parts.items()):
            return False
        for k, (before, after) in parts.items():
            old[k] = [old[k][0] if k in old else before, after]
        old = {k: states for k, states in old.items() if _dump(states[0]) != _dump(states[1])}
        if not old:  # back where the step began: nothing is left to take back
            db.session.delete(last)
            return True
        de, en = _names(label)
        data = {k: v for k, v in last.data.items() if k != "label_en"}
        last.data = {**data, "parts": old, "fresh": sorted(set(last.data.get("fresh", [])) | set(fresh)),
                     **({"label_en": en} if en else {})}
        last.label = de
        last.need = _need_of(last.data)
        return True


def _agent():
    """The AI agent this change is made by ({"id": token id, "name"}), or None for a person at the editor."""
    from flask import g, has_app_context

    return g.get("agent") if has_app_context() else None


def _need_of(data):
    """The role taking back or redoing a step with this `data` needs: "admin" if it holds a settings section
    whose route only admins use (surveys.section_role), or was made by such a route (surveys.ADMIN_STEP_NODES),
    else "editor"."""
    from app.surveys import ADMIN_STEP_NODES, section_role

    if data.get("page") == "settings" and data.get("node") in ADMIN_STEP_NODES:
        return "admin"
    if any(key.startswith("settings:") and section_role(key.partition(":")[2]) == "admin" for key in data.get("parts", {})):
        return "admin"
    return "editor"


def need(step):
    """The role `step` needs (EditStep.need; for a step from before that column read from its data)."""
    return step.need or _need_of(step.data or {})


def need_now(survey, step):
    """The role taking back or redoing `step` needs now: need(step), and "admin" where the step would change what
    only admins change by now (security audit 2026-10-06, S9): a dashboard with a valid link (boards.may_change),
    the data pool while a dashboard of the survey has one (surveys.pool_frozen), or whether marked participations
    fill a quota place outside a draft (quota_views.quota_marked)."""
    from app import boards

    parts = step.data.get("parts", {})
    with_link = {b.id for b in survey.boards if boards.has_link(b)}
    if any(k.startswith("board:") and int(k.partition(":")[2]) in with_link for k in parts):
        return "admin"
    before, after = ((s or {}) for s in parts.get("settings:quality") or [None, None])
    if with_link and _dump(before.get("data_pool")) != _dump(after.get("data_pool")):
        return "admin"
    if not survey.is_editable and bool(before.get("quota_drop_marked")) != bool(after.get("quota_drop_marked")):
        return "admin"
    return need(step)


def begin(survey, layout=False):
    """Call before a change; `.done(label)` after it, before the commit. layout: the change moves variables of the
    drawing of the model (part "plan:layout"); elsewhere a renaming carries the plan along without a step of it."""
    return _Recorder(survey, layout)


def _prune(survey, user_id, new):
    """A new step ends what could be redone; older than DAYS and beyond KEEP go."""
    mine = db.select(EditStep.id).filter_by(survey_id=survey.id, user_id=user_id)
    db.session.execute(db.delete(EditStep).where(EditStep.id.in_(mine.filter(EditStep.undone, EditStep.id != new.id))))
    db.session.execute(db.delete(EditStep).where(EditStep.survey_id == survey.id,
                                                 EditStep.created_at < utcnow() - timedelta(days=DAYS)))
    kept = db.session.scalars(mine.order_by(EditStep.id.desc()).limit(KEEP)).all()
    if len(kept) == KEEP:
        db.session.execute(db.delete(EditStep).where(EditStep.survey_id == survey.id, EditStep.user_id == user_id,
                                                     EditStep.id < kept[-1]))


def forget(survey):
    """The survey starts: its structure is fixed from now on, nothing can be taken back."""
    db.session.execute(db.delete(EditStep).where(EditStep.survey_id == survey.id))


def history(survey, user, everything=False):
    """What the arrows offer this person: {"undo": [steps, newest first], "redo": [steps, next first]}.
    Every page of a survey shows this list, and it only needs the labels: the states a step holds (`data`,
    whole questions and the flow) are read from the database when a step is taken back or redone, not here
    (only for a step from before EditStep.need, to know its role). Left out are the steps that need a role
    the person no longer has (need); everything: those too."""
    if not user.is_authenticated:
        return {"undo": [], "redo": []}
    rows = []
    for s, en in db.session.execute(  # the English name from the data without the rest of it (label())
            db.select(EditStep, EditStep.data["label_en"].as_string()).options(defer(EditStep.data))
            .filter(EditStep.survey_id == survey.id, EditStep.user_id == user.id,
                    EditStep.created_at >= utcnow() - timedelta(days=DAYS))
            .order_by(EditStep.id.desc())).all():
        s.label_en = en
        rows.append(s)
    if rows and not everything and not survey.can(user, "admin"):
        rows = [s for s in rows if need(s) != "admin"]
    return {"undo": [s for s in rows if not s.undone], "redo": [s for s in reversed(rows) if s.undone]}


def refused_role(step):
    """The message for someone without the role a step needs."""
    return de_en("Dafür fehlen die Rechte: „{step}“ ändert Einstellungen, die nur die Rolle „Admin“ ändert. "
                 "Zurücknehmen oder wiederholen kann den Schritt nur, wer diese Rolle hat.",
                 "You lack the rights for this: “{step}” changes settings that only the role “Admin” changes. Only "
                 "someone with this role can undo or redo the step.", step=label(step))


def clock(step):
    return to_local(step.created_at).strftime("%H:%M")


def when(step):
    """When a step was saved, for the line in the head of a survey (S2) and the list of the last changes under ↶
    (UI/UX audit 2026-10-09, X70: a change of days ago no longer looks like one of today): the time today, else day
    and time."""
    at, today = to_local(step.created_at), to_local(utcnow()).date()
    if at.date() == today:
        return at.strftime("%H:%M")
    return fmt(step.created_at, True, "en" if english() else "de")


# --- taking back and redoing ----------------------------------------------------------


def _what(survey, key, parts):
    """A part in words, for the message about a conflict."""
    kind, _, ident = key.partition(":")
    if kind == "flow":
        return de_en("der Ablauf", "the flow")
    if kind == "plan":
        return de_en("die Anordnung des Modells", "the arrangement of the model")
    known = next((s for s in parts.get(key, []) if s), None)
    if kind == "item":
        item = db.session.get(Item, int(ident))
        code = item.code if item is not None else (known or {}).get("code", "")
        return de_en("die Frage „{code}“", "the question “{code}”", code=code)
    if kind == "settings":
        return de_en(*{"meta": ("die Einstellungen", "the settings"), "image_style": ("der Bildstil", "the image style"),
                       "theme": ("das Erscheinungsbild", "the appearance"),
                       "panel": ("die Teilnahmelinks", "the participation links"),
                       "end_texts": ("die Texte der Endseiten", "the end page texts"),
                       "quality": ("die Qualitätssicherung", "the quality assurance"),
                       "weighting": ("die Gewichtung", "the weighting")}.get(ident, ("die Einstellungen", "the settings")))
    if kind == "linked":
        return de_en("das verknüpfte Element „{name}“", "the linked element “{name}”", name=(known or {}).get("name", ""))
    if kind == "board":
        board = next((b for b in survey.boards if b.id == int(ident)), None)
        return de_en("das Dashboard „{name}“", "the dashboard “{name}”",
                     name=board.name if board is not None else (known or {}).get("name", ""))
    if kind == "quota":
        quota = next((q for q in survey.quotas if q.id == int(ident)), None)
        return de_en("die Quote „{name}“", "the quota “{name}”",
                     name=quota.name if quota is not None else (known or {}).get("name", ""))
    block = next((b for b in survey.blocks if b.id == int(ident)), None)
    name = (block.title() if block is not None else "") or "Block"
    return (de_en("der Block „{name}“", "the block “{name}”", name=name) if kind == "block"
            else de_en("die Reihenfolge der Fragen in „{name}“", "the order of the questions in “{name}”", name=name))


def _cannot(step, undoing):
    """The start of a refusal: "„…“ lässt sich nicht zurücknehmen" (or "wiederholen")."""
    if undoing:
        return de_en("„{step}“ lässt sich nicht zurücknehmen", "“{step}” cannot be undone", step=label(step))
    return de_en("„{step}“ lässt sich nicht wiederholen", "“{step}” cannot be redone", step=label(step))


def _conflict(survey, step, keys, undoing):
    other = db.session.scalars(
        db.select(EditStep).filter(EditStep.survey_id == survey.id, EditStep.id > step.id,
                                   EditStep.user_id != step.user_id).order_by(EditStep.id.desc())).all()
    by = next((s for s in other if set(keys) & set(s.data.get("parts", {}))), None)
    what = _what(survey, keys[0], step.data["parts"])
    what = what[0].upper() + what[1:]
    who = (de_en(" von {name} um {time} Uhr", " by {name} at {time}", name=by.user.display_name, time=clock(by))
           if by is not None
           else de_en(" (zum Beispiel im Reiter „Übersetzung“, bei den Quoten oder an einem Bild)",
                      " (for example in the tab “Translation”, in the quotas or on an image)"))
    return _cannot(step, undoing) + de_en(
        ": {what} wurde seitdem{who} geändert. Der Schritt wurde aus dem Verlauf genommen, damit diese Änderung nicht "
        "überschrieben wird.",
        ": {what} has been changed since{who}. The step was removed from the history so that this change is not "
        "overwritten.", what=what, who=who)


def _free_codes(survey, target):
    """A question the step brings back or renames back, whose variable name another question took in the
    meantime (someone else added one while the name was free), gets a number, as when it comes back from the
    trash (trash.restore): two questions never share a name. The names taken are read from the database under the
    survey's row (edit_guard.taken_codes). Changes `target`; returns [(old name, new name)]."""
    from app import edit_guard

    mine = {int(k[5:]) for k in target if k.startswith("item:")}
    used = edit_guard.taken_codes(survey, but=mine)
    renamed = []
    for key, state in target.items():
        if not key.startswith("item:") or state is None:
            continue
        code, n = state["code"], 2
        while code in used:
            code, n = f"{state['code']}_{n}", n + 1
        used.add(code)
        if code != state["code"]:
            renamed.append((state["code"], code))
            state["code"] = code
    return renamed


def _theme_files_gone(survey, target):
    """True when the theme the step puts back names logo files that are deleted by now (applying a design
    deletes the old ones; decisions: the page asks before): that step would show a broken image."""
    from app import media, theme

    state = target.get("settings:theme")
    look = theme.clean((state or {}).get("theme")) or {}
    logo = look.get("logo") or {}
    names = [logo.get(k) for k in ("file", "file_dark", "icon") if logo.get(k)]
    names += [p.get("file") for p in look.get("partners") or [] if p.get("file")]
    return any(not media.exists(survey, n) for n in names)


def _gone_elements(target):
    """A question the step puts back that names a linked element which is gone by now (dissolved meanwhile) and
    which the step does not bring back itself (part linked:<key>) comes back as an ordinary question, as from
    the trash (trash.restore). Changes `target`; returns True if it dropped a link."""
    from app import linked

    dropped = False
    for key, state in target.items():
        if not key.startswith("item:") or state is None:
            continue
        ckey = ((state.get("config") or {}).get("construct") or {}).get("key")
        if isinstance(ckey, str) and target.get(f"linked:{ckey}") is None:
            config = linked.without_gone(state["config"])
            if config is not state["config"]:
                state["config"], dropped = config, True
    return dropped


def _known_quotas(survey, root, own=()):
    """Take the quotas that were deleted in the meantime out of the calculations and ends of a flow a step holds,
    as deleting a quota does with the flow of that moment (quotas.remove). Changes `root`; returns True if it
    named one. An element that comes back then names no quota, like a new one, and the pre-launch check asks for it.
    own: ids of the quotas the step itself holds (it deleted or made them and puts them back with their places
    in the flow); they stay. Left out are only quotas someone else deleted, or whose step is gone."""
    from app import flow

    known, changed = {q.id for q in survey.quotas} | set(own), False

    def kept(ids):
        nonlocal changed
        rest = [q for q in ids if q in known]
        changed = changed or len(rest) != len(ids)
        return rest

    for node, _, _ in flow.walk(root):
        if node["t"] not in ("quota", "end"):
            continue
        if node.get("quotas"):
            node["quotas"] = kept(node["quotas"])
        for qid in [q for q in node.get("links") or {} if q not in {str(k) for k in known}]:
            del node["links"][qid]  # the completion links of an end of the older form, by quota
            changed = True
        for case in node.get("cases") or []:  # the triggers of an end
            if case.get("quotas"):
                case["quotas"] = kept(case["quotas"])
            if case.get("quota") is not None and case["quota"] not in known:
                case.update(quota=None, cells=[])
                changed = True
    return changed


def _apply(survey, step, undoing):
    """Put the parts of `step` into their state before (undoing) or after it. Raises Refused, changing nothing.
    Returns [(old, new)] of the variable names that had to change because they are taken now (`_free_codes`)."""
    from app import flow, trash

    parts = step.data.get("parts", {})
    src, dst = (1, 0) if undoing else (0, 1)
    # A quota deleted since the step is no change of the flow that the step would overwrite: both of its flows
    # are read without it (as the flow is now), and the step keeps them so for the other direction.
    flows = copy.deepcopy(parts.get("flow") or [])
    own = [int(k.partition(":")[2]) for k in parts if k.startswith("quota:")]
    if any([_known_quotas(survey, root, own) for root in flows if root]):
        parts = {**parts, "flow": flows}
        step.data = {**step.data, "parts": parts}
    now = _normalized(survey)
    changed = [k for k, states in parts.items() if now.get(k) != (None if states[src] is None else _dump(states[src]))]
    if changed:
        raise Refused(_conflict(survey, step, changed, undoing))
    if _elements_refused(survey, step, undoing):
        raise Refused(_elements_refused(survey, step, undoing))
    target = {k: copy.deepcopy(states[dst]) for k, states in parts.items()}
    fresh = set(step.data.get("fresh", []))
    in_trash = {e.data.get("id"): e for e in survey.trash}
    blocks = {b.id: b for b in survey.blocks}
    items = {i.id: i for b in survey.blocks for i in b.items}
    renamed = _free_codes(survey, target)
    gone = _gone_elements(target)
    locked = _answers_refused(survey, step, target, items, undoing) or _retention_refused(survey, step, target, undoing)
    if locked:
        raise Refused(locked)
    if _theme_files_gone(survey, target):
        raise Refused(_cannot(step, undoing) + de_en(
            ": Die Logodateien des damaligen Erscheinungsbilds gibt es nicht mehr (etwa nach „Design übernehmen“). "
            "Der Schritt wurde aus dem Verlauf genommen.",
            ": the logo files of the appearance of that moment no longer exist (e.g. after “Apply design”). "
            "The step was removed from the history."))
    if renamed or gone:
        # the step now holds what was really put back: the other direction finds the questionnaire as it left it
        step.data = {**step.data, "parts": {k: [target[k] if n == dst else state for n, state in enumerate(states)]
                                             for k, states in parts.items()}}
    ids = lambda kind, there: [int(k.partition(":")[2]) for k, v in target.items()
                               if k.startswith(kind + ":") and (v is not None) == there]
    for iid in ids("item", True):
        if iid not in items and iid not in in_trash and iid not in fresh and target[f"item:{iid}"]["type"] not in trash.SKIP:
            raise Refused(_cannot(step, undoing) + de_en(
                ": Die Frage „{code}“ wurde endgültig aus dem Papierkorb gelöscht.",
                ": the question “{code}” was deleted from the bin permanently.", code=target[f"item:{iid}"]["code"]))
    taken = _ids_taken(survey, target)
    if taken:
        raise Refused(taken)
    codes_before = {iid: i.code for iid, i in items.items()}
    with db.session.no_autoflush:  # rows change their block here: a flush in between would delete them as orphans
        for iid in ids("item", False):  # questions that go: as when deleting, into the trash with their images
            item = items[iid]
            if not (undoing and iid in fresh and not item.assets):  # a question just added and untouched simply goes
                trash.put(item)
        for bid in ids("block", True):
            block = blocks.get(bid)
            if block is None:
                block = blocks[bid] = Block(id=bid, position=len(survey.blocks))
                survey.blocks.append(block)
            state = target[f"block:{bid}"]
            block.titles, block.settings = state["titles"], state["settings"]
            block.show_title, block.randomize_items = state["show_title"], state["randomize_items"]
        for iid in ids("item", True):
            state = target[f"item:{iid}"]
            item = items.get(iid)
            if item is None:
                item = items[iid] = Item(id=iid, type=state["type"])
                entry = in_trash.get(iid)
                if entry is not None:  # its images are still on disk
                    item.assets = [Asset(**a) for a in entry.data.get("assets", [])]
                    survey.trash.remove(entry)
            item.code, item.answer_mode = state["code"], state["answer_mode"]
            item.config, item.texts = state["config"], state["texts"]
        for bid in ids("order", True):
            rows = [items[i] for i in target[f"order:{bid}"]]
            blocks[bid].items = rows
            for n, row in enumerate(rows):
                row.position = n
        for bid in ids("block", False):
            survey.blocks.remove(blocks[bid])
        quotas = {q.id: q for q in survey.quotas}
        for qid in ids("quota", True):
            state = target[f"quota:{qid}"]
            quota = quotas.get(qid)
            if quota is None:
                quota = Quota(id=qid)
                survey.quotas.append(quota)
            for f in QUOTA_FIELDS:
                setattr(quota, f, state[f])
            old = {c.id: c for c in quota.cells}
            cells = []
            for row in state["cells"]:  # a Teilquote that comes back is counted below from the participations
                cell = old.get(row["id"]) or QuotaCell(id=row["id"], running=0, done=0, refused=0, over=0)
                for f in CELL_FIELDS:
                    setattr(cell, f, row[f])
                cells.append(cell)
            quota.cells = cells
        for qid in ids("quota", False):
            survey.quotas.remove(quotas[qid])
        from app.models import Board

        boards = {b.id: b for b in survey.boards}
        for bid in ids("board", True):  # a dashboard that comes back comes without its links
            board = boards.get(bid)
            if board is None:
                board = Board(id=bid)
                survey.boards.append(board)
            for f, value in target[f"board:{bid}"].items():
                setattr(board, f, copy.deepcopy(value))
        for bid in ids("board", False):
            survey.boards.remove(boards[bid])
        for key, state in target.items():
            if key.startswith("settings:"):
                for f, value in state.items():
                    setattr(survey, f, copy.deepcopy(value))
        if "plan:layout" in target:
            plan = survey.study_plan if isinstance(survey.study_plan, dict) else {}
            survey.study_plan = {**plan, "layout": copy.deepcopy(target["plan:layout"] or {})}
        if "flow" in target:
            survey.flow = copy.deepcopy(target["flow"])
            flag_modified(survey, "flow")
        _apply_interviewers(survey, target)
        from app import study_plan

        # the plan followed the renaming in the editor (study_plan.rename_code): it follows the step back too,
        # also to a name _free_codes gave
        study_plan.rename_codes(survey, {codes_before[iid]: items[iid].code for iid in ids("item", True)
                                         if iid in codes_before and items[iid].code != codes_before[iid]})
    db.session.flush()
    _apply_elements(survey, step, target, now, undoing)
    flow.normalize(survey)
    if ids("quota", True):
        # The participations still hold their places in the cells (Response.quota): a quota that comes back
        # counts what they hold, not 0, or a hard quota lets too many in after the restart (decision 2026-10-12).
        from app import quotas as quota_counts

        db.session.flush()
        quota_counts.recount(survey)
    return renamed


def _answers_refused(survey, step, target, items, undoing):
    """Why putting `target` back would change what the real answers of a question mean (variable name, codes,
    scale points, reversal; app/answer_lock.py), or None. Such a step comes from before the rule held on every
    way, or from a change no step checks; it leaves the history like a step in conflict, the rest stays."""
    from app import answer_lock

    for key, state in target.items():
        item = items.get(int(key[5:])) if key.startswith("item:") and state is not None else None
        if item is None:
            continue
        changed = answer_lock.refused_shape(survey, item, answer_lock.shape(item),
                                            answer_lock.shape_of(item, state["code"], state["config"]))
        if changed:
            if undoing:
                why = de_en("„{step}“ lässt sich darum nicht zurücknehmen; der Schritt wurde aus dem Verlauf genommen, "
                            "die Frage bleibt, wie sie ist.",
                            "“{step}” therefore cannot be undone; the step was removed from the history, the question "
                            "stays as it is.", step=label(step))
            else:
                why = de_en("„{step}“ lässt sich darum nicht wiederholen; der Schritt wurde aus dem Verlauf genommen, "
                            "die Frage bleibt, wie sie ist.",
                            "“{step}” therefore cannot be redone; the step was removed from the history, the question "
                            "stays as it is.", step=label(step))
            return answer_lock.message(item, changed, why)
    return None


def _ids_taken(survey, target):
    """A dashboard the step puts back under its id, while another survey has a dashboard with that id by now (ids
    of dashboards deleted before they were kept apart, models._keep_ids_apart): a conflict, not a server error."""
    from app.models import Board

    own = {b.id for b in survey.boards}
    for key, state in target.items():
        if not key.startswith("board:") or state is None or int(key.partition(":")[2]) in own:
            continue
        other = db.session.get(Board, int(key.partition(":")[2]))
        if other is not None and other.survey_id != survey.id:
            return de_en("Das Dashboard „{name}“ lässt sich nicht zurückholen: Seine Nummer ist inzwischen vergeben. Der "
                         "Schritt wurde aus dem Verlauf genommen.",
                         "The dashboard “{name}” cannot be brought back: its number is taken by now. The step was "
                         "removed from the history.", name=state.get("name") or "")
    return None


def _retention_refused(survey, step, target, undoing):
    """Why putting `target` back would make a retention period longer although real participations exist (they
    agreed to the periods the consent named, retention.from_form), or None. Such a step leaves the history like a
    step in conflict, the periods stay as they are."""
    from app import retention

    wanted = next((state["retention"] for key, state in target.items()
                   if key.startswith("settings:") and isinstance(state, dict) and "retention" in state), False)
    if wanted is False or not retention.locked(survey):
        return None
    before, after = retention.of(survey), retention.of(SimpleNamespace(retention=wanted))
    if not any(before[k] is not None and (after[k] is None or after[k] > before[k]) for k in before):
        return None
    why = (de_en("„{step}“ lässt sich nicht zurücknehmen: Es gibt echte Teilnahmen, die Aufbewahrungsfristen dürfen nur "
                 "kürzer werden. Der Schritt wurde aus dem Verlauf genommen.",
                 "“{step}” cannot be undone: there are real participations, the retention periods may only get shorter. "
                 "The step was removed from the history.", step=label(step)) if undoing else
           de_en("„{step}“ lässt sich nicht wiederholen: Es gibt echte Teilnahmen, die Aufbewahrungsfristen dürfen nur "
                 "kürzer werden. Der Schritt wurde aus dem Verlauf genommen.",
                 "“{step}” cannot be redone: there are real participations, the retention periods may only get shorter. "
                 "The step was removed from the history.", step=label(step)))
    return why


def _elements_refused(survey, step, undoing):
    """Why taking back `step` would take a linked element it made from questions that use it since (inserted
    here or in another survey), or None."""
    from app import linked

    if not undoing:
        return None
    parts = step.data.get("parts", {})
    for key in step.data.get("made", []):
        c = linked.get(key)
        if c is not None and any(f"item:{i.id}" not in parts for i, _ in linked.uses(c)):
            return _cannot(step, True) + de_en(
                ": Das verknüpfte Element „{name}“ wird inzwischen von weiteren Fragen verwendet.",
                ": the linked element “{name}” is used by further questions by now.", name=c.name)
    return None


def _apply_elements(survey, step, target, now, undoing):
    """The linked elements of a step (parts linked:<key>). An element whose questions the survey asked when the
    step was checked is put into its state as the step left it (the check made sure nobody changed it since). One
    that is gone is made anew (redoing "verknüpfbar gemacht"); one the step made goes when the step is taken
    back, unless another survey has inserted it meanwhile. Elements only asked again now (a question back from
    the trash, a link undone) are left as they are: other surveys may have changed them since."""
    from app import linked
    from app.models import Construct, ConstructPick

    made = set(step.data.get("made", []))
    for key, state in target.items():
        if not key.startswith("linked:"):
            continue
        ckey = key.partition(":")[2]
        c = db.session.scalar(db.select(Construct).filter_by(key=ckey))
        if state is None:
            if undoing and ckey in made and c is not None:  # _elements_refused said it is used here only
                db.session.execute(db.delete(ConstructPick).where(ConstructPick.construct_id == c.id))
                db.session.delete(c)
            continue
        if c is None:
            c = Construct(key=ckey, status=linked.STATUS)
            db.session.add(c)
        elif now.get(key) is None:
            continue
        for f in LINKED_FIELDS:
            setattr(c, f, copy.deepcopy(state[f]))
        c.search_text = linked._search_text(c)
    db.session.flush()


def _run(survey, user, undoing, upto):
    """Take back (or redo) this person's steps one by one, up to and including step `upto` (None: one step).
    Returns (steps done, message of the refusal or None). Each step is committed on its own, so what was
    taken back stays taken back when a later step is refused. A step done has `renamed`: [(old, new)] of the
    variable names that are taken by now, and the names its questions got instead."""
    done = []
    while True:
        _hold(survey)
        steps = history(survey, user)["undo" if undoing else "redo"]
        if not steps or (upto is not None and not any(s.id == upto for s in steps)):
            if not done:  # the step asked for (or the next one) is there, but needs a role the person has not
                hidden = history(survey, user, everything=True)["undo" if undoing else "redo"]
                wanted = next((s for s in hidden if s.id == upto), None) if upto is not None else (hidden[0] if hidden else None)
                if wanted is not None and need(wanted) == "admin" and not survey.can(user, "admin"):
                    return done, refused_role(wanted)
            return done, None
        step = steps[0]
        _hold_rows(survey, step)
        try:
            if need_now(survey, step) == "admin" and not survey.can(user, "admin"):
                raise Refused(_cannot(step, undoing) + de_en(
                    ": Das ändert inzwischen nur die Rolle „Admin“ (ein Dashboard mit gültigem Link, der Datenpool "
                    "bei einem solchen Dashboard oder die Quoten im Feld). Der Schritt wurde aus dem Verlauf genommen.",
                    ": by now only the role “Admin” changes this (a dashboard with a valid link, the data pool with such "
                    "a dashboard or the quotas in the field). The step was removed from the history."))
            renamed = _apply(survey, step, undoing)
        except Refused as e:
            db.session.rollback()
            step = db.session.get(EditStep, step.id)
            if step is not None:
                db.session.delete(step)
            if not undoing:  # what came after it can no longer be redone either
                db.session.execute(db.delete(EditStep).where(EditStep.survey_id == survey.id,
                                                             EditStep.user_id == user.id, EditStep.undone))
            db.session.commit()
            return done, str(e)
        step.undone = undoing
        db.session.commit()
        step.renamed = renamed
        done.append(step)
        if upto is None or step.id == upto:
            return done, None


def _hold(survey):
    """Hold the survey's row until the step is committed and read everything anew under it: the history, the
    questionnaire, the step. Two members (or two tabs) taking back steps at once run one after the other, and a
    renaming in the element editor, which holds the survey too, does not slip in between."""
    from app import edit_guard

    edit_guard.lock(survey)
    db.session.expire_all()  # what the request loaded before the lock is read anew


def _hold_rows(survey, step):
    """Hold the questions, blocks and dashboards `step` writes, each kind in the order of the ids, before it is
    compared with the questionnaire as it is now (_apply): always after the survey, as the element editor does. A
    save of one of them under way ends first; its change is then a conflict of the step instead of being
    overwritten by it."""
    from app import edit_guard
    from app.models import Board

    parts = step.data.get("parts", {})
    for prefix, model in (("item:", Item), ("block:", Block), ("board:", Board)):
        for ident in sorted({int(k[len(prefix):]) for k in parts if k.startswith(prefix) and k[len(prefix):].isdigit()}):
            row = db.session.get(model, ident)
            if row is not None and getattr(row, "survey_id", survey.id) == survey.id:
                edit_guard.lock(row)


def undo(survey, user, upto=None):
    return _run(survey, user, True, upto)


def redo(survey, user, upto=None):
    return _run(survey, user, False, upto)
