"""Feedback: researchers report bugs and suggestions via the bug icon in the header; the
operator (admins) reads and closes them on /feedback/. Suggestions for new scales come from
the Konstrukte page and are filed with the tag "Scale suggestion"."""
import re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import notify, twofactor
from app.extensions import db
from app.lang import de_en
from app.models import Feedback

bp = Blueprint("feedback", __name__, url_prefix="/feedback")


PER_DAY = 50  # per person; the list is read by people


def _too_many():
    from datetime import timedelta

    from app.models import utcnow

    return db.session.scalar(db.select(db.func.count(Feedback.id)).filter(
        Feedback.user_id == current_user.id, Feedback.created_at >= utcnow() - timedelta(hours=24))) >= PER_DAY


def _safe_page(value):
    from app.links import local_path

    return local_path((value or "").strip()[:500])


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    page = _safe_page(request.values.get("page"))
    if request.method == "POST":
        message = request.form.get("message", "").strip()
        severity = request.form.get("severity", "")
        if not message:
            flash(de_en("Bitte beschreiben Sie kurz, worum es geht.", "Please describe briefly what it is about."), "error")
            return render_template("feedback/new.html", page=page, severities=Feedback.SEVERITIES,
                                   form=request.form)
        if _too_many():
            flash(de_en("Heute sind schon {n} Rückmeldungen von Ihnen eingegangen. Bitte morgen wieder.",
                        "{n} pieces of feedback from you have already arrived today. Please try again tomorrow.", n=PER_DAY), "error")
            return render_template("feedback/new.html", page=page, severities=Feedback.SEVERITIES,
                                   form=request.form)
        item = Feedback(user_id=current_user.id, message=message[:5000], page=page,
                        severity=int(severity) if severity in ("1", "2", "3") else 1)
        db.session.add(item)
        db.session.commit()
        notify.feedback(item)
        flash(de_en("Danke! Ihre Rückmeldung ist angekommen.", "Thank you! Your feedback has arrived."), "success")
        return redirect(page or url_for("dashboard.index"))
    return render_template("feedback/new.html", page=page, severities=Feedback.SEVERITIES, form={})


DOI_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)\Z", re.I)
SCALE_FIELDS = (("title", "Titel"), ("description", "Beschreibung"), ("justification", "Begründung"),
                ("reference", "Quelle"))
SCALE_FIELDS_EN = {"title": "Title", "description": "Description", "justification": "Justification", "reference": "Source"}


@bp.route("/scale", methods=["GET", "POST"])
@login_required
def scale():
    """Suggest a scale for the construct toolbox (filed as feedback with the scale tag)."""
    # a link may fill in the first fields (the page of a linked element, app/linked.py)
    form = request.form if request.method == "POST" else {k: request.args.get(k, "")[:500] for k in ("title", "description")}
    if request.method == "POST":
        values = {k: request.form.get(k, "").strip() for k, _ in SCALE_FIELDS}
        doi_raw = request.form.get("doi", "").strip()
        doi = DOI_RE.match(doi_raw)
        missing = [de_en(label, SCALE_FIELDS_EN[k]) for k, label in SCALE_FIELDS if not values[k]] + ([] if doi_raw else ["DOI"])
        if missing:
            flash(de_en("Bitte ausfüllen: {fields}.", "Please fill in: {fields}.", fields=", ".join(missing)), "error")
        elif not doi:
            flash(de_en("Die DOI sieht nicht richtig aus (z. B. 10.1037/0022-3514.84.4.822).",
                        "The DOI does not look right (e.g. 10.1037/0022-3514.84.4.822)."), "error")
        elif _too_many():
            flash(de_en("Heute sind schon {n} Rückmeldungen von Ihnen eingegangen. Bitte morgen wieder.",
                        "{n} pieces of feedback from you have already arrived today. Please try again tomorrow.", n=PER_DAY), "error")
        else:
            lines = [f"{label}: {values[k]}" for k, label in SCALE_FIELDS]
            lines.append(f"DOI: https://doi.org/{doi.group(1)}")
            db.session.add(Feedback(user_id=current_user.id, message="\n\n".join(lines)[:5000], severity=1,
                                    page=url_for("constructs.index"), tag=Feedback.SCALE_TAG))
            db.session.commit()
            flash(de_en("Danke für den Vorschlag! Wir prüfen die Skala anhand der Quelle.",
                        "Thank you for the suggestion! We will check the scale against the source."), "success")
            return redirect(url_for("constructs.index"))
    return render_template("feedback/scale.html", form=form)


