"""compose.yaml after the security audit of 2026-10-13: each container gets only the lines of the .env it needs
(SV-2/DP-6), memory and process limits and hardening for every service (SV-3/DP-5), the image digest of certbot and
the files Docker and git leave out (DP-11, DP-16), the weekly check for known vulnerabilities with the shipped
JavaScript libraries (DP-10), and the deploy scripts that say when they skip a check (SV-5/DP-9, DP-17).

No YAML library in the tests: compose.yaml is read as text, like the other tests of deploy/docker."""
import os
import re

import pytest

from tests.helpers.internal_docs import internal_text
from tests.platform.test_audit8_b_deploy import needs_bash

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCKER = os.path.join(HERE, "deploy", "docker")


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


COMPOSE = _read("deploy", "docker", "compose.yaml")
SERVICES = COMPOSE[COMPOSE.index("\nservices:\n"):COMPOSE.index("\nvolumes:\n")]


def _block(text, head):
    """The lines under `head` (a line of its own), up to the next line indented as little as `head` or less."""
    start = text.index("\n" + head + "\n") + len(head) + 2
    indent = len(head) - len(head.lstrip())
    lines = []
    for line in text[start:].split("\n"):
        if line.strip() and not line.lstrip().startswith("#") and len(line) - len(line.lstrip()) <= indent:
            break
        lines.append(line)
    return "\n".join(lines)


def _keys(block):
    """The names of a mapping of names without values ("  NAME:")."""
    return re.findall(r"^\s+([A-Z][A-Z0-9_]*):\s*(?:#.*)?$", block, re.M)


SETTINGS = _keys(_block(COMPOSE, "x-app-settings: &app-settings"))
SECRETS = _keys(_block(COMPOSE, "x-app-secrets: &app-secrets"))
MAIL_SECRETS = _keys(_block(COMPOSE, "x-mail-secrets: &mail-secrets"))
RESEARCHER_SECRETS = _keys(_block(COMPOSE, "x-researcher-secrets: &researcher-secrets"))
PARTICIPANT_SECRETS = _keys(_block(COMPOSE, "x-participant-secrets: &participant-secrets"))
APP = _block(COMPOSE, "x-app: &app")
APP_ENV = _block(APP, "  environment: &app-env")
APP_FIXED = _block(COMPOSE, "x-app-fixed: &app-fixed")
# what compose.yaml sets itself in the app containers (x-app and the services), and what never goes to a server
EXPLICIT = {"DATABASE_URL", "PROXY_COUNT", "COMPUTE_URL", "STATUS_URL", "PARTICIPANT_BASE_URL", "SIDE", "CONTAINER_STATS", "LIFECYCLE_IN_REQUEST", "MEDIA_X_ACCEL"}
NOT_PASSED = {"DEV_LOGIN", "DEV_TRUST_BRIDGE", "BACKUP_DAYS"}  # local development only; BACKUP_DAYS: the Dockerfile


def _config_names():
    names = set(re.findall(r'(?:environ\.get|environ\[|getenv|_bool|_list)\(\s*"([A-Z][A-Z0-9_]+)"', _read("config.py")))
    return names | {"DB_POOL_SIZE", "DB_MAX_OVERFLOW"}  # read in a loop (config.engine_options)


def _service(name):
    """The lines of a service, without comments."""
    head = re.search(rf"^  {re.escape(name)}:(?: &[^\s]+)?$", SERVICES, re.M).group(0)
    block = _block(SERVICES, head)
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line).rstrip() for line in block.split("\n"))


def test_every_setting_of_config_reaches_the_app_containers():
    """Audit 2026-10-13, SV-2/DP-6: the app containers no longer read the whole .env, so a setting that config.py reads
    but compose.yaml does not pass on would silently keep its default on the server."""
    passed = set(SETTINGS) | set(SECRETS) | set(MAIL_SECRETS) | set(RESEARCHER_SECRETS) | EXPLICIT | NOT_PASSED
    assert sorted(_config_names() - passed) == []
    # no name twice: a secret among the settings would reach the participants after all
    every = SETTINGS + SECRETS + MAIL_SECRETS + RESEARCHER_SECRETS
    assert len(every) == len(set(every))
    assert "<<: [*app-settings, *app-secrets, *mail-secrets, *app-fixed]" in APP_ENV
    for name in EXPLICIT - {"SIDE", "CONTAINER_STATS"}:
        assert re.search(rf"^\s+{name}: \S", APP_FIXED, re.M), name


