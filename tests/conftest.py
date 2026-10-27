import os
import shutil
import sqlite3
import sys

# One BLAS/OpenMP thread per test process, set before anything imports numpy. Otherwise each xdist worker starts
# a thread per logical CPU for every matrix step, and a few parallel runs fill all cores of the machine.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import pytest  # noqa: E402
from jinja2 import BytecodeCache
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.routing import Rule

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# pytest --testmon runs only the tests whose code changed. One record for all checkouts of this machine: a fresh clone
# then starts from what the others already ran instead of running everything once (TESTMON_DATAFILE overrides it).
os.environ.setdefault("TESTMON_DATAFILE", os.path.join(__import__("tempfile").gettempdir(), "askurious-testmon.db"))
# Some shells (the assistant's sessions) set PYTHONDONTWRITEBYTECODE. Then pytest prepares every test file
# again on every start, in every worker (4 s for collecting instead of 1 s). __pycache__ is in .gitignore.
sys.dont_write_bytecode = False

from config import Config, database_url, engine_options  # noqa: E402

# The whole suite against PostgreSQL: TEST_DATABASE_URL=postgresql://user:password@host:port/postgres (a server
# where this user may create databases). Each worker builds a template database once; every test gets a fresh
# copy of it (CREATE DATABASE ... TEMPLATE), as the SQLite tests get a copy of the template file.
PG_URL = database_url(os.environ.get("TEST_DATABASE_URL"))
# A PostgreSQL server for the tests of moving into it (tests/platform/test_database_admin.py) while the rest runs on SQLite
PG_SERVER = PG_URL or database_url(os.environ.get("TEST_PG_SERVER"))
needs_sqlite = pytest.mark.skipif(bool(PG_URL), reason="looks into the SQLite file itself")


def _pg_name(kind):
    return f"askurious_{kind}_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"


def _pg_url(name):
    from sqlalchemy.engine import make_url

    return make_url(PG_SERVER).set(database=name).render_as_string(hide_password=False)


