"""Datenschutzkonzept: Administration > Datenschutz (Ralf, 2026-10-10; all decisions as recommended).

One report that shows which personal data the platform processes, who processes them (per group of people, never
named persons), which of them go to outside services, and which measures protect them. It is built anew on every
call from the code's own knowledge and the configuration of this server, so it shows what is switched on here,
not what could be. What the code cannot know (controller, data protection officer, supervisory authority, the way
to report a breach, the next review) a site admin enters on the page; they are kept in SiteSetting.

The order follows what the GDPR asks to be shown (Art. 5 (2) accountability, Art. 30 (1) a-g record of processing
activities, Art. 25 defaults, Art. 32 security, Art. 35 threshold analysis, Art. 44-46 third countries); the
measures are grouped by the seven protection goals of the Standard-Datenschutzmodell 3.1 of the German data
protection conference (DSK, 14 May 2024). Research and outline: datenschutzkonzept-gliederung.md.

build() gives plain data (strings, lists, dicts) for the page (templates/admin/privacy.html) and the Word file
(app/privacy_concept_docx.py), so both always say the same. Legal assessments (legal basis, transfers to third
countries, whether an impact assessment is needed) are proposals for the controller and the data protection officer
to confirm; the report says so."""
import os
import re
from urllib.parse import urlsplit

from flask import current_app

from app.lang import de_en
from app.timeutil import local_today

# the fields a site admin fills in (SiteSetting.key, at most 200 characters each): section 1, then appendix 1. The
# second line after the RWTH forms (record of processing activities, TOM list after Art. 32; comparison 2026-10-20,
# V2 T1-T5): department, contact persons, server site, information security officer, admins' duty and training,
# emergency plan, restore tests, encryption at rest, data processing agreements of the outside services
ORG_FIELDS = ("privacy_controller", "privacy_dpo", "privacy_authority", "privacy_breach", "privacy_review",
              "privacy_department", "privacy_contact_subject", "privacy_contact_tech", "privacy_isb", "privacy_site",
              "privacy_admin_duty", "privacy_emergency", "privacy_restore_test", "privacy_encryption",
              "privacy_avv_openai", "privacy_avv_slack", "privacy_avv_mail")
DPIA_FIELD = "privacy_dpia_coding"
FIELDS = ORG_FIELDS + (DPIA_FIELD,)
MAX_CHARS = 200


def field_labels():
    return {
        "privacy_controller": (de_en("Verantwortlicher (Name, Anschrift)", "Controller (name, address)"),
                               de_en("z. B. RWTH Aachen, Lehrstuhl …, Anschrift", "e.g. RWTH Aachen, chair …, address")),
        "privacy_dpo": (de_en("Datenschutzbeauftragte Person (Kontakt)", "Data protection officer (contact)"),
                        de_en("Name oder Stelle, E-Mail", "name or office, email")),
        "privacy_authority": (de_en("Zuständige Aufsichtsbehörde", "Competent supervisory authority"),
                              de_en("z. B. LDI NRW", "e.g. LDI NRW")),
        "privacy_breach": (de_en("Meldeweg bei Datenpannen", "How a data breach is reported"),
                           de_en("wer informiert wen, wie erreichbar; Meldung an die Behörde binnen 72 Stunden",
                                 "who tells whom, how to reach them; report to the authority within 72 hours")),
        "privacy_review": (de_en("Nächste Überprüfung des Konzepts", "Next review of this concept"),
                           de_en("z. B. 2027-04", "e.g. 2027-04")),
        "privacy_department": (de_en("Fachabteilung mit Organisationskennziffer", "Department with organisation code"),
                               de_en("z. B. Lehrstuhl …, Kennziffer …", "e.g. chair …, code …")),
        "privacy_contact_subject": (de_en("Fachliche Ansprechperson", "Contact person for the subject matter"),
                                    de_en("Name oder Funktion, E-Mail", "name or role, email")),
        "privacy_contact_tech": (de_en("Technische Ansprechperson", "Technical contact person"),
                                 de_en("Name oder Funktion, E-Mail", "name or role, email")),
        "privacy_isb": (de_en("Informationssicherheitsbeauftragte Person", "Information security officer"),
                        de_en("Name oder Stelle, E-Mail", "name or office, email")),
        "privacy_site": (de_en("Serverstandort und physische Sicherung", "Server site and physical protection"),
                         de_en("z. B. Rechenzentrum …, Zutritt nur mit Karte", "e.g. data centre …, access by card only")),
        "privacy_admin_duty": (de_en("Verpflichtung und Schulung der Admins", "Admins' duty of confidentiality and training"),
                               de_en("z. B. auf das Datengeheimnis verpflichtet am …, Schulung jährlich",
                                     "e.g. bound to data secrecy on …, training every year")),
        "privacy_emergency": (de_en("Notfallplan", "Emergency plan"),
                              de_en("wo er liegt, wer ihn auslöst", "where it is kept, who starts it")),
        "privacy_restore_test": (de_en("Letzter Wiederherstellungstest", "Last restore test"),
                                 de_en("Datum und Ergebnis einer vollständigen Wiederherstellung",
                                       "date and result of a complete restore")),
        "privacy_encryption": (de_en("Verschlüsselung von Datenträger und Datenbank", "Encryption of disk and database"),
                               de_en("z. B. Datenträger mit LUKS verschlüsselt; Sicherungen mit GPG",
                                     "e.g. disk encrypted with LUKS; backups with GPG")),
        "privacy_avv_openai": (de_en("Vertrag zur Auftragsverarbeitung mit OpenAI", "Data processing agreement with OpenAI"),
                               de_en("liegt vor seit …, Fundstelle", "in place since …, where it is kept")),
        "privacy_avv_slack": (de_en("Vertrag zur Auftragsverarbeitung mit Slack", "Data processing agreement with Slack"),
                              de_en("liegt vor seit …, Fundstelle", "in place since …, where it is kept")),
        "privacy_avv_mail": (de_en("Vertrag zur Auftragsverarbeitung mit dem Mail-Relay",
                                   "Data processing agreement with the mail relay"),
                             de_en("liegt vor seit …, Fundstelle; entfällt beim eigenen Relay",
                                   "in place since …, where it is kept; not needed for an own relay")),
        DPIA_FIELD: (de_en("Stellungnahme der datenschutzbeauftragten Person zur DSFA KI-Kodierhilfe",
                           "Statement of the data protection officer on the impact assessment of the AI coding help"),
                     de_en("Datum, Ergebnis, ggf. Auflagen", "date, result, conditions if any")),
    }


def org():
    """{key: {"value", "at", "by"}} of the fields above; empty value when not filled in."""
    from app.extensions import db
    from app.models import SiteSetting, User

    rows = {s.key: s for s in db.session.scalars(db.select(SiteSetting).where(SiteSetting.key.in_(FIELDS)))}
    out = {}
    for key in FIELDS:
        s = rows.get(key)
        who = db.session.get(User, s.changed_by_id) if s is not None and s.changed_by_id else None
        out[key] = {"value": s.value if s is not None else "", "at": s.changed_at if s is not None else None,
                    "by": who.display_name if who is not None else ""}
    return out


def save_org(form, user):
    """Store the fields from a form; returns how many changed. Longer texts are cut to MAX_CHARS."""
    from app.extensions import db
    from app.models import SiteSetting, utcnow

    changed = 0
    for key in FIELDS:
        if key not in form:
            continue
        value = re.sub(r"\s+", " ", form.get(key) or "").strip()[:MAX_CHARS]
        s = db.session.get(SiteSetting, key)
        if s is None:
            if not value:
                continue
            s = SiteSetting(key=key, value="")
            db.session.add(s)
        if s.value != value:
            s.value, s.changed_by_id, s.changed_at = value, user.id, utcnow()
            changed += 1
    db.session.commit()
    return changed


