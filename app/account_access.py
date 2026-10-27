"""Revoke credentials an account could leave behind (audit 2026-10-13, AU-2)."""
from app.extensions import db
from app.lang import de_en
from app.models import AgentToken, AiAccess, AuthToken, CoscineConnection, DataToken, GroupJoinLink, MemberInvite, ZenodoConnection


def revoke(user_id):
    """Part of the caller's transaction: password change, block or emergency sign-out. Also signs the account out of
    Zenodo, Coscine and its own AI access: whoever had the account could have put their own token or a base URL of
    their own there, which would go on receiving data (Abgleich 2026-10-15, AU-2 rest). Returns the names of the
    services it signed out of, for the note to the person (`tell`)."""
    for model, column in ((DataToken, DataToken.user_id), (AgentToken, AgentToken.user_id),
                          (MemberInvite, MemberInvite.created_by_id),
                          (GroupJoinLink, GroupJoinLink.created_by_id), (AuthToken, AuthToken.user_id)):
        condition = column == user_id
        if model is AuthToken:
            condition = db.or_(condition, AuthToken.created_by_id == user_id)
        db.session.execute(db.delete(model).where(condition))
    block_interviewers(user_id)
    from app import transcript_coding_links

    transcript_coding_links.block_of_account(user_id)  # its coding links, likewise (app/transcript_coding_links.py)
    return disconnect_services(user_id)


def block_interviewers(user_id):
    """Block the interviewers' accesses the account made (app/interviewer.py): whoever had the account could have
    made one for themselves, valid up to a year (audit 2026-10-22, A-04). Blocked, not deleted: the interviews stay,
    and the guide's admins let a real interviewer in again on the page "Interviews" with one click. The number goes
    to `tell` through flask.g."""
    from flask import g, has_app_context

    from app.models import InterviewerAccess

    done = db.session.execute(db.update(InterviewerAccess).where(
        InterviewerAccess.created_by_id == user_id, InterviewerAccess.blocked.is_(False)).values(blocked=True)
        .execution_options(synchronize_session=False)).rowcount
    if has_app_context():
        g.interviewers_blocked = done
    return done


def revoke_links(user_id):
    """Only the account's one-time links (sign-in, reset, address check): what a first password replaces, while its
    data access tokens, invitations and join links stay (Abgleich 2026-10-15, T01-N2; AU-12)."""
    db.session.execute(db.delete(AuthToken).where(AuthToken.user_id == user_id))


def disconnect_services(user_id):
    """Delete the stored Zenodo tokens, the Coscine token and the own AI key of the account. The providers keep them
    valid until revoked there; the account has to connect anew. Returns the names of those it had."""
    gone = []
    for model, name in ((ZenodoConnection, "Zenodo"), (CoscineConnection, "Coscine"),
                        (AiAccess, de_en("eigener KI-Zugang", "own AI access"))):
        if db.session.execute(db.delete(model).where(model.user_id == user_id)).rowcount:
            gone.append(name)
    return gone


def tell(names):
    """The note to the person whose connections `revoke` removed (only to themselves, never to an admin who blocks)."""
    from flask import flash, g

    blocked = g.pop("interviewers_blocked", 0)
    if blocked:
        flash(de_en("Aus Sicherheitsgründen wurden auch die von Ihnen angelegten Interviewlinks gesperrt ({n}). Unter "
                    "„Interviews“ des Leitfadens geben Sie sie mit einem Klick wieder frei.",
                    "For security, the interview links you created were blocked as well ({n}). Unblock them with one "
                    "click under “Interviews” of the guide.", n=blocked), "warning")
    if not names:
        return

    flash(de_en("Aus Sicherheitsgründen wurden auch diese Verbindungen getrennt: {names}. Verbinden Sie sie unter "
                "„Konto › Verbindungen“ neu, wenn Sie sie weiter brauchen. Beim Anbieter bleiben die alten Schlüssel gültig, bis sie "
                "dort widerrufen werden oder ablaufen.",
                "For security, these connections were removed as well: {names}. Connect them again under “Account › Connections” if "
                "you still need them. At the provider the old keys stay valid until they are revoked there or expire.",
                names=", ".join(names)), "warning")
