import os
from datetime import timedelta
from urllib.parse import urlsplit

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _list(name):
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


def _bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DEV_SECRET_KEY = "dev-change-me"
# the files of one installation over the public code (instance_overlay/README.md)
INSTANCE_OVERLAY = os.environ.get("INSTANCE_OVERLAY", os.path.join(BASE_DIR, "instance_overlay"))


def _overlay_lines(name):
    """The lines of a file of the installation's overlay (# comments and empty lines left out); none without it."""
    try:
        with open(os.path.join(INSTANCE_OVERLAY, name), encoding="utf-8") as f:
            return [l.split("#")[0].strip() for l in f if l.split("#")[0].strip()]
    except OSError:
        return []


# Networks of the operator itself that freely chosen web-service and AI targets may never reach although they are
# public (its campus, its hospital): PROTECTED_NETS in the .env and the lines of instance_overlay/protected_nets.txt.
# Neither WEBSERVICE_BLOCKED_NETS nor any later setting can lift them (app/webservice.py, blocked_nets). The public
# code names none (Ralf 2026-10-19); the RWTH installation brings its allocations in its overlay.
PROTECTED_NETS = tuple(_list("PROTECTED_NETS") + _overlay_lines("protected_nets.txt"))


def database_url(url):
    """DATABASE_URL as SQLAlchemy needs it: postgres:// and postgresql:// (as hosts write them) use psycopg 3,
    SQLAlchemy's own default would be psycopg2. Anything else stays as it is; empty -> ""."""
    url = (url or "").strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def json_safe(value):
    """value with NaN and infinite numbers as None: JSON knows no such numbers. Python writes them anyway and
    SQLite stores them, PostgreSQL refuses the whole row."""
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def json_dumps(value):
    import json

    try:
        return json.dumps(value, allow_nan=False)
    except ValueError:  # NaN or infinity somewhere: rare, so only then walk through the value
        return json.dumps(json_safe(value))


def engine_options(url):
    """A server database closes idle connections now and then: test each one before use. SQLite needs nothing."""
    if url.startswith("sqlite"):
        return {}
    options = {"pool_pre_ping": True, "pool_recycle": 1800, "json_serializer": json_dumps}
    # connections per server process: SQLAlchemy keeps 5 and opens up to 10 more when busy (15). DB_POOL_SIZE and
    # DB_MAX_OVERFLOW lower that when a further brand's containers share the database server and the processes of
    # all together would pass its max_connections (datenbank-je-marke-konzept.md). Empty: SQLAlchemy's own numbers.
    for name, key in (("DB_POOL_SIZE", "pool_size"), ("DB_MAX_OVERFLOW", "max_overflow")):
        value = os.environ.get(name, "").strip()
        if value.isdigit():
            options[key] = int(value)
    return options


def url_host(url):
    """The host name of an address ("http://localhost:5060" -> "localhost", "//[::1]:5060" -> "::1"), or ""."""
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def is_local_host(host):
    """This computer's own names, and the endings reserved for development (.localhost, .test). Only under such a
    name may the app run with the development SECRET_KEY or with DEV_LOGIN (create_app, auth.dev_login)."""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith((".localhost", ".test"))