def _host(url):
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _licence_text():
    try:
        with open(os.path.join(os.path.dirname(current_app.root_path), "licence.md"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _own_access_state():
    """(in use?, words) of the own AI access, for everyone since 2026-10-10 (app/ai_access.py): how many people
    stored a key."""
    from app.extensions import db
    from app.models import AiAccess

    n = db.session.scalar(db.select(db.func.count(AiAccess.user_id))) or 0
    return n > 0, de_en("für alle; {n} Personen mit eigenem Schlüssel", "for everyone; {n} people with their own key", n=n)


def _feature_state(key):
    """(on anywhere?, words) of an experimental feature: for everyone, for persons, groups and organisations, or
    nobody; plus how often it is in use (a survey that uses it keeps it, app/experimental.py)."""
    from app import experimental

    if key not in experimental.KEYS:
        return False, ""
    o = experimental.overview(key)
    n, what = o["uses"]
    if o["everyone"]:
        text = de_en("an für alle", "on for everyone")
    elif o["people"] or o["groups"] or o["orgs"]:
        text = de_en("an für {p} Personen, {g} Gruppen und {k} Organisationen",
                     "on for {p} people, {g} groups and {k} organisations",
                     p=len(o["people"]), g=len(o["groups"]), k=len(o["orgs"]))
    else:
        text = de_en("für niemanden an", "on for nobody")
    if n:
        text += de_en("; genutzt in {n} {what}", "; used in {n} {what}", n=n, what=what)
    return bool(o["everyone"] or o["people"] or o["groups"] or o["orgs"] or n), text


def _on(flag, detail=""):
    word = de_en("an", "on") if flag else de_en("aus", "off")
    return (bool(flag), f"{word} ({detail})" if flag and detail else word)


def roles():
    """Groups of people and what each sees and does (never named persons)."""
    return [
        {"who": de_en("Admin der Plattform", "Site admin"),
         "how": de_en("Häkchen am Konto oder ADMIN_ORCIDS in der Serverkonfiguration",
                      "tick on the account or ADMIN_ORCIDS in the server configuration"),
         "sees": de_en("Alle Konten und Befragungen (ohne Antworten zu öffnen), Protokoll, KI-Nutzung aller Konten, Server; "
                       "kann Konten sperren und löschen, ihre Heimat-Organisation ändern (deren Admins verwalten sie dann) und "
                       "Befragungen plattformweit löschen; dieses Konzept.",
                       "All accounts and surveys (without opening answers), access log, AI usage, server; can lock "
                       "and delete accounts, change their home organisation (whose admins then manage them) and delete "
                       "surveys platform-wide; this concept.")},
        {"who": de_en("Forschende (Konto)", "Researcher (account)"),
         "how": de_en("Anmeldung mit ORCID oder E-Mail und Passwort; Stufe je Befragungsart je Konto",
                      "sign-in with ORCID or email and password; level per survey type per account"),
         "sees": de_en("Eigene Befragungen und solche, an denen sie beteiligt sind.",
                       "Their own surveys and those they take part in.")},
        {"who": de_en("Beteiligte mit Rolle „Verwalten“", "Member with role “Manage”"),
         "how": de_en("Rolle in einer Befragung (SurveyMember)", "role in a survey (SurveyMember)"),
         "sees": de_en("Alles der Befragung: Fragebogen, Rohdaten, Kontaktangaben, Einstellungen, Löschen.",
                       "Everything of the survey: questionnaire, raw data, contact data, settings, deletion.")},
        {"who": de_en("Beteiligte mit Rolle „Bearbeiten“", "Member with role “Edit”"),
         "how": de_en("Rolle in einer Befragung, auch über eine Arbeitsgruppe",
                      "role in a survey, also through a work group"),
         "sees": de_en("Fragebogen bearbeiten, Feld starten und stoppen, Rohdaten herunterladen.",
                       "Edit the questionnaire, start and stop fieldwork, download raw data.")},
        {"who": de_en("Beteiligte mit Rolle „Ansehen“", "Member with role “View”"),
         "how": de_en("Rolle in einer Befragung, auch über eine Arbeitsgruppe",
                      "role in a survey, also through a work group"),
         "sees": de_en("Fragebogen und Ergebnisse lesen, keine Rohdaten und keine Befragungsdatei; bei Interviewleitfäden "
                       "keine Interviews.",
                       "Read questionnaire and results, no raw data and no survey file; no interviews of an interview guide.")},
        {"who": de_en("Arbeitsgruppe (Leitung, Mitglieder)", "Work group (lead, members)"),
         "how": de_en("Gruppe erhält Ansehen oder Bearbeiten an einer Befragung, nie Verwalten",
                      "a group gets View or Edit on a survey, never Manage"),
         "sees": de_en("Wie die Rolle der Gruppe; Bearbeiten schließt Rohdaten ein.",
                       "As the group's role; Edit includes raw data.")},
        {"who": de_en("Admin einer Organisation", "Organisation admin"),
         "how": de_en("von Plattform-Admins ernannt (Administration > Organisationen) oder bei „Gruppe zur Organisation "
                      "machen“ aus der Leitung der Gruppe; im Sicherheitsprotokoll",
                      "appointed by site admins (Administration > Organisations) or from the group's lead when a group "
                      "is made an organisation; in the security log"),
         "sees": de_en("Unter „Verwaltung“ mit zweitem Faktor nach Passwort-Anmeldung: Personen der Organisation (Name, "
                       "ORCID iD, Heimat, Gruppen der Organisation, angelegt und zuletzt angemeldet, Zahl ihrer "
                       "Befragungen, laufenden und selbst angelegten Befragungen und echten Teilnahmen, nur in "
                       "Befragungen dieser Organisation); offene Einladungen (Name oder ORCID iD, Art, Datum, wer "
                       "eingeladen hat, Ende) und sie zurückziehen; Konten mit Heimat hier anlegen (ORCID iD oder Name und "
                       "E-Mail-Adresse mit Einmal-Link); für Konten mit Heimat hier (außer denen anderer Admins der "
                       "Organisation und Konten mit Rollen außerhalb der Organisation, die ein Plattform-Admin verwaltet) "
                       "Nutzertyp, Laufzeit, "
                       "Befragungstypen (bis „Anlegen“), Sperre und Einmal-Link, höchstens im Rahmen der Organisation; "
                       "Gruppen der Organisation; Liste ihrer Befragungen mit Stand, Beteiligten und Zahl der "
                       "Teilnahmen. Bestehende Konten treten nur bei, wenn die Person eine Einladung selbst annimmt (ein Gast "
                       "bekommt die Organisation dabei als Heimat). Konten, die nur zu dieser Organisation gehören, zur Löschung vormerken (sofort "
                       "gesperrt, nach 30 Tagen gelöscht, bis dahin zurücknehmbar), dabei Befragungen und Designs, die "
                       "nur der Person gehören, an eine Person der Organisation übergeben oder mitlöschen; Personen, die "
                       "auch zu einer anderen Organisation gehören, aus Organisation und ihren Gruppen entfernen. Nie "
                       "Inhalte oder Antworten von Befragungen und nie selbst übernehmen; Admins der Organisation löscht "
                       "ein Plattform-Admin.",
                       "Under “Management”, with a second factor after a password sign-in: the organisation's people "
                       "(name, ORCID iD, home, the organisation's groups, created and last signed in, number of their "
                       "surveys, running and self-created surveys and real participations, only in surveys of this "
                       "organisation); open invitations (name or ORCID iD, kind, date, who "
                       "invited, end) and withdrawing them; create accounts at home here (ORCID iD, or name and email address with a "
                       "one-time link); for accounts at home here (except those of other admins of the organisation and "
                       "accounts with roles beyond the organisation, which a site admin manages) "
                       "user type, period, survey "
                       "types (up to “Create”), block and one-time link, at most within the organisation's limits; its "
                       "groups; the list of its surveys with state, people involved and number of participations. Existing "
                       "accounts join only when the person accepts an invitation themselves (a guest gets the "
                       "organisation as home with it). Mark "
                       "accounts that belong to this organisation only for deletion (blocked at once, deleted after 30 days, until "
                       "then reversible), passing surveys and designs that belong to the person alone to a person of "
                       "the organisation or deleting them; remove people who also belong to another organisation from "
                       "it and its groups. Never content or answers of surveys and never receiving anything "
                       "themselves; admins of the organisation are deleted by a site admin.")},
        {"who": de_en("Gast", "Guest"),
         "how": de_en("Konto, das nur über eine persönliche Einladung in eine Befragung entstand",
                      "account that came in only by a personal invitation to a survey"),
         "sees": de_en("Nur die Befragungen, in die es eingeladen ist; legt nichts an, findet niemanden in der Suche.",
                       "Only the surveys it is invited to; creates nothing, finds nobody in the search.")},
        {"who": de_en("Alle angemeldeten Forschenden (Beispielbefragung)", "All signed-in researchers (example survey)"),
         "how": de_en("Plattform-Admins, die Verwalten an der Befragung haben, machen sie zur Beispielbefragung, mit "
                      "zweitem Faktor, wenn er eingeschaltet ist; im Zugriffsprotokoll; archiviert endet es",
                      "site admins who have Manage on the survey make it an example survey, with the second factor "
                      "when it is switched on; in the access log; archiving ends it"),
         "sees": de_en("Wie Ansehen, aber ohne Kommentare (auch nicht in deren Export und Bildern) und ohne die "
                       "Mitglieder von Gruppen; Namen der Beteiligten; eigene Kopie ohne Daten.",
                       "As View, but without comments (nor their export and images) and without the members of "
                       "groups; names of those involved; a copy of their own without data.")},
        {"who": de_en("Inhaber eines Lese-Tokens (R, Python)", "Holder of a read token (R, Python)"),
         "how": de_en("Token von Beteiligten mit Bearbeiten oder Verwalten, solange sie die aktuellen "
                      "Nutzungsbedingungen angenommen haben; gespeichert nur als Hash",
                      "token of a member with Edit or Manage, as long as they have accepted the current terms of use; "
                      "stored as a hash only"),
         "sees": de_en("Rohdaten der Befragung als Datei.", "The survey's raw data as a file.")},
        {"who": de_en("Personen mit Dashboard- oder Quotenlink", "People with a dashboard or quota link"),
         "how": de_en("Öffentlicher Link mit Token", "public link with a token"),
         "sees": de_en("Nur zusammengefasste Zahlen, keine einzelnen Antworten.",
                       "Aggregated figures only, no single answers.")},
        {"who": de_en("Teilnehmende", "Participants"),
         "how": de_en("Ohne Konto, über den Teilnahmelink", "without an account, through the participation link"),
         "sees": de_en("Die eigene Teilnahme; Löschen, Beschwerde.", "Their own participation; deletion, complaint.")},
        {"who": de_en("Teilnehmende einer Live-Sitzung", "Participants of a live session"),
         "how": de_en("Geräte-Cookie, auf Wunsch Spitzname", "device cookie, a nickname if they wish"),
         "sees": de_en("Die eigenen Stimmen und die gezeigten Ergebnisse.", "Their own votes and the results shown.")},
        {"who": de_en("Interviewende ohne Konto (Interviewlink)", "Interviewers without an account (interviewer link)"),
         "how": de_en("Persönlicher Link für einen Leitfaden von Beteiligten mit Bearbeiten oder Verwalten; nur als Hash "
                      "gespeichert; befristet (Voreinstellung 90 Tage), höchstens drei Geräte, sperrbar",
                      "personal link for one guide from members with Edit or Manage; stored as a hash only; limited in "
                      "time (default 90 days), at most three devices, can be blocked"),
         "sees": de_en("Nur die eigenen Interviews: führen, aufnehmen, transkribieren, bis das Transkript als geprüft "
                       "übergeben ist; nicht den Leitfaden bearbeiten, keine Auswertung, keine Exporte, keine Interviews "
                       "anderer.", "Only their own interviews: conduct, record, transcribe, until the transcript is handed "
                       "over as checked; no editing of the guide, no analysis, no exports, no interviews of others.")},
        {"who": de_en("Interviewte Personen", "Interviewees"),
         "how": de_en("Eingetragen von Forschenden; mit dem Teilnehmerbildschirm ein Link je Interview "
                      "für ein einziges eigenes Gerät, ohne Konto",
                      "entered by researchers; with the participant screen a link per interview for a "
                      "single device of their own, without an account"),
         "sees": de_en("Sonst nichts auf der Plattform; auf dem Teilnehmerbildschirm nur die Aufgabe, die die "
                       "Interviewenden gerade freigeben, und das eigene Ergebnis dazu; Löschung über die Forschenden.",
                       "Otherwise nothing on the platform; on the participant screen only the task the interviewers "
                       "release at that moment and their own result for it; deletion through the researchers.")},
        {"who": de_en("Testende (Vorschau, Telefonlink)", "Testers (preview, phone link)"),
         "how": de_en("Vorschau- oder Telefonlink der Befragung", "preview or phone link of the survey"),
         "sees": de_en("Den Fragebogen; schreiben Kommentare, auf Wunsch mit Bildschirmfoto.",
                       "The questionnaire; write comments, with a screenshot if they wish.")},
    ]


# Per activity in the order of activities(): a key for the installation (overlay), when it came into the platform and
# when it last changed, as far as the version history (from 2026-09-26) shows (comparison with the RWTH form, V1).
# A new activity or a change of one adds or updates its line here.
ACTIVITY_META = (
    ("accounts", "2026-09-26", "2026-10-20"), ("participation", "2026-09-26", "2026-10-20"),
    ("pool", "2026-10-04", "2026-10-15"), ("live", "2026-10-03", "2026-10-19"),
    ("interviews", "2026-10-04", "2026-10-27"), ("interviewer_links", "2026-10-15", "2026-10-15"),
    ("participant_screen", "2026-10-20", "2026-10-20"), ("diary", "2026-10-11", "2026-10-27"),
    ("invitations", "2026-09-26", "2026-10-20"), ("paper", "2026-10-10", "2026-10-17"),
    ("comments", "2026-09-30", "2026-10-17"), ("feedback", "2026-09-27", "2026-10-16"),
    ("audit_log", "2026-10-04", "2026-10-19"), ("ai", "2026-10-01", "2026-10-20"),
    ("agent", "2026-10-15", "2026-10-20"), ("archives", "2026-09-26", "2026-10-20"),
    ("operation", "2026-09-26", "2026-10-20"),
)


# Per activity what the RWTH form of the record asks beyond Art. 30 (1) (comparison with the templates 2026-10-20, points
# 2 and 3): the internal recipients by function, and transfers to third countries (the services of section 5 say
# where they are; "on here" is read from the configuration in activities()).
INTERNAL = {
    "accounts": ("Admins der Plattform (Konten, Freigaben); Admins von Organisationen für ihre Mitglieder",
                 "Platform admins (accounts, grants); organisation admins for their members"),
    "participation": ("Forschende der Befragung nach Rolle (Ansehen, Bearbeiten, Verwalten); Admins der Plattform sehen "
                      "keine Antworten", "Researchers of the survey by role (view, edit, manage); platform admins see "
                      "no answers"),
    "pool": ("Admins der Plattform (Reiter Konstrukte)", "Platform admins (tab Constructs)"),
    "live": ("Forschende der Abstimmung nach Rolle; Publikum sieht nur zusammengefasste Ergebnisse",
             "Researchers of the poll by role; the audience sees aggregated results only"),
    "interviews": ("Forschende des Leitfadens nach Rolle; Interviewende über ihren Link nur ihre Interviews",
                   "Researchers of the guide by role; interviewers through their link only their interviews"),
    "interviewer_links": ("Forschende des Leitfadens; die Interviewenden selbst", "Researchers of the guide; the "
                          "interviewers themselves"),
    "participant_screen": ("Forschende des Leitfadens nach Rolle", "Researchers of the guide by role"),
    "diary": ("Forschende der Studie nach Rolle", "Researchers of the study by role"),
    "invitations": ("Forschende der Befragung mit Verwalten", "Researchers of the survey with Manage"),
    "paper": ("Forschende der Befragung nach Rolle", "Researchers of the survey by role"),
    "comments": ("Beteiligte der Befragung und eingeladene Testende", "Members of the survey and invited testers"),
    "feedback": ("Admins der Plattform", "Platform admins"),
    "audit_log": ("Admins der Plattform; je Befragung deren Verwaltende", "Platform admins; per survey its managers"),
    "ai": ("Die Person, die die Funktion nutzt; Admins sehen die Zahl der Aufrufe", "The person using the feature; "
           "admins see the number of calls"),
    "agent": ("Die Person, der der Zugang gehört", "The person who owns the access"),
    "archives": ("Forschende der Befragung, die ablegen", "Researchers of the survey who deposit"),
    "operation": ("Admins des Servers (Betrieb, Sicherungen)", "Server admins (operation, backups)"),
}
# third countries per activity: (services in the USA it may use, the switch that turns them on)
THIRD = {
    "accounts": (("ORCID", "ORCID_CLIENT_ID"), ("Slack", "SLACK_WEBHOOK_URL")),
    "participation": (("OpenAI", "OPENAI_API_KEY"),),
    "interviews": (("OpenAI", "OPENAI_API_KEY"),),
    "diary": (("Google Play", None),),
    "feedback": (("Slack", "SLACK_WEBHOOK_URL"),),
    "ai": (("OpenAI", "OPENAI_API_KEY"),),
}


def _third_country(key):
    """The answer to "Übermittlung in ein Drittland" for one activity, from THIRD and the switches of this server."""
    c = current_app.config
    on = [name for name, switch in THIRD.get(key, ()) if switch is None or c.get(switch)]
    if not on:
        return de_en("Findet nicht statt und ist nicht geplant (auf diesem Server).",
                     "Does not take place and is not planned (on this server).")
    return de_en("Findet statt: {names} (USA), Grundlage siehe Abschnitt 5.",
                 "Takes place: {names} (USA), basis see section 5.", names=", ".join(on))


def overlay():
    """What the installation adds to this concept (INSTANCE_OVERLAY/privacy_concept.json, instance_overlay/README.md):
    {"activities": {key: {"catalog": text, "basis": text}}}, a text either a string or {"de": …, "en": …}. The public
    code names no operator, so catalogue names and the legal bases of a particular body come from there. {} without
    one or when it cannot be read."""
    import json

    folder = current_app.config.get("INSTANCE_OVERLAY") or ""
    path = os.path.join(folder, "privacy_concept.json")
    if not folder or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        current_app.logger.warning("privacy_concept: %s could not be read", path)
        return {}
    return data if isinstance(data, dict) else {}


def _text(value):
    """A text of the overlay: a string, or German and English."""
    if isinstance(value, dict):
        return de_en(str(value.get("de") or value.get("en") or ""), str(value.get("en") or value.get("de") or ""))
    return str(value or "")


def activities():
    """The record of processing activities (Art. 30 (1) a-g): one entry per activity, numbered, with the dates of
    ACTIVITY_META and what the installation adds (catalogue name, its legal basis; overlay())."""
    from app import brand

    own = overlay().get("activities") or {}
    rows = _activities()
    procedure = f"{brand.name()} ({brand.public_base()})"
    for n, (row, (key, since, changed)) in enumerate(zip(rows, ACTIVITY_META), start=1):
        extra = own.get(key) if isinstance(own.get(key), dict) else {}
        row.update(no=n, key=key, since=since, changed=changed, catalog=_text(extra.get("catalog")),
                   own_basis=_text(extra.get("basis")), procedure=procedure,
                   internal=de_en(*INTERNAL[key]), third_country=_third_country(key))
    return rows


def _activities():
    owner = de_en("Betreiber der Plattform", "Provider of the platform")
    researchers = de_en("Forschende der Befragung; der Betreiber verarbeitet im Auftrag (Art. 28, Nutzungsbedingungen "
                        "Abschnitt 2)", "Researchers of the survey; the provider of the platform processes on their behalf (Art. 28, terms "
                        "of use section 2)")
    consent = de_en("Einwilligung der Teilnehmenden (Art. 6 Abs. 1 lit. a, bei besonderen Kategorien Art. 9 Abs. 2 "
                    "lit. a DSGVO), eingeholt von den Forschenden", "Consent of the participants (Art. 6 (1) (a), for "
                    "special categories Art. 9 (2) (a) GDPR), obtained by the researchers")
    return [
        {"name": de_en("Konten und Anmeldung", "Accounts and sign-in"),
         "purpose": de_en("Zugang zur Plattform, Zuordnung von Befragungen, Schutz vor Missbrauch.",
                          "Access to the platform, assigning surveys, protection against misuse."),
         "subjects": de_en("Forschende, Admins, eingeladene Personen, Personen auf der Warteliste",
                             "Researchers, admins, invited people, people on the waiting list"),
         "data": de_en("ORCID iD, Name, E-Mail-Adresse, Passwort als scrypt-Hash, Geheimnis der "
                       "Zwei-Faktor-Anmeldung (verschlüsselt), Anmeldezeiten, grobe Browserbezeichnung der Sitzungen, "
                       "Zustimmung zu den Nutzungsbedingungen, Rollen und Gruppen, Organisationen (Heimat, Mitgliedschaft, Admin-Rolle), "
                       "freigegebene Befragungstypen, Nutzertyp mit Beginn und "
                       "Ende der Laufzeit, betreuende Person und Lehrveranstaltung, Einladungslink der Registrierung, "
                       "Nummer des Kürzels in Organisationen mit verborgenen Namen, "
                       "angeheftete und ausgeblendete Befragungen, "
                       "Bearbeitungsspuren; auf der Warteliste der Anmeldung ORCID iD, Name und freiwillig eine "
                       "E-Mail-Adresse für die Nachricht, dass ein Platz frei ist.",
                       "ORCID iD, name, email address, password as a scrypt hash, secret of the two-factor "
                       "sign-in (encrypted), sign-in times, coarse browser label of the sessions, acceptance of the terms "
                       "of use, roles and groups, organisations (home, membership, admin role), enabled survey types, user type with "
                       "start and end of the period, supervising person "
                       "and course, invitation link of the registration, number of the label in organisations with hidden "
                       "names, pinned and hidden surveys, editing traces; on the waiting list of the "
                       "sign-up ORCID iD, name and, optionally, an email address for the notice that a place is free."),
         "recipients": de_en("Admins; ORCID (Anmeldung); Mail-Relay und Slack, wenn eingeschaltet.",
                             "Admins; ORCID (sign-in); mail relay and Slack when switched on."),
         "deletion": de_en("Bis das Konto gelöscht wird (selbst oder durch Admins); danach bleiben nur Nummer, interne "
                           "Kennung und Datum; Mitgliedschaften, Heimat und Admin-Rolle in Organisationen gehen mit dem "
                           "Konto. E-Mail-Adresse und Passwort-Hash auch, wenn die Person diese Anmeldung auf der "
                           "Seite „Konto › Profil und Anmeldung“ entfernt (nur solange ORCID verbunden ist); die ORCID iD, wenn die Person sie dort trennt (nur "
                           "mit Passwort). Vorgemerkte Konten (iD und Name einer Person, die vor ihrer ersten Anmeldung "
                           "hinzugefügt wurde): bis zur ersten Anmeldung, gelöscht vom stündlichen Lauf, sobald keine "
                           "Befragung, Gruppe, Organisation, Freigabe oder kein Adressbuch sie mehr nennt. Befristete Nutzertypen: Sperre am Ende der Laufzeit, Löschung nach der "
                           "Löschfrist des Typs (voreingestellt 6 Monate, Lehrveranstaltungen 3 Monate), wenn nichts zu "
                           "übergeben ist, sonst Entscheidung durch Admins. Vormerkung durch Admins der Organisation: "
                           "Konto sofort gesperrt, Löschung nach 30 Tagen durch den stündlichen Lauf; bis dahin bleiben "
                           "Vormerkung, vormerkende Person und Auswahl gespeichert, Übergaben ohne mögliche Person halten "
                           "die Löschung als „Löschung fällig“ an. Einladungsadressen einer Liste: bis zur "
                           "Annahme, zum Zurückziehen oder Ablauf, höchstens 30 Tage. Einladungslinks der Plattform (nur Prüfwert, "
                           "Notiz, Zähler): bis 30 Tage nach ihrem Ablauf. Warteliste: bis zur ersten "
                           "Anmeldung, höchstens 90 Tage; die E-Mail-Adresse, sobald die Nachricht verschickt ist.",
                           "Until the account is deleted (by the person or by admins); then only number, internal id and "
                           "date remain; memberships, home and admin role in organisations go with the account. Email "
                           "address and password hash also when the person removes this sign-in on the page “Account › Profile and sign-in” "
                           "(only while ORCID is connected); the ORCID iD when the person disconnects it there (only with a password). "
                           "Pending accounts (iD and name of a person added before their first sign-in): until the first "
                           "sign-in, deleted by the hourly sweep as soon as no survey, group, organisation, share or address book names "
                           "them. User types limited in time: blocked at the end of the period, deleted after the "
                           "type's deletion period (by default 6 months, courses 3 months) if nothing has to be handed "
                           "over, otherwise admins decide. Marked by organisation admins: blocked at once, deleted after "
                           "30 days by the hourly sweep; until then the mark, the person who marked it and the choices "
                           "are stored, and a handover without a person able to receive it holds the deletion as "
                           "“deletion due”. Invitation addresses of a list: until accepted, withdrawn or "
                           "expired, at most 30 days. Invitation links of the platform (hash, note, counter only): until 30 days after "
                           "they expire. Waiting list: until the first sign-in, at most 90 days; the email "
                           "address as soon as the notice is sent."),
         "controller": owner,
         "basis": de_en("Art. 6 Abs. 1 lit. b DSGVO (Nutzungsbedingungen); bei öffentlichen Stellen ggf. lit. e",
                        "Art. 6 (1) (b) GDPR (terms of use); for public bodies possibly (e)")},
        {"name": de_en("Teilnahme an Befragungen", "Taking part in surveys"),
         "purpose": de_en("Wissenschaftliche Datenerhebung und Auswertung der Forschenden.",
                          "Scientific data collection and analysis by the researchers."),
         "subjects": de_en("Teilnehmende; je nach Befragung auch Beschäftigte (Befragungen von Mitarbeitenden), "
                           "Studierende oder andere Gruppen, die die Forschenden einladen",
                           "Participants; depending on the survey also employees (staff surveys), students or other "
                           "groups the researchers invite"),
         "data": de_en("Antworten mit Freitexten (je nach Fragen auch besondere Kategorien nach Art. 9, z. B. das "
                       "Geburtsland der Eltern aus den demografischen Standardfragen als Hinweis auf die ethnische "
                       "Herkunft; Geburtsmonat und -jahr machen mit Bundesland, Haushaltsgröße oder Beruf Personen in "
                       "kleinen Stichproben erkennbar, die Konstruktkarten sagen das), Zeitpunkte, "
                       "Sprache der Teilnahme mit Wechseln unterwegs; je nach Einstellung: grobe Geräteangaben in Klassen, Seitenzeiten, Fensterwechsel und Einfügen "
                       "(nur Anzahl), URL-Parameter (z. B. Panel-Kennung), Land und Bundesland aus der IP-Adresse (die "
                       "Adresse selbst wird nicht gespeichert), Standort in gewählter Genauigkeit, Sprachaufnahmen, Nachrichten eines KI-Chats mit den Antworten der KI, "
                       "Zeichnungen, Abspielprotokolle von Videos, Werte aus externen Abschnitten; Hash des Löschcodes; "
                       "Sitzungs-Cookie. Unvollständige Mehrfachauswahlen werden bei Zurück oder Sprachwechsel als "
                       "Entwurf für die erneute Anzeige gespeichert, nicht ausgewertet und beim Abschluss oder Ablehnen entfernt.",
                       "Answers with free texts (depending on the questions also special categories under Art. 9, e.g. "
                       "the parents' country of birth from the demographic standard questions as a hint at ethnic "
                       "origin; month and year of birth together with federal state, household size or occupation make "
                       "people recognisable in small samples, the construct cards say so), "
                       "times, language of the participation with changes on the way; depending on the settings: coarse device classes, page times, leaving the window and "
                       "pasting (counts only), URL parameters (e.g. a panel id), country and federal state from the IP "
                       "address (the address itself is not stored), location at the chosen precision, voice recordings, "
                       "the messages of an AI chat with the answers of the AI, "
                       "drawings, playback logs of videos, values from external sections; hash of the deletion code; "
                       "session cookie. Incomplete multiple selections are stored as drafts on Back or language changes, "
                       "restored for display, excluded from analysis and removed on completion or refusal."),
         "recipients": de_en("Beteiligte nach Rolle (Rohdaten nur Bearbeiten und Verwalten); Dashboard-Links nur "
                             "zusammengefasst; externe Dienste nur, wenn die Befragung sie nutzt (Abschnitt 5). Wellen "
                             "eines Längsschnitts werden über den persönlichen Code verknüpft: im Export nur mit "
                             "Bearbeiten in jeder Welle, auf der Ergebnisseite zusammengefasst nur mit Leserecht in jeder "
                             "Welle; beides steht im Zugriffsprotokoll.",
                             "Members by role (raw data only Edit and Manage); dashboard links aggregated only; outside "
                             "services only when the survey uses them (section 5). Waves of a longitudinal study are "
                             "linked by the personal code: in the export only with Edit in every wave, on the results "
                             "page aggregated only with read access to every wave; both are in the access log."),
         "deletion": de_en("Je Befragung ab Feldende: Kennungen und Kontaktangaben (Voreinstellung 6 Monate), alle "
                           "Antworten (Voreinstellung 10 Jahre); vorher jederzeit durch die Teilnehmenden (15 Minuten nach Ende im "
                           "Browser, danach mit Löschcode, sofern die Befragung einen vergibt) oder die Forschenden.",
                           "Per survey from the end of fieldwork: identifiers and contact data (default 6 months), all "
                           "answers (default 10 years); earlier at any time by the participants (15 minutes after completion in the browser, "
                           "then with the deletion code, if the survey issues one) or the researchers."),
         "controller": researchers + " " + de_en(
             "Führen Forschende mehrerer Einrichtungen eine Befragung gemeinsam durch, sind die Einrichtungen gemeinsam "
             "Verantwortliche (Art. 26 DSGVO) und legen in einer Vereinbarung fest, wer welche Pflichten erfüllt.",
             "If researchers of several institutions run a survey together, the institutions are joint controllers "
             "(Art. 26 GDPR) and set out in an arrangement who fulfils which obligations."),
         "basis": consent},
        {"name": de_en("Gemeinsame Auswertung validierter Konstrukte", "Pooled analysis of validated constructs"),
         "purpose": de_en("Metaanalysen über Befragungen hinweg.", "Meta-analyses across surveys."),
         "subjects": de_en("Teilnehmende von Befragungen mit Konstrukten", "Participants of surveys with constructs"),
         "data": de_en("Antworten auf die Konstrukte und die Standardfragen Alter und Geschlecht, Sprache, Monat, "
                       "Version; mit Ersatzkennungen (eigener Schlüssel POOL_KEY). Nicht die demografischen "
                       "Standardfragen nach RatSWD (Bildung, Einkommen, Herkunft usw.; constructs.pooled).",
                       "Answers to the constructs and the standard questions age and gender, language, month, version; "
                       "with substitute identifiers (own key POOL_KEY). Not the demographic standard questions after "
                       "RatSWD (education, income, origin etc.; constructs.pooled)."),
         "recipients": de_en("Admins (Reiter Konstrukte); veröffentlicht nur zusammengefasst.",
                             "Admins (tab Constructs); published only aggregated."),
         "deletion": de_en("Zusammengeführte Dateien höchstens 10 Jahre; gelöschte Antworten fallen heraus.",
                           "Merged files at most 10 years; deleted answers drop out."),
         "controller": owner,
         "basis": de_en("Einwilligung (Art. 6 Abs. 1 lit. a DSGVO), fester Absatz der Einwilligung",
                        "Consent (Art. 6 (1) (a) GDPR), fixed paragraph of the consent")},
        {"name": de_en("Live-Sitzungen", "Live sessions"),
         "purpose": de_en("Abstimmungen und Fragen in Veranstaltungen.", "Votes and questions in events."),
         "subjects": de_en("Teilnehmende einer Live-Sitzung, meist Studierende einer Lehrveranstaltung oder Publikum "
                           "einer Veranstaltung", "Participants of a live session, mostly students of a course or the "
                           "audience of an event"),
         "data": de_en("Stimmen, Geräte-Cookie (vom Server vergebene, signierte Gerätekennung, auch im lokalen Speicher des Browsers), auf Wunsch ein Spitzname (mit der Teilnahme gespeichert, nie im Export); "
                       "Fragen im Fragenkanal ohne Personenbezug, mit der Folie beim Absenden; Emoji-Reaktionen als "
                       "Zähler je Folie (frühere „Unklar“-Klicks zählen als 😕); eigene Klicks und Stimmen in befragungsbezogenen serverseitigen Gerätemarken (höchstens sieben Tage). "
                       "Stiftnotizen der Vortragenden auf Präsentationsfolien (Striche mit Stiftdruck je Durchgang und Folie, "
                       "ohne Handschrifterkennung; der Laserpunkt nur für zwei Sekunden): Inhalt der Forschenden, keine Daten "
                       "der Teilnehmenden.",
                       "Votes, device cookie (a device identifier issued and signed by the server, also in the browser's local storage), a nickname if wished (stored with the participation, never in the export); "
                       "questions in the Q&A without reference to a person, with the slide at sending; emoji reactions "
                       "as counts per slide (former “Unclear” taps count as 😕); own taps and votes in server-side device receipts scoped to a survey (at most seven days). "
                       "The presenter's pen notes on presentation slides (strokes with pen pressure per run and slide, "
                       "no handwriting recognition; the laser dot for two seconds only): the researchers' content, no data "
                       "of participants."),
         "recipients": de_en("Beteiligte nach Rolle; Ergebnisse auf dem Präsentationsbildschirm.",
                             "Members by role; results on the presentation screen."),
         "deletion": de_en("Wie Teilnahmen der Befragung, mit der Frist für die Antworten (gezählt ab der letzten Teilnahme, "
                            "weil eine Live-Abstimmung nie geschlossen wird; Einstellungen › Datenschutz und Aufbewahrung) auch die "
                            "Fragen aus dem Publikum, Reaktionen je Folie; serverseitige Gerätemarken höchstens sieben Tage, Beispielvorschauen "
                            "getrennt von gespeicherten Testdurchläufen eine Stunde, Netzwerkmarken für neue Teilnahmen zwei Stunden. "
                            "Zuweisungszähler in Paradaten identifizieren keine Person und sind kein Betrugsnachweis. "
                            "Stiftnotizen gehen mit ihrem Durchgang („Löschen“ beim Durchgang, „Alles zurück“) und mit der Abstimmung.",
                            "As participations of the survey, with the period for the answers (counted from the last "
                            "participation, as a live poll is never closed; Settings › Data protection and retention) also the "
                            "questions from the audience and reactions per slide; server-side device receipts at most seven days, example "
                            "previews separately from stored test runs one hour, network receipts for new participations two hours. "
                            "Assignment counts in paradata do not identify a person or prove fraud. "
                            "Pen notes go with their run (“Delete” at the run, “Start over”) and with the poll."),
         "controller": researchers, "basis": consent},
        {"name": de_en("Interviews eines Interviewleitfadens", "Interviews of an interview guide"),
         "purpose": de_en("Qualitative Forschung der Forschenden.", "Qualitative research by the researchers."),
         "subjects": de_en("Interviewte Personen", "Interviewees"),
         "data": de_en("Kennung, Datum und Form, Art der Einwilligung, Notizen und Zeitmarken, Häkchen je Leitfrage (Aspekte, "
                       "gestellte Fragen, ob eine konkrete Frage zutraf), Ergebnisse von Aufgaben im Gespräch (Rangfolge, "
                       "Kartensortierung, Bewertung, Bildauswahl; mit der Zeit der Interviewuhr, mit dem Teilnehmerbildschirm "
                       "auch, dass es dort gegeben und ob es von den Interviewenden geändert wurde, und die Zeit vom Zeigen bis "
                       "zum Ergebnis), Kurzfragebogen, "
                       "Tonaufnahme (im Interview aufgenommen, auf Wunsch mit dem Ton eines geteilten Tabs oder Bildschirms der Videokonferenz, oder aus einer Videokonferenz hochgeladen; nur Ton, mit Herkunft und "
                       "Sprecher je Datei), Transkript mit Name der Person, die es als geprüft markiert, und Transkriptkopf "
                       "(Ort der Aufnahme und Angaben zur interviewten Person aus dem Kurzfragebogen, für den Export als "
                       "Word und PDF; nur Forschende exportieren), Bestätigung, dass alle Sprechenden eingewilligt haben. Nicht anonym.",
                       "Identifier, date and form, kind of consent, notes and time marks, ticks per guide question (aspects, "
                       "questions asked, whether a specific question applied), results of tasks in the conversation (ranking, "
                       "card sorting, rating, image choice; with the time on the interview clock, with the participant screen "
                       "also that it was given there and whether the interviewers changed it, and the time from showing it to "
                       "the result), short questionnaire, audio "
                       "recording (recorded in the interview, if wished with the sound of a shared tab or screen of the video conference, or uploaded from a video conference; sound only, with origin and speaker "
                       "per file), transcript with the name of the person who marks it as checked, and transcript head "
                       "(place of recording and details of the interviewee from the short questionnaire, for the export "
                       "as Word and PDF; only researchers export), confirmation that everyone who speaks consented. Not anonymous."),
         "recipients": de_en("Beteiligte mit Bearbeiten oder Verwalten; die Interviewenden des jeweiligen Interviews "
                             "(Interviewlink) bis zur Übergabe; Transkriptionsserver (Whisper), wenn eingeschaltet.",
                             "Members with Edit or Manage; the interviewers of the respective interview (interviewer link) "
                             "until it is handed over; transcription server (Whisper) when switched on."),
         "deletion": de_en("Ab Ende des Interviews: Tonaufnahme nach eigener Frist (Voreinstellung: bis das Transkript "
                           "geprüft ist), übrige Angaben nach der Frist des Leitfadens (Voreinstellung 10 Jahre). Auf dem "
                           "Interviewgerät: Was den Server noch nicht erreicht hat (Tonstücke, Notizen, Antworten), bleibt im "
                           "lokalen Speicher des Browsers, bis der Server es bestätigt, auch über einen Verbindungsabbruch oder "
                           "einen geschlossenen Tab hinweg; danach wird es dort gelöscht. Für das Öffnen ohne Verbindung "
                           "(Service Worker) außerdem eine Kopie der Interviewseite mit den Notizen und der Stand des "
                           "Interviews bis zu seinem Ende, höchstens 12 Stunden nach der letzten Nutzung. Wartende Tonstücke "
                           "lassen sich auf Wunsch als Datei sichern; diese Datei verwaltet die Person auf ihrem Gerät selbst "
                           "und löscht sie nach dem Einlesen. Offline-Modus (je Leitfaden): Der Server "
                           "reserviert vorab Plätze (pid, Kennung, Löschcode) für ein Gerät, ohne Zeile in der Datenbank; "
                           "das Gerät hält die ohne Netz geführten Interviews mit einer PIN verschlüsselt (AES-GCM, "
                           "Schlüssel aus PBKDF2, ohne Generalschlüssel) bis zur Übertragung, höchstens sieben Tage; "
                           "danach verfallen Paket und unbenutzte Plätze. Ein Löschcode gilt schon vor der Übertragung.",
                           "From the end of the interview: recording after its own period (default: until the transcript "
                           "is checked), everything else after the guide's period (default 10 years). On the interview "
                           "device: what has not reached the server yet (audio pieces, notes, answers) stays in the browser's "
                           "local storage until the server confirms it, also across a lost connection or a closed tab; then "
                           "it is deleted there. For opening without a connection (service worker) also a copy of the "
                           "interview page with the notes and the state of the interview until it ends, at most 12 hours "
                           "after its last use. Waiting audio pieces can be saved as a file on request; the person manages "
                           "that file on their device and deletes it after reading it in. Offline mode (per "
                           "guide): the server reserves places (pid, identifier, deletion code) for one device in advance, "
                           "without a database row; the device keeps the interviews conducted without a network encrypted "
                           "with a PIN (AES-GCM, key from PBKDF2, no master key) until the transfer, at most seven days; then "
                           "the pack and unused places expire. A deletion code works even before the transfer."),
         "controller": researchers,
         "basis": de_en("Einwilligung der interviewten Person, eingeholt von den Forschenden; für eine Tonaufnahme die "
                        "Einwilligung aller Sprechenden (§ 201 StGB)",
                        "Consent of the interviewee, obtained by the researchers; for an audio recording the consent of "
                        "everyone who speaks (section 201 German Criminal Code)")},
        {"name": de_en("Interviewlinks", "Interviewer links"),
         "purpose": de_en("Interviews durch beauftragte Personen ohne Konto führen lassen.",
                          "Having interviews conducted by engaged people without an account."),
         "subjects": de_en("Interviewende ohne Konto", "Interviewers without an account"),
         "data": de_en("Name oder Kürzel (von den Forschenden eingetragen), Hash des Links, Art der Geräte, Zeit der ersten "
                       "und letzten Nutzung, bestätigte Fassung der Hinweise; Sitzungs-Cookie mit Zugang, Gerät und letzter "
                       "Anfrage; Fehlversuche wie andere Fehlversuche nur als Kurzwert der Adresse.",
                       "Name or initials (entered by the researchers), hash of the link, kind of devices, time of first and "
                       "last use, confirmed version of the notes; session cookie with access, device and last request; "
                       "failed attempts like other failed attempts only as a hash of the address."),
         "recipients": de_en("Beteiligte mit Bearbeiten oder Verwalten des Leitfadens.", "Members with Edit or Manage on the guide."),
         "deletion": de_en("Mit dem Löschen des Zugangs oder des Leitfadens; der Name bleibt in den Interviews als Angabe, "
                           "wer sie geführt hat. Sitzungen enden nach 60 Minuten ohne Aktivität.",
                           "With the deletion of the access or the guide; the name stays in the interviews as who conducted "
                           "them. Sessions end after 60 minutes without activity."),
         "controller": researchers,
         "basis": de_en("Durchführung des Forschungsvorhabens im Auftrag der Forschenden (Art. 6 Abs. 1 lit. b oder e DSGVO, "
                        "je nach Verhältnis zur Einrichtung)", "Carrying out the research project on behalf of the researchers "
                        "(Art. 6(1)(b) or (e) GDPR, depending on the relation to the institution)")},
        {"name": de_en("Teilnehmerbildschirm im Interview", "Participant screen in the interview"),
         "purpose": de_en("Die interviewte Person löst eine Aufgabe des Leitfadens oder füllt den Kurzfragebogen auf "
                          "ihrem eigenen Gerät aus, auch im Videogespräch.", "The interviewee solves a task of the guide or "
                          "fills in the short questionnaire on their own device, also in a video interview."),
         "subjects": de_en("Interviewte Personen", "Interviewees"),
         "data": de_en("Im Interview (Response.interview): Hash des Links, Zeit seiner Erzeugung, Konto, für das er erzeugt "
                       "wurde, eine zufällige Gerätekennung der Verbindung, Zeit der letzten Anfrage des Geräts, die "
                       "freigegebene Aufgabe oder der Kurzfragebogen; das Ergebnis und die Antworten wie im Interview eingetragene, mit "
                       "dem Vermerk, dass sie vom Teilnehmerbildschirm kamen. Im Browser des Geräts ein "
                       "technisch notwendiges Sitzungs-Cookie mit Interview, Gerätekennung und Anfang des Hashs. Keine "
                       "Angaben zum Gerät, keine IP-Adresse; Fehlversuche mit einem Link und gesendete Ergebnisse je Absender "
                       "nur als Kurzwert der Adresse (Ratenbegrenzung); im Server-Log Öffnen und Abweisen eines Links ohne "
                       "Link und ohne Adresse.",
                       "In the interview (Response.interview): hash of the link, time it was made, account it was made "
                       "for, a random device identifier of the connection, time of the device's last request, the task "
                       "or the short questionnaire released; the result and the answers like those entered in the interview, with "
                       "the note that they came from the participant screen. In the device's browser a technically "
                       "necessary session cookie with interview, device identifier and the start of the hash. No details "
                       "of the device, no IP address; failed attempts with a link and results sent per sender only as a "
                       "hash of the address (rate limit); in the server log opening and refusing a link, without the link "
                       "and without an address."),
         "recipients": de_en("Wie die Interviews: Beteiligte mit Bearbeiten oder Verwalten; die Interviewenden des "
                             "jeweiligen Interviews.", "As the interviews: members with Edit or Manage; the interviewers of "
                             "the respective interview."),
         "deletion": de_en("Die Verbindung endet mit dem Interview, beim Trennen, mit einem neuen Link und spätestens nach "
                           "6 Stunden (ungeöffnet nach 30 Minuten); ihre Angaben gehen beim Beenden des Interviews oder "
                           "Trennen. Das Ergebnis geht mit dem Interview (Frist des Leitfadens, Löschcode).",
                           "The connection ends with the interview, on disconnecting, with a new link and after 6 hours at "
                           "the latest (unopened after 30 minutes); its details go when the interview is ended or the screen "
                           "disconnected. The result goes with the interview (the guide's period, deletion code)."),
         "controller": researchers,
         "basis": de_en("Einwilligung der interviewten Person, eingeholt von den Forschenden (die Vorlage „Information und "
                        "Einwilligung“ nennt den Teilnehmerbildschirm)", "Consent of the interviewee, obtained by the "
                        "researchers (the template “Information and consent” names the participant screen)")},
        {"name": de_en("Tagebuchstudien mit der App", "Diary surveys with the app"),
         "purpose": de_en("Wiederholte kurze Befragungen im Alltag (Experience Sampling) der Forschenden.",
                          "Repeated short surveys in daily life (experience sampling) by the researchers."),
         "subjects": de_en("Teilnehmende einer Tagebuchstudie", "Participants of a diary survey"),
         "data": de_en("Auf dem Telefon zusätzlich Studienbeschreibung, Studienkontakt, Übertragungsstatus und begrenzte Eingabeentwürfe im Android-Zwischenzustand (keine Cloud-Sicherung); Mikrofon/Standort nur mit Freigabe bei entsprechender Frage, keine Hintergrundaufnahme. Antworten je Eintrag, verbunden über eine zufällige Personenkennung (pseudonym); Teil, Nummer und "
                       "Tag des Eintrags, Zeitpunkt der Abfrage und der Benachrichtigung, ob nach einer Erinnerung oder "
                       "ohne Verbindung beantwortet, Beginn und Ende (ohne Verbindung: Angaben des Telefons), Eingang "
                       "beim Server, eine zufällige Kennung der Antwort aus der App (gegen doppeltes Speichern), "
                       "Zeitzone; ohne Verbindung beantwortete Einträge "
                       "liegen bis zur Speicherbestätigung oder zum Entfernen der Studie in der App; Ruhezeiten der Person für zufällige Abfragen, Pausen, die die Forschenden einlegen, mit dem Zeitpunkt des Fortsetzens; zur "
                       "Person Zeitraum, Stand, Zeitpunkt des Verlassens oder des Beendens durch die Forschenden, Sprache, Zeitzonen mit dem Tag des Wechsels, "
                       "letzter Abgleich, letzter Abruf der Seiten für Antworten ohne Verbindung und der Hash ihres "
                       "App-Schlüssels; bei persönlichen Codes, welcher Code zu welcher Personenkennung gehört, die "
                       "Freigabe zum Wiederverbinden und der Zeitpunkt des letzten Wiederverbindens. Keine "
                       "Gerätekennung. Personenbezogene Inhalte richten sich nach den Fragen und der Einwilligung der Studie. Personenbezogene Zugangsrevision und Prüfwerte von "
                       "Fragebogenlinks; die Links sind zwölf Stunden gültig, abgelaufene Prüfwerte werden beim nächsten "
                       "Wartungslauf gelöscht. Personenbezogene Offline-Seitenbelege mit Fassung und "
                       "Zufallsreihenfolge für 30 Tage; Originalfelder ungültiger oder inkompatibler Antworten zur Prüfung "
                       "durch Bearbeitende, bei Korrekturen mit Begründung, Zeitpunkt und Bearbeitendenkennung.",
                       "On the phone also study summary/contact, transfer status and bounded drafts in Android saved instance state (no cloud backup); microphone/location only with permission for a relevant question, no background recording. Answers per entry, linked by a random person identifier (pseudonymous); part, number and day of the "
                       "entry, time of the prompt and of the notification, whether answered after a reminder or without "
                       "a connection, start and end (without a connection: what the phone says), arrival at the server, "
                       "a random identifier of the answer from the app (against storing it twice), time zone; entries "
                       "answered without a connection stay in the app until "
                       "confirmed as stored or the study is removed; the person's quiet hours for random prompts, pauses set by the researchers with the moment of resuming; for the person the "
                       "period, status, when they left or the researchers ended their study, language, time zones with the day of each change, last "
                       "synchronisation, last fetch of the pages for answers without a connection and the hash of their "
                       "app key; with personal codes, which code belongs to which person identifier, the release to "
                       "connect again and when they last connected again. No device identifier. Personal content depends on the questions and the study consent. Person-scoped access revision and hashes of questionnaire links; links are valid for twelve "
                       "hours and expired hashes are deleted during the next maintenance run. Person-scoped offline page "
                       "receipts with version and random order for 30 days; original fields "
                       "of invalid or incompatible submissions for editor review, corrections with clarification, time and editor ID."),
         "recipients": de_en("Beteiligte nach Rolle; veröffentlichte Daten nur mit neu vergebener Personennummer.",
                             "Members by role; published data only with a newly assigned person number."),
         "deletion": de_en("Wie Teilnahmen der Befragung; „Meine Antworten löschen“ in der App und der Löschcode löschen "
                           "alle Einträge der Person, auch ihre Zeilen in gesicherten Exportständen; nie beendete Beitritte nach 7 Tagen auf "
                           "demselben Weg.",
                           "As participations of the survey; “Delete my answers” in the app and the deletion code delete "
                           "all entries of the person, also their rows in saved export states; joins never finished after 7 days in the same "
                           "way."),
         "controller": researchers, "basis": consent},
        {"name": de_en("Einladungen und getrennte Kontaktangaben", "Invitations and separate contact data"),
         "purpose": de_en("Einladen per E-Mail; Kontaktangaben z. B. für Verlosungen getrennt von den Antworten.",
                          "Inviting by email; contact data e.g. for prize draws kept apart from the answers."),
         "subjects": de_en("Eingeladene Personen, Teilnehmende", "Invited people, participants"),
         "data": de_en("E-Mail-Adresse und Name der Einladung (nach Abmeldung nur ein gesalzener Hash); Angaben des "
                       "Kontaktabschnitts ohne Verbindung zur Teilnahme, ohne Zeitstempel.",
                       "Email address and name of the invitation (after opting out only a salted hash); data of the "
                       "contact section without a link to the participation, without a timestamp."),
         "recipients": de_en("Beteiligte mit Verwalten; Mail-Relay.", "Members with Manage; mail relay."),
         "deletion": de_en("Mit der Frist für Kennungen und Kontaktangaben; Postausgang nach dem Versand geleert.",
                           "With the period for identifiers and contact data; outbox emptied after sending."),
         "controller": researchers, "basis": consent},
        {"name": de_en("Papierfragebögen (Scans)", "Paper questionnaires (scans)"),
         "purpose": de_en("Auf Papier ausgefüllte Fragebögen einlesen und als Teilnahmen übernehmen.",
                          "Reading questionnaires filled in on paper and taking them over as participations."),
         "subjects": de_en("Teilnehmende auf Papier", "Participants on paper"),
         "data": de_en("Scans der Bögen (Graustufen, ohne Metadaten) mit Kreuzen und Handschrift, daraus gelesene und "
                       "abgetippte Antworten, zufällige Bogennummer; keine Namen (der Bogen bittet darum).",
                       "Scans of the sheets (greyscale, without metadata) with ticks and handwriting, the answers read "
                       "and typed in from them, a random sheet number; no names (the sheet asks for that)."),
         "recipients": de_en("Beteiligte mit Bearbeiten oder Verwalten; die Kreuze erkennt der Server selbst. Nur "
                             "wenn Forschende die Handschrift von der KI lesen lassen: Ausschnitte der Schreibfelder nur über "
                             "ihren eigenen KI-Zugang, nie über den Schlüssel der Plattform, an "
                             "den KI-Dienst (KI-Funktionen).",
                             "Members with Edit or Manage; the server reads the ticks itself. Only when researchers have "
                             "the AI read the handwriting: sections of the writing fields, only over their own AI access and "
                             "never over the platform's key, to the AI service (AI "
                             "functions)."),
         "deletion": de_en("Scans nach Wahl der Forschenden sofort, 30 Tage (Voreinstellung) nach der Übernahme oder "
                           "mit den Antworten; außerdem mit der Teilnahme (Löschanfrage), mit beiden Fristen der "
                           "Befragung und mit der Befragung. Seiten ohne Bogen nach 7 Tagen. Die Papierbögen verwahren "
                           "und vernichten die Forschenden.",
                           "Scans as the researchers choose: at once, 30 days (default) after the takeover or with the "
                           "answers; also with the participation (deletion request), with both periods of the survey "
                           "and with the survey. Pages without a sheet after 7 days. The researchers keep and destroy "
                           "the paper sheets."),
         "controller": researchers, "basis": consent},
        {"name": de_en("Kommentare in Vorschau und Pretest", "Comments in preview and pretest"),
         "purpose": de_en("Fragebogen vor dem Feld prüfen.", "Checking the questionnaire before fieldwork."),
         "subjects": de_en("Testende, Forschende", "Testers, researchers"),
         "data": de_en("Kommentartext, Name falls angegeben, Bildschirmfoto (Metadaten entfernt), grobe Angaben zur "
                       "Umgebung.", "Comment text, name if given, screenshot (metadata removed), coarse environment "
                       "information."),
         "recipients": de_en("Beteiligte der Befragung.", "Members of the survey."),
         "deletion": de_en("Mit der Befragung oder durch die Forschenden.", "With the survey or by the researchers."),
         "controller": researchers,
         "basis": de_en("Berechtigtes Interesse oder Einwilligung der Testenden, festzulegen von den Forschenden",
                        "Legitimate interest or consent of the testers, to be set by the researchers")},
        {"name": de_en("Rückmeldungen und Beschwerden", "Feedback and complaints"),
         "purpose": de_en("Fehler beheben, Beschwerden von Teilnehmenden bearbeiten.",
                          "Fixing errors, handling complaints of participants."),
         "subjects": de_en("Forschende, Teilnehmende", "Researchers, participants"),
         "data": de_en("Text, Seite, Konto der meldenden Person; bei Beschwerden Befragung, freiwillig Name und E-Mail-"
                       "Adresse, die Entscheidung der Admins mit Begründung.",
                       "Text, page, account of the reporting person; for complaints the survey, voluntarily name and "
                       "email address, the admins' decision with its reasons."),
         "recipients": de_en("Admins; Slack (ohne Auszug aus Beschwerden; aus Rückmeldungen nur in der ausführlichen Form), wenn eingeschaltet; bei Beschwerden per E-Mail Eingangsbestätigung "
                             "und Entscheidung an die meldende Person, die Entscheidung über eine Maßnahme an die Admins "
                             "der Befragung (Art. 16, 17 DSA).",
                             "Admins; Slack (no excerpt of complaints; of feedback only in the detailed form) when switched on; for complaints by email the confirmation of "
                             "receipt and the decision to the reporting person, a decision on a measure to the admins of "
                             "the survey (Art. 16, 17 DSA)."),
         "deletion": de_en("Keine automatische Frist (siehe offene Punkte).", "No automatic period (see open points)."),
         "controller": owner,
         "basis": de_en("Art. 6 Abs. 1 lit. b, c oder e DSGVO, vom Verantwortlichen festzulegen",
                        "Art. 6 (1) (b), (c) or (e) GDPR, to be set by the controller")},
        {"name": de_en("Protokoll datenschutzrelevanter Handlungen", "Log of data-protection actions"),
         "purpose": de_en("Nachweis, wer Rohdaten geladen, Zugriffe geändert oder gelöscht hat.",
                          "Proof of who downloaded raw data, changed access or deleted."),
         "subjects": de_en("Forschende, Admins", "Researchers, admins"),
         "data": de_en("Person, Befragungscode, Aktion, Angaben, Zeit; ohne Antworten und Kontaktdaten.",
                       "Person, survey code, action, details, time; without answers and contact data."),
         "recipients": de_en("Admins.", "Admins."),
         "deletion": de_en("Nach 365 Tagen.", "After 365 days."),
         "controller": owner,
         "basis": de_en("Art. 6 Abs. 1 lit. c oder e i. V. m. Art. 5 Abs. 2 und Art. 32 DSGVO",
                        "Art. 6 (1) (c) or (e) with Art. 5 (2) and Art. 32 GDPR")},
        {"name": de_en("KI-Funktionen", "AI functions"),
         "purpose": de_en("Texte, Übersetzungen, Prüfungen, Bilder und Vorschläge für Forschende.",
                          "Texts, translations, reviews, images and suggestions for researchers."),
         "subjects": de_en("Forschende; bei der KI-Kodierhilfe und beim KI-Lesen von Papierbögen Teilnehmende",
                           "Researchers; for the AI coding help and the AI reading of paper sheets participants"),
         "data": de_en("Eingaben der Forschenden (Fragebogentexte, Ideen, Chat); bei der KI-Kodierhilfe Freitextantworten "
                       "(höchstens 150 je Aufruf, je 300 Zeichen, nur echte Teilnahmen); beim KI-Lesen von "
                       "Papierbögen Bildausschnitte der Schreibfelder eines Bogens (ohne Bogennummer, nur nach "
                       "Bestätigung, dass die Teilnehmenden informiert wurden); Verbrauch je Konto und Tag; "
                       "Protokoll der Aufrufe (Zweck, Modell, Tokens, Erfolg, Person, ohne Inhalte) für die Seiten "
                       "„KI-Nutzung aller Konten“ und „KI-Nutzung“; Schalter „Generative KI nutzen“ je Konto; "
                       "eigener KI-Zugang: Anbieter, Adresse, Modell, Schlüssel (verschlüsselt).",
                       "Researchers' input (questionnaire texts, ideas, chat); for the AI coding help free-text answers "
                       "(at most 150 per call, 300 characters each, real participations only); for the AI reading of "
                       "paper sheets image sections of the writing fields of one sheet (without the sheet number, only "
                       "after confirming that the participants were told); usage per account and "
                       "day; log of the calls (purpose, model, tokens, success, person, no content) for the pages "
                       "“AI consumption” and “AI usage”; switch “Use generative AI” per account; "
                       "own AI access: provider, address, model, key (encrypted)."),
         "recipients": de_en("KI-Anbieter (Abschnitt 5), ohne Speicherung beim Anbieter für späteren Abruf (store=False).",
                             "AI provider (section 5), without storage at the provider for later retrieval (store=False)."),
         "deletion": de_en("Verbrauchsstatistik nach 730 Tagen, der Bezug zur Person mit dem Konto; Ergebnisse mit der "
                           "Befragung; eigener KI-Zugang mit „Entfernen“ oder mit dem Konto.", "Usage statistics after 730 "
                           "days, the link to the person with the account; results with the survey; own AI access with "
                           "“Remove” or with the account."),
         "controller": de_en("Betreiber; für Antworttexte der KI-Kodierhilfe die Forschenden, der KI-Anbieter als "
                             "weiterer Auftragsverarbeiter", "Provider of the platform; for answer texts of the AI coding help the "
                             "researchers, the AI provider as a further processor"),
         "basis": de_en("Konten: wie Konten; Antworttexte: Einwilligung, die Forschenden bestätigen die Information",
                        "Accounts: as accounts; answer texts: consent, the researchers confirm the information")},
        {"name": de_en("Agenten-Schnittstelle (experimentell)", "Agent interface (experimental)"),
         "purpose": de_en("Forschende lassen einen selbst gewählten KI-Agenten (etwa Claude Code) ihre Fragebögen lesen "
                          "und Entwürfe bearbeiten (MCP, app/mcp.py).",
                          "Researchers let an AI agent of their choice (such as Claude Code) read their questionnaires "
                          "and edit drafts (MCP, app/mcp.py)."),
         "subjects": de_en("Forschende", "Researchers"),
         "data": de_en("Token je Person nur als Hash, Name, Umfang (lesen oder Entwürfe bearbeiten), gewählte "
                       "Befragungen, Ablauf, letzte Nutzung; Konto-ID; Fragebogentexte, die der Agent abruft oder "
                       "ändert; Bearbeitungsspuren (Verlauf, Anwesenheit im Editor); Protokolleintrag je Aufruf. Keine "
                       "Daten von Teilnehmenden.",
                       "Token per person as a hash only, name, scope (read or edit drafts), chosen surveys, expiry, last "
                       "use; account id; questionnaire texts the agent fetches or changes; editing traces (history, "
                       "presence in the editor); a log entry per call. No participants' data."),
         "recipients": de_en("Der Agent der Person und der KI-Anbieter, den sie für ihn wählt, auf ihre Veranlassung; "
                             "auf Seiten der Plattform kein Auftragsverarbeiter, die Plattform schickt nichts an ein "
                             "Modell.", "The person's agent and the AI provider they choose for it, at their request; no "
                             "processor on the platform's side, the platform sends nothing to a model."),
         "deletion": de_en("Token mit Widerruf, 30 Tage nach Ablauf (90 Tage), bei Passwortwechsel oder mit dem Konto; "
                           "Protokoll nach 365 Tagen; Verlauf nach 7 Tagen.", "Token when revoked, 30 days after expiry "
                           "(90 days), on a password change or with the account; log after 365 days; history after 7 "
                           "days."),
         "controller": owner,
         "basis": de_en("Art. 6 Abs. 1 lit. b DSGVO (Nutzungsbedingungen Abschnitt 6)",
                        "Art. 6 (1) (b) GDPR (terms of use section 6)")},
        {"name": de_en("Ablage in Forschungsdatenarchiven", "Deposit in research data archives"),
         "purpose": de_en("Veröffentlichung und Archivierung auf Veranlassung der Forschenden.",
                          "Publication and archiving at the researchers' request."),
         "subjects": de_en("Teilnehmende, Forschende", "Participants, researchers"),
         "data": de_en("Exportierte Daten und Metadaten, wie die Forschenden sie wählen.",
                       "Exported data and metadata as the researchers choose them."),
         "recipients": de_en("Zenodo, Coscine.", "Zenodo, Coscine."),
         "deletion": de_en("Nach den Regeln des Archivs; auf der Plattform wie die Befragung.",
                           "By the archive's rules; on the platform as the survey."),
         "controller": de_en("Forschende", "Researchers"), "basis": consent},
        {"name": de_en("Betrieb, Sicherungen und Protokolle des Servers", "Operation, backups and server logs"),
         "purpose": de_en("Verfügbarkeit, Wiederherstellung, Fehlersuche, Schutz vor Überlastung.",
                          "Availability, recovery, troubleshooting, protection against overload."),
         "subjects": de_en("Alle", "Everyone"),
         "data": de_en("Sicherungen der Datenbank und der hochgeladenen Dateien (alle Daten); Webserver-Protokoll nur fehlgeschlagener Anfragen der "
                       "Forschendenseite ohne Adresse und ohne Parameter, bei Adressen mit Zugangsschlüssel (Passwort-, "
                       "Einladungs-, Beitritts-, Bestätigungs- und Löschlinks) ohne den Schlüssel, keines der Teilnahmeseite; Ratenbegrenzung mit "
                       "gesalzenem Hash der IP-Adresse im Arbeitsspeicher, für Codes, Anmeldung, Tokens und Stimmen einer Live-Abstimmung je Netz und Frage sowie gestartete Schätzungen je Konto (Kurzwert der Kontonummer, eine Stunde) in der Datenbank "
                       "(Tabelle rate_hit, Schlüssel wechselt täglich), für neue Teilnahmen, Live-Stimmen, Beschwerden, den Umfang von Sprachaufnahmen (IPv6 auch je /48) "
                       "und Eingangsbestätigungen je E-Mail-Adresse ebenso (Tabelle rate_count), in nginx je Adresse nur im Arbeitsspeicher; "
                       "Sicherheitsprotokoll in der Containerausgabe (Anmeldungen, Fehlversuche, Sperren, Passwort- und "
                       "Faktoränderungen, Einmal-Links, Einladungen, Beitrittslinks, Ratenbegrenzungen je Minute) mit "
                       "Kontonummer und täglich wechselndem Kurzwert des Netzes, ohne IP-Adresse, Passwörter oder Inhalte; dasselbe Protokoll "
                       "zusätzlich in Tagesdateien im Datenordner des Servers, 90 Tage aufbewahrt.",
                       "Backups of the database and of the uploaded files (all data); web server log of failed requests of the researchers' side only, "
                       "without address and without parameters, for addresses with an access key (password, invitation, join, "
                       "confirmation and deletion links) without the key, none of the participants' side; rate limits with a salted "
                       "hash of the IP address in memory, for codes, sign-in, tokens and votes of a live vote per network and question and estimations started per account (hash of the account number, one hour) in the database (table rate_hit, key "
                       "changes daily), likewise for new participations, live votes, complaints, the size of voice recordings (IPv6 also per /48) and confirmations of "
                       "receipt per email address (table rate_count), in nginx per address in memory only; security log in the container output (sign-ins, "
                       "failed attempts, locks, changes of password and factor, one-time links, invitations, join links, "
                       "rate limits per minute) with the account number and a daily changing hash of the network, without "
                       "IP address, passwords or contents; the same log also in daily files in the server's data folder, kept "
                       "90 days."),
         "recipients": de_en("Betreibende des Servers.", "The people who run the server."),
         "deletion": de_en("Docker-Betrieb: Sicherungen alle vier Stunden, 14 Tage lang; 5 vor Aktualisierungen (höchstens 30 Tage); "
                           "Containerausgabe (auch das Sicherheitsprotokoll) höchstens 3 × 20 MB; Hash-Salz bei jedem "
                           "Neustart neu; Zählerzeilen der Ratenbegrenzung nach etwa einer Stunde (rate_hit) bzw. einem Tag "
                           "(rate_count).",
                           "Docker operation: backups every four hours, kept 14 days; 5 before updates (at most 30 days); container output at "
                           "most 3 × 20 MB (the security log too); hash salt new at every restart; counter rows of the rate "
                           "limits after about an hour (rate_hit) or a day (rate_count)."),
         "controller": owner,
         "basis": de_en("Art. 6 Abs. 1 lit. c oder e i. V. m. Art. 32 DSGVO", "Art. 6 (1) (c) or (e) with Art. 32 GDPR")},
    ]


# which field of section 1 holds the data processing agreement of a service (T5); the others need none or have their own
AVV_FIELD = {"OpenAI": "privacy_avv_openai", "Slack": "privacy_avv_slack", "SMTP": "privacy_avv_mail"}


def _avv(row, org_values):
    """The entry on the data processing agreement of a service row, "" for services that need none recorded here."""
    for word, key in AVV_FIELD.items():
        if word in row["name"] or word in row.get("words", ()):
            return org_values[key]["value"] or de_en("offen", "open")
    return ""


def services(org_values=None):
    """Outside services: what goes there, who triggers it, the switch, whether it is on here, the country, and the
    data processing agreement where section 1 records it (org_values: org(); None reads it)."""
    rows = _services()
    org_values = org_values or org()
    for row in rows:
        row["avv"] = _avv(row, org_values)
    return rows


def _services():
    from app import mail, notify

    c = current_app.config
    slack_full = notify.full()  # what goes to Slack, chosen on this page (short by default)
    ai_on = bool(c.get("OPENAI_API_KEY"))
    coding_on, coding_text = _feature_state("coding")
    usa = de_en("USA: Grundlage (Angemessenheitsbeschluss Art. 45 oder Garantien Art. 46) im Vertrag festhalten",
                "USA: record the basis (adequacy decision Art. 45 or safeguards Art. 46) in the contract")
    rows = [
        {"name": "OpenAI (" + de_en("Text", "text") + ")", "words": ("OpenAI",),
         "data": de_en("Eingaben der Forschenden und Admins für Texte, Übersetzungen, Prüfungen, AInstein; keine "
                       "Antworten von Teilnehmenden.", "Input of researchers and admins for texts, translations, reviews, "
                       "AInstein; no participants' answers."),
         "who": de_en("Forschende, Admins", "Researchers, admins"), "switch": "OPENAI_API_KEY",
         "state": _on(ai_on, c.get("OPENAI_TEXT_MODEL", "")), "where": usa},
        {"name": "OpenAI (" + de_en("Bilder", "images") + ")", "words": ("OpenAI",),
         "data": de_en("Bildbeschreibungen der Forschenden.", "Image prompts of the researchers."),
         "who": de_en("Forschende", "Researchers"), "switch": "OPENAI_API_KEY",
         "state": _on(ai_on, c.get("OPENAI_IMAGE_MODEL", "")), "where": usa},
        {"name": de_en("KI-Kodierhilfe (nur eigener KI-Zugang)", "AI coding help (own AI access only)"),
         "words": ("Kategorisieren",),
         "data": de_en("Freitextantworten von Teilnehmenden: nur echte Teilnahmen, höchstens 150 je Aufruf, je 300 "
                       "Zeichen; nur über den eigenen KI-Zugang der Forschenden (z. B. KI:connect.nrw), nie über den "
                       "Schlüssel der Plattform (seit 2026-10-21); erst nachdem die Person, der die Befragung gehört, oder "
                       "ein Admin der Befragung für diesen Empfänger bestätigt hat, dass die Teilnehmenden informiert sind.",
                       "Participants' free-text answers: real participations only, at most 150 per call, 300 characters "
                       "each; only over the researchers' own AI access (e.g. KI:connect.nrw), never over the platform's key "
                       "(since 2026-10-21); only after the owner or an admin of the survey confirmed for this recipient "
                       "that the participants were told."),
         "who": de_en("Forschende", "Researchers"), "switch": de_en("eigener KI-Zugang und experimentell „coding“",
                                                                    "own AI access and experimental “coding”"),
         "state": (coding_on, coding_text),
         "where": de_en("je nach Anbieter des eigenen Zugangs (KI:connect.nrw: Deutschland)",
                        "depending on the provider of the own access (KI:connect.nrw: Germany)")},
    ]
    # the own AI access (app/ai_access.py; eigener-ki-schluessel-konzept.md), for everyone since 2026-10-10
    own_on, own_text = _own_access_state()
    rows.append({"name": de_en("Eigener KI-Zugang (z. B. KI:connect.nrw)", "Own AI access (e.g. KI:connect.nrw)"),
                 "words": ("KI:connect", "KI-Zugang", "eigenen KI-Schlüssel", "eigener KI-Schlüssel"),
                 "data": de_en("Alle Textaufrufe der Person mit eigenem Zugang (auch Antworttexte der KI-Kodierhilfe) "
                               "gehen über deren Schlüssel, Kontingent und Bedingungen an KI:connect.nrw, OpenAI oder "
                               "eine selbst eingetragene Adresse; Bilder weiter über den Schlüssel der Installation. "
                               "Der Schlüssel ist verschlüsselt gespeichert und wird nie wieder angezeigt.",
                               "All text calls of a person with their own access (also answer texts of the AI coding "
                               "help) go over their key, quota and terms to KI:connect.nrw, OpenAI or an address they "
                               "typed; images keep using the installation's key. The key is stored encrypted and "
                               "never shown again."),
                 "who": de_en("Forschende", "Researchers"), "switch": de_en("Konto › KI-Nutzung, für alle",
                                                                             "Account › AI usage, for everyone"),
                 "state": (own_on, own_text),
                 "where": de_en("Je nach Anbieter; KI:connect.nrw: Deutschland. Für Antworttexte ist der Anbieter "
                                "Auftragsverarbeiter der Forschenden.", "Depends on the provider; KI:connect.nrw: "
                                "Germany. For answer texts the provider is a processor of the researchers.")})
    # the KI-Chat for participants (app/ai_chat.py; ki-assistent-tagebuch-konzept.md, 2026-10-23)
    chat_on, chat_text = _feature_state("ai_chat")
    rows.append({"name": de_en("KI-Chat für Teilnehmende (Modellzugang der Befragung)",
                               "AI chat for participants (the survey's model access)"),
                 "words": ("KI-Chat",),
                 "data": de_en("Die Nachrichten der Teilnehmenden im KI-Chat mit dem bisherigen Gespräch und den "
                               "Anweisungen der Forschenden; E-Mail-, Webadressen und Telefonnummern vorher maskiert, "
                               "keine Kennung der Teilnahme. Nur über den Modellzugang, den die Befragung selbst einträgt "
                               "(Anbieter, Adresse, Schlüssel, Modell), nie über den Schlüssel der Plattform oder einen "
                               "persönlichen Zugang; die Einwilligung nennt ihn, Teilnehmende stimmen mit eigenem "
                               "Häkchen zu. Gespeichert werden alle Nachrichten beider Seiten auf dem Server der "
                               "Plattform, gelöscht mit der Teilnahme.",
                               "Participants' messages in the AI chat with the conversation so far and the researchers' "
                               "instructions; email and web addresses and phone numbers masked first, no identifier of "
                               "the participation. Only over the model access the survey enters itself (provider, address, "
                               "key, model), never over the platform's key or a personal access; the consent names it, "
                               "participants agree with a tick of its own. All messages of both sides are stored on the "
                               "platform's server and deleted with the participation."),
                 "who": de_en("Teilnehmende", "Participants"),
                 "switch": de_en("experimentell „ai_chat“, Modellzugang je Befragung", "experimental “ai_chat”, model access per survey"),
                 "state": (chat_on, chat_text),
                 "where": de_en("Je nach Anbieter des Zugangs der Befragung (KI:connect.nrw: Deutschland); der Anbieter "
                                "ist Auftragsverarbeiter der Forschenden, die einen Vertrag mit ihm brauchen.",
                                "Depends on the provider of the survey's access (KI:connect.nrw: Germany); the provider "
                                "is a processor of the researchers, who need a contract with it.")})
    agent_on, agent_text = _feature_state("agent")
    rows.append({"name": de_en("KI-Agent der Forschenden (MCP)", "Researchers' AI agent (MCP)"),
                 "words": ("KI-Agenten-Schnittstelle",),
                 "data": de_en("Aufbau und Texte der Fragebögen, die das Token erreicht (ohne Antworten und ohne "
                               "lizenzpflichtige Wortlaute); in Entwürfen Änderungen des Agenten. Die Plattform schickt "
                               "nichts an ein Modell, der Agent der Person ruft ab.",
                               "Structure and texts of the questionnaires the token reaches (without answers and without "
                               "licensed wordings); in drafts the agent's changes. The platform sends nothing to a "
                               "model; the person's agent fetches."),
                 "who": de_en("Forschende (ihr Agent)", "Researchers (their agent)"),
                 "switch": de_en("experimentell „agent“, Token auf „Konto › Verbindungen“", "experimental “agent”, token on “Account › Connections”"),
                 "state": (agent_on, agent_text),
                 "where": de_en("Je nach Wahl der Person; in ihrer Verantwortung (Nutzungsbedingungen Abschnitt 6)",
                                "Depends on the person's choice; under their responsibility (terms of use section 6)")})
    whisper = c.get("WHISPER_URL", "")
    mail_on = mail.enabled()
    ws_on, ws_text = _feature_state("webservice")
    rows += [
        {"name": de_en("Transkription (Whisper)", "Transcription (Whisper)"), "words": ("Whisper", "Transkription"),
         "data": de_en("Tonaufnahmen von Interviews, Titel des Leitfadens und Namen der Blöcke.",
                       "Audio recordings of interviews, title of the guide and names of the blocks."),
         "who": de_en("Forschende", "Researchers"), "switch": "WHISPER_URL",
         "state": _on(whisper, _host(whisper)),
         "where": de_en("Server des Betreibers oder in seinem Auftrag", "Server of the provider or run on its behalf") +
                  (de_en(". Lokale WhisperX-Warteschlange: temporäres Audio und Zwischentranskripte abgeschlossener Verarbeitungsphasen im privaten Auftragsverzeichnis, ausschließlich zur Wiederaufnahme desselben Auftrags mit denselben Einstellungen. Löschung bei Abschluss, endgültigem Fehler, Abbruch oder nach höchstens 24 Stunden; fertige Ergebnisse eine Stunde. Nach einem Verarbeitungsfehler mit vorhandenem Zwischenstand erfolgt höchstens ein automatischer Wiederholungsversuch, nicht bei Speicher- oder Zeitlimit. Bereinigung bei laufendem Dienst oder beim nächsten Start. Sprecherlabels erkennen keine Namen oder Rollen.",
                         ". Local WhisperX queue: temporary audio and intermediate transcripts of completed processing phases in the private job directory, solely to resume the same job with the same settings. Deleted on completion, terminal failure, cancellation or after at most 24 hours; finished results one hour. A processing failure with an existing checkpoint triggers at most one automatic retry, except for memory or time limits. Cleanup while running or on next start. Speaker labels identify no names or roles.") if c.get("WHISPER_JOBS") else "") +
                  (de_en(" Der Modell-Cache hält nur Whisper/VAD-Modelle für höchstens fünf Minuten Leerlauf oder acht Aufträge vor; Prompts und VAD-Zustände werden je Auftrag zurückgesetzt. Audio und Transkripte werden nicht im Modell-Cache gehalten. Bearbeitungsberechtigte sehen Verarbeitungsphase, Queue-Platz, fertige Aufnahmeteile und eine ungefähre Restzeit. Dafür speichert der Dienst Audiodauer, Phasenstart und abgeschlossene Phasenlaufzeiten nur im temporären Auftrag sowie zusammengefasste Laufzeitwerte je Modell, Sprache, Verarbeitungsoption und Phase, die nach sieben Tagen ohne neue Messung gelöscht werden; diese enthalten keine Auftragskennungen, Prompts oder Transkripte und werden nicht auf der öffentlichen Statusseite gezeigt.",
                         " The model cache retains only Whisper/VAD models for up to five idle minutes or eight jobs; prompts and VAD states are reset per job. Audio and transcripts are not retained in the model cache. Editors can see processing phase, queue position, completed recording parts and approximate time remaining. For this purpose the service stores audio duration, phase start and completed phase timings only in the temporary job, and aggregated timings by model, language, processing options and phase, deleted after seven days without a new measurement; these contain no job identifiers, prompts or transcripts and are not shown on the public status page.") if c.get("WHISPER_JOBS") else "")},
        {"name": "ORCID", "words": ("ORCID",),
         "data": de_en("Anmeldung (Weiterleitung des Browsers), Abfrage öffentlicher Profile zu ORCID iDs.",
                       "Sign-in (browser redirect), lookups of public profiles for ORCID iDs."),
         "who": de_en("Forschende, Admins", "Researchers, admins"), "switch": "ORCID_CLIENT_ID",
         "state": _on(c.get("ORCID_CLIENT_ID")), "where": usa},
        {"name": "Zenodo", "words": ("Zenodo",),
         "data": de_en("Exportierte Daten und Metadaten nach Wahl der Forschenden.",
                       "Exported data and metadata as chosen by the researchers."),
         "who": de_en("Forschende", "Researchers"), "switch": "ZENODO_CLIENT_ID",
         "state": _on(c.get("ZENODO_CLIENT_ID") or c.get("ZENODO_SANDBOX_CLIENT_ID")),
         "where": de_en("EU (CERN, Schweiz/Frankreich)", "EU (CERN, Switzerland/France)")},
        {"name": "Coscine (RWTH)", "words": ("Coscine",),
         "data": de_en("Datenpakete, Präregistrierungen, Fassungen von Leitfäden mit Metadaten; beim Anlegen eines Projekts "
                       "aus Askurious Name, Beschreibung und Projektleitung, wie die Forschenden sie eintragen; bei einer "
                       "Einladung ins Coscine-Projekt Anmelde-E-Mail-Adresse und Rolle der einzeln angekreuzten "
                       "Mitforschenden; bei einer Veröffentlichungsanfrage die Nachricht an den Dienst; Bilder, Tabellen, Video, Ton und Befragungsdateien, "
                       "die Forschende aus Coscine in eine Befragung holen, kommen von dort (Kopie); "
                       "persönliches Token (auch "
                       "zur Prüfung, ob es noch gilt: auf „Konto › Verbindungen“ und, solange eine automatische Ablage damit läuft, "
                       "einmal am Tag).",
                       "Data packages, pre-registrations, revisions of guides with metadata; when a project is created "
                       "from Askurious its name, description and principal investigator as the researchers enter them; "
                       "for an invitation into the Coscine project the sign-in email address and role of the co-researchers "
                       "ticked one by one; for a publication request the message to the service; images, tables, video, sound and survey files researchers "
                       "take from Coscine into a survey come from there (a copy); "
                       "personal token (also to check "
                       "whether it is still valid: on “Account › Connections” and, while an automatic upload runs with it, once a day)."),
         "who": de_en("Forschende", "Researchers"), "switch": de_en("für alle, Token je Person", "for everyone, token per person"),
         "state": _on(True), "where": de_en("Deutschland", "Germany")},
        {"name": de_en("Google Play und Android System WebView (App)", "Google Play and Android System WebView (app)"),
         "words": ("Google Play",),
         # the WebView's own requests to Google (askurious-android README, "The WebView and Google"; audit 2026-10-18,
         # AND-10): not sent by the platform or the app's code, so no transfer of the operator
         "data": de_en("Keine Daten der Plattform: Teilnehmende laden die App für Tagebuchstudien dort selbst; die App "
                       "sendet nur an die Teilnahmeadresse. Die Fragebögen laufen in Android System WebView (von Google); "
                       "die WebView fragt von sich aus Safe Browsing (verkürzte Prüfwerte der Adressen) und, solange auf "
                       "dem Telefon ein Autofill-Dienst eingeschaltet ist, Autofill-Vorhersagen über "
                       "content-autofill.googleapis.com ab (Kennwerte des Formulars, keine Antworten, IP-Adresse des "
                       "Telefons). Die Nutzungsstatistik der WebView ist abgeschaltet, die Fragebögen sind vom Autofill "
                       "ausgenommen; die Vorhersage-Anfragen lässt die WebView nicht abschalten.",
                       "No data of the platform: participants download the app for diary surveys there themselves; the "
                       "app only sends to the participants' address. The questionnaires run in Android System WebView "
                       "(by Google); on its own the WebView queries Safe Browsing (shortened hashes of the addresses) and, "
                       "while an autofill service is switched on on the phone, autofill predictions through "
                       "content-autofill.googleapis.com (characteristics of the form, no answers, the phone's IP "
                       "address). The WebView's usage statistics are switched off and the questionnaires are excluded "
                       "from autofill; the WebView offers no way to switch the prediction requests off."),
         "who": de_en("Teilnehmende", "Participants"), "switch": de_en("für alle", "for everyone"),
         "state": _on(True), "where": usa},
        {"name": de_en("E-Mail (SMTP-Relay)", "Email (SMTP relay)"), "words": ("E-Mail", "Mail"),
         "data": de_en("Empfängeradressen und Inhalt von Einladungen und Kontonachrichten.",
                       "Recipient addresses and content of invitations and account messages."),
         "who": de_en("Forschende, Admins, System", "Researchers, admins, system"), "switch": "MAIL_HOST, MAIL_FROM",
         "state": _on(mail_on, _host("smtp://" + (c.get("MAIL_HOST") or ""))),
         "where": de_en("Je nach Relay", "Depends on the relay")},
        {"name": "Slack", "words": ("Slack",),
         "data": de_en("Hinweise auf Beschwerden und Fehlermeldungen, Neuanmeldungen, Bitten um neues Passwort, "
                       "Warnungen zu Admin-Konten und Befragungen. Knapp (Voreinstellung, Administration › Server): "
                       "nur Kontonummer, Befragungscode und Link. Ausführlich: dazu Name, Organisation, Befragungstitel "
                       "und ein Auszug von Fehlermeldungen; Beschwerden von Teilnehmenden bleiben knapp." + (" Gewählt: ausführlich." if slack_full else " Gewählt: knapp."),
                       "Notices of complaints and error reports, new registrations, requests for a new password, "
                       "warnings about admin accounts and surveys. Short (default, Administration › Server): "
                       "only account number, survey code and link. Detailed: also name, organisation, survey title and "
                       "an excerpt of error reports; complaints of participants stay short." + (" Chosen: detailed." if slack_full else " Chosen: short.")),
         "who": de_en("System", "System"), "switch": "SLACK_WEBHOOK_URL",
         "state": _on(c.get("SLACK_WEBHOOK_URL")), "where": usa},
        {"name": de_en("Zeitstempeldienst (DFN)", "Time-stamp service (DFN)"), "words": (),  # hashes only
         "data": de_en("Nur Hashwerte der Siegelkette und Präregistrierung; mit SEAL_MERKLE je Runde nur die Wurzel "
                       "eines Merkle-Baums über alle Ketten, die Pfade dazu sind verblendet.",
                       "Only hash values of the seal chain and pre-registration; with SEAL_MERKLE only the root of a "
                       "Merkle tree over all chains per round, the paths to it are blinded."),
         "who": de_en("System", "System"), "switch": "TSA_URL",
         "state": _on(c.get("TSA_URL"), _host(c.get("TSA_URL") or "")), "where": de_en("Deutschland", "Germany")},
        {"name": de_en("Rechenknecht (zweiter Rechner)", "Compute node (second machine)"), "words": ("Rechenknecht",),
         "data": de_en("Modelldaten für HB- und Latent-Class-Schätzungen, pseudonymisiert (Teilnahmen als 1, 2, 3 …); "
                       "für Strukturgleichungs- und Längsschnittmodelle, Bootstraps und Simulationen nur Zahlen ohne "
                       "Kennungen; signiert.",
                       "Model input for HB and latent class estimations, pseudonymised (participations as 1, 2, 3 …); "
                       "for structural equation and longitudinal models, bootstraps and simulations numbers only, "
                       "without identifiers; signed."),
         "who": de_en("System, auf Anstoß der Forschenden", "System, started by researchers"), "switch": "COMPUTE_URL",
         "state": _on(c.get("COMPUTE_URL"), _host(c.get("COMPUTE_URL") or "")),
         "where": de_en("Rechner des Betreibers", "Machine of the provider")},
        {"name": de_en("Web Service im Ablauf", "Web service in the flow"), "words": ("Web Service", "Schnittstelle"),
         "data": de_en("Frühere Antworten oder URL-Parameter, die die Forschenden in die Adresse einsetzen.",
                       "Earlier answers or URL parameters that the researchers put into the address."),
         "who": de_en("Teilnehmende (beim Durchlaufen)", "Participants (when passing)"),
         "switch": de_en("experimentell „webservice“", "experimental “webservice”"), "state": (ws_on, ws_text),
         "where": de_en("Je nach Adresse der Forschenden", "Depends on the researchers' address")},
        {"name": de_en("Externer Abschnitt, Panel-Rückleitung", "External section, panel redirect"),
         "words": ("Panel", "extern"),
         "data": de_en("Über den Browser: Rückweg-Link (verschlüsselt), Panel-Kennung aus dem Link, Status, Quotenzelle; "
                       "zurück nur die vereinbarten Werte.", "Through the browser: return link (encrypted), panel id from "
                       "the link, status, quota cell; only the declared values come back."),
         "who": de_en("Teilnehmende", "Participants"), "switch": de_en("je Befragung", "per survey"),
         "state": (True, de_en("je Befragung", "per survey")),
         "where": de_en("Je nach Web-App oder Panel", "Depends on the web app or panel")},
        {"name": de_en("Kartenkacheln und Ortssuche", "Map tiles and place search"),
         "words": ("OpenStreetMap", "Karte"),
         "data": de_en("Der Browser lädt Kartenbilder und sucht Orte: IP-Adresse, Kartenausschnitt, Suchtext.",
                       "The browser loads map images and searches places: IP address, map section, search text."),
         "who": de_en("Teilnehmende und Forschende bei Karten-Elementen", "Participants and researchers with map elements"),
         "switch": "MAP_TILE_URL, MAP_SEARCH_URL",
         "state": _on(c.get("MAP_TILE_URL"), ", ".join(h for h in (_host(c.get("MAP_TILE_URL") or ""),
                                                                    _host(c.get("MAP_SEARCH_URL") or "")) if h)),
         "where": de_en("Je nach Kartendienst; Voreinstellung OpenStreetMap Foundation (Vereinigtes Königreich)",
                        "Depends on the map service; default OpenStreetMap Foundation (United Kingdom)")},
        {"name": "doi.org / Crossref", "words": (),
         "data": de_en("Nur DOIs von Quellen, keine personenbezogenen Daten.", "Only DOIs of sources, no personal data."),
         "who": de_en("Forschende, Admins", "Researchers, admins"), "switch": "—",
         "state": (True, de_en("an", "on")), "where": de_en("Entfällt", "Not applicable")},
    ]
    # recordings stay on this server; named so nobody looks for a service (for everyone since 2026-10-16)
    rows.append({"name": de_en("Sprachnachrichten", "Voice messages"), "words": (),
                 "data": de_en("Bleiben auf diesem Server; kein externer Dienst.", "Stay on this server; no outside "
                               "service."), "who": de_en("Teilnehmende", "Participants"),
                 "switch": de_en("Elementtyp „Sprachnachricht“", "element type “Voice message”"),
                 "state": (True, de_en("für alle", "for everyone")), "where": de_en("Entfällt", "Not applicable")})
    return rows


def measures():
    """Technical and organisational measures (Art. 32), grouped by the seven protection goals of the SDM 3.1."""
    totp = de_en("Admins: ORCID-Anmeldung oder Passwort mit Authenticator-Code; nach ORCID ist kein zusätzlicher "
                 "App-Code verpflichtend.", "Admins: ORCID sign-in or a password with an authenticator code; an ORCID "
                 "sign-in does not require an additional app code.")
    totp += " " + de_en("Für andere Konten ist 2FA freiwillig; ein eingerichteter Faktor wird bei Passwortanmeldung und Passwort-Reset abgefragt. Passwortwechsel, Kontosperre und Überall abmelden widerrufen Datenzugänge und offene Einladungs-/Beitrittslinks und trennen Zenodo, Coscine und den eigenen KI-Zugang; ein erstes Passwort widerruft nichts. Die Sperre nach 100 Fehlversuchen in Folge hält einen Browser nicht auf, in dem sich das Konto mit seinem aktuellen Passwort schon angemeldet hat (signiertes Cookie, 90 Tage).",
                         "For other accounts, 2FA is optional; an enabled factor is required at password sign-in and password reset. Password changes, account blocks and signing out everywhere revoke data access tokens and pending invitations/join links and disconnect Zenodo, Coscine and the own AI access; a first password revokes nothing. The lock after 100 wrong attempts in a row does not hold a browser in which the account signed in with its current password before (signed cookie, 90 days).")
    return [
        (de_en("Vertraulichkeit", "Confidentiality"), [
            (de_en("Verschlüsselte Übertragung, HSTS; Cookies Secure, HttpOnly, Präfix __Host-, SameSite=Lax",
                   "Encrypted transfer, HSTS; cookies Secure, HttpOnly, prefix __Host-, SameSite=Lax"),
             "config.py, app/__init__.py"),
            (de_en("Passwort-Reset über eine dauerhafte verschlüsselte Warteschlange, gleiche Annahme bekannter und unbekannter Adressen; Aufträge eine Stunde gültig, danach Löschung beim nächsten Wartungsdurchlauf. Neue selbst gewählte Anmeldeadressen erst nach Bestätigung im selben angemeldeten Konto; Bestätigungsaufträge 24 Stunden gültig, danach Löschung beim nächsten Wartungsdurchlauf; Link nur als Hash und Passwort-Hash verschlüsselt, kein Klartextpasswort. Bestehende und administrative Zugänge bleiben nutzbar.",
                   "Password resets through a durable encrypted queue, identical submission for known and unknown addresses; jobs valid for one hour, then deleted during the next maintenance pass. New self-chosen sign-in addresses only after confirmation in the same signed-in account; confirmation jobs valid for 24 hours, then deleted during the next maintenance pass; links stored as hashes and password hashes encrypted, no plaintext password. Existing and administrative access remains usable."), "app/account_jobs.py, app/email_confirmation.py"),
            (de_en("KI-Kategorisierung: erkennbare Kontaktdaten vor Versand entfernt, Antworten als fremde Daten behandelt, kleinere Stapel und Prüfung konzentrierter Vorschläge. KI-Übersetzung: bekannte Personenangaben und Kontaktdaten lokal ersetzt und wieder eingesetzt. Keine vollständige Anonymisierung freier Texte.",
                   "AI categorisation: recognisable contact details removed before transmission, answers treated as third-party data, smaller batches and review of concentrated suggestions. AI translation: known personal details and contact details replaced and restored locally. Not complete anonymisation of free text."), "app/ai_privacy.py, app/coding_ai.py, app/translations.py"),
            (de_en("Passwörter als scrypt-Hash; Sperrliste der 10.000 häufigsten Passwörter und naheliegender Wörter; Pause "
                   "nach wiederholten Fehlversuchen je Absender und Konto, Sperre bis zur Freigabe durch Admins nach 100 "
                   "Fehlversuchen in Folge",
                   "Passwords as scrypt hashes; block list of the 10,000 most frequent passwords and obvious words; pause "
                   "after repeated failures per sender and account, lock until an admin releases it after 100 failures "
                   "in a row"), "app/passwords.py"),
            (totp, "app/twofactor.py"),
            (de_en("Aktionen, die Zugänge vergeben oder entziehen, verlangen eine Anmeldung von höchstens 15 Minuten; eine "
                   "ORCID-Anmeldung zählt nur, wenn ORCID dabei das Passwort abgefragt hat (auth_time im ID-Token, dessen "
                   "Signatur mit den öffentlichen Schlüsseln von ORCID geprüft wird). Für den Weg über "
                   "die erneute Anmeldung hält das Sitzungs-Cookie die Eingaben des Formulars verschlüsselt, höchstens "
                   "30 Minuten und ohne Passwörter, Codes und Tokens.",
                   "Actions that grant or remove access require a sign-in of at most 15 minutes ago; an ORCID sign-in "
                   "counts only if ORCID asked for the password during it (auth_time in the ID token, whose signature is checked "
                   "against ORCID's public keys). For the way through signing in "
                   "again, the session cookie holds the form's entries encrypted, for at most 30 minutes and without "
                   "passwords, codes and tokens."), "app/accounts.py, app/auth.py"),
            (de_en("Rollen je Befragung (Ansehen, Bearbeiten, Verwalten); Rohdaten, Befragungsdatei und Duplizieren nur ab "
                   "Bearbeiten; Administration nur für Admins", "Roles per survey (View, Edit, Manage); raw data, the survey "
                   "file and duplicating only from Edit; administration only for admins"),
             "app/surveys/base.py, app/access.py, app/admin.py"),
            (de_en("Statistik der Admins (Administration > Befragungen; Verwaltung > Befragungen nur über die eigene "
                   "Organisation) nur als Zählung echter Teilnahmen, keine "
                   "Antworten; Zahlen unter 5 in Aufteilungen nur als „< 5“, Raster nicht feiner als eine Stunde; "
                   "Befragungen, die ihre Forschenden aus der Übersicht heraushalten, zählen mit, werden aber nicht genannt",
                   "Statistics for admins (Administration > Surveys; Management > Surveys only over the own organisation) only "
                   "as counts of real participations, never answers; "
                   "numbers under 5 in breakdowns only as “< 5”, no grid finer than one hour; surveys their researchers "
                   "keep out of the overview are counted but not named"),
             "app/admin_stats.py"),
            (de_en("Befragungstypen je Konto (Aus, Ansehen, Bearbeiten, Anlegen), nur von Admins gesetzt und im "
                   "Sicherheitsprotokoll vermerkt: sie begrenzen die Rolle in jeder Befragung des Typs, „Aus“ blendet ihn "
                   "samt geteilter Befragungen aus, nur „Anlegen“ legt eigene Befragungen des Typs an",
                   "Survey types per account (Off, View, Edit, Create), set by admins only and noted in the security log: "
                   "they cap the role in every survey of the type, “Off” hides it together with shared surveys, only "
                   "“Create” creates own surveys of the type"),
             "app/kind_access.py, app/access.py"),
            (de_en("Nutzertypen mit Laufzeit: Am Ende sperrt die Plattform das Konto sofort (Sitzungen, Tokens und Links "
                   "enden), nach der Löschfrist löscht sie es, wenn nichts zu übergeben ist; Typ, Verlängerung, Ablauf und "
                   "Löschung stehen im Sicherheitsprotokoll, die automatische Löschung im Protokoll. Jede betreuende Person "
                   "(Rolle „Betreuung“, auch mehrere) wird Admin jeder neuen Befragung der Person und bekommt die "
                   "Nachricht vor dem Ende; die Person sieht das auf „Konto“. Ein Nutzertyp ohne Betreuung nimmt alle "
                   "Betreuenden heraus, ihre Admin-Rollen in bestehenden Befragungen bleiben. Die "
                   "Leitung einer Lehrveranstaltung lädt Adresslisten ein und erzeugt Einmal-Links nur für Konten ihrer "
                   "Lehrveranstaltung ohne ORCID iD. Nutzertypen mit Laufzeit und Löschfrist legt jede Organisation "
                   "selbst fest (Verwaltung > Nutzertypen); jede beginnt mit „Mitarbeitende“ ohne Laufzeit.",
                   "User types with a period: at the end the platform blocks the account at once (sessions, tokens and "
                   "links end), after the deletion period it deletes it if nothing has to be handed over; type, "
                   "extension, end and deletion are in the security log, the automatic deletion in the access log. Every "
                   "supervising person (role “supervisor”, also several) becomes admin of every new survey of the person "
                   "and gets the notice before the end; the person sees this on “Account”. A user type without "
                   "supervision takes all supervisors out; their admin roles in existing surveys stay. The lead of a course invites address lists and creates one-time links only "
                   "for accounts of the course without an ORCID iD. Each organisation sets its own user types with "
                   "period and deletion period (Management > User types); each starts with “Staff” without a period."),
             "app/account_types.py, app/bulk_invites.py, app/joinlinks.py"),
            (de_en("Beitritts- und Leitungslinks von Gruppen nur als Hash gespeichert, befristet und in der Zahl der "
                   "Nutzungen begrenzt; sie ruhen, solange die Person, die sie erzeugt hat, gesperrt ist oder niemanden "
                   "mehr in die Gruppe holen darf; Leitungslinks widerrufbar",
                   "Join and lead links of groups stored as hashes only, limited in time and number of uses; they rest "
                   "while the person who created them is blocked or may no longer bring anyone into the group; lead "
                   "links can be revoked"), "app/joinlinks.py"),
            (de_en("Agenten-Schnittstelle: Token nur als Hash, nur im Header (in der Adresse abgelehnt), 90 Tage "
                   "gültig, widerrufbar; Umfang lesen oder Entwürfe bearbeiten, wahlweise auf Befragungen begrenzt; Rolle "
                   "in der Befragung bei jedem Aufruf geprüft; ändert nur Entwürfe über dieselben Prüfungen wie der "
                   "Editor, nichts an Feldphase, Daten, Links, Beteiligten und Einwilligung; keine Antworten, keine "
                   "lizenzpflichtigen Wortlaute; Ratenbegrenzung je Token; jeder Aufruf im Protokoll",
                   "Agent interface: token as a hash only, only in the header (refused in the address), valid 90 days, "
                   "revocable; scope read or edit drafts, optionally limited to surveys; role in the survey checked at "
                   "every call; changes drafts only, through the same checks as the editor, nothing of fieldwork, data, "
                   "links, people involved and consent; no answers, no licensed wordings; rate limit per token; every "
                   "call in the log"), "app/agent_access.py, app/mcp.py, app/agent_tools.py"),
            (de_en("Interviewlinks nur als Hash gespeichert, Schlüssel nach „#“ (nicht in Protokollen); je Zugang nur "
                   "eigene Interviews bis zur Übergabe, sonst 404; befristet, Gerätezahl begrenzt, Sperren, neuer Link und "
                   "Geräte abmelden beenden alle Sitzungen sofort; keine Forschenden-Oberfläche ohne Konto",
                   "Interviewer links stored as hashes only, key after “#” (not in logs); per access only own interviews "
                   "until handed over, otherwise 404; limited in time and devices, blocking, a new link and signing out "
                   "devices end every session at once; no researcher interface without an account"), "app/interviewer.py"),
            (de_en("Teilnehmerbildschirm im Interview: Link je Interview mit 256 zufälligen Bits, nur als Hash gespeichert, "
                   "Schlüssel nach „#“ (nicht in Protokollen); ein einziges Gerät, an Sitzung und zufällige Gerätekennung "
                   "gebunden; ungeöffnet 30 Minuten, verbunden höchstens 6 Stunden, endet mit dem Interview, beim Trennen "
                   "und mit einem neuen Link; das Gerät liest nur die gerade freigegebene Aufgabe und sendet nur deren "
                   "Antwort; Fehlversuche und Antworten je Absender begrenzt; Änderungen der Interviewenden markiert",
                   "Participant screen in the interview: link per interview with 256 random bits, stored as a hash only, "
                   "key after “#” (not in logs); one device only, bound to its session and a random device identifier; "
                   "30 minutes unopened, at most 6 hours connected, ends with the interview, on disconnecting and with a "
                   "new link; the device reads only the task released now and sends only its answer; failed attempts and "
                   "answers per sender limited; changes by the interviewers marked"), "app/interview_screen.py"),
            (de_en("Zugangsdaten zu Diensten, Tokens und das TOTP-Geheimnis verschlüsselt gespeichert; Lese-Tokens nur "
                   "als Hash. Die Schlüssel, die die Teilnahmeseite braucht (Webservices, externe Abschnitte, Panel-Links), "
                   "liegen unter einem eigenen Schlüssel; die Teilnahme-Container bekommen den Schlüssel der Rechenaufträge nicht",
                   "Credentials for services, tokens and the TOTP secret stored encrypted; read tokens as hashes only. "
                   "The keys the participants' side needs (web services, external sections, panel links) are locked "
                   "with a key of their own; the participation containers do not get the key of the computing jobs"),
             "app/secrets_box.py"),
            (de_en("Dashboard- und Quotenlinks werden über einen Hash nachgeschlagen; ihre Kopie zur Anzeige ist verschlüsselt. "
                   "Öffentliche Dashboards pausieren nach Löschungen bis zu einer neuen ausreichend großen Gruppe. "
                   "Einzelfallfilter, genaue Kartenpositionen und Falllisten erfordern Bearbeiten.",
                   "Dashboard and quota links are looked up by hash; their display copy is encrypted. Public dashboards "
                   "pause after erasure until a new sufficiently large group is available. Individual filters, exact map "
                   "positions and case lists require Edit."), "app/shared_tokens.py, app/boards/compute.py, app/results.py"),
            (de_en("Große Auswertungen und Exporte laufen in eigenen Prozessen. Private Ergebnisdateien sind an Person und "
                   "Ansicht gebunden, werden bei jedem Abruf erneut autorisiert und nach einer Stunde durch die Haushaltung "
                   "entfernt. Die Qualitätswerte je Teilnahme (Bearbeitungszeit, Markierungen) behalten sie signiert in "
                   "einer Datei der Befragung für den nächsten Bericht. Löschungen brechen laufende Berechnungen ab und "
                   "entfernen die gespeicherten Dateien. "
                   "Wartende Daten-API-Abrufe sind gemeinsam und je Token begrenzt; Clients können ohne Warten einen "
                   "Jobverweis ohne Bearer-Geheimnis abrufen.",
                   "Large reports and exports run in separate processes. Private result files are bound to the person and "
                   "view, authorised again on every retrieval, and removed by housekeeping after one hour. They keep the "
                   "quality values per participation (duration, marks) signed in a file of the survey for the next report. "
                   "Erasure cancels "
                   "running calculations and removes stored files. Waiting data API requests are limited jointly and "
                   "per token; clients can retrieve a job reference without a bearer secret without waiting."), "app/report_jobs.py, app/erasure.py"),
            (de_en("Eine private, signierte Exporttabelle dient mehreren Dateiformaten. Sie ist nach einer Stunde ungültig, "
                   "wird durch die Haushaltung entfernt und bei Löschungen sofort verworfen. Datenbankrevisionen erkennen "
                   "Änderungen ohne erneutes Lesen aller Antworten. Papier-Scans werden in getrennten Prozessen verarbeitet.",
                   "A private, signed export table serves several file formats. It expires after one hour, is removed by "
                   "housekeeping and discarded immediately on erasure. Database revisions detect changes without rereading "
                   "all answers. Paper scans are processed in separate processes."),
             "app/export_tables.py, app/data_revision.py, app/paper_worker.py, app/housekeeping.py"),
            (de_en("Sicherheitskopfzeilen: CSP ohne Inline-Skripte, Einbettung nur von hier, Bilder auf allen Seiten "
                   "nur von hier und den Kartenservern (Forschende zusätzlich der Statusseite), Formulare der Forschenden "
                   "nur an diese Seite, ORCID und die Teilnahmeadresse, keine Zwischenspeicherung angemeldeter Seiten und "
                   "der Teilnahme; Bilder unter /media nur im Browser zwischengespeichert (privat, einen Tag), nie in "
                   "geteilten Caches", "Security headers: CSP without inline scripts, framing only from here, images on "
                   "all pages only from here and the map tile servers (researchers also from the status page), "
                   "researchers' forms only to this site, ORCID and the participants' address, no caching of signed-in "
                   "pages and participation; images under /media cached only by the browser (private, one day), never "
                   "in shared caches"), "app/__init__.py, app/media.py"),
            (de_en("Tokens in Linkpfaden (Dashboards, Einladungen, Vorschau-, Passwort- und Tagebuchlinks) und der "
                   "Cron-Schlüssel in der Adresse im Fehlerprotokoll der App unkenntlich, Zeilenumbrüche und "
                   "Steuerzeichen dort maskiert (keine gefälschten Protokollzeilen); Rechenknecht nur über https oder im "
                   "eigenen Netz, Nachrichten mit Richtung, Methode und Einmalwert signiert (keine Wiedereinspielung)",
                   "Tokens in link paths (dashboards, invitations, preview, password and diary links) and the cron key "
                   "in an address masked in the app's error log, line breaks and control characters escaped there (no "
                   "forged log lines); compute node only over https or inside the own network, messages signed with "
                   "direction, method and a one-time value (no replay)"),
             "app/log_mask.py, app/compute.py"),
            (de_en("Hochgeladene Dateien und Aufnahmen außerhalb der öffentlich erreichbaren Dateien",
                   "Uploaded files and recordings outside the publicly reachable files"), "instance/"),
            (de_en("Datenbankrolle ohne Administratorrechte", "Database role without administrator rights"),
             "deploy/docker/db-app-role.sh"),
            (de_en("Tagebuchlinks und Browserzugänge sind zwölf Stunden gültig, an die aktuelle Zugangsrevision und an "
                   "den Browser gebunden, der den Link zuerst öffnet. "
                   "Austritt, Pause, Beenden und Gerätewechsel widerrufen alte Zugänge; Prüfung vor jedem Zugriff und "
                   "Speichern unter einer Datenbanksperre. Eine neu ausgegebene Abschlussbefragung bleibt möglich.",
                   "Diary links and browser grants are valid for twelve hours and bound to the current access revision "
                   "and to the browser that opens the link first. "
                   "Leaving, pausing, ending and switching devices revoke previous access; each access and write is "
                   "checked under a database lock. A newly issued exit questionnaire remains available."), "app/diary_access.py"),
            (de_en("Ein benutzter persönlicher Tagebuchcode verbindet ein neues Gerät nur nach einer Freigabe durch "
                   "Bearbeitende, einmal und zwei Stunden lang; das bisherige Gerät verliert dann den Zugang.",
                   "A used personal diary code connects a new device only after a release by editors, once and for two "
                   "hours; the earlier device then loses access."), "app/diary.py"),
            (de_en("Wird eine Person einer Tagebuchstudie gelöscht, wird ihr Einladungscode im selben Schritt von ihr "
                   "entkoppelt (auch seine Freigabe endet); eine spätere Person kann nie über diesen Code verbunden "
                   "werden.",
                   "When a person of a diary survey is deleted, their invitation code is detached from them in the same "
                   "step (its release ends too); a later person can never be connected through that code."),
             "app/models.py, app/diary.py"),
            (de_en("Frei gewählte Webservice- und KI-Ziele dürfen die eigenen Netze des Betreibers (PROTECTED_NETS, "
                   "instance_overlay/protected_nets.txt) nicht erreichen. Prüfung aller DNS-Adressen einschließlich "
                   "IPv4 in IPv6; Verbindung nur zur geprüften Adresse. Zusätzliche Netze können gesperrt werden; "
                   "die festen KI-Anbieter (KI:connect.nrw, OpenAI) und administrativ freigegebene KI-Hosts sind "
                   "ausdrückliche Ausnahmen.",
                   "Freely chosen web-service and AI targets cannot reach the operator's own networks (PROTECTED_NETS, "
                   "instance_overlay/protected_nets.txt). Every DNS address is checked, including IPv4 embedded in IPv6; "
                   "connections use the checked address. Further networks may be blocked; the fixed AI providers "
                   "(KI:connect.nrw, OpenAI) and administratively allowed AI hosts are explicit exceptions."), "app/webservice.py, app/ai_access.py, config.py"),
            (de_en("Im Docker-Produktivbetrieb aktiviert: eingeschränkte Datenbankrolle für die Laufzeit, separate "
                   "Migrationsjobs mit Eigentümerrechten und getrennte Docker-Netze; nginx, Status und Rechenknecht "
                   "haben keinen Datenbankzugang, die Teilnahme-Container kein gemeinsames Netz mit dem Container der "
                   "Forschenden (den Abbruch einer Transkription nach einer Löschung reichen sie als Auftragsnummer über "
                   "die Datenbank an die Haushaltung weiter). Die Statusseite prüft den Rechenknecht indirekt über die App; "
                   "sie erhält nur den Gesundheitszustand, keine Auftragsdaten oder Rechenknecht-Zugangsdaten. "
                   "Für WhisperX speichert und zeigt sie nur, ob der Dienst läuft, gestört oder abgeschaltet ist, "
                   "keine Auftragszahlen, Kennungen, Audioinhalte, Transkripte oder Zugangsdaten. Diese Betriebsmessungen werden sieben "
                   "Tage gespeichert; die öffentliche Statusseite zeigt 24 Stunden",
                   "Enabled in Docker production: a restricted database role for runtime, separate migration jobs "
                   "with owner privileges and separate Docker networks; nginx, status and compute have no database access, "
                   "the participation containers share no network with the researchers' container (they hand the "
                   "cancel of a transcription after a deletion on as a job number through the database to the "
                   "housekeeping). "
                   "The status page checks compute indirectly through the app; it receives only health status, "
                   "no job data or compute credentials. For WhisperX it stores and shows only whether the service runs, "
                   "is disrupted or is switched off, no job counts, identifiers, audio, transcripts or credentials. These operational "
                   "measurements are retained for seven days; the public status page shows 24 hours"),
             "deploy/docker/isolation/README.md"),
            (de_en("Vorbereitet, aber aus: eine eigene Datenbankrolle für die Teilnahme-Container, die nur die "
                   "Tabellen lesen und schreiben darf, die die Teilnahme braucht (Befragung, Antworten, Quoten, "
                   "Live-Abstimmung, Tagebuch, Löschcodes, Beschwerden, öffentliche Dashboards; von den Konten die Kontozeile "
                   "(Name, Zugehörigkeit, ORCID iD) für die Einwilligung); Anmeldedaten (Passwort, zweiter Faktor), Sitzungen, "
                   "Zugangsschlüssel, Protokolle und die Arbeit im Editor bleiben ihr verschlossen. Ein Einbruch auf der "
                   "Teilnahmeseite erreicht sie so nicht über die Datenbank. Neue "
                   "Tabellen bleiben zu, bis sie ausdrücklich eingeordnet sind",
                   "Prepared but off: a database role of its own for the participation containers, which may read and "
                   "write only the tables participation needs (survey, answers, quotas, live voting, diary, deletion "
                   "codes, complaints, public dashboards; of the accounts the account row (name, affiliation, ORCID iD) for the consent); "
                   "sign-in data (password, second factor), sessions, access keys, logs and the work in the editor stay closed "
                   "to it. A break-in on the participants' side thus does not reach them through the database. New "
                   "tables stay closed until they are classified explicitly"),
             "app/participant_grants.py, deploy/docker/participant-db-role.sh"),
            (de_en("Jeder Container erhält nur die Geheimnisse, die er braucht (die Teilnahme-Container keine Zugänge zu "
                   "ORCID, Zenodo, KI-Diensten, kein Passwort des Mailservers und kein Datenbank-Superuser; ihre E-Mails, "
                   "etwa die Eingangsbestätigung einer Beschwerde, warten in der Mail-Warteschlange, bis die Wartung sie "
                   "binnen etwa einer Minute verschickt, danach bleiben Adresse und Text nicht gespeichert; mit eigenem "
                   "Schlüssel der Forschenden-Sitzungen können sie keine Sitzung der Forschenden ausstellen); "
                   "App-Container ohne Sonderrechte und mit nur lesbarem Dateisystem",
                   "Each container gets only the secrets it needs (the participation containers no access to ORCID, "
                   "Zenodo, AI services, no password of the mail server and no database superuser; their emails, such "
                   "as the confirmation of receipt of a complaint, wait in the mail queue until the housekeeping sends "
                   "them within about a minute, after which address and text are not kept; with an own key for the "
                   "researchers' sessions they cannot issue a researcher's session); app containers without special "
                   "rights and with a read-only file system"), "deploy/docker/compose.yaml, app/mail.py, app/cookies.py"),
            (de_en("Schlüssel beim Start auf Länge, Zufall und ratbare Wörter geprüft: Ein zu kurzer oder zufallsloser "
                   "verhindert den Start, ein schwacher wird bei jedem Start gemeldet; eigene, per HKDF abgeleitete "
                   "Schlüssel je Zweck für neue Zwecke; Wechsel eines Schlüssels mit dem alten als Rückfall, gespeicherte "
                   "Geheimnisse werden neu verschlüsselt; eine unverschlüsselte Mail-Anmeldung wird laut gemeldet",
                   "Keys checked for length, randomness and guessable words at the start: one too short or without "
                   "randomness stops the start, a weak one is reported at every start; keys of their own per purpose, "
                   "derived by HKDF, for new purposes; a key is changed with the old one as a fallback, stored secrets "
                   "are encrypted anew; an unencrypted mail login is reported loudly"),
             "app/key_strength.py, app/subkeys.py, app/key_rotation.py, app/mail.py"),
        ]),
        (de_en("Integrität", "Integrity"), [
            (de_en("Schutz vor seitenübergreifenden Anfragen (CSRF) für jedes Formular; das Token angemeldeter Forschender "
                   "gilt 12 Stunden (CSRF_HOURS) und wird bei jeder Anmeldung neu vergeben",
                   "Protection against cross-site requests (CSRF) for every form; the token of signed-in researchers is "
                   "valid for 12 hours (CSRF_HOURS) and renewed with every sign-in"), "app/extensions.py, app/__init__.py"),
            (de_en("Siegelkette der Teilnahmen mit Zeitstempel des DFN (öffentlich nur Hashwerte, Fallzahlen und "
                   "Löschwege; nach Löschung durch die Plattform oder mit dem Konto ohne Titel der Befragung); "
                   "Präregistrierung als ZIP mit SHA-256",
                   "Seal chain of participations with DFN time stamps (public: hash values, case counts and ways of "
                   "deletion only; after a deletion by the platform or with the account without the survey's title); "
                   "pre-registration as ZIP with SHA-256"),
             "app/seal.py, app/prereg.py"),
            (de_en("Signierte Rückwege (externer Abschnitt) und signierte Nachrichten zum Rechenknecht (HMAC)",
                   "Signed return links (external section) and signed messages to the compute node (HMAC)"),
             "app/external.py, app/compute.py"),
            (de_en("Bearbeitungsspuren und Rückgängig je Änderung", "Editing traces and undo per change"),
             "app/models.py (EditMark, EditStep)"),
        ]),
        (de_en("Verfügbarkeit und Belastbarkeit", "Availability and resilience"), [
            (de_en("Datenbank und Dateien werden im Linux-Docker-Betrieb in einem gemeinsamen Sicherungsfenster geschützt; Antworttransaktionen und Dateioperationen beim Abschluss warten während dieses Fensters, eine Anfrage höchstens einige Sekunden (STORAGE_GATE_WAIT), danach bittet die Seite, sie erneut zu senden; nichts davon wird halb gespeichert. Die Sperre endet vor einer externen Übertragung.",
                   "On the Linux Docker server, database and files share a protected backup window; answer transactions and their commit file operations wait during that window, a request at most a few seconds (STORAGE_GATE_WAIT), after which the page asks to send it again; nothing of it is stored halfway. The gate ends before any offsite transfer."),
             "app/storage_guard.py, tools/storage_backup_lock.py, deploy/docker/backup.sh"),
            (de_en("Sicherung der Datenbank alle vier Stunden (14 Tage) und vor jeder Aktualisierung; hochgeladene "
                   "Dateien alle vier Stunden als Schnappschuss, nur neue oder geänderte Inhalte kosten Platz (14 Tage); "
                   "auf dem Server selbst. Mit BACKUP_ENCRYPT_KEY liegen die regelmäßigen Dumps und die "
                   "Upload-Schnappschüsse nur verschlüsselt da (GPG oder age, öffentlicher Schlüssel; der private nur "
                   "offline bei den Betreibern); die Dumps vor einer Aktualisierung gleich nach einem gelungenen Deploy. "
                   "Kopie außerhalb (verschlüsselt) vorbereitet, aber aus",
                   "Database backup every four hours (14 days) and before every update; uploaded files every four "
                   "hours as a snapshot, only new or changed contents take space (14 days); on the server itself. With "
                   "BACKUP_ENCRYPT_KEY the regular dumps and the upload snapshots lie encrypted only (GPG or age, public "
                   "key; the private one offline with the operators only); the dumps before an update right after a "
                   "deploy that went through. A copy off the server (encrypted) prepared but off"),
             "deploy/docker/backup.sh, snapshot_uploads.py, lock_uploads.sh, offsite.sh"),
            (de_en("Die Sicherungen liegen außerhalb der Container der App; Admins der Plattform sehen unter "
                   "Administration > Server nur ihre Liste (Name, Größe, Zeitpunkt). Herunterladen, Zurückspielen und "
                   "Löschen geht nur auf dem Server selbst, so führt eine übernommene Admin-Anmeldung nicht an die Daten "
                   "der Sicherungen",
                   "The backups lie outside the app's containers; platform admins see only their list (name, size, "
                   "time) under Administration > Server. Downloading, restoring and deleting happen on the server "
                   "itself only, so a taken-over admin sign-in does not lead to the data in the backups"),
             "deploy/docker/backup.sh (backups.json), app/backups.py"),
            (de_en("Teilnahme, Einstellung „Schneller blättern, auch bei schwachem Netz“: Die Antworten des laufenden "
                   "Blocks liegen nur im Sitzungsspeicher des Tabs (sessionStorage), bis der Server sie bestätigt; jede "
                   "Seite geht einzeln mit CSRF-Schutz an den Server, doppelte Sendungen ändern nichts, am Blockende "
                   "prüft der Server alles erneut; der Speicher endet mit dem Tab, kein Cookie, keine Kennung",
                   "Participation, setting “Page through faster, even on a weak connection”: the answers of the current block "
                   "stay only in the tab's session storage (sessionStorage) until the server confirms them; each page goes "
                   "to the server on its own with CSRF protection, sending it twice changes nothing, at the end of the block "
                   "the server checks everything again; the storage ends with the tab, no cookie, no identifier"),
             "app/blockload.py, app/static/participant.js"),
            (de_en("Zwei Container für die Teilnahme, nacheinander neu gestartet; getrennte Container für Forschende",
                   "Two containers for participation, restarted one after the other; separate containers for researchers"),
             "deploy/docker/compose.yaml"),
            (de_en("Speicher- und Prozessgrenzen je Container: Eine ausufernde Anfrage oder Schätzung reißt die Datenbank "
                   "nicht mit", "Memory and process limits per container: a runaway request or estimation does not take "
                   "the database down"), "deploy/docker/compose.yaml"),
            (de_en("Ratenbegrenzungen für Anmeldung (nur Fehlversuche zählen), Teilnahme, Löschcodes, Tokens, "
                   "Kommentare, E-Mails; die für Codes, Anmeldung und Tokens für alle Prozesse in der Datenbank; dazu eine "
                   "Bremse je Adresse in nginx",
                   "Rate limits for sign-in (only failed attempts count), participation, deletion codes, tokens, "
                   "comments, mails; those for codes, sign-in and tokens for all processes in the database; and a brake "
                   "per address in nginx"),
             "config.py, app/limits.py, deploy/docker/nginx"),
            (de_en("Sicherheitsprotokoll: Anmeldungen, Fehlversuche, Sperren, Passwort- und Faktoränderungen, Einmal-Links, "
                   "Einladungen und Beitrittslinks, Ratenbegrenzungen; ohne IP-Adresse und ohne Geheimnisse",
                   "Security log: sign-ins, failed attempts, locks, changes of password and factor, one-time links, "
                   "invitations and join links, rate limits; without IP address and without secrets"),
             "app/security_log.py"),
            (de_en("Kosten- und Lastgrenzen der KI: Tagesbudget der Plattform, gleichzeitige Aufrufe, Ausgabelimit",
                   "Cost and load limits of the AI: daily budget of the platform, calls at the same time, output limit"),
             "app/limits.py, app/ai.py"),
            (de_en("Speicherplatz je Befragung und Konto für hochgeladene Dateien, Mindestplatz auf der Platte; Prüfung "
                   "und Schreiben unter einer gemeinsamen Dateisperre über Prozesse und Container hinweg. Eine lokale "
                   "Belegungskarte hält vorhandene Dateipfade und Größen sowie laufende Reservierungen; Änderungen "
                   "werden unter der Sperre nachgeführt und regelmäßig mit den Dateien abgeglichen. Unbekannte "
                   "Schreibwege werden vollständig nachgezählt. Verwaiste Belegungseinträge entfernt der Hintergrundabgleich.",
                   "Storage room per survey and account for uploaded files, minimum free space on the disk; admission "
                   "and writing under a shared file lock across processes and containers. A local inventory holds "
                   "existing file paths and sizes and outstanding reservations; changes are recorded under the lock "
                   "and regularly reconciled with the files. Unknown writers use full physical counting. "
                   "Background reconciliation removes orphaned inventory entries."),
             "app/media.py, app/storage_room.py, app/storage_usage.py"),
            (de_en("Große Exporte teilen sich eine Lastgrenze; ZIP-Bilder werden stückweise gelesen, JSON und ZIP haben "
                   "Größenbudgets. Zu große JSON-Downloads wechseln auf ZIP.",
                   "Large exports share a work limit; ZIP images are read in chunks, JSON and ZIP have size budgets. "
                   "Oversized JSON downloads switch to ZIP."), "app/heavy.py, app/transfer.py, app/survey_files.py"),
        ]),
        (de_en("Nichtverkettung", "Unlinkability"), [
            (de_en("Organisationen als logische Trennung in einer Datenbank: Gruppen teilen nur innerhalb ihrer "
                   "Organisation, die Personensuche findet nur Personen der eigenen Organisationen und gemeinsamer "
                   "Befragungen; über den Zaun nur die persönliche Einladung in eine Befragung. Je Organisation lässt "
                   "sich festlegen, dass nur ihre Admins oder nur die Plattform Personen von außen hereinholen, und dass "
                   "sich ihre Mitglieder nur als „Mitglied 12“ sehen (ohne Name und ORCID iD; Namen sehen Admins und "
                   "Gruppenleitungen, Teilnehmende die Verantwortlichen). "
                   "Plattform-Admins und der Serverbetrieb sehen alle Organisationen",
                   "Organisations as a logical separation in one database: groups share only within their "
                   "organisation, the people search finds only people of one's own organisations and shared surveys; "
                   "across the fence only the personal invitation to a survey. Per organisation it can be set that "
                   "only its admins or only the platform bring in people from outside, and that its members see each other "
                   "only as “Member 12” (without name and ORCID iD; admins and group leads see names, participants "
                   "the people responsible). Site admins and server "
                   "operation see all organisations"), "app/orgs.py, app/access.py, app/surveys/people.py"),
            (de_en("Kontaktabschnitt ohne Verbindung zur Teilnahme, ohne Zeitstempel", "Contact section without a link "
                   "to the participation, without a timestamp"), "app/models.py (ContactEntry)"),
            (de_en("Ersatzkennungen in der gemeinsamen Auswertung mit eigenem Schlüssel", "Substitute identifiers in the "
                   "pooled analysis with a key of its own"), "app/constructs.py (POOL_KEY)"),
            (de_en("Getrennte Adressen und Container für Teilnehmende und Forschende (wenn eingerichtet)",
                   "Separate addresses and containers for participants and researchers (when set up)"), "app/hosts.py"),
            (de_en("Rechenknecht erhält nur pseudonymisierte Modelldaten", "The compute node only gets pseudonymised "
                   "model input"), "app/compute.py"),
            (de_en("Dashboard- und Quotenlinks zeigen nur zusammengefasste Zahlen", "Dashboard and quota links show "
                   "aggregated figures only"), "app/boards/"),
            (de_en("Tagebuchstudien: ein zufälliger Schlüssel je Studie, auf dem Server nur als Hash; keine Gerätekennung; "
                   "die App leert vor jedem Eintrag Cookies und Speicher der WebView, zwei Studien eines Telefons lassen "
                   "sich nicht verbinden; Nutzungsstatistik der WebView an Google abgeschaltet, Fragebögen vom Autofill "
                   "ausgenommen; veröffentlichte Daten mit neu vergebener Personennummer. Offlineantworten liegen "
                   "einzeln mit AES-GCM verschlüsselt in einer privaten SQLite-Datenbank. Kennungen und UTC-Tage werden "
                   "nur als HMAC-Suchwerte mit zufälligem, verschlüsselt abgelegtem Schlüssel indiziert; Gleichheit und "
                   "Anzahlen bleiben erkennbar, Inhalte und genaue Zeiten verschlüsselt. Die Übernahme der alten Queue "
                   "wird vollständig bestätigt, bevor deren Ablage entfernt wird; defekte Datenbanken werden nicht "
                   "automatisch gelöscht. Löschung einzelner Antworten überschreibt nicht die gesamte Warteschlange.",
                   "Diary surveys: one random key per diary survey, on the server only as a hash; no device identifier; the app "
                   "clears cookies and storage of the WebView before every entry, two diary surveys on one phone cannot be "
                   "linked; the WebView's usage statistics to Google switched off, questionnaires excluded from autofill; published data with a newly assigned person number. Offline answers are individually AES-GCM "
                   "encrypted in a private SQLite database. Identifiers and UTC days are indexed only as HMAC lookup "
                   "values with a random, encrypted key; equality and counts remain visible, contents and exact times "
                   "stay encrypted. Migration of the old queue is fully committed before its source is removed; corrupt "
                   "databases are not automatically deleted. Erasing individual answers does not rewrite the entire "
                   "queue."), "app/diary_api.py, app/export.py"),
        ]),
        (de_en("Transparenz", "Transparency"), [
            (de_en("Datenschutzseite der Plattform und je Befragung; Standardabsätze der Einwilligung je eingeschalteter "
                   "Funktion", "Privacy page of the platform and per survey; standard paragraphs of the consent per "
                   "feature switched on"), "app/templates/public/privacy.html, app/items/consent.py"),
            (de_en("Pflichtangaben nach Art. 13 DSGVO: Der Standardtext der Einwilligung nennt Empfänger (Server der "
                   "Plattform, Betreiber als Auftragsverarbeiter, KI-Dienst nach Bestätigung), Speicherdauer oder ihre "
                   "Kriterien, Rechtsgrundlage und Widerruf mit Wirkung für die Zukunft; die Datenschutzseite je "
                   "Befragung nennt Verantwortliche, Datenschutzbeauftragte (Angabe der Forschenden oder der "
                   "Installation), die Rechte nach Art. 15 bis 21 und die Aufsichtsbehörde",
                   "Information under Art. 13 GDPR: the standard consent text names the recipients (the platform's "
                   "server, the operator as processor, the AI service once confirmed), the retention period or its "
                   "criteria, the legal basis and withdrawal with effect for the future; the privacy page of each survey "
                   "names the controller, the data protection officer (as entered by the researchers or set by the "
                   "installation), the rights under Art. 15 to 21 and the supervisory authority"),
             "app/items/consent.py, app/templates/participant/privacy.html, instance_overlay/README.md"),
            (de_en("Besondere Kategorien (Art. 9 DSGVO): Schalter je Befragung; der Standardtext nennt Art. 9 Abs. 2 lit. a, "
                   "Teilnehmende willigen mit einem eigenen Häkchen ausdrücklich ein; Einstellungen und Startprüfung "
                   "weisen auf eine Datenschutz-Folgenabschätzung durch die Forschenden hin. Tonaufnahmen "
                   "(Sprachnachricht) mit eigenem Häkchen; sie werden nicht veröffentlicht. Welche Bestätigungen eine "
                   "Befragung verlangt, steht ab dem Start fest",
                   "Special categories (Art. 9 GDPR): a switch per survey; the standard text names Art. 9(2)(a), "
                   "participants give explicit consent with a tick of its own; the settings and the pre-launch check "
                   "point the researchers to a data protection impact assessment. Audio recordings (voice message) with "
                   "a tick of their own; they are not published. Which confirmations a survey asks is fixed from its "
                   "start"), "app/items/consent.py, app/design.py, app/lifecycle.py"),
            (de_en("Datenfluss je Befragung als Word (Daten › Dokumentation): die Schritte von Abschnitt 4 mit den "
                   "Einstellungen der Befragung, für das Verzeichnis der Forschenden; ohne Antworten",
                   "Data flow per survey as Word (Data › Documentation): the steps of section 4 with the settings of the "
                   "survey, for the researchers' record; without answers"), "app/data_flow_docx.py"),
            (de_en("Nutzungsbedingungen mit Versionsstand und Zustimmung je Fassung", "Terms of use with a version and "
                   "acceptance per version"), "licence.md, app/terms.py"),
            (de_en("Protokoll datenschutzrelevanter Handlungen; KI-Nutzung je Zweck; dieses Konzept",
                   "Log of data-protection actions; AI usage per purpose; this concept"), "app/audit.py, app/ai_stats.py"),
            (de_en("Seite „KI-Nutzung“: Jede Person sieht ihre KI-Aufrufe der letzten 30 Tage und welcher Zugang "
                   "benutzt wird", "Page “AI usage”: every person sees their AI calls of the last 30 days and which "
                   "access is used"), "app/ai_access.py, app/templates/accounts/ai.html"),
        ]),
        (de_en("Intervenierbarkeit", "Intervenability"), [
            (de_en("Teilnehmende löschen selbst: 15 Minuten nach Ende im Browser, danach mit Löschcode (sofern eingeschaltet); Löschanfragen auf der Seite "
                   "„Export“", "Participants delete themselves: 15 minutes after completion in the browser, then with the deletion code (if switched on); "
                   "deletion requests on the page “Export”"), "app/erasure.py, app/public.py"),
            (de_en("Beschwerdeformular auf der Datenschutzseite; Admins können Befragungen pausieren und löschen",
                   "Complaint form on the privacy page; admins can pause and delete surveys"),
             "app/feedback.py, app/platform_erase.py"),
            (de_en("Konten löschen sich selbst oder werden von Admins gelöscht", "Accounts delete themselves or are "
                   "deleted by admins"), "app/accounts.py"),
            (de_en("Schalter „Generative KI nutzen“ je Konto: aus, schickt Askurious nichts der Person an KI-Dienste",
                   "Switch “Use generative AI” per account: off, Askurious sends nothing of the person to AI services"),
             "app/ai_access.py, app/ai.py"),
        ]),
        (de_en("Datenminimierung", "Data minimisation"), [
            (de_en("Keine IP-Adressen gespeichert; Land und Bundesland nur auf Wunsch, aus einer Tabelle auf dem Server",
                   "No IP addresses stored; country and federal state only on request, from a table on the server"),
             "app/geoip.py"),
            (de_en("Geräteangaben nur in Klassen, nie die Kennung des Browsers", "Device information in classes only, "
                   "never the browser's user agent"), "app/paradata.py"),
            (de_en("Standort voreingestellt gerundet; nach der Frist für Kennungen vergröbert", "Location rounded by "
                   "default; coarsened after the period for identifiers"), "app/items/geolocation.py, app/retention.py"),
            (de_en("Aufbewahrungsfristen je Befragung, voreingestellt; Löschungen nur als Anzahl je Tag gezählt",
                   "Retention periods per survey, preset; deletions counted only as a number per day"),
             "app/retention.py, app/erasure.py"),
            (de_en("Löschungen gehen bis zu Ende: die Fristen jeder Befragung für sich (ein Fehler hält keine andere auf), "
                   "und Dateien gelöschter Befragungen und Teilnahmen werden in jeder Haushaltsrunde erneut entfernt, "
                   "bis nichts mehr übrig ist",
                   "Deletions are carried through: the periods of each survey on their own (an error holds up no "
                   "other), and files of deleted surveys and participations are removed again in every housekeeping "
                   "round until nothing is left"), "app/retention.py, app/cleanup.py, app/lifecycle.py"),
            (de_en("Papierfragebögen: Scans in Graustufen ohne Metadaten, nicht öffentlich abrufbar, nach der Übernahme "
                   "gelöscht (voreingestellt 30 Tage); Seiten anderer Befragungen werden nicht gespeichert; die PDFs der "
                   "Druckaufträge (Bogennummern mit Löschcodes) gehen mit derselben Frist, sobald alle Bögen erledigt "
                   "sind, spätestens mit den Aufbewahrungsfristen der Befragung",
                   "Paper questionnaires: scans in greyscale without metadata, not publicly reachable, deleted after "
                   "the takeover (30 days by default); pages of other surveys are not stored; the PDFs of the print "
                   "runs (sheet numbers with deletion codes) go after the same time once all sheets are done, at the "
                   "latest with the survey's retention periods"), "app/paper.py"),
            (de_en("KI-Kodierhilfe: nur echte Teilnahmen, gekürzte Texte, keine Speicherung beim Anbieter",
                   "AI coding help: real participations only, shortened texts, no storage at the provider"),
             "app/coding_ai.py"),
            (de_en("Webserver protokolliert keine Adressen und keine Teilnahmeseiten", "The web server logs no addresses "
                   "and no participation pages"), "deploy/docker/nginx"),
        ]),
    ]


def defaults():
    """What is off or minimal until someone switches it on (Art. 25 (2))."""
    return [
        de_en("Experimentelle Funktionen sind für niemanden an, bis Admins sie freigeben.",
              "Experimental features are on for nobody until admins grant them."),
        de_en("Geräteangaben, Seitenzeiten, Fensterwechsel und Einfügen, Land aus der IP-Adresse und das Cookie gegen "
              "Mehrfachteilnahme sind je Befragung aus, bis die Forschenden sie einschalten.",
              "Device information, page times, leaving the window and pasting, country from the IP address and the cookie "
              "against repeat participation are off per survey until the researchers switch them on."),
        de_en("Neue Befragungen beginnen mit Fristen (6 Monate für Kennungen und Kontaktangaben, 10 Jahre für "
              "Antworten); nach echten Teilnahmen lassen sie sich nur verkürzen.",
              "New surveys start with periods (6 months for identifiers and contact data, 10 years for answers); after "
              "real participations they can only be shortened."),
        de_en("Das Element „Standort“ speichert voreingestellt eine gerundete Position.",
              "The element “Location” stores a rounded position by default."),
        de_en("E-Mail, Rechenknecht, Slack und Transkription sind aus, solange die Serverkonfiguration sie nicht nennt.",
              "Email, compute node, Slack and transcription are off as long as the server configuration does not name them."),
        de_en("KI-Funktionen laufen nur, wenn Forschende sie aufrufen.", "AI functions only run when researchers call them."),
    ]


def criteria():
    """The nine criteria of WP 248 rev.01 (Article 29 Working Party, endorsed by the EDPB)."""
    return [de_en("Bewerten oder Einstufen", "Evaluation or scoring"),
            de_en("Automatisierte Entscheidung mit Rechtswirkung", "Automated decision with legal effect"),
            de_en("Systematische Überwachung", "Systematic monitoring"),
            de_en("Vertrauliche oder höchst persönliche Daten", "Sensitive or highly personal data"),
            de_en("Verarbeitung in großem Umfang", "Processing on a large scale"),
            de_en("Abgleichen oder Zusammenführen von Datensätzen", "Matching or combining data sets"),
            de_en("Schutzbedürftige Betroffene", "Vulnerable data subjects"),
            de_en("Innovative Nutzung neuer Technologien", "Innovative use of new technologies"),
            de_en("Hindert an der Ausübung eines Rechts", "Prevents the exercise of a right")]


def risks():
    """Threshold analysis per activity (Art. 35; criteria of WP 248 rev.01: two or more usually call for an impact
    assessment). Proposals; the decision and its reasons are the controller's."""
    return [
        (de_en("Konten und Anmeldung", "Accounts and sign-in"), de_en("keines", "none"),
         de_en("Keine DSFA nötig.", "No impact assessment needed.")),
        (de_en("Teilnahme an Befragungen", "Taking part in surveys"),
         de_en("je nach Befragung: vertrauliche Daten (Art. 9), schutzbedürftige Personen (z. B. Minderjährige), großer "
               "Umfang", "depending on the survey: sensitive data (Art. 9), vulnerable people (e.g. minors), large scale"),
         de_en("Prüfen die Forschenden je Befragung; die Plattform liefert die Maßnahmen dieses Konzepts.",
               "The researchers check per survey; the platform supplies the measures of this concept.")),
        (de_en("Gemeinsame Auswertung validierter Konstrukte", "Pooled analysis of validated constructs"),
         de_en("Zusammenführen von Datensätzen; ggf. großer Umfang", "combining data sets; possibly large scale"),
         de_en("Zwei Kriterien möglich: DSFA empfohlen oder Begründung dokumentieren, warum nicht (Pseudonymisierung, "
               "nur Skalenwerte).", "Two criteria possible: impact assessment recommended, or document why not "
               "(pseudonymisation, scale values only).")),
        (de_en("KI-Kodierhilfe", "AI coding help"),
         de_en("neue Technologie; Freitexte können vertraulich sein; Drittland", "new technology; free texts may be "
               "sensitive; third country"),
         de_en("Zwei Kriterien: DSFA in Anlage 1; vor einer breiten Freigabe mit der datenschutzbeauftragten Person "
               "abstimmen.", "Two criteria: impact assessment in appendix 1; agree it with the data protection officer "
               "before the feature is granted widely.")),
        (de_en("Interviews", "Interviews"),
         de_en("vertrauliche Daten (Stimme, Gesagtes); nicht anonym", "sensitive data (voice, what is said); not anonymous"),
         de_en("Prüfen die Forschenden je Studie. Mit Interviewlinks zusätzlich: Der Link allein gibt Zugang zu den "
               "noch nicht übergebenen Interviews einer Person; die Forschenden geben ihn nur ihr selbst, verpflichten sie "
               "auf Vertraulichkeit und sperren ihn bei Verlust oder Ende der Arbeit. Mit dem Teilnehmerbildschirm: "
               "Der Link öffnet nur die gerade freigegebene Aufgabe eines Interviews auf einem einzigen "
               "Gerät; die Forschenden geben ihn nur der interviewten Person. Mit dem Offline-Modus: "
               "Interviews liegen bis zu sieben Tage auf mobilen Geräten; Maßnahmen: Verschlüsselung mit einer "
               "Geräte-PIN, Frist, Löschen nach der Übertragung, Zurückziehen eines Feldpakets bei Verlust; die "
               "Forschenden beziehen das in ihre Prüfung (ggf. DSFA) ein und verlangen eine Gerätesperre.",
               "The researchers check per project. With interviewer links also: the link alone gives access to a "
               "person's interviews not yet handed over; the researchers give it only to that person, oblige them to "
               "confidentiality and block it on loss or when the work ends. With the participant screen: "
               "the link opens only the task of one interview released at that moment, on a single "
               "device; the researchers give it only to the interviewee. With the offline mode: "
               "interviews stay on mobile devices for up to seven days; measures: encryption with a device PIN, the "
               "period, deletion after the transfer, withdrawing a field pack on loss; the researchers include this in "
               "their check (an impact assessment if needed) and require a device lock.")),
        (de_en("Live-Sitzungen, Kommentare, Rückmeldungen, Protokoll, Betrieb",
               "Live sessions, comments, feedback, log, operation"), de_en("keines", "none"),
         de_en("Keine DSFA nötig.", "No impact assessment needed.")),
    ]


def open_points(org_values, service_rows):
    """What this server does not yet meet or what the published texts leave out, found anew on every call."""
    c = current_app.config
    out = []
    labels = field_labels()
    missing = [labels[k][0] for k in FIELDS if not org_values[k]["value"]]
    if missing:
        out.append(de_en("Nicht ausgefüllt: {f}.", "Not filled in: {f}.", f=", ".join(missing)))
    licence = _licence_text()
    for row in service_rows:
        if row["state"][0] and row["words"] and not any(w in licence for w in row["words"]):
            out.append(de_en("{name} ist an, die Nutzungsbedingungen (licence.md) nennen den Dienst nicht.",
                             "{name} is on, the terms of use (licence.md) do not name the service.", name=row["name"]))
    if c.get("SLACK_WEBHOOK_URL"):
        out.append(de_en("Slack erhält Auszüge von Beschwerden und Namen neuer Konten (USA): Grundlage der Übermittlung "
                         "festhalten oder Auszüge weglassen.", "Slack receives excerpts of complaints and names of new "
                         "accounts (USA): record the basis of the transfer or leave the excerpts out."))
    if "alle vier Stunden" not in licence:
        out.append(de_en("Die Nutzungsbedingungen nennen nur die Sicherung vor Aktualisierungen, nicht die laufenden "
                         "Sicherungen alle vier Stunden (Docker-Betrieb).", "The terms of use name only the backup before "
                         "updates, not the regular backups every four hours (Docker operation)."))
    out.append(de_en("Rückmeldungen und Beschwerden haben keine Löschfrist.", "Feedback and complaints have no deletion "
                     "period."))
    out.append(de_en("Alle Sicherungen liegen auf demselben Server; eine Kopie außerhalb ist vorbereitet, aber aus "
                     "(OFFSITE_TARGET, BETRIEB.md).", "All backups are on the same server; a copy off the server is "
                     "prepared but off (OFFSITE_TARGET, BETRIEB.md)."))
    if not org_values["privacy_restore_test"]["value"]:
        out.append(de_en("Wiederherstellung: Der Code prüft jede Sicherung nur darauf, dass PostgreSQL sie lesen kann "
                         "(pg_restore -l); eine vollständige Wiederherstellung zur Probe belegt er nicht. Einen Test "
                         "durchführen und unter „Letzter Wiederherstellungstest“ eintragen.",
                         "Restore: the code only checks that PostgreSQL can read each backup (pg_restore -l); it does "
                         "not prove a complete trial restore. Run a test and enter it under “Last restore test”."))
    if not org_values["privacy_encryption"]["value"]:
        out.append(de_en("Verschlüsselung: Der Code verschlüsselt Sicherungen auf dem Server nur mit BACKUP_ENCRYPT_KEY "
                         "und die Kopie außerhalb nur mit OFFSITE_TARGET; ob Datenträger und Datenbank verschlüsselt "
                         "sind, kann er nicht wissen. Unter „Verschlüsselung von Datenträger und Datenbank“ eintragen.",
                         "Encryption: the code encrypts backups on the server only with BACKUP_ENCRYPT_KEY and the copy "
                         "off the server only with OFFSITE_TARGET; whether disk and database are encrypted it cannot "
                         "know. Enter it under “Encryption of disk and database”."))
    for row in service_rows:
        if row["state"][0] and row.get("avv") == de_en("offen", "open"):
            out.append(de_en("{name} ist an: Vertrag zur Auftragsverarbeitung eintragen (Abschnitt 1).",
                             "{name} is on: enter the data processing agreement (section 1).", name=row["name"]))
    out.append(de_en("Rechtsgrundlagen, Drittlandübermittlungen und Schwellwertanalyse mit der datenschutzbeauftragten "
                     "Person abstimmen und das Ergebnis hier eintragen.", "Agree legal bases, third-country transfers and "
                     "the threshold analysis with the data protection officer and enter the result here."))
    return out


def dpia_coding(statement=""):
    """Appendix 1: the impact assessment (Art. 35 (7) a-d) of the AI coding help (app/coding_ai.py), from the draft
    of 2026-10-10 (Ralf: as an appendix of this concept, for admins only).
    Limits, provider and switches come from the code and this server's configuration; the ratings are proposals for
    the controller and the data protection officer (statement: the field DPIA_FIELD)."""
    from app import coding_ai

    c = current_app.config
    openai_on = bool(c.get("OPENAI_API_KEY"))
    _, coding_text = _feature_state("coding")
    _, own_text = _own_access_state()
    model = c.get("OPENAI_TEXT_MODEL", "")
    n, chars = coding_ai.MAX_TEXTS, coding_ai.MAX_CHARS
    yes, no, maybe = de_en("ja", "yes"), de_en("nein", "no"), de_en("möglich", "possible")
    low, mid, high = de_en("gering", "low"), de_en("mittel", "medium"), de_en("hoch", "high")
    gone = de_en("entfällt", "does not apply")
    return {
        "result": de_en(
            "Ergebnis (Vorschlag): Mit den vorhandenen Maßnahmen ist das Restrisiko mittel, solange die Antworttexte über "
            "OpenAI (USA) gehen, und gering über einen Anbieter in Deutschland wie KI:connect.nrw (eigener KI-Zugang). "
            "Eine vorherige Konsultation der Aufsichtsbehörde (Art. 36) ist in keinem Fall nötig. Für eine breite "
            "Freigabe sollten ein Anbieter in der EU als Standardweg und der geprüfte Vertrag mit OpenAI vorliegen.",
            "Result (proposal): with the measures in place the remaining risk is medium as long as the answer texts go "
            "over OpenAI (USA), and low over a provider in Germany such as KI:connect.nrw (own AI access). A prior "
            "consultation of the supervisory authority (Art. 36) is needed in neither case. Before granting it widely, an "
            "EU provider as the default route and the checked contract with OpenAI should be in place."),
        "scope": [
            de_en("Gegenstand: die Vorschläge der KI auf der Seite „Kategorisieren“ (experimentelle Funktion „coding“), "
                  "also ein Kategorienschema und die Zuordnung offener Antworten. Hier: {state}.",
                  "Subject: the AI's suggestions on the page “Kategorisieren” (experimental feature “coding”), i.e. a "
                  "category scheme and the assignment of open answers. Here: {state}.", state=coding_text),
            de_en("Für die Antworttexte sind die Forschenden verantwortlich, der Betreiber ist Auftragsverarbeiter "
                  "(Art. 28 Abs. 3 lit. f) und bewertet hier, ob die Funktion sicher angeboten werden kann (Art. 25). "
                  "Ob eine einzelne Befragung eine eigene DSFA braucht (Thema, Teilnehmende, besondere Kategorien), prüfen "
                  "die Forschenden.",
                  "The researchers are the controllers of the answer texts; the provider of the platform is a processor "
                  "(Art. 28 (3) (f)) and assesses here whether the feature can be offered safely (Art. 25). Whether a "
                  "single survey needs its own impact assessment (topic, participants, special categories) the researchers "
                  "check."),
        ],
        "facts": [
            (de_en("Zweck", "Purpose"),
             de_en("Kategorienschema vorschlagen (qualitative Inhaltsanalyse nach Mayring) und offene Antworten den "
                   "Kategorien zuordnen; nur Vorschläge, eine Person übernimmt sie.",
                   "Propose a category scheme (qualitative content analysis after Mayring) and assign open answers to the "
                   "categories; suggestions only, a person takes them over.")),
            (de_en("Betroffene", "Data subjects"), de_en("Teilnehmende einer Befragung", "Participants of a survey")),
            (de_en("Daten an den KI-Dienst", "Data sent to the AI service"),
             de_en("Fragetext, Namen vorhandener Kategorien, Freitextantworten: höchstens {n} je Aufruf, je höchstens "
                   "{chars} Zeichen, nur echte Teilnahmen.", "Question text, names of existing categories, free-text "
                   "answers: at most {n} per call, at most {chars} characters each, real participations only.",
                   n=n, chars=chars)),
            (de_en("Nicht übermittelt", "Not sent"),
             de_en("Kennungen, Zeitstempel, andere Antworten und Angaben zur Teilnahme.",
                   "Identifiers, timestamps, other answers and details of the participation.")),
            (de_en("Empfänger", "Recipients"),
             de_en("Schlüssel der Installation: OpenAI, USA ({openai}). Eigener KI-Zugang ({own}): KI:connect.nrw "
                   "(Deutschland), OpenAI oder eine eingetragene Adresse.",
                   "The installation's key: OpenAI, USA ({openai}). Own AI access ({own}): KI:connect.nrw (Germany), "
                   "OpenAI or a typed address.",
                   openai=(de_en("hier an", "on here") + (f", {model}" if model else "")) if openai_on
                   else de_en("hier aus", "off here"), own=own_text or de_en("hier aus", "off here"))),
            (de_en("Speicherung beim Anbieter", "Storage at the provider"),
             de_en("Aufruf mit store=False, keine Ablage für spätere Abrufe; OpenAI bewahrt Eingaben bis zu 30 Tage zur "
                   "Missbrauchskontrolle auf und trainiert nicht damit.", "Calls with store=False, nothing kept for later "
                   "retrieval; OpenAI keeps input for up to 30 days to monitor abuse and does not train on it.")),
            (de_en("Speicherung hier", "Storage here"),
             de_en("Vorschläge im Kodierschema und an der Teilnahme; gelöscht mit der Befragung oder der Teilnahme. "
                   "Verbrauch ohne Inhalte 730 Tage.", "Suggestions in the coding scheme and at the participation; deleted "
                   "with the survey or the participation. Usage without content 730 days.")),
            (de_en("Rechtsgrundlage (Vorschlag)", "Legal basis (proposal)"),
             de_en("Einwilligung der Teilnehmenden (Art. 6 Abs. 1 lit. a; bei besonderen Kategorien Art. 9 Abs. 2 lit. "
                   "a); die Person, der die Befragung gehört, oder ein Admin bestätigt je Befragung und Empfänger, dass "
                   "informiert wurde.",
                   "Consent of the participants (Art. 6 (1) (a); for special categories Art. 9 (2) (a)); the researchers "
                   "(the owner or an admin) confirm per survey and recipient that participants were told.")),
        ],
        "threshold": [
            (crit, met, why) for crit, (met, why) in zip(criteria(), [
                (no, de_en("Kategorisiert werden Texte, keine Personen.", "Texts are categorised, not people.")),
                (no, de_en("Nur Vorschläge, eine Person entscheidet.", "Suggestions only, a person decides.")),
                (no, de_en("Einmal auf Klick, keine laufende Beobachtung.", "Once on a click, no ongoing observation.")),
                (maybe, de_en("Freitexte können Gesundheit, Haltungen oder Namen Dritter enthalten.",
                              "Free texts can hold health, attitudes or names of others.")),
                (no, de_en("Höchstens {n} Texte einer Frage je Aufruf.", "At most {n} texts of one question per call.",
                           n=n)),
                (no, de_en("Nur die Texte einer Frage.", "Only the texts of one question.")),
                (de_en("je Befragung", "per survey"), de_en("Etwa Minderjährige oder Patientinnen und Patienten.",
                                                         "For example minors or patients.")),
                (yes, de_en("Großes Sprachmodell eines externen Anbieters.", "Large language model of an outside "
                            "provider.")),
                (no, de_en("Teilnahme und Löschung bleiben unberührt.", "Taking part and deletion stay untouched.")),
            ])],
        "necessity": [
            de_en("Ohne die Antworttexte kann ein Modell keine Kategorien vorschlagen; Kodieren von Hand bleibt jederzeit "
                  "möglich.", "Without the answer texts a model cannot propose categories; coding by hand stays possible "
                  "at any time."),
            de_en("Datenminimierung: nur die Texte einer Frage, gekürzt auf {chars} Zeichen, ohne Kennungen.",
                  "Data minimisation: only the texts of one question, cut to {chars} characters, without identifiers.",
                  chars=chars),
            de_en("Alternativen: ein Anbieter in Deutschland (KI:connect.nrw) oder ein eigenes Modell über eine "
                  "eingetragene Adresse.", "Alternatives: a provider in Germany (KI:connect.nrw) or a model of your own at a "
                  "typed address."),
            de_en("Transparenz: Hinweis neben jeder Schaltfläche, Textbaustein für Information und Einwilligung, Anteil "
                  "der KI im Kodierleitfaden.", "Transparency: a note next to every button, a paragraph for information "
                  "and consent, the AI's share in the coding guide."),
            de_en("Betroffenenrechte: Die Löschung einer Teilnahme löscht die Vorschläge daran; was beim Anbieter zur "
                  "Missbrauchskontrolle liegt, kann die Plattform nicht löschen.", "Rights of data subjects: deleting a "
                  "participation deletes its suggestions; what the provider keeps to monitor abuse the platform cannot "
                  "delete."),
        ],
        "risks": [
            ("R1", de_en("Antworttexte mit Gesundheits- oder anderen besonderen Angaben verlassen die Plattform",
                         "Answer texts with health or other special details leave the platform"), high, mid, low),
            ("R2", de_en("Zugriff von Behörden im Drittland (USA)", "Access by authorities in the third country (USA)"),
             mid, mid, gone),
            ("R3", de_en("Anbieter nutzt Texte über den Auftrag hinaus (Training, Aufbewahrung)",
                         "Provider uses texts beyond the order (training, keeping)"), mid, low, low),
            ("R4", de_en("Re-Identifizierung, weil ein Text Namen oder seltene Umstände nennt",
                         "Re-identification because a text names people or rare circumstances"), mid, mid, low),
            ("R5", de_en("Teilnehmende wissen nicht, dass ihre Texte an eine KI gehen",
                         "Participants do not know that their texts go to an AI"), high, low, low),
            ("R6", de_en("Falsche oder verzerrte Zuordnung fließt ungeprüft in Ergebnisse",
                         "Wrong or biased assignment flows unchecked into results"), mid, low, low),
            ("R7", de_en("Wörtliches Zitat in einem Ankerbeispiel überdauert die Löschung einer Antwort",
                         "A verbatim quote in an anchor example outlives the deletion of an answer"), low, low, low),
            ("R8", de_en("Schlüssel des eigenen KI-Zugangs wird ausgelesen", "The key of a person's own AI access is read out"),
             mid, low, low),
        ],
        "measures": [
            (de_en("Experimentell, nur nach Freigabe durch Admins", "Experimental, only once admins grant it"), "R1, R5",
             "app/experimental.py"),
            (de_en("Bestätigung je Befragung und Empfänger, dass informiert wurde, nur durch die Person, der die "
                   "Befragung gehört, oder ein Admin; Textbaustein je Empfänger für die Einwilligung",
                   "Confirmation per survey and recipient that participants were told, only by the owner or an admin "
                   "of the survey; a paragraph per recipient for the consent"), "R5",
             "app/coding.py (ai_informed, ai_notice)"),
            (de_en("Nur echte Teilnahmen, höchstens {n} Texte zu je {chars} Zeichen, ohne Kennungen",
                   "Real participations only, at most {n} texts of {chars} characters each, without identifiers",
                   n=n, chars=chars), "R1, R4", "app/coding_ai.py"),
            (de_en("Aufruf mit store=False", "Calls with store=False"), "R3", "app/ai.py"),
            (de_en("Ankerbeispiele umschrieben, ohne Zitate, Namen oder Orte", "Anchor examples paraphrased, without "
                   "quotes, names or places"), "R4, R7", "app/coding_ai.py (SCHEME_INSTRUCTIONS)"),
            (de_en("Nur Vorschläge, Übernahme durch eine Person, Anteil der KI im Kodierleitfaden", "Suggestions only, "
                   "accepted by a person, the AI's share in the coding guide"), "R6", "app/coding_ai.py"),
            (de_en("Eigener KI-Zugang mit Anbieter in Deutschland; Schlüssel verschlüsselt, nie wieder angezeigt",
                   "Own AI access with a provider in Germany; key encrypted, never shown again"), "R2, R8",
             "app/ai_access.py"),
            (de_en("Eigener KI-Zugang: Verbindung nur zur geprüften öffentlichen Adresse, kein Proxy, ganzer Aufruf "
                   "höchstens 90 Sekunden, Antwort höchstens 2 MB; „Generative KI nutzen“ aus stoppt alle Aufrufe, "
                   "auch Speichern, Prüfen und die Transkription",
                   "Own AI access: connection only to the checked public address, no proxy, the whole call at most 90 "
                   "seconds, an answer at most 2 MB; “Use generative AI” off stops every call, also saving, checking "
                   "and the transcription"), "R1",
             "app/ai_access.py (GuardedTransport), app/interview_transcribe.py"),
            (de_en("Löschen einer Teilnahme löscht die Vorschläge daran", "Deleting a participation deletes its "
                   "suggestions"), "R7", "app/models.py (Response.coding)"),
        ],
        "proposed": [
            de_en("Anbieter in der EU (z. B. KI:connect.nrw) als Standardweg für Antworttexte; OpenAI nur, wenn Forschende "
                  "es bewusst wählen.", "A provider in the EU (e.g. KI:connect.nrw) as the default route for answer texts; "
                  "OpenAI only when researchers choose it on purpose."),
            de_en("Vertrag mit OpenAI prüfen und ablegen: Auftragsverarbeitung (Art. 28), Grundlage der Übermittlung "
                  "(Art. 45 oder 46), Aufbewahrung zur Missbrauchskontrolle, kein Training.",
                  "Check and file the contract with OpenAI: processing on behalf (Art. 28), basis of the transfer "
                  "(Art. 45 or 46), keeping to monitor abuse, no training."),
            de_en("Vor dem Senden offensichtliche Namen, E-Mail-Adressen und Telefonnummern maskieren.",
                  "Mask obvious names, email addresses and phone numbers before sending."),
            de_en("Im Textbaustein für die Einwilligung Anbieter und Land je nach Weg nennen.",
                  "Name the provider and country in the consent paragraph according to the route."),
        ],
        "review": de_en("Überprüfung jährlich und bei jeder Änderung an Anbieter, Modell, Datenumfang oder Freigabe "
                        "(Art. 35 Abs. 11).", "Review every year and with every change of provider, model, amount of data "
                        "or grant (Art. 35 (11))."),
        "statement": statement,
    }


def sources():
    return [
        de_en("Verordnung (EU) 2016/679 (DSGVO), Art. 5, 24, 25, 28, 30, 32, 35, 44–46. https://eur-lex.europa.eu/eli/reg/2016/679/oj",
              "Regulation (EU) 2016/679 (GDPR), Art. 5, 24, 25, 28, 30, 32, 35, 44–46. https://eur-lex.europa.eu/eli/reg/2016/679/oj"),
        de_en("Datenschutzkonferenz (2024). Das Standard-Datenschutzmodell, Version 3.1. "
              "https://www.datenschutzkonferenz-online.de/media/ah/SDM-Methode-V31.pdf",
              "German Data Protection Conference (2024). The Standard Data Protection Model, version 3.1. "
              "https://www.datenschutzkonferenz-online.de/media/ah/SDM-Methode-V31.pdf"),
        de_en("Datenschutzkonferenz (2018). Kurzpapier Nr. 1: Verzeichnis von Verarbeitungstätigkeiten; Kurzpapier Nr. 5: "
              "Datenschutz-Folgenabschätzung. https://www.datenschutzkonferenz-online.de/kurzpapiere.html",
              "German Data Protection Conference (2018). Short paper no. 1: Record of processing activities; short paper "
              "no. 5: Data protection impact assessment. https://www.datenschutzkonferenz-online.de/kurzpapiere.html"),
        de_en("Artikel-29-Datenschutzgruppe (2017). Leitlinien zur Datenschutz-Folgenabschätzung, WP 248 rev.01.",
              "Article 29 Working Party (2017). Guidelines on Data Protection Impact Assessment, WP 248 rev.01."),
    ]


def headings():
    """{key: (anchor, title)} of the sections, the same on the page and in the Word file."""
    return {
        "scope": ("zweck", de_en("1. Zweck, Geltungsbereich und Verantwortung", "1. Purpose, scope and responsibility")),
        "roles": ("rollen", de_en("2. Personengruppen und ihre Rechte", "2. Groups of people and their rights")),
        "record": ("verzeichnis", de_en("3. Verzeichnis der Verarbeitungstätigkeiten (Art. 30)",
                                        "3. Record of processing activities (Art. 30)")),
        "flow": ("datenfluss", de_en("4. Datenfluss einer Befragung", "4. Data flow of a survey")),
        "services": ("dienste", de_en("5. Empfänger und externe Dienste (Art. 28, 44–46)",
                                      "5. Recipients and outside services (Art. 28, 44–46)")),
        "measures": ("massnahmen", de_en("6. Technische und organisatorische Maßnahmen (Art. 32)",
                                         "6. Technical and organisational measures (Art. 32)")),
        "defaults": ("voreinstellungen", de_en("7. Datenschutzfreundliche Voreinstellungen (Art. 25)",
                                               "7. Privacy by default (Art. 25)")),
        "risks": ("risiko", de_en("8. Schwellwertanalyse zur Datenschutz-Folgenabschätzung (Art. 35)",
                                  "8. Threshold analysis for an impact assessment (Art. 35)")),
        "breach": ("datenpannen", de_en("9. Datenpannen (Art. 33, 34)", "9. Data breaches (Art. 33, 34)")),
        "open": ("offen", de_en("10. Offene Punkte", "10. Open points")),
        "sources": ("quellen", de_en("11. Quellen", "11. Sources")),
        "dpia": ("dsfa-kodierhilfe", de_en("Anlage 1: Datenschutz-Folgenabschätzung KI-Kodierhilfe (Art. 35)",
                                           "Appendix 1: Impact assessment of the AI coding help (Art. 35)")),
    }


def intro():
    return [
        de_en("Dieses Konzept zeigt, welche personenbezogenen Daten die Plattform verarbeitet, wer sie verarbeitet (je "
              "Personengruppe), welche davon an externe Dienste gehen und welche Maßnahmen sie schützen. Es wird bei jedem "
              "Aufruf aus dem Programmcode und der Konfiguration dieses Servers erzeugt und zeigt deshalb, was hier "
              "eingeschaltet ist.",
              "This concept shows which personal data the platform processes, who processes them (per group of people), "
              "which of them go to outside services and which measures protect them. It is built on every call from the "
              "code and the configuration of this server, so it shows what is switched on here."),
        de_en("Die Gliederung folgt den Nachweispflichten der DSGVO (Art. 5 Abs. 2, 24, 25, 30, 32, 35); die Maßnahmen "
              "sind nach den Gewährleistungszielen des Standard-Datenschutzmodells 3.1 geordnet. Rechtsgrundlagen, "
              "Drittlandübermittlungen und die Schwellwertanalyse sind Vorschläge, die der Verantwortliche mit der "
              "datenschutzbeauftragten Person bestätigt.",
              "The structure follows the GDPR's duties of proof (Art. 5 (2), 24, 25, 30, 32, 35); the measures are "
              "ordered by the protection goals of the Standard Data Protection Model 3.1. Legal bases, third-country "
              "transfers and the threshold analysis are proposals that the controller confirms with the data protection "
              "officer."),
    ]


def breach():
    return de_en("Eine Verletzung des Schutzes personenbezogener Daten meldet der Verantwortliche binnen 72 Stunden der "
                 "Aufsichtsbehörde (Art. 33), bei hohem Risiko auch den betroffenen Personen (Art. 34). Für Befragungen "
                 "meldet der Betreiber als Auftragsverarbeiter den Forschenden unverzüglich (Nutzungsbedingungen, "
                 "Abschnitt 2).",
                 "The controller reports a personal data breach to the supervisory authority within 72 hours (Art. 33), "
                 "and to the people concerned if the risk is high (Art. 34). For surveys the provider, as processor, "
                 "tells the researchers without delay (terms of use, section 2).")


def flow():
    """Section 4: the way of the data of a survey from consent to deletion, step by step with the data, the measures
    and who handles them, as the code does it (comparison with the RWTH example table of data flows, D1). The page
    "Datenfluss (Word)" of a survey fills the same steps with its own settings."""
    return [
        {"step": de_en("Einwilligung", "Consent"),
         "data": de_en("Zustimmung oder Ablehnung, die Bestätigungen (z. B. Freiwilligkeit, ausdrückliche Einwilligung "
                       "in besondere Kategorien, Tonaufnahme)", "Agreement or refusal, the confirmations (e.g. voluntary "
                       "participation, explicit consent to special categories, audio recording)"),
         "measures": de_en("Standardtext mit den Angaben nach Art. 13 (Rechtsgrundlage, Widerruf, Empfänger, "
                           "Speicherdauer); Datenschutzseite je Befragung; Ablehnen beendet die Teilnahme; was "
                           "Teilnehmende bestätigen, steht ab dem Start fest", "Standard text with the information under "
                           "Art. 13 (legal basis, withdrawal, recipients, retention); privacy page per survey; refusing ends "
                           "the participation; what participants confirm is fixed from the start"),
         "who": de_en("Forschende (Text), Teilnehmende", "Researchers (text), participants"),
         "where": "app/items/consent.py"},
        {"step": de_en("Rekrutierung", "Recruitment"),
         "data": de_en("Teilnahmelink; je nach Einstellung Kennungen aus dem Aufruf-Link (z. B. Panel), Einladungen per "
                       "E-Mail, Abschlusslinks zum Panel", "Participation link; depending on the settings identifiers "
                       "from the link (e.g. a panel), invitations by email, completion links to the panel"),
         "measures": de_en("Kennungen nur gespeichert, wenn eingestellt, und nach Frist gelöscht; Einladungsadressen "
                           "getrennt von den Antworten; an das Panel nur Kennung und Stand, keine Antworten",
                           "Identifiers stored only when set and deleted after a period; invitation addresses apart from "
                           "the answers; only identifier and state go to the panel, no answers"),
         "who": de_en("Forschende, Panelanbieter", "Researchers, panel providers"),
         "where": "app/panel.py, app/survey_invites.py"},
        {"step": de_en("Registrierung und Pseudonym", "Registration and pseudonym"),
         "data": de_en("Zufällige Teilnahme-Kennung der Plattform; je nach Einstellung Kennung aus dem Aufruf-Link, "
                       "persönlicher Code für mehrere Wellen, Code einer Tagebuchstudie, Hash des Löschcodes",
                       "Random participation ID of the platform; depending on the settings an identifier from the link, "
                       "personal code for several waves, code of a diary study, hash of the deletion code"),
         "measures": de_en("Keine Namen in den Antworten; die Liste, die Kennungen Personen zuordnet, führt nur, wer "
                           "rekrutiert (Panelanbieter oder Forschende) und nicht die Plattform; Löschcode nur als Hash",
                           "No names in the answers; the list that links identifiers to people is kept only by whoever "
                           "recruits (panel provider or researchers), not by the platform; deletion code only as a hash"),
         "who": de_en("Forschende, Panelanbieter", "Researchers, panel providers"),
         "where": "app/panel.py, app/participate.py"},
        {"step": de_en("Erhebung", "Collection"),
         "data": de_en("Antworten, Zeitpunkte, Sprache; je nach Einstellung Paradaten (Gerätetyp, Seitenzeiten, "
                       "Fensterwechsel), Region aus der IP-Adresse, Standort, Sprachaufnahmen",
                       "Answers, times, language; depending on the settings paradata (device type, page times, leaving "
                       "the window), region from the IP address, location, voice recordings"),
         "measures": de_en("Keine IP-Adressen, keine Tracking-Cookies, nur ein Sitzungs-Cookie; Paradaten nur grob und "
                           "nur eingeschaltet; Löschen im Browser bis 15 Minuten nach dem Ende oder mit Löschcode",
                           "No IP addresses, no tracking cookies, only a session cookie; paradata coarse and only when "
                           "switched on; deleting in the browser up to 15 minutes after the end or with a deletion code"),
         "who": de_en("Teilnehmende, Plattform", "Participants, platform"), "where": "app/participate.py"},
        {"step": de_en("Übermittlung", "Transfer"),
         "data": de_en("Alle Eingaben zwischen Browser oder App und Server", "All input between browser or app and server"),
         "measures": de_en("TLS mit HSTS; Cookies Secure, HttpOnly, SameSite; Teilnahmeseite getrennt von der Seite der "
                           "Forschenden, wo eingerichtet", "TLS with HSTS; cookies Secure, HttpOnly, SameSite; participant "
                           "side apart from the researchers' side where set up"),
         "who": de_en("Plattform", "Platform"), "where": "config.py, app/hosts.py"},
        {"step": de_en("Speicherung", "Storage"),
         "data": de_en("Datenbank und hochgeladene Dateien auf dem Server des Betreibers; Sicherungen",
                       "Database and uploaded files on the server of the provider of the platform; backups"),
         "measures": de_en("Betreiber als Auftragsverarbeiter; Rechte je Rolle; Sicherungen alle vier Stunden, auf "
                           "Wunsch verschlüsselt; Siegelkette gegen stille Änderungen", "Provider of the platform as processor; rights per "
                           "role; backups every four hours, encrypted if set up; seal chain against silent changes"),
         "who": de_en("Betreiber", "Provider of the platform"), "where": "deploy/docker/backup.sh, app/seal.py"},
        {"step": de_en("Auswertung", "Analysis"),
         "data": de_en("Ergebnisse und Exporte; Freitexte für die KI-Kodierung nur nach Bestätigung",
                       "Results and exports; free texts for AI coding only after confirmation"),
         "measures": de_en("Rohdaten nur mit Bearbeiten oder Verwalten; Dashboards zusammengefasst; Zugriffsprotokoll; "
                           "KI ohne Speicherung beim Anbieter für späteren Abruf", "Raw data only with Edit or Manage; "
                           "dashboards aggregated; access log; AI without storage at the provider for later retrieval"),
         "who": de_en("Forschende", "Researchers"), "where": "app/surveys/, app/coding_ai.py, app/audit.py"},
        {"step": de_en("Veröffentlichung", "Publication"),
         "data": de_en("Anonyme Datensätze, Fragebogen und Codebuch (Open Data), keine Tonaufnahmen",
                       "Anonymous data sets, questionnaire and codebook (open data), no voice recordings"),
         "measures": de_en("Nur auf Veranlassung der Forschenden; offene Antworten (Freitexte, Postleitzahlen, genaue "
                           "Daten, Kartenpositionen) nur mit eigenem Häkchen; Ablage bei Zenodo oder Coscine",
                           "Only when the researchers start it; open answers (free texts, postal codes, exact dates, map "
                           "positions) only with a tick of their own; deposit with Zenodo or Coscine"),
         "who": de_en("Forschende", "Researchers"), "where": "app/zenodo.py, app/coscine.py"},
        {"step": de_en("Löschung", "Deletion"),
         "data": de_en("Kennungen und getrennte Angaben, alle Antworten, Sicherungen",
                       "Identifiers and separate details, all answers, backups"),
         "measures": de_en("Fristen je Befragung (voreingestellt 6 Monate für Kennungen, 10 Jahre für Antworten), "
                           "stündlicher Lauf; Löschen durch Teilnehmende und Forschende; Sicherungen laufen nach 14 Tagen "
                           "aus", "Periods per survey (by default 6 months for identifiers, 10 years for answers), hourly "
                           "sweep; deletion by participants and researchers; backups expire after 14 days"),
         "who": de_en("Plattform, Forschende, Teilnehmende", "Platform, researchers, participants"),
         "where": "app/retention.py, app/erasure.py"},
    ]


def sdm_art32():
    """For the Word file: which protection goal of the SDM answers which letter of Art. 32 (1) GDPR (comparison with
    the RWTH TOM list, T6). Unlinkability and intervenability serve Art. 5 and 25 rather than Art. 32."""
    return [
        (de_en("Datenminimierung", "Data minimisation"), "lit. a",
         de_en("Pseudonymisierung (Kennungen, Ersatzkennungen im Pool)", "Pseudonymisation (identifiers, substitute "
               "identifiers in the pool)")),
        (de_en("Vertraulichkeit", "Confidentiality"), "lit. a, b",
         de_en("Verschlüsselung der Übertragung und der Geheimnisse; Vertraulichkeit der Systeme",
               "Encryption of transfer and secrets; confidentiality of the systems")),
        (de_en("Integrität", "Integrity"), "lit. b", de_en("Integrität der Systeme und Dienste",
                                                           "Integrity of the systems and services")),
        (de_en("Verfügbarkeit und Belastbarkeit", "Availability and resilience"), "lit. b, c",
         de_en("Verfügbarkeit und Belastbarkeit; Wiederherstellung nach einem Zwischenfall",
               "Availability and resilience; restore after an incident")),
        (de_en("Transparenz", "Transparency"), "lit. d",
         de_en("Verfahren zur regelmäßigen Überprüfung (dieses Konzept, Protokolle, offene Punkte)",
               "Procedure for regular review (this concept, logs, open points)")),
        (de_en("Nichtverkettung", "Unlinkability"), "—", de_en("Art. 5 Abs. 1 lit. b, Art. 25", "Art. 5 (1) (b), Art. 25")),
        (de_en("Intervenierbarkeit", "Intervenability"), "—", de_en("Art. 12–22, Art. 25", "Art. 12–22, Art. 25")),
    ]


def controls():
    """Section 6 in the order of the RWTH TOM list (comparison with the templates 2026-10-20, point 1): per control type
    the letter of Art. 32 (1) and the measures of this section that serve it. What the code cannot show (access to the
    rooms, the date of a restore test) points to the field of section 1."""
    return [
        (de_en("Pseudonymisierung", "Pseudonymisation"), "lit. a",
         de_en("Kennungen nur auf Wunsch und mit Löschfrist; Ersatzkennungen im Pool (POOL_KEY); getrennt gespeicherte "
               "Kontaktangaben; Personenkennung der Tagebuch-App zufällig", "Identifiers only when wanted and with a "
               "deletion period; substitute identifiers in the pool (POOL_KEY); contact details stored apart; random "
               "person identifier of the diary app")),
        (de_en("Verschlüsselung", "Encryption"), "lit. a",
         de_en("TLS mit HSTS; Geheimnisse der Zwei-Faktor-Anmeldung verschlüsselt; Sicherungen verschlüsselt, wenn "
               "BACKUP_ENCRYPT_KEY gesetzt ist; Datenträger und Datenbank: Feld in Abschnitt 1",
               "TLS with HSTS; secrets of the two-factor sign-in encrypted; backups encrypted when BACKUP_ENCRYPT_KEY is "
               "set; disk and database: field in section 1")),
        (de_en("Zutrittskontrolle", "Physical access control"), "lit. b",
         de_en("Nicht im Code: Feld „Serverstandort und physische Sicherung“ in Abschnitt 1",
               "Not in the code: field “Server site and physical protection” in section 1")),
        (de_en("Zugangskontrolle", "System access control"), "lit. b",
         de_en("Anmeldung mit ORCID oder Passwort (scrypt-Hash, Sperrliste, Pause und Sperre nach Fehlversuchen); zweiter "
               "Faktor für Admins; Sitzungs-Cookies Secure, HttpOnly, __Host-", "Sign-in with ORCID or a password (scrypt "
               "hash, block list, pause and lock after failed attempts); second factor for admins; session cookies "
               "Secure, HttpOnly, __Host-")),
        (de_en("Zugriffskontrolle", "Data access control"), "lit. b",
         de_en("Rollen je Befragung (Ansehen, Bearbeiten, Verwalten), Rohdaten nur mit Bearbeiten oder Verwalten; "
               "Admins sehen keine Antworten; Sicherungen für Admins nur als Liste", "Roles per survey (view, edit, "
               "manage), raw data only with Edit or Manage; admins see no answers; backups only as a list for admins")),
        (de_en("Weitergabekontrolle", "Transfer control"), "lit. b",
         de_en("Verschlüsselte Übertragung; externe Dienste nur mit Schalter (Abschnitt 5); KI ohne Speicherung beim "
               "Anbieter, Kontaktdaten vorher entfernt; Exporte im Zugriffsprotokoll", "Encrypted transfer; outside "
               "services only with a switch (section 5); AI without storage at the provider, contact details removed "
               "first; exports in the access log")),
        (de_en("Eingabekontrolle", "Input control"), "lit. b",
         de_en("Protokoll datenschutzrelevanter Handlungen; Bearbeitungsspuren; Siegelkette über beendete Teilnahmen und "
               "Löschungen", "Log of data-protection actions; editing traces; seal chain over finished participations "
               "and deletions")),
        (de_en("Verfügbarkeitskontrolle", "Availability control"), "lit. b, c",
         de_en("Sicherungen alle vier Stunden und vor jeder Aktualisierung, geprüft mit pg_restore -l; Wiederherstellung: "
               "Feld in Abschnitt 1", "Backups every four hours and before every update, checked with pg_restore -l; "
               "restore: field in section 1")),
        (de_en("Belastbarkeit", "Resilience"), "lit. b",
         de_en("Zwei Teilnahme-Container, die nacheinander neu starten; Sicherungsfenster ohne halb gespeicherte "
               "Antworten; Schätzungen als eigener Prozess niedriger Priorität", "Two participant containers that restart "
               "one after the other; backup window without half-stored answers; estimations as a process of their own "
               "at low priority")),
        (de_en("Datenschutz-Management", "Data protection management"), "lit. d",
         de_en("Dieses Konzept, bei jedem Aufruf neu erzeugt; offene Punkte; nächste Überprüfung (Abschnitt 1); "
               "Prüfung der Rechtstexte bei jeder Änderung des Codes", "This concept, built anew on every call; open "
               "points; next review (section 1); check of the legal texts with every change of the code")),
    ]


def build():
    """The whole concept as plain data, in the language of the page."""
    from app import brand, terms

    org_values = org()
    service_rows = services(org_values)
    c = current_app.config
    return {
        "meta": {"brand": brand.name(), "base": brand.public_base(), "date": local_today().isoformat(),
                 "terms": terms.version(), "sides": bool(c.get("PARTICIPANT_BASE_URL"))},
        "org": org_values, "labels": field_labels(),
        "roles": roles(), "activities": activities(), "flow": flow(), "sdm_art32": sdm_art32(), "controls": controls(), "services": service_rows,
        "measures": measures(), "defaults": defaults(), "criteria": criteria(), "risks": risks(),
        "open": open_points(org_values, service_rows), "sources": sources(),
        "headings": headings(), "intro": intro(), "breach": breach(), "org_keys": ORG_FIELDS,
        "dpia": dpia_coding(org_values[DPIA_FIELD]["value"]),
    }
