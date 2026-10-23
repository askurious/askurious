"""Contact data of participants: export, draw, erasure requests, deletion."""
from flask import flash, redirect, request, url_for
from flask_login import login_required

from app.lang import de_en
from app.surveys.base import bp, get_survey
from app.surveys.data import _data_page


# --- separate contact form ------------------------------------------------------------


@bp.route("/<sid:survey_id>/contact/export.csv")
@login_required
def export_contacts(survey_id):
    from flask import Response as FlaskResponse

    from app import contact, export

    survey = get_survey(survey_id)
    from app import accounts

    again = accounts.need_fresh(url_for("surveys.data", survey_id=survey.id) + "#contact-data")
    if again:
        return again
    if not contact.can_download(survey):
        flash(de_en("Kontaktdaten sind erst ab {n} abgeschlossenen Teilnahmen abrufbar (sonst ließen sie sich Antworten "
                    "zuordnen).",
                    "Contact details can only be retrieved from {n} completed participations on (otherwise they could be "
                    "matched to answers).", n=contact.MIN_RESPONSES), "error")
        return redirect(url_for("surveys.data", survey_id=survey.id) + "#contact-data")
    return FlaskResponse(contact.to_csv(survey, "semicolon" if request.args.get("sep") == "semicolon" else "comma"), mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "getrennt")}"', "Cache-Control": "no-store"})


@bp.route("/<sid:survey_id>/contact/draw", methods=["POST"])
@login_required
def draw_contacts(survey_id):
    from app import contact

    survey = get_survey(survey_id)
    from app import accounts

    again = accounts.need_fresh(url_for("surveys.data", survey_id=survey.id) + "#contact-data")
    if again:
        return again
    if not contact.can_download(survey):
        flash(de_en("Die Verlosung ist erst ab {n} abgeschlossenen Teilnahmen möglich.",
                    "The prize draw is only possible from {n} completed participations on.", n=contact.MIN_RESPONSES), "error")
        return redirect(url_for("surveys.data", survey_id=survey.id) + "#contact-data")
    n = max(1, min(request.form.get("n", 1, type=int) or 1, 100))
    winners = contact.draw(survey, n)
    # shown inside the section (surveys/data.html), where the page opens; the draw itself is not stored
    flash(de_en("Gezogen: {who}", "Drawn: {who}", who=", ".join(winners)) if winners
          else de_en("Gezogen: keine E-Mail-Adressen vorhanden", "Drawn: no email addresses available"), "inline")
    return redirect(url_for("surveys.data", survey_id=survey.id) + "#contact-data")


@bp.route("/<sid:survey_id>/erasure", methods=["POST"])
@login_required
def erase_request(survey_id):
    """A person asked the researchers to delete their data and named something to find it by (app/erasure.py:
    deletion code, participation ID, an identifier from the link, or an e-mail address of the separated
    section). First post: what was found, shown on the Export page. Second post (`confirm` = the number that was
    shown): deleted, if still exactly that much is found."""
    from app import erasure, seal
    from app.timeutil import fmt

    survey = get_survey(survey_id)
    term = " ".join(request.form.get("term", "").split())[:300]
    responses, entries = erasure.find(survey, term)
    # a diary study: a person goes with all entries (erasure._remove), so the page names and confirms all of them,
    # not only the one found (audit 2026-10-11, N6)
    persons = len({r.enrolment_id for r in responses if getattr(r, "enrolment_id", None)})
    responses = __import__("app.diary", fromlist=["whole_persons"]).whole_persons(responses)[0]
    found = len(responses) + len(entries)
    if not term:
        flash(de_en("Bitte eingeben, was die Person genannt hat.", "Please enter what the person named."), "error")
    elif not found:
        flash(de_en("Dazu wurde in dieser Befragung nichts gefunden: keine Teilnahme mit diesem Löschcode, dieser Teilnahme-ID "
                    "oder dieser Kennung aus dem Link und kein Eintrag des getrennten Abschnitts mit dieser E-Mail-Adresse.",
                    "Nothing was found for this in this survey: no participation with this deletion code, this "
                    "participation ID or this identifier from the link, and no entry of the separate section with this "
                    "email address."), "warning")
    elif request.form.get("confirm", type=int) == found and responses and seal.request_reason(
            request.form.get("why") or "wish", request.form.get("why_text", "")) is None:
        # a participation goes only with a reason, which the seal chain keeps (siegelkette-konzept.md E4)
        flash(de_en("Bitte einen Grund wählen; „anderer Rechtsgrund“ braucht {a} bis {b} Zeichen.",
                    "Please pick a reason; “another legal ground” needs {a} to {b} characters.",
                    a=seal.WHY_MIN, b=seal.WHY_MAX), "error")
        return _data_page(survey, erase_found={"term": term, "n": found, "entries": len(entries), "persons": persons,
                                               "responses": [{"started": fmt(r.started_at), "status": r.status,
                                                              "source": r.source} for r in responses]})
    elif request.form.get("confirm", type=int) == found:
        from app import accounts

        again = accounts.need_fresh(url_for("surveys.data", survey_id=survey.id) + "#erasure")
        if again:
            return again
        erasure.erase(survey, responses, "request", entries,
                      reason=seal.request_reason(request.form.get("why") or "wish", request.form.get("why_text", "")))
        nr, ne = len(responses), len(entries)
        parts = ([de_en("1 Teilnahme", "1 participation") if nr == 1
                  else de_en("{n} Teilnahmen", "{n} participations", n=nr)] if responses else []) \
            + ([de_en("1 Eintrag des getrennten Abschnitts", "1 entry of the separate section") if ne == 1
                else de_en("{n} Einträge des getrennten Abschnitts", "{n} entries of the separate section", n=ne)]
               if entries else [])
        flash(de_en("{what} endgültig gelöscht. Vorher heruntergeladene Dateien enthalten die Daten noch: bitte durch "
                    "einen neuen Export ersetzen.",
                    "{what} deleted permanently. Files downloaded before still contain the data: please replace them with "
                    "a new export.", what=de_en(" und ", " and ").join(parts)), "success")
    else:
        return _data_page(survey, erase_found={"term": term, "n": found, "entries": len(entries), "persons": persons,
                                               "responses": [{"started": fmt(r.started_at), "status": r.status,
                                                              "source": r.source} for r in responses]})
    return redirect(url_for("surveys.data", survey_id=survey.id) + "#erasure")


@bp.route("/<sid:survey_id>/contact/delete", methods=["POST"])
@login_required
def delete_contacts(survey_id):
    from app import contact

    survey = get_survey(survey_id)
    contact.delete_all(survey)
    flash(de_en("Alle Kontaktdaten dieser Befragung wurden gelöscht.", "All contact details of this survey were deleted."), "success")
    return redirect(url_for("surveys.data", survey_id=survey.id) + "#contact-data")