def test_no_app_container_reads_the_whole_env_and_participants_get_no_researcher_secret():
    """Audit 2026-10-13, SV-2/DP-6: the participants' containers held the database superuser's password, the OpenAI
    key, the ORCID and Zenodo secrets and Slack's signing secret (env_file: .env for every app container)."""
    assert not re.search(r"^\s*env_file: \.env\s*$", COMPOSE, re.M)
    # only the brand's own (profile), and its participants' database line (participant-db-role.sh, CFG-04)
    assert set(re.findall(r"^\s+- path: (\S+)", COMPOSE, re.M)) == {".env.brand", ".env.brand-participant"}
    assert set(RESEARCHER_SECRETS) == {"ORCID_CLIENT_SECRET", "ZENODO_CLIENT_SECRET", "ZENODO_SANDBOX_CLIENT_SECRET",
                                       "OPENAI_API_KEY", "WHISPER_TOKEN", "SLACK_SIGNING_SECRET", "CRON_KEY",
                                       "RESEARCHER_SESSION_KEY", "RESEARCHER_SESSION_KEY_FALLBACKS"}
    for name in ("POSTGRES_PASSWORD", "DB_APP_PASSWORD", "DB_APP_USER", "BRAND_COMPUTE_SECRET"):
        assert name not in SETTINGS + SECRETS + MAIL_SECRETS + RESEARCHER_SECRETS
    participant = _service("participant-a")
    # audit 2026-10-18, CFG-04: neither the mail password nor the researchers' session key on the participants' side
    assert "<<: [*app-settings, *participant-secrets, *app-fixed]" in participant
    assert "app-env" not in participant and "researcher-secrets" not in participant and "mail-secrets" not in participant
    assert MAIL_SECRETS == ["MAIL_PASSWORD"] and "MAIL_PASSWORD" not in SECRETS + SETTINGS
    assert "<<: *app-env" in _service("housekeeping")  # sends what waits in the queue
    assert "participant-b: *participant" in SERVICES
    assert "<<: [*app-env, *researcher-secrets]" in _service("researcher")
    # the superuser's password only inside DATABASE_URL, and only while the app role is missing (BT4); the
    # participants' line falls back to the same (audit 2026-10-18, CFG-04)
    assert COMPOSE.count("${POSTGRES_PASSWORD}") == 3
    assert all(line.strip().startswith(("DATABASE_URL:", "POSTGRES_PASSWORD:"))
               for line in COMPOSE.splitlines() if "${POSTGRES_PASSWORD}" in line and not line.strip().startswith("#"))
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}" in _service("db")
    # the Rechenknecht and the status page stay without the app's keys
    for name in ("compute", "status"):
        block = _service(name)
        assert "SECRET_KEY" not in block and "app-env" not in block and "app-secrets" not in block


def test_participant_side_needs_none_of_the_researcher_secrets():
    """Audit 2026-10-13, SV-2: what reads the researcher-only secrets belongs to the researchers' side (app/hosts.py),
    and no participant template looks at them."""
    from app import hosts

    readers = {"ORCID_CLIENT_SECRET": "auth", "ZENODO_CLIENT_SECRET": "zenodo_auth", "WHISPER_TOKEN": "interview_transcribe",
               "SLACK_SIGNING_SECRET": "slack", "CRON_KEY": "cron"}
    for name, blueprint in readers.items():
        assert hosts.BLUEPRINT_SIDES[blueprint] == hosts.RESEARCHER, name
    folder = os.path.join(HERE, "app", "templates", "participant")
    for root, _, files in os.walk(folder):
        for f in files:
            text = _read(os.path.relpath(os.path.join(root, f), HERE))
            assert not any(name in text for name in RESEARCHER_SECRETS), f


def _services_list():
    return re.findall(r"^  ([a-z][a-z-]*):", SERVICES, re.M)


