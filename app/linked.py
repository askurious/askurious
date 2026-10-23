"""Verknüpfte Elemente: the same question in several surveys, so that its answers can be analysed together.

A linked element is a row of `Construct` with status "linked" and visibility "private" (the toolbox never lists
it): its key starts with "v-", `spec` holds the element as a template like a construct's, `seed_version` is its
Fassung and `created_by_id` the person who made it. Every use is an ordinary question whose config carries
{"construct": {"key", "version", "unlocked", "template"}}, as one inserted from the toolbox; so the lock, the list
of modifications, duplicating a survey and copying a question work for both alike.

An element is either
- open: no use runs and no use has real participations. Whoever may change it (may_change) changes it in any use;
  the change becomes the template and is taken over by every other use in a draft they may edit (save_template).
  A change by someone who may only use it adapts their own use. Nobody has answered it yet, so all
  uses still ask the same question.
- frozen (frozen()): a use runs or real participations exist. Structure and wording are locked; only languages
  the element does not have yet can be filled in. One survey may adapt it ("Entsperren"): the use stays linked
  and is marked as adapted.
Test runs and dummy data never freeze an element.

Who may insert an element (usable): whoever made it, and whoever can open a survey that uses it.
Concept: geteilte-elemente-konzept.md (decisions E1-E12 of 2026-10-03).
"""
import copy
import json
import secrets
from types import SimpleNamespace

from app.extensions import db
from app.lang import de_en
from app.models import Block, Construct, ConstructPick, Item, Response, Survey, User, utcnow

PREFIX = "v-"
STATUS = "linked"
# presentation and analysis choices of one survey: not part of the element, never taken over from another use
# "when": "Nur zeigen, wenn …" names a question of this survey (app/item_condition.py), never part of the element
LOCAL_KEYS = ("construct", "level", "display", "auto_next", "randomize", "when")
NAME_MAX = 120


def key_of(item):
    """The key of the linked element `item` uses, or None."""
    key = (((item.config or {}).get("construct")) or {}).get("key")
    return key if isinstance(key, str) and key.startswith(PREFIX) else None


def is_linked(item):
    return key_of(item) is not None


def get(key):
    if not isinstance(key, str) or not key.startswith(PREFIX):
        return None
    return db.session.scalar(db.select(Construct).filter_by(key=key, status=STATUS))


def element(item):
    key = key_of(item)
    return get(key) if key else None


def adapted(item):
    return bool(((item.config or {}).get("construct") or {}).get("unlocked"))


# ---- which questions can be linked ----

def linkable(item):
    """None if `item` can become a linked element, else why not (a sentence for the researcher)."""
    survey = item.block.survey
    kind = item.kind
    if survey.live is not None:
        return de_en("Fragen einer Live-Abstimmung lassen sich nicht verknüpfen.", "Questions of a live poll cannot be linked.")
    if survey.interview is not None:
        return de_en("Elemente eines Interviewleitfadens lassen sich nicht verknüpfen.",
                     "Elements of an interview guide cannot be linked.")
    if not kind.collects_answer or kind.computed or item.type == "consent" or kind.category == "feedback":
        return de_en("Nur Fragen mit einer Antwort lassen sich verknüpfen.", "Only questions with an answer can be linked.")
    if kind.design_lock:
        return de_en("Elemente mit einem Versuchsplan (Conjoint, MaxDiff, ACBC) lassen sich nicht verknüpfen.",
                     "Elements with an experimental design (conjoint, MaxDiff, ACBC) cannot be linked.")
    if (item.config or {}).get("construct"):
        return de_en("Das Element stammt schon aus „Konstrukte“ oder ist schon verknüpft.",
                     "The element already comes from “Constructs” or is already linked.")
    config = item.config or {}
    # what points at other questions or at the survey around it: the next survey does not have them
    if "{{" in json.dumps(item.texts or {}, ensure_ascii=False):
        return de_en("Der Text zeigt Antworten anderer Fragen oder Werte einer Schleife; in einer anderen Befragung gibt es "
                     "sie nicht.",
                     "The text shows answers to other questions or values of a loop; another survey does not have them.")
    # (the condition of the question itself stays in its survey: LOCAL_KEYS)
    config = {k: v for k, v in config.items() if k != "when"}
    if config.get("carry") or '"when"' in json.dumps(config) or kind.remap_config(copy.deepcopy(config), lambda old: None) != config:
        return de_en("Das Element hängt von anderen Fragen ab (Bedingungen, übernommene Optionen oder Werte).",
                     "The element depends on other questions (conditions, options or values carried over).")
    return None


