"""The cron route that closes surveys at their end date."""
from flask import Blueprint, abort, current_app, request

from app import lifecycle
from app.extensions import csrf


# --- cron ------------------------------------------------------------------------

cron_bp = Blueprint("cron", __name__, url_prefix="/cron")


@cron_bp.route("/close", methods=["GET", "POST"])
@csrf.exempt
def cron_close():
    import hmac

    # the key belongs in the header X-Cron-Key (query strings end up in the server's access log);
    # ?key= still works for scheduled tasks set up before
    key = current_app.config.get("CRON_KEY")
    given = request.headers.get("X-Cron-Key") or request.args.get("key") or ""
    if not key or not hmac.compare_digest(given.encode(), key.encode()):
        abort(404)
    if not request.headers.get("X-Cron-Key"):
        # the app's log masks it (log_mask.CRON_KEY), the web server's access log may not (code review 2026-10-15, H9)
        current_app.logger.warning("/cron/close called with ?key= in the address; send the header X-Cron-Key instead")
    return {"closed": lifecycle.close_due_surveys()}
