"""The access log ("Protokoll", Administration): who downloaded raw or contact data, changed who has access, deleted
participations or accounts, or copied the database, and when (security audit 2026-10-04, N2); since the audit of
2026-10-06 (S16) also group members and shares, interview downloads and deleted own accounts. Only what data
protection needs (Ralf, 2026-10-06): sign-ins, one-time links, passwords, experimental features, the right to
create surveys and the live Q&A list are not written down; older rows of such actions go with the housekeeping.

One row per request of the endpoints in ACTIONS, written after the request when it was answered (status below
400): the person (number and name with ORCID iD, kept as text so that the row stays readable after an account or
a survey is gone), the survey, what was done and a short detail (export kind and data pool, the iD and role of a
changed co-owner). Never answers, never contact data, never a password. Rows are kept KEEP_DAYS days and then
removed by the housekeeping (lifecycle.close_due_surveys -> sweep). Only site admins see the log.

The action is stored as its German text, as before: that text is the key of the action (the housekeeping compares
it with ACTIONS), and the page shows it in the language of the person who reads the log (ACTIONS_EN, filter
log_action; decision E9, 2026-10-06). A stored text no action has any more stays as it is.
"""
from datetime import timedelta

from flask import g, request

from app.extensions import db
from app.lang import english

KEEP_DAYS = 365