def _plain_name(item):
    from app.items.base import plain

    text = " ".join(plain(item.text("question", item.block.survey.default_language) or "").split())
    return (text[:NAME_MAX - 1] + "…" if len(text) > NAME_MAX else text) or item.code


def template(item):
    """The element as a template: what every use asks (without the presentation choices of one survey)."""
    config = {k: v for k, v in copy.deepcopy(item.config or {}).items() if k not in LOCAL_KEYS}
    return {"type": item.type, "code": item.code, "config": config, "texts": copy.deepcopy(item.texts or {})}


def _search_text(c):
    from app import constructs

    return constructs._search_text(c)


def create(item, user, name=""):
    """Make `item` the first use of a new linked element (Fassung 1). The caller commits."""
    c = Construct(key=PREFIX + secrets.token_hex(5), name=(" ".join((name or "").split())[:NAME_MAX] or _plain_name(item)),
                  status=STATUS, visibility="private", created_by_id=user.id, seed_version=1,
                  spec={**template(item), "origin_survey_id": item.block.survey_id})
    c.search_text = _search_text(c)
    db.session.add(c)
    # since: participations that began earlier were not told about the shared analysis (app/linked_pool.py)
    item.config = {**(item.config or {}), "construct": {"key": c.key, "version": 1, "unlocked": False,
                                                        "template": copy.deepcopy(c.spec), "since": utcnow().isoformat()}}
    return c


def link_survey(survey, user):
    """Make every question of `survey` that can be linked a linked element (duplicating with the tick "Fragen mit
    dem Original verknüpfen"). Returns (number linked, [codes that could not be linked, with the reason]).
    Questions already linked stay as they are."""
    linked, skipped = 0, []
    for block in survey.blocks:
        for item in block.items:
            if is_linked(item) or not item.kind.collects_answer or item.type in ("consent", "page_break"):
                continue
            if (item.config or {}).get("construct"):  # from the toolbox: linked to the toolbox already
                continue
            reason = linkable(item)
            if reason:
                skipped.append((item.code, reason))
                continue
            create(item, user)
            linked += 1
    return linked, skipped


# ---- uses and state ----

INDEX = "ix_item_construct_key"


# the indexed expression per database: queries must use exactly this text, or the database reads every question
KEY_SQL = {"sqlite": "json_extract(item.config, '$.construct.key')",
           "postgresql": "((item.config -> 'construct') ->> 'key')"}


def _key_column():
    """The key in an item's config as SQL: on SQLite and PostgreSQL exactly the expression of the index INDEX
    (ensure_index), so looking up the uses of an element does not read every question; elsewhere the JSON path
    of SQLAlchemy."""
    expression = KEY_SQL.get(db.engine.dialect.name)
    if expression:
        return db.literal_column(expression)
    return Item.config["construct"]["key"].as_string()


def ensure_index():
    """At start-up (app/__init__.py): the index on the construct key of questions (idempotent)."""
    expression = KEY_SQL.get(db.engine.dialect.name)
    if not expression:
        return
    from sqlalchemy import text

    with db.engine.begin() as con:
        con.execute(text(f"CREATE INDEX IF NOT EXISTS {INDEX} ON item (({expression.replace('item.', '')}))"))


def uses(c):
    """[(item, survey)] of every question that uses element `c`, in the order the surveys were created."""
    rows = db.session.execute(
        db.select(Item, Survey).join(Block, Item.block_id == Block.id).join(Survey, Block.survey_id == Survey.id)
        .filter(_key_column() == c.key).order_by(Survey.id, Block.position, Item.position))
    return [(i, s) for i, s in rows]


def real_counts(survey_ids):
    """{survey id: number of real participations} (not test runs, dummies or previews)."""
    if not survey_ids:
        return {}
    rows = db.session.execute(
        db.select(Response.survey_id, db.func.count(Response.id))
        .filter(Response.survey_id.in_(survey_ids), Response.source == "real", Response.is_preview.is_(False))
        .group_by(Response.survey_id))
    return dict(rows.all())


def version_of(item):
    """The Fassung of the element a use asks (1 for uses from before Fassungen)."""
    return (((item.config or {}).get("construct")) or {}).get("version") or 1


