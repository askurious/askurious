import copy
import re
import secrets
from datetime import date, datetime, timezone

from flask_login import UserMixin
from sqlalchemy import JSON, BigInteger, Float, ForeignKey, Index, String, Text, UniqueConstraint, select, text
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.extensions import db, json_given
from app.shared_tokens import SharedLinkToken


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def counted_once(key, count):
    """A count of participations, counted once per request: a page asked Survey.tally, completed_count and
    export.median_seconds two or three times each (the tabs, the page, the footer; audit 2026-10-06, P8). Every
    write of the session (a flush, an UPDATE or DELETE past it, a rollback) forgets what was counted, so a count
    after a completion in the same request is counted afresh (lifecycle.maybe_close). Outside a request: counted
    every time."""
    from flask import g, has_request_context

    if not has_request_context():
        return count()
    memo = g.setdefault("_counted", {})
    if key not in memo:
        memo[key] = count()
    return memo[key]


def _forget_counts(*_args):
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_counted", None)


def _forget_counts_on_write(state):
    if not state.is_select:
        _forget_counts()


def _watch_counts():
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    event.listen(Session, "after_flush", _forget_counts)
    event.listen(Session, "after_rollback", _forget_counts)
    event.listen(Session, "after_soft_rollback", _forget_counts)
    event.listen(Session, "do_orm_execute", _forget_counts_on_write)


_watch_counts()


# Survey codes: no characters that are easily confused (0/o, 1/l/i); lower case, so case never matters
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LENGTH = 6


def random_code():
    """A random survey code with at least one letter (so it never looks like a number)."""
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not code.isdigit():
            return code


def new_code():
    return random_code()  # tests replace this with sequential codes ("1", "2", ...)


PID_LENGTH = 20


def new_pid(survey_code):
    """Participant ID for {{pid}}: <survey code>-<20 random characters> (same alphabet as the codes)."""
    return f"{survey_code}-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(PID_LENGTH))


def display_seed(response):
    """Seed of the random displays per participant (sides of "Zuordnung", order of the conjoint options, the
    crowd's sample): the pid, known before the first save, so the first page and the export agree. Participations
    whose layout was drawn before that (no "seed" mark) keep their id, as they were shown (review 2026-10-15, F1)."""
    pid = getattr(response, "pid", None)
    if pid and ((getattr(response, "layout", None) or {}).get("seed") == "pid"):
        return pid
    return getattr(response, "id", None)


# A person is known by a key, stored in the columns named "orcid": the ORCID iD, or for an account without
# ORCID (sign-in with e-mail and password, app/passwords.py) an internal key. Where the account behind an
# author entry was deleted, the entry keeps the name under a key no account can ever have (app/accounts.py).
ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]\Z")
FORMER_PREFIX = "x-"
LOCAL_PREFIX = "q-"


def is_orcid(key):
    return bool(key) and ORCID_RE.match(key) is not None


def local_key():
    """The key of an account without ORCID: never an ORCID iD, and not guessable, so nobody adds accounts to
    a survey by counting through keys. 18 characters."""
    return LOCAL_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(16))


def former_key():
    """A key for the author entry of a deleted account: never an ORCID iD, never guessable, 18 characters."""
    return FORMER_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(16))


def _default_mode():
    """Light or dark for a new account: the standard of the brand it is made under (app/brand.py)."""
    from app import brand

    return brand.default_mode()


class User(UserMixin, db.Model):
    """A researcher. Identified by ORCID iD; participants never have accounts."""

    id: Mapped[int] = mapped_column(primary_key=True)
    orcid: Mapped[str | None] = mapped_column(String(19), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    affiliation: Mapped[str] = mapped_column(String(300), default="")
    is_admin: Mapped[bool] = mapped_column(default=False)
    # Registration number: 1 = first researcher who signed in, then 2, 3, ...; never reused
    number: Mapped[int | None] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # the domain the account was made under (askurious.de, polltergeist.de; _created_host): for a later split into
    # one database per brand (datenbank-je-marke-konzept.md, migration 0029); None for accounts from before
    created_host: Mapped[str | None] = mapped_column(String(253))
    last_login_at: Mapped[datetime | None]
    # Version of licence.md the researcher accepted (None = not yet); see app/terms.py
    terms_version: Mapped[str | None] = mapped_column(String(20))
    terms_accepted_at: Mapped[datetime | None]
    # the experimental features a site admin switched on for this person (keys of app/experimental.FEATURES; until
    # 2026-10-05 the person ticked them on the page "Konto"); groups and "for everyone": ExperimentGrant
    experiments: Mapped[list | None] = mapped_column(JSON)
    # the old single tick for all experimental features; carried over into `experiments` at the start
    # (experimental.upgrade_legacy) and false from then on
    experimental_legacy: Mapped[bool] = mapped_column("experimental", default=False)
    # the switch "Lernhilfen" on the page "Konto" (app/learning.py): reasons at decisions, reading aids at statistics,
    # the method finder; on for new accounts, off for those that existed before (migration 0004)
    learning_aids: Mapped[bool] = mapped_column(default=True, server_default="0")
    # the switch "Generative KI nutzen" on the page "KI-Nutzung" (app/ai_access.py, ki-nutzung-seite-konzept.md E4):
    # off, Askurious shows no AI functions and AI notes to this person and sends nothing of theirs to AI services
    ai_enabled: Mapped[bool] = mapped_column(default=True, server_default="1")
    # the folds "Warum?" this person chose "Nicht mehr zeigen" for (keys of app/learning.REASONS; migration 0015);
    # emptied by "Ausgeblendete Erklärungen wieder zeigen" on "Konto"
    learning_hidden: Mapped[list | None] = mapped_column(JSON)
    # the look of the researchers' pages for this person (app/display.py): light/dark, colour style, text size;
    # empty = Askurious as it comes, light or dark after the device (migration 0005); new accounts start "Hell"
    # (Ralf, 2026-10-05), accounts from before keep what they had
    display: Mapped[dict | None] = mapped_column(JSON, default=lambda: {"mode": _default_mode()})
    # the newest blog entry this person had seen, for the dashboard line "Neu in den Neuigkeiten" (migration 0038);
    # the line is gone (Ralf, 2026-10-10), the column stays unused so no migration is needed
    blog_seen_at: Mapped[datetime | None]
    # the setup assistant after the first sign-in (app/account_setup.py, konto-einrichtung-konzept.md, Ralf 2026-10-18):
    # true for an account made from then on until the person finishes or ends it; accounts from before never see it
    setup_due: Mapped[bool] = mapped_column(default=False, server_default="0")
    # an account kept for an ORCID iD someone added before its first sign-in (app/people_keys.py,
    # konto-schluessel-plan.md B2): no number yet, in no list of accounts; the first sign-in with ORCID makes it whole
    pending: Mapped[bool] = mapped_column(default=False, server_default="0")

    contacts: Mapped[list["Contact"]] = relationship(
        foreign_keys="Contact.owner_id", back_populates="owner", cascade="all, delete-orphan"
    )

    @property
    def display_name(self):
        """The name the signed-in person sees: "Mitglied 12" when this person's organisation hides names
        (app/names.py)."""
        from app import names

        return names.shown(self, self.name or "ORCID user")

    @property
    def experimental(self):
        """Whether any experimental feature is on for this person (app/experimental.py)."""
        from app import experimental

        return bool(experimental.active(self))

    @experimental.setter
    def experimental(self, on):
        """All experimental features on or off at once (tests, and code from before one tick per feature)."""
        from app import experimental

        self.experiments = list(experimental.KEYS) if on else []

    @property
    def has_orcid(self):
        return is_orcid(self.orcid)

    def get_id(self):
        """What the session and the "stay signed in" cookie hold: the id, the account's session version and the id
        of this sign-in session (app/sessions.py), and when known its authenticated sign-in method. Raising the
        version (new password, "überall abmelden") ends every
        session at once; closing the session row ("Abmelden") ends this one with all copies of its cookies
        (auth.load_user)."""
        from app import sessions
        from flask import has_request_context, session

        row = db.session.get(UserLogin, self.id)
        token = f"{self.id}.{(row.session_version or 0) if row is not None else 0}.{sessions.current_sid()}"
        via = session.get("signed_in_via") if has_request_context() else None
        # Flask-Login signs this value in its remember cookie too. Preserve the proof of ORCID across restore.
        return token + "." + via if via in ("orcid", "password", "password+code", "link") else token


class MemberInvite(db.Model):
    """An invitation by e-mail address to a survey or a group (app/invites.py), only with mail switched on: the
    person gets a link, signs in with ORCID or sets a password, and becomes a member with the role chosen. Only the
    hash of the token is stored; the row goes once the link is used, withdrawn, or expired (INVITE_DAYS), with the
    survey or group, and with the account that invited (account deletion)."""

    __tablename__ = "member_invite"

    id: Mapped[int] = mapped_column(primary_key=True)
    # no foreign keys, like participant_handover: the rows go in code with the survey (lifecycle.delete), the group
    # (groups.remove) and the account that invited (accounts.delete)
    survey_id: Mapped[int | None] = mapped_column(index=True)
    group_id: Mapped[int | None] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(String(10))
    email: Mapped[str] = mapped_column(String(254), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    # an invitation of a list whose links the inviter passed on himself (Sammeleinladung without mail,
    # app/bulk_invites.py): it can be accepted also while mail is off
    handed_out: Mapped[bool | None]


class GroupJoinLink(db.Model):
    """A link to join a group (app/joinlinks.py, gruppen-freischaltung-konzept.md E5-E8): whoever opens it and signs
    in becomes a member, until it expires, is used `max_uses` times or is revoked. A lead with the right to create
    surveys makes them for the role "member"; a site admin makes one for the role "lead", used once, to give a group
    its lead. Only the hash of the token is stored."""

    __tablename__ = "group_join_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    # no foreign keys, like member_invite: the rows go in code with the group (groups.remove) and the account that
    # made them (accounts.delete)
    group_id: Mapped[int] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    max_uses: Mapped[int] = mapped_column(default=30)
    uses: Mapped[int] = mapped_column(default=0)
    created_by_id: Mapped[int | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]


class DataToken(db.Model):
    """A read token for the data of one survey (app/data_access.py): R or Python fetch the CSV, the .rds file or the
    codebook with it, as the buttons of the export page give them. Only the hash of the token is stored; the token
    itself is shown once. It works while it has not expired and its person may download the data of the survey
    (role "Bearbeiten" or "Admin", checked at every request) with an account that is not blocked. Revoking removes
    the row."""

    __tablename__ = "data_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    # no foreign keys, like member_invite: the rows go in code with the survey (lifecycle.delete) and the account
    # (accounts.delete), and a process of the code before, still running during a rolling deploy, deletes either
    # without a key it does not know standing in its way
    survey_id: Mapped[int] = mapped_column(index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]


class AgentToken(db.Model):
    """A personal token of the AI agent interface (app/agent_access.py, app/mcp.py; experimental "agent"): an agent
    such as Claude Code reads the questionnaires of its person and, with the scope "edit", changes drafts. Only the
    hash is stored; the token is shown once. It works while it has not expired, the feature is on for its person and
    their account may sign in; the role in each survey is checked at every call. `surveys`: the survey ids it is
    limited to, or None for all of the person's surveys. Revoking removes the row."""

    __tablename__ = "agent_token"
    READ, EDIT = "read", "edit"

    id: Mapped[int] = mapped_column(primary_key=True)
    # no foreign key, like data_token: the rows go in code with the account (accounts.delete, account_access.revoke)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scope: Mapped[str] = mapped_column(String(10), default="read")
    surveys: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]


class InterviewerAccess(db.Model):
    """A personal link of one interviewer for one interview guide (app/interviewer.py; concept
    konzept-interviewer-zugang-2026-10-15.md, Ralf 2026-10-15): whoever opens it conducts interviews of that guide
    without an account and sees only their own (Response.interview["access"]). The link alone is the key; only its
    hash is stored, and it is shown once. `revision` changes when the access is blocked, gets a new link or its
    devices are signed out, so that every session of it ends with its next request. `devices`: the browsers that
    opened it, [{"id", "kind", "first", "last"}], at most `max_devices`. The rights (`may_record`,
    `may_transcribe`, `may_code`) change on the page "Interviews" without a new link: the server reads them with every
    request, and neither the revision nor a session changes with them. Without `may_interview` the link only codes:
    no interview to begin, record or transcribe (the server refuses them). `may_code` (experimental "transcript_coding"):
    "none", "own" (its own interviews) or "all" (all interviews of an analysis it is assigned to; Ralf 2026-10-28).
    `show_learning` ("Lernhilfen zeigen", on by default, Ralf 2026-10-29): its sessions see the learning aids
    (app/learning.py, on) as a researcher with the switch on."""

    __tablename__ = "interviewer_access"
    CODE_NONE, CODE_OWN, CODE_ALL = "none", "own", "all"

    id: Mapped[int] = mapped_column(primary_key=True)
    # no foreign keys, like data_token: the rows go in code with the guide (lifecycle.delete); the account that made
    # one only loses its name there (accounts.delete)
    survey_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    revision: Mapped[str] = mapped_column(String(32))
    blocked: Mapped[bool] = mapped_column(default=False)
    may_interview: Mapped[bool] = mapped_column(default=True, server_default="1")
    may_record: Mapped[bool] = mapped_column(default=True)
    may_transcribe: Mapped[bool] = mapped_column(default=True)
    may_code: Mapped[str] = mapped_column(String(8), default="none", server_default="none")
    show_learning: Mapped[bool] = mapped_column(default=True, server_default="1")  # "Lernhilfen zeigen" (0108)
    max_devices: Mapped[int] = mapped_column(default=3)
    devices: Mapped[list | None] = mapped_column(JSON)
    hints_version: Mapped[str | None] = mapped_column(String(20))
    created_by_id: Mapped[int | None] = mapped_column(index=True)
    created_by_name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]


# --- coding interview transcripts (app/transcript_coding.py, experimental "transcript_coding") ---------------------
# No foreign keys, like interviewer_access: the rows go in code with the guide (lifecycle.delete) and lose only names
# and ids of a deleted account (accounts.delete). The interviewer links themselves are never changed by them.


class CodingProject(db.Model):
    """An analysis of an interview guide's transcripts ("Auswertung"): a name, a research question, a category system
    of its own and members of its own; several per guide, all on the same transcripts. `visibility`: "all" (every
    member sees every interview) or "own" (each member, group or coding link sees only its own interviews; the team
    always sees all). `mains_fixed`: only the team changes the main categories; the others add candidates and
    propose changes."""

    __tablename__ = "coding_project"
    ALL, OWN = "all", "own"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(120))
    question: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(10), default=ALL)
    mains_fixed: Mapped[bool] = mapped_column(default=False)
    created_by_id: Mapped[int | None] = mapped_column(index=True)
    created_by_name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    # "KI-Vorschläge erlauben" (app/transcript_coding_ai.py): off until the team switches it on
    ai_allowed: Mapped[bool] = mapped_column(default=False, server_default="0")
    # further settings of the analysis that need no column of their own (e.g. double coding)
    settings: Mapped[dict | None] = mapped_column(JSON)


class CodingCategory(db.Model):
    """A category of an analysis: "main" (a main category), "sub" (a subcategory under `parent_id`) or "candidate" (a
    proposed main category, drawn dashed, until the team takes it on). `guid`: the key for REFI-QDA (from the guide as
    in app/qdc.py, else a new one); `source`: where it came from in the guide ("block:12", "item:34"). The author
    (`author_key` "u:<user>", "l:<coding link>", "i:<interviewer link>") and the time stay with each entry. Removed
    entries keep their row (`removed_at`), so that "Rückgängig" and the history find them; `merged_into` and
    `former_names` belong to merging and renaming (stage 3)."""

    __tablename__ = "coding_category"
    MAIN, SUB, CANDIDATE = "main", "sub", "candidate"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(index=True)
    parent_id: Mapped[int | None] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(10), default=SUB)
    guid: Mapped[str] = mapped_column(String(36))
    source: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    definition: Mapped[str] = mapped_column(Text, default="")
    delimitation: Mapped[str] = mapped_column(Text, default="")
    anchor: Mapped[dict | None] = mapped_column(JSON)  # a coded passage (stage 2): {"interview", "coding"}
    examples: Mapped[list | None] = mapped_column(JSON)  # up to two further ones, like the anchor (0110)
    colour: Mapped[str] = mapped_column(String(7), default="")
    position: Mapped[int] = mapped_column(default=0)
    author_key: Mapped[str] = mapped_column(String(40), default="")
    author_id: Mapped[int | None] = mapped_column(index=True)
    author_name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    removed_at: Mapped[datetime | None]
    merged_into: Mapped[int | None] = mapped_column(index=True)
    former_names: Mapped[list | None] = mapped_column(JSON)


class CodingEvent(db.Model):
    """One entry of an analysis's history: a category made, changed, removed, taken on, a change proposed and decided,
    a step taken back ("created", "changed", "removed", "restored", "promoted", "proposal", "undone"; stage 3 adds
    merging with its answers "Passt" and "herausgelöst"). `data`: the state before and after, or what is proposed;
    `reply_to`: the entry this one answers; `status` of a proposal: "open", "accepted", "declined". `merge`: the
    saves of one visit to a text field are one entry (app/autosave.py, merge)."""

    __tablename__ = "coding_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(index=True)
    category_id: Mapped[int | None] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(20))
    reply_to: Mapped[int | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(10), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict | None] = mapped_column(JSON)
    actor_key: Mapped[str] = mapped_column(String(40), default="")
    actor_id: Mapped[int | None] = mapped_column(index=True)
    actor_name: Mapped[str] = mapped_column(String(200), default="")
    merge: Mapped[str | None] = mapped_column(String(80))
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class CodingMemo(db.Model):
    """A memo of an analysis (app/transcript_coding_memos.py, after Kuckartz & Rädiker; Ralf 2026-10-29): a note at a
    coded passage ("passage": `response_id` and `coding_id`), at a category ("category": `category_id`) or at an
    interview ("interview": `response_id`), with its author (`author_key` as in CodingCategory) and time. Only the
    author and the team change or delete it; a deleted one keeps its row for a day (`deleted_at`), so that
    "Rückgängig" brings it back. It goes with the analysis, the interview, the coded passage and the author's account."""

    __tablename__ = "coding_memo"
    PASSAGE, CATEGORY, INTERVIEW = "passage", "category", "interview"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(10))
    response_id: Mapped[int | None] = mapped_column(index=True)
    coding_id: Mapped[str | None] = mapped_column(String(24))
    category_id: Mapped[int | None] = mapped_column(index=True)
    text: Mapped[str] = mapped_column(Text)
    author_key: Mapped[str] = mapped_column(String(40), default="")
    author_id: Mapped[int | None] = mapped_column(index=True)
    author_name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None]


