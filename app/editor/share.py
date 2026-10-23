"""Shared preview links (pretest): run, comment, QR code."""
from flask import Blueprint, abort, render_template, request, url_for

from app import access
from app.editor.preview import PRETEST_DAYS, _comment_shot, _run_preview, _share_serializer, pretest_generation
from app.extensions import db


# --- preview on the researcher's own phone (QR code) ------------------------------

share_bp = Blueprint("preview_share", __name__, url_prefix="/p")


def _maker(data):
    """The account that made the link, or None."""
    from app.models import User

    if "user" in data:
        return db.session.get(User, data["user"])
    from app import people_keys

    return people_keys.account(data.get("u"), create=False)  # previously issued links (by key) expire normally


def _shared_survey(token, check_member=True):
    """The survey of a valid phone or pretest link, else None; raises SignatureExpired when its days are over.
    A link of an older generation (all were revoked) or of someone who lost access is no link any more."""
    from itsdangerous import BadSignature, SignatureExpired

    from app.models import Survey

    try:
        data, made = _share_serializer().loads(token, max_age=max(PRETEST_DAYS) * 24 * 3600, return_timestamp=True)
    except SignatureExpired:
        raise
    except BadSignature:
        return None
    import time

    days = data.get("d") if data.get("d") in PRETEST_DAYS else 1
    if time.time() - made.timestamp() > days * 24 * 3600:
        raise SignatureExpired("expired")
    survey = db.session.get(Survey, data.get("s"))
    if survey is None or int(data.get("g") or 0) != pretest_generation(survey):
        return None
    maker = _maker(data)
    if check_member and access.own_role_of_user(maker, survey) is None:  # who made the link still has access
        # by an entry or a group: what an example survey gives everyone does not keep the pretest links of someone who
        # left alive (audit 2026-10-11, rights H2); only the phone link someone made as an example's viewer holds,
        # while the survey is one (marked "x" by share_url, one day)
        if not (data.get("x") and days == 1 and access.showcased(survey, maker) and access.role_of_user(maker, survey)):
            return None
    if check_member:
        from app import kind_access

        # nor someone whose level for the survey's type was set to "off" (app/kind_access.py): the link ends with the
        # sight of the survey, as role_of_user caps it (audit 2026-10-18, AUTHZ-04)
        if maker is None or not kind_access.sees(maker, survey.kind):
            return None
    return survey


def _may_comment(token, survey):
    """Comments over a phone or pretest link: not when its maker sees the survey only as an example survey."""
    data = _share_serializer().loads(token)  # already checked by _shared_survey
    maker = _maker(data)
    return not (access.showcased(survey, maker) and access.own_role_of_user(maker, survey) is None)


@share_bp.route("/<token>", methods=["GET", "POST"])
def run(token):
    """The preview on a phone or for a test person, without login: the token proves a member created the link."""
    from itsdangerous import SignatureExpired

    try:
        survey = _shared_survey(token)
    except SignatureExpired:
        return render_template("participant/share_expired.html"), 410
    if survey is None:
        abort(404)
    from flask import session

    from app.participate import PRETEST_KEY

    seen = list(session.get(PRETEST_KEY) or [])
    if survey.id not in seen:  # the privacy page of a draft shows its content to this browser (participate._survey, S19)
        session[PRETEST_KEY] = seen[-9:] + [survey.id]
    lang = request.values.get("lang", survey.default_language)
    lang = lang if lang in survey.languages else survey.default_language
    return _run_preview(survey, lang, lambda **kw: url_for("preview_share.run", token=token, **kw), on_phone=True,
                        comment_url=url_for("preview_share.comment", token=token) if _may_comment(token, survey) else None)


@share_bp.route("/<token>/comment", methods=["POST"])
def comment(token):
    """A comment from a test person on the phone link (no login; the valid link is the permission)."""
    from itsdangerous import BadSignature

    from app import comments

    try:
        survey = _shared_survey(token)
    except BadSignature:  # also expired
        abort(404)
    if survey is None or not _may_comment(token, survey):
        abort(404)
    from app import limits

    if not limits.allow_phone_comment(survey):
        return {"error": "Über diesen Link sind heute schon sehr viele Kommentare eingegangen. Bitte später erneut."}, 429
    from app import comment_shots

    try:
        comment, error = comments.add(survey, request.form, request.form.get("name") or "Testperson (Pretest-Link)",
                                      "phone", page=request.form.get("page", type=int), files=request.files)
    except comment_shots.Refused as e:
        return {"error": str(e)}, e.status
    return ({"error": error}, 400) if error else comments.as_dict(comment)


@share_bp.route("/<token>/comment/<int:comment_id>/shot")
def comment_shot(token, comment_id):
    """The image of an own comment of this test run; images of others stay with the members of the survey."""
    from itsdangerous import BadSignature

    from app import comments

    try:
        survey = _shared_survey(token, check_member=False)
    except BadSignature:
        abort(404)
    if survey is None or comment_id not in comments.mine(survey):
        abort(404)
    return _comment_shot(survey, comment_id)


@share_bp.route("/<token>/comment/<int:comment_id>/delete", methods=["POST"])
def comment_delete(token, comment_id):
    from itsdangerous import BadSignature

    from app import comments

    try:
        survey = _shared_survey(token, check_member=False)
    except BadSignature:
        abort(404)
    if survey is None:
        abort(404)
    return ({"ok": True}) if comments.delete_own(survey, comment_id) else ({"error": "nicht möglich"}, 403)