@bp.route("/")
@login_required
def index():
    again = twofactor.admin_page()
    if again:
        return again
    show = request.args.get("show", "new")
    stmt = db.select(Feedback).order_by(Feedback.status, Feedback.severity.desc(), Feedback.created_at.desc())
    if show != "all":
        stmt = stmt.filter(Feedback.status == "new")
    return render_template("feedback/index.html", items=list(db.session.scalars(stmt)), show=show,
                           severities=Feedback.SEVERITIES, measures=MEASURES)


@bp.route("/<int:fid>/status", methods=["POST"])
@login_required
def set_status(fid):
    again = twofactor.admin_page()
    if again:
        return again
    item = db.session.get(Feedback, fid) or abort(404)
    item.status = "done" if request.form.get("status") == "done" else "new"
    db.session.commit()
    return redirect(url_for("feedback.index", show=request.args.get("show", "new")))


# what the operators did about a complaint (Art. 17(3) DSA asks for the measure and its reasons; copyright audit
# 2026-10-16, U11): key -> (German, English)
MEASURES = {
    "none": ("keine Maßnahme", "no measure"),
    "removed": ("Inhalt entfernt oder gesperrt", "content removed or disabled"),
    "survey": ("Befragung gesperrt", "survey suspended"),
    "account": ("Konto gesperrt", "account suspended"),
}
_ADDRESS = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


@bp.route("/<int:fid>/decide", methods=["POST"])
@login_required
def decide(fid):
    """The decision on a complaint with its reasons: kept with the complaint, sent to the person who complained (if
    they gave an address) and to the admins of the survey (Art. 16(5), Art. 17 DSA), and the complaint closed.
    What the measure itself takes (deleting an image, suspending the survey or the account) the admins do where it
    is done; this records and tells it."""
    again = twofactor.admin_page()
    if again:
        return again
    from app import mail
    from app.models import utcnow
    from app.timeutil import fmt

    item = db.session.get(Feedback, fid) or abort(404)
    measure, reason = request.form.get("measure", ""), (request.form.get("reason") or "").strip()[:3000]
    if item.severity != Feedback.COMPLAINT or measure not in MEASURES or not reason:
        flash(de_en("Bitte Maßnahme und Begründung angeben.", "Please give the measure and its reasons."), "error")
        return redirect(url_for("feedback.index", show=request.args.get("show", "new")))
    de, en = MEASURES[measure]
    when = fmt(utcnow(), lang="de")
    item.message = f"{item.message}\n\n--- Entscheidung {when} ({current_user.display_name}): {de}\n{reason}"[:20000]
    item.status = "done"
    db.session.commit()
    what = item.survey and f"„{item.survey.title}“ ({item.survey.code})" or "–"
    text = (f"Entscheidung über die Beschwerde zur Befragung {what}: {de}.\nBegründung: {reason}\n\n"
            f"Decision on the complaint about the survey {what}: {en}.\nReasons: {reason}\n\n"
            "Gegen die Entscheidung können Sie sich über das Impressum an die Betreiber wenden; der Rechtsweg bleibt "
            "offen. / You can contest the decision with the operators via the legal notice; recourse to the courts "
            "remains open." + mail.signature())
    found = _ADDRESS.search(item.contact or "")
    if found:
        mail.send(found.group(0), "Entscheidung über Ihre Beschwerde / Decision on your complaint", text)
    if item.survey is not None and measure != "none":
        from app.models import UserLogin

        for m in item.survey.admins():
            login = db.session.get(UserLogin, m.user_id) if m.user_id else None
            if login is not None and login.email:
                mail.send(login.email, "Maßnahme zu Ihrer Befragung / Measure concerning your survey", text)
    flash(de_en("Entscheidung festgehalten.", "Decision recorded.") + ("" if mail.enabled() else " " + de_en(
        "E-Mail ist nicht eingerichtet: Teilen Sie sie den Beteiligten selbst mit.",
        "Mail is not set up: tell those concerned yourself.")), "success")
    return redirect(url_for("feedback.index", show=request.args.get("show", "new")))


def open_count():
    return db.session.scalar(db.select(db.func.count(Feedback.id)).filter(Feedback.status == "new")) or 0