class CodingMember(db.Model):
    """Who codes in an analysis besides the team of the guide, with the role "Kodieren" (codes, never changes the
    guide): a person ("person", `user_id`), an Askurious group ("group", `group_id`, live for its members) or an
    interviewer link assigned to this analysis ("interviewer", `access_id`; it codes here only while the link has
    the right InterviewerAccess.may_code, which says which interviews). `access_ids`: with "own" interviews, the
    interviewer links whose interviews count as theirs."""

    __tablename__ = "coding_member"
    PERSON, GROUP, INTERVIEWER = "person", "group", "interviewer"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(12))
    user_id: Mapped[int | None] = mapped_column(index=True)
    group_id: Mapped[int | None] = mapped_column(index=True)
    access_id: Mapped[int | None] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(String(10), default="coder")
    access_ids: Mapped[list | None] = mapped_column(JSON)
    added_by_id: Mapped[int | None] = mapped_column(index=True)
    added_by_name: Mapped[str] = mapped_column(String(200), default="")
    added_at: Mapped[datetime] = mapped_column(default=utcnow)


class SurveyInvite(db.Model):
    """An address on the invitation list of a survey (app/survey_invites.py, mailversand-konzept.md M6, M7; mail on
    only). The participation never stores the token: the row only knows "open, started, done" (no time, no
    participation), so reminders reach only those who have not taken part, and the answers stay without a link to
    the address. Opting out empties the address and keeps a salted hash, so the address is not added again. The rows
    go with the survey, by hand ("Adressliste löschen"), and with the period "Kennungen und Kontaktdaten" of the
    survey (retention.anonymise). No foreign keys, like participant_handover."""

    __tablename__ = "survey_invite"
    # one row per address and survey: two uploads at the same moment cannot add it twice (audit 2026-10-09b, E1)
    __table_args__ = (Index("uq_survey_invite_survey_email", "survey_id", "email_hash", unique=True),)
    OPEN, STARTED, DONE, OPTOUT = "open", "started", "done", "optout"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    email: Mapped[str] = mapped_column(String(254), default="")
    email_hash: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    lang: Mapped[str] = mapped_column(String(10), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(10), default=OPEN)
    sends: Mapped[int] = mapped_column(default=0)  # mails that went out: invitation and reminders
    last_sent_at: Mapped[datetime | None]
    failed: Mapped[bool] = mapped_column(default=False)  # the mail server refused the last mail several times
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # "started"/"done" noted by the participation, taken into status once a day (survey_invites.apply_states)
    pending_status: Mapped[str | None] = mapped_column(String(10))


class MailOutbox(db.Model):
    """Mails of a series waiting to go out (app/mailqueue.py), sent at MAIL_PER_MINUTE. Once sent, a row keeps only
    who queued it and when it went (address and text are emptied) for a day, for the limit per account; after
    MAX_ATTEMPTS refusals of the mail itself it goes (a mail server that cannot be reached counts for none)."""

    __tablename__ = "mail_outbox"
    # at most one waiting mail per list entry: invitation or reminder, never both or twice (audit 2026-10-09b, E1)
    __table_args__ = (Index("uq_mail_outbox_waiting_invite", "invite_id", unique=True,
                            sqlite_where=text("sent_at IS NULL AND invite_id IS NOT NULL"),
                            postgresql_where=text("sent_at IS NULL AND invite_id IS NOT NULL")),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int | None] = mapped_column(index=True)
    invite_id: Mapped[int | None] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(10), default="invite")  # invite | reminder | links
    to: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    reply_to: Mapped[str | None] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    claimed_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)
    sender_id: Mapped[int | None] = mapped_column(index=True)  # the account that queued it (MAIL_PER_ACCOUNT_DAY)
    sent_at: Mapped[datetime | None]


class AccountType(db.Model):
    """A user type (app/account_types.py, konzept-nutzertypen-2026-10-15.md): a template site admins keep under
    Administration > Personen, taken over once when an account comes in: its period, its survey types
    (app/kind_access.py), whether it may create surveys, whether it has a supervisor, and how long after its end the
    account is kept. Seeded with five types (key); further ones have no key."""

    __tablename__ = "account_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str | None] = mapped_column(String(20), unique=True)
    # the organisation the type belongs to (Ralf 2026-10-16: types per organisation, each starts with "Mitarbeitende"
    # only); None = the organisation everything started in (app/orgs.py, migration 0075)
    org_id: Mapped[int | None] = mapped_column(index=True)
    name_de: Mapped[str] = mapped_column(String(80))
    name_en: Mapped[str] = mapped_column(String(80), default="")
    # "" without end; "m:4" / "w:6" months or weeks from the start; "m:" / "w:" the number is given at the invitation;
    # "d:" a fixed end date given at the invitation (courses)
    duration: Mapped[str] = mapped_column(String(20), default="")
    kinds: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # as UserLogin.kinds; None = all four
    may_create: Mapped[bool | None]  # no longer read since 2026-10-18: "create" in kinds (app/kind_access.py); migration 0080
    supervised: Mapped[bool] = mapped_column(default=False)
    delete_months: Mapped[int | None]  # None: kept after its end until an admin deletes it
    is_default: Mapped[bool] = mapped_column(default=False)  # the type of new accounts
    hidden: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class UserLogin(db.Model):
    """What the site keeps about an account besides the person: whether an admin blocked it or lets it create
    surveys (app/accounts.py), and the sign-in with e-mail and password (app/passwords.py). Some columns are
    for later (verified addresses, a second factor); they exist because a new column would need a manual
    ALTER TABLE on every server. No row = an ORCID account nobody changed anything about."""

    __tablename__ = "user_login"
    ACTIVE, PENDING, BLOCKED = "active", "pending", "blocked"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default=ACTIVE)
    status_changed_at: Mapped[datetime | None]
    status_changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    # until 2026-10-18 the right to create surveys (None = by groups, True, False); since then the level "create" per type
    # in kinds took its place (app/kind_access.py, migration 0080), and only False is read: an admin's "no", which the
    # sign-in list does not overturn (kind_access.said_no, S17)
    may_create: Mapped[bool | None] = mapped_column(default=None)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)  # lower case; the sign-in name
    email_verified_at: Mapped[datetime | None]  # mailbox confirmation; None for legacy/manual access
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_changed_at: Mapped[datetime | None]
    failed_count: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]
    # wrong passwords and codes in a row since the last right one (app/passwords.py, audit 2026-10-11 AU3): the pause
    # does not reset it; at passwords.HARD_LIMIT the sign-in with password and code is locked (hard_locked_at) until a
    # site admin releases it, a new password over a one-time link, or passwords.HARD_LOCK has passed
    failed_streak: Mapped[int] = mapped_column(default=0, server_default="0")
    hard_locked_at: Mapped[datetime | None]
    session_version: Mapped[int] = mapped_column(default=0)
    # "open": the account came in by the former limit of open sign-up alone (2026-10-10 to 2026-10-29); no longer
    # written or read (app/signin.py). Mapped until a later migration drops it with the table sign_in_wait.
    came_via: Mapped[str | None] = mapped_column(String(10))
    # the survey types this account may see or edit (app/kind_access.py, konzept-rechte-befragungstypen-2026-10-15.md):
    # only what differs from "edit", e.g. {"live": "view", "diary": "off"}; None = all four types (migration 0067)
    kinds: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    # user types with a period (app/account_types.py, konzept-nutzertypen-2026-10-15.md): the type taken over when the
    # account came in; the period waiting for its start (valid_spec, "m:4", "w:6", "d:2027-03-31", until the person
    # sets the password of an account made by an admin); then from when and until when it holds. At valid_until the
    # account is blocked with block_reason "expired" (an admin's block leaves it empty); supervisor: the person who
    # becomes admin of every survey it creates; origin_group: the course it came in through (one-time links by the lead);
    # expiry_noticed: the last advance notice by mail (30, 14 or 3 days)
    account_type_id: Mapped[int | None] = mapped_column(ForeignKey("account_type.id", ondelete="SET NULL"), index=True)
    valid_spec: Mapped[str | None] = mapped_column(String(20))
    valid_from: Mapped[datetime | None]
    valid_until: Mapped[datetime | None] = mapped_column(index=True)
    block_reason: Mapped[str | None] = mapped_column(String(10))
    supervisor_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    origin_group_id: Mapped[int | None] = mapped_column(ForeignKey("workgroup.id", ondelete="SET NULL"), index=True)
    expiry_noticed: Mapped[int | None]
    totp_secret: Mapped[str | None] = mapped_column(String(64))  # unused: too short for the encrypted secret
    # second factor (app/twofactor.py): the TOTP secret encrypted with secrets_box, and the last time step used
    totp_box: Mapped[str | None] = mapped_column(String(255))
    totp_step: Mapped[int | None]
    # backup codes for a lost phone (twofactor.new_backup_codes): SHA-256 hashes as a JSON list, each used once
    totp_backup: Mapped[str | None] = mapped_column(Text)
    # "Passwort vergessen?" on the sign-in page (auth.forgot_password): when the person last asked for a new one-time
    # link; site admins see it under Forschende. Cleared by a new link, a new password or "Anfrage erledigt"
    reset_requested_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LoginSession(db.Model):
    """One sign-in of an account in one browser (app/sessions.py): open until "Abmelden", ending it on the page
    "Konto", a new password or "überall abmelden"."""

    __tablename__ = "login_session"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(index=True)
    label: Mapped[str] = mapped_column(String(60), default="")  # browser and system, coarse ("Firefox · Windows")
    ended_at: Mapped[datetime | None]


class AuthToken(db.Model):
    """A one-time link (app/passwords.py): so far "setup", the link a site admin passes on so that someone
    sets their password. Only the hash of the token is stored."""

    __tablename__ = "auth_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20), default="setup")
    email: Mapped[str | None] = mapped_column(String(254))  # pending address; not yet a sign-in name
    secret_box: Mapped[str | None] = mapped_column(Text)  # pending password hash/version, never a plaintext password
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AccountJob(db.Model):
    """Short-lived encrypted reset requests, including unknown addresses; resolved outside the HTTP request."""
    __tablename__ = "account_job"
    id: Mapped[int] = mapped_column(primary_key=True)
    payload_box: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class SignInGrant(db.Model):
    """A site admin's yes that this account may sign in, whatever the whitelist says (auth._may_sign_in). An
    account without ORCID needs none, a site admin made it. It gets one when it connects an ORCID iD
    (accounts.link_orcid), so that the rules for ORCID iDs do not lock it out afterwards."""

    __tablename__ = "sign_in_grant"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    granted_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SignInOrcid(db.Model):
    """An ORCID iD a site admin lets in (Administration > Personen), whether it has an account yet or not
    (app/signin.py). Until October 2026 this list was ALLOWED_ORCIDS in the server's configuration; it is kept here
    since (Ralf, 2026-10-06)."""

    __tablename__ = "sign_in_orcid"

    orcid: Mapped[str] = mapped_column(String(19), primary_key=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    # the survey types chosen for this iD in "Person hinzufügen" (app/kind_access.py): taken over at its first
    # sign-in; None = the default for new accounts
    kinds: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    # the user type and period chosen for this iD in "Person hinzufügen" (app/account_types.py): taken over at its
    # first sign-in, the period starting then; None = the type for new accounts
    account_type_id: Mapped[int | None] = mapped_column(ForeignKey("account_type.id", ondelete="SET NULL"), index=True)
    valid_spec: Mapped[str | None] = mapped_column(String(20))
    # the organisation this iD comes into at its first sign-in (app/orgs.py); None = the one everything started in
    org_id: Mapped[int | None] = mapped_column(index=True)
    # the registration link this iD came by (app/signup_links.py, migration 0098); None = added by hand
    link_id: Mapped[int | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SignUpLink(db.Model):
    """A registration link of the platform (app/signup_links.py, konzept-organisation-sichtbarkeit-einladungen-2026-10-23.md
    E1-E9): whoever opens it and signs in with ORCID for the first time gets an account at home in `org_id`, with the
    user type and period chosen here, until it expires, is used `max_uses` times or is revoked. Only the hash of the
    token is stored. No foreign keys, like group_join_link: the rows go in code (revoking, the sweep)."""

    __tablename__ = "sign_up_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    org_id: Mapped[int | None] = mapped_column(index=True)
    account_type_id: Mapped[int | None] = mapped_column(index=True)
    valid_spec: Mapped[str | None] = mapped_column(String(20))  # None: as the user type says
    max_uses: Mapped[int] = mapped_column(default=1)
    uses: Mapped[int] = mapped_column(default=0)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_by_id: Mapped[int | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]


class Organisation(db.Model):
    """An organisation (app/orgs.py, konzept-abteilungen-2026-10-15.md, Ralf 2026-10-15): a department, chair,
    institute or company above the groups. A fence, not an access: people, groups, surveys and designs belong to one,
    and sharing, searching and "for everyone" end at its edge; the one gap is the personal entry in a survey. The
    organisation everything started in has the key "main". The caps (kinds_cap, may_create, max_accounts, the two
    switches for invitations across the fence, the type for new accounts) are set by site admins only."""

    __tablename__ = "organisation"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str | None] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(80))  # short, for the platform's pages
    # the full name for the texts of surveys (consent, privacy notes, Zenodo; Ralf 2026-10-16), e.g. "Lehrstuhl für
    # Kommunikationswissenschaft, RWTH Aachen University"; empty: the short name
    long_name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="")
    kinds_cap: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # as UserLogin.kinds; None = all four
    may_create: Mapped[bool | None]  # False: no member whose home it is creates surveys
    max_accounts: Mapped[int | None]
    invite_out: Mapped[bool] = mapped_column(default=True)  # members may bring people from outside into surveys
    invite_in: Mapped[bool] = mapped_column(default=True)  # members may be brought into surveys of others
    # who brings in people from outside (app/orgs.py SCOPES; migration 0097): "create" | "admins" | "platform";
    # None = "create". invite_scope by site admins, invite_scope_own by the organisation's admins, only stricter
    invite_scope: Mapped[str | None] = mapped_column(String(10))
    invite_scope_own: Mapped[str | None] = mapped_column(String(10))
    # whether members see each other's names (app/names.py; migration 0099): "shown" | "hidden" | "admins" (its admins
    # decide with names_hidden_own); None = "shown"
    names_mode: Mapped[str | None] = mapped_column(String(10))
    names_hidden_own: Mapped[bool | None]
    basis: Mapped[str] = mapped_column(String(300), default="")  # contract or legal basis, site admins only
    default_type_id: Mapped[int | None] = mapped_column(ForeignKey("account_type.id", ondelete="SET NULL"), index=True)
    # the user types its admins may give (ids of AccountType); None = all that are not hidden (A16, migration 0072)
    allowed_types: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    # The organisation design, light and dark (app/org_designs.py, konzept-org-designs-2026-10-19.md, Ralf 2026-10-19):
    # a whole theme with preset "org" (app/theme.py), made by the organisation's admins; new surveys start with it and
    # members choose it, a copy each time. The templates are what site admins uploaded as a starting point for them.
    # The former columns themes (the "RWTH" tick), theme_light and theme_dark went with migration 0091.
    design_light: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    design_dark: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    template_light: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    template_dark: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    from_group_name: Mapped[str] = mapped_column(String(80), default="")  # made from this group (Ralf, A23)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    members: Mapped[list["OrganisationMember"]] = relationship(
        back_populates="org", cascade="all, delete-orphan", order_by="(OrganisationMember.role, OrganisationMember.id)")


class OrgAlias(db.Model):
    """The number a person has in an organisation that hides names (app/names.py, E16): shown as "Mitglied 12" to
    other members, given once and kept. No foreign keys, like group_join_link: the rows go in code with the account
    (accounts.delete) and the organisation (orgs.delete)."""

    __tablename__ = "org_alias"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_org_alias_person"),
                      UniqueConstraint("org_id", "number", name="uq_org_alias_number"))

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    number: Mapped[int]


class PersonRow:
    """A row that names a person (app/people_keys.py): `account` is the account once the person has signed in, None
    while it is only kept for an iD added before (User.pending) or the account was deleted. `orcid` is the account's
    iD (None without ORCID or account); the key column of that name went with migration 0085, setting it names the
    account of an iD (a pending one if it never signed in)."""

    @hybrid_property
    def orcid(self):
        user = self.user
        return user.orcid if user is not None else None

    @orcid.inplace.setter
    def _orcid_setter(self, key):
        from app import people_keys

        if key:  # an empty key names nobody: user_id given beside it stays
            self.user = people_keys.account(key, getattr(self, "name", "") or "")

    @orcid.inplace.expression
    @classmethod
    def _orcid_expression(cls):
        return select(User.orcid).where(User.id == cls.user_id).scalar_subquery()

    @property
    def account(self):
        user = self.user
        return user if user is not None and not user.pending else None

    @property
    def signed_up(self):
        return self.account is not None

    @property
    def orcid_id(self):
        """The person's ORCID iD for links and exports, or None (an account without ORCID, a deleted account, or a
        person whose organisation hides names from the viewer, app/names.py)."""
        user = self.user
        if user is None or not user.has_orcid:
            return None
        from app import names

        return None if names.is_hidden(user) else user.orcid


class OrganisationMember(PersonRow, db.Model):
    """A person in an organisation, by ORCID iD like the members of groups. Role "admin" or "member"; is_home: the
    organisation whose admins manage the account (at most one per person; without such a row the home is "main").
    Members of the groups of an organisation belong to it too, without a row (app/orgs.py)."""

    __tablename__ = "organisation_member"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organisation.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")  # admin | member
    is_home: Mapped[bool] = mapped_column(default=False)
    valid_until: Mapped[datetime | None]  # a further membership with an end (not the home)
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    org: Mapped[Organisation] = relationship(back_populates="members")
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])


class SiteSetting(db.Model):
    """A setting of the platform that a site admin changes in the administration, not in the server's
    configuration: "every person may sign in with ORCID" (app/signin.py), and markers of data taken over once."""

    __tablename__ = "site_setting"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), default="")
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    changed_at: Mapped[datetime] = mapped_column(default=utcnow)


