"""Data access for R and Python: read tokens per survey and person (export page, "Datenzugang für R und Python").

Who analyses every day during fieldwork used to download the data by hand each time. A read token lets a script fetch
the data set as CSV or .rds and the codebook, the same files as the buttons of the export page (surveys.csv_body,
surveys.rds_body): the survey's saved data pool with the marking of the quality assurance, and the choices the export
page offers as parameters of the address (sep=semicolon, source=real|test|dummy, all=1, drop=1, scales=1, standard=1).

The token goes in the header "Authorization: Bearer …", never in the address: addresses end up in access logs and
browser histories, and a request that carries one there is refused. Only the hash of the token is stored
(models.once_hash, SHA-256 like the other one-time links); it is shown once, right after it was made. It works while
it has not expired (DEFAULT_DAYS, at most MAX_DAYS), and while its person may download the data of the survey: role
"Bearbeiten" or "Admin" (as for the download buttons, surveys.required_role), an account that is not blocked and may
sign in. This is checked at every request, so losing the right stops the token at once; the row goes with the survey
and with the account. The admins of a survey see and revoke every token of it, the others their own.

Every download with a token is in the access log ("Protokoll") like a download by hand, with the token's name, and so
are making and revoking one: a token is a way to the raw data. Downloads per token and wrong tokens per sender are
limited (app/limits.py). The addresses belong to the researchers' side (app/hosts.py).
"""
import secrets
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, request, url_for
from flask import Response as FlaskResponse
from flask_login import current_user, login_required

from app import audit, brand, heavy, limits
from app.extensions import db
from app.lang import de_en
from app.models import DataToken, Survey, User, once_hash, utcnow

bp = Blueprint("data_access", __name__)

PREFIX = "askdt_"       # tells a token apart in a script or a secret scanner
DEFAULT_DAYS = 90
MAX_DAYS = 365
PER_PERSON = 10         # valid tokens of one person per survey
KEEP_EXPIRED_DAYS = 30  # an expired token stays listed (as expired) this long, then it goes
QUERY_NAMES = ("token", "access_token", "api_key", "key", "bearer")  # never accepted in the address


def _hash(token):
    return once_hash(token or "")


def offered(survey):
    """The section is on the export page of this survey (not for a live vote: its export is another one)."""
    return survey.live is None


def owner_may(owner, survey):
    """May the person of a token still download the data of this survey: role "Bearbeiten" or "Admin", an account
    that is not blocked and may sign in (as auth.load_user asks for a sign-in)."""
    from app import accounts, auth

    if owner is None or survey is None or not offered(survey) or accounts.is_blocked(owner):
        return False
    if not current_app.config["DEV_LOGIN"] and not auth.may_sign_in(owner.orcid):
        return False
    return survey.can(owner, "editor")


def sweep():
    """Tokens expired more than KEEP_EXPIRED_DAYS ago go (before every listing)."""
    gone = db.session.execute(db.delete(DataToken).where(
        DataToken.expires_at < utcnow() - timedelta(days=KEEP_EXPIRED_DAYS))).rowcount
    if gone:
        db.session.commit()


def listing(survey, user):
    """The tokens this person sees on the export page: all of the survey for its admins, else the own ones. Each as
    {"row", "owner" (name for admins, None for the own), "state": "active" | "expired" | "dormant"}; "dormant": its
    person may not download the data now (role, blocked), so it does not work."""
    if not offered(survey):
        return []
    sweep()
    query = db.select(DataToken).where(DataToken.survey_id == survey.id).order_by(DataToken.created_at.desc())
    admin = survey.can(user, "admin")
    if not admin:
        query = query.where(DataToken.user_id == user.id)
    rows = list(db.session.scalars(query))
    owners = {u.id: u for u in db.session.scalars(db.select(User).where(User.id.in_({r.user_id for r in rows})))}
    now = utcnow()
    out = []
    for row in rows:
        owner = owners.get(row.user_id)
        state = "expired" if row.expires_at < now else ("active" if owner_may(owner, survey) else "dormant")
        name = None
        if admin and row.user_id != user.id:
            name = owner.display_name if owner is not None else de_en("Konto gelöscht", "account deleted")
        out.append({"row": row, "owner": name, "state": state})
    return out