# endpoint: what the log says. Downloads count when the file went out (200); changes when they were answered
# without an error (a redirect back counts: the page then says whether it worked).
ACTIONS = {
    "diary.offline_review": "Offline-Antworten geprüft",
    "paper.sheet_discard": "Papierbogen verworfen",
    "paper.page_remove": "Papierseite gelöscht",
    # raw data
    "surveys.export_csv": "Datenexport CSV",
    "surveys.export_spss": "Datenexport SPSS (ZIP)",
    "surveys.export_sav": "Datenexport .sav",
    "surveys.export_omv": "Datenexport .omv",
    "surveys.export_rds": "Datenexport .rds",
    "data_access.api_csv": "Datenexport CSV (Token)",
    "data_access.api_rds": "Datenexport .rds (Token)",
    "data_access.api_codebook": "Codebuch (Token)",
    # the AI agent interface (app/mcp.py, app/agent_access.py): every tool call of an agent, with tool and token
    "mcp.endpoint": "Agenten-Zugriff",
    "surveys.export_conjoint": "Datenexport Langformat",
    "surveys.export_vignettes": "Datenexport Vignetten (Langformat)",
    "surveys.export_voice": "Sprachnachrichten exportiert",
    "waves.export_merged": "Datenexport Wellen zusammengeführt",
    "surveys.results_panel": "Wellen verknüpft ausgewertet",
    "surveys.snapshot_file": "Sicherung heruntergeladen",
    "surveys.zenodo_upload": "Zenodo",
    "coscine.store": "Coscine",
    "constructs.linked_data": "Daten eines verknüpften Elements",
    "constructs.pool_csv": "Konstrukt-Pool",
    "admin.construct_compose_file": "Konstrukt-Pool (Zusammenstellung)",
    # interviews (guide with "Interviews erheben")
    "interview_results.overview_file": "Interviews: Fallübersicht",
    "interview_results.project_file": "Interviews: QDA-Projekt",
    "interview_results.notes_word": "Interviews: Notizen (Word)",
    "interview_results.notes_files": "Interviews: Notizen (ZIP)",
    "interview_summaries.file_csv": "Interviews: Fallzusammenfassungen CSV",
    "interview_summaries.file_docx": "Interviews: Fallzusammenfassungen Word",
    "interview_transcribe.download": "Interviews: Transkript",
    "interview_audio.listen": "Interviews: Audio heruntergeladen",
    # contact data and participations
    "surveys.export_contacts": "Kontaktdaten exportiert",
    "surveys.draw_contacts": "Kontaktdaten: Verlosung",
    "surveys.delete_contacts": "Kontaktdaten gelöscht",
    "survey_invites.addresses": "Einladungsliste: Adressen aufgenommen",
    "survey_invites.export": "Einladungsliste heruntergeladen",
    "survey_invites.delete": "Einladungsliste gelöscht",
    "survey_invites.send": "Einladungsmails verschickt",
    "survey_invites.value_links_mail": "Links je Person per E-Mail verschickt",
    "surveys.erase_request": "Teilnahmen gelöscht",
    "surveys.delete": "Befragung gelöscht",
    # who has access
    "surveys.add_member": "Person beteiligt",
    "surveys.remove_member": "Person entfernt",
    "surveys.set_member_role": "Rolle geändert",
    "surveys.add_group_access": "Gruppe freigegeben",
    "surveys.remove_group_access": "Gruppenfreigabe entfernt",
    "surveys.set_group_access": "Rolle einer Gruppe geändert",
    "surveys.set_showcase": "Beispielbefragung an/aus",
    "data_access.create": "Datenzugang: Token angelegt",
    "data_access.revoke": "Datenzugang: Token widerrufen",
    "agent_access.create": "KI-Agent: Token angelegt",
    "agent_access.revoke": "KI-Agent: Token widerrufen",
    "groups.add_member": "Gruppe: Person aufgenommen",
    "groups.remove_member": "Gruppe: Person entfernt",
    "groups.set_role": "Gruppe: Rolle geändert",
    "admin.group_lead": "Gruppe: Leitung von Admin eingesetzt",
    "groups.share": "Gruppe: Befragung/Design geteilt",
    "groups.delete": "Gruppe gelöscht",
    # coding interview transcripts (app/transcript_coding.py): who may read the transcripts of an analysis
    "transcript_coding.add_members": "Kodieren: Mitglied aufgenommen",
    "transcript_coding.remove_member": "Kodieren: Mitglied entfernt",
    "transcript_coding.interviewers": "Kodieren: Interviewlinks freigegeben/entfernt",
    "transcript_coding.project_file": "Kodieren: REFI-QDA-Projekt heruntergeladen",
    "admin.group_action": "Gruppe gelöscht",  # only "delete" (done() in the view); renaming and "darf anlegen" stay out
    # accounts and the database (site admins)
    "admin.new_account": "Konto angelegt",
    "admin.block_account": "Konto gesperrt/entsperrt",
    "admin.account": "Anmeldeadresse geändert",  # only the address (done() in the view): it decides who gets a link
    "admin.sign_in_orcids": "Anmeldung freigeschaltet/entfernt",
    "admin.delete_account": "Konto gelöscht",
    "org.delete_account": "Konto zur Löschung vorgemerkt/zurückgenommen (Organisation)",
    "admin.platform_erase": "Löschung durch die Plattform",
    "admin.database": "Datenbank geprüft/kopiert/alte Datei gelöscht",
    # the own account: written only when it worked (done() below)
    "accounts.delete_own": "Eigenes Konto gelöscht",
}
# the same in English, for the page (log_action); the stored text stays the German one above
ACTIONS_EN = {
    "diary.offline_review": "Offline answers reviewed",
    "paper.sheet_discard": "Paper sheet discarded",
    "paper.page_remove": "Paper page deleted",
    "surveys.export_csv": "Data export CSV",
    "surveys.export_spss": "Data export SPSS (ZIP)",
    "surveys.export_sav": "Data export .sav",
    "surveys.export_omv": "Data export .omv",
    "surveys.export_rds": "Data export .rds",
    "data_access.api_csv": "Data export CSV (token)",
    "data_access.api_rds": "Data export .rds (token)",
    "data_access.api_codebook": "Codebook (token)",
    "mcp.endpoint": "Agent access",
    "surveys.export_conjoint": "Data export long format",
    "surveys.export_vignettes": "Data export vignettes (long format)",
    "surveys.export_voice": "Voice messages exported",
    "waves.export_merged": "Data export waves merged",
    "surveys.results_panel": "Waves linked and analysed",
    "surveys.snapshot_file": "Backup downloaded",
    "surveys.zenodo_upload": "Zenodo",
    "coscine.store": "Coscine",
    "constructs.linked_data": "Data of a linked element",
    "constructs.pool_csv": "Construct pool",
    "admin.construct_compose_file": "Construct pool (compilation)",
    "interview_results.overview_file": "Interviews: case overview",
    "interview_results.project_file": "Interviews: QDA project",
    "interview_results.notes_word": "Interviews: notes (Word)",
    "interview_results.notes_files": "Interviews: notes (ZIP)",
    "interview_summaries.file_csv": "Interviews: case summaries CSV",
    "interview_summaries.file_docx": "Interviews: case summaries Word",
    "interview_transcribe.download": "Interviews: transcript",
    "interview_audio.listen": "Interviews: audio downloaded",
    "surveys.export_contacts": "Contact data exported",
    "surveys.draw_contacts": "Contact data: prize draw",
    "surveys.delete_contacts": "Contact data deleted",
    "survey_invites.addresses": "Invitation list: addresses added",
    "survey_invites.export": "Invitation list downloaded",
    "survey_invites.delete": "Invitation list deleted",
    "survey_invites.send": "Invitation mails sent",
    "survey_invites.value_links_mail": "Links per person sent by email",
    "surveys.erase_request": "Participations deleted",
    "surveys.delete": "Survey deleted",
    "surveys.add_member": "Person added",
    "surveys.remove_member": "Person removed",
    "surveys.set_member_role": "Role changed",
    "surveys.add_group_access": "Group given access",
    "surveys.remove_group_access": "Group access removed",
    "surveys.set_group_access": "Role of a group changed",
    "surveys.set_showcase": "Example survey on/off",
    "data_access.create": "Data access: token created",
    "data_access.revoke": "Data access: token revoked",
    "agent_access.create": "AI agent: token created",
    "agent_access.revoke": "AI agent: token revoked",
    "groups.add_member": "Group: person added",
    "groups.remove_member": "Group: person removed",
    "groups.set_role": "Group: role changed",
    "admin.group_lead": "Group: lead set by an admin",
    "groups.share": "Group: survey/design shared",
    "groups.delete": "Group deleted",
    "transcript_coding.add_members": "Coding: member added",
    "transcript_coding.remove_member": "Coding: member removed",
    "transcript_coding.interviewers": "Coding: interviewer links given access/removed",
    "transcript_coding.project_file": "Coding: REFI-QDA project downloaded",
    "admin.group_action": "Group deleted",
    "admin.new_account": "Account created",
    "admin.block_account": "Account blocked/unblocked",
    "admin.account": "Sign-in address changed",
    "admin.sign_in_orcids": "Sign-in allowed/removed",
    "admin.delete_account": "Account deleted",
    "org.delete_account": "Account marked for deletion/taken back (organisation)",
    "admin.platform_erase": "Deletion by the platform",
    "admin.database": "Database checked/copied/old file deleted",
    "accounts.delete_own": "Own account deleted",
}
_EN_OF_STORED = {ACTIONS[endpoint]: en for endpoint, en in ACTIONS_EN.items()}


