"""What a deletion on the participants' side leaves for the housekeeping to finish.

The participants' containers may run under a database role of their own (app/participant_grants.py) that writes
neither the coding guide of a survey (survey.codings), nor the analyses of interview transcripts (coding_*), nor the
field packs of an interview guide (survey.interview). A deletion with the code still has to reach them: anchor
examples that quote a deleted answer, memos at a deleted interview, and the place of a field pack whose interview is
deleted before the device came back. On that side these steps are written down here, in the transaction of the
deletion itself, and the housekeeping (app/housekeeping.py, every minute) carries them out with the researchers'
rights and removes the rows. The participation, its files and its messages go at once, as before.

A row holds no answer and no text of a person: the kind, the survey, and numbers or digests that say where to clean
(table participant_cleanup, migration 0130). Without the participants' side of its own (one address, SIDE unset)
nothing is queued: the same steps run at once.
"""
from types import SimpleNamespace

from flask import current_app

from app.extensions import db

CODING, TRANSCRIPTS, OFFLINE = "coding", "transcripts", "offline"
BATCH = 200


def deferred():
    """Whether this process is the participants' side of its own: its steps go to the queue."""
    return current_app.config.get("SIDE") == "participant"


def queue(kind, survey_id, payload):
    """Note a step, within the caller's transaction: it stands exactly when the deletion does."""
    from app.models import ParticipantCleanup

    db.session.add(ParticipantCleanup(kind=kind, survey_id=survey_id, payload=payload))


def offline_erased(survey_id):
    """The participation ids of field pack places whose deletion is noted but not carried out yet: the sync of the
    device refuses them already (app/interview_offline.py)."""
    from app.models import ParticipantCleanup

    rows = db.session.scalars(db.select(ParticipantCleanup.payload).where(
        ParticipantCleanup.kind == OFFLINE, ParticipantCleanup.survey_id == survey_id))
    return {str((p or {}).get("pid") or "") for p in rows} - {""}


def _run(row):
    from app.models import Survey

    survey = db.session.get(Survey, row.survey_id)
    payload = row.payload if isinstance(row.payload, dict) else {}
    if survey is None:
        return  # the survey went meanwhile, and everything of it with it
    if row.kind == CODING:
        from app import coding

        coding.empty_quoted(survey, payload.get("hits") or {})
    elif row.kind == TRANSCRIPTS:
        from app import transcript_coding_view

        ids = [i for i in payload.get("interviews") or [] if isinstance(i, int)]
        transcript_coding_view.forget_now([SimpleNamespace(id=i, survey_id=survey.id) for i in ids])
    elif row.kind == OFFLINE:
        from app import interview_offline

        interview_offline.mark_erased(survey, str(payload.get("pid") or ""))


def drain():
    """The housekeeping: carry out the noted steps, oldest first, each in a transaction of its own. A step that fails
    stays for the next round and does not hold up the others. Returns how many went through."""
    from app.models import ParticipantCleanup

    ids = list(db.session.scalars(db.select(ParticipantCleanup.id).order_by(ParticipantCleanup.created_at).limit(BATCH)))
    done = 0
    for row_id in ids:
        try:
            row = db.session.get(ParticipantCleanup, row_id)
            if row is None:
                continue
            _run(row)
            db.session.delete(row)
            db.session.commit()
            done += 1
        except Exception:  # noqa: BLE001 - logged; the row stays
            db.session.rollback()
            current_app.logger.exception("Clean-up after a deletion on the participants' side failed (row %s)", row_id)
    return done