def test_every_service_has_memory_and_process_limits_and_no_new_privileges():
    """Audit 2026-10-13, SV-3/DP-5: all eight containers ran with Memory=0, PidsLimit=nil, no cap_drop, no
    no-new-privileges, a writable root file system. Now every service has limits; the app image runs without any
    capability and read-only with /tmp in memory."""
    hardened = _block(COMPOSE, "x-hardened: &hardened")
    assert "cap_drop: [ALL]" in hardened and 'security_opt: ["no-new-privileges:true"]' in hardened
    assert "read_only: true" in hardened
    assert "<<: *hardened" in APP and re.search(r"^  mem_limit: \d+[mg]$", APP, re.M) and "pids_limit:" in APP
    assert "- /tmp:size=" in APP
    services = _services_list()
    assert {"db", "participant-a", "participant-b", "researcher", "compute", "status", "nginx", "certbot"} <= set(services)
    for name in services:
        if name == "participant-b":
            continue                                                               # a copy of participant-a
        block = _service(name)
        from_app = "<<: *app" in block or "<<: *brand-researcher" in block
        assert from_app or re.search(r"^    mem_limit: \d+[mg]$", block, re.M), name
        assert from_app or re.search(r"^    pids_limit: \d+$", block, re.M), name
        assert from_app or "<<: *hardened" in block or 'security_opt: ["no-new-privileges:true"]' in block, name
    # everything built from the app's image is hardened completely, with its own /tmp
    for name in ("compute", "status", "brand-compute"):
        block = _service(name)
        assert "image: askurious:latest" in block and "<<: *hardened" in block and "- /tmp:size=" in block, name
    # the database: room above the app containers, and the last the kernel picks when the server runs short
    db = _service("db")
    assert re.search(r"^    oom_score_adj: -\d+$", db, re.M)
    assert int(re.search(r"pids_limit: (\d+)", db).group(1)) > 500                # max_connections=500, one process each


def test_app_writes_only_into_its_volumes_and_tmp():
    """Audit 2026-10-13, SV-3: read_only for the app containers holds only if the app writes nowhere else. The image
    makes only instance/ and /status the app user's (the rest of /app is root's, so it could not write there before);
    compose mounts volumes on both and a tmpfs on /tmp."""
    dockerfile = _read("deploy", "docker", "Dockerfile")
    assert "chown -R askurious instance /status" in dockerfile and "USER askurious" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile                             # no __pycache__ beside the code
    assert "- instance:/app/instance" in APP and "- status:/status" in _service("status")


def test_certbot_pinned_by_digest_and_build_context_without_secrets():
    """Audit 2026-10-13, DP-11 and DP-16: the certbot image by tag and digest (raised by hand anyway), a .dockerignore
    that keeps .env files, keys, dumps and the git history away from the Docker daemon, and a .gitignore that keeps
    them out of commits."""
    assert re.search(r"^    image: certbot/certbot:v[\d.]+@sha256:[0-9a-f]{64}$", _service("certbot"), re.M)
    ignore = _read(".dockerignore").splitlines()
    for pattern in (".git", "**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/*.dump", "instance", "tests"):
        assert pattern in ignore, pattern
    for needed in ("app", "migrations", "config.py", "log.md", "licence.md", "requirements.lock", "deploy"):
        assert needed not in ignore                                                # the Dockerfile copies them
    gitignore = _read(".gitignore").splitlines()
    for pattern in (".env", ".env.*", "netcup.ini", "*.pem", "*.key", "*.asc", "*.dump"):
        assert pattern in gitignore, pattern


