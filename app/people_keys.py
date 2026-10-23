"""Members and shares point to accounts (konto-schluessel-plan.md, all as recommended, Ralf 2026-10-18).

A person is an account (User.id). The ORCID iD is one way to sign in, like e-mail with password. Every row that names
a person (PERSON_MODELS) carries the account in `user_id`; its column `orcid` is the account's key of before (the iD,
or "q-…" for an account without ORCID) and is kept in step here until a later migration drops it, so code that still
reads it and a container of the code before keep working during a deploy. Entries of deleted accounts keep their name
under a key "x-…" and point to no account (B3).

Someone added by ORCID iD before their first sign-in gets an account at once, kept as pending (B2): no number, in no
list of accounts. Their first sign-in with ORCID makes it whole (auth._sign_in). Connecting an iD that has such an
account to an account without ORCID takes the pending account's rows over (accounts.link_orcid).
"""
from app.extensions import db
from app.models import (ConstructShare, Contact, DesignEditor, DesignShare, OrganisationMember, SurveyMember, User,
                        WorkGroupMember, is_orcid)

PERSON_MODELS = (SurveyMember, WorkGroupMember, OrganisationMember, DesignShare, DesignEditor, ConstructShare, Contact)


def account(key, name="", create=True):
    """The account behind `key` (an ORCID iD or the key of an account without ORCID), or None. For an iD without
    account a pending one is made (create): the person can be added before their first sign-in."""
    if not key:
        return None
    if key.startswith(ACCOUNT_PREFIX) and key[len(ACCOUNT_PREFIX):].isdigit():
        return db.session.get(User, int(key[len(ACCOUNT_PREFIX):]))  # "u<id>": an account without ORCID (key_for)
    user = db.session.scalar(db.select(User).filter_by(orcid=key))
    if user is not None or not create or not is_orcid(key):
        return user
    for obj in db.session.new:  # made earlier in this flush or request, not yet in the database
        if isinstance(obj, User) and obj.orcid == key:
            return obj
    from app.auth import new_account_ids

    ids = new_account_ids()
    taken = [o.id for o in db.session.new if isinstance(o, User) and o.id]
    user = User(id=max([ids["id"], *[t + 1 for t in taken]]), number=None, orcid=key, name=(name or "")[:200],
                pending=True)
    db.session.add(user)
    return user


ACCOUNT_PREFIX = "u"  # forms and the people search name an account without ORCID iD "u<id>" (since stage 2b)


def key_for(user):
    """How forms and the people search name an account: its ORCID iD, else "u<id>". Accounts without ORCID made
    before 2b also have a "q-…" key, which account() still reads until migration 0085 takes it out."""
    return user.orcid if user.has_orcid else f"{ACCOUNT_PREFIX}{user.id}"


def key_of(user_id):
    """The key column of before for the account (its iD, its "q-…" key, or None): kept in step until 0085."""
    user = db.session.get(User, user_id) if user_id else None
    return user.orcid if user is not None else None


def take_over(user, pending):
    """Connecting an iD that had a pending account: its rows go to `user`; where both have a row for the same
    survey, group or organisation, the higher role stays (the caller made the key columns equal before)."""
    if pending is None or pending.id == user.id:
        return
    for model in PERSON_MODELS:
        db.session.execute(db.update(model).where(model.user_id == pending.id).values(user_id=user.id)
                           .execution_options(synchronize_session=False))
    db.session.flush()
    db.session.delete(pending)


def pending_filter():
    """For lists and counts of accounts: only accounts that are whole (not pending)."""
    return User.pending.is_(False)


def sweep():
    """Pending accounts that no member or share row names any more (the person was removed again before ever signing
    in): their iD and name go, data minimisation. Returns how many went."""
    from app.models import SignInGrant

    from app.models import ResearchProject

    used = db.union(*(db.select(model.user_id).where(model.user_id.is_not(None)) for model in PERSON_MODELS))
    gone = list(db.session.scalars(db.select(User).where(User.pending.is_(True), User.id.not_in(used),
                                                         User.id.not_in(db.select(SignInGrant.user_id)))))
    shared = {s.get("user_id") for (shares,) in db.session.execute(db.select(ResearchProject.shares)
                                                                     .where(ResearchProject.shares.is_not(None)))
              for s in (shares if isinstance(shares, list) else []) if isinstance(s, dict)}
    gone = [u for u in gone if u.id not in shared]  # an AInstein project shared with the iD keeps it too
    for user in gone:
        db.session.delete(user)
    db.session.commit()
    return len(gone)


def names(entry, user):
    """Whether a JSON entry (an AInstein share or comment) names this account: by "user_id", or until migration 0085
    by the key of before ("orcid")."""
    if not isinstance(entry, dict) or user is None:
        return False
    return entry.get("user_id") is not None and entry.get("user_id") == user.id


def entry_for(user):
    """How a JSON entry names an account from now on: "user_id", and until migration 0085 also the key of before
    for a container of the code before."""
    return {"user_id": user.id}
