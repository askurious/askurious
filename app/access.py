"""Who may open a survey, and in which role: the one place that answers it.

Access comes from two sources. A person is a co-owner of the survey (SurveyMember, with a role up to "admin");
that list is also the list of authors. Or the survey is shared with a group the person is a member of
(SurveyGroup): that gives access, not authorship, and it is live, so who joins the group has it at once and who
leaves loses it with the next request. The highest role counts.

A group gives "viewer" or "editor", never "admin". "Editor" includes the raw data, and who belongs to a group is
decided by its leads, not by the admins of the survey (security audit 2026-10-04, M2 had limited groups to
"viewer"); Ralf decided on 2026-10-05 that a survey's admins may still give a group "editor", knowingly.

An example survey (Survey.showcase, set by site admins who are its admins) gives every signed-in researcher at least
"viewer", and nothing more; it does not enter survey_ids (palette, footer, linked elements count only one's own work),
the start page lists such surveys in a section of their own (concept demo-befragungen-konzept.md, Ralf 2026-10-11).
Who has its role only from that may not comment in the preview and may take a copy (showcase_only).

Organisations (app/orgs.py, konzept-abteilungen-2026-10-15.md) are a fence: a group gives access only to surveys of
its own organisation; the personal entry is the one way across.
"""
from app.extensions import db
from app.models import Block, Item, Survey, SurveyGroup, SurveyMember, WorkGroupMember

GROUP_ROLES = ("viewer", "editor")  # what a group can have on a survey (never "admin")
_RANK = {None: 0, "viewer": 1, "editor": 2, "admin": 3}


def _group_ids(user_id):
    return db.select(WorkGroupMember.group_id).filter(WorkGroupMember.user_id == user_id)


def _account(key):
    from app import people_keys

    return people_keys.account(key, create=False)


def showcased(survey, user=None):
    """An example survey open to this person (to all without `user`): marked and not archived (archiving takes it
    away for everyone). One of the platform for everyone but guests; one of an organisation (Survey.org_showcase, A13)
    for the members of the survey's organisation (app/orgs.py)."""
    if survey.archived or not (survey.showcase or survey.org_showcase):
        return False
    from app import constructs

    # only for a marked survey: the start page asks this for every card, and walking the questionnaire of each one
    # cost two queries per card (test_the_start_page_asks_the_same_number_of_queries_for_more_surveys)
    if constructs.restricted_keys(survey):
        return False
    if user is None:
        return bool(survey.showcase)
    from app import orgs

    if survey.showcase and orgs.home_id(user) != orgs.guest_id():
        return True
    return bool(survey.org_showcase) and orgs.of(survey) in orgs.ids_of(user)


def showcase_query(user=None):
    """Ids of the example surveys open to all; with `user` also those of their organisations, and none of the
    platform for a guest."""
    from app import constructs

    # Apply the same boundary to listings as to direct URLs, including legacy showcase flags.
    permitted = ~Survey.blocks.any(Block.items.any(
        Item.config["construct"]["key"].as_string().in_(constructs.NOT_SHARED)))
    if user is None:
        return db.select(Survey.id).filter(Survey.showcase.is_(True), Survey.archived.is_(False), permitted)
    from app import orgs

    platform = Survey.showcase.is_(True) if not orgs.is_guest(user) else db.false()
    own = db.and_(Survey.org_showcase.is_(True), orgs.column(Survey.org_id).in_(orgs.ids_of(user)))
    return db.select(Survey.id).filter(db.or_(platform, own), Survey.archived.is_(False), permitted)


def role_of_user(user, survey, groups=None):
    """The role of this account on the survey ("viewer" | "editor" | "admin"), or None; capped by the person's level
    for the survey's type (app/kind_access.py: "off" hides it, "view" leaves "viewer"). groups: the ids of the
    person's work groups when the caller has them (roles_of_user), else read here."""
    from app import kind_access

    if user is None:
        return None
    best = own_role_of_user(user, survey, groups)
    if best is None and showcased(survey, user):
        best = "viewer"  # an example survey: every signed-in researcher (of its organisation) may view it
    return kind_access.cap(best, kind_access.level(user, survey.kind)) if best is not None else None