def test_shipped_javascript_libraries_are_listed_for_the_vulnerability_check():
    """Audit 2026-10-13, DP-10: the libraries in app/static/vendor were never checked for known vulnerabilities. Their
    versions stand in tools/vendor_versions.txt (as the files themselves say), the deploy and the weekly maintenance
    window ask OSV about them."""
    import sys

    sys.path.insert(0, os.path.join(HERE, "tools"))
    try:
        import check_vulnerabilities
    finally:
        sys.path.pop(0)
    listed = {name: version for name, version, *_ in check_vulnerabilities.pins(os.path.join(HERE, "tools", "vendor_versions.txt"))}
    vendor = os.path.join("app", "static", "vendor")
    assert re.search(r"Leaflet ([\d.]+)", _read(vendor, "leaflet", "leaflet.js")).group(1) == listed["leaflet"]
    assert f'"{listed["pdfjs-dist"]}"' in _read(vendor, "pdfjs", "pdf.min.js")
    assert "leaflet.heat" in listed
    assert all(p[2] == "npm" for p in check_vulnerabilities.pins(os.path.join(HERE, "tools", "vendor_versions.txt")))
    deploy = _read("deploy", "docker", "deploy-docker.sh")
    assert "tools/vendor_versions.txt" in deploy[deploy.index("check_vulnerabilities.py"):]
    assert "tools/check_vulnerabilities.py tools/vendor_versions.txt" in deploy        # shipped for maintenance.sh
    maintenance = _read("deploy", "docker", "maintenance.sh")
    check = maintenance[maintenance.index("check_vulnerabilities.py"):]
    assert "/src/tools/vendor_versions.txt" in check and "./alarm.sh" in check and ":ro" in maintenance
    assert "RepoDigests" in maintenance                                            # DP-11: what ran when


def test_deploy_says_when_it_skips_a_check():
    """Audit 2026-10-13, SV-5/DP-9 and DP-17: SKIP_TESTS, SKIP_OSV and ALLOW_UNMERGED went by without a trace;
    the Plesk deploy had no check for uncommitted or unmerged code at all."""
    docker = _read("deploy", "docker", "deploy-docker.sh")
    assert "WARNING: this deploy skips checks" in docker
    assert "echo 'skipped=$SKIPPED'" in docker
    assert "tail -n 1 $DIR/app/deployed_at.txt" not in docker                     # the commit is the second line
    assert "--require-hashes" in _read("tools", "rechenknecht", "README.md")


@needs_bash
@pytest.mark.deploy
def test_skipped_checks_land_in_deployed_at(tmp_path):
    """Audit 2026-10-13, SV-5: a deploy with SKIP_TESTS=1 SKIP_OSV=1 writes a third line into app/deployed_at.txt, which
    Administration > Server shows; one with all checks writes none."""
    from tests.platform.test_audit8_b_deploy import World, _write

    world = World(tmp_path)
    _write(str(world.path("askurious-env", ".env")), "SECRET_KEY=alt\nCOMPUTE_SECRET=c\n", 0o600)
    out = world.run()                                                             # World skips tests and OSV
    assert out.returncode == 0, out.stdout + out.stderr
    assert "WARNING: this deploy skips checks (tests,osv)" in out.stdout
    lines = world.text("askurious", "app", "deployed_at.txt").split()
    assert len(lines) == 3 and lines[2] == "skipped=tests,osv"


def _bytes(limit):
    return int(limit[:-1]) * {"m": 1024 ** 2, "g": 1024 ** 3}[limit[-1]]


def test_memory_limits_with_whisperx_stay_below_the_host():
    """Abgleich 2026-10-15, T08-N-4: with the profile whisper the limits came to some 21.8 GB on the 19 GB server, and
    BETRIEB.md named other limits than compose.yaml (T08-N-2)."""
    app = re.search(r"^  mem_limit: (\d+[mg])$", APP, re.M).group(1)
    limits = {}
    for name in _services_list():
        if name == "participant-b":                                                # a copy of participant-a
            continue
        block = _service(name)
        if 'profiles: ["brand"]' in block or "<<: *brand-researcher" in block:
            continue
        own = re.search(r"^    mem_limit: (\d+[mg])$", block, re.M)
        limits[name] = own.group(1) if own else app
    limits["participant-b"] = limits["participant-a"]
    total = sum(_bytes(v) for v in limits.values())
    # 22 GB on the RWTH server since 2026-10-17: the limits leave some 2.5 GB to the host system
    assert "whisper" in limits and total <= 19.5 * 1024 ** 3, limits
    assert total - _bytes(limits["whisper"]) <= 15.5 * 1024 ** 3
    # the table in BETRIEB.md names what compose.yaml sets
    betrieb = internal_text("deploy", "docker", "BETRIEB.md")
    if betrieb is None:  # only in the private instance repository
        return
    table = betrieb[betrieb.index("| Dienst | `mem_limit`"):]
    table = table[:table.index("\n\n")]
    shown = {"2560m": "2,5 GB", "3g": "3 GB", "4g": "4 GB", "2g": "2 GB", "768m": "768 MB", "256m": "256 MB"}
    for name in ("participant-a", "researcher", "compute", "housekeeping", "status", "db", "nginx", "certbot", "whisper"):
        row = next(line for line in table.splitlines() if f"`{name}" in line)
        assert shown[limits[name]] in row, (name, row)