class Config:
    # Explicit operator-checked private instrument grants; empty/malformed means no new restricted-wording use.
    # JSON list entries: key, survey_uid, orcid, languages (list), evidence (internal document reference).
    INSTRUMENT_WORDING_PERMISSIONS = os.environ.get("INSTRUMENT_WORDING_PERMISSIONS", "[]")
    # The default is for local development only; create_app refuses to start with it unless PUBLIC_BASE_URL is local.
    # Set it once and keep it: a new key signs everyone out, makes the deletion codes participants noted down
    # worthless without notice (app/erasure.py derives them from it), and breaks the return links of an external
    # section that participants have not used yet (app/external.py). Stored OAuth tokens also need it unless
    # TOKEN_KEY is set.
    SECRET_KEY = os.environ.get("SECRET_KEY") or DEV_SECRET_KEY  # also for an empty line SECRET_KEY= in .env
    # The keys of before while SECRET_KEY is changed (comma-separated, newest first; BETRIEB.md, "Schlüssel wechseln"):
    # sessions signed with them stay valid, and secrets stored under a key derived from them stay readable
    # (app/cookies.py, app/secrets_box.py; audit 2026-10-13, KR-6). Empty: only SECRET_KEY.
    SECRET_KEY_FALLBACKS = _list("SECRET_KEY_FALLBACKS")
    # SQLite by default. PostgreSQL works as well (DATABASE_URL=postgresql://user:password@host/name, driver psycopg
    # from requirements-postgres.txt); the page Administration > Datenbank checks a server and copies the data over.
    SQLALCHEMY_DATABASE_URI = database_url(os.environ.get("DATABASE_URL")) or         "sqlite:///" + os.path.join(BASE_DIR, "instance", "app.db")
    SQLALCHEMY_ENGINE_OPTIONS = engine_options(SQLALCHEMY_DATABASE_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # DP-15: opt-in runtime role; schema changes belong to the deployment job.
    DATABASE_SCHEMA_MODE = os.environ.get("DATABASE_SCHEMA_MODE", "auto").strip().lower()
    # SQLite (app/extensions.py, sqlite_settings): with the write-ahead log, readers and the one writer do not
    # block each other, and a commit does not wait for the disk twice; a writer that finds the database busy
    # waits this many seconds before it gives up. WAL needs a local file system: on a network drive set
    # SQLITE_JOURNAL=DELETE in .env. WAL keeps two files next to the database (app.db-wal, app.db-shm); a
    # backup takes all three, or uses "sqlite3 app.db .backup".
    SQLITE_JOURNAL = os.environ.get("SQLITE_JOURNAL", "WAL").strip().upper()
    SQLITE_BUSY_SECONDS = int(os.environ.get("SQLITE_BUSY_SECONDS", "20"))
    # How long a server process keeps the counts for balanced assignment and the answers for the live comparison
    # before it reads them again (app/balance.py); 0 = read on every request
    BALANCE_SECONDS = int(os.environ.get("BALANCE_SECONDS", "15"))
    # How long a server process keeps that an account may sign in, for the check on every request (auth.load_user);
    # a "no" is never kept, and taking a right away forgets the kept yes of the process that did it (auth.py), so a
    # removed iD is out at once there and after at most this long in the others. 0 = asked on every request
    # (10 until the audit 2026-10-18, AUTH-3)
    SIGN_IN_SECONDS = int(os.environ.get("SIGN_IN_SECONDS", "5"))
    # Heavy evaluations (data exports, method section, results computed anew; app/heavy.py): at most this many at
    # once across all web/child processes of a container, the next synchronous request waits up to HEAVY_WAIT
    # seconds, then "please try again" (503); a new background report asks for a place without waiting.
    # 0 = no limit
    HEAVY_PARALLEL = int(os.environ.get("HEAVY_PARALLEL", "2"))
    REPORT_JOB_THRESHOLD = int(os.environ.get("REPORT_JOB_THRESHOLD", "5000"))  # 0 disables separate report processes
    REPORT_JOB_WAIT = int(os.environ.get("REPORT_JOB_WAIT", "10"))  # synchronous API wait, capped at 30 seconds
    # below the threshold, in a process that also serves participants: a report of participations x elements from this
    # on (about a second of CPU) runs in a process of its own too; 0 switches the estimate off (app/report_jobs.py)
    REPORT_JOB_COST = int(os.environ.get("REPORT_JOB_COST", "50000"))
    # the same in the researchers' container (SIDE=researcher), from this many participations x elements on: a long
    # computation in a thread of a gunicorn worker held the others of that worker back (GIL; audit 2026-10-22, G-06:
    # 29 s instead of 4 s next to 8 busy threads). Higher than for the participants' side, because a process of its own
    # starts cold; 0 switches it off
    REPORT_JOB_COST_RESEARCHER = int(os.environ.get("REPORT_JOB_COST_RESEARCHER", "300000"))
    # the address space of a report process at most this many MB (Linux; 0 = no limit, app/report_jobs.limit_memory).
    # 1200 by default since the audit of 2026-10-22 (G-02): about 30 KB per participation, so some 30 000; two of them
    # and the workers stay below the 3 GB of the researchers' container (a worker there: some 450 MB address space)
    REPORT_JOB_MEMORY_MB = int(os.environ.get("REPORT_JOB_MEMORY_MB", "1200"))
    REPORT_API_WAITERS = int(os.environ.get("REPORT_API_WAITERS", "4"))  # shared HTTP waiters, one per token
    HEAVY_WAIT = int(os.environ.get("HEAVY_WAIT", "30"))
    # Minutes a quota place stays reserved for a participation that has not sent a page since the quota calculation
    # (app/quotas.py, audit 2026-10-18, BIZ-06); after a page the quota's own hold time applies.
    QUOTA_FRESH_HOLD_MINUTES = int(os.environ.get("QUOTA_FRESH_HOLD_MINUTES", "5"))
    # Minutes after which a participation that sent only its first page with a once-only URL parameter (a panel ID)
    # no longer turns the next person with that ID away as a duplicate; the ID goes to the new one (app/panel.py,
    # audit 2026-10-18, BIZ-05).
    PANEL_ONCE_TAKEOVER_MINUTES = int(os.environ.get("PANEL_ONCE_TAKEOVER_MINUTES", "10"))
    # Seconds a design search in the editor may take (CBC design, experimental plan of vignettes; code review
    # 2026-10-15, B1/B2): then the best design so far is kept and the editor says the search was cut short. 0 = no limit
    DESIGN_SECONDS = float(os.environ.get("DESIGN_SECONDS", "20"))
    # The values of quality control kept per server process (app/quality.py, request_values), about 1 to 2 KB each
    QC_VALUES_KEPT = int(os.environ.get("QC_VALUES_KEPT", "50000"))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "instance", "uploads")
    # seconds a request waits for the backup window (app/storage_guard.py) before it gets 503 with Retry-After
    # (audit 2026-10-15, NB-4); below nginx's 60 s, so a worker thread is not held for the whole window
    STORAGE_GATE_WAIT = float(os.environ.get("STORAGE_GATE_WAIT", "10"))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    # Flask 3.1 also limits each text field of a form to 500 KB; the researcher pages may send long texts up to the
    # limit above (the participant pages have their own, smaller one)
    MAX_FORM_MEMORY_SIZE = MAX_CONTENT_LENGTH
    # A page of a questionnaire is far smaller than an upload: the largest answer (drawn lines on an image) stays
    # under 1 MB. Applies to the participant pages and the preview over the phone link (app/__init__.py).
    PARTICIPANT_FORM_BYTES = int(os.environ.get("PARTICIPANT_FORM_BYTES", str(2 * 1024 * 1024)))
    # A survey read in from a file (app/transfer.py) brings its images along as text: this page alone takes more
    # than an upload of one image. A reverse proxy in front has its own limit (nginx: client_max_body_size).
    IMPORT_MAX_BYTES = int(os.environ.get("IMPORT_MAX_BYTES", str(40 * 1024 * 1024)))
    EXPORT_JSON_MAX_BYTES = int(os.environ.get("EXPORT_JSON_MAX_BYTES", str(32 * 1024 * 1024)))
    EXPORT_ZIP_MAX_BYTES = int(os.environ.get("EXPORT_ZIP_MAX_BYTES", str(5 * 1024 ** 3)))

    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5060").rstrip("/")
    # Own address for participants (app/hosts.py), e.g. https://ask.askurious.de: participants' links, QR codes and
    # return links lead there, and the participants' paths on PUBLIC_BASE_URL move there. Empty: one address for all.
    PARTICIPANT_BASE_URL = os.environ.get("PARTICIPANT_BASE_URL", "").strip().rstrip("/")
    # Diary studies on the main address (app/hosts.py, diary_on_main): the app's API, the join link of the QR code, the
    # entry addresses and the questionnaire pages of a diary study are answered by the researchers' containers and not
    # sent on to PARTICIPANT_BASE_URL. On while the participants' containers and their nginx paths do not carry the
    # diary code yet (2026-10-12: only the researchers' side could be updated); 0 once they do.
    DIARY_ON_MAIN_ADDRESS = _bool("DIARY_ON_MAIN_ADDRESS", True)
    # the Android app of the diary studies (app/diary_api.py, askurious-android): the SHA-256 fingerprints of its
    # signing keys (comma-separated, AA:BB:...), for /.well-known/assetlinks.json, so that the join link opens the app;
    # empty = the link opens the join page in the browser. DIARY_APP_URL: where the join page sends people for the app
    DIARY_APP_FINGERPRINTS = [f.strip() for f in os.environ.get("DIARY_APP_FINGERPRINTS", "").split(",") if f.strip()]
    DIARY_APP_URL = os.environ.get("DIARY_APP_URL", "https://play.google.com/store/apps/details?id=de.askurious.app")
    # The oldest version of the app (its versionCode) that diary studies still accept: sent as "min_app_version" with
    # the study (diary.study_json), and an older app asks the person to update it. 0 = every version.
    DIARY_MIN_APP_VERSION = int(os.environ.get("DIARY_MIN_APP_VERSION", "0") or 0)
    # Further domains with a name of their own (app/brand.py), e.g. "polltergeist.de=Polltergeist": a request to the domain
    # or one of its sub-domains shows that name instead of "Askurious", and the links stay on that domain. Empty: one name.
    BRANDS = os.environ.get("BRANDS", "").strip()
    # This installation as a whole is one further brand, with its own database (app/brand.py), e.g.
    # "polltergeist.de=POLLtergeist" with PUBLIC_BASE_URL https://polltergeist.de: its name also in mails, housekeeping
    # and estimations. Empty: the main name (datenbank-je-marke-konzept.md, prepared 2026-10-09, not in use yet).
    SITE_BRAND = os.environ.get("SITE_BRAND", "").strip()
    # One process per side (deploy/docker): "participant" or "researcher" answers only that side's paths. Empty: the
    # name a request came to decides (with PARTICIPANT_BASE_URL), or one address answers everything.
    # Docker runs cleanup in a single dedicated process; local/Plesk installations retain request cleanup.
    LIFECYCLE_IN_REQUEST = os.environ.get("LIFECYCLE_IN_REQUEST", "1") == "1"
    MEDIA_X_ACCEL = os.environ.get("MEDIA_X_ACCEL", "0") == "1"
    SIDE = os.environ.get("SIDE", "").strip().lower()
    # Until this day (YYYY-MM-DD) a participation under way on the old address goes along to the new one; empty: disabled
    PARTICIPANT_HANDOVER_UNTIL = os.environ.get("PARTICIPANT_HANDOVER_UNTIL", "").strip()
    # the status page of this installation (deploy/docker: https://status.askurious.de), linked in the footer; empty: no link
    STATUS_URL = os.environ.get("STATUS_URL", "").strip()
    # the load of the containers, written each minute by deploy/docker/container-stats.sh (Administration > Server,
    # app/containers.py); empty: the section shows only the load of this machine
    CONTAINER_STATS = os.environ.get("CONTAINER_STATS", "").strip()
    # only these services of that file, comma-separated (a further brand's installation: its own and the shared ones);
    # empty: all
    CONTAINER_SERVICES = os.environ.get("CONTAINER_SERVICES", "").strip()
    # days the daily dumps of the database are kept (deploy/docker/backup.sh: 14); named on the privacy page with the
    # dumps before deploys (audit 2026-10-10, L-12). 0: no daily dumps, the page names the copy before updates only
    BACKUP_DAYS = int(os.environ.get("BACKUP_DAYS", "0") or 0)
    # Number of proxies in front of the app (Plesk with nginx in front of the app: 1; no proxy: leave the line out).
    # Too large a number lets participants choose their own address (IP region, limits per sender); verify it on
    # Administration -> IP-Region with a made-up X-Forwarded-For header (the page says how).
    PROXY_COUNT = int(os.environ.get("PROXY_COUNT", "0"))
    # 1: HSTS with includeSubDomains where the app sends it itself (Plesk; behind nginx in Docker nginx sends it, with
    # the same setting in its environment): every sub-domain of the address then only over https, for a year in every
    # browser that saw it (audit 2026-10-18, CFG-10). Off by default until the DNS zones are checked (BETRIEB.md). No preload
    HSTS_SUBDOMAINS = _bool("HSTS_SUBDOMAINS", False)
    # old host names that answer with a permanent redirect to PUBLIC_BASE_URL (same path and query)
    REDIRECT_HOSTS = [h.lower() for h in _list("REDIRECT_HOSTS")]
    # Networks a web service in the flow (app/webservice.py) must not call although they are public, comma-separated,
    # on top of the operator's PROTECTED_NETS (above), which stay blocked whatever this says.
    WEBSERVICE_BLOCKED_NETS = list(PROTECTED_NETS) + _list("WEBSERVICE_BLOCKED_NETS")
    # New surveys start with a block "Demographische Angaben" (gender and age from the construct toolbox)
    NEW_SURVEY_DEMOGRAPHICS = True
    TRANSLATION_CHECK = True  # before the start: every text translated into every language (app/translations.py)
    # Results that have to be computed first: the page opens at once with a progress bar and then asks for them
    # (app/surveys/, results). Off: the request itself computes and answers with the results (the tests).
    RESULTS_WAIT_PAGE = True
    # "under construction": the yellow and black tape under the header of the landing and researcher pages (never
    # on participant pages), for a development server: UNDER_CONSTRUCTION=1 in its .env. Off (the standard since
    # 2026-10-07, Ralf) the band under the header has the accent colour of the look; no tape markup or CSS is sent
    UNDER_CONSTRUCTION = _bool("UNDER_CONSTRUCTION", False)
    # Donations (page "Preise und Spenden"): Wero payment link (created in the banking app; preferred) and PayPal. Both
    # empty (the standard of the public code; the operator's links go into its .env, Ralf 2026-10-19): no such page
    # and no gift in the header
    DONATION_WERO_URL = os.environ.get("DONATION_WERO_URL", "")
    DONATION_URL = os.environ.get("DONATION_URL", "")
    # Imprint (Impressum): shown on /impressum once an address is set; lines of the address separated by "|"; also
    # under "Datenschutz: Verantwortlich". No default: the operator's contact goes into its .env, its legal entity and
    # texts into instance_overlay/ (Ralf 2026-10-19; the RWTH installation: askurious-instance-rwth)
    IMPRINT_NAME = os.environ.get("IMPRINT_NAME", "")
    IMPRINT_ADDRESS = [l.strip() for l in os.environ.get("IMPRINT_ADDRESS", "").split("|") if l.strip()]
    IMPRINT_EMAIL = os.environ.get("IMPRINT_EMAIL", "")
    # The files of one installation over the public code (instance_overlay/README.md): a template in its templates/
    # takes the place of the template of the same name in app/templates/, such as the imprint and privacy texts of the
    # operator. Empty or missing folder: the code's own templates only.
    INSTANCE_OVERLAY = INSTANCE_OVERLAY
    # The wording of scales of others (GESIS/ZIS, journal articles), which the public code does not carry
    # (app/constructs.py, INSTRUMENTS; instance_overlay/README.md). Without the file those constructs are left out.
    INSTRUMENT_WORDINGS = os.environ.get("INSTRUMENT_WORDINGS") or os.path.join(INSTANCE_OVERLAY, "instruments.json")
    SESSION_COOKIE_SECURE = PUBLIC_BASE_URL.startswith("https://")
    SESSION_COOKIE_SAMESITE = "Lax"
    # Behind https the cookies carry the prefix "__Host-": the browser then takes them only from this very host
    # (Secure, Path=/, no Domain), so a page on another sub-domain of the same domain cannot slip in a cookie of its
    # own, such as a session signed in as someone else (security audit 2026-10-04, N4). The old name "session" is
    # never read behind https (the transition ended on 2026-10-12, audit 2026-10-11 AU8; app/cookies.py).
    SESSION_COOKIE_NAME = "__Host-session" if SESSION_COOKIE_SECURE else "session"
    # The participants' address signs its session cookie with a salt of its own (app/cookies.py, code review
    # 2026-10-08 H1). Until this day (YYYY-MM-DD) it still reads a cookie signed the way before, so participants
    # under way there when the change went live go on; empty: not at all.
    PARTICIPANT_OLD_COOKIE_UNTIL = os.environ.get("PARTICIPANT_OLD_COOKIE_UNTIL", "2026-10-18").strip()
    # "Angemeldet bleiben" (Flask-Login): same protection as the session cookie, and a month instead of a year
    REMEMBER_COOKIE_NAME = "__Host-remember" if SESSION_COOKIE_SECURE else "remember_token"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    # however often it is used, a sign-in lasts at most this long; then sign in again (audit 2026-10-09, A5)
    SESSION_MAX_AGE = timedelta(days=int(os.environ.get("SESSION_MAX_DAYS", "30") or 30))
    # Participants may pause and come back within a week (signed cookie with the response id only)
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    # Forms of participants stay valid as long as the session (no expiry while someone thinks about a page, a pause of
    # days included); the signed-in researchers' forms for CSRF_HOURS (app/__init__.py, _researcher_csrf_age), and
    # their open pages fetch a fresh token in time (app.js, /auth/csrf-token; audit 2026-10-18, INJ-4)
    WTF_CSRF_TIME_LIMIT = None
    CSRF_HOURS = float(os.environ.get("CSRF_HOURS", "12") or 12)

    # Second factor (code from an authenticator app, app/twofactor.py): every account may set it up, and once set up
    # a password sign-in asks for it; site admins signing in with a password always give a code, with ORCID none
    # (Ralf, 2026-10-13). A code given counts for ADMIN_TOTP_HOURS in this browser. ADMIN_TOTP no longer changes any
    # of that (#984); it is kept so an existing .env stays valid (Abgleich 2026-10-15, T01-N4).
    ADMIN_TOTP = _bool("ADMIN_TOTP", False)
    ADMIN_TOTP_HOURS = int(os.environ.get("ADMIN_TOTP_HOURS", "12"))

    # DEV_LOGIN=1 allows password-less login (local development only!)
    DEV_LOGIN = _bool("DEV_LOGIN", False)
    # a development server in a container or a VM sees the host's bridge address for a browser on the same computer:
    # 1 lets private addresses count as this computer for the development key and DEV_LOGIN (app.is_local_request).
    # Without a proxy and under a local PUBLIC_BASE_URL only (create_app refuses it otherwise; audit 2026-10-18, CFG-01)
    DEV_TRUST_BRIDGE = _bool("DEV_TRUST_BRIDGE", False)

    # ORCID sign-in (Public API client from https://orcid.org/developer-tools)
    # the institute's own Whisper server for transcribing interviews (experimental, app/interview_transcribe.py):
    # base address, optional token (Bearer), model name, path of the OpenAI-compatible interface, seconds to wait
    WHISPER_URL = os.environ.get("WHISPER_URL", "").strip()
    WHISPER_TOKEN = os.environ.get("WHISPER_TOKEN", "").strip()
    WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1").strip()
    WHISPER_PATH = os.environ.get("WHISPER_PATH", "/v1/audio/transcriptions").strip()
    WHISPER_TIMEOUT = int(os.environ.get("WHISPER_TIMEOUT", "3600"))
    # the token of the status page's JSON (/status/<token>.json, app/status_node.py), at least 24 characters, else that
    # address does not exist; the administration shows the link (app/monitoring_feed.py). The token of the monitoring
    # feed itself is made under Administration > Server, not here.
    STATUS_JSON_TOKEN = os.environ.get("STATUS_JSON_TOKEN", "").strip()
    # Opt-in durable job protocol of deploy/docker/whisper; existing Whisper servers keep their interface.
    WHISPER_JOBS = _bool("WHISPER_JOBS", False)
    WHISPER_ALIGN = _bool("WHISPER_ALIGN", True)
    WHISPER_DIARIZE = _bool("WHISPER_DIARIZE", True)
    WHISPER_CANCEL_URL = os.environ.get("WHISPER_CANCEL_URL", "").strip()
    ORCID_CLIENT_ID = os.environ.get("ORCID_CLIENT_ID", "")
    ORCID_CLIENT_SECRET = os.environ.get("ORCID_CLIENT_SECRET", "")
    ORCID_BASE_URL = os.environ.get("ORCID_BASE_URL", "https://orcid.org").rstrip("/")
    # Admins may always sign in. Who else may is kept in the database and changed in Administration > Forschende
    # (app/signin.py); ALLOWED_ORCIDS ("*" = every ORCID holder) is only taken over from here once and then not read
    ADMIN_ORCIDS = _list("ADMIN_ORCIDS")
    ALLOWED_ORCIDS = _list("ALLOWED_ORCIDS")
    # a new account (first ORCID sign-in, or made by a site admin) may create surveys of its own; a site admin can
    # take that back on its page. 0 = only the whitelist, the tick of an admin and groups decide (as before 2026-10-05)
    NEW_ACCOUNTS_MAY_CREATE = _bool("NEW_ACCOUNTS_MAY_CREATE", True)
    # the setup assistant once after the first sign-in of a new account (app/account_setup.py); 0 = new accounts go
    # straight to their page, as before 2026-10-18
    ACCOUNT_SETUP = _bool("ACCOUNT_SETUP", True)

    # OpenAI
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_TEXT_MODEL = os.environ.get("OPENAI_TEXT_MODEL", "gpt-6-luna")
    OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")
    # prices of the AI models for the cost overview (app/ai_stats.py); empty = ai_prices.json in the instance folder
    AI_PRICES_FILE = os.environ.get("AI_PRICES_FILE", "")

    # Map item (items/map_marker.py): tile server and place search. Defaults: OpenStreetMap tiles and Nominatim
    # (usage policies: attribution, moderate use, search only on submit and at most 1 request/s).
    # MAP_SEARCH_URL empty = no search box on any map.
    MAP_TILE_URL = os.environ.get("MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
    MAP_TILE_ATTRIBUTION = os.environ.get(
        "MAP_TILE_ATTRIBUTION",
        '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>')
    MAP_SEARCH_URL = os.environ.get("MAP_SEARCH_URL", "https://nominatim.openstreetmap.org/search")
    # Optional tile server with dark maps, for surveys with a dark look and "Karten: dunkel" (app/theme.py). Empty:
    # the usual tiles are darkened in the browser, so no further service is called.
    MAP_TILE_URL_DARK = os.environ.get("MAP_TILE_URL_DARK", "")
    MAP_TILE_ATTRIBUTION_DARK = os.environ.get("MAP_TILE_ATTRIBUTION_DARK", "")

    # Limits against automated abuse (app/limits.py); 0 = no limit. A sender is its address; an IPv6 address counts
    # by its /64 (one connection has that many addresses). New participations, live votes and complaints count in
    # the database for all server processes together (table rate_count; audit 2026-10-18, BIZ-01)
    START_LIMIT = int(os.environ.get("START_LIMIT", "300"))        # new participations per sender ...
    START_WINDOW = int(os.environ.get("START_WINDOW", "600"))      # ... within this many seconds
    # new participations per sender in one survey: narrower, so one sender cannot fill a survey (in the database,
    # like START_LIMIT); a school class behind one address stays below it
    SURVEY_SENDER_LIMIT = int(os.environ.get("SURVEY_SENDER_LIMIT", "30"))
    SURVEY_SENDER_WINDOW = int(os.environ.get("SURVEY_SENDER_WINDOW", "600"))
    # new participations per survey from all senders together, counted by the database (so for all server
    # processes), far above what a survey gets (3000 in 10 minutes are 5 per second). Reaching it turns nobody away
    # (a flood from many senders would lock real participants out): the tab "Datenerhebung" shows a note and Slack
    # hears it, once per survey and window (audit 2026-10-06, S5)
    SURVEY_START_LIMIT = int(os.environ.get("SURVEY_START_LIMIT", "3000"))
    SURVEY_START_WINDOW = int(os.environ.get("SURVEY_START_WINDOW", "600"))
    # requests for a dashboard link (/d/<token>) per sender; wide, since a lecture hall often shares one address
    BOARD_LIMIT = int(os.environ.get("BOARD_LIMIT", "20000"))
    BOARD_WINDOW = int(os.environ.get("BOARD_WINDOW", "600"))
    # wrong codes per sender and window on every public address with a survey code (/s/<code> and its pages, the code
    # page /live/ and /live/<code>, the codes of diary studies): against trying codes out; beyond it these addresses
    # answer 429 for a while. Counted in the database for all server processes since the security audit 2026-10-13
    # (TN-2; before up to 24 times 30 in memory), so wider than before: a lecture hall behind one address mistypes too
    CODE_LIMIT = int(os.environ.get("CODE_LIMIT", "100"))
    CODE_WINDOW = int(os.environ.get("CODE_WINDOW", "600"))
    # ... and per IPv6 /48 in the same window: a hoster's /48 holds 65 536 senders of their own (audit 2026-10-18,
    # BIZ-07); IPv4 is not added up
    CODE_WIDE_LIMIT = int(os.environ.get("CODE_WIDE_LIMIT", "1000"))
    # the limits per sender against load (starts, starts per survey, live votes, complaints) count an IPv6 sender per
    # /64 and its /48 as well, there this many times the limit: a hoster's /48 is 65 536 senders of their own, a
    # campus behind one /48 stays below it (audit 2026-10-22, D-01); 0 = per /64 only
    SENDER_WIDE_FACTOR = int(os.environ.get("SENDER_WIDE_FACTOR", "20"))
    # wrong test and invitation codes of one diary study from all senders together; beyond it these codes of the
    # study wait until the window moves on (audit 2026-10-18, BIZ-07)
    STUDY_CODE_LIMIT = int(os.environ.get("STUDY_CODE_LIMIT", "300"))
    STUDY_CODE_WINDOW = int(os.environ.get("STUDY_CODE_WINDOW", "3600"))
    # Dashboards (app/boards/): how often a server process looks whether a dashboard has to be computed anew,
    # and how often an open dashboard asks for a new state (the lecture hall view more often); seconds
    BOARD_CHECK_SECONDS = float(os.environ.get("BOARD_CHECK_SECONDS", "3"))
    # how old the state behind a link is at least before it is made anew: many open links, one computation
    BOARD_PUBLIC_SECONDS = float(os.environ.get("BOARD_PUBLIC_SECONDS", "30"))
    # how far back the moment of a new step of the dashboard links lies (boards.step_cutoff): a completion that is
    # stamped but not yet stored when the step is made must not slip into it later
    BOARD_STEP_MARGIN = float(os.environ.get("BOARD_STEP_MARGIN", "60"))
    BOARD_POLL_SECONDS = int(os.environ.get("BOARD_POLL_SECONDS", "15"))
    BOARD_HALL_POLL_SECONDS = int(os.environ.get("BOARD_HALL_POLL_SECONDS", "5"))
    # Live votes (app/live/, phones at /live/<code>): a phone is known by its cookie only, so a script without one
    # is a new phone every time. Per sender and LIVE_WINDOW seconds (in the database, like START_LIMIT; wide, since a whole
    # lecture hall often shares one address): new participations, questions from the audience, votes on them and
    # requests with reactions; per sender and run in the same window: new participations and audience questions
    # (LIVE_RUN_SENDER_*; device ids new to the run count against twice LIVE_RUN_SENDER_JOINS, audit 2026-10-15,
    # NB-7). Per run (counted by the database, all processes): LIVE_RUN_PARTICIPATIONS and
    # LIVE_RUN_QUESTIONS turn nobody away; reaching them shows a note on "Präsentieren" and tells Slack, once per run
    # and window (audit 2026-10-06, S5). More than LIVE_QA_FLOOD audience questions within a minute wait for the
    # presenter.
    LIVE_WINDOW = int(os.environ.get("LIVE_WINDOW", "600"))
    LIVE_JOIN_LIMIT = int(os.environ.get("LIVE_JOIN_LIMIT", "600"))
    LIVE_QA_LIMIT = int(os.environ.get("LIVE_QA_LIMIT", "60"))
    LIVE_VOTE_LIMIT = int(os.environ.get("LIVE_VOTE_LIMIT", "600"))
    LIVE_REACT_LIMIT = int(os.environ.get("LIVE_REACT_LIMIT", "1200"))
    LIVE_RUN_PARTICIPATIONS = int(os.environ.get("LIVE_RUN_PARTICIPATIONS", "5000"))
    LIVE_RUN_QUESTIONS = int(os.environ.get("LIVE_RUN_QUESTIONS", "500"))
    LIVE_RUN_SENDER_JOINS = int(os.environ.get("LIVE_RUN_SENDER_JOINS", "300"))
    LIVE_RUN_SENDER_QUESTIONS = int(os.environ.get("LIVE_RUN_SENDER_QUESTIONS", "30"))
    LIVE_QA_FLOOD = int(os.environ.get("LIVE_QA_FLOOD", "20"))
    # Devices that may vote on one question (an answer, a page of a block, an upvote in the Q&A) from one sender's
    # network in a run, counted by the database for all processes (app/live/devices.py, audit 2026-10-18, BIZ-02);
    # as wide as a lecture hall behind one address. Device ids are issued and signed by the server. 0 = no limit.
    LIVE_SENDER_QUESTION_VOTES = int(os.environ.get("LIVE_SENDER_QUESTION_VOTES", "300"))
    # hierarchical Bayes and latent class estimations computing at the same time on the whole server (all its
    # processes, file locks in uploads/hb/_slots; audit 2026-10-06, P7); more wait
    HB_PARALLEL = int(os.environ.get("HB_PARALLEL", "2"))
    # estimations (HB, latent classes, SEM and panel bootstraps, power simulations) one account may start within
    # ESTIMATE_WINDOW seconds, counted for all processes (rate_count): one account with many copies of a survey put its
    # jobs before everyone else's (audit 2026-10-22, D-04). 0 = no limit
    ESTIMATE_LIMIT = int(os.environ.get("ESTIMATE_LIMIT", "12"))
    ESTIMATE_WINDOW = int(os.environ.get("ESTIMATE_WINDOW", "3600"))
    # dummy participations one survey may hold in all (app/surveys/data.py, make_dummy; audit 2026-10-22, D-05)
    DUMMY_MAX = int(os.environ.get("DUMMY_MAX", "5000"))
    # the estimations in a process of their own at the lowest priority instead of a thread of the web process
    # (app/estimate.py); ESTIMATE_PROCESS=0: threads as before (the fallback also when the process cannot start)
    # the omega intervals of the results page as a bootstrap job of their own (app/omega_job.py) instead of in the request
    OMEGA_BACKGROUND = os.environ.get("OMEGA_BACKGROUND", "1").strip().lower() not in ("0", "false", "no", "off", "")
    # the structural equation model of the results page as a job of its own (app/sem_fit_job.py) instead of in the
    # request (thread "Rechenknecht", 2026-10-17)
    SEM_BACKGROUND = os.environ.get("SEM_BACKGROUND", "1").strip().lower() not in ("0", "false", "no", "off", "")
    ESTIMATE_PROCESS = os.environ.get("ESTIMATE_PROCESS", "1").strip().lower() not in ("0", "false", "no", "off", "")
    # Rechenknecht (app/compute.py, tools/rechenknecht/README.md): HB and latent class estimations on a second
    # machine; both set: the jobs go there, with a fallback to this server; empty (standard): computed here
    COMPUTE_URL = os.environ.get("COMPUTE_URL", "").strip().rstrip("/")
    COMPUTE_SECRET = os.environ.get("COMPUTE_SECRET", "").strip()
    # signature 1 of the Rechenknecht's messages (time, path, body; app/compute.py), next to signature 2 (direction,
    # method, nonce; audit 2026-10-18, CFG-07): off by default since 2026-10-22 (audit E-02: without a nonce it could
    # be replayed, and a request without signature 2 fell back to it). 1 only for an outside Rechenknecht of an older
    # version, until it runs the current code (BETRIEB.md); the one in the compose network comes with every deploy
    COMPUTE_SIGNATURE_V1 = _bool("COMPUTE_SIGNATURE_V1", False)
    # RFC 3161 timestamps for seal chains and pre-registrations (app/seal.py, app/prereg.py): the service of the DFN;
    # TSA_URL= (empty) switches them off, e.g. on an installation without internet; https by default (audit 2026-10-11,
    # AU7: nobody on the way can hold up or blow up the reply)
    TSA_URL = os.environ.get("TSA_URL", "https://zeitstempel.dfn.de").strip()
    # a new survey starts with the seal chain on (Philipp, 2026-10-08; app/seal.py)
    SEAL_DEFAULT = _bool("SEAL_DEFAULT", True)
    # one DFN timestamp per round for all seal chains, over a Merkle tree (app/seal.py, anchor_round; Ralf,
    # 2026-10-16, konzept-merkle-zeitstempel-2026-10-16.md); off by default: switched on after the deploy
    SEAL_MERKLE = _bool("SEAL_MERKLE", False)
    PHONE_RUNS_PER_DAY = int(os.environ.get("PHONE_RUNS_PER_DAY", "300"))          # test runs per survey, phone link
    LOGIN_LIMIT = int(os.environ.get("LOGIN_LIMIT", "30"))         # sign-in attempts with a password per sender ...
    LOGIN_WINDOW = int(os.environ.get("LOGIN_WINDOW", "600"))      # ... within this many seconds
    RESET_LIMIT = int(os.environ.get("RESET_LIMIT", "5"))          # requests "Passwort vergessen?" per sender ...
    RESET_WINDOW = int(os.environ.get("RESET_WINDOW", "3600"))     # ... within this many seconds
    # complaints from the privacy page: per survey within 24 hours, beyond this they are kept but no longer
    # announced in Slack and get no mail of receipt; per sender and survey within 24 hours, beyond this they are
    # refused; mails of receipt per address within 24 hours, whoever sends the complaints (audit 2026-10-18, BIZ-03)
    COMPLAINTS_PER_DAY = int(os.environ.get("COMPLAINTS_PER_DAY", "30"))
    COMPLAINTS_PER_SENDER = int(os.environ.get("COMPLAINTS_PER_SENDER", "3"))
    COMPLAINT_MAILS_PER_ADDRESS = int(os.environ.get("COMPLAINT_MAILS_PER_ADDRESS", "1"))
    # a participation begun before the survey closed (field end, "Beenden", a pause) may still be finished for this
    # many hours after the closing; later it shows the page "Befragung beendet" and stays "begonnen". An archived
    # survey takes nothing any more. -1: no time limit, as before (audit 2026-10-18, BIZ-04)
    CLOSE_GRACE_HOURS = int(os.environ.get("CLOSE_GRACE_HOURS", "24"))
    ERASE_LIMIT = int(os.environ.get("ERASE_LIMIT", "10"))         # tries of a deletion code per sender ...
    ERASE_WINDOW = int(os.environ.get("ERASE_WINDOW", "600"))      # ... within this many seconds
    VERIFY_LIMIT = int(os.environ.get("VERIFY_LIMIT", "60"))       # checks of a seal chain code (/verify) per sender ...
    VERIFY_WINDOW = int(os.environ.get("VERIFY_WINDOW", "600"))    # ... within this many seconds (audit 2026-10-09, K4)
    SEAL_CHECK_LIMIT = int(os.environ.get("SEAL_CHECK_LIMIT", "6"))  # "Jetzt prüfen" of a seal chain per survey ...
    SEAL_CHECK_WINDOW = int(os.environ.get("SEAL_CHECK_WINDOW", "600"))  # ... within this many seconds (audit 2026-10-10, S5)
    # read tokens for R and Python (app/data_access.py): downloads per token within this many seconds, and requests
    # with a wrong token per sender (then 429 for a while)
    TOKEN_LIMIT = int(os.environ.get("TOKEN_LIMIT", "60"))
    TOKEN_WINDOW = int(os.environ.get("TOKEN_WINDOW", "3600"))
    TOKEN_TRY_LIMIT = int(os.environ.get("TOKEN_TRY_LIMIT", "20"))
    TOKEN_TRY_WINDOW = int(os.environ.get("TOKEN_TRY_WINDOW", "600"))
    # tokens of the AI agent interface (app/agent_access.py, app/mcp.py): calls per token within this many seconds;
    # a questionnaire built by an agent takes a few hundred, a loop gone wrong is slowed down
    AGENT_LIMIT = int(os.environ.get("AGENT_LIMIT", "600"))
    AGENT_WINDOW = int(os.environ.get("AGENT_WINDOW", "3600"))
    # diary studies (app/diary_api.py): entries a person may begin or send later within this many seconds; far above
    # what a schedule asks for (at most 12 modules, self-started ones up to 50 a day), against a phone or script in a
    # loop (audit 2026-10-11, H2)
    DIARY_PERSON_LIMIT = int(os.environ.get("DIARY_PERSON_LIMIT", "60"))
    DIARY_PERSON_WINDOW = int(os.environ.get("DIARY_PERSON_WINDOW", "600"))
    # the other requests of a person of the diary app within DIARY_PERSON_WINDOW: /study, /offline/<module>, /leave,
    # /erase (audit 2026-10-13, AN-2); the app asks /study when it opens and now and then, a page at most every 15 min
    DIARY_SYNC_LIMIT = int(os.environ.get("DIARY_SYNC_LIMIT", "120"))
    # E-mail (app/mail.py, concept mailversand-konzept.md): SMTP access of the server; MAIL_HOST and MAIL_FROM empty =
    # off, and then no page shows any mail function. MAIL_SECURITY starttls (default, port 587), ssl (465) or none.
    MAIL_HOST = os.environ.get("MAIL_HOST", "").strip()
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "0") or 0)
    MAIL_SECURITY = os.environ.get("MAIL_SECURITY", "").strip().lower()
    MAIL_USER = os.environ.get("MAIL_USER", "").strip()
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "").strip()             # e.g. noreply@askurious.de, the sender of every mail
    MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Askurious").strip()
    MAIL_PER_MINUTE = int(os.environ.get("MAIL_PER_MINUTE", "60"))  # mails of a series (app/mailqueue.py) per minute
    MAIL_SYNC = False  # True sends in the request instead of a thread (tests)
    INVITES_PER_DAY = int(os.environ.get("INVITES_PER_DAY", "2000"))  # invitation mails per survey within 24 hours
    # series mails one account queues within 24 hours, across all its surveys (invitations, links per person)
    MAIL_PER_ACCOUNT_DAY = int(os.environ.get("MAIL_PER_ACCOUNT_DAY", "2000"))
    # Slack incoming webhook: new complaints (and errors reported by researchers) are announced there; empty = off
    SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
    # buttons in those messages (app/slack_actions.py): the Slack app's signing secret, and who may click (member ids)
    SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
    SLACK_ADMINS = _list("SLACK_ADMINS")
    PHONE_COMMENTS_PER_DAY = int(os.environ.get("PHONE_COMMENTS_PER_DAY", "200"))  # per survey, phone link
    # images on comments (app/comment_shots.py): over the phone link per survey and 24 hours, and all of a survey
    COMMENT_SHOTS_PER_DAY = int(os.environ.get("COMMENT_SHOTS_PER_DAY", "30"))
    COMMENT_SHOTS_MAX_MB = int(os.environ.get("COMMENT_SHOTS_MAX_MB", "200"))
    # images on comments one sender may send within COMMENT_SHOT_SENDER_WINDOW seconds, counted before the image is read
    # (also in the editor preview; audit 2026-10-09b, O1)
    COMMENT_SHOT_SENDER_LIMIT = int(os.environ.get("COMMENT_SHOT_SENDER_LIMIT", "20"))
    COMMENT_SHOT_SENDER_WINDOW = int(os.environ.get("COMMENT_SHOT_SENDER_WINDOW", "3600"))
    # voice messages (app/items/voice.py): all recordings of a survey; above it the page offers the text field only
    VOICE_MAX_MB = int(os.environ.get("VOICE_MAX_MB", "500"))
    # ... and the share of it (percent) one sender (IPv4 address, IPv6 /64; its /48 with SENDER_WIDE_FACTOR) may fill
    # within an hour: one script filled the whole survey's room (audit 2026-10-22, D-02); a lab of 75 behind one
    # address with a minute each stays below 20 %. 0 = no share per sender
    VOICE_SENDER_SHARE = int(os.environ.get("VOICE_SENDER_SHARE", "20"))
    # Uploads of researchers (images, video and audio, paper scans, interview recordings; security audit 2026-10-13,
    # DA-5; app/media.py check_room): all files of a survey, and of all surveys a person runs as admin, in MB (0 = no
    # limit); and before every such file the disk must keep STORAGE_MIN_FREE_MB free
    SURVEY_STORAGE_MAX_MB = int(os.environ.get("SURVEY_STORAGE_MAX_MB", "5000"))
    ACCOUNT_STORAGE_MAX_MB = int(os.environ.get("ACCOUNT_STORAGE_MAX_MB", "20000"))
    STORAGE_MIN_FREE_MB = int(os.environ.get("STORAGE_MIN_FREE_MB", "1000"))
    # First deploy with this off; enable only after every writer has the accounting hooks.
    STORAGE_USAGE_ENABLED = os.environ.get("STORAGE_USAGE_ENABLED", "0") == "1"
    # Video / audio stimuli (app/items/stimulus.py, experimental "media"): one file and all files of a survey; a minute of
    # 720p video takes some 10-15 MB, and every participant loads the whole file
    MEDIA_MAX_MB = int(os.environ.get("MEDIA_MAX_MB", "50"))
    MEDIA_SURVEY_MAX_MB = int(os.environ.get("MEDIA_SURVEY_MAX_MB", "250"))
    AI_TEXTS_PER_DAY = int(os.environ.get("AI_TEXTS_PER_DAY", "100"))              # per researcher
    AI_IMAGES_PER_DAY = int(os.environ.get("AI_IMAGES_PER_DAY", "200"))            # per researcher
    # Against cost and overload by AI (security audit 2026-10-13, KR-2; app/limits.py charge_ai, app/ai.py): site admins
    # get this many times the limits above (finite, no longer none); all calls over the installation's key together stop
    # at a daily budget of the platform (0 = none; Slack hears of it once a day); AI calls running at the same time per
    # server process and per person in a process (more get "gleich noch einmal versuchen"); the most output tokens of a
    # text call over the Responses API (OpenAI; 0 = no limit), generous since reasoning models think in them too
    AI_ADMIN_FACTOR = int(os.environ.get("AI_ADMIN_FACTOR", "5"))
    AI_PLATFORM_TEXTS_PER_DAY = int(os.environ.get("AI_PLATFORM_TEXTS_PER_DAY", "5000"))
    AI_PLATFORM_IMAGES_PER_DAY = int(os.environ.get("AI_PLATFORM_IMAGES_PER_DAY", "500"))
    AI_PARALLEL = int(os.environ.get("AI_PARALLEL", "4"))
    AI_PARALLEL_PER_ACCOUNT = int(os.environ.get("AI_PARALLEL_PER_ACCOUNT", "2"))
    AI_MAX_OUTPUT_TOKENS = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "16000"))
    # own AI access (app/ai_access.py, experimental "own_ai_key"): with an own key the daily limits above do not count,
    # only this technical one against endless loops (E7); AI_OWN_URL_HOSTS: hosts allowed as a typed base address
    # although they are not public (an institute's own model server), comma-separated
    OWN_AI_CALLS_PER_DAY = int(os.environ.get("OWN_AI_CALLS_PER_DAY", "2000"))     # per researcher
    AI_OWN_URL_HOSTS = [h.lower() for h in _list("AI_OWN_URL_HOSTS")]

    # Secret for /cron/close in the header X-Cron-Key (Plesk scheduled task); ?key=... still works but is warned about
    CRON_KEY = os.environ.get("CRON_KEY", "")

    # "Mit Zenodo verbinden" (OAuth): one registered application per target
    # Coscine, the research data platform of RWTH Aachen (experimental, app/coscine.py); tests point it elsewhere
    COSCINE_API = os.environ.get("COSCINE_API", "https://coscine.rwth-aachen.de/coscine/api/v2").rstrip("/")
    ZENODO_CLIENT_ID = os.environ.get("ZENODO_CLIENT_ID", "")
    ZENODO_CLIENT_SECRET = os.environ.get("ZENODO_CLIENT_SECRET", "")
    ZENODO_SANDBOX_CLIENT_ID = os.environ.get("ZENODO_SANDBOX_CLIENT_ID", "")
    ZENODO_SANDBOX_CLIENT_SECRET = os.environ.get("ZENODO_SANDBOX_CLIENT_SECRET", "")
    # Key for encrypting stored OAuth tokens and for the links and opt-out hashes of invitation mails (defaults to
    # a key derived from SECRET_KEY)
    TOKEN_KEY = os.environ.get("TOKEN_KEY", "")
    # the security log in daily files as well (app/security_log.py, audit 2026-10-22, E-04): this folder, else on the
    # Docker server (SIDE set) instance/security-log; kept SECURITY_LOG_DAYS days
    SECURITY_LOG_DIR = os.environ.get("SECURITY_LOG_DIR", "").strip()
    SECURITY_LOG_DAYS = int(os.environ.get("SECURITY_LOG_DAYS", "90"))
    # The TOKEN_KEYs of before while TOKEN_KEY is changed (comma-separated, newest first): what was locked with them still
    # opens, and `python -m app.key_rotation` locks it anew with TOKEN_KEY (BETRIEB.md, "Schlüssel wechseln"; audit
    # 2026-10-13, KR-6). Empty: none.
    TOKEN_KEY_FALLBACKS = _list("TOKEN_KEY_FALLBACKS")
    # Key of the box the participants' containers open (app/secrets_box.py, PARTICIPANT): the keys of web services,
    # external sections and panel links in the surveys, the seals of return links and diary entries. Its own, so that
    # the participants' side needs no TOKEN_KEY, which opens second factors and OAuth tokens (audit 2026-10-22, E-01).
    # Empty: TOKEN_KEY's box, as before. After setting it: `python -m app.key_rotation` (BETRIEB.md)
    PARTICIPANT_TOKEN_KEY = os.environ.get("PARTICIPANT_TOKEN_KEY", "")
    PARTICIPANT_TOKEN_KEY_FALLBACKS = _list("PARTICIPANT_TOKEN_KEY_FALLBACKS")
    # Key of the pseudonyms in the pooled construct data (study_id, participant_id; app/constructs.py): its own and
    # lasting, so a new SECRET_KEY keeps them. Empty: SECRET_KEY, as before, so the pseudonyms stay as they were
    POOL_KEY = os.environ.get("POOL_KEY", "")
    # Key of the random prompt times of diary studies (app/diary.seed_of): its own and lasting, so a new SECRET_KEY
    # keeps the times already scheduled and answered. Empty: SECRET_KEY, as before, so running studies keep their times
    DIARY_KEY = os.environ.get("DIARY_KEY", "")
    # Key of the researchers' sessions only (app/cookies.py), given to the researchers' container alone and never to
    # the participants' (deploy/docker/compose.yaml): a break-in on the participants' side then cannot sign a
    # researcher's or admin's session (audit 2026-10-18, CFG-04). Empty: SECRET_KEY, as before. Setting it signs every
    # researcher out once; RESEARCHER_SESSION_KEY_FALLBACKS (comma-separated) keeps sessions of a key of before
    RESEARCHER_SESSION_KEY = os.environ.get("RESEARCHER_SESSION_KEY", "")
    RESEARCHER_SESSION_KEY_FALLBACKS = _list("RESEARCHER_SESSION_KEY_FALLBACKS")

    # Hierarchical Bayes estimation (app/maxdiff_hb.py): True computes it within the request instead of in the
    # background (tests)
    HB_SYNC = False
    # Latent class segments (app/segments.py): the same in the request (tests); LC_STARTS, LC_SEED and LC_KMAX
    # (starts per K, seed, most classes) default to 20, 1 and 6
    LC_SYNC = False
    # how many seconds a server process keeps the reading-time medians of a survey for the tempo hint (app/tempo.py);
    # 0 = always anew
    TEMPO_MEDIAN_SECONDS = 60
