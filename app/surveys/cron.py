"""The cron route that closes surveys at their end date."""
from flask import Blueprint, abort, current_app, request

from app import lifecycle
from app.extensions import csrf


# --- cron ------------------------------------------------------------------------

cron_bp = Blueprint("cron", __name__, url_prefix="/cron")


@cron_bp.route("/close", methods=["POST"])
@csrf.exempt
def cron_close():
    """For a server without the housekeeping process (the Docker server has it and answers 404 here in nginx): a
    scheduled task sends POST with the key in the header X-Cron-Key. The key is never taken from the address (it
    would end up in access logs), and wrong keys are counted per sender like other secrets."""
    import hmac

    from app import limits

    key = current_app.config.get("CRON_KEY")
    given = request.headers.get("X-Cron-Key") or ""
    if not key or not limits.allow_secret_try(count=False):
        abort(404)
    if not hmac.compare_digest(given.encode(), key.encode()):
        limits.allow_secret_try()
        abort(404)
    return {"closed": lifecycle.close_due_surveys()}
