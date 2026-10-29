"""Pages that are reachable without login."""
import os

from flask import Blueprint, render_template, request

bp = Blueprint("public", __name__)


def map_hosts():
    """The servers a map of this installation loads from in the browser (MAP_TILE_URL, MAP_TILE_URL_DARK and
    MAP_SEARCH_URL), for the privacy pages: (host names, whether all of them are OpenStreetMap's own)."""
    from urllib.parse import urlsplit

    from flask import current_app

    cfg, hosts = current_app.config, []
    for key in ("MAP_TILE_URL", "MAP_TILE_URL_DARK", "MAP_SEARCH_URL"):
        host = (urlsplit((cfg.get(key) or "").replace("{s}.", "")).hostname or "").lower()
        if host and host not in hosts:
            hosts.append(host)
    return hosts, all(h == "openstreetmap.org" or h.endswith(".openstreetmap.org") for h in hosts)


DEMO_BASE = "https://ask.askurious.de"  # where the demo surveys of the start page run


def demo_base():
    """The participants' address for the demo surveys the start page links to: Askurious' own, or under a brand
    (BRANDS or a whole installation with SITE_BRAND) that brand's, so its start page does not lead out to Askurious
    (audit 2026-10-10, B8)."""
    from app import brand, hosts

    return hosts.participant_base() if brand.current() else DEMO_BASE


@bp.route("/")
def index():
    return render_template("public/index.html", demo_base=demo_base())


@bp.route("/imprint")
def privacy():
    """Imprint and privacy notice (one page)."""
    from flask import current_app

    cfg = current_app.config
    imprint = {"name": cfg["IMPRINT_NAME"], "address": cfg["IMPRINT_ADDRESS"], "email": cfg["IMPRINT_EMAIL"]}
    return render_template("public/privacy.html", imprint=imprint, map_hosts=map_hosts()[0])


@bp.route("/accessibility")
def accessibility():
    """Accessibility of the platform, linked from the footer of the researchers' pages and the landing page: the
    pages of the surveys have their own statement (participate.accessibility, linked from their privacy page); the
    researchers' pages are not assessed yet. Draft, to be checked by the provider like the privacy notice."""
    from datetime import date

    from flask import current_app

    from app.lang import english
    from app.participate import ACCESSIBILITY_STATEMENT
    from app.timeutil import fmt_local

    return render_template("public/accessibility.html", email=current_app.config.get("IMPRINT_EMAIL", ""),
                           made=fmt_local(date(*ACCESSIBILITY_STATEMENT), lang="en" if english() else "de"))


# the texts behind /sources, shown as plain text: how the data files came about (README.md, the "method of making
# the alterations" of ODbL 4.6 b) and the server's libraries with their licences. The derived data files themselves are
# no longer downloads; ODbL 4.6 is met by that description and a copy on request (Ralf, 2026-10-23, licence audit A1).
# Paths relative to the app package. LICENSE, LICENSE.de.txt and NOTICE (the parts that are not under the EUPL) sit in
# the root of the code and are copied into the image (deploy/docker/Dockerfile).
SOURCE_FILES = {
    "README.md": "data/README.md",
    # tools/third_party_licences.py writes it from the lock files
    "THIRD_PARTY_LICENSES.txt": "data/THIRD_PARTY_LICENSES.txt",
    "LICENSE": "../LICENSE",
    "LICENSE.de.txt": "../LICENSE.de.txt",
    "NOTICE": "../NOTICE",
    "OFL-NotoSans.txt": "data/fonts/OFL.txt",
}


@bp.route("/sources")
def sources():
    """Licences and data sources: what Askurious contains from others, for everyone (participants included, linked
    from their privacy page and the imprint), with how the derived postcode data came about (copyright audit
    2026-10-16, U8 and U41; licence audit 2026-10-23, A1)."""
    return render_template("public/sources.html")


@bp.route("/sources/<name>")
def source_file(name):
    from flask import abort, current_app, send_file

    if name not in SOURCE_FILES:
        abort(404)
    full = os.path.normpath(os.path.join(current_app.root_path, SOURCE_FILES[name]))
    return send_file(full, mimetype="text/plain; charset=utf-8", as_attachment=False, max_age=86400)


