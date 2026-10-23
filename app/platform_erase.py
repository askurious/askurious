"""Deletion by the platform (Philipp, 2026-10-10): the admins of the platform delete participations of a survey, or
take a whole survey down, e.g. after a data-protection violation or an order of an authority.

The seal chain must show it (decisions.md, "Löschung durch die Plattform"): every case deleted this way gets a
deletion record whose reason is "platform: " and the admin's reason in a few words, and a survey taken down ends its
chain with a record "closed" with that reason. The reason is part of the hash and stamped by the DFN with the next
stamp, like every field of an entry; nobody can change it afterwards, the platform neither. It is public with the
chain (seal.json, /verify), so it names no person.

The researchers of the survey learn of it afterwards, by e-mail to the sign-in address of their accounts; who has
none, or every one when mail is off, is named to the admin, who tells them otherwise. The access log notes who
deleted what and why (app/audit.py, "Löschung durch die Plattform").
"""
import re

from app import erasure, mail, seal
from app.extensions import db
from app.lang import de_en
from app.models import Response, SurveyMember, User, UserLogin

REASON_MIN = 10  # characters: a reason, not a word
REASON_MAX = seal.REASON_MAX - len(seal.PLATFORM)
CASE = re.compile(r"^(?:#|fall\s*|case\s*)(\d+)$", re.IGNORECASE)


def clean_reason(text):
    """The reason as it is stored: one line, at most REASON_MAX characters; None if it is too short."""
    text = " ".join(str(text or "").split())
    return text[:REASON_MAX] if len(text) >= REASON_MIN else None


def find(survey, text):
    """What the lines of `text` name: (participations, entries of the separated section, lines that found nothing).
    A line "#12", "Fall 12" or "case 12" is a case number of the seal chain; any other line is looked up as the
    researchers' deletion requests do (deletion code, participation ID, identifier from the link, e-mail address of
    the separated section; erasure.find)."""
    by_case = {case: rid for rid, (case, _digest) in seal.export_map(survey).items()}
    responses, entries, missing = {}, {}, []
    for line in (" ".join(x.split()) for x in str(text or "").splitlines()):
        if not line:
            continue
        m = CASE.match(line)
        if m:
            rid = by_case.get(int(m.group(1)))
            r = db.session.get(Response, rid) if rid else None
            if r is None or r.survey_id != survey.id:
                missing.append(line)
            else:
                responses[r.id] = r
            continue
        found, more = erasure.find(survey, line[:300])
        if not found and not more:
            missing.append(line)
        responses.update({r.id: r for r in found})
        entries.update({e.id: e for e in more})
    return list(responses.values()), list(entries.values()), missing


def recipients(survey):
    """The researchers of the survey: [(name, e-mail address or None, user or None)], in the order of the authors."""
    rows = db.session.execute(
        db.select(SurveyMember, User, UserLogin.email).select_from(SurveyMember)
        .outerjoin(User, User.id == SurveyMember.user_id).outerjoin(UserLogin, UserLogin.user_id == User.id)
        .where(SurveyMember.survey_id == survey.id).order_by(SurveyMember.position, SurveyMember.id)).all()
    return [((user.display_name if user else member.name) or "", email or None, user)
            for member, user, email in rows]


def erase_cases(survey, responses, entries, reason):
    """Delete these participations and entries for good, with the platform's reason in the deletion records.
    Returns the number of participations."""
    erasure.erase(survey, responses, "platform", entries, reason=seal.platform_reason(reason))
    return len(responses)


def take_down(survey, reason):
    """Delete the survey with everything it holds; a seal chain ends with the platform's reason."""
    from app import lifecycle

    # the title goes from a seal chain that stays as well (decision 7a, S7): it may be why the survey went
    lifecycle.delete(survey, force=True, seal_reason=seal.platform_reason(reason), hide_title=True)
    db.session.commit()


def tell(people, title, code, reason, deleted=None):
    """Tell the researchers afterwards. deleted: the number of participations deleted; None: the survey was taken
    down. Returns (names told, names not told)."""
    from app import brand, lang

    told, not_told = [], []
    for name, email, user in people:
        if not email or not mail.enabled():
            not_told.append(name)
            continue
        with mail.in_language(lang.of(user)):
            if deleted is None:
                subject = de_en("Ihre Befragung „{t}“ wurde von der Plattform gelöscht",
                                "Your survey “{t}” has been deleted by the platform", t=title)
                what = de_en("die Admins der Plattform {b} haben Ihre Befragung „{t}“ ({c}) mit allen Teilnahmen, "
                             "Dateien und Einstellungen gelöscht.",
                             "the admins of the platform {b} have deleted your survey “{t}” ({c}) with all its "
                             "participations, files and settings.", b=brand.name(), t=title, c=code)
                seal_note = de_en("Hatte die Befragung eine Siegelkette, endet sie mit einem Vermerk mit diesem Grund; "
                                  "die Kette bleibt prüfbar.",
                                  "If the survey had a seal chain, it ends with a record giving this reason; the chain "
                                  "can still be checked.")
            else:
                subject = de_en("Teilnahmen Ihrer Befragung „{t}“ wurden von der Plattform gelöscht",
                                "Participations of your survey “{t}” have been deleted by the platform", t=title)
                what = de_en("die Admins der Plattform {b} haben in Ihrer Befragung „{t}“ ({c}) {n} gelöscht.",
                             "the admins of the platform {b} have deleted {n} in your survey “{t}” ({c}).",
                             b=brand.name(), t=title, c=code,
                             n=de_en("1 Teilnahme", "1 participation") if deleted == 1
                             else de_en("{n} Teilnahmen", "{n} participations", n=deleted))
                seal_note = de_en("Die Siegelkette vermerkt jede dieser Löschungen mit diesem Grund (Reiter „Export“, "
                                  "Siegelkette, „Jetzt prüfen“). Dateien, die Sie vorher heruntergeladen haben, "
                                  "enthalten die gelöschten Daten noch; bitte ersetzen Sie sie.",
                                  "The seal chain records each of these deletions with this reason (tab “Export”, "
                                  "seal chain, “Check now”). Files you downloaded before still contain the deleted "
                                  "data; please replace them.")
            text = (de_en("Guten Tag {name},", "Hello {name},", name=name) + "\n\n" + what + "\n\n"
                    + de_en("Grund:", "Reason:") + " " + reason + "\n\n" + seal_note + "\n\n"
                    + de_en("Bei Fragen antworten Sie über „Rückmeldung geben“ auf der Plattform.",
                            "If you have questions, reply via “Give feedback” on the platform.") + mail.signature())
        mail.send(email, subject, text)
        told.append(name)
    return told, not_told