def frozen(c, user=None, version=None):
    """None while Fassung `version` (default: the newest) of element `c` is open, else why it is frozen (a
    sentence). Each Fassung freezes by its own uses: a new Fassung is open until a survey asks it for real. Surveys
    `user` cannot open are not named."""
    from app import access

    version = version or c.seed_version
    surveys = {s.id: s for i, s in uses(c) if version_of(i) == version}
    own = set(db.session.scalars(access.survey_ids(user))) if user is not None else set(surveys)

    def name(s):
        if s.id in own:
            return de_en("„{title}“", "“{title}”", title=s.title)
        return de_en("eine Befragung, die Sie nicht öffnen können,", "a survey you cannot open")

    running = [s for s in surveys.values() if s.status == Survey.RUNNING]
    if running:
        return de_en("Es wird in {name} erhoben.", "It is being collected in {name}.", name=name(running[0]))
    counts = real_counts(list(surveys))
    answered = [surveys[sid] for sid, n in counts.items() if n]
    if answered:
        said = name(answered[0])
        return de_en("{name} hat echte Teilnahmen.", "{name} has real participations.", name=said[0].upper() + said[1:])
    return None


def template_of(item, c):
    """What a use is compared with: the element as it is for the newest Fassung (also while the copy in the
    question's config lags behind, e.g. right after undo), else the copy of its older Fassung."""
    if version_of(item) == c.seed_version:
        return c.spec
    return (((item.config or {}).get("construct")) or {}).get("template") or c.spec


def text_locked(item, lang):
    """True if the texts of `item` in `lang` are locked: a frozen element, not adapted here, in a language the
    element has."""
    if not is_linked(item) or adapted(item):
        return False
    c = element(item)
    template = template_of(item, c) if c is not None else {}
    return c is not None and lang in (template or {}).get("texts", {}) and frozen(c, version=version_of(item)) is not None


def _admin(user):
    """A site admin's rights here, with the code of the second factor while ADMIN_TOTP is on (twofactor.admin_rights)."""
    from app import twofactor

    return twofactor.admin_rights(user)


def usable(user, c):
    """True if `user` may see and insert element `c`."""
    from app import access

    if c is None or user is None or not user.is_authenticated:
        return False
    if _admin(user) or (c.created_by_id == user.id and _editable_uses(user, c)):
        return True
    from app import groups

    if any(share.user_id == user.id for share in c.person_shares):
        return True
    if any(share.group_id in set(db.session.scalars(groups.ids_of(user))) for share in c.group_shares):
        return True
    ids = set(db.session.scalars(access.survey_ids(user)))
    return any(s.id in ids for _, s in uses(c))


def palette(user):
    """The linked elements `user` may insert, by name: their own, those shared with their groups and those used in
    surveys they can open."""
    from app import access, groups
    from app.models import ConstructGroupShare, ConstructShare

    keys = set(db.session.scalars(
        db.select(_key_column()).select_from(Item).join(Block, Item.block_id == Block.id)
        .filter(Block.survey_id.in_(access.survey_ids(user)), _key_column().like(PREFIX + "%"))))
    candidates = list(db.session.scalars(
        db.select(Construct).filter(Construct.status == STATUS,
                                    (Construct.created_by_id == user.id) | Construct.key.in_(keys)
                                    | Construct.id.in_(db.select(ConstructShare.construct_id)
                                                       .filter(ConstructShare.user_id == user.id))
                                    | Construct.id.in_(db.select(ConstructGroupShare.construct_id)
                                                       .filter(ConstructGroupShare.group_id.in_(groups.ids_of(user)))))
        .order_by(Construct.name)))
    return [c for c in candidates if usable(user, c)]


# ---- changes ----

def _codes(other, spec):
    """The statement codes of a use: those it has keep their name, new ones follow its variable name."""
    config = copy.deepcopy(spec["config"])
    for group in ("statements", "pairs"):
        rows = config.get(group)
        if not isinstance(rows, list):
            continue
        mine = {r.get("id"): r.get("code") for r in (other.config or {}).get(group) or [] if isinstance(r, dict)}
        if other.code == spec.get("code") and not mine:
            continue
        taken = set()
        for n, r in enumerate(rows):
            code = mine.get(r.get("id")) or (r.get("code") if other.code == spec.get("code") else f"{other.code}_{n + 1}")
            while code in taken:
                code += "_"
            taken.add(code)
            r["code"] = code
    return config


