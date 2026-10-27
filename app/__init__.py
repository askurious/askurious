import hashlib
import os
import re

from flask import Flask, g, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import DEV_SECRET_KEY, Config, is_local_host, url_host
from app.extensions import csrf, db, login_manager


STATIC_MAX_AGE = 365 * 24 * 3600
# licence files of vendored code without an extension (LICENSE, LICENSE_CORE_JS): shown as plain text (B-3)
LICENCE_FILE = re.compile(r"(^|/)LICENSE[^/.]*$")
# blueprints whose files a browser may keep for signed-in people too: images by random name, public map data
CACHEABLE_BLUEPRINTS = ("media", "geo")
# blueprints of pages for participants and other visitors without an account: never kept (security audit 2026-10-06, S15)
PARTICIPANT_BLUEPRINTS = ("participate", "preview_share", "live_public", "diary_api")
# forms anyone can open: sign-in, "Passwort vergessen?" and the check of a cited state; never kept either (audit
# 2026-10-09b, O4)
NO_STORE_ENDPOINTS = ("auth.sign_in", "auth.forgot_password", "public.verify")
PERMISSIONS = "camera=(), microphone=(), geolocation=(), display-capture=(), payment=(), usb=(), serial=(), bluetooth=(), interest-cohort=()"



def _deployed_at():
    """When the deploy script uploaded this code and which commit it was, for the status page (app/status_node.py):
    {"at": local datetime, "id": "0bdef88f" or "", "skipped": ["tests", "osv"] or []}; None outside a deploy.
    deployed_at.txt holds the UTC time, the commit on a second line and, when the deploy skipped checks
    (SKIP_TESTS, SKIP_OSV), a third line "skipped=tests,osv" (audit 2026-10-13, SV-5/DP-9)."""
    from datetime import datetime

    from app.timeutil import to_local

    try:
        with open(os.path.join(os.path.dirname(__file__), "deployed_at.txt")) as f:
            lines = f.read().split()
        stamp = datetime.strptime(lines[0], "%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, IndexError):
        return None
    commit = lines[1] if len(lines) > 1 and re.fullmatch(r"[0-9a-f]{7,40}", lines[1]) else ""
    skipped = [s for line in lines[2:] if line.startswith("skipped=")
               for s in line[len("skipped="):].split(",") if s in ("tests", "osv", "unmerged")]
    return {"at": to_local(stamp), "id": commit, "skipped": skipped}


def _round_filter(value, precision=0, method="common"):
    """Jinja's round, but "common" rounds half up (kaufmännisch): 12.5 -> 13. Returns a float like Jinja's."""
    import math

    from app import units

    if method == "ceil":
        return math.ceil(value * 10 ** precision) / 10 ** precision
    if method == "floor":
        return math.floor(value * 10 ** precision) / 10 ** precision
    rounded = units.round_half_up(value, precision)
    return float(rounded) if rounded is not None else value


BRIDGE_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "fe80::/10")