def own_role_of_user(user, survey, groups=None):
    """The role from the person's own entry and their groups, without what an example survey gives everyone."""
    if user is None:
        return None
    member = next((m for m in survey.members if m.user_id == user.id), None)
    best = member.role if member else None
    if survey.group_links and best != "admin":
        from app import orgs

        mine = set(db.session.scalars(_group_ids(user.id))) if groups is None else groups
        home = orgs.of(survey)
        for link in survey.group_links:
            # only a group of the survey's own organisation (app/orgs.py, A8): a share from before a group moved out
            # opens nothing across the fence
            if link.group_id in mine and orgs.of(link.group) == home:
                given = link.role if link.role in GROUP_ROLES else "viewer"  # never more than "editor"
                if _RANK[given] > _RANK[best]:
                    best = given
    return best


def roles_of_user(user, surveys):
    """{survey id: role_of_user} for many surveys at once: their members and shares with groups in two queries and
    the person's groups once, instead of three queries per survey (performance audit 2026-10-18, PERF-04: the list
    "In andere Befragung kopieren" of the element page, 192 queries with 81 drafts)."""
    from sqlalchemy.orm import selectinload

    surveys = list(surveys)
    if user is None or not surveys:
        return {s.id: None for s in surveys}
    ids = [s.id for s in surveys]
    db.session.scalars(db.select(Survey).where(Survey.id.in_(ids)).options(
        selectinload(Survey.members), selectinload(Survey.group_links).selectinload(SurveyGroup.group))).all()
    groups = set(db.session.scalars(_group_ids(user.id)))
    return {s.id: role_of_user(user, s, groups) for s in surveys}


def role_of_orcid(key, survey):
    """role_of_user for the account with this key (ORCID iD or "q-..."), e.g. who made a stored link."""
    return role_of_user(_account(key), survey)


def own_role_of_orcid(key, survey):
    return own_role_of_user(_account(key), survey)


def role(user, survey):
    return role_of_user(user, survey)


def showcase_only(user, survey):
    """Whether the person sees this survey only because it is an example survey (no entry, no group)."""
    return showcased(survey, user) and own_role_of_user(user, survey) is None


def may_copy_out(user, survey):
    """Who may take the questionnaire out of the survey: as a survey file (surveys.export_file, export_zip, the
    multi-export of "Meine Befragungen") or into a diary study (diary.take_over_page). The same people as for
    duplicating (app/duplicate.py), without the right to create that duplicating needs on top: "Bearbeiten" or
    "Admin", or an example survey seen only as one. "Ansehen" reads, it takes nothing out (audit 2026-10-18, AUTHZ-05)."""
    return survey.can(user, "editor") or showcase_only(user, survey)


def survey_ids(user, every_kind=False):
    """Subquery: ids of all surveys this researcher can open (as co-owner or through a group), without the types
    that are "off" for the person (app/kind_access.py); every_kind: with them (deleting the account)."""
    from app import kind_access

    from app import orgs
    from app.models import WorkGroup

    personal = db.select(SurveyMember.survey_id).filter(SurveyMember.user_id == user.id)
    # by a group only within the survey's organisation (app/orgs.py, A8)
    by_group = (db.select(SurveyGroup.survey_id).join(Survey, Survey.id == SurveyGroup.survey_id)
                .join(WorkGroup, WorkGroup.id == SurveyGroup.group_id)
                .filter(SurveyGroup.group_id.in_(_group_ids(user.id)),
                        orgs.column(Survey.org_id) == orgs.column(WorkGroup.org_id)))
    ids = personal.union(by_group)
    hidden = None if every_kind else kind_access.hidden_clause(user)
    if hidden is None:
        return ids
    return db.select(Survey.id).filter(Survey.id.in_(ids), hidden)


def showcase_ids(user):
    """Subquery: the example surveys open to this researcher (showcase_query without the types that are "off")."""
    from app import kind_access

    hidden = kind_access.hidden_clause(user)
    return showcase_query(user) if hidden is None else showcase_query(user).filter(hidden)


def via(user, survey):
    """The groups of this researcher the survey is shared with (for "über Gruppe ..." on the start page)."""
    if not survey.group_links:
        return []
    from app import orgs

    mine = set(db.session.scalars(_group_ids(user.id)))
    return [link.group for link in survey.group_links if link.group_id in mine and orgs.same(link.group, survey)]