def apply(other, c):
    """Give the use `other` the template of `c` (its own variable name and presentation choices stay)."""
    spec = c.spec
    config = _codes(other, spec)
    for k in LOCAL_KEYS:
        if k in (other.config or {}):
            config[k] = copy.deepcopy(other.config[k])
    config["construct"] = {**(other.config or {}).get("construct", {}), "key": c.key, "version": c.seed_version,
                           "template": copy.deepcopy(spec)}
    texts = copy.deepcopy(other.texts or {})
    texts.update(copy.deepcopy(spec.get("texts") or {}))
    other.config, other.texts = config, texts


def _differs(item, c):
    """True if the use `item` asks something else than element `c` would give it (apply): its own variable name,
    statement codes and presentation choices do not count."""
    spec = c.spec
    own = {k: v for k, v in (item.config or {}).items() if k not in LOCAL_KEYS}
    texts = item.texts or {}
    from app import item_texts

    def same(lang, t):  # a stored copy of a standard wording is the same as none (app/item_texts.py, S9)
        def bare(x):
            return item_texts.strip(item.kind, {lang: x or {}}, lang)
        return texts.get(lang) == t or bare(texts.get(lang)) == bare(t)
    return (item.type != spec.get("type") or own != _codes(item, spec)
            or any(not same(lang, t) for lang, t in (spec.get("texts") or {}).items()))


def save_template(item, user, touch=True):
    """After `user` saved `item`: while its element is open, the item is the new template and every other use in a
    draft takes it over (adapted uses keep theirs). Only someone who may change the element (may_change) makes a new
    template, and it reaches only drafts `user` may edit ("Bearbeiten"); a change by anyone else adapts this use
    ("unlocked") and the element stays (security audit 2026-10-06, S1). Returns the number of other uses changed.
    The caller commits. touch=False leaves the item's own config alone (after undo: a change there would block the
    next undo step, which checks that the question is as the step left it; template_of reads the element instead)."""
    from app import undo

    c = element(item)
    if c is None or adapted(item) or version_of(item) != c.seed_version or frozen(c) is not None:
        return 0
    meta = (item.config or {}).get("construct") or {}
    if not may_change(user, c):
        if touch and _differs(item, c):
            item.config = {**item.config, "construct": {**meta, "unlocked": True}}
        return 0
    known = meta.get("template")
    if known is not None and known != c.spec:
        # the use lags behind the element: it was in the bin (or taken back by undo) while another use changed the
        # element. It never becomes the template from there, or it would silently take back the newer wording in
        # every other draft (code review 2026-10-07, E8). Unchanged since, it takes the element's state; changed
        # from the older state, it counts as adapted. touch=False (after undo) leaves it as it is.
        if touch and _differs(item, SimpleNamespace(spec=known)):
            item.config = {**item.config, "construct": {**meta, "unlocked": True}}
        elif touch:
            apply(item, c)
        return 0
    origin_id = (c.spec or {}).get("origin_survey_id")
    spec = {**template(item), **({"origin_survey_id": origin_id} if origin_id else {})}
    if spec != c.spec:
        c.spec = spec
        c.search_text = _search_text(c)
    if touch and (meta.get("template") != c.spec or meta.get("version") != c.seed_version):
        item.config = {**item.config, "construct": {**meta, "version": c.seed_version, "template": copy.deepcopy(c.spec)}}
    changed = 0
    for other, survey in uses(c):
        if other.id == item.id or adapted(other) or not survey.is_editable or version_of(other) != c.seed_version:
            continue
        if not survey.can(user, "editor"):  # a draft the person may not edit keeps the element as it was
            continue
        if ((other.config or {}).get("construct") or {}).get("template") == c.spec and other.type == c.spec["type"]:
            continue
        step = undo.begin(survey)
        apply(other, c)
        step.done(undo.both("„{code}“ vom verknüpften Element übernommen", "“{code}” updated from the linked element", code=other.code),
                  page="item", item=other.id)
        changed += 1
    return changed


