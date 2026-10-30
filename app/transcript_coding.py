"""Interviews kodieren: passages of interview transcripts assigned to categories, together and without overwriting each
other (experimental "transcript_coding", app/experimental.py; concept "Interviews kodieren", all as recommended, Ralf
2026-10-27). Stage 1: the analyses, their category systems, members, coding links and history; the coding view
(stage 2), the overviews and merging (stage 3) and the REFI-QDA exchange (stage 4) build on it.

Analyses (E12, CodingProject): several per guide, each with a name, a research question, a category system of its own
and members of its own, all on the same transcripts (Response.interview["transcript"]). An analysis starts from the
guide (all of it or some Themenblöcke: a Themenblock becomes a main category, its Leitfragen subcategories, with the
GUIDs of app/qdc.py, so that the codebook of the guide and this system name the same category the same way), empty, or
as a copy of another analysis.

Who (E2), the actor of a request (`actor`):
- the team: "Bearbeiten" or "Admin" on the guide, everything;
- members with the role "Kodieren" (CodingMember): a person or an Askurious group (app/groups.py), signed in; they code
  and add to the category system, never change the guide;
- an interviewer link (app/interviewer.py) the team ticked in an analysis ("darf eigene Interviews kodieren", off by
  default): its holder sees only the own interviews, also after "Transkript geprüft". The interviewer link itself, its
  sessions, revisions, devices and its access to interviews stay exactly as they are: the tick is a row of this
  module, read only here;
- (a person without an account codes by an interviewer link with the right "darf kodieren" and without "darf
  Interviews führen"; the coding links of 2026-10-27 are gone, app/transcript_coding_links.py.)

Which interviews (E3): "alle sehen alle Interviews" (the default) or "jede Gruppe ihre eigenen": then a person sees the
interviews they conducted, a group those of its members, and a member or coding link also those of the interviewer
links the team assigned to it; the team always sees all.

The category system: main categories, subcategories and candidates (proposed main categories, drawn dashed). With
"Hauptkategorien festgelegt" (E4) only the team changes main categories; everyone may add candidates and propose a
new name or a sharper definition, which the team takes on or declines. Without it main categories follow the rules of
subcategories: every member adds them (name and definition required, delimitation optional; the anchor example, a
coded passage, comes with stage 2), with author and time; the author and the team change them, everyone else
proposes. Before a new one is made, categories with a similar name are shown first (`similar`). Every change is an
entry of the history (CodingEvent) with the state before and after, so that "Rückgängig" takes back one's own last
step as long as nobody changed that category since."""
import difflib
import uuid
from dataclasses import dataclass, field
from functools import wraps

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app import autosave
from app.extensions import db
from app.lang import de_en
from app.models import CodingCategory, CodingEvent, CodingMember, CodingMemo, CodingProject, InterviewerAccess, Survey, utcnow

bp = Blueprint("transcript_coding", __name__, url_prefix="/guide")

FEATURE = "transcript_coding"
NAME_MAX = 120
TEXT_MAX = 4000
QUESTION_MAX = 2000
SIMILAR = 0.6  # SequenceMatcher ratio from which a name counts as similar
MAX_PROJECTS = 30  # analyses per guide
MAX_CATEGORIES = 500  # live categories per analysis
MAX_DEPTH = 8  # levels of a category system: a main category and up to seven below it (Ralf 2026-10-28: 3 to 6 or more)
UNDO_KINDS = ("created", "changed", "removed", "promoted", "moved", "ordered")


# --- who asks ------------------------------------------------------------------------------------------------------


@dataclass
class Actor:
    """Who works in an analysis in this request. kind: "team", "member", "link" or "interviewer"; key: what their
    entries are marked with ("u:<user id>", "i:<interviewer link id>"; "l:<id>" on entries of the
    coding links of 2026-10-27)."""

    kind: str
    key: str
    name: str
    user_id: int | None = None
    access: object | None = None  # the InterviewerAccess of an interviewer
    members: list = field(default_factory=list)  # the CodingMember rows that let a member in

    @property
    def team(self):
        return self.kind == "team"


def _user_actor(kind, user, members=()):
    return Actor(kind, f"u:{user.id}", user.display_name, user_id=user.id, members=list(members))


def team(survey, user=None):
    user = current_user if user is None else user
    return bool(getattr(user, "is_authenticated", False)) and survey.can(user, "editor")


def memberships(project, user):
    """The rows that make `user` a member of `project`: as a person or through one of their groups."""
    from app.groups import ids_of

    from app import orgs
    from app.models import WorkGroup

    if not getattr(user, "is_authenticated", False):
        return []
    rows = list(db.session.scalars(db.select(CodingMember).where(
        CodingMember.project_id == project.id,
        db.or_(db.and_(CodingMember.kind == CodingMember.PERSON, CodingMember.user_id == user.id),
               db.and_(CodingMember.kind == CodingMember.GROUP, CodingMember.group_id.in_(ids_of(user)))))))
    # a group shares only within its organisation: a group row lets in only while the group and the guide are in the
    # same one, however the group got elsewhere
    guide = db.session.get(Survey, project.survey_id)
    return [r for r in rows if r.kind != CodingMember.GROUP or _same_org(db.session.get(WorkGroup, r.group_id or 0), guide, orgs)]


def _same_org(group, guide, orgs):
    return group is not None and guide is not None and orgs.same(group, guide)


def interviewer_member(project, access):
    """The row that lets this interviewer link code in `project`, or None (the tick is off)."""
    if access is None:
        return None
    return db.session.scalar(db.select(CodingMember).filter_by(project_id=project.id, kind=CodingMember.INTERVIEWER,
                                                               access_id=access.id))


def actor(survey, project):
    """The actor of this request in `project`, or None. A signed-in person with "Bearbeiten" on the guide is the team,
    else a member if a row lets them in; then an interviewer link with the
    right "darf kodieren" the team assigned to this analysis. Nobody but the team works in an analysis of an
    archived guide."""
    from app import experimental, interviewer

    if current_user.is_authenticated:
        if team(survey):
            return _user_actor("team", current_user) if experimental.enabled(FEATURE, survey=survey) else None
        rows = memberships(project, current_user)
        if rows and not survey.archived and experimental.enabled(FEATURE, survey=survey):
            return _user_actor("member", current_user, rows)
    if interviewer.any_session() and not survey.archived:
        access = interviewer.current(survey)
        if access is not None and interviewer_codes_in(project, access):
            row = interviewer_member(project, access)
            return Actor("interviewer", f"i:{access.id}", access.name, access=access, members=[row] if row else [])
    return None