def addresses(survey):
    """The full addresses of the downloads, and the snippets for R and Python that use them (plain text: the page
    escapes them, also in the attribute of the copy chip). R needs no package: download.file sends headers since
    R 3.6; the token comes from the environment (.Renviron), never from the script."""
    base = brand.public_base()
    urls = {kind: base + url_for(f"data_access.api_{kind}", survey_id=survey.id) for kind in ("csv", "rds", "codebook")}
    rds, csv = urls["rds"], urls["csv"]
    urls["r"] = "\n".join((
        'token <- Sys.getenv("ASKURIOUS_TOKEN")',
        'file <- tempfile(fileext = ".rds")',
        f'download.file("{rds}", file, mode = "wb", quiet = TRUE,',
        '              headers = c(Authorization = paste("Bearer", token)))',
        'data <- readRDS(file)'))
    urls["python"] = "\n".join((
        'import io, os',
        'import pandas as pd',
        'import requests',
        '',
        f'r = requests.get("{csv}",',
        '                 headers={"Authorization": "Bearer " + os.environ["ASKURIOUS_TOKEN"]}, timeout=300)',
        'r.raise_for_status()',
        'data = pd.read_csv(io.BytesIO(r.content), encoding="utf-8-sig")'))
    return urls


def _back(survey):
    return redirect(url_for("surveys.data", survey_id=survey.id) + "#data-access")


@bp.route("/surveys/<sid:survey_id>/data-access/new", methods=["POST"])
@login_required
def create(survey_id):
    """A new token for the person signed in, shown once on the export page (the page is drawn as the answer, so the
    token is never in a session cookie or an address)."""
    from app.surveys import _data_page, get_survey

    survey = get_survey(survey_id, need="editor")  # who may download the data (viewers may not)
    from app import accounts

    again = accounts.need_fresh(url_for("surveys.data", survey_id=survey.id))
    if again:
        return again
    if not offered(survey):
        abort(404)
    name = " ".join(request.form.get("name", "").split())[:80]
    if not name:
        flash(de_en("Bitte dem Token einen Namen geben, z. B. „Auswertung Laptop“.",
                    "Please give the token a name, e.g. “analysis laptop”."), "error")
        return _back(survey)
    days = request.form.get("days", DEFAULT_DAYS, type=int)
    if days is None or not 1 <= days <= MAX_DAYS:
        flash(de_en("Ein Token gilt 1 bis {n} Tage.", "A token is valid for 1 to {n} days.", n=MAX_DAYS), "error")
        return _back(survey)
    now = utcnow()
    mine = db.session.scalar(db.select(db.func.count(DataToken.id)).where(
        DataToken.survey_id == survey.id, DataToken.user_id == current_user.id, DataToken.expires_at >= now))
    if mine >= PER_PERSON:
        flash(de_en("Mehr als {n} gültige Token je Person und Befragung sind nicht möglich; bitte ein altes widerrufen.",
                    "More than {n} valid tokens per person and survey are not possible; please revoke an old one.",
                    n=PER_PERSON), "error")
        return _back(survey)
    token = PREFIX + secrets.token_urlsafe(32)
    row = DataToken(survey_id=survey.id, user_id=current_user.id, name=name, token_hash=_hash(token),
                    created_at=now, expires_at=now + timedelta(days=days))
    db.session.add(row)
    db.session.commit()
    audit.done(detail=f"Token „{name}“")
    return _data_page(survey, new_token={"value": token, "name": name, "expires_at": row.expires_at})


@bp.route("/surveys/<sid:survey_id>/data-access/<int:token_id>/revoke", methods=["POST"])
@login_required
def revoke(survey_id, token_id):
    """Revoke a token: its person, or an admin of the survey (any token of it). It stops working at once."""
    from app.surveys import get_survey

    survey = get_survey(survey_id, need="viewer")  # a person who lost the right to the data still revokes their own
    row = db.session.get(DataToken, token_id)
    if row is None or row.survey_id != survey.id or (row.user_id != current_user.id and not survey.can(current_user, "admin")):
        abort(404)
    name = row.name
    db.session.delete(row)
    db.session.commit()
    audit.done(detail=f"Token „{name}“")
    flash(de_en("Token „{name}“ widerrufen; es funktioniert nicht mehr.", "Token “{name}” revoked; it no longer works.",
                name=name), "success")
    return _back(survey)


# --- the downloads -----------------------------------------------------------------------------------------------


def _refuse(status, message):
    response = jsonify(error=message)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="data"'
    if status == 429:
        response.headers["Retry-After"] = str(current_app.config["TOKEN_WINDOW"] if g.get("token_known")
                                              else current_app.config["TOKEN_TRY_WINDOW"])
    return response