def may_change(user, c):
    """The maker needs editing rights on the source; its admins recover authority after their departure.
    Ownerless elements are managed by editors of a use; dissolving only touches editable uses (AZ-11)."""
    if _admin(user):
        return True
    rows = uses(c)
    origin_id = (c.spec or {}).get("origin_survey_id")
    origin = db.session.get(Survey, origin_id) if origin_id else min((s for _, s in rows), key=lambda s: s.id, default=None)
    if c.created_by_id == user.id and origin is not None and origin.can(user, "editor"):
        return True
    creator = db.session.get(User, c.created_by_id) if c.created_by_id else None
    if origin is not None and origin.can(user, "admin") and (creator is None or not origin.can(creator, "editor")):
        return True
    if c.created_by_id is not None:
        return False
    return bool(_editable_uses(user, c))


def _editable_uses(user, c):
    from app import access

    ids = set(db.session.scalars(access.survey_ids(user)))
    return [(i, s) for i, s in uses(c) if s.id in ids and s.can(user, "editor")]


def dissolvable(user, c):
    """Site admins may dissolve all uses; everyone else only those they may edit, with ownership authority."""
    if _admin(user):
        return uses(c)
    return _editable_uses(user, c) if may_change(user, c) else []


def new_version_refused(item):
    """None if a new Fassung may come from this use, else why not. It comes from the newest Fassung once that is
    frozen (while it is open, changes reach every use anyway), in a draft without real participations: answers
    already given were given to the old Fassung."""
    c = element(item)
    survey = item.block.survey
    if c is None or version_of(item) != c.seed_version:
        return de_en("Neue Fassungen entstehen aus der neuesten.", "New revisions come from the newest one.")
    if frozen(c, version=c.seed_version) is None:
        return de_en("Die Fassung ist offen: Änderungen gelten ohnehin für alle Verwendungen.",
                     "The revision is open: changes apply to all uses anyway.")
    if not survey.is_editable:
        return de_en("Die Befragung läuft oder ist geschlossen.", "The survey is running or closed.")
    if real_counts([survey.id]).get(survey.id):
        return de_en("Diese Befragung hat schon echte Teilnahmen mit Fassung {v}. Eine neue Fassung entsteht in "
                     "einer Befragung ohne Daten, z. B. in einem Duplikat.",
                     "This survey already has real participations with revision {v}. A new revision is made in a survey "
                     "without data, e.g. in a duplicate.", v=c.seed_version)
    return None


def new_version(item):
    """Fassung n+1 from this use (as it is now, also adapted): it becomes the template, starts open and is the only
    use of the new Fassung; surveys asking an older Fassung keep it (switch offers the new one). The caller commits."""
    c = element(item)
    c.seed_version += 1
    origin_id = (c.spec or {}).get("origin_survey_id")
    c.spec = {**template(item), **({"origin_survey_id": origin_id} if origin_id else {})}
    c.search_text = _search_text(c)
    meta = (item.config or {}).get("construct") or {}
    item.config = {**item.config, "construct": {**meta, "version": c.seed_version, "unlocked": False,
                                                "template": copy.deepcopy(c.spec)}}
    return c


def switch_refused(item):
    """None if this use may take the newest Fassung, else why not."""
    c = element(item)
    survey = item.block.survey
    if c is None or version_of(item) >= c.seed_version:
        return de_en("Die Frage stellt schon die neueste Fassung.", "The question already asks the newest revision.")
    if not survey.is_editable:
        return de_en("Die Befragung läuft oder ist geschlossen.", "The survey is running or closed.")
    if real_counts([survey.id]).get(survey.id):
        return de_en("Die Befragung hat schon echte Teilnahmen: Mit einer anderen Fassung wären ihre Antworten nicht mehr "
                     "untereinander vergleichbar.",
                     "The survey already has real participations: with another revision their answers would no longer be "
                     "comparable with each other.")
    return None


def switch(item):
    """This use asks the newest Fassung (its own variable name and presentation choices stay). The caller commits."""
    c = element(item)
    apply(item, c)
    item.config = {**item.config, "construct": {**item.config["construct"], "unlocked": False}}


def candidates(user, item):
    """The linked elements an existing question can be joined to: those `user` may use, of the same type."""
    return [c for c in palette(user) if (c.spec or {}).get("type") == item.type]