def _pg_admin(*statements):
    from sqlalchemy import create_engine

    engine = create_engine(PG_SERVER, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
    engine.dispose()


@event.listens_for(Engine, "connect")
def _fast_sqlite(dbapi_connection, _record):
    """Test databases live in a temp folder for one test: no waiting for the disk on every commit
    and no journal file per transaction (on Windows that alone was about 20 ms per commit)."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA synchronous = OFF")
        cur.execute("PRAGMA journal_mode = MEMORY")
        if _FOREIGN_KEYS[0]:
            cur.execute("PRAGMA foreign_keys = ON")
        cur.close()


# Foreign keys hold in the tests as on PostgreSQL (audit 2026-10-21, B8): SQLite ignores them unless asked, so a new
# table with a key on survey or user that no delete path empties failed only on the server. The app itself leaves
# them off on SQLite (app/extensions.py). Tests marked sqlite_foreign_keys_off run without: SQLite's batch mode of
# the migrations copies and drops referenced tables, which PostgreSQL alters in place.
# one switch for every copy of this module (tests import it as tests.conftest besides pytest's own conftest)
from tests.helpers.sqlite_keys import FOREIGN_KEYS as _FOREIGN_KEYS  # noqa: E402


@pytest.fixture(autouse=True)
def _sqlite_foreign_keys(request):
    _FOREIGN_KEYS[0] = request.node.get_closest_marker("sqlite_foreign_keys_off") is None
    yield
    _FOREIGN_KEYS[0] = True


class _TemplateCache(BytecodeCache):
    """Compiled templates, kept for the whole run: every test has its own app, and each app would
    otherwise compile every template it renders again. Jinja checks the source checksum on loading."""

    def __init__(self):
        self.store = {}

    def load_bytecode(self, bucket):
        if bucket.key in self.store:
            bucket.bytecode_from_string(self.store[bucket.key])

    def dump_bytecode(self, bucket):
        self.store[bucket.key] = bucket.bytecode_to_string()


_TEMPLATES = _TemplateCache()

_URL_BUILDERS = {}
_compile_builder = getattr(Rule, "_compile_builder", None)


def _shared_url_builder(self, append_unknown=True):
    """Every test app registers the same 200 routes, and Werkzeug compiles two URL builders per route
    (about 70 ms per app, most of create_app). The builder depends only on the route's parts and
    defaults and gets its rule at call time, so the apps of one run can share it."""
    try:
        key = (self.rule, tuple(self._trace), tuple(sorted((self.defaults or {}).items())), append_unknown)
        if key not in _URL_BUILDERS:
            _URL_BUILDERS[key] = _compile_builder(self, append_unknown)
        return _URL_BUILDERS[key]
    except (AttributeError, TypeError):  # another Werkzeug, or defaults that cannot be a key: compile as usual
        return _compile_builder(self, append_unknown)


if _compile_builder is not None:  # a Werkzeug internal; without it the tests run as before, only slower
    Rule._compile_builder = _shared_url_builder


def _config(folder, url=None):
    class TestConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = url or f"sqlite:///{folder / 'test.db'}"
        SQLALCHEMY_ENGINE_OPTIONS = engine_options(url or "sqlite")
        AI_PRICES_FILE = str(folder / "ai_prices.json")  # never the real instance folder
        UPLOAD_FOLDER = str(folder / "uploads")
        WTF_CSRF_ENABLED = False
        OMEGA_BACKGROUND = False  # drawn in the request, as the tests read it at once; tests/analysis/test_omega_job.py covers the job
        SEM_BACKGROUND = False  # the model estimated in the request; tests/analysis/test_sem_fit_job.py covers the job
        ESTIMATE_PROCESS = False  # threads, which the tests join; tests/analysis/test_estimate.py covers the process
        SQLITE_JOURNAL = ""  # the tests keep their own fast settings (_fast_sqlite); tests/platform/test_scaling.py covers WAL
        BALANCE_SECONDS = 0  # counts and comparison pools always fresh; tests/quotas/test_balance.py covers the keeping
        SIGN_IN_SECONDS = 0  # a changed sign-in list holds at once; tests/accounts/test_sign_in_list.py covers the keeping
        BOARD_PUBLIC_SECONDS = 0  # a link sees every new state at once; tests/dashboards/test_review_small_dashboards.py covers the keeping
        BOARD_STEP_MARGIN = 0  # a step of the links counts what was just stored; tests/platform/test_security_links.py covers the margin
        DEV_LOGIN = True
        INSTANCE_OVERLAY = ""  # the code's own templates, never those of an installation in instance_overlay/
        # stand-in wordings with the structure of the scales of others (never the real ones, which only the
        # installation has: app/constructs.py, INSTRUMENTS)
        INSTRUMENT_WORDINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "instruments.json")
        # a key of its own, not the development key: with that the app answers only requests from this computer
        # (app.is_local_request, audit 2026-10-18, CFG-01), and the tests send from made-up addresses (REMOTE_ADDR)
        SECRET_KEY = "test-Q7wRt5zK9pL2mN8bV4cX6jH3gF1dS0aYx8Lp2Mn4Bv6Cx"
        ADMIN_TOTP = False  # the administration without a code from an app; tests/accounts/test_twofactor.py covers it
        CRON_KEY = "k"
        TSA_URL = ""  # never the DFN from a test; tests/exports/test_seal.py stands in for the service
        SEAL_DEFAULT = False  # the tests switch the seal chain on where they need it (tests/exports/test_seal.py)
        ADMIN_ORCIDS = ["0000-0000-0000-001X"]
        NEW_ACCOUNTS_MAY_CREATE = False  # tests sign in people who only work on what is shared; see tests/accounts/test_new_accounts.py
        ACCOUNT_SETUP = False  # new accounts go straight to their pages; tests/accounts/test_account_setup.py switches it on
        OPENAI_API_KEY = ""
        NEW_SURVEY_DEMOGRAPHICS = False  # tests build on an empty block 2; see test_new_survey_starts_with_demographics
        TRANSLATION_CHECK = False  # the test survey has de and en; see test_start_needs_complete_translation
        RESULTS_WAIT_PAGE = False  # /results answers with the results; tests/analysis/test_results_store.py covers the page before
        REPORT_JOB_COST = 0  # reports below the threshold in the request; tests/platform/test_heavy_offload.py covers the estimate
        REPORT_JOB_COST_RESEARCHER = 0  # the same for SIDE=researcher (test_heavy_offload.py)
        MAIL_HOST = ""  # mail off unless a test switches it on (mail_on below)
        MAIL_FROM = ""
        MAIL_SYNC = True
        ACCOUNT_JOBS_SYNC = True
        UNDER_CONSTRUCTION = False  # the header tape is tested on its own (test_under_construction_only_for_researchers)
        # no housekeeping in a test's requests: by the clock it ran in some and not in others (code review
        # 2026-10-15, L2); tests call lifecycle.close_due_surveys themselves, tests/platform/test_scheduled_housekeeping.py
        # covers the switch
        LIFECYCLE_IN_REQUEST = False

    return TestConfig


@pytest.fixture(scope="session")
def _db_template(tmp_path_factory):
    """An empty database as create_app leaves it (tables, seed constructs, search index), built once.
    Every test starts from its own copy instead of creating all of it again."""
    from app import create_app
    from app.extensions import db

    folder = tmp_path_factory.mktemp("template")
    url = None
    if PG_URL:
        name = _pg_name("template")
        _pg_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", f"CREATE DATABASE {name}")
        url = _pg_url(name)
    template = create_app(_config(folder, url))
    with template.app_context():
        db.engine.dispose()
        db.engines.clear()
    return name if PG_URL else folder / "test.db"


@pytest.fixture
def app(tmp_path, monkeypatch, _db_template):
    import itertools

    from app import constructs, create_app, extensions, models, participate, quality
    from app.extensions import db

    quality._KEPT.clear()  # the values of quality control kept per process: none from another test's database
    quality._PAGE.clear()  # nor the parts of its page
    participate._start_tried.clear()  # planned starts tried from a link (T3): per process, not per test database
    participate._release_tried.clear()  # places given back at the door (F3): per process as well
    from app import limits

    limits._asked.clear()  # answers of the shared rate limits kept in the process: from another test's database
    limits._fallback.clear()
    from app import lifecycle

    # the housekeeping's clocks per process: a test calling it runs every step, whatever the test before did (L2)
    monkeypatch.setattr(lifecycle, "_last_check", 0.0)
    monkeypatch.setattr(lifecycle, "_last_rare", None)
    from app import orcid_token

    # never ORCID's real key list: a test that signs ID tokens serves its own keys (tests/helpers/orcid_keys.py)
    orcid_token.forget()

    def _no_orcid(base):
        raise orcid_token.requests.ConnectionError("no call to ORCID in tests")

    monkeypatch.setattr(orcid_token, "_fetch_keys", _no_orcid)

    url = None
    if PG_URL:
        name = _pg_name("test")
        _pg_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", f"CREATE DATABASE {name} TEMPLATE {_db_template}")
        url = _pg_url(name)
    else:
        shutil.copyfile(_db_template, tmp_path / "test.db")
    # sequential survey codes ("1", "2", ...) so tests can write /surveys/1/...; tests/editor/test_links.py covers real codes
    counter = itertools.count(1)
    monkeypatch.setattr(models, "new_code", lambda: str(next(counter)))
    with monkeypatch.context() as m:
        # the copy of the template has every column and every seed construct: checking it again in every test cost
        # about 50 ms (a third of create_app). The template ran both; tests/platform/test_schema.py and the seed tests call them.
        m.setattr(extensions, "add_missing_columns", lambda app: None)
        m.setattr(constructs, "ensure_seed", lambda: None)
        app = create_app(_config(tmp_path, url))
    app.jinja_env.bytecode_cache = _TEMPLATES
    from flask.testing import FlaskClient

    class BrowserClient(FlaskClient):
        def post(self, *args, **kwargs):
            # A real phone opens the join page before using its forms. Tests of raw POSTs use open(method="POST").
            from urllib.parse import urlsplit

            path = urlsplit(str(args[0] if args else kwargs.get("path", ""))).path
            if path.startswith("/live/") and len(path.split("/")) >= 4:
                with self.session_transaction() as session:
                    joined = bool(session.get("live-device"))
                if not joined:
                    self.get("/".join(path.split("/")[:3]))
            return super().post(*args, **kwargs)

    app.test_client_class = BrowserClient
    yield app
    with app.app_context():
        db.engine.dispose()
        db.engines.clear()


@pytest.fixture
def client(app):
    c = app.test_client()
    c.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    c.post("/terms/", data={"accept": "1"})
    return c


@pytest.fixture
def survey(client):
    """A draft survey (id 1), languages de+en. Block 1 holds the consent (item 1, always
    present and pinned); block 2 is an empty content block."""
    client.post("/surveys/new", data={"title": "Test", "languages": ["de", "en"], "default_language": "de"})
    return 1


def experiments_on(client, *keys, orcid="0000-0000-0000-001X"):
    """Switch experimental features on for one account, as a site admin would (app/experimental.py); none = all off."""
    from app.extensions import db
    from app.models import User

    with client.application.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
        user.experiments = list(keys)
        db.session.commit()


# "Anlegen" for all four survey types: what UserLogin.may_create=True was before 2026-10-18 (app/kind_access.py)
CREATE_ALL = {"survey": "create", "live": "create", "interview": "create", "diary": "create"}


def allow(app, *orcids, keep=False):
    """Let exactly these iDs sign in by themselves, as ALLOWED_ORCIDS of the configuration did until 2026-10-06:
    the list of the administration ("*": the switch "every ORCID holder", app/signin.py), and the right to create
    surveys for their accounts, those there now (as the takeover gave it) and those made at a first sign-in.
    keep: add to the list instead of replacing it."""
    from app import signin
    from app.extensions import db
    from app.models import SignInOrcid, SiteSetting, User

    with app.app_context():
        if not keep:
            db.session.execute(db.delete(SignInOrcid))
            db.session.execute(db.delete(SiteSetting).where(SiteSetting.key == signin.EVERYONE))
        if "*" in orcids:
            signin._set(signin.EVERYONE, "1")
        listed = [o for o in orcids if o != "*"]
        db.session.add_all(SignInOrcid(orcid=o) for o in listed if db.session.get(SignInOrcid, o) is None)
        query = db.select(User).filter(User.orcid.in_(listed)) if "*" not in orcids else db.select(User)
        signin._may_create([u for u in db.session.scalars(query) if u.has_orcid])
        db.session.commit()
    if orcids:
        app.config["NEW_ACCOUNTS_MAY_CREATE"] = True


def may_sign_in(app, *orcids):
    """Put these iDs on the list "Anmeldung freischalten" only, without the right to create that allow() gives: for
    sharing a design, a linked element or a research project with a person, which needs someone who can sign in
    (rechte-uebersicht-konzept.md H4)."""
    from app.extensions import db
    from app.models import SignInOrcid

    with app.app_context():
        db.session.add_all(SignInOrcid(orcid=o) for o in orcids if db.session.get(SignInOrcid, o) is None)
        db.session.commit()


def add(client, type_, block=2, **extra):
    r = client.post(f"/surveys/1/editor/blocks/{block}/items", data={"type": type_, **extra})
    return r


def fill_questions(app, survey_id=1, text="Frage?"):
    """Give every element that the pre-launch check finds without a question one (UI/UX audit 2026-10-05, V11: an
    empty question blocks the start), for tests whose elements are about something else."""
    from app import design
    from app.extensions import db
    from app.models import Survey

    with app.app_context():
        s = db.session.get(Survey, survey_id)
        for item in [i for b in s.blocks for i in b.items if design.empty_texts(i)]:
            lang = s.default_language
            texts = dict(item.texts or {})
            texts[lang] = {**texts.get(lang, {}), "question": text}
            item.texts = texts
        db.session.commit()



def fill_table(client, url, **factors):
    """Fill a loop's table (its page at `url`) with every combination of the factors, one column each:
    fill_table(client, url, robot=["Pepper", "Nao"]). The form the page posts; other settings stay at their defaults."""
    import itertools

    names = list(factors)
    data = {"col_id": [f"c{n}" for n in range(len(names))], "col_name": names, "row_id": []}
    for r, combo in enumerate(itertools.product(*factors.values())):
        data["row_id"].append(f"r{r}")
        for n, value in enumerate(combo):
            data[f"cell_r{r}_c{n}"] = value
    return client.post(url, data=data)

# with the tick for the audio recording a survey with a voice message asks (items/consent.py, 2026-10-20); a tick the
# consent does not ask is ignored
CONSENT_OK = {"i1": "1", "i1_c_voluntary": "1", "i1_c_data": "1", "i1_c_audio": "1", "i1_c_commitment": "1"}


def fill_consent(client, **facts):
    """Make the consent (item 1) complete: text, responsible researcher, no minimum age, no open data. The key
    facts live in the survey settings (consent-facts), the text and confirmations in the element."""
    # a contact address: without it (and without an institution) the pre-launch check blocks the start (V13)
    client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X",
                                                  "contact_email": "studie@example.org", **facts})
    client.post("/surveys/1/editor/items/1", data={
        "code": "consent", "question": "Einwilligung", "body": "Text", "duration": "5",
        "confirm_voluntary": "1", "confirm_data": "1",
        "confirm_commitment": "1", "agree": "Fortsetzen", "disagree": "Nein",
    })


def split_node(survey):
    """The separation point of the flow (node "split"), or None."""
    from app import flow
    return next((n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "split"), None)


# --- Everyday runs leave out the slowest tests (about a fifth of the suite's time) --------------------------------
# slow: the single tests of at least 3 s in a full run, listed in tests/slow_tests.txt (tools/slow_tests.py writes it
# from measured durations; test suite concept 2026-10-18, G), or marked in their file. deploy: the deploy scripts played through
# with bash, up to 100 s per test with Git's bash; they run by themselves when the branch changes deploy files.
# `--full` runs everything (always before a PR is ready); a file named on the command line, -k or -m run what they pick.
SLOW_TESTS = {line.strip() for line in open(os.path.join(os.path.dirname(__file__), "slow_tests.txt"), encoding="utf-8")
              if line.strip() and not line.startswith("#")}
DEPLOY_FILES = {"test_audit8_b_deploy.py", "test_audit9_p3_deploy.py"}
DEPLOY_PATHS = ("deploy/", "migrations/", "tests/platform/test_audit8_b_deploy.py", "tests/platform/test_audit9_p3_deploy.py")


def pytest_addoption(parser):
    parser.addoption("--full", action="store_true", help="also the slow and the deploy tests (always before a PR is ready)")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: a test of several seconds (tests/slow_tests.txt); left out without --full")
    config.addinivalue_line("markers", "sqlite_foreign_keys_off: SQLite without PRAGMA foreign_keys (migration "
                            "downgrades in batch mode)")
    config.addinivalue_line("markers", "deploy: plays the deploy scripts through; left out without --full or deploy changes")
    # one worker takes over the rest of another that is still busy: a few long tests no longer decide the end of the run
    if getattr(config.option, "numprocesses", None) and getattr(config.option, "dist", None) == "load":
        config.option.dist = "worksteal"
    # every package the server installs, before any test: a venv without one turned the same tests red in every run
    if not hasattr(config, "workerinput"):
        from tests.helpers import requirements_check

        missing, other = requirements_check.check()
        if missing:
            pytest.exit(requirements_check.message(missing, sys.executable), returncode=4)
        if other:
            print("Other versions than requirements.lock / requirements-dev.txt: "
                  + ", ".join(f"{n} {have} (wanted {want})" for n, want, have in other), file=sys.stderr, flush=True)
    # a parallel run waits for one of TEST_SLOTS places on this machine (tests/helpers/slots.py); workers do not
    if getattr(config.option, "numprocesses", None) and not hasattr(config, "workerinput"):
        count = int(os.environ.get("TEST_SLOTS", "2") or 0)
        if count > 0:
            from tests.helpers import slots

            config._test_slot = slots.take(count, say=lambda text: print(text, file=sys.stderr, flush=True))
    # a run of everything on Windows hands the deploy tests and about half of the other test files to WSL, where bash
    # is many times faster and Python a fifth (tests/helpers/wsl_deploy.py); workers learn it from the environment
    if not hasattr(config, "workerinput") and not config.option.collectonly and _runs_everything(config):
        from tests.helpers import wsl_deploy

        if wsl_deploy.wanted() and wsl_deploy.available():
            part = wsl_deploy.share(config.rootpath) if wsl_deploy.split_wanted() else []
            os.environ["ASKURIOUS_DEPLOY_IN_WSL"] = "1"
            os.environ["ASKURIOUS_WSL_SHARE"] = "\n".join(part)
            workers = getattr(config.option, "numprocesses", None) or 2
            config._wsl_run = wsl_deploy.Run(config.rootpath, part, workers if isinstance(workers, int) else 4)
            print(f"The deploy tests and {len(part)} test files run in WSL meanwhile (WSL_SPLIT=0 hands over only the "
                  "deploy tests, WSL_TESTS=0 keeps everything here).", file=sys.stderr, flush=True)


def _wsl_share():
    """The test files the WSL part runs: on Windows the ones it hands over, in WSL the ones it was given."""
    part = os.environ.get("ASKURIOUS_WSL_PART")
    if part:
        with open(part, encoding="utf-8") as listed:
            return set(listed.read().split())
    return set(os.environ.get("ASKURIOUS_WSL_SHARE", "").split())


def pytest_ignore_collect(collection_path, config):
    """A split run (tests/helpers/wsl_deploy.py) does not even collect the files of the other side. WSL collects its
    own files and those with deploy tests, of which it runs only the deploy tests."""
    if not collection_path.name.startswith("test_") or collection_path.suffix != ".py":
        return None
    in_wsl = bool(os.environ.get("ASKURIOUS_WSL_PART"))
    if not in_wsl and not os.environ.get("ASKURIOUS_WSL_SHARE"):
        return None
    try:
        name = collection_path.resolve().relative_to(config.rootpath.resolve()).as_posix()
    except ValueError:
        return None
    if not in_wsl:
        return True if name in _wsl_share() else None
    from tests.helpers import wsl_deploy

    return None if name in _wsl_share() or wsl_deploy.has_deploy(collection_path) else True


def _runs_everything(config):
    """--full over the whole tests folder, nothing picked or left out (-k, -m, --lf, --deselect, --ignore, --testmon)."""
    option = config.option
    if not config.getoption("--full") or option.keyword or option.markexpr or getattr(option, "lf", False) \
            or getattr(option, "deselect", None) or getattr(option, "ignore", None) \
            or getattr(option, "ignore_glob", None) or getattr(option, "testmon", False):
        return False
    tests = (config.rootpath / "tests").resolve()
    return all((config.invocation_params.dir / arg).resolve() == tests for arg in config.args)


def pytest_sessionfinish(session, exitstatus):
    """A green run of everything leaves a mark for this state of the code (tests/helpers/green_runs.py), so that the
    deploy of the same state does not run the whole suite again (test suite concept 2026-10-18, F)."""
    config = session.config
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    wsl_passed = 0
    if os.environ.get("ASKURIOUS_WSL_PART") and not hasattr(config, "workerinput"):
        # the WSL part names its skipped tests, so that Windows runs them again (WSL lacks Node and a browser)
        for report in getattr(reporter, "stats", {}).get("skipped", []):
            print(f"\nWSL-SKIPPED {report.nodeid}", end="", flush=True)
        print(flush=True)
        return
    run = getattr(config, "_wsl_run", None)
    if run is not None:  # the tests that ran in WSL meanwhile count for this run
        from tests.helpers import wsl_deploy

        code, text = run.finish()
        lines = [line for line in text.splitlines() if line.strip() and not line.startswith(wsl_deploy.SKIPPED)]
        if reporter:
            reporter.write_line("")
            if code != 0:
                for line in lines[-60:]:
                    reporter.write_line(line)
            reporter.write_line(f"Tests in WSL: {wsl_deploy.summary(text)}" + ("" if code == 0 else f" (exit {code})"))
        wsl_passed = wsl_deploy.counted(text, "passed")
        again = sorted({node for node in wsl_deploy.skipped(text)})
        if again and code == 0:
            again_code, again_text, again_passed = wsl_deploy.run_here(config.rootpath, again)
            if reporter:
                if again_code != 0:
                    for line in again_text.splitlines()[-60:]:
                        reporter.write_line(line)
                reporter.write_line(f"Skipped in WSL, run again here: {len(again)} tests, {again_passed} passed")
            wsl_passed += again_passed
            code = again_code
        if code != 0 or not wsl_passed and run.part:
            session.exitstatus = exitstatus = pytest.ExitCode.TESTS_FAILED
    if exitstatus != 0 or hasattr(config, "workerinput") or not _runs_everything(config):
        return
    from tests.helpers import green_runs

    stats = getattr(reporter, "stats", {})
    passed = len([r for r in stats.get("passed", []) if getattr(r, "when", "call") == "call"]) + wsl_passed
    if passed:
        green_runs.record(config.rootpath, passed, len(stats.get("skipped", [])))


def pytest_unconfigure(config):
    from tests.helpers import slots

    slots.release(getattr(config, "_test_slot", None))


# A test that hangs ends as a failure instead of blocking the run (full suites stuck at 80 to 92 %, 2026-10-15): after
# TEST_TIMEOUT seconds (default 20 minutes, far above the slowest deploy test; 0 switches it off) faulthandler writes
# every thread's stack to stderr and ends the process. Under xdist the worker counts as crashed, its test as failed,
# and a new worker takes the rest. Built in (no pytest-timeout), so it works in every environment.
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "1200") or 0)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    import faulthandler

    if TEST_TIMEOUT > 0:
        faulthandler.dump_traceback_later(TEST_TIMEOUT, exit=True)
    try:
        yield
    finally:
        if TEST_TIMEOUT > 0:
            faulthandler.cancel_dump_traceback_later()


def _deploy_changed(root):
    """Does this branch (commits since main and uncommitted files) touch deploy files? Unknown counts as yes."""
    import subprocess

    try:
        out = []
        for args in (["diff", "--name-only", "origin/main...HEAD"], ["status", "--porcelain"]):
            run = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)
            if run.returncode:
                return True
            out += [line[3:] if args[0] == "status" else line for line in run.stdout.splitlines()]
        return any(p.strip().strip('"').startswith(DEPLOY_PATHS) for p in out)
    except (OSError, subprocess.SubprocessError):
        return True


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.nodeid in SLOW_TESTS:
            item.add_marker(pytest.mark.slow)
        elif item.path.name in DEPLOY_FILES:
            item.add_marker(pytest.mark.deploy)
    if os.environ.get("ASKURIOUS_DEPLOY_IN_WSL") == "1":  # they run in WSL meanwhile (tests/helpers/wsl_deploy.py)
        gone = [item for item in items if item.get_closest_marker("deploy")]
        if gone:
            config.hook.pytest_deselected(items=gone)
            items[:] = [item for item in items if not item.get_closest_marker("deploy")]
    if os.environ.get("ASKURIOUS_WSL_PART"):  # in WSL: of the files with deploy tests only those, the rest stays on Windows
        root = config.rootpath.resolve()
        mine = _wsl_share()
        gone = [item for item in items if not item.get_closest_marker("deploy")
                and item.path.resolve().relative_to(root).as_posix() not in mine]
        if gone:
            config.hook.pytest_deselected(items=gone)
            items[:] = [item for item in items if item not in gone]
    if config.getoption("--full") or config.option.keyword or config.option.markexpr:
        return
    named = set()
    for arg in config.args:
        path = config.invocation_params.dir / arg.split("::")[0]
        if path.is_file():
            named.add(path.resolve())
    leave = {"slow"} | (set() if _deploy_changed(config.rootpath) else {"deploy"})
    keep, gone = [], []
    for item in items:
        out = item.path.resolve() not in named and any(item.get_closest_marker(m) for m in leave)
        (gone if out else keep).append(item)
    if gone:
        config.hook.pytest_deselected(items=gone)
        items[:] = keep


def mail_on(app):
    """Switch e-mail on for this test app (app/mail.py); returns the list the sent mails land in instead of a server."""
    app.config.update(MAIL_HOST="smtp.example.org", MAIL_FROM="noreply@example.org", MAIL_SYNC=True)
    sent = app.extensions["mail_outbox_test"] = []
    return sent


def account_ref(orcid):
    """How a consent's "researchers" names the account of this iD since 2b ("u<id>", items/consent.member_ref).
    Needs an app context."""
    from app.extensions import db
    from app.models import User

    return f"u{db.session.scalar(db.select(User.id).filter_by(orcid=orcid))}"


# The participants' side may run under a database role of its own (app/participant_grants.py, audit 2026-10-18,
# CFG-04). In these folders every statement of a participant request is checked against that list
# (tests/helpers/participant_sql.py); PARTICIPANT_SQL_RECORD=<file> instead records what all tests touch.
PARTICIPANT_SQL_FOLDERS = ("participation", "quotas", "live", "diary", "privacy", "dashboards", "items")


@pytest.fixture(autouse=True)
def _participant_sql(request):
    record = os.environ.get("PARTICIPANT_SQL_RECORD")
    folder = request.node.path.parent.name
    if not record and folder not in PARTICIPANT_SQL_FOLDERS:
        yield
        return
    from tests.helpers import participant_sql

    participant_sql.start()
    try:
        yield
    finally:
        seen = participant_sql.stop()
    if record:
        participant_sql.write(seen, record)
        return
    wrong = participant_sql.violations(seen)
    if wrong:
        pytest.fail("The participants' side needs rights outside app/participant_grants.py: "
                    + "; ".join(f"{t} {p} ({', '.join(w[:3])})" for (t, p), w in sorted(wrong.items())))


def own_ai(app, orcid="0000-0000-0000-001X", base_url="https://llm.example.org/v1"):
    """An own AI access for this account (app/ai_access.py): the AI coding help runs only over one (Ralf, 2026-10-21)."""
    from app import secrets_box
    from app.extensions import db
    from app.models import AiAccess, User

    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
        db.session.add(AiAccess(user_id=user.id, provider="custom", base_url=base_url,
                                key_enc=secrets_box.encrypt("sk-own"), key_hint="sk-own", text_model="m"))
        db.session.commit()