class AccountSupervisor(db.Model):
    """A person who supervises an account (app/account_types.py, N10): admin of every survey the account creates, told
    before its end. Several per account (Ralf 2026-10-18, migration 0081); UserLogin.supervisor_id mirrors the first
    for the code before."""

    __tablename__ = "account_supervisor"
    __table_args__ = (UniqueConstraint("user_id", "supervisor_id", name="uq_account_supervisor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    supervisor_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class DeletedAccount(db.Model):
    """What stays of a deleted account: its registration number and its internal id, so neither is ever given
    again, and when and by whom ("self" | "admin") it was deleted. Nothing about the person.

    The id matters for safety: SQLite gives the id of a deleted last row to the next one, and a browser that
    still holds the "stay signed in" cookie of the deleted account would then be signed in as the new person."""

    __tablename__ = "deleted_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(index=True)  # the former User.id; deliberately no foreign key
    number: Mapped[int | None] = mapped_column(index=True)
    deleted_at: Mapped[datetime] = mapped_column(default=utcnow)
    by: Mapped[str] = mapped_column(String(10), default="self")


class OrgDeletion(db.Model):
    """An account an organisation admin marked for deletion (app/org_delete.py, org-personen-loeschen-konzept.md):
    blocked from the mark on, deleted by the hourly sweep at due_at unless taken back. choices: what becomes of what
    belongs to the person alone, {"heir": user id (the preset), "items": {"s<survey id>" | "d<design id>": "delete" |
    "<user id>"}, "confirm": ["s<survey id>", ...]} (deleting a survey with real participations needs its tick)."""

    __tablename__ = "org_deletion"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    org_id: Mapped[int] = mapped_column(index=True)  # no foreign key, as Design.org_id
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    requested_at: Mapped[datetime] = mapped_column(default=utcnow)
    due_at: Mapped[datetime] = mapped_column(index=True)
    choices: Mapped[dict] = mapped_column(JSON, default=dict)
    open_points: Mapped[list | None] = mapped_column(JSON)  # what kept the sweep from deleting at due_at


class Contact(PersonRow, db.Model):
    """Cached "friends": researchers a user has invited or worked with, for quick re-invites.

    The contact may not have an account yet, so we keep the ORCID iD and a cached name.
    """

    __table_args__ = (UniqueConstraint("owner_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])
    name: Mapped[str] = mapped_column(String(200), default="")
    last_used_at: Mapped[datetime] = mapped_column(default=utcnow)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id], back_populates="contacts")


def _seal_default():
    """A new survey starts with the seal chain on (config SEAL_DEFAULT; existing surveys keep theirs)."""
    from flask import current_app, has_app_context

    return bool(current_app.config.get("SEAL_DEFAULT", True)) if has_app_context() else False


class Survey(db.Model):
    """A questionnaire, owned jointly by all of its members.

    Lifecycle: draft (editable) -> running -> closed. A closed survey can be reopened,
    but never returns to draft, because responses were collected under its logic.
    """

    DRAFT, RUNNING, CLOSED = "draft", "running", "closed"
    # Changed by database triggers, including bulk SQL and code from before a rolling deployment.
    data_rev: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    structure_rev: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    data_nonce: Mapped[str] = mapped_column(String(32), default="", server_default="")
    structure_nonce: Mapped[str] = mapped_column(String(32), default="", server_default="")

    id: Mapped[int] = mapped_column(primary_key=True)
    # Public, unguessable id for the participant link (/s/<uid>)
    uid: Mapped[str] = mapped_column(String(16), unique=True, default=lambda: secrets.token_urlsafe(9))
    # Short public code for links (/s/<code>, /surveys/<code>/...); the uid stays internal (upload folder)
    code: Mapped[str] = mapped_column(String(12), unique=True, default=lambda: new_code())
    title: Mapped[str] = mapped_column(String(300))
    # the title in the other languages {lang: text} (Übersetzung page); `title` is the default language
    title_translations: Mapped[dict | None] = mapped_column(JSON)
    project: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # research question / hypotheses: context for the AI suggestions (constructs, items)
    research_question: Mapped[str] = mapped_column(Text, default="", server_default="")
    # the study plan (app/study_plan.py): hypotheses, design, sample, quality rules, analysis plan; None = not begun
    study_plan: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default=DRAFT, index=True)
    archived: Mapped[bool] = mapped_column(default=False)
    # the domain the survey was made under (as User.created_host); None for surveys from before
    created_host: Mapped[str | None] = mapped_column(String(253))

    # the organisation it belongs to (app/orgs.py, konzept-abteilungen-2026-10-15.md): a fence, not an access; None =
    # the organisation everything started in. No foreign key: the column is added to a table with triggers without
    # copying it, and an organisation is only deleted once nothing belongs to it (migration 0071)
    org_id: Mapped[int | None] = mapped_column(index=True)

    # Flow options
    allow_back: Mapped[bool] = mapped_column(default=True, server_default="1")
    # Continue automatically once a single click completes the page (only on suitable pages)
    auto_advance: Mapped[bool] = mapped_column(default=False, server_default="0")
    # The progress bar at the top of each questionnaire page
    show_progress: Mapped[bool] = mapped_column(default=True, server_default="1")
    # Participants may switch the language (switch at the top of each page); else the browser's setting decides
    language_choice: Mapped[bool] = mapped_column(default=True, server_default="1")
    # Entry and exit via external systems (URL parameters, redirects), see app/panel.py
    panel: Mapped[dict | None] = mapped_column(JSON)
    # Zenodo deposit of this survey: {"id", "doi", "state", "url", "target", "files"}
    zenodo: Mapped[dict | None] = mapped_column(JSON)
    # Coscine (app/coscine.py): {"project", "resource", "values": {profile: {path: value}},
    # "uploads": [{"at", "by", "project", "resource", "pid", "folder", "files": [{"name", "sha256", "size"}]}]}
    coscine: Mapped[dict | None] = mapped_column(JSON)
    # Next step of the setup wizard; None once the setup is finished
    setup_step: Mapped[str | None] = mapped_column(String(20))
    # Keep preview runs (source "test") so they can be exported
    keep_test_runs: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Record coarse technical information (device class, OS, browser, window width class), see app/paradata.py
    paradata: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Record the time spent on each page (seconds, measured on the server), see app/paradata.py
    page_timing: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Count per participation how often the window or tab was left and how often text was pasted into a text
    # field (app/paradata.py, FOCUS_COLUMNS); named in the consent, fixed once the survey has started
    focus_paste: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Every page carries a note to AI systems that people do not see, asking for a code word (app/quality.py, BAIT_FIELD);
    # named in the consent, fixed once the survey has started
    ai_bait: Mapped[bool] = mapped_column(default=False, server_default="0")
    # "Schneller blättern, auch bei schwachem Netz" (app/blockload.py): the pages of a block without
    # dependencies inside it come in one document and are switched in the browser
    block_load: Mapped[bool] = mapped_column(default=False, server_default="0")
    # With focus_paste: also the length of pasted text and the input steps in text fields (app/paradata.py). Set with
    # focus_paste from 2026-10-04 on; surveys whose consent was written before keep False and count as before
    focus_inputs: Mapped[bool] = mapped_column(default=False, server_default="0")
    # A cookie without an identifier marks the browser once a participation has ended; the link then shows only a
    # note there (app/participate.py, MARKER_DAYS); named in the consent, fixed once the survey has started
    repeat_marker: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Estimate country and German federal state from the IP address, once, with the agreement to take part; only
    # the result is kept (app/geoip.py); named in the consent, fixed once the survey has started
    geo_ip: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Left out of the site admins' overview of all surveys (admin area, tab "Forschende"); the counts on that page
    # still include it. A wish of the survey's admins, changeable at any time
    admin_hidden: Mapped[bool] = mapped_column(default=False, server_default="0")
    # An example survey (demo): every signed-in researcher may open it as "Ansehen" (app/access.py), without
    # commenting, and take a copy of their own; set only by site admins who are its admins (concept
    # demo-befragungen-konzept.md, Ralf 2026-10-11). Changeable at any time, written to the access log
    showcase: Mapped[bool] = mapped_column(default=False, server_default="0")
    # an example survey of its organisation (app/orgs.py, A13): every member of the survey's organisation may open it
    # as "Ansehen"; set by the admins of that organisation who are admins of the survey (migration 0072)
    org_showcase: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Participants get a deletion code on the end page and the privacy page; with it they delete their own
    # answers later, from any device (app/erasure.py). On for new surveys, off for those from before the code
    # existed (their consent says what holds without it); named in the consent, fixed once the survey has started
    erase_code: Mapped[bool] = mapped_column(default=False, server_default="0")  # off for new surveys too since the pre-test 2026-10-16
    # Seal chain (app/seal.py; on for new surveys, SEAL_DEFAULT): every real participation that ended gets a numbered entry in a
    # hash chain, deletions a deletion record, and the chain is stamped by the DFN. Can be switched on at any time,
    # never off once there is a chain: a chain that stops would hide what was deleted after
    seal: Mapped[bool] = mapped_column(default=lambda: _seal_default(), server_default="0")
    # Retention periods in months, counted from the end of the fieldwork (app/retention.py): {"personal": the
    # identifiers stored from the link and the entries of the separated section, "answers": all participations};
    # a missing or empty value = no period. New surveys start with 6 months and 10 years; surveys from before the
    # periods existed have none. Named in the consent; with real participations a period can only get shorter
    retention: Mapped[dict | None] = mapped_column(JSON, default=lambda: {"personal": 6, "answers": 120})
    # paper questionnaires (experimental "paper", app/paper.py): {"review": "unsure" | "all" | "none" (which sheets are
    # checked by hand before they become participations), "keep": "0" | "30" | "answers" (how long scans stay after
    # the sheet was taken over)}; None = the defaults
    paper: Mapped[dict | None] = mapped_column(JSON)
    # A note for pages sent faster than they can be read, see app/tempo.py: "off" | "all" (every page with
    # questions) | "blocks" (the blocks with settings["tempo_hint"]); shown "once" per participation or "always"
    tempo_hint: Mapped[str] = mapped_column(String(10), default="off", server_default="off")
    tempo_dose: Mapped[str] = mapped_column(String(10), default="once", server_default="once")
    # More of the hint (tempo.options): {"rule": "words" | "median", "read": also pages without a question,
    # "tasks": also the choice tasks of conjoint and MaxDiff}; empty = reading time, pages with questions only
    tempo_options: Mapped[dict | None] = mapped_column(JSON)
    # An own wording of the hint: {lang: {"hint": on pages with questions, "hint_read": on pages without}};
    # empty = the standard wording (tempo.message)
    tempo_texts: Mapped[dict | None] = mapped_column(JSON)
    # Quality assurance (page "Qualitätssicherung"): a completed participation faster than this share of the median
    # completion time (percent, 5..100) counts as a speeder, in results and in every export (app/export.py)
    speeder_pct: Mapped[int] = mapped_column(default=50, server_default="50")
    # Which speeder rule holds (export.speeder_rule): "duration" (speeder_pct above) | "rsi" (the relative speed
    # index of Leiner 2019 over the page times, only with page_timing) | "" = the default: the index with page
    # times, else the duration. speeder_rsi: the limit of the index in tenths (20 = 2.0); above it = speeder.
    # New surveys get "" (the default rule); surveys that existed when the column was added keep "duration"
    # (server_default, filled in by add_missing_columns), so a running survey does not change its speeders
    speeder_rule: Mapped[str] = mapped_column(String(10), default="", server_default="duration")
    speeder_rsi: Mapped[int] = mapped_column(default=20, server_default="20")
    # The data pool of the results, set on the page "Qualitätssicherung" and offered first on the export page:
    # {"sources": ["real", ...], "all": bool (also unfinished)}; empty = real, completed (surveys.stored_pool)
    data_pool: Mapped[dict | None] = mapped_column(JSON)
    # Which conspicuous things mark a participation besides speeding (app/quality.py, rules()):
    # {"flags": ["straight", "missing", "attention", "self"], "min": 1..4}; empty = all of them, at least two
    quality_rules: Mapped[dict | None] = mapped_column(JSON)
    # The waves of a longitudinal design this survey belongs to (app/waves.py, page "Studienplan"): {"waves": [{"survey":
    # id, "label": "vorher"}, ...]} in their order, this survey among them; the same list is kept by every wave the person
    # who set it may edit. Empty = no waves. Not copied when the survey is duplicated (duplicate.SETTINGS)
    waves: Mapped[dict | None] = mapped_column(JSON)
    # The category schemes of open answers ("Kategorisieren" on the results page, app/coding.py): {key: {"single": bool,
    # "cats": [{"id", "name", "definition", "example", "group", "fixed"}], "next": int}}, key = item id + the rest of the
    # column after the item's code ("12", "12_a3_text"). The assignments lie in response.coding. Not copied when the
    # survey is duplicated: they belong to the answers
    codings: Mapped[dict | None] = mapped_column(JSON)
    # Participations that quality assurance marks as they complete (speeder against the median so far, or the marking
    # rule) fill no quota place: the place is given back (quotas.settle). Off = they fill it as before; some panel
    # providers want no filtering at run time. Page "Quoten", changeable in the field
    quota_drop_marked: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Weighting of the results by raking (app/weights.py), page "Quoten": {"kind": "quotas" | "census", "quotas":
    # [quota codes], "census": ["age", "gender", "state"]}; empty = no weighting. The results page offers "gewichtet"
    weighting: Mapped[dict | None] = mapped_column(JSON)
    # {lang: {"thanks": ..., "declined": ..., "closed": ...}}; empty = standard wording
    end_texts: Mapped[dict | None] = mapped_column(JSON)
    # Separate contact form after completion (raffle, comments), see ContactEntry
    # {"enabled": bool, "email": bool, "comment": bool, "texts": {lang: {"intro": ...}}}
    contact: Mapped[dict | None] = mapped_column(JSON)
    # Survey flow tree (randomizers, branches, end nodes over blocks), see app/flow.py
    flow: Mapped[dict | None] = mapped_column(JSON)
    # the standard of the scales of this survey (app/scale_default.py; user test 2026-10-27, point 10): {"points",
    # "anchors", "labeling", "label_display", "numbers"}; None: no standard, each scale as set at it
    scale_default: Mapped[dict | None] = mapped_column(JSON)
    # Declined consents are not stored; only this bare number is kept (for reporting decline rates)
    declined_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # Illustration style for all AI images: {"key": "watercolor" | ... | "custom", "custom": "..."}
    image_style: Mapped[dict | None] = mapped_column(JSON)

    default_language: Mapped[str] = mapped_column(String(8), default="de")
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["de"])

    # Field period: at least one of field_end / max_responses must be set before starting
    field_end: Mapped[datetime | None]
    max_responses: Mapped[int | None]
    opened_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    close_reason: Mapped[str] = mapped_column(String(20), default="")  # manual | time | sample | quota | paused
    # a planned start of the fieldwork (UTC; "Start planen" on the page "Datenerhebung"): the housekeeping starts the
    # draft or paused survey then, by the same path as the button (lifecycle.start_due_surveys); None = none planned
    start_at: Mapped[datetime | None]
    # what the planned start goes with: {"field_end": ISO UTC or None, "max_responses": n or None, "lang": "de" | "en",
    # "by": user id}; after a planned start that did not go through {"failed": reason, "planned": ISO UTC, "lang"}
    # (shown on the page until someone starts, plans again or removes the note). Neither is copied with the survey
    start_plan: Mapped[dict | None] = mapped_column(JSON)
    # [{"at": ISO UTC, "event": "start" | "close", "reason": ...}] for the timeline on the Export page
    status_log: Mapped[list | None] = mapped_column(JSON)
    # a live vote (app/live/) instead of a questionnaire: {"current": item id or None, "open": answers taken,
    # "show": results on the screen}; None for every other survey
    live: Mapped[dict | None] = mapped_column(JSON)
    # an interview guide (app/interview.py) instead of a questionnaire: {"template", "target", "mode", "order",
    # "short", "post"}; None for every other survey. Askurious collects no answers for it (concept
    # interviewleitfaden-konzept.md): no participant link, no consent, nothing stored from the interview view
    interview: Mapped[dict | None] = mapped_column(JSON)
    # a diary study (experience sampling, app/diary.py) instead of a questionnaire: {"modules": [{"key", "name",
    # "trigger": {"t": "fixed", "times", "weekdays"} | {"t": "self", "label", "max_per_day"}, "expires", "days",
    # "notify"}], "period_days", "start": "same_day" | "next_day"}; the blocks name their part in
    # Block.settings["part"] ("entry", a module key, "exit"). None for every other survey
    sampling: Mapped[dict | None] = mapped_column(JSON)
    # Questionnaire version: raised when a paused/closed survey is opened for editing again
    qversion: Mapped[int] = mapped_column(default=1, server_default="1")

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    members: Mapped[list["SurveyMember"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", order_by="(SurveyMember.position, SurveyMember.id)"
    )
    # ordered: PostgreSQL gives unordered rows in no fixed order (e.g. the rows of a pooled construct export)
    # passive_deletes: lifecycle.delete removes the participations in one statement first (code review 2026-10-07, K8)
    responses: Mapped[list["Response"]] = relationship(back_populates="survey", cascade="all, delete-orphan",
                                                       order_by="Response.id", passive_deletes=True)
    blocks: Mapped[list["Block"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", order_by="Block.position"
    )
    trash: Mapped[list["TrashedItem"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", order_by="TrashedItem.deleted_at.desc()"
    )
    # newest first; on deletion of the survey the labels stay taken (survey_id becomes NULL)
    labels: Mapped[list["SurveyLabel"]] = relationship(order_by="SurveyLabel.created_at.desc()")
    # groups the survey is shared with (app/access.py): access for their members, not authorship
    group_links: Mapped[list["SurveyGroup"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", order_by="SurveyGroup.id")
    pins: Mapped[list["SurveyPin"]] = relationship(cascade="all, delete-orphan")
    # quotas (app/quotas.py): cells with a target, calculated at a "Quotenberechnung" in the flow
    quotas: Mapped[list["Quota"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", order_by="(Quota.position, Quota.id)")
    quota_shares: Mapped[list["QuotaShare"]] = relationship(cascade="all, delete-orphan", order_by="QuotaShare.id")
    # dashboards (app/boards/): pages of tiles with results, quotas and field figures, shown by a link
    boards: Mapped[list["Board"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan", order_by="(Board.position, Board.id)")
    translation_marks: Mapped[list["TranslationMark"]] = relationship(cascade="all, delete-orphan")
    # how many participations were deleted on request, as bare numbers per day (app/erasure.py)
    erasures: Mapped[list["Erasure"]] = relationship(cascade="all, delete-orphan", order_by="Erasure.day")
    # the look of the participant pages (app/theme.py); no row = the standard look
    theme_row: Mapped["SurveyTheme | None"] = relationship(back_populates="survey", cascade="all, delete-orphan", uselist=False)

    @property
    def theme(self):
        return self.theme_row.data if self.theme_row else None

    @theme.setter
    def theme(self, data):
        if not data:
            self.theme_row = None
        elif self.theme_row is None:
            self.theme_row = SurveyTheme(data=data)
        else:
            self.theme_row.data = data

    @property
    def label(self):
        return self.labels[0].label if self.labels else None

    @property
    def public_key(self):
        """What the participant link shows: the newest label, else the code."""
        return self.label or self.code

    @property
    def block_start(self):
        """Number of the first block: 0 if it is the consent block (the consent is block 0), else 1."""
        first = self.blocks[0] if self.blocks else None
        return 0 if first and len(first.items) == 1 and first.items[0].type == "consent" else 1

    def next_block_title(self):
        """Default name for a new block: counts content blocks only, so the first one after the
        consent is "Block 1", not "Block 2"."""
        content = [b for b in self.blocks if not (len(b.items) == 1 and b.items[0].type == "consent")
                   and not (b.settings or {}).get("demographics")]  # neither consent nor the demographics block
        return f"Block {len(content) + 1}"

    @property
    def kind(self):
        """"live" (live vote), "interview" (interview guide), "diary" (diary study) or "survey" (a questionnaire): the
        columns `live`, `interview` and `sampling` tell them apart, at most one of them is set."""
        if self.live is not None:
            return "live"
        if self.interview is not None:
            return "interview"
        return "diary" if self.sampling is not None else "survey"

    @property
    def consent_item(self):
        """The survey's consent: the first one alone in its block, else the first one."""
        consents = [i for b in self.blocks for i in b.items if i.type == "consent"]
        alone = [i for i in consents if len(i.block.items) == 1]
        return (alone or consents or [None])[0]

    @property
    def is_editable(self):
        """Logic may only change in draft; running/closed surveys allow text fixes only."""
        return self.status == self.DRAFT

    @property
    def has_run(self):
        """Started at least once, also when a draft again in a new revision: the page "Datenerhebung" then shows status
        and timeline on top (Ralf, 2026-10-11)."""
        return (self.status != self.DRAFT or self.opened_at is not None
                or any(e.get("event") in ("start", "edit") for e in self.status_log or []))

    def member_of(self, user):
        """The person's own entry in the list of co-owners (authors), or None; access may also come through a
        group, see role_of."""
        return next((m for m in self.members if m.user_id == user.id), None)

    def role_of(self, user):
        """The role this person has on the survey, as co-owner or through a group (app/access.py); None = no access."""
        from app import access

        return access.role(user, self)

    def title_in(self, lang=None):
        """The title for participants in a language, else in the default language."""
        if lang and lang != self.default_language:
            return (self.title_translations or {}).get(lang) or self.title
        return self.title

    def can(self, user, need):
        """True if `user` has at least role `need` (viewer < editor < admin)."""
        role = self.role_of(user)
        return role is not None and SurveyMember.LEVEL[role] >= SurveyMember.LEVEL[need]

    def admins(self):
        return [m for m in self.members if m.role == "admin"]

    def tally(self):
        """{(source, is_preview, status): number of participations}, counted by the database. The pages of a
        survey used to load every participation with all its answers to count them. Counted once per request
        (counted_once). A diary study (app/diary.py) counts persons: the participation of each person's entry part;
        its entries are counted by diary.entry_tally."""
        def count():
            if self.sampling is not None:  # the entry parts only, filtered by the database (audit 2026-10-11, M10)
                rows = db.session.execute(
                    db.select(Response.source, Response.is_preview, Response.status, db.func.count(Response.id))
                    .filter(Response.survey_id == self.id, Response.part_expr() == "entry")
                    .group_by(Response.source, Response.is_preview, Response.status))
                counts = {}
                for source, preview, status, n in rows:
                    counts[(source, bool(preview), status)] = counts.get((source, bool(preview), status), 0) + n
                return counts
            rows = db.session.execute(
                db.select(Response.source, Response.is_preview, Response.status, db.func.count(Response.id))
                # further words of a live vote are no participations; only live votes have them (audit 2026-10-21, B2)
                .filter(Response.survey_id == self.id, *([Response.not_live_extra()] if self.live is not None else []))
                .group_by(Response.source, Response.is_preview, Response.status))
            return {(source, bool(preview), status): n for source, preview, status, n in rows}
        return dict(counted_once(("tally", self.id), count))

    def has_real_responses(self):
        return db.session.scalar(db.select(Response.id).filter_by(survey_id=self.id, source="real").limit(1)) is not None

    def completed_count(self):
        if self.sampling is not None:  # a diary study: persons whose entry part is completed (the sample target)
            return counted_once(("completed", self.id), lambda: sum(
                n for (source, preview, status), n in self.tally().items() if not preview and status == Response.COMPLETED))
        return counted_once(("completed", self.id), lambda: db.session.scalar(
            db.select(db.func.count(Response.id)).filter(
                Response.survey_id == self.id, Response.completed_at.is_not(None), Response.is_preview.is_(False),
                *([Response.not_live_extra()] if self.live is not None else [])
            )
        ))


class SurveyTheme(db.Model):
    """The theme of a survey: colours, font and corners of the pages participants see (app/theme.py). A table of
    its own instead of a column of Survey: db.create_all() adds a missing table by itself, a new column would
    need a manual ALTER TABLE on every server."""

    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    survey: Mapped["Survey"] = relationship(back_populates="theme_row")


class Design(db.Model):
    """A theme a researcher keeps under a name (app/designs.py): a copy of a survey's theme, used again by copying
    it into another survey. Its logo files lie in instance/uploads/_design_<id>/."""

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    # shared with the whole organisation it belongs to (app/orgs.py, A13): every member may apply and duplicate it
    org_shared: Mapped[bool] = mapped_column(default=False, server_default="0")

    # the organisation it belongs to (app/orgs.py, konzept-abteilungen-2026-10-15.md): a fence, not an access; None =
    # the organisation everything started in. No foreign key: the column is added to a table with triggers without
    # copying it, and an organisation is only deleted once nothing belongs to it (migration 0071)
    org_id: Mapped[int | None] = mapped_column(index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    owner: Mapped["User"] = relationship()
    shares: Mapped[list["DesignShare"]] = relationship(cascade="all, delete-orphan", order_by="DesignShare.id")
    editors: Mapped[list["DesignEditor"]] = relationship(cascade="all, delete-orphan", order_by="DesignEditor.id")
    group_shares: Mapped[list["DesignGroupShare"]] = relationship(
        back_populates="design", cascade="all, delete-orphan", order_by="DesignGroupShare.id")


class DesignShare(PersonRow, db.Model):
    """A design is shared with a researcher (by ORCID iD, so also before that person's first sign-in): they may
    use and duplicate it, and change it only with the right to edit (DesignEditor)."""

    __table_args__ = (UniqueConstraint("design_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("design.id", ondelete="CASCADE"), index=True)
    # the account (konto-schluessel-plan.md B1)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class DesignEditor(PersonRow, db.Model):
    """The right of one researcher to change a design that is shared with them in person (designs.may_edit),
    given and taken by its owner. A table of its own, so the tables of the running servers stay as they are."""

    __tablename__ = "design_editor"
    __table_args__ = (UniqueConstraint("design_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("design.id", ondelete="CASCADE"), index=True)
    # the account (konto-schluessel-plan.md B1)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class WorkGroup(db.Model):
    """A group of researchers (app/groups.py): a named list of people. A group owns nothing; surveys and designs
    stay with persons, and a group only carries access to what was shared with it. So deleting a group, or
    leaving one, ends that access and loses nothing."""

    __tablename__ = "workgroup"  # "group" is a reserved word in SQL

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    # set by site admins only: whoever joins gets "create" for every type at "edit", once (kind_access.joined, E3 of
    # konzept-stufe-anlegen-2026-10-18.md)
    may_create: Mapped[bool] = mapped_column(default=False)
    # set by site admins only: the survey types a new account gets when it comes in through this group (join link,
    # invitation), once (app/kind_access.py); None = no preset, the default for new accounts holds
    kinds_preset: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    # set by site admins only: a course (Lehrveranstaltung, app/account_types.py) gives the new accounts that come in
    # through it this user type, valid until course_until (end of that day)
    account_type_id: Mapped[int | None] = mapped_column(ForeignKey("account_type.id", ondelete="SET NULL"), index=True)
    course_until: Mapped[datetime | None]
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)

    # the organisation it belongs to (app/orgs.py, konzept-abteilungen-2026-10-15.md): a fence, not an access; None =
    # the organisation everything started in. No foreign key: the column is added to a table with triggers without
    # copying it, and an organisation is only deleted once nothing belongs to it (migration 0071)
    org_id: Mapped[int | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    members: Mapped[list["WorkGroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan",
        order_by="(WorkGroupMember.role, WorkGroupMember.id)")  # "lead" sorts before "member"
    design_shares: Mapped[list["DesignGroupShare"]] = relationship(back_populates="group", cascade="all, delete-orphan")
    construct_shares: Mapped[list["ConstructGroupShare"]] = relationship(back_populates="group", cascade="all, delete-orphan")
    survey_links: Mapped[list["SurveyGroup"]] = relationship(back_populates="group", cascade="all, delete-orphan")
    experiment_grants: Mapped[list["ExperimentGrant"]] = relationship(cascade="all, delete-orphan")  # app/experimental.py

    def member_of(self, user):
        return next((m for m in self.members if m.user_id == user.id), None)

    def leads(self):
        return [m for m in self.members if m.role == "lead"]

    def led_by(self, user):
        m = self.member_of(user)
        return m is not None and m.role == "lead"


class WorkGroupMember(PersonRow, db.Model):
    """A member of a group. Stored by ORCID like SurveyMember, so people can be added before their first sign-in."""

    __tablename__ = "workgroup_member"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("workgroup.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")  # cached for not-yet-registered people
    role: Mapped[str] = mapped_column(String(10), default="member")  # lead | member, see ROLES
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # how the person came in, when it was not by being added: "link" (a join link of the group), "leadlink" (a lead
    # link of a site admin); app/joinlinks.py, gruppen-freischaltung-konzept.md
    via: Mapped[str | None] = mapped_column(String(10))

    group: Mapped[WorkGroup] = relationship(back_populates="members")
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])

    ROLES = {
        "lead": ("Leitung", "Mitglieder aufnehmen und entfernen, Rollen ändern, Gruppe umbenennen und löschen"),
        "member": ("Mitglied", "sieht die Gruppe und was mit ihr geteilt wurde; kann jederzeit austreten"),
    }
    ROLES_EN = {
        "lead": ("Lead", "add and remove members, change roles, rename and delete the group"),
        "member": ("Member", "sees the group and what was shared with it; can leave at any time"),
    }

    @classmethod
    def roles(cls):
        """{role: (label, what it may)} in the language of the page."""
        from app.lang import english

        return cls.ROLES_EN if english() else cls.ROLES

    @property
    def display_name(self):
        real = (self.user and self.user.name) or self.name or "ORCID user"
        if self.user is None:
            return real
        from app import names

        return names.shown(self.user, real)


class ConstructShare(PersonRow, db.Model):
    """A linked element (app/linked.py) is shared with one researcher (by ORCID iD, so also before that person's
    first sign-in): they may insert it and see its shared analysis for the surveys they can open, not change it."""

    __tablename__ = "construct_share"
    __table_args__ = (UniqueConstraint("construct_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    construct_id: Mapped[int] = mapped_column(ForeignKey("construct.id", ondelete="CASCADE"), index=True)
    # the account (konto-schluessel-plan.md B1)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ConstructGroupShare(db.Model):
    """A linked element (app/linked.py) is shared with a group: every member, also future ones, may insert it and
    see its shared analysis for the surveys they can open; only whoever made it changes it."""

    __tablename__ = "construct_group_share"
    __table_args__ = (UniqueConstraint("construct_id", "group_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    construct_id: Mapped[int] = mapped_column(ForeignKey("construct.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("workgroup.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    group: Mapped["WorkGroup"] = relationship(back_populates="construct_shares")


class DesignGroupShare(db.Model):
    """A design is shared with a group: every member may use and duplicate it, not change it (also the leads of
    the group; changing takes the right of a person, DesignEditor). The right to the theme of an institution
    is still checked per person (designs.usable)."""

    __tablename__ = "design_group_share"
    __table_args__ = (UniqueConstraint("design_id", "group_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("design.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("workgroup.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    design: Mapped[Design] = relationship(back_populates="group_shares")
    group: Mapped[WorkGroup] = relationship(back_populates="design_shares")


class SurveyGroup(db.Model):
    """A survey is shared with a group: its members may open it in this role ("viewer" or "editor", never
    "admin"), also those who join the group later. They do not become co-owners (authors); see app/access.py."""

    __tablename__ = "survey_group"
    __table_args__ = (UniqueConstraint("survey_id", "group_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("workgroup.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="viewer")
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    survey: Mapped["Survey"] = relationship(back_populates="group_links")
    group: Mapped[WorkGroup] = relationship(back_populates="survey_links")


class ExperimentGrant(db.Model):
    """A site admin's word on an experimental feature (app/experimental.py) beyond one person's own switch
    (User.experiments): on for a whole group (group_id), for a whole organisation (org_id), on for everyone (neither
    group, organisation nor person), or a person kept out (user_id, allowed false), which wins over all of them."""

    __tablename__ = "experiment_grant"

    id: Mapped[int] = mapped_column(primary_key=True)
    feature: Mapped[str] = mapped_column(String(40), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("workgroup.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organisation.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])
    allowed: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SurveyMember(PersonRow, db.Model):
    """A co-owner of a survey. Stored by ORCID so people can be invited before their first login."""

    __table_args__ = (UniqueConstraint("survey_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")  # cached for not-yet-registered people
    affiliation: Mapped[str] = mapped_column(String(300), default="")
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    # Author order (e.g. in the consent text and later in publications)
    position: Mapped[int] = mapped_column(default=0, server_default="0")
    # admin (everything) | editor (questionnaire, blocks, flow) | viewer (read only), see ROLES
    role: Mapped[str] = mapped_column(String(10), default="admin", server_default="admin")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    survey: Mapped[Survey] = relationship(back_populates="members")
    user: Mapped[User | None] = relationship(foreign_keys=[user_id])

    ROLES = {
        "admin": ("Admin", "alles: Einstellungen, Beteiligte und Rollen, Veröffentlichen, Archivieren und Löschen"),
        "editor": ("Bearbeiten", "Fragebogen, Blöcke, Ablauf, Bilder, Testdurchläufe und Dummy-Daten, Daten herunterladen, Starten, Pausieren und Schließen, Duplizieren und Befragungsdatei; keine Einstellungen"),
        "viewer": ("Ansehen", "nur lesen: Fragebogen, Einstellungen, Vorschau; keine Daten herunterladen, keine Befragungsdatei"),
    }
    # the same roles told in the words of a live vote and an interview guide (Survey.kind; UI/UX audit 2026-10-05,
    # V89): there is no questionnaire, no start/pause
    ROLES_BY_KIND = {
        "live": {
            "admin": ("Admin", "alles: Einstellungen, Beteiligte und Rollen"),
            "editor": ("Bearbeiten", "Fragen bearbeiten und präsentieren; keine Einstellungen"),
            "viewer": ("Ansehen", "nur zusehen: Fragen und Ergebnisse lesen"),
        },
        "interview": {
            "admin": ("Admin", "alles: Einstellungen, Beteiligte und Rollen"),
            "editor": ("Bearbeiten", "Leitfaden bearbeiten und Interviews führen; keine Einstellungen"),
            "viewer": ("Ansehen", "Leitfaden lesen und die Interviewansicht durchgehen, ohne zu speichern; keine Interviewdaten"),
        },
    }
    LEVEL = {"viewer": 0, "editor": 1, "admin": 2}
    # the same in English (app/lang.py), key by key
    ROLES_EN = {
        "admin": ("Admin", "everything: settings, people involved and roles, publishing, archiving and deleting"),
        "editor": ("Edit", "questionnaire, blocks, flow, images, test runs and dummy data, downloading data, starting, pausing and closing, duplicating and the survey file; no settings"),
        "viewer": ("View", "read only: questionnaire, settings, preview; no downloading data, no survey file"),
    }
    ROLES_BY_KIND_EN = {
        "live": {
            "admin": ("Admin", "everything: settings, people involved and roles"),
            "editor": ("Edit", "edit and present questions; no settings"),
            "viewer": ("View", "watch only: read questions and results"),
        },
        "interview": {
            "admin": ("Admin", "everything: settings, people involved and roles"),
            "editor": ("Edit", "edit the guide and conduct interviews; no settings"),
            "viewer": ("View", "read the guide and go through the interview view without saving; no interview data"),
        },
    }

    @classmethod
    def roles(cls, kind="survey"):
        """{role: (label, what it may)} in the words of a kind (questionnaire, live vote, guide), in the language of
        the page."""
        from app.lang import english

        if english():
            return cls.ROLES_BY_KIND_EN.get(kind, cls.ROLES_EN)
        return cls.ROLES_BY_KIND.get(kind, cls.ROLES)

    @classmethod
    def roles_for(cls, survey):
        """{role: (label, what it may)} in the words of the survey's kind (questionnaire, live vote, guide)."""
        return cls.roles(getattr(survey, "kind", "survey"))

    @property
    def display_name(self):
        real = (self.user and self.user.name) or self.name or "ORCID user"
        if self.user is None:
            return real
        from app import names

        return names.shown(self.user, real)

    @property
    def is_former(self):
        """The account of this author was deleted: the entry keeps name and affiliation, nobody has access by it."""
        return self.user_id is None


class Response(db.Model):
    """One participant's session. Anonymous: no IP, no user agent.

    Preview runs by researchers are stored as responses with is_preview=True; they are
    never counted, exported, or used for balancing.
    """

    ACTIVE, COMPLETED, DECLINED, SCREENOUT = "active", "completed", "declined", "screenout"
    QUOTAFULL = "quotafull"  # ended at an end of the kind "Quote voll" (flow.END_KINDS)
    EXCLUDED = "excluded"  # ended at an end of the kind "Ausschluss", e.g. after failed attention checks
    ENDED_EARLY = (SCREENOUT, QUOTAFULL, EXCLUDED)  # ended by the flow before the last page

    id: Mapped[int] = mapped_column(primary_key=True)
    # no index of its own: ix_response_counts and ix_response_start_flood begin with it (migration 0088, PERF-10)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id"))
    language: Mapped[str] = mapped_column(String(8), default="de")
    # is_preview: not a real participant (test run or dummy); never counted, balanced, or auto-closing
    is_preview: Mapped[bool] = mapped_column(default=False, server_default="0")
    source: Mapped[str] = mapped_column(String(10), default="real", server_default="real")  # real | test | dummy
    status: Mapped[str] = mapped_column(String(10), default=ACTIVE, server_default="active")
    position: Mapped[int] = mapped_column(default=0, server_default="0")  # current page index
    answers: Mapped[dict] = mapped_column(JSON, default=dict)  # {item_id (str): answer}
    layout: Mapped[dict | None] = mapped_column(JSON)  # presentation order for this response (runtime.build_layout)
    paradata: Mapped[dict | None] = mapped_column(JSON)  # coarse device categories, if the survey records them
    params: Mapped[dict | None] = mapped_column(JSON)  # URL parameters declared with mode "store" (pseudonymous)
    # params["live_extra"] once more as a column of its own: a further word of a live vote (app/live/audience.py, T-19)
    # is no participation, and every count leaves it out. Read from the JSON, the counts read every row instead of the
    # index ix_response_counts (performance audit 2026-10-18, PERF-02: 50 ms instead of 1.4 ms at 10 000, the platform's
    # count a full scan). Kept in step with params by _live_extra_of_params; a row an older program wrote during a
    # rolling deploy is set by mark_live_extra (housekeeping)
    live_extra: Mapped[bool] = mapped_column(default=False, server_default="0")
    qversion: Mapped[int] = mapped_column(default=1, server_default="1")  # questionnaire version answered
    # participant ID for texts and redirects ({{pid}}), e.g. to link an external web app; see runtime.ensure_layout
    pid: Mapped[str | None] = mapped_column(String(40), unique=True)
    # where the participation came from, estimated from the IP address if the survey asks for it (Survey.geo_ip,
    # app/geoip.py): ISO code of the country and, in Germany, the state key of the element Bundesland. None = not
    # looked up, "" = looked up without a result. The address itself is never stored
    geo_country: Mapped[str | None] = mapped_column(String(2))
    geo_state: Mapped[str | None] = mapped_column(String(2))
    # the hash of the deletion code this participation was shown (Survey.erase_code, app/erasure.py): whoever
    # has the code finds the row by it; the row itself does not tell the code. None = no code was shown
    erase_hash: Mapped[str | None] = mapped_column(String(64))
    # an entry of a diary study (app/diary.py): the person it belongs to, and which part it is: {"part": "entry" |
    # module key | "exit", "n": number of the entry within the module, "day": day of the study period, "due": the
    # prompt's time (UTC, ISO) or None when the person started it, "notified": when the phone showed the prompt,
    # "tz": the time zone of the phone, "trigger": "fixed" | "self"}. None for every other participation
    # no foreign key, like enrolment.survey_id; no index of its own: ix_response_diary_started begins with it and
    # holds every row that has one (migration 0088, PERF-10)
    enrolment_id: Mapped[int | None] = mapped_column()
    occasion: Mapped[dict | None] = mapped_column(JSON)
    # occasion["part"] once more as a column of its own, so that the counts of a diary study (Survey.tally,
    # timeline._real_times, diary.entry_tally) filter in the database instead of reading the JSON of every entry
    # (audit 2026-10-11, M10/PF4). Kept in step with occasion by _part_of_occasion; None for every other
    # participation, and for an entry an older program wrote during a rolling deploy (part_expr reads the JSON then)
    part: Mapped[str | None] = mapped_column(String(8))
    # an entry of a diary study (audit 2026-10-11, M1, M2, M5): when the server received it (an entry answered in
    # the app's browser: its completion; one answered without a connection and sent later: the moment it arrived,
    # while started and completed are what the phone says), the prompt's time to the minute (one entry per person,
    # part and prompt: ix_response_prompt), and the random id the app gives each answer (ix_response_uuid: an answer
    # sent twice is stored once). None for every other participation
    received_at: Mapped[datetime | None]
    due_at: Mapped[datetime | None]
    uuid: Mapped[str | None] = mapped_column(String(36))
    # an interview of an interview guide (app/interview_data.py, experimental "Interviews erheben"): {"label", "by",
    # "by_name", "date", "mode", "consent": {"form", "audio"}, "elapsed", "blocks": {block id: seconds},
    # "marks": [{"t", "item", "text"}]}; None for every participation of a questionnaire or a live vote
    interview: Mapped[dict | None] = mapped_column(JSON)
    # what the quota calculations of the flow decided for this participation, once (app/quotas.py):
    # {quota id (str): {"cell": cell id or None, "res": "in" | "full" | "none" | "other" | "over",
    #                   "held": a place is reserved, "done": counted as completed, "marked": marked by quality
    #                   control at completion, the place given back (Survey.quota_drop_marked)}}
    quota: Mapped[dict | None] = mapped_column(JSON)
    # the external sections of the flow this participation left for and came back from (app/external.py):
    # {node id: {"k": key of its return link, "out": first left, "n": times left, "back": came back,
    #            "s": seconds outside, "v": {declared value: what the web app handed back}}}
    external: Mapped[dict | None] = mapped_column(JSON)
    # the categories researchers assigned to this participation's open answers (app/coding.py, schemes in
    # survey.codings): {key: [category id, ...]}; a key missing = not categorised yet. Part of the participation, so
    # it goes with a deletion request, a retention period and the survey (concept K7)
    coding: Mapped[dict | None] = mapped_column(JSON)
    # how the answers came in: None = online, "paper" = a paper questionnaire taken over from a scan (app/paper.py,
    # export column "mode"). A paper sheet has no completion time: started and finished are the moment of taking over
    channel: Mapped[str | None] = mapped_column(String(10))
    # a paper sheet: {"sheet": its number, "run": the print run id, "scanned": first scan (ISO), "checked_by": user id,
    # "checked_at": ISO, "changes": corrections made by hand}; None online
    paper: Mapped[dict | None] = mapped_column(JSON)
    # left for an external section and not back yet: since when (the Feld page counts these)
    outside_since: Mapped[datetime | None]
    last_active_at: Mapped[datetime | None]
    # both with an index: the dashboard of Administration > Befragungen counts by them per period (app/admin_stats.py)
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(index=True)
    # counts the changes of the row. Two requests of one participation that overlap (a page sent twice) both read
    # it before either has stored anything; the one that stores second is refused (StaleDataError) and nothing of
    # it is kept, so it cannot take a second quota place or count a completion twice (app/participate.py)
    version: Mapped[int] = mapped_column(default=1, server_default="1")

    __mapper_args__ = {"version_id_col": version}
    # the counts of a survey (Survey.tally, completed_count, export.completion_seconds, the admin list, the footer)
    # read only this index, not the rows: a row holds all answers (5 KB on average), and counting 5000 of them took
    # 23 ms on SQLite, 0.3 ms with the index (audit 2026-10-06, P3)
    # ix_response_outside: the housekeeping looks once a minute for who is outside past the return link
    # (external.sweep_params); only the few rows with outside_since are in it (code review 2026-10-07, D6)
    # ix_response_erase_hash: the page "Antworten löschen" of the platform looks a deletion code up in all surveys
    # (erasure.anywhere); only rows that were shown a code are in it
    # ix_response_start_flood: the start limit of a survey (limits.survey_start_flood) walks the newest real
    # participations of the survey; with this index it reads no row (16 ms -> 0.08 ms at 5000; audit 2026-10-09, N-7)
    # live_extra last: the counts that leave further words of a live vote out still read only the index (PERF-02)
    __table_args__ = (Index("ix_response_counts", "survey_id", "source", "is_preview", "status", "completed_at",
                            "started_at", "live_extra"),
                      Index("ix_response_start_flood", "survey_id", "source", "id", "started_at"),
                      Index("ix_response_outside", "outside_since", sqlite_where=text("outside_since IS NOT NULL"),
                            postgresql_where=text("outside_since IS NOT NULL")),
                      Index("ix_response_erase_hash", "erase_hash", sqlite_where=text("erase_hash IS NOT NULL"),
                            postgresql_where=text("erase_hash IS NOT NULL")),
                      # ix_response_channel: does a survey hold paper questionnaires (export.has_paper, every export and
                      # results computation); only the few rows with a channel are in it (audit 2026-10-22, G-03: 50 ms
                      # at 10 000 participations, reading every row)
                      Index("ix_response_channel", "survey_id", "channel", sqlite_where=text("channel IS NOT NULL"),
                            postgresql_where=text("channel IS NOT NULL")),
                      # ix_response_part: the entries of a diary study by their part (only rows with a part)
                      Index("ix_response_part", "survey_id", "part", sqlite_where=text("part IS NOT NULL"),
                            postgresql_where=text("part IS NOT NULL")),
                      # one entry per person, part and prompt; one row per answer the app sends (its uuid)
                      Index("ix_response_prompt", "enrolment_id", "part", "due_at", unique=True,
                            sqlite_where=text("due_at IS NOT NULL"), postgresql_where=text("due_at IS NOT NULL")),
                      Index("ix_response_diary_started", "enrolment_id", "part", "started_at",
                            sqlite_where=text("enrolment_id IS NOT NULL"),
                            postgresql_where=text("enrolment_id IS NOT NULL")),
                      Index("ix_response_uuid", "enrolment_id", "uuid", unique=True,
                            sqlite_where=text("uuid IS NOT NULL"), postgresql_where=text("uuid IS NOT NULL")))

    survey: Mapped[Survey] = relationship(back_populates="responses")

    @validates("occasion")
    def _part_of_occasion(self, _key, value):
        """Every occasion that is set also sets the column part (None without a part)."""
        part = value.get("part") if isinstance(value, dict) else None
        self.part = str(part)[:8] if part else None
        return value

    @validates("params")
    def _live_extra_of_params(self, _key, value):
        """Every params that is set also sets the column live_extra: only the mark live votes write themselves (True),
        never a URL parameter's value (a string; audit 2026-10-21, B2)."""
        self.live_extra = isinstance(value, dict) and value.get("live_extra") is True
        return value

    @classmethod
    def part_expr(cls):
        """The part of an entry in SQL: the column, else (a row of an older program) occasion["part"], else "entry",
        as (occasion or {}).get("part", "entry") reads it in Python."""
        return db.func.coalesce(cls.part, cls.occasion["part"].as_string(), "entry")

    @classmethod
    def not_live_extra(cls):
        """In SQL: not a further word of a live vote (params["live_extra"], app/live/audience.py, T-19), which is no
        participation of its own: counts of participations leave it out, as the live view and the export do (Ralf,
        2026-10-17: 94 instead of 40 in Administration > Befragungen). On the column live_extra, which ix_response_counts
        holds: the count reads no row (PERF-02)."""
        return cls.live_extra.is_(False)


def live_extra_in_params(dialect=None):
    """In SQL: params says it is a further word of a live vote (what the column live_extra mirrors): the JSON value
    true, nothing else. Compared, not cast: a value that is no boolean (a URL parameter of that name) stopped the cast
    on PostgreSQL (audit 2026-10-21, B14). SQLite gives JSON true as 1, so it asks for the JSON type instead."""
    dialect = dialect or db.session.get_bind().dialect.name
    if dialect == "sqlite":
        return db.func.coalesce(db.func.json_type(Response.params, "$.live_extra") == "true", False)
    return db.func.coalesce(Response.params["live_extra"].as_string() == "true", False)


def mark_live_extra():
    """Sets live_extra on the further words of live votes an older program stored without it (during a rolling deploy
    the participant container not yet restarted runs the old code; migration 0087 set the ones before). Reads only
    the rows of live votes; returns how many it set. Housekeeping, hourly (lifecycle._rare_sweeps)."""
    live = db.select(Survey.id).where(json_given(Survey.live))
    n = db.session.execute(db.update(Response).where(
        Response.survey_id.in_(live), Response.live_extra.is_(False), live_extra_in_params())
        .values(live_extra=True).execution_options(synchronize_session=False)).rowcount or 0
    db.session.commit()
    return n


class PaperRun(db.Model):
    """A print run of a paper questionnaire (app/paper.py): a number of sheets in one language, each with its own
    random sheet number and its own drawn layout. `plans`: {plan key: the positions of every box and writing field
    per page of a sheet (app/paper_pdf.py)}; sheets with the same layout share a plan."""

    __tablename__ = "paper_run"
    # no foreign keys in the three paper tables, like group_join_link: their rows go in code with the survey
    # (paper.delete_survey)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    language: Mapped[str] = mapped_column(String(8), default="de")
    qversion: Mapped[int] = mapped_column(default=1)
    count: Mapped[int] = mapped_column(default=0)
    plans: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column()  # no foreign key: the account may go, the run stays
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PaperSheet(db.Model):
    """One printed sheet of a print run. `key`: the random number printed on every page (40 bits, readable as
    7K3Q-9F2M); it says nothing about the person. `state`: printed | scanned (pages under way) | review | done
    (taken over: `response_id`) | declined (no consent: never stored) | erased (deleted on request before or after it
    was taken over) | discarded (thrown away by the researchers). `values`: what the review set by hand {field
    index: "x" ticked | "o" empty | text typed}; `erase_hash`: the hash of the deletion code on the slip."""

    __tablename__ = "paper_sheet"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(index=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    key: Mapped[int] = mapped_column(BigInteger, unique=True)
    number: Mapped[int] = mapped_column(default=1)  # 1..count within its run
    layout: Mapped[dict | None] = mapped_column(JSON)
    plan: Mapped[str] = mapped_column(String(16), default="")
    pages: Mapped[int] = mapped_column(default=1)
    state: Mapped[str] = mapped_column(String(10), default="printed")
    values: Mapped[dict | None] = mapped_column(JSON)
    erase_hash: Mapped[str | None] = mapped_column(String(64))
    # the participation id the sheet becomes (drawn at printing, so the deletion code on its slip can be printed)
    pid: Mapped[str | None] = mapped_column(String(40))
    response_id: Mapped[int | None] = mapped_column()  # no foreign key: a deleted participation leaves the number
    checked_by: Mapped[int | None] = mapped_column()
    checked_at: Mapped[datetime | None]
    done_at: Mapped[datetime | None]
    destroy: Mapped[bool] = mapped_column(default=False)  # deleted on request: the paper is still to be destroyed

    # a deletion code typed on the privacy page that fits no participation is looked up here (paper.erase_unscanned);
    # a partial index like ix_response_erase_hash (audit 2026-10-10, P-S10)
    __table_args__ = (Index("ix_paper_sheet_erase_hash", "erase_hash", sqlite_where=text("erase_hash IS NOT NULL"),
                            postgresql_where=text("erase_hash IS NOT NULL")),)


class PaperPage(db.Model):
    """One scanned page: the file (instance/paper-scans/<survey uid>/<name>), which sheet and page it is, and what
    was read from it (`reading`: {"H": the page's transform, "f": {field index: share of ink}}). A page whose code
    could not be read has no sheet (`sheet_id` None) and goes after a week."""

    __tablename__ = "paper_page"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    sheet_id: Mapped[int | None] = mapped_column(index=True)
    page: Mapped[int] = mapped_column(default=0)
    filename: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(10), default="new")  # new | read | unreadable | foreign | double
    reading: Mapped[dict | None] = mapped_column(JSON)
    uploaded_by: Mapped[int | None] = mapped_column()
    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class OnceValue(db.Model):
    """A value of a URL parameter stored with a real participation, as its SHA-256 (app/panel.py): whether a value
    of a once-only parameter (a panel ID) took part already is one look in the index, not a search through the
    parameters of every participation within the write lock (audit 2026-10-06, P5). Kept in step with
    Response.params by mapper events (_keep_once_values); every value is there, not only those of once-only parameters, so a
    parameter made once-only later still finds the participations before. `once`: claimed as once-only when the
    participation was stored; the UNIQUE index over these keeps two first pages with the same ID sent at the same
    moment from both staying (it took the place of an advisory lock, audit 2026-10-05, T-11)."""

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"))
    response_id: Mapped[int] = mapped_column(ForeignKey("response.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(41))
    value_hash: Mapped[str] = mapped_column(String(64))
    once: Mapped[bool] = mapped_column(default=False, server_default="0")

    __table_args__ = (Index("ix_once_value_lookup", "survey_id", "name", "value_hash"),
                      Index("ix_once_value_claim", "survey_id", "name", "value_hash", unique=True,
                            sqlite_where=text("once"), postgresql_where=text("once")))


class Erasure(db.Model):
    """Participations and entries of the separated section that were deleted because someone asked for it, as
    bare numbers per day and way: "self" (the person, in the browser that took part or with the deletion code)
    or "request" (the researchers, for a person who wrote to them). No id, no time of day, nothing of what was
    deleted: the row says that data left the survey, so that researchers replace files they downloaded before,
    and it cannot tell whose data it was (app/erasure.py)."""

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id"), index=True)
    day: Mapped[str] = mapped_column(String(10))  # local date, YYYY-MM-DD
    way: Mapped[str] = mapped_column(String(10))
    responses: Mapped[int] = mapped_column(default=0)
    contacts: Mapped[int] = mapped_column(default=0)


class SealEntry(db.Model):
    """One entry of a survey's seal chain (app/seal.py): a case sealed when it ended, amended, deleted, or all cases
    deleted at once. Rows are only ever added; none is changed except that the salt and the participation's id go
    with the participation (neither is part of the hash). Kept by the survey's uid, not its id: the chain outlives
    the survey, as the deletion records do the participations."""
    __tablename__ = "seal_entry"
    __table_args__ = (db.UniqueConstraint("survey_uid", "seq", name="uq_seal_entry_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_uid: Mapped[str] = mapped_column(String(16), index=True)
    seq: Mapped[int] = mapped_column()  # 1, 2, … per survey
    # enabled | sealed | amended | deleted | purged | closed | prereg | fieldstarted | fieldclosed
    kind: Mapped[str] = mapped_column(String(20))
    ref: Mapped[int | None] = mapped_column()  # the case (seq of its entry "sealed") an amendment or a deletion is about
    status: Mapped[str | None] = mapped_column(String(10))  # the case's status (Response.status)
    content: Mapped[str | None] = mapped_column(String(64))  # SHA-256 of salt and the case's stored data
    salt: Mapped[str | None] = mapped_column(String(32))  # random; emptied when the case is deleted
    response_id: Mapped[int | None] = mapped_column(index=True)  # emptied when the case is deleted
    # a short code ("erasure_self", "retention", …) or, for a deletion by the platform, "platform: " and its reason
    # in a few words (app/platform_erase.py); part of the hash like every field
    reason: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[str] = mapped_column(String(32))  # UTC, ISO 8601, as it goes into the hash
    prev: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))
    # what the content hash covers (seal.record): empty = 1 (answers, status, language, end, version), 2 = also the
    # values handed back by external sections and web services (audit 2026-10-10, L-15); not part of the hash
    rec: Mapped[int | None] = mapped_column()


class SealChain(db.Model):
    """A survey that has a seal chain: its number (the first part of a check code), uid and title. Written with the
    first entry and kept when the survey is deleted, so that /verify/<code> still finds the chain and shows that the
    survey went and why (audit 2026-10-10, L-1). No foreign key, like the entries."""
    __tablename__ = "seal_chain"

    survey_uid: Mapped[str] = mapped_column(String(16), primary_key=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(300))


class SealAnchor(db.Model):
    """A timestamp of a seal chain (app/seal.py): the DFN (RFC 3161) signed that the chain of survey_uid up to entry
    seq, whose hash is digest, existed at time. `path`: empty if the token stamps digest itself; else the steps from
    digest to the root of the Merkle tree of a round that the token stamps (seal.anchor_round, SEAL_MERKLE)."""
    __tablename__ = "seal_anchor"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_uid: Mapped[str] = mapped_column(String(16), index=True)
    seq: Mapped[int] = mapped_column()
    digest: Mapped[str] = mapped_column(String(64))
    tsa: Mapped[str] = mapped_column(String(200))
    token: Mapped[bytes] = mapped_column(db.LargeBinary)
    time: Mapped[datetime] = mapped_column()  # the time the token states (UTC)
    path: Mapped[list | None] = mapped_column(JSON, nullable=True)  # seal_verify.MERKLE_RULE


class SealStatus(db.Model):
    """The last check of a survey's seal chain against the participations stored now (app/seal.py, check_quick): run
    every hour in the background, at "Jetzt prüfen" and for /verify/. Shown on the tab "Export" and on /verify/ so
    that a case removed past the app shows without anyone pressing a button (siegelkette-konzept.md, E1, E2).
    `problems`: [[kind, value], …] (missing, changed, unsealed, chain), at most PROBLEMS_KEPT; `notified`: a hash of
    the problems the researchers were told of, so that each break is told once."""
    __tablename__ = "seal_status"

    survey_uid: Mapped[str] = mapped_column(String(16), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(default=utcnow)
    chain_ok: Mapped[bool] = mapped_column(default=True)
    data_ok: Mapped[bool] = mapped_column(default=True)
    present: Mapped[int] = mapped_column(default=0)  # cases stored now that match their hash
    missing: Mapped[int] = mapped_column(default=0)  # cases gone without a deletion record
    changed: Mapped[int] = mapped_column(default=0)  # cases whose data changed past the app
    unsealed: Mapped[int] = mapped_column(default=0)  # participations that ended without an entry
    problems: Mapped[list | None] = mapped_column(JSON)
    notified: Mapped[str | None] = mapped_column(String(64))
    # what the survey looked like at the last check (seal.fingerprint) and when the hourly round last looked at it:
    # an unchanged survey is not read again every hour (audit 2026-10-10, S6/P5)
    fingerprint: Mapped[str | None] = mapped_column(String(100))
    looked_at: Mapped[datetime | None]


class Quota(db.Model):
    """A quota: a named set of cells, calculated at one place in the flow (node "quota", app/flow.py). A person
    gets a place in the first cell whose conditions all hold (or, with pick "least", in the least-filled of
    those that hold), or none if there is no room (app/quotas.py)."""

    HARD, SOFT = "hard", "soft"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))  # export columns quota_<code>, quota_<code>_res
    name: Mapped[str] = mapped_column(String(200), default="")
    # hard: reserved and completed places never exceed the target. soft: people are let in until the target is
    # reached by completed participations; those inside may finish, at most `tolerance` above the target
    mode: Mapped[str] = mapped_column(String(10), default=HARD)
    tolerance: Mapped[int] = mapped_column(default=10)
    tolerance_pct: Mapped[bool] = mapped_column(default=True)  # the tolerance is a percentage of each target
    # nobody's cell: always "continue" now; "end" only in surveys started with a "Quotenprüfung" (app/quotas.py)
    no_cell: Mapped[str] = mapped_column(String(10), default="continue")
    # a reserved place is given back after this many minutes without a page being sent (app/quotas.py; 15 for new
    # quotas = quotas.DEFAULT_HOLD since the audit 2026-10-18, BIZ-06)
    hold_minutes: Mapped[int] = mapped_column(default=15)
    # when a participation counts: "complete" = when it is completed (until then its place is only reserved).
    # "check" (at once and for good, whatever happens afterwards) only in surveys started with a "Quotenprüfung"
    count_at: Mapped[str] = mapped_column(String(10), default="complete")
    # several cells fit a person: "first" = the first one in the quota's order, "least" = the least-filled one
    pick: Mapped[str] = mapped_column(String(10), default="first")
    position: Mapped[int] = mapped_column(default=0)

    survey: Mapped[Survey] = relationship(back_populates="quotas")
    cells: Mapped[list["QuotaCell"]] = relationship(
        back_populates="quota", cascade="all, delete-orphan", order_by="(QuotaCell.position, QuotaCell.id)")


class QuotaCell(db.Model):
    """One cell of a quota: conditions that must all hold (as in the flow: {"item", "op", "value"}), a target,
    and the counters. The counters only change through single UPDATE statements (app/quotas.py), so two
    people who arrive at the same moment never share the last place."""

    __tablename__ = "quota_cell"

    id: Mapped[int] = mapped_column(primary_key=True)
    quota_id: Mapped[int] = mapped_column(ForeignKey("quota.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), default="")
    conds: Mapped[list | None] = mapped_column(JSON)
    target: Mapped[int] = mapped_column(default=0)
    closed: Mapped[bool] = mapped_column(default=False)  # closed by hand: nobody gets in, whatever the count
    running: Mapped[int] = mapped_column(default=0)   # reserved places of participations still under way
    done: Mapped[int] = mapped_column(default=0)      # completed participations
    refused: Mapped[int] = mapped_column(default=0)   # turned away because the cell was full
    over: Mapped[int] = mapped_column(default=0)      # of `done`: completed without a reserved place
    freed: Mapped[int] = mapped_column(default=0, server_default="0")  # completed, marked by quality assurance, place given back
    position: Mapped[int] = mapped_column(default=0)

    quota: Mapped[Quota] = relationship(back_populates="cells")


class QuotaShare(SharedLinkToken, db.Model):
    """A link that shows the state of the quotas without signing in, read-only, e.g. for a panel provider
    (app/quota_views.py). It names no person; it can be revoked and may run out. `param`/`value`: only the
    participations that came with this value of a stored URL parameter are counted (the provider's own)."""

    __tablename__ = "quota_share"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column("token", String(64), unique=True)
    token_box: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(100), default="")
    param: Mapped[str] = mapped_column(String(41), default="")
    value: Mapped[str] = mapped_column(String(100), default="")
    show_conds: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime | None]
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Board(db.Model):
    """A dashboard of a survey (app/boards/): tiles that show the results of single questions, the state of
    quotas and figures of the fieldwork, in the look of the survey. `tiles`: [{"key": "item:<id>" |
    "quota:<id>" | "field:<name>", "view", "w" (width in twelfths), "x" (column it begins in, 0 = automatic), "pct", "n", "by" (the question it is split by), "title"}] in the order they are shown.
    `settings`: free for later options (the old "future" is gone, boards.settle_future)."""

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    tiles: Mapped[list | None] = mapped_column(JSON)
    settings: Mapped[dict | None] = mapped_column(JSON)
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    survey: Mapped["Survey"] = relationship(back_populates="boards")
    shares: Mapped[list["BoardShare"]] = relationship(
        back_populates="board", cascade="all, delete-orphan", order_by="BoardShare.id")


class SemVersion(db.Model):
    """A version of the structural equation model of a survey (app/sem_versions.py, model versions 2026-10-26): the
    settings of the model ("settings": method, the model settings of the plan, the hypotheses of the model) with
    their key, and the figures as they were when the version was made ("figures": N, paths, R², measurement,
    redundancy, BIC or fit, PLSpredict; no answers, no participation). number counts per survey; name is optional.
    No foreign key, like live_ink: the rows go in
    code with the survey (lifecycle.delete)."""

    __tablename__ = "sem_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    number: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(80), default="")
    key: Mapped[str] = mapped_column(String(32), default="")
    settings: Mapped[dict | None] = mapped_column(JSON)
    figures: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LiveReaction(db.Model):
    """An emoji sent from a phone to the screen of a live vote (app/live/, concept package G). Kept for a short
    while only: the screen shows the new ones, and older rows go when the next one comes."""

    __tablename__ = "live_reaction"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    emoji: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LiveQuestion(db.Model):
    """A question from the audience of a live vote (Q&A, app/live/, concept package E): anonymous, one run of
    the live vote, voted up by other devices (the votes of a device are kept in its session, never here).
    status: "open", "pending" (waits for the presenter: approval required or a swear word), "answered", "hidden"."""

    __tablename__ = "live_question"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(BigInteger, default=0)
    # the run it was asked in, once that run was merged into another one (live.control._merge_run); None: run_id
    origin_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(String(300))
    votes: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(10), default="open")
    # the slide (question or presentation slide) on the screen when it was asked: the review at the end groups the
    # questions by it (concept konzept-live-feedback-praesentation-2026-10-12.md); None: asked before any slide
    item_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LiveFeedback(db.Model):
    """A tap on "Unklar" on a phone of a live vote (app/live/, concept konzept-live-feedback-praesentation-2026-10-12.md):
    one row per tap, counted per slide and run for the presenter view and the review at the end. Names no device:
    which taps are a phone's own (to take one back) stands in that phone's session only.
    item_id: the slide on the screen at the tap, None before any slide. No foreign key, like ai_access: the rows go in
    code with the survey (lifecycle.delete) and with a run (live.control)."""

    __tablename__ = "live_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    run_id: Mapped[int] = mapped_column(BigInteger, default=0)
    item_id: Mapped[int | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(String(10), default="unclear")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LiveInk(db.Model):
    """What the presenter wrote with a pen on a presentation slide of a live vote (app/live/ink.py, concept
    stiftnotizen-konzept-2026-10-23.md): one row per run and slide. strokes: [{"id", "c" colour, "w" width as a share
    of the slide's width, "m" 1 for the highlighter, "p" [[x, y, pressure], ...] with x and y from 0 to 1 over the
    slide}]; laser: [x, y, unix seconds] of the laser dot, shown on the screen for two seconds, never exported. rev
    counts every change, so the screen fetches the strokes only when they changed. The presenter's own content, no
    data of participants. No foreign key, like live_feedback: the rows go in code with the survey (lifecycle.delete)
    and with a run (live.control)."""

    __tablename__ = "live_ink"
    __table_args__ = (UniqueConstraint("survey_id", "run_id", "item_id", name="uq_live_ink_slide"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    run_id: Mapped[int] = mapped_column(BigInteger, default=0)
    item_id: Mapped[int] = mapped_column()
    strokes: Mapped[list | None] = mapped_column(JSON)
    laser: Mapped[list | None] = mapped_column(JSON)
    rev: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class AiChatAccess(db.Model):
    """The model access of a survey with the element "KI-Chat" (experimental "ai_chat", app/ai_chat.py; concept
    ki-assistent-tagebuch-konzept.md, E4 as decided by Ralf 2026-10-23): the chatbot is itself what the study looks at,
    so its origin is set per survey and recorded, never a researcher's personal access and never the platform's key.
    The key is locked in the participants' box (secrets_box.PARTICIPANT), since their containers make the calls; it is
    never shown again, only its last four characters. From the start on, provider, address and model stay fixed; a new
    key (KI:connect keys run out after 30 days) raises `version`, which every logged message carries. No foreign key,
    like live_ink: the row goes in code with the survey (lifecycle.delete)."""

    __tablename__ = "ai_chat_access"

    survey_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    provider: Mapped[str] = mapped_column(String(20))          # kiconnect | openai | custom
    base_url: Mapped[str] = mapped_column(String(300))
    key_enc: Mapped[str] = mapped_column(Text)                  # encrypted (participants' box)
    key_hint: Mapped[str] = mapped_column(String(8), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[int] = mapped_column(default=1)
    per_day: Mapped[int] = mapped_column(default=500)          # messages of all participants of the survey per day
    valid_until: Mapped[date | None]
    set_by: Mapped[str] = mapped_column(String(300), default="")  # the name of who entered it (kept if the account goes)
    set_at: Mapped[datetime] = mapped_column(default=utcnow)
    history: Mapped[list | None] = mapped_column(JSON)         # earlier versions: [{version, provider, base_url, model, set_by, set_at}]


class AiChatTurn(db.Model):
    """One message of the element "KI-Chat" (app/ai_chat.py): what the participant wrote (role "user") or what the model
    answered ("assistant"), in full, as study data (Ralf 2026-10-23: the original text is kept, the masked one sent to
    the model beside it). status: ok, error, limit, crisis (a hint to help was shown), no_access. No foreign key: the
    rows go in code with their participation (an event on Response), with the survey (lifecycle.delete, retention.purge)
    and orphans with the housekeeping (ai_chat.sweep)."""

    __tablename__ = "ai_chat_turn"
    __table_args__ = (Index("ix_ai_chat_turn_survey_at", "survey_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column()
    response_id: Mapped[int] = mapped_column(index=True)
    item_id: Mapped[int] = mapped_column()
    seq: Mapped[int] = mapped_column(default=0)
    role: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text, default="")
    masked: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="ok")
    model: Mapped[str] = mapped_column(String(120), default="")
    access_version: Mapped[int] = mapped_column(default=0)
    prompt_hash: Mapped[str] = mapped_column(String(16), default="")
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    millis: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class BoardShare(SharedLinkToken, db.Model):
    """A link that shows a dashboard without signing in (/d/<token>), read-only. Like QuotaShare it names no
    person, runs out and can be revoked. `hall`: the lecture hall view, which also shows the QR code and the
    address of the survey. `opens` and `opened_at` count how often the page was opened; nothing about who."""

    __tablename__ = "board_share"

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("board.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column("token", String(64), unique=True)
    token_box: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(100), default="")
    hall: Mapped[bool] = mapped_column(default=False)
    # as QuotaShare: only the participations that came with this value of a stored URL parameter (a panel
    # provider's own); such a link shows the figures of the fieldwork and the quotas, never results of questions
    param: Mapped[str] = mapped_column(String(41), default="")
    value: Mapped[str] = mapped_column(String(100), default="")
    expires_at: Mapped[datetime]
    # "field": the link was made to work until some days after the field end and follows it when it is moved
    # (boards.expiry); empty for links with a fixed end and for those from before this column
    valid: Mapped[str | None] = mapped_column(String(8))
    revoked: Mapped[bool] = mapped_column(default=False)
    opens: Mapped[int] = mapped_column(default=0)
    opened_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    board: Mapped[Board] = relationship(back_populates="shares")


class Block(db.Model):
    """A logical block of items: the unit for survey flow, randomization, and loops.

    Page break items split a block into pages.
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    # {lang: title}; the title is for researchers and optionally shown as page heading
    titles: Mapped[dict] = mapped_column(JSON, default=dict)
    show_title: Mapped[bool] = mapped_column(default=False)
    randomize_items: Mapped[bool] = mapped_column(default=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    survey: Mapped[Survey] = relationship(back_populates="blocks")
    items: Mapped[list["Item"]] = relationship(
        back_populates="block", cascade="all, delete-orphan", order_by="Item.position"
    )

    def pages(self):
        """Items grouped into pages at page breaks (empty pages dropped)."""
        pages, current = [], []
        for item in self.items:
            if item.type == "page_break":
                if current:
                    pages.append(current)
                current = []
            else:
                current.append(item)
        if current:
            pages.append(current)
        return pages

    def title(self, lang=None):
        lang = lang or self.survey.default_language
        return (self.titles or {}).get(lang) or (self.titles or {}).get(self.survey.default_language, "")

    def default_named(self, lang=None):
        """Whether the block still has the name it got when it was made ("Block 3"): that number stays while the
        blocks around it move, so pages that show the block's number leave such a name out."""
        return bool(re.fullmatch(r"Block \d+", self.title(lang).strip()))


_ITEMS = None


def _item_types():
    """app.items (the registry of element types), imported on first use: it imports this module itself. Kept in a
    global so Item.kind and Item.cfg do not run an import statement on every access (performance audit
    2026-10-09b, P1b: 180 000 accesses on the page of quality assurance)."""
    global _ITEMS
    if _ITEMS is None:
        from app import items

        _ITEMS = items
    return _ITEMS


class Item(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("block.id"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    type: Mapped[str] = mapped_column(String(40))
    # Variable name in the export, unique within a survey (e.g. BFI_1, gender)
    code: Mapped[str] = mapped_column(String(64))
    # optional | nudge (highlight missing answers once, then allow skipping) | required
    answer_mode: Mapped[str] = mapped_column(String(10), default="optional", server_default="optional")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    texts: Mapped[dict] = mapped_column(JSON, default=dict)

    block: Mapped[Block] = relationship(back_populates="items")
    assets: Mapped[list["Asset"]] = relationship(back_populates="item", cascade="all, delete-orphan")

    ANSWER_MODES = ("optional", "nudge", "required")

    @staticmethod
    def answer_mode_labels():
        """Name and description of each answer mode: the same in the element's form and in the editor's badge."""
        from app.lang import de_en

        return {"optional": (de_en("Freiwillig", "Optional"), de_en("Kann übersprungen werden", "Can be skipped")),
                "nudge": (de_en("Nachfragen", "Request"),
                          de_en("Fehlt eine Antwort, wird die Frage einmal markiert; ein zweiter Klick auf „Weiter“ geht "
                                "ohne Antwort weiter.",
                                "If an answer is missing, the question is marked once; a second click on “Next” continues "
                                "without an answer.")),
                "required": (de_en("Pflicht", "Required"),
                             de_en("Ohne Antwort geht es nicht weiter", "No moving on without an answer"))}

    @property
    def required(self):
        return self.answer_mode == "required"

    @property
    def nudge(self):
        return self.answer_mode == "nudge"

    @property
    def survey(self):
        return self.block.survey

    @property
    def kind(self):
        return _item_types().get(self.type)

    @property
    def cfg(self):
        """The config for reading (templates, analysis): what the item stores, over the defaults of its kind for the
        settings it lacks (a row from before a setting existed). Only for reading: a change goes to `config`.

        The copies of the missing defaults are made once per object and type (performance audit 2026-10-09b, P1b:
        quality assurance asked 67 000 times and made a deep copy each time). The stored settings are read anew on
        every call, so a new `config` or one changed in place is always what comes back; only the defaults it lacks
        are shared between the calls of one object."""
        config = self.config or {}
        defaults = _item_types().stable_defaults(self.type)
        missing = [k for k in defaults if k not in config]
        if not missing:
            return config
        memo = self.__dict__.get("_cfg_defaults")
        if memo is None or memo[0] != self.type:
            memo = (self.type, {})
            self.__dict__["_cfg_defaults"] = memo
        copies = memo[1]
        for k in missing:
            if k not in copies:
                copies[k] = copy.deepcopy(defaults[k])
        return {**{k: copies[k] for k in missing}, **config}

    def resolved(self, lang=None):
        """(texts, origin) in `lang`: the texts as shown and the language each one is written in (app/item_texts.py).

        Kept on the object while its stored texts stay the same objects (N-2 of the audit 2026-10-09): templates ask
        for one text per option, and each call resolved the whole tree again (editor of a list of 3000 options:
        2.5 s). A new `texts` (every change assigns one; also loading it again after a commit) or a new dict of a
        language resolves anew. The result is shared between the calls: read it, never change it."""
        from app import item_texts

        default, texts = self.block.survey.default_language, self.texts
        langs = tuple((texts or {}).values()) if isinstance(texts, dict) else ()
        memo = getattr(self, "_resolved_memo", None)
        if memo is None or memo[0] is not texts or len(memo[1]) != len(langs) \
                or any(a is not b for a, b in zip(memo[1], langs)):
            memo = (texts, langs, {})
            self._resolved_memo = memo
        key = (self.type, default, lang or default)
        if key not in memo[2]:
            memo[2][key] = item_texts.resolve(self.kind, texts, default, lang)
        return memo[2][key]

    def t(self, lang=None):
        """All texts for a language: its own texts; a text it lacks falls back to the default language, a standard
        wording (buttons of the consent, federal states, "Keine davon", ...) comes in this language (S9, W44)."""
        return self.resolved(lang)[0]

    def lang_texts(self, lang=None):
        """The texts written in `lang` (own and standard wordings), without fallbacks to the default language: the
        fields of the editor in that language."""
        from app import item_texts

        lang = lang or self.block.survey.default_language
        texts, origin = self.resolved(lang)
        return item_texts.only(texts, origin, lang)

    def text(self, key, lang=None):
        return self.t(lang).get(key, "")

    def text_lang(self, key, lang=None, *path):
        """The language a shown text is written in: `lang` when it has its own text or a standard wording of its
        own, else the default language it falls back to (W45: such a text gets its own lang and dir). `path`: an
        entry of a group of texts, e.g. text_lang("options", "ar", "a1b2")."""
        from app import item_texts

        lang = lang or self.block.survey.default_language
        return item_texts.origin_of(self.resolved(lang)[1], (key, *path), lang)


class Asset(db.Model):
    """A file used by an item: an uploaded image or a generated one.

    `key` identifies the combination of variables the asset belongs to
    (canonical JSON, "" for items without variables). Generated assets must be
    approved by a researcher before a survey can start.
    """

    PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id"), index=True)
    key: Mapped[str] = mapped_column(String(500), default="")
    filename: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(10), default="upload")  # upload | ai
    prompt: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(10), default=APPROVED)
    style: Mapped[str] = mapped_column(String(40), default="", server_default="")  # image_styles.style_id at generation
    rights: Mapped[dict | None] = mapped_column(JSON)  # documented image rights; explicit public-archive confirmation
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    item: Mapped[Item] = relationship(back_populates="assets")


def _random_id():
    return secrets.token_hex(16)


class ContactEntry(db.Model):
    """E-mail address and/or comment left after the questionnaire, deliberately unlinkable:
    no reference to a response, no timestamp, a random key, and a table without rowid, so
    neither columns nor storage order reveal which participant it belongs to."""

    __table_args__ = {"sqlite_with_rowid": False}

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_random_id)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), default="")  # legacy contact form
    comment: Mapped[str] = mapped_column(Text, default="")        # legacy contact form
    answers: Mapped[dict | None] = mapped_column(JSON)            # answers of the separated section


class SplitSession(db.Model):
    """A participant's way through the separated section (after the main part was finalized).

    Holds no reference to the response and no timestamps; its random id lives only in the
    participant's session cookie. When the section ends, the answers go into a ContactEntry
    and this row is deleted. It quacks like a Response for runtime.step.
    """

    __table_args__ = {"sqlite_with_rowid": False}
    ACTIVE, COMPLETED = "active", "completed"
    is_preview = True  # never counted, never balances anything
    completed_at = None
    last_active_at = None

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_random_id)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id"), index=True)
    language: Mapped[str] = mapped_column(String(8), default="de")
    layout: Mapped[dict | None] = mapped_column(JSON)
    answers: Mapped[dict | None] = mapped_column(JSON)
    position: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(10), default="active")
    # counts the changes of the row, as Response.version: of two requests that overlap (the last page sent twice) the
    # one that stores second is refused (StaleDataError), so the answers become one contact entry, not two (code
    # review 2026-10-15, F5)
    version: Mapped[int] = mapped_column(default=1, server_default="1")

    __mapper_args__ = {"version_id_col": version}


class CoscineConnection(db.Model):
    """A researcher's personal Coscine API token (app/coscine.py), stored encrypted: Coscine offers no
    OAuth for other applications (coscine-konzept.md, C1). Deleted with "trennen" and with the account."""

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), unique=True)
    token: Mapped[str] = mapped_column(Text)  # encrypted
    name: Mapped[str] = mapped_column(String(300), default="")  # the name Coscine knows the person by
    expires_at: Mapped[datetime | None]  # read from the token
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AiAccess(db.Model):
    """A researcher's own AI access (experimental "own_ai_key", app/ai_access.py): provider, base URL, the key
    (encrypted with app/secrets_box) and the chosen text model. One row per person; deleted with "Entfernen" and with
    the account. The key is never shown again, only its last four characters."""

    __tablename__ = "ai_access"

    # no foreign key, like group_join_link: the row goes in code with the account (accounts.delete)
    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    provider: Mapped[str] = mapped_column(String(20))          # kiconnect | openai | custom
    base_url: Mapped[str] = mapped_column(String(300))
    key_enc: Mapped[str] = mapped_column(Text)                  # encrypted
    key_hint: Mapped[str] = mapped_column(String(8), default="")
    text_model: Mapped[str] = mapped_column(String(120), default="")
    models: Mapped[list | None] = mapped_column(JSON)            # the ids /models listed at the last save or check
    valid_until: Mapped[date | None]
    checked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(40))  # a short code: 401, 403, 404, 429, unreachable …
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ZenodoConnection(db.Model):
    """A researcher's OAuth connection to Zenodo (or its sandbox). Tokens are stored encrypted."""

    __table_args__ = (UniqueConstraint("user_id", "target"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    target: Mapped[str] = mapped_column(String(10))  # sandbox | zenodo
    access_token: Mapped[str] = mapped_column(Text)   # encrypted
    refresh_token: Mapped[str] = mapped_column(Text, default="")  # encrypted
    expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


construct_tag = db.Table(
    "construct_tag",
    db.Column("construct_id", ForeignKey("construct.id"), primary_key=True),
    db.Column("tag_id", ForeignKey("tag.id"), primary_key=True),
)


class Tag(db.Model):
    """A classification of constructs within a facet, e.g. art:einstellung, thema:technik, fach:psychologie."""

    id: Mapped[int] = mapped_column(primary_key=True)
    facet: Mapped[str] = mapped_column(String(20), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)  # "<facet>:<name>"
    name: Mapped[str] = mapped_column(String(80))


class Construct(db.Model):
    """A validated instrument from the construct toolbox.

    `spec` holds the item template ({"type", "code", "config", "texts": {lang: …}}); inserting a
    construct copies it into an ordinary item. Seed constructs are maintained in app/constructs.py
    and updated when their seed_version increases; surveys keep the copy they were built from."""

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    short: Mapped[str] = mapped_column(String(300), default="")

    # the organisation it belongs to (app/orgs.py, konzept-abteilungen-2026-10-15.md): a fence, not an access; None =
    # the organisation everything started in. No foreign key: the column is added to a table with triggers without
    # copying it, and an organisation is only deleted once nothing belongs to it (migration 0071)
    org_id: Mapped[int | None] = mapped_column(index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[str] = mapped_column(String(300), default="")
    year: Mapped[int | None]
    citation: Mapped[str] = mapped_column(Text, default="")
    doi: Mapped[str] = mapped_column(String(200), default="")
    scoring: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="review")  # review | verified
    visibility: Mapped[str] = mapped_column(String(20), default="public")  # public | private (own constructs, later)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    seed_version: Mapped[int] = mapped_column(default=0)
    search_text: Mapped[str] = mapped_column(Text, default="")  # flattened text for the LIKE fallback
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    tags: Mapped[list[Tag]] = relationship(secondary=construct_tag, order_by="Tag.slug")
    # linked elements only (app/linked.py): the groups it is shared with
    group_shares: Mapped[list["ConstructGroupShare"]] = relationship(cascade="all, delete-orphan")
    person_shares: Mapped[list["ConstructShare"]] = relationship(cascade="all, delete-orphan")

    @property
    def languages(self):
        return list((self.spec or {}).get("texts", {}))

    @property
    def n_items(self):
        config = (self.spec or {}).get("config", {})
        return len(config.get("statements") or config.get("pairs") or []) or 1

    @property
    def points(self):
        return (self.spec or {}).get("config", {}).get("points")

    @property
    def author_year(self):
        return f"{self.authors} ({self.year})" if self.year else self.authors


class ConstructSubmission(db.Model):
    """A linked element (app/linked.py) submitted for the construct toolbox (app/submissions.py). The operator asks
    back, rejects with a reason or accepts: accepting makes a new toolbox construct (`toolbox_key`) as a copy; the
    linked element and its uses stay as they are. `fields` holds what the submitter gave (description, citation,
    doi, scoring, reverse, answer to a question); `seen` is False while the submitter has not seen the latest
    decision."""

    __tablename__ = "construct_submission"
    SUBMITTED, QUESTION, ACCEPTED, REJECTED = "submitted", "question", "accepted", "rejected"
    OPEN = (SUBMITTED, QUESTION)

    id: Mapped[int] = mapped_column(primary_key=True)
    construct_id: Mapped[int] = mapped_column(ForeignKey("construct.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    state: Mapped[str] = mapped_column(String(12), default="submitted", index=True)
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")  # the operator's question or reason
    toolbox_key: Mapped[str] = mapped_column(String(60), default="")
    seen: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_at: Mapped[datetime | None]

    construct: Mapped["Construct"] = relationship()
    user: Mapped[User | None] = relationship()


class ConstructCitation(db.Model):
    """A publication that used a toolbox construct (app/citations.py): an article or a data set, by DOI, with the
    metadata doi.org gave when it was entered. Entered by researchers for their survey (`survey_id`; it then counts
    for every toolbox construct in it) or by the operator at the construct."""

    __tablename__ = "construct_citation"
    __table_args__ = (UniqueConstraint("construct_id", "doi"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    construct_id: Mapped[int] = mapped_column(ForeignKey("construct.id", ondelete="CASCADE"), index=True)
    doi: Mapped[str] = mapped_column(String(200))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # title, authors [[family, given]], year, journal, kind
    survey_id: Mapped[int | None] = mapped_column(ForeignKey("survey.id", ondelete="SET NULL"), index=True)
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ConstructPick(db.Model):
    """A construct starred by a researcher as a favourite: favourites are the constructs the questionnaire
    editor lists without a search (constructs.sidebar). `used_at` (last inserted) is no longer written or read:
    the list is filled up by how often a construct occurs in the surveys (constructs.counts)."""

    __tablename__ = "construct_pick"
    __table_args__ = (UniqueConstraint("user_id", "construct_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    construct_id: Mapped[int] = mapped_column(ForeignKey("construct.id", ondelete="CASCADE"), index=True)
    starred: Mapped[bool] = mapped_column(default=False)
    used_at: Mapped[datetime | None]


class ConstructShortlist(db.Model):
    """One entry of a curated "Schnellbox" of the questionnaire editor (app/shortlist.py, konzept-konstrukt-schnellbox-
    2026-10-16.md, K1): the list of an organisation, kept by its admins, or with org_id empty the platform's list, kept
    by site admins, which holds for every organisation without a list of its own. By construct key (a toolbox construct
    taken out of the toolbox is simply left out), in the order of `position`."""

    __tablename__ = "construct_shortlist"

    id: Mapped[int] = mapped_column(primary_key=True)
    # no database key: SQLite's table copies of "organisation" would leave it pointing at a temporary table; orgs.delete
    # takes the rows along (as agent_token, migration 0065)
    org_id: Mapped[int | None] = mapped_column(index=True)
    key: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TrashedItem(db.Model):
    """A deleted question, kept for TRASH_DAYS so it can be restored (see app/trash.py).

    `data` holds the item as it was (type, code, answer_mode, config, texts, block_id,
    position, block title and its assets); the asset files stay on disk until purged."""

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime] = mapped_column(default=utcnow)

    survey: Mapped[Survey] = relationship(back_populates="trash")


class Feedback(db.Model):
    """A bug report or suggestion from a researcher (bug icon in the header), or a complaint about a survey from
    anyone who opens it (link on its privacy page): severity COMPLAINT, `tag` the kind, listed first."""

    SEVERITIES = {
        1: ("Anregung", "suggestion"),
        2: ("Störend", "annoying problem"),
        3: ("Fehler", "error"),
        4: ("Beschwerde", "complaint"),
    }
    COMPLAINT = 4

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    severity: Mapped[int] = mapped_column(default=1)
    message: Mapped[str] = mapped_column(Text)
    page: Mapped[str] = mapped_column(String(500), default="")  # where the report was written
    status: Mapped[str] = mapped_column(String(10), default="new", index=True)  # new | done
    tag: Mapped[str] = mapped_column(String(40), default="", server_default="")  # e.g. SCALE_TAG
    # complaints: the survey they are about and how to reach the sender (optional)
    survey_id: Mapped[int | None] = mapped_column(ForeignKey("survey.id", ondelete="SET NULL"), index=True)
    contact: Mapped[str] = mapped_column(String(200), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User | None] = relationship()
    survey: Mapped[Survey | None] = relationship()

    SCALE_TAG = "Scale suggestion"


class BlogPost(db.Model):
    """An entry of the public news page /blog (app/blog.py; blog-konzept.md, Ralf 2026-10-10 "wie empfohlen"). Site
    admins write, publish, withdraw and delete entries. German is required to publish, English may stay empty (the
    page then shows the German text with a note). `brands`: the keys of the brands it appears under (brand.site(),
    "askurious" for the main name); empty = all. Published entries are public; a draft only for admins."""

    DRAFT, PUBLISHED = "draft", "published"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)
    published_at: Mapped[datetime | None] = mapped_column(index=True)  # set at the first "Veröffentlichen", changeable
    # the last change of title, summary or text after the first publication ("aktualisiert am"); None before
    content_changed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    # no foreign keys, like data_token: accounts.delete empties them in code, and a process of the code before, still
    # running during a rolling deploy, deletes an account without a key it does not know standing in its way
    created_by_id: Mapped[int | None]
    updated_by_id: Mapped[int | None]
    category: Mapped[str] = mapped_column(String(20), default="feature")  # key of blog.CATEGORIES
    brands: Mapped[list | None] = mapped_column(JSON)
    title_de: Mapped[str] = mapped_column(String(200), default="")
    title_en: Mapped[str] = mapped_column(String(200), default="")
    summary_de: Mapped[str] = mapped_column(Text, default="")
    summary_en: Mapped[str] = mapped_column(Text, default="")
    body_de: Mapped[str] = mapped_column(Text, default="")
    body_en: Mapped[str] = mapped_column(Text, default="")
    cover_image_id: Mapped[int | None]  # a BlogImage of this entry; no key, the image goes with its entry

    updated_by: Mapped[User | None] = relationship(primaryjoin="foreign(BlogPost.updated_by_id) == User.id", viewonly=True)
    images: Mapped[list["BlogImage"]] = relationship(primaryjoin="BlogPost.id == foreign(BlogImage.post_id)",
                                                     back_populates="post", cascade="all, delete-orphan",
                                                     order_by="BlogImage.id")
    old_slugs: Mapped[list["BlogSlug"]] = relationship(primaryjoin="BlogPost.id == foreign(BlogSlug.post_id)",
                                                       cascade="all, delete-orphan")


class BlogImage(db.Model):
    """An image of a blog entry, written anew as WebP (app/blog.py store_image); the file lies under
    UPLOAD_FOLDER/../blog/<post id>/<name>. In the text it stands as {{bild<id>}}; its alternative text is required."""

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(index=True)  # no key: the images go with their entry in code (cascade)
    filename: Mapped[str] = mapped_column(String(40))
    width: Mapped[int] = mapped_column(default=0)
    height: Mapped[int] = mapped_column(default=0)
    alt_de: Mapped[str] = mapped_column(String(300), default="")
    alt_en: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    post: Mapped[BlogPost] = relationship(primaryjoin="BlogPost.id == foreign(BlogImage.post_id)", back_populates="images")


class BlogSlug(db.Model):
    """An earlier address of a published blog entry: /blog/<old> leads to the entry (301)."""

    slug: Mapped[str] = mapped_column(String(100), primary_key=True)
    post_id: Mapped[int] = mapped_column(index=True)


class SurveyLabel(db.Model):
    """A chosen name for the participant link (/s/<label>). Labels are never released: once used, a
    label is taken for good and keeps leading to its survey, also after a newer label was set."""

    label: Mapped[str] = mapped_column(String(40), primary_key=True)  # lower case
    survey_id: Mapped[int | None] = mapped_column(ForeignKey("survey.id", ondelete="SET NULL"), index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PreviewComment(db.Model):
    """A comment left in the preview (editor or phone link) on an item or a whole page. Researchers mark
    comments as resolved or ignored; the list with its handling is part of the export (comments.csv)."""

    OPEN, RESOLVED, IGNORED = "open", "resolved", "ignored"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    block_id: Mapped[int | None]
    item_id: Mapped[int | None] = mapped_column(index=True)  # None = the whole page
    target: Mapped[str] = mapped_column(String(300), default="")  # block and item as shown then (kept if deleted)
    page: Mapped[int | None]
    qversion: Mapped[int] = mapped_column(default=1)
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(120), default="")
    # the account that wrote it in the editor preview, and the one that handled it: account deletion blanks the
    # names by these (app/accounts.py), whatever the name is by then; None on the phone link and in older rows
    author_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    via: Mapped[str] = mapped_column(String(10), default="editor")  # editor | phone
    status: Mapped[str] = mapped_column(String(10), default=OPEN, index=True)
    handled_by: Mapped[str] = mapped_column(String(120), default="")
    handled_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    handled_at: Mapped[datetime | None]
    note: Mapped[str] = mapped_column(Text, default="")
    # an image of the page (app/comment_shots.py): the file name in instance/comment-shots/<survey uid>/, None = none
    shot: Mapped[str | None] = mapped_column(String(40))
    shot_w: Mapped[int | None]
    shot_h: Mapped[int | None]
    # where it was written: {"device", "os", "browser", "size", "lang", "scheme"}, all short labels (comment_shots.env)
    env: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TranslationMark(db.Model):
    """What one translated text field is based on (app/translations.py): the text of the default language at the
    time it was translated or confirmed, and whether the AI translated it and nobody has confirmed it yet.

    The translation itself stays where it always is (item.texts[lang] and the like); this row only allows to see
    that the source has changed since (and how), without a history. `unit` is "item:12", "block:3", "end",
    "contact", "flow:<node id>" or "loop:<block id>"; `path` the place of the text inside it ("options.a1b2")."""

    __table_args__ = (UniqueConstraint("survey_id", "unit", "lang", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    unit: Mapped[str] = mapped_column(String(60))
    lang: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(Text, default="")
    ai: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class SurveyPin(db.Model):
    """A survey pinned (starred) by a researcher: it stands at the top of their list on the start page."""

    __table_args__ = (UniqueConstraint("user_id", "survey_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SurveyHide(db.Model):
    """A survey a researcher took out of their own list ("Für mich ausblenden"): it stands under "Ausgeblendet" on
    the start page. Only for that person; no right changes, and the survey's admins cannot hide their own."""

    __table_args__ = (UniqueConstraint("user_id", "survey_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # no foreign keys, like ai_access: the rows go in code with the account (accounts.delete) and with the survey
    # (lifecycle.delete)
    user_id: Mapped[int] = mapped_column(index=True)
    survey_id: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AiCall(db.Model):
    """One AI request, for the admin page "KI-Nutzung aller Konten" (app/ai_stats.py) and the person's own page "KI-Nutzung"
    (app/ai_access.py): purpose, item type, model and the usage the provider reports (tokens; images and size), and
    who clicked (user_id, ki-nutzung-seite-konzept.md E6; emptied when the account is deleted). No survey, no prompt,
    no content."""

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(10))           # text | image
    purpose: Mapped[str] = mapped_column(String(30))        # see ai_stats.PURPOSES
    item_type: Mapped[str | None] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    images: Mapped[int] = mapped_column(default=0)
    size: Mapped[str | None] = mapped_column(String(20))
    ms: Mapped[int | None]                                   # duration of the request
    ok: Mapped[bool] = mapped_column(default=True)
    # whose access: None or "platform" (the installation's key), "own" (a researcher's own key, app/ai_access.py;
    # no cost on the admin page); provider of an own key: kiconnect | openai | custom
    source: Mapped[str | None] = mapped_column(String(10))
    provider: Mapped[str | None] = mapped_column(String(20))
    # the person who clicked; no foreign key, like ai_access: accounts.delete empties it (migration 0042)
    user_id: Mapped[int | None] = mapped_column(index=True)


class AiUsage(db.Model):
    """AI calls of a researcher per day and kind ("text", "image"), for the daily limits (app/limits.py)."""

    __table_args__ = (UniqueConstraint("user_id", "day", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10))  # local date, YYYY-MM-DD
    kind: Mapped[str] = mapped_column(String(10))
    count: Mapped[int] = mapped_column(default=0)


class RateHit(db.Model):
    """One counted request of a rate limit that has to hold across all server processes and containers
    (app/limits.py, _take_shared; security audit 2026-10-13, TN-2/DP-3): tries of deletion codes, survey and diary codes,
    sign-ins with a password, "Passwort vergessen?", /verify, read tokens, secrets of the diary app, invitations and
    join links. key: a keyed digest of the sender (IPv4 address or IPv6 /64, salted per day) or of the sender and the
    account, never the address itself. at: seconds since 1970 (time.time()). Rows older than the longest window go
    every minute (limits.sweep, lifecycle.close_due_surveys)."""

    __tablename__ = "rate_hit"
    __table_args__ = (Index("ix_rate_hit_kind_key_at", "kind", "key", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(32))
    at: Mapped[float] = mapped_column(Float, index=True)


class RateCount(db.Model):
    """How many requests of a limit against load came from one sender in a short time slot, for all server processes
    and containers together (app/limits.py, _take_counted; audit 2026-10-18, BIZ-01): new participations (START_LIMIT,
    SURVEY_SENDER_LIMIT), live votes, complaints and their mails of receipt. Before, each of the 24 participants'
    processes counted in its own memory, so the limits were up to 24 times as wide. One row per kind, sender and slot
    (a tenth of the window), raised by one statement, so a request costs one indexed sum and one upsert. key: a keyed
    digest of the sender (IPv4 address or IPv6 /64, with a key that changes every day), of the sender and the survey or
    of a recipient's address, never the address itself. slot: the slot's start in seconds since 1970. Rows go once the
    longest window is over (limits.sweep, every minute; at most a day)."""

    __tablename__ = "rate_count"

    kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    slot: Mapped[int] = mapped_column(primary_key=True, index=True)
    n: Mapped[int] = mapped_column(default=1)


class EditMark(db.Model):
    """Who has a part of a survey open in an editor and who saved it last (see app/edit_guard.py).

    One row per person and part ("item:12", "loop:ab3", "settings:meta"). `seen_at` is refreshed while the
    editor is open, `saved_at` on every save; both are only shown to the other members."""

    __table_args__ = (UniqueConstraint("survey_id", "key", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    seen_at: Mapped[datetime | None]
    saved_at: Mapped[datetime | None]

    user: Mapped[User] = relationship()


class AccessLog(db.Model):
    """One entry of the access log (app/audit.py): who did what with whose data, and when. Person and survey are
    kept as text as well, so that an entry stays readable once the account or the survey is gone; entries are
    removed after audit.KEEP_DAYS days."""

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(index=True)  # no foreign key: the entry outlives the account
    who: Mapped[str] = mapped_column(String(220), default="")
    survey_id: Mapped[int | None] = mapped_column(index=True)  # no foreign key: the entry outlives the survey
    survey_code: Mapped[str] = mapped_column(String(20), default="", index=True)
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[int] = mapped_column(default=200)


class EditStep(db.Model):
    """One change to a draft questionnaire that its author can take back and redo (see app/undo.py).

    `data["parts"]` holds, per changed part ("item:12", "order:3", "block:3", "flow"), its state before and
    after the change; None means the part did not exist. `undone` steps wait for "Wiederholen". `need`: the
    role taking it back or redoing it needs ("admin" for a step with settings only admins change, else
    "editor"; empty for steps from before this column, undo.need reads it from `data` then)."""

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    label: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    undone: Mapped[bool] = mapped_column(default=False)
    need: Mapped[str | None] = mapped_column(String(10), nullable=True)

    user: Mapped[User] = relationship()


class IdFloor(db.Model):
    """The highest id a deleted survey, block, question, quota, group, dashboard or participation ever had, per
    table. SQLite gives the id of a deleted last row to the next one; answers, layouts and stored files are kept
    under these ids, so a new question would show the answers to a deleted one. `_keep_ids_apart` below never gives
    an id at or below this floor again."""

    __tablename__ = "id_floor"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)  # table name
    highest: Mapped[int] = mapped_column(default=0)


class Enrolment(db.Model):
    """One person in a diary study (app/diary.py, concepts/experiencesampling.md): the participation of that kind. It
    holds no name, address or device id: the app keeps a random secret and the row only its hash, so two studies on
    one phone cannot be linked. Its entries are Response rows with enrolment_id; they go with it."""

    PENDING, ACTIVE, LEFT, ENDED = "pending", "active", "left", "ended"  # "finished" follows from last_day
    PAUSED = "paused"  # paused by the researchers (app/diary.py, pause); settings["pauses"] holds the days

    id: Mapped[int] = mapped_column(primary_key=True)
    # no foreign key (as survey_invite): the rows go with the survey by hand (lifecycle.delete, retention)
    survey_id: Mapped[int] = mapped_column(index=True)
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # the pseudonymous id of the person in the export, like Response.pid
    pid: Mapped[str] = mapped_column(String(40), unique=True)
    source: Mapped[str] = mapped_column(String(10), default="real", server_default="real")  # real | test | dummy
    status: Mapped[str] = mapped_column(String(10), default=PENDING, server_default="pending")
    language: Mapped[str] = mapped_column(String(8), default="de")
    tz: Mapped[str] = mapped_column(String(64), default="Europe/Berlin", server_default="Europe/Berlin")
    # the study period, in the person's local dates: set when the entry part is completed
    first_day: Mapped[date | None]
    last_day: Mapped[date | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_sync_at: Mapped[datetime | None]
    # what the person set in the app: {"quiet": [{"from": "YYYY-MM-DD", "q": ["22:00", "07:00"] | None}]}, the quiet
    # hours for the random prompts, each from the day after it was set (app/diary.py, set_quiet); "tz": [{"from":
    # "YYYY-MM-DD" | None, "tz": name}], the time zones of the phone, each from the day after the change (set_tz);
    # "offline_at": when the phone last fetched the pages to answer without a connection; "left_at": when the
    # person left the study in the app
    settings: Mapped[dict | None] = mapped_column(JSON)


class DiaryInvite(db.Model):
    """A personal invitation code of a diary study (app/diary.py): each joins one person. A row of its own, so that
    two people joining with one code at the same moment cannot both use it (used_at is set by one UPDATE that only
    one of them wins) and no join overwrites the schedule (audit 2026-10-11, N4; before: a list in survey.sampling)."""

    __tablename__ = "diary_invite"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column()  # no foreign key, like enrolment: the rows go with the survey by hand
    code: Mapped[str] = mapped_column(String(8))
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # the person who joined with the code, so that the code can connect a phone to that participation again
    # (diary.rejoin; no foreign key, like survey_id); empty for codes used before 2026-10-13
    enrolment_id: Mapped[int | None] = mapped_column()
    # when the researchers released the code to connect once more (rejoin "release"); empty again once used
    released_at: Mapped[datetime | None]

    __table_args__ = (UniqueConstraint("survey_id", "code"),)


class ParticipantHandover(db.Model):
    """What the main address hands over to the participants' address for one browser (app/hosts.py, code review
    2026-10-08 H1/H2): only the random `id` goes into the address, the content stays here. `payload` holds the keys
    of a participation under way, the right to see a draft and the mark "took part"; a handover is taken once
    (`used`, payload emptied). `fingerprint`: the participation's keys hashed, one row each, so that a copied cookie
    of the main address never moves the same participation a second time; kept for a session's lifetime."""

    __tablename__ = "participant_handover"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    survey_id: Mapped[int] = mapped_column()  # no foreign key: the row lives two minutes, or a week as a fingerprint
    fingerprint: Mapped[str | None] = mapped_column(String(64), unique=True)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    used: Mapped[bool] = mapped_column(default=False)


class ResearchProject(db.Model):
    """A research project in AInstein (app/ainstein.py), before there is a survey: a researcher develops a research
    question with the AI along a canvas, derives hypotheses and constructs and turns them into a survey.
    `canvas`: {box key: {"text": what the researcher wrote, "proposal": the AI's last suggestion not yet taken,
    "before": the text the AI last replaced}}.
    `chat`: [{"role": "user" | "ai", "text", "at"}]. `plan`: {"intervention": what happens between the measurements,
    "hypotheses": [{"text", "kind", "constructs"}], "constructs": [{"name", "role", "definition", "measure", "spec",
    "when", "n"}]} (measure: a toolbox key, "agreed" (built from `spec`, as agreed in the conversation), "ai" or "";
    when: "before", "after" or "both")."""

    id: Mapped[int] = mapped_column(primary_key=True)
    # the address of the project (/ainstein/<code>): 8 random hex digits, so projects cannot be counted through
    code: Mapped[str] = mapped_column(String(8), unique=True, default=lambda: secrets.token_hex(4))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)

    # the organisation it belongs to (app/orgs.py, konzept-abteilungen-2026-10-15.md): a fence, not an access; None =
    # the organisation everything started in. No foreign key: the column is added to a table with triggers without
    # copying it, and an organisation is only deleted once nothing belongs to it (migration 0071)
    org_id: Mapped[int | None] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    idea: Mapped[str] = mapped_column(Text, default="")
    canvas: Mapped[dict | None] = mapped_column(JSON)
    chat: Mapped[list | None] = mapped_column(JSON)
    plan: Mapped[dict | None] = mapped_column(JSON)
    # notes on the earlier conversation for the model, every 25 messages: {"text", "upto" (messages covered), "at"}
    memory: Mapped[dict | None] = mapped_column(JSON)
    # who else may open it: [{"kind": "person", "orcid", "name", "role"} | {"kind": "group", "group_id", "name",
    # "role"}], role "view" (read and comment) or "edit" (also talk with AInstein and change canvas and plan)
    shares: Mapped[list | None] = mapped_column(JSON)
    # comments of the people it is shared with (and its owner): [{"id", "orcid", "name", "text", "box", "at"}]
    comments: Mapped[list | None] = mapped_column(JSON)
    survey_id: Mapped[int | None] = mapped_column(ForeignKey("survey.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


def _created_host():
    """The domain a new account or survey is made under: the brand's (BRANDS, SITE_BRAND; app/brand.py) or, in a
    request to the main name, PUBLIC_BASE_URL's. None outside a request without SITE_BRAND (a script, a test)."""
    from flask import current_app, has_app_context, has_request_context

    from app import brand
    from config import url_host

    if not has_app_context():
        return None
    found = brand.domain()
    if found:
        return found
    return (url_host(current_app.config["PUBLIC_BASE_URL"]) or None) if has_request_context() else None


def _remember_host(mapper, connection, target):
    if target.created_host is None:
        target.created_host = _created_host()


# Tables whose floor is kept on SQLite only: a participation is deleted on the participants' side too (declined),
# whose database role has no right on id_floor, and the sequences of PostgreSQL never go back anyway
_FLOOR_ON_SQLITE_ONLY = {"response", "enrolment"}


def _raise_floor(connection, name, highest):
    """Lift the floor of table `name` to `highest` (never lower it)."""
    floors = IdFloor.__table__
    # marked for the recorder of the participants' SQL (tests/helpers/participant_sql.py): never sent on PostgreSQL
    only = {"sqlite_only": name in _FLOOR_ON_SQLITE_ONLY}
    floor = connection.scalar(db.select(floors.c.highest).where(floors.c.name == name).execution_options(**only))
    if floor is None:
        connection.execute(floors.insert().values(name=name, highest=highest).execution_options(**only))
    elif floor < highest:
        connection.execute(floors.update().where(floors.c.name == name).values(highest=highest).execution_options(**only))


def keep_floor_before_bulk_delete(model, *where):
    """Before a DELETE in one SQL statement (past the mapper events, which `_keep_ids_apart` listens to): lift the floor
    to the highest id the statement deletes, in the same transaction (audit 2026-10-21, B1)."""
    table = model.__table__
    connection = db.session.connection()
    if table.name in _FLOOR_ON_SQLITE_ONLY and connection.dialect.name != "sqlite":
        return
    highest = connection.scalar(db.select(db.func.max(table.c.id)).where(*where))
    if highest:
        _raise_floor(connection, table.name, highest)


def _keep_ids_apart(model):
    from sqlalchemy import event

    table, floors = model.__table__, IdFloor.__table__

    @event.listens_for(model, "after_delete")
    def _remember(mapper, connection, target):
        if table.name in _FLOOR_ON_SQLITE_ONLY and connection.dialect.name != "sqlite":
            return
        _raise_floor(connection, table.name, target.id)

    @event.listens_for(model, "before_insert")
    def _above_floor(mapper, connection, target):
        if target.id is not None or connection.dialect.name != "sqlite":
            return  # sequences of other databases never go back
        def above():
            floor = connection.scalar(db.select(floors.c.highest).where(floors.c.name == table.name)
                                      .execution_options(sqlite_only=True)) or 0
            highest = connection.scalar(db.select(db.func.max(table.c.id)).execution_options(sqlite_only=True)) or 0
            return floor, floor < highest

        floor, by_itself = above()
        if by_itself:
            return  # the database counts on above the floor by itself
        # An id of our own: take the write lock first (a write that changes nothing, as quotas.recount does), so that
        # two processes inserting at the same moment do not both take floor + 1; then read again, as the other one may
        # have stored its row meanwhile (participations are stored by many at once; audit 2026-10-21, B1)
        connection.execute(floors.update().where(floors.c.name == table.name).values(highest=floors.c.highest)
                           .execution_options(sqlite_only=True))
        floor, by_itself = above()
        if by_itself:
            return
        given = connection.info.setdefault("ids_given", {})  # several new rows in one flush
        target.id = given[table.name] = max(floor, given.get(table.name, 0)) + 1


# Quotas too: calculations and ends in the flow name them by id (undo can put such an element back after its
# quota was deleted), and participations keep their place by the id of the cell. Groups: a research project
# (AInstein) names a group it is shared with by id; dashboards: undo puts a deleted one back under its id
# (code review 2026-10-07, K1/E9). Participations: paper sheets, seal chains, saved states, the pseudonyms of the
# construct pool and older exports name them by id; SQLite gave the id of a deleted last one to a new participation
# in another survey (audit 2026-10-21, B1). Their bulk deletes call keep_floor_before_bulk_delete. The persons of a
# diary study: a used invitation code names its person by id, and SQLite gave the id of a deleted last person to the
# next one, whom the old code then connected (audit 2026-10-23, S-01)
for _model in (Survey, Block, Item, Quota, QuotaCell, WorkGroup, Board, Response, Enrolment):
    _keep_ids_apart(_model)


def _detach_invites():
    from sqlalchemy import event

    @event.listens_for(Enrolment, "before_delete")
    def _let_go(_mapper, connection, person):
        """A person who goes lets go of the invitation code they joined with, in the same transaction: the code never
        names anyone again, so no release can connect another person with it (audit 2026-10-23, S-01)."""
        connection.execute(DiaryInvite.__table__.update().where(DiaryInvite.__table__.c.enrolment_id == person.id)
                           .values(enrolment_id=None, released_at=None))


_detach_invites()

def _remember_hosts():
    from sqlalchemy import event

    for model in (User, Survey):
        event.listen(model, "before_insert", _remember_host)


_remember_hosts()


def once_hash(value):
    """How a value of a stored URL parameter stands in OnceValue (app/panel.py, migration 0011)."""
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _once_rows(response):
    """{(name, hash)} a participation holds in OnceValue: the strings among the parameters of a real one."""
    if response.source != "real" or response.is_preview:
        return set()
    return {(name, once_hash(value)) for name, value in (response.params or {}).items()
            if isinstance(value, str) and value and len(name) <= 41}


def _keep_once_values():
    """OnceValue follows Response.params, whichever way a participation is stored, changed or deleted through the
    session. Deletions and changes past the session (retention.purge and anonymise, lifecycle.delete) remove the
    rows themselves."""
    from sqlalchemy import event, inspect

    table = OnceValue.__table__

    @event.listens_for(Response, "after_insert")
    def _inserted(mapper, connection, target):
        rows = _once_rows(target)
        if rows:
            connection.execute(table.insert(), [{"survey_id": target.survey_id, "response_id": target.id, "name": name,
                                                 "value_hash": h, "once": False} for name, h in sorted(rows)])

    @event.listens_for(Response, "after_update")
    def _updated(mapper, connection, target):
        state = inspect(target)
        if not any(state.attrs[key].history.has_changes() for key in ("params", "source", "is_preview")):
            return
        wanted = _once_rows(target)
        have = {(name, h): rid for rid, name, h in connection.execute(
            db.select(table.c.id, table.c.name, table.c.value_hash).where(table.c.response_id == target.id))}
        gone = [rid for key, rid in have.items() if key not in wanted]
        if gone:
            connection.execute(table.delete().where(table.c.id.in_(gone)))
        new = sorted(wanted - set(have))
        if new:
            connection.execute(table.insert(), [{"survey_id": target.survey_id, "response_id": target.id, "name": name,
                                                 "value_hash": h, "once": False} for name, h in new])

    @event.listens_for(Response, "after_delete")
    def _deleted(mapper, connection, target):
        loaded = inspect(target).dict
        if "params" in loaded and not loaded["params"]:
            return  # nothing of it is there
        connection.execute(table.delete().where(table.c.response_id == target.id))


_keep_once_values()


def _free_survey_codes():
    """A new survey's code is one that is neither another survey's code nor a label taken already (SurveyLabel):
    links.resolve reads labels first, so a code like "survey" that someone had chosen as a label led to the other
    survey (audit 2026-10-12, T-11). Drawn here, before the insert, unless the code was set already."""
    from sqlalchemy import event

    @event.listens_for(Survey, "before_insert")
    def _draw(mapper, connection, target):
        if target.code:
            return
        code = new_code()
        for _ in range(20):
            taken = connection.execute(db.select(SurveyLabel.label).where(SurveyLabel.label == code)).first() \
                or connection.execute(db.select(Survey.id).where(Survey.code == code)).first()
            if not taken:
                break
            code = new_code()
        target.code = code


_free_survey_codes()


class BrowserReceipt(db.Model):
    """Short-lived server state for anonymous actions; never a participation or an answer pool."""
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(index=True)



class ResponsePoolRevision(db.Model):
    """Monotone revisions per data pool, including deleted participations (SP13 PF-3)."""
    survey_id: Mapped[int] = mapped_column(ForeignKey("survey.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(10), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    nonce: Mapped[str] = mapped_column(String(32), default="", server_default="")


class ResponseRevision(db.Model):
    """Revisions per data pool in SHARDS rows, one chosen by the participation's id (app/data_revision.py): two
    participants who save at the same moment count in different rows and do not wait for each other. Before, every
    saved page also wrote the survey's row and one row per pool, and on PostgreSQL all saves of a survey ran one
    after the other (audit 2026-10-15, T05-N-1). No foreign key: the rows of a deleted survey go with the hourly
    sweep (data_revision.sweep). response_pool_revision and survey.data_rev are no longer counted."""
    survey_id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(10), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), primary_key=True)
    shard: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    nonce: Mapped[str] = mapped_column(String(32), default="", server_default="")


from sqlalchemy import event as _schema_event


@_schema_event.listens_for(db.metadata, "after_create")
def _revision_triggers(metadata, connection, **kwargs):
    from app.data_revision import install

    created = {table.name for table in kwargs.get("tables", ())}
    if {"survey", "response", "response_revision"} <= created:
        install(connection)


class WhisperCancel(db.Model):
    """A transcription job to cancel, written by the participants' side when a deletion takes answers with an
    interview recording along (app/whisper_jobs.py, cancel_records). The participants' containers reach neither the
    Whisper service nor the researchers' container any more (audit 2026-10-22, E-01, stage 2b); the housekeeping hands
    the rows on (drain_cancels) and deletes them. Only the job's number and the service it runs on, nothing about the
    person; a random key, so the table needs no sequence."""

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_random_id)
    job_id: Mapped[str] = mapped_column(String(32))
    service: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