def _who(user):
    """The person of a row of the log: number, name and the ORCID iD if the account has one (a password account has
    none since migration 0085; before, the row said "(None)", Ralf 2026-10-19)."""
    return f"Nr. {user.number or '?'} {user.display_name}{f' ({user.orcid})' if user.has_orcid else ''}"[:220]


def log_action(stored):
    """The stored action (its German text) in the language of the page."""
    return _EN_OF_STORED.get(stored, stored) if english() else stored


def log_who(stored):
    """The stored person ("Nr. 12 Name (iD)") in the language of the page: "No. 12 …" in English. Rows of password
    accounts written 2026-10-18/19 say "(None)" for the missing iD; it is left out."""
    if stored and stored.endswith(" (None)"):
        stored = stored[:-7]
    if english() and stored and stored.startswith("Nr. "):
        return "No. " + stored[4:]
    return stored


# endpoints that are written down only when the view said it worked (done()): a wrong password also ends in a
# redirect, and deleting the own account signs the person out before the log is written; the two admin endpoints do
# more than the one thing that belongs in the log (audit 2026-10-09, A3); a download with a read token has nobody
# signed in, the token names the person (app/data_access.py); a deletion by the platform is written with its reason
# (app/platform_erase.py) once it is done
ON_SUCCESS = ("accounts.delete_own", "surveys.results_panel", "admin.account", "admin.group_action", "admin.platform_erase",
              "data_access.create", "data_access.revoke",
              "data_access.api_csv", "data_access.api_rds", "data_access.api_codebook",
              "agent_access.create", "agent_access.revoke", "mcp.endpoint")
