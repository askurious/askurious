"""The rights of the participants' own database role (audit 2026-10-18, CFG-04; prepared, off by default).

The participants' containers (deploy/docker: participant-a, participant-b, brand-participant) answer only the
participants' paths (app/hosts.py: participate, live_public, preview_share, board_share, quota_share, demo_webapp,
diary_api, interview_screen_public and the shared media/geo/static/health endpoints). Once PARTICIPANT_DB_USER is in the .env they run under a
role of their own, which the migration job (app/schema_migrate.py, grant_participant) gives exactly these rights after
every migration; a table that is not in GRANTS stays closed to it, also a new one (no default privileges).

How the list was found: a recorder of every SQL statement sent while a participants' request was answered
(tests/helpers/participant_sql.py) over the tests of participation, quotas, live, diary, privacy (deletion codes,
complaints), dashboards, items, panel, external sections, the demo web app and paper; plus reading the code for what
those tests cannot see (SIDE=participant: the mail queue, the old cookie's handover rows; the cache triggers of
app/data_revision.py, which write with the caller's rights). tests/conftest.py checks the tests of those folders
against this list, so a participant request that needs a right outside it fails there; and
tests/platform/test_participant_grants.py refuses a table (model or migration) that is in neither GRANTS nor
NOT_GRANTED. Run `PARTICIPANT_SQL_RECORD=<file> pytest tests/<folder>` to measure anew.

Every write includes SELECT: PostgreSQL needs it for WHERE and RETURNING. SELECT ... FOR UPDATE needs UPDATE.
"SELECT (a, b)" grants those columns only: a query that names another column of the table is refused.
"""

R = ("SELECT",)
RW = ("SELECT", "INSERT", "UPDATE", "DELETE")