def join(item, c):
    """Join an existing question to element `c` without changing it (also in a running or closed survey): it asks
    the newest Fassung, and where it differs from it, it counts as adapted. Its earlier participations were not told
    about the shared analysis ("since"). Returns the differences. The caller commits."""
    from app import constructs

    item.config = {**(item.config or {}), "construct": {"key": c.key, "version": c.seed_version, "unlocked": False,
                                                        "template": copy.deepcopy(c.spec), "since": utcnow().isoformat()}}
    mods = constructs.modifications(item, c, de=True)
    if mods:
        item.config = {**item.config, "construct": {**item.config["construct"], "unlocked": True}}
    return mods


def versions(c, user=None):
    """[(Fassung, number of uses, frozen sentence or None)] of the Fassungen still asked, newest first."""
    counts = {}
    for item, survey in uses(c):
        if user is not None and not _admin(user) and not survey.role_of(user):
            continue
        counts[version_of(item)] = counts.get(version_of(item), 0) + 1
    counts.setdefault(c.seed_version, 0)
    return [(v, counts[v], frozen(c, user=user, version=v)) for v in sorted(counts, reverse=True)]


def save_survey(survey, user, touch=True):
    """save_template for every linked question of a draft (pages that change texts of many questions at once, as
    the translation page). Returns the number of other uses changed."""
    if not survey.is_editable:
        return 0
    return sum(save_template(i, user, touch) for b in survey.blocks for i in b.items if is_linked(i))


def unlink(item):
    """The question becomes an ordinary copy: no lock, no shared analysis; everything else stays."""
    item.config = {k: v for k, v in (item.config or {}).items() if k != "construct"}


def without_gone(config):
    """`config` without its link when the linked element it names is gone (dissolved while the question lay in
    the trash, or while a step of the editor's history held it): a question coming back would else stay
    "linked" to nothing for good. Returns the same dict when nothing is to drop."""
    key = ((config or {}).get("construct") or {}).get("key")
    if not (isinstance(key, str) and key.startswith(PREFIX)) or get(key) is not None:
        return config
    return {k: v for k, v in config.items() if k != "construct"}


def dissolve(c, only=None):
    """Every use (or only the items in `only`) becomes an ordinary copy; the element is gone once no use is left.
    Returns True if it is gone. The caller commits."""
    rows = uses(c)
    for item, _ in rows:
        if only is None or item in only:
            unlink(item)
    if only is not None and any(item not in only for item, _ in rows):
        return False
    from app.models import ConstructSubmission

    db.session.execute(db.delete(ConstructPick).where(ConstructPick.construct_id == c.id))
    # its submissions for the toolbox (app/submissions.py) go too; an accepted one's toolbox construct stays
    db.session.execute(db.delete(ConstructSubmission).where(ConstructSubmission.construct_id == c.id))
    db.session.delete(c)
    return True


def rename(c, name):
    name = " ".join((name or "").split())[:NAME_MAX]
    if name:
        c.name = name
        c.search_text = _search_text(c)
    return bool(name)


def overview(c, user):
    """What the page of an element and the box in the element editor show."""
    from app import access

    rows = uses(c)
    total_uses = len(rows)
    own = set(db.session.scalars(access.survey_ids(user)))
    if not _admin(user):
        rows = [(item, survey) for item, survey in rows if survey.id in own]
    counts = real_counts(list({s.id for _, s in rows}))
    seen, surveys = set(), []
    for item, s in rows:
        surveys.append({"item": item, "survey": s, "visible": s.id in own or _admin(user), "adapted": adapted(item),
                        "version": ((item.config or {}).get("construct") or {}).get("version"),
                        "real": counts.get(s.id, 0) if s.id in own or _admin(user) else None, "first": s.id not in seen})
        seen.add(s.id)
    # Only site admins see totals across inaccessible surveys; others see visible participations,
    # so that the count of a single survey without access cannot be worked out (security audit 2026-10-04, N5)
    visible = {u["survey"].id for u in surveys if u["visible"]}
    whole = _admin(user)
    real = sum(counts.values()) if whole else sum(n for sid, n in counts.items() if sid in visible)
    return {"element": c, "uses": surveys, "surveys": len(seen), "real": real,
            "hidden_uses": len(rows) < total_uses,
            "partial_dissolve": len(dissolvable(user, c)) < total_uses,
            "real_partial": not whole and (len(rows) < total_uses or len(visible) < len(seen)),
            "frozen": frozen(c, user), "versions": versions(c, user),
            "owner": may_change(user, c), "dissolvable": len(dissolvable(user, c))}