def signed_in_or_coder(view):
    """Instead of login_required: lets through a signed-in person and a browser holding an interviewer link; `actor`
    decides about the analysis itself (404 for everyone else)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        from app import interviewer

        if current_user.is_authenticated or interviewer.any_session():
            return view(*args, **kwargs)
        return current_app.login_manager.unauthorized()

    return wrapper


def guide(code):
    survey = db.session.scalar(db.select(Survey).filter_by(code=code))
    if survey is None or survey.interview is None:
        abort(404)
    return survey


def project_of(survey, pid):
    project = db.session.get(CodingProject, pid)
    if project is None or project.survey_id != survey.id:
        abort(404)
    return project


def _team_survey(code):
    """The guide for a page only the team opens: "Bearbeiten" and the feature (or a guide that uses it)."""
    from app import experimental, interview_data

    survey = interview_data._survey(code, need="editor")
    experimental.require(FEATURE, survey)
    return survey


def _in(code, pid, team_only=False):
    """(survey, project, actor) of a request, else 404."""
    survey = guide(code)
    project = project_of(survey, pid)
    who = actor(survey, project)
    if who is None or (team_only and not who.team):
        abort(404)
    if who.team:
        from flask import g

        g.audit_survey = survey  # the access log (app/audit.py) names the guide of a change of members or links
    return survey, project, who


# --- analyses ------------------------------------------------------------------------------------------------------


def projects(survey):
    return list(db.session.scalars(db.select(CodingProject).filter_by(survey_id=survey.id)
                                   .order_by(CodingProject.created_at, CodingProject.id)))


def projects_for(survey, user):
    """The analyses `user` works in as a member (for the link to them on the interviewer and results pages)."""
    return [p for p in projects(survey) if memberships(p, user)]


def may_code(access):
    """Whether this interviewer link has the right "darf kodieren" (its own interviews or all)."""
    return access is not None and access.may_code in (InterviewerAccess.CODE_OWN, InterviewerAccess.CODE_ALL)


def open_for_link(survey, access):
    """Whether "Interviews kodieren" is open for an interviewer link without an account: through the guide, as soon as
    the researcher who made the link or anyone of its team ("Bearbeiten", "Admin") has the feature; the link itself
    has no account to switch it on for, and an anonymous session is never asked (Ralf 2026-10-28). Off: the link
    codes nowhere (its stored right stays as it is) and works as an interviewer link as before."""
    from app import experimental
    from app.models import User

    people = [db.session.get(User, access.created_by_id)] if access is not None and access.created_by_id else []
    people += [m.user for m in survey.members if m.role in ("editor", "admin")]
    return any(experimental.enabled(FEATURE, user=person, survey=survey) for person in people if person is not None)


CODERS_ALL, CODERS_SELECTED = "all", "selected"


def coder_mode(project):
    """Who of the interviewer links with the right codes in `project`: "all" (the default) or "selected" (those with
    a row). An analysis of before without the setting, with links ticked, keeps exactly them (Ralf 2026-10-28)."""
    mode = (project.settings or {}).get("interviewers")
    if mode in (CODERS_ALL, CODERS_SELECTED):
        return mode
    ticked = db.session.scalar(db.select(CodingMember.id).filter_by(project_id=project.id, kind=CodingMember.INTERVIEWER).limit(1))
    return CODERS_SELECTED if ticked else CODERS_ALL


def interviewer_codes_in(project, access):
    """Whether this interviewer link codes in `project`: it has the right, and all with it code there or it is
    ticked."""
    if not may_code(access) or access.survey_id != project.survey_id:
        return False
    if not open_for_link(db.session.get(Survey, project.survey_id), access):
        return False
    return coder_mode(project) == CODERS_ALL or interviewer_member(project, access) is not None


def interviewer_projects(survey, access):
    """The analyses this interviewer link codes in: it has the right and is assigned to them (or all with it are)."""
    if not may_code(access) or not open_for_link(survey, access):
        return []
    ids = set(db.session.scalars(db.select(CodingMember.project_id).filter_by(kind=CodingMember.INTERVIEWER, access_id=access.id)))
    return [p for p in projects(survey) if p.id in ids or coder_mode(p) == CODERS_ALL]


def _clean(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _text(value, limit=TEXT_MAX):
    return str(value or "").replace("\r\n", "\n").strip()[:limit]


def new_guid():
    return str(uuid.uuid4()).upper()


def create_project(survey, name, question="", start="guide", blocks=None, copy_of=None, by=None):
    """A new analysis; returns (project, problem). start: "guide" (with `blocks`, the ids of the Themenblöcke to take,
    None = all), "empty" or "copy" (of the analysis `copy_of`). The caller commits."""
    name = _clean(name, NAME_MAX)
    if not name:
        return None, de_en("Bitte einen Namen angeben.", "Please enter a name.")
    if any(p.name.casefold() == name.casefold() for p in projects(survey)):
        return None, de_en("Eine Auswertung „{name}“ gibt es schon.", "There is already an analysis “{name}”.", name=name)
    if len(projects(survey)) >= MAX_PROJECTS:
        return None, de_en("Höchstens {n} Auswertungen je Leitfaden.", "At most {n} analyses per guide.", n=MAX_PROJECTS)
    source = None
    if start == "copy":
        source = db.session.get(CodingProject, copy_of) if isinstance(copy_of, int) else None
        if source is None or source.survey_id != survey.id:
            return None, de_en("Bitte die Auswertung wählen, die kopiert wird.", "Please choose the analysis to copy.")
    elif start not in ("guide", "empty"):
        return None, de_en("Bitte wählen, womit die Auswertung beginnt.", "Please choose what the analysis starts with.")
    project = CodingProject(survey_id=survey.id, name=name, question=_text(question, QUESTION_MAX),
                            visibility=source.visibility if source else CodingProject.ALL,
                            mains_fixed=source.mains_fixed if source else False,
                            created_by_id=getattr(by, "id", None), created_by_name=(getattr(by, "name", "") or "")[:200])
    db.session.add(project)
    db.session.flush()
    who = _user_actor("team", by) if by is not None and getattr(by, "id", None) else Actor("team", "", "")
    if start == "guide":
        from_guide(survey, project, blocks, who)
    elif start == "copy":
        copy_categories(source, project, who)
    return project, None


def from_guide(survey, project, block_ids, who):
    """Themenblöcke as main categories, their Leitfragen as subcategories (the narrative prompt, the definition and
    delimitation the AI assistant proposed and the researchers kept, and the content aspects as the definition), with
    the GUIDs of the guide's codebook (app/qdc.py). Unterfragen and tasks stay out: they belong to their Leitfrage."""
    from app import interview, qdc
    from app.items.base import plain

    lang = survey.default_language
    wanted = None if block_ids is None else {int(b) for b in block_ids}
    for n, block in enumerate(interview.guide_blocks(survey)):
        if wanted is not None and block.id not in wanted:
            continue
        colour = qdc.COLOURS[n % len(qdc.COLOURS)]
        main = _add(project, who, CodingCategory.MAIN, qdc._name(block.title(lang), f"Themenblock {n + 1}"), "", "",
                    guid=qdc._guid(survey, "block", block.id), source=f"block:{block.id}", colour=colour, event=False)
        for item, leitfrage in interview.outline(block):
            if item.type != "guide_question" or leitfrage is not None:
                continue
            text = qdc.leitfrage_description(item, lang)
            aspects = [t for _rid, t in item.kind.rows(item, "aspects", lang)]
            if aspects:
                text += ("\n\n" if text else "") + de_en("Inhaltliche Aspekte: ", "Content aspects: ") + "; ".join(plain(a) for a in aspects)
            delimit = ((item.config or {}).get("category") or {}).get("delimit") or ""
            _add(project, who, CodingCategory.SUB, qdc._name(item.text("question", lang), item.code), text[:TEXT_MAX],
                 delimit[:TEXT_MAX], parent=main, guid=qdc._guid(survey, "item", item.id), source=f"item:{item.id}",
                 colour=colour, event=False)


def copy_categories(source, project, who):
    """The live categories of `source` into `project`, with their GUIDs (the same category in another analysis) and
    their authors; the history stays with `source`."""
    ids = {}
    mains, candidates = tree(source)
    for main, below in mains:
        ids[main.id] = _copy(main, project, None)
        for cat in below:  # depth first: the one above is always copied before
            if cat.parent_id in ids:
                ids[cat.id] = _copy(cat, project, ids[cat.parent_id])
    for cat in candidates:
        ids[cat.id] = _copy(cat, project, None)


def _copy(cat, project, parent_id):
    new = CodingCategory(project_id=project.id, parent_id=parent_id, kind=cat.kind, guid=cat.guid, source=cat.source,
                         name=cat.name, definition=cat.definition, delimitation=cat.delimitation, colour=cat.colour,
                         position=cat.position, author_key=cat.author_key, author_id=cat.author_id,
                         author_name=cat.author_name, created_at=cat.created_at)
    db.session.add(new)
    db.session.flush()
    return new.id


def delete_project(project):
    """An analysis with its categories, history, members and memos (its codings in the interviews: stage 2)."""
    from app.models import CodingMemo

    for model in (CodingCategory, CodingEvent, CodingMember, CodingMemo):
        db.session.execute(db.delete(model).where(model.project_id == project.id))
    db.session.delete(project)


def forget_survey(survey_id):
    """Everything of the analyses of a guide that goes (lifecycle.delete)."""
    from app.models import CodingMemo

    ids = list(db.session.scalars(db.select(CodingProject.id).filter_by(survey_id=survey_id)))
    if ids:
        for model in (CodingCategory, CodingEvent, CodingMember, CodingMemo):
            db.session.execute(db.delete(model).where(model.project_id.in_(ids)))
        db.session.execute(db.delete(CodingProject).where(CodingProject.id.in_(ids)))


# --- which interviews ----------------------------------------------------------------------------------------------


def _group_users(group_ids):
    from app.models import WorkGroupMember

    if not group_ids:
        return set()
    return set(db.session.scalars(db.select(WorkGroupMember.user_id).where(WorkGroupMember.group_id.in_(group_ids),
                                                                           WorkGroupMember.user_id.is_not(None))))


def scope(who):
    """(user ids, interviewer link ids) whose interviews count as the actor's own."""
    users, accesses = set(), set()
    if who.link is not None:
        accesses |= {int(a) for a in (who.link.access_ids or []) if str(a).isdigit()}
    for row in who.members:
        if row.kind == CodingMember.PERSON and row.user_id:
            users.add(row.user_id)
        accesses |= {int(a) for a in (row.access_ids or []) if str(a).isdigit()}
    users |= _group_users([r.group_id for r in who.members if r.kind == CodingMember.GROUP])
    return users, accesses


def interviews(project, who):
    """The real interviews of the guide the actor sees in `project` (E3). An interviewer link with the right "eigene"
    sees its own ones, also after "Transkript geprüft"; with "alle" what a coding link sees: all of them, with "jede
    Gruppe ihre eigenen" its own ones (and those its row names). Read only, for coding. The team sees all."""
    from app import interview_data

    survey = db.session.get(Survey, project.survey_id)
    rows = [r for r in interview_data.interviews(survey, source="real")]
    if who.kind == "interviewer":
        row = who.members[0] if who.members else None
        if who.access.may_code == InterviewerAccess.CODE_ALL:
            if project.visibility == CodingProject.ALL:
                return rows
            mine = {who.access.id} | {int(a) for a in ((row.access_ids if row is not None else None) or []) if str(a).isdigit()}
            return [r for r in rows if (r.interview or {}).get("access") in mine]
        return [r for r in rows if (r.interview or {}).get("access") == who.access.id]
    if who.team or project.visibility == CodingProject.ALL:
        return rows
    users, accesses = scope(who)
    return [r for r in rows if (r.interview or {}).get("by") in users or (r.interview or {}).get("access") in accesses]


def has_transcript(row):
    return bool(((row.interview or {}).get("transcript") or {}).get("text"))