def _loopback(address):
    """Whether `address` (text) is this computer: 127.0.0.0/8, ::1, or ::ffff:127.x as a dual-stack socket gives it."""
    import ipaddress

    try:
        ip = ipaddress.ip_address((address or "").split("%")[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def is_local_request():
    """Whether this request came from the developer's own computer: the only case in which the development
    SECRET_KEY and DEV_LOGIN answer (create_app, auth.dev_login).

    Two things must hold. The name is one of this computer's own (the Host header itself: ProxyFix does not take
    X-Forwarded-Host, x_host=0 in create_app). And the address is this computer: without a proxy (PROXY_COUNT=0)
    the peer of the connection, behind a proxy the entry the own proxy appended to X-Forwarded-For (after
    ProxyFix), which a client cannot choose. The Host header alone is the client's to choose: with PROXY_COUNT=0
    "Host: localhost" from anywhere counted as local before (audit 2026-10-18, CFG-01/AUTH-4).

    A development server in a container or a VM sees its host's bridge address even for a browser on the same
    computer: DEV_TRUST_BRIDGE=1 lets private addresses count as well, without a proxy only, and create_app starts
    with it only under a local PUBLIC_BASE_URL."""
    import ipaddress

    from flask import current_app

    if not is_local_host(url_host("//" + request.host)):
        return False
    if _loopback(request.remote_addr):
        return True
    if current_app.config.get("PROXY_COUNT") or not current_app.config.get("DEV_TRUST_BRIDGE"):
        return False
    try:
        address = ipaddress.ip_address((request.remote_addr or "").split("%")[0])
    except ValueError:
        return False
    # the networks of a bridge or a VM (RFC 1918, unique local, link-local); not is_private, which also counts the
    # documentation ranges such as 203.0.113.0/24
    nets = [ipaddress.ip_network(net) for net in BRIDGE_NETS]
    return any(address in net for net in nets if net.version == address.version)


def _tile_source(url):
    """The origin of a tile server for img-src, a sub-domain placeholder ({s}.tile.example) as a wildcard."""
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    host = ".".join("*" if "{" in label else label for label in parts.netloc.split("."))
    return f"{parts.scheme}://{host}"


def _overlay_templates(app):
    """Templates of the installation (INSTANCE_OVERLAY/templates) come first, then the code's own: an operator puts
    their imprint, privacy and accessibility texts there without changing the public code."""
    from jinja2 import ChoiceLoader, FileSystemLoader

    folder = os.path.join(app.config.get("INSTANCE_OVERLAY") or "", "templates")
    if app.config.get("INSTANCE_OVERLAY") and os.path.isdir(folder):
        app.jinja_loader = ChoiceLoader([FileSystemLoader(folder), app.jinja_loader])


def create_app(config_class=Config, server=False):
    """The app. server=True for the entry point of a real server (passenger_wsgi.py: Plesk and gunicorn in Docker),
    which may not take the local default of PUBLIC_BASE_URL for granted (below)."""
    app = Flask(__name__)
    app.config.from_object(config_class)
    _overlay_templates(app)
    from app.request_values import CleanRequest

    app.request_class = CleanRequest  # no NUL or other control characters in form, query or JSON values (PG)
    # Both are for a server on the developer's own computer only. Asked the other way round than before ("is
    # this behind https?"): a server at http://survey.example, or one whose address was mistyped, is not local either.
    local = is_local_host(url_host(app.config["PUBLIC_BASE_URL"]))
    if not local and app.config["SECRET_KEY"] in ("", DEV_SECRET_KEY):
        # a known key would let anyone forge sessions (log in as any researcher) and decrypt stored tokens
        raise RuntimeError("SECRET_KEY is missing or the development default, and PUBLIC_BASE_URL "
                           f"({app.config['PUBLIC_BASE_URL'] or 'empty'}) is not a local address; "
                           "set a random SECRET_KEY in .env")
    if not local and app.config["DEV_LOGIN"]:
        # /auth/dev-login signs in any ORCID iD without a password, admins included
        raise RuntimeError(f"DEV_LOGIN is on and PUBLIC_BASE_URL ({app.config['PUBLIC_BASE_URL'] or 'empty'}) is not a "
                           "local address; remove DEV_LOGIN from .env (local development only)")
    if not local and app.config.get("DEV_TRUST_BRIDGE"):
        # lets private addresses count as this computer (is_local_request): never on a server
        raise RuntimeError(f"DEV_TRUST_BRIDGE is on and PUBLIC_BASE_URL ({app.config['PUBLIC_BASE_URL'] or 'empty'}) is "
                           "not a local address; remove DEV_TRUST_BRIDGE from .env (local development only)")
    if (server and local and "PUBLIC_BASE_URL" not in os.environ
            and (app.config["SECRET_KEY"] in ("", DEV_SECRET_KEY) or app.config["DEV_LOGIN"])):
        # a server whose .env lacks the line PUBLIC_BASE_URL has the local default there, and the checks above let
        # the development key or DEV_LOGIN through (audit 2026-10-18, CFG-01 b). On the developer's computer the app
        # starts through run.py or create_app() itself, never through passenger_wsgi.py
        raise RuntimeError("PUBLIC_BASE_URL is not set, and the server would start with "
                           + ("DEV_LOGIN" if app.config["DEV_LOGIN"] else "the development SECRET_KEY")
                           + "; set PUBLIC_BASE_URL and a random SECRET_KEY in .env")
    from app import key_strength

    key_strength.check(app, local)  # a key too short or weak stops the start (KR-5, CFG-02)
    from app import mail

    if mail.problem(app):  # MAIL_SECURITY=none with a login: refused at every mail, said once here (CFG-09)
        app.logger.error("Mail: %s (Administration > Server, Mailserver)", mail.problem(app))
    # The check above reads PUBLIC_BASE_URL, and a server whose .env lacks that line has the local default there:
    # it would start with the known key. So the app also looks at each request: with the development key it
    # answers only under this computer's own names and only to this computer (is_local_request).
    @app.before_request
    def _development_key_stays_local():
        if app.config["SECRET_KEY"] in ("", DEV_SECRET_KEY) and not is_local_request():
            return ("Dieser Server läuft mit dem Entwicklungsschlüssel und antwortet deshalb nur unter localhost. "
                    "Bitte in .env einen zufälligen SECRET_KEY und die Adresse des Servers als PUBLIC_BASE_URL "
                    "eintragen.", 503, {"Content-Type": "text/plain; charset=utf-8"})

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    @app.context_processor
    def _page_assets():
        from jinja2.utils import Namespace

        return {"assets": Namespace(loaded_leaflet=False, loaded_geolocation=False, extra_scripts=[])}

    static_hashes = {}  # (path, mtime, size) -> short content hash; a changed file gets a new entry

    @app.url_defaults
    def _static_version(endpoint, values):
        """?v=<content hash> on static files: a changed app.js/app.css gets a new address at once, an
        unchanged one keeps its address across deploys and checkouts (tar and git reset file times)."""
        if endpoint == "static" and "filename" in values and "v" not in values:
            path = os.path.join(app.static_folder, values["filename"])
            try:
                st = os.stat(path)
                key = (path, st.st_mtime_ns, st.st_size)
                if key not in static_hashes:
                    with open(path, "rb") as f:
                        static_hashes[key] = hashlib.sha256(f.read()).hexdigest()[:12]
                values["v"] = static_hashes[key]
            except OSError:
                pass

    def _static_max_age(filename):
        """Versioned static files never change under their address: browsers may keep them a year.
        Without ?v= (old links, hand-written URLs) the browser asks again every time."""
        if request.endpoint != "static" or not request.args.get("v"):
            return None
        path = os.path.join(app.static_folder, filename)
        try:
            st = os.stat(path)
            key = (path, st.st_mtime_ns, st.st_size)
            if key not in static_hashes:
                with open(path, "rb") as source:
                    static_hashes[key] = hashlib.sha256(source.read()).hexdigest()[:12]
            return STATIC_MAX_AGE if request.args["v"] == static_hashes[key] else 0
        except OSError:
            return 0

    app.get_send_file_max_age = _static_max_age

    def _icon_sprite():
        """The address of app/static/icons.svg with its ?v= (tools/icon_sprite.py), once per request: the icon()
        macro of _icons.html points every icon there instead of drawing it inline (audit 2026-10-22, G-05)."""
        from flask import has_request_context, url_for

        if not has_request_context():
            return app.static_url_path + "/icons.svg"
        if "icon_sprite" not in g:
            g.icon_sprite = url_for("static", filename="icons.svg")
        return g.icon_sprite

    app.jinja_env.globals["icon_sprite"] = _icon_sprite

    @app.after_request
    def _licence_as_text(response):
        """The licence files of the vendored code (static/vendor/*/LICENSE, LICENSE_CORE_JS, ...) have no extension:
        sent as application/octet-stream with nosniff, a browser saves them instead of showing them. As plain text,
        like nginx's locations/*.conf do (licence audit 2026-10-21, B-3)."""
        if request.endpoint == "static" and LICENCE_FILE.search((request.view_args or {}).get("filename", "")):
            response.headers["Content-Type"] = "text/plain; charset=utf-8"
        return response

    @app.before_request
    def _redirect_old_hosts():
        """Old addresses (REDIRECT_HOSTS) move permanently to PUBLIC_BASE_URL, keeping path and query."""
        from flask import redirect, request

        if app.config["REDIRECT_HOSTS"] and request.host.split(":")[0].lower() in app.config["REDIRECT_HOSTS"]:
            target = app.config["PUBLIC_BASE_URL"] + request.full_path.rstrip("?")
            return redirect(target, code=308 if request.method not in ("GET", "HEAD") else 301)

    from app.url_names import redirect_german

    app.before_request(redirect_german)  # German addresses of before -> English, before the side's guard
    from app import hosts

    if app.config["SIDE"] not in ("", hosts.PARTICIPANT, hosts.RESEARCHER):
        raise RuntimeError(f"SIDE is {app.config['SIDE']!r}; allowed: participant, researcher, or leave it out")
    if app.config["SIDE"] == hosts.PARTICIPANT and not app.config.get("PARTICIPANT_BASE_URL"):
        # its public pages move to PUBLIC_BASE_URL: without an own address that may be this very process (H5)
        raise RuntimeError("SIDE=participant needs PARTICIPANT_BASE_URL (the participants' own address)")
    for name in ("PARTICIPANT_HANDOVER_UNTIL", "PARTICIPANT_OLD_COOKIE_UNTIL"):
        try:
            hosts.day_setting(app.config.get(name))
        except ValueError:  # else every old participation link on the main address would answer 500 (H5)
            raise RuntimeError(f"{name} is {app.config.get(name)!r}; allowed: a day as YYYY-MM-DD, or empty") from None
    from app import compute

    if app.config.get("COMPUTE_URL") and not compute.url_ok(app.config["COMPUTE_URL"]):
        # plain http to an outside host: the estimations stay on this server (audit 2026-10-13, IN-6)
        app.logger.warning("COMPUTE_URL ignored: plain http only inside the own network; use https")
    for url_key, credit_key in (("MAP_TILE_URL", "MAP_TILE_ATTRIBUTION"), ("MAP_TILE_URL_DARK", "MAP_TILE_ATTRIBUTION_DARK")):
        # another tile service needs its own credit for its map style; the OSM line alone does not give it
        # (copyright audit 2026-10-16, U34)
        url = app.config.get(url_key) or ""
        credit = app.config.get(credit_key) or app.config.get("MAP_TILE_ATTRIBUTION") or ""  # dark falls back
        if url and "openstreetmap.org" not in url and (not credit or credit == Config.MAP_TILE_ATTRIBUTION):
            app.logger.warning("%s is not OpenStreetMap's: set %s to the credit its provider asks for",
                               url_key, credit_key)
    # each side answers its own paths (own address for participants, app/hosts.py); before the CSRF check below
    app.before_request(hosts.guard)
    app.jinja_env.globals["url_for"] = hosts.url_for
    app.jinja_env.globals["participant_base"] = hosts.participant_base
    from app import notify

    app.jinja_env.globals["slack_full"] = notify.full  # what goes to Slack, for the privacy texts (app/notify.py)

    # PROXY_COUNT is the number of proxies in front of the app (Plesk with nginx in front: 1; no proxy: no line),
    # checked on Administration -> IP-Region. ProxyFix takes the client address and the scheme from the entries
    # those proxies appended. X-Forwarded-Host is not taken (x_host=0): every absolute address is built from
    # PUBLIC_BASE_URL, and the host name only serves the local checks above (is_local_request) and the redirect of
    # REDIRECT_HOSTS, which read the Host header as nginx passes it on (the name the browser asked for). Trusting
    # the forwarded name let "X-Forwarded-Host: localhost" pass for a local request (audit 2026-10-03, S4/H12).
    if app.config["PROXY_COUNT"]:
        n = app.config["PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=0)

    from app.ids import IdConverter

    app.url_map.converters["int"] = IdConverter  # a number too large for the database: 404, not 500 (C2)
    from app.links import SurveyIdConverter

    app.url_map.converters["sid"] = SurveyIdConverter  # /surveys/<code>/...
    from app.url_names import StepConverter

    app.url_map.converters["step"] = StepConverter  # /surveys/<code>/setup/<step>: English in the address
    @app.before_request
    def _small_forms_for_participants():
        """Pages anyone can send to without signing in take small forms only. Registered before the CSRF check,
        which is the first to read the form."""
        if request.blueprint in ("participate", "preview_share", "live_public", "diary_api") and app.config.get("PARTICIPANT_FORM_BYTES"):
            request.max_content_length = app.config["PARTICIPANT_FORM_BYTES"]
            if request.mimetype == "multipart/form-data" and request.blueprint == "participate":
                from app.items import voice

                # a page that carries voice messages: room for all of them (T-9)
                request.max_content_length += voice.records((request.view_args or {}).get("uid"))
        elif request.endpoint == "wizard.import_file":  # a survey file holds its images (app/transfer.py)
            request.max_content_length = app.config["IMPORT_MAX_BYTES"]
        elif request.endpoint == "paper.upload":  # scanned paper questionnaires (app/paper.py)
            from app import paper

            request.max_content_length = paper.UPLOAD_BYTES
        elif request.endpoint == "compute.result":  # a signed estimate from the Rechenknecht (app/compute.py)
            request.max_content_length = 64 * 1024 * 1024

    db.init_app(app)
    from app import storage_guard  # noqa: F401 -- registers transaction/file gates for consistent volume backups
    from flask_migrate import Migrate

    from app.migrations import DIRECTORY as migrations_folder

    Migrate(app, db, directory=migrations_folder, render_as_batch=True, compare_type=True)  # flask db … (development)
    login_manager.init_app(app)
    csrf.init_app(app)

    @app.before_request
    def _researcher_csrf_age():
        """After the CSRF check (registered after it): a signed-in researcher's token counts CSRF_HOURS, not for the
        whole session (audit 2026-10-18, INJ-4). Participants' pages keep tokens without expiry (a pause of days)."""
        from flask_login import current_user
        from flask_wtf.csrf import CSRFError, validate_csrf
        from wtforms import ValidationError

        from app import hosts

        if not g.get("csrf_valid") or not app.config.get("CSRF_HOURS") or not current_user.is_authenticated:
            return None
        if hosts.side_of(request.endpoint) in (hosts.PARTICIPANT, hosts.SHARED):
            return None
        try:
            validate_csrf(csrf._get_csrf_token(), time_limit=int(app.config["CSRF_HOURS"] * 3600))
        except ValidationError as e:
            raise CSRFError(e.args[0]) from None
        return None

    from app import admin, auth, constructs_page, dashboard, element_types, feedback, terms, editor, lifecycle, media, participate, public, samplesize, surveys, timeutil, wizard, zenodo_auth
    from app import translations
    from app import seal  # noqa: F401  its after_flush listener writes the seal chains (experimental)
    from sqlalchemy.orm.exc import StaleDataError

    # a page or answer sent twice (double click, double tap): the participant pages, the preview, the live phones;
    # a researcher's change that met a participation saved at the same moment is named (audit 2026-10-21, B7)
    app.register_error_handler(StaleDataError, _stale)

    def _backup_window(e):
        """A request that waited STORAGE_GATE_WAIT for the backup window (app/storage_guard.py; audit 2026-10-15,
        NB-4): the participants' pages ask to send again, every other address gets 503 with Retry-After."""
        if request.blueprint == "participate":
            return participate._database_busy(e)
        from app.lang import de_en

        db.session.rollback()
        return (de_en("Gerade läuft eine Datensicherung. Bitte versuchen Sie es in einigen Sekunden noch einmal.",
                      "A backup is running. Please try again in a few seconds."), 503,
                {"Retry-After": "10", "Content-Type": "text/plain; charset=utf-8"})

    app.register_error_handler(storage_guard.StorageBusy, _backup_window)
    from app import jobs

    def _estimate_limit(e):
        """limits.allow_estimate refused (audit 2026-10-22, D-04): the page says so, as for "already running"."""
        from flask import flash, jsonify, redirect

        from app.lang import de_en
        from app.links import local_path

        db.session.rollback()
        message = de_en("Sie haben in der letzten Stunde schon {n} Schätzungen gestartet. Bitte warten Sie, bis eine "
                        "fertig ist, und starten Sie die nächste etwas später.",
                        "You have started {n} estimations within the last hour. Please wait until one is done and "
                        "start the next a little later.", n=app.config.get("ESTIMATE_LIMIT"))
        if request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json":
            return jsonify(state="limit", message=message, ok=False), 429
        flash(message, "error")
        return redirect(local_path(request.referrer or "") or "/"), 303

    app.register_error_handler(jobs.EstimateLimit, _estimate_limit)
    from sqlalchemy.exc import DataError

    def _number_out_of_range(e):
        """A number the database cannot hold (a huge id in a form or query; SQLite: OverflowError, PostgreSQL:
        DataError): 400 instead of 500, with a line in the log (audit 2026-10-10, C2). Blueprints with their own
        answer (the editor) keep it."""
        from app.lang import de_en

        db.session.rollback()
        app.logger.warning("Number out of range on %s %s: %s", request.method, request.path, type(e).__name__)
        return de_en("Die Zahl ist zu groß. Bitte einen kleineren Wert eingeben.",
                     "The number is too large. Please enter a smaller value."), 400

    app.register_error_handler(OverflowError, _number_out_of_range)
    from app import log_mask

    log_mask.install(app)  # no working token of a link in the log (audit 2026-10-13, KR-9)
    try:
        # every Word export (methods text, manuscript tables, simulator, ...) drops characters XML cannot hold, not
        # only after a questionnaire export ran in this process (questionnaire_docx._only_xml; audit 2026-10-13, DA-9)
        from app import questionnaire_docx  # noqa: F401
    except ImportError:  # a server without python-docx: no Word export at all (app/admin.py says so)
        pass
    app.register_error_handler(DataError, _number_out_of_range)
    app.register_blueprint(public.bp)
    from app import blog

    app.register_blueprint(blog.bp)  # the news page /blog and its administration (blog-konzept.md)
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(constructs_page.bp)
    app.register_blueprint(element_types.bp)  # the overview of all element types (concept of 2026-10-08)
    app.register_blueprint(terms.bp)
    app.register_blueprint(feedback.bp)
    app.register_blueprint(admin.bp)
    from app import designs

    app.register_blueprint(designs.bp)
    from app import geo

    app.register_blueprint(geo.bp)
    from app import compute

    app.register_blueprint(compute.bp)  # Rechenknecht reports (off without COMPUTE_URL and COMPUTE_SECRET)
    app.jinja_env.globals.update(designs_for=designs.available, design_may_edit=designs.may_edit)
    from app import org_designs

    app.jinja_env.globals.update(org_designs_for=org_designs.for_user)
    from app import panel as panel_mod

    app.jinja_env.globals.update(panel_outcome_help=panel_mod.outcome_help)  # "Was bedeuten die Status?"
    from app import groups

    app.register_blueprint(groups.bp)
    app.jinja_env.globals.update(groups_in_nav=groups.in_nav, groups_of=groups.of, group_label=groups.label)
    from app import names, org_admin, orgs

    app.register_blueprint(org_admin.bp)  # "Verwaltung" of an organisation's admins (stage 3)
    # organisations (app/orgs.py): shown only once there is more than one (A24)
    app.jinja_env.globals.update(org_several=orgs.several, org_get=orgs.get, org_of=orgs.of, org_label=orgs.label,
                                 orgs_creatable=orgs.creatable, org_is_admin=orgs.is_admin_of,
                                 orgs_administered=orgs.administered, org_for_new=orgs.for_new,
                                 org_display_name=orgs.display_name, org_name_of=orgs.name_of,
                                 orgs_creatable_all=lambda: orgs.all_orgs(guests=False),
                                 org_affiliation=orgs.affiliation_of,
                                 invite_scopes=orgs.SCOPES, invite_scope_label=orgs.scope_label,
                                 platform_scope=orgs.platform_scope, scope_of=orgs.scope_of,
                                 open_from_outside=orgs.open_from_outside,
                                 names_modes=names.MODES, names_mode_label=names.mode_label, names_hide=names.hides,
                                 name_hidden=names.is_hidden, orcid_hidden=names.orcid_hidden,
                                 person_name_of=names.of_id, names_own_note=names.own_note,
                                 org_short=orgs.short_of,
                                 org_logo_for=lambda survey: __import__("app.theme", fromlist=["x"]).org_logo(orgs.of(survey)))
    from app import access as access_rules

    app.jinja_env.globals.update(showcase_only=access_rules.showcase_only)  # an example survey seen only as one
    from app import retention

    # the retention periods in the closing list of "Datenerhebung" (surveys/_status.html; UI/UX audit 2026-10-16, 2)
    app.jinja_env.globals.update(retention_of=retention.of, retention_label=retention.label)
    # duplicating in the menu "⋯" of a survey (surveys/_survey_menu.html) only with the right to create surveys
    app.jinja_env.globals["may_create_surveys"] = lambda user: __import__("app.auth", fromlist=["x"]).may_create_surveys(user)
    from app import kind_access

    def _kind_sees(kind, user=None):
        from flask_login import current_user

        return kind_access.sees(user or current_user, kind)

    # the survey types this person sees (app/kind_access.py): help sections, header areas
    app.jinja_env.globals.update(kind_sees=_kind_sees, kind_label=kind_access.label, kind_level_label=kind_access.level_label,
                                 kind_keys=kind_access.KINDS, kind_level_of=kind_access.level,
                                 kind_levels_mine=lambda: kind_access.levels(__import__("flask_login").current_user),
                                 kind_rank=lambda level: kind_access._RANK.get(level, 0),
                                 # "Anlegen" for a type: duplicating a survey of it (dashboard, menu "⋯")
                                 kind_creates=lambda kind: kind_access.may_create(__import__("flask_login").current_user, kind))
    from app import account_types

    # user types with a period (app/account_types.py): lists in the administration, the hint before the end (N11)
    app.jinja_env.globals.update(account_type_options=lambda org_id=None: account_types.all_types(hidden=False,
                                                                                                    org_id=org_id),
                                 account_type_name=account_types.name, period_describe=account_types.describe,
                                 course_spec=account_types.course_spec,
                                 account_type_get=lambda type_id: account_types.get(type_id),
                                 expiry_notice=lambda: account_types.notice_for(__import__("flask_login").current_user))
    from app import twofactor as twofactor_rules

    # a site admin's rights on an ordinary page: with the second factor while ADMIN_TOTP is on (twofactor.admin_rights)
    app.jinja_env.globals.update(admin_rights=twofactor_rules.admin_rights)
    from app import accounts

    from app.models import is_orcid

    app.register_blueprint(accounts.bp)
    from app import twofactor

    app.register_blueprint(twofactor.bp)
    from app import invites

    app.register_blueprint(invites.bp)  # invitations by e-mail address (mail on only)
    app.jinja_env.globals["open_invites"] = invites.open_for
    from app import joinlinks

    app.register_blueprint(joinlinks.bp)  # links to join a group (gruppen-freischaltung-konzept.md)
    from app import signup_links

    app.register_blueprint(signup_links.bp)  # registration links of the platform (app/signup_links.py)
    from app import bulk_invites

    app.register_blueprint(bulk_invites.bp)  # lists of addresses for a course (konzept-nutzertypen-2026-10-15.md)
    from app import survey_invites

    app.register_blueprint(survey_invites.bp)  # invitation mails to participants (mail on only)
    app.jinja_env.globals.update(invitations_on=survey_invites.may_use, invitation_state=survey_invites.state)
    app.jinja_env.globals["is_orcid"] = is_orcid
    from app import mail

    app.jinja_env.globals["mail_enabled"] = mail.enabled  # off: no page shows a mail function (app/mail.py)

    def orcid_has_account(orcid):
        """True when someone signed in here with this ORCID iD (only ORCID sign-in gives an account an iD as key):
        _macros.orcid_link shows other iDs as "unauthenticated" (ORCID display guidelines). Once per iD and request."""
        from app.models import User

        if not orcid:
            return False  # an empty key names no account (orgs.display_name, 2026-10-19)
        known = g.setdefault("orcid_accounts", {})
        if orcid not in known:
            known[orcid] = db.session.scalar(db.select(User.id).filter(User.orcid == orcid, User.pending.is_(False))
                                             .limit(1)) is not None
        return known[orcid]

    app.jinja_env.globals["orcid_has_account"] = orcid_has_account

    def once(name):
        """True the first time a page asks for `name` in this request: script tags that several parts of a page
        need (Leaflet and the map scripts of every map on the results page) are written once (code review
        2026-10-07, F9)."""
        written = g.setdefault("written_once", set())
        if name in written:
            return False
        written.add(name)
        return True

    app.jinja_env.globals["once"] = once
    app.register_blueprint(samplesize.bp)
    app.register_blueprint(wizard.bp)
    app.register_blueprint(surveys.bp)
    app.register_blueprint(surveys.cron_bp)
    from app import data_access

    app.register_blueprint(data_access.bp)  # read tokens for R and Python (export page)
    from app import agent_access, mcp

    app.register_blueprint(agent_access.bp)  # tokens of the AI agent interface (page "Konto")
    app.register_blueprint(mcp.bp)  # the MCP endpoint for AI agents (experimental "agent")
    app.register_blueprint(editor.bp)
    app.register_blueprint(editor.share_bp)
    from app import quota_views  # its routes hang on the flow blueprint; the share links have their own

    app.register_blueprint(editor.flow_bp)
    app.register_blueprint(quota_views.share_bp)
    from app import boards

    app.register_blueprint(boards.bp)
    app.register_blueprint(boards.share_bp)
    from app import monitoring_feed

    app.register_blueprint(monitoring_feed.bp)  # counts of the last seven days for the operators' monitoring, by token
    app.register_blueprint(translations.bp)
    app.register_blueprint(media.bp)
    app.register_blueprint(participate.bp)
    app.register_blueprint(zenodo_auth.bp)
    from app import coscine

    app.register_blueprint(coscine.bp)  # experimental (app/experimental.py)
    app.jinja_env.globals.update(coscine_connected=lambda user: coscine.connection(user) is not None,
                                 coscine_handle=coscine.handle_url, coscine_auto_label=coscine.auto_label,
                                 coscine_space_warn=coscine.SPACE_WARN, coscine_day=coscine.day_of)
    from app import slack_actions

    app.register_blueprint(slack_actions.bp)
    from app import live

    app.register_blueprint(live.bp)
    app.register_blueprint(live.public_bp)
    from app import interview

    app.register_blueprint(interview.bp)
    from app import diary, diary_api

    app.register_blueprint(diary.bp)
    app.register_blueprint(diary_api.bp)
    from app import ai_chat

    app.register_blueprint(ai_chat.bp)  # the model access of a survey with the element "KI-Chat"
    ai_chat.keep_with_responses()
    from app import interview_ai

    app.register_blueprint(interview_ai.bp)
    from app import diary_ai

    app.register_blueprint(diary_ai.bp)  # the AI assistant of a diary survey (design, moment questions, check)
    from app import interview_data

    app.register_blueprint(interview_data.bp)
    from app import interview_results

    app.register_blueprint(interview_results.bp)
    from app import interview_audio

    app.register_blueprint(interview_audio.bp)
    from app import interview_transcribe

    app.register_blueprint(interview_transcribe.bp)
    from app import interview_search

    app.register_blueprint(interview_search.bp)
    from app import interview_summaries

    app.register_blueprint(interview_summaries.bp)
    from app import interview_remote

    app.register_blueprint(interview_remote.bp)
    from app import interview_screen  # the interviewee's own screen for tasks, experimental

    app.register_blueprint(interview_screen.bp)
    app.register_blueprint(interview_screen.public_bp)
    from app import interview_offline  # interviews without a network

    app.register_blueprint(interview_offline.bp)
    from app import interviewer

    app.register_blueprint(interviewer.bp)  # links for interviewers without an account
    from app import transcript_coding, transcript_coding_links, transcript_coding_view  # noqa: F401  coding transcripts, experimental; the links' and the view's routes join its blueprint

    app.register_blueprint(transcript_coding.bp)
    from app import ainstein

    app.register_blueprint(ainstein.bp)  # experimental (app/experimental.py)
    from app import learning

    app.register_blueprint(learning.bp)
    app.jinja_env.globals["learning"] = learning  # Lernhilfen: templates/_learn.html
    app.jinja_env.globals["learning_on"] = learning.on
    from app import apa

    from app.media import size as image_size

    from app.media import thumbnail_name

    app.jinja_env.globals["image_thumbnail"] = thumbnail_name
    app.jinja_env.globals["image_size"] = image_size  # width/height for <img>: no jumping while it loads
    app.jinja_env.globals["apa"] = apa  # sources in APA style: templates/_sources.html
    from app import quality

    app.jinja_env.globals["quality"] = quality  # its citations for the short references on "Qualitätssicherung"
    from app import display

    app.jinja_env.globals["display_attributes"] = display.attributes  # the look of these pages per person, base.html
    from app import brand as brand_mod

    app.jinja_env.globals["site"] = brand_mod.site  # the brand of the domain (data-site, start page; app/brand.py)
    brand_mod.install(app)  # "Askurious" written in a template: the name of the domain (M1, app/brand.py)
    app.jinja_env.globals["brand_status_url"] = brand_mod.status_url  # status.polltergeist.de under that brand
    from app.live import live_now

    app.jinja_env.globals["live_now"] = live_now  # status_badge: a live poll being presented "läuft" (W87)
    from app import lang

    # the language of these pages per person (German or English): de_en("Konto", "Account"), base.html's <html lang>
    app.jinja_env.globals.update(de_en=lang.de_en, de_en_n=lang.de_en_n, de_en_pick=lang.pick, ui_language=lang.current)
    app.jinja_env.globals["seal_problem"] = seal.problem_text  # a problem of the seal chain as a sentence (E10)
    from app import study_plan, study_templates  # the templates add their route to the plan's blueprint

    app.register_blueprint(study_plan.bp)
    from app import waves

    app.register_blueprint(waves.bp)  # waves of a longitudinal design: set on the plan's page, merged on the Export page
    from app import coding

    app.register_blueprint(coding.bp)  # categorising open answers: opened from the results page
    from app import paper

    app.register_blueprint(paper.bp)  # paper questionnaires: print, scan, take over (experimental)
    app.jinja_env.globals.update(paper_review_choices=paper.review_choices, paper_keep_choices=paper.keep_choices,
                                 paper_why=paper.why_label)
    app.jinja_env.globals["coding_check"] = coding.coding_check  # the second coder does not see the categories
    app.jinja_env.globals["coding"] = coding  # the button "Kategorisieren" and the categories on the results page
    from app import ai_access

    app.register_blueprint(ai_access.bp)  # the page "KI-Nutzung" with the own AI key
    ai_access.init_app(app)  # generative AI off: the pages that are AI as a whole say so
    app.jinja_env.globals["ai_access_shown"] = ai_access.shown  # "KI: eigener Zugang (…)" at the AI functions
    app.jinja_env.globals["ai_recipient"] = ai_access.recipient  # "… geht an OpenAI" or the own provider
    app.jinja_env.globals["ai_on"] = ai_access.ai_on  # the switch on "KI-Nutzung": off, no AI function shows (E4)
    from app import ai_fetch

    ai_fetch.init_app(app)  # AI calls of the page's script: limit dialog and errors at the button, no banner
    from app import demo_webapp

    app.register_blueprint(demo_webapp.bp)  # weather demo of an external section
    from app import handbook

    app.register_blueprint(handbook.bp)  # the handbook under /docs/, reached by the book in the header
    from app import plugins

    plugins.load(app)  # installed add-on modules (app/plugins.py): their pages, features and buttons
    # "Hilfe zu dieser Seite": the tab notes itself, the header links its section (UI/UX audit 2026-10-16, 17)
    app.jinja_env.globals.update(help_note=handbook.note_tab, help_url=handbook.help_url)
    app.jinja_env.globals["study_plan"] = study_plan  # the study plan: settings, wizard, editor, results
    from app import ddi

    app.jinja_env.globals["ddi"] = ddi  # what a data archive gets: the last step of the wizard
    from app import sample_model

    app.jinja_env.globals["sample_model"] = sample_model  # one sample model for every page (UI/UX audit 2026-10-07, S3)
    from app import item_source

    app.jinja_env.globals["item_source"] = item_source  # the source of a scale's items (user test 2026-10-27)
    from app import scale_default

    app.jinja_env.globals["scale_default"] = scale_default  # the standard of a survey's scales (user test 2026-10-27)
    from app import constructs as constructs_mod
    from app.wizard import DEMOGRAPHICS

    # names Askurious gives in both languages, for texts that name them (UI/UX audit 2026-10-07, W115)
    app.jinja_env.globals["seed_name"] = constructs_mod.seed_name
    app.jinja_env.globals["demographics_block"] = lambda: lang.pick(DEMOGRAPHICS)
    from app import sem_model

    app.jinja_env.globals["sem_model"] = sem_model  # structural equation models (experimental): results page
    from app import sem_versions

    app.jinja_env.globals["sem_versions"] = sem_versions  # versions of the model (model versions 2026-10-26)
    from app import panel_models

    app.jinja_env.globals["panel_models"] = panel_models  # models over the waves: power of the hypotheses over time
    from app import plan_model

    app.jinja_env.globals["plan_model"] = plan_model  # the drawing of the model in the study plan (experimental)
    app.jinja_env.globals["study_templates"] = study_templates.TEMPLATES
    from app import methods_text

    app.jinja_env.globals["methods_text"] = methods_text  # the paragraph on exclusions (Qualitätssicherung)

    from jinja2 import pass_context

    from app import units

    # numbers and dates on the researchers' and admins' pages follow their page language (E11: 6 Oct 2026, 1,234.5,
    # 12%); participant templates, the participants' blueprints and everything outside a request stay as they were
    # (units.page_lang). |round, |fixed and |pct stay machine numbers: they also fill CSS widths and hidden fields
    @pass_context
    def _localdt(ctx, dt, with_time=True):
        return timeutil.fmt(dt, with_time, units.page_lang(ctx.name))

    @pass_context
    def _localdate(ctx, dt):
        return timeutil.fmt(dt, False, units.page_lang(ctx.name))

    @pass_context
    def _endday(ctx, end):  # a field end: the last day it covers, midnight at its end being the day before (FUNC-06)
        return timeutil.fmt_local(timeutil.last_day(end), False, units.page_lang(ctx.name))

    @pass_context
    def _walldt(ctx, loc, with_time=True):  # a datetime already in local time (snapshot names), W113
        return timeutil.fmt_local(loc, with_time, units.page_lang(ctx.name))

    @pass_context
    def _stat_num(ctx, value, places=2, lang=None, signed=False):
        return units.stat(value, places, lang or units.page_lang(ctx.name), signed)

    @pass_context
    def _p_bare(ctx, p):
        return units.p_bare(p, units.page_lang(ctx.name))

    @pass_context
    def _dec(ctx, value, places=2, signed=False):
        return units.decimal_text(value, places, signed, units.page_lang(ctx.name))

    @pass_context
    def _filesize(ctx, value):
        """A file size with the page's decimal mark: 3,9 MB or 3.9 MB (Jinja's filesizeformat writes 3.9 MB on every
        page, UI audit 2026-10-07, W92); steps of 1000 as filesizeformat."""
        size, unit = float(value or 0), 0
        while size >= 1000 and unit < 4:
            size, unit = size / 1000, unit + 1
        if not unit:
            return f"{int(size)} Bytes"
        return f"{units.decimal_text(size, 1, False, units.page_lang(ctx.name))} {('kB', 'MB', 'GB', 'TB')[unit - 1]}"

    @pass_context
    def _thousands(ctx, value):
        return units.whole_text(value, units.page_lang(ctx.name))

    @pass_context
    def _percent(ctx, share):
        return units.percent_text(share, units.page_lang(ctx.name))

    @pass_context
    def _percent0(ctx, share):
        return units.percent_text(share, units.page_lang(ctx.name), places=0)

    @pass_context
    def _pctsign(ctx, value):
        pre, suf = units.affixes("percent", lang=units.page_lang(ctx.name))
        return f"{pre}{value}{suf}"

    def _after_colon(text):
        from app import lang

        return text if not text or lang.english() else text[:1].upper() + text[1:]

    @pass_context
    def _tick_labels(ctx, values, signed=False, most=3):
        return units.tick_labels(values, signed, most, units.page_lang(ctx.name))

    app.jinja_env.filters["localdt"] = _localdt
    app.jinja_env.filters["localdate"] = _localdate
    app.jinja_env.filters["endday"] = _endday
    # a stored (UTC) end for a date field: the local day, YYYY-MM-DD (app/account_types.py)
    app.jinja_env.filters["localdate_iso"] = lambda dt: timeutil.to_local(dt).date().isoformat() if dt else ""
    app.jinja_env.filters["walldt"] = _walldt
    app.jinja_env.filters["localclock"] = timeutil.clock
    app.jinja_env.filters["pct"] = units.percent
    from app.hypotheses import note_html, sentence_html
    app.jinja_env.filters["hyp_note"] = note_html  # notes of the hypotheses: only their subscripts as HTML (X1)
    app.jinja_env.filters["hyp_sentence"] = sentence_html  # their sentences: only i/sub/sup as HTML (C-01)
    # a share with its sign in the page language and one decimal: 12,5 % or 12.5% (results, dashboards, downloads)
    app.jinja_env.filters["percent"] = _percent
    app.jinja_env.filters["percent0"] = _percent0  # whole: progress and other shares of the operation, axis ticks
    # a number already in per cent (shown as it is) with the sign of the page language: 13 % or 13% (W111)
    app.jinja_env.filters["pctsign"] = _pctsign
    app.jinja_env.filters["dec"] = _dec  # fixed(places) with the page's decimal mark: 2,5 or 2.5
    app.jinja_env.filters["filesize"] = _filesize  # 3,9 MB or 3.9 MB
    app.jinja_env.filters["thousands"] = _thousands  # a whole number with the page's separators: 93.367 or 93,367
    # the first word after a colon in German starts with a capital ("Datenpool: Echte Teilnahmen"); English keeps
    # its lower case ("Data pool: real participations"), as British style does for a phrase (pre-test 2026-10-20, 12)
    app.jinja_env.filters["after_colon"] = _after_colon
    # numbers on every page round half up (kaufmännisch): Jinja's round rounds half to even (12.5 -> 12), and
    # "%.2f"|format does the same on the binary value; templates use |round and |fixed(places) instead
    app.jinja_env.filters["round"] = _round_filter
    app.jinja_env.filters["fixed"] = units.fixed
    app.jinja_env.globals["stat_num"] = _stat_num  # the num() of _macros.html: results page and researcher pages
    app.jinja_env.globals["p_places"] = units.p_places  # a p just below .05 does not read as .050
    app.jinja_env.globals["p_bare"] = _p_bare  # p in APA style, no leading zero, on every page (X61)
    from app import panel as panel_mod

    # links per person by name, "Anna Schmidt (f9jpxr)", for whom may see names (UI/UX audit 2026-10-09, X56)
    app.jinja_env.globals["link_value"] = panel_mod.shown_value
    app.jinja_env.globals["quota_cell_label"] = panel_mod.cap_cell_label
    from app import results as results_mod

    from app import flow as flow_mod

    app.jinja_env.globals["flow_parts"] = flow_mod.parts  # the single conditions of an element "Bedingung"
    app.jinja_env.globals["level_colour"] = results_mod.level_colour  # stacked levels on the diverging scale (V55, S11)
    app.jinja_env.globals["diverging_colour"] = results_mod.diverging_colour
    app.jinja_env.globals["sequential_colour"] = results_mod.sequential_colour  # maps and heat maps (S11)
    from app.items import federal_state as _states

    app.jinja_env.globals["state_outline"] = _states.outline  # the results map of federal states without tiles (V58)
    from markupsafe import Markup as _Markup

    app.jinja_env.globals["bkg_credit"] = _Markup("© " + _states.BKG)  # dl-de/by-2-0 under outline maps
    app.jinja_env.globals["round_ticks"] = results_mod.round_ticks  # round values on an axis (W64)
    from app import design as design_mod

    app.jinja_env.globals["looks_ordered"] = design_mod.looks_ordered  # ordered answers measured as nominal (W66)
    app.jinja_env.globals["bar_top"] = results_mod.bar_top  # frequency bars scaled to the largest share (V57)
    from app.items.base import plain as _plain
    app.jinja_env.filters["plain"] = _plain  # a text without its formatting marks, for lists (UI audit 2026-10-12, U29)
    app.jinja_env.filters["by_count"] = results_mod.by_count  # nominal frequencies: most frequent first, "keine Angabe" last
    app.jinja_env.globals["tick_labels"] = _tick_labels
    from jinja2 import pass_context

    from app import ui_i18n

    @pass_context
    def _ui(ctx, de, en, lang=None, **values):
        """ui("Weiter", "Next"): platform text in the language of the page (app/ui_i18n.py)."""
        return ui_i18n.tr(lang or ctx.get("lang"), de, en, **values)

    app.jinja_env.globals["ui"] = _ui

    @pass_context
    def _tool(ctx, de, en, **values):
        """tool("Pflicht prüfen", "Check required"): a text of the preview's own controls (bar, comments, device
        stage, untranslated marks). The researcher reads them in their own language whatever the survey's language
        (Ralf, 2026-10-07); a test person on the phone link has no account and reads the survey's language."""
        if ctx.get("on_phone"):
            return ui_i18n.tr(ctx.get("lang"), de, en, **values)
        return lang.de_en(de, en, **values)

    @pass_context
    def _tool_attrs(ctx):
        """ lang and dir for an element holding tool() texts: the researcher's language, left to right."""
        from markupsafe import Markup

        if ctx.get("on_phone"):
            return Markup("")
        return Markup(' lang="{}" dir="ltr"').format(lang.current())

    app.jinja_env.globals["tool"] = _tool
    app.jinja_env.globals["tool_attrs"] = _tool_attrs

    @pass_context
    def _text_attrs(ctx, item, key, *path):
        """ lang="de" dir="auto" on a text of an element that falls back to the default language (W45, S9); in the
        preview with "Unübersetzte markieren" also data-untranslated, which the preview marks as not translated."""
        from markupsafe import Markup

        lang = ctx.get("lang")
        if not lang or item is None or lang == item.block.survey.default_language:
            return Markup("")
        source = item.text_lang(key, lang, *path)
        if ui_i18n.html_lang(source) == ui_i18n.html_lang(lang):
            return Markup("")
        mark = ""
        if ctx.get("preview") and ctx.get("mark_untranslated"):
            mark = Markup(' data-untranslated="{}"').format(_tool(ctx, "noch nicht übersetzt", "not translated yet"))
        return Markup(' lang="{}" dir="auto"').format(ui_i18n.html_lang(source)) + mark

    app.jinja_env.globals["text_attrs"] = _text_attrs
    app.jinja_env.globals["ui_texts_js"] = ui_i18n.for_js
    app.jinja_env.globals["html_lang"] = ui_i18n.html_lang
    app.jinja_env.globals["html_dir"] = ui_i18n.html_dir
    from app import tempo

    app.jinja_env.globals["tempo"] = tempo
    from app import experimental

    app.jinja_env.globals["experimental"] = experimental.enabled  # experimental features (switched on by site admins)
    app.jinja_env.globals["experimental_on"] = experimental.active
    from app import vignettes

    app.jinja_env.globals["vignettes_of"] = vignettes.nodes  # the vignette loops of a survey
    from app import export as export_mod

    app.jinja_env.globals["speeder_rule_text"] = export_mod.page_rule_text  # "Relative Speed Index über 2,0" or the share
    app.jinja_env.globals["loop_tables_of"] = export_mod.loop_tables  # the loops with a table (Export page)
    app.jinja_env.globals["loop_long_of"] = export_mod.loop_long_nodes  # the loops with a long format of their own
    app.jinja_env.globals["loop_table_tsv"] = export_mod.loop_table_tsv  # its "Kopieren" button (Export page)
    from app import retention

    app.jinja_env.globals["retention"] = retention  # settings, Export page: the periods to pick from
    from app import pagetime

    app.jinja_env.globals["pagetime"] = pagetime
    from app import paradata

    app.jinja_env.globals["marker_days"] = __import__("app.participate", fromlist=["MARKER_DAYS"]).MARKER_DAYS
    app.jinja_env.globals["focus_source"] = paradata.DIEDENHOFEN  # settings: source of "Fensterwechsel und Einfügen"
    from app import quality

    app.jinja_env.globals["agent_sources"] = quality.AGENT_SOURCES  # page "Qualitätssicherung": bots and AI agents
    from app import geoip

    app.jinja_env.globals["geoip"] = geoip  # settings: sources, attribution and issue of the IP table
    from app.items import geolocation

    app.jinja_env.globals["geolocation_finest"] = geolocation.finest  # consent editor: what the location element stores
    from collections.abc import Mapping

    class _GeolocationStores(Mapping):  # what the location element stores, in the language of the page
        def __getitem__(self, key):
            return geolocation.STORES_EN[key] if lang.english() else geolocation.STORES[key]

        def __iter__(self):
            return iter(geolocation.STORES)

        def __len__(self):
            return len(geolocation.STORES)

    app.jinja_env.globals["geolocation_stores"] = _GeolocationStores()
    from app.surveys import LANGUAGE_NAMES

    app.jinja_env.globals["language_names"] = LANGUAGE_NAMES
    from app import duration

    app.jinja_env.globals.update(duration_label=duration.label, duration_label_html=duration.label_html, duration_short=duration.short, duration_estimate_short=duration.estimate_short,
                                 duration_minutes=duration.minutes, estimate_duration=duration.estimate)
    from app import constructs as constructs_mod

    app.jinja_env.globals["construct_badge"] = constructs_mod.badge
    app.jinja_env.globals["toolbox_key"] = constructs_mod.toolbox_key
    from app import edit_guard

    app.jinja_env.globals["edit_guard"] = edit_guard
    from app import theme

    app.jinja_env.globals.update(themes=theme, theme_css=theme.survey_css, theme_logo=theme.logo_for, theme_icon=theme.icon_for, theme_partners=theme.partners_for)
    from app.items import consent as consent_item

    app.jinja_env.globals["survey_sender"] = consent_item.sender

    @app.before_request
    def _auto_close():
        if app.config.get("LIFECYCLE_IN_REQUEST", True):
            lifecycle.maybe_close_due_surveys()

    app.before_request(terms.gate)
    from app import people_keys  # noqa: F401 - members and shares kept in step with their accounts (before_flush)
    from app import account_setup

    account_setup.init_app(app)  # the setup assistant after the first sign-in, after the terms (konto-einrichtung-konzept.md)
    app.jinja_env.globals["terms_pending"] = terms.needs_acceptance  # base.html: no areas in the header until then (X69)
    from app import accounts as accounts_mod

    app.jinja_env.globals["fresh_refill"] = accounts_mod.refill  # base.html: entries back after a fresh sign-in
    from app import errors

    errors.register(app)  # 403/404/500 in the frame of every page (UI/UX audit 2026-10-09, X68)
    from app import heavy

    heavy.register(app)  # the places for heavy evaluations go back at the end of every request (P9)
    from app import storage_room

    storage_room.register(app)

    def _origin(url):
        from urllib.parse import urlsplit

        parts = urlsplit(url or "")
        return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""

    # What a page may load: everything from this site; images also from the tile servers of the maps (any https
    # address: tile URLs carry sub-domains), requests from scripts also to the place search. No plugins, no <base>
    # to another site, no framing by others. Scripts only as files from this site: no inline script and no
    # inline handler (onclick=...) in any template (tests/audits/test_review2_reste.py), so a script that got into a
    # page does not run. Inline styles stay allowed (style="..." on many elements, the survey's theme).
    content_policy = "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "media-src 'self' blob:",  # a voice message plays back in the browser before it is sent (items/voice.py)
        "connect-src " + " ".join(dict.fromkeys(["'self'"] + [o for o in (_origin(app.config.get("MAP_SEARCH_URL")),) if o])),
        "object-src 'none'",
        "base-uri 'self'",
        # forms send to this site; "https:" because a sent page may redirect onward on purpose (completion links of
        # panels, external sections, ORCID). It keeps out javascript: and plain http (security audit 2026-10-04, H2)
        "form-action 'self' https:",
        "frame-ancestors 'self'",
    ])

    # The participants' pages (hosts.PARTICIPANT: survey, live vote, pretest, public dashboards …) load images only
    # from this site and the configured tile servers, not from any https address: markup that got into a survey
    # cannot send what a participant sees to another site through an image (bucket list 2026-10-18; E8 of the
    # participant address, decisions.md). Everything else as for the researchers.
    tiles = dict.fromkeys(t for t in (_tile_source(app.config.get(k)) for k in ("MAP_TILE_URL", "MAP_TILE_URL_DARK")) if t)
    participant_policy = content_policy.replace("img-src 'self' data: blob: https:",
                                                " ".join(["img-src 'self' data: blob:", *tiles]))
    # The researchers' pages (audit 2026-10-18, INJ-1): images likewise only from this site, the tile servers and the
    # status page of the brand (the dot in the footer); forms only to this site and where a sent form of theirs goes
    # on to: ORCID (sign-in, connecting an account), Zenodo (connecting an account) and the participants' address of the brand (a test run). Both
    # depend on the brand of the request (app/brand.py), so they are filled in per request (_researcher_policy).
    # style-src keeps 'unsafe-inline': several hundred style="" attributes in the templates and the survey's theme
    # set as inline styles; a nonce does not cover attributes, so this needs a rebuild of the templates first.
    researcher_policy = participant_policy.replace("form-action 'self' https:", "form-action 'self'{forms}").replace(
        "img-src 'self' data: blob:", "img-src 'self' data: blob:{images}", 1)

    def _researcher_policy():
        from app import brand, zenodo_auth

        images = [o for o in (_origin(brand.status_url()),) if o and o not in tiles]
        # "Mit Zenodo verbinden" is a POST form that redirects to Zenodo's sign-in: without its address here the
        # browser stops that redirect and the button does nothing (only targets this server has an application for)
        zenodo = [zenodo_auth.BASES[t] for t in zenodo_auth.BASES if zenodo_auth.available(t)]
        forms = [o for o in dict.fromkeys((_origin(app.config.get("ORCID_BASE_URL")), _origin(brand.public_base()),
                                           _origin(brand.participant_base_url()), *zenodo)) if o]
        return researcher_policy.format(images="".join(" " + o for o in images), forms="".join(" " + o for o in forms))

    from app import audit

    audit.register(app)  # the access log (Administration -> Protokoll)

    from app import cookies

    app.session_interface = cookies.SessionInterface()  # reads the old cookie name for a while (config)
    cookies.install_remember_key()  # the "stay signed in" cookie under RESEARCHER_SESSION_KEY, when set (CFG-04)

    @app.after_request
    def _security_headers(response):
        """No framing by other sites (the editor frames its own previews), no MIME sniffing, no full
        addresses to other sites, and https only once a browser has seen the site over https."""
        from app import hosts

        side = hosts.side_of(request.endpoint)
        # participants' pages (and the shared static files) keep "form-action 'self' https:": completion links of
        # panels and external sections send a participant on to any https address
        policy = participant_policy if side in (hosts.PARTICIPANT, hosts.SHARED) else _researcher_policy()
        if g.get("embeddable"):
            # a dashboard at its address for frames (boards.show_embedded): any site may frame this one page
            response.headers["Content-Security-Policy"] = policy.replace("frame-ancestors 'self'", "frame-ancestors *")
        else:
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            response.headers.setdefault("Content-Security-Policy", policy)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # no camera, microphone, position or payment for any page (none uses them), and no window of another
        # site that keeps a handle on this one (security audit 2026-10-04, H2)
        # except the microphone for a page that records and says so (g.microphone: interviews, items/voice.py) and the
        # position for a page with the location element (g.geolocation: items/geolocation.py), and a picture of the
        # page for a comment from a test run (g.display_capture: the preview with its comment box, comment_shot.js)
        allowed = PERMISSIONS
        if g.get("microphone"):
            allowed = allowed.replace("microphone=()", "microphone=(self)")
        if g.get("geolocation"):
            allowed = allowed.replace("geolocation=()", "geolocation=(self)")
        if g.get("display_capture"):
            allowed = allowed.replace("display-capture=()", "display-capture=(self)")
        response.headers.setdefault("Permissions-Policy", allowed)
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # pages and data for signed-in people (results, settings, one-time links, keys) are not kept by the browser
        # or a proxy: a shared computer shows nothing of them after signing out (security audit 2026-10-04, N7)
        from flask_login import current_user

        # downloads with send_file come with "no-cache" (kept, but asked again), which counts as unset here; static
        # files, images (/media) and the public map data (/geo) keep their own (security audit 2026-10-06, S15)
        cache = response.headers.get("Cache-Control")
        if ((cache is None or cache == "no-cache") and current_user.is_authenticated
                and request.endpoint != "static" and request.blueprint not in CACHEABLE_BLUEPRINTS):
            response.headers["Cache-Control"] = "no-store"
        # participant pages (the end page shows the deletion code), shared previews and live pages: nothing of them
        # stays in the browser's cache or history of a lab computer for the next person (S15)
        if request.blueprint in PARTICIPANT_BLUEPRINTS or request.endpoint in NO_STORE_ENDPOINTS:
            response.headers["Cache-Control"] = "no-store"
        if request.endpoint == "static" and response.cache_control.max_age == STATIC_MAX_AGE:
            # a versioned file (?v= its hash) never changes under its address: not even a reload asks again, as behind
            # nginx (static_cache_policy) also where the app serves the files itself (block-load concept, stage c)
            response.cache_control.immutable = True
        if request.is_secure:
            # includeSubDomains only with HSTS_SUBDOMAINS=1, as nginx (audit 2026-10-18, CFG-10; off until the DNS is checked)
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                                        if app.config.get("HSTS_SUBDOMAINS") else "max-age=31536000")
        return response

    @app.after_request
    def _navigate(response):
        """A POST of the scripts that go where the server sends them (app.js postAndGo, header X-Navigate): its
        redirect becomes {"location": ...}, so the target page is drawn once, for the browser, and not first for the
        script as well (code review 2026-10-07, F4). Cookies of the answer stay as they are."""
        if (request.headers.get("X-Navigate") == "1" and request.method == "POST"
                and response.status_code in (301, 302, 303, 307, 308) and response.location):
            import json as json_mod

            target = response.location
            del response.headers["Location"]
            response.status_code = 200
            response.set_data(json_mod.dumps({"location": target}))
            response.mimetype = "application/json"
            response.headers["X-Navigate"] = "1"
        return response

    @app.context_processor
    def _feedback():
        from flask_login import current_user

        from app.models import Feedback

        if not current_user.is_authenticated:
            return {}
        return {"feedback_severities": Feedback.SEVERITIES,
                "feedback_open_count": feedback.open_count() if current_user.is_admin else 0}

    with app.app_context():
        from app import models  # noqa: F401
        from app import orgs  # noqa: F401  (new surveys, groups, designs go into the home organisation)
        from app import migrations
        from app.extensions import postgres_settings, sqlite_settings, start_up_lock

        sqlite_settings(app)
        postgres_settings(app)
        # the schema: Alembic migrations, one server process at a time; a database from before them is compared
        # with the models and stamped (app/migrations.py)
        migrations.upgrade(app)
        from app.data_revision import validate

        with db.engine.connect() as connection:
            validate(connection)
        if app.config["SIDE"] == hosts.PARTICIPANT:
            # the data steps below are the researchers' container's (restart.sh starts it first): the participants'
            # side may run under a role that reads and writes only its own tables (app/participant_grants.py,
            # audit 2026-10-18, CFG-04), and accounts, organisations or the sign-in list are none of them
            return app
        with start_up_lock():  # PostgreSQL: one server process at a time (several start together after a deploy)
            from app import constructs

            constructs.ensure_seed()  # seed constructs and full-text index (data, not schema)
            from app import linked

            if app.config.get("DATABASE_SCHEMA_MODE", "auto") != "check":
                linked.ensure_index()  # runtime roles cannot create even an IF NOT EXISTS index
            from app import quotas

            quotas.upgrade_drafts()  # drafts with a "Quotenprüfung": calculation plus end "Quote voll" (data, not schema)
            from app.items import consent

            consent.upgrade_drafts()  # the former pool paragraph leaves the consent texts of drafts (data)
            from app import boards

            boards.settle_future()  # the old option "also future ones": its questions become fixed tiles (data)
            from app import experimental

            experimental.upgrade_legacy()  # the old single tick: one tick per experimental feature (data)
            from app import signin

            signin.adopt_config()  # ALLOWED_ORCIDS of the configuration into the administration, once (data)
            from app import account_types

            account_types.ensure_seed()  # the five user types, once (data; konzept-nutzertypen-2026-10-15.md)
            from app import orgs

            orgs.ensure_seed()  # the organisation everything started in, once (data; konzept-abteilungen-2026-10-15.md)
            account_types.ensure_per_org()  # each organisation its own types, "Mitarbeitende" at least (2026-10-16)
    return app