# table: (privileges, why the participants' side needs them)
GRANTS = {
    "alembic_version": (R, "the start compares the schema's version with the code (DATABASE_SCHEMA_MODE=check)"),
    "survey": (("SELECT", "UPDATE"), "every participant page reads its survey; live votes and diary entries move "
               "its counters and state (live slide, revision counters); a planned start from a link starts it"),
    "block": (R, "the questionnaire's blocks for the flow, pages and dashboards"),
    "item": (R, "the questions and elements shown and checked"),
    "survey_label": (R, "own link names (/s/name) resolve to their survey"),
    "survey_theme": (R, "the look of the participant pages"),
    "survey_member": (R, "the researchers the consent and the privacy page name (name, affiliation)"),
    "user": (R, "the researchers' names the consent and the privacy page show (SurveyMember.display_name); no "
             "sign-in data, which lies in user_login"),
    # column rights: never the password hash, the second factor or the sign-in state
    "user_login": (("SELECT (user_id, email, kinds, status, valid_until, session_version)",), "the privacy page of a "
                   "live vote without its own contact address names the email of the account that created it "
                   "(app/live/base.py, contact_email); a pretest link checks the survey types its maker may still see "
                   "(app/kind_access.py, kinds) and that its maker's sign-in still holds: not blocked, period not over, "
                   "same session_version as when the link was made (app/editor/share.py, _maker_still_signs)"),
    # a pretest or phone link (/p/...) holds only while its maker still has access to the survey
    # (app/editor/share.py, _shared_survey: access.own_role_of_user, kind_access.home_cap)
    "survey_group": (R, "a pretest link: does its maker still reach the survey through a group"),
    "workgroup_member": (R, "a pretest link: is its maker still in that group"),
    "organisation": (R, "a pretest link: the cap of its maker's home organisation on survey types"),
    "organisation_member": (R, "a pretest link: its maker's home organisation"),
    "asset": (R, "images of elements shown on the page"),
    "construct": (R, "the scales of linked construct elements shown on a page"),
    "construct_tag": (R, "with the construct: its keywords, loaded with it"),
    "tag": (R, "with the construct: the keywords themselves"),
    "response": (RW, "the participation itself: started, answered page by page, ended, deleted with its deletion "
                 "code or by the diary app"),
    "response_revision": (("SELECT", "INSERT", "UPDATE"), "the cache trigger on response counts every change here "
                          "(app/data_revision.py, runs with the caller's rights)"),
    "split_session": (RW, "the separated part of a participation (contact details apart from the answers)"),
    "contact_entry": (("SELECT", "INSERT"), "email address and comment of the separated part"),
    "participant_handover": (RW, "a participation under way handed over from the main address, taken once; the old "
                             "cookie's fingerprint rows (app/hosts.py)"),
    "once_value": (RW, "link parameters that may take part only once, and their release"),
    "whisper_cancel": (("SELECT", "INSERT"), "a deletion that takes an interview recording along queues the cancel of "
                       "its transcription; the housekeeping hands it on (app/whisper_jobs.py, audit 2026-10-22, E-01)"),
    "browser_receipt": (RW, "short-lived receipts of live devices, declines, random assignments and diary devices"),
    "rate_hit": (("SELECT", "INSERT", "DELETE"), "the shared rate limits for deletion codes, live joins and tokens "
                 "(SQLite takes its write lock with an empty DELETE)"),
    "rate_count": (("SELECT", "INSERT", "UPDATE"), "the load limits for new participations, live votes and complaints"),
    "quota": (R, "quotas checked in the flow and shown by quota and dashboard links"),
    "quota_cell": (("SELECT", "UPDATE"), "cells counted when a participation ends"),
    "quota_share": (R, "the quota links (/q/...)"),
    "board": (R, "the dashboards the public links show"),
    "board_share": (("SELECT", "UPDATE"), "the dashboard links (/d/...): last use"),
    "seal_entry": (("SELECT", "INSERT"), "the seal chain gets the hash of each ended case and deletion"),
    # a new survey starts with the chain on but without an entry (SEAL_DEFAULT): its first ended case begins the chain
    # and names the survey here (seal._remember). Closed, every first answer of a new live vote and the first finished
    # participation of a new survey ended in a server error (live quiz, 2026-10-23)
    "seal_chain": (("SELECT", "INSERT"), "the first ended case of a survey with the seal chain on records the survey "
                   "of the chain (its number, uid, title)"),
    "erasure": (("SELECT", "INSERT"), "the count of participations deleted with their code (no content)"),
    "preview_comment": (("SELECT", "INSERT", "DELETE"), "comments of testers on the pretest link (/p/...): written, "
                        "shown with their screenshot, taken back by their writer"),
    "feedback": (("SELECT", "INSERT"), "complaints of participants about a survey: kept, counted per day, the case "
                 "number read back after the commit (the row is loaded anew, all its columns); a column right is "
                 "possible once participate.complaint no longer reloads it"),
    # the id only (RETURNING of the new row's key): reading the queue, the mails of others, stays closed
    "mail_outbox": (("SELECT (id)", "INSERT"), "SIDE=participant leaves its mails (receipt of a complaint) in the "
                    "queue for the housekeeping (app/mail.py, queued_here)"),
    "enrolment": (RW, "the people of a diary study: joining, entries, pause, leaving and deleting through the app"),
    "diary_invite": (("SELECT", "UPDATE"), "an invitation code of a diary study is redeemed"),
    "live_question": (("SELECT", "INSERT", "UPDATE"), "audience questions in a live vote: asked, listed, voted"),
    "live_reaction": (("SELECT", "INSERT", "DELETE"), "emoji reactions in a live vote, the old ones dropped after two "
                      "minutes"),
    "live_feedback": (("SELECT", "INSERT"), "emojis per slide in a live vote"),
    "paper_sheet": (("SELECT", "UPDATE"), "a deletion code typed for a paper sheet not taken over yet marks it erased "
                    "(app/paper.py, erase_unscanned; locked FOR UPDATE)"),
    # the element "KI-Chat" (app/ai_chat.py): the participants' containers call the model of the survey, so they read
    # its access (the key in the participants' box) and write the messages; deleting a participation takes them along
    "ai_chat_access": (R, "the model access of a survey with the element KI-Chat: address, model and key (encrypted)"),
    "ai_chat_turn": (RW, "the messages of the KI-Chat: written while the person chats, marked when the model did not "
                     "answer, counted for the limits, deleted with the participation"),
    "paper_page": (("SELECT", "DELETE"), "with it, the scans of that sheet go (paper.drop_scans, _drop_doubles)"),
}