# --- the category system -------------------------------------------------------------------------------------------


def live(project):
    """The categories in use: not removed, not merged into another; in their order."""
    return list(db.session.scalars(db.select(CodingCategory).where(
        CodingCategory.project_id == project.id, CodingCategory.removed_at.is_(None), CodingCategory.merged_into.is_(None))
        .order_by(CodingCategory.position, CodingCategory.id)))


def tree(project):
    """[(main category, [everything below it, depth first])] and the candidates. A subcategory may stand under a main
    category or under another subcategory, down to MAX_DEPTH levels. Each category gets for the pages `level` (1: a
    main category or candidate), `path` (the names from its main category down, "A › B › C"), `rel` (the path below
    its main category), `anc` (the ids above it) and `kids` (how many stand directly below it)."""
    cats = live(project)
    below = {}
    for c in cats:
        if c.kind == CodingCategory.SUB:
            below.setdefault(c.parent_id, []).append(c)
    seen = set()

    def walk(cat, level, anc):
        out = []
        for k in below.get(cat.id, []):
            if k.id in seen:  # a loop in the data: shown once
                continue
            seen.add(k.id)
            k.level, k.path, k.anc = level, f"{cat.path} › {k.name}", anc + [cat.id]
            k.rel = k.name if level == 2 else f"{cat.rel} › {k.name}"
            k.kids = len(below.get(k.id, []))
            out.append(k)
            out.extend(walk(k, level + 1, k.anc))
        return out

    tops = [c for c in cats if c.kind != CodingCategory.SUB]
    for c in tops:
        c.level, c.path, c.rel, c.anc, c.kids = 1, c.name, "", [], len(below.get(c.id, []))
        seen.add(c.id)
    mains = [c for c in tops if c.kind == CodingCategory.MAIN]
    return [(m, walk(m, 2, [])) for m in mains], [c for c in tops if c.kind == CodingCategory.CANDIDATE]


def numbers(mains):
    """{category id: "1", "1.2", "1.2.3" ...} for the main categories of tree() and everything below them, numbered in
    their order down every level (candidates have none): the coding guide and the Excel export number alike."""
    out = {}
    for n, (main, subs) in enumerate(mains, 1):
        out[main.id] = str(n)
        count = {}
        for s in subs:
            count[s.parent_id] = count.get(s.parent_id, 0) + 1
            out[s.id] = f"{out.get(s.parent_id, out[main.id])}.{count[s.parent_id]}"
    return out


def chain(cat, by_id):
    """The categories above `cat`, its main category last (empty for a main category or a candidate)."""
    out, seen = [], {cat.id}
    while cat.parent_id and cat.parent_id in by_id and cat.parent_id not in seen:
        cat = by_id[cat.parent_id]
        seen.add(cat.id)
        out.append(cat)
    return out


def top_of(cat, by_id):
    """The main category (or candidate) `cat` stands under, or itself."""
    above = chain(cat, by_id)
    return above[-1] if above else cat


def level_of(cat, by_id):
    return len(chain(cat, by_id)) + 1


def subtree(project, cat):
    """The ids of `cat` and of everything below it."""
    below = {}
    for c in live(project):
        if c.parent_id:
            below.setdefault(c.parent_id, []).append(c.id)
    out, todo = set(), [cat.id]
    while todo:
        cid = todo.pop()
        if cid in out:
            continue
        out.add(cid)
        todo.extend(below.get(cid, []))
    return out


def height(project, cat):
    """How many levels `cat` spans with everything below it (1: nothing below)."""
    below = {}
    for c in live(project):
        if c.parent_id:
            below.setdefault(c.parent_id, []).append(c.id)

    def h(cid, seen):
        kids = [k for k in below.get(cid, []) if k not in seen]
        return 1 + max([h(k, seen | {k}) for k in kids] or [0])

    return h(cat.id, {cat.id})


def _key(name):
    return " ".join(name.casefold().split())


def similar(project, name, limit=5):
    """Categories whose name (or a former name) is like `name`, most alike first: [(category, score)]."""
    wanted = _key(name)
    if len(wanted) < 2:
        return []
    found = []
    for cat in live(project):
        best = 0.0
        for other in [cat.name] + list(cat.former_names or []):
            other = _key(other)
            score = difflib.SequenceMatcher(None, wanted, other).ratio()
            if wanted in other or other in wanted:
                score = max(score, 0.8)
            if set(wanted.split()) & set(other.split()):
                score = max(score, 0.65)
            best = max(best, score)
        if best >= SIMILAR:
            found.append((cat, best))
    return sorted(found, key=lambda f: -f[1])[:limit]


def guarded(project):
    """"Fremde Kategorien verschieben und zusammenführen nur Team und Autor" (a setting of the analysis, off by
    default; Ralf 2026-10-29): then the others propose moving and merging, as they propose changes."""
    return bool((project.settings or {}).get("guarded"))


def own_or_team(cat, who):
    return who.team or (bool(cat.author_key) and cat.author_key == who.key)


def may_create_main(project, who):
    return who.team or not project.mains_fixed


def may_edit(project, cat, who):
    """Change or remove directly: the team always; with "Hauptkategorien festgelegt" no one else a main category;
    otherwise the author of the entry."""
    if who.team:
        return True
    if cat.kind == CodingCategory.MAIN and project.mains_fixed:
        return False
    return bool(cat.author_key) and cat.author_key == who.key


def resolver(project):
    """category id -> the live category its codings count for now: itself, or, after merging, the category it was
    merged into (followed along a chain); None for a removed one. The codings keep the id they were made with
    (Response.interview["codings"]), so that undoing a merge and taking one's passages out need no rewrite of
    every interview."""
    rows = {c.id: c for c in db.session.scalars(db.select(CodingCategory).filter_by(project_id=project.id))}

    def resolve(cat_id):
        seen = set()
        while cat_id in rows and cat_id not in seen:
            seen.add(cat_id)
            cat = rows[cat_id]
            if cat.merged_into is None:
                return None if cat.removed_at is not None else cat_id
            cat_id = cat.merged_into
        return None

    return resolve


def uses(project, cat_id):
    """How many coded passages count for the category (Response.interview["codings"]), merged ones included."""
    survey = db.session.get(Survey, project.survey_id)
    from app import interview_data

    resolve = resolver(project)
    return sum(1 for r in interview_data.interviews(survey) for c in ((r.interview or {}).get("codings") or [])
               if c.get("project") == project.id and resolve(c.get("cat")) == cat_id)


def _state(cat):
    return {"name": cat.name, "definition": cat.definition, "delimitation": cat.delimitation, "kind": cat.kind,
            "removed": cat.removed_at is not None}


def _event(project, who, kind, cat=None, data=None, reason="", status="", reply_to=None, merge=None):
    event = CodingEvent(project_id=project.id, category_id=getattr(cat, "id", None), kind=kind, data=data,
                        reason=_text(reason, 1000), status=status, reply_to=reply_to, actor_key=who.key,
                        actor_id=who.user_id, actor_name=who.name[:200], merge=merge)
    db.session.add(event)
    project.updated_at = utcnow()
    return event


def siblings_of(project, parent_id, sub):
    """The categories directly under `parent_id` (None: the main categories and candidates), in their order; `sub`
    says whether subcategories are meant (the same sibling groups as reorder)."""
    return [c for c in live(project) if c.parent_id == parent_id and (c.kind == CodingCategory.SUB) == sub]


def sibling_lists(project):
    """For "Einordnen nach …" in the dialog "Neue Kategorie": {"main": [[id, name]], "candidate": [...], "<id>": [...]
    the subcategories directly under that category}, each in its order."""
    out = {"main": [], "candidate": []}
    for c in live(project):
        out.setdefault(str(c.parent_id) if c.kind == CodingCategory.SUB else c.kind, []).append([c.id, c.name])
    return out


def after_value(raw):
    """"Einordnen nach …" as the dialogs send it: "start", the id of a sibling, or None for the end."""
    raw = str(raw if raw is not None else "").strip()
    return "start" if raw == "start" else int(raw) if raw.isdigit() else None


def _place(project, cat, after):
    """Put a new `cat` among its siblings: directly after the sibling `after` (an id), at the start ("start") or at the
    end (None; also for an id that is no sibling, e.g. from a list that changed meanwhile). The siblings keep their
    positions in the new order, as reorder does (Ralf 2026-10-30)."""
    if after is None:
        return
    group = siblings_of(project, cat.parent_id, cat.kind == CodingCategory.SUB)
    rest = [c for c in group if c.id != cat.id]
    if after == "start":
        at = 0
    else:
        at = next((n + 1 for n, c in enumerate(rest) if c.id == after), len(rest))
    slots = sorted(c.position for c in group)
    for c, p in zip(rest[:at] + [cat] + rest[at:], slots):
        c.position = p


def _add(project, who, kind, name, definition, delimitation, parent=None, guid=None, source=None, colour="", event=True,
         after=None):
    position = (db.session.scalar(db.select(db.func.max(CodingCategory.position)).filter_by(project_id=project.id)) or 0) + 1
    cat = CodingCategory(project_id=project.id, parent_id=getattr(parent, "id", None), kind=kind, guid=guid or new_guid(),
                         source=source, name=name, definition=definition, delimitation=delimitation,
                         colour=colour or getattr(parent, "colour", "") or "", position=position, author_key=who.key,
                         author_id=who.user_id, author_name=who.name[:200])
    db.session.add(cat)
    db.session.flush()
    _place(project, cat, after)
    if event:
        _event(project, who, "created", cat, {"after": _state(cat)})
    return cat