def test_whisperx_image_pinned_hashed_and_checked_every_week():
    """Abgleich 2026-10-15, T08-N-3: the WhisperX image installed its packages without hashes on an unpinned base, and
    neither the weekly rebuild nor the weekly check for known vulnerabilities looked at it."""
    import sys

    dockerfile = _read("deploy", "docker", "whisper", "Dockerfile")
    assert re.search(r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    assert dockerfile.count("--require-hashes --no-deps") == 2
    for name in ("requirements.txt", "requirements-cpu.txt"):
        text = _read("deploy", "docker", "whisper", name)
        pins = re.findall(r"^(\S+==\S+) \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)", text, re.M)
        assert pins and len(pins) == len(re.findall(r"^\S+==", text, re.M)), name
    maintenance = _read("deploy", "docker", "maintenance.sh")
    assert "whisper/Dockerfile" in maintenance and "--accepted /src/deploy/docker/whisper/osv-accepted.txt" in maintenance
    sys.path.insert(0, os.path.join(HERE, "tools"))
    try:
        import check_vulnerabilities
    finally:
        sys.path.pop(0)
    known = check_vulnerabilities.accepted(os.path.join(DOCKER, "whisper", "osv-accepted.txt"))
    assert ("torch", "PYSEC-2026-2286") in known and ("transformers", "GHSA-29pf-2h5f-8g72") in known
    assert dict(check_vulnerabilities.pins(os.path.join(DOCKER, "whisper", "requirements-cpu.txt")))["torch"] == "2.8.0+cpu"


def test_accepted_vulnerabilities_do_not_fail_the_check_but_new_ones_do(tmp_path, monkeypatch, capsys):
    """T08-N-3: an id judged in whisper/README.md is listed without an alarm; another id of the same package alarms."""
    import sys

    sys.path.insert(0, os.path.join(HERE, "tools"))
    try:
        import check_vulnerabilities
    finally:
        sys.path.pop(0)
    (tmp_path / "req.txt").write_text("torch==2.8.0+cpu\n", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("# judged\ntorch GHSA-old  # why\n", encoding="utf-8")
    for found, code in ((["GHSA-old"], 0), (["GHSA-old", "GHSA-new"], 1)):
        monkeypatch.setattr(check_vulnerabilities, "check", lambda packages, f=found: {packages[0]: f})
        monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "req.txt"), "--accepted", str(tmp_path / "ok.txt")])
        with pytest.raises(SystemExit) as end:
            check_vulnerabilities.main()
        assert end.value.code == code
    assert "accepted: 1" in capsys.readouterr().out


def test_dev_requirements_without_known_vulnerabilities_are_checked_at_deploy():
    """Abgleich 2026-10-15, DP-20: pytest 8.4.2 (GHSA-6w46-j5rx-g56g, tmpdir handling) was not in any check."""
    assert "pytest==9.1.1" in _read("requirements-dev.txt").splitlines()
    deploy = _read("deploy", "docker", "deploy-docker.sh")
    assert "requirements-dev.txt" in deploy[deploy.index("check_vulnerabilities.py"):deploy.index("Known vulnerabilities")]


# --- audit 2026-10-18: images by digest (CFG-05), CI and reasons for skipped checks (CFG-06), HSTS (CFG-10) ---------

