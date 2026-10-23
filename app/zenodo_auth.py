""""Mit Zenodo verbinden": OAuth 2.0 authorization code flow per researcher.

After connecting, uploads run under the researcher's own Zenodo account without handling
tokens. Access tokens are refreshed automatically; "Trennen" deletes the stored tokens.
"""
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
from flask import Blueprint, abort, current_app, flash, redirect, request, session, url_for
from flask_login import current_user, login_required

from app import bounded, brand
from app import secrets_box
from app.extensions import db
from app.lang import de_en
from app.links import local_path
from app.models import ZenodoConnection, utcnow

bp = Blueprint("zenodo_auth", __name__, url_prefix="/zenodo")

BASES = {"sandbox": "https://sandbox.zenodo.org", "zenodo": "https://zenodo.org"}
TOKEN_MAX_ANSWER = 64 * 1024  # bytes of the token answer (app/bounded.py; audit 2026-10-13, IN-7)
SCOPES = "deposit:write"  # drafts only: publishing (and the DOI) is the researchers' step on Zenodo (Philipp, 2026-10-04)


def credentials(target):
    cfg = current_app.config
    if target == "sandbox":
        return cfg.get("ZENODO_SANDBOX_CLIENT_ID"), cfg.get("ZENODO_SANDBOX_CLIENT_SECRET")
    return cfg.get("ZENODO_CLIENT_ID"), cfg.get("ZENODO_CLIENT_SECRET")


def available(target):
    return all(credentials(target))


def connection(user, target):
    return db.session.scalar(db.select(ZenodoConnection).filter_by(user_id=user.id, target=target))


def _redirect_uri():
    return brand.public_base() + url_for("zenodo_auth.callback")


def _token_request(target, data):
    client_id, client_secret = credentials(target)
    try:
        r = bounded.request("POST", f"{BASES[target]}/oauth/token", max_bytes=TOKEN_MAX_ANSWER, seconds=60,
                            via=requests.post, timeout=30,
                            data={**data, "client_id": client_id, "client_secret": client_secret})
    except requests.RequestException as e:
        from app.zenodo import not_responding

        current_app.logger.warning("Zenodo token request (%s) failed: %s", target, e)
        raise RuntimeError(not_responding()) from e
    if r.status_code != 200:
        current_app.logger.warning("Zenodo token request (%s): %s %s", target, r.status_code, r.text[:200])
        raise RuntimeError(de_en("Zenodo lehnt die Anmeldung ab. Bitte noch einmal „Mit Zenodo verbinden“ wählen; bleibt es "
                                 "dabei, die Admins der Plattform benachrichtigen.",
                                 "Zenodo rejects the sign-in. Please choose “Connect with Zenodo” again; if it persists, "
                                 "notify the platform admins."))
    # an answer that is no JSON, or no access token in it, is a refusal too, not an internal error (audit 2026-10-11, AU7)
    try:
        payload = r.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
        raise RuntimeError(de_en("Zenodo hat keine gültige Antwort geschickt. Bitte später erneut versuchen.",
                                 "Zenodo did not send a valid answer. Please try again later."))
    return payload


def _store(user, target, payload):
    conn = connection(user, target) or ZenodoConnection(user_id=user.id, target=target)
    conn.access_token = secrets_box.encrypt(payload["access_token"])
    if isinstance(payload.get("refresh_token"), str) and payload["refresh_token"]:
        conn.refresh_token = secrets_box.encrypt(payload["refresh_token"])
    try:  # a lifetime that is no number counts as none given (AU7)
        expires = int(payload.get("expires_in") or 0)
    except (TypeError, ValueError, OverflowError):
        expires = 0
    expires = min(max(expires, 0), 10 * 365 * 86400)
    conn.expires_at = utcnow() + timedelta(seconds=expires - 60) if expires else None
    db.session.add(conn)
    return conn


def access_token(user, target):
    """A valid access token for the user's connection, refreshed if needed ("" if none)."""
    conn = connection(user, target)
    if conn is None:
        return ""
    if conn.expires_at and conn.expires_at <= utcnow():
        refresh = secrets_box.decrypt(conn.refresh_token)
        if not refresh:
            return ""
        try:
            _store(user, target, _token_request(target, {"grant_type": "refresh_token", "refresh_token": refresh}))
            db.session.commit()
        except RuntimeError:
            return ""
    return secrets_box.decrypt(conn.access_token)


@bp.route("/connect/<target>", methods=["POST"])
@login_required
def connect(target):
    """Start the round trip to Zenodo: only as a POST with the page's CSRF token, so another site cannot start it
    (audit 2026-10-22, A-05)."""
    from app import accounts

    again = accounts.need_fresh(url_for("accounts.connections") + "#zenodo")
    if again:
        return again
    if target not in BASES or not available(target):
        abort(404)
    from app.auth import _pkce_challenge

    state = secrets.token_urlsafe(24)
    # PKCE (RFC 7636, S256) as with ORCID (auth.py): the verifier stays in the session, Zenodo gets its hash; a
    # server without PKCE ignores both parameters (RFC 6749, 3.1) (audit 2026-10-13, IN-5)
    verifier = secrets.token_urlsafe(48)
    session["zenodo_oauth"] = {"state": state, "target": target, "next": request.form.get("next", ""),
                               "verifier": verifier}
    query = urlencode({"client_id": credentials(target)[0], "response_type": "code", "scope": SCOPES,
                       "redirect_uri": _redirect_uri(), "state": state, "code_challenge": _pkce_challenge(verifier),
                       "code_challenge_method": "S256"})
    return redirect(f"{BASES[target]}/oauth/authorize?{query}")


@bp.route("/callback")
@login_required
def callback():
    pending = session.pop("zenodo_oauth", None) or {}
    nxt = pending.get("next", "")
    back = redirect(local_path(nxt) or url_for("dashboard.index"))
    if not pending or request.args.get("state") != pending.get("state"):
        flash(de_en("Zenodo-Verbindung: ungültiger Zustand, bitte erneut versuchen.", "Zenodo connection: invalid state, please try again."), "error")
        return back
    if "code" not in request.args:
        flash(de_en("Zenodo-Verbindung abgebrochen.", "Zenodo connection cancelled."), "warning")
        return back
    target = pending["target"]
    try:
        payload = _token_request(target, {"grant_type": "authorization_code", "code": request.args["code"],
                                          "redirect_uri": _redirect_uri(),
                                          **({"code_verifier": pending["verifier"]} if pending.get("verifier") else {})})
    except RuntimeError as e:
        flash(str(e), "error")
        return back
    _store(current_user, target, payload)
    db.session.commit()
    flash(de_en("Mit der Zenodo-Sandbox verbunden.", "Connected with the Zenodo sandbox.") if target == "sandbox"
          else de_en("Mit Zenodo verbunden.", "Connected with Zenodo."), "success")
    return back


@bp.route("/disconnect/<target>", methods=["POST"])
@login_required
def disconnect(target):
    conn = connection(current_user, target)
    if conn is not None:
        db.session.delete(conn)
        db.session.commit()
        flash(de_en("Zenodo-Verbindung getrennt; die gespeicherten Schlüssel wurden gelöscht.",
                    "Zenodo connection removed; the stored keys were deleted."), "success")
    nxt = request.form.get("next", "")
    return redirect(local_path(nxt) or url_for("dashboard.index"))