def create(project, who, kind, name, definition="", delimitation="", parent_id=None, after=None):
    """A new category; returns (category, problem). Whoever may not add a main category adds a candidate instead.
    `after` places it among its siblings ("Einordnen nach …", _place); "Rückgängig" takes it out again, and the others
    keep their order."""
    name, definition, delimitation = _clean(name, NAME_MAX), _text(definition), _text(delimitation)
    if kind not in (CodingCategory.MAIN, CodingCategory.SUB, CodingCategory.CANDIDATE):
        abort(400)
    if kind == CodingCategory.MAIN and not may_create_main(project, who):
        kind = CodingCategory.CANDIDATE
    if not name:
        return None, de_en("Bitte einen Namen angeben.", "Please enter a name.")
    parent = None
    if kind == CodingCategory.SUB:
        parent = db.session.get(CodingCategory, parent_id) if isinstance(parent_id, int) else None
        if (parent is None or parent.project_id != project.id or parent.kind not in (CodingCategory.MAIN, CodingCategory.SUB)
                or parent.removed_at is not None or parent.merged_into is not None):
            return None, de_en("Bitte die übergeordnete Kategorie wählen.", "Please choose the category above.")
        if level_of(parent, {c.id: c for c in live(project)}) >= MAX_DEPTH:
            return None, de_en("Tiefer als {n} Ebenen geht es nicht.", "It does not go deeper than {n} levels.", n=MAX_DEPTH)
    if not definition and (kind != CodingCategory.MAIN or not who.team):
        return None, de_en("Bitte eine Definition angeben: Wann gehört eine Stelle hierher?",
                           "Please enter a definition: when does a passage belong here?")
    siblings = [c for c in live(project) if (c.parent_id == getattr(parent, "id", None)) and
                (c.kind == CodingCategory.SUB) == (kind == CodingCategory.SUB)]
    if any(_key(c.name) == _key(name) for c in siblings):
        return None, de_en("„{name}“ gibt es hier schon.", "“{name}” already exists here.", name=name)
    if len(live(project)) >= MAX_CATEGORIES:
        return None, de_en("Höchstens {n} Kategorien je Auswertung.", "At most {n} categories per analysis.", n=MAX_CATEGORIES)
    return _add(project, who, kind, name, definition, delimitation, parent=parent, after=after), None


FIELDS = ("name", "definition", "delimitation")


def _values(form):
    return {"name": _clean(form.get("name"), NAME_MAX), "definition": _text(form.get("definition")),
            "delimitation": _text(form.get("delimitation"))}


def change(project, cat, who, values, merge=None):
    """Name, definition and delimitation of a category the actor may edit; returns a problem or None. The saves of one
    visit to a field are one entry of the history (`merge`)."""
    if not may_edit(project, cat, who):
        return de_en("Diese Kategorie ändern nur {who}; Sie können eine Änderung vorschlagen.",
                     "Only {who} change this category; you can propose a change.",
                     who=de_en("die Forschenden des Leitfadens", "the researchers of the guide") if cat.kind == CodingCategory.MAIN and project.mains_fixed
                     else de_en("die Person, die sie angelegt hat, und die Forschenden", "the person who created it and the researchers"))
    values = {k: v for k, v in values.items() if k in FIELDS}
    if "name" in values and not values["name"]:
        return de_en("Bitte einen Namen angeben.", "Please enter a name.")
    if "definition" in values and not values["definition"] and (cat.kind != CodingCategory.MAIN or not who.team):
        return de_en("Die Definition bleibt Pflicht.", "The definition stays required.")
    if "name" in values and _key(values["name"]) != _key(cat.name):
        siblings = [c for c in live(project) if c.id != cat.id and c.parent_id == cat.parent_id
                    and (c.kind == CodingCategory.SUB) == (cat.kind == CodingCategory.SUB)]
        if any(_key(c.name) == _key(values["name"]) for c in siblings):
            return de_en("„{name}“ gibt es hier schon.", "“{name}” already exists here.", name=values["name"])
    before = _state(cat)
    for k, v in values.items():
        setattr(cat, k, v)
    if _state(cat) == before:
        return None
    if _key(before["name"]) != _key(cat.name) and before["name"] not in (cat.former_names or []):
        cat.former_names = (cat.former_names or []) + [before["name"]]  # found again under its old name (similar)
    cat.updated_at = utcnow()
    last = _last(project, cat.id)
    if merge and last is not None and last.kind == "changed" and last.merge == merge and last.actor_key == who.key:
        last.data = {**(last.data or {}), "after": _state(cat)}
        last.at = utcnow()
    else:
        _event(project, who, "changed", cat, {"before": before, "after": _state(cat)}, merge=merge)
    return None


def _last(project, cat_id):
    return db.session.scalar(db.select(CodingEvent).filter_by(project_id=project.id, category_id=cat_id)
                             .where(CodingEvent.kind.not_in(("proposal", "accepted", "declined")))
                             .order_by(CodingEvent.id.desc()).limit(1))  # the answers to a proposal change nothing themselves


def remove(project, cat, who):
    """Remove a category nobody coded with; a main category only without subcategories. Returns a problem or None."""
    if not may_edit(project, cat, who):
        return de_en("Diese Kategorie entfernen nur die Forschenden oder wer sie angelegt hat.",
                     "Only the researchers or whoever created it remove this category.")
    if any(c.parent_id == cat.id for c in live(project)):
        return de_en("Erst die Unterkategorien entfernen oder verschieben.", "Remove or move the subcategories first.")
    if uses(project, cat.id):
        return de_en("Mit dieser Kategorie ist schon kodiert; sie lässt sich zusammenführen, nicht entfernen.",
                     "Passages are already coded with this category; it can be merged, not removed.")
    before = _state(cat)
    cat.removed_at = utcnow()
    _event(project, who, "removed", cat, {"before": before, "after": _state(cat)})
    return None


def promote(project, cat, who):
    """The team takes a candidate on as a main category."""
    if not who.team or cat.kind != CodingCategory.CANDIDATE:
        abort(404)
    before = _state(cat)
    cat.kind = CodingCategory.MAIN
    _event(project, who, "promoted", cat, {"before": before, "after": _state(cat)})


def may_move(project, cat, who):
    """Moving: a subcategory anyone in the analysis may hang elsewhere, also under another main category; the main
    level (making something a main category, putting a main category below another) is the team's while the main
    categories are fixed, else also the author's (Ralf 2026-10-28)."""
    if cat.kind == CodingCategory.SUB:
        return not guarded(project) or own_or_team(cat, who)
    return who.team or (not project.mains_fixed and may_edit(project, cat, who))


def may_propose_move(project, cat, who):
    """With the setting `guarded`: someone else's subcategory is moved by proposing it."""
    return cat.kind == CodingCategory.SUB and guarded(project) and not own_or_team(cat, who)


def drop_data(project, who, mains, candidates):
    """What the page's script needs to offer "unterhängen" and "zusammenführen" after a drop, with the rules of move()
    and may_merge(): the categories with kind, level, what stands above, may_move, and the texts for the dialog."""
    from app.transcript_coding_review import stats

    st = stats(project, who)
    cats = {}
    for c in [c for m, below in mains for c in [m] + below] + list(candidates):
        cats[str(c.id)] = {"id": c.id, "name": c.name, "kind": c.kind, "level": c.level, "anc": c.anc, "parent": c.parent_id,
                           "definition": c.definition, "delimitation": c.delimitation,
                           "anchor": (c.anchor or {}).get("quote", ""), "n": st.get(c.id, {}).get("n", 0),
                           "move": may_move(project, c, who) or may_propose_move(project, c, who),
                           "propose": may_propose_move(project, c, who), "author": bool(c.author_key) and c.author_key == who.key}
    return {"cats": cats, "team": who.team, "fixed": bool(project.mains_fixed), "max": MAX_DEPTH,
            "may_main": may_create_main(project, who), "guarded": guarded(project)}


def move_targets(project, cat, who, mains):
    """[(id, path)] where `cat` may be hung: not under itself or what stands below it, not deeper than MAX_DEPTH,
    not where it stands; (None, …) first for "as a main category" where allowed. `mains` from tree()."""
    flat = [c for m, below in mains for c in [m] + below]
    mine = [c for c in flat if c.id == cat.id or cat.id in c.anc]
    span = (max(c.level for c in mine) - getattr(cat, "level", 1) + 1) if mine else 1
    out = []
    if cat.kind != CodingCategory.MAIN and may_create_main(project, who):
        out.append((None, de_en("— als Hauptkategorie —", "— as a main category —")))
    for c in flat:
        if c.id == cat.id or cat.id in c.anc or c.id == cat.parent_id or c.level + span > MAX_DEPTH:
            continue
        out.append((c.id, c.path))
    return out


