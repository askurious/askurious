"""Geodata the maps of the results page load by themselves: the boundaries of the postal code areas, one file per
postal region (app/data/plz_areas, OpenStreetMap, ODbL). Public data, but only the pages of signed-in researchers
use it, so only they get it. The files are sent as they lie on the disk (gzip); nothing is read into memory."""
from flask import Blueprint, abort, request, send_file
from flask_login import login_required

bp = Blueprint("geo", __name__, url_prefix="/geo")

YEAR = 365 * 24 * 3600


@bp.route("/postcode/<region>.json")
@login_required
def plz_areas(region):
    from app.items import postal_code

    path = postal_code.region_file(region)  # two digits and an existing file, nothing else becomes a path
    if path is None:
        abort(404)
    # with the version in the address (?v=) the file never changes under it: browsers may keep it for a year
    versioned = request.args.get("v") == postal_code.areas_version()
    response = send_file(path, mimetype="application/json", conditional=True, max_age=YEAR if versioned else None)
    response.headers["Content-Encoding"] = "gzip"
    return response