def test_every_base_image_is_pinned_by_digest():
    """CFG-05: python:3.12-slim, postgres:17 and nginx:1.30.5-alpine went by their tags alone; the weekly pull took
    whatever the tag showed. Every FROM and image: line now names tag and digest (images built here excepted), and
    restart.sh recreates the database container for a new image only in the maintenance window."""
    import sys

    sys.path.insert(0, os.path.join(HERE, "tools"))
    import image_digests

    assert image_digests.unpinned() == []
    names = {(name, tag) for _f, name, tag, _d in image_digests.pinned()}
    assert {("python", "3.12-slim"), ("nginx", "1.30.5-alpine"), ("certbot/certbot", "v5.8.0")} <= names
    # the database keeps its tag until a maintenance window sets DB_IMAGE by digest: a changed db line stops the
    # rolling deploy in the isolation check (deploy of 2026-10-19)
    assert "image: ${DB_IMAGE:-postgres:17}" in _read("deploy", "docker", "compose.yaml")
    restart = _read("deploy", "docker", "restart.sh")
    assert "$C up -d --no-deps --no-recreate db" in restart
    maintenance = _read("deploy", "docker", "maintenance.sh")
    assert "/src/tools/image_digests.py" in maintenance and "alarm.sh" in maintenance


def test_image_digests_update_writes_the_new_digest(tmp_path, monkeypatch):
    import shutil
    import sys

    sys.path.insert(0, os.path.join(HERE, "tools"))
    import image_digests

    for rel in image_digests.FILES:
        os.makedirs(tmp_path / os.path.dirname(rel), exist_ok=True)
        shutil.copy(os.path.join(HERE, rel), tmp_path / rel)
    monkeypatch.setattr(image_digests, "ROOT", str(tmp_path))
    new = "sha256:" + "1" * 64
    monkeypatch.setattr(image_digests, "registry_digest", lambda name, tag: new if name == "nginx" else
                        next(d for _f, n, t, d in image_digests.pinned() if (n, t) == (name, tag)))
    assert image_digests.main([]) == 1                                         # a moved tag: exit 1, nothing written
    assert image_digests.main(["--update"]) == 0
    assert f"nginx:1.30.5-alpine@{new}" in (tmp_path / "deploy/docker/nginx-image/Dockerfile").read_text()
    assert image_digests.main([]) == 0


def test_ci_runs_the_tests_and_the_vulnerability_check():
    """CFG-06; since 2026-10-19 the workflow starts only by hand (Ralf: no CI on GitHub for now, the cost of private
    Actions), and the deploy asks GitHub nothing."""
    ci = _read(".github", "workflows", "ci.yml")
    on = ci.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    assert on.strip() == "workflow_dispatch:", on                              # no push, pull_request, schedule
    assert "permissions:\n  contents: read" in ci
    assert "python -m pytest" in ci and "tools/check_vulnerabilities.py requirements.lock tools/vendor_versions.txt" in ci
    for folder in os.listdir(os.path.join(HERE, "tests")):                     # every test folder in one part
        if os.path.isdir(os.path.join(HERE, "tests", folder)) and folder not in ("helpers", "data", "__pycache__"):
            assert f"tests/{folder}" in ci, folder
    # actions pinned by commit, not by a tag that can move
    assert all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in re.findall(r"uses: (\S+)", ci))
    docker = _read("deploy", "docker", "deploy-docker.sh")
    assert not re.search(r"\bgh run\b|REQUIRE_CI|CI_CHECK", docker)


@needs_bash
@pytest.mark.deploy
def test_a_skipped_check_needs_a_reason_and_github_is_not_asked(tmp_path):
    """CFG-06: SKIP_TESTS/SKIP_OSV/ALLOW_UNMERGED went up with a warning only; now each needs SKIP_REASON, which lands
    in ~/askurious-deploys.log on the server. Since 2026-10-19 the deploy asks no CI on GitHub (Ralf: none for now,
    the local checks decide): a gh that would report a failed run is never called."""
    from tests.platform.test_audit8_b_deploy import World, _write

    world = World(tmp_path)
    _write(str(world.path("askurious-env", ".env")), "SECRET_KEY=alt\nCOMPUTE_SECRET=c\n", 0o600)
    out = world.run(SKIP_REASON="")
    assert out.returncode == 1 and "SKIP_REASON" in out.stdout
    asked = world.bin / "gh-was-asked"
    _write(str(world.bin / "gh"), f"#!/usr/bin/env bash\ntouch \"$FAKEBIN/{asked.name}\"\necho failure\nexit 1\n", 0o755)
    out = world.run(SKIP_REASON="hotfix (test)")
    assert out.returncode == 0, out.stdout + out.stderr
    assert not asked.exists() and "CI on GitHub" not in out.stdout
    assert "skipped=tests,osv reason=hotfix (test)" in world.text("askurious-deploys.log")