def _authorised(survey_id):
    """(token row, its person, the survey) for the token of this request, or a response that refuses it."""
    if any(name in request.args for name in QUERY_NAMES):
        return _refuse(400, de_en("Das Token gehört in den Header „Authorization: Bearer …“, nie in die Adresse.",
                                  "The token belongs in the header “Authorization: Bearer …”, never in the address."))
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    value = value.strip()
    row = None
    if scheme.lower() == "bearer" and value:
        row = db.session.scalar(db.select(DataToken).filter_by(token_hash=_hash(value)))
    if row is None or row.survey_id != survey_id:
        # only wrong tokens are counted and refused per sender: a valid token behind the same address (a shared
        # network, NAT) goes through however many wrong ones others sent from there (as the diary app, diary_api)
        if not limits.allow_token_try(count=False):
            return _refuse(429, de_en("Zu viele falsche Token; bitte später erneut versuchen.",
                                      "Too many wrong tokens; please try again later."))
        limits.allow_token_try()
        return _refuse(401, de_en("Kein gültiges Token für diese Befragung (Header „Authorization: Bearer …“).",
                                  "No valid token for this survey (header “Authorization: Bearer …”)."))
    owner = db.session.get(User, row.user_id)
    if owner is not None:
        from app.lang import of

        g.ui_lang = of(owner)  # messages, labels and codebook in the language of its person, as at the button
    if row.expires_at < utcnow():
        return _refuse(401, de_en("Das Token ist abgelaufen; im Reiter „Export“ lässt sich ein neues anlegen.",
                                  "The token has expired; a new one can be created on the tab “Export”."))
    survey = db.session.get(Survey, survey_id)
    if not owner_may(owner, survey):
        return _refuse(403, de_en("Die Person dieses Tokens darf die Daten dieser Befragung nicht mehr herunterladen.",
                                  "The person of this token may no longer download the data of this survey."))
    from app import terms

    if owner.terms_version != terms.version():  # as for agent tokens (agent_access.owner_may; code review 2026-10-15, H8)
        return _refuse(403, de_en("Die Person dieses Tokens muss zuerst die geänderten Nutzungsbedingungen annehmen "
                                  "(nach der nächsten Anmeldung); danach funktioniert das Token wieder.",
                                  "The person of this token must first accept the changed terms of use (after the next "
                                  "sign-in); then the token works again."))
    g.token_known = True
    if not limits.allow_token(row.id):
        return _refuse(429, de_en("Zu viele Abrufe mit diesem Token; bitte später erneut versuchen.",
                                  "Too many downloads with this token; please try again later."))
    return row, owner, survey


def _pool(survey):
    """The survey's saved data pool (as the export page starts with it), changed only by what the address names."""
    from app import export
    from app.surveys import stored_pool

    pool = stored_pool(survey)
    args = request.args
    if "source" in args:
        pool["sources"] = [x for x in export.SOURCES if x in args.getlist("source")] or ["real"]
    if "all" in args:
        pool["all"] = args.get("all") == "1"
    if "drop" in args:
        pool["drop"] = args.get("drop") == "1"
    return pool


def _file(survey_id, kind):
    from app import export
    from app.surveys import csv_body, rds_body

    found = _authorised(survey_id)
    if not isinstance(found, tuple):
        return found
    row, owner, survey = found
    from app import report_jobs

    report = report_jobs.api_response(survey, row.id)
    if report is not None:
        return report
    if not heavy.hold(account=owner.id):  # a place for heavy evaluations, as the buttons take (audit 2026-10-10, A2)
        return heavy.busy()
    g.coding_user = owner  # the token's person: what they code blind stays out of the file (coding.hidden, R1)
    pool, scales, standard = _pool(survey), request.args.get("scales") == "1", request.args.get("standard") == "1"
    if kind == "rds":
        body, mimetype, name = rds_body(survey, pool, scales, standard), "application/octet-stream", export.filename(survey, "r")[:-4] + ".rds"
    else:
        sep = "semicolon" if request.args.get("sep") == "semicolon" else "comma"
        what = "data" if kind == "csv" else "codebook"
        mimetype, name = "text/csv; charset=utf-8", export.filename(survey, what)
        body = export.csv_file(survey, include_incomplete=pool["all"], separator=sep, sources=pool["sources"],
                               speed_sources=pool["sources"], drop_speeders=pool["drop"], scales=scales,
                               standard=standard) if kind == "csv" else csv_body(survey, what, pool, sep, scales, standard)
    row.last_used_at = utcnow()
    db.session.commit()
    g.audit_survey = survey  # written down as surveys.export_csv and export_rds are, with the token's name
    audit.done(user=owner, detail=f"Token „{row.name}“")
    if kind == "csv":
        from flask import send_file

        response = send_file(body, mimetype=mimetype, conditional=False, download_name=name)
        response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
        response.headers["Cache-Control"] = "no-store"
        response.call_on_close(body.close)
        return response
    return FlaskResponse(body, mimetype=mimetype, headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@bp.route("/api/v1/surveys/<sid:survey_id>/data.csv")
def api_csv(survey_id):
    return _file(survey_id, "csv")


@bp.route("/api/v1/surveys/<sid:survey_id>/data.rds")
def api_rds(survey_id):
    return _file(survey_id, "rds")


@bp.route("/api/v1/surveys/<sid:survey_id>/codebook.csv")
def api_codebook(survey_id):
    return _file(survey_id, "codebook")