@bp.route("/listofallfeatures")
def features():
    """All features by topic, experimental ones marked, with a footnote: on reasonable request (app/feature_list.py)."""
    from flask import current_app

    from app import experimental, feature_list

    return render_template("public/features.html", groups=feature_list.groups(), experimental_keys=set(experimental.KEYS),
                           email=current_app.config.get("IMPRINT_EMAIL", ""))


@bp.route("/privacy")
def privacy_old():
    from flask import redirect, url_for

    return redirect(url_for("public.privacy"), 301)


@bp.route("/robots.txt")
def robots():
    """Crawlers stay out of the surveys (/s/) and the researcher area; the landing page and the news may be indexed."""
    from flask import Response

    from app import brand

    lines = ["User-agent: *", "Disallow: /s/", "Disallow: /surveys/", "Disallow: /dashboard/", "Disallow: /auth/", "Allow: /",
             f"Sitemap: {brand.public_base()}/sitemap.xml", ""]  # the landing page and the news page /blog (app/blog.py)
    return Response("\n".join(lines), mimetype="text/plain")


@bp.route("/favicon.ico")
def favicon():
    """Browsers ask for it at the root by themselves (also on pages without an icon link, e.g. a PDF)."""
    from flask import current_app, send_from_directory

    from app import brand

    name = "polltergeist-favicon.ico" if brand.site() == "polltergeist" else "favicon.ico"  # the brand's own (app/brand.py)
    return send_from_directory(current_app.static_folder, name, mimetype="image/vnd.microsoft.icon", max_age=7 * 86400)