def test_hsts_includes_subdomains_only_when_switched_on(tmp_path, monkeypatch):
    """CFG-10: includeSubDomains in nginx (servers.sh) and in the app, prepared and off: only HSTS_SUBDOMAINS=1 adds it
    (the DNS zones are checked first, a browser keeps the rule a year); no preload."""
    servers = _read("deploy", "docker", "nginx", "servers.sh")
    assert 'if [ "${HSTS_SUBDOMAINS:-0}" = "1" ]; then hsts="max-age=31536000; includeSubDomains"; else hsts="max-age=31536000"; fi' in servers
    assert "includeSubDomains; preload" not in servers                          # no preload without Ralf
    for side in ("participant", "researcher"):
        conf = _read("deploy", "docker", "nginx", "locations", f"{side}.conf")
        assert "max-age=31536000" not in conf and "Strict-Transport-Security $askurious_hsts always;" in conf
    assert "HSTS_SUBDOMAINS: ${HSTS_SUBDOMAINS:-0}" in COMPOSE


def test_the_app_sends_hsts_with_subdomains_only_when_switched_on(app, client):
    assert client.get("/", base_url="https://survey.example").headers["Strict-Transport-Security"] == "max-age=31536000"
    app.config["HSTS_SUBDOMAINS"] = True
    assert client.get("/", base_url="https://survey.example").headers["Strict-Transport-Security"] == \
        "max-age=31536000; includeSubDomains"


def test_participants_get_no_compute_secret_and_their_own_token_key():
    """Audit 2026-10-22, E-01: with COMPUTE_SECRET a break-in on the participants' side could hand the Rechenknecht's
    results of running jobs to the researchers' side; the participants' containers hand out no jobs. TOKEN_KEY opens
    every stored secret; what the participants open lies under PARTICIPANT_TOKEN_KEY (stage 2 of the same audit)."""
    assert set(PARTICIPANT_SECRETS) == set(SECRETS) - {"COMPUTE_SECRET", "TOKEN_KEY", "TOKEN_KEY_FALLBACKS"}
    assert {"PARTICIPANT_TOKEN_KEY", "PARTICIPANT_TOKEN_KEY_FALLBACKS"} <= set(SECRETS)
    brand = _service("brand-participant")
    assert 'COMPUTE_SECRET: ""' in brand


def test_participants_share_no_network_with_the_researchers_container():
    """Audit 2026-10-22, E-01 stage 2b: in the isolated networks the participants' containers reached
    researcher:8000 past nginx (its rate limits, the blocked /rechenknecht/ and /guide/internal/, a forged
    X-Forwarded-For). Now they share no network with it; nginx and status reach both, the database lies in both."""
    text = open(os.path.join(HERE, "deploy", "docker", "isolation", "networks.yaml"), encoding="utf-8").read()

    def names(service):
        """The networks of `service`: a flow map on its line, or the keys of the block below it (nginx)."""
        block = re.search(rf"^  {service}:\n    networks: !override(.*?)(?=^  \S|^\S)", text, re.M | re.S).group(1)
        if block.strip().startswith("{"):
            return set(re.findall(r"([\w-]+): \{", block.strip()[1:]))
        return set(re.findall(r"^      ([\w-]+):", block, re.M))
    for participant in ("participant-a", "participant-b"):
        assert not names(participant) & names("researcher"), participant
        assert names(participant) == {"frontend", "backend"}
    assert {"researcher-front"} <= names("nginx") and {"frontend"} <= names("nginx")
    assert {"researcher-front", "frontend"} <= names("status")
    assert {"backend", "researcher-back"} <= names("db") and "researcher-back" in names("housekeeping")