def move(project, cat, who, parent_id):
    """Hang `cat` with everything below it under the category `parent_id` (None: as a main category). One step in the
    history ("moved"), to be taken back; whoever has passages in it is asked "Passt / Meine Stellen herauslösen".
    Returns a problem or None."""
    cats = {c.id: c for c in live(project)}
    if cat.id not in cats:
        abort(404)
    parent = cats.get(parent_id) if parent_id is not None else None
    if parent_id is not None and (parent is None or parent.kind == CodingCategory.CANDIDATE):
        return de_en("Bitte die Kategorie wählen, unter die sie kommt.", "Please choose the category to put it under.")
    if parent is None and cat.kind == CodingCategory.MAIN:
        return de_en("„{name}“ ist schon eine Hauptkategorie.", "“{name}” is already a main category.", name=cat.name)
    if parent is not None and parent.id == cat.parent_id:
        return de_en("„{name}“ steht schon dort.", "“{name}” already stands there.", name=cat.name)
    if not may_move(project, cat, who) or (parent is None and not may_create_main(project, who)):
        return de_en("Hauptkategorien ändern nur die Forschenden.", "Only the researchers change main categories.")
    below = subtree(project, cat)
    if parent is not None and parent.id in below:
        return de_en("Nicht unter sich selbst oder etwas, das darunter steht.", "Not under itself or something below it.")
    if parent is not None and level_of(parent, cats) + height(project, cat) > MAX_DEPTH:
        return de_en("Dann stünde etwas tiefer als {n} Ebenen.", "Something would then stand deeper than {n} levels.", n=MAX_DEPTH)
    new_kind = CodingCategory.SUB if parent is not None else CodingCategory.MAIN
    siblings = [c for c in cats.values() if c.id != cat.id and c.parent_id == getattr(parent, "id", None) and
                (c.kind == CodingCategory.SUB) == (new_kind == CodingCategory.SUB)]
    if any(_key(c.name) == _key(cat.name) for c in siblings):
        return de_en("Dort gibt es „{name}“ schon.", "“{name}” already exists there.", name=cat.name)
    tree(project)  # paths for the history
    old = cats.get(cat.parent_id)
    before = {"parent": cat.parent_id, "kind": cat.kind, "name": cat.name, "where": getattr(old, "path", "") if old else ""}
    cat.parent_id, cat.kind = getattr(parent, "id", None), new_kind
    cat.updated_at = utcnow()
    from app.transcript_coding_review import affected

    _event(project, who, "moved", cat, {"before": before, "after": {"parent": cat.parent_id, "kind": cat.kind, "name": cat.name,
                                                                    "where": getattr(parent, "path", "") if parent else ""},
                                        "ids": sorted(below), "affected": affected(project, below, but=who.key)})
    return None


def reorder(project, cat, who, before_id):
    """Put `cat` among the categories under the same one (or the main categories) before `before_id`, at the end for
    None (dragging between siblings, Ralf 2026-10-28). One step in the history, to take back. Returns a problem or None."""
    if not may_move(project, cat, who):
        return de_en("Hauptkategorien ändern nur die Forschenden.", "Only the researchers change main categories.")
    siblings = [c for c in live(project) if c.parent_id == cat.parent_id and
                (c.kind == CodingCategory.SUB) == (cat.kind == CodingCategory.SUB)]
    if before_id is not None and before_id not in {c.id for c in siblings}:
        return de_en("Nur zwischen Kategorien derselben Ebene.", "Only between categories of the same level.")
    before = {c.id: c.position for c in siblings}
    rest = [c for c in siblings if c.id != cat.id]
    at = next((n for n, c in enumerate(rest) if c.id == before_id), len(rest))
    ordered = rest[:at] + [cat] + rest[at:]
    slots = sorted(c.position for c in siblings)  # the same positions, in the new order (others keep theirs)
    for c, p in zip(ordered, slots):
        c.position = p
    if all(before[c.id] == c.position for c in siblings):
        return None
    _event(project, who, "ordered", cat, {"before": {str(k): v for k, v in before.items()}, "after": {"name": cat.name}})
    return None


def propose(project, cat, who, values, reason=""):
    """A proposed change of name, definition or delimitation by someone who may not change the category directly."""
    values = {k: v for k, v in values.items() if k in FIELDS and v and v != getattr(cat, k)}
    if not values:
        return de_en("Der Vorschlag ändert nichts.", "The proposal changes nothing.")
    if open_proposals(project, cat.id, who.key):
        return de_en("Sie haben hier schon einen offenen Vorschlag.", "You already have an open proposal here.")
    _event(project, who, "proposal", cat, {"proposed": values, "before": {k: getattr(cat, k) for k in values}},
           reason=reason, status="open")
    return None


def propose_move(project, cat, who, parent_id, reason=""):
    """A proposed move of someone else's subcategory (setting `guarded`); the checks of move() first, so that only
    what could be done is proposed."""
    if open_proposals(project, cat.id, who.key):
        return de_en("Sie haben hier schon einen offenen Vorschlag.", "You already have an open proposal here.")
    cats = {c.id: c for c in live(project)}
    parent = cats.get(parent_id) if parent_id is not None else None
    if parent is None or parent.kind == CodingCategory.CANDIDATE or parent.id == cat.parent_id:
        return de_en("Bitte die Kategorie wählen, unter die sie kommt.", "Please choose the category to put it under.")
    if parent.id in subtree(project, cat):
        return de_en("Nicht unter sich selbst oder etwas, das darunter steht.", "Not under itself or something below it.")
    if level_of(parent, cats) + height(project, cat) > MAX_DEPTH:
        return de_en("Dann stünde etwas tiefer als {n} Ebenen.", "Something would then stand deeper than {n} levels.", n=MAX_DEPTH)
    tree(project)
    _event(project, who, "proposal", cat, {"move": {"parent": parent.id, "where": getattr(parent, "path", parent.name)}},
           reason=reason, status="open")
    return None


def propose_merge(project, source, target, who, reason="", values=None):
    """A proposed merge of someone else's subcategory into another one (setting `guarded`)."""
    if open_proposals(project, source.id, who.key):
        return de_en("Sie haben hier schon einen offenen Vorschlag.", "You already have an open proposal here.")
    if not " ".join(str(reason or "").split()):
        return de_en("Bitte eine Begründung angeben.", "Please give a reason.")
    keep = {k: str(v)[:4000] for k, v in (values or {}).items() if k in ("name", "definition", "delimitation", "anchor") and v}
    _event(project, who, "proposal", source, {"merge": {"target": target.id, "target_name": target.name, "values": keep}},
           reason=" ".join(str(reason).split())[:1000], status="open")
    return None


def open_proposals(project, cat_id=None, actor_key=None):
    query = db.select(CodingEvent).filter_by(project_id=project.id, kind="proposal", status="open")
    if cat_id is not None:
        query = query.filter_by(category_id=cat_id)
    if actor_key is not None:
        query = query.filter_by(actor_key=actor_key)
    return list(db.session.scalars(query.order_by(CodingEvent.id)))


def decide(project, event, who, accept):
    """Take on or decline a proposal: whoever may change the category directly."""
    cat = db.session.get(CodingCategory, event.category_id) if event.category_id else None
    if cat is None or cat.project_id != project.id or event.kind != "proposal" or event.status != "open":
        abort(404)
    if not may_edit(project, cat, who):
        abort(403)
    if accept:
        data = event.data or {}
        if data.get("move"):  # a proposed move (setting `guarded`)
            problem = move(project, cat, who, data["move"].get("parent"))
        elif data.get("merge"):  # a proposed merge
            from app.transcript_coding_review import merge

            target = db.session.get(CodingCategory, data["merge"].get("target") or 0)
            if target is None or target.project_id != project.id or target.removed_at or target.merged_into:
                return de_en("Die Zielkategorie gibt es nicht mehr.", "The target category no longer exists.")
            problem = merge(project, cat, target, who, event.reason or data["merge"].get("target_name", ""),
                            dict(data["merge"].get("values") or {}) or None)
        else:
            problem = change(project, cat, who, dict(data.get("proposed") or {}))
        if problem:
            return problem
    event.status = "accepted" if accept else "declined"
    _event(project, who, "accepted" if accept else "declined", cat, reply_to=event.id)
    return None


def undoable(project, who):
    """The own last step that can still be taken back: one of UNDO_KINDS not taken back, after which nobody changed
    that category."""
    for event in db.session.scalars(db.select(CodingEvent).filter_by(project_id=project.id, actor_key=who.key)
                                    .where(CodingEvent.kind.in_(UNDO_KINDS)).order_by(CodingEvent.id.desc()).limit(20)):
        if (event.data or {}).get("undone"):
            continue
        last = _last(project, event.category_id)
        return event if last is not None and last.id == event.id else None
    return None