@bp.route("/healthz")
def healthz():
    """Liveness plus a check that the compiled dependencies (vendored wheels) import. Versions and error
    texts are for signed-in site admins only; everyone else sees ok/error per package."""
    from flask_login import current_user

    detail = current_user.is_authenticated and current_user.is_admin
    deps, ok = {}, True
    for name in ("numpy", "PIL", "cryptography", "docx", "lxml"):  # docx and lxml: the questionnaire as Word
        try:
            version = __import__(name).__version__
            deps[name] = version if detail else "ok"
        except Exception as e:  # report instead of failing the probe
            ok = False
            deps[name] = f"error: {e}" if detail else "error"
    from sqlalchemy import text

    from app.extensions import db

    fts = False
    try:  # construct search uses FTS5 when available, LIKE otherwise (always in other databases than SQLite)
        if db.engine.dialect.name == "sqlite":
            with db.engine.connect() as con:
                fts = bool(con.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'construct_fts'")).first())
    except Exception:
        pass
    return {"status": "ok" if ok else "degraded", "deps": deps, "fulltext": fts}, 200 if ok else 500


@bp.get("/healthz/compute")
def compute_healthz():
    """Internal status relay; nginx refuses this path on public hosts."""
    from app import compute

    ok = compute.healthy()
    return {"status": "ok" if ok else "degraded"}, 200 if ok else 503, {"Cache-Control": "no-store"}


@bp.get("/healthz/whisper")
def whisper_healthz():
    """Internal aggregate relay. nginx blocks public access; status has no Whisper credential."""
    from app import whisper_jobs

    payload, code = whisper_jobs.status()
    return payload, code, {"Cache-Control": "no-store"}


@bp.route("/privacy/delete", methods=["GET", "POST"])
def erase():
    """Delete one's own answers with the deletion code alone, without knowing the survey (Philipp, 2026-10-08): the
    number in front of the code names the survey (app/erasure.py). Linked from the privacy page of the platform.
    The same limit against guessing as on the page of a survey (participate.erase); nothing is asked or kept.
    Participants come here: in the frame of their pages and in any survey language (?lang=, else the browser's), X46."""
    from app import erasure, limits
    from app.participate import _ltr  # the example code left to right in an Arabic sentence too (X32)
    from app.surveys import LANGUAGE_NAMES
    from app.ui_i18n import tr

    codes = [code for code in LANGUAGE_NAMES if code != "de-du"]
    lang = request.args.get("lang") if request.args.get("lang") in LANGUAGE_NAMES else _erase_language(codes)
    error, status, done = None, 200, False
    if request.method == "POST":
        if not limits.allow_erase():
            status = 429
            error = tr(lang, "Zu viele Versuche. Bitte versuchen Sie es in einigen Minuten noch einmal.",
                       "Too many attempts. Please try again in a few minutes.")
        elif not request.form.get("sure"):
            error = tr(lang, "Bitte bestätigen Sie, dass Ihre Antworten endgültig gelöscht werden sollen.",
                       "Please confirm that your answers are to be deleted for good.")
        else:
            survey, target = erasure.anywhere(request.form.get("code"))
            from app import paper

            from app import interview_offline

            if target is None and paper.erase_unscanned(request.form.get("code")) is not None:
                done = True  # a paper sheet not taken over yet: marked deleted (app/paper.py)
            elif target is None and interview_offline.erase_pending(request.form.get("code")) is not None:
                done = True  # an interview still on the device of a field pack: never synced (app/interview_offline.py)
            elif target is None:
                error = tr(lang, "Zu diesem Code gibt es keine Teilnahme. Bitte prüfen Sie die Eingabe.",
                           "There is no participation for this code. Please check what you entered.")
            else:
                erasure.erase(survey, [target], "self")
                done = True
    choices = [(code, "Deutsch" if code == "de" else LANGUAGE_NAMES.get(code, code)) for code in codes]
    return render_template("public/erase.html", done=done, error=error, example=_ltr(erasure.example()), lang=lang,
                           language_choices=choices), status


def _erase_language(codes):
    """The language of the deletion page without ?lang=: as on the other public pages, the one picked in the header
    (DE/EN, kept in the session) or the signed-in person's, else the browser's among all survey languages, German if it
    names none. Before, a pick in the header was not heeded: the start page spoke English and this page German (UI
    audit 2026-10-12, U47)."""
    from flask import session
    from flask_login import current_user

    from app import lang as ui_lang

    if session.get(ui_lang.PICKED) in ui_lang.LANGUAGES or getattr(current_user, "is_authenticated", False):
        return ui_lang.current()
    return request.accept_languages.best_match(codes) or "de"


@bp.route("/verify/")
@bp.route("/verify/<code>")
def verify(code=None):
    """Check a state of a seal chain cited in a paper (app/seal.py, verify_cite; Philipp 2026-10-08): for anyone,
    without an account. /verify?code=… (the form) leads to /verify/<code>, the address to cite."""
    from flask import redirect, url_for

    from app import seal

    from app import limits

    if code is None and request.args.get("code"):
        return redirect(url_for("public.verify", code=request.args["code"].strip()))
    if code and not limits.allow_verify():  # a check costs a timestamp check per stamp (audit 2026-10-09, K4)
        return render_template("public/verify.html", code=code, result=None, busy=True), 429
    result = seal.verify_cite(code) if code else None
    return render_template("public/verify.html", code=code, result=result), 404 if code and not result["found"] else 200


@bp.route("/verify/<code>/seal.json")
def verify_chain(code):
    """The chain behind a check code (hash values and timestamps, no answers), to check it without Askurious."""
    import json

    from flask import Response, abort

    from app import limits, seal

    if not limits.allow_verify():
        abort(429)
    result = seal.verify_cite(code)
    if not result.get("found"):
        abort(404)
    from urllib.parse import quote

    from app import brand

    # the address of this chain goes with it: seal_verify.py compares an unstamped end of a file with it (S2), built
    # from the configured address of the brand, never from the Host header of the request (audit 2026-10-11, TN7)
    url = f"{brand.public_base()}/verify/{quote(code, safe='')}/seal.json"
    body = json.dumps(seal.chain(result["survey"], url=url), ensure_ascii=False, indent=1)
    return Response(body, mimetype="application/json", headers={"Content-Disposition": 'attachment; filename="seal.json"'})


@bp.route("/verify/seal_verify.py")
def verify_checker():
    """The checker (app/seal_verify.py): a file of its own that needs no Askurious."""
    import os

    from flask import current_app, send_file

    return send_file(os.path.join(current_app.root_path, "seal_verify.py"), as_attachment=True,
                     mimetype="text/x-python", download_name="seal_verify.py")


@bp.route("/language", methods=["POST"])
def language():
    """The language picked in the header before signing in (DE/EN, base.html): kept in the session (lang.browser),
    a new account takes it once; back to the page it came from."""
    from flask import redirect, session

    from app.lang import LANGUAGES, PICKED
    from app.links import local_path

    if request.form.get("lang") in LANGUAGES:
        session[PICKED] = request.form["lang"]
    return redirect(local_path(request.form.get("next")) or "/")