# form fields that are worth a word in the detail; nothing else of the request is written down
DETAIL_FIELDS = ("orcid", "role", "action", "kind", "target", "group", "source", "all", "fmt", "sep", "feature", "state",
                 "group_id", "member_id", "id", "value", "nid", "rid", "part", "layout",
                 "tolerant")


def done(user=None, worked=True, detail=None):
    """The view of an ON_SUCCESS endpoint did what was asked: the log writes it down, for `user` (default: the
    person signed in now; the account being deleted is passed before it is gone). worked=False takes it back.
    `detail` is added to the detail of the row (see note())."""
    from flask_login import current_user

    g.audit_done = worked
    if not worked:
        return
    if detail:
        note(detail)
    user = user if user is not None else current_user
    g.audit_who = (user.id, _who(user))


def note(detail):
    """A word for the detail of this request's row that the form does not hold, e.g. which group lost its access
    (the link id of the form is gone with the link). Never contact data."""
    g.audit_detail = ", ".join(x for x in (g.get("audit_detail"), str(detail)) if x)


def _detail():
    values = {**(request.view_args or {}), **{k: v for k, v in request.args.items()}, **{k: v for k, v in request.form.items()}}
    parts = []
    for key in DETAIL_FIELDS:
        if key in values and key != "survey_id":
            parts.append(f"{key}={str(values[key])[:60]}")
    sources = request.args.getlist("source")
    if len(sources) > 1:
        parts = [p for p in parts if not p.startswith("source=")] + [f"source={'+'.join(sources)[:60]}"]
    if g.get("audit_detail"):
        parts.append(g.audit_detail[:120])
    return ", ".join(parts)[:300]


def _record(response):
    from flask_login import current_user

    action = ACTIONS.get(request.endpoint or "")
    if action is None or response.status_code >= 400 or g.get("audit_skip"):
        return response
    if request.endpoint in ON_SUCCESS:
        if not g.get("audit_done"):
            return response
        user_id, who = g.audit_who
        return _write(response, action, user_id, who, g.get("audit_survey"))
    if not current_user.is_authenticated:
        return response
    if request.endpoint == "interview_audio.listen" and request.args.get("download") != "1":
        return response  # listening in the page (pieces of the file, again and again) is no download
    # 206: a request with "Range: bytes=0-" gets the whole file as well (audit 2026-10-09, A2)
    if request.method == "GET" and (response.status_code not in (200, 206)
                                    or request.endpoint in ("admin.database", "admin.delete_account", "org.delete_account")):
        return response  # a page that was only looked at, or a download that did not happen
    survey = g.get("audit_survey")
    if survey is None and request.endpoint.startswith("surveys."):
        return response  # refused before the survey was handed out (missing rights)
    return _write(response, action, current_user.id,
                  _who(current_user), survey)


def _write(response, action, user_id, who, survey):
    from app.models import AccessLog, utcnow

    try:
        db.session.rollback()  # whatever the request left open is not written by the log
        db.session.add(AccessLog(at=utcnow(), user_id=user_id, who=who,
                                 survey_id=survey.id if survey is not None else None,
                                 survey_code=(survey.code if survey is not None else "")[:20], action=action[:60],
                                 detail=_detail(), status=response.status_code))
        db.session.commit()
    except Exception:  # the log must never break the answer the person already has
        db.session.rollback()
    return response


def register(app):
    app.after_request(_record)
    app.add_template_filter(log_action)
    app.add_template_filter(log_who)


def sweep(now):
    """Remove rows older than KEEP_DAYS and rows of actions that are no longer written down (housekeeping)."""
    from app.models import AccessLog

    gone = db.session.execute(db.delete(AccessLog).where(
        db.or_(AccessLog.at < now - timedelta(days=KEEP_DAYS), AccessLog.action.not_in(set(ACTIONS.values()))))).rowcount
    if gone:
        db.session.commit()
    return gone