def undo(project, who):
    """Take back the own last step (`undoable`); returns a problem or None."""
    event = undoable(project, who)
    cat = db.session.get(CodingCategory, event.category_id) if event is not None and event.category_id else None
    if cat is None:
        return de_en("Nichts zum Zurücknehmen.", "Nothing to take back.")
    before = (event.data or {}).get("before")
    if event.kind == "ordered":  # the order of before among its siblings
        if not may_move(project, cat, who):
            return de_en("Hauptkategorien ändern inzwischen nur die Forschenden.", "Only the researchers change main categories now.")
        for cid, pos in (before or {}).items():
            c = db.session.get(CodingCategory, int(cid))
            if c is not None and c.project_id == project.id:
                c.position = pos
        event.data = {**(event.data or {}), "undone": True}
        _event(project, who, "undone", cat, {"after": _state(cat)}, reply_to=event.id)
        return None
    if event.kind == "moved":  # back where it stood, if that still works
        back = before.get("parent")
        cats = {c.id: c for c in live(project)}
        if before.get("kind") == CodingCategory.MAIN and not may_create_main(project, who) or not may_move(project, cat, who):
            return de_en("Hauptkategorien ändern inzwischen nur die Forschenden.", "Only the researchers change main categories now.")
        if back is not None and (back not in cats or back in subtree(project, cat)
                                 or level_of(cats[back], cats) + height(project, cat) > MAX_DEPTH):
            return de_en("Der alte Platz ist nicht mehr frei.", "The old place is no longer free.")
        cat.parent_id, cat.kind = back, before.get("kind")
        cat.updated_at = utcnow()
        event.data = {**(event.data or {}), "undone": True}
        _event(project, who, "undone", cat, {"after": _state(cat)}, reply_to=event.id)
        return None
    if not may_edit(project, cat, who):  # e.g. the main categories were fixed since
        return de_en("Diese Kategorie ändern inzwischen nur die Forschenden.", "Only the researchers change this category now.")
    if event.kind == "created":
        if uses(project, cat.id) or any(c.parent_id == cat.id for c in live(project)):
            return de_en("Die Kategorie wird schon genutzt.", "The category is already in use.")
        cat.removed_at = utcnow()
    else:
        for k in FIELDS + ("kind",):
            setattr(cat, k, before[k])
        cat.removed_at = utcnow() if before.get("removed") else None
    cat.updated_at = utcnow()
    event.data = {**(event.data or {}), "undone": True}
    _event(project, who, "undone", cat, {"after": _state(cat)}, reply_to=event.id)
    return None


def undo_label(event):
    if event is None:
        return ""
    name = ((event.data or {}).get("after") or {}).get("name") or ""
    return {"created": de_en("„{name}“ angelegt", "“{name}” created", name=name),
            "changed": de_en("„{name}“ geändert", "“{name}” changed", name=name),
            "removed": de_en("„{name}“ entfernt", "“{name}” removed", name=name),
            "promoted": de_en("„{name}“ übernommen", "“{name}” taken on", name=name),
            "moved": de_en("„{name}“ umgehängt", "“{name}” moved", name=name),
            "ordered": de_en("„{name}“ umsortiert", "“{name}” reordered", name=name)}.get(event.kind, "")


def history(project, limit=30):
    return list(db.session.scalars(db.select(CodingEvent).filter_by(project_id=project.id)
                                   .order_by(CodingEvent.id.desc()).limit(limit)))


def event_text(event, names):
    """One line of the history in words."""
    data = event.data or {}
    name = (data.get("after") or data.get("before") or {}).get("name") or names.get(event.category_id, "")
    return {"created": de_en("legt „{name}“ an", "creates “{name}”", name=name),
            "changed": de_en("ändert „{name}“", "changes “{name}”", name=name),
            "removed": de_en("entfernt „{name}“", "removes “{name}”", name=name),
            "restored": de_en("holt „{name}“ zurück", "restores “{name}”", name=name),
            "promoted": de_en("übernimmt „{name}“ als Hauptkategorie", "takes “{name}” on as a main category", name=name),
            "proposal": (de_en("schlägt vor, „{name}“ unter „{where}“ zu hängen", "proposes to put “{name}” under “{where}”",
                               name=name, where=(data.get("move") or {}).get("where", "")) if data.get("move") else
                         de_en("schlägt vor, „{name}“ mit „{target}“ zusammenzuführen", "proposes to merge “{name}” with “{target}”",
                               name=name, target=(data.get("merge") or {}).get("target_name", "")) if data.get("merge") else
                         de_en("schlägt eine Änderung an „{name}“ vor", "proposes a change to “{name}”", name=name)),
            "accepted": de_en("übernimmt den Vorschlag zu „{name}“", "takes on the proposal for “{name}”", name=name),
            "declined": de_en("lehnt den Vorschlag zu „{name}“ ab", "declines the proposal for “{name}”", name=name),
            "undone": de_en("nimmt den letzten Schritt an „{name}“ zurück", "takes back the last step on “{name}”", name=name),
            "ordered": de_en("sortiert „{name}“ um", "reorders “{name}”", name=name),
            "summarised": de_en("fasst {label} zu „{name}“ zusammen", "summarises {label} on “{name}”",
                                label=data.get("label") or "", name=name),
            "moved": (de_en("hängt „{name}“ unter „{where}“", "puts “{name}” under “{where}”", name=name, where=(data.get("after") or {}).get("where"))
                      if (data.get("after") or {}).get("where") else
                      de_en("macht „{name}“ zur Hauptkategorie", "makes “{name}” a main category", name=name)),
            }.get(event.kind, event.kind)


# --- members -------------------------------------------------------------------------------------------------------


def members(project):
    return list(db.session.scalars(db.select(CodingMember).filter_by(project_id=project.id).order_by(CodingMember.id)))


def member_name(row):
    from app.models import InterviewerAccess, User, WorkGroup

    if row.kind == CodingMember.PERSON:
        user = db.session.get(User, row.user_id) if row.user_id else None
        return user.display_name if user is not None else de_en("gelöschtes Konto", "deleted account")
    if row.kind == CodingMember.GROUP:
        group = db.session.get(WorkGroup, row.group_id) if row.group_id else None
        return group.name if group is not None else de_en("gelöschte Gruppe", "deleted group")
    access = db.session.get(InterviewerAccess, row.access_id) if row.access_id else None
    return access.name if access is not None else de_en("gelöschter Interviewlink", "deleted interviewer link")


def add_member(project, kind, by, user=None, group=None, access=None):
    """A person, a group or an interviewer link as member with the role "Kodieren"; returns a problem or None."""
    if kind == CodingMember.PERSON:
        if user is None:
            return de_en("Diese Person ist nicht bekannt.", "This person is not known.")
        if db.session.scalar(db.select(CodingMember.id).filter_by(project_id=project.id, kind=kind, user_id=user.id)):
            return de_en("{name} kodiert hier schon.", "{name} already codes here.", name=user.display_name)
        row = CodingMember(project_id=project.id, kind=kind, user_id=user.id)
    elif kind == CodingMember.GROUP:
        if group is None:
            return de_en("Diese Gruppe ist nicht bekannt.", "This group is not known.")
        if db.session.scalar(db.select(CodingMember.id).filter_by(project_id=project.id, kind=kind, group_id=group.id)):
            return de_en("Die Gruppe {name} kodiert hier schon.", "The group {name} already codes here.", name=group.name)
        row = CodingMember(project_id=project.id, kind=kind, group_id=group.id)
    elif kind == CodingMember.INTERVIEWER:
        if access is None or access.survey_id != project.survey_id:
            abort(404)
        if interviewer_member(project, access) is not None:
            return None
        row = CodingMember(project_id=project.id, kind=kind, access_id=access.id)
    else:
        abort(400)
    row.added_by_id, row.added_by_name = by.id, (by.name or "")[:200]
    db.session.add(row)
    return None


# --- the pages -----------------------------------------------------------------------------------------------------


VIEWS = ("interviews", "categories", "settings")  # the views of the page of an analysis; overview and check have routes
SETTINGS_ANCHORS = ("einstellungen", "mitglieder", "kodierlinks")


def view_of(anchor):
    """The view of the page an anchor of before belongs to (the page had all of them on one page until 2026-10-28)."""
    if anchor in SETTINGS_ANCHORS:
        return "settings"
    if anchor == "kategorien" or (anchor[:1] == "k" and anchor[1:].isdigit()):
        return "categories"
    return "interviews"


def _project_url(survey, project, anchor="", view=None):
    view = view or view_of(anchor)
    return (url_for("transcript_coding.project", code=survey.code, pid=project.id, view=None if view == "interviews" else view)
            + (f"#{anchor}" if anchor else ""))


@bp.route("/<code>/interviews/coding")
@signed_in_or_coder
def index(code):
    """The analyses of a guide: the team sees all and makes new ones; a member is led to their analysis."""
    survey = guide(code)
    if not team(survey):
        from app import experimental

        mine = projects_for(survey, current_user) if current_user.is_authenticated and experimental.enabled(FEATURE, survey=survey) else []
        if len(mine) == 1:
            return redirect(_project_url(survey, mine[0]))
        if not mine:
            abort(404)
        return render_template("transcript_coding/mine.html", survey=survey, projects=mine)
    survey = _team_survey(code)
    rows = projects(survey)
    counts = {p.id: (len([c for c in live(p) if c.kind == CodingCategory.MAIN]), len([c for c in live(p) if c.kind == CodingCategory.SUB]),
                     len(members(p)))
              for p in rows}
    from app import interview

    return render_template("transcript_coding/index.html", survey=survey, projects=rows, counts=counts,
                           blocks=interview.guide_blocks(survey), n_interviews=len(interviews_all(survey)))


def interviews_all(survey):
    from app import interview_data

    return interview_data.interviews(survey, source="real")