# table: why the participants' side never needs it
NOT_GRANTED = {
    "access_log": "the access log of researchers' and admins' actions on data",
    "account_job": "password reset requests of accounts",
    "account_supervisor": "supervisors of researchers' accounts",
    "account_type": "user types of accounts",
    "agent_token": "tokens of the AI agent interface",
    "ai_access": "researchers' own AI keys",
    "ai_call": "AI requests of researchers",
    "ai_usage": "AI use per person and day",
    "auth_token": "one-time links of accounts",
    "blog_image": "the public blog, served by the researchers' side",
    "blog_post": "the public blog, served by the researchers' side",
    "blog_slug": "the public blog, served by the researchers' side",
    "construct_citation": "the construct toolbox of researchers",
    "construct_group_share": "sharing of linked elements between researchers",
    "construct_pick": "researchers' favourite constructs",
    "construct_share": "sharing of linked elements between researchers",
    "construct_shortlist": "the editor's quick box of recommended constructs",
    "construct_submission": "submissions to the construct toolbox and their review",
    "contact": "researchers invited recently",
    "coscine_connection": "researchers' Coscine tokens",
    "data_token": "read tokens for data access from R and Python",
    "deleted_account": "numbers of deleted accounts",
    "design": "saved designs of researchers (the survey's own look is survey_theme)",
    "design_editor": "who may change a shared design",
    "design_group_share": "designs shared with a group",
    "design_share": "designs shared with a person",
    "edit_mark": "who edits which part of a draft",
    "edit_step": "undo and redo of the editor",
    "experiment_grant": "experimental features switched on for researchers",
    "sem_version": "versions of the structural equation model on the results page of researchers",
    "group_join_link": "join links of researchers' groups",
    "id_floor": "highest ids of deleted surveys, blocks and questions: written when researchers delete (participations: on SQLite only)",
    "interviewer_access": "interviewer links of the interview guide (researchers' side)",
    "live_ink": "the presenter's pen notes on slides, drawn and shown only on the researchers' side",
    "login_session": "researchers' sign-ins per browser",
    "member_invite": "invitations of researchers to surveys and groups",
    "org_alias": "numbers of hidden names in organisations, shown on researchers' pages only",
    "org_deletion": "accounts marked for deletion by organisation admins",
    "paper_run": "print runs of paper questionnaires, made by researchers",
    "research_project": "research projects in AInstein",
    "response_pool_revision": "former data-pool revisions, no longer written (since response_revision)",
    "seal_anchor": "DFN timestamps, fetched by the housekeeping",
    "seal_status": "the last check of a seal chain, by the housekeeping",
    "sign_in_grant": "accounts allowed to sign in",
    "sign_in_orcid": "ORCID iDs allowed to sign in",
    "sign_in_wait": "waiting list of sign-ups",
    "sign_up_link": "registration links of the platform, made by site admins",
    "site_setting": "settings of the platform from the administration",
    "survey_hide": "surveys hidden from a researcher's list",
    "survey_invite": "address lists for invitation mails, sent by researchers",
    "survey_pin": "surveys pinned on a researcher's start page",
    "translation_mark": "the editor's translation state",
    "trashed_item": "the editor's bin of deleted questions",
    "workgroup": "researchers' work groups",
    "zenodo_connection": "researchers' Zenodo tokens",
}


def columns(table):
    """The columns of a SELECT granted for some columns only ("SELECT (a, b)"), else None (all of them)."""
    for privilege in GRANTS.get(table, ((), ""))[0]:
        if privilege.startswith("SELECT ("):
            return {c.strip() for c in privilege[len("SELECT ("):-1].split(",")}
    return None


def allowed(table):
    """The privileges the participants' role has on `table` (an empty tuple when it is closed)."""
    return tuple(p.split(" ")[0] for p in GRANTS.get(table, ((), ""))[0])