# the participants' side of a StaleDataError (participate._sent_twice: the page sent twice is dropped without a word):
# the participant pages, the live phones, the device of an interview, the diary app, shared previews, the preview
PARTICIPANT_BLUEPRINTS = ("participate", "live_public", "interview_screen_public", "diary_api", "preview_share")


def _participant_side():
    endpoint = request.endpoint or ""
    return request.blueprint in PARTICIPANT_BLUEPRINTS or endpoint.startswith("editor.preview")


def _stale(e):
    """StaleDataError: a participation (or another versioned row) was saved by another request while this one was at
    work, so nothing of this one is kept. On the participants' side a page sent twice (participate._sent_twice). On a
    researcher's page (a coding, a paper correction) the change is gone too, but the researcher learns it: a message,
    and the page to do it again; a request of a script gets 409 with the message (audit 2026-10-21, B7)."""
    from flask import flash, jsonify, redirect, url_for
    from urllib.parse import urlsplit

    from app import participate
    from app.extensions import db
    from app.lang import de_en

    if _participant_side():
        return participate._sent_twice(e)
    db.session.rollback()
    message = de_en("Die Daten wurden gleichzeitig an anderer Stelle geändert; Ihre Änderung wurde nicht gespeichert. "
                    "Bitte versuchen Sie es noch einmal.",
                    "The data was changed elsewhere at the same time; your change was not saved. Please try again.")
    rule = request.url_rule
    post_only = request.method != "GET" and rule is not None and "GET" not in (rule.methods or ())
    wants_json = request.is_json or request.accept_mimetypes.best == "application/json"
    if post_only and wants_json:
        return jsonify(ok=False, status="stale", error="stale", message=message), 409
    flash(message, "error")
    if not post_only:
        return redirect(request.url, code=303)
    back = urlsplit(request.referrer or "")
    if back.netloc == request.host and back.path.startswith("/") and not back.path.startswith(("//", "/\\")):
        return redirect(back.path + (f"?{back.query}" if back.query else ""), code=303)
    return redirect(url_for("dashboard.index"), code=303)