@bp.route("/<code>/interviews/coding/new", methods=["POST"])
@signed_in_or_coder
def new(code):
    survey = _team_survey(code)
    start = request.form.get("start", "guide")
    blocks = request.form.getlist("blocks", type=int) if request.form.get("which") == "some" else None
    if blocks is not None and not blocks:
        flash(de_en("Bitte mindestens einen Themenblock wählen.", "Please choose at least one topic block."), "error")
        return redirect(url_for("transcript_coding.index", code=survey.code))
    project, problem = create_project(survey, request.form.get("name"), request.form.get("question"), start, blocks,
                                      request.form.get("copy_of", type=int), current_user)
    if problem:
        db.session.rollback()
        flash(problem, "error")
        return redirect(url_for("transcript_coding.index", code=survey.code))
    db.session.commit()
    flash(de_en("Auswertung „{name}“ angelegt.", "Analysis “{name}” created.", name=project.name), "success")
    return redirect(_project_url(survey, project))


@bp.route("/<code>/interviews/coding/<int:pid>")
@signed_in_or_coder
def project(code, pid):
    survey, project, who = _in(code, pid)
    from app import interviewer, transcript_coding_double, transcript_coding_review

    view = request.args.get("view") if request.args.get("view") in VIEWS else "interviews"
    if view == "settings" and not who.team:
        abort(404)  # the settings are the team's (checked here, not only hidden)
    from app.groups import of as groups_of

    mains, candidates = tree(project)
    names = {c.id: c.name for c in live(project)}
    proposals = open_proposals(project)
    # the category system as a list (the default) or as a map (from "Übersicht" until 2026-10-28)
    if view == "categories" and request.args.get("show") == "map":  # the name of before (Ralf 2026-10-28: "Diagramm")
        return redirect(url_for("transcript_coding.project", code=survey.code, pid=project.id, view="categories", show="diagram",
                                **({"merge": request.args["merge"]} if request.args.get("merge") else {})))
    show = "map" if view == "categories" and request.args.get("show") == "diagram" else "list"
    context = dict(survey=survey, project=project, who=who, mains=mains, candidates=candidates, view=view, show=show,
                   max_depth=MAX_DEPTH, may_move=lambda c: may_move(project, c, who),
                   drop_data=drop_data(project, who, mains, candidates) if view == "categories" else None,
                   move_targets=lambda c: move_targets(project, c, who, mains),
                   map_svg=transcript_coding_review.map_svg(project, who, survey, interactive=True) if show == "map" else "",
                   proposals=proposals, may_edit=lambda c: may_edit(project, c, who),
                   may_main=may_create_main(project, who), undo_event=undoable(project, who), undo_label=undo_label,
                   history=history(project), event_text=lambda e: event_text(e, names),
                   interviews=interviews(project, who), has_transcript=has_transcript,
                   own_proposal=lambda c: bool(open_proposals(project, c.id, who.key)),
                   proposals_of=lambda c: [e for e in proposals if e.category_id == c.id],
                   notices=transcript_coding_review.notices(project, who),
                   ai_switch=who.team and _ai_on(),
                   double_of=lambda rid: transcript_coding_double.assignment(project, rid),
                   double_finished=transcript_coding_double.finished,
                   double_people=transcript_coding_double.assignable(project, survey) if who.team else [],
                   coded=lambda r: len(transcript_coding_double.visible(project, who, r, [
                       c for c in ((r.interview or {}).get("codings") or []) if c.get("project") == project.id])))
    from app import transcript_coding_memos

    context.update(guarded=guarded(project), may_propose_move=lambda c: may_propose_move(project, c, who),
                   siblings=sibling_lists(project))
    cat_memos = transcript_coding_memos.visible(project, who, kind=CodingMemo.CATEGORY)
    context.update(memos=[transcript_coding_memos.as_json(m, who) for m in cat_memos],
                   memo_counts=transcript_coding_memos.counts(cat_memos))
    if who.team:
        accesses = interviewer.accesses(survey)
        context.update(members=members(project), member_name=member_name, accesses=accesses,
                       coders=[a for a in accesses if may_code(a)], coder_mode=coder_mode(project),
                       ticked={m.access_id for m in members(project) if m.kind == CodingMember.INTERVIEWER},
                       groups=groups_of(current_user, within=survey), people=_people(survey, project),
                       access_names={a.id: a.name for a in accesses},
                       others=[p for p in projects(survey) if p.id != project.id])
    if view == "interviews":  # the search of the material (app/transcript_coding_search.py)
        from app import transcript_coding_search

        context["search"] = transcript_coding_search.results(project, who, request.args) if request.args.get("q") else None
    return render_template("transcript_coding/project.html", **context)


def _ai_on():
    from app import ai_access

    return ai_access.ai_on()


def _people(survey, project):
    """Whom the team can add as a person: the survey's people without "Bearbeiten" and the colleagues of the groups of
    the one asking, without those already in: [(key, name)]."""
    from app import people_keys
    from app.groups import colleagues

    taken = {m.user_id for m in members(project) if m.kind == CodingMember.PERSON}
    out = {}
    for m in survey.members:
        if m.user_id and m.user_id not in taken and m.role == "viewer" and m.user is not None:
            out[m.user_id] = (people_keys.key_for(m.user), m.display_name)
    editors = {m.user_id for m in survey.members if m.role != "viewer"}
    for key, name in colleagues(current_user):
        user = people_keys.account(key, create=False)
        if user is not None and user.id not in taken and user.id not in editors:
            out.setdefault(user.id, (key, name))
    return sorted(out.values(), key=lambda kv: kv[1].casefold())


@bp.route("/<code>/interviews/coding/<int:pid>/settings", methods=["POST"])
@signed_in_or_coder
@autosave.aware
def settings(code, pid):
    """Name, research question, which interviews members see, "Hauptkategorien festgelegt" (the team)."""
    survey, project, who = _in(code, pid, team_only=True)
    name = _clean(request.form.get("name"), NAME_MAX)
    if not name:
        flash(de_en("Bitte einen Namen angeben.", "Please enter a name."), "error")
        return redirect(_project_url(survey, project))
    if any(p.id != project.id and p.name.casefold() == name.casefold() for p in projects(survey)):
        flash(de_en("Eine Auswertung „{name}“ gibt es schon.", "There is already an analysis “{name}”.", name=name), "error")
        return redirect(_project_url(survey, project))
    project.name = name
    project.question = _text(request.form.get("question"), QUESTION_MAX)
    project.visibility = CodingProject.OWN if request.form.get("visibility") == CodingProject.OWN else CodingProject.ALL
    project.mains_fixed = bool(request.form.get("mains_fixed"))
    if request.form.get("has_guarded"):  # a form of before without the field leaves the setting as it is
        project.settings = {**(project.settings or {}), "guarded": bool(request.form.get("guarded"))}
    project.updated_at = utcnow()
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(_project_url(survey, project))


@bp.route("/<code>/interviews/coding/<int:pid>/delete", methods=["POST"])
@signed_in_or_coder
def delete(code, pid):
    survey, project, who = _in(code, pid, team_only=True)
    name = project.name
    delete_project(project)
    db.session.commit()
    flash(de_en("Auswertung „{name}“ gelöscht.", "Analysis “{name}” deleted.", name=name), "success")
    return redirect(url_for("transcript_coding.index", code=survey.code))


@bp.route("/<code>/interviews/coding/<int:pid>/similar")
@signed_in_or_coder
def similar_names(code, pid):
    """The categories like a name being typed, for the dialog "Neue Kategorie" (shown before anything is made)."""
    survey, project, who = _in(code, pid)
    tree(project)  # sets `path`
    names = {c.id: getattr(c, "path", c.name) for c in live(project)}
    return jsonify([{"id": c.id, "name": c.name, "kind": c.kind, "parent": names.get(c.parent_id, ""),
                     "definition": c.definition[:300], "author": c.author_name,
                     "former": list(c.former_names or [])} for c, _score in similar(project, request.args.get("q", ""))])


@bp.route("/<code>/interviews/coding/<int:pid>/categories/new", methods=["POST"])
@signed_in_or_coder
def add(code, pid):
    survey, project, who = _in(code, pid)
    name = request.form.get("name", "")
    # the similar ones first: a new one only once they were shown (the dialog shows them while typing)
    if similar(project, name) and not request.form.get("anyway"):
        flash(de_en("Es gibt schon ähnliche Kategorien: {names}. Bitte prüfen, ob eine davon passt, sonst „Trotzdem neu anlegen“.",
                    "There are similar categories already: {names}. Please check whether one of them fits, else “Create anyway”.",
                    names=", ".join(f"„{c.name}“" for c, _s in similar(project, name))), "error")
        return redirect(_project_url(survey, project, "kategorien"))
    cat, problem = create(project, who, request.form.get("kind", CodingCategory.SUB), name, request.form.get("definition"),
                          request.form.get("delimitation"), request.form.get("parent", type=int),
                          after=after_value(request.form.get("after")))
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("„{name}“ angelegt.", "“{name}” created.", name=cat.name) if cat.kind != CodingCategory.CANDIDATE else
              de_en("„{name}“ als Kandidat für eine neue Hauptkategorie angelegt.", "“{name}” created as a candidate for a new main category.", name=cat.name),
              "success")
    return redirect(_project_url(survey, project, f"k{cat.id}" if cat else "kategorien"))


def _category(project, cat_id):
    cat = db.session.get(CodingCategory, cat_id)
    if cat is None or cat.project_id != project.id or cat.removed_at is not None or cat.merged_into is not None:
        abort(404)
    return cat


@bp.route("/<code>/interviews/coding/<int:pid>/categories/<int:cid>", methods=["POST"])
@signed_in_or_coder
@autosave.aware
def edit(code, pid, cid):
    """Name, definition and delimitation of one category, as the page saves them while typing."""
    survey, project, who = _in(code, pid)
    cat = _category(project, cid)
    problem = change(project, cat, who, _values(request.form), merge=autosave.merge(f"cat{cat.id}"))
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(_project_url(survey, project, f"k{cat.id}"))


@bp.route("/<code>/interviews/coding/<int:pid>/categories/<int:cid>/move", methods=["POST"])
@signed_in_or_coder
def move_category(code, pid, cid):
    """"Verschieben nach …" and drag & drop in the list: `parent` the category to put it under, empty for a main
    category; `json=1` answers with JSON for the list's script."""
    survey, project, who = _in(code, pid)
    cat = _category(project, cid)
    raw = str(request.form.get("parent", ""))
    proposed = may_propose_move(project, cat, who)
    if proposed:  # setting "only team and author": a proposal instead (Ralf 2026-10-29)
        problem = propose_move(project, cat, who, int(raw) if raw.isdigit() else None, request.form.get("reason", ""))
    else:
        problem = move(project, cat, who, int(raw) if raw.isdigit() else None)
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("Vorschlag gesendet: „{name}“ umhängen. Team und Autor entscheiden.",
                    "Proposal sent: move “{name}”. The team and the author decide.", name=cat.name) if proposed else
              de_en("„{name}“ umgehängt.", "“{name}” moved.", name=cat.name), "success")
    if request.form.get("json"):
        return jsonify(ok=not problem, error=str(problem or ""), **({"proposed": True} if proposed and not problem else {}))
    return redirect(_project_url(survey, project, f"k{cat.id}", view="categories"))


@bp.route("/<code>/interviews/coding/<int:pid>/categories/<int:cid>/order", methods=["POST"])
@signed_in_or_coder
def order_category(code, pid, cid):
    """Dragging between siblings (list and map): `before` the sibling to stand before, empty for the end."""
    survey, project, who = _in(code, pid)
    cat = _category(project, cid)
    raw = str(request.form.get("before", ""))
    problem = reorder(project, cat, who, int(raw) if raw.isdigit() else None)
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
    if request.form.get("json"):
        return jsonify(ok=not problem, error=str(problem or ""))
    return redirect(_project_url(survey, project, f"k{cat.id}", view="categories"))


@bp.route("/<code>/interviews/coding/<int:pid>/categories/<int:cid>/remove", methods=["POST"])
@signed_in_or_coder
def remove_category(code, pid, cid):
    survey, project, who = _in(code, pid)
    cat = _category(project, cid)
    problem = remove(project, cat, who)
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("„{name}“ entfernt.", "“{name}” removed.", name=cat.name), "success")
    return redirect(_project_url(survey, project, "kategorien"))


@bp.route("/<code>/interviews/coding/<int:pid>/categories/<int:cid>/promote", methods=["POST"])
@signed_in_or_coder
def promote_category(code, pid, cid):
    survey, project, who = _in(code, pid, team_only=True)
    cat = _category(project, cid)
    promote(project, cat, who)
    db.session.commit()
    flash(de_en("„{name}“ ist jetzt eine Hauptkategorie.", "“{name}” is now a main category.", name=cat.name), "success")
    return redirect(_project_url(survey, project, f"k{cat.id}"))


@bp.route("/<code>/interviews/coding/<int:pid>/categories/<int:cid>/propose", methods=["POST"])
@signed_in_or_coder
def propose_change(code, pid, cid):
    survey, project, who = _in(code, pid)
    cat = _category(project, cid)
    problem = propose(project, cat, who, _values(request.form), request.form.get("reason", ""))
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("Vorschlag gesendet.", "Proposal sent."), "success")
    return redirect(_project_url(survey, project, f"k{cat.id}"))


@bp.route("/<code>/interviews/coding/<int:pid>/proposals/<int:eid>/<answer>", methods=["POST"])
@signed_in_or_coder
def answer_proposal(code, pid, eid, answer):
    survey, project, who = _in(code, pid)
    event = db.session.get(CodingEvent, eid)
    if event is None or event.project_id != project.id or answer not in ("accept", "decline"):
        abort(404)
    problem = decide(project, event, who, answer == "accept")
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("Vorschlag übernommen.", "Proposal taken on.") if answer == "accept" else
              de_en("Vorschlag abgelehnt.", "Proposal declined."), "success")
    return redirect(_project_url(survey, project, "kategorien"))


@bp.route("/<code>/interviews/coding/<int:pid>/undo", methods=["POST"])
@signed_in_or_coder
def undo_step(code, pid):
    survey, project, who = _in(code, pid)
    problem = undo(project, who)
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("Zurückgenommen.", "Taken back."), "success")
    return redirect(_project_url(survey, project, "kategorien"))


@bp.route("/<code>/interviews/coding/<int:pid>/members", methods=["POST"])
@signed_in_or_coder
def add_members(code, pid):
    """A person or a group as member (role "Kodieren")."""
    from app import people_keys
    from app.models import WorkGroup

    survey, project, who = _in(code, pid, team_only=True)
    kind = request.form.get("kind")
    if kind == CodingMember.GROUP:
        group = db.session.get(WorkGroup, request.form.get("group", type=int) or 0)
        from app.groups import of as groups_of

        if group is not None and group.id not in {g.id for g in groups_of(current_user, within=survey)}:
            group = None  # only a group of the one adding, of the guide's organisation
        problem = add_member(project, kind, current_user, group=group)
    else:
        key = " ".join(request.form.get("person", "").split())
        user = people_keys.account(key) if key else None
        problem = add_member(project, CodingMember.PERSON, current_user, user=user)
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("Mitglied hinzugefügt: kodiert mit, ändert den Leitfaden nicht.", "Member added: codes along, does not change the guide."), "success")
    return redirect(_project_url(survey, project, "mitglieder"))


@bp.route("/<code>/interviews/coding/<int:pid>/members/<int:mid>/remove", methods=["POST"])
@signed_in_or_coder
def remove_member(code, pid, mid):
    survey, project, who = _in(code, pid, team_only=True)
    row = db.session.get(CodingMember, mid)
    if row is None or row.project_id != project.id:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    flash(de_en("Mitglied entfernt. Seine Kategorien bleiben.", "Member removed. Their categories stay."), "success")
    return redirect(_project_url(survey, project, "mitglieder"))


@bp.route("/<code>/interviews/coding/<int:pid>/members/<int:mid>/interviews", methods=["POST"])
@signed_in_or_coder
@autosave.aware
def member_interviews(code, pid, mid):
    """With "jede Gruppe ihre eigenen": the interviewer links whose interviews count as this member's own."""
    from app import interviewer

    survey, project, who = _in(code, pid, team_only=True)
    row = db.session.get(CodingMember, mid)
    if row is None or row.project_id != project.id or row.kind == CodingMember.INTERVIEWER:
        abort(404)
    known = {a.id for a in interviewer.accesses(survey)}
    row.access_ids = sorted({a for a in request.form.getlist("access", type=int) if a in known})
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(_project_url(survey, project, "mitglieder"))


@bp.route("/<code>/interviews/coding/<int:pid>/interviewers", methods=["POST"])
@signed_in_or_coder
@autosave.aware
def interviewers(code, pid):
    """Which interviewer links with the right "darf kodieren" code in this analysis (the right itself, own or all
    interviews, is set at the link on the page "Interviews"). Only rows of this module change; the interviewer links
    stay as they are. The assignment of a link without the right stays, unseen, until it gets the right again.
    One step for "Rückgängig"."""
    from app import interviewer, undo

    survey, project, who = _in(code, pid, team_only=True)
    accesses = {a.id: a for a in interviewer.accesses(survey) if may_code(a)}
    wanted = {a for a in request.form.getlist("access", type=int) if a in accesses}
    step = undo.begin(survey)
    mode = request.form.get("who")
    if mode not in (CODERS_ALL, CODERS_SELECTED):
        mode = coder_mode(project)
    elif mode == CODERS_SELECTED and coder_mode(project) == CODERS_ALL:
        wanted = set(accesses)  # just switched over: everyone who coded here so far stays, then untick
    project.settings = {**(project.settings or {}), "interviewers": mode}
    if mode == CODERS_SELECTED:  # with "alle" the ticks stay as they are, for switching back
        for row in [m for m in members(project) if m.kind == CodingMember.INTERVIEWER]:
            if row.access_id in accesses and row.access_id not in wanted:
                db.session.delete(row)
        for aid in wanted:
            add_member(project, CodingMember.INTERVIEWER, current_user, access=accesses[aid])
    step.done(undo.both("Interviewende in „{name}“", "Interviewers in “{name}”", name=project.name), page="coding",
              node=str(project.id))
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return redirect(_project_url(survey, project, "mitglieder"))


@bp.app_context_processor
def _helpers():
    return {"coding_projects_of_interviewer": interviewer_projects}
